"""
Processor operations that search for and match given items with remote items.

Searches for matches on remote APIs, matches the item to the best matching result from the query,
and assigns the ID of the matched object back to the item.
"""
from collections.abc import Mapping, Sequence, Iterable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from musify.core.base import MusifyObject, MusifyItemSettable
from musify.core.enum import TagField, TagFields as Tag
from musify.core.result import Result
from musify.exception import MusifyAttributeError
from musify.libraries.core.collection import MusifyCollection
from musify.libraries.remote.core.api import RemoteAPI
from musify.libraries.remote.core.enum import RemoteObjectType
from musify.libraries.remote.core.factory import RemoteObjectFactory
from musify.log import REPORT
from musify.processors.match import ItemMatcher
from musify.types import UnitIterable
from musify.utils import align_string, get_max_width


@dataclass(frozen=True)
class ItemSearchResult[T: MusifyItemSettable](Result):
    """Stores the results of the searching process."""
    #: Sequence of Items for which matches were found from the search.
    matched: Sequence[T] = field(default=tuple())
    #: Sequence of Items for which matches were not found from the search.
    unmatched: Sequence[T] = field(default=tuple())
    #: Sequence of Items which were skipped during the search.
    skipped: Sequence[T] = field(default=tuple())


@dataclass(frozen=True)
class SearchConfig:
    """Key settings related to a search algorithm."""
    #: The fields to match results on.
    match_fields: TagField | Iterable[TagField]
    #: A sequence of the tag names to use as search fields in the 1st pass.
    search_fields_1: Sequence[TagField] = (Tag.NAME,)
    #: If no results are found from the tag names in ``search_fields_1`` on the 1st pass,
    #: an optional sequence of the tag names to use as search fields in the 2nd pass.
    search_fields_2: Iterable[TagField] = ()
    #: If no results are found from the tag names in ``search_fields_2`` on the 2nd pass,
    #: an optional sequence of the tag names to use as search fields in the 3rd pass.
    search_fields_3: Iterable[TagField] = ()

    #: The number of the results to request when querying the API.
    result_count: int = 10
    #: The minimum acceptable score for an item to be considered a match.
    min_score: float = 0.1
    #: The maximum score for an item to be considered a perfect match.
    #: After this score is reached by an item, any other items are disregarded as potential matches.
    max_score: float = 0.8
    #: When True, items determined to be karaoke are allowed when matching added items.
    #: Skip karaoke results otherwise.
    allow_karaoke: bool = False


class RemoteItemSearcher(ItemMatcher):
    """
    Searches for remote matches for a list of item collections.

    :param object_factory: The :py:class:`RemoteObjectFactory` to use when creating new remote objects.
        This must have a :py:class:`RemoteAPI` assigned for this processor to work as expected.
    :param use_cache: Use the cache when calling the API endpoint. Set as False to refresh the cached response.
    """

    __slots__ = ("factory", "use_cache")

    #: The :py:class:`SearchSettings` for each :py:class:`RemoteObjectType`
    search_settings: dict[RemoteObjectType, SearchConfig] = {
        RemoteObjectType.TRACK: SearchConfig(
            match_fields={Tag.TITLE, Tag.ARTIST, Tag.ALBUM, Tag.LENGTH},
            search_fields_1=[Tag.NAME, Tag.ARTIST],
            search_fields_2=[Tag.NAME, Tag.ALBUM],
            search_fields_3=[Tag.NAME],
            result_count=10,
            min_score=0.1,
            max_score=0.8,
            allow_karaoke=False,
        ),
        RemoteObjectType.ALBUM: SearchConfig(
            match_fields={Tag.ARTIST, Tag.ALBUM, Tag.LENGTH},
            search_fields_1=[Tag.NAME, Tag.ARTIST],
            search_fields_2=[Tag.NAME],
            result_count=5,
            min_score=0.1,
            max_score=0.7,
            allow_karaoke=False,
        )
    }

    @property
    def api(self) -> RemoteAPI:
        """The :py:class:`RemoteAPI` to call"""
        return self.factory.api

    def __init__(self, object_factory: RemoteObjectFactory, use_cache: bool = False):
        super().__init__()

        #: The :py:class:`RemoteObjectFactory` to use when creating new remote objects.
        self.factory = object_factory
        #: When true, use the cache when calling the API endpoint
        self.use_cache = use_cache

    def _get_results(
            self, item: MusifyObject, kind: RemoteObjectType, settings: SearchConfig
    ) -> list[dict[str, Any]] | None:
        """Query the API to get results for the current item based on algorithm settings"""
        self.clean_tags(item)

        def execute_query(keys: Iterable[TagField]) -> tuple[list[dict[str, Any]], str]:
            """Generate and execute the query against the API for the given item's cleaned ``keys``"""
            attributes = [item.clean_tags.get(key) for key in keys]
            q = " ".join(str(attr) for attr in attributes if attr)
            return self.api.query(q, kind=kind, limit=settings.result_count), q

        results, query = execute_query(settings.search_fields_1)
        if not results and settings.search_fields_2:
            results, query = execute_query(settings.search_fields_2)
        if not results and settings.search_fields_3:
            results, query = execute_query(settings.search_fields_3)

        if results:
            self._log_padded([item.name, f"Query: {query}", f"{len(results)} results"])
            return results
        self._log_padded([item.name, f"Query: {query}", "Match failed: No results."], pad="<")

    def _log_results(self, results: Mapping[str, ItemSearchResult]) -> None:
        """Logs the final results of the ItemSearcher"""
        if not results:
            return

        max_width = get_max_width(results)

        total_matched = 0
        total_unmatched = 0
        total_skipped = 0
        total_all = 0

        for name, result in results.items():
            matched = len(result.matched)
            unmatched = len(result.unmatched)
            skipped = len(result.skipped)
            total = total_matched + total_unmatched + total_skipped

            total_matched += matched
            total_unmatched += unmatched
            total_skipped += skipped
            total_all += total

            colour1 = "\33[92m" if matched > 0 else "\33[94m"
            colour2 = "\33[92m" if unmatched == 0 else "\33[91m"
            colour3 = "\33[92m" if skipped == 0 else "\33[93m"

            self.logger.report(
                f"\33[1m{align_string(name, max_width=max_width)} \33[0m|"
                f"{colour1}{matched:>6} matched \33[0m| "
                f"{colour2}{unmatched:>6} unmatched \33[0m| "
                f"{colour3}{skipped:>6} skipped \33[0m| "
                f"\33[97m{total:>6} total \33[0m"
            )

        self.logger.report(
            f"\33[1;96m{'TOTALS':<{max_width}} \33[0m|"
            f"\33[92m{total_matched:>6} matched \33[0m| "
            f"\33[91m{total_unmatched:>6} unmatched \33[0m| "
            f"\33[93m{total_skipped:>6} skipped \33[0m| "
            f"\33[97m{total_all:>6} total \33[0m"
        )
        self.logger.print(REPORT)

    @staticmethod
    def _determine_remote_object_type(obj: MusifyObject) -> RemoteObjectType:
        if hasattr(obj, "kind"):
            return obj.kind
        raise MusifyAttributeError(f"Given object does not specify a RemoteObjectType: {obj.__class__.__name__}")

    def __call__(self, *args, **kwargs) -> dict[str, ItemSearchResult]:
        return self.search(*args, **kwargs)

    def search[T: MusifyItemSettable](
            self, collections: Collection[MusifyCollection[T]]
    ) -> dict[str, ItemSearchResult[T]]:
        """
        Searches for remote matches for the given list of item collections.

        :return: Map of the collection's name to its :py:class:`ItemSearchResult` object.
        """
        self.logger.debug("Searching: START")
        if not [item for c in collections for item in c.items if item.has_uri is None]:
            self.logger.debug("\33[93mNo items to search. \33[0m")
            return {}

        kinds = {coll.__class__.__name__ for coll in collections}
        kind = kinds.pop() if len(kinds) == 1 else "collection"
        self.logger.info(
            f"\33[1;95m ->\33[1;97m "
            f"Searching for matches on {self.api.source} for {len(collections)} {kind}s\33[0m"
        )

        bar = self.logger.get_progress_bar(iterable=collections, desc="Searching", unit=f"{kind}s")
        with ThreadPoolExecutor(thread_name_prefix="searcher-main") as executor:
            search_results = dict(executor.map(lambda coll: (coll.name, self._search_collection(coll)), bar))

        self.logger.print()
        self._log_results(search_results)
        self.logger.debug("Searching: DONE\n")
        return search_results

    def _search_collection[T: MusifyItemSettable](self, collection: MusifyCollection) -> ItemSearchResult[T]:
        kind = collection.__class__.__name__

        skipped = tuple(item for item in collection if item.has_uri is not None)
        if len(skipped) == len(collection):
            self._log_padded([collection.name, "Skipping search, no items to search"], pad='<')

        if getattr(collection, "compilation", True) is False:
            self._log_padded([collection.name, "Searching for collection as a unit"], pad='>')
            self._search_collection_unit(collection=collection)

            missing = [item for item in collection.items if item.has_uri is None]
            if missing:
                self._log_padded([collection.name, f"Searching for {len(missing)} unmatched items in this {kind}"])
                self._search_items(collection=collection)
        else:
            self._log_padded([collection.name, "Searching for distinct items in collection"], pad='>')
            self._search_items(collection=collection)

        return ItemSearchResult(
            matched=tuple(item for item in collection if item.has_uri and item not in skipped),
            unmatched=tuple(item for item in collection if item.has_uri is None and item not in skipped),
            skipped=skipped
        )

    def _get_item_match[T: MusifyItemSettable](
            self, item: T, match_on: UnitIterable[TagField] | None = None, results: Iterable[T] = None
    ) -> tuple[T, T | None]:
        kind = self._determine_remote_object_type(item)
        search_config = self.search_settings[kind]

        if results is None:
            responses = self._get_results(item, kind=kind, settings=search_config)
            # noinspection PyTypeChecker
            results: Iterable[T] = map(self.factory[kind], responses or ())

        result = self.match(
            item,
            results=results,
            match_on=match_on if match_on is not None else search_config.match_fields,
            min_score=search_config.min_score,
            max_score=search_config.max_score,
            allow_karaoke=search_config.allow_karaoke,
        ) if results else None

        return item, result

    def _search_items[T: MusifyItemSettable](self, collection: Iterable[T]) -> None:
        """Search for matches on individual items in an item collection that have ``None`` on ``has_uri`` attribute"""
        with ThreadPoolExecutor(thread_name_prefix="searcher-items") as executor:
            matches = executor.map(self._get_item_match, filter(lambda i: i.has_uri is None, collection))

        for item, match in matches:
            if match and match.has_uri:
                item.uri = match.uri

    def _search_collection_unit[T: MusifyItemSettable](self, collection: MusifyCollection[T]) -> None:
        """
        Search for matches on an entire collection as a whole
        i.e. search for just the collection and not its distinct items.
        """
        if all(item.has_uri for item in collection):
            return

        kind = self._determine_remote_object_type(collection)
        search_config = self.search_settings[kind]

        responses = self._get_results(collection, kind=kind, settings=search_config)
        key = self.api.collection_item_map[kind]
        for response in responses:
            self.api.extend_items(response, kind=kind, key=key, use_cache=self.use_cache)

        # noinspection PyProtectedMember,PyTypeChecker
        # order to prioritise results that are closer to the item count of the input collection
        results: list[T] = sorted(map(self.factory[kind], responses), key=lambda x: abs(x._total - len(collection)))

        result = self.match(
            collection,
            results=results,
            match_on=search_config.match_fields,
            min_score=search_config.min_score,
            max_score=search_config.max_score,
            allow_karaoke=search_config.allow_karaoke,
        )

        if not result:
            return

        with ThreadPoolExecutor(thread_name_prefix="searcher-collection") as executor:
            matches = executor.map(
                lambda item: self._get_item_match(item, match_on=[Tag.TITLE], results=result.items),
                filter(lambda i: i.has_uri is None, collection)
            )

        for item, match in matches:
            if match and match.has_uri:
                item.uri = match.uri

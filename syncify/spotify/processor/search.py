from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, MutableMapping, Any, Tuple, Mapping, Literal

from spotify import ItemType
from spotify.library import SpotifyTrack, SpotifyAlbum
from syncify.abstract.collection import ItemCollection, Album
from syncify.abstract.item import Item, Track, Base
from syncify.spotify.api import API
from syncify.spotify.processor.match import ItemMatcher


@dataclass
class SearchResult:
    matched: List[Item]
    unmatched: List[Item]
    skipped: List[Item]


class SearchCollection(ItemCollection):
    @property
    def name(self) -> str:
        return self._name

    @property
    def items(self) -> List[Item]:
        return self._items

    def __init__(self, name: str, items: List[Item]):
        self._name = name
        self._items = items

    def as_dict(self) -> MutableMapping[str, Any]:
        return {"name": self.name, "items": self.items}


@dataclass
class Algorithm:
    search_fields_1: List[str]
    match_fields: Optional[List[Literal["name", "artist", "album", "length", "year"]]]
    result_count: int

    min_score: int = 1
    max_score: int = 2

    search_fields_2: Optional[List[str]] = None
    search_fields_3: Optional[List[str]] = None


@dataclass
class AlgorithmSettings:
    full: Algorithm = Algorithm(search_fields_1=["name", "artist"],
                                search_fields_2=["name", "album"],
                                search_fields_3=["name"],
                                match_fields=["name", "artist", "album", "length"],
                                result_count=10)


class ItemSearcher(ItemMatcher, metaclass=ABCMeta):

    @property
    @abstractmethod
    def api(self) -> API:
        raise NotImplementedError

    def _get_results(self, item: Base, kind: ItemType, algo: Algorithm) -> Optional[List[MutableMapping[str, Any]]]:
        item_clean = self.clean_tags(item)

        def execute_query(keys: List[str]) -> Tuple[List[MutableMapping[str, Any]], str]:
            attributes = set(getattr(item_clean, key, None) for key in keys)
            q = " ".join(attr for attr in attributes if attr)
            return self.api.query(q, kind=kind, limit=algo.result_count), q

        results, query = execute_query(algo.search_fields_1)
        if not results and algo.search_fields_2:
            results, query = execute_query(algo.search_fields_2)
        if not results and algo.search_fields_3:
            results, query = execute_query(algo.search_fields_3)

        if not results:
            self._log_padded([item_clean.name, f"Query: {query}", "Match failed: No results."], pad="<")
        else:
            self._log_padded([item_clean.name, f"Query: {query}", f"{len(results)} results"], pad=" ")
            return results

    def _log_results(self, results: Mapping[str, SearchResult]) -> None:
        if not results:
            return

        max_width = self._get_max_width(results, max_width=50)

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

            colour1 = '\33[92m' if matched > 0 else '\33[94m'
            colour2 = '\33[92m' if unmatched == 0 else '\33[91m'
            colour3 = '\33[92m' if skipped == 0 else '\33[93m'

            self._logger.info(
                f"{self._truncate_align_str(name, max_width=max_width)} |"
                f'{colour1}{matched:>4} matched \33[0m|'
                f'{colour2}{unmatched:>4} unmatched \33[0m|'
                f'{colour3}{skipped:>4} skipped \33[0m|'
                f'\33[1m {total:>4} total \33[0m'
            )

        self._line()
        self._logger.info(
            f"\33[1;96m{self._truncate_align_str('TOTALS', max_width=max_width)} \33[0m|"
            f'\33[92m{total_matched:>5} matched \33[0m|'
            f'\33[91m{total_unmatched:>5} unmatched \33[0m|'
            f'\33[93m{total_skipped:>5} skipped \33[0m|'
            f"\33[1m{total_all:>6} total \33[0m\n"
        )
        self._line()

    def search(
            self, collections: List[ItemCollection], algo: Algorithm = AlgorithmSettings.full
    ) -> MutableMapping[str, SearchResult]:
        self._logger.debug('Searching items: START')
        if not [self.clean_tags(item) for c in collections for item in c.items if item.has_uri is None]:
            self._log_padded(["\33[93mNo items to search. \33[0m"], pad="<")
            return {}

        self._logger.info(f"\33[1;95m ->\33[1;97m "
                          f"Searching for matches on Spotify for {len(collections)} collections\33[0m")
        bar = self._get_progress_bar(iterable=collections, desc="Searching", unit="collections")

        search_results: MutableMapping[str, SearchResult] = {}
        for collection in bar:
            if not [item for item in collection.items if item.has_uri is None]:
                self._log_padded([collection.name, "Skipping search, no tracks to search"], pad='<')

            if getattr(collection, "compilation", None):
                self._log_padded([collection.name, "Searching with item algorithm"], pad='>')
                result = self._search_items(collection=collection, algo=algo)
            else:
                self._log_padded([collection.name, "Searching with album algorithm"], pad='>')
                result = self._search_album(collection=collection, algo=algo)
                missing = [item for item in result.items if item.has_uri is None]
                if missing:
                    self._log_padded([collection.name,
                                      f"Searching for {len(missing)} unmatched items in this collection"])
                    search = SearchCollection(name=collection.name, items=missing)
                    result = self._search_items(collection=search, algo=algo)

            search_results[collection.name] = SearchResult(
                matched=[track for track in result if track.has_uri],
                unmatched=[track for track in result if track.has_uri is None],
                skipped=[track for track in result if not track.has_uri]
            )

        self._log_results(search_results)
        self._logger.debug('Searching items: DONE\n')
        return search_results

    def _search_items(self, collection: ItemCollection, algo: Algorithm) -> ItemCollection:
        matches = []
        for item in collection.items:
            if not isinstance(item, Track):
                raise NotImplementedError(f"Currently only able to search for Track items, "
                                          f"not {item.__class__.__name__}")

            results = [SpotifyTrack(r) for r in self._get_results(item, kind=ItemType.TRACK, algo=algo)]
            results = [result for result in results if self.not_karaoke(item, result=result)]
            result = self.score_match(item, results=results, match_on=algo.match_fields,
                                      min_score=algo.min_score, max_score=algo.max_score)
            matches.append(result) if result else matches.append(self.clean_tags(item))

        return SearchCollection(collection.name, items=matches)

    def _search_album(self, collection: Album, algo: Algorithm) -> ItemCollection:
        results = [SpotifyAlbum(r) for r in self._get_results(collection, kind=ItemType.ALBUM, algo=algo)]
        results = [result for result in results if self.not_karaoke(collection, result=result)]

        # order and prioritise results that are closer to the item count of the input collection
        results = sorted(results, key=lambda x: abs(x.total_tracks - len(collection)))
        result = self.score_match(collection, results=results, match_on=algo.match_fields,
                                  min_score=algo.min_score, max_score=algo.max_score)
        return result if result else collection
"""
The XAutoPF implementation of a :py:class:`LocalPlaylist`.
"""
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from os.path import exists
from typing import Any

import xmltodict

from musify.core.base import MusifyItem
from musify.core.enum import Fields, Field, TagFields
from musify.core.printer import PrettyPrinter
from musify.core.result import Result
from musify.exception import FieldError
from musify.file.base import File
from musify.file.path_mapper import PathMapper
from musify.libraries.local.playlist.base import LocalPlaylist
from musify.libraries.local.track import LocalTrack
from musify.processors.compare import Comparer
from musify.processors.filter import FilterDefinedList, FilterComparers
from musify.processors.filter_matcher import FilterMatcher
from musify.processors.limit import ItemLimiter, LimitType
from musify.processors.sort import ItemSorter, ShuffleMode
from musify.utils import merge_maps, to_collection

AutoMatcher = FilterMatcher[
    LocalTrack, FilterDefinedList[LocalTrack], FilterDefinedList[LocalTrack], FilterComparers[LocalTrack]
]


@dataclass(frozen=True)
class SyncResultXAutoPF(Result):
    """Stores the results of a sync with a local XAutoPF playlist."""
    #: The total number of tracks in the playlist before the sync.
    start: int
    #: The description of the playlist before sync.
    start_description: str
    #: The number of tracks that matched the include settings before the sync.
    start_included: int
    #: The number of tracks that matched the exclude settings before the sync.
    start_excluded: int
    #: The number of tracks that matched all the :py:class:`Comparer` settings before the sync.
    start_compared: int
    #: Was a limiter present on the playlist before the sync.
    start_limiter: bool
    #: Was a sorter present on the playlist before the sync.
    start_sorter: bool

    #: The total number of tracks in the playlist after the sync.
    final: int
    #: The description of the playlist after sync.
    final_description: str
    #: The number of tracks that matched the include settings after the sync.
    final_included: int
    #: The number of tracks that matched the exclude settings after the sync.
    final_excluded: int
    #: The number of tracks that matched all the :py:class:`Comparer` settings after the sync.
    final_compared: int
    #: Was a limiter present on the playlist after the sync.
    final_limiter: bool
    #: Was a sorter present on the playlist after the sync.
    final_sorter: bool


class XAutoPF(LocalPlaylist[AutoMatcher]):
    """
    For reading and writing data from MusicBee's auto-playlist format.

    **Note**: You must provide a list of tracks to search on initialisation for this playlist type.

    :param path: Absolute path of the playlist.
    :param tracks: Optional. Available Tracks to search through for matches.
        If none are provided, no tracks will be loaded initially
    :param path_mapper: Optionally, provide a :py:class:`PathMapper` for paths stored in the playlist file.
        Useful if the playlist file contains relative paths and/or paths for other systems that need to be
        mapped to absolute, system-specific paths to be loaded and back again when saved.
    """

    __slots__ = ("_parser", "_description",)

    valid_extensions = frozenset({".xautopf"})

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, value: str | None):
        self._description = value

    @property
    def image_links(self):
        return {}

    def __init__(
            self, path: str, tracks: Collection[LocalTrack] = (), path_mapper: PathMapper = PathMapper(), *_, **__
    ):
        self._validate_type(path)
        if not exists(path):
            # TODO: implement creation of auto-playlist from scratch (very low priority)
            raise NotImplementedError(
                f"No playlist at given path: {path}. "
                "This program is not yet able to create this playlist type from scratch."
            )

        self._parser = XMLPlaylistParser(path=path, path_mapper=path_mapper)
        self._description = self._parser.xml_source["Description"]

        super().__init__(
            path=path,
            matcher=self._parser.get_matcher(),
            limiter=self._parser.get_limiter(),
            sorter=self._parser.get_sorter(),
            path_mapper=self._parser.path_mapper,
        )

        self.load(tracks=tracks)

    def load(self, tracks: Collection[LocalTrack] = ()) -> list[LocalTrack]:
        tracks_list = list(tracks)
        self.sorter.sort_by_field(tracks_list, field=Fields.LAST_PLAYED, reverse=True)

        self._match(tracks=tracks, reference=tracks_list[0] if len(tracks) > 0 else None)
        self._limit(ignore=self.matcher.exclude)
        self._sort()

        self._original = self.tracks.copy()
        return self.tracks

    def save(self, dry_run: bool = True, *_, **__) -> SyncResultXAutoPF:
        """
        Write the tracks in this Playlist and its settings (if applicable) to file.

        :param dry_run: Run function, but do not modify file at all.
        :return: The results of the sync as a :py:class:`SyncResultXAutoPF` object.
        """
        xml_start = deepcopy(self.xml)
        xml_final = deepcopy(self.xml)

        count_start = len(self._original)
        source_start: dict[str, Any] = xml_start["SmartPlaylist"]["Source"]
        source_final: dict[str, Any] = xml_final["SmartPlaylist"]["Source"]

        # update the stored XML object
        source_final["Description"] = self.description
        self._update_xml_paths(xml_final)
        # self._update_comparers(xml_final)
        # self._update_limiter(xml_final)
        # self._update_sorter(xml_final)

        if not dry_run:  # save the modified XML object to file and update stored values
            self.xml = xml_final
            self._save_xml()
            self._original = self.tracks.copy()

        return SyncResultXAutoPF(
            start=count_start,
            start_description=source_start["Description"],
            start_included=len([p for p in source_start.get("ExceptionsInclude", "").split("|") if p]),
            start_excluded=len([p for p in source_start.get("Exceptions", "").split("|") if p]),
            start_compared=len(source_start["Conditions"].get("Condition", [])),
            start_limiter=source_start["Limit"].get("@Enabled", "False") == "True",
            start_sorter=len(source_start.get("SortBy", source_start.get("DefinedSort", []))) > 0,
            final=len(self.tracks),
            final_description=source_final["Description"],
            final_included=len([p for p in source_final.get("ExceptionsInclude", "").split("|") if p]),
            final_excluded=len([p for p in source_final.get("Exceptions", "").split("|") if p]),
            final_compared=len(source_final["Conditions"].get("Condition", [])),
            final_limiter=source_final["Limit"].get("@Enabled", "False") == "True",
            final_sorter=len(source_final.get("SortBy", source_final.get("DefinedSort", []))) > 0,
        )

    def _update_xml_paths(self, xml: dict[str, Any]) -> None:
        """Update the stored, parsed XML object with valid include and exclude paths"""
        output = self.matcher.to_xml(
            items=self.tracks,
            original=self._original,
            path_mapper=""
        )
        merge_maps(source=xml, new=output, extend=False, overwrite=True)

    def _update_comparers(self, xml: dict[str, Any]) -> None:
        """Update the stored, parsed XML object with appropriately formatted comparer settings"""
        # TODO: implement comparison XML part updater (low priority)
        raise NotImplementedError

    def _update_limiter(self, xml: dict[str, Any]) -> None:
        """Update the stored, parsed XML object with appropriately formatted limiter settings"""
        # TODO: implement limit XML part updater (low priority)
        raise NotImplementedError

    def _update_sorter(self, xml: dict[str, Any]) -> None:
        """Update the stored, parsed XML object with appropriately formatted sorter settings"""
        # TODO: implement sort XML part updater (low priority)
        raise NotImplementedError

    def _save_xml(self) -> None:
        """Save XML representation of the playlist"""
        with open(self.path, 'w', encoding="utf-8") as file:
            xml_str = xmltodict.unparse(self.xml, pretty=True, short_empty_elements=True)
            file.write(xml_str.replace("/>", " />").replace('\t', '  '))


class XMLPlaylistParser(PrettyPrinter):

    __slots__ = ("path", "path_mapper",)

    # noinspection SpellCheckingInspection
    #: Map of MusicBee field name to Field enum
    field_name_map = {
        "None": None,
        "Title": Fields.TITLE,
        "ArtistPeople": Fields.ARTIST,
        "Album": Fields.ALBUM,  # album ignoring articles like 'the' and 'a' etc.
        "Album Artist": Fields.ALBUM_ARTIST,
        "TrackNo": Fields.TRACK_NUMBER,
        "TrackCount": Fields.TRACK_TOTAL,
        "GenreSplits": Fields.GENRES,
        "Year": Fields.YEAR,  # could also be 'YearOnly'?
        "BeatsPerMin": Fields.BPM,
        "DiscNo": Fields.DISC_NUMBER,
        "DiscCount": Fields.DISC_TOTAL,
        # "": Fields.COMPILATION,  # unmapped for compare
        "Comment": Fields.COMMENTS,
        "FileDuration": Fields.LENGTH,
        "Rating": Fields.RATING,
        # "ComposerPeople": Fields.COMPOSER,  # currently not supported by this program
        # "Conductor": Fields.CONDUCTOR,  # currently not supported by this program
        # "Publisher": Fields.PUBLISHER,  # currently not supported by this program
        "FilePath": Fields.PATH,
        "FolderName": Fields.FOLDER,
        "FileName": Fields.FILENAME,
        "FileExtension": Fields.EXT,
        # "": Fields.SIZE,  # unmapped for compare
        "FileKind": Fields.TYPE,
        "FileBitrate": Fields.BIT_RATE,
        "BitDepth": Fields.BIT_DEPTH,
        "FileSampleRate": Fields.SAMPLE_RATE,
        "FileChannels": Fields.CHANNELS,
        # "": Fields.DATE_CREATED,  # unmapped for compare
        "FileDateModified": Fields.DATE_MODIFIED,
        "FileDateAdded": Fields.DATE_ADDED,
        "FileLastPlayed": Fields.LAST_PLAYED,
        "FilePlayCount": Fields.PLAY_COUNT,
    }

    #: Settings for custom sort codes.
    custom_sort: dict[int, Mapping[Field, bool]] = {
        6: {
            Fields.ALBUM: False,
            Fields.DISC_NUMBER: False,
            Fields.TRACK_NUMBER: False,
            Fields.FILENAME: False
        }
        # TODO: implement field_code 78 - manual order according to the order of tracks found
        #  in the MusicBee library file for a given playlist.
    }

    @property
    def xml_smart_playlist(self) -> dict[str, Any]:
        """The smart playlist data part of the loaded XML playlist data"""
        return self.xml["SmartPlaylist"]

    @property
    def xml_source(self) -> dict[str, Any]:
        """The source data part of the loaded XML playlist data"""
        return self.xml_smart_playlist["Source"]

    def __init__(self, path: str, path_mapper: PathMapper = PathMapper()):
        self.path = path
        with open(self.path, "r", encoding="utf-8") as file:
            #: A map representation of the loaded XML playlist data
            self.xml: dict[str, Any] = xmltodict.parse(file.read())

        self.path_mapper = path_mapper

    def _get_comparer(self, xml: Mapping[str, Any]) -> Comparer:
        """
        Initialise and return a :py:class:`Comparer` from the relevant chunk of settings in ``xml`` playlist data.

        :param xml: The relevant chunk to generate a single :py:class:`Comparer` as found in
            the loaded XML object for this playlist.
            This function expects to be given only the XML part related to one Comparer condition.
        :return: The initialised :py:class:`Comparer`.
        """
        field_str = xml.get("@Field", "None")
        field: Field = self.field_name_map.get(field_str)
        if field is None:
            raise FieldError("Unrecognised field name", field=field_str)

        expected: tuple[str, ...] | None = tuple(v for k, v in xml.items() if k.startswith("@Value"))
        if len(expected) == 0 or expected[0] == "[playing track]":
            expected = None

        return Comparer(condition=xml["@Comparison"], expected=expected, field=field)

    def _get_xml_from_comparer(self, comparer: Comparer) -> dict[str, Any]:
        """Parse the given ``comparer`` to its XML playlist representation."""

    def get_matcher(self) -> AutoMatcher:
        """Initialise and return a :py:class:`FilterMatcher` object from loaded XML playlist data."""
        # tracks to include/exclude even if they meet/don't meet match compare conditions
        include_str: str = self.xml_source.get("ExceptionsInclude") or ""
        include = self.path_mapper.map_many(set(include_str.split("|")), check_existence=True)
        exclude_str: str = self.xml_source.get("Exceptions") or ""
        exclude = self.path_mapper.map_many(set(exclude_str.split("|")), check_existence=True)

        comparers: dict[Comparer, tuple[bool, FilterComparers]] = {}
        for condition in to_collection(self.xml_source["Conditions"]["Condition"]):
            if any(key in condition for key in {"And", "Or"}):
                combine = "And" in condition
                conditions = condition["And" if combine else "Or"]
                sub_filter = FilterComparers(
                    comparers=[self._get_comparer(sub) for sub in to_collection(conditions["Condition"])],
                    match_all=conditions["@CombineMethod"] == "All"
                )
            else:
                combine = False
                sub_filter = FilterComparers()

            comparers[self._get_comparer(xml=condition)] = (combine, sub_filter)

        if len(comparers) == 1 and not next(iter(comparers.values()))[1].ready:
            # when user has not set an explicit comparer, a single empty 'allow all' comparer is assigned
            # check for this 'allow all' comparer and remove it if present to speed up comparisons
            c = next(iter(comparers))
            if "contains" in c.condition.casefold() and len(c.expected) == 1 and not c.expected[0]:
                comparers = {}

        filter_include = FilterDefinedList[LocalTrack](values=[path.casefold() for path in include])
        filter_exclude = FilterDefinedList[LocalTrack](values=[path.casefold() for path in exclude])
        filter_compare = FilterComparers[LocalTrack](
            comparers, match_all=self.xml_source["Conditions"]["@CombineMethod"] == "All"
        )

        filter_include.transform = lambda x: self.path_mapper.map(x, check_existence=False).casefold()
        filter_exclude.transform = lambda x: self.path_mapper.map(x, check_existence=False).casefold()

        group_by_value = self._pascal_to_snake(self.xml_smart_playlist["@GroupBy"])
        group_by = None if group_by_value == "track" else TagFields.from_name(group_by_value)[0]

        return FilterMatcher(
            include=filter_include, exclude=filter_exclude, comparers=filter_compare, group_by=group_by
        )

    def _get_xml_from_matcher(
            self, matcher: FilterMatcher, items: list[File], original: list[File | MusifyItem]
    ) -> Mapping[str, Any]:
        """
        Parse the given ``matcher`` to its XML playlist representation.

        :param items: The items to export.
        :param original: The original items matched from the settings in the original file.
        :return: A map representing the values to be exported to the XML playlist file.
        """
        if not isinstance(matcher.include, FilterDefinedList) and not isinstance(matcher.exclude, FilterDefinedList):
            matcher.logger.warning(
                "Cannot export this filter to XML: Include and Exclude settings must both be list filters"
            )
            return {}

        items_mapped: Mapping[str, File] = {item.path.casefold(): item for item in items}

        if matcher.comparers:
            # match again on current conditions to check for differences from original list
            # which ensures that the paths included in the XML output
            # do not include paths that match any of the comparer or group_by conditions

            # copy the list of tracks as the sorter will modify the list order
            original = original.copy()
            # get the last played track as reference in case comparer is looking for the playing tracks as reference
            ItemSorter.sort_by_field(original, field=Fields.LAST_PLAYED, reverse=True)

            matched_mapped = {
                item.path.casefold(): item for item in matcher.comparers(original, reference=original[0])
            } if matcher.comparers.ready else {}
            # noinspection PyProtectedMember
            matched_mapped |= {
                item.path.casefold(): item for item in matcher._get_group_by_results(original, matched_mapped.values())
            }

            # get new include/exclude paths based on the leftovers after matching on comparers and group_by settings
            matcher.exclude.values = list(matched_mapped.keys() - items_mapped)
            matcher.include.values = [v for v in list(items_mapped - matched_mapped.keys()) if v not in matcher.exclude]
        else:
            matched_mapped = items_mapped

        include_items = tuple(items_mapped[path] for path in matcher.include if path in items_mapped)
        exclude_items = tuple(matched_mapped[path] for path in matcher.exclude if path in matched_mapped)

        source = {}
        if len(include_items) > 0:  # assign include paths to XML object
            include_paths = self.path_mapper.unmap_many(include_items, check_existence=False)
            source["ExceptionsInclude"] = "|".join(include_paths).replace("&", "&amp;")
        if len(exclude_items) > 0:  # assign exclude paths to XML object
            exclude_paths = self.path_mapper.unmap_many(exclude_items, check_existence=False)
            source["Exceptions"] = "|".join(exclude_paths).replace("&", "&amp;")

        return {
            "SmartPlaylist": {
                "@GroupBy": matcher.group_by.name.lower() if matcher.group_by else "track",
                "Source": source,
            }
        }

    def get_limiter(self) -> ItemLimiter | None:
        """Initialise and return a :py:class:`ItemLimiter` object from loaded XML playlist data."""
        conditions: Mapping[str, str] = self.xml_source["Limit"]
        if conditions["@Enabled"] != "True":
            return
        # filter_duplicates = conditions["@FilterDuplicates"] == "True"

        # MusicBee appears to have some extra allowance on time and byte limits of ~1.25
        return ItemLimiter(
            limit=int(conditions["@Count"]),
            on=LimitType.from_name(conditions["@Type"])[0],
            sorted_by=conditions["@SelectedBy"],
            allowance=1.25
        )

    def _get_xml_from_limiter(self, limiter: ItemLimiter) -> dict[str, Any]:
        """Parse the given ``limiter`` to its XML playlist representation."""

    def get_sorter(self) -> ItemSorter | None:
        """Initialise and return a :py:class:`ItemLimiter` object from loaded XML playlist data."""
        fields: Sequence[Field] | Mapping[Field | bool] = ()

        if "SortBy" in self.xml_source:
            field_code = int(self.xml_source["SortBy"].get("@Field", 0))
        elif "DefinedSort" in self.xml_source:
            field_code = int(self.xml_source["DefinedSort"]["@Id"])
        else:
            return

        if field_code in self.custom_sort:
            fields = self.custom_sort[field_code]
            return ItemSorter(fields=fields)
        elif field_code != 78:
            field = Fields.from_value(field_code)[0]

            if "SortBy" in self.xml_source:
                fields = {field: self.xml_source["SortBy"]["@Order"] == "Descending"}
            elif "DefinedSort" in self.xml_source:
                fields = [field]
            else:
                raise NotImplementedError("Sort type in XML not recognised")

        shuffle_mode_value = self._pascal_to_snake(self.xml_smart_playlist["@ShuffleMode"])
        if not fields and shuffle_mode_value != "none":
            shuffle_mode = ShuffleMode.from_name(shuffle_mode_value)[0]
            shuffle_weight = float(self.xml_smart_playlist.get("@ShuffleSameArtistWeight", 0))

            return ItemSorter(fields=fields, shuffle_mode=shuffle_mode, shuffle_weight=shuffle_weight)
        return ItemSorter(fields=fields or self.custom_sort[6])  # TODO: workaround - see cls.custom_sort

    def _get_xml_from_sorter(self, sorter: ItemSorter) -> dict[str, Any]:
        """Parse the given ``sorter`` to its XML playlist representation."""

    def as_dict(self):
        return {"path": self.path, "path_mapper": self.path_mapper}

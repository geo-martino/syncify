from __future__ import annotations

import json
import logging.config
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping, Generator, Collection
from dataclasses import dataclass
from datetime import datetime
from os.path import isabs, join, dirname, splitext, exists
from typing import Any, Self, get_args, Iterable

import yaml

from syncify.abstract import NamedObject
from syncify.abstract.enums import TagField
from syncify.abstract.misc import PrettyPrinter
from syncify.abstract.object import Library
from syncify.exception import ConfigError
from syncify.fields import LocalTrackField
from syncify.local.exception import InvalidFileType
from syncify.local.library import MusicBee, LocalLibrary
from syncify.remote.api import RemoteAPI
from syncify.remote.library import RemoteObject
from syncify.remote.library.library import RemoteLibrary
from syncify.remote.library.object import PLAYLIST_SYNC_KINDS
from syncify.remote.processors.check import RemoteItemChecker, ALLOW_KARAOKE_DEFAULT
from syncify.remote.processors.search import RemoteItemSearcher
from syncify.remote.processors.wrangle import RemoteDataWrangler
from syncify.spotify import SPOTIFY_SOURCE_NAME
from syncify.spotify.api import SpotifyAPI
from syncify.spotify.library import SpotifyObject
from syncify.spotify.library.library import SpotifyLibrary
from syncify.spotify.processors.processors import SpotifyItemChecker, SpotifyItemSearcher
from syncify.spotify.processors.wrangle import SpotifyDataWrangler
from syncify.utils.helpers import to_collection
from syncify.utils.logger import LOGGING_DT_FORMAT, SyncifyLogger


@dataclass
class RemoteClasses:
    """Stores the key classes for a remote source"""
    name: str
    api: type[RemoteAPI]
    wrangler: type[RemoteDataWrangler]
    object: type[RemoteObject]
    library: type[RemoteLibrary]
    checker: type[RemoteItemChecker]
    searcher: type[RemoteItemSearcher]


# map of the names of all supported remote sources and their associated implementations
REMOTE_CONFIG: Mapping[str, RemoteClasses] = {
    SPOTIFY_SOURCE_NAME: RemoteClasses(
        name=SPOTIFY_SOURCE_NAME,
        api=SpotifyAPI,
        wrangler=SpotifyDataWrangler,
        object=SpotifyObject,
        library=SpotifyLibrary,
        checker=SpotifyItemChecker,
        searcher=SpotifyItemSearcher,
    )
}


def _get_local_track_tags(tags: Any) -> tuple[LocalTrackField, ...]:
    values = to_collection(tags, tuple)
    tags = LocalTrackField.to_tags(LocalTrackField.from_name(*values) if values else LocalTrackField.all())
    order = [field for field in LocalTrackField.all()]
    return tuple(sorted(LocalTrackField.from_name(*tags), key=lambda x: order.index(x)))


class Config(PrettyPrinter):
    """
    Set up config and provide framework for initialising various objects
    needed for the main functionality of the program from a given config file at ``path``.

    The following options are in place for configuration values:

    - `DEFAULT`: When a value is not found, a default value will be used.
    - `REQUIRED`: The configuration will fail if this value is not given. Only applies when the key is called.
    - `OPTIONAL`: This value does not need to be set and ``None`` will be set when this is the case.
        The configuration will not fail if this value is not given.

    Sub-configs have an ``override`` parameter that can be set using the ``override`` key in initial config block.
    When override is True and ``config`` given, override loaded config from the file with values in ``config``
    only using loaded values when values are not present in given ``config``.
    When override is False and ``config`` given, loaded config takes priority
    and given ``config`` values are only used when values are not present in the file.
    By default, always keep the current settings.

    :param path: Path of the config file to use. If relative path given, appends package root path.
    """

    def __init__(self, path: str = "config.yml"):
        self.run_dt = datetime.now()
        self._package_root = dirname(dirname(dirname(__file__)))
        self.config_path = self._make_path_absolute(path)
        
        self._cfg: dict[Any, Any] = {}
        
        # general operation settings
        self._output_folder: str | None = None
        self._dry_run: bool | None = None
        self._pause_message: str | None = None
        
        # core settings
        self.local: dict[str, ConfigLocal] = {}
        self.remote: dict[str, ConfigRemote] = {}

        # specific operation settings
        self.filter: ConfigFilter | None = None
        self.reports: ConfigReports | None = None
    
    def load(self, key: str | None = None, fail_on_missing: bool = True):
        """
        Load old config from the config file at the given ``key`` respecting ``override`` rules.

        :param key: The key to pull config from within the file.
            Used as the parent key to use to pull the required configuration from the config file.
            If not given, use the root values in the config file.
        :param fail_on_missing: Raise exception if the given key cannot be found.
        """
        new = self.__class__.__new__(self.__class__)
        cfg = self._load_config(key, fail_on_missing=fail_on_missing)
        new._cfg = cfg
        keep = not cfg.get("override", False)  # default = keep the current settings

        self._output_folder: str | None = self.output_folder if keep else None
        self._dry_run: bool | None = self.dry_run if keep else None
        self._pause_message = self.pause_message if keep else None

        for name, settings in cfg.get("local", {}).items():
            match settings["kind"]:
                case "musicbee":
                    library = ConfigMusicBee(file=settings, old=self.local.get(name), keep=keep)
                case _:
                    library = ConfigLocal(file=settings, old=self.local.get(name), keep=keep)

            self.local[name] = library

        for name, settings in cfg.get("remote", {}).items():
            kind = settings["kind"]
            match kind:  # remap certain source kinds to expected values
                case "spotify":
                    kind = SPOTIFY_SOURCE_NAME

            library = ConfigRemote(name=kind, file=settings, old=self.remote.get(name), keep=keep)
            # noinspection PyProtectedMember
            assert library.api._api is None  # ensure api has not already been instantiated

            if not exists(library.api.token_path) and not isabs(library.api.token_path):
                library.api._token_path = join(dirname(self.output_folder), library.api.token_path)
            if not exists(library.api.cache_path) and not isabs(library.api.cache_path):
                library.api._cache_path = join(dirname(self.output_folder), library.api.cache_path)

            self.remote[name] = library

        self.filter = ConfigFilter(file=cfg, old=self.filter, keep=keep)
        self.reports = ConfigReports(file=cfg, old=self.reports, keep=keep)

        self._cfg = cfg

    def _make_path_absolute(self, path: str) -> str:
        """Append the package root to any relative path to make it an absolute path. Do nothing if path is absolute."""
        if not isabs(path):
            path = join(self._package_root, path)
        return path

    def _load_config(self, key: str | None = None, fail_on_missing: bool = True) -> dict[Any, Any]:
        """
        Load the config file

        :param key: The key to pull config from within the file.
        :param fail_on_missing: Raise exception if the given key cannot be found.
        :return: The config file.
        :raise InvalidFileType: When the given config file is not of the correct type.
        :raise ConfigError: When the given key cannot be found and ``fail_on_missing`` is True.
        """
        if splitext(self.config_path)[1].casefold() not in [".yml", ".yaml"]:
            raise InvalidFileType(f"Unrecognised file type: {self.config_path}")
        elif not exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, 'r') as file:
            config = yaml.full_load(file)
        if fail_on_missing and key and key not in config:
            raise ConfigError("Unrecognised config name: {key} | Available: {value}", key=key, value=config)

        return config.get(key, config)

    def load_log_config(self, path: str = "logging.yml") -> None:
        """
        Load logging config from the JSON or YAML file at the given ``path``.
        If relative path given, appends package root path.
        """
        if not isabs(path):
            path = join(self._package_root, path)

        ext = splitext(path)[1].casefold()
        allowed = {".yml", ".yaml", ".json"}
        if ext not in allowed:
            raise ConfigError(
                "Unrecognised log config file type: {key}. Valid: {value}", key=ext, value=allowed
            )

        with open(path, "r") as file:
            if ext in {".yml", ".yaml"}:
                log_config = yaml.full_load(file)
            elif ext in {".json"}:
                log_config = json.load(file)

        SyncifyLogger.compact = log_config.pop("compact", False)

        for formatter in log_config["formatters"].values():  # ensure ANSI colour codes in format are recognised
            formatter["format"] = formatter["format"].replace(r"\33", "\33")

        logging.config.dictConfig(log_config)

    ###########################################################################
    ## General
    ###########################################################################
    @property
    def output_folder(self) -> str:
        """`DEFAULT = '<package_root>/_data'` | The output folder for saving diagnostic data"""
        if self._output_folder is not None:
            return self._output_folder

        parent_folder = self._make_path_absolute(self._cfg.get("output", "_data"))
        self._output_folder = join(parent_folder, self.run_dt.strftime(LOGGING_DT_FORMAT))
        os.makedirs(self._output_folder, exist_ok=True)
        return self._output_folder

    @property
    def dry_run(self) -> bool:
        """`DEFAULT = True` | Whether this run is a dry run i.e. don't write out where possible"""
        if self._dry_run is not None:
            return self._dry_run

        self._dry_run = self._cfg.get("dry_run", True)
        return self._dry_run

    @property
    def pause_message(self) -> str:
        """`DEFAULT = 'Pausing, hit return to continue...'` | The message to display when running the pause operation"""
        if self._pause_message is not None:
            return self._pause_message

        default = "Pausing, hit return to continue..."
        self._pause_message = self._cfg["message"].strip() if "message" in self._cfg else default
        return self._pause_message

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_time": self.run_dt,
            "config_path": self.config_path,
            "output_folder": self.output_folder,
            "dry_run": self.dry_run,
            "local": {name: config for name, config in self.local.items()},
            "remote": {name: config for name, config in self.remote.items()},
            "filter": self.filter,
            "pause_message": self.pause_message,
            "reports": self.reports,
        }


###########################################################################
## Shared
###########################################################################
class ConfigLibrary(PrettyPrinter, ABC):
    """Set the settings for a library from a config file and terminal arguments."""

    def __init__(self):
        self.library_loaded: bool = False  # marks whether initial loading of the library has happened

    @property
    @abstractmethod
    def library(self) -> Library:
        """An initialised library"""
        raise NotImplementedError

    def as_dict(self) -> dict[str, Any]:
        return {"library": self.library}


class ConfigFilter(PrettyPrinter):
    """
    Set the settings for granular filtering from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        self._cfg = file.get("filter", file)

        self.include = self.ConfigFilterOptions(
            name="include", file=self._cfg, old=old.include if old is not None else None, keep=keep, include=True,
        )
        self.exclude = self.ConfigFilterOptions(
            name="exclude", file=self._cfg, old=old.exclude if old is not None else None, keep=keep, include=False,
        )

    def process[T: str | NamedObject](self, values: Iterable[T]) -> tuple[T, ...]:
        """Filter down ``values`` that match this filter's settings from"""
        return self.exclude.process(self.include.process(values))

    def as_dict(self) -> dict[str, Any]:
        return {
            "include": self.include,
            "exclude": self.exclude,
        }

    class ConfigFilterOptions(PrettyPrinter):
        """
        Set the settings for filter options from a config file and terminal arguments.
        See :py:class:`Config` for more documentation regarding initialisation and operation.

        :param name: The key to load filter options for.
            Used as the parent key to use to pull the required configuration from the config file.
        :param file: The loaded config from the config file.
        """

        def __init__(
                self, name: str, file: dict[Any, Any], old: Self | None = None, keep: bool = False, include: bool = True
        ):
            self._cfg: dict | list = file.get(name, {})

            self.include: bool = include

            self.available: Collection[str] | None = old.available if old is not None and keep else None
            self._values: tuple[str, ...] | None = old.values if old is not None and keep else None
            self._prefix: str | None = old.prefix if old is not None and keep else None
            self._start: str | None = old.start if old is not None and keep else None
            self._stop: str | None = old.stop if old is not None and keep else None

            # TODO: this needs to be replicated everywhere for overrides to work correctly
            if old is not None and not self.values:
                self._values = old.values
            if old is not None and not self.prefix:
                self._prefix = old.prefix
            if old is not None and not self.start:
                self._start = old.start
            if old is not None and not self.stop:
                self._stop = old.stop

        def process[T: str | NamedObject](self, values: Iterable[T]) -> tuple[T, ...]:
            """Returns all strings from ``values`` that match this filter's settings"""
            def name(value: T) -> str:
                """Get the name from a given ``value`` based on the object type"""
                return value.name if isinstance(value, NamedObject) else value

            if self.prefix:
                if self.include:
                    values = [value for value in values if name(value).startswith(self.prefix)]
                else:
                    values = [value for value in values if not name(value).startswith(self.prefix)]

            if self.start:
                if self.include:
                    values = [value for value in values if name(value) >= self.start]
                else:
                    values = [value for value in values if name(value) <= self.start]

            if self.stop:
                if self.include:
                    values = [value for value in values if name(value) <= self.stop]
                else:
                    values = [value for value in values if name(value) <= self.stop]

            return to_collection(values, tuple)

        @property
        def values(self) -> tuple[str, ...] | None:
            """
            `DEFAULT = ()` | The filtered values.
            Filters ``available`` values based on ``prefix``, ``start``, and ``stop`` as applicable.
            ``available`` values are taken from the config if the config is a list of strings.
            """
            if self._values is not None:
                return self._values

            is_str_collection = isinstance(self._cfg, Collection) and all(isinstance(v, str) for v in self._cfg)
            if self.available:
                values = self.available
            elif not isinstance(self._cfg, Mapping) and is_str_collection:
                values = self._cfg
            elif isinstance(self._cfg, Mapping) and self._cfg.get("values"):
                values = self._cfg["values"]
            else:
                values = ()

            self._values = self.process(values)
            return self._values

        @property
        def prefix(self) -> str | None:
            """`OPTIONAL` | The prefix of the items to match on."""
            if self._prefix is not None:
                return self._prefix
            self._prefix = self._cfg.get("prefix") if isinstance(self._cfg, Mapping) else None
            return self._prefix

        @property
        def start(self) -> str | None:
            """`OPTIONAL` | The exact name for the first item to match on."""
            if self._start is not None:
                return self._start
            self._start = self._cfg.get("start") if isinstance(self._cfg, Mapping) else None
            return self._start

        @property
        def stop(self) -> str | None:
            """`OPTIONAL` | The exact name for the last item to match on."""
            if self._stop is not None:
                return self._stop

            self._stop = self._cfg.get("stop") if isinstance(self._cfg, Mapping) else None
            return self._stop

        def __iter__(self):
            return (value for value in self.values)

        def __len__(self):
            return len(self.values)

        def as_dict(self) -> dict[str, Any]:
            return {
                "include": self.include,
                "values": self.values,
                "available": self.available,
                "prefix": self.prefix,
                "start": self.start,
                "stop": self.stop,
            }


class ConfigPlaylists(ConfigFilter):
    """
    Set the settings for the playlists from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__(file=file.get("playlists", {}), old=old, keep=keep)

        self._filter: dict[str, tuple[str, ...]] | None = old.filter if old is not None and keep else None

    @property
    def filter(self) -> dict[str, tuple[str, ...]]:
        """`DEFAULT = {}` | Tags and values of items to filter out of every playlist when loading"""
        if self._filter is not None:
            return self._filter

        self._filter = {}
        for tag, values in self._cfg.get("filter", {}).items():
            if tag not in TagField.__tags__ or not values:
                continue
            self._filter[tag] = to_collection(values, tuple)

        return self._filter

    def as_dict(self) -> dict[str, Any]:
        return super().as_dict() | {"filter": self.filter}


class ConfigReportBase(PrettyPrinter):
    """
    Base class for settings reports settings.

    :param file: The loaded config from the config file.
    """
    def __init__(self, name: str, file: dict[Any, Any]):
        self._cfg = file.get(name, {})
        self.name = name
        self.enabled = self._cfg.get("enabled", True)

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled}


class ConfigReports(PrettyPrinter, Iterable[ConfigReportBase]):
    """
    Set the settings for all reports from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """
    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        self._cfg = file.get("reports", {})

        self.library_differences = ConfigLibraryDifferences(
            file=self._cfg, _=old.library_differences if old is not None else None, __=keep
        )
        self.missing_tags = ConfigMissingTags(
            file=self._cfg, old=old.missing_tags if old is not None else None, keep=keep
        )

        self.all: tuple[ConfigReportBase, ...] = (self.library_differences, self.missing_tags)

    def __iter__(self) -> Generator[[ConfigReportBase], None, None]:
        return (report for report in self.all)

    def as_dict(self) -> dict[str, Any]:
        return {report.name: report for report in self.all}


class ConfigLibraryDifferences(ConfigReportBase):
    """
    Set the settings for the library differences report from a config file and terminal arguments.

    :param file: The loaded config from the config file.
    """
    def __init__(self, file: dict[Any, Any], _: Self | None = None, __: bool = False):
        super().__init__(name="library_differences", file=file)


class ConfigMissingTags(ConfigReportBase):
    """
    Set the settings for the missing tags report from a config file and terminal arguments.

    :param file: The loaded config from the config file.
    """

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__(name="missing_tags", file=file)

        self._tags: tuple[LocalTrackField, ...] | None = old.tags if old is not None and keep else None
        self._match_all: bool | None = old.match_all if old is not None and keep else None

    @property
    def tags(self) -> tuple[LocalTrackField, ...]:
        """`DEFAULT = (<all LocalTrackFields>)` | The tags to be updated."""
        if self._tags is not None:
            return self._tags
        self._tags = _get_local_track_tags(self._cfg.get("tags"))
        return self._tags

    @property
    def match_all(self) -> bool:
        """
        `DEFAULT = True` | When True, consider a track as having missing tags only if it is missing all the given tags.
        """
        if self._match_all is not None:
            return self._match_all
        self._match_all = self._cfg.get("match_all", True)
        return self._match_all

    def as_dict(self) -> dict[str, Any]:
        return super().as_dict() | {
            "tags": [t for tag in self.tags for t in tag.to_tag()],
            "match_all": self.match_all,
        }


###########################################################################
## Local
###########################################################################
class ConfigLocal(ConfigLibrary):
    """
    Set the settings for the local functionality of the program from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """

    @property
    def _platform_key(self) -> str:
        platform_map = {"win32": "win", "linux": "lin", "darwin": "mac"}
        return platform_map[sys.platform]

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__()
        self._cfg = file

        self._library_folder: str | None = old.library_folder if old is not None and keep else None
        self._playlist_folder: str | None = old.playlist_folder if old is not None and keep else None
        self._other_folders: tuple[str, ...] | None = old.other_folders if old is not None and keep else None

        self._library = old.library if old and keep else None

        self.playlists = ConfigPlaylists(file=self._cfg, old=old.playlists if old is not None else None, keep=keep)
        self.update = self.ConfigUpdateTags(file=self._cfg, old=old.update if old is not None else None, keep=keep)

    @property
    def library(self) -> LocalLibrary:
        if self._library is not None and isinstance(self._library, LocalLibrary):
            return self._library

        self._library = LocalLibrary(
            library_folder=self.library_folder,
            playlist_folder=self.playlist_folder,
            other_folders=self.other_folders,
            include=self.playlists.include,
            exclude=self.playlists.exclude,
        )
        return self._library

    @property
    def _cfg_paths(self) -> dict[Any, Any]:
        return self._cfg.get("paths", {})

    @property
    def library_folder(self) -> str:
        """`REQUIRED` | The path of the local library folder"""
        if self._library_folder is not None:
            return self._library_folder

        if isinstance(self._cfg_paths.get("library"), str):
            self._library_folder = self._cfg_paths["library"]
            return self._library_folder
        elif not isinstance(self._cfg_paths.get("library"), dict):
            raise ConfigError("Config not found", key=["local", "paths", "library"], value=self._cfg_paths)

        # assume platform sub-keys
        value = self._cfg_paths["library"].get(self._platform_key)
        if not value:
            raise ConfigError(
                "Library folder for the current platform not given",
                key=["local", "paths", "library", self._platform_key],
                value=self._cfg_paths["library"]
            )

        self._library_folder = value
        return self._library_folder

    @property
    def playlist_folder(self) -> str | None:
        """`OPTIONAL` | The path of the playlist folder."""
        if self._playlist_folder is not None:
            return self._playlist_folder
        self._playlist_folder = self._cfg_paths.get("playlists")
        return self._playlist_folder

    @property
    def other_folders(self) -> tuple[str, ...]:
        """`DEFAULT = ()` | The paths of other folder to use for replacement when processing local libraries"""
        if self._other_folders is not None:
            return self._other_folders
        self._other_folders = to_collection(self._cfg_paths.get("other"), tuple) or ()
        return self._other_folders

    def as_dict(self) -> dict[str, Any]:
        return {
            "library_folder": self.library_folder,
            "playlist_folder": self.playlist_folder,
            "other_folders": self.other_folders,
            "playlists": self.playlists,
            "update": self.update,
        } | super().as_dict()

    class ConfigUpdateTags(PrettyPrinter):
        """
        Set the settings for the playlists from a config file and terminal arguments.
        See :py:class:`Config` for more documentation regarding initialisation and operation.

        :param file: The loaded config from the config file.
        """

        def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
            self._cfg = file.get("update", {})
            self._tags: tuple[LocalTrackField, ...] | None = old.tags if old is not None and keep else None
            self._replace: bool | None = old.replace if old is not None and keep else None

        @property
        def tags(self) -> tuple[LocalTrackField, ...]:
            """`DEFAULT = (<all LocalTrackFields>)` | The tags to be updated."""
            if self._tags is not None:
                return self._tags
            self._tags = _get_local_track_tags(self._cfg.get("tags"))
            return self._tags

        @property
        def replace(self) -> bool:
            """`OPTIONAL` | Destructively replace tags in each file."""
            if self._replace is not None:
                return self._replace
            self._replace = self._cfg.get("replace")
            return self._replace

        def as_dict(self) -> dict[str, Any]:
            return {
                "tags": [t for tag in self.tags for t in tag.to_tag()],
                "replace": self.replace,
            }


class ConfigMusicBee(ConfigLocal):
    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__(file=file, old=old, keep=keep)

        self._musicbee_folder: str | None = old.musicbee_folder if old is not None and keep else None

    @property
    def library(self) -> LocalLibrary:
        if self._library is not None and isinstance(self._library, MusicBee):
            return self._library

        self._library = MusicBee(
            musicbee_folder=self.musicbee_folder,
            library_folder=self.library_folder,
            playlist_folder=self.playlist_folder,
            other_folders=self.other_folders,
            include=self.playlists.include,
            exclude=self.playlists.exclude,
        )
        return self._library

    @property
    def musicbee_folder(self) -> str | None:
        """`OPTIONAL` | The path of the MusicBee library folder."""
        if self._musicbee_folder is not None:
            return self._musicbee_folder
        self._musicbee_folder = self._cfg_paths.get("musicbee")
        return self._musicbee_folder

    def as_dict(self) -> dict[str, Any]:
        return {"musicbee_folder": self.musicbee_folder} | super().as_dict()


###########################################################################
## Remote
###########################################################################
class ConfigRemote(ConfigLibrary):
    """
    Set the settings for the remote functionality of the program from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """

    def __init__(self, name: str, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__()
        self._cfg = file

        api_config_map = {
            SPOTIFY_SOURCE_NAME: ConfigSpotify
        }

        self.name = name
        if self.name not in api_config_map:
            raise ConfigError(
                "No configuration found for this remote source type '{key}'. Available: {value}. "
                f"Valid source types: {", ".join(api_config_map)}",
                key=self._cfg["kind"], value=file,
            )

        replace_api = old and isinstance(old.api, api_config_map[self.name])
        self.api: ConfigAPI = api_config_map[self.name](file=self._cfg, old=old.api if replace_api else None, keep=keep)

        self._library = old.library if old and keep else None

        replace_wrangler = old and keep and isinstance(old.wrangler, REMOTE_CONFIG[self.name].wrangler)
        self._wrangler = old.wrangler if replace_wrangler else None
        replace_checker = old and keep and isinstance(old.checker, REMOTE_CONFIG[self.name].checker)
        self._checker = old.checker if replace_checker else None
        replace_searcher = old and keep and isinstance(old.searcher, REMOTE_CONFIG[self.name].searcher)
        self._searcher = old.searcher if replace_searcher else None

        self.playlists = self.ConfigPlaylists(file=self._cfg, old=old.playlists if old is not None else None, keep=keep)

    @property
    def library(self) -> RemoteLibrary:
        if self._library is not None and isinstance(self._library, REMOTE_CONFIG[self.name].library):
            return self._library

        self._library = REMOTE_CONFIG[self.name].library(
            api=self.api.api,
            include=self.playlists.include,
            exclude=self.playlists.exclude,
            use_cache=self.api.use_cache,
        )
        return self._library

    @property
    def wrangler(self) -> RemoteDataWrangler:
        """An initialised remote wrangler"""
        if self._wrangler is not None and isinstance(self._wrangler, REMOTE_CONFIG[self.name].wrangler):
            return self._wrangler
        self._wrangler = REMOTE_CONFIG[self.name].wrangler()
        return self._wrangler

    @property
    def checker(self) -> RemoteItemChecker:
        """An initialised remote wrangler"""
        if self._checker is not None and isinstance(self._checker, REMOTE_CONFIG[self.name].checker):
            return self._checker

        interval = self._cfg.get("interval", 10)
        allow_karaoke = self._cfg.get("allow_karaoke", ALLOW_KARAOKE_DEFAULT)
        self._checker = REMOTE_CONFIG[self.name].checker(
            api=self.api.api, interval=interval, allow_karaoke=allow_karaoke
        )
        return self._checker

    @property
    def searcher(self) -> RemoteItemSearcher:
        """An initialised remote wrangler"""
        if self._searcher is not None and isinstance(self._checker, REMOTE_CONFIG[self.name].searcher):
            return self._searcher

        self._searcher = REMOTE_CONFIG[self.name].searcher(api=self.api.api, use_cache=self.api.use_cache)
        return self._searcher

    def as_dict(self) -> dict[str, Any]:
        return {
            "api": self.api,
            "wrangler": bool(self.wrangler.remote_source),  # just check it loaded
            "checker": self.checker,
            "searcher": self.searcher,
            "playlists": self.playlists.as_dict(),
        } | super().as_dict()

    class ConfigPlaylists(ConfigPlaylists):
        """
        Set the settings for processing remote playlists from a config file and terminal arguments.
        See :py:class:`Config` for more documentation regarding initialisation and operation.

        :param file: The loaded config from the config file.
        """

        def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
            super().__init__(file=file, old=old, keep=keep)

            self.sync = self.ConfigPlaylistsSync(file=self._cfg, old=old.sync if old is not None else None, keep=keep)

        def as_dict(self) -> dict[str, Any]:
            return super().as_dict() | {"sync": self.sync}

        class ConfigPlaylistsSync(PrettyPrinter):
            """
            Set the settings for synchronising remote playlists from a config file and terminal arguments.
            See :py:class:`Config` for more documentation regarding initialisation and operation.

            :param file: The loaded config from the config file.
            """

            def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
                self._cfg = file.get("sync", {})

                self._kind: str | None = old.kind if old is not None and keep else None
                self._reload: bool | None = old.reload if old is not None and keep else None

            @property
            def kind(self) -> str:
                """`DEFAULT = 'old'` | Sync option for the remote playlist."""
                if self._kind is not None:
                    return self._kind

                valid = get_args(PLAYLIST_SYNC_KINDS)
                kind = self._cfg.get("kind", "old")
                if kind not in valid:
                    raise ConfigError("Invalid kind given: {key}. Allowed values: {value}", key=kind, value=valid)

                self._kind = kind
                return self._kind

            @property
            def reload(self) -> bool:
                """`DEFAULT = True` | Reload playlists after synchronisation."""
                if self._reload is not None:
                    return self._reload
                self._reload = self._cfg.get("reload", True)
                return self._reload

            def as_dict(self) -> dict[str, Any]:
                return {
                    "kind": self.kind,
                    "reload": self.reload,
                }


class ConfigAPI(PrettyPrinter, ABC):
    """
    Set the settings for the remote API from a config file and terminal arguments.
    See :py:class:`Config` for more documentation regarding initialisation and operation.

    :param file: The loaded config from the config file.
    """

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        # marks whether initial authorisation of the API has happened
        self.loaded: bool = old.loaded if old is not None and keep else False
        self._cfg = file.get("api", {})

        self._api: RemoteAPI | None = old.api if old is not None and keep else None
        self._token_path: str | None = old.token_path if old is not None and keep else None
        self._cache_path: str | None = old.cache_path if old is not None and keep else None
        self._use_cache: bool | None = old.use_cache if old is not None and keep else None

    @property
    @abstractmethod
    def api(self) -> RemoteAPI:
        """Set up and return a valid API session for this remote source type."""
        raise NotImplementedError

    @property
    def token_path(self) -> str:
        """`DEFAULT = 'token.json'` | The client secret to use when authorising access to the API."""
        if self._token_path is not None:
            return self._token_path
        self._token_path = self._cfg.get("token_path", "token.json")
        return self._token_path

    @property
    def cache_path(self) -> str:
        """`DEFAULT = '.api_cache'` | The path of the cache to use when using cached requests for the API"""
        if self._cache_path is not None:
            return self._cache_path
        self._cache_path = self._cfg.get("cache_path", '.api_cache')
        return self._cache_path

    @property
    def use_cache(self) -> bool:
        """
        `DEFAULT = True` | When True, use requests cache where possible when making API calls.
        When False, always make calls to the API, refreshing any cached data in the process.
        """
        if self._use_cache is not None:
            return self._use_cache
        self._use_cache = self._cfg.get("use_cache", True)
        return self._use_cache

    def as_dict(self) -> dict[str, Any]:
        return super().as_dict() | {
            "token_path": self.token_path,
            "use_cache": self.use_cache,
        }


class ConfigSpotify(ConfigAPI):

    def __init__(self, file: dict[Any, Any], old: Self | None = None, keep: bool = False):
        super().__init__(file=file, old=old, keep=keep)

        self._client_id: str | None = old.client_id if old is not None and keep else None
        self._client_secret: str | None = old.client_secret if old is not None and keep else None
        self._scopes: tuple[str, ...] | None = old.scopes if old is not None and keep else None
        self._user_auth: bool | None = old.user_auth if old is not None and keep else None

    @property
    def client_id(self) -> str | None:
        """`OPTIONAL` | The client ID to use when authorising access to the API."""
        if self._client_id is not None:
            return self._client_id
        self._client_id = self._cfg.get("client_id")
        return self._client_id

    @property
    def client_secret(self) -> str | None:
        """`OPTIONAL` | The client secret to use when authorising access to the API."""
        if self._client_secret is not None:
            return self._client_secret
        self._client_secret = self._cfg.get("client_secret")
        return self._client_secret

    @property
    def scopes(self) -> tuple[str, ...]:
        """`DEFAULT = ()` | The scopes to use when authorising access to the API."""
        if self._scopes is not None:
            return self._scopes
        self._scopes = to_collection(self._cfg.get("scopes"), tuple) or ()
        return self._scopes

    @property
    def user_auth(self) -> bool:
        """
        `DEFAULT = False` | When True, authorise user access to the API. When False, only authorise basic access.
        """
        if self._user_auth is not None:
            return self._user_auth
        self._user_auth = self._cfg.get("user_auth", False)
        return self._user_auth

    @property
    def api(self) -> SpotifyAPI:
        if self._api is not None:
            if not self.loaded:
                self._api.authorise()
                self.loaded = True
            # noinspection PyTypeChecker
            return self._api

        self._api = SpotifyAPI(
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=self.scopes,
            token_file_path=self.token_path,
            cache_path=self.cache_path,
        )
        return self._api

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": "<OBFUSCATED>" if self.client_id else None,
            "client_secret": "<OBFUSCATED>" if self.client_secret else None,
            "scopes": self.scopes,
            "user_auth": self.user_auth,
        }


if __name__ == "__main__":
    conf = Config()
    conf.load_log_config("logging.yml")
    conf.load("general")
    print(conf.filter.include)
    conf.load("check")
    print(conf.filter.include)

    conf.remote["spotify"].api.api.authorise()

    print(conf)
    print(json.dumps(conf.json(), indent=2))

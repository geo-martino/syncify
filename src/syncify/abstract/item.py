from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Hashable
from typing import Any

from syncify.abstract.enums import TagField
from syncify.abstract.misc import PrettyPrinter


class BaseObject(ABC):
    """
    Generic base class for all local/remote item/collections.

    :ivar tag_sep: When representing a list of tags as a string, use this value as the separator.
    """

    __slots__ = "_clean_tags"

    tag_sep: str = "; "

    @property
    @abstractmethod
    def name(self) -> str:
        """A name for this object"""
        raise NotImplementedError

    @property
    def clean_tags(self) -> dict[TagField, Any]:
        """A map of tags that have been cleaned to use when matching/searching"""
        return self._clean_tags

    def __init__(self):
        self._clean_tags: dict[TagField, Any] = {}


class ObjectPrinterMixin(BaseObject, PrettyPrinter, metaclass=ABCMeta):
    pass


class Item(ObjectPrinterMixin, Hashable, metaclass=ABCMeta):
    """
    Generic class for storing an item.

    :ivar tag_sep: When representing a list of tags as a string, use this value as the separator.
    """

    @property
    @abstractmethod
    def uri(self) -> str | None:
        """URI (Uniform Resource Indicator) is the unique identifier for this item."""
        raise NotImplementedError

    @uri.setter
    @abstractmethod
    def uri(self, value: str | None) -> None:
        """Set both the ``uri`` property and the ``has_uri`` property ."""
        raise NotImplementedError

    @property
    @abstractmethod
    def has_uri(self) -> bool | None:
        """Does this track have a valid associated URI. When None, answer is unknown."""
        raise NotImplementedError

    @abstractmethod
    def __hash__(self):
        raise NotImplementedError

    def __eq__(self, item: Item):
        """URI attributes equal if at least one item has a URI, names equal otherwise"""
        if self.has_uri or item.has_uri:
            return self.has_uri == item.has_uri and self.uri == item.uri
        return self.name == item.name

    def __ne__(self, item: Item):
        return not self.__eq__(item)

    def __getitem__(self, key: str) -> Any:
        """Get the value of a given attribute key"""
        return getattr(self, key)


class Artist(Item, metaclass=ABCMeta):
    """
    Metadata/tags associated with an artist.

    :ivar tag_sep: When representing a list of tags as a string, use this value as the separator.
    """

    @property
    def name(self):
        return self.artist

    @property
    @abstractmethod
    def artist(self) -> str:
        """The artist's name"""
        raise NotImplementedError

    @property
    @abstractmethod
    def genres(self) -> list[str] | None:
        """List of genres associated with this artist"""
        raise NotImplementedError

    @property
    @abstractmethod
    def image_links(self) -> dict[str, str]:
        """The images associated with this artist in the form ``{image name: image link}``"""
        raise NotImplementedError

    @property
    def has_image(self) -> bool:
        """Does this artist have images associated with them"""
        return len(self.image_links) > 0

    @property
    @abstractmethod
    def rating(self) -> int | None:
        """The popularity of this artist"""
        raise NotImplementedError


class Track(Item, metaclass=ABCMeta):
    """
    Metadata/tags associated with a track.

    :ivar tag_sep: When representing a list of tags as a string, use this value as the separator.
    """

    @property
    def name(self) -> str:
        """This track's title"""
        return self.title

    @property
    @abstractmethod
    def title(self) -> str | None:
        """This track's title"""
        raise NotImplementedError

    @property
    @abstractmethod
    def artist(self) -> str | None:
        """Joined string representation of all artists featured on this track"""
        raise NotImplementedError

    @property
    @abstractmethod
    def artists(self) -> list[str | Artist]:
        """List of all artists featured on this track."""
        raise NotImplementedError

    @property
    @abstractmethod
    def album(self) -> str | None:
        """The album this track is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def album_artist(self) -> str | None:
        """The artist of the album this track is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def track_number(self) -> int | None:
        """The position this track has on the album it is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def track_total(self) -> int | None:
        """The track number of tracks on the album this track is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def genres(self) -> list[str] | None:
        """List of genres associated with this track"""
        raise NotImplementedError

    @property
    @abstractmethod
    def year(self) -> int | None:
        """The year this track was released"""
        raise NotImplementedError

    @property
    @abstractmethod
    def bpm(self) -> float | None:
        """The tempo of this track"""
        raise NotImplementedError

    @property
    @abstractmethod
    def key(self) -> str | None:
        """The key of this track in alphabetical musical notation format"""
        raise NotImplementedError

    @property
    @abstractmethod
    def disc_number(self) -> int | None:
        """The number of the disc from the album this track is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def disc_total(self) -> int | None:
        """The total number the discs from the album this track is featured on"""
        raise NotImplementedError

    @property
    @abstractmethod
    def compilation(self) -> bool | None:
        """Is the album this track is featured on a compilation"""
        raise NotImplementedError

    @property
    @abstractmethod
    def comments(self) -> list[str] | None:
        """Comments associated with this track set by the user"""
        raise NotImplementedError

    @property
    @abstractmethod
    def image_links(self) -> dict[str, str]:
        """The images associated with the album this track is featured on in the form ``{image name: image link}``"""
        raise NotImplementedError

    @property
    def has_image(self) -> bool:
        """Does the album this track is associated with have an image"""
        return len(self.image_links) > 0

    @property
    @abstractmethod
    def length(self) -> float:
        """Total duration of this track in seconds"""
        raise NotImplementedError

    @property
    @abstractmethod
    def rating(self) -> float | None:
        """The rating for this track"""
        raise NotImplementedError
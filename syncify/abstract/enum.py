from enum import IntEnum
from typing import Set, Self, Any


class SyncifyEnum(IntEnum):

    @classmethod
    def all(cls) -> Set[Self]:
        return {e for e in cls if e.name != "ALL"}

    @classmethod
    def from_name(cls, name: str) -> Self:
        """
        Returns the first enum that matches the given name

        :exception EnumNotFoundError: If a corresponding enum cannot be found.
        """
        for enum in cls:
            if name.strip().upper() == enum.name.upper():
                return enum
        raise EnumNotFoundError(name)


class EnumNotFoundError(Exception):
    """Exception raised when unable to find an enum by search.

    :param value: The value that caused the error.
    :param message: Explanation of the error.
    """

    def __init__(self, value: Any, message: str = "Could not find enum"):
        self.message = message
        super().__init__(f"{self.message}: {value}")

from typing import Any

from syncify.shared.exception import SyncifyError
from syncify.shared.remote.enum import RemoteIDType, RemoteObjectType


class RemoteError(SyncifyError):
    """Exception raised for remote errors"""


###########################################################################
## Enum errors
###########################################################################
class RemoteIDTypeError(RemoteError):
    """
    Exception raised for remote ID type errors.

    :param message: Explanation of the error.
    :param kind: The ID type related to the error.
    """

    def __init__(self, message: str | None = None, kind: RemoteIDType | None = None, value: Any = None):
        self.kind = kind.name if kind else None
        self.message = message
        formatted = f"{self.kind} | {self.message}" if self.kind else self.message
        formatted += f": {value}" if value else ""
        super().__init__(formatted)


class RemoteObjectTypeError(RemoteError):
    """
    Exception raised for remote object type errors.

    :param message: Explanation of the error.
    :param kind: The item type related to the error.
    """

    def __init__(
            self, message: str | None = None, kind: RemoteObjectType | None = None, value: Any = None):
        self.kind = kind.name if kind else None
        formatted = f"{self.kind} | {message}" if self.kind else message
        formatted += f": {value}" if value else ""
        super().__init__(formatted)

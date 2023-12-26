from datetime import timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from syncify.processors.base import DynamicProcessor, dynamicprocessormethod


class TimeMapper(DynamicProcessor):
    """Map of time character representation to it unit conversion from seconds"""

    @classmethod
    def _processor_method_fmt(cls, name: str) -> str:
        return name.casefold().strip()[0]

    def __init__(self, func: str):
        super().__init__()
        self._set_processor_name(func)

    def __call__(self, value: Any):
        """Run the mapping function"""
        return self.map(value)

    def map(self, value: Any):
        """Run the mapping function"""
        return super().__call__(value)

    @dynamicprocessormethod
    def hours(self, value: Any) -> timedelta:
        """Map given ``value`` in hours to :py:class:`timedelta`"""
        return timedelta(hours=int(value))

    @dynamicprocessormethod
    def days(self, value: Any) -> timedelta:
        """Map given ``value`` in days to :py:class:`timedelta`"""
        return timedelta(days=int(value))

    @dynamicprocessormethod
    def weeks(self, value: Any) -> timedelta:
        """Map given ``value`` in weeks to :py:class:`timedelta`"""
        return timedelta(weeks=int(value))

    @dynamicprocessormethod
    def months(self, value: Any) -> relativedelta:
        """Map given ``value`` in months to :py:class:`timedelta`"""
        return relativedelta(months=int(value))

    def as_dict(self) -> dict[str, Any]:
        return {"function": self._processor_name}
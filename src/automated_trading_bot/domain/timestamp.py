from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Timestamp:
    """An explicit UTC instant, preserving the supplied datetime unchanged."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise TypeError("value must be a datetime")
        offset = self.value.utcoffset()
        if self.value.tzinfo is None or offset is None:
            raise ValueError("value must be timezone-aware")
        if offset != timedelta(0):
            raise ValueError("value must have a UTC offset of zero")

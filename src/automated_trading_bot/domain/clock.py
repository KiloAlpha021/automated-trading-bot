from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from automated_trading_bot.domain.timestamp import Timestamp


class Clock(Protocol):
    """Source of the current validated UTC timestamp."""

    def now(self) -> Timestamp:
        """Return the current UTC timestamp."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock backed by the system UTC clock."""

    def now(self) -> Timestamp:
        return Timestamp(value=datetime.now(UTC))


@dataclass(slots=True)
class TestClock:
    """Deterministic controllable clock for tests."""

    _current: Timestamp

    def __post_init__(self) -> None:
        if not isinstance(self._current, Timestamp):
            raise TypeError("_current must be a Timestamp")

    def now(self) -> Timestamp:
        return self._current

    def set(self, value: Timestamp) -> None:
        if not isinstance(value, Timestamp):
            raise TypeError("value must be a Timestamp")

        self._current = value

    def advance(self, delta: timedelta) -> Timestamp:
        if not isinstance(delta, timedelta):
            raise TypeError("delta must be a timedelta")

        if delta < timedelta(0):
            raise ValueError("delta must not be negative")

        self._current = Timestamp(value=self._current.value + delta)
        return self._current

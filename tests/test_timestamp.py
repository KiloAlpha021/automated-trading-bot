from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import pytest

from automated_trading_bot.domain.timestamp import Timestamp


@pytest.mark.parametrize("zone", [UTC, timezone(timedelta(0), "UTC-equivalent")])
def test_timestamp_preserves_utc_datetime(zone: tzinfo) -> None:
    value = datetime(2026, 9, 10, 12, 30, 45, 123456, tzinfo=zone)

    timestamp = Timestamp(value=value)

    assert timestamp.value is value


def test_timestamp_is_immutable() -> None:
    value = datetime(2026, 9, 10, tzinfo=UTC)
    timestamp = Timestamp(value=value)

    with pytest.raises(FrozenInstanceError):
        timestamp.value = datetime(2026, 9, 11, tzinfo=UTC)

    assert timestamp.value is value


class UndefinedOffset(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize("zone", [None, UndefinedOffset()])
def test_timestamp_rejects_naive_datetime(zone) -> None:
    value = datetime(2026, 9, 10, tzinfo=zone)

    with pytest.raises(ValueError, match="^value must be timezone-aware$"):
        Timestamp(value=value)


@pytest.mark.parametrize("hours", [-5, 1])
def test_timestamp_rejects_non_utc_datetime(hours: int) -> None:
    value = datetime(2026, 9, 10, tzinfo=timezone(timedelta(hours=hours)))

    with pytest.raises(ValueError, match="^value must have a UTC offset of zero$"):
        Timestamp(value=value)


@pytest.mark.parametrize("value", [None, True, 0, 1.0, "2026-09-10T00:00:00Z", date(2026, 9, 10)])
def test_timestamp_rejects_non_datetime(value: object) -> None:
    with pytest.raises(TypeError, match="^value must be a datetime$"):
        Timestamp(value=value)


def test_timestamp_requires_explicit_value() -> None:
    with pytest.raises(TypeError):
        Timestamp()

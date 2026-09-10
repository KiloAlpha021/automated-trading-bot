from datetime import UTC, datetime, timedelta

import pytest

from automated_trading_bot.domain.clock import SystemClock, TestClock as ControlledClock
from automated_trading_bot.domain.timestamp import Timestamp


def make_timestamp(
    year: int = 2026,
    month: int = 9,
    day: int = 10,
    hour: int = 12,
    minute: int = 0,
) -> Timestamp:
    return Timestamp(
        value=datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=UTC,
        )
    )


def test_system_clock_returns_timestamp() -> None:
    clock = SystemClock()

    result = clock.now()

    assert isinstance(result, Timestamp)


def test_system_clock_returns_utc_time() -> None:
    clock = SystemClock()

    before = datetime.now(UTC)
    result = clock.now()
    after = datetime.now(UTC)

    assert result.value.utcoffset() == timedelta(0)
    assert before <= result.value <= after


def test_test_clock_returns_initial_timestamp_unchanged() -> None:
    initial = make_timestamp()
    clock = ControlledClock(initial)

    result = clock.now()

    assert result is initial


def test_test_clock_rejects_non_timestamp_initial_value() -> None:
    with pytest.raises(TypeError, match="^_current must be a Timestamp$"):
        ControlledClock("2026-09-10T12:00:00Z")  # type: ignore[arg-type]


def test_test_clock_can_be_set_to_new_timestamp() -> None:
    initial = make_timestamp()
    replacement = make_timestamp(hour=15)
    clock = ControlledClock(initial)

    clock.set(replacement)

    assert clock.now() is replacement


def test_test_clock_set_rejects_non_timestamp() -> None:
    clock = ControlledClock(make_timestamp())

    with pytest.raises(TypeError, match="^value must be a Timestamp$"):
        clock.set(datetime.now(UTC))  # type: ignore[arg-type]


def test_test_clock_advances_deterministically() -> None:
    initial = make_timestamp(hour=12)
    clock = ControlledClock(initial)

    result = clock.advance(timedelta(hours=2, minutes=30))

    assert result.value == datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    assert clock.now() is result


def test_test_clock_can_advance_across_day_boundary() -> None:
    initial = make_timestamp(day=10, hour=23)
    clock = ControlledClock(initial)

    result = clock.advance(timedelta(hours=2))

    assert result.value == datetime(2026, 9, 11, 1, 0, tzinfo=UTC)


def test_test_clock_zero_advance_preserves_instant() -> None:
    initial = make_timestamp()
    clock = ControlledClock(initial)

    result = clock.advance(timedelta(0))

    assert result.value == initial.value


def test_test_clock_rejects_negative_advance_without_mutating_state() -> None:
    initial = make_timestamp()
    clock = ControlledClock(initial)

    with pytest.raises(ValueError, match="^delta must not be negative$"):
        clock.advance(timedelta(microseconds=-1))

    assert clock.now() is initial


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.5,
        "1 hour",
    ],
)
def test_test_clock_rejects_non_timedelta_advance(value: object) -> None:
    initial = make_timestamp()
    clock = ControlledClock(initial)

    with pytest.raises(TypeError, match="^delta must be a timedelta$"):
        clock.advance(value)  # type: ignore[arg-type]

    assert clock.now() is initial

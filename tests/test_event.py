from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from automated_trading_bot.domain.event import EventEnvelope
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.timestamp import Timestamp


def make_event() -> EventEnvelope:
    return EventEnvelope(
        event_id=EventId(uuid4()),
        correlation_id=CorrelationId(uuid4()),
        causation_id=CausationId(uuid4()),
        idempotency_key=IdempotencyKey("event:test:1"),
        occurred_at=Timestamp(
            value=datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        ),
        schema_version=1,
    )


def test_event_envelope_preserves_values() -> None:
    event_id = EventId(uuid4())
    correlation_id = CorrelationId(uuid4())
    causation_id = CausationId(uuid4())
    idempotency_key = IdempotencyKey("event:test:42")
    occurred_at = Timestamp(
        value=datetime(2026, 9, 10, 18, 30, tzinfo=UTC)
    )

    event = EventEnvelope(
        event_id=event_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at,
        schema_version=2,
    )

    assert event.event_id is event_id
    assert event.correlation_id is correlation_id
    assert event.causation_id is causation_id
    assert event.idempotency_key is idempotency_key
    assert event.occurred_at is occurred_at
    assert event.schema_version == 2


@pytest.mark.parametrize(
    "field_name, invalid_value, expected_message",
    [
        ("event_id", uuid4(), "event_id must be an EventId"),
        (
            "correlation_id",
            uuid4(),
            "correlation_id must be a CorrelationId",
        ),
        (
            "causation_id",
            uuid4(),
            "causation_id must be a CausationId",
        ),
        (
            "idempotency_key",
            "event:test:1",
            "idempotency_key must be an IdempotencyKey",
        ),
        (
            "occurred_at",
            datetime.now(UTC),
            "occurred_at must be a Timestamp",
        ),
    ],
)
def test_event_envelope_rejects_wrong_contract_types(
    field_name: str,
    invalid_value: object,
    expected_message: str,
) -> None:
    values = {
        "event_id": EventId(uuid4()),
        "correlation_id": CorrelationId(uuid4()),
        "causation_id": CausationId(uuid4()),
        "idempotency_key": IdempotencyKey("event:test:1"),
        "occurred_at": Timestamp(
            value=datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        ),
        "schema_version": 1,
    }
    values[field_name] = invalid_value

    with pytest.raises(TypeError, match=f"^{expected_message}$"):
        EventEnvelope(**values)


@pytest.mark.parametrize(
    "invalid_value",
    [
        "1",
        1.0,
        True,
        None,
    ],
)
def test_event_envelope_rejects_non_integer_schema_version(
    invalid_value: object,
) -> None:
    values = {
        "event_id": EventId(uuid4()),
        "correlation_id": CorrelationId(uuid4()),
        "causation_id": CausationId(uuid4()),
        "idempotency_key": IdempotencyKey("event:test:1"),
        "occurred_at": Timestamp(
            value=datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        ),
        "schema_version": invalid_value,
    }

    with pytest.raises(
        TypeError,
        match="^schema_version must be an int$",
    ):
        EventEnvelope(**values)


@pytest.mark.parametrize("invalid_value", [0, -1, -100])
def test_event_envelope_rejects_schema_version_below_one(
    invalid_value: int,
) -> None:
    values = {
        "event_id": EventId(uuid4()),
        "correlation_id": CorrelationId(uuid4()),
        "causation_id": CausationId(uuid4()),
        "idempotency_key": IdempotencyKey("event:test:1"),
        "occurred_at": Timestamp(
            value=datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        ),
        "schema_version": invalid_value,
    }

    with pytest.raises(
        ValueError,
        match="^schema_version must be at least 1$",
    ):
        EventEnvelope(**values)


def test_event_envelope_is_immutable() -> None:
    event = make_event()

    with pytest.raises(FrozenInstanceError):
        event.schema_version = 2

    assert event.schema_version == 1
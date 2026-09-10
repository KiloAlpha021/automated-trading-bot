from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from automated_trading_bot.domain.event import (
    DuplicateEventError,
    EventEnvelope,
    EventRegistry,
)
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
def test_event_envelope_round_trip() -> None:
    original = make_event()

    serialized = original.to_dict()
    reconstructed = EventEnvelope.from_dict(serialized)

    assert reconstructed == original
    assert reconstructed is not original


# Fixed identities keep duplicate-rejection acceptance tests deterministic.
def make_registry_event() -> EventEnvelope:
    return EventEnvelope(
        event_id=EventId(UUID(int=1)),
        correlation_id=CorrelationId(UUID(int=2)),
        causation_id=CausationId(UUID(int=3)),
        idempotency_key=IdempotencyKey("event:registry:1"),
        occurred_at=Timestamp(datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
        schema_version=1,
    )


def test_registry_accepts_unseen_event_id() -> None:
    registry = EventRegistry()
    assert registry.register(EventId(UUID(int=1))) is None


def test_registry_rejects_repeated_event_id() -> None:
    registry = EventRegistry()
    event_id = EventId(UUID(int=1))
    registry.register(event_id)
    for _ in range(2):
        with pytest.raises(DuplicateEventError, match=f"^duplicate EventId: {event_id.value}$"):
            registry.register(event_id)


def test_registry_accepts_otherwise_identical_events_with_distinct_ids() -> None:
    registry = EventRegistry()
    first = make_registry_event()
    second = replace(first, event_id=EventId(UUID(int=4)))
    assert replace(second, event_id=first.event_id) == first
    registry.register(first.event_id)
    registry.register(second.event_id)
    for event in (first, second):
        with pytest.raises(DuplicateEventError):
            registry.register(event.event_id)


def test_registry_rejects_round_tripped_identity() -> None:
    registry = EventRegistry()
    original = make_registry_event()
    reconstructed = EventEnvelope.from_dict(original.to_dict())
    assert reconstructed == original
    assert reconstructed.event_id is not original.event_id
    registry.register(original.event_id)
    with pytest.raises(DuplicateEventError):
        registry.register(reconstructed.event_id)


def test_registry_rejects_same_identity_with_changed_metadata() -> None:
    registry = EventRegistry()
    original = make_registry_event()
    changed = replace(
        original,
        correlation_id=CorrelationId(UUID(int=5)),
        causation_id=CausationId(UUID(int=6)),
        idempotency_key=IdempotencyKey("event:registry:changed"),
        occurred_at=Timestamp(datetime(2026, 9, 11, 18, 0, tzinfo=UTC)),
        schema_version=2,
    )
    registry.register(original.event_id)
    with pytest.raises(DuplicateEventError):
        registry.register(changed.event_id)


def test_registry_history_is_local_to_retained_instance() -> None:
    retained = EventRegistry()
    event_id = EventId(UUID(int=1))
    retained.register(event_id)
    fresh = EventRegistry()
    fresh.register(event_id)
    with pytest.raises(DuplicateEventError):
        retained.register(event_id)


@pytest.mark.parametrize(
    "invalid",
    [None, UUID(int=1), str(UUID(int=1)), CorrelationId(UUID(int=1)),
     CausationId(UUID(int=1)), IdempotencyKey("event:registry:1"), [], True],
)
def test_registry_rejects_invalid_input_without_changing_history(invalid: object) -> None:
    registry = EventRegistry()
    existing = EventId(UUID(int=2))
    registry.register(existing)
    with pytest.raises(TypeError, match="^event_id must be an EventId$"):
        registry.register(invalid)
    registry.register(EventId(UUID(int=1)))
    with pytest.raises(DuplicateEventError):
        registry.register(existing)

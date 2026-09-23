from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest

from automated_trading_bot.domain.command_contracts import (
    COMMAND_ENVELOPE_V1,
    CommandId,
    VersionedCommandEnvelope,
)
from automated_trading_bot.domain.event import EventEnvelope
from automated_trading_bot.domain.event_contracts import (
    EVENT_ENVELOPE_V1,
    EVENT_ENVELOPE_V1_DEFINITION,
    VersionedEventEnvelope,
)
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.idempotency import (
    CommandAdmission,
    ContentFingerprint,
    EventAdmission,
    EventAdmissionDisposition,
    IdempotencyDisposition,
    IdempotencyRecord,
    OperationScope,
    adjudicate_event,
    adjudicate_idempotency,
    fingerprint_content,
)
from automated_trading_bot.domain.persistence import EventPersistenceRequest
from automated_trading_bot.domain.timestamp import Timestamp


def key(value: str = "operation-key") -> IdempotencyKey:
    return IdempotencyKey(value)


def record(data: bytes = b"canonical", *, scope: str = "orders.apply", item_key: IdempotencyKey | None = None) -> IdempotencyRecord:
    return IdempotencyRecord(OperationScope(scope), item_key or key(), data, fingerprint_content(data))


def event_admission(*, event_id: EventId | None = None, data: bytes | None = None, item_key: IdempotencyKey | None = None) -> EventAdmission:
    chosen_key = item_key or key()
    envelope = EventEnvelope(
        event_id=event_id or EventId(uuid4()),
        correlation_id=CorrelationId(uuid4()),
        causation_id=CausationId(uuid4()),
        idempotency_key=chosen_key,
        occurred_at=Timestamp(datetime(2025, 1, 1, tzinfo=UTC)),
        schema_version=1,
    )
    event = VersionedEventEnvelope(envelope, b"payload", EVENT_ENVELOPE_V1)
    representation = data or EVENT_ENVELOPE_V1_DEFINITION.codec.encode(event)
    return EventAdmission(
        event,
        EventPersistenceRequest(EVENT_ENVELOPE_V1, representation),
        record(representation, item_key=chosen_key),
    )


@pytest.mark.parametrize("value", ["a", "A1", "handler.v1", "scope:item-1", "x" * 64])
def test_operation_scope_accepts_stable_values(value: str) -> None:
    scope = OperationScope(value)
    assert scope.value is value
    assert scope.to_string() == value


@pytest.mark.parametrize("value", ["", "-bad", " has-space", "x" * 65, "slash/value"])
def test_operation_scope_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        OperationScope(value)


@pytest.mark.parametrize("value", [None, 1, True, b"scope"])
def test_operation_scope_rejects_non_strings(value: object) -> None:
    with pytest.raises(TypeError):
        OperationScope(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("data", [b"", b"\x00", bytes(range(256)), b"canonical-event"])
def test_fingerprint_is_sha256_of_exact_bytes(data: bytes) -> None:
    fingerprint = fingerprint_content(data)
    assert fingerprint.value == sha256(data).digest()
    assert len(fingerprint.value) == 32


@pytest.mark.parametrize("value", [bytearray(32), memoryview(bytes(32)), "0" * 32, b"short"])
def test_fingerprint_rejects_wrong_type_or_length(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ContentFingerprint(value)  # type: ignore[arg-type]


def test_record_has_exact_fields_and_preserves_original_bytes() -> None:
    data = bytes(bytearray(b"\x00canonical\xff"))
    item = record(data)
    assert [field.name for field in fields(item)] == ["operation_scope", "idempotency_key", "canonical_bytes", "content_fingerprint"]
    assert item.canonical_bytes is data


def test_record_rejects_mismatched_fingerprint() -> None:
    with pytest.raises(ValueError):
        IdempotencyRecord(OperationScope("scope"), key(), b"a", fingerprint_content(b"b"))


def test_idempotency_outcomes_cover_new_duplicate_and_conflict() -> None:
    established = record(b"same")
    assert adjudicate_idempotency(established, None).disposition is IdempotencyDisposition.NEW
    assert adjudicate_idempotency(record(b"same"), established).disposition is IdempotencyDisposition.EXACT_DUPLICATE
    assert adjudicate_idempotency(record(b"different"), established).disposition is IdempotencyDisposition.CONFLICTING_REUSE


def test_same_digest_different_bytes_remains_conflict() -> None:
    established = record(b"first")
    candidate = record(b"second")
    object.__setattr__(candidate, "content_fingerprint", established.content_fingerprint)
    assert adjudicate_idempotency(candidate, established).disposition is IdempotencyDisposition.CONFLICTING_REUSE


def test_adjudication_rejects_different_scoped_key() -> None:
    with pytest.raises(ValueError):
        adjudicate_idempotency(record(scope="one"), record(scope="two"))


def test_event_admission_preserves_components_and_detects_event_integrity() -> None:
    event_id = EventId(uuid4())
    established = event_admission(event_id=event_id)
    duplicate = EventAdmission(established.event, established.persistence_request, established.idempotency_record)
    conflicting = event_admission(event_id=event_id, data=b"different-canonical-event")
    assert adjudicate_event(established, None).disposition is EventAdmissionDisposition.NEW
    assert adjudicate_event(duplicate, established).disposition is EventAdmissionDisposition.EXACT_DUPLICATE
    assert adjudicate_event(conflicting, established).disposition is EventAdmissionDisposition.CONFLICTING_EVENT


def test_event_adjudication_rejects_different_event_id() -> None:
    with pytest.raises(ValueError):
        adjudicate_event(event_admission(), event_admission())


def test_event_admission_rejects_incoherent_bytes() -> None:
    admission = event_admission()
    with pytest.raises(ValueError):
        EventAdmission(admission.event, EventPersistenceRequest(EVENT_ENVELOPE_V1, b"other"), admission.idempotency_record)


def test_command_admission_is_inert_and_key_coherent() -> None:
    chosen_key = key()
    command = VersionedCommandEnvelope(CommandId(uuid4()), CorrelationId(uuid4()), CausationId(uuid4()), chosen_key, b"payload", COMMAND_ENVELOPE_V1)
    admission = CommandAdmission(command, record(b"validated-command", item_key=chosen_key))
    assert admission.command is command
    assert [field.name for field in fields(admission)] == ["command", "idempotency_record"]
    assert not any(hasattr(admission, name) for name in ("execute", "dispatch", "send", "publish"))


def test_value_contracts_are_frozen_and_slotted() -> None:
    item = record()
    with pytest.raises(FrozenInstanceError):
        item.canonical_bytes = b"changed"  # type: ignore[misc]
    assert not hasattr(item, "__dict__")


def test_exact_public_surface() -> None:
    import automated_trading_bot.domain.idempotency as module
    assert module.__all__ == (
        "OperationScope", "ContentFingerprint", "IdempotencyRecord", "IdempotencyDisposition", "IdempotencyOutcome",
        "EventAdmission", "EventAdmissionDisposition", "EventAdmissionOutcome", "CommandAdmission",
        "fingerprint_content", "adjudicate_idempotency", "adjudicate_event",
    )

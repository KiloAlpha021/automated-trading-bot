from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from automated_trading_bot.domain.concurrency import ConcurrencyToken, ConcurrentTransitionRequest
from automated_trading_bot.domain.delivery import DestinationId, HandlerId, HandlerVersion, InboxIdentity, InboxRecord, InboxState, OutboxIdentity, OutboxRecord, OutboxState, transition_inbox
from automated_trading_bot.domain.event import EventEnvelope
from automated_trading_bot.domain.event_contracts import EVENT_ENVELOPE_V1, EVENT_ENVELOPE_V1_DEFINITION, VersionedEventEnvelope
from automated_trading_bot.domain.identifiers import CausationId, CorrelationId, EventId, IdempotencyKey
from automated_trading_bot.domain.idempotency import EventAdmission, IdempotencyRecord, OperationScope, fingerprint_content
from automated_trading_bot.domain.persistence import EventPersistenceRequest, TransitionPersistenceRequest
from automated_trading_bot.domain.state_transitions import GenericState, StateId, TransitionDisposition, TransitionResult, TransitionState, TRANSITION_CONTRACT_V1
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.transaction_composition import ConsumerTransactionIntent, ProducerTransactionIntent


def transition_request() -> ConcurrentTransitionRequest:
    state_id = StateId(uuid4())
    state = GenericState(state_id, TransitionState.ACTIVE, TRANSITION_CONTRACT_V1)
    return ConcurrentTransitionRequest(state_id, TransitionPersistenceRequest(TransitionResult(state, state, TransitionDisposition.ILLEGAL)), ConcurrencyToken(0))


def admission(event_id: EventId | None = None) -> EventAdmission:
    chosen_id = event_id or EventId(uuid4())
    idem = IdempotencyKey("transaction-key-" + str(chosen_id.value))
    envelope = EventEnvelope(chosen_id, CorrelationId(uuid4()), CausationId(uuid4()), idem, Timestamp(datetime(2025, 1, 1, tzinfo=UTC)), 1)
    event = VersionedEventEnvelope(envelope, b"payload", EVENT_ENVELOPE_V1)
    data = EVENT_ENVELOPE_V1_DEFINITION.codec.encode(event)
    record = IdempotencyRecord(OperationScope("transaction.event"), idem, data, fingerprint_content(data))
    return EventAdmission(event, EventPersistenceRequest(EVENT_ENVELOPE_V1, data), record)


def pending_outbox(item: EventAdmission, suffix: str = "primary") -> OutboxRecord:
    return OutboxRecord(OutboxIdentity(item.event.envelope.event_id, DestinationId("destination." + suffix)), OutboxState.PENDING)


def applied_inbox(item: EventAdmission):
    record = InboxRecord(InboxIdentity(item.event.envelope.event_id, HandlerId("handler.main")), HandlerVersion("v1"), InboxState.PENDING)
    return transition_inbox(record, InboxState.APPLIED)


def test_valid_producer_intent_has_exact_fields_and_preserves_members() -> None:
    item = admission()
    transition = transition_request()
    events = (item,)
    outbox = (pending_outbox(item),)
    intent = ProducerTransactionIntent(transition, events, outbox)
    assert [field.name for field in fields(intent)] == ["transition", "events", "outbox"]
    assert intent.transition is transition and intent.events is events and intent.outbox is outbox


def test_producer_requires_nonempty_events() -> None:
    with pytest.raises(ValueError):
        ProducerTransactionIntent(transition_request(), (), ())


def test_producer_rejects_orphan_nonpending_and_duplicate_outbox() -> None:
    item = admission()
    other = admission()
    with pytest.raises(ValueError):
        ProducerTransactionIntent(transition_request(), (item,), (pending_outbox(other),))
    delivered = OutboxRecord(pending_outbox(item).identity, OutboxState.DELIVERED)
    with pytest.raises(ValueError):
        ProducerTransactionIntent(transition_request(), (item,), (delivered,))
    duplicate = pending_outbox(item)
    with pytest.raises(ValueError):
        ProducerTransactionIntent(transition_request(), (item,), (duplicate, duplicate))


def test_valid_consumer_allows_empty_outgoing_membership() -> None:
    item = admission()
    intent = ConsumerTransactionIntent(applied_inbox(item), transition_request(), (), ())
    assert [field.name for field in fields(intent)] == ["inbox_transition", "transition", "outgoing_events", "outbox"]


def test_valid_consumer_accepts_coherent_outgoing_event_and_outbox() -> None:
    incoming = admission()
    outgoing = admission()
    intent = ConsumerTransactionIntent(applied_inbox(incoming), transition_request(), (outgoing,), (pending_outbox(outgoing),))
    assert intent.outgoing_events[0] is outgoing


def test_consumer_requires_transition_to_applied() -> None:
    item = admission()
    pending = InboxRecord(InboxIdentity(item.event.envelope.event_id, HandlerId("handler")), HandlerVersion("v1"), InboxState.PENDING)
    with pytest.raises(ValueError):
        ConsumerTransactionIntent(transition_inbox(pending, InboxState.RETRY), transition_request(), (), ())


def test_consumer_rejects_orphan_outbox() -> None:
    incoming = admission()
    outgoing = admission()
    with pytest.raises(ValueError):
        ConsumerTransactionIntent(applied_inbox(incoming), transition_request(), (), (pending_outbox(outgoing),))


def test_intents_are_frozen_slotted_and_inert() -> None:
    item = admission()
    intent = ProducerTransactionIntent(transition_request(), (item,), ())
    with pytest.raises(FrozenInstanceError):
        intent.events = ()  # type: ignore[misc]
    assert not hasattr(intent, "__dict__")
    assert not any(hasattr(intent, name) for name in ("commit", "rollback", "execute", "persist", "dispatch"))

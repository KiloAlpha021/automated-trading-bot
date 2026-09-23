"""Inert descriptions of atomic persistence membership."""

from dataclasses import dataclass as _dataclass

from automated_trading_bot.domain.concurrency import (
    ConcurrentTransitionRequest as _ConcurrentTransitionRequest,
)
from automated_trading_bot.domain.delivery import (
    DeliveryTransitionDisposition as _DeliveryTransitionDisposition,
    InboxState as _InboxState,
    InboxTransitionResult as _InboxTransitionResult,
    OutboxRecord as _OutboxRecord,
    OutboxState as _OutboxState,
)
from automated_trading_bot.domain.idempotency import EventAdmission as _EventAdmission

__all__ = (
    "ProducerTransactionIntent",
    "ConsumerTransactionIntent",
)


def _require_events(events: object, subject: str) -> tuple[_EventAdmission, ...]:
    if type(events) is not tuple:
        raise TypeError(f"{subject} must be a tuple")
    if any(type(event) is not _EventAdmission for event in events):
        raise TypeError(f"{subject} must contain EventAdmission values")
    return events


def _require_outbox(outbox: object) -> tuple[_OutboxRecord, ...]:
    if type(outbox) is not tuple:
        raise TypeError("outbox must be a tuple")
    if any(type(record) is not _OutboxRecord for record in outbox):
        raise TypeError("outbox must contain OutboxRecord values")
    identities = tuple(record.identity for record in outbox)
    if len(set(identities)) != len(identities):
        raise ValueError("outbox identities must be unique")
    if any(record.state is not _OutboxState.PENDING for record in outbox):
        raise ValueError("outbox records must start PENDING")
    return outbox


def _require_outbox_events(
    events: tuple[_EventAdmission, ...],
    outbox: tuple[_OutboxRecord, ...],
) -> None:
    event_ids = {event.event.envelope.event_id for event in events}
    if any(record.identity.event_id not in event_ids for record in outbox):
        raise ValueError("every outbox EventId must belong to an included event")


@_dataclass(frozen=True, slots=True)
class ProducerTransactionIntent:
    transition: _ConcurrentTransitionRequest
    events: tuple[_EventAdmission, ...]
    outbox: tuple[_OutboxRecord, ...]

    def __post_init__(self) -> None:
        if type(self.transition) is not _ConcurrentTransitionRequest:
            raise TypeError("transition must be a ConcurrentTransitionRequest")
        events = _require_events(self.events, "events")
        if not events:
            raise ValueError("events must not be empty")
        outbox = _require_outbox(self.outbox)
        _require_outbox_events(events, outbox)


@_dataclass(frozen=True, slots=True)
class ConsumerTransactionIntent:
    inbox_transition: _InboxTransitionResult
    transition: _ConcurrentTransitionRequest
    outgoing_events: tuple[_EventAdmission, ...]
    outbox: tuple[_OutboxRecord, ...]

    def __post_init__(self) -> None:
        if type(self.inbox_transition) is not _InboxTransitionResult:
            raise TypeError("inbox_transition must be an InboxTransitionResult")
        if type(self.transition) is not _ConcurrentTransitionRequest:
            raise TypeError("transition must be a ConcurrentTransitionRequest")
        if (
            self.inbox_transition.disposition
            is not _DeliveryTransitionDisposition.TRANSITIONED
            or self.inbox_transition.next_record.state is not _InboxState.APPLIED
        ):
            raise ValueError("inbox_transition must transition to APPLIED")
        events = _require_events(self.outgoing_events, "outgoing_events")
        outbox = _require_outbox(self.outbox)
        _require_outbox_events(events, outbox)

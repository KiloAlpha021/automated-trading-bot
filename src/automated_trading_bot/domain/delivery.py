"""Pure inbox and outbox delivery-state contracts."""

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from re import fullmatch as _fullmatch

from automated_trading_bot.domain.identifiers import EventId as _EventId

__all__ = (
    "HandlerId",
    "HandlerVersion",
    "InboxIdentity",
    "InboxState",
    "InboxRecord",
    "DestinationId",
    "OutboxIdentity",
    "OutboxState",
    "OutboxRecord",
    "DeliveryTransitionDisposition",
    "InboxTransitionResult",
    "OutboxTransitionResult",
    "transition_inbox",
    "transition_outbox",
)

_STABLE_NAME = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"


def _require_stable_name(value: object) -> None:
    if type(value) is not str:
        raise TypeError("value must be a string")
    if _fullmatch(_STABLE_NAME, value) is None:
        raise ValueError("value must be a stable identifier")


@_dataclass(frozen=True, slots=True)
class HandlerId:
    value: str

    def __post_init__(self) -> None:
        _require_stable_name(self.value)

    def to_string(self) -> str:
        return self.value


@_dataclass(frozen=True, slots=True)
class HandlerVersion:
    value: str

    def __post_init__(self) -> None:
        _require_stable_name(self.value)

    def to_string(self) -> str:
        return self.value


@_dataclass(frozen=True, slots=True)
class InboxIdentity:
    event_id: _EventId
    handler_id: HandlerId

    def __post_init__(self) -> None:
        if type(self.event_id) is not _EventId:
            raise TypeError("event_id must be an EventId")
        if type(self.handler_id) is not HandlerId:
            raise TypeError("handler_id must be a HandlerId")


class InboxState(_StrEnum):
    PENDING = "PENDING"
    RETRY = "RETRY"
    APPLIED = "APPLIED"
    QUARANTINED = "QUARANTINED"


@_dataclass(frozen=True, slots=True)
class InboxRecord:
    identity: InboxIdentity
    handler_version: HandlerVersion
    state: InboxState

    def __post_init__(self) -> None:
        if type(self.identity) is not InboxIdentity:
            raise TypeError("identity must be an InboxIdentity")
        if type(self.handler_version) is not HandlerVersion:
            raise TypeError("handler_version must be a HandlerVersion")
        if type(self.state) is not InboxState:
            raise TypeError("state must be an InboxState")


@_dataclass(frozen=True, slots=True)
class DestinationId:
    value: str

    def __post_init__(self) -> None:
        _require_stable_name(self.value)

    def to_string(self) -> str:
        return self.value


@_dataclass(frozen=True, slots=True)
class OutboxIdentity:
    event_id: _EventId
    destination_id: DestinationId

    def __post_init__(self) -> None:
        if type(self.event_id) is not _EventId:
            raise TypeError("event_id must be an EventId")
        if type(self.destination_id) is not DestinationId:
            raise TypeError("destination_id must be a DestinationId")


class OutboxState(_StrEnum):
    PENDING = "PENDING"
    RETRY = "RETRY"
    DELIVERED = "DELIVERED"
    QUARANTINED = "QUARANTINED"


@_dataclass(frozen=True, slots=True)
class OutboxRecord:
    identity: OutboxIdentity
    state: OutboxState

    def __post_init__(self) -> None:
        if type(self.identity) is not OutboxIdentity:
            raise TypeError("identity must be an OutboxIdentity")
        if type(self.state) is not OutboxState:
            raise TypeError("state must be an OutboxState")


class DeliveryTransitionDisposition(_StrEnum):
    TRANSITIONED = "TRANSITIONED"
    UNCHANGED = "UNCHANGED"
    ILLEGAL = "ILLEGAL"


@_dataclass(frozen=True, slots=True)
class InboxTransitionResult:
    previous_record: InboxRecord
    next_record: InboxRecord
    disposition: DeliveryTransitionDisposition

    def __post_init__(self) -> None:
        if type(self.previous_record) is not InboxRecord:
            raise TypeError("previous_record must be an InboxRecord")
        if type(self.next_record) is not InboxRecord:
            raise TypeError("next_record must be an InboxRecord")
        if type(self.disposition) is not DeliveryTransitionDisposition:
            raise TypeError("disposition must be a DeliveryTransitionDisposition")


@_dataclass(frozen=True, slots=True)
class OutboxTransitionResult:
    previous_record: OutboxRecord
    next_record: OutboxRecord
    disposition: DeliveryTransitionDisposition

    def __post_init__(self) -> None:
        if type(self.previous_record) is not OutboxRecord:
            raise TypeError("previous_record must be an OutboxRecord")
        if type(self.next_record) is not OutboxRecord:
            raise TypeError("next_record must be an OutboxRecord")
        if type(self.disposition) is not DeliveryTransitionDisposition:
            raise TypeError("disposition must be a DeliveryTransitionDisposition")


def transition_inbox(record: InboxRecord, target_state: InboxState) -> InboxTransitionResult:
    if type(record) is not InboxRecord:
        raise TypeError("record must be an InboxRecord")
    if type(target_state) is not InboxState:
        raise TypeError("target_state must be an InboxState")
    if record.state in (InboxState.APPLIED, InboxState.QUARANTINED):
        disposition = DeliveryTransitionDisposition.UNCHANGED
    elif target_state is InboxState.PENDING:
        disposition = DeliveryTransitionDisposition.ILLEGAL
    elif record.state is InboxState.RETRY and target_state is InboxState.RETRY:
        disposition = DeliveryTransitionDisposition.UNCHANGED
    else:
        next_record = InboxRecord(record.identity, record.handler_version, target_state)
        return InboxTransitionResult(record, next_record, DeliveryTransitionDisposition.TRANSITIONED)
    return InboxTransitionResult(record, record, disposition)


def transition_outbox(record: OutboxRecord, target_state: OutboxState) -> OutboxTransitionResult:
    if type(record) is not OutboxRecord:
        raise TypeError("record must be an OutboxRecord")
    if type(target_state) is not OutboxState:
        raise TypeError("target_state must be an OutboxState")
    if record.state in (OutboxState.DELIVERED, OutboxState.QUARANTINED):
        disposition = DeliveryTransitionDisposition.UNCHANGED
    elif target_state is OutboxState.PENDING:
        disposition = DeliveryTransitionDisposition.ILLEGAL
    elif record.state is OutboxState.RETRY and target_state is OutboxState.RETRY:
        disposition = DeliveryTransitionDisposition.UNCHANGED
    else:
        next_record = OutboxRecord(record.identity, target_state)
        return OutboxTransitionResult(record, next_record, DeliveryTransitionDisposition.TRANSITIONED)
    return OutboxTransitionResult(record, record, disposition)

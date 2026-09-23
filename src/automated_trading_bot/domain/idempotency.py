"""Durable idempotency admission contracts without execution authority."""

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from hashlib import sha256 as _sha256
from re import fullmatch as _fullmatch

from automated_trading_bot.domain.command_contracts import (
    VersionedCommandEnvelope as _VersionedCommandEnvelope,
)
from automated_trading_bot.domain.event_contracts import (
    VersionedEventEnvelope as _VersionedEventEnvelope,
)
from automated_trading_bot.domain.identifiers import IdempotencyKey as _IdempotencyKey
from automated_trading_bot.domain.persistence import (
    EventPersistenceRequest as _EventPersistenceRequest,
)

__all__ = (
    "OperationScope",
    "ContentFingerprint",
    "IdempotencyRecord",
    "IdempotencyDisposition",
    "IdempotencyOutcome",
    "EventAdmission",
    "EventAdmissionDisposition",
    "EventAdmissionOutcome",
    "CommandAdmission",
    "fingerprint_content",
    "adjudicate_idempotency",
    "adjudicate_event",
)

_STABLE_NAME = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"


def _require_stable_name(value: object, subject: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{subject} must be a string")
    if _fullmatch(_STABLE_NAME, value) is None:
        raise ValueError(f"{subject} must be a stable identifier")


@_dataclass(frozen=True, slots=True)
class OperationScope:
    value: str

    def __post_init__(self) -> None:
        _require_stable_name(self.value, "value")

    def to_string(self) -> str:
        return self.value


@_dataclass(frozen=True, slots=True)
class ContentFingerprint:
    value: bytes

    def __post_init__(self) -> None:
        if type(self.value) is not bytes:
            raise TypeError("value must be bytes")
        if len(self.value) != 32:
            raise ValueError("value must be a 32-byte SHA-256 digest")


def fingerprint_content(representation: bytes) -> ContentFingerprint:
    if type(representation) is not bytes:
        raise TypeError("representation must be bytes")
    return ContentFingerprint(_sha256(representation).digest())


@_dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    operation_scope: OperationScope
    idempotency_key: _IdempotencyKey
    canonical_bytes: bytes
    content_fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        if type(self.operation_scope) is not OperationScope:
            raise TypeError("operation_scope must be an OperationScope")
        if type(self.idempotency_key) is not _IdempotencyKey:
            raise TypeError("idempotency_key must be an IdempotencyKey")
        if type(self.canonical_bytes) is not bytes:
            raise TypeError("canonical_bytes must be bytes")
        if type(self.content_fingerprint) is not ContentFingerprint:
            raise TypeError("content_fingerprint must be a ContentFingerprint")
        if self.content_fingerprint != fingerprint_content(self.canonical_bytes):
            raise ValueError("content_fingerprint must match canonical_bytes")


class IdempotencyDisposition(_StrEnum):
    NEW = "NEW"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    CONFLICTING_REUSE = "CONFLICTING_REUSE"


@_dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    disposition: IdempotencyDisposition

    def __post_init__(self) -> None:
        if type(self.disposition) is not IdempotencyDisposition:
            raise TypeError("disposition must be an IdempotencyDisposition")


def adjudicate_idempotency(
    candidate: IdempotencyRecord,
    established: IdempotencyRecord | None,
) -> IdempotencyOutcome:
    if type(candidate) is not IdempotencyRecord:
        raise TypeError("candidate must be an IdempotencyRecord")
    if established is not None and type(established) is not IdempotencyRecord:
        raise TypeError("established must be an IdempotencyRecord or None")
    if established is None:
        return IdempotencyOutcome(IdempotencyDisposition.NEW)
    if (
        candidate.operation_scope != established.operation_scope
        or candidate.idempotency_key != established.idempotency_key
    ):
        raise ValueError("records must have the same scoped idempotency key")
    if (
        candidate.content_fingerprint == established.content_fingerprint
        and candidate.canonical_bytes == established.canonical_bytes
    ):
        return IdempotencyOutcome(IdempotencyDisposition.EXACT_DUPLICATE)
    return IdempotencyOutcome(IdempotencyDisposition.CONFLICTING_REUSE)


@_dataclass(frozen=True, slots=True)
class EventAdmission:
    event: _VersionedEventEnvelope
    persistence_request: _EventPersistenceRequest
    idempotency_record: IdempotencyRecord

    def __post_init__(self) -> None:
        if type(self.event) is not _VersionedEventEnvelope:
            raise TypeError("event must be a VersionedEventEnvelope")
        if type(self.persistence_request) is not _EventPersistenceRequest:
            raise TypeError("persistence_request must be an EventPersistenceRequest")
        if type(self.idempotency_record) is not IdempotencyRecord:
            raise TypeError("idempotency_record must be an IdempotencyRecord")
        if self.event.contract_version != self.persistence_request.contract_version:
            raise ValueError("event and persistence contract versions must match")
        if self.event.envelope.idempotency_key != self.idempotency_record.idempotency_key:
            raise ValueError("event and idempotency record keys must match")
        if self.persistence_request.representation != self.idempotency_record.canonical_bytes:
            raise ValueError("persistence representation must match canonical_bytes")


class EventAdmissionDisposition(_StrEnum):
    NEW = "NEW"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    CONFLICTING_EVENT = "CONFLICTING_EVENT"


@_dataclass(frozen=True, slots=True)
class EventAdmissionOutcome:
    disposition: EventAdmissionDisposition

    def __post_init__(self) -> None:
        if type(self.disposition) is not EventAdmissionDisposition:
            raise TypeError("disposition must be an EventAdmissionDisposition")


def adjudicate_event(
    candidate: EventAdmission,
    established: EventAdmission | None,
) -> EventAdmissionOutcome:
    if type(candidate) is not EventAdmission:
        raise TypeError("candidate must be an EventAdmission")
    if established is not None and type(established) is not EventAdmission:
        raise TypeError("established must be an EventAdmission or None")
    if established is None:
        return EventAdmissionOutcome(EventAdmissionDisposition.NEW)
    if candidate.event.envelope.event_id != established.event.envelope.event_id:
        raise ValueError("event admissions must have the same EventId")
    if (
        candidate.event.contract_version == established.event.contract_version
        and candidate.persistence_request.representation
        == established.persistence_request.representation
    ):
        return EventAdmissionOutcome(EventAdmissionDisposition.EXACT_DUPLICATE)
    return EventAdmissionOutcome(EventAdmissionDisposition.CONFLICTING_EVENT)


@_dataclass(frozen=True, slots=True)
class CommandAdmission:
    command: _VersionedCommandEnvelope
    idempotency_record: IdempotencyRecord

    def __post_init__(self) -> None:
        if type(self.command) is not _VersionedCommandEnvelope:
            raise TypeError("command must be a VersionedCommandEnvelope")
        if type(self.idempotency_record) is not IdempotencyRecord:
            raise TypeError("idempotency_record must be an IdempotencyRecord")
        if self.command.idempotency_key != self.idempotency_record.idempotency_key:
            raise ValueError("command and idempotency record keys must match")

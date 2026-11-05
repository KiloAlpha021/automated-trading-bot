"""Protected emergency-restriction publication and exact reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class PublicationError(RuntimeError):
    """Publication cannot be proven safe and remains restrictive."""


class RestrictionState(StrEnum):
    PENDING_DENY = "PENDING_DENY"
    EFFECTIVE_DENY = "EFFECTIVE_DENY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    TERMINAL_DENIED = "TERMINAL_DENIED"


@dataclass(frozen=True, slots=True)
class RestrictionReceiptV1:
    operation_id: str
    request_digest: str
    issuer_identity: str
    issuer_domain: str
    scope_key: str
    ticket_sequence: int
    receipt_digest: str

    def __post_init__(self) -> None:
        for name in (
            "operation_id", "request_digest", "issuer_identity", "issuer_domain",
            "scope_key", "receipt_digest",
        ):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise PublicationError(f"{name} is required")
        if self.ticket_sequence < 0:
            raise PublicationError("ticket sequence is invalid")


@dataclass(frozen=True, slots=True)
class RestrictionStatusV1:
    state: RestrictionState
    scope_key: str
    ticket_sequence: int
    receipt_digest: str


class TransactionConnection(Protocol):
    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ProtectedRestrictionPublisherAdapter:
    """Publisher-only adapter; ambiguous writes require exact readback."""

    def __init__(self, connection: TransactionConnection) -> None:
        self._connection = connection

    def publish_pending(self, receipt: RestrictionReceiptV1) -> RestrictionStatusV1:
        if type(receipt) is not RestrictionReceiptV1:
            raise PublicationError("typed authenticated restriction receipt required")
        try:
            self._connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            self._connection.execute(
                "SELECT atis_soe.begin_restriction_v2(%s,%s,%s,%s,%s,%s,%s)",
                (
                    receipt.operation_id, receipt.request_digest,
                    receipt.issuer_identity, receipt.issuer_domain,
                    receipt.scope_key, receipt.ticket_sequence,
                    receipt.receipt_digest,
                ),
            )
            self._connection.commit()
        except BaseException as error:
            self._connection.rollback()
            status = self.read_restriction_status(receipt)
            if status is None:
                raise PublicationError("PENDING publication is unresolved") from error
            if status.state is not RestrictionState.PENDING_DENY:
                raise PublicationError("PENDING publication has conflicting state") from error
            return status
        status = self.read_restriction_status(receipt)
        if status is None or status.state is not RestrictionState.PENDING_DENY:
            raise PublicationError("durable PENDING readback failed")
        return status

    def finalize_restriction(
        self, receipt: RestrictionReceiptV1, state: RestrictionState
    ) -> RestrictionStatusV1:
        if state not in (
            RestrictionState.EFFECTIVE_DENY,
            RestrictionState.RECONCILIATION_REQUIRED,
            RestrictionState.TERMINAL_DENIED,
        ):
            raise PublicationError("invalid terminal restriction state")
        try:
            self._connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            self._connection.execute(
                "SELECT atis_soe.finalize_restriction_v2(%s,%s,%s,%s)",
                (receipt.operation_id, receipt.request_digest,
                 receipt.issuer_identity, state.value),
            )
            self._connection.commit()
        except BaseException as error:
            self._connection.rollback()
            result = self.read_restriction_status(receipt)
            if result is None or result.state is not state:
                raise PublicationError("terminal publication is unresolved") from error
            return result
        result = self.read_restriction_status(receipt)
        if result is None or result.state is not state:
            raise PublicationError("terminal publication readback failed")
        return result

    def read_restriction_status(
        self, receipt: RestrictionReceiptV1
    ) -> RestrictionStatusV1 | None:
        row = self._connection.execute(
            "SELECT * FROM atis_soe.read_restriction_status_v2(%s,%s,%s)",
            (receipt.operation_id, receipt.request_digest, receipt.issuer_identity),
        ).fetchone()
        if row is None:
            return None
        result = RestrictionStatusV1(
            RestrictionState(str(row[0])), str(row[1]), int(row[2]), str(row[3])
        )
        if (
            result.scope_key != receipt.scope_key
            or result.ticket_sequence != receipt.ticket_sequence
            or result.receipt_digest != receipt.receipt_digest
        ):
            raise PublicationError("restriction readback context mismatch")
        return result

    def recover_publication(self, receipt: RestrictionReceiptV1) -> RestrictionStatusV1:
        result = self.read_restriction_status(receipt)
        if result is None:
            raise PublicationError("missing publication evidence remains restrictive")
        return result

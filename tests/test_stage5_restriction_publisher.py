from __future__ import annotations

import pytest

from automated_trading_bot.stage5.commit_boundary.restriction_publisher import (
    ProtectedRestrictionPublisherAdapter,
    PublicationError,
    RestrictionReceiptV1,
    RestrictionState,
)


class Result:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Connection:
    def __init__(self) -> None:
        self.state: str | None = None
        self.fail_write = False
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> Result:
        if "begin_restriction_v2" in query:
            if self.fail_write:
                raise TimeoutError("lost acknowledgement")
            self.state = "PENDING_DENY"
        elif "finalize_restriction_v2" in query:
            if self.fail_write:
                raise TimeoutError("lost acknowledgement")
            self.state = str(parameters[3])
        elif "read_restriction_status_v2" in query and self.state is not None:
            return Result((self.state, "SCOPE", 7, "RECEIPT"))
        return Result()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def receipt() -> RestrictionReceiptV1:
    return RestrictionReceiptV1("OP", "DIGEST", "SECURITY", "SECURITY", "SCOPE", 7, "RECEIPT")


def test_pending_requires_exact_durable_readback() -> None:
    connection = Connection()
    status = ProtectedRestrictionPublisherAdapter(connection).publish_pending(receipt())
    assert status.state is RestrictionState.PENDING_DENY
    assert connection.commits == 1


def test_lost_acknowledgement_without_readback_remains_restrictive() -> None:
    connection = Connection()
    connection.fail_write = True
    with pytest.raises(PublicationError, match="unresolved"):
        ProtectedRestrictionPublisherAdapter(connection).publish_pending(receipt())
    assert connection.rollbacks == 1


def test_lost_acknowledgement_with_exact_readback_is_reconciled() -> None:
    connection = Connection()
    connection.state = "PENDING_DENY"
    connection.fail_write = True
    status = ProtectedRestrictionPublisherAdapter(connection).publish_pending(receipt())
    assert status.state is RestrictionState.PENDING_DENY


def test_cross_issuer_readback_cannot_be_substituted() -> None:
    connection = Connection()
    connection.state = "PENDING_DENY"
    foreign = RestrictionReceiptV1(
        "OP", "DIGEST", "OPERATIONS", "OPERATIONS", "OTHER", 7, "RECEIPT"
    )
    with pytest.raises(PublicationError, match="context mismatch"):
        ProtectedRestrictionPublisherAdapter(connection).read_restriction_status(foreign)

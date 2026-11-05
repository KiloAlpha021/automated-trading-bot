from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    AuthenticationFailure,
    AttemptContext,
    DurableOutcome,
    InvalidOutcome,
    IsolationViolation,
    OperationConflict,
    OperationContractError,
    OperationDeadline,
    OperationRequest,
    OutcomeStatus,
    SerializableOperationEnvelope,
    VerifiedAttemptEvidence,
)


class SerializationFailure(RuntimeError):
    sqlstate = "40001"


@dataclass
class FakeDatabase:
    isolation: str = "serializable"
    outcomes: dict[str, DurableOutcome] = field(default_factory=dict)
    capital_effect: int = 0
    liability_effect: int = 0
    backend: int = 100
    transaction: int = 200
    serialization_failures: int = 0
    ambiguous_commit: bool = False
    rollback_ok: bool = True
    close_ok: bool = True
    connect_error: Exception | None = None
    connections: list[FakeConnection] = field(default_factory=list, init=False)

    def connect(self, deadline: OperationDeadline) -> FakeConnection:
        deadline.ensure()
        if self.connect_error is not None:
            raise self.connect_error
        self.backend += 1
        self.transaction += 1
        connection = FakeConnection(self, self.backend, self.transaction)
        self.connections.append(connection)
        return connection


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.row: tuple[object, ...] | None = None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        if statement == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            self.connection.begun = True
        elif statement.startswith("SELECT current_setting"):
            assert self.connection.begun
            self.row = (
                self.connection.database.isolation,
                str(self.connection.backend),
                str(self.connection.transaction),
            )
        elif statement.startswith("SELECT set_config"):
            self.row = (parameters[0],)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class FakeConnection:
    def __init__(self, database: FakeDatabase, backend: int, transaction: int) -> None:
        self.database = database
        self.backend = backend
        self.transaction = transaction
        self.begun = False
        self.closed = False
        self.terminated = False
        self.local_outcomes = dict(database.outcomes)
        self.local_capital = database.capital_effect
        self.local_liability = database.liability_effect

    def set_deadline(self, deadline: OperationDeadline) -> None:
        deadline.ensure()

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self, deadline: OperationDeadline) -> None:
        deadline.ensure()
        if self.database.serialization_failures:
            self.database.serialization_failures -= 1
            raise SerializationFailure("synthetic serialization conflict")
        self.database.outcomes = dict(self.local_outcomes)
        self.database.capital_effect = self.local_capital
        self.database.liability_effect = self.local_liability
        if self.database.ambiguous_commit:
            self.database.ambiguous_commit = False
            raise RuntimeError("acknowledgement lost")

    def rollback(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        return self.database.rollback_ok

    def close(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        self.closed = self.database.close_ok
        return self.closed

    def cancel(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        return True

    def terminate(self) -> None:
        self.terminated = True
        self.closed = True


class FakeBackend:
    def __init__(self) -> None:
        self.verify_calls: list[AttemptContext] = []
        self.proof_ids: list[str] = []
        self.denial_code: str | None = None
        self.foreign_outcome = False
        self.denied_effect = False
        self.replay_proof = False
        self.authentication_error = False

    def lookup(
        self, cursor: FakeCursor, request: OperationRequest, context: AttemptContext
    ) -> DurableOutcome | None:
        del context
        return cursor.connection.local_outcomes.get(request.operation_id)

    def verify_attempt(
        self, cursor: FakeCursor, request: OperationRequest, context: AttemptContext
    ) -> VerifiedAttemptEvidence:
        del cursor
        self.verify_calls.append(context)
        if self.authentication_error:
            raise AuthenticationFailure("synthetic issuer mismatch")
        proof_id = "TP-REPLAY" if self.replay_proof else f"TP-{context.attempt}"
        self.proof_ids.append(proof_id)
        return VerifiedAttemptEvidence(
            request.operation_id,
            request.digest,
            context.attempt,
            context.backend_identity,
            context.transaction_identity,
            request.scope,
            request.expected_generation,
            request.expected_epoch,
            "I01-SYNTHETIC",
            proof_id,
            f"VERIFY-{context.attempt}",
        )

    def execute(
        self,
        cursor: FakeCursor,
        request: OperationRequest,
        context: AttemptContext,
        evidence: VerifiedAttemptEvidence,
    ) -> DurableOutcome:
        del context, evidence
        operation_id = "FOREIGN" if self.foreign_outcome else request.operation_id
        if self.denial_code is not None:
            effect = (request.amount_minor or 0) if self.denied_effect else 0
            outcome = DurableOutcome(
                operation_id, request.digest, OutcomeStatus.DENIED,
                self.denial_code, effect, effect,
            )
        else:
            effect = request.amount_minor or 0
            outcome = DurableOutcome(
                operation_id, request.digest, OutcomeStatus.COMMITTED,
                "RESERVED", effect, effect,
            )
            cursor.connection.local_capital += effect
            cursor.connection.local_liability += effect
        cursor.connection.local_outcomes[operation_id] = outcome
        return outcome


def request(**changes: Any) -> OperationRequest:
    values: dict[str, Any] = {
        "operation_id": "OP-1",
        "request_type": "CAPITAL_RESERVATION",
        "scope": "PORTFOLIO:P1",
        "amount_minor": 5000,
        "currency": "GBP",
        "expected_generation": 7,
        "expected_epoch": 11,
        "immutable_parameters": {"g00_ticket_id": "G00-1", "portfolio_id": "P1"},
    }
    values.update(changes)
    return OperationRequest.create(**values)


def envelope(
    database: FakeDatabase, backend: FakeBackend | None = None, **settings: Any
) -> tuple[SerializableOperationEnvelope, FakeBackend]:
    selected = backend or FakeBackend()
    return SerializableOperationEnvelope(database.connect, selected, **settings), selected


def test_canonical_digest_and_unicode_policy() -> None:
    assert request(immutable_parameters={"b": 2, "a": ["é", 1]}).digest == request(
        immutable_parameters={"a": ["\u00e9", 1], "b": 2}
    ).digest
    for change in (
        {"operation_id": ""},
        {"scope": " PORTFOLIO:P1"},
        {"immutable_parameters": {"label": "e\u0301"}},
        {"immutable_parameters": {"label": "bad\u0000"}},
    ):
        with pytest.raises(OperationContractError):
            request(**change)


@pytest.mark.parametrize(
    "change",
    [{"scope": "PORTFOLIO:P2"}, {"expected_generation": 8},
     {"expected_epoch": 12}, {"amount_minor": 5001}, {"currency": "USD"}],
)
def test_digest_binds_immutable_authority_fields(change: dict[str, Any]) -> None:
    assert request().digest != request(**change).digest


def test_serializable_atomic_success_and_idempotent_readback() -> None:
    database = FakeDatabase()
    subject, _ = envelope(database)
    first = subject.execute(request())
    second = subject.execute(request())
    assert first.outcome.status is OutcomeStatus.COMMITTED
    assert second.attempts[-1].disposition == "IDEMPOTENT_READBACK"
    assert (database.capital_effect, database.liability_effect) == (5000, 5000)
    assert all(item.attempts[0].isolation == "serializable" for item in (first, second))


def test_40001_revalidates_fresh_attempt_and_preserves_single_effect() -> None:
    database = FakeDatabase(serialization_failures=1)
    subject, backend = envelope(database, backoff_ms=(0,))
    result = subject.execute(request())
    assert [record.disposition for record in result.attempts] == [
        "SERIALIZATION_ABORT", "COMMITTED",
    ]
    assert backend.proof_ids == ["TP-1", "TP-2"]
    assert len({context.transaction_identity for context in backend.verify_calls}) == 2
    assert (database.capital_effect, database.liability_effect) == (5000, 5000)


def test_retry_exhaustion_is_restrictive() -> None:
    database = FakeDatabase(serialization_failures=3)
    subject, _ = envelope(database, max_attempts=3, backoff_ms=(0,))
    result = subject.execute(request())
    assert result.outcome.result_code == "SERIALIZATION_RETRY_EXHAUSTED"
    assert (database.capital_effect, database.liability_effect) == (0, 0)


def test_ambiguous_commit_uses_fresh_connection_without_duplicate_effect() -> None:
    database = FakeDatabase(ambiguous_commit=True)
    subject, _ = envelope(database)
    result = subject.execute(request())
    assert result.attempts[-1].disposition == "AMBIGUOUS_COMMIT_RECOVERED"
    assert len(database.connections) == 2 and database.connections[0].closed
    assert (database.capital_effect, database.liability_effect) == (5000, 5000)


def test_unreleased_ambiguous_connection_stays_unknown_without_readback() -> None:
    database = FakeDatabase(ambiguous_commit=True, close_ok=False)
    subject, _ = envelope(database)
    result = subject.execute(request())
    assert result.outcome.result_code == "COMMIT_OUTCOME_UNKNOWN"
    assert result.cleanup_unresolved and len(database.connections) == 1
    assert database.connections[0].terminated


def test_isolation_downgrade_rejects_before_authority_verification() -> None:
    database = FakeDatabase(isolation="read committed")
    subject, backend = envelope(database)
    with pytest.raises(IsolationViolation):
        subject.execute(request())
    assert backend.verify_calls == []


def test_existing_conflicting_digest_rejects() -> None:
    original = request()
    database = FakeDatabase(outcomes={
        original.operation_id: DurableOutcome(
            original.operation_id, original.digest, OutcomeStatus.COMMITTED,
            "RESERVED", 5000, 5000,
        )
    })
    subject, _ = envelope(database)
    with pytest.raises(OperationConflict):
        subject.execute(request(amount_minor=4000))


@pytest.mark.parametrize("code", ["DENIED_EXPIRED", "DENIED_TIME_UNCERTAIN"])
def test_protective_denial_is_durable_with_zero_financial_effect(code: str) -> None:
    database, backend = FakeDatabase(), FakeBackend()
    backend.denial_code = code
    subject, _ = envelope(database, backend)
    result = subject.execute(request())
    assert database.outcomes["OP-1"] == result.outcome
    assert (database.capital_effect, database.liability_effect) == (0, 0)


def test_denied_outcome_with_financial_effect_rejects_and_rolls_back() -> None:
    database, backend = FakeDatabase(), FakeBackend()
    backend.denial_code, backend.denied_effect = "DENIED", True
    subject, _ = envelope(database, backend)
    with pytest.raises(InvalidOutcome):
        subject.execute(request())
    assert database.outcomes == {}


def test_foreign_outcome_identity_rejects() -> None:
    database, backend = FakeDatabase(), FakeBackend()
    backend.foreign_outcome = True
    subject, _ = envelope(database, backend)
    with pytest.raises(InvalidOutcome):
        subject.execute(request())
    assert database.outcomes == {}


def test_attempt_proof_replay_after_serialization_conflict_rejects() -> None:
    database, backend = FakeDatabase(serialization_failures=1), FakeBackend()
    backend.replay_proof = True
    subject, _ = envelope(database, backend, backoff_ms=(0,))
    with pytest.raises(AuthenticationFailure):
        subject.execute(request())
    assert (database.capital_effect, database.liability_effect) == (0, 0)


def test_issuer_impersonation_rejects_before_protected_operation() -> None:
    database, backend = FakeDatabase(), FakeBackend()
    backend.authentication_error = True
    subject, _ = envelope(database, backend)
    with pytest.raises(AuthenticationFailure):
        subject.execute(request())
    assert database.outcomes == {}


def test_cleanup_failure_is_quarantined_and_restrictive() -> None:
    database = FakeDatabase(serialization_failures=1, rollback_ok=False, close_ok=False)
    subject, _ = envelope(database, backoff_ms=(0,))
    result = subject.execute(request())
    assert result.outcome.result_code == "CLEANUP_UNRESOLVED"
    assert result.cleanup_unresolved and database.connections[0].terminated


def test_connection_acquisition_failure_does_not_claim_success() -> None:
    database = FakeDatabase(connect_error=TimeoutError("connect deadline"))
    subject, _ = envelope(database)
    with pytest.raises(TimeoutError):
        subject.execute(request())

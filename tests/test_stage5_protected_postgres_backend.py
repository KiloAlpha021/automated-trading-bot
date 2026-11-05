from __future__ import annotations

from dataclasses import replace

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    AttemptContext,
    DurableOutcome,
    G00AdmissionReceipt,
    OperationRequest,
    OutcomeStatus,
    PostgresProtectedBackend,
    SyntheticG00TrustRoot,
    VerifiedAttemptEvidence,
    migration_sql,
)
from automated_trading_bot.stage5.commit_boundary.psycopg_adapter import (
    ProtectedPsycopgPersistentSessionFactory,
)


class Cursor:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0)


def request(operation_id: str = "OP-1", amount: int = 500) -> OperationRequest:
    return OperationRequest.create(
        operation_id=operation_id,
        request_type="CAPITAL_RESERVATION",
        scope="PORTFOLIO:PG",
        amount_minor=amount,
        currency="GBP",
        expected_generation=1,
        expected_epoch=2,
        immutable_parameters={
            "g00_ticket_id": f"G00-{operation_id}",
            "g00_ticket_sequence": 7,
            "g00_replay_identity": f"NONCE-{operation_id}",
        },
    )


def attestation(subject: OperationRequest):
    trust = SyntheticG00TrustRoot({"test-key": b"k" * 32})
    receipt = trust.sign(
        G00AdmissionReceipt(
            issuer_identity="G00-TEST",
            deployment_identity="DEPLOYMENT-TEST",
            recipient="SOE-TEST",
            operation_id=subject.operation_id,
            request_digest=subject.digest,
            scope=subject.scope,
            generation=subject.expected_generation,
            issuer_epoch=subject.expected_epoch,
            ticket_sequence=7,
            observed_cutover_sequence=None,
            issued_ns=1,
            valid_until_ns=10**30,
            nonce=f"NONCE-{subject.operation_id}",
            key_id="test-key",
        )
    )
    return trust.verify_and_consume(
        receipt,
        expected_issuer="G00-TEST",
        expected_deployment="DEPLOYMENT-TEST",
        expected_recipient="SOE-TEST",
        operation_id=subject.operation_id,
        request_digest=subject.digest,
        scope=subject.scope,
        generation=subject.expected_generation,
        issuer_epoch=subject.expected_epoch,
        admitted_ns=0,
    )


def attempt_evidence(
    subject: OperationRequest, context: AttemptContext
) -> VerifiedAttemptEvidence:
    return VerifiedAttemptEvidence(
        subject.operation_id,
        subject.digest,
        context.attempt,
        context.backend_identity,
        context.transaction_identity,
        subject.scope,
        subject.expected_generation,
        subject.expected_epoch,
        "I01-TEST",
        f"TIME-{context.transaction_identity}",
        f"VERIFY-{context.transaction_identity}",
    )


def context(subject: OperationRequest) -> AttemptContext:
    return AttemptContext(
        subject.operation_id,
        subject.digest,
        1,
        "backend-1",
        "transaction-1",
        "serializable",
        "G00-OP-1",
        10**30,
    )


def test_migration_contains_database_owned_security_boundaries() -> None:
    sql = migration_sql()
    for required in (
        "NOLOGIN NOINHERIT",
        "REVOKE ALL ON ALL TABLES",
        "SECURITY DEFINER",
        "SET search_path = atis_soe, pg_catalog",
        "current_setting('transaction_isolation')",
        "FOR SHARE",
        "FOR UPDATE",
        "RECONCILIATION_REQUIRED",
    ):
        assert required in sql


def test_backend_uses_only_protected_functions() -> None:
    subject = request()
    verified = attestation(subject)
    backend = PostgresProtectedBackend(attempt_evidence, lambda _: verified)
    cursor = Cursor(
        [
            (verified.verification_digest,),
            (
                subject.operation_id,
                subject.digest,
                "COMMITTED",
                "RESERVED",
                500,
                500,
            ),
        ]
    )
    backend.verify_attempt(cursor, subject, context(subject))
    outcome = backend.lookup(cursor, subject, context(subject))
    assert outcome == DurableOutcome(
        subject.operation_id,
        subject.digest,
        OutcomeStatus.COMMITTED,
        "RESERVED",
        500,
        500,
    )
    statements = "\n".join(call[0] for call in cursor.calls)
    assert "assert_admission_attestation" in statements
    assert "lookup_outcome" in statements
    assert "commit_capital" not in statements
    assert not any(token in statements.upper() for token in ("INSERT ", "UPDATE ", "DELETE "))


def test_backend_rejects_wrong_attestation_context_before_effect() -> None:
    subject = request()
    verified = replace(attestation(subject), scope="PORTFOLIO:OTHER")
    backend = PostgresProtectedBackend(attempt_evidence, lambda _: verified)
    cursor = Cursor([])
    with pytest.raises(ValueError, match="foreign request context"):
        backend.verify_attempt(cursor, subject, context(subject))
    assert cursor.calls == []


def test_backend_rejects_replay_identity_substitution() -> None:
    subject = request()
    verified = replace(attestation(subject), replay_identity="OTHER-NONCE")
    backend = PostgresProtectedBackend(attempt_evidence, lambda _: verified)
    cursor = Cursor([(verified.verification_digest,)])
    evidence = backend.verify_attempt(cursor, subject, context(subject))
    with pytest.raises(ValueError, match="replay identity"):
        backend.execute(cursor, subject, context(subject), evidence)


def test_lookup_is_exact_operation_and_digest() -> None:
    subject = request()
    verified = attestation(subject)
    backend = PostgresProtectedBackend(attempt_evidence, lambda _: verified)
    cursor = Cursor(
        [
            (
                subject.operation_id,
                subject.digest,
                "DENIED",
                "DENIED_RESTRICTION",
                0,
                0,
            )
        ]
    )
    outcome = backend.lookup(cursor, subject, context(subject))
    assert outcome is not None and outcome.status is OutcomeStatus.DENIED
    assert cursor.calls[0][1] == (subject.operation_id, subject.digest)


def test_spawn_runtime_factory_contains_no_verifier_database_authority() -> None:
    protected = ProtectedPsycopgPersistentSessionFactory(
        "dbname=runtime role=atis_soe_runtime",
        "dbname=verifier role=atis_soe_verifier",
        7,
    )
    spawned = protected.runtime_factory()
    assert spawned.conninfo == "dbname=runtime role=atis_soe_runtime"
    assert "verifier" not in repr(spawned)
    assert not hasattr(spawned.executor, "verifier_conninfo")


@pytest.mark.parametrize("parameters", [{}, {"scope_envelope_digest": "present"}, {"atis_scope_envelope_digest": "present", "qualified": True}])
def test_u1_scope_less_and_presence_flags_cannot_grant(parameters):
    old = request()
    subject = OperationRequest.create(operation_id=old.operation_id,request_type=old.request_type,scope=old.scope,amount_minor=old.amount_minor,currency=old.currency,expected_generation=old.expected_generation,expected_epoch=old.expected_epoch,immutable_parameters={**dict(old.immutable_parameters),**parameters})
    verified = attestation(subject)
    backend = PostgresProtectedBackend(attempt_evidence,lambda _:verified)
    cursor = Cursor([(subject.operation_id,subject.digest,"COMMITTED","RESERVED",500,500)])
    with pytest.raises(PermissionError,match="U1_FINANCIAL_CONTAINMENT"):
        backend.execute(cursor,subject,context(subject),attempt_evidence(subject,context(subject)))
    assert cursor.calls == []

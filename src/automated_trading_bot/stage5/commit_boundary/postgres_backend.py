"""Protected PostgreSQL backend for the Stage 5 operation envelope.

The database functions remain the authority boundary.  This adapter supplies
only immutable, already-verified candidate evidence and never performs direct
protected-table DML.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from importlib import import_module
import json
from time import monotonic_ns
from typing import Any, Mapping, Protocol, cast

from .g00_auth import G00AdmissionReceipt, VerifiedG00Attestation
from .operation_envelope import OperationRequest
from .preconnected_pool import VerifiedAdmissionEvidence
from .process_isolation import IsolatedCommand, IsolatedOutcome, IsolatedStatus
from .postgres_serializable import (
    AttemptContext,
    DurableOutcome,
    OutcomeStatus,
    VerifiedAttemptEvidence,
)
from automated_trading_bot.stage5.identity_scope import canonical_bytes


class AttemptEvidenceProvider(Protocol):
    def __call__(
        self, request: OperationRequest, context: AttemptContext
    ) -> VerifiedAttemptEvidence: ...


class AttestationProvider(Protocol):
    def __call__(self, request: OperationRequest) -> VerifiedG00Attestation: ...


def _required_text(request: OperationRequest, name: str) -> str:
    value = request.immutable_parameters.get(name)
    if type(value) is not str or not value:
        raise ValueError(f"{name} is required for protected PostgreSQL admission")
    return value


def _required_int(request: OperationRequest, name: str) -> int:
    value = request.immutable_parameters.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative int")
    return value


def _plain_canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_canonical(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_canonical(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PostgresProtectedBackend:
    """EnforcementBackend backed exclusively by hardened database functions."""

    attempt_evidence_provider: AttemptEvidenceProvider
    attestation_provider: AttestationProvider

    @staticmethod
    def _outcome(row: tuple[object, ...]) -> DurableOutcome:
        return DurableOutcome(
            operation_id=str(row[0]),
            request_digest=str(row[1]),
            status=OutcomeStatus(str(row[2])),
            result_code=str(row[3]),
            capital_effect_minor=int(cast(int, row[4])),
            liability_effect_minor=int(cast(int, row[5])),
        )

    def lookup(
        self, cursor: Any, request: OperationRequest, context: AttemptContext
    ) -> DurableOutcome | None:
        del context
        cursor.execute(
            "SELECT * FROM atis_soe.lookup_outcome(%s, %s)",
            (request.operation_id, request.digest),
        )
        row = cursor.fetchone()
        return None if row is None else self._outcome(row)

    def verify_attempt(
        self, cursor: Any, request: OperationRequest, context: AttemptContext
    ) -> VerifiedAttemptEvidence:
        attestation = self.attestation_provider(request)
        evidence = self.attempt_evidence_provider(request, context)
        ticket = _required_int(request, "g00_ticket_sequence")
        expected = (
            request.operation_id,
            request.digest,
            request.scope,
            request.expected_generation,
            request.expected_epoch,
            ticket,
        )
        actual = (
            attestation.operation_id,
            attestation.request_digest,
            attestation.scope,
            attestation.generation,
            attestation.issuer_epoch,
            attestation.ticket_sequence,
        )
        if actual != expected:
            raise ValueError("verified G00 attestation has a foreign request context")
        cursor.execute(
            "SELECT atis_soe.assert_admission_attestation("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                attestation.replay_identity,
                request.operation_id,
                request.digest,
                request.scope,
                request.expected_generation,
                request.expected_epoch,
                ticket,
                request.request_type,
                request.amount_minor,
                request.currency,
                f"{context.transaction_identity}:{context.attempt}",
                evidence.time_proof_id,
            ),
        )
        row = cursor.fetchone()
        if row is None or str(row[0]) != attestation.verification_digest:
            raise ValueError("database G00 attestation binding was not established")
        return evidence

    def execute(
        self,
        cursor: Any,
        request: OperationRequest,
        context: AttemptContext,
        evidence: VerifiedAttemptEvidence,
    ) -> DurableOutcome:
        if request.amount_minor is None or request.currency is None:
            raise ValueError("protected capital operation requires amount and currency")
        attestation = self.attestation_provider(request)
        _required_int(request, "g00_ticket_sequence")
        if attestation.replay_identity != _required_text(request, "g00_replay_identity"):
            raise ValueError("request replay identity differs from verified attestation")
        # U1/C013: neither legacy metadata nor a present envelope qualifies
        # identity, catalogue, predecessor, capacity or liability authority.
        # Keep every new-exposure route closed until separately qualified.
        # Historical outcomes are observed only through lookup(); an exception
        # here must never manufacture a terminal DENIED outcome for ambiguity.
        raise PermissionError(
            "U1_FINANCIAL_CONTAINMENT: new financial authority is unqualified"
        )


def migration_sql() -> str:
    """Return the exact packaged candidate migration text."""

    from importlib.resources import files

    root = files("automated_trading_bot.stage5.commit_boundary").joinpath("sql")
    return "\n".join(
        root.joinpath(name).read_text(encoding="utf-8")
        for name in ("0001_soe_commit_boundary.sql", "0002_scope_publication.sql")
    )


def _verified_attestation(receipt: G00AdmissionReceipt) -> VerifiedG00Attestation:
    verification_digest = hashlib.sha256(
        b"ATIS-S5-G00-VERIFIED-V1\x00"
        + receipt.payload()
        + receipt.signature.encode("ascii")
    ).hexdigest()
    return VerifiedG00Attestation(
        issuer_identity=receipt.issuer_identity,
        deployment_identity=receipt.deployment_identity,
        recipient=receipt.recipient,
        operation_id=receipt.operation_id,
        request_digest=receipt.request_digest,
        scope=receipt.scope,
        generation=receipt.generation,
        issuer_epoch=receipt.issuer_epoch,
        ticket_sequence=receipt.ticket_sequence,
        observed_cutover_sequence=receipt.observed_cutover_sequence,
        replay_identity=receipt.nonce,
        key_id=receipt.key_id,
        verification_digest=verification_digest,
    )


def _attempt_digest(evidence: VerifiedAdmissionEvidence) -> str:
    body = json.dumps(
        {
            "attempt_id": evidence.attempt_id,
            "authority_epoch": evidence.authority_epoch,
            "authority_generation": evidence.authority_generation,
            "authority_identity": evidence.authority_identity,
            "authority_issued_ns": evidence.authority_issued_ns,
            "operation_id": evidence.operation_id,
            "request_digest": evidence.request_digest,
            "scope": evidence.scope,
            "timeproof_identity": evidence.timeproof_identity,
            "timeproof_issued_ns": evidence.timeproof_issued_ns,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"ATIS-S5-ATTEMPT-EVIDENCE-V1\x00" + body).hexdigest()


@dataclass(frozen=True, slots=True)
class ProtectedPersistentExecutor:
    """Picklable worker executor wired only to protected database functions."""

    policy_version: int

    def __post_init__(self) -> None:
        if self.policy_version <= 0:
            raise ValueError("positive policy version is required")

    @staticmethod
    def _request(command: IsolatedCommand) -> OperationRequest:
        payload = command.payload
        parameters = payload.get("immutable_parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("immutable_parameters must be a dictionary")
        return OperationRequest(
            operation_id=command.operation_id,
            request_type=str(payload.get("request_type", "")),
            scope=str(payload.get("scope", "")),
            amount_minor=cast(int | None, payload.get("amount_minor")),
            currency=cast(str | None, payload.get("currency")),
            expected_generation=int(payload.get("expected_generation", -1)),
            expected_epoch=int(payload.get("expected_epoch", -1)),
            immutable_parameters=parameters,
            digest=command.request_digest,
        )

    @staticmethod
    def register_verified_evidence(
        verifier_conninfo: str,
        policy_version: int,
        request: OperationRequest,
        evidence: VerifiedAdmissionEvidence,
        attestation: VerifiedG00Attestation,
    ) -> None:
        psycopg = import_module("psycopg")
        connection = psycopg.connect(verifier_conninfo, autocommit=True)
        try:
            ProtectedPersistentExecutor.register_verified_evidence_on_connection(
                connection, policy_version, request, evidence, attestation
            )
        finally:
            connection.close()

    @staticmethod
    def register_verified_evidence_on_connection(
        connection: Any,
        policy_version: int,
        request: OperationRequest,
        evidence: VerifiedAdmissionEvidence,
        attestation: VerifiedG00Attestation,
    ) -> None:
        remaining_ns = max(0, evidence.g00_receipt.valid_until_ns - monotonic_ns())
        if remaining_ns == 0:
            raise ValueError("G00 evidence expired before database registration")
        valid_until = datetime.now(timezone.utc) + timedelta(
            microseconds=max(1, remaining_ns // 1_000)
        )
        try:
            connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            scope_envelope = request.immutable_parameters.get("scope_envelope")
            scope_digest = request.immutable_parameters.get("scope_envelope_digest")
            operation_core_digest = request.immutable_parameters.get(
                "operation_core_digest"
            )
            operation_class = request.immutable_parameters.get("operation_class")
            catalogue_epoch = request.immutable_parameters.get("catalogue_epoch")
            if scope_envelope is not None:
                if (
                    not isinstance(scope_envelope, Mapping)
                    or type(scope_digest) is not str
                    or type(operation_core_digest) is not str
                    or type(operation_class) is not str
                    or type(catalogue_epoch) is not int
                ):
                    raise ValueError("canonical scope envelope metadata is malformed")
                connection.execute(
                    "SELECT atis_soe.register_scope_envelope_v1("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    (
                        request.operation_id, request.digest, operation_class,
                        catalogue_epoch, operation_core_digest, scope_digest,
                        canonical_bytes(_plain_canonical(scope_envelope)),
                    ),
                )
            connection.execute(
                "SELECT atis_soe.register_g00_attestation("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attestation.replay_identity,
                    attestation.verification_digest,
                    attestation.issuer_identity,
                    attestation.deployment_identity,
                    attestation.recipient,
                    request.operation_id,
                    request.digest,
                    request.request_type,
                    request.amount_minor,
                    request.currency,
                    request.scope,
                    request.expected_generation,
                    request.expected_epoch,
                    attestation.ticket_sequence,
                    attestation.observed_cutover_sequence,
                    valid_until,
                    policy_version,
                ),
            )
            connection.execute(
                "SELECT atis_soe.register_attempt_evidence("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"{evidence.attempt_id}:1",
                    _attempt_digest(evidence),
                    request.operation_id,
                    request.digest,
                    request.scope,
                    request.expected_generation,
                    request.expected_epoch,
                    evidence.authority_identity,
                    evidence.timeproof_identity,
                    evidence.authority_issued_ns,
                    evidence.timeproof_issued_ns,
                    max(evidence.authority_issued_ns, evidence.timeproof_issued_ns),
                    policy_version,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def __call__(
        self,
        cursor: Any,
        command: IsolatedCommand,
        admission: VerifiedAdmissionEvidence,
    ) -> IsolatedOutcome:
        request = self._request(command)
        attestation = _verified_attestation(admission.g00_receipt)
        verified_attempt = VerifiedAttemptEvidence(
            request.operation_id,
            request.digest,
            1,
            "PRECONNECTED_POSTGRESQL",
            admission.attempt_id,
            request.scope,
            request.expected_generation,
            request.expected_epoch,
            admission.authority_identity,
            admission.timeproof_identity,
            _attempt_digest(admission),
        )
        context = AttemptContext(
            request.operation_id,
            request.digest,
            1,
            "PRECONNECTED_POSTGRESQL",
            admission.attempt_id,
            "serializable",
            command.g00_sequence,
            0,
        )
        backend = PostgresProtectedBackend(lambda _r, _c: verified_attempt, lambda _r: attestation)
        checked = backend.verify_attempt(cursor, request, context)
        outcome = backend.execute(cursor, request, context, checked)
        status = (
            IsolatedStatus.COMMITTED
            if outcome.status is OutcomeStatus.COMMITTED
            else IsolatedStatus.DENIED
        )
        return IsolatedOutcome(
            outcome.operation_id,
            outcome.request_digest,
            status,
            outcome.result_code,
            outcome.capital_effect_minor,
            outcome.liability_effect_minor,
        )

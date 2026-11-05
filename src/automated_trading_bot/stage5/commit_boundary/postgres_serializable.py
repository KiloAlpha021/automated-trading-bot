"""Deadline-owned SERIALIZABLE envelope for Stage 5 enforcement calls."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic_ns, sleep
from typing import Any, Protocol

from .operation_envelope import OperationRequest


Clock = Callable[[], int]
Sleeper = Callable[[float], None]


class CommitBoundaryError(RuntimeError):
    """Base error for an invalid enforcement boundary."""


class IsolationViolation(CommitBoundaryError):
    """The database did not establish SERIALIZABLE isolation."""


class OperationConflict(CommitBoundaryError):
    """An operation identity was reused with different canonical content."""


class SerializationConflict(CommitBoundaryError):
    """PostgreSQL aborted the complete logical operation with SQLSTATE 40001."""


class DefinitiveDenial(CommitBoundaryError):
    """The protected backend definitively denied the operation."""


class AmbiguousCommit(CommitBoundaryError):
    """Commit acknowledgement was lost or otherwise ambiguous."""


class DeadlineExhausted(CommitBoundaryError):
    """The monotonic operation deadline was exhausted."""


class CancellationFailure(CommitBoundaryError):
    """A driver could not confirm cancellation or termination."""


class CleanupFailure(CommitBoundaryError):
    """Rollback or connection cleanup did not complete."""


class InvalidOutcome(CommitBoundaryError):
    """A returned or durable outcome violated the atomic outcome contract."""


class AuthenticationFailure(CommitBoundaryError):
    """Attempt-bound authority or trusted-time evidence was invalid."""


class OutcomeStatus(StrEnum):
    COMMITTED = "COMMITTED"
    DENIED = "DENIED"
    RESTRICTIVE_UNKNOWN = "RESTRICTIVE_UNKNOWN"


@dataclass(frozen=True, slots=True)
class OperationDeadline:
    expires_at_ns: int
    _clock_ns: Clock = field(repr=False, compare=False)

    @classmethod
    def after_ms(cls, timeout_ms: int, clock_ns: Clock = monotonic_ns) -> "OperationDeadline":
        if type(timeout_ms) is not int or timeout_ms < 1 or timeout_ms > 300_000:
            raise ValueError("timeout_ms must be between 1 and 300000")
        return cls(clock_ns() + timeout_ms * 1_000_000, clock_ns)

    def remaining_ns(self) -> int:
        return max(0, self.expires_at_ns - self._clock_ns())

    def remaining_ms(self) -> int:
        remaining = self.remaining_ns()
        if remaining == 0:
            raise DeadlineExhausted("operation deadline exhausted")
        return max(1, (remaining + 999_999) // 1_000_000)

    def ensure(self) -> None:
        if self.remaining_ns() == 0:
            raise DeadlineExhausted("operation deadline exhausted")


@dataclass(frozen=True, slots=True)
class DurableOutcome:
    operation_id: str
    request_digest: str
    status: OutcomeStatus
    result_code: str
    capital_effect_minor: int = 0
    liability_effect_minor: int = 0

    def __post_init__(self) -> None:
        if type(self.capital_effect_minor) is not int or self.capital_effect_minor < 0:
            raise ValueError("capital_effect_minor must be a non-negative int")
        if type(self.liability_effect_minor) is not int or self.liability_effect_minor < 0:
            raise ValueError("liability_effect_minor must be a non-negative int")


@dataclass(frozen=True, slots=True)
class AttemptContext:
    operation_id: str
    request_digest: str
    attempt: int
    backend_identity: str
    transaction_identity: str
    isolation: str
    ordering_key: str
    deadline_ns: int


@dataclass(frozen=True, slots=True)
class VerifiedAttemptEvidence:
    operation_id: str
    request_digest: str
    attempt: int
    backend_identity: str
    transaction_identity: str
    scope: str
    authority_generation: int
    authority_epoch: int
    issuer_identity: str
    time_proof_id: str
    verification_digest: str


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    operation_id: str
    request_digest: str
    attempt: int
    backend_identity: str
    transaction_identity: str
    isolation: str
    disposition: str
    ordering_key: str = "UNESTABLISHED"
    evidence_digest: str = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class EnvelopeResult:
    outcome: DurableOutcome
    attempts: tuple[AttemptRecord, ...]
    cleanup_unresolved: bool = False


class Cursor(Protocol):
    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> Any: ...
    def fetchone(self) -> tuple[object, ...] | None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def set_deadline(self, deadline: OperationDeadline) -> None: ...
    def commit(self, deadline: OperationDeadline) -> None: ...
    def rollback(self, deadline: OperationDeadline) -> bool: ...
    def close(self, deadline: OperationDeadline) -> bool: ...
    def cancel(self, deadline: OperationDeadline) -> bool: ...
    def terminate(self) -> None: ...


class ConnectionFactory(Protocol):
    def __call__(self, deadline: OperationDeadline) -> Connection: ...


class EnforcementBackend(Protocol):
    def lookup(
        self, cursor: Cursor, request: OperationRequest, context: AttemptContext
    ) -> DurableOutcome | None: ...

    def verify_attempt(
        self, cursor: Cursor, request: OperationRequest, context: AttemptContext
    ) -> VerifiedAttemptEvidence: ...

    def execute(
        self,
        cursor: Cursor,
        request: OperationRequest,
        context: AttemptContext,
        evidence: VerifiedAttemptEvidence,
    ) -> DurableOutcome: ...


def _sqlstate(error: Exception) -> str | None:
    value = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    return value if type(value) is str else None


class SerializableOperationEnvelope:
    """Run one sealed, authority-neutral enforcement operation."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        backend: EnforcementBackend,
        *,
        max_attempts: int = 3,
        operation_timeout_ms: int = 5_000,
        backoff_ms: tuple[int, ...] = (5, 10, 20),
        clock_ns: Clock = monotonic_ns,
        sleeper: Sleeper = sleep,
    ) -> None:
        if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if type(operation_timeout_ms) is not int or not 1 <= operation_timeout_ms <= 300_000:
            raise ValueError("operation_timeout_ms must be between 1 and 300000")
        if not backoff_ms or any(type(value) is not int or value < 0 for value in backoff_ms):
            raise ValueError("backoff_ms must contain non-negative ints")
        self._connection_factory = connection_factory
        self._backend = backend
        self._max_attempts = max_attempts
        self._operation_timeout_ms = operation_timeout_ms
        self._backoff_ms = backoff_ms
        self._clock_ns = clock_ns
        self._sleeper = sleeper

    @staticmethod
    def _ordering_key(request: OperationRequest) -> str:
        candidate = request.immutable_parameters.get("g00_ticket_id", request.operation_id)
        if type(candidate) is not str or not candidate:
            raise InvalidOutcome("g00_ticket_id must be canonical text")
        return candidate

    @staticmethod
    def _begin(connection: Connection, deadline: OperationDeadline) -> tuple[Cursor, str, str, str]:
        deadline.ensure()
        connection.set_deadline(deadline)
        cursor = connection.cursor()
        cursor.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        timeout = f"{deadline.remaining_ms()}ms"
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", (timeout,))
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
        cursor.execute(
            "SELECT current_setting('transaction_isolation'), "
            "pg_backend_pid()::text, txid_current()::text"
        )
        row = cursor.fetchone()
        if row is None or len(row) != 3:
            raise IsolationViolation("transaction identity was not returned")
        isolation, backend, transaction = (str(value) for value in row)
        if isolation.lower() != "serializable":
            raise IsolationViolation("protected operation requires SERIALIZABLE isolation")
        return cursor, isolation.lower(), backend, transaction

    @staticmethod
    def _validate_outcome(outcome: DurableOutcome, request: OperationRequest) -> None:
        if outcome.operation_id != request.operation_id or outcome.request_digest != request.digest:
            raise InvalidOutcome("outcome has a foreign operation identity or digest")
        if outcome.status is not OutcomeStatus.COMMITTED and (
            outcome.capital_effect_minor != 0 or outcome.liability_effect_minor != 0
        ):
            raise InvalidOutcome("non-committed outcome cannot create financial effects")

    def _lookup_checked(
        self, cursor: Cursor, request: OperationRequest, context: AttemptContext
    ) -> DurableOutcome | None:
        outcome = self._backend.lookup(cursor, request, context)
        if outcome is not None:
            if outcome.request_digest != request.digest:
                raise OperationConflict("operation ID is already bound to another digest")
            self._validate_outcome(outcome, request)
        return outcome

    @staticmethod
    def _validate_evidence(
        evidence: VerifiedAttemptEvidence,
        request: OperationRequest,
        context: AttemptContext,
        seen_proofs: set[str],
    ) -> None:
        expected = (
            request.operation_id,
            request.digest,
            context.attempt,
            context.backend_identity,
            context.transaction_identity,
            request.scope,
            request.expected_generation,
            request.expected_epoch,
        )
        actual = (
            evidence.operation_id,
            evidence.request_digest,
            evidence.attempt,
            evidence.backend_identity,
            evidence.transaction_identity,
            evidence.scope,
            evidence.authority_generation,
            evidence.authority_epoch,
        )
        if actual != expected:
            raise AuthenticationFailure("attempt evidence is bound to a foreign context")
        if not evidence.issuer_identity or not evidence.time_proof_id or not evidence.verification_digest:
            raise AuthenticationFailure("attempt evidence is incomplete")
        if evidence.time_proof_id in seen_proofs:
            raise AuthenticationFailure("TimeProof was reused across transaction attempts")
        seen_proofs.add(evidence.time_proof_id)

    @staticmethod
    def _terminate(connection: Connection) -> None:
        try:
            connection.terminate()
        except Exception:
            pass

    def _cleanup(
        self,
        connection: Connection,
        deadline: OperationDeadline,
        *,
        rollback: bool,
    ) -> bool:
        clean = True
        if rollback:
            try:
                clean = connection.rollback(deadline)
            except Exception:
                clean = False
        if not clean:
            try:
                connection.cancel(deadline)
            except Exception:
                pass
        try:
            closed = connection.close(deadline)
        except Exception:
            closed = False
        if not closed:
            self._terminate(connection)
        return clean and closed

    def _unknown(
        self,
        request: OperationRequest,
        attempts: list[AttemptRecord],
        result_code: str,
        *,
        cleanup_unresolved: bool = False,
    ) -> EnvelopeResult:
        return EnvelopeResult(
            DurableOutcome(
                request.operation_id,
                request.digest,
                OutcomeStatus.RESTRICTIVE_UNKNOWN,
                result_code,
            ),
            tuple(attempts),
            cleanup_unresolved,
        )

    def _readback(
        self,
        request: OperationRequest,
        deadline: OperationDeadline,
        attempt: int,
        ordering_key: str,
    ) -> tuple[DurableOutcome | None, bool]:
        connection: Connection | None = None
        try:
            connection = self._connection_factory(deadline)
            cursor, isolation, backend, transaction = self._begin(connection, deadline)
            context = AttemptContext(
                request.operation_id,
                request.digest,
                attempt,
                backend,
                transaction,
                isolation,
                ordering_key,
                deadline.expires_at_ns,
            )
            outcome = self._lookup_checked(cursor, request, context)
            clean = self._cleanup(connection, deadline, rollback=True)
            connection = None
            return outcome, clean
        except Exception:
            if connection is not None:
                self._cleanup(connection, deadline, rollback=True)
            return None, False

    def _backoff(self, deadline: OperationDeadline, attempt: int) -> None:
        delay_ms = self._backoff_ms[min(attempt - 1, len(self._backoff_ms) - 1)]
        if delay_ms == 0:
            deadline.ensure()
            return
        if deadline.remaining_ms() <= delay_ms:
            raise DeadlineExhausted("retry backoff would exceed operation deadline")
        self._sleeper(delay_ms / 1_000)
        deadline.ensure()

    def execute(self, request: OperationRequest) -> EnvelopeResult:
        deadline = OperationDeadline.after_ms(self._operation_timeout_ms, self._clock_ns)
        attempts: list[AttemptRecord] = []
        seen_proofs: set[str] = set()
        ordering_key = self._ordering_key(request)

        for attempt in range(1, self._max_attempts + 1):
            connection: Connection | None = None
            context: AttemptContext | None = None
            evidence_digest = "UNVERIFIED"
            try:
                deadline.ensure()
                connection = self._connection_factory(deadline)
                cursor, isolation, backend, transaction = self._begin(connection, deadline)
                context = AttemptContext(
                    request.operation_id,
                    request.digest,
                    attempt,
                    backend,
                    transaction,
                    isolation,
                    ordering_key,
                    deadline.expires_at_ns,
                )
                existing = self._lookup_checked(cursor, request, context)
                if existing is not None:
                    clean = self._cleanup(connection, deadline, rollback=True)
                    connection = None
                    attempts.append(
                        AttemptRecord(
                            request.operation_id,
                            request.digest,
                            attempt,
                            backend,
                            transaction,
                            isolation,
                            "IDEMPOTENT_READBACK",
                            ordering_key,
                        )
                    )
                    return EnvelopeResult(existing, tuple(attempts), not clean)

                evidence = self._backend.verify_attempt(cursor, request, context)
                self._validate_evidence(evidence, request, context, seen_proofs)
                evidence_digest = evidence.verification_digest
                outcome = self._backend.execute(cursor, request, context, evidence)
                self._validate_outcome(outcome, request)
                durable = self._lookup_checked(cursor, request, context)
                if durable is None or durable != outcome:
                    raise InvalidOutcome("protected operation did not persist its exact outcome")

                try:
                    connection.commit(deadline)
                except Exception as error:
                    if _sqlstate(error) == "40001":
                        clean = self._cleanup(connection, deadline, rollback=True)
                        connection = None
                        attempts.append(
                            AttemptRecord(
                                request.operation_id,
                                request.digest,
                                attempt,
                                backend,
                                transaction,
                                isolation,
                                "SERIALIZATION_ABORT",
                                ordering_key,
                                evidence_digest,
                            )
                        )
                        if not clean:
                            return self._unknown(
                                request,
                                attempts,
                                "CLEANUP_UNRESOLVED",
                                cleanup_unresolved=True,
                            )
                        if attempt == self._max_attempts:
                            break
                        self._backoff(deadline, attempt)
                        continue

                    released = self._cleanup(connection, deadline, rollback=False)
                    connection = None
                    if not released:
                        attempts.append(
                            AttemptRecord(
                                request.operation_id,
                                request.digest,
                                attempt,
                                backend,
                                transaction,
                                isolation,
                                "AMBIGUOUS_RELEASE_FAILED",
                                ordering_key,
                                evidence_digest,
                            )
                        )
                        return self._unknown(
                            request,
                            attempts,
                            "COMMIT_OUTCOME_UNKNOWN",
                            cleanup_unresolved=True,
                        )
                    recovered, clean = self._readback(request, deadline, attempt, ordering_key)
                    disposition = (
                        "AMBIGUOUS_COMMIT_RECOVERED"
                        if recovered is not None
                        else "AMBIGUOUS_COMMIT_UNKNOWN"
                    )
                    attempts.append(
                        AttemptRecord(
                            request.operation_id,
                            request.digest,
                            attempt,
                            backend,
                            transaction,
                            isolation,
                            disposition,
                            ordering_key,
                            evidence_digest,
                        )
                    )
                    if recovered is None:
                        return self._unknown(
                            request,
                            attempts,
                            "COMMIT_OUTCOME_UNKNOWN",
                            cleanup_unresolved=not clean,
                        )
                    return EnvelopeResult(recovered, tuple(attempts), not clean)

                clean = self._cleanup(connection, deadline, rollback=False)
                connection = None
                attempts.append(
                    AttemptRecord(
                        request.operation_id,
                        request.digest,
                        attempt,
                        backend,
                        transaction,
                        isolation,
                        "COMMITTED" if clean else "COMMITTED_CLEANUP_UNRESOLVED",
                        ordering_key,
                        evidence_digest,
                    )
                )
                return EnvelopeResult(outcome, tuple(attempts), not clean)

            except DeadlineExhausted:
                clean = True
                if connection is not None:
                    clean = self._cleanup(connection, deadline, rollback=True)
                    connection = None
                backend = context.backend_identity if context else "UNESTABLISHED"
                transaction = context.transaction_identity if context else "UNESTABLISHED"
                isolation = context.isolation if context else "UNESTABLISHED"
                attempts.append(
                    AttemptRecord(
                        request.operation_id,
                        request.digest,
                        attempt,
                        backend,
                        transaction,
                        isolation,
                        "G00_PROGRESS_BREACH",
                        ordering_key,
                        evidence_digest,
                    )
                )
                return self._unknown(
                    request,
                    attempts,
                    "G00_PROGRESS_BREACH",
                    cleanup_unresolved=not clean,
                )
            except Exception as error:
                clean = True
                if connection is not None:
                    clean = self._cleanup(connection, deadline, rollback=True)
                    connection = None
                if _sqlstate(error) == "40001":
                    backend = context.backend_identity if context else "UNESTABLISHED"
                    transaction = context.transaction_identity if context else "UNESTABLISHED"
                    isolation = context.isolation if context else "UNESTABLISHED"
                    attempts.append(
                        AttemptRecord(
                            request.operation_id,
                            request.digest,
                            attempt,
                            backend,
                            transaction,
                            isolation,
                            "SERIALIZATION_ABORT",
                            ordering_key,
                            evidence_digest,
                        )
                    )
                    if not clean:
                        return self._unknown(
                            request,
                            attempts,
                            "CLEANUP_UNRESOLVED",
                            cleanup_unresolved=True,
                        )
                    if attempt == self._max_attempts:
                        break
                    try:
                        self._backoff(deadline, attempt)
                    except DeadlineExhausted:
                        return self._unknown(request, attempts, "G00_PROGRESS_BREACH")
                    continue
                raise

        return self._unknown(request, attempts, "SERIALIZATION_RETRY_EXHAUSTED")

"""Concrete psycopg adapter for the bounded SOE PostgreSQL contract.

The import is deliberately lazy: deployment must provide and pin psycopg;
this module does not modify project dependency policy.
"""

from __future__ import annotations

from importlib import import_module
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from .preconnected_pool import VerifiedAdmissionEvidence, WorkerReadiness
from .process_isolation import IsolatedCommand, IsolatedOutcome
from .postgres_serializable import OperationDeadline
from .postgres_backend import ProtectedPersistentExecutor, _verified_attestation


PersistentExecutor: TypeAlias = Callable[
    [Any, IsolatedCommand, VerifiedAdmissionEvidence], IsolatedOutcome
]


class PsycopgConnection:
    """Deadline-aware wrapper around a psycopg connection."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._closed = False

    def set_deadline(self, deadline: OperationDeadline) -> None:
        deadline.ensure()

    def cursor(self) -> Any:
        return self._raw.cursor()

    def commit(self, deadline: OperationDeadline) -> None:
        deadline.ensure()
        self._raw.commit()
        deadline.ensure()

    def rollback(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        self._raw.rollback()
        deadline.ensure()
        return True

    def cancel(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        self._raw.cancel_safe(timeout=deadline.remaining_ms() / 1_000)
        return True

    def close(self, deadline: OperationDeadline) -> bool:
        deadline.ensure()
        self._raw.close()
        self._closed = True
        return True

    def terminate(self) -> None:
        if not self._closed:
            self._raw.close()
            self._closed = True


class PsycopgConnectionFactory:
    """Create non-autocommit connections within the operation deadline."""

    def __init__(self, conninfo: str) -> None:
        if not conninfo.strip():
            raise ValueError("conninfo is required")
        self._conninfo = conninfo

    def __call__(self, deadline: OperationDeadline) -> PsycopgConnection:
        psycopg = import_module("psycopg")
        timeout_seconds = max(1, (deadline.remaining_ms() + 999) // 1_000)
        raw = psycopg.connect(
            self._conninfo,
            autocommit=False,
            connect_timeout=timeout_seconds,
        )
        deadline.ensure()
        return PsycopgConnection(raw)


class PsycopgPersistentSession:
    """One worker-owned preconnected session with no authority cache."""

    def __init__(self, conninfo: str, executor: PersistentExecutor) -> None:
        psycopg = import_module("psycopg")
        self._raw = psycopg.connect(conninfo, autocommit=True)
        self._executor = executor
        row = self._raw.execute("SELECT pg_backend_pid()").fetchone()
        if row is None:
            self._raw.close()
            raise RuntimeError("PostgreSQL backend identity is unavailable")
        self._connection_identity = str(row[0])

    def readiness(self) -> WorkerReadiness:
        status = getattr(self._raw.info.transaction_status, "name", "UNKNOWN")
        return WorkerReadiness(
            connection_identity=self._connection_identity,
            transaction_idle=not self._raw.closed and status == "IDLE",
            authority_cache_empty=True,
            timeproof_cache_empty=True,
            operation_cache_empty=True,
        )

    def execute(
        self,
        command: IsolatedCommand,
        evidence: VerifiedAdmissionEvidence,
    ) -> IsolatedOutcome:
        if not self.readiness().safe:
            raise RuntimeError("persistent PostgreSQL session is not ready")
        commit_started = False
        try:
            self._raw.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            row = self._raw.execute("SHOW transaction_isolation").fetchone()
            if row is None or str(row[0]) != "serializable":
                raise RuntimeError("PostgreSQL transaction is not SERIALIZABLE")
            outcome = self._executor(self._raw.cursor(), command, evidence)
            commit_started = True
            self._raw.commit()
            return outcome
        except BaseException:
            if not commit_started:
                try:
                    self._raw.rollback()
                except BaseException:
                    pass
            raise

    def close(self) -> None:
        self._raw.close()


@dataclass(frozen=True, slots=True)
class PsycopgPersistentSessionFactory:
    """Picklable factory; the PostgreSQL connection is created only in the worker."""

    conninfo: str
    executor: PersistentExecutor

    def __post_init__(self) -> None:
        if not self.conninfo.strip():
            raise ValueError("conninfo is required")

    def __call__(self) -> PsycopgPersistentSession:
        return PsycopgPersistentSession(self.conninfo, self.executor)


@dataclass(frozen=True, slots=True)
class ProtectedPsycopgPersistentSessionFactory:
    """Windows-spawn-safe factory with no caller-selected protected executor."""

    conninfo: str
    verifier_conninfo: str
    policy_version: int
    protected_candidate: bool = True

    def __post_init__(self) -> None:
        if not self.conninfo.strip() or not self.verifier_conninfo.strip():
            raise ValueError("runtime and verifier connections are required")
        if self.policy_version <= 0:
            raise ValueError("positive identity policy version is required")

    def __call__(self) -> PsycopgPersistentSession:
        return PsycopgPersistentSession(
            self.conninfo,
            ProtectedPersistentExecutor(self.policy_version),
        )

    def register(self, command: IsolatedCommand, evidence: VerifiedAdmissionEvidence) -> None:
        request = ProtectedPersistentExecutor._request(command)
        ProtectedPersistentExecutor.register_verified_evidence(
            self.verifier_conninfo,
            self.policy_version,
            request,
            evidence,
            _verified_attestation(evidence.g00_receipt),
        )

    def open_registrar(self) -> ProtectedEvidenceRegistrar:
        return ProtectedEvidenceRegistrar(self.verifier_conninfo, self.policy_version)

    def runtime_factory(self) -> PsycopgPersistentSessionFactory:
        """Return the spawn payload without verifier credentials."""
        return PsycopgPersistentSessionFactory(
            self.conninfo, ProtectedPersistentExecutor(self.policy_version)
        )


class ProtectedEvidenceRegistrar:
    """Supervisor-owned preconnected verifier; never serialized into a worker."""

    def __init__(self, conninfo: str, policy_version: int) -> None:
        psycopg = import_module("psycopg")
        self._raw = psycopg.connect(conninfo, autocommit=True)
        self._policy_version = policy_version

    def register(self, command: IsolatedCommand, evidence: VerifiedAdmissionEvidence) -> None:
        request = ProtectedPersistentExecutor._request(command)
        ProtectedPersistentExecutor.register_verified_evidence_on_connection(
            self._raw,
            self._policy_version,
            request,
            evidence,
            _verified_attestation(evidence.g00_receipt),
        )

    def close(self) -> None:
        self._raw.close()



class ProtectedU3Registrar:
    """Standalone verifier connection; no worker payload or financial interface."""

    def __init__(self, verifier_conninfo: str) -> None:
        self._raw = import_module("psycopg").connect(verifier_conninfo, autocommit=True)

    def prepare_identity(
        self, evidence: object, *, timeout_ms: int = 5000
    ) -> dict[str, Any]:
        return self._submit(evidence, "PREPARE", timeout_ms)

    def register_scope(
        self, evidence: object, *, timeout_ms: int = 5000
    ) -> dict[str, Any]:
        return self._submit(evidence, "REGISTER", timeout_ms)

    def read_exact(self, evidence: object, *, timeout_ms: int = 5000) -> dict[str, Any]:
        return self._submit(evidence, "READ", timeout_ms)

    def _submit(self, evidence: object, action: str, timeout_ms: int) -> dict[str, Any]:
        from .postgres_backend import submit_u3_registration_on_connection

        return submit_u3_registration_on_connection(
            self._raw, evidence, action=action, timeout_ms=timeout_ms
        )

    def close(self) -> None:
        self._raw.close()

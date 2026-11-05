"""Crash-safe synthetic recovery-intent journal for isolated SOE workers."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import monotonic_ns, time_ns
from typing import Any

from .recovery_authority import (
    RecoveryAuthorityError,
    RecoveryAuthorityVerifier,
    RecoveryReceipt,
    RecoveryTerminalOutcome,
)


class JournalConflict(RuntimeError):
    """A durable operation identity was reused with incompatible content."""


class JournalDeadlineExceeded(TimeoutError):
    """The supervisor-owned journal budget expired before durable completion."""


class JournalPersistenceUnresolved(RuntimeError):
    """Journal durability could not be truthfully established."""


class RecoveryState(StrEnum):
    INTENT = "INTENT"
    EFFECT_POSSIBLE = "EFFECT_POSSIBLE"
    AMBIGUOUS_HOLD = "AMBIGUOUS_HOLD"
    RECOVERED_COMMITTED = "RECOVERED_COMMITTED"
    RECOVERED_DENIED = "RECOVERED_DENIED"
    UNRESOLVED = "UNRESOLVED"
    FAILED_BEFORE_EFFECT = "FAILED_BEFORE_EFFECT"
    CAPACITY_DENIED = "CAPACITY_DENIED"
    PERSISTENCE_UNRESOLVED = "PERSISTENCE_UNRESOLVED"


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    operation_id: str
    request_digest: str
    attempt_id: str
    worker_pid: int | None
    transaction_uncertain: bool
    state: RecoveryState
    g00_sequence: str
    recovery_attempts: int
    recovery_result: dict[str, Any] | None
    reconciliation_evidence: str | None
    updated_ns: int


class RecoveryJournal:
    """SQLite FULL-sync journal owned only by the process supervisor."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self._construct(path, authority=None)

    @classmethod
    def _open_with_authority(
        cls,
        path: str | Path,
        authority: RecoveryAuthorityVerifier,
    ) -> RecoveryJournal:
        journal = cls.__new__(cls)
        journal._construct(path, authority=authority)
        return journal

    def _construct(
        self,
        path: str | Path,
        *,
        authority: RecoveryAuthorityVerifier | None,
    ) -> None:
        self.path = Path(path)
        new_store = not self.path.exists()
        self._closed_sentinel = self.path.with_suffix(self.path.suffix + ".closed")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._gate_lock = Lock()
        self._authority = authority
        self._persistence_unresolved: str | None = None
        self._initialize(new_store)
        self._restore_fail_closed_gate()

    @property
    def bootstrap_challenge(self) -> str:
        """Return the store-bound challenge an installation authority must sign."""
        with closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            row = connection.execute(
                "SELECT bootstrap_challenge FROM journal_identity WHERE singleton=1"
            ).fetchone()
        if row is None or not row[0]:
            raise JournalConflict("journal installation challenge is unavailable")
        return str(row[0])

    @property
    def installation_id(self) -> str:
        with closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            row = connection.execute(
                "SELECT installation_id FROM journal_identity WHERE singleton=1"
            ).fetchone()
        if row is None or not row[0]:
            raise JournalConflict("journal installation identity is unavailable")
        return str(row[0])

    def authorize_bootstrap(self, receipt: RecoveryReceipt) -> None:
        """Open a pristine store only after consuming store-bound authority."""
        challenge = self.bootstrap_challenge
        if self._authority is None:
            raise RecoveryAuthorityError("independent recovery authority is required")
        self._require_authority_binding()
        receipt_identity = self._authority.consume_bootstrap(receipt, challenge)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            intents = connection.execute("SELECT 1 FROM recovery_intent LIMIT 1").fetchone()
            identity = connection.execute(
                "SELECT bootstrap_consumed FROM journal_identity WHERE singleton=1"
            ).fetchone()
            gate = connection.execute(
                "SELECT state,evidence FROM exposure_gate WHERE singleton=1"
            ).fetchone()
            if (
                intents is not None
                or identity is None
                or bool(identity[0])
                or gate is None
                or gate[0] != "CLOSED"
                or self.installation_id != self._authority.installation_id
            ):
                raise JournalConflict("bootstrap is permitted only for a pristine closed store")
            connection.execute(
                "UPDATE exposure_gate SET state='OPEN',evidence=?,updated_ns=? WHERE singleton=1",
                (receipt_identity, time_ns()),
            )
            connection.execute(
                "UPDATE journal_identity SET bootstrap_consumed=1 WHERE singleton=1"
            )
        self._closed_sentinel.unlink(missing_ok=True)
        with self._gate_lock:
            self._persistence_unresolved = None

    @property
    def exposure_allowed(self) -> bool:
        """New exposure is forbidden after uncertain persistence or restart holds."""
        with self._gate_lock:
            return self._persistence_unresolved is None

    @property
    def persistence_unresolved_reason(self) -> str | None:
        with self._gate_lock:
            return self._persistence_unresolved

    def deny_new_exposure(self, reason: str) -> None:
        if not reason:
            raise ValueError("persistence-unresolved reason is required")
        with self._gate_lock:
            if self._persistence_unresolved is None:
                self._persistence_unresolved = reason
        self._persist_closed_sentinel(reason)
        try:
            # This path is part of the supervisor's restrictive response.  A
            # contended journal must not consume a second, implicit timeout.
            # A failed immediate write remains CLOSED in this process and a
            # restart with no durable gate row also defaults CLOSED.
            with sqlite3.connect(self.path, timeout=0.0) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute(
                    "INSERT INTO exposure_gate(singleton,state,evidence,updated_ns) "
                    "VALUES (1,'CLOSED',?,?) ON CONFLICT(singleton) DO UPDATE SET "
                    "state='CLOSED',evidence=excluded.evidence,updated_ns=excluded.updated_ns",
                    (reason, time_ns()),
                )
        except BaseException:
            # Volatile denial remains authoritative for this process.  A restart
            # treats a missing or unreadable durable gate as CLOSED.
            pass

    def _persist_closed_sentinel(self, reason: str) -> None:
        """Persist an independent fail-closed marker before touching SQLite."""
        temporary = self._closed_sentinel.with_suffix(self._closed_sentinel.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump({"state": "CLOSED", "reason": reason}, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._closed_sentinel)
        except BaseException:
            temporary.unlink(missing_ok=True)
            # The in-process gate remains CLOSED.  A missing store defaults
            # CLOSED and an existing OPEN database is never sufficient while
            # the supervisor has observed this failure.

    @staticmethod
    def _remaining_ms(deadline_ns: int | None) -> int:
        if deadline_ns is None:
            return 2_000
        remaining_ns = deadline_ns - monotonic_ns()
        if remaining_ns <= 0:
            raise JournalDeadlineExceeded("recovery-journal deadline expired")
        return max(1, min(2_000, remaining_ns // 1_000_000))

    @classmethod
    def _check_deadline(cls, deadline_ns: int | None) -> None:
        if deadline_ns is not None and monotonic_ns() >= deadline_ns:
            raise JournalDeadlineExceeded("recovery-journal deadline expired")

    @contextmanager
    def _connect(self, deadline_ns: int | None = None) -> Iterator[sqlite3.Connection]:
        timeout_ms = self._remaining_ms(deadline_ns)
        connection = sqlite3.connect(self.path, timeout=timeout_ms / 1_000)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA busy_timeout={timeout_ms}")
        try:
            yield connection
            self._check_deadline(deadline_ns)
            try:
                connection.commit()
            except BaseException as error:
                self.deny_new_exposure("SQLITE_COMMIT_OUTCOME_UNRESOLVED")
                raise JournalPersistenceUnresolved(
                    "recovery-journal commit durability is unresolved"
                ) from error
            self._check_deadline(deadline_ns)
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise JournalDeadlineExceeded("recovery-journal lock budget expired") from error
            raise
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, new_store: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_intent (
                    operation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    worker_pid INTEGER,
                    transaction_uncertain INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    g00_sequence TEXT NOT NULL,
                    recovery_attempts INTEGER NOT NULL,
                    recovery_result TEXT,
                    reconciliation_evidence TEXT,
                    updated_ns INTEGER NOT NULL,
                    PRIMARY KEY (operation_id, attempt_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exposure_gate (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    state TEXT NOT NULL CHECK (state IN ('OPEN','CLOSED')),
                    evidence TEXT NOT NULL,
                    updated_ns INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_terminal_conflict (
                    operation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    prior_outcome TEXT NOT NULL,
                    conflicting_outcome TEXT NOT NULL,
                    receipt_identity TEXT NOT NULL,
                    PRIMARY KEY (operation_id, attempt_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS journal_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    installation_id TEXT NOT NULL,
                    authority_fingerprint TEXT NOT NULL,
                    bootstrap_challenge TEXT NOT NULL UNIQUE,
                    bootstrap_consumed INTEGER NOT NULL DEFAULT 0 CHECK (
                        bootstrap_consumed IN (0,1)
                    ),
                    created_ns INTEGER NOT NULL
                )
                """
            )
            identity_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(journal_identity)")
            }
            if "installation_id" not in identity_columns:
                connection.execute(
                    "ALTER TABLE journal_identity ADD COLUMN installation_id TEXT "
                    "NOT NULL DEFAULT 'LEGACY_UNBOUND'"
                )
            if "authority_fingerprint" not in identity_columns:
                connection.execute(
                    "ALTER TABLE journal_identity ADD COLUMN authority_fingerprint TEXT "
                    "NOT NULL DEFAULT 'LEGACY_UNBOUND'"
                )
            if new_store:
                connection.execute(
                    "INSERT OR IGNORE INTO exposure_gate VALUES "
                    "(1,'CLOSED','BOOTSTRAP_AUTHORITY_REQUIRED',?)",
                    (time_ns(),),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO journal_identity VALUES (1,?,?,?,0,?)",
                    (
                        "UNBOUND" if self._authority is None else self._authority.installation_id,
                        "UNBOUND" if self._authority is None else self._authority.policy_fingerprint,
                        secrets.token_hex(32),
                        time_ns(),
                    ),
                )
            else:
                # An installation that predates challenge tracking is never a
                # candidate for first bootstrap.  Record it as already used.
                connection.execute(
                    "INSERT OR IGNORE INTO journal_identity VALUES (1,?,?,?,1,?)",
                    (
                        "LEGACY_UNBOUND",
                        "LEGACY_UNBOUND",
                        secrets.token_hex(32),
                        time_ns(),
                    ),
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admission_evidence (
                    operation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    timeproof_identity TEXT NOT NULL UNIQUE,
                    authority_identity TEXT NOT NULL,
                    authority_generation INTEGER NOT NULL,
                    authority_epoch INTEGER NOT NULL,
                    admitted_ns INTEGER NOT NULL,
                    PRIMARY KEY (operation_id, attempt_id)
                )
                """
            )

    def _restore_fail_closed_gate(self) -> None:
        """A restart never skips an unresolved financial-effect boundary."""
        with closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            gate = connection.execute(
                "SELECT state,evidence FROM exposure_gate WHERE singleton=1"
            ).fetchone()
            row = connection.execute(
                "SELECT state FROM recovery_intent WHERE state IN (?, ?, ?, ?) LIMIT 1",
                (
                    RecoveryState.EFFECT_POSSIBLE.value,
                    RecoveryState.AMBIGUOUS_HOLD.value,
                    RecoveryState.UNRESOLVED.value,
                    RecoveryState.PERSISTENCE_UNRESOLVED.value,
                ),
            ).fetchone()
        if row is not None:
            self.deny_new_exposure(f"RESTART_WITH_{row[0]}")
        elif self._closed_sentinel.exists():
            self.deny_new_exposure("RESTART_WITH_FAIL_CLOSED_SENTINEL")
        elif gate is None or gate[0] != "OPEN":
            self.deny_new_exposure(
                "RESTART_WITH_MISSING_GATE" if gate is None else f"RESTART_WITH_{gate[1]}"
            )
        else:
            self.deny_new_exposure("RESTART_REQUALIFICATION_REQUIRED")

    def requalify_exposure(
        self,
        *,
        operation_id: str,
        request_digest: str,
        receipt: RecoveryReceipt,
    ) -> None:
        """Open only after exact authenticated terminal reconciliation."""
        if not operation_id or not request_digest or self._authority is None:
            raise RecoveryAuthorityError("independent terminal authority is required")
        self._require_authority_binding()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_digest,state,attempt_id,g00_sequence "
                "FROM recovery_intent WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None or row[0] != request_digest or row[1] not in (
                RecoveryState.RECOVERED_COMMITTED.value,
                RecoveryState.RECOVERED_DENIED.value,
            ):
                raise JournalConflict("terminal reconciliation does not match durable state")
            expected_outcome = (
                RecoveryTerminalOutcome.COMMITTED
                if row[1] == RecoveryState.RECOVERED_COMMITTED.value
                else RecoveryTerminalOutcome.DENIED
            )
            unresolved = connection.execute(
                "SELECT 1 FROM recovery_intent WHERE state IN (?, ?, ?, ?) LIMIT 1",
                (
                    RecoveryState.EFFECT_POSSIBLE.value,
                    RecoveryState.AMBIGUOUS_HOLD.value,
                    RecoveryState.UNRESOLVED.value,
                    RecoveryState.PERSISTENCE_UNRESOLVED.value,
                ),
            ).fetchone()
            if unresolved is not None:
                raise JournalConflict("another operation still requires restrictive recovery")
            receipt_identity = self._authority.consume_terminal(
                receipt,
                store_challenge=self.bootstrap_challenge,
                operation_id=operation_id,
                request_digest=request_digest,
                attempt_id=str(row[2]),
                g00_sequence=str(row[3]),
                expected_outcome=expected_outcome,
            )
            connection.execute(
                "UPDATE exposure_gate SET state='OPEN',evidence=?,updated_ns=? WHERE singleton=1",
                (receipt_identity, time_ns()),
            )
        try:
            self._closed_sentinel.unlink(missing_ok=True)
        except OSError as error:
            self.deny_new_exposure("FAIL_CLOSED_SENTINEL_CLEAR_FAILED")
            raise JournalPersistenceUnresolved(
                "fail-closed sentinel could not be cleared"
            ) from error
        with self._gate_lock:
            self._persistence_unresolved = None

    def _require_authority_binding(self) -> None:
        if self._authority is None:
            raise RecoveryAuthorityError("independent recovery authority is required")
        with closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            row = connection.execute(
                "SELECT installation_id,authority_fingerprint FROM journal_identity "
                "WHERE singleton=1"
            ).fetchone()
        expected = (
            self._authority.installation_id,
            self._authority.policy_fingerprint,
        )
        if row != expected:
            raise RecoveryAuthorityError("journal recovery-authority binding mismatch")

    def reserve_admission_evidence(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        request_digest: str,
        timeproof_identity: str,
        authority_identity: str,
        authority_generation: int,
        authority_epoch: int,
        admitted_ns: int,
        deadline_ns: int | None = None,
    ) -> None:
        """Durably consume attempt and TimeProof identities before dispatch."""
        try:
            with self._connect(deadline_ns) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO admission_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        attempt_id,
                        request_digest,
                        timeproof_identity,
                        authority_identity,
                        authority_generation,
                        authority_epoch,
                        admitted_ns,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise JournalConflict("attempt or TimeProof identity was already consumed") from error

    def prepare_protected_dispatch(
        self,
        *,
        operation_id: str,
        request_digest: str,
        attempt_id: str,
        worker_pid: int,
        g00_sequence: str,
        timeproof_identity: str,
        authority_identity: str,
        authority_generation: int,
        authority_epoch: int,
        admitted_ns: int,
        deadline_ns: int | None = None,
    ) -> None:
        """Atomically persist admission, worker ownership and effect fencing."""
        try:
            with self._connect(deadline_ns) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO admission_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (operation_id, attempt_id, request_digest, timeproof_identity,
                     authority_identity, authority_generation, authority_epoch, admitted_ns),
                )
                connection.execute(
                    "INSERT INTO recovery_intent VALUES (?, ?, ?, ?, 1, ?, ?, 0, NULL, ?, ?)",
                    (operation_id, attempt_id, request_digest, worker_pid,
                     RecoveryState.EFFECT_POSSIBLE.value, g00_sequence,
                     "PROTECTED_DISPATCH_AUTHORIZED", time_ns()),
                )
        except sqlite3.IntegrityError as error:
            raise JournalConflict(
                "operation, attempt or TimeProof identity was already consumed or conflicts"
            ) from error

    def record_intent(
        self,
        *,
        operation_id: str,
        request_digest: str,
        attempt_id: str,
        g00_sequence: str,
        deadline_ns: int | None = None,
    ) -> None:
        with self._connect(deadline_ns) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT request_digest, g00_sequence FROM recovery_intent "
                "WHERE operation_id=? AND attempt_id=?",
                (operation_id, attempt_id),
            ).fetchone()
            if existing is not None:
                if existing != (request_digest, g00_sequence):
                    raise JournalConflict("operation/attempt identity conflicts with durable intent")
                return
            connection.execute(
                "INSERT INTO recovery_intent VALUES (?, ?, ?, NULL, 0, ?, ?, 0, NULL, NULL, ?)",
                (
                    operation_id,
                    attempt_id,
                    request_digest,
                    RecoveryState.INTENT.value,
                    g00_sequence,
                    time_ns(),
                ),
            )

    def record_capacity_denied(
        self,
        *,
        operation_id: str,
        request_digest: str,
        attempt_id: str,
        g00_sequence: str,
        deadline_ns: int | None = None,
    ) -> None:
        self.record_intent(
            operation_id=operation_id,
            request_digest=request_digest,
            attempt_id=attempt_id,
            g00_sequence=g00_sequence,
            deadline_ns=deadline_ns,
        )
        self._transition(
            operation_id,
            attempt_id,
            RecoveryState.CAPACITY_DENIED,
            False,
            deadline_ns=deadline_ns,
        )

    def bind_worker(
        self,
        operation_id: str,
        attempt_id: str,
        worker_pid: int,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        with self._connect(deadline_ns) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE recovery_intent SET worker_pid=?, updated_ns=? "
                "WHERE operation_id=? AND attempt_id=? AND state=?",
                (worker_pid, time_ns(), operation_id, attempt_id, RecoveryState.INTENT.value),
            ).rowcount
            if changed != 1:
                raise JournalConflict("worker can bind only to a current intent")

    def mark_effect_possible(
        self,
        operation_id: str,
        attempt_id: str,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        """Durably fence dispatch before a protected effect can occur."""
        self._transition(
            operation_id,
            attempt_id,
            RecoveryState.EFFECT_POSSIBLE,
            True,
            expected_state=RecoveryState.INTENT,
            reconciliation_evidence="PROTECTED_DISPATCH_AUTHORIZED",
            deadline_ns=deadline_ns,
        )

    def hold(
        self,
        operation_id: str,
        attempt_id: str,
        evidence: str,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        self._transition(
            operation_id,
            attempt_id,
            RecoveryState.AMBIGUOUS_HOLD,
            True,
            reconciliation_evidence=evidence,
            deadline_ns=deadline_ns,
        )

    def unresolved(
        self,
        operation_id: str,
        attempt_id: str,
        evidence: str,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        # A failed readback must retain the explicit protective hold.  The
        # recovery_attempts counter and evidence distinguish it from an
        # unattempted hold without weakening the state itself.
        self._transition(
            operation_id,
            attempt_id,
            RecoveryState.AMBIGUOUS_HOLD,
            True,
            increment_recovery=True,
            reconciliation_evidence=evidence,
            deadline_ns=deadline_ns,
        )

    def resolved(
        self,
        operation_id: str,
        attempt_id: str,
        *,
        committed: bool,
        result: dict[str, Any],
        evidence: str,
        deadline_ns: int | None = None,
    ) -> None:
        expected_status = "COMMITTED" if committed else "DENIED"
        if type(committed) is not bool or result.get("status") != expected_status:
            raise JournalConflict("worker acknowledgement must contain an exact terminal status")
        state = (
            RecoveryState.RECOVERED_COMMITTED if committed else RecoveryState.RECOVERED_DENIED
        )
        self._transition(
            operation_id,
            attempt_id,
            state,
            False,
            increment_recovery=True,
            result=result,
            reconciliation_evidence=evidence,
            expected_state=RecoveryState.EFFECT_POSSIBLE,
            deadline_ns=deadline_ns,
        )

    def resolve_authenticated_terminal(
        self,
        operation_id: str,
        attempt_id: str,
        request_digest: str,
        receipt: RecoveryReceipt,
        *,
        deadline_ns: int | None = None,
    ) -> RecoveryTerminalOutcome:
        """Consume exact terminal authority before clearing an unresolved hold."""
        if self._authority is None:
            raise RecoveryAuthorityError("independent terminal authority is required")
        self._require_authority_binding()
        record = self.get(operation_id, attempt_id, deadline_ns=deadline_ns)
        if record is None or record.request_digest != request_digest:
            raise JournalConflict("terminal recovery does not match durable identity")
        allowed_states = {
            RecoveryState.AMBIGUOUS_HOLD,
            RecoveryState.UNRESOLVED,
            RecoveryState.RECOVERED_COMMITTED,
            RecoveryState.RECOVERED_DENIED,
        }
        if record.state not in allowed_states:
            raise JournalConflict("terminal recovery requires an unresolved durable hold")
        if type(receipt) is not RecoveryReceipt:
            raise RecoveryAuthorityError("recovery requires an exact receipt capability")
        outcome = receipt.terminal_outcome
        if type(outcome) is not RecoveryTerminalOutcome:
            raise RecoveryAuthorityError("terminal recovery outcome is missing or not terminal")
        receipt_identity = self._authority.consume_terminal(
            receipt,
            store_challenge=self.bootstrap_challenge,
            operation_id=operation_id,
            request_digest=request_digest,
            attempt_id=attempt_id,
            g00_sequence=record.g00_sequence,
            expected_outcome=outcome,
        )
        result = {
            "operation_id": operation_id,
            "request_digest": request_digest,
            "status": outcome.value,
            "result_code": "AUTHENTICATED_TERMINAL_READBACK",
            "capital_effect_minor": 0,
            "liability_effect_minor": 0,
        }
        target_state = (
            RecoveryState.RECOVERED_COMMITTED
            if outcome is RecoveryTerminalOutcome.COMMITTED
            else RecoveryState.RECOVERED_DENIED
        )
        conflict = False
        with self._connect(deadline_ns) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT request_digest,g00_sequence,state FROM recovery_intent "
                "WHERE operation_id=? AND attempt_id=?",
                (operation_id, attempt_id),
            ).fetchone()
            if current is None or current[:2] != (request_digest, record.g00_sequence):
                raise JournalConflict("terminal recovery durable identity changed")
            state = RecoveryState(current[2])
            retained_conflict = connection.execute(
                "SELECT 1 FROM recovery_terminal_conflict "
                "WHERE operation_id=? AND attempt_id=?",
                (operation_id, attempt_id),
            ).fetchone()
            if retained_conflict is not None:
                conflict = True
            elif state in {RecoveryState.RECOVERED_COMMITTED, RecoveryState.RECOVERED_DENIED}:
                if state is target_state:
                    raise JournalConflict("terminal recovery is already resolved")
                connection.execute(
                    "INSERT INTO recovery_terminal_conflict VALUES (?,?,?,?,?)",
                    (operation_id, attempt_id, state.value, target_state.value, receipt_identity),
                )
                conflict = True
            elif state not in {RecoveryState.AMBIGUOUS_HOLD, RecoveryState.UNRESOLVED}:
                raise JournalConflict("terminal recovery requires an unresolved durable hold")
            if conflict:
                connection.execute(
                    "UPDATE recovery_intent SET state=?,transaction_uncertain=1,"
                    "reconciliation_evidence=?,updated_ns=? "
                    "WHERE operation_id=? AND attempt_id=?",
                    (
                        RecoveryState.AMBIGUOUS_HOLD.value,
                        "CONFLICTING_AUTHENTICATED_TERMINAL_EVIDENCE",
                        time_ns(), operation_id, attempt_id,
                    ),
                )
                connection.execute(
                    "UPDATE exposure_gate SET state='CLOSED',evidence=?,updated_ns=? "
                    "WHERE singleton=1",
                    ("CONFLICTING_AUTHENTICATED_TERMINAL_EVIDENCE", time_ns()),
                )
            else:
                connection.execute(
                    "UPDATE recovery_intent SET state=?,transaction_uncertain=0,"
                    "recovery_attempts=recovery_attempts+1,recovery_result=?,"
                    "reconciliation_evidence=?,updated_ns=? "
                    "WHERE operation_id=? AND attempt_id=?",
                    (
                        target_state.value, json.dumps(result, sort_keys=True),
                        receipt_identity, time_ns(), operation_id, attempt_id,
                    ),
                )
        if conflict:
            self.deny_new_exposure("CONFLICTING_AUTHENTICATED_TERMINAL_EVIDENCE")
            raise JournalConflict("conflicting authenticated terminal evidence requires a hold")
        return outcome

    def failed_before_effect(
        self,
        operation_id: str,
        attempt_id: str,
        evidence: str,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        self._transition(
            operation_id,
            attempt_id,
            RecoveryState.FAILED_BEFORE_EFFECT,
            False,
            reconciliation_evidence=evidence,
            deadline_ns=deadline_ns,
        )

    def _transition(
        self,
        operation_id: str,
        attempt_id: str,
        state: RecoveryState,
        uncertain: bool,
        *,
        increment_recovery: bool = False,
        result: dict[str, Any] | None = None,
        reconciliation_evidence: str | None = None,
        expected_state: RecoveryState | None = None,
        deadline_ns: int | None = None,
    ) -> None:
        with self._connect(deadline_ns) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not uncertain:
                prior = connection.execute(
                    "SELECT state FROM recovery_intent WHERE operation_id=? AND attempt_id=?",
                    (operation_id, attempt_id),
                ).fetchone()
                conflict = connection.execute(
                    "SELECT 1 FROM recovery_terminal_conflict "
                    "WHERE operation_id=? AND attempt_id=?",
                    (operation_id, attempt_id),
                ).fetchone()
                if conflict is not None or (
                    prior is not None and prior[0] in {
                        RecoveryState.AMBIGUOUS_HOLD.value,
                        RecoveryState.UNRESOLVED.value,
                        RecoveryState.PERSISTENCE_UNRESOLVED.value,
                    }
                ):
                    raise JournalConflict("clearing a recovery hold requires authenticated terminal evidence")
            state_clause = "" if expected_state is None else " AND state=?"
            parameters: tuple[Any, ...] = (
                int(uncertain),
                state.value,
                int(increment_recovery),
                None if result is None else json.dumps(result, sort_keys=True),
                reconciliation_evidence,
                time_ns(),
                operation_id,
                attempt_id,
            )
            if expected_state is not None:
                parameters += (expected_state.value,)
            changed = connection.execute(
                """
                UPDATE recovery_intent
                   SET transaction_uncertain=?, state=?,
                       recovery_attempts=recovery_attempts+?, recovery_result=?,
                       reconciliation_evidence=?, updated_ns=?
                 WHERE operation_id=? AND attempt_id=?""" + state_clause,
                parameters,
            ).rowcount
            if changed != 1:
                raise JournalConflict("recovery transition target does not exist")

    def get(
        self,
        operation_id: str,
        attempt_id: str,
        *,
        deadline_ns: int | None = None,
    ) -> RecoveryRecord | None:
        with self._connect(deadline_ns) as connection:
            row = connection.execute(
                "SELECT operation_id, request_digest, attempt_id, worker_pid, "
                "transaction_uncertain, state, g00_sequence, recovery_attempts, "
                "recovery_result, reconciliation_evidence, updated_ns "
                "FROM recovery_intent WHERE operation_id=? AND attempt_id=?",
                (operation_id, attempt_id),
            ).fetchone()
        if row is None:
            return None
        result = None if row[8] is None else json.loads(str(row[8]))
        return RecoveryRecord(
            operation_id=str(row[0]),
            request_digest=str(row[1]),
            attempt_id=str(row[2]),
            worker_pid=None if row[3] is None else int(row[3]),
            transaction_uncertain=bool(row[4]),
            state=RecoveryState(str(row[5])),
            g00_sequence=str(row[6]),
            recovery_attempts=int(row[7]),
            recovery_result=result,
            reconciliation_evidence=None if row[9] is None else str(row[9]),
            updated_ns=int(row[10]),
        )

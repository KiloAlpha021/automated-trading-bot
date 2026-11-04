"""Bounded process isolation for synchronous PostgreSQL SOE attempts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import BoundedSemaphore
from time import monotonic_ns
from typing import Any, Protocol, TypeAlias

from .recovery_journal import (
    JournalDeadlineExceeded,
    JournalPersistenceUnresolved,
    RecoveryJournal,
    RecoveryState,
)
from .recovery_authority import RecoveryReceipt, RecoveryTerminalOutcome


class IsolatedStatus(StrEnum):
    COMMITTED = "COMMITTED"
    DENIED = "DENIED"
    RESTRICTIVE_UNKNOWN = "RESTRICTIVE_UNKNOWN"


class RecoveryOnlyViolation(RuntimeError):
    """The cold-spawn supervisor was invoked as an admitted operation executor."""


@dataclass(frozen=True, slots=True)
class IsolatedCommand:
    operation_id: str
    request_digest: str
    attempt_id: str
    g00_sequence: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not all((self.operation_id, self.request_digest, self.attempt_id, self.g00_sequence)):
            raise ValueError("isolated command identities must be non-empty")


@dataclass(frozen=True, slots=True)
class IsolatedOutcome:
    operation_id: str
    request_digest: str
    status: IsolatedStatus
    result_code: str
    capital_effect_minor: int = 0
    liability_effect_minor: int = 0

    def __post_init__(self) -> None:
        if self.capital_effect_minor < 0 or self.liability_effect_minor < 0:
            raise ValueError("financial effects must be non-negative")
        if self.status is not IsolatedStatus.COMMITTED and (
            self.capital_effect_minor or self.liability_effect_minor
        ):
            raise ValueError("non-committed outcome cannot contain financial effects")


@dataclass(frozen=True, slots=True)
class SupervisorResult:
    outcome: IsolatedOutcome
    worker_pid: int | None
    worker_terminated: bool
    worker_reaped: bool
    elapsed_ms: float
    journal_state: RecoveryState


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


WorkerHandler: TypeAlias = Callable[[IsolatedCommand, CancelEvent], IsolatedOutcome | None]


class ProcessIsolationSupervisor:
    """Own bounded, out-of-band recovery readback workers only.

    Admitted financial operations use ``PreconnectedWorkerPoolSupervisor`` and
    positively READY persistent workers.  This supervisor intentionally keeps
    Windows spawn outside that path and cannot execute a protected operation.
    """

    def __init__(
        self,
        journal: RecoveryJournal,
        *,
        max_workers: int = 4,
        operation_timeout_ms: int = 3_000,
        cancellation_grace_ms: int = 250,
        recovery_budget_ms: int = 750,
        reap_budget_ms: int = 500,
    ) -> None:
        if not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        if reap_budget_ms < 200:
            raise ValueError("process reap budget must preserve at least 200ms cleanup")
        if operation_timeout_ms <= cancellation_grace_ms + recovery_budget_ms + reap_budget_ms + 50:
            raise ValueError("operation deadline must include execution, cancellation, and recovery")
        self._journal = journal
        self._capacity = BoundedSemaphore(max_workers)
        self._operation_timeout_ms = operation_timeout_ms
        self._cancellation_grace_ms = cancellation_grace_ms
        self._recovery_budget_ms = recovery_budget_ms
        self._reap_budget_ms = reap_budget_ms

    @staticmethod
    def _unknown(command: IsolatedCommand, code: str) -> IsolatedOutcome:
        return IsolatedOutcome(
            command.operation_id,
            command.request_digest,
            IsolatedStatus.RESTRICTIVE_UNKNOWN,
            code,
        )

    @staticmethod
    def _validate(command: IsolatedCommand, outcome: IsolatedOutcome) -> None:
        if (
            outcome.operation_id != command.operation_id
            or outcome.request_digest != command.request_digest
        ):
            raise ValueError("worker outcome has a foreign identity or digest")

    @staticmethod
    def _remaining_seconds(deadline_ns: int) -> float:
        return max(0.0, (deadline_ns - monotonic_ns()) / 1_000_000_000)

    def execute(
        self,
        command: IsolatedCommand,
        handler: WorkerHandler,
        recovery_handler: WorkerHandler,
    ) -> SupervisorResult:
        del command, handler, recovery_handler
        raise RecoveryOnlyViolation(
            "ProcessIsolationSupervisor is recovery-only; admitted operations require "
            "positively READY preconnected workers"
        )

    def recover(
        self,
        command: IsolatedCommand,
        receipt: RecoveryReceipt | None,
        *,
        timeout_ms: int,
    ) -> SupervisorResult:
        """Resolve a hold only from an authenticated, single-use terminal receipt."""
        if timeout_ms <= self._reap_budget_ms + 50:
            raise ValueError("recovery timeout must include the process reap budget")
        started_ns = monotonic_ns()
        deadline_ns = started_ns + timeout_ms * 1_000_000
        record = self._journal.get(
            command.operation_id,
            command.attempt_id,
            deadline_ns=deadline_ns,
        )
        if record is None:
            raise ValueError("recovery requires an existing durable intent")
        if (
            record.request_digest != command.request_digest
            or record.g00_sequence != command.g00_sequence
        ):
            raise ValueError("recovery command conflicts with durable identity")
        allowed_states = {RecoveryState.AMBIGUOUS_HOLD, RecoveryState.UNRESOLVED}
        if receipt is not None:
            # Authenticated contradictory evidence must be able to restore a hold.
            allowed_states.update(
                {RecoveryState.RECOVERED_COMMITTED, RecoveryState.RECOVERED_DENIED}
            )
        if record.state not in allowed_states:
            raise ValueError("recovery requires an unresolved durable hold")
        if receipt is not None and type(receipt) is not RecoveryReceipt:
            raise TypeError("recovery accepts only an authenticated terminal receipt")
        if not self._capacity.acquire(blocking=False):
            return SupervisorResult(
                self._unknown(command, "RECOVERY_CAPACITY_EXHAUSTED"),
                None,
                False,
                True,
                (monotonic_ns() - started_ns) / 1_000_000,
                record.state,
            )
        try:
            if receipt is not None:
                try:
                    terminal = self._journal.resolve_authenticated_terminal(
                        command.operation_id,
                        command.attempt_id,
                        command.request_digest,
                        receipt,
                        deadline_ns=deadline_ns,
                    )
                    updated = self._journal.get(
                        command.operation_id,
                        command.attempt_id,
                        deadline_ns=deadline_ns,
                    )
                    if updated is None:
                        raise JournalPersistenceUnresolved(
                            "recovery result disappeared before response"
                        )
                    expected_state = (
                        RecoveryState.RECOVERED_COMMITTED
                        if terminal is RecoveryTerminalOutcome.COMMITTED
                        else RecoveryState.RECOVERED_DENIED
                    )
                    if updated.state is not expected_state or updated.transaction_uncertain:
                        final = self._unknown(command, "RECOVERY_TERMINAL_EVIDENCE_CONFLICT")
                    else:
                        final = IsolatedOutcome(
                            command.operation_id,
                            command.request_digest,
                            (
                                IsolatedStatus.COMMITTED
                                if terminal is RecoveryTerminalOutcome.COMMITTED
                                else IsolatedStatus.DENIED
                            ),
                            "AUTHENTICATED_TERMINAL_READBACK",
                        )
                    final_state = updated.state
                except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
                    final = self._unknown(command, "RECOVERY_JOURNAL_UNRESOLVED")
                    final_state = RecoveryState.AMBIGUOUS_HOLD
            else:
                try:
                    self._journal.unresolved(
                        command.operation_id,
                        command.attempt_id,
                        "RECOVERY_READBACK_MISSING_OR_DEADLINE",
                        deadline_ns=deadline_ns,
                    )
                    updated = self._journal.get(
                        command.operation_id,
                        command.attempt_id,
                        deadline_ns=deadline_ns,
                    )
                    if updated is None:
                        raise JournalPersistenceUnresolved(
                            "unresolved recovery hold disappeared before response"
                        )
                    final_state = updated.state
                except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
                    final_state = RecoveryState.AMBIGUOUS_HOLD
                final = self._unknown(command, "AMBIGUOUS_OUTCOME_HELD")
            return SupervisorResult(
                final,
                None,
                False,
                True,
                (monotonic_ns() - started_ns) / 1_000_000,
                final_state,
            )
        finally:
            self._capacity.release()

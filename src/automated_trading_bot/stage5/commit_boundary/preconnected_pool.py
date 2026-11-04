"""Fixed-capacity preconnected workers for bounded SOE operations."""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from multiprocessing.connection import wait
from threading import Lock
from time import monotonic_ns, time_ns
from typing import Any, Protocol, TypeAlias, cast

from .g00_auth import G00AdmissionReceipt, G00Verifier
from .process_isolation import (
    IsolatedCommand,
    IsolatedOutcome,
    IsolatedStatus,
    ProcessIsolationSupervisor,
    SupervisorResult,
)
from .recovery_authority import RecoveryReceipt
from .recovery_journal import (
    JournalDeadlineExceeded,
    JournalPersistenceUnresolved,
    RecoveryJournal,
    RecoveryState,
)


@dataclass(frozen=True, slots=True)
class WorkerReadiness:
    connection_identity: str
    transaction_idle: bool
    authority_cache_empty: bool
    timeproof_cache_empty: bool
    operation_cache_empty: bool

    @property
    def safe(self) -> bool:
        return all(
            (
                self.transaction_idle,
                self.authority_cache_empty,
                self.timeproof_cache_empty,
                self.operation_cache_empty,
            )
        )


@dataclass(frozen=True, slots=True)
class WorkerStartupDiagnostic:
    """Parent-observed evidence for a bounded, pre-admission worker bootstrap."""

    pid: int
    expected_interpreter: str
    child_interpreter: str | None
    phases: tuple[tuple[str, int], ...]
    exit_code: int | None
    failure: str | None


@dataclass(frozen=True, slots=True)
class VerifiedAdmissionEvidence:
    operation_id: str
    request_digest: str
    attempt_id: str
    scope: str
    authority_identity: str
    authority_generation: int
    authority_epoch: int
    authority_issued_ns: int
    timeproof_identity: str
    timeproof_issued_ns: int
    g00_receipt: G00AdmissionReceipt

    def validate(self, command: IsolatedCommand, admitted_ns: int) -> None:
        scope = command.payload.get("scope")
        if (
            self.operation_id != command.operation_id
            or self.request_digest != command.request_digest
            or self.attempt_id != command.attempt_id
            or self.scope != scope
            or self.g00_receipt.operation_id != command.operation_id
            or self.g00_receipt.request_digest != command.request_digest
            or self.g00_receipt.scope != scope
            or str(self.g00_receipt.ticket_sequence) != command.g00_sequence
        ):
            raise ValueError("admission evidence has a foreign operation context")
        if not self.authority_identity or not self.timeproof_identity:
            raise ValueError("authenticated authority and TimeProof identities are required")
        if self.authority_generation < 0 or self.authority_epoch < 0:
            raise ValueError("authority generation and epoch must be non-negative")
        wall_now_ns = time_ns()
        if self.authority_issued_ns <= 0 or self.timeproof_issued_ns <= 0:
            raise ValueError("authority and TimeProof issuance times are required")
        if self.authority_issued_ns > wall_now_ns or self.timeproof_issued_ns > wall_now_ns:
            raise ValueError("future authority or TimeProof evidence is forbidden")
        if self.g00_receipt.ticket_sequence < 0 or self.g00_receipt.generation < 0:
            raise ValueError("authenticated G00 ticket and generation are required")


class G00AdmissionRejected(RuntimeError):
    """The authenticated ticket conflicts with a pending/effective restriction."""


class G00AdmissionController:
    """Serialize protected admission against authenticated restrictive cutovers."""

    def __init__(
        self,
        *,
        issuer_identity: str,
        deployment_identity: str,
        recipient: str,
        generation: int,
        issuer_epoch: int,
        trust_root: G00Verifier,
    ) -> None:
        if (
            not all((issuer_identity, deployment_identity, recipient))
            or generation < 0
            or issuer_epoch < 0
        ):
            raise ValueError("G00 issuer and generation are required")
        self._issuer_identity = issuer_identity
        self._deployment_identity = deployment_identity
        self._recipient = recipient
        self._generation = generation
        self._issuer_epoch = issuer_epoch
        self._trust_root = trust_root
        self._state_lock = Lock()
        self._serialization_lock = Lock()
        self._pending_cutover: int | None = None
        self._effective_cutover: int | None = None

    def request_restriction(self, cutover_sequence: int) -> None:
        """Announce a writer before it waits for the protected boundary."""
        if cutover_sequence < 0:
            raise ValueError("cutover sequence must be non-negative")
        with self._state_lock:
            if self._pending_cutover is None:
                self._pending_cutover = cutover_sequence
            else:
                self._pending_cutover = min(self._pending_cutover, cutover_sequence)

    def publish_restriction(self, cutover_sequence: int) -> None:
        """Linearize the cutover after all earlier admitted work completes."""
        self.request_restriction(cutover_sequence)
        with self._serialization_lock, self._state_lock:
            if self._effective_cutover is None:
                self._effective_cutover = cutover_sequence
            else:
                self._effective_cutover = min(self._effective_cutover, cutover_sequence)
            if self._pending_cutover == cutover_sequence:
                self._pending_cutover = None

    @contextmanager
    def admit(
        self, evidence: VerifiedAdmissionEvidence, admitted_ns: int
    ) -> Iterator[None]:
        """Hold the no-overtake boundary through protected acknowledgement."""
        self.verify(evidence, admitted_ns)
        with self.admit_verified(evidence):
            yield

    def verify(
        self, evidence: VerifiedAdmissionEvidence, admitted_ns: int
    ) -> None:
        """Authenticate immutable G00 evidence before any worker is leased."""
        try:
            self._trust_root.verify_and_consume(
                evidence.g00_receipt,
                expected_issuer=self._issuer_identity,
                expected_deployment=self._deployment_identity,
                expected_recipient=self._recipient,
                operation_id=evidence.operation_id,
                request_digest=evidence.request_digest,
                scope=evidence.scope,
                generation=self._generation,
                issuer_epoch=self._issuer_epoch,
                admitted_ns=admitted_ns,
            )
        except ValueError as error:
            raise G00AdmissionRejected(str(error)) from error

    @contextmanager
    def admit_verified(
        self, evidence: VerifiedAdmissionEvidence
    ) -> Iterator[None]:
        """Recheck cutover ordering and hold it through acknowledgement."""
        with self._serialization_lock:
            with self._state_lock:
                cutovers = [
                    value
                    for value in (self._pending_cutover, self._effective_cutover)
                    if value is not None
                ]
                if cutovers and evidence.g00_receipt.ticket_sequence >= min(cutovers):
                    raise G00AdmissionRejected("ticket conflicts with restrictive cutover")
            yield

    def check_cutover(self, evidence: VerifiedAdmissionEvidence) -> None:
        """Reject an already-pending cutover before any worker lease."""
        with self._state_lock:
            cutovers = [
                value
                for value in (self._pending_cutover, self._effective_cutover)
                if value is not None
            ]
            if cutovers and evidence.g00_receipt.ticket_sequence >= min(cutovers):
                raise G00AdmissionRejected("ticket conflicts with restrictive cutover")


@dataclass(frozen=True, slots=True)
class WorkerPoolTelemetry:
    configured_capacity: int
    live: int
    idle: int
    leased: int
    retiring: int
    terminated: int
    reaped: int
    unresolved: int


class WorkerLifecycle(StrEnum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    LEASED = "LEASED"
    RETIRING = "RETIRING"
    TERMINATED = "TERMINATED"
    REAPED = "REAPED"
    UNRESOLVED = "UNRESOLVED"


class PersistentWorkerSession(Protocol):
    def readiness(self) -> WorkerReadiness: ...

    def execute(
        self,
        command: IsolatedCommand,
        evidence: VerifiedAdmissionEvidence,
    ) -> IsolatedOutcome: ...

    def close(self) -> None: ...


SessionFactory: TypeAlias = Callable[[], PersistentWorkerSession]
EvidenceProvider: TypeAlias = Callable[[IsolatedCommand, int], VerifiedAdmissionEvidence]


@dataclass(slots=True)
class _WorkerSlot:
    process: Any
    control: Any
    events: Any
    readiness: WorkerReadiness
    retired: bool = False
    leased: bool = False
    lifecycle: WorkerLifecycle = WorkerLifecycle.IDLE
    handles_closed: bool = False


def _command_from_dict(payload: Mapping[str, Any]) -> IsolatedCommand:
    return IsolatedCommand(
        operation_id=str(payload["operation_id"]),
        request_digest=str(payload["request_digest"]),
        attempt_id=str(payload["attempt_id"]),
        g00_sequence=str(payload["g00_sequence"]),
        payload=cast(Mapping[str, Any], payload["payload"]),
    )


def _evidence_from_dict(payload: Mapping[str, Any]) -> VerifiedAdmissionEvidence:
    receipt_payload = cast(Mapping[str, Any], payload["g00_receipt"])
    return VerifiedAdmissionEvidence(
        operation_id=str(payload["operation_id"]),
        request_digest=str(payload["request_digest"]),
        attempt_id=str(payload["attempt_id"]),
        scope=str(payload["scope"]),
        authority_identity=str(payload["authority_identity"]),
        authority_generation=int(payload["authority_generation"]),
        authority_epoch=int(payload["authority_epoch"]),
        authority_issued_ns=int(payload["authority_issued_ns"]),
        timeproof_identity=str(payload["timeproof_identity"]),
        timeproof_issued_ns=int(payload["timeproof_issued_ns"]),
        g00_receipt=G00AdmissionReceipt(
            issuer_identity=str(receipt_payload["issuer_identity"]),
            deployment_identity=str(receipt_payload["deployment_identity"]),
            recipient=str(receipt_payload["recipient"]),
            operation_id=str(receipt_payload["operation_id"]),
            request_digest=str(receipt_payload["request_digest"]),
            scope=str(receipt_payload["scope"]),
            generation=int(receipt_payload["generation"]),
            issuer_epoch=int(receipt_payload["issuer_epoch"]),
            ticket_sequence=int(receipt_payload["ticket_sequence"]),
            observed_cutover_sequence=(
                None
                if receipt_payload["observed_cutover_sequence"] is None
                else int(receipt_payload["observed_cutover_sequence"])
            ),
            issued_ns=int(receipt_payload["issued_ns"]),
            valid_until_ns=int(receipt_payload["valid_until_ns"]),
            nonce=str(receipt_payload["nonce"]),
            key_id=str(receipt_payload["key_id"]),
            signature=str(receipt_payload["signature"]),
        ),
    )


def _persistent_worker_entry(factory: SessionFactory, receiver: Any, sender: Any) -> None:
    session: PersistentWorkerSession | None = None
    try:
        sender.send(("STARTUP", "CHILD_ENTRY", monotonic_ns(), os.getpid(), sys.executable))
        session = factory()
        sender.send(
            ("STARTUP", "SESSION_CREATED", monotonic_ns(), os.getpid(), sys.executable)
        )
        readiness = session.readiness()
        sender.send(("READY", asdict(readiness)))
        while True:
            message = receiver.recv()
            if message[0] == "STOP":
                sender.send(("STOPPED", None))
                return
            command = _command_from_dict(cast(Mapping[str, Any], message[1]))
            evidence = _evidence_from_dict(cast(Mapping[str, Any], message[2]))
            admitted_ns = int(message[3])
            evidence.validate(command, admitted_ns)
            if not session.readiness().safe:
                raise RuntimeError("worker is not IDLE and cache-empty at admission")
            outcome = session.execute(command, evidence)
            post = session.readiness()
            sender.send(("OUTCOME", asdict(outcome), asdict(post)))
    except EOFError:
        return
    except BaseException as error:
        try:
            sender.send(("ERROR", f"{type(error).__name__}:{error}"))
        except BaseException:
            pass
    finally:
        if session is not None:
            try:
                session.close()
            except BaseException:
                pass
        receiver.close()
        sender.close()


class PreconnectedWorkerPoolSupervisor:
    """Own a fixed pool; ambiguity retires a worker and never replays its operation."""

    def __init__(
        self,
        journal: RecoveryJournal,
        session_factory: SessionFactory,
        *,
        g00_controller: G00AdmissionController,
        capacity: int = 2,
        operation_timeout_ms: int = 1_000,
        retire_budget_ms: int = 200,
        readiness_timeout_ms: int = 10_000,
        require_protected_factory: bool = False,
    ) -> None:
        if not 1 <= capacity <= 16:
            raise ValueError("capacity must be between 1 and 16")
        if retire_budget_ms < 200:
            raise ValueError("worker-retirement budget must preserve at least 200ms cleanup")
        if operation_timeout_ms <= retire_budget_ms + 50:
            raise ValueError("operation timeout must reserve a worker-retirement budget")
        self._protected_registrar: (
            Callable[[IsolatedCommand, VerifiedAdmissionEvidence], None] | None
        )
        self._protected_registrar_owner: Any | None
        if require_protected_factory:
            from .psycopg_adapter import ProtectedPsycopgPersistentSessionFactory

            if type(session_factory) is not ProtectedPsycopgPersistentSessionFactory:
                raise ValueError(
                    "protected worker pool requires the sealed PostgreSQL session factory"
                )
            self._protected_registrar_owner = session_factory.open_registrar()
            self._protected_registrar = self._protected_registrar_owner.register
            session_factory = session_factory.runtime_factory()
        else:
            self._protected_registrar_owner = None
            self._protected_registrar = None
        self._journal = journal
        self._factory = session_factory
        self._capacity = capacity
        self._operation_timeout_ms = operation_timeout_ms
        self._retire_budget_ms = retire_budget_ms
        self._readiness_timeout_ms = readiness_timeout_ms
        self._g00 = g00_controller
        self._context = multiprocessing.get_context("spawn")
        self._lock = Lock()
        self._replacement_lock = Lock()
        self._slots: list[_WorkerSlot] = []
        self._available: deque[_WorkerSlot] = deque()
        self._lifecycle_history: dict[int, WorkerLifecycle] = {}
        self._termination_requested: set[int] = set()
        self._reaped_pids: set[int] = set()
        self._unresolved_pids: set[int] = set()
        self._startup_diagnostics: dict[int, WorkerStartupDiagnostic] = {}
        try:
            for _ in range(capacity):
                slot = self._spawn_slot(readiness_timeout_ms)
                self._slots.append(slot)
                self._available.append(slot)
                self._lifecycle_history[int(slot.process.pid)] = WorkerLifecycle.IDLE
        except BaseException:
            self.close(timeout_ms=readiness_timeout_ms)
            raise

    @staticmethod
    def _unknown(command: IsolatedCommand, code: str) -> IsolatedOutcome:
        return IsolatedOutcome(
            command.operation_id,
            command.request_digest,
            IsolatedStatus.RESTRICTIVE_UNKNOWN,
            code,
        )

    @staticmethod
    def _remaining(deadline_ns: int) -> float:
        return max(0.0, (deadline_ns - monotonic_ns()) / 1_000_000_000)

    @staticmethod
    def _readiness(payload: Mapping[str, Any]) -> WorkerReadiness:
        return WorkerReadiness(
            connection_identity=str(payload["connection_identity"]),
            transaction_idle=bool(payload["transaction_idle"]),
            authority_cache_empty=bool(payload["authority_cache_empty"]),
            timeproof_cache_empty=bool(payload["timeproof_cache_empty"]),
            operation_cache_empty=bool(payload["operation_cache_empty"]),
        )

    def _spawn_slot(self, timeout_ms: int) -> _WorkerSlot:
        worker_receive, supervisor_send = self._context.Pipe(duplex=False)
        supervisor_receive, worker_send = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_persistent_worker_entry,
            args=(self._factory, worker_receive, worker_send),
            daemon=False,
        )
        started = False
        published = False
        stages: list[tuple[str, int]] = []
        child_interpreter: str | None = None
        failure: str | None = None
        observed_exit_code: int | None = None
        deadline_ns = monotonic_ns() + timeout_ms * 1_000_000
        try:
            process.start()
            started = True
            if process.pid is None:
                raise RuntimeError("started worker has no process identity")
            worker_receive.close()
            worker_send.close()
            while True:
                ready = wait(
                    (supervisor_receive, process.sentinel),
                    timeout=self._remaining(deadline_ns),
                )
                if not ready:
                    failure = "BOOTSTRAP_TIMEOUT"
                    raise RuntimeError(
                        "preconnected worker readiness timed out; "
                        f"pid={process.pid}; interpreter={child_interpreter}; stages={stages}"
                    )
                if supervisor_receive not in ready:
                    process.join(timeout=0)
                    observed_exit_code = process.exitcode
                    phase = "PRE_CHILD_ENTRY_EXIT" if not stages else "BOOTSTRAP_EXIT"
                    failure = phase
                    raise RuntimeError(
                        "preconnected worker exited before readiness; "
                        f"phase={phase}; pid={process.pid}; exit_code={observed_exit_code}; "
                        f"interpreter={child_interpreter}; stages={stages}"
                    )
                try:
                    message = cast(tuple[Any, ...], supervisor_receive.recv())
                except EOFError as error:
                    process.join(timeout=0)
                    observed_exit_code = process.exitcode
                    phase = "PRE_CHILD_ENTRY_EXIT" if not stages else "BOOTSTRAP_EOF"
                    failure = phase
                    raise RuntimeError(
                        "preconnected worker bootstrap pipe closed; "
                        f"phase={phase}; pid={process.pid}; exit_code={observed_exit_code}; "
                        f"interpreter={child_interpreter}; stages={stages}"
                    ) from error
                if message[0] != "STARTUP":
                    break
                if len(message) != 5:
                    raise RuntimeError(f"invalid worker startup event: {message}")
                event_pid = int(message[3])
                event_interpreter = os.path.normcase(os.path.abspath(str(message[4])))
                expected_interpreter = os.path.normcase(os.path.abspath(sys.executable))
                if event_pid != process.pid:
                    failure = "CHILD_PID_MISMATCH"
                    raise RuntimeError(
                        f"worker startup PID mismatch: expected={process.pid}; actual={event_pid}"
                    )
                if event_interpreter != expected_interpreter:
                    child_interpreter = event_interpreter
                    failure = "WRONG_INTERPRETER"
                    raise RuntimeError(
                        "worker interpreter provenance mismatch: "
                        f"expected={expected_interpreter}; actual={event_interpreter}"
                    )
                child_interpreter = event_interpreter
                stages.append((str(message[1]), int(message[2])))
            if message[0] != "READY":
                failure = "READINESS_ERROR"
                raise RuntimeError(
                    f"preconnected worker failed readiness: {message}; stages={stages}"
                )
            readiness = self._readiness(cast(Mapping[str, Any], message[1]))
            if not readiness.safe:
                failure = "UNSAFE_READINESS"
                raise RuntimeError(
                    "preconnected worker readiness was not IDLE and cache-empty"
                )
            slot = _WorkerSlot(process, supervisor_send, supervisor_receive, readiness)
            if not process.is_alive():
                process.join(timeout=0)
                observed_exit_code = process.exitcode
                failure = "EXIT_AFTER_READY"
                raise RuntimeError(
                    "preconnected worker exited after READY but before capacity publication; "
                    f"pid={process.pid}; exit_code={observed_exit_code}"
                )
            self._startup_diagnostics[process.pid] = WorkerStartupDiagnostic(
                pid=process.pid,
                expected_interpreter=os.path.normcase(os.path.abspath(sys.executable)),
                child_interpreter=child_interpreter,
                phases=tuple(stages),
                exit_code=None,
                failure=None,
            )
            published = True
            return slot
        finally:
            if not published:
                cleanup_failed = False
                for endpoint in (worker_receive, worker_send):
                    try:
                        endpoint.close()
                    except BaseException:
                        cleanup_failed = True
                if started:
                    if observed_exit_code is None and not process.is_alive():
                        process.join(timeout=0)
                        observed_exit_code = process.exitcode
                    process.join(timeout=0.05)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=0.5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=0.5)
                    if observed_exit_code is None:
                        observed_exit_code = process.exitcode
                    if process.pid is not None:
                        self._startup_diagnostics[process.pid] = WorkerStartupDiagnostic(
                            pid=process.pid,
                            expected_interpreter=os.path.normcase(
                                os.path.abspath(sys.executable)
                            ),
                            child_interpreter=child_interpreter,
                            phases=tuple(stages),
                            exit_code=observed_exit_code,
                            failure=failure or "STARTUP_FAILURE",
                        )
                for endpoint in (supervisor_send, supervisor_receive):
                    try:
                        endpoint.close()
                    except BaseException:
                        cleanup_failed = True
                if started and process.is_alive():
                    raise RuntimeError("unpublished worker could not be terminated and reaped")
                if cleanup_failed:
                    raise RuntimeError("unpublished worker pipe cleanup was incomplete")

    @property
    def startup_diagnostics(self) -> dict[int, WorkerStartupDiagnostic]:
        """Parent-observed bootstrap provenance keyed by child PID."""
        with self._lock:
            return dict(self._startup_diagnostics)

    def readiness(self) -> tuple[WorkerReadiness, ...]:
        with self._lock:
            return tuple(slot.readiness for slot in self._slots if not slot.retired)

    def telemetry(self) -> WorkerPoolTelemetry:
        with self._lock:
            live = sum(
                int(slot.process.is_alive() and slot.lifecycle is not WorkerLifecycle.REAPED)
                for slot in self._slots
            )
            idle = sum(
                int(slot.lifecycle is WorkerLifecycle.IDLE and slot.process.is_alive())
                for slot in self._slots
            )
            leased = sum(int(slot.lifecycle is WorkerLifecycle.LEASED) for slot in self._slots)
            retiring = sum(
                int(slot.lifecycle is WorkerLifecycle.RETIRING) for slot in self._slots
            )
            return WorkerPoolTelemetry(
                self._capacity,
                live,
                idle,
                leased,
                retiring,
                len(self._termination_requested),
                len(self._reaped_pids),
                len(self._unresolved_pids),
            )

    def _lease(self) -> _WorkerSlot | None:
        with self._lock:
            if not self._available:
                return None
            slot = self._available.popleft()
            slot.leased = True
            slot.lifecycle = WorkerLifecycle.LEASED
            self._lifecycle_history[int(slot.process.pid)] = WorkerLifecycle.LEASED
            return slot

    def _release(self, slot: _WorkerSlot, readiness: WorkerReadiness) -> None:
        with self._lock:
            slot.readiness = readiness
            slot.leased = False
            if not slot.retired and readiness.safe and slot.process.is_alive():
                slot.lifecycle = WorkerLifecycle.IDLE
                self._lifecycle_history[int(slot.process.pid)] = WorkerLifecycle.IDLE
                self._available.append(slot)

    def _retire(self, slot: _WorkerSlot, deadline_ns: int) -> tuple[bool, bool]:
        pid = int(slot.process.pid)
        with self._lock:
            slot.retired = True
            slot.leased = False
            slot.lifecycle = WorkerLifecycle.RETIRING
            self._lifecycle_history[pid] = WorkerLifecycle.RETIRING
            try:
                self._available.remove(slot)
            except ValueError:
                pass
        terminated = False
        if slot.process.is_alive():
            slot.process.join(min(0.01, self._remaining(deadline_ns)))
        if slot.process.is_alive():
            terminated = True
            with self._lock:
                self._termination_requested.add(pid)
            slot.process.terminate()
            slot.process.join(self._remaining(deadline_ns))
        if slot.process.is_alive():
            slot.process.kill()
            slot.process.join(self._remaining(deadline_ns))
        reaped = not slot.process.is_alive()
        handles_closed = True
        for endpoint in (slot.control, slot.events):
            try:
                endpoint.close()
            except BaseException:
                handles_closed = False
        slot.handles_closed = handles_closed
        with self._lock:
            if reaped and handles_closed:
                slot.lifecycle = WorkerLifecycle.REAPED
                self._reaped_pids.add(pid)
                self._unresolved_pids.discard(pid)
            else:
                slot.lifecycle = WorkerLifecycle.UNRESOLVED
                self._unresolved_pids.add(pid)
            self._lifecycle_history[pid] = slot.lifecycle
        return terminated, reaped

    def execute(
        self,
        command: IsolatedCommand,
        evidence_provider: EvidenceProvider,
    ) -> SupervisorResult:
        admitted_ns = monotonic_ns()
        deadline_ns = admitted_ns + self._operation_timeout_ms * 1_000_000
        response_deadline_ns = deadline_ns - self._retire_budget_ms * 1_000_000
        if not self._journal.exposure_allowed:
            return SupervisorResult(
                self._unknown(command, "JOURNAL_PERSISTENCE_UNRESOLVED"),
                None,
                False,
                False,
                (monotonic_ns() - admitted_ns) / 1_000_000,
                RecoveryState.PERSISTENCE_UNRESOLVED,
            )
        evidence = evidence_provider(command, admitted_ns)
        evidence.validate(command, admitted_ns)
        self._g00.verify(evidence, admitted_ns)
        self._g00.check_cutover(evidence)
        if self._protected_registrar is not None:
            self._protected_registrar(command, evidence)
        slot = self._lease()
        if slot is None:
            try:
                self._journal.record_capacity_denied(
                    operation_id=command.operation_id,
                    request_digest=command.request_digest,
                    attempt_id=command.attempt_id,
                    g00_sequence=command.g00_sequence,
                    deadline_ns=deadline_ns,
                )
                journal_state = RecoveryState.CAPACITY_DENIED
                result_code = "WORKER_CAPACITY_EXHAUSTED"
            except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
                self._journal.deny_new_exposure("CAPACITY_DENIAL_NOT_CONFIRMED")
                journal_state = RecoveryState.UNRESOLVED
                result_code = "JOURNAL_PERSISTENCE_UNRESOLVED"
            return SupervisorResult(
                self._unknown(command, result_code),
                None,
                False,
                False,
                (monotonic_ns() - admitted_ns) / 1_000_000,
                journal_state,
            )
        effect_possible = False
        try:
            self._journal.prepare_protected_dispatch(
                operation_id=command.operation_id,
                request_digest=command.request_digest,
                attempt_id=command.attempt_id,
                worker_pid=int(slot.process.pid),
                g00_sequence=command.g00_sequence,
                timeproof_identity=evidence.timeproof_identity,
                authority_identity=evidence.authority_identity,
                authority_generation=evidence.authority_generation,
                authority_epoch=evidence.authority_epoch,
                admitted_ns=admitted_ns,
                deadline_ns=deadline_ns,
            )
            with self._g00.admit_verified(evidence):
                effect_possible = True
                slot.control.send(("RUN", asdict(command), asdict(evidence), admitted_ns))
                message: tuple[str, Any] | tuple[str, Any, Any] | None = None
                remaining = self._remaining(response_deadline_ns)
                if remaining > 0 and slot.events.poll(remaining):
                    message = cast(tuple[str, Any] | tuple[str, Any, Any], slot.events.recv())
            if message is not None and message[0] == "OUTCOME":
                if len(message) != 3:
                    raise RuntimeError("worker outcome omitted post-operation readiness")
                outcome_message = message
                payload = cast(Mapping[str, Any], outcome_message[1])
                outcome = IsolatedOutcome(
                    operation_id=str(payload["operation_id"]),
                    request_digest=str(payload["request_digest"]),
                    status=IsolatedStatus(str(payload["status"])),
                    result_code=str(payload["result_code"]),
                    capital_effect_minor=int(payload["capital_effect_minor"]),
                    liability_effect_minor=int(payload["liability_effect_minor"]),
                )
                if outcome.operation_id != command.operation_id or outcome.request_digest != command.request_digest:
                    raise ValueError("worker outcome has a foreign identity or digest")
                readiness = self._readiness(
                    cast(Mapping[str, Any], outcome_message[2])
                )
                if not readiness.safe:
                    raise RuntimeError("acknowledged worker did not return IDLE and cache-empty")
                self._journal.resolved(
                    command.operation_id,
                    command.attempt_id,
                    committed=outcome.status is IsolatedStatus.COMMITTED,
                    result=asdict(outcome),
                    evidence="PRECONNECTED_WORKER_ACKNOWLEDGED",
                    deadline_ns=deadline_ns,
                )
                self._release(slot, readiness)
                record = self._journal.get(
                    command.operation_id,
                    command.attempt_id,
                    deadline_ns=deadline_ns,
                )
                assert record is not None
                return SupervisorResult(
                    outcome,
                    int(slot.process.pid),
                    False,
                    False,
                    (monotonic_ns() - admitted_ns) / 1_000_000,
                    record.state,
                )
            hold_confirmed = False
            ambiguity_evidence = "PRECONNECTED_WORKER_TIMEOUT_OR_AMBIGUOUS_COMMIT"
            if message is not None and message[0] == "ERROR" and len(message) >= 2:
                ambiguity_evidence = f"PRECONNECTED_WORKER_ERROR:{message[1]}"
            try:
                self._journal.hold(
                    command.operation_id,
                    command.attempt_id,
                    ambiguity_evidence,
                    deadline_ns=deadline_ns,
                )
                confirmed = self._journal.get(
                    command.operation_id,
                    command.attempt_id,
                    deadline_ns=deadline_ns,
                )
                hold_confirmed = (
                    confirmed is not None
                    and confirmed.state is RecoveryState.AMBIGUOUS_HOLD
                )
            except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
                self._journal.deny_new_exposure("AMBIGUOUS_HOLD_NOT_CONFIRMED")
            terminated, reaped = self._retire(slot, deadline_ns)
            journal_state = (
                RecoveryState.AMBIGUOUS_HOLD
                if hold_confirmed
                else RecoveryState.PERSISTENCE_UNRESOLVED
            )
            return SupervisorResult(
                self._unknown(
                    command,
                    "AMBIGUOUS_OUTCOME_HELD"
                    if hold_confirmed
                    else "JOURNAL_PERSISTENCE_UNRESOLVED",
                ),
                int(slot.process.pid),
                terminated,
                reaped,
                (monotonic_ns() - admitted_ns) / 1_000_000,
                journal_state,
            )
        except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
            self._journal.deny_new_exposure("JOURNAL_OPERATION_NOT_CONFIRMED")
            terminated, reaped = self._retire(slot, deadline_ns)
            return SupervisorResult(
                self._unknown(command, "JOURNAL_PERSISTENCE_UNRESOLVED"),
                int(slot.process.pid),
                terminated,
                reaped,
                (monotonic_ns() - admitted_ns) / 1_000_000,
                RecoveryState.PERSISTENCE_UNRESOLVED,
            )
        except BaseException:
            record = None
            if not effect_possible:
                try:
                    record = self._journal.get(
                        command.operation_id,
                        command.attempt_id,
                        deadline_ns=deadline_ns,
                    )
                except JournalDeadlineExceeded:
                    record = None
            if effect_possible or (
                record is not None
                and record.state in (RecoveryState.EFFECT_POSSIBLE, RecoveryState.AMBIGUOUS_HOLD)
            ):
                try:
                    self._journal.hold(
                        command.operation_id,
                        command.attempt_id,
                        "POST_DISPATCH_FAILURE_REQUIRES_RECONCILIATION",
                        deadline_ns=deadline_ns,
                    )
                except (JournalDeadlineExceeded, JournalPersistenceUnresolved):
                    self._journal.deny_new_exposure(
                        "POST_DISPATCH_HOLD_NOT_CONFIRMED"
                    )
            elif record is not None and record.state is RecoveryState.INTENT:
                self._journal.failed_before_effect(
                    command.operation_id,
                    command.attempt_id,
                    "ADMISSION_OR_EVIDENCE_FAILURE",
                    deadline_ns=deadline_ns,
                )
            self._retire(slot, deadline_ns)
            raise

    def replace_retired(self, *, timeout_ms: int | None = None) -> int:
        """Replace retired capacity using the configured cold-start budget.

        Replacement occurs outside admitted-operation timing.  A caller may
        provide a larger maintenance budget, but cannot shorten the qualified
        readiness budget and thereby turn Windows spawn latency into capacity.
        """
        readiness_timeout_ms = max(timeout_ms or 0, self._readiness_timeout_ms)
        replacements = 0
        with self._replacement_lock:
            with self._lock:
                retired = [slot for slot in self._slots if slot.retired]
            for slot in retired:
                replacement = self._spawn_slot(readiness_timeout_ms)
                published = False
                try:
                    with self._lock:
                        if slot not in self._slots or not slot.retired:
                            raise RuntimeError("retired slot changed during replacement")
                        self._slots.remove(slot)
                        self._slots.append(replacement)
                        self._available.append(replacement)
                        self._lifecycle_history[
                            int(replacement.process.pid)
                        ] = WorkerLifecycle.IDLE
                        published = True
                    replacements += 1
                finally:
                    if not published:
                        cleanup_deadline = (
                            monotonic_ns() + readiness_timeout_ms * 1_000_000
                        )
                        self._retire(replacement, cleanup_deadline)
        return replacements

    def recover(
        self,
        command: IsolatedCommand,
        receipt: RecoveryReceipt | None,
        *,
        timeout_ms: int,
    ) -> SupervisorResult:
        helper = ProcessIsolationSupervisor(self._journal, max_workers=1)
        return helper.recover(command, receipt, timeout_ms=timeout_ms)

    def close(self, *, timeout_ms: int = 1_000) -> None:
        deadline_ns = monotonic_ns() + timeout_ms * 1_000_000
        for slot in self._slots:
            if not slot.retired and slot.process.is_alive():
                try:
                    slot.control.send(("STOP",))
                except (BrokenPipeError, EOFError, OSError):
                    pass
            self._retire(slot, deadline_ns)
        if self._protected_registrar_owner is not None:
            self._protected_registrar_owner.close()

    def __enter__(self) -> PreconnectedWorkerPoolSupervisor:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

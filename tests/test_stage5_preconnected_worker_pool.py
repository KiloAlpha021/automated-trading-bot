from __future__ import annotations

import multiprocessing
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import automated_trading_bot.stage5.commit_boundary.preconnected_pool as pool_module
from automated_trading_bot.stage5.commit_boundary import (
    G00AdmissionController,
    G00AdmissionReceipt,
    G00AdmissionRejected,
    IsolatedCommand,
    IsolatedOutcome,
    IsolatedStatus,
    JournalConflict,
    JournalDeadlineExceeded,
    JournalPersistenceUnresolved,
    PreconnectedWorkerPoolSupervisor,
    RecoveryAuthorityError,
    RecoveryReceiptType,
    RecoveryState,
    RecoveryTerminalOutcome,
    SyntheticG00TrustRoot,
    VerifiedAdmissionEvidence,
    WorkerReadiness,
)
from automated_trading_bot.stage5.commit_boundary.recovery_journal import RecoveryJournal as _RecoveryJournal
from stage5_recovery_authority_fixtures import (
    bootstrap_receipt,
    open_journal,
    terminal_receipt,
)


TEST_G00_KEY = b"Q05-SYNTHETIC-G00-KEY-ONLY-NOT-PRODUCTION-0001"
TEST_G00_ROOT = SyntheticG00TrustRoot({"q05-key-1": TEST_G00_KEY})


def RecoveryJournal(path: Path, **kwargs: object) -> _RecoveryJournal:
    """Construct an explicitly authorised synthetic first-bootstrap journal."""
    if kwargs:
        raise TypeError("caller-supplied recovery verification policy is forbidden")
    return open_journal(path)


def _create_closed_journal_for_loss_test(path: str) -> None:
    journal = RecoveryJournal(Path(path))
    journal.deny_new_exposure("PERSISTENCE_UNRESOLVED")


def g00_controller(
    root: SyntheticG00TrustRoot = TEST_G00_ROOT,
) -> G00AdmissionController:
    return G00AdmissionController(
        issuer_identity="SYNTHETIC-G00",
        deployment_identity="Q05-ISOLATED",
        recipient="SOE-01",
        generation=1,
        issuer_epoch=1,
        trust_root=root,
    )


class FakeSession:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.closed = False

    def readiness(self) -> WorkerReadiness:
        return WorkerReadiness(
            connection_identity=f"FAKE-{self.mode}",
            transaction_idle=not self.closed and self.mode != "unsafe-readiness",
            authority_cache_empty=True,
            timeproof_cache_empty=True,
            operation_cache_empty=True,
        )

    def execute(
        self,
        command: IsolatedCommand,
        evidence: VerifiedAdmissionEvidence,
    ) -> IsolatedOutcome:
        del evidence
        if self.mode == "hang":
            time.sleep(5)
        operation_id = "FOREIGN" if self.mode == "foreign" else command.operation_id
        return IsolatedOutcome(
            operation_id,
            command.request_digest,
            IsolatedStatus.COMMITTED,
            "COMMITTED",
            5000,
            5000,
        )

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class FakeFactory:
    mode: str = "success"

    def __call__(self) -> FakeSession:
        if self.mode == "readiness-error":
            raise RuntimeError("injected readiness failure")
        if self.mode == "readiness-eof":
            os._exit(17)
        return FakeSession(self.mode)


def command(operation_id: str = "POOL-1") -> IsolatedCommand:
    return IsolatedCommand(
        operation_id=operation_id,
        request_digest=f"DIGEST-{operation_id}",
        attempt_id=f"ATTEMPT-{operation_id}",
        g00_sequence="10",
        payload={"scope": "ACCOUNT:TEST", "effect": 5000},
    )


def fresh_evidence(
    item: IsolatedCommand,
    admitted_ns: int,
    root: SyntheticG00TrustRoot = TEST_G00_ROOT,
) -> VerifiedAdmissionEvidence:
    issued_ns = max(admitted_ns, time.monotonic_ns())
    issued_wall_ns = time.time_ns()
    receipt = root.sign(
        G00AdmissionReceipt(
            issuer_identity="SYNTHETIC-G00",
            deployment_identity="Q05-ISOLATED",
            recipient="SOE-01",
            operation_id=item.operation_id,
            request_digest=item.request_digest,
            scope="ACCOUNT:TEST",
            generation=1,
            issuer_epoch=1,
            ticket_sequence=int(item.g00_sequence),
            observed_cutover_sequence=None,
            issued_ns=issued_ns,
            valid_until_ns=issued_ns + 5_000_000_000,
            nonce=f"G00-{item.attempt_id}-{uuid.uuid4()}",
            key_id="q05-key-1",
        )
    )
    return VerifiedAdmissionEvidence(
        operation_id=item.operation_id,
        request_digest=item.request_digest,
        attempt_id=item.attempt_id,
        scope="ACCOUNT:TEST",
        authority_identity="SYNTHETIC-I01",
        authority_generation=1,
        authority_epoch=1,
        authority_issued_ns=issued_wall_ns,
        timeproof_identity=f"TP-{item.attempt_id}",
        timeproof_issued_ns=issued_wall_ns,
        g00_receipt=receipt,
    )


def test_pool_preconnects_idle_workers_and_reuses_only_clean_acknowledged_worker(
    tmp_path: Path,
) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    with PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=1000,
        retire_budget_ms=200,
    ) as pool:
        readiness = pool.readiness()
        assert len(readiness) == 1 and readiness[0].safe
        diagnostic = next(iter(pool.startup_diagnostics.values()))
        assert diagnostic.phases[0][0] == "CHILD_ENTRY"
        assert diagnostic.child_interpreter == diagnostic.expected_interpreter
        assert diagnostic.failure is None and diagnostic.exit_code is None
        result = pool.execute(command(), fresh_evidence)
        assert result.outcome.status is IsolatedStatus.COMMITTED
        assert result.elapsed_ms <= 1000
        assert result.journal_state is RecoveryState.RECOVERED_COMMITTED
        assert len(pool.readiness()) == 1
    assert multiprocessing.active_children() == []


def test_admitted_operation_cannot_construct_recovery_only_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_recovery_supervisor(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("admitted operation reached recovery-only cold-spawn path")

    monkeypatch.setattr(
        pool_module, "ProcessIsolationSupervisor", forbidden_recovery_supervisor
    )
    with PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=1000,
        retire_budget_ms=200,
    ) as pool:
        result = pool.execute(command("NO-LEGACY-PATH"), fresh_evidence)

    assert result.outcome.status is IsolatedStatus.COMMITTED
    assert result.elapsed_ms <= 1000
    assert multiprocessing.active_children() == []


def test_restart_is_closed_for_missing_or_unresolved_durable_gate(tmp_path: Path) -> None:
    path = tmp_path / "restart-gate.sqlite"
    journal = RecoveryJournal(path)
    journal.deny_new_exposure("PERSISTENCE_UNRESOLVED")
    assert not RecoveryJournal(path).exposure_allowed


def test_total_store_loss_cannot_manufacture_first_bootstrap(tmp_path: Path) -> None:
    path = tmp_path / "lost-store.sqlite"
    process = multiprocessing.get_context("spawn").Process(
        target=_create_closed_journal_for_loss_test, args=(str(path),)
    )
    process.start()
    process.join(5)
    assert process.exitcode == 0
    path.unlink()
    path.with_suffix(path.suffix + ".closed").unlink(missing_ok=True)

    restarted = _RecoveryJournal(path)

    assert not restarted.exposure_allowed
    assert restarted.persistence_unresolved_reason == "RESTART_WITH_BOOTSTRAP_AUTHORITY_REQUIRED"
    with pytest.raises(RecoveryAuthorityError):
        restarted.authorize_bootstrap(bootstrap_receipt(restarted))


def test_empty_or_corrupt_store_cannot_open_exposure(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    assert not _RecoveryJournal(empty).exposure_allowed

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(sqlite3.DatabaseError):
        _RecoveryJournal(corrupt)


def test_bootstrap_authority_is_single_use_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "single-use-bootstrap.sqlite"
    journal = open_journal(path, authorize_new=False)
    receipt = bootstrap_receipt(journal)
    journal.authorize_bootstrap(receipt)

    restarted = open_journal(path, authorize_new=False)
    assert not restarted.exposure_allowed
    with pytest.raises(RecoveryAuthorityError, match="replay"):
        restarted.authorize_bootstrap(receipt)


def test_fail_closed_sentinel_overrides_stale_open_database(tmp_path: Path) -> None:
    path = tmp_path / "stale-open.sqlite"
    journal = RecoveryJournal(path)
    journal.deny_new_exposure("SQLITE_GATE_WRITE_UNCERTAIN")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE exposure_gate SET state='OPEN',evidence='STALE_OPEN' WHERE singleton=1"
        )

    restarted = _RecoveryJournal(path)

    assert not restarted.exposure_allowed
    assert restarted.persistence_unresolved_reason == "RESTART_WITH_FAIL_CLOSED_SENTINEL"


def test_one_terminal_operation_cannot_reopen_another_unresolved_hold(tmp_path: Path) -> None:
    path = tmp_path / "global-gate.sqlite"
    journal = RecoveryJournal(path)
    for operation in ("A", "B"):
        journal.record_intent(
            operation_id=operation,
            request_digest=f"DIGEST-{operation}",
            attempt_id=f"ATTEMPT-{operation}",
            g00_sequence="10",
        )
        journal.mark_effect_possible(operation, f"ATTEMPT-{operation}")
        journal.hold(operation, f"ATTEMPT-{operation}", "AMBIGUOUS")
    journal.resolve_authenticated_terminal(
        "A",
        "ATTEMPT-A",
        "DIGEST-A",
        terminal_receipt(journal, "A", "DIGEST-A", RecoveryTerminalOutcome.COMMITTED),
    )
    journal.deny_new_exposure("PERSISTENCE_UNRESOLVED")

    with pytest.raises(JournalConflict, match="another operation"):
        journal.requalify_exposure(
            operation_id="A",
            request_digest="DIGEST-A",
            receipt=terminal_receipt(
                journal,
                "A",
                "DIGEST-A",
                RecoveryTerminalOutcome.COMMITTED,
            ),
        )
    assert not journal.exposure_allowed

    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM exposure_gate")
    assert not RecoveryJournal(path).exposure_allowed


def test_gate_requires_exact_authenticated_terminal_reconciliation(tmp_path: Path) -> None:
    path = tmp_path / "requalify.sqlite"
    journal = RecoveryJournal(path)
    item = command("REQUALIFY")
    journal.record_intent(
        operation_id=item.operation_id,
        request_digest=item.request_digest,
        attempt_id=item.attempt_id,
        g00_sequence=item.g00_sequence,
    )
    journal.bind_worker(item.operation_id, item.attempt_id, 123)
    journal.mark_effect_possible(
        item.operation_id,
        item.attempt_id,
    )
    journal.deny_new_exposure("PERSISTENCE_UNRESOLVED")
    with pytest.raises((RecoveryAuthorityError, JournalConflict)):
        journal.requalify_exposure(
            operation_id=item.operation_id,
            request_digest=item.request_digest,
            receipt=terminal_receipt(
                journal,
                item.operation_id,
                item.request_digest,
                RecoveryTerminalOutcome.COMMITTED,
                receipt_type=RecoveryReceiptType.BOOTSTRAP,
            ),
        )
    assert not journal.exposure_allowed


def test_ambiguous_worker_is_held_retired_and_replaced(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory("hang"),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=700,
        retire_budget_ms=200,
    )
    try:
        result = pool.execute(command(), fresh_evidence)
        record = journal.get("POOL-1", "ATTEMPT-POOL-1")
        assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
        assert result.elapsed_ms <= 750
        assert result.worker_terminated and result.worker_reaped
        assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD
        assert record.transaction_uncertain
        # Replacement cold start uses the configured 10s readiness budget and
        # remains outside the already-completed admitted operation.
        assert pool.replace_retired() == 1
        assert len(pool.readiness()) == 1 and pool.readiness()[0].safe
    finally:
        pool.close()
    assert multiprocessing.active_children() == []


def test_stale_or_wrong_context_evidence_is_rejected(tmp_path: Path) -> None:
    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
    )
    item = command()

    def stale(
        current: IsolatedCommand,
        admitted_ns: int,
    ) -> VerifiedAdmissionEvidence:
        evidence = fresh_evidence(current, admitted_ns)
        return VerifiedAdmissionEvidence(
            operation_id=evidence.operation_id,
            request_digest="FOREIGN",
            attempt_id=evidence.attempt_id,
            scope=evidence.scope,
            authority_identity=evidence.authority_identity,
            authority_generation=evidence.authority_generation,
            authority_epoch=evidence.authority_epoch,
            authority_issued_ns=admitted_ns - 1,
            timeproof_identity=evidence.timeproof_identity,
            timeproof_issued_ns=admitted_ns - 1,
            g00_receipt=evidence.g00_receipt,
        )

    try:
        with pytest.raises(ValueError, match="foreign operation context"):
            pool.execute(item, stale)
    finally:
        pool.close()
    record = RecoveryJournal(tmp_path / "journal.sqlite").get(
        item.operation_id, item.attempt_id
    )
    assert record is None


def test_pool_capacity_exhaustion_denies_new_exposure(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory("hang"),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=700,
        retire_budget_ms=200,
    )
    first: list[object] = []
    thread = threading.Thread(
        target=lambda: first.append(pool.execute(command("LONG"), fresh_evidence))
    )
    try:
        thread.start()
        time.sleep(0.1)
        denied = pool.execute(command("CAPACITY"), fresh_evidence)
        thread.join(timeout=2.0)
        assert denied.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
        assert denied.outcome.result_code == "WORKER_CAPACITY_EXHAUSTED"
        assert denied.journal_state is RecoveryState.CAPACITY_DENIED
        assert len(first) == 1 and not thread.is_alive()
    finally:
        pool.close()


def test_attempt_and_timeproof_replay_are_durably_rejected(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal, FakeFactory(), g00_controller=g00_controller(), capacity=1
    )

    def same_proof(
        item: IsolatedCommand,
        admitted_ns: int,
    ) -> VerifiedAdmissionEvidence:
        evidence = fresh_evidence(item, admitted_ns)
        return VerifiedAdmissionEvidence(
            operation_id=evidence.operation_id,
            request_digest=evidence.request_digest,
            attempt_id=evidence.attempt_id,
            scope=evidence.scope,
            authority_identity=evidence.authority_identity,
            authority_generation=evidence.authority_generation,
            authority_epoch=evidence.authority_epoch,
            authority_issued_ns=evidence.authority_issued_ns,
            timeproof_identity="TP-ONE-USE",
            timeproof_issued_ns=evidence.timeproof_issued_ns,
            g00_receipt=evidence.g00_receipt,
        )

    try:
        assert pool.execute(command("FIRST"), same_proof).outcome.status is IsolatedStatus.COMMITTED
        with pytest.raises(JournalConflict, match="already consumed"):
            pool.execute(command("SECOND"), same_proof)
    finally:
        pool.close()


def test_post_dispatch_foreign_outcome_remains_ambiguous_hold(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory("foreign"),
        g00_controller=g00_controller(),
        capacity=1,
    )
    item = command("POST-DISPATCH")
    try:
        with pytest.raises(ValueError, match="foreign identity"):
            pool.execute(item, fresh_evidence)
    finally:
        pool.close()
    record = journal.get(item.operation_id, item.attempt_id)
    assert record is not None
    assert record.state is RecoveryState.AMBIGUOUS_HOLD
    assert record.transaction_uncertain


def test_journal_lock_contention_respects_operation_budget(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = RecoveryJournal(path)
    blocker = sqlite3.connect(path, timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=400,
        retire_budget_ms=200,
    )
    started = time.monotonic()
    try:
        result = pool.execute(command("LOCKED"), fresh_evidence)
    finally:
        blocker.rollback()
        blocker.close()
        pool.close()
    assert (time.monotonic() - started) < 0.75
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert result.outcome.result_code == "JOURNAL_PERSISTENCE_UNRESOLVED"
    assert result.journal_state is RecoveryState.PERSISTENCE_UNRESOLVED


def test_g00_pending_restriction_cannot_be_overtaken(tmp_path: Path) -> None:
    controller = g00_controller()
    controller.request_restriction(10)
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory(),
        capacity=1,
        g00_controller=controller,
    )
    item = command("G00-DENIED")
    try:
        with pytest.raises(G00AdmissionRejected, match="restrictive cutover"):
            pool.execute(item, fresh_evidence)
        controller.publish_restriction(10)
    finally:
        pool.close()
    record = journal.get(item.operation_id, item.attempt_id)
    assert record is None


def test_concurrent_replacement_preserves_capacity(tmp_path: Path) -> None:
    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory("hang"),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=500,
        retire_budget_ms=200,
    )
    try:
        pool.execute(command("RETIRE"), fresh_evidence)
        results: list[int] = []
        threads = [
            threading.Thread(target=lambda: results.append(pool.replace_retired(timeout_ms=3000)))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        telemetry = pool.telemetry()
        assert sorted(results) == [0, 1]
        assert telemetry.live == telemetry.configured_capacity == 1
        assert telemetry.idle == 1 and telemetry.leased == 0
    finally:
        pool.close()
    assert multiprocessing.active_children() == []


def test_persistent_worker_telemetry_does_not_report_reaped(tmp_path: Path) -> None:
    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
    )
    try:
        result = pool.execute(command("TELEMETRY"), fresh_evidence)
        telemetry = pool.telemetry()
        assert not result.worker_terminated
        assert not result.worker_reaped
        assert telemetry.live == telemetry.idle == 1
        assert telemetry.terminated == telemetry.reaped == 0
    finally:
        pool.close()


@pytest.mark.parametrize(
    "failure",
    (JournalDeadlineExceeded("injected deadline"), JournalPersistenceUnresolved("injected fsync")),
)
def test_unconfirmed_hold_is_truthful_and_blocks_new_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    pool = PreconnectedWorkerPoolSupervisor(
        journal,
        FakeFactory("hang"),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=700,
        retire_budget_ms=200,
    )

    def fail_hold(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(journal, "hold", fail_hold)
    try:
        first = pool.execute(command("PERSISTENCE-FAIL"), fresh_evidence)
        second = pool.execute(command("MUST-NOT-DISPATCH"), fresh_evidence)
        assert first.journal_state is RecoveryState.PERSISTENCE_UNRESOLVED
        assert first.outcome.result_code == "JOURNAL_PERSISTENCE_UNRESOLVED"
        assert not journal.exposure_allowed
        assert second.journal_state is RecoveryState.PERSISTENCE_UNRESOLVED
        assert second.worker_pid is None
        assert journal.get("MUST-NOT-DISPATCH", "ATTEMPT-MUST-NOT-DISPATCH") is None
    finally:
        pool.close()


def test_restart_with_effect_possible_remains_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = RecoveryJournal(path)
    journal.record_intent(
        operation_id="RESTART-HOLD",
        request_digest="DIGEST-RESTART-HOLD",
        attempt_id="ATTEMPT-RESTART-HOLD",
        g00_sequence="10",
    )
    journal.mark_effect_possible("RESTART-HOLD", "ATTEMPT-RESTART-HOLD")
    restarted = RecoveryJournal(path)
    assert not restarted.exposure_allowed
    assert restarted.persistence_unresolved_reason == "RESTART_WITH_EFFECT_POSSIBLE"


def test_g00_forgery_wrong_context_replay_and_revocation_are_rejected(
    tmp_path: Path,
) -> None:
    root = SyntheticG00TrustRoot({"q05-key-1": TEST_G00_KEY})
    controller = g00_controller(root)
    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory(),
        g00_controller=controller,
        capacity=1,
    )
    try:
        forged_item = command("FORGED")

        def forged(
            item: IsolatedCommand, admitted_ns: int
        ) -> VerifiedAdmissionEvidence:
            evidence = fresh_evidence(item, admitted_ns, root)
            return replace(
                evidence,
                g00_receipt=replace(evidence.g00_receipt, signature="00" * 32),
            )

        with pytest.raises(G00AdmissionRejected, match="signature"):
            pool.execute(forged_item, forged)
        assert pool.replace_retired(timeout_ms=3_000) == 0

        wrong_item = command("WRONG-DEPLOYMENT")

        def wrong_context(
            item: IsolatedCommand, admitted_ns: int
        ) -> VerifiedAdmissionEvidence:
            evidence = fresh_evidence(item, admitted_ns, root)
            receipt = replace(
                evidence.g00_receipt,
                deployment_identity="FOREIGN-DEPLOYMENT",
                signature="",
            )
            return replace(evidence, g00_receipt=root.sign(receipt))

        with pytest.raises(G00AdmissionRejected, match="foreign admission context"):
            pool.execute(wrong_item, wrong_context)
        assert pool.replace_retired(timeout_ms=3_000) == 0

        first = command("REPLAY")
        captured: list[VerifiedAdmissionEvidence] = []

        def capture_first(
            item: IsolatedCommand, admitted_ns: int
        ) -> VerifiedAdmissionEvidence:
            evidence = fresh_evidence(item, admitted_ns, root)
            captured.append(evidence)
            return evidence

        assert pool.execute(first, capture_first).outcome.status is IsolatedStatus.COMMITTED
        first_evidence = captured[0]
        second = IsolatedCommand(
            operation_id=first.operation_id,
            request_digest=first.request_digest,
            attempt_id="ATTEMPT-REPLAY-SECOND",
            g00_sequence=first.g00_sequence,
            payload=first.payload,
        )
        def replayed(
            _item: IsolatedCommand, admitted_ns: int
        ) -> VerifiedAdmissionEvidence:
            return replace(
                first_evidence,
                attempt_id=second.attempt_id,
                timeproof_identity="TP-REPLAY-SECOND",
                authority_issued_ns=admitted_ns,
                timeproof_issued_ns=admitted_ns,
            )

        with pytest.raises(G00AdmissionRejected, match="replayed"):
            pool.execute(second, replayed)
        assert pool.replace_retired(timeout_ms=3_000) == 0

        revoked_item = command("REVOKED")
        revoked_box: list[VerifiedAdmissionEvidence] = []

        def revoked_provider(
            item: IsolatedCommand, admitted_ns: int
        ) -> VerifiedAdmissionEvidence:
            evidence = fresh_evidence(item, admitted_ns, root)
            revoked_box.append(evidence)
            return evidence

        revoked_provider(revoked_item, time.monotonic_ns())
        root.revoke("q05-key-1")
        with pytest.raises(G00AdmissionRejected, match="revoked"):
            pool.execute(
                revoked_item,
                lambda _item, admitted_ns: replace(
                    revoked_box[0],
                    authority_issued_ns=admitted_ns,
                    timeproof_issued_ns=admitted_ns,
                ),
            )
    finally:
        pool.close()


@pytest.mark.parametrize(
    "case",
    (
        "stale-generation",
        "stale-epoch",
        "wrong-operation",
        "wrong-scope",
        "wrong-digest",
        "expired",
        "unknown-key",
    ),
)
def test_g00_stale_and_wrong_context_receipts_are_rejected(
    tmp_path: Path, case: str
) -> None:
    root = SyntheticG00TrustRoot({"q05-key-1": TEST_G00_KEY})
    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / f"{case}.sqlite"),
        FakeFactory(),
        g00_controller=g00_controller(root),
        capacity=1,
    )
    item = command(f"G00-{case}")

    def invalid(
        current: IsolatedCommand, admitted_ns: int
    ) -> VerifiedAdmissionEvidence:
        evidence = fresh_evidence(current, admitted_ns, root)
        receipt = evidence.g00_receipt
        if case == "stale-generation":
            receipt = replace(receipt, generation=0, signature="")
        elif case == "stale-epoch":
            receipt = replace(receipt, issuer_epoch=0, signature="")
        elif case == "wrong-operation":
            receipt = replace(receipt, operation_id="FOREIGN", signature="")
        elif case == "wrong-scope":
            receipt = replace(receipt, scope="ACCOUNT:FOREIGN", signature="")
        elif case == "wrong-digest":
            receipt = replace(receipt, request_digest="FOREIGN", signature="")
        elif case == "expired":
            receipt = replace(
                receipt,
                issued_ns=admitted_ns - 2,
                valid_until_ns=admitted_ns - 1,
                signature="",
            )
        else:
            receipt = replace(receipt, key_id="unknown", signature="00" * 32)
            return replace(evidence, g00_receipt=receipt)
        return replace(evidence, g00_receipt=root.sign(receipt))

    try:
        with pytest.raises((ValueError, G00AdmissionRejected)):
            pool.execute(item, invalid)
    finally:
        pool.close()
    record = RecoveryJournal(tmp_path / f"{case}.sqlite").get(
        item.operation_id, item.attempt_id
    )
    assert record is None


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    (
        ("readiness-error", "readiness"),
        (
            "readiness-eof",
            "bootstrap pipe closed|exited before readiness|readiness timed out",
        ),
        ("unsafe-readiness", "readiness"),
    ),
)
def test_failed_worker_readiness_leaves_no_untracked_child_or_pipe(
    tmp_path: Path, mode: str, expected_error: str
) -> None:
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(RuntimeError, match=expected_error):
        PreconnectedWorkerPoolSupervisor(
            RecoveryJournal(tmp_path / f"{mode}.sqlite"),
            FakeFactory(mode),
            g00_controller=g00_controller(),
            capacity=1,
            readiness_timeout_ms=1_000,
        )
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        remaining = {child.pid for child in multiprocessing.active_children()} - before
        if not remaining:
            break
        time.sleep(0.01)
    assert {child.pid for child in multiprocessing.active_children()} - before == set()


def test_spawn_start_failure_closes_every_pipe() -> None:
    class Endpoint:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Process:
        pid = None

        def start(self) -> None:
            raise OSError("injected process-start failure")

        def is_alive(self) -> bool:
            return False

    class Context:
        def __init__(self) -> None:
            self.endpoints: list[Endpoint] = []

        def Pipe(self, *, duplex: bool) -> tuple[Endpoint, Endpoint]:
            assert not duplex
            pair = (Endpoint(), Endpoint())
            self.endpoints.extend(pair)
            return pair

        def Process(self, **_kwargs: object) -> Process:
            return Process()

    supervisor = object.__new__(PreconnectedWorkerPoolSupervisor)
    context = Context()
    supervisor._context = context
    supervisor._factory = FakeFactory()
    with pytest.raises(OSError, match="process-start"):
        supervisor._spawn_slot(100)
    assert len(context.endpoints) == 4
    assert all(endpoint.closed for endpoint in context.endpoints)


@pytest.mark.parametrize(
    ("ready_mode", "expected_failure", "expected_text"),
    (
        ("dead", "PRE_CHILD_ENTRY_EXIT", "exited before readiness"),
        ("timeout", "BOOTSTRAP_TIMEOUT", "readiness timed out"),
        ("wrong-interpreter", "WRONG_INTERPRETER", "interpreter provenance mismatch"),
    ),
)
def test_parent_observes_pre_entry_death_stall_and_wrong_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    ready_mode: str,
    expected_failure: str,
    expected_text: str,
) -> None:
    class Endpoint:
        def __init__(self, messages: list[tuple[object, ...]] | None = None) -> None:
            self.messages = list(messages or [])
            self.closed = False

        def recv(self) -> tuple[object, ...]:
            return self.messages.pop(0)

        def close(self) -> None:
            self.closed = True

    class Process:
        pid = 99117
        sentinel = object()

        def __init__(self) -> None:
            self.exitcode: int | None = 17 if ready_mode == "dead" else None
            self.alive = ready_mode != "dead"

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            del timeout

        def terminate(self) -> None:
            self.alive = False
            self.exitcode = -15

        def kill(self) -> None:
            self.alive = False
            self.exitcode = -9

    class Context:
        def __init__(self) -> None:
            self.calls = 0
            self.process = Process()
            self.supervisor_receive = Endpoint(
                [
                    (
                        "STARTUP",
                        "CHILD_ENTRY",
                        time.monotonic_ns(),
                        self.process.pid,
                        "C:/foreign/python.exe",
                    )
                ]
            )

        def Pipe(self, *, duplex: bool) -> tuple[Endpoint, Endpoint]:
            assert not duplex
            self.calls += 1
            if self.calls == 1:
                return Endpoint(), Endpoint()
            return self.supervisor_receive, Endpoint()

        def Process(self, **_kwargs: object) -> Process:
            return self.process

    supervisor = object.__new__(PreconnectedWorkerPoolSupervisor)
    context = Context()
    supervisor._context = context
    supervisor._factory = FakeFactory()
    supervisor._startup_diagnostics = {}

    def fake_wait(objects: tuple[object, ...], timeout: float) -> list[object]:
        assert timeout >= 0
        if ready_mode == "dead":
            return [context.process.sentinel]
        if ready_mode == "timeout":
            return []
        return [context.supervisor_receive]

    monkeypatch.setattr(pool_module, "wait", fake_wait)
    with pytest.raises(RuntimeError, match=expected_text):
        supervisor._spawn_slot(100)

    diagnostic = supervisor._startup_diagnostics[context.process.pid]
    assert diagnostic.pid == context.process.pid
    assert diagnostic.failure == expected_failure
    assert diagnostic.exit_code is not None
    assert not context.process.is_alive()


def test_registration_failure_reaps_unpublished_replacement(tmp_path: Path) -> None:
    class RegistrationFailingList(list[object]):
        def remove(self, _value: object) -> None:
            raise RuntimeError("injected registration failure")

    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory("hang"),
        g00_controller=g00_controller(),
        capacity=1,
        operation_timeout_ms=500,
        retire_budget_ms=200,
    )
    try:
        pool.execute(command("REGISTRATION-FAIL"), fresh_evidence)
        before = {child.pid for child in multiprocessing.active_children()}
        pool._slots = RegistrationFailingList(pool._slots)
        with pytest.raises(RuntimeError, match="registration failure"):
            pool.replace_retired(timeout_ms=3_000)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            extra = {child.pid for child in multiprocessing.active_children()} - before
            if not extra:
                break
            time.sleep(0.01)
        assert {child.pid for child in multiprocessing.active_children()} - before == set()
    finally:
        pool.close()


def test_failed_termination_or_pipe_cleanup_is_reported_unresolved(
    tmp_path: Path,
) -> None:
    class UnreapableProcess:
        pid = 999001

        def is_alive(self) -> bool:
            return True

        def join(self, _timeout: float) -> None:
            return None

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    class BrokenEndpoint:
        def close(self) -> None:
            raise OSError("injected pipe-close failure")

    pool = PreconnectedWorkerPoolSupervisor(
        RecoveryJournal(tmp_path / "journal.sqlite"),
        FakeFactory(),
        g00_controller=g00_controller(),
        capacity=1,
    )
    fake = pool_module._WorkerSlot(
        UnreapableProcess(),
        BrokenEndpoint(),
        BrokenEndpoint(),
        pool.readiness()[0],
    )
    try:
        terminated, reaped = pool._retire(fake, time.monotonic_ns() + 1_000_000)
        telemetry = pool.telemetry()
        assert terminated and not reaped
        assert fake.lifecycle is pool_module.WorkerLifecycle.UNRESOLVED
        assert not fake.handles_closed
        assert telemetry.unresolved == 1
    finally:
        pool.close()

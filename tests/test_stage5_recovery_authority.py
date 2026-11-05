from __future__ import annotations

import sqlite3
import threading
import time
import gc
from dataclasses import replace
from pathlib import Path

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    JournalConflict,
    RecoveryAuthorityError,
    RecoveryAuthorityVerifier,
    RecoveryJournalFactory,
    RecoveryReceiptType,
    RecoveryTerminalOutcome,
    recovery_authority_store_path,
)
from automated_trading_bot.stage5.commit_boundary.recovery_journal import RecoveryJournal
from stage5_recovery_authority_fixtures import (
    authority_for,
    bootstrap_receipt,
    open_journal,
    terminal_receipt,
)


def _resolved(journal: RecoveryJournal, operation: str, *, committed: bool = True) -> None:
    attempt = f"ATTEMPT-{operation}"
    journal.record_intent(
        operation_id=operation,
        request_digest=f"DIGEST-{operation}",
        attempt_id=attempt,
        g00_sequence="10",
    )
    journal.mark_effect_possible(operation, attempt)
    journal.hold(operation, attempt, "AMBIGUOUS")
    journal.resolve_authenticated_terminal(
        operation,
        attempt,
        f"DIGEST-{operation}",
        terminal_receipt(
            journal, operation, f"DIGEST-{operation}",
            RecoveryTerminalOutcome.COMMITTED if committed else RecoveryTerminalOutcome.DENIED,
        ),
    )


def test_caller_cannot_inject_always_true_verifiers(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        RecoveryJournal(  # type: ignore[call-arg]
            tmp_path / "journal.sqlite",
            bootstrap_evidence_verifier=lambda *_: True,
            terminal_evidence_verifier=lambda *_: True,
        )


def test_forged_bootstrap_and_wrong_context_remain_closed(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path, authorize_new=False)
    forged = replace(bootstrap_receipt(journal), signature="00" * 128)
    with pytest.raises(RecoveryAuthorityError, match="signature"):
        journal.authorize_bootstrap(forged)
    wrong_store = bootstrap_receipt(journal, store_challenge="WRONG")
    with pytest.raises(RecoveryAuthorityError, match="context"):
        journal.authorize_bootstrap(wrong_store)
    assert not journal.exposure_allowed


def test_valid_bootstrap_is_single_use_in_independent_store(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path, authorize_new=False)
    receipt = bootstrap_receipt(journal)
    journal.authorize_bootstrap(receipt)
    assert journal.exposure_allowed
    journal.deny_new_exposure("TEST-CLOSED")
    restarted = open_journal(path, authorize_new=False)
    with pytest.raises(RecoveryAuthorityError, match="replay"):
        restarted.authorize_bootstrap(receipt)
    assert not restarted.exposure_allowed


def test_concurrent_receipt_consumption_allows_one_success(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path, authorize_new=False)
    receipt = bootstrap_receipt(journal)
    results: list[str] = []

    def consume() -> None:
        try:
            authority_for(path).consume_bootstrap(receipt, journal.bootstrap_challenge)
            results.append("CONSUMED")
        except RecoveryAuthorityError:
            results.append("REJECTED")

    workers = [threading.Thread(target=consume) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(results) == ["CONSUMED", "REJECTED"]
    assert not journal.exposure_allowed


def test_consumption_before_gate_transition_crash_stays_closed(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path, authorize_new=False)
    receipt = bootstrap_receipt(journal)
    authority_for(path).consume_bootstrap(receipt, journal.bootstrap_challenge)
    restarted = open_journal(path, authorize_new=False)
    assert not restarted.exposure_allowed
    with pytest.raises(RecoveryAuthorityError, match="replay"):
        restarted.authorize_bootstrap(receipt)


def test_journal_deletion_does_not_reset_receipt_replay_domain(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path, authorize_new=False)
    receipt = bootstrap_receipt(journal)
    journal.authorize_bootstrap(receipt)
    del journal
    gc.collect()
    deadline = time.monotonic() + 2.0
    while True:
        try:
            path.unlink()
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    path.with_suffix(path.suffix + ".closed").unlink(missing_ok=True)
    recreated = RecoveryJournalFactory().open(path)
    assert not recreated.exposure_allowed
    with pytest.raises(RecoveryAuthorityError):
        recreated.authorize_bootstrap(receipt)


def test_terminal_receipt_is_exact_single_use_and_global(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path)
    _resolved(journal, "A")
    journal.deny_new_exposure("A-AMBIGUOUS")
    wrong = terminal_receipt(
        journal,
        "A",
        "WRONG-DIGEST",
        RecoveryTerminalOutcome.COMMITTED,
    )
    with pytest.raises(RecoveryAuthorityError, match="context"):
        journal.requalify_exposure(
            operation_id="A", request_digest="DIGEST-A", receipt=wrong
        )
    valid = terminal_receipt(
        journal,
        "A",
        "DIGEST-A",
        RecoveryTerminalOutcome.COMMITTED,
    )
    journal.requalify_exposure(
        operation_id="A", request_digest="DIGEST-A", receipt=valid
    )
    assert journal.exposure_allowed
    journal.deny_new_exposure("REPLAY-CHECK")
    with pytest.raises(RecoveryAuthorityError, match="replay"):
        journal.requalify_exposure(
            operation_id="A", request_digest="DIGEST-A", receipt=valid
        )


def test_terminal_receipt_cannot_release_another_unresolved_operation(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path)
    _resolved(journal, "A")
    journal.record_intent(
        operation_id="B",
        request_digest="DIGEST-B",
        attempt_id="ATTEMPT-B",
        g00_sequence="11",
    )
    journal.mark_effect_possible("B", "ATTEMPT-B")
    journal.hold("B", "ATTEMPT-B", "AMBIGUOUS")
    journal.deny_new_exposure("GLOBAL-HOLD")
    receipt = terminal_receipt(
        journal,
        "A",
        "DIGEST-A",
        RecoveryTerminalOutcome.COMMITTED,
    )
    with pytest.raises(JournalConflict, match="another operation"):
        journal.requalify_exposure(
            operation_id="A", request_digest="DIGEST-A", receipt=receipt
        )
    assert not journal.exposure_allowed


def test_authority_store_loss_and_runtime_signing_are_restrictive(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path)
    has_signer = hasattr(journal, "sign") or hasattr(journal, "private_key")
    authority_path = recovery_authority_store_path(path)
    del journal
    gc.collect()
    authority_path.unlink()
    authority_path.with_suffix(authority_path.suffix + "-wal").unlink(missing_ok=True)
    authority_path.with_suffix(authority_path.suffix + "-shm").unlink(missing_ok=True)
    with pytest.raises(RecoveryAuthorityError, match="provisioned"):
        RecoveryAuthorityVerifier(authority_path)
    assert not has_signer


def test_receipt_type_nonterminal_and_policy_context_rejected(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = open_journal(path)
    _resolved(journal, "A")
    journal.deny_new_exposure("TEST")
    receipt = terminal_receipt(
        journal,
        "A",
        "DIGEST-A",
        RecoveryTerminalOutcome.COMMITTED,
        receipt_type=RecoveryReceiptType.BOOTSTRAP,
    )
    with pytest.raises(RecoveryAuthorityError, match="type"):
        journal.requalify_exposure(
            operation_id="A", request_digest="DIGEST-A", receipt=receipt
        )
    assert not journal.exposure_allowed
    with sqlite3.connect(recovery_authority_store_path(path)) as connection:
        consumed = connection.execute("SELECT COUNT(*) FROM consumed_receipt").fetchone()
        rejected = connection.execute(
            "SELECT COUNT(*) FROM consumed_receipt WHERE nonce=?", (receipt.nonce,)
        ).fetchone()
    # Bootstrap plus the authenticated terminal receipt used by _resolved.
    assert consumed is not None and int(consumed[0]) == 2
    assert rejected == (0,)

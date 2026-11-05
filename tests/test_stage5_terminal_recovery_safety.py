from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from threading import Barrier

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    IsolatedCommand,
    IsolatedStatus,
    JournalConflict,
    ProcessIsolationSupervisor,
    RecoveryAuthorityError,
    RecoveryState,
    RecoveryTerminalOutcome,
)
from stage5_recovery_authority_fixtures import open_journal, sign_receipt, terminal_receipt


class Nonterminal(StrEnum):
    UNKNOWN = "RESTRICTIVE_UNKNOWN"
    READBACK = "READBACK_UNCERTAIN"


def held(tmp_path: Path):
    journal = open_journal(tmp_path / "journal.sqlite")
    command = IsolatedCommand("OP", "DIGEST", "ATTEMPT", "10", {})
    journal.record_intent(
        operation_id="OP", request_digest="DIGEST", attempt_id="ATTEMPT", g00_sequence="10"
    )
    journal.mark_effect_possible("OP", "ATTEMPT")
    journal.hold("OP", "ATTEMPT", "UNCERTAIN")
    return journal, command


def receipt(journal, outcome=RecoveryTerminalOutcome.COMMITTED):
    return terminal_receipt(
        journal, "OP", "DIGEST", outcome, attempt_id="ATTEMPT", g00_sequence="10"
    )


def assert_held(journal):
    # Query durable state directly, independently of SupervisorResult.
    with sqlite3.connect(journal.path) as db:
        assert db.execute(
            "SELECT state,transaction_uncertain FROM recovery_intent "
            "WHERE operation_id='OP' AND attempt_id='ATTEMPT'"
        ).fetchone() == ("AMBIGUOUS_HOLD", 1)


@pytest.mark.parametrize("outcome", [Nonterminal.UNKNOWN, Nonterminal.READBACK, None])
def test_signed_nonterminal_cannot_resolve_hold(tmp_path, outcome):
    journal, command = held(tmp_path)
    malformed = sign_receipt(replace(receipt(journal), terminal_outcome=outcome))
    with pytest.raises(RecoveryAuthorityError, match="terminal"):
        ProcessIsolationSupervisor(journal).recover(command, malformed, timeout_ms=10000)
    assert_held(journal)
    with sqlite3.connect(journal.path.parent / ".soe01-recovery-authority.sqlite") as db:
        assert db.execute(
            "SELECT count(*) FROM consumed_receipt WHERE receipt_type='TERMINAL'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("method", ["resolved", "failed_before_effect", "record_capacity_denied"])
def test_ordinary_transition_cannot_clear_recovery_hold(tmp_path, method):
    journal, _ = held(tmp_path)
    with pytest.raises(JournalConflict, match="authenticated"):
        if method == "resolved":
            journal.resolved("OP", "ATTEMPT", committed=False,
                             result={"status": "DENIED"}, evidence="CALLER")
        elif method == "failed_before_effect":
            journal.failed_before_effect("OP", "ATTEMPT", "CALLER")
        else:
            journal.record_capacity_denied(operation_id="OP", request_digest="DIGEST",
                                          attempt_id="ATTEMPT", g00_sequence="10")
    assert_held(journal)


@pytest.mark.parametrize("concurrent", [False, True])
def test_conflicting_signed_receipts_leave_sticky_hold(tmp_path, monkeypatch, concurrent):
    journal, command = held(tmp_path)
    receipts = [receipt(journal), receipt(journal, RecoveryTerminalOutcome.DENIED)]
    if concurrent:
        barrier = Barrier(2)
        consume = journal._authority.consume_terminal

        def synchronized(*args, **kwargs):
            nonce = consume(*args, **kwargs)
            barrier.wait(timeout=5)
            return nonce

        monkeypatch.setattr(journal._authority, "consume_terminal", synchronized)

    def recover(value):
        try:
            return ProcessIsolationSupervisor(journal).recover(command, value, timeout_ms=10000)
        except JournalConflict as error:
            return error

    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(recover, receipts))
        monkeypatch.undo()
    else:
        results = [recover(value) for value in receipts]
    assert sum(isinstance(value, JournalConflict) for value in results) >= 1
    assert_held(journal)
    assert not journal.exposure_allowed
    reopened = open_journal(journal.path)
    assert_held(reopened)
    assert not reopened.exposure_allowed
    # Neither a fresh signed receipt nor an ordinary journal transition can
    # erase the retained conflict, including after restart.
    with pytest.raises(JournalConflict, match="conflicting"):
        ProcessIsolationSupervisor(reopened).recover(command, receipt(reopened), timeout_ms=10000)
    with pytest.raises(JournalConflict):
        reopened.resolved("OP", "ATTEMPT", committed=True,
                          result={"status": "COMMITTED"}, evidence="CALLER")
    assert_held(reopened)


def test_worker_acknowledgement_is_limited_to_effect_possible(tmp_path):
    journal = open_journal(tmp_path / "journal.sqlite")
    journal.record_intent(operation_id="OP", request_digest="DIGEST",
                          attempt_id="ATTEMPT", g00_sequence="10")
    journal.mark_effect_possible("OP", "ATTEMPT")
    with pytest.raises(JournalConflict, match="terminal status"):
        journal.resolved("OP", "ATTEMPT", committed=False,
                         result={"status": "RESTRICTIVE_UNKNOWN"}, evidence="WORKER")
    assert journal.get("OP", "ATTEMPT").state is RecoveryState.EFFECT_POSSIBLE
    journal.resolved("OP", "ATTEMPT", committed=True,
                     result={"status": "COMMITTED"}, evidence="WORKER")
    assert journal.get("OP", "ATTEMPT").state is RecoveryState.RECOVERED_COMMITTED


def test_conflict_before_response_cannot_return_terminal_success(tmp_path, monkeypatch):
    journal, command = held(tmp_path)
    committed = receipt(journal)
    denied = receipt(journal, RecoveryTerminalOutcome.DENIED)
    resolve = journal.resolve_authenticated_terminal

    def conflict_after_resolution(*args, **kwargs):
        outcome = resolve(*args, **kwargs)
        with pytest.raises(JournalConflict, match="conflicting"):
            resolve("OP", "ATTEMPT", "DIGEST", denied)
        return outcome

    monkeypatch.setattr(journal, "resolve_authenticated_terminal", conflict_after_resolution)
    result = ProcessIsolationSupervisor(journal).recover(command, committed, timeout_ms=10000)
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert result.journal_state is RecoveryState.AMBIGUOUS_HOLD
    assert_held(journal)

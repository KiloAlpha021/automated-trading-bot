from __future__ import annotations

import multiprocessing
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    IsolatedCommand,
    IsolatedStatus,
    JournalConflict,
    JournalDeadlineExceeded,
    JournalPersistenceUnresolved,
    ProcessIsolationSupervisor,
    RecoveryAuthorityError,
    RecoveryState,
    RecoveryTerminalOutcome,
)
from automated_trading_bot.stage5.commit_boundary.recovery_journal import RecoveryJournal as _RecoveryJournal
from automated_trading_bot.stage5.commit_boundary.process_isolation import (
    RecoveryOnlyViolation,
)
from stage5_recovery_authority_fixtures import open_journal
from stage5_recovery_authority_fixtures import terminal_receipt
from stage5_process_isolation_fixtures import (
    missing_readback,
    success_handler,
)



def RecoveryJournal(path: Path) -> _RecoveryJournal:
    return open_journal(path)


def command(operation: str = "OP-1", attempt: str = "A-1") -> IsolatedCommand:
    return IsolatedCommand(
        operation_id=operation,
        request_digest=f"DIGEST-{operation}",
        attempt_id=attempt,
        g00_sequence=f"G00-{operation}",
        payload={"effect": 5000},
    )


def supervisor(path: Path, **settings: int) -> ProcessIsolationSupervisor:
    return ProcessIsolationSupervisor(RecoveryJournal(path), **settings)


def held(journal: _RecoveryJournal, item: IsolatedCommand) -> None:
    journal.record_intent(
        operation_id=item.operation_id,
        request_digest=item.request_digest,
        attempt_id=item.attempt_id,
        g00_sequence=item.g00_sequence,
    )
    journal.hold(item.operation_id, item.attempt_id, "ADMITTED_PATH_AMBIGUITY")


def terminal(
    journal: _RecoveryJournal,
    item: IsolatedCommand,
    outcome: RecoveryTerminalOutcome,
):
    return terminal_receipt(
        journal,
        item.operation_id,
        item.request_digest,
        outcome,
        attempt_id=item.attempt_id,
        g00_sequence=item.g00_sequence,
    )


def test_acknowledged_operation_is_durable_and_reaped(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command()
    held(journal, item)
    result = ProcessIsolationSupervisor(journal).recover(
        item, terminal(journal, item, RecoveryTerminalOutcome.COMMITTED), timeout_ms=10_000
    )
    assert result.outcome.status is IsolatedStatus.COMMITTED
    assert result.worker_reaped and not result.worker_terminated
    assert result.journal_state is RecoveryState.RECOVERED_COMMITTED
    assert result.elapsed_ms < 10_000
    assert multiprocessing.active_children() == []


def test_admitted_execute_is_rejected_before_worker_or_journal_mutation(
    tmp_path: Path,
) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    subject = ProcessIsolationSupervisor(journal)
    item = command()
    with pytest.raises(RecoveryOnlyViolation, match="recovery-only"):
        subject.execute(item, success_handler, missing_readback)

    assert journal.get(item.operation_id, item.attempt_id) is None
    assert multiprocessing.active_children() == []


def test_missing_recovery_readback_remains_restrictive(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command()
    held(journal, item)
    result = ProcessIsolationSupervisor(journal).recover(
        item, None, timeout_ms=10_000
    )
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert result.outcome.result_code == "AMBIGUOUS_OUTCOME_HELD"
    assert result.worker_reaped
    record = journal.get(item.operation_id, item.attempt_id)
    assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD


def test_unresolved_ambiguity_remains_restrictive_hold(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    subject = ProcessIsolationSupervisor(journal)
    with pytest.raises(ValueError, match="existing durable intent"):
        subject.recover(command(), None, timeout_ms=10_000)
    assert journal.get("OP-1", "A-1") is None
    assert multiprocessing.active_children() == []


@pytest.mark.parametrize(
    "failure",
    (
        JournalDeadlineExceeded("injected journal deadline"),
        JournalPersistenceUnresolved("injected journal persistence failure"),
    ),
)
def test_recovery_journal_failure_returns_restrictive_with_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command()
    held(journal, item)
    subject = ProcessIsolationSupervisor(journal)
    observed_deadlines: list[int | None] = []

    def fail_unresolved(
        operation_id: str,
        attempt_id: str,
        evidence: str,
        *,
        deadline_ns: int | None = None,
    ) -> None:
        del operation_id, attempt_id, evidence
        observed_deadlines.append(deadline_ns)
        raise failure

    monkeypatch.setattr(journal, "unresolved", fail_unresolved)
    result = subject.recover(item, None, timeout_ms=10_000)

    assert observed_deadlines and observed_deadlines[0] is not None
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert result.outcome.result_code == "AMBIGUOUS_OUTCOME_HELD"
    assert result.journal_state is RecoveryState.AMBIGUOUS_HOLD
    assert result.worker_reaped
    assert result.elapsed_ms < 10_000
    durable = journal.get("OP-1", "A-1")
    assert durable is not None and durable.state is RecoveryState.AMBIGUOUS_HOLD


def test_recovery_capacity_exhaustion_preserves_existing_holds(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    second_item = command("OP-CAPACITY")
    held(journal, second_item)
    subject = ProcessIsolationSupervisor(journal, max_workers=1)
    assert subject._capacity.acquire(blocking=False)
    second = subject.recover(
        second_item,
        terminal(journal, second_item, RecoveryTerminalOutcome.COMMITTED),
        timeout_ms=10_000,
    )
    subject._capacity.release()
    assert second.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert second.outcome.result_code == "RECOVERY_CAPACITY_EXHAUSTED"
    assert second.journal_state is RecoveryState.AMBIGUOUS_HOLD
    assert multiprocessing.active_children() == []


def test_journal_survives_restart_and_rejects_conflict(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    first = RecoveryJournal(path)
    item = command()
    first.record_intent(
        operation_id=item.operation_id,
        request_digest=item.request_digest,
        attempt_id=item.attempt_id,
        g00_sequence=item.g00_sequence,
    )
    first.hold(item.operation_id, item.attempt_id, "CRASH")
    recovered = RecoveryJournal(path).get(item.operation_id, item.attempt_id)
    assert recovered is not None and recovered.state is RecoveryState.AMBIGUOUS_HOLD
    with pytest.raises(JournalConflict):
        first.record_intent(
            operation_id=item.operation_id,
            request_digest="FOREIGN",
            attempt_id=item.attempt_id,
            g00_sequence=item.g00_sequence,
        )


def test_denial_has_zero_financial_effect(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command()
    held(journal, item)
    result = ProcessIsolationSupervisor(journal).recover(
        item, terminal(journal, item, RecoveryTerminalOutcome.DENIED), timeout_ms=10_000
    )
    assert result.outcome.status is IsolatedStatus.DENIED
    assert result.outcome.capital_effect_minor == 0
    assert result.outcome.liability_effect_minor == 0
    assert result.journal_state is RecoveryState.RECOVERED_DENIED


def test_foreign_recovery_outcome_cannot_clear_hold(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command()
    held(journal, item)
    subject = ProcessIsolationSupervisor(journal)
    foreign = terminal_receipt(
        journal,
        "FOREIGN",
        item.request_digest,
        RecoveryTerminalOutcome.COMMITTED,
        attempt_id=item.attempt_id,
        g00_sequence=item.g00_sequence,
    )
    with pytest.raises(RecoveryAuthorityError, match="context"):
        subject.recover(item, foreign, timeout_ms=10_000)
    record = journal.get("OP-1", "A-1")
    assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD
    assert record.transaction_uncertain


@pytest.mark.parametrize(
    "change",
    (
        {"request_digest": "FOREIGN-DIGEST"},
        {"attempt_id": "FOREIGN-ATTEMPT"},
        {"g00_sequence": "FOREIGN-G00"},
        {"signature": "00" * 128},
    ),
)
def test_forged_or_wrong_context_terminal_evidence_retains_hold(
    tmp_path: Path, change: dict[str, object]
) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command("OP-CONTEXT")
    held(journal, item)
    receipt = replace(
        terminal(journal, item, RecoveryTerminalOutcome.COMMITTED), **change
    )
    with pytest.raises(RecoveryAuthorityError):
        ProcessIsolationSupervisor(journal).recover(item, receipt, timeout_ms=10_000)
    record = journal.get(item.operation_id, item.attempt_id)
    assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD
    assert record.transaction_uncertain


def test_restrictive_unknown_receipt_cannot_clear_hold(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command("OP-UNKNOWN")
    held(journal, item)
    result = ProcessIsolationSupervisor(journal).recover(item, None, timeout_ms=10_000)
    record = journal.get(item.operation_id, item.attempt_id)
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD
    assert record.transaction_uncertain


def test_recovery_rejects_executable_callback_without_financial_mutation(tmp_path: Path) -> None:
    journal = RecoveryJournal(tmp_path / "journal.sqlite")
    item = command("OP-CALLBACK")
    held(journal, item)
    effects = tmp_path / "effects.sqlite"
    with sqlite3.connect(effects) as connection:
        connection.execute("CREATE TABLE effects(capital INTEGER, liability INTEGER)")
        connection.execute("INSERT INTO effects VALUES (5000, 5000)")

    def financial_callback(*_args: object) -> None:
        with sqlite3.connect(effects) as connection:
            connection.execute(
                "UPDATE effects SET capital=capital+5000, liability=liability+5000"
            )

    with pytest.raises(TypeError, match="authenticated terminal receipt"):
        ProcessIsolationSupervisor(journal).recover(  # type: ignore[arg-type]
            item, financial_callback, timeout_ms=10_000
        )
    with sqlite3.connect(effects) as connection:
        assert connection.execute("SELECT capital,liability FROM effects").fetchone() == (
            5000,
            5000,
        )
    record = journal.get(item.operation_id, item.attempt_id)
    assert record is not None and record.state is RecoveryState.AMBIGUOUS_HOLD

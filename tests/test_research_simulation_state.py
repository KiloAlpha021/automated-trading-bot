from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import ClaimCeiling
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.research_governance import AssuranceMode, JournalError, ApprovalEvidence, FrozenResearchPlan, JournalCheckpoint
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
    create_order,
    SimulatedOrder,
)
from automated_trading_bot.research.simulation_state import (
    SimulationStateError,
    SimulationStateJournal,
)


# All controller fixtures are ENGINEERING_SIMULATION_ONLY, not deployment trust.
from dataclasses import replace
from typing import cast
import test_portfolio_construction as pf
from automated_trading_bot.research import portfolio_construction as pc
from automated_trading_bot.research.simulation_ledger import FinancialEvent, FinancialEventKind, LedgerEventId
from automated_trading_bot.research.simulation_state import (
    AcceptedPortfolioState, EngineeringPortfolioController, PortfolioControllerConfig,
    PortfolioCommand, ExecutionCommand, FinancialCommand,
)
from automated_trading_bot.corporate_actions.model import ActionVersionId



DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
USD = Currency("USD")
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))


@dataclass(frozen=True, slots=True)
class Trust:
    checkpoint_digests: frozenset[str]
    identity: str = "test-trust:s4-11"
    assurance_mode: AssuranceMode = AssuranceMode.ENGINEERING_SIMULATION

    def require_approval(self, evidence: ApprovalEvidence, plan: FrozenResearchPlan) -> None:
        raise AssertionError("approval is outside this fixture")

    def require_checkpoint(self, checkpoint: JournalCheckpoint) -> None:
        if checkpoint.checkpoint_digest not in self.checkpoint_digests:
            raise JournalError("checkpoint is not independently established")


def at(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, 2, hour, tzinfo=timezone.utc))


def make_order() -> SimulatedOrder:
    eligibility = DecisionInputEligibility(
        EligibilityState.ELIGIBLE,
        (),
        "atis:research:feature-observation:v1:sha256:" + "3" * 64,
        at(10),
        ClaimCeiling.ENGINEERING_ONLY,
    )
    return create_order(
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        currency=USD,
        release_at=at(11),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=eligibility,
    )


def cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        "atis:simulation-cost-model:v1:ec0",
        CostScenario.EC0,
        tuple(
            CostRate(component, Decimal(0), CostProvenance.MODELLED, DIGEST)
            for component in CostComponent
        ),
        True,
    )


def execution() -> ExecutionSnapshot:
    return ExecutionSnapshot(
        at(13), Decimal("20"), at(12), ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE, Decimal("2"), USD, DIGEST,
    )


def journal(root: Path, trust: Trust, checkpoint: JournalCheckpoint | None = None) -> SimulationStateJournal:
    return SimulationStateJournal(
        root,
        journal_identity="simulation:s4-11:test",
        epoch="epoch:1",
        opening_cash=Decimal("100000"),
        currency=USD,
        research_run_id=RUN,
        opened_at=at(9),
        evidence_digest=DIGEST,
        checkpoint=checkpoint,
        trust=trust,
    )


def test_command_history_recovers_complete_reconciled_state(tmp_path: Path) -> None:
    value = journal(tmp_path / "journal", Trust(frozenset()))
    order = make_order()
    value.submit("command:submit", order)
    final = value.execute_and_post(
        "command:execute", order.order_id, Decimal("2"), execution(), cost_model()
    )
    recovered = value.recover()
    assert recovered == final
    assert len(recovered.oms.orders) == len(recovered.oms.fills) == 1
    assert len(recovered.ledger.events) == 2


def test_checkpoint_is_independently_required(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    initial = journal(root, Trust(frozenset()))
    initial.submit("command:submit", make_order())
    candidate = initial.checkpoint_candidate(establishment_identity="test-trust:s4-11")
    with pytest.raises(SimulationStateError, match="checkpoint"):
        initial.require_checkpointed()
    accepted = journal(root, Trust(frozenset({candidate.checkpoint_digest})), candidate)
    accepted.require_checkpointed()


def test_truncation_and_replacement_fail_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    initial = journal(root, Trust(frozenset()))
    order = make_order()
    initial.submit("command:submit", order)
    initial.execute_and_post("command:execute", order.order_id, Decimal("2"), execution(), cost_model())
    candidate = initial.checkpoint_candidate(establishment_identity="test-trust:s4-11")
    (root / "00000002.json").unlink()
    truncated = journal(root, Trust(frozenset({candidate.checkpoint_digest})), candidate)
    with pytest.raises(SimulationStateError, match="checkpoint mismatch"):
        truncated.require_checkpointed()


def test_incomplete_or_concurrent_publication_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    value = journal(root, Trust(frozenset()))
    (root / ".simulation-state.lock").write_text("busy", encoding="utf-8")
    with pytest.raises(SimulationStateError, match="already active"):
        value.submit("command:submit", make_order())
    (root / ".simulation-state.lock").unlink()
    (root / ".00000001.json.1.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(SimulationStateError, match="unexpected"):
        value.recover()


def controller_config(monthly: bool = False) -> PortfolioControllerConfig:
    admission = pf._admission("MOM-001", 1)
    book = pc.create_commitment_book(pc.allocate_equal_budgets((admission,), total_capital=Decimal("10000"), hypothetical=True), (admission,))
    schedule = pf._calendar(date(2026, 10, 30)) if monthly else None
    return PortfolioControllerConfig("portfolio:fixture", "journal:fixture", "fresh:2", book,
        Decimal("10000"), pf._run(1), pf._time(1, 9), pf.DIGEST,
        "MONTHLY" if monthly else "GENERIC",
        "" if schedule is None else str(schedule.calendar_id.value),
        "" if schedule is None else str(schedule.selected_calendar_version_id.value) if schedule.selected_calendar_version_id is not None else "")


def controller(tmp_path: Path, monthly: bool = False) -> EngineeringPortfolioController:
    return EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2",
                                          controller_config(monthly), establish=True)


def propose(c: EngineeringPortfolioController, identity: str, kind: str, payload: object,
            cutoff: Timestamp | None = None) -> AcceptedPortfolioState:
    state = c.read_current()
    return c.propose(identity, PortfolioCommand(kind, cutoff or pf._time(1, 10), payload),
                     expected_sequence=state.sequence, expected_digest=state.digest)


def buy_fixture(c: EngineeringPortfolioController) -> AcceptedPortfolioState:
    admission = pf._admission("MOM-001", 1)
    order = pf._order(admission)
    propose(c, "reserve", "RESERVE", (pc.CommitmentRequest("MOM-001", order, pf._ceiling()),))
    propose(c, "submit", "SUBMIT", order)
    return propose(c, "execute", "EXECUTE_AND_POST",
                   ExecutionCommand(order.order_id, Decimal("100"), pf._snapshot("20", "100"), pf._cost_model()), pf._time(1, 13))


def test_controller_fresh_bootstrap_and_authority_limit(tmp_path: Path) -> None:
    c = controller(tmp_path)
    assert c.read_current().authority == "ENGINEERING_SIMULATION_ONLY"
    assert c.read_current().ledger.state.cash == 10000
    with pytest.raises(SimulationStateError, match="authority NOT_ESTABLISHED"):
        replace(controller_config(), authority="AUTHORITY_ESTABLISHED")
    with pytest.raises(SimulationStateError, match="fresh epoch"):
        controller(tmp_path)
    with pytest.raises(SimulationStateError, match="bootstrap"):
        EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", replace(controller_config(), epoch="foreign"))


def test_controller_execution_recovery_and_final_consumer(tmp_path: Path) -> None:
    c = controller(tmp_path)
    final = buy_fixture(c)
    assert final.ledger.state.cash == 8000
    assert final.ledger.state.positions[0].quantity == 100
    book = cast(pc.CommitmentBook, final.book)
    assert book.records[-1].actual_cash_effect == 2000
    assert book.records[-1].reserved_cash == 0
    restarted = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())
    assert restarted.read_current() == final
    evidence = cast(pc.PortfolioEvidence, restarted.build_current_evidence())
    assert "OPERATIONAL_AUTHORITY_NOT_ESTABLISHED" in evidence.limitations
    assert evidence == c.build_current_evidence()


@pytest.mark.parametrize("phase", ["prepared", "record_persisted", "head_advanced", "readback", "acknowledged"])
def test_controller_crash_points_never_double_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    c = controller(tmp_path)
    initial = c.read_current()
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:funding"), 2, pf._time(1, 10),
                          FinancialEventKind.CASH_ADJUSTMENT, pf._run(1), pf.DIGEST, pf.USD, cash_delta=Decimal("1"))
    cmd = PortfolioCommand("CASH_ADJUSTMENT", pf._time(1, 10), FinancialCommand(event, pf._time(1, 10)))
    def crash(point: str) -> None:
        if point == phase:
            raise RuntimeError("injected interruption")
    monkeypatch.setattr(c, "_crash_point", crash)
    with pytest.raises(RuntimeError, match="injected"):
        c.propose("fund", cmd, expected_sequence=0, expected_digest=initial.digest)
    restarted = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())
    state = restarted.read_current()
    assert state.sequence == (0 if phase in {"prepared", "record_persisted"} else 1)
    result = restarted.propose("fund", cmd, expected_sequence=0, expected_digest=initial.digest)
    assert result.sequence == 1 and result.ledger.state.cash == 10001
    assert len(result.ledger.events) == 2
    assert (tmp_path / "accepted-v2" / "ack-00000001.json").is_file()


def test_controller_stale_duplicate_conflict_and_lock(tmp_path: Path) -> None:
    c = controller(tmp_path)
    state = c.read_current()
    order = pf._order(pf._admission("MOM-001", 1))
    command = PortfolioCommand("RESERVE", pf._time(1, 10), (pc.CommitmentRequest("MOM-001", order, pf._ceiling()),))
    accepted = c.propose("r", command, expected_sequence=0, expected_digest=state.digest)
    assert c.propose("r", command, expected_sequence=0, expected_digest=state.digest) == accepted
    with pytest.raises(SimulationStateError, match="conflicting duplicate"):
        c.propose("r", replace(command, kind="SUBMIT"), expected_sequence=1, expected_digest=accepted.digest)
    with pytest.raises(SimulationStateError, match="stale accepted"):
        c.propose("r2", command, expected_sequence=0, expected_digest=state.digest)
    with c._lock():
        with pytest.raises(SimulationStateError, match="writer already active"):
            c.propose("r3", command, expected_sequence=1, expected_digest=accepted.digest)
    assert c.read_current() == accepted


@pytest.mark.parametrize("damage", ["missing", "head", "rollback", "reordered"])
def test_controller_corrupt_or_truncated_state_rejects(tmp_path: Path, damage: str) -> None:
    c = controller(tmp_path)
    old_head = (tmp_path / "accepted-v2" / "head.json").read_bytes()
    buy_fixture(c)
    path = tmp_path / "journal-v2" / "00000003.json"
    if damage == "missing":
        path.unlink()
    elif damage == "head":
        (tmp_path / "accepted-v2" / "head.json").write_text('{}')
    elif damage == "rollback":
        (tmp_path / "accepted-v2" / "head.json").write_bytes(old_head)
    else:
        path.write_bytes((tmp_path / "journal-v2" / "00000001.json").read_bytes())
    with pytest.raises(SimulationStateError):
        EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())


def test_controller_monthly_omission_and_raw_replacement_reject(tmp_path: Path) -> None:
    c = controller(tmp_path, monthly=True)
    old = c.read_current()
    opening = pf._calendar(date(2026, 11, 2)).utc_open
    assert opening == Timestamp(datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc))
    decision = pf._time(30, 22)
    order = create_order(instrument_id=pf.INSTRUMENT, side=OrderSide.BUY, quantity=Decimal(100), currency=pf.USD,
        release_at=Timestamp(datetime(2026, 11, 2, 14, tzinfo=timezone.utc)), research_run_id=pf._run(1),
        evidence_digest=pf.DIGEST, eligibility=replace(pf._eligibility(), decision_time=decision))
    with pytest.raises(SimulationStateError, match="mandatory monthly schedule"):
        propose(c, "early", "RESERVE", (pc.CommitmentRequest("MOM-001", order, pf._ceiling()),), decision)
    with pytest.raises(SimulationStateError, match="raw replacement forbidden"):
        propose(c, "erase", "REPLACE_BOOK", old.book)
    assert c.read_current() == old


@pytest.mark.parametrize("kind", [FinancialEventKind.MARK, FinancialEventKind.CASH_DIVIDEND,
                                 FinancialEventKind.SPLIT, FinancialEventKind.CASH_ADJUSTMENT])
def test_controller_nontrade_events_roundtrip(tmp_path: Path, kind: FinancialEventKind) -> None:
    c = controller(tmp_path)
    buy_fixture(c)
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:action"), 3, pf._time(1, 14), kind,
        pf._run(1), pf.DIGEST, None if kind is FinancialEventKind.SPLIT else pf.USD,
        None if kind is FinancialEventKind.CASH_ADJUSTMENT else pf.INSTRUMENT,
        quantity=Decimal("0.5") if kind is FinancialEventKind.SPLIT else None,
        unit_amount=Decimal("1") if kind is FinancialEventKind.CASH_DIVIDEND else Decimal("21") if kind is FinancialEventKind.MARK else None,
        cash_delta=Decimal("10") if kind is FinancialEventKind.CASH_ADJUSTMENT else None,
        corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:" + "3" * 64) if kind in {FinancialEventKind.CASH_DIVIDEND, FinancialEventKind.SPLIT} else None)
    result = propose(c, "action", kind.value, FinancialCommand(event, pf._time(1, 14)), pf._time(1, 14))
    assert result.ledger.events[-1] == event
    assert result.ledger.state.cash == (8100 if kind is FinancialEventKind.CASH_DIVIDEND else 8010 if kind is FinancialEventKind.CASH_ADJUSTMENT else 8000)
    assert result.ledger.state.positions[0].quantity == (50 if kind is FinancialEventKind.SPLIT else 100)
    assert EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config()).read_current() == result


def test_controller_split_preserves_truth_and_blocks_until_cancellation(tmp_path: Path) -> None:
    c = controller(tmp_path)
    current = buy_fixture(c)
    sell = replace(pf._order(pf._admission("MOM-001", 1), side=OrderSide.SELL, quantity="75", release_hour=15), decision_time=pf._time(1, 14))
    request = pf._sell_request(sell, current.oms, current.ledger)
    propose(c, "sell", "RESERVE", (request,), pf._time(1, 14))
    propose(c, "sell-submit", "SUBMIT", sell, pf._time(1, 14))
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:reverse"), 3, pf._time(1, 16),
        FinancialEventKind.SPLIT, pf._run(1), pf.DIGEST, instrument_id=pf.INSTRUMENT,
        quantity=Decimal("0.5"), corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:" + "3" * 64))
    state = propose(c, "split", "SPLIT", FinancialCommand(event, pf._time(1, 16)), pf._time(1, 16))
    assert state.ledger.state.positions[0].quantity == 50 and state.infeasible_reservations
    assert cast(pc.CommitmentBook, state.book).records[-1].reserved_holding == 75
    with pytest.raises(SimulationStateError, match="INFEASIBLE_RESERVATION"):
        c.build_current_evidence()
    with pytest.raises(SimulationStateError, match="INFEASIBLE_RESERVATION"):
        propose(c, "new", "RESERVE", (), pf._time(1, 16))
    resolved = propose(c, "resolve", "RESOLVE_OBLIGATION", sell.order_id, pf._time(1, 16))
    assert not resolved.infeasible_reservations
    assert resolved.ledger == state.ledger
    assert cast(pc.CommitmentBook, resolved.book).records[-1].reserved_holding == 0


def test_controller_backward_execution_preserves_every_accepted_byte(tmp_path: Path) -> None:
    c = controller(tmp_path)
    final = buy_fixture(c)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')}
    order = final.oms.orders[0].order
    with pytest.raises(SimulationStateError, match="backward decision"):
        propose(c, "backward", "EXECUTE_AND_POST", ExecutionCommand(order.order_id, Decimal(1), pf._snapshot("20", "1"), pf._cost_model()), pf._time(1, 12))
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')}
    assert c.read_current() == final


def test_controller_monthly_positive_and_removal_survive_restart(tmp_path: Path) -> None:
    c = controller(tmp_path, monthly=True)
    before = c.read_current()
    book = cast(pc.CommitmentBook, before.book)
    final, following = pf._calendar(date(2026, 10, 30)), pf._calendar(date(2026, 11, 2))
    schedule = pc.schedule_monthly_rebalance(final_session=final, next_session=following,
        session_sequence=(final, following), decision_time=pf._time(30, 22), preserved_obligations=(),
        calendar_inputs=pf._calendar_inputs(), commitments=book, oms=before.oms, ledger=before.ledger)
    order = create_order(instrument_id=pf.INSTRUMENT, side=OrderSide.BUY, quantity=Decimal(100), currency=pf.USD,
        release_at=schedule.release_time, research_run_id=pf._run(1), evidence_digest=pf.DIGEST,
        eligibility=replace(pf._eligibility(), decision_time=schedule.decision_time))
    propose(c, "monthly-reserve", "RESERVE", (pc.CommitmentRequest("MOM-001", order, pf._ceiling(), schedule=schedule),), schedule.decision_time)
    current = propose(c, "monthly-submit", "SUBMIT", order, schedule.decision_time)
    removal = pc.schedule_monthly_rebalance(final_session=final, next_session=following,
        session_sequence=(final, following), decision_time=pf._time(30, 22), preserved_obligations=("MOM-001",),
        removed_strategies=("MOM-001",), calendar_inputs=pf._calendar_inputs(),
        commitments=cast(pc.CommitmentBook, current.book), oms=current.oms, ledger=current.ledger)
    removed = propose(c, "remove", "REMOVE_STRATEGY", removal, removal.decision_time)
    accepted = cast(pc.CommitmentBook, removed.book)
    assert accepted.removed_strategies == ("MOM-001",)
    assert accepted.records[-1].reserved_cash == 4000
    # Both fields erased and the digest recomputed can describe a pure calculation,
    # but cannot replace the controller's accepted state through any command.
    erased = pc.CommitmentBook(accepted.allocation, accepted.admissions, accepted.records)
    with pytest.raises(SimulationStateError, match="raw replacement forbidden"):
        propose(c, "erase", "REPLACE_BOOK", erased, removal.decision_time)
    restarted = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config(True))
    assert restarted.read_current() == removed
    replacement_order = replace(order, order_id=OrderId(UUID("22345678-1234-4234-8234-123456789abc")))
    with pytest.raises(pc.PortfolioConstructionError, match="removed strategy"):
        propose(restarted, "new-entry", "RESERVE", (pc.CommitmentRequest("MOM-001", replacement_order, pf._ceiling(), schedule=removal),), removal.decision_time)
    assert restarted.read_current() == removed
    cancelled = propose(restarted, "cancel-retained", "CANCEL", order.order_id, removal.decision_time)
    assert cast(pc.CommitmentBook, cancelled.book).removed_strategies == ("MOM-001",)
    assert cast(pc.CommitmentBook, cancelled.book).records[-1].reserved_cash == 0


def test_controller_stale_sell_after_completed_sale(tmp_path: Path) -> None:
    c = controller(tmp_path)
    hundred = buy_fixture(c)
    order = replace(pf._order(pf._admission("MOM-001", 1), side=OrderSide.SELL, quantity="40", release_hour=15), decision_time=pf._time(1, 14))
    propose(c, "sell40", "RESERVE", (pf._sell_request(order, hundred.oms, hundred.ledger),), pf._time(1, 14))
    propose(c, "submit40", "SUBMIT", order, pf._time(1, 14))
    snapshot = replace(pf._snapshot("20", "40"), execution_at=pf._time(1, 16))
    sixty = propose(c, "execute40", "EXECUTE_AND_POST", ExecutionCommand(order.order_id, Decimal(40), snapshot, pf._cost_model()), pf._time(1, 16))
    assert sixty.ledger.state.positions[0].quantity == 60
    stale_order = replace(order, quantity=Decimal(80), decision_time=pf._time(1, 17), release_at=pf._time(1, 18))
    with pytest.raises(SimulationStateError, match="stale sell decision state"):
        propose(c, "stale80", "RESERVE", (pf._sell_request(stale_order, hundred.oms, hundred.ledger),), pf._time(1, 17))
    assert c.read_current() == sixty


@pytest.mark.parametrize("defect", ["currency", "run", "sequence", "future", "duplicate"])
def test_controller_invalid_financial_events_are_atomic(tmp_path: Path, defect: str) -> None:
    c = controller(tmp_path)
    before = c.read_current()
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:fund"), 2, pf._time(1, 10),
        FinancialEventKind.CASH_ADJUSTMENT, pf._run(1), pf.DIGEST, pf.USD, cash_delta=Decimal(1))
    if defect == "currency":
        event = replace(event, currency=Currency("GBP"))
    elif defect == "run":
        event = replace(event, research_run_id=pf._run(99))
    elif defect == "sequence":
        event = replace(event, sequence=3)
    elif defect == "future":
        event = replace(event, occurred_at=pf._time(1, 11))
    else:
        event = replace(event, event_id=before.ledger.events[0].event_id)
    files_before = {str(p):p.read_bytes() for p in tmp_path.rglob('*.json')}
    with pytest.raises(ValueError, match="currency|identity, sequence or chronology"):
        propose(c, "bad", "CASH_ADJUSTMENT", FinancialCommand(event, pf._time(1, 10)))
    assert c.read_current() == before
    assert files_before == {str(p):p.read_bytes() for p in tmp_path.rglob('*.json')}


def test_controller_unaccepted_tail_never_promoted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = controller(tmp_path)
    initial = c.read_current()
    def crash(phase: str) -> None:
        if phase == "record_persisted":
            raise RuntimeError("crash before acceptance")
    monkeypatch.setattr(c, "_crash_point", crash)
    order = pf._order(pf._admission("MOM-001", 1))
    request = pc.CommitmentRequest("MOM-001", order, pf._ceiling())
    with pytest.raises(RuntimeError):
        propose(c, "prepare", "RESERVE", (request,))
    restarted = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())
    assert restarted.read_current() == initial
    with pytest.raises(SimulationStateError, match="conflicting unaccepted tail"):
        propose(restarted, "different", "RESERVE", (request,))
    assert restarted.read_current() == initial


def test_controller_rehashed_record_cannot_replace_accepted_head(tmp_path: Path) -> None:
    c = controller(tmp_path)
    buy_fixture(c)
    path = tmp_path / "journal-v2" / "00000001.json"
    record = c._read(path)
    record["command_id"] = "foreign-rehashed-command"
    c._write(path, record)
    with pytest.raises(SimulationStateError, match="semantic replay mismatch"):
        c.read_current()



@pytest.mark.parametrize("phase", ["record", "head"])
def test_controller_interrupted_file_replace_recovers_prior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    import os
    c = controller(tmp_path)
    initial = c.read_current()
    original = os.replace
    def interrupted(source: object, target: object) -> None:
        path = Path(str(target))
        if path.name == ("00000001.json" if phase == "record" else "head.json"):
            raise OSError("injected replacement failure")
        original(str(source), str(target))
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        order = pf._order(pf._admission("MOM-001", 1))
        request = pc.CommitmentRequest("MOM-001", order, pf._ceiling())
        with pytest.raises(OSError, match="injected replacement"):
            propose(c, "r", "RESERVE", (request,))
    restarted = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())
    assert restarted.read_current() == initial
    accepted = propose(restarted, "r", "RESERVE", (request,))
    assert accepted.sequence == 1


def test_controller_competing_handles_cannot_accept_conflicting_successors(tmp_path: Path) -> None:
    c = controller(tmp_path)
    other = EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config())
    initial = other.read_current()
    request = pc.CommitmentRequest("MOM-001", pf._order(pf._admission("MOM-001", 1)), pf._ceiling())
    command = PortfolioCommand("RESERVE", pf._time(1, 10), (request,))
    with c._lock():
        with pytest.raises(SimulationStateError, match="writer already active"):
            other.propose("second", command, expected_sequence=0, expected_digest=initial.digest)
    accepted = c.propose("first", command, expected_sequence=0, expected_digest=initial.digest)
    with pytest.raises(SimulationStateError, match="stale accepted predecessor"):
        other.propose("second", command, expected_sequence=0, expected_digest=initial.digest)
    assert other.read_current() == accepted



def test_controller_duplicate_corporate_action_cannot_double_apply(tmp_path: Path) -> None:
    c = controller(tmp_path)
    buy_fixture(c)
    action = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:split"), 3, pf._time(1, 14),
        FinancialEventKind.SPLIT, pf._run(1), pf.DIGEST, instrument_id=pf.INSTRUMENT,
        quantity=Decimal("0.5"), corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:"+"5"*64))
    accepted = propose(c, "split", "SPLIT", FinancialCommand(action, pf._time(1,14)), pf._time(1,14))
    duplicate = replace(action, event_id=LedgerEventId("atis:simulation-ledger-event:v1:split-again"), sequence=4)
    with pytest.raises(SimulationStateError, match="duplicate corporate action"):
        propose(c, "split-again", "SPLIT", FinancialCommand(duplicate, pf._time(1,14)), pf._time(1,14))
    assert c.read_current() == accepted
    assert accepted.ledger.state.positions[0].quantity == 50


def test_controller_foreign_instrument_event_rejects(tmp_path: Path) -> None:
    c = controller(tmp_path)
    initial = c.read_current()
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:foreign-mark"), 2, pf._time(1,10),
        FinancialEventKind.MARK, pf._run(1), pf.DIGEST, pf.USD, pf.INSTRUMENT, unit_amount=Decimal(20))
    with pytest.raises(SimulationStateError, match="instrument is not established"):
        propose(c, "mark", "MARK", FinancialCommand(event, pf._time(1,10)))
    assert c.read_current() == initial



def test_controller_copied_roots_are_not_the_established_launch(tmp_path: Path) -> None:
    import shutil
    controller(tmp_path)
    shutil.copytree(tmp_path / "journal-v2", tmp_path / "foreign-journal")
    shutil.copytree(tmp_path / "accepted-v2", tmp_path / "foreign-accepted")
    with pytest.raises(SimulationStateError, match="bootstrap"):
        EngineeringPortfolioController(tmp_path / "foreign-journal", tmp_path / "foreign-accepted", controller_config())


def test_controller_orphan_lock_fails_closed_without_deletion(tmp_path: Path) -> None:
    c = controller(tmp_path)
    before = (tmp_path / "accepted-v2" / "head.json").read_bytes()
    lock = tmp_path / "accepted-v2" / ".controller.lock"
    lock.write_text("simulated terminated writer")
    with pytest.raises(SimulationStateError, match="writer already active"):
        c.read_current()
    assert lock.exists()
    assert (tmp_path / "accepted-v2" / "head.json").read_bytes() == before



def test_controller_funding_withdrawal_preserves_truth_and_blocks_buy_liability(tmp_path: Path) -> None:
    c = controller(tmp_path)
    order = pf._order(pf._admission("MOM-001", 1))
    propose(c, "reserve", "RESERVE", (pc.CommitmentRequest("MOM-001", order, pf._ceiling()),))
    propose(c, "submit", "SUBMIT", order)
    event = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:withdrawal"), 2, pf._time(1, 12),
        FinancialEventKind.CASH_ADJUSTMENT, pf._run(1), pf.DIGEST, pf.USD, cash_delta=Decimal(-9500))
    accepted = propose(c, "withdrawal", "CASH_ADJUSTMENT", FinancialCommand(event, pf._time(1,12)), pf._time(1,12))
    assert accepted.ledger.state.cash == 500
    assert accepted.infeasible_reservations == ("INFEASIBLE_RESERVATION:cash",)
    assert cast(pc.CommitmentBook, accepted.book).records[-1].reserved_cash == 4000
    with pytest.raises(SimulationStateError, match="INFEASIBLE_RESERVATION"):
        propose(c, "new", "RESERVE", (), pf._time(1,12))
    with pytest.raises(SimulationStateError, match="INFEASIBLE_RESERVATION"):
        c.build_current_evidence()
    cancelled = propose(c, "cancel", "RESOLVE_OBLIGATION", order.order_id, pf._time(1,12))
    assert cancelled.ledger == accepted.ledger
    assert not cancelled.infeasible_reservations
    assert cast(pc.CommitmentBook, cancelled.book).records[-1].reserved_cash == 0



def test_controller_partial_cancellation_preserves_1600_releases_2400(tmp_path: Path) -> None:
    c = controller(tmp_path)
    order = pf._order(pf._admission("MOM-001", 1))
    propose(c, "reserve", "RESERVE", (pc.CommitmentRequest("MOM-001", order, pf._ceiling()),))
    propose(c, "submit", "SUBMIT", order)
    partial = propose(c, "partial", "EXECUTE_AND_POST",
        ExecutionCommand(order.order_id, Decimal(40), pf._snapshot("40", "40"), pf._cost_model()), pf._time(1,13))
    assert cast(pc.CommitmentBook, partial.book).records[-1].actual_cash_effect == 1600
    assert cast(pc.CommitmentBook, partial.book).records[-1].reserved_cash == 2400
    cancelled = propose(c, "cancel", "CANCEL", order.order_id, pf._time(1,14))
    record = cast(pc.CommitmentBook, cancelled.book).records[-1]
    assert record.actual_cash_effect == 1600 and record.reserved_cash == 0
    assert record.filled_quantity == 40 and record.quantity - record.filled_quantity == 60
    assert cancelled.ledger == partial.ledger and cancelled.oms.fills == partial.oms.fills
    assert cancelled.ledger.state.cash == 8400
    assert EngineeringPortfolioController(tmp_path / "journal-v2", tmp_path / "accepted-v2", controller_config()).read_current() == cancelled

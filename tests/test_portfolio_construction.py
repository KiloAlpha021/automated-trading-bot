from __future__ import annotations

from dataclasses import replace
from typing import Any

from automated_trading_bot.research import portfolio_construction as pc
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.calendars import (
    CalendarSessionResolutionCandidates,
    CalendarSessionResolutionQuery,
    resolve_calendar_session,
    CalendarSessionResolutionResult,
    LocalWallTime,
    TimezoneId,
    TradingDayKind,
)
from automated_trading_bot.corporate_actions.model import ActionVersionId
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import (
    EvidenceContentDigest,
)
from automated_trading_bot.research.dataset_qualification import ClaimCeiling
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.portfolio_construction import (
    AdmissionClass,
    InsufficientEvidenceError,
    AttributionOutcome,
    CommitmentRequest,
    CommitmentState,
    PortfolioAdmission,
    PortfolioConstructionError,
    PriceCostCeiling,
    StrategyFinancialEffects,
    StressKind,
    allocate_equal_budgets,
    allocate_shared_cents,
    admit_assurance_context,
    attribute_portfolio,
    build_portfolio_evidence,
    correlation_stress,
    create_commitment_book,
    execution_cost_stress,
    reconcile_commitment,
    record_stress,
    reserve_commitments,
    schedule_monthly_rebalance,
)
from automated_trading_bot.research.reconciliation_performance import reconcile
from automated_trading_bot.research.robustness_assurance import (
    AssuranceContext,
    ResearchAuthority,
    RobustnessCase,
    RobustnessReport,
    RobustnessTest,
    StrategyBinding,
    assess_robustness,
)
from automated_trading_bot.research.statistical_assurance import AssuranceOutcome
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
    SimulatedOrder,
    SimulatedOms,
    create_order,
    post_fill_to_ledger,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerEventId,
    SimulationLedger,
)


USD = Currency("USD")
GBP = Currency("GBP")
DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))


def _time(day: int, hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 10, day, hour, tzinfo=UTC))


def _run(index: int) -> ResearchRunId:
    return ResearchRunId(f"atis:research:run:v1:sha256:{index:064x}")


def _admission(
    strategy_id: str,
    index: int,
    admission_class: AdmissionClass = AdmissionClass.HYPOTHETICAL_PARTICIPATION,
) -> PortfolioAdmission:
    context, robustness = _assurance(index, strategy_id)
    return admit_assurance_context(
        context=context,
        admission_class=admission_class,
        robustness=robustness if admission_class is AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY else None,
    )


def _assurance(
    index: int, strategy_id: str = "MOM-001"
) -> tuple[AssuranceContext, RobustnessReport]:
    binding = StrategyBinding(
        strategy_id,
        _run(index).value,
        _time(1, 10).value.isoformat(),
        f"sha256:{index:064x}",
        ResearchAuthority.RESEARCH_ONLY,
    )
    performance = reconcile(SimulatedOms(), _ledger(_run(index))).performance
    assert performance is not None
    context = AssuranceContext(
        binding,
        f"sha256:{index + 20:064x}",
        f"experiment-{index}",
        "dataset-1",
        "v1",
        "qualification-1",
        f"admission-{index}",
        (_run(index).value,),
        performance,
        f"sha256:{index + 10:064x}",
    )
    cases = tuple(
        RobustnessCase(test, AssuranceOutcome.TEST_PASS, f"{test.value} passed", (f"evidence-{test.value}",))
        for test in RobustnessTest
    )
    return context, assess_robustness(context, cases)


def _eligibility() -> DecisionInputEligibility:
    return DecisionInputEligibility(
        EligibilityState.ELIGIBLE,
        (),
        "atis:research:feature-observation:v1:sha256:" + "3" * 64,
        _time(1, 10),
        ClaimCeiling.ECONOMIC_VALIDATION,
    )


def _order(
    admission: PortfolioAdmission,
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "100",
    release_hour: int = 11,
    currency: Currency = USD,
) -> SimulatedOrder:
    return create_order(
        instrument_id=INSTRUMENT,
        side=side,
        quantity=Decimal(quantity),
        currency=currency,
        release_at=_time(1, release_hour),
        research_run_id=ResearchRunId(admission.strategy.research_run_id),
        evidence_digest=DIGEST,
        eligibility=_eligibility(),
    )


def _cost_model(scenario: CostScenario = CostScenario.EC0) -> ExecutionCostModel:
    rate = "0" if scenario is CostScenario.EC0 else "2"
    rates = tuple(
        CostRate(component, Decimal(rate), CostProvenance.MODELLED, DIGEST)
        for component in CostComponent
    )
    return ExecutionCostModel(f"atis:simulation-cost-model:v1:{scenario.value.lower()}", scenario, rates, scenario is CostScenario.EC0)


def _ceiling(price: str = "40", *, current: bool = True, currency: Currency = USD) -> PriceCostCeiling:
    return PriceCostCeiling(Decimal(price), _time(1, 9), DIGEST, currency, current, _cost_model())


def _ledger(run: ResearchRunId, amount: str = "100000") -> SimulationLedger:
    opening = FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:opening"),
        1,
        _time(1, 9),
        FinancialEventKind.OPENING_CASH,
        run,
        DIGEST,
        currency=USD,
        cash_delta=Decimal(amount),
    )
    return SimulationLedger.empty(USD).apply(opening)


def _holding_ledger(run: ResearchRunId, quantity: str = "100") -> SimulationLedger:
    ledger = _ledger(run)
    trade = FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:holding"),
        2,
        _time(1, 9),
        FinancialEventKind.TRADE,
        run,
        DIGEST,
        currency=USD,
        instrument_id=INSTRUMENT,
        quantity=Decimal(quantity),
        unit_amount=Decimal("20"),
    )
    return ledger.apply(trade)


def _snapshot(price: str, quantity: str) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        _time(1, 13),
        Decimal(price),
        _time(1, 12),
        ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE,
        Decimal(quantity),
        USD,
        DIGEST,
    )


def test_t01_equal_allocation_uses_final_canonical_residual() -> None:
    admissions = (_admission("TREND-001", 2), _admission("MR-001-R1", 3), _admission("MOM-001", 1))
    allocation = allocate_equal_budgets(hypothetical=True, admissions=admissions)
    assert tuple((item.strategy_id, item.amount) for item in allocation.budgets) == (
        ("MOM-001", Decimal("33333.33")),
        ("MR-001-R1", Decimal("33333.33")),
        ("TREND-001", Decimal("33333.34")),
    )


def test_t02_two_strategy_allocation_is_exact() -> None:
    allocation = allocate_equal_budgets(hypothetical=True, admissions=(_admission("TREND-001", 2), _admission("MOM-001", 1)))
    assert [item.amount for item in allocation.budgets] == [Decimal("50000"), Decimal("50000")]


def test_t03_zero_eligible_strategies_retain_all_cash() -> None:
    admissions = (_admission("MOM-001", 1, AdmissionClass.HYPOTHETICAL_PARTICIPATION),)
    allocation = allocate_equal_budgets(admissions)
    assert allocation.budgets == () and allocation.unallocated_cents == 10_000_000
    assert create_commitment_book(allocation, admissions).records == ()


def test_t04_buy_reservation_uses_conservative_upper_obligation() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000")), (admission,))
    order = _order(admission)
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    assert reserved.records[-1].reserved_cash == Decimal("4000.00")
    assert Decimal(book.allocation.total_cents)/100 - reserved.records[-1].reserved_cash == 6000


def test_t05_partial_fill_retains_exact_remaining_reservation() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000")), (admission,))
    order = _order(admission)
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal("62.5"), _snapshot("40", "62.5"), _cost_model())
    ledger = post_fill_to_ledger(_ledger(_run(1), "10000"), oms.fills[0])
    updated = reconcile_commitment(reserved, order_id=order.order_id, oms=oms, ledger=ledger)
    assert updated.records[-1].state is CommitmentState.PARTIALLY_FILLED
    assert updated.records[-1].reserved_cash == Decimal("1500.00")
    assert updated.records[-1].actual_cash_effect == Decimal("2500.00")
    assert updated.records[-1].actual_cash_effect + updated.records[-1].reserved_cash == Decimal("4000.00")


def test_t06_overlapping_holdings_remain_strategy_attributable() -> None:
    admissions = (_admission("MOM-001", 1), _admission("TREND-001", 2))
    first = _order(admissions[0], quantity="100")
    second = _order(admissions[1], quantity="50")
    oms = SimulatedOms().submit(first).submit(second)
    oms = oms.execute(first.order_id, Decimal("100"), _snapshot("20", "100"), _cost_model())
    oms = oms.execute(second.order_id, Decimal("50"), _snapshot("20", "50"), _cost_model())
    ledger = _ledger(_run(1))
    for fill in oms.fills:
        ledger = post_fill_to_ledger(ledger, fill)
    attribution = attribute_portfolio(
        admissions=admissions,
        oms=oms,
        ledger=ledger,
        reconciliation=reconcile(oms, ledger),
        effects=(),
    )
    assert tuple(item.holdings[0][1] for item in attribution.strategies) == (
        Decimal("100"),
        Decimal("50"),
    )
    assert attribution.outcome is AttributionOutcome.COMPLETE
    assert tuple((x.cost_basis, x.realised_pnl, x.unrealised_pnl, x.dividends, x.execution_costs, x.net_pnl) for x in attribution.strategies) == (
        (Decimal(2000), Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        (Decimal(1000), Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0)))
    assert ledger.state.position(INSTRUMENT).quantity == Decimal("150")  # type: ignore[union-attr]


def test_t07_shared_cost_allocation_conserves_cents() -> None:
    result = allocate_shared_cents(Decimal("10.00"), (("MOM-001", Decimal("60")), ("TREND-001", Decimal("40"))))
    assert result == (("MOM-001", Decimal("6")), ("TREND-001", Decimal("4")))
    assert sum((amount for _, amount in result), Decimal()) == Decimal("10.00")


def test_t08_attribution_mismatch_is_invalid() -> None:
    admission = _admission("MOM-001", 1)
    oms, ledger = _holdings()
    mark = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:t08-mark"),
        len(ledger.events)+1, _time(1, 10), FinancialEventKind.MARK, _run(1), DIGEST,
        currency=USD, instrument_id=INSTRUMENT, unit_amount=Decimal("31.90"))
    ledger = ledger.apply(mark)
    result = reconcile(oms, ledger)
    assert result.performance is not None and result.performance.net_pnl == Decimal("1190")
    effect = StrategyFinancialEffects("MOM-001", Decimal(0), Decimal("1200"), Decimal("100000"),
        Decimal(0), Decimal(0), (), (mark.event_id.value,), (ledger.events[0].event_id.value,), (), (), True)
    attribution = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=result, effects=(effect,))
    assert attribution.outcome is AttributionOutcome.INVALID
    assert "FINANCIAL_COMPONENT_MISMATCH:MOM-001" in attribution.limitations


def test_t09_strategy_budget_overcommitment_fails_closed() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("5000")), (admission,))
    with pytest.raises(PortfolioConstructionError, match="strategy budget"):
        reserve_commitments(book, (CommitmentRequest("MOM-001", _order(admission, quantity="150"), _ceiling()),))


def test_t10_simultaneous_batch_rejection_is_atomic_for_every_permutation() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), total_capital=Decimal("10000"), hypothetical=True), (admission,))
    requests = (CommitmentRequest("MOM-001", _order(admission, quantity="125"), _ceiling()),
                CommitmentRequest("MOM-001", _order(admission, quantity="175", release_hour=12), _ceiling()))
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    before = (book, oms, ledger)
    for batch in permutations(requests):
        with pytest.raises(PortfolioConstructionError, match="overcommitment"):
            reserve_commitments(book, batch)
        assert (book, oms, ledger) == before
        assert not book.records and not oms.orders


def test_t11_cancellation_releases_only_after_oms_reconciliation() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000")), (admission,))
    order = _order(admission)
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal("40"), _snapshot("40", "40"), _cost_model())
    ledger = post_fill_to_ledger(_ledger(_run(1), "10000"), oms.fills[0])
    reserved = reconcile_commitment(reserved, order_id=order.order_id, oms=oms, ledger=ledger)
    assert reserved.records[-1].reserved_cash == 2400
    cancelled = oms.cancel(order.order_id)
    assert reserved.records[-1].state is CommitmentState.PARTIALLY_FILLED
    released = reconcile_commitment(reserved, order_id=order.order_id, oms=cancelled, ledger=ledger)
    assert released.records[-1].state is CommitmentState.RELEASED
    assert released.records[-1].filled_quantity == Decimal("40")
    assert released.records[-1].actual_cash_effect == Decimal("1600")
    assert released.records[-1].reserved_cash == Decimal("0")
    assert reserved.records[-1].reserved_cash - released.records[-1].reserved_cash == Decimal("2400")



def _holdings() -> tuple[SimulatedOms, SimulationLedger]:
    eligibility = replace(_eligibility(), decision_time=_time(1, 7))
    order = create_order(instrument_id=INSTRUMENT, side=OrderSide.BUY, quantity=Decimal("100"), currency=USD,
        release_at=_time(1, 8), research_run_id=_run(1), evidence_digest=DIGEST, eligibility=eligibility)
    snapshot = replace(_snapshot("20", "100"), execution_at=_time(1, 9), price_available_at=_time(1, 8))
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal("100"), snapshot, _cost_model())
    opening = replace(_ledger(_run(1)).events[0], occurred_at=_time(1, 6))
    ledger = SimulationLedger.empty(USD).apply(opening)
    return oms, post_fill_to_ledger(ledger, oms.fills[0])


def _sell_request(order: SimulatedOrder, oms: SimulatedOms, ledger: SimulationLedger) -> CommitmentRequest:
    return CommitmentRequest("MOM-001", order, holdings_ledger=ledger, holdings_as_of=order.decision_time,
        holdings_oms=oms, holdings_reconciliation=reconcile(oms, ledger), execution_cost_model=_cost_model())

def test_t12_sell_40_of_100_leaves_60_and_rejects_overlap() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    oms, ledger = _holdings()
    first = _order(admission, side=OrderSide.SELL, quantity="40")
    second = _order(admission, side=OrderSide.SELL, quantity="70", release_hour=12)
    reserved = reserve_commitments(book, (_sell_request(first, oms, ledger),))
    with pytest.raises(PortfolioConstructionError, match="sell overcommitment"):
        reserve_commitments(reserved, (_sell_request(second, oms, ledger),))
    oms = oms.submit(first).execute(first.order_id, Decimal("40"), _snapshot("20", "40"), _cost_model())
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    updated = reconcile_commitment(reserved, order_id=first.order_id, oms=oms, ledger=ledger)
    position = ledger.state.position(INSTRUMENT)
    assert position is not None and position.quantity == 60
    assert updated.records[-1].reserved_holding == 0


def _calendar_inputs(end: date = date(2026, 11, 2)) -> tuple[tuple[CalendarSessionResolutionQuery, CalendarSessionResolutionCandidates], ...]:
    from test_calendar_session_resolution import candidates, day, query, session, timezone_ref, version, CORPUS
    decision = Timestamp(datetime(2026, 10, 30, 22, tzinfo=UTC))
    zone = replace(timezone_ref(), timezone_id=TimezoneId("America/New_York"))
    us_version = replace(version(), market_code="US", venue_code="XNYS", timezone_rule_ref=zone)
    result = []
    for i in range((end - date(2026, 10, 30)).days + 1):
        local = date(2026, 10, 30) + timedelta(days=i)
        q = replace(query(), local_date=local, effective_as_of=decision, knowledge_cutoff=decision, evaluation_at=decision)
        closed = local.weekday() >= 5
        d = replace(day(), local_date=local, day_kind=TradingDayKind.SPECIAL_NON_TRADING_DAY if closed else TradingDayKind.REGULAR_TRADING_DAY)
        source = session()
        offset = -240 if local.month == 10 else -300
        opening, closing = LocalWallTime(9, 30, 0, 0, offset, False, False, None), LocalWallTime(16, 0, 0, 0, offset, False, False, None)
        sess = replace(source, local_date=local, local_open=opening, local_close=closing,
            timezone_rule_ref=zone, utc_open=opening.to_utc(local, zone, CORPUS), utc_close=closing.to_utc(local, zone, CORPUS))
        c = candidates(q=q, versions=(us_version,), days=(d,), sessions=() if closed else (sess,))
        result.append((q, c))
    return tuple(result)


def _calendar(day: date) -> CalendarSessionResolutionResult:
    inputs = _calendar_inputs(max(day, date(2026, 11, 2)))
    q, c = next((q, c) for q, c in inputs if q.local_date == day)
    return resolve_calendar_session(query=q, candidates=c)


def _schedule(**kwargs: Any) -> pc.RebalanceSchedule:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    kwargs.setdefault("commitments", book)
    kwargs.setdefault("oms", SimulatedOms())
    kwargs.setdefault("ledger", _ledger(_run(1)))
    return schedule_monthly_rebalance(**kwargs)


def test_t13_monthly_rebalance_releases_next_eligible_session() -> None:
    final, following = _calendar(date(2026, 10, 30)), _calendar(date(2026, 11, 2))
    schedule = _schedule(calendar_inputs=_calendar_inputs(), final_session=final, next_session=following, session_sequence=(final, following), decision_time=Timestamp(datetime(2026, 10, 30, 22, tzinfo=UTC)), preserved_obligations=("MOM-001",))
    assert schedule.release_time == Timestamp(datetime(2026, 11, 2, 14, 30, tzinfo=UTC))


def test_removed_strategy_schedule_declares_preservation() -> None:
    final, following = _calendar(date(2026, 10, 30)), _calendar(date(2026, 11, 2))
    schedule = _schedule(calendar_inputs=_calendar_inputs(), final_session=final, next_session=following, session_sequence=(final, following), decision_time=Timestamp(datetime(2026, 10, 30, 22, tzinfo=UTC)), preserved_obligations=("MOM-001",), removed_strategies=("MOM-001",))
    assert schedule.removed_strategies == schedule.preserved_obligations


def test_same_close_or_ambiguous_calendar_fails_closed() -> None:
    with pytest.raises(PortfolioConstructionError, match="currentness|after decision"):
        final, following = _calendar(date(2026, 10, 30)), _calendar(date(2026, 11, 2))
        _schedule(calendar_inputs=_calendar_inputs(), final_session=final, next_session=following, session_sequence=(final, following), decision_time=Timestamp(datetime(2026, 11, 2, 15, tzinfo=UTC)), preserved_obligations=())


def test_t16_execution_cost_stress_has_exact_numerical_binding() -> None:
    oms, ledger = _holdings()
    model = _cost_model(CostScenario.EC1)
    costs = (Decimal("2"),)  # 100 * $20 * (5 * 2bps)
    performance = reconcile(oms, ledger).performance
    assert performance is not None
    result = execution_cost_stress(scenario_id="T16", baseline_evidence_digest="sha256:" + "1" * 64,
        gross_pnl=performance.gross_pnl, stressed_costs=costs, oms=oms, ledger=ledger, cost_model=model)
    assert result.kind is StressKind.EXECUTION_COST
    assert dict(result.metrics) == {"gross_pnl": Decimal("0"), "cost_drag": Decimal("2"), "net_pnl": Decimal("-2")}
    with pytest.raises(PortfolioConstructionError, match="fabricated"):
        execution_cost_stress(scenario_id="T16", baseline_evidence_digest="sha256:" + "1" * 64,
            gross_pnl=performance.gross_pnl + 1, stressed_costs=costs, oms=oms, ledger=ledger, cost_model=model)


def test_t17_covariance_stress_rejects_invalid_matrix() -> None:
    valid = correlation_stress(scenario_id="T17-valid", baseline_evidence_digest="sha256:" + "1" * 64, strategy_ids=("MOM-001", "TREND-001"), correlation=((Decimal("1"), Decimal("0.5")), (Decimal("0.5"), Decimal("1"))))
    assert valid.kind is StressKind.CORRELATION
    assert valid.source_strategy_ids == ("MOM-001", "TREND-001")
    assert valid.source_correlation == ((Decimal("1"), Decimal("0.5")), (Decimal("0.5"), Decimal("1")))
    assert valid.metrics == (("correlation:MOM-001:MOM-001", Decimal("1")),
                             ("correlation:MOM-001:TREND-001", Decimal("0.5")),
                             ("correlation:TREND-001:TREND-001", Decimal("1")))
    with pytest.raises(PortfolioConstructionError, match="positive-semidefinite"):
        correlation_stress(scenario_id="T17", baseline_evidence_digest="sha256:" + "1" * 64, strategy_ids=("MOM-001", "TREND-001"), correlation=((Decimal("1"), Decimal("2")), (Decimal("2"), Decimal("1"))))


def _integrated() -> tuple[pc.CommitmentBook, SimulatedOms, SimulationLedger, pc.PortfolioAttribution]:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    order = _order(admission)
    book = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal(100), _snapshot("20", "100"), _cost_model())
    ledger = post_fill_to_ledger(_ledger(_run(1)), oms.fills[0])
    book = reconcile_commitment(book, order_id=order.order_id, oms=oms, ledger=ledger)
    attr = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), effects=())
    return book, oms, ledger, attr


def _evidence(book: pc.CommitmentBook, oms: SimulatedOms, ledger: SimulationLedger,
              attr: pc.PortfolioAttribution, stress: tuple[pc.StressResult, ...]) -> pc.PortfolioEvidence:
    return build_portfolio_evidence(configuration_id="configuration-1", allocation=book.allocation,
        commitments=book, oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), attribution=attr, stress_results=stress)


def test_t18_canonical_replay_is_input_order_independent() -> None:
    book, oms, ledger, attr = _integrated()
    a = execution_cost_stress(scenario_id="cost", baseline_evidence_digest=book.evidence_digest,
        gross_pnl=Decimal(0), stressed_costs=(Decimal(2),), oms=oms, ledger=ledger, cost_model=_cost_model(CostScenario.EC1))
    b = correlation_stress(scenario_id="correlation", baseline_evidence_digest=book.evidence_digest,
        strategy_ids=("MOM-001",), correlation=((Decimal(1),),))
    evidence = _evidence(book, oms, ledger, attr, (a,b))
    assert evidence == _evidence(book, oms, ledger, attr, (b,a))
    assert len(evidence.oms_order_ids) == len(evidence.fill_ids) == 1
    assert evidence.ledger_event_ids and evidence.performance.net_pnl == 0
    assert "HYPOTHETICAL_PARTICIPATION" in evidence.limitations


@pytest.mark.parametrize("defect", ["stale", "currency", "duplicate", "same_close"])
def test_negative_commitment_inputs_fail_closed(defect: str) -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000")), (admission,))
    order = _order(admission, currency=GBP if defect == "currency" else USD, release_hour=10 if defect == "same_close" else 11)
    ceiling = _ceiling(current=defect != "stale", currency=GBP if defect == "currency" else USD)
    request = CommitmentRequest("MOM-001", order, ceiling)
    if defect == "duplicate":
        book = reserve_commitments(book, (request,))
    with pytest.raises((PortfolioConstructionError, ValueError), match="current|currency|duplicate|same-close"):
        reserve_commitments(book, (request,))


def test_missing_attribution_is_explicitly_incomplete() -> None:
    admission = _admission("MOM-001", 1)
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    result = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), effects=())
    assert result.outcome is AttributionOutcome.COMPLETE
    assert result.strategies[0].funding == Decimal("100000")
    assert result.portfolio_net_pnl == 0


def test_fabricated_stress_metrics_and_authority_expansion_are_rejected() -> None:
    with pytest.raises(PortfolioConstructionError, match="evidence_ids"):
        record_stress(scenario_id="fabricated", kind=StressKind.ALLOCATION, baseline_evidence_digest="sha256:" + "1" * 64, metrics=(("return", Decimal("1")),), evidence_ids=(), limitations=())
    with pytest.raises(TypeError, match="unexpected keyword|validated assurance"):
        PortfolioAdmission(authority="TRADING")  # type: ignore[call-arg]


def test_missing_robustness_cannot_create_evidence_supported_admission() -> None:
    context, _ = _assurance(1)
    with pytest.raises(PortfolioConstructionError, match="INSUFFICIENT_EVIDENCE"):
        admit_assurance_context(
            context=context,
            admission_class=AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY,
            robustness=None,
        )


def test_invalid_pit_order_is_rejected_by_existing_oms_contract() -> None:
    admission = _admission("MOM-001", 1)
    ineligible = DecisionInputEligibility(
        EligibilityState.INELIGIBLE,
        ("FUTURE_KNOWN",),
        "atis:research:feature-observation:v1:sha256:" + "4" * 64,
        _time(1, 10),
        ClaimCeiling.CONTRACT_ONLY,
    )
    order = create_order(
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        currency=USD,
        release_at=_time(1, 11),
        research_run_id=ResearchRunId(admission.strategy.research_run_id),
        evidence_digest=DIGEST,
        eligibility=ineligible,
    )
    assert SimulatedOms().submit(order).orders[0].status.value == "REJECTED"


def test_forged_or_incomplete_admission_evidence_is_rejected() -> None:
    with pytest.raises(TypeError):
        PortfolioAdmission()
    context, report = _assurance(1)
    with pytest.raises(PortfolioConstructionError, match="INSUFFICIENT_EVIDENCE"):
        admit_assurance_context(
            context=context,
            admission_class=AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY,
            robustness=replace(report, evidence_digest="sha256:" + "0" * 64),
        )
    with pytest.raises(Exception, match="exact ROB-01"):
        assess_robustness(context, report.cases[:-1])


def test_foreign_allocation_run_is_rejected() -> None:
    first = _admission("MOM-001", 1)
    foreign = _admission("MOM-001", 2)
    with pytest.raises(PortfolioConstructionError, match="strategy-run"):
        create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(first,)), (foreign,))


def test_unknown_effect_and_missing_component_provenance_are_rejected() -> None:
    admission = _admission("MOM-001", 1)
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    unknown = StrategyFinancialEffects("UNKNOWN", Decimal(), Decimal(), Decimal(), Decimal(), Decimal(), (), (), (), (), (), False)
    with pytest.raises(PortfolioConstructionError, match="unknown strategy"):
        attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), effects=(unknown,))
    with pytest.raises(PortfolioConstructionError, match="component provenance"):
        StrategyFinancialEffects("MOM-001", Decimal(), Decimal(), Decimal(), Decimal("1"), Decimal(), (), (), (), (), (), True)


@pytest.mark.parametrize("defect", ["above_ceiling", "mixed_model"])
def test_fill_liability_rejects_unapproved_execution_without_mutating_book(defect: str) -> None:
    admission = _admission("MOM-001", 1)
    allocation = allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000"))
    book = create_commitment_book(allocation, (admission,))
    order = _order(admission)
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    oms = SimulatedOms().submit(order)
    if defect == "above_ceiling":
        oms = oms.execute(order.order_id, Decimal("10"), _snapshot("41", "10"), _cost_model())
    else:
        oms = oms.execute(order.order_id, Decimal("10"), _snapshot("40", "10"), _cost_model())
        oms = oms.execute(order.order_id, Decimal("10"), _snapshot("40", "10"), _cost_model(CostScenario.EC1))
    ledger = _ledger(_run(1), "10000")
    for fill in oms.fills:
        ledger = post_fill_to_ledger(ledger, fill)
    before = reserved.evidence_digest
    with pytest.raises(PortfolioConstructionError, match="price or cost model"):
        reconcile_commitment(reserved, order_id=order.order_id, oms=oms, ledger=ledger)
    assert reserved.evidence_digest == before


def test_sell_requires_ledger_derived_current_holdings() -> None:
    admission = _admission("MOM-001", 1)
    allocation = allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000"))
    book = create_commitment_book(allocation, (admission,))
    order = _order(admission, side=OrderSide.SELL, quantity="1")
    with pytest.raises(PortfolioConstructionError, match="authoritative holding"):
        reserve_commitments(book, (CommitmentRequest("MOM-001", order),))


def test_calendar_skipped_session_is_rejected() -> None:
    final = _calendar(date(2026, 10, 30))
    intervening = _calendar(date(2026, 11, 2))
    skipped = _calendar(date(2026, 11, 3))
    with pytest.raises(PortfolioConstructionError, match="omits|immediately adjacent"):
        _schedule(calendar_inputs=_calendar_inputs(), final_session=final, next_session=skipped, session_sequence=(final, intervening, skipped), decision_time=Timestamp(datetime(2026, 10, 30, 22, tzinfo=UTC)), preserved_obligations=())


def test_foreign_attribution_and_duplicate_stress_are_rejected() -> None:
    admission = _admission("MOM-001", 1)
    allocation = allocate_equal_budgets(hypothetical=True, admissions=(admission,))
    commitments = create_commitment_book(allocation, (admission,))
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    reconciliation = reconcile(oms, ledger)
    attribution = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=reconciliation, effects=())
    with pytest.raises(PortfolioConstructionError, match="foreign attribution"):
        build_portfolio_evidence(configuration_id="config", allocation=allocation, commitments=commitments, oms=oms, ledger=ledger, reconciliation=reconciliation, attribution=replace(attribution, oms_digest="foreign"), stress_results=())
    stress = correlation_stress(scenario_id="duplicate", baseline_evidence_digest=commitments.evidence_digest, strategy_ids=("MOM-001",), correlation=((Decimal("1"),),))
    with pytest.raises(PortfolioConstructionError, match="duplicate stress"):
        build_portfolio_evidence(configuration_id="config", allocation=allocation, commitments=commitments, oms=oms, ledger=ledger, reconciliation=reconciliation, attribution=attribution, stress_results=(stress, stress))


def test_financial_event_provenance_rejects_duplicates_and_wrong_components() -> None:
    with pytest.raises(PortfolioConstructionError, match="contains duplicates"):
        StrategyFinancialEffects("MOM-001", Decimal("1"), Decimal(), Decimal(), Decimal(), Decimal(), ("event-1", "event-1"), (), (), (), (), True)
    admission = _admission("MOM-001", 1)
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    effect = StrategyFinancialEffects("MOM-001", Decimal(), Decimal(), Decimal(), Decimal("1"), Decimal(), (), (), (), ("atis:simulation-ledger-event:v1:opening",), (), True)
    with pytest.raises(PortfolioConstructionError, match="foreign or incorrectly classified"):
        attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), effects=(effect,))


def test_conservative_rounding_and_unconfirmed_cancellation_preserve_liability() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets(hypothetical=True, admissions=(admission,), total_capital=Decimal("10000")), (admission,))
    order = _order(admission, quantity="1")
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling("1.001")),))
    assert reserved.records[-1].reserved_cash == Decimal("1.01")
    submitted = SimulatedOms().submit(order)
    unreleased = reconcile_commitment(reserved, order_id=order.order_id, oms=submitted, ledger=_ledger(_run(1), "10000"))
    assert unreleased.records[-1].state is CommitmentState.RESERVED
    assert unreleased.records[-1].reserved_cash == Decimal("1.01")


def test_invalid_ledger_event_order_fails_closed() -> None:
    out_of_order = FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:out-of-order"),
        3,
        _time(1, 10),
        FinancialEventKind.CASH_ADJUSTMENT,
        _run(1),
        DIGEST,
        currency=USD,
        cash_delta=Decimal("1"),
    )
    with pytest.raises(ValueError, match="contiguous"):
        _ledger(_run(1)).apply(out_of_order)


def test_missing_and_forged_research_evidence_never_promotes_hypothetical() -> None:
    context, report = _assurance(1)
    for evidence in (None, report, replace(report, context_digest="foreign")):
        with pytest.raises(InsufficientEvidenceError, match="INSUFFICIENT_EVIDENCE") as exc:
            admit_assurance_context(context=context, admission_class=AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY, robustness=evidence)
        assert exc.value.outcome == "INSUFFICIENT_EVIDENCE"
    admission = _admission("MOM-001", 1)
    assert admission.admission_class is AdmissionClass.HYPOTHETICAL_PARTICIPATION
    assert "RESEARCH_ELIGIBILITY_NOT_ESTABLISHED" in admission.limitations
    allocation = allocate_equal_budgets((admission,))
    assert not allocation.budgets
    with pytest.raises(PortfolioConstructionError, match="not allocated"):
        reserve_commitments(create_commitment_book(allocation, (admission,)), (CommitmentRequest("MOM-001", _order(admission), _ceiling()),))


def test_interleaved_a_b_a_partial_fill_and_terminal_release() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    a, b = _order(admission), _order(admission, release_hour=12)
    book = reserve_commitments(book, (CommitmentRequest("MOM-001", a, _ceiling()), CommitmentRequest("MOM-001", b, _ceiling())))
    oms = SimulatedOms().submit(a).submit(b).execute(a.order_id, Decimal("10"), _snapshot("40", "10"), _cost_model())
    ledger = post_fill_to_ledger(_ledger(_run(1)), oms.fills[0])
    book = reconcile_commitment(book, order_id=a.order_id, oms=oms, ledger=ledger)
    assert book.records[-1].predecessor_digest == book.records[-2].record_digest
    assert book.records[-1].reserved_cash == 3600
    later = replace(_snapshot("40", "20"), execution_at=_time(1, 14))
    oms = oms.execute(a.order_id, Decimal("20"), later, _cost_model())
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    book = reconcile_commitment(book, order_id=a.order_id, oms=oms, ledger=ledger)
    assert book.records[-1].actual_cash_effect == 1200
    assert book.records[-1].reserved_cash == 2800
    oms = oms.cancel(a.order_id)
    book = reconcile_commitment(book, order_id=a.order_id, oms=oms, ledger=ledger)
    assert book.records[-1].state is CommitmentState.RELEASED
    assert book.records[-1].reserved_cash == 0 and book.records[-1].actual_cash_effect == 1200


def test_same_id_changed_cost_model_parameters_rejected() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    order = _order(admission)
    book = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    changed = replace(_cost_model(CostScenario.EC1), model_id=_cost_model().model_id)
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal("10"), _snapshot("40", "10"), changed)
    ledger = post_fill_to_ledger(_ledger(_run(1)), oms.fills[0])
    before = (book, oms, ledger)
    with pytest.raises(PortfolioConstructionError, match="cost model"):
        reconcile_commitment(book, order_id=order.order_id, oms=oms, ledger=ledger)
    assert before == (book, oms, ledger)


def test_attribution_content_change_retaining_digest_is_rejected() -> None:
    admission = _admission("MOM-001", 1)
    allocation = allocate_equal_budgets((admission,), hypothetical=True)
    book = create_commitment_book(allocation, (admission,))
    oms, ledger = SimulatedOms(), _ledger(_run(1))
    rec = reconcile(oms, ledger)
    attr = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=rec, effects=())
    with pytest.raises(PortfolioConstructionError, match="attribution content"):
        build_portfolio_evidence(configuration_id="config", allocation=allocation, commitments=book, oms=oms,
            ledger=ledger, reconciliation=rec, attribution=replace(attr, portfolio_net_pnl=Decimal("999")), stress_results=())


def test_correlation_dimensions_and_permutations() -> None:
    one, half = Decimal(1), Decimal("0.5")
    for matrix in (((one,),), ((one, half, half), (half, one, half), (half, half, one))):
        with pytest.raises(PortfolioConstructionError, match="positive-semidefinite"):
            correlation_stress(scenario_id="dimension", baseline_evidence_digest="base", strategy_ids=("A", "B"), correlation=matrix)
    a = correlation_stress(scenario_id="same", baseline_evidence_digest="base", strategy_ids=("A", "B"), correlation=((one, half), (half, one)))
    b = correlation_stress(scenario_id="same", baseline_evidence_digest="base", strategy_ids=("B", "A"), correlation=((one, half), (half, one)))
    assert a == b


def test_calendar_omission_rejected_without_trusting_session_subset() -> None:
    final, following = _calendar(date(2026, 10, 30)), _calendar(date(2026, 11, 2))
    with pytest.raises(PortfolioConstructionError, match="omits"):
        _schedule(final_session=final, next_session=following, session_sequence=(final, following),
            calendar_inputs=(_calendar_inputs()[0], _calendar_inputs()[-1]), decision_time=_time(30, 22), preserved_obligations=())


@pytest.mark.parametrize("field", ["admission_class", "strategy", "authority"])
def test_r1_admission_substitution_rejected(field: str) -> None:
    admission = _admission("MOM-001", 1)
    forged = object.__new__(PortfolioAdmission)
    for key in admission.__dataclass_fields__:
        object.__setattr__(forged, key, getattr(admission,key))
    value: object = {"admission_class": AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY,
        "strategy": replace(admission.strategy, research_run_id=_run(2).value), "authority": "TRADING"}[field]
    object.__setattr__(forged, field, value)
    with pytest.raises(PortfolioConstructionError, match="INSUFFICIENT_EVIDENCE|substitution"):
        allocate_equal_budgets((forged,), hypothetical=True)


def test_r2_rehashed_zero_reservation_rejected() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    reserved = reserve_commitments(book, (CommitmentRequest("MOM-001", _order(admission), _ceiling()),))
    record = reserved.records[0]
    fields = {k: getattr(record,k) for k in record.__dataclass_fields__ if k != "record_digest"}
    fields["reserved_cash"] = Decimal(0)
    forged = pc._record(**fields)
    with pytest.raises(PortfolioConstructionError, match="reservation liability"):
        pc.CommitmentBook(book.allocation, book.admissions, (forged,))
    assert reserved.records[0].reserved_cash == 4000


def test_r3_instrument_split_applies_across_admitted_runs() -> None:
    admissions = (_admission("MOM-001",1), _admission("TREND-001",2))
    book = create_commitment_book(allocate_equal_budgets(admissions,hypothetical=True), admissions)
    oms,ledger = _holdings()
    split = FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:reverse-split"),len(ledger.events)+1,
        _time(1,10),FinancialEventKind.SPLIT,_run(2),DIGEST,instrument_id=INSTRUMENT,quantity=Decimal("0.5"),
        corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:"+"3"*64))
    ledger=ledger.apply(split)
    with pytest.raises(PortfolioConstructionError, match="sell overcommitment"):
        reserve_commitments(book,(_sell_request(_order(admissions[0],side=OrderSide.SELL,quantity="75"),oms,ledger),))
    assert ledger.state.position(INSTRUMENT).quantity == 50  # type: ignore[union-attr]


def test_r3_stale_prefix_and_r4_sell_proceeds_not_buy_expenditure() -> None:
    admission=_admission("MOM-001",1)
    book=create_commitment_book(allocate_equal_budgets((admission,),hypothetical=True,total_capital=Decimal(10000)),(admission,))
    oms,ledger=_holdings()
    old_oms,old_ledger=oms,ledger
    sell=_order(admission,side=OrderSide.SELL,quantity="40")
    book=reserve_commitments(book,(_sell_request(sell,oms,ledger),))
    oms=oms.submit(sell).execute(sell.order_id,Decimal(40),_snapshot("20","40"),_cost_model())
    ledger=post_fill_to_ledger(ledger,oms.fills[-1])
    book=reconcile_commitment(book,order_id=sell.order_id,oms=oms,ledger=ledger)
    assert book.records[-1].actual_cash_effect == 800
    with pytest.raises(PortfolioConstructionError, match="stale holding"):
        reserve_commitments(book,(_sell_request(_order(admission,side=OrderSide.SELL,quantity="80",release_hour=12),old_oms,old_ledger),))
    buy=_order(admission,quantity="240",release_hour=12)
    reserved=reserve_commitments(book,(CommitmentRequest("MOM-001",buy,_ceiling()),))
    assert reserved.records[-1].reserved_cash == 9600
    with pytest.raises(PortfolioConstructionError,match="overcommitment"):
        reserve_commitments(book,(CommitmentRequest("MOM-001",_order(admission,quantity="260",release_hour=12),_ceiling()),))


def test_r5_recomputed_digest_does_not_qualify_fabricated_stress() -> None:
    book,oms,ledger,attr=_integrated()
    cost=execution_cost_stress(scenario_id="cost",baseline_evidence_digest=book.evidence_digest,
        gross_pnl=Decimal(0),stressed_costs=(Decimal(2),),oms=oms,ledger=ledger,cost_model=_cost_model(CostScenario.EC1))
    forged=pc._stress_result(cost.scenario_id,cost.kind,cost.baseline_evidence_digest,
        (("gross_pnl",Decimal(0)),("cost_drag",Decimal(2)),("net_pnl",Decimal(999))),cost.limitations,cost.input_evidence_ids)
    forged=replace(forged,source_cost_model=cost.source_cost_model)
    with pytest.raises(PortfolioConstructionError,match="stress content"):
        _evidence(book,oms,ledger,attr,(forged,))
    foreign=correlation_stress(scenario_id="foreign",baseline_evidence_digest=book.evidence_digest,
        strategy_ids=("FOREIGN",),correlation=((Decimal(1),),))
    with pytest.raises(PortfolioConstructionError,match="strategy membership"):
        _evidence(book,oms,ledger,attr,(foreign,))


def _pending_exit_and_removal_preserve_state(remove: bool) -> None:
    admission=_admission("MOM-001",1)
    book=create_commitment_book(allocate_equal_budgets((admission,),hypothetical=True),(admission,))
    oms,ledger=_holdings()
    sell=_order(admission,side=OrderSide.SELL,quantity="40")
    book=reserve_commitments(book,(_sell_request(sell,oms,ledger),))
    oms=oms.submit(sell)
    final,following=_calendar(date(2026,10,30)),_calendar(date(2026,11,2))
    schedule=_schedule(calendar_inputs=_calendar_inputs(),final_session=final,next_session=following,
        session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=("MOM-001",),
        removed_strategies=("MOM-001",) if remove else (),commitments=book,oms=oms,ledger=ledger)
    assert schedule.retained_book == book and schedule.source_oms == oms and schedule.source_ledger == ledger
    carried = pc.carry_forward_rebalance(schedule)
    assert carried.records == book.records and carried.allocation == book.allocation
    if remove:
        assert carried.removed_strategies == ("MOM-001",)
        with pytest.raises(PortfolioConstructionError,match="removed strategy"):
            reserve_commitments(carried,(CommitmentRequest("MOM-001",_order(admission,release_hour=12),_ceiling()),))
    assert schedule.retained_book.records[-1].reserved_holding == 40
    assert ledger.state.position(INSTRUMENT).quantity == 100  # type: ignore[union-attr]
    with pytest.raises(PortfolioConstructionError,match="obligations omitted"):
        _schedule(calendar_inputs=_calendar_inputs(),final_session=final,next_session=following,
            session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=(),commitments=book,oms=oms,ledger=ledger)


def test_r7_funding_not_profit_and_dividend_counted_once() -> None:
    effect=StrategyFinancialEffects("MOM-001",Decimal(10),Decimal(5),Decimal(100000),Decimal(2),Decimal(50),
        ("trade",),("mark",),("opening",),("dividend",),("adjustment",),True)
    assert effect.net_pnl == 17


def test_r6_schedule_binds_market_version_and_order_release() -> None:
    final,following=_calendar(date(2026,10,30)),_calendar(date(2026,11,2))
    inputs=_calendar_inputs()
    schedule=_schedule(calendar_inputs=inputs,final_session=final,next_session=following,
        session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=())
    admission=schedule.retained_book.admissions[0]
    order=create_order(instrument_id=INSTRUMENT,side=OrderSide.BUY,quantity=Decimal(1),currency=USD,
        release_at=schedule.release_time,research_run_id=_run(1),evidence_digest=DIGEST,
        eligibility=replace(_eligibility(),decision_time=schedule.decision_time))
    request=CommitmentRequest(admission.strategy.strategy_id,order,_ceiling(),schedule=schedule)
    assert reserve_commitments(schedule.retained_book,(request,)).records[-1].reserved_cash == 40
    changed=replace(schedule,release_time=Timestamp(datetime(2026,11,2,14,tzinfo=UTC)))
    with pytest.raises(PortfolioConstructionError,match="schedule.*substituted"):
        reserve_commitments(schedule.retained_book,(replace(request,schedule=changed),))
    early=replace(order,release_at=Timestamp(datetime(2026,11,2,14,tzinfo=UTC)))
    with pytest.raises(PortfolioConstructionError,match="release.*schedule"):
        reserve_commitments(schedule.retained_book,(replace(request,order=early),))
    foreign=tuple((q,replace(c,calendar_versions=tuple(replace(v,market_code="GB",venue_code="XLON") for v in c.calendar_versions))) for q,c in inputs)
    with pytest.raises(PortfolioConstructionError,match="US XNYS"):
        _schedule(calendar_inputs=foreign,final_session=final,next_session=following,
            session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=())
    # A version-content change retaining the selected version identity cannot reuse a schedule digest.
    changed_inputs=tuple((q,replace(c,calendar_versions=tuple(replace(v,knowledge_from=Timestamp(datetime(2026,1,1,0,0,2,tzinfo=UTC))) for v in c.calendar_versions))) for q,c in inputs)
    with pytest.raises(PortfolioConstructionError,match="calendar|schedule"):
        reserve_commitments(schedule.retained_book,(replace(request,schedule=replace(schedule,calendar_inputs=changed_inputs)),))


def test_r7_offsetting_component_errors_reject() -> None:
    admission=_admission("MOM-001",1)
    oms,ledger=_holdings()
    mark=FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:offset-mark"),len(ledger.events)+1,
        _time(1,10),FinancialEventKind.MARK,_run(1),DIGEST,currency=USD,instrument_id=INSTRUMENT,unit_amount=Decimal("21"))
    ledger=ledger.apply(mark)
    effect=StrategyFinancialEffects("MOM-001",Decimal(10),Decimal(90),Decimal(100000),Decimal(0),Decimal(0),
        (ledger.events[1].event_id.value,),(mark.event_id.value,),(ledger.events[0].event_id.value,),(),(),True)
    assert effect.net_pnl == 100
    attr=attribute_portfolio(admissions=(admission,),oms=oms,ledger=ledger,reconciliation=reconcile(oms,ledger),effects=(effect,))
    assert attr.outcome is AttributionOutcome.INVALID
    assert "FINANCIAL_COMPONENT_MISMATCH:MOM-001" in attr.limitations


def test_t14_pending_exit_preserved() -> None:
    _pending_exit_and_removal_preserve_state(False)


def test_t15_strategy_removal_preserves_holdings_and_obligations() -> None:
    _pending_exit_and_removal_preserve_state(True)


def test_r7_real_dividend_costs_and_funding_conserve_independently() -> None:
    admission=_admission("MOM-001",1)
    order=_order(admission)
    oms=SimulatedOms().submit(order).execute(order.order_id,Decimal(100),_snapshot("20","100"),_cost_model(CostScenario.EC1))
    ledger=post_fill_to_ledger(_ledger(_run(1)),oms.fills[0])
    mark=FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:cost-mark"),len(ledger.events)+1,
        _time(1,13),FinancialEventKind.MARK,_run(1),DIGEST,currency=USD,instrument_id=INSTRUMENT,unit_amount=Decimal(20))
    ledger=ledger.apply(mark)
    dividend=FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:dividend"),len(ledger.events)+1,
        _time(1,13),FinancialEventKind.CASH_DIVIDEND,_run(1),DIGEST,currency=USD,instrument_id=INSTRUMENT,
        unit_amount=Decimal("0.5"),corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:"+"4"*64))
    ledger=ledger.apply(dividend)
    funding=FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:funding"),len(ledger.events)+1,
        _time(1,14),FinancialEventKind.CASH_ADJUSTMENT,_run(1),DIGEST,currency=USD,cash_delta=Decimal(1000))
    ledger=ledger.apply(funding)
    attr=attribute_portfolio(admissions=(admission,),oms=oms,ledger=ledger,reconciliation=reconcile(oms,ledger),effects=())
    assert attr.outcome is AttributionOutcome.COMPLETE
    strategy=attr.strategies[0]
    assert (strategy.funding,strategy.dividends,strategy.execution_costs,strategy.cost_basis,strategy.unrealised_pnl,strategy.net_pnl) == (
        Decimal(101000),Decimal(50),Decimal(2),Decimal(2002),Decimal(-2),Decimal(48))
    assert attr.portfolio_net_pnl == 48


@pytest.mark.parametrize("currency", [USD, GBP])
def test_f1_final_evidence_currency_boundary(currency: Currency) -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    oms = SimulatedOms()
    ledger = SimulationLedger.empty(currency).apply(replace(_ledger(_run(1)).events[0], currency=currency))
    rec = reconcile(oms, ledger)
    assert rec.performance is not None  # Standalone predecessor supports both currencies.
    if currency == USD:
        attr = attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=rec, effects=())
        assert _evidence(book, oms, ledger, attr, ()).performance.net_pnl == Decimal(0)
    else:
        with pytest.raises(PortfolioConstructionError, match="portfolio/ledger currency mismatch"):
            attribute_portfolio(admissions=(admission,), oms=oms, ledger=ledger, reconciliation=rec, effects=())
        usd = _ledger(_run(1))
        attr = attribute_portfolio(admissions=(admission,), oms=oms, ledger=usd, reconciliation=reconcile(oms, usd), effects=())
        with pytest.raises(PortfolioConstructionError, match="portfolio/ledger currency mismatch"):
            build_portfolio_evidence(configuration_id="currency", allocation=book.allocation, commitments=book,
                oms=oms, ledger=ledger, reconciliation=rec, attribution=attr, stress_results=())


@pytest.mark.parametrize("foreign", ["order", "fill", "event"])
def test_f1_mixed_currency_sources_rejected(foreign: str) -> None:
    book, oms, ledger, attr = _integrated()
    before = (book, oms, ledger)
    changed_oms, changed_ledger = oms, ledger
    if foreign == "order":
        changed_oms = replace(oms, orders=(replace(oms.orders[0], order=replace(oms.orders[0].order, currency=GBP)),))
    elif foreign == "fill":
        changed_oms = replace(oms, fills=(replace(oms.fills[0], currency=GBP),))
    else:
        changed_ledger = replace(ledger, events=(replace(ledger.events[0], currency=GBP), *ledger.events[1:]))
    with pytest.raises(PortfolioConstructionError, match="currency mismatch"):
        build_portfolio_evidence(configuration_id="mixed", allocation=book.allocation, commitments=book,
            oms=changed_oms, ledger=changed_ledger, reconciliation=reconcile(oms, ledger), attribution=attr, stress_results=())
    assert (book, oms, ledger) == before


def _f2_scheduled_reservation() -> tuple[pc.RebalanceSchedule, pc.CommitmentBook]:
    final, following = _calendar(date(2026,10,30)), _calendar(date(2026,11,2))
    schedule = _schedule(calendar_inputs=_calendar_inputs(), final_session=final, next_session=following,
        session_sequence=(final,following), decision_time=_time(30,22), preserved_obligations=())
    order = create_order(instrument_id=INSTRUMENT, side=OrderSide.BUY, quantity=Decimal(1), currency=USD,
        release_at=schedule.release_time, research_run_id=_run(1), evidence_digest=DIGEST,
        eligibility=replace(_eligibility(), decision_time=schedule.decision_time))
    book = reserve_commitments(schedule.retained_book, (CommitmentRequest("MOM-001",order,_ceiling(),schedule=schedule),))
    return schedule, book


def _unchecked_book(book: pc.CommitmentBook, **changes: object) -> pc.CommitmentBook:
    """Model an external/deserialized payload bypassing the dataclass constructor."""
    forged = object.__new__(pc.CommitmentBook)
    for field in book.__dataclass_fields__:
        object.__setattr__(forged, field, changes.get(field, getattr(book,field)))
    digest = pc._digest("S4-13-COMMITMENT-BOOK-V1", {
        "allocation": forged.allocation.evidence_digest,
        "removed_strategies": sorted(forged.removed_strategies),
        "rebalance_transitions": [s.evidence_digest for s in forged.rebalance_transitions],
        "admissions": [a.evidence_digest for a in forged.admissions],
        "records": [r.record_digest for r in forged.records],
    })
    object.__setattr__(forged, "evidence_digest", digest)
    return forged


@pytest.mark.parametrize("defect", ["early_order", "old_schedule_digest", "recomputed_schedule_digest", "unqualified_calendar"])
def test_f2_reconstructed_and_final_boundaries_reject_calendar_bypass(defect: str) -> None:
    schedule, book = _f2_scheduled_reservation()
    original = book.records[0]
    request = original.reservation_request
    assert request is not None
    if defect == "early_order":
        early = create_order(instrument_id=INSTRUMENT, side=OrderSide.BUY, quantity=Decimal(1), currency=USD,
            release_at=Timestamp(datetime(2026,11,2,14,tzinfo=UTC)), research_run_id=_run(1), evidence_digest=DIGEST,
            eligibility=replace(_eligibility(), decision_time=schedule.decision_time))
        request = replace(request, order=early)
    else:
        changed = replace(schedule, release_time=Timestamp(datetime(2026,11,2,14,tzinfo=UTC)))
        if defect == "recomputed_schedule_digest":
            resolved = tuple(resolve_calendar_session(query=q,candidates=c) for q,c in schedule.calendar_inputs)
            digest = pc._digest("S4-13-REBALANCE-V1", {"calendar_inputs": schedule.calendar_inputs,
                "resolved": resolved, "commitments": schedule.retained_book,
                "oms": pc._oms_digest(schedule.source_oms), "ledger": pc._ledger_digest(schedule.source_ledger),
                "decision": changed.decision_time.value.isoformat(), "release": changed.release_time.value.isoformat(),
                "final": changed.final_session_date, "next": changed.next_session_date,
                "obligations": list(changed.preserved_obligations), "removed": list(changed.removed_strategies)})
            changed = replace(changed, evidence_digest=digest)
        elif defect == "unqualified_calendar":
            changed = replace(schedule, calendar_inputs=tuple((q,replace(c,currentness_assessments=())) for q,c in schedule.calendar_inputs))
        request = replace(request, schedule=changed)
    fields = {k:getattr(original,k) for k in original.__dataclass_fields__ if k != "record_digest"}
    fields.update(original_order=request.order, order_id=request.order.order_id, reservation_request=request)
    record = pc._record(**fields)
    with pytest.raises(PortfolioConstructionError, match="schedule|calendar"):
        pc.CommitmentBook(book.allocation, book.admissions, (record,))
    forged = _unchecked_book(book, records=(record,))
    oms = SimulatedOms().submit(request.order)
    ledger = _ledger(_run(1))
    attr = attribute_portfolio(admissions=book.admissions, oms=oms, ledger=ledger, reconciliation=reconcile(oms,ledger), effects=())
    with pytest.raises(PortfolioConstructionError, match="schedule|calendar"):
        _evidence(forged,oms,ledger,attr,())
    assert book.records == (original,) and original.reserved_cash == 40


def _f3_removed_buy() -> tuple[pc.CommitmentBook, SimulatedOms, SimulationLedger]:
    admission = _admission("MOM-001",1)
    book = create_commitment_book(allocate_equal_budgets((admission,),hypothetical=True,total_capital=Decimal(10000)),(admission,))
    order = _order(admission)
    book = reserve_commitments(book,(CommitmentRequest("MOM-001",order,_ceiling()),))
    oms, ledger = SimulatedOms().submit(order), _ledger(_run(1),"10000")
    final,following = _calendar(date(2026,10,30)),_calendar(date(2026,11,2))
    schedule = _schedule(calendar_inputs=_calendar_inputs(),final_session=final,next_session=following,
        session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=("MOM-001",),
        removed_strategies=("MOM-001",),commitments=book,oms=oms,ledger=ledger)
    return pc.carry_forward_rebalance(schedule),oms,ledger


def test_f3_rehashed_removal_erasure_rejects_without_releasing_4000() -> None:
    removed,oms,ledger = _f3_removed_buy()
    assert removed.records[-1].reserved_cash == Decimal(4000)
    assert removed.rebalance_transitions[0].decision_time == _time(30,22)
    request = CommitmentRequest("MOM-001",_order(removed.admissions[0],release_hour=12),_ceiling())
    with pytest.raises(PortfolioConstructionError, match="retained rebalance transitions"):
        replace(removed,removed_strategies=(),evidence_digest="")
    erased = _unchecked_book(removed,removed_strategies=())
    before = (removed,oms,ledger)
    with pytest.raises(PortfolioConstructionError, match="retained rebalance transitions"):
        reserve_commitments(erased,(request,))
    attr = attribute_portfolio(admissions=removed.admissions,oms=oms,ledger=ledger,reconciliation=reconcile(oms,ledger),effects=())
    with pytest.raises(PortfolioConstructionError, match="retained rebalance transitions"):
        _evidence(erased,oms,ledger,attr,())
    assert (removed,oms,ledger) == before
    assert removed.records[-1].reserved_cash == 4000 and removed.allocation.total_cents == 1000000


def test_f3_retained_exit_can_cancel_but_new_entries_cannot_reconstruct() -> None:
    admission = _admission("MOM-001",1)
    book = create_commitment_book(allocate_equal_budgets((admission,),hypothetical=True),(admission,))
    oms,ledger = _holdings()
    sell = _order(admission,side=OrderSide.SELL,quantity="40")
    book = reserve_commitments(book,(_sell_request(sell,oms,ledger),))
    oms = oms.submit(sell)
    final,following = _calendar(date(2026,10,30)),_calendar(date(2026,11,2))
    schedule = _schedule(calendar_inputs=_calendar_inputs(),final_session=final,next_session=following,
        session_sequence=(final,following),decision_time=_time(30,22),preserved_obligations=("MOM-001",),
        removed_strategies=("MOM-001",),commitments=book,oms=oms,ledger=ledger)
    removed = pc.carry_forward_rebalance(schedule)
    assert removed.records[-1].reserved_holding == 40
    cancelled = reconcile_commitment(removed,order_id=sell.order_id,oms=oms.cancel(sell.order_id),ledger=ledger)
    assert cancelled.removed_strategies == ("MOM-001",) and cancelled.records[-1].reserved_holding == 0
    assert cancelled.rebalance_transitions == removed.rebalance_transitions
    assert ledger.state.position(INSTRUMENT).quantity == 100  # type: ignore[union-attr]
    extra = reserve_commitments(book,(CommitmentRequest("MOM-001",_order(admission,release_hour=12),_ceiling()),))
    with pytest.raises(PortfolioConstructionError,match="removed strategy cannot add reconstructed"):
        pc.CommitmentBook(extra.allocation,extra.admissions,extra.records,
            removed_strategies=removed.removed_strategies,rebalance_transitions=removed.rebalance_transitions)


def test_f3_effective_time_and_retained_obligation_substitution_reject() -> None:
    removed,_,_ = _f3_removed_buy()
    schedule = removed.rebalance_transitions[0]
    changed = replace(schedule, decision_time=_time(30,21))
    with pytest.raises(PortfolioConstructionError,match="currentness|substitution"):
        replace(removed,rebalance_transitions=(changed,),evidence_digest="")
    with pytest.raises(PortfolioConstructionError,match="replaces retained obligations"):
        replace(removed,records=(),evidence_digest="")
    with pytest.raises(PortfolioConstructionError,match="retained rebalance transitions"):
        replace(removed,rebalance_transitions=(),evidence_digest="")



def test_h04_zero_weight_receives_no_cent_for_every_permutation() -> None:
    inputs = (("A", Decimal(1)), ("B", Decimal(1)), ("Z", Decimal(0)))
    for permutation in permutations(inputs):
        assert allocate_shared_cents(Decimal("0.01"), permutation) == (
            ("A", Decimal("0.00")), ("B", Decimal("0.01")), ("Z", Decimal("0.00")))
    assert allocate_shared_cents(Decimal("10"), (("A", Decimal(60)), ("B", Decimal(40)))) == (
        ("A", Decimal(6)), ("B", Decimal(4)))


def test_h03_final_consumer_rejects_post_split_sell_overreservation() -> None:
    book, oms, ledger, _ = _integrated()
    order = replace(_order(_admission("MOM-001", 1), side=OrderSide.SELL, quantity="75", release_hour=15), decision_time=_time(1, 14))
    book = reserve_commitments(book, (_sell_request(order, oms, ledger),))
    oms = oms.submit(order)
    ledger = ledger.apply(FinancialEvent(LedgerEventId("atis:simulation-ledger-event:v1:h03"), len(ledger.events)+1,
        _time(1, 16), FinancialEventKind.SPLIT, _run(1), DIGEST, instrument_id=INSTRUMENT,
        quantity=Decimal("0.5"), corporate_action_version_id=ActionVersionId("atis:corporate-action-version:v1:" + "3" * 64)))
    before = (book, oms, ledger)
    attr = attribute_portfolio(admissions=book.admissions, oms=oms, ledger=ledger, reconciliation=reconcile(oms, ledger), effects=())
    with pytest.raises(PortfolioConstructionError, match="INFEASIBLE_RESERVATION"):
        _evidence(book, oms, ledger, attr, ())
    assert before == (book, oms, ledger)



def test_h02_reconstructed_backward_fill_rejects_like_producer() -> None:
    admission = _admission("MOM-001", 1)
    book = create_commitment_book(allocate_equal_budgets((admission,), hypothetical=True), (admission,))
    order = _order(admission)
    book = reserve_commitments(book, (CommitmentRequest("MOM-001", order, _ceiling()),))
    oms = SimulatedOms().submit(order).execute(order.order_id, Decimal(10), replace(_snapshot("20", "10"), execution_at=_time(1,14)), _cost_model())
    ledger = post_fill_to_ledger(_ledger(_run(1)), oms.fills[0])
    book = reconcile_commitment(book, order_id=order.order_id, oms=oms, ledger=ledger)
    oms = oms.execute(order.order_id, Decimal(10), _snapshot("20", "10"), _cost_model())
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    before = (book, oms, ledger)
    with pytest.raises(PortfolioConstructionError, match="chronology"):
        reconcile_commitment(book, order_id=order.order_id, oms=oms, ledger=ledger)
    prior = book.records[-1]
    values = {name:getattr(prior,name) for name in prior.__dataclass_fields__ if name != "record_digest"}
    values.update(sequence=3, predecessor_digest=prior.record_digest, filled_quantity=Decimal(20),
        reserved_cash=Decimal(3200), actual_cash_effect=Decimal(400), source_oms=oms, source_ledger=ledger)
    forged = pc._record(**values)
    with pytest.raises(PortfolioConstructionError, match="chronology"):
        pc.CommitmentBook(book.allocation, book.admissions, book.records+(forged,))
    assert before == (book, oms, ledger)

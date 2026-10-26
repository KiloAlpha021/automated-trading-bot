from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId, TradeId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import ClaimCeiling
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import (
    DecisionInputEligibility,
    EligibilityState,
)
from automated_trading_bot.research.reconciliation_performance import (
    DiscrepancyCode,
    PerformanceTruthError,
    ReconciliationState,
    reconcile,
    require_performance,
)
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostBreakdown,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
    OrderStatus,
    SIMULATED_EXECUTION_AUTHORITY,
    SimulatedExecutionError,
    SimulatedFill,
    SimulatedOms,
    SimulatedOrder,
    create_order,
    post_fill_to_ledger,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerEventId,
    SimulationLedger,
)


DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))
USD = Currency("USD")
GBP = Currency("GBP")


def time(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, hour, tzinfo=timezone.utc))


def eligibility(
    state: EligibilityState = EligibilityState.ELIGIBLE,
) -> DecisionInputEligibility:
    return DecisionInputEligibility(
        state,
        () if state is EligibilityState.ELIGIBLE else ("INPUT_INVALID",),
        "atis:research:feature-observation:v1:sha256:" + "3" * 64,
        time(10),
        ClaimCeiling.ECONOMIC_VALIDATION,
    )


def order(**values: object) -> SimulatedOrder:
    fields = dict(
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        currency=USD,
        release_at=time(11),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=eligibility(),
    )
    fields.update(values)
    return create_order(**fields)  # type: ignore[arg-type]


def model(
    scenario: CostScenario = CostScenario.EC1,
    basis_points: tuple[str, ...] = ("1", "2", "3", "4", "5"),
) -> ExecutionCostModel:
    if scenario is CostScenario.EC0:
        basis_points = ("0",) * 5
    rates = tuple(
        CostRate(component, Decimal(rate), provenance, DIGEST)
        for component, rate, provenance in zip(
            CostComponent,
            basis_points,
            (
                CostProvenance.OBSERVED,
                CostProvenance.OBSERVED,
                CostProvenance.MODELLED,
                CostProvenance.MODELLED,
                CostProvenance.PROXY,
            ),
            strict=True,
        )
    )
    return ExecutionCostModel(
        f"atis:simulation-cost-model:v1:{scenario.value.lower()}",
        scenario,
        rates,
        scenario is CostScenario.EC0,
    )


def snapshot(**values: object) -> ExecutionSnapshot:
    fields = dict(
        execution_at=time(13),
        price=Decimal("20"),
        price_available_at=time(12),
        security_state=ExecutionInputState.AVAILABLE,
        liquidity_state=ExecutionInputState.AVAILABLE,
        available_quantity=Decimal("10"),
        currency=USD,
        evidence_digest=DIGEST,
    )
    fields.update(values)
    return ExecutionSnapshot(**fields)  # type: ignore[arg-type]


def accepted(**values: object) -> tuple[SimulatedOms, SimulatedOrder]:
    candidate = order(**values)
    return SimulatedOms().submit(candidate), candidate


def filled(quantity: str = "10", **snapshot_values: object) -> tuple[SimulatedOms, SimulatedOrder]:
    oms, candidate = accepted()
    result = oms.execute(candidate.order_id, Decimal(quantity), snapshot(**snapshot_values), model())
    return result, candidate


def direct_fill() -> tuple[SimulatedOms, SimulatedFill]:
    source, _ = filled("1")
    target, _ = accepted()
    return target, source.fills[0]


def unsafe_costs(costs: CostBreakdown, **changes: object) -> CostBreakdown:
    value = object.__new__(CostBreakdown)
    for field in fields(CostBreakdown):
        object.__setattr__(value, field.name, changes.get(field.name, getattr(costs, field.name)))
    return value


def unsafe_fill(fill: SimulatedFill, **changes: object) -> SimulatedFill:
    value = object.__new__(SimulatedFill)
    for field in fields(SimulatedFill):
        object.__setattr__(value, field.name, changes.get(field.name, getattr(fill, field.name)))
    return value


def ledger() -> SimulationLedger:
    opening = FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:opening"),
        1,
        time(9),
        FinancialEventKind.OPENING_CASH,
        RUN,
        DIGEST,
        currency=USD,
        cash_delta=Decimal("1000"),
    )
    return SimulationLedger.empty(USD).apply(opening)


def test_eligible_order_is_accepted_with_deterministic_identity() -> None:
    oms, candidate = accepted()
    assert oms.order(candidate.order_id).status is OrderStatus.ACCEPTED  # type: ignore[union-attr]
    assert candidate.order_id == order().order_id


def test_ineligible_order_is_explicitly_rejected() -> None:
    oms = SimulatedOms().submit(order(eligibility=eligibility(EligibilityState.INELIGIBLE)))
    assert oms.orders[0].status is OrderStatus.REJECTED
    assert "INELIGIBLE_DECISION_INPUT" in oms.orders[0].reasons


def test_same_close_release_is_rejected() -> None:
    oms = SimulatedOms().submit(order(release_at=time(10)))
    assert oms.orders[0].status is OrderStatus.REJECTED
    assert "ORDER_RELEASE_NOT_AFTER_DECISION" in oms.orders[0].reasons


def test_cancellation_is_terminal() -> None:
    oms, candidate = accepted()
    cancelled = oms.cancel(candidate.order_id)
    assert cancelled.orders[0].status is OrderStatus.CANCELLED
    with pytest.raises(SimulatedExecutionError, match="terminal"):
        cancelled.execute(candidate.order_id, Decimal("1"), snapshot(), model())


def test_explicit_unfilled_is_valid_and_terminal() -> None:
    oms, candidate = accepted()
    result = oms.mark_unfilled(candidate.order_id, "NO_MARKET_SESSION")
    assert result.orders[0].status is OrderStatus.UNFILLED
    assert result.attempts[0].outcome is OrderStatus.UNFILLED


def test_partial_fill_preserves_remaining_quantity() -> None:
    oms, candidate = filled("4")
    record = oms.order(candidate.order_id)
    assert record is not None
    assert record.status is OrderStatus.PARTIALLY_FILLED
    assert record.remaining_quantity == Decimal("6")


def test_full_fill_is_terminal_and_linked() -> None:
    oms, candidate = filled()
    assert oms.orders[0].status is OrderStatus.FULLY_FILLED
    assert oms.fills[0].order_id == candidate.order_id
    with pytest.raises(SimulatedExecutionError, match="terminal"):
        oms.execute(candidate.order_id, Decimal("1"), snapshot(), model())


def test_requested_overfill_is_rejected() -> None:
    oms, candidate = accepted()
    with pytest.raises(SimulatedExecutionError, match="overfill"):
        oms.execute(candidate.order_id, Decimal("11"), snapshot(), model())


def test_fill_without_order_is_rejected() -> None:
    oms, _ = filled("1")
    orphan = replace(oms.fills[0], order_id=OrderId(UUID(int=9)), fill_id=TradeId(UUID(int=8)))
    with pytest.raises(SimulatedExecutionError, match="does not exist"):
        SimulatedOms().apply_fill(orphan)


def test_order_identity_conflict_fails_closed() -> None:
    oms, candidate = accepted()
    conflicting = replace(candidate, quantity=Decimal("9"))
    with pytest.raises(SimulatedExecutionError, match="conflicts"):
        oms.submit(conflicting)


def test_fill_identity_conflict_fails_closed() -> None:
    oms, _ = filled("2")
    conflicting = replace(oms.fills[0], quantity=Decimal("1"))
    with pytest.raises(SimulatedExecutionError, match="conflicts"):
        oms.apply_fill(conflicting)


def test_valid_direct_fill_is_accepted() -> None:
    oms, fill = direct_fill()
    accepted_fill = oms.apply_fill(fill)
    assert accepted_fill.fills == (fill,)
    assert accepted_fill.orders[0].status is OrderStatus.PARTIALLY_FILLED


def test_direct_fill_none_requested_quantity_records_fill_quantity() -> None:
    oms, fill = direct_fill()
    result = oms.apply_fill(fill)
    assert result.attempts[-1].requested_quantity == fill.quantity


def test_direct_fill_valid_requested_quantity_is_recorded() -> None:
    oms, fill = direct_fill()
    result = oms.apply_fill(fill, requested_quantity=Decimal("2"))
    assert result.attempts[-1].requested_quantity == Decimal("2")


@pytest.mark.parametrize(
    "requested",
    [Decimal("-1"), Decimal("0"), Decimal("NaN"), Decimal("Infinity"), 1],
)
def test_direct_fill_rejects_invalid_requested_quantity(requested: object) -> None:
    oms, fill = direct_fill()
    with pytest.raises((TypeError, SimulatedExecutionError)):
        oms.apply_fill(fill, requested_quantity=requested)  # type: ignore[arg-type]


def test_direct_fill_rejects_requested_quantity_below_fill() -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="below fill"):
        oms.apply_fill(fill, requested_quantity=Decimal("0.5"))


def test_direct_fill_rejects_requested_quantity_above_prefill_remaining() -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="remaining"):
        oms.apply_fill(fill, requested_quantity=Decimal("11"))


@pytest.mark.parametrize(
    "changes",
    [
        {"fill_id": "bad"},
        {"order_id": "bad"},
        {"instrument_id": "bad"},
        {"side": "BUY"},
        {"execution_at": "bad"},
        {"currency": "USD"},
        {"execution_evidence": "bad"},
        {"research_run_id": "bad"},
        {"costs": "bad"},
    ],
)
def test_direct_fill_rejects_malformed_primitive_types(changes: dict[str, object]) -> None:
    oms, fill = direct_fill()
    with pytest.raises(TypeError):
        oms.apply_fill(unsafe_fill(fill, **changes))


@pytest.mark.parametrize(
    "quantity",
    [Decimal("-1"), Decimal("0"), Decimal("NaN"), Decimal("Infinity"), 1],
)
def test_direct_fill_rejects_malformed_quantity(quantity: object) -> None:
    oms, fill = direct_fill()
    with pytest.raises((TypeError, SimulatedExecutionError)):
        oms.apply_fill(unsafe_fill(fill, quantity=quantity))


@pytest.mark.parametrize(
    "price",
    [Decimal("-1"), Decimal("0"), Decimal("NaN"), Decimal("Infinity"), 1],
)
def test_direct_fill_rejects_malformed_execution_price(price: object) -> None:
    oms, fill = direct_fill()
    with pytest.raises((TypeError, SimulatedExecutionError)):
        oms.apply_fill(unsafe_fill(fill, execution_price=price))


@pytest.mark.parametrize("execution_at", [time(11), time(10)])
def test_direct_fill_rejects_execution_not_after_release(
    execution_at: Timestamp,
) -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="after order release"):
        oms.apply_fill(replace(fill, execution_at=execution_at))


def test_direct_fill_rejects_mismatched_research_run() -> None:
    oms, fill = direct_fill()
    other_run = ResearchRunId("atis:research:run:v1:sha256:" + "9" * 64)
    with pytest.raises(SimulatedExecutionError, match="research attribution"):
        oms.apply_fill(replace(fill, research_run_id=other_run))


@pytest.mark.parametrize(
    "component",
    (
        "commission_fees",
        "bid_ask_spread",
        "slippage",
        "market_impact",
        "additional_friction",
    ),
)
def test_cost_breakdown_and_direct_fill_reject_negative_component(
    component: str,
) -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="cannot be negative"):
        replace(fill.costs, **{component: Decimal("-0.01")})
    malformed = unsafe_costs(fill.costs, **{component: Decimal("-0.01")})
    with pytest.raises(SimulatedExecutionError, match="cannot be negative"):
        oms.apply_fill(unsafe_fill(fill, costs=malformed))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_cost_breakdown_and_direct_fill_reject_nonfinite_component(
    value: Decimal,
) -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="finite"):
        replace(fill.costs, commission_fees=value)
    malformed = unsafe_costs(fill.costs, commission_fees=value)
    with pytest.raises(SimulatedExecutionError, match="finite"):
        oms.apply_fill(unsafe_fill(fill, costs=malformed))


def test_direct_fill_rejects_non_decimal_cost_component() -> None:
    oms, fill = direct_fill()
    malformed = unsafe_costs(fill.costs, commission_fees=1)
    with pytest.raises(TypeError, match="Decimal"):
        oms.apply_fill(unsafe_fill(fill, costs=malformed))


def test_direct_fill_rejects_noncanonical_cost_model_identity() -> None:
    oms, fill = direct_fill()
    malformed = unsafe_fill(fill, cost_model_id="not-canonical")
    with pytest.raises(SimulatedExecutionError, match="not canonical"):
        oms.apply_fill(malformed)


def test_direct_fill_rejects_wrong_cost_scenario_type() -> None:
    oms, fill = direct_fill()
    malformed = unsafe_fill(fill, cost_scenario="EC1")
    with pytest.raises(TypeError, match="CostScenario"):
        oms.apply_fill(malformed)


def test_direct_fill_rejects_mismatched_instrument() -> None:
    oms, fill = direct_fill()
    other = InstrumentId(UUID("22345678-1234-4234-8234-123456789abc"))
    with pytest.raises(SimulatedExecutionError, match="does not match"):
        oms.apply_fill(replace(fill, instrument_id=other))


def test_direct_fill_rejects_mismatched_side() -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="does not match"):
        oms.apply_fill(replace(fill, side=OrderSide.SELL))


def test_direct_fill_rejects_mismatched_currency() -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="currency"):
        oms.apply_fill(replace(fill, currency=GBP))


def test_direct_fill_rejects_overfill() -> None:
    oms, fill = direct_fill()
    with pytest.raises(SimulatedExecutionError, match="overfill"):
        oms.apply_fill(replace(fill, quantity=Decimal("11")))


def test_direct_fill_after_terminal_state_is_rejected() -> None:
    completed, candidate = filled()
    fill = completed.fills[0]
    with pytest.raises(SimulatedExecutionError, match="terminal"):
        completed.apply_fill(replace(fill, fill_id=TradeId(UUID(int=77))))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"price": None}, "PRICE_UNAVAILABLE"),
        ({"security_state": ExecutionInputState.UNAVAILABLE}, "SECURITY_UNAVAILABLE"),
        ({"security_state": ExecutionInputState.UNKNOWN}, "SECURITY_UNKNOWN"),
        ({"liquidity_state": ExecutionInputState.UNAVAILABLE}, "LIQUIDITY_UNAVAILABLE"),
        ({"available_quantity": None}, "LIQUIDITY_QUANTITY_INVALID"),
        ({"price_available_at": time(14)}, "PRICE_NOT_AVAILABLE_AT_EXECUTION"),
        ({"execution_at": time(11)}, "EXECUTION_NOT_AFTER_RELEASE"),
        ({"evidence_digest": None}, "EXECUTION_EVIDENCE_MISSING"),
        ({"currency": None}, "CURRENCY_UNKNOWN"),
        ({"currency": GBP}, "CURRENCY_MISMATCH"),
    ],
)
def test_invalid_execution_inputs_produce_no_fill(
    changes: dict[str, object], reason: str
) -> None:
    oms, candidate = accepted()
    result = oms.execute(candidate.order_id, Decimal("1"), snapshot(**changes), model())
    assert result.fills == ()
    assert result.orders[0].status is OrderStatus.UNFILLED
    assert reason in result.attempts[0].reasons


def test_available_liquidity_caps_fill_to_partial() -> None:
    oms, _ = filled("10", available_quantity=Decimal("3"))
    assert oms.fills[0].quantity == Decimal("3")
    assert oms.orders[0].status is OrderStatus.PARTIALLY_FILLED


def test_replay_is_deterministic_for_frozen_inputs() -> None:
    first, candidate = accepted()
    first = first.execute(candidate.order_id, Decimal("4"), snapshot(), model())
    second, candidate_again = accepted()
    second = second.execute(candidate_again.order_id, Decimal("4"), snapshot(), model())
    assert first == second


def test_cost_components_reconcile_and_retain_provenance() -> None:
    costs = model().calculate(Decimal("10"), Decimal("20"))
    assert costs.total == Decimal("0.30")
    assert {rate.provenance for rate in model().rates} == {
        CostProvenance.OBSERVED,
        CostProvenance.MODELLED,
        CostProvenance.PROXY,
    }


@pytest.mark.parametrize("scenario", tuple(CostScenario))
def test_cost_scenario_identity_and_determinism(scenario: CostScenario) -> None:
    cost_model = model(scenario)
    assert cost_model.scenario is scenario
    assert cost_model.calculate(Decimal("2"), Decimal("10")) == cost_model.calculate(
        Decimal("2"), Decimal("10")
    )
    assert cost_model.diagnostic_only is (scenario is CostScenario.EC0)


def test_ec0_cannot_be_represented_as_realistic_baseline() -> None:
    with pytest.raises(SimulatedExecutionError, match="diagnostic-only"):
        replace(model(CostScenario.EC0), diagnostic_only=False)


def test_matching_currency_fill_posts_once_to_s4_04_ledger() -> None:
    oms, _ = filled("2")
    posted = post_fill_to_ledger(ledger(), oms.fills[0])
    assert len(posted.events) == 2
    assert posted.state.position(INSTRUMENT) is not None
    with pytest.raises(ValueError, match="conflicts with immutable history"):
        post_fill_to_ledger(posted, oms.fills[0])
    assert posted.events[-1].evidence_digest == oms.fills[0].execution_evidence


def test_mismatched_currency_fill_is_rejected_by_ledger_boundary() -> None:
    oms, _ = filled("1")
    with pytest.raises(SimulatedExecutionError, match="ledger currency"):
        post_fill_to_ledger(SimulationLedger.empty(GBP), oms.fills[0])


def test_sell_costs_reduce_ledger_proceeds() -> None:
    buy_oms, _ = filled("2")
    funded = post_fill_to_ledger(ledger(), buy_oms.fills[0])
    sell_order = order(side=OrderSide.SELL, quantity=Decimal("1"))
    sell_oms = SimulatedOms().submit(sell_order).execute(
        sell_order.order_id, Decimal("1"), snapshot(), model()
    )
    sold = post_fill_to_ledger(funded, sell_oms.fills[0])
    assert sold.state.cash < funded.state.cash + Decimal("20")


def test_history_is_immutable_and_has_no_live_authority() -> None:
    oms, _ = filled("1")
    with pytest.raises(FrozenInstanceError):
        oms.orders = ()  # type: ignore[misc]
    assert oms.authority == SIMULATED_EXECUTION_AUTHORITY
    assert not any(
        hasattr(oms, name)
        for name in ("connect_broker", "submit_to_broker", "grant_capital", "live_execute")
    )


def test_r9_zero_fill_unfilled_preserves_reason_and_reconciles() -> None:
    oms, candidate = accepted()
    before = (oms.orders, oms.fills, oms.attempts)
    result = oms.mark_unfilled(candidate.order_id, "NO_MARKET_SESSION")
    record = result.order(candidate.order_id)
    assert record is not None
    assert record.status is OrderStatus.UNFILLED
    assert record.filled_quantity == 0 and record.remaining_quantity == 10
    assert record.reasons == ("NO_MARKET_SESSION",)
    assert result.fills == () and len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.order_id == candidate.order_id
    assert attempt.requested_quantity == 10 and attempt.fill_id is None
    assert attempt.outcome is OrderStatus.UNFILLED
    assert attempt.reasons == ("NO_MARKET_SESSION",)
    assert (oms.orders, oms.fills, oms.attempts) == before
    performance = require_performance(reconcile(result, ledger()))
    assert performance.net_pnl == 0 and performance.portfolio_value == 1000


@pytest.mark.parametrize("parts", [("4",), ("1", "3")])
def test_r9_partial_mark_unfilled_rejects_atomically_and_cancel_reconciles(
    parts: tuple[str, ...],
) -> None:
    oms, candidate = accepted()
    financial = ledger()
    for index, part in enumerate(parts):
        oms = oms.execute(candidate.order_id, Decimal(part),
                          snapshot(execution_at=time(13 + index)), model())
        financial = post_fill_to_ledger(financial, oms.fills[-1])
    before = (oms.orders, oms.fills, oms.attempts, financial)
    before_oms = replace(oms)
    with pytest.raises(SimulatedExecutionError, match="UNFILLED requires zero executed quantity"):
        oms.mark_unfilled(candidate.order_id, "REMAINDER_UNAVAILABLE")
    assert oms == before_oms
    assert (oms.orders, oms.fills, oms.attempts, financial) == before
    assert oms.orders[0].status is OrderStatus.PARTIALLY_FILLED
    assert oms.orders[0].filled_quantity == 4
    assert oms.orders[0].remaining_quantity == 6
    assert len(oms.attempts) == len(parts)
    assert financial.state.cash == Decimal("919.88")
    assert require_performance(reconcile(oms, financial)).execution_cost_drag.total == Decimal("0.12")
    cancelled = oms.cancel(candidate.order_id)
    assert cancelled.orders[0].status is OrderStatus.CANCELLED
    assert cancelled.orders[0].filled_quantity == 4
    assert cancelled.orders[0].remaining_quantity == 6
    assert cancelled.fills == oms.fills and cancelled.attempts == oms.attempts
    assert require_performance(reconcile(cancelled, financial)) == require_performance(reconcile(oms, financial))
    # Corrupt only a copy to challenge the unchanged reconciliation boundary.
    inconsistent = replace(oms, orders=(replace(oms.orders[0], status=OrderStatus.UNFILLED),))
    result = reconcile(inconsistent, financial)
    assert result.state is ReconciliationState.DISCREPANCY
    assert {d.code for d in result.discrepancies} == {DiscrepancyCode.QUANTITY_MISMATCH}
    assert result.performance is None
    with pytest.raises(PerformanceTruthError, match="authoritative performance requires reconciled evidence"):
        require_performance(result)


@pytest.mark.parametrize("terminal", ["filled", "cancelled", "rejected", "unfilled"])
def test_r9_terminal_mark_unfilled_rejected_without_mutation(terminal: str) -> None:
    oms, candidate = accepted()
    if terminal == "filled":
        oms = oms.execute(candidate.order_id, Decimal("10"), snapshot(), model())
    elif terminal == "cancelled":
        oms = oms.cancel(candidate.order_id)
    elif terminal == "rejected":
        candidate = order(eligibility=eligibility(EligibilityState.INELIGIBLE))
        oms = SimulatedOms().submit(candidate)
    else:
        oms = oms.mark_unfilled(candidate.order_id, "NO_SESSION")
    before = replace(oms)
    with pytest.raises(SimulatedExecutionError, match="terminal order accepts no transition or fill"):
        oms.mark_unfilled(candidate.order_id, "NO_SESSION")
    assert oms == before
    assert (oms.orders, oms.fills, oms.attempts) == (before.orders, before.fills, before.attempts)


@pytest.mark.parametrize("foreign", [False, True])
def test_r9_missing_or_foreign_order_rejected_without_mutation(foreign: bool) -> None:
    oms, candidate = accepted()
    identity = order(quantity=Decimal("9")).order_id if foreign else OrderId(UUID(int=999))
    assert identity != candidate.order_id
    before = replace(oms)
    with pytest.raises(SimulatedExecutionError, match="order does not exist"):
        oms.mark_unfilled(identity, "NO_SESSION")
    assert oms == before
    assert (oms.orders, oms.fills, oms.attempts) == (before.orders, before.fills, before.attempts)

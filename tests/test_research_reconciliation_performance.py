from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import ClaimCeiling
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.reconciliation_performance import (
    DiscrepancyCode,
    MetricAvailability,
    PerformanceTruthError,
    RECONCILIATION_AUTHORITY,
    ReconciliationState,
    reconcile,
    require_performance,
)
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
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


DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
OTHER_DIGEST = EvidenceContentDigest("sha256:" + "9" * 64)
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
OTHER_RUN = ResearchRunId("atis:research:run:v1:sha256:" + "8" * 64)
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))
OTHER_INSTRUMENT = InstrumentId(UUID("22345678-1234-4234-8234-123456789abc"))
USD = Currency("USD")
GBP = Currency("GBP")


def time(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, hour, tzinfo=timezone.utc))


def eligibility() -> DecisionInputEligibility:
    return DecisionInputEligibility(
        EligibilityState.ELIGIBLE,
        (),
        "atis:research:feature-observation:v1:sha256:" + "3" * 64,
        time(10),
        ClaimCeiling.ECONOMIC_VALIDATION,
    )


def cost_model() -> ExecutionCostModel:
    rates = tuple(
        CostRate(component, Decimal(value), CostProvenance.MODELLED, DIGEST)
        for component, value in zip(CostComponent, ("1", "2", "3", "4", "5"), strict=True)
    )
    return ExecutionCostModel("atis:simulation-cost-model:v1:ec1", CostScenario.EC1, rates, False)


def snapshot(hour: int = 13, price: str = "20", quantity: str = "10") -> ExecutionSnapshot:
    return ExecutionSnapshot(
        time(hour),
        Decimal(price),
        time(hour - 1),
        ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE,
        Decimal(quantity),
        USD,
        DIGEST,
    )


def order(side: OrderSide = OrderSide.BUY, quantity: str = "10"):
    return create_order(
        instrument_id=INSTRUMENT,
        side=side,
        quantity=Decimal(quantity),
        currency=USD,
        release_at=time(11),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=eligibility(),
    )


def opening() -> FinancialEvent:
    return FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:opening"),
        1,
        time(9),
        FinancialEventKind.OPENING_CASH,
        RUN,
        DIGEST,
        currency=USD,
        cash_delta=Decimal("1000"),
    )


def chain(fill_quantity: str = "2") -> tuple[SimulatedOms, SimulationLedger]:
    candidate = order()
    oms = SimulatedOms().submit(candidate).execute(
        candidate.order_id, Decimal(fill_quantity), snapshot(quantity=fill_quantity), cost_model()
    )
    ledger = post_fill_to_ledger(SimulationLedger.empty(USD).apply(opening()), oms.fills[0])
    return oms, ledger


def unsafe(instance: object, **changes: object):
    value = object.__new__(type(instance))
    for field in fields(instance):
        object.__setattr__(value, field.name, changes.get(field.name, getattr(instance, field.name)))
    return value


def codes(result) -> set[DiscrepancyCode]:
    return {item.code for item in result.discrepancies}


def test_valid_chain_reconciles_and_produces_performance_truth() -> None:
    oms, ledger = chain()
    result = reconcile(oms, ledger)
    performance = require_performance(result)
    assert result.state is ReconciliationState.RECONCILED
    assert performance.net_pnl == ledger.state.portfolio_value - Decimal("1000")
    assert performance.gross_pnl - performance.execution_cost_drag.total == performance.net_pnl
    assert performance.portfolio_value == ledger.state.portfolio_value
    assert performance.turnover == Decimal("40")
    assert performance.participation is MetricAvailability.NOT_ESTABLISHED
    assert performance.capacity is MetricAvailability.NOT_ESTABLISHED


def test_multiple_partial_fills_reconcile_deterministically() -> None:
    candidate = order(quantity="4")
    oms = SimulatedOms().submit(candidate)
    oms = oms.execute(candidate.order_id, Decimal("2"), snapshot(13, quantity="2"), cost_model())
    oms = oms.execute(candidate.order_id, Decimal("2"), snapshot(14, price="21", quantity="2"), cost_model())
    ledger = SimulationLedger.empty(USD).apply(opening())
    for fill in oms.fills:
        ledger = post_fill_to_ledger(ledger, fill)
    assert reconcile(oms, ledger) == reconcile(oms, ledger)
    assert reconcile(oms, ledger).state is ReconciliationState.RECONCILED


def test_buy_and_sell_postings_reconcile_with_component_cost_drag() -> None:
    buy_order = order(quantity="2")
    buy_oms = SimulatedOms().submit(buy_order).execute(
        buy_order.order_id, Decimal("2"), snapshot(), cost_model()
    )
    ledger = post_fill_to_ledger(SimulationLedger.empty(USD).apply(opening()), buy_oms.fills[0])
    sell_order = order(OrderSide.SELL, quantity="1")
    oms = buy_oms.submit(sell_order).execute(
        sell_order.order_id, Decimal("1"), snapshot(14, price="25", quantity="1"), cost_model()
    )
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    performance = require_performance(reconcile(oms, ledger))
    assert performance.execution_cost_drag.total == sum(
        (fill.costs.total for fill in oms.fills), Decimal(0)
    )
    assert performance.realised_pnl == ledger.state.realised_pnl


def test_missing_order_is_discrepancy_and_performance_fails_closed() -> None:
    oms, ledger = chain()
    result = reconcile(replace(oms, orders=()), ledger)
    assert result.state is ReconciliationState.DISCREPANCY
    assert DiscrepancyCode.MISSING_ORDER in codes(result)
    with pytest.raises(PerformanceTruthError, match="requires reconciled"):
        require_performance(result)


def test_missing_fill_referenced_by_attempt_is_discrepancy() -> None:
    oms, ledger = chain()
    result = reconcile(replace(oms, fills=()), ledger)
    assert DiscrepancyCode.MISSING_FILL in codes(result)


def test_missing_ledger_event_is_discrepancy() -> None:
    oms, _ = chain()
    result = reconcile(oms, SimulationLedger.empty(USD).apply(opening()))
    assert DiscrepancyCode.MISSING_LEDGER_EVENT in codes(result)


def test_duplicate_fill_and_duplicate_posting_are_rejected() -> None:
    oms, ledger = chain()
    duplicate_oms = replace(oms, fills=oms.fills + oms.fills)
    result = reconcile(duplicate_oms, ledger)
    assert DiscrepancyCode.DUPLICATE_FILL in codes(result)
    duplicate_ledger = unsafe(ledger, events=ledger.events + (ledger.events[-1],))
    result = reconcile(oms, duplicate_ledger)
    assert result.state is ReconciliationState.DISCREPANCY
    assert DiscrepancyCode.DUPLICATE_LEDGER_POSTING in codes(result)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("instrument_id", OTHER_INSTRUMENT, DiscrepancyCode.INSTRUMENT_MISMATCH),
        ("side", OrderSide.SELL, DiscrepancyCode.SIDE_MISMATCH),
        ("currency", GBP, DiscrepancyCode.CURRENCY_MISMATCH),
        ("research_run_id", OTHER_RUN, DiscrepancyCode.ATTRIBUTION_MISMATCH),
        ("execution_at", time(10), DiscrepancyCode.TIMING_MISMATCH),
    ],
)
def test_fill_order_mismatch_is_explicit(field: str, value: object, expected: DiscrepancyCode) -> None:
    oms, ledger = chain()
    malformed = unsafe(oms.fills[0], **{field: value})
    result = reconcile(replace(oms, fills=(malformed,)), ledger)
    assert expected in codes(result)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("instrument_id", OTHER_INSTRUMENT, DiscrepancyCode.INSTRUMENT_MISMATCH),
        ("quantity", Decimal("3"), DiscrepancyCode.QUANTITY_MISMATCH),
        ("currency", GBP, DiscrepancyCode.CURRENCY_MISMATCH),
        ("occurred_at", time(12), DiscrepancyCode.TIMING_MISMATCH),
        ("research_run_id", OTHER_RUN, DiscrepancyCode.ATTRIBUTION_MISMATCH),
        ("evidence_digest", OTHER_DIGEST, DiscrepancyCode.ATTRIBUTION_MISMATCH),
        ("unit_amount", Decimal("99"), DiscrepancyCode.COST_MISMATCH),
    ],
)
def test_fill_ledger_mismatch_is_explicit(field: str, value: object, expected: DiscrepancyCode) -> None:
    oms, ledger = chain()
    malformed_event = unsafe(ledger.events[-1], **{field: value})
    malformed_ledger = unsafe(ledger, events=ledger.events[:-1] + (malformed_event,))
    result = reconcile(oms, malformed_ledger)
    assert expected in codes(result) or DiscrepancyCode.INVALID_INPUT in codes(result)


def test_order_aggregate_quantity_disagreement_is_explicit() -> None:
    oms, ledger = chain()
    malformed = replace(oms.orders[0], filled_quantity=Decimal("1"))
    result = reconcile(replace(oms, orders=(malformed,)), ledger)
    assert DiscrepancyCode.QUANTITY_MISMATCH in codes(result)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_fill_or_ledger_values_fail_closed(value: Decimal) -> None:
    oms, ledger = chain()
    malformed_fill = unsafe(oms.fills[0], execution_price=value)
    result = reconcile(replace(oms, fills=(malformed_fill,)), ledger)
    assert result.state is ReconciliationState.INVALID_OR_UNKNOWN
    malformed_state = unsafe(ledger.state, portfolio_value=value)
    result = reconcile(oms, unsafe(ledger, state=malformed_state))
    assert result.state is ReconciliationState.INVALID_OR_UNKNOWN


def test_ledger_state_disagreement_is_explicit() -> None:
    oms, ledger = chain()
    malformed_state = replace(ledger.state, cash=ledger.state.cash + Decimal("1"))
    result = reconcile(oms, unsafe(ledger, state=malformed_state))
    assert DiscrepancyCode.LEDGER_STATE_MISMATCH in codes(result)


def test_invalid_input_state_cannot_produce_performance() -> None:
    result = reconcile(object(), object())
    assert result.state is ReconciliationState.INVALID_OR_UNKNOWN
    assert result.performance is None


def test_reconciliation_does_not_mutate_oms_or_ledger() -> None:
    oms, ledger = chain()
    original = (oms, ledger)
    reconcile(oms, ledger)
    assert (oms, ledger) == original
    with pytest.raises(FrozenInstanceError):
        oms.fills = ()  # type: ignore[misc]


def test_no_live_or_broker_authority_is_exposed() -> None:
    oms, ledger = chain()
    result = reconcile(oms, ledger)
    assert result.authority == RECONCILIATION_AUTHORITY
    assert not any(
        hasattr(result, name)
        for name in ("connect_broker", "submit_order", "grant_capital", "live_execute")
    )

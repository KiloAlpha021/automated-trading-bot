"""Deterministic reconciliation and performance truth for research simulation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from automated_trading_bot.domain.identifiers import OrderId, TradeId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.research.simulated_execution import (
    CostBreakdown,
    ExecutionAttempt,
    OrderRecord,
    OrderStatus,
    SimulatedFill,
    SimulatedOms,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    SimulationLedger,
)


RECONCILIATION_AUTHORITY = "RESEARCH_SIMULATION_TRUTH_NO_TRADING_AUTHORITY"
_ZERO = Decimal(0)
_FILL_EVENT_PREFIX = "atis:simulation-ledger-event:v1:fill-"


class ReconciliationState(StrEnum):
    RECONCILED = "RECONCILED"
    DISCREPANCY = "DISCREPANCY"
    INVALID_OR_UNKNOWN = "INVALID_OR_UNKNOWN"


class DiscrepancyCode(StrEnum):
    MISSING_ORDER = "MISSING_ORDER"
    MISSING_FILL = "MISSING_FILL"
    MISSING_LEDGER_EVENT = "MISSING_LEDGER_EVENT"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    DUPLICATE_FILL = "DUPLICATE_FILL"
    DUPLICATE_LEDGER_POSTING = "DUPLICATE_LEDGER_POSTING"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    TIMING_MISMATCH = "TIMING_MISMATCH"
    ATTRIBUTION_MISMATCH = "ATTRIBUTION_MISMATCH"
    COST_MISMATCH = "COST_MISMATCH"
    LEDGER_STATE_MISMATCH = "LEDGER_STATE_MISMATCH"
    INVALID_INPUT = "INVALID_INPUT"


class MetricAvailability(StrEnum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class PerformanceTruthError(ValueError):
    """Performance truth cannot be established from the supplied evidence."""


@dataclass(frozen=True, slots=True)
class Discrepancy:
    code: DiscrepancyCode
    subject: str


@dataclass(frozen=True, slots=True)
class CostDrag:
    commission_fees: Decimal
    bid_ask_spread: Decimal
    slippage: Decimal
    market_impact: Decimal
    additional_friction: Decimal

    @property
    def total(self) -> Decimal:
        return (
            self.commission_fees
            + self.bid_ask_spread
            + self.slippage
            + self.market_impact
            + self.additional_friction
        )


@dataclass(frozen=True, slots=True)
class PerformanceTruth:
    gross_pnl: Decimal
    execution_cost_drag: CostDrag
    net_pnl: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    portfolio_value: Decimal
    turnover: Decimal
    participation: MetricAvailability
    capacity: MetricAvailability


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    state: ReconciliationState
    discrepancies: tuple[Discrepancy, ...]
    performance: PerformanceTruth | None
    authority: str = RECONCILIATION_AUTHORITY


def _finite(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise PerformanceTruthError(f"{name} must be finite")
    return value


def _expected_unit_amount(fill: SimulatedFill) -> Decimal:
    total = _validated_costs(fill.costs).total
    price = _finite(fill.execution_price, "execution_price")
    quantity = _finite(fill.quantity, "fill quantity")
    if quantity <= 0:
        raise PerformanceTruthError("fill quantity must be positive")
    per_unit_cost = total / quantity
    return price + per_unit_cost if fill.side is OrderSide.BUY else price - per_unit_cost


def _validated_costs(costs: object) -> CostDrag:
    if type(costs) is not CostBreakdown:
        raise TypeError("fill costs must be a CostBreakdown")
    values = tuple(
        _finite(getattr(costs, name), name)
        for name in (
            "commission_fees",
            "bid_ask_spread",
            "slippage",
            "market_impact",
            "additional_friction",
        )
    )
    if any(value < 0 for value in values):
        raise PerformanceTruthError("cost components cannot be negative")
    return CostDrag(*values)


def _add(
    items: list[Discrepancy], code: DiscrepancyCode, subject: object
) -> None:
    item = Discrepancy(code, str(subject))
    if item not in items:
        items.append(item)


def _fill_events(ledger: SimulationLedger) -> tuple[FinancialEvent, ...]:
    return tuple(
        event
        for event in ledger.events
        if event.event_id.value.startswith(_FILL_EVENT_PREFIX)
    )


def _valid_attempt(attempt: ExecutionAttempt) -> bool:
    return (
        type(attempt.order_id) is OrderId
        and type(attempt.requested_quantity) is Decimal
        and attempt.requested_quantity.is_finite()
        and attempt.requested_quantity > 0
        and type(attempt.outcome) is OrderStatus
        and type(attempt.reasons) is tuple
        and all(type(reason) is str for reason in attempt.reasons)
        and (attempt.fill_id is None or type(attempt.fill_id) is TradeId)
    )


def reconcile(oms: object, ledger: object) -> ReconciliationResult:
    """Reconcile immutable S4-05 history to authoritative S4-04 truth."""
    if type(oms) is not SimulatedOms or type(ledger) is not SimulationLedger:
        return ReconciliationResult(
            ReconciliationState.INVALID_OR_UNKNOWN,
            (Discrepancy(DiscrepancyCode.INVALID_INPUT, "oms_or_ledger_type"),),
            None,
        )
    discrepancies: list[Discrepancy] = []
    invalid = False
    try:
        for value, name in (
            (ledger.state.cash, "cash"),
            (ledger.state.realised_pnl, "realised_pnl"),
            (ledger.state.unrealised_pnl, "unrealised_pnl"),
            (ledger.state.portfolio_value, "portfolio_value"),
        ):
            _finite(value, name)
        replayed = SimulationLedger.replay(ledger.currency, ledger.events)
        if replayed.state != ledger.state:
            _add(discrepancies, DiscrepancyCode.LEDGER_STATE_MISMATCH, "replay")
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        _add(discrepancies, DiscrepancyCode.INVALID_INPUT, "ledger")
        invalid = True

    if any(type(item) is not OrderRecord for item in oms.orders):
        _add(discrepancies, DiscrepancyCode.INVALID_INPUT, "orders")
        invalid = True
    if any(type(item) is not ExecutionAttempt for item in oms.attempts):
        _add(discrepancies, DiscrepancyCode.INVALID_INPUT, "attempts")
        invalid = True
    if any(type(item) is not SimulatedFill for item in oms.fills):
        _add(discrepancies, DiscrepancyCode.INVALID_INPUT, "fills")
        invalid = True
    if invalid:
        return ReconciliationResult(
            ReconciliationState.INVALID_OR_UNKNOWN, tuple(discrepancies), None
        )

    order_counts = Counter(item.order.order_id for item in oms.orders)
    fill_counts = Counter(item.fill_id for item in oms.fills)
    event_counts = Counter(item.event_id for item in _fill_events(ledger))
    for order_id, count in order_counts.items():
        if count != 1:
            _add(discrepancies, DiscrepancyCode.DUPLICATE_ORDER, order_id)
    for fill_id, count in fill_counts.items():
        if count != 1:
            _add(discrepancies, DiscrepancyCode.DUPLICATE_FILL, fill_id)
    for ledger_event_id, count in event_counts.items():
        if count != 1:
            _add(
                discrepancies,
                DiscrepancyCode.DUPLICATE_LEDGER_POSTING,
                ledger_event_id,
            )

    orders = {item.order.order_id: item for item in oms.orders}
    fills = {item.fill_id: item for item in oms.fills}
    events = {item.event_id.value: item for item in _fill_events(ledger)}

    if any(not _valid_attempt(attempt) for attempt in oms.attempts):
        _add(discrepancies, DiscrepancyCode.INVALID_INPUT, "attempts")
        return ReconciliationResult(
            ReconciliationState.INVALID_OR_UNKNOWN, tuple(discrepancies), None
        )

    fill_attempts: Counter[TradeId] = Counter()
    cumulative_by_order: dict[OrderId, Decimal] = {}
    fill_outcomes = {OrderStatus.PARTIALLY_FILLED, OrderStatus.FULLY_FILLED}
    for attempt in oms.attempts:
        order_record = orders.get(attempt.order_id)
        if order_record is None:
            _add(discrepancies, DiscrepancyCode.MISSING_ORDER, attempt.order_id)
        fill = fills.get(attempt.fill_id) if attempt.fill_id is not None else None
        if attempt.fill_id is None:
            if attempt.outcome in fill_outcomes:
                _add(
                    discrepancies,
                    DiscrepancyCode.IDENTITY_MISMATCH,
                    f"attempt:{attempt.order_id}",
                )
            continue
        fill_attempts[attempt.fill_id] += 1
        if attempt.outcome not in fill_outcomes:
            _add(
                discrepancies,
                DiscrepancyCode.IDENTITY_MISMATCH,
                f"attempt:{attempt.fill_id}",
            )
        if fill is None:
            _add(discrepancies, DiscrepancyCode.MISSING_FILL, attempt.fill_id)
            continue
        if fill.order_id != attempt.order_id:
            _add(
                discrepancies,
                DiscrepancyCode.IDENTITY_MISMATCH,
                f"attempt:{attempt.fill_id}",
            )
        try:
            fill_quantity = _finite(fill.quantity, "fill quantity")
            if fill_quantity <= 0:
                raise PerformanceTruthError("fill quantity must be positive")
        except (TypeError, ValueError):
            _add(discrepancies, DiscrepancyCode.INVALID_INPUT, attempt.fill_id)
            invalid = True
            continue
        if attempt.requested_quantity < fill_quantity:
            _add(
                discrepancies,
                DiscrepancyCode.QUANTITY_MISMATCH,
                f"attempt:{attempt.fill_id}",
            )
        if order_record is None or fill.order_id != attempt.order_id:
            continue
        cumulative = cumulative_by_order.get(attempt.order_id, _ZERO) + fill_quantity
        cumulative_by_order[attempt.order_id] = cumulative
        expected_outcome = (
            OrderStatus.FULLY_FILLED
            if cumulative == order_record.order.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        if cumulative > order_record.order.quantity or attempt.outcome is not expected_outcome:
            _add(
                discrepancies,
                DiscrepancyCode.QUANTITY_MISMATCH,
                f"attempt:{attempt.fill_id}",
            )

    for fill in oms.fills:
        subject = fill.fill_id.value.hex
        order_record = orders.get(fill.order_id)
        if order_record is None:
            _add(discrepancies, DiscrepancyCode.MISSING_ORDER, subject)
            continue
        if fill_attempts[fill.fill_id] != 1:
            _add(discrepancies, DiscrepancyCode.IDENTITY_MISMATCH, f"attempt:{subject}")
        order = order_record.order
        if fill.instrument_id != order.instrument_id:
            _add(discrepancies, DiscrepancyCode.INSTRUMENT_MISMATCH, subject)
        if fill.side is not order.side:
            _add(discrepancies, DiscrepancyCode.SIDE_MISMATCH, subject)
        if fill.currency != order.currency:
            _add(discrepancies, DiscrepancyCode.CURRENCY_MISMATCH, subject)
        if fill.research_run_id != order.research_run_id:
            _add(discrepancies, DiscrepancyCode.ATTRIBUTION_MISMATCH, subject)
        if fill.execution_at.value <= order.release_at.value:
            _add(discrepancies, DiscrepancyCode.TIMING_MISMATCH, subject)
        expected_ledger_event_key = _FILL_EVENT_PREFIX + subject
        event = events.get(expected_ledger_event_key)
        if event is None:
            _add(discrepancies, DiscrepancyCode.MISSING_LEDGER_EVENT, subject)
            continue
        if event.kind is not FinancialEventKind.TRADE:
            _add(discrepancies, DiscrepancyCode.IDENTITY_MISMATCH, subject)
        if event.instrument_id != fill.instrument_id:
            _add(discrepancies, DiscrepancyCode.INSTRUMENT_MISMATCH, subject)
        expected_quantity = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        if event.quantity != expected_quantity:
            _add(discrepancies, DiscrepancyCode.QUANTITY_MISMATCH, subject)
        if event.currency != fill.currency:
            _add(discrepancies, DiscrepancyCode.CURRENCY_MISMATCH, subject)
        if event.occurred_at.value < fill.execution_at.value:
            _add(discrepancies, DiscrepancyCode.TIMING_MISMATCH, subject)
        if (
            event.research_run_id != fill.research_run_id
            or event.evidence_digest != fill.execution_evidence
        ):
            _add(discrepancies, DiscrepancyCode.ATTRIBUTION_MISMATCH, subject)
        try:
            if event.unit_amount != _expected_unit_amount(fill):
                _add(discrepancies, DiscrepancyCode.COST_MISMATCH, subject)
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            _add(discrepancies, DiscrepancyCode.INVALID_INPUT, subject)
            invalid = True

    fill_event_ids = {
        _FILL_EVENT_PREFIX + fill.fill_id.value.hex for fill in oms.fills
    }
    for ledger_event_key in events:
        if ledger_event_key not in fill_event_ids:
            _add(discrepancies, DiscrepancyCode.MISSING_FILL, ledger_event_key)

    for order_id, record in orders.items():
        related = tuple(fill for fill in oms.fills if fill.order_id == order_id)
        try:
            total = sum((_finite(fill.quantity, "fill quantity") for fill in related), _ZERO)
        except (TypeError, ValueError):
            _add(discrepancies, DiscrepancyCode.INVALID_INPUT, order_id)
            invalid = True
            continue
        if total != record.filled_quantity:
            _add(discrepancies, DiscrepancyCode.QUANTITY_MISMATCH, order_id)
        expected_status = record.status
        if total > 0:
            expected_status = (
                OrderStatus.FULLY_FILLED
                if total == record.order.quantity
                else OrderStatus.PARTIALLY_FILLED
            )
        if total > record.order.quantity or expected_status is not record.status:
            _add(discrepancies, DiscrepancyCode.QUANTITY_MISMATCH, order_id)

    state = (
        ReconciliationState.INVALID_OR_UNKNOWN
        if invalid
        else ReconciliationState.DISCREPANCY
        if discrepancies
        else ReconciliationState.RECONCILED
    )
    if state is not ReconciliationState.RECONCILED:
        return ReconciliationResult(state, tuple(discrepancies), None)
    try:
        performance = _performance(oms, ledger)
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        return ReconciliationResult(
            ReconciliationState.INVALID_OR_UNKNOWN,
            (Discrepancy(DiscrepancyCode.INVALID_INPUT, "performance"),),
            None,
        )
    return ReconciliationResult(state, (), performance)


def _performance(oms: SimulatedOms, ledger: SimulationLedger) -> PerformanceTruth:
    drag = CostDrag(_ZERO, _ZERO, _ZERO, _ZERO, _ZERO)
    turnover = _ZERO
    for fill in oms.fills:
        costs = _validated_costs(fill.costs)
        drag = CostDrag(
            drag.commission_fees + costs.commission_fees,
            drag.bid_ask_spread + costs.bid_ask_spread,
            drag.slippage + costs.slippage,
            drag.market_impact + costs.market_impact,
            drag.additional_friction + costs.additional_friction,
        )
        turnover += abs(_finite(fill.quantity, "fill quantity")) * _finite(
            fill.execution_price, "execution_price"
        )
    funding = sum(
        (
            _finite(event.cash_delta, "cash_delta")
            for event in ledger.events
            if event.kind
            in {FinancialEventKind.OPENING_CASH, FinancialEventKind.CASH_ADJUSTMENT}
        ),
        _ZERO,
    )
    portfolio = _finite(ledger.state.portfolio_value, "portfolio_value")
    net = portfolio - funding
    gross = net + drag.total
    if gross - drag.total != net:
        raise PerformanceTruthError("gross/net performance does not reconcile")
    return PerformanceTruth(
        gross,
        drag,
        net,
        _finite(ledger.state.realised_pnl, "realised_pnl"),
        _finite(ledger.state.unrealised_pnl, "unrealised_pnl"),
        portfolio,
        turnover,
        MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )


def require_performance(result: object) -> PerformanceTruth:
    if type(result) is not ReconciliationResult:
        raise TypeError("result must be a ReconciliationResult")
    if result.state is not ReconciliationState.RECONCILED or result.performance is None:
        raise PerformanceTruthError("authoritative performance requires reconciled evidence")
    return result.performance

"""Deterministic simulated OMS, execution outcomes, and cost attribution.

This module has research-simulation authority only. It cannot submit orders to
a broker, grant capital authority, or act as a production OMS or Risk Gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId, TradeId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import (
    DecisionInputEligibility,
    EligibilityState,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerEventId,
    SimulationLedger,
)


SIMULATED_EXECUTION_AUTHORITY = "RESEARCH_SIMULATION_ONLY_NO_LIVE_TRADING_AUTHORITY"
_ZERO = Decimal(0)
_BPS = Decimal(10_000)


class SimulatedExecutionError(ValueError):
    """The simulated order, execution, or posting contract was violated."""


class OrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNFILLED = "UNFILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FULLY_FILLED = "FULLY_FILLED"


class ExecutionInputState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class CostScenario(StrEnum):
    EC0 = "EC0"
    EC1 = "EC1"
    EC2 = "EC2"
    EC3 = "EC3"


class CostProvenance(StrEnum):
    OBSERVED = "OBSERVED"
    MODELLED = "MODELLED"
    PROXY = "PROXY"


class CostComponent(StrEnum):
    COMMISSION_FEES = "COMMISSION_FEES"
    BID_ASK_SPREAD = "BID_ASK_SPREAD"
    SLIPPAGE = "SLIPPAGE"
    MARKET_IMPACT = "MARKET_IMPACT"
    ADDITIONAL_FRICTION = "ADDITIONAL_FRICTION"


def _finite(value: Decimal, name: str, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite() or (positive and value <= 0):
        qualifier = "positive and finite" if positive else "finite"
        raise SimulatedExecutionError(f"{name} must be {qualifier}")
    return value


def _uuid(body: object) -> UUID:
    raw = bytearray(sha256(canonical_json(body)).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _time(value: Timestamp) -> str:
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class CostRate:
    component: CostComponent
    basis_points: Decimal
    provenance: CostProvenance
    evidence_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        if type(self.component) is not CostComponent:
            raise TypeError("component must be a CostComponent")
        _finite(self.basis_points, "basis_points")
        if self.basis_points < 0:
            raise SimulatedExecutionError("basis_points cannot be negative")
        if type(self.provenance) is not CostProvenance:
            raise TypeError("provenance must be a CostProvenance")
        if type(self.evidence_digest) is not EvidenceContentDigest:
            raise TypeError("evidence_digest must be an EvidenceContentDigest")


@dataclass(frozen=True, slots=True)
class CostBreakdown:
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
class ExecutionCostModel:
    model_id: str
    scenario: CostScenario
    rates: tuple[CostRate, ...]
    diagnostic_only: bool

    def __post_init__(self) -> None:
        if type(self.model_id) is not str or not self.model_id.startswith(
            "atis:simulation-cost-model:v1:"
        ):
            raise SimulatedExecutionError("model_id is not canonical")
        if type(self.scenario) is not CostScenario:
            raise TypeError("scenario must be a CostScenario")
        if type(self.rates) is not tuple or any(type(rate) is not CostRate for rate in self.rates):
            raise TypeError("rates must be a tuple of CostRate values")
        if {rate.component for rate in self.rates} != set(CostComponent):
            raise SimulatedExecutionError("cost model requires every cost component exactly once")
        if len(self.rates) != len(CostComponent):
            raise SimulatedExecutionError("cost model contains duplicate components")
        if type(self.diagnostic_only) is not bool:
            raise TypeError("diagnostic_only must be a bool")
        all_zero = all(rate.basis_points == 0 for rate in self.rates)
        if self.scenario is CostScenario.EC0:
            if not self.diagnostic_only or not all_zero:
                raise SimulatedExecutionError("EC0 must be zero-cost and diagnostic-only")
        elif self.diagnostic_only or all_zero:
            raise SimulatedExecutionError("EC1-EC3 must be non-diagnostic with explicit cost")

    def calculate(self, quantity: Decimal, price: Decimal) -> CostBreakdown:
        _finite(quantity, "quantity", positive=True)
        _finite(price, "price", positive=True)
        notional = quantity * price
        values = {
            rate.component: notional * rate.basis_points / _BPS for rate in self.rates
        }
        return CostBreakdown(
            values[CostComponent.COMMISSION_FEES],
            values[CostComponent.BID_ASK_SPREAD],
            values[CostComponent.SLIPPAGE],
            values[CostComponent.MARKET_IMPACT],
            values[CostComponent.ADDITIONAL_FRICTION],
        )


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: OrderId
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    currency: Currency
    release_at: Timestamp
    research_run_id: ResearchRunId
    evidence_digest: EvidenceContentDigest
    eligibility_state: EligibilityState
    eligibility_reasons: tuple[str, ...]
    eligibility_observation_id: str
    decision_time: Timestamp

    def __post_init__(self) -> None:
        if type(self.order_id) is not OrderId or type(self.instrument_id) is not InstrumentId:
            raise TypeError("invalid order identity")
        if type(self.side) is not OrderSide:
            raise TypeError("side must be an OrderSide")
        _finite(self.quantity, "quantity", positive=True)
        if type(self.currency) is not Currency or type(self.release_at) is not Timestamp:
            raise TypeError("invalid order currency or release time")
        if type(self.research_run_id) is not ResearchRunId or type(
            self.evidence_digest
        ) is not EvidenceContentDigest:
            raise TypeError("invalid order attribution")
        if type(self.eligibility_state) is not EligibilityState or type(
            self.eligibility_reasons
        ) is not tuple:
            raise TypeError("invalid eligibility binding")
        if type(self.eligibility_observation_id) is not str or not self.eligibility_observation_id:
            raise SimulatedExecutionError("eligibility observation identity is required")
        if type(self.decision_time) is not Timestamp:
            raise TypeError("decision_time must be a Timestamp")


def create_order(
    *,
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: Decimal,
    currency: Currency,
    release_at: Timestamp,
    research_run_id: ResearchRunId,
    evidence_digest: EvidenceContentDigest,
    eligibility: DecisionInputEligibility,
) -> SimulatedOrder:
    if type(instrument_id) is not InstrumentId or type(side) is not OrderSide:
        raise TypeError("invalid order instrument or side")
    _finite(quantity, "quantity", positive=True)
    if type(currency) is not Currency or type(release_at) is not Timestamp:
        raise TypeError("invalid order currency or release time")
    if type(research_run_id) is not ResearchRunId:
        raise TypeError("research_run_id must be a ResearchRunId")
    if type(evidence_digest) is not EvidenceContentDigest:
        raise TypeError("evidence_digest must be an EvidenceContentDigest")
    if type(eligibility) is not DecisionInputEligibility:
        raise TypeError("eligibility must be a DecisionInputEligibility")
    body = {
        "currency": currency.code,
        "decision_time": _time(eligibility.decision_time),
        "evidence_digest": evidence_digest.value,
        "instrument_id": instrument_id.to_string(),
        "observation_id": eligibility.observation_id,
        "quantity": str(quantity),
        "release_at": _time(release_at),
        "research_run_id": research_run_id.value,
        "side": side.value,
    }
    return SimulatedOrder(
        OrderId(_uuid(body)),
        instrument_id,
        side,
        quantity,
        currency,
        release_at,
        research_run_id,
        evidence_digest,
        eligibility.state,
        eligibility.reasons,
        eligibility.observation_id,
        eligibility.decision_time,
    )


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order: SimulatedOrder
    status: OrderStatus
    filled_quantity: Decimal = _ZERO
    reasons: tuple[str, ...] = ()

    @property
    def remaining_quantity(self) -> Decimal:
        return self.order.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    execution_at: Timestamp
    price: Decimal | None
    price_available_at: Timestamp | None
    security_state: ExecutionInputState
    liquidity_state: ExecutionInputState
    available_quantity: Decimal | None
    currency: Currency | None
    evidence_digest: EvidenceContentDigest | None

    def __post_init__(self) -> None:
        if type(self.execution_at) is not Timestamp:
            raise TypeError("execution_at must be a Timestamp")
        if self.price is not None:
            _finite(self.price, "price", positive=True)
        if self.price_available_at is not None and type(self.price_available_at) is not Timestamp:
            raise TypeError("price_available_at must be a Timestamp or None")
        if type(self.security_state) is not ExecutionInputState or type(
            self.liquidity_state
        ) is not ExecutionInputState:
            raise TypeError("invalid execution input state")
        if self.available_quantity is not None:
            _finite(self.available_quantity, "available_quantity")
        if self.currency is not None and type(self.currency) is not Currency:
            raise TypeError("currency must be Currency or None")
        if self.evidence_digest is not None and type(
            self.evidence_digest
        ) is not EvidenceContentDigest:
            raise TypeError("evidence_digest must be EvidenceContentDigest or None")


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    fill_id: TradeId
    order_id: OrderId
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    execution_price: Decimal
    execution_at: Timestamp
    currency: Currency
    execution_evidence: EvidenceContentDigest
    cost_model_id: str
    cost_scenario: CostScenario
    costs: CostBreakdown
    research_run_id: ResearchRunId

    def __post_init__(self) -> None:
        if type(self.fill_id) is not TradeId or type(self.order_id) is not OrderId:
            raise TypeError("invalid fill or order identity")
        if type(self.instrument_id) is not InstrumentId or type(self.side) is not OrderSide:
            raise TypeError("invalid fill instrument or side")
        _finite(self.quantity, "quantity", positive=True)
        _finite(self.execution_price, "execution_price", positive=True)
        if type(self.execution_at) is not Timestamp or type(self.currency) is not Currency:
            raise TypeError("invalid execution time or currency")
        if type(self.execution_evidence) is not EvidenceContentDigest:
            raise TypeError("execution_evidence must be an EvidenceContentDigest")
        if type(self.cost_model_id) is not str or not self.cost_model_id:
            raise SimulatedExecutionError("cost_model_id is required")
        if type(self.cost_scenario) is not CostScenario or type(self.costs) is not CostBreakdown:
            raise TypeError("invalid fill cost binding")
        if type(self.research_run_id) is not ResearchRunId:
            raise TypeError("research_run_id must be a ResearchRunId")


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    order_id: OrderId
    requested_quantity: Decimal
    outcome: OrderStatus
    reasons: tuple[str, ...]
    fill_id: TradeId | None = None


_TERMINAL = {
    OrderStatus.REJECTED,
    OrderStatus.CANCELLED,
    OrderStatus.UNFILLED,
    OrderStatus.FULLY_FILLED,
}


@dataclass(frozen=True, slots=True)
class SimulatedOms:
    orders: tuple[OrderRecord, ...] = ()
    fills: tuple[SimulatedFill, ...] = ()
    attempts: tuple[ExecutionAttempt, ...] = ()
    authority: str = SIMULATED_EXECUTION_AUTHORITY

    def order(self, order_id: OrderId) -> OrderRecord | None:
        return next((record for record in self.orders if record.order.order_id == order_id), None)

    def submit(self, order: SimulatedOrder) -> "SimulatedOms":
        if type(order) is not SimulatedOrder:
            raise TypeError("order must be a SimulatedOrder")
        existing = self.order(order.order_id)
        if existing is not None:
            if existing.order == order:
                return self
            raise SimulatedExecutionError("order identity conflicts with immutable history")
        reasons = list(order.eligibility_reasons)
        if order.eligibility_state is not EligibilityState.ELIGIBLE:
            reasons.append("INELIGIBLE_DECISION_INPUT")
        if order.release_at.value <= order.decision_time.value:
            reasons.append("ORDER_RELEASE_NOT_AFTER_DECISION")
        canonical = tuple(sorted(set(reasons)))
        status = OrderStatus.REJECTED if canonical else OrderStatus.ACCEPTED
        return replace(self, orders=self.orders + (OrderRecord(order, status, reasons=canonical),))

    def cancel(self, order_id: OrderId) -> "SimulatedOms":
        record = self._active(order_id)
        return self._replace_order(record, replace(record, status=OrderStatus.CANCELLED))

    def mark_unfilled(self, order_id: OrderId, reason: str) -> "SimulatedOms":
        record = self._active(order_id)
        if type(reason) is not str or not reason.strip():
            raise SimulatedExecutionError("unfilled reason must be explicit")
        updated = replace(record, status=OrderStatus.UNFILLED, reasons=(reason,))
        attempt = ExecutionAttempt(order_id, record.remaining_quantity, OrderStatus.UNFILLED, (reason,))
        return replace(self._replace_order(record, updated), attempts=self.attempts + (attempt,))

    def execute(
        self,
        order_id: OrderId,
        requested_quantity: Decimal,
        snapshot: ExecutionSnapshot,
        cost_model: ExecutionCostModel,
    ) -> "SimulatedOms":
        record = self._active(order_id)
        _finite(requested_quantity, "requested_quantity", positive=True)
        if requested_quantity > record.remaining_quantity:
            raise SimulatedExecutionError("requested fill would overfill order")
        if type(snapshot) is not ExecutionSnapshot or type(cost_model) is not ExecutionCostModel:
            raise TypeError("invalid execution snapshot or cost model")
        reasons = _execution_reasons(record.order, snapshot)
        if reasons:
            if record.filled_quantity == 0:
                updated = replace(record, status=OrderStatus.UNFILLED, reasons=reasons)
                next_oms = self._replace_order(record, updated)
            else:
                next_oms = self
            attempt = ExecutionAttempt(order_id, requested_quantity, OrderStatus.UNFILLED, reasons)
            return replace(next_oms, attempts=self.attempts + (attempt,))
        assert snapshot.available_quantity is not None
        assert snapshot.price is not None
        assert snapshot.currency is not None
        assert snapshot.evidence_digest is not None
        fill_quantity = min(requested_quantity, snapshot.available_quantity)
        if fill_quantity <= 0:
            return self.mark_unfilled(order_id, "NO_AVAILABLE_LIQUIDITY")
        costs = cost_model.calculate(fill_quantity, snapshot.price)
        fill_body = {
            "cost_model_id": cost_model.model_id,
            "evidence_digest": snapshot.evidence_digest.value,
            "execution_at": _time(snapshot.execution_at),
            "execution_price": str(snapshot.price),
            "fill_sequence": len(self.fills) + 1,
            "order_id": str(order_id.value),
            "quantity": str(fill_quantity),
        }
        fill = SimulatedFill(
            TradeId(_uuid(fill_body)),
            order_id,
            record.order.instrument_id,
            record.order.side,
            fill_quantity,
            snapshot.price,
            snapshot.execution_at,
            snapshot.currency,
            snapshot.evidence_digest,
            cost_model.model_id,
            cost_model.scenario,
            costs,
            record.order.research_run_id,
        )
        return self.apply_fill(fill, requested_quantity=requested_quantity)

    def apply_fill(
        self, fill: SimulatedFill, *, requested_quantity: Decimal | None = None
    ) -> "SimulatedOms":
        if type(fill) is not SimulatedFill:
            raise TypeError("fill must be a SimulatedFill")
        existing = next((value for value in self.fills if value.fill_id == fill.fill_id), None)
        if existing is not None:
            if existing == fill:
                return self
            raise SimulatedExecutionError("fill identity conflicts with immutable history")
        record = self._active(fill.order_id)
        if fill.instrument_id != record.order.instrument_id or fill.side is not record.order.side:
            raise SimulatedExecutionError("fill does not match its order")
        if fill.currency != record.order.currency:
            raise SimulatedExecutionError("fill currency does not match order currency")
        _finite(fill.quantity, "fill quantity", positive=True)
        cumulative = record.filled_quantity + fill.quantity
        if cumulative > record.order.quantity:
            raise SimulatedExecutionError("fill would overfill order")
        status = (
            OrderStatus.FULLY_FILLED
            if cumulative == record.order.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        updated = replace(record, status=status, filled_quantity=cumulative, reasons=())
        requested = fill.quantity if requested_quantity is None else requested_quantity
        attempt = ExecutionAttempt(fill.order_id, requested, status, (), fill.fill_id)
        next_oms = self._replace_order(record, updated)
        return replace(
            next_oms,
            fills=self.fills + (fill,),
            attempts=self.attempts + (attempt,),
        )

    def _active(self, order_id: OrderId) -> OrderRecord:
        if type(order_id) is not OrderId:
            raise TypeError("order_id must be an OrderId")
        record = self.order(order_id)
        if record is None:
            raise SimulatedExecutionError("order does not exist")
        if record.status in _TERMINAL:
            raise SimulatedExecutionError("terminal order accepts no transition or fill")
        return record

    def _replace_order(self, old: OrderRecord, new: OrderRecord) -> "SimulatedOms":
        return replace(
            self,
            orders=tuple(new if value is old else value for value in self.orders),
        )


def _execution_reasons(
    order: SimulatedOrder, snapshot: ExecutionSnapshot
) -> tuple[str, ...]:
    reasons = []
    if snapshot.price is None or snapshot.price_available_at is None:
        reasons.append("PRICE_UNAVAILABLE")
    elif snapshot.price_available_at.value > snapshot.execution_at.value:
        reasons.append("PRICE_NOT_AVAILABLE_AT_EXECUTION")
    if snapshot.security_state is not ExecutionInputState.AVAILABLE:
        reasons.append(f"SECURITY_{snapshot.security_state.value}")
    if snapshot.liquidity_state is not ExecutionInputState.AVAILABLE:
        reasons.append(f"LIQUIDITY_{snapshot.liquidity_state.value}")
    if snapshot.available_quantity is None or snapshot.available_quantity <= 0:
        reasons.append("LIQUIDITY_QUANTITY_INVALID")
    if snapshot.execution_at.value <= order.release_at.value:
        reasons.append("EXECUTION_NOT_AFTER_RELEASE")
    if snapshot.currency is None:
        reasons.append("CURRENCY_UNKNOWN")
    elif snapshot.currency != order.currency:
        reasons.append("CURRENCY_MISMATCH")
    if snapshot.evidence_digest is None:
        reasons.append("EXECUTION_EVIDENCE_MISSING")
    return tuple(sorted(set(reasons)))


def post_fill_to_ledger(
    ledger: SimulationLedger, fill: SimulatedFill
) -> SimulationLedger:
    if type(ledger) is not SimulationLedger or type(fill) is not SimulatedFill:
        raise TypeError("ledger and fill have invalid types")
    if fill.currency != ledger.currency:
        raise SimulatedExecutionError("fill currency does not match ledger currency")
    per_unit_cost = fill.costs.total / fill.quantity
    unit_amount = (
        fill.execution_price + per_unit_cost
        if fill.side is OrderSide.BUY
        else fill.execution_price - per_unit_cost
    )
    if unit_amount <= 0:
        raise SimulatedExecutionError("cost-adjusted execution price must remain positive")
    signed_quantity = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
    event = FinancialEvent(
        event_id=LedgerEventId(
            "atis:simulation-ledger-event:v1:fill-" + fill.fill_id.value.hex
        ),
        sequence=len(ledger.events) + 1,
        occurred_at=fill.execution_at,
        kind=FinancialEventKind.TRADE,
        research_run_id=fill.research_run_id,
        evidence_digest=fill.execution_evidence,
        currency=fill.currency,
        instrument_id=fill.instrument_id,
        quantity=signed_quantity,
        unit_amount=unit_amount,
    )
    return ledger.apply(event)

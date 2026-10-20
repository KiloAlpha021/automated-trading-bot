"""Immutable, attributable financial accounting for research simulations.

The ledger consumes explicit financial events and has no order, execution,
broker, risk, or capital authority.  Every transition returns a new ledger so
that the complete event history remains available for deterministic replay.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
import re

from automated_trading_bot.corporate_actions.model import ActionVersionId
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ResearchRunId


SIMULATION_ONLY_AUTHORITY = "RESEARCH_SIMULATION_ONLY_NO_TRADING_AUTHORITY"
_EVENT_ID = re.compile(r"atis:simulation-ledger-event:v1:[A-Za-z0-9._:-]{1,160}")


class LedgerContractError(ValueError):
    """An event or transition violates the simulation-ledger contract."""


class FinancialEventKind(StrEnum):
    OPENING_CASH = "OPENING_CASH"
    CASH_ADJUSTMENT = "CASH_ADJUSTMENT"
    TRADE = "TRADE"
    MARK = "MARK"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    SPLIT = "SPLIT"


@dataclass(frozen=True, slots=True)
class LedgerEventId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _EVENT_ID.fullmatch(self.value) is None:
            raise LedgerContractError("event_id is not canonical")


def _finite(value: Decimal, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise LedgerContractError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    event_id: LedgerEventId
    sequence: int
    occurred_at: Timestamp
    kind: FinancialEventKind
    research_run_id: ResearchRunId
    evidence_digest: EvidenceContentDigest
    instrument_id: InstrumentId | None = None
    quantity: Decimal | None = None
    unit_amount: Decimal | None = None
    cash_delta: Decimal | None = None
    corporate_action_version_id: ActionVersionId | None = None

    def __post_init__(self) -> None:
        if type(self.event_id) is not LedgerEventId:
            raise TypeError("event_id must be a LedgerEventId")
        if type(self.sequence) is not int or self.sequence < 1:
            raise LedgerContractError("sequence must be a positive integer")
        if type(self.occurred_at) is not Timestamp:
            raise TypeError("occurred_at must be a Timestamp")
        if type(self.kind) is not FinancialEventKind:
            raise TypeError("kind must be a FinancialEventKind")
        if type(self.research_run_id) is not ResearchRunId:
            raise TypeError("research_run_id must be a ResearchRunId")
        if type(self.evidence_digest) is not EvidenceContentDigest:
            raise TypeError("evidence_digest must be an EvidenceContentDigest")
        for name in ("quantity", "unit_amount", "cash_delta"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)
        self._validate_shape()

    def _validate_shape(self) -> None:
        instrument_required = {
            FinancialEventKind.TRADE,
            FinancialEventKind.MARK,
            FinancialEventKind.CASH_DIVIDEND,
            FinancialEventKind.SPLIT,
        }
        if self.kind in instrument_required:
            if type(self.instrument_id) is not InstrumentId:
                raise LedgerContractError(f"{self.kind.value} requires instrument_id")
        elif self.instrument_id is not None:
            raise LedgerContractError(f"{self.kind.value} forbids instrument_id")

        if self.kind in {FinancialEventKind.OPENING_CASH, FinancialEventKind.CASH_ADJUSTMENT}:
            if self.cash_delta is None or self.cash_delta == 0:
                raise LedgerContractError(f"{self.kind.value} requires nonzero cash_delta")
            self._forbid(quantity=True, unit_amount=True, action=True)
        elif self.kind is FinancialEventKind.TRADE:
            if self.quantity is None or self.quantity == 0:
                raise LedgerContractError("TRADE requires nonzero quantity")
            if self.unit_amount is None or self.unit_amount <= 0:
                raise LedgerContractError("TRADE requires positive unit_amount")
            self._forbid(cash_delta=True, action=True)
        elif self.kind is FinancialEventKind.MARK:
            if self.unit_amount is None or self.unit_amount < 0:
                raise LedgerContractError("MARK requires nonnegative unit_amount")
            self._forbid(quantity=True, cash_delta=True, action=True)
        elif self.kind is FinancialEventKind.CASH_DIVIDEND:
            if self.unit_amount is None or self.unit_amount < 0:
                raise LedgerContractError("CASH_DIVIDEND requires nonnegative unit_amount")
            self._require_action()
            self._forbid(quantity=True, cash_delta=True)
        elif self.kind is FinancialEventKind.SPLIT:
            if self.quantity is None or self.quantity <= 0:
                raise LedgerContractError("SPLIT requires a positive factor in quantity")
            self._require_action()
            self._forbid(unit_amount=True, cash_delta=True)

    def _require_action(self) -> None:
        if type(self.corporate_action_version_id) is not ActionVersionId:
            raise LedgerContractError(f"{self.kind.value} requires corporate-action attribution")

    def _forbid(
        self,
        *,
        quantity: bool = False,
        unit_amount: bool = False,
        cash_delta: bool = False,
        action: bool = False,
    ) -> None:
        values = {
            "quantity": self.quantity if quantity else None,
            "unit_amount": self.unit_amount if unit_amount else None,
            "cash_delta": self.cash_delta if cash_delta else None,
            "corporate_action_version_id": self.corporate_action_version_id if action else None,
        }
        present = [name for name, value in values.items() if value is not None]
        if present:
            raise LedgerContractError(f"{self.kind.value} forbids {', '.join(present)}")


@dataclass(frozen=True, slots=True)
class PositionState:
    instrument_id: InstrumentId
    quantity: Decimal
    cost_basis: Decimal
    mark_price: Decimal
    realised_pnl: Decimal

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.mark_price

    @property
    def unrealised_pnl(self) -> Decimal:
        return self.market_value - self.cost_basis


@dataclass(frozen=True, slots=True)
class LedgerState:
    currency: Currency
    cash: Decimal
    positions: tuple[PositionState, ...]
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    portfolio_value: Decimal

    def position(self, instrument_id: InstrumentId) -> PositionState | None:
        return next((item for item in self.positions if item.instrument_id == instrument_id), None)


@dataclass(frozen=True, slots=True)
class SimulationLedger:
    currency: Currency
    events: tuple[FinancialEvent, ...]
    state: LedgerState
    authority: str = SIMULATION_ONLY_AUTHORITY

    @classmethod
    def empty(cls, currency: Currency) -> "SimulationLedger":
        if type(currency) is not Currency:
            raise TypeError("currency must be a Currency")
        zero = Decimal(0)
        return cls(currency, (), LedgerState(currency, zero, (), zero, zero, zero))

    def apply(self, event: FinancialEvent) -> "SimulationLedger":
        if type(event) is not FinancialEvent:
            raise TypeError("event must be a FinancialEvent")
        existing = next((item for item in self.events if item.event_id == event.event_id), None)
        if existing is not None:
            if existing == event:
                return self
            raise LedgerContractError("event_id conflicts with immutable history")
        if event.sequence != len(self.events) + 1:
            raise LedgerContractError("event sequence is not contiguous")
        if not self.events and event.kind is not FinancialEventKind.OPENING_CASH:
            raise LedgerContractError("first event must establish attributable opening cash")
        if self.events and event.kind is FinancialEventKind.OPENING_CASH:
            raise LedgerContractError("opening cash may be established only once")
        next_state = _transition(self.state, event)
        return SimulationLedger(self.currency, self.events + (event,), next_state)

    @classmethod
    def replay(
        cls, currency: Currency, events: tuple[FinancialEvent, ...]
    ) -> "SimulationLedger":
        if type(events) is not tuple:
            raise TypeError("events must be a tuple")
        ledger = cls.empty(currency)
        for event in events:
            ledger = ledger.apply(event)
        return ledger


def _transition(state: LedgerState, event: FinancialEvent) -> LedgerState:
    positions = {item.instrument_id: item for item in state.positions}
    cash = state.cash
    if event.kind in {FinancialEventKind.OPENING_CASH, FinancialEventKind.CASH_ADJUSTMENT}:
        assert event.cash_delta is not None
        cash += event.cash_delta
    elif event.kind is FinancialEventKind.TRADE:
        assert event.instrument_id is not None
        assert event.quantity is not None
        assert event.unit_amount is not None
        old = positions.get(event.instrument_id)
        if old is None:
            old = PositionState(event.instrument_id, Decimal(0), Decimal(0), event.unit_amount, Decimal(0))
        new_quantity = old.quantity + event.quantity
        if new_quantity < 0:
            raise LedgerContractError("trade would create an unsupported short position")
        if event.quantity > 0:
            cost_basis = old.cost_basis + event.quantity * event.unit_amount
            realised = old.realised_pnl
        else:
            if old.quantity == 0:
                raise LedgerContractError("cannot sell an empty position")
            sold = -event.quantity
            average_cost = old.cost_basis / old.quantity
            cost_basis = old.cost_basis - average_cost * sold
            realised = old.realised_pnl + (event.unit_amount - average_cost) * sold
        if new_quantity == 0:
            cost_basis = Decimal(0)
        positions[event.instrument_id] = PositionState(
            event.instrument_id, new_quantity, cost_basis, event.unit_amount, realised
        )
        cash -= event.quantity * event.unit_amount
    elif event.kind is FinancialEventKind.MARK:
        assert event.instrument_id is not None
        assert event.unit_amount is not None
        old = positions.get(event.instrument_id)
        if old is None or old.quantity == 0:
            raise LedgerContractError("cannot mark an unknown or empty position")
        positions[event.instrument_id] = replace(old, mark_price=event.unit_amount)
    elif event.kind is FinancialEventKind.CASH_DIVIDEND:
        assert event.instrument_id is not None
        assert event.unit_amount is not None
        old = positions.get(event.instrument_id)
        if old is None or old.quantity == 0:
            raise LedgerContractError("cannot pay a dividend on an unknown or empty position")
        income = old.quantity * event.unit_amount
        cash += income
        positions[event.instrument_id] = replace(old, realised_pnl=old.realised_pnl + income)
    elif event.kind is FinancialEventKind.SPLIT:
        assert event.instrument_id is not None
        assert event.quantity is not None
        old = positions.get(event.instrument_id)
        if old is None or old.quantity == 0:
            raise LedgerContractError("cannot split an unknown or empty position")
        positions[event.instrument_id] = replace(
            old,
            quantity=old.quantity * event.quantity,
            mark_price=old.mark_price / event.quantity,
        )
    else:  # pragma: no cover - the enum and constructor make this unreachable
        raise LedgerContractError("unknown financial event")

    ordered = tuple(sorted(positions.values(), key=lambda item: item.instrument_id.to_string()))
    realised = sum((item.realised_pnl for item in ordered), Decimal(0))
    unrealised = sum((item.unrealised_pnl for item in ordered), Decimal(0))
    market_value = sum((item.market_value for item in ordered), Decimal(0))
    portfolio_value = cash + market_value
    if portfolio_value != cash + market_value:
        raise LedgerContractError("portfolio value does not reconcile")
    return LedgerState(state.currency, cash, ordered, realised, unrealised, portfolio_value)

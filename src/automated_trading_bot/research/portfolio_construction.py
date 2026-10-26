"""Research-only portfolio construction, commitments, and attribution.

S4-13 composes accepted Stage 4 interfaces.  It does not execute orders, own
financial truth, or grant broker, capital, paper, or live-trading authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Mapping
from uuid import UUID

from automated_trading_bot.calendars.model import TradingDayKind
from automated_trading_bot.calendars.resolution import (
    CalendarSessionResolutionResult, CalendarSessionResolutionQuery,
    CalendarSessionResolutionCandidates, resolve_calendar_session,
)
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import DatasetQualification
from automated_trading_bot.research.identity import ResearchRun
from automated_trading_bot.research.mean_reversion_strategy import MeanReversionPortfolio
from automated_trading_bot.research.momentum_strategy import MomentumPortfolio
from automated_trading_bot.research.pit_features import DecisionInputEligibility
from automated_trading_bot.research.reconciliation_performance import (
    PerformanceTruth,
    ReconciliationResult,
    reconcile,
    require_performance,
)
from automated_trading_bot.research.research_governance import (
    FrozenResearchPlan,
    ResearchGovernor,
    ResearchJournal,
)
from automated_trading_bot.research.robustness_assurance import (
    AssuranceContext,
    RobustnessReport,
    StrategyBinding,
    assess_robustness,
    bind_strategy_output,
    establish_context,
)
from automated_trading_bot.research.simulated_execution import (
    ExecutionCostModel,
    OrderStatus,
    SimulatedOms,
    SimulatedOrder,
)
from automated_trading_bot.research.simulation_ledger import FinancialEventKind, SimulationLedger
from automated_trading_bot.research.trend_strategy import TrendPortfolio


PORTFOLIO_AUTHORITY = "RESEARCH_ONLY_NO_BROKER_CAPITAL_OR_TRADING_AUTHORITY"
USD = Currency("USD")
_ZERO = Decimal("0")
_CENT = Decimal("0.01")
_ADMISSION_FACTORY_TOKEN = object()
_STRATEGY_OUTPUT = MomentumPortfolio | TrendPortfolio | MeanReversionPortfolio


class PortfolioConstructionError(ValueError):
    """Raised when portfolio evidence fails the S4-13 contract."""


class InsufficientEvidenceError(PortfolioConstructionError):
    """Missing provenance cannot confer research eligibility."""

    outcome = "INSUFFICIENT_EVIDENCE"


class AdmissionClass(StrEnum):
    HYPOTHETICAL_PARTICIPATION = "HYPOTHETICAL_PARTICIPATION"
    EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY = "EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY"


class CommitmentSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class CommitmentState(StrEnum):
    RESERVED = "RESERVED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    RELEASED = "RELEASED"


class AttributionOutcome(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE_ATTRIBUTION = "INCOMPLETE_ATTRIBUTION"
    INVALID = "INVALID"


class StressKind(StrEnum):
    EXECUTION_COST = "EXECUTION_COST"
    ALLOCATION = "ALLOCATION"
    STRATEGY_REMOVAL = "STRATEGY_REMOVAL"
    CONCENTRATION = "CONCENTRATION"
    OVERLAP = "OVERLAP"
    CORRELATION = "CORRELATION"
    COINCIDENT_DRAWDOWN = "COINCIDENT_DRAWDOWN"
    TURNOVER_CASH = "TURNOVER_CASH"


@dataclass(frozen=True, slots=True, init=False)
class PortfolioAdmission:
    source_context: AssuranceContext
    source_robustness: RobustnessReport | None
    admission_class: AdmissionClass
    strategy: StrategyBinding
    context_digest: str
    plan_digest: str
    admission_event_id: str
    dataset_id: str
    dataset_version: str
    qualification_id: str
    robustness_evidence_digest: str | None
    limitations: tuple[str, ...]
    permitted_conclusions: tuple[str, ...]
    evidence_digest: str

    def __new__(cls, factory_token: object = None) -> PortfolioAdmission:
        if factory_token is not _ADMISSION_FACTORY_TOKEN:
            raise TypeError("portfolio admissions must be created from validated assurance evidence")
        return object.__new__(cls)
    authority: str = PORTFOLIO_AUTHORITY

    @classmethod
    def _from_assurance(
        cls,
        context: AssuranceContext,
        admission_class: AdmissionClass,
        robustness: RobustnessReport | None,
        limitations: tuple[str, ...],
    ) -> PortfolioAdmission:
        if type(context) is not AssuranceContext or type(admission_class) is not AdmissionClass:
            raise TypeError("accepted assurance context and admission class required")
        _texts(limitations, "limitations", nonempty=False)
        robustness_digest: str | None = None
        effective_limitations = limitations
        permitted = (
            "HYPOTHETICAL_ENGINEERING_PARTICIPATION_ONLY",
            "ECONOMIC_EDGE_NOT_ESTABLISHED",
            "NO_BROKER_CAPITAL_OR_TRADING_AUTHORITY",
        )
        if admission_class is AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY:
            raise InsufficientEvidenceError(
                "INSUFFICIENT_EVIDENCE: attributable ROB outcomes require authentic source evidence; "
                "report identity or TEST_PASS alone is insufficient"
            )
        if robustness is not None:
            rebuilt = assess_robustness(context, robustness.cases)
            if rebuilt != robustness:
                raise PortfolioConstructionError("hypothetical robustness binding is invalid")
            robustness_digest = robustness.evidence_digest
        effective_limitations = tuple(sorted(set((*limitations,
            "HYPOTHETICAL_PARTICIPATION", "RESEARCH_ELIGIBILITY_NOT_ESTABLISHED"))))
        body = {
            "class": admission_class.value,
            "strategy": context.strategy,
            "source_context": context,
            "authority": PORTFOLIO_AUTHORITY,
            "context": context.context_digest,
            "plan": context.plan_digest,
            "admission": context.admission_event_id,
            "dataset": [context.dataset_id, context.dataset_version, context.qualification_id],
            "robustness": robustness_digest,
            "limitations": list(effective_limitations),
            "permitted": list(permitted),
        }
        value = cls(_ADMISSION_FACTORY_TOKEN)
        fields = (
            context,
            robustness,
            admission_class,
            context.strategy,
            context.context_digest,
            context.plan_digest,
            context.admission_event_id,
            context.dataset_id,
            context.dataset_version,
            context.qualification_id,
            robustness_digest,
            effective_limitations,
            permitted,
            _digest("S4-13-ADMISSION-V1", body),
            PORTFOLIO_AUTHORITY,
        )
        for name, item in zip(cls.__dataclass_fields__, fields, strict=True):
            object.__setattr__(value, name, item)
        return value


@dataclass(frozen=True, slots=True)
class StrategyBudget:
    strategy_id: str
    research_run_id: str
    cents: int

    @property
    def amount(self) -> Decimal:
        return Decimal(self.cents) / Decimal(100)


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    currency: Currency
    total_cents: int
    budgets: tuple[StrategyBudget, ...]
    unallocated_cents: int
    evidence_digest: str
    authority: str = PORTFOLIO_AUTHORITY
    hypothetical: bool = False

    def budget(self, strategy_id: str) -> StrategyBudget | None:
        return next((item for item in self.budgets if item.strategy_id == strategy_id), None)


@dataclass(frozen=True, slots=True)
class PriceCostCeiling:
    upper_price: Decimal
    available_at: Timestamp
    evidence_digest: EvidenceContentDigest
    currency: Currency
    current: bool
    cost_model: ExecutionCostModel

    def __post_init__(self) -> None:
        _positive(self.upper_price, "upper_price")
        if type(self.available_at) is not Timestamp:
            raise TypeError("available_at must be Timestamp")
        if type(self.evidence_digest) is not EvidenceContentDigest:
            raise TypeError("evidence_digest must be EvidenceContentDigest")
        if type(self.currency) is not Currency or type(self.current) is not bool:
            raise TypeError("invalid ceiling currency or currentness")
        if type(self.cost_model) is not ExecutionCostModel:
            raise TypeError("cost_model must be ExecutionCostModel")


@dataclass(frozen=True, slots=True)
class CommitmentRequest:
    strategy_id: str
    order: SimulatedOrder
    price_cost_ceiling: PriceCostCeiling | None = None
    holdings_ledger: SimulationLedger | None = None
    holdings_as_of: Timestamp | None = None
    holdings_oms: SimulatedOms | None = None
    holdings_reconciliation: ReconciliationResult | None = None
    execution_cost_model: ExecutionCostModel | None = None
    schedule: RebalanceSchedule | None = None


@dataclass(frozen=True, slots=True)
class CommitmentRecord:
    sequence: int
    strategy_id: str
    research_run_id: str
    order_id: OrderId
    instrument_id: InstrumentId
    side: CommitmentSide
    quantity: Decimal
    filled_quantity: Decimal
    reserved_cash: Decimal
    reserved_holding: Decimal
    actual_cash_effect: Decimal
    state: CommitmentState
    upper_price: Decimal | None
    unit_cash_ceiling: Decimal | None
    cost_model_id: str | None
    ceiling_evidence_digest: str | None
    order_status: OrderStatus
    predecessor_digest: str | None
    record_digest: str
    original_order: SimulatedOrder
    cost_model: ExecutionCostModel
    reservation_request: CommitmentRequest | None = None
    source_oms: SimulatedOms | None = None
    source_ledger: SimulationLedger | None = None


@dataclass(frozen=True, slots=True)
class CommitmentBook:
    allocation: PortfolioAllocation
    admissions: tuple[PortfolioAdmission, ...]
    records: tuple[CommitmentRecord, ...] = ()
    evidence_digest: str = ""
    authority: str = PORTFOLIO_AUTHORITY
    removed_strategies: tuple[str, ...] = ()
    rebalance_transitions: tuple[RebalanceSchedule, ...] = ()

    def __post_init__(self) -> None:
        if type(self.allocation) is not PortfolioAllocation:
            raise TypeError("allocation must be PortfolioAllocation")
        if type(self.admissions) is not tuple or any(
            type(item) is not PortfolioAdmission for item in self.admissions
        ):
            raise TypeError("admissions must be PortfolioAdmission values")
        _unique_admissions(self.admissions)
        _texts(self.removed_strategies, "removed_strategies", nonempty=False)
        if set(self.removed_strategies) - {a.strategy.strategy_id for a in self.admissions}:
            raise PortfolioConstructionError("unknown removed strategy")
        rebuilt_allocation = allocate_equal_budgets(
            self.admissions, total_capital=Decimal(self.allocation.total_cents) / 100,
            currency=self.allocation.currency, hypothetical=self.allocation.hypothetical,
        )
        if rebuilt_allocation != self.allocation:
            raise PortfolioConstructionError("allocation/admission strategy-run or content mismatch")
        admission_runs = {
            item.strategy.strategy_id: item.strategy.research_run_id for item in self.admissions
        }
        budget_runs = {item.strategy_id: item.research_run_id for item in self.allocation.budgets}
        if budget_runs != {
            strategy_id: admission_runs[strategy_id]
            for strategy_id in budget_runs
            if strategy_id in admission_runs
        } or set(budget_runs) - set(admission_runs):
            raise PortfolioConstructionError("allocation/admission strategy-run identity mismatch")
        if type(self.records) is not tuple or any(
            type(item) is not CommitmentRecord for item in self.records
        ):
            raise TypeError("records must be CommitmentRecord values")
        _validate_rebalance_transitions(self)
        _validate_history(self.records, self.allocation, self.admissions)
        for end in range(1, len(self.records) + 1):
            latest = _latest_records(self.records[:end])
            for budget in self.allocation.budgets:
                used = sum((r.actual_cash_effect + r.reserved_cash for r in latest
                            if r.strategy_id == budget.strategy_id and r.side is CommitmentSide.BUY), _ZERO)
                if used > budget.amount:
                    raise PortfolioConstructionError("commitment history budget overcommitment")
            if any(r.strategy_id not in budget_runs or budget_runs[r.strategy_id] != r.research_run_id for r in latest):
                raise PortfolioConstructionError("commitment history strategy-run mismatch")
        expected = _digest(
            "S4-13-COMMITMENT-BOOK-V1",
            {
                "allocation": self.allocation.evidence_digest,
                "removed_strategies": sorted(self.removed_strategies),
                "rebalance_transitions": [s.evidence_digest for s in self.rebalance_transitions],
                "admissions": [item.evidence_digest for item in self.admissions],
                "records": [item.record_digest for item in self.records],
            },
        )
        if self.evidence_digest and self.evidence_digest != expected:
            raise PortfolioConstructionError("commitment book digest mismatch")
        object.__setattr__(self, "evidence_digest", expected)


@dataclass(frozen=True, slots=True)
class StrategyFinancialEffects:
    strategy_id: str
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    funding_effect: Decimal
    corporate_action_effect: Decimal
    other_adjustment: Decimal
    realised_event_ids: tuple[str, ...]
    unrealised_event_ids: tuple[str, ...]
    funding_event_ids: tuple[str, ...]
    corporate_action_event_ids: tuple[str, ...]
    adjustment_event_ids: tuple[str, ...]
    complete: bool

    def __post_init__(self) -> None:
        _text(self.strategy_id, "strategy_id")
        for name in (
            "realised_pnl",
            "unrealised_pnl",
            "funding_effect",
            "corporate_action_effect",
            "other_adjustment",
        ):
            _finite(getattr(self, name), name)
        for name in (
            "realised_event_ids",
            "unrealised_event_ids",
            "funding_event_ids",
            "corporate_action_event_ids",
            "adjustment_event_ids",
        ):
            _texts(getattr(self, name), name, nonempty=False)
        if type(self.complete) is not bool:
            raise TypeError("complete must be bool")
        component_bindings = (
            (self.realised_pnl, self.realised_event_ids),
            (self.unrealised_pnl, self.unrealised_event_ids),
            (self.funding_effect, self.funding_event_ids),
            (self.corporate_action_effect, self.corporate_action_event_ids),
            (self.other_adjustment, self.adjustment_event_ids),
        )
        if self.complete and any(amount != 0 and not identities for amount, identities in component_bindings):
            raise PortfolioConstructionError("nonzero financial effect requires component provenance")

    @property
    def net_pnl(self) -> Decimal:
        return (
            self.realised_pnl
            + self.unrealised_pnl
            + self.corporate_action_effect
        )


@dataclass(frozen=True, slots=True)
class StrategyAttribution:
    strategy_id: str
    research_run_id: str
    holdings: tuple[tuple[InstrumentId, Decimal], ...]
    executed_notional: Decimal
    execution_costs: Decimal
    net_pnl: Decimal
    provenance_ids: tuple[str, ...]
    cost_basis: Decimal = _ZERO
    realised_pnl: Decimal = _ZERO
    unrealised_pnl: Decimal = _ZERO
    dividends: Decimal = _ZERO
    funding: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class PortfolioAttribution:
    outcome: AttributionOutcome
    strategies: tuple[StrategyAttribution, ...]
    disclosed_adjustments: Decimal
    portfolio_net_pnl: Decimal
    limitations: tuple[str, ...]
    evidence_digest: str
    oms_digest: str
    ledger_digest: str
    reconciliation_digest: str
    admission_digests: tuple[str, ...]
    source_effects: tuple[StrategyFinancialEffects, ...] = ()
    shared_adjustment_event_ids: tuple[str, ...] = ()
    authority: str = PORTFOLIO_AUTHORITY


@dataclass(frozen=True, slots=True)
class RebalanceSchedule:
    retained_book: CommitmentBook
    source_oms: SimulatedOms
    source_ledger: SimulationLedger
    calendar_inputs: tuple[tuple[CalendarSessionResolutionQuery, CalendarSessionResolutionCandidates], ...]
    final_session: CalendarSessionResolutionResult
    next_session: CalendarSessionResolutionResult
    decision_time: Timestamp
    release_time: Timestamp
    final_session_date: str
    next_session_date: str
    preserved_obligations: tuple[str, ...]
    removed_strategies: tuple[str, ...]
    evidence_digest: str
    authority: str = PORTFOLIO_AUTHORITY


@dataclass(frozen=True, slots=True)
class StressResult:
    scenario_id: str
    kind: StressKind
    baseline_evidence_digest: str
    metrics: tuple[tuple[str, Decimal], ...]
    input_evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence_digest: str
    authority: str = PORTFOLIO_AUTHORITY
    source_cost_model: ExecutionCostModel | None = None
    source_strategy_ids: tuple[str, ...] = ()
    source_correlation: tuple[tuple[Decimal, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class PortfolioEvidence:
    configuration_id: str
    allocation_digest: str
    admission_digests: tuple[str, ...]
    commitment_digest: str
    oms_order_ids: tuple[str, ...]
    fill_ids: tuple[str, ...]
    ledger_event_ids: tuple[str, ...]
    reconciliation_state: str
    performance: PerformanceTruth
    attribution_digest: str
    stress_digests: tuple[str, ...]
    replay_digest: str
    limitations: tuple[str, ...]
    evidence_digest: str
    authority: str = PORTFOLIO_AUTHORITY


def admit_strategy(
    *,
    strategy_output: _STRATEGY_OUTPUT,
    admission_class: AdmissionClass,
    governor: ResearchGovernor,
    journal: ResearchJournal,
    plan: FrozenResearchPlan,
    run: ResearchRun,
    qualification: DatasetQualification,
    pit_eligibility: tuple[DecisionInputEligibility, ...],
    reconciliation: ReconciliationResult,
    robustness: RobustnessReport | None,
    limitations: tuple[str, ...] = (),
) -> PortfolioAdmission:
    """Bind admitted predecessor evidence without granting economic authority."""
    if type(admission_class) is not AdmissionClass:
        raise TypeError("admission_class must be AdmissionClass")
    _texts(limitations, "limitations", nonempty=False)
    context = establish_context(
        strategy_output=strategy_output,
        governor=governor,
        journal=journal,
        plan=plan,
        run=run,
        qualification=qualification,
        pit_eligibility=pit_eligibility,
        reconciliation=reconciliation,
    )
    if bind_strategy_output(strategy_output) != context.strategy:
        raise PortfolioConstructionError("strategy/context identity mismatch")
    return PortfolioAdmission._from_assurance(context, admission_class, robustness, limitations)


def admit_assurance_context(
    *,
    context: AssuranceContext,
    admission_class: AdmissionClass,
    robustness: RobustnessReport | None,
    limitations: tuple[str, ...] = (),
) -> PortfolioAdmission:
    """Construct a hypothetical fixture only; supplied contexts are not authenticated."""
    return PortfolioAdmission._from_assurance(context, admission_class, robustness, limitations)


def allocate_equal_budgets(
    admissions: tuple[PortfolioAdmission, ...],
    *,
    total_capital: Decimal = Decimal("100000.00"),
    currency: Currency = USD,
    hypothetical: bool = False,
) -> PortfolioAllocation:
    """Allocate exact minor units; residual cents go to final canonical IDs."""
    if type(admissions) is not tuple or any(type(item) is not PortfolioAdmission for item in admissions):
        raise TypeError("admissions must be PortfolioAdmission values")
    _unique_admissions(admissions)
    if currency != USD:
        raise PortfolioConstructionError("initial S4-13 scope is USD only")
    if type(hypothetical) is not bool:
        raise TypeError("hypothetical must be bool")
    cents = _exact_cents(total_capital, "total_capital")
    eligible = sorted(
        (
            item
            for item in admissions
            if (item.admission_class is AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY
                or (hypothetical and item.admission_class is AdmissionClass.HYPOTHETICAL_PARTICIPATION))
        ),
        key=lambda item: item.strategy.strategy_id,
    )
    budgets: list[StrategyBudget] = []
    if eligible:
        quotient, remainder = divmod(cents, len(eligible))
        cutoff = len(eligible) - remainder
        budgets = [
            StrategyBudget(
                item.strategy.strategy_id,
                item.strategy.research_run_id,
                quotient + (1 if index >= cutoff else 0),
            )
            for index, item in enumerate(eligible)
        ]
    allocated = sum(item.cents for item in budgets)
    body = {
        "currency": currency.code,
        "hypothetical": hypothetical,
        "total_cents": cents,
        "budgets": [[item.strategy_id, item.research_run_id, item.cents] for item in budgets],
        "unallocated_cents": cents - allocated,
    }
    return PortfolioAllocation(
        currency,
        cents,
        tuple(budgets),
        cents - allocated,
        _digest("S4-13-ALLOCATION-V1", body),
        hypothetical=hypothetical,
    )


def create_commitment_book(
    allocation: PortfolioAllocation, admissions: tuple[PortfolioAdmission, ...]
) -> CommitmentBook:
    return CommitmentBook(allocation, tuple(sorted(admissions, key=lambda item: item.strategy.strategy_id)))


def reserve_commitments(
    book: CommitmentBook, requests: tuple[CommitmentRequest, ...]
) -> CommitmentBook:
    """Reserve new obligations in canonical order, independent of input order."""
    if type(book) is not CommitmentBook or type(requests) is not tuple:
        raise TypeError("book and request tuple required")
    if any(type(item) is not CommitmentRequest for item in requests):
        raise TypeError("requests must be CommitmentRequest values")
    _check_book(book)
    ordered = sorted(
        requests,
        key=lambda item: (
            item.order.release_at.value,
            item.strategy_id,
            item.order.order_id.value.hex,
        ),
    )
    current = book
    for request in ordered:
        current = _reserve_one(current, request)
    return current


def reconcile_commitment(
    book: CommitmentBook,
    *,
    order_id: OrderId,
    oms: SimulatedOms,
    ledger: SimulationLedger,
) -> CommitmentBook:
    """Update a reservation only from reconciled OMS and ledger truth."""
    if type(book) is not CommitmentBook or type(oms) is not SimulatedOms or type(ledger) is not SimulationLedger:
        raise TypeError("exact commitment, OMS, and ledger types required")
    _check_book(book)
    _verify_currency(oms, ledger, book.allocation.currency)
    for known in book.records:
        if known.source_ledger is not None and ledger.events[:len(known.source_ledger.events)] != known.source_ledger.events:
            raise PortfolioConstructionError("commitment ledger history is stale")
    _validate_financial_extension(book.records, oms, ledger, book.admissions)
    result = reconcile(oms, ledger)
    require_performance(result)
    prior = _latest_record(book.records, order_id)
    if prior.state is CommitmentState.RELEASED:
        raise PortfolioConstructionError("released commitment cannot transition")
    order_record = oms.order(order_id)
    if order_record is None or order_record.order != prior.original_order:
        raise PortfolioConstructionError("OMS order attribution mismatch")
    fills = tuple(fill for fill in oms.fills if fill.order_id == order_id)
    if tuple(fill.execution_at.value for fill in fills) != tuple(sorted(fill.execution_at.value for fill in fills)):
        raise PortfolioConstructionError("fill chronology is not monotone")
    for fill in fills:
        if fill.cost_model_id != prior.cost_model.model_id or fill.costs != prior.cost_model.calculate(fill.quantity, fill.execution_price):
            raise PortfolioConstructionError("fill exceeds approved price or cost model parameters")
        if (
            fill.instrument_id != prior.instrument_id
            or fill.research_run_id.value != prior.research_run_id
            or fill.currency != book.allocation.currency
            or fill.execution_at.value <= order_record.order.release_at.value
        ):
            raise PortfolioConstructionError("fill identity, currency, or chronology mismatch")
        if prior.side is CommitmentSide.BUY and (
            prior.upper_price is None
            or fill.execution_price > prior.upper_price
            or fill.cost_model_id != prior.cost_model_id
        ):
            raise PortfolioConstructionError("fill exceeds approved price or cost model")
    filled = sum((fill.quantity for fill in fills), _ZERO)
    actual = sum(
        (
            fill.quantity * fill.execution_price + fill.costs.total
            if prior.side is CommitmentSide.BUY
            else fill.quantity * fill.execution_price - fill.costs.total
            for fill in fills
        ),
        _ZERO,
    )
    remaining = prior.quantity - filled
    if remaining < 0:
        raise PortfolioConstructionError("fills exceed committed quantity")
    terminal = order_record.status in {
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.UNFILLED,
        OrderStatus.FULLY_FILLED,
    }
    if terminal:
        state = CommitmentState.RELEASED
        cash = _ZERO
        holding = _ZERO
    else:
        state = CommitmentState.PARTIALLY_FILLED if filled else CommitmentState.RESERVED
        if prior.side is CommitmentSide.BUY:
            if prior.upper_price is None or prior.cost_model_id is None:
                raise PortfolioConstructionError("buy commitment ceiling is incomplete")
            original_request = next(
                (
                    record
                    for record in reversed(book.records)
                    if record.order_id == order_id and record.upper_price is not None
                ),
                None,
            )
            if original_request is None:
                raise PortfolioConstructionError("buy commitment history is incomplete")
            if prior.unit_cash_ceiling is None:
                raise PortfolioConstructionError("buy unit cash ceiling is missing")
            cash = _ceil_cents(prior.unit_cash_ceiling * remaining)
            holding = _ZERO
        else:
            cash = _ZERO
            holding = remaining
    if prior.side is CommitmentSide.BUY:
        latest_other = tuple(
            item for item in _latest_records(book.records) if item.order_id != order_id
        )
        strategy_total = actual + cash + sum(
            (
                (item.actual_cash_effect if item.side is CommitmentSide.BUY else _ZERO) + item.reserved_cash
                for item in latest_other
                if item.strategy_id == prior.strategy_id
            ),
            _ZERO,
        )
        portfolio_total = actual + cash + sum(
            ((item.actual_cash_effect if item.side is CommitmentSide.BUY else _ZERO) + item.reserved_cash for item in latest_other), _ZERO
        )
        budget = book.allocation.budget(prior.strategy_id)
        if budget is None or strategy_total > budget.amount:
            raise PortfolioConstructionError("partial-fill strategy liability exceeds budget")
        if portfolio_total > Decimal(book.allocation.total_cents) / Decimal(100):
            raise PortfolioConstructionError("partial-fill portfolio liability exceeds cash")
    next_record = _record(
        sequence=len(book.records) + 1,
        strategy_id=prior.strategy_id,
        research_run_id=prior.research_run_id,
        order_id=prior.order_id,
        instrument_id=prior.instrument_id,
        side=prior.side,
        quantity=prior.quantity,
        filled_quantity=filled,
        reserved_cash=cash,
        reserved_holding=holding,
        actual_cash_effect=actual,
        state=state,
        upper_price=prior.upper_price,
        unit_cash_ceiling=prior.unit_cash_ceiling,
        cost_model_id=prior.cost_model_id,
        ceiling_evidence_digest=prior.ceiling_evidence_digest,
        order_status=order_record.status,
        predecessor_digest=book.records[-1].record_digest,
        original_order=prior.original_order,
        cost_model=prior.cost_model,
        reservation_request=prior.reservation_request,
        source_oms=oms,
        source_ledger=ledger,
    )
    return CommitmentBook(book.allocation, book.admissions, book.records + (next_record,), removed_strategies=book.removed_strategies, rebalance_transitions=book.rebalance_transitions)


def attribute_portfolio(
    *,
    admissions: tuple[PortfolioAdmission, ...],
    oms: SimulatedOms,
    ledger: SimulationLedger,
    reconciliation: ReconciliationResult,
    effects: tuple[StrategyFinancialEffects, ...],
    disclosed_adjustments: Decimal = _ZERO,
    shared_adjustment_event_ids: tuple[str, ...] = (),
) -> PortfolioAttribution:
    """Build a conserving analytical overlay over authoritative financial truth."""
    _unique_admissions(admissions)
    _verify_currency(oms, ledger, USD)
    performance = require_performance(reconciliation)
    if reconcile(oms, ledger) != reconciliation:
        raise PortfolioConstructionError("reconciliation is not attributable to supplied OMS/ledger")
    _finite(disclosed_adjustments, "disclosed_adjustments")
    effect_map = {item.strategy_id: item for item in effects}
    if len(effect_map) != len(effects):
        raise PortfolioConstructionError("duplicate strategy effects")
    admission_map = {item.strategy.research_run_id: item for item in admissions}
    if len(admission_map) != len(admissions):
        raise PortfolioConstructionError("research-run attribution is not unique")
    admitted_strategy_ids = {item.strategy.strategy_id for item in admissions}
    if set(effect_map) - admitted_strategy_ids:
        raise PortfolioConstructionError("unknown strategy financial effects")
    _texts(shared_adjustment_event_ids, "shared_adjustment_event_ids", nonempty=False)
    if disclosed_adjustments != 0 and not shared_adjustment_event_ids:
        raise PortfolioConstructionError("disclosed adjustment requires ledger provenance")
    ledger_events = {item.event_id.value: item for item in ledger.events}
    claimed_events: set[str] = set()

    def validate_events(
        identities: tuple[str, ...],
        run_id: str,
        kinds: set[FinancialEventKind],
    ) -> None:
        for identity in identities:
            event = ledger_events.get(identity)
            if event is None or event.research_run_id.value != run_id or event.kind not in kinds:
                raise PortfolioConstructionError("foreign or incorrectly classified financial provenance")
            if identity in claimed_events:
                raise PortfolioConstructionError("duplicate financial event attribution")
            claimed_events.add(identity)
    invalid_reasons: list[str] = []
    incomplete: list[str] = []
    grouped: list[StrategyAttribution] = []
    derived: dict[str, dict[str, Decimal]] = {
        run_id: {name: _ZERO for name in ("realised", "dividends", "funding", "adjustment")}
        for run_id in admission_map
    }
    quantities: dict[tuple[str, InstrumentId], Decimal] = {}
    bases: dict[tuple[str, InstrumentId], Decimal] = {}
    replay = SimulationLedger.empty(ledger.currency)
    for event in ledger.events:
        before = replay.state
        replay = replay.apply(event)
        run_id = event.research_run_id.value
        if run_id not in derived:
            invalid_reasons.append("UNMAPPED_LEDGER_RESEARCH_RUN")
            continue
        component = derived[run_id]
        if event.kind is FinancialEventKind.OPENING_CASH:
            component["funding"] += event.cash_delta or _ZERO
        elif event.kind is FinancialEventKind.CASH_ADJUSTMENT:
            component["adjustment"] += event.cash_delta or _ZERO
        elif event.instrument_id is not None:
            instrument = event.instrument_id
            key = (run_id, instrument)
            owners = [k for k, q in quantities.items() if k[1] == instrument and q != 0]
            if event.kind is FinancialEventKind.TRADE:
                assert event.quantity is not None and event.unit_amount is not None
                quantities[key] = quantities.get(key, _ZERO) + event.quantity
                if event.quantity > 0:
                    bases[key] = bases.get(key, _ZERO) + event.quantity * event.unit_amount
                else:
                    position = before.position(instrument)
                    assert position is not None
                    if len(owners) > 1:
                        incomplete.append("OVERLAPPING_POSITION_SALE_ATTRIBUTION_NOT_ESTABLISHED")
                    bases[key] = bases.get(key, _ZERO) + event.quantity * position.cost_basis / position.quantity
                    component["realised"] += replay.state.realised_pnl - before.realised_pnl
                if quantities[key] < 0:
                    invalid_reasons.append("STRATEGY_HOLDING_UNDERFLOW")
            elif event.kind is FinancialEventKind.SPLIT:
                assert event.quantity is not None
                for owner in owners:
                    quantities[owner] *= event.quantity
                if len(owners) > 1:
                    incomplete.append("SHARED_CORPORATE_ACTION_ATTRIBUTION_NOT_ESTABLISHED")
            elif event.kind is FinancialEventKind.CASH_DIVIDEND:
                if len(owners) != 1 or owners[0][0] != run_id:
                    incomplete.append("SHARED_CORPORATE_ACTION_ATTRIBUTION_NOT_ESTABLISHED")
                component["dividends"] += replay.state.realised_pnl - before.realised_pnl
    if replay != ledger:
        raise PortfolioConstructionError("ledger replay content mismatch")
    for admission in sorted(admissions, key=lambda item: item.strategy.strategy_id):
        strategy_id = admission.strategy.strategy_id
        run_id = admission.strategy.research_run_id
        fills = tuple(fill for fill in oms.fills if fill.research_run_id.value == run_id)
        holdings: dict[InstrumentId, Decimal] = {}
        notional = _ZERO
        costs = _ZERO
        provenance: list[str] = []
        for fill in fills:
            sign = Decimal(1) if fill.side.value == "BUY" else Decimal(-1)
            holdings[fill.instrument_id] = holdings.get(fill.instrument_id, _ZERO) + sign * fill.quantity
            notional += fill.quantity * fill.execution_price
            costs += fill.costs.total
            provenance.append(fill.fill_id.value.hex)
        holdings = {instrument: quantity for (owner, instrument), quantity in quantities.items() if owner == run_id and quantity != 0}
        component = derived[run_id]
        basis = sum((value for (owner, _), value in bases.items() if owner == run_id), _ZERO)
        unrealised = sum((quantity * position.mark_price for instrument, quantity in holdings.items()
            if (position := ledger.state.position(instrument)) is not None), _ZERO) - basis
        realised = component["realised"]
        dividends = component["dividends"]
        derived_net = realised + unrealised + dividends
        effect = effect_map.get(strategy_id)
        if effect is None:
            net_pnl = derived_net
            effect_provenance: tuple[str, ...] = ()
        else:
            net_pnl = derived_net
            if (effect.realised_pnl, effect.unrealised_pnl, effect.funding_effect,
                    effect.corporate_action_effect, effect.other_adjustment) != (
                    realised, unrealised, component["funding"], dividends, component["adjustment"]):
                invalid_reasons.append(f"FINANCIAL_COMPONENT_MISMATCH:{strategy_id}")
            validate_events(effect.realised_event_ids, run_id, {FinancialEventKind.TRADE})
            validate_events(effect.unrealised_event_ids, run_id, {FinancialEventKind.MARK})
            validate_events(effect.funding_event_ids, run_id, {FinancialEventKind.OPENING_CASH})
            validate_events(
                effect.corporate_action_event_ids,
                run_id,
                {FinancialEventKind.CASH_DIVIDEND, FinancialEventKind.SPLIT},
            )
            validate_events(effect.adjustment_event_ids, run_id, {FinancialEventKind.CASH_ADJUSTMENT})
            effect_provenance = tuple(
                sorted(
                    (
                        *effect.realised_event_ids,
                        *effect.unrealised_event_ids,
                        *effect.funding_event_ids,
                        *effect.corporate_action_event_ids,
                        *effect.adjustment_event_ids,
                    )
                )
            )
            if not effect.complete:
                incomplete.append(f"INCOMPLETE_EFFECTS:{strategy_id}")
        grouped.append(
            StrategyAttribution(
                strategy_id,
                run_id,
                tuple(sorted(holdings.items(), key=lambda item: item[0].to_string())),
                notional,
                costs,
                net_pnl,
                tuple((*provenance, *effect_provenance)),
                basis, realised, unrealised, dividends, component["funding"] + component["adjustment"],
            )
        )
    unknown_runs = {
        fill.research_run_id.value for fill in oms.fills if fill.research_run_id.value not in admission_map
    }
    if unknown_runs:
        invalid_reasons.append("UNMAPPED_FILL_RESEARCH_RUN")
    consolidated: dict[InstrumentId, Decimal] = {}
    for item in grouped:
        for instrument_id, quantity in item.holdings:
            consolidated[instrument_id] = consolidated.get(instrument_id, _ZERO) + quantity
    ledger_holdings = {
        item.instrument_id: item.quantity for item in ledger.state.positions if item.quantity != 0
    }
    if consolidated != ledger_holdings:
        invalid_reasons.append("HOLDINGS_ATTRIBUTION_MISMATCH")
    attributed_pnl = sum((item.net_pnl for item in grouped), _ZERO) + disclosed_adjustments
    for identity in shared_adjustment_event_ids:
        adjustment_event = ledger_events.get(identity)
        if adjustment_event is None or adjustment_event.kind is not FinancialEventKind.CASH_ADJUSTMENT:
            raise PortfolioConstructionError("shared adjustment provenance is invalid")
        if identity in claimed_events:
            raise PortfolioConstructionError("duplicate financial event attribution")
        claimed_events.add(identity)
    if disclosed_adjustments != 0:
        invalid_reasons.append("FUNDING_CANNOT_BALANCE_TRADING_PNL")
    if sum((item.cost_basis for item in grouped), _ZERO) != sum((p.cost_basis for p in ledger.state.positions), _ZERO):
        invalid_reasons.append("COST_BASIS_CONSERVATION_MISMATCH")
    if sum((item.execution_costs for item in grouped), _ZERO) != sum((fill.costs.total for fill in oms.fills), _ZERO):
        invalid_reasons.append("EXECUTION_COST_CONSERVATION_MISMATCH")
    if not incomplete:
        if sum((x.realised_pnl + x.dividends for x in grouped), _ZERO) != ledger.state.realised_pnl:
            invalid_reasons.append("REALISED_COMPONENT_CONSERVATION_MISMATCH")
        if sum((x.unrealised_pnl for x in grouped), _ZERO) != ledger.state.unrealised_pnl:
            invalid_reasons.append("UNREALISED_COMPONENT_CONSERVATION_MISMATCH")
        funding = sum((e.cash_delta or _ZERO for e in ledger.events
                       if e.kind in {FinancialEventKind.OPENING_CASH, FinancialEventKind.CASH_ADJUSTMENT}), _ZERO)
        if sum((x.funding for x in grouped), _ZERO) != funding:
            invalid_reasons.append("FUNDING_COMPONENT_CONSERVATION_MISMATCH")
    if not incomplete and attributed_pnl != performance.net_pnl:
        invalid_reasons.append("PNL_ATTRIBUTION_MISMATCH")
    if invalid_reasons:
        outcome = AttributionOutcome.INVALID
        limitations = tuple(sorted(set(invalid_reasons)))
    elif incomplete:
        outcome = AttributionOutcome.INCOMPLETE_ATTRIBUTION
        limitations = tuple(sorted(set(incomplete)))
    else:
        outcome = AttributionOutcome.COMPLETE
        limitations = ()
    body = {
        "outcome": outcome.value,
        "strategies": [_canonical(item) for item in grouped],
        "adjustments": str(disclosed_adjustments),
        "portfolio_net_pnl": str(performance.net_pnl),
        "limitations": list(limitations),
        "oms": _oms_digest(oms),
        "ledger": _ledger_digest(ledger),
        "reconciliation": _reconciliation_digest(reconciliation),
        "admissions": sorted(item.evidence_digest for item in admissions),
    }
    return PortfolioAttribution(
        outcome,
        tuple(grouped),
        disclosed_adjustments,
        performance.net_pnl,
        limitations,
        _digest("S4-13-ATTRIBUTION-V1", body),
        _oms_digest(oms),
        _ledger_digest(ledger),
        _reconciliation_digest(reconciliation),
        tuple(sorted(item.evidence_digest for item in admissions)),
        tuple(sorted(effects, key=lambda item: item.strategy_id)),
        shared_adjustment_event_ids,
    )


def allocate_shared_cents(
    total: Decimal, notionals: tuple[tuple[str, Decimal], ...]
) -> tuple[tuple[str, Decimal], ...]:
    """Allocate a cent-denominated shared adjustment by executed notional."""
    total_cents = _exact_cents(total, "total")
    if type(notionals) is not tuple or not notionals:
        raise PortfolioConstructionError("notional evidence required")
    if len({name for name, _ in notionals}) != len(notionals):
        raise PortfolioConstructionError("duplicate strategy notional")
    ordered = sorted(notionals)
    for name, amount in ordered:
        _text(name, "strategy_id")
        _finite(amount, "notional")
        if amount < 0:
            raise PortfolioConstructionError("notional cannot be negative")
    denominator = sum((amount for _, amount in ordered), _ZERO)
    if denominator <= 0:
        raise PortfolioConstructionError("positive executed notional required")
    raw = [Decimal(total_cents) * amount / denominator for _, amount in ordered]
    cents = [int(value) for value in raw]
    remainder = total_cents - sum(cents)
    positive_indices = [i for i, (_, amount) in enumerate(ordered) if amount > 0]
    for index in positive_indices[len(positive_indices) - remainder:] if remainder else ():
        cents[index] += 1
    return tuple((item[0], Decimal(value) / Decimal(100)) for item, value in zip(ordered, cents, strict=True))


def schedule_monthly_rebalance(
    *,
    final_session: CalendarSessionResolutionResult,
    next_session: CalendarSessionResolutionResult,
    session_sequence: tuple[CalendarSessionResolutionResult, ...],
    decision_time: Timestamp,
    preserved_obligations: tuple[str, ...],
    removed_strategies: tuple[str, ...] = (),
    calendar_inputs: tuple[tuple[CalendarSessionResolutionQuery, CalendarSessionResolutionCandidates], ...] = (),
    commitments: CommitmentBook | None = None,
    oms: SimulatedOms | None = None,
    ledger: SimulationLedger | None = None,
) -> RebalanceSchedule:
    """Bind a month-end decision to the next eligible session."""
    if not calendar_inputs:
        raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: complete calendar resolver inputs required")
    if commitments is None or oms is None or ledger is None:
        raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: current obligations and reconciled holdings required")
    if commitments != CommitmentBook(commitments.allocation, commitments.admissions, commitments.records, removed_strategies=commitments.removed_strategies, rebalance_transitions=commitments.rebalance_transitions):
        raise PortfolioConstructionError("rebalance commitment substitution")
    _verify_currency(oms, ledger, commitments.allocation.currency)
    require_performance(reconcile(oms, ledger))
    if any(event.occurred_at.value > decision_time.value for event in ledger.events):
        raise PortfolioConstructionError("rebalance ledger is after decision")
    for record in _latest_records(commitments.records):
        current = oms.order(record.order_id)
        if current is None or current.order != record.original_order or current.status != record.order_status or current.filled_quantity != record.filled_quantity:
            raise PortfolioConstructionError("rebalance obligations not current")
        if record.source_ledger is not None and ledger.events[:len(record.source_ledger.events)] != record.source_ledger.events:
            raise PortfolioConstructionError("rebalance history is stale")
    required = {r.strategy_id for r in _active_records(commitments.records)}
    if not required.issubset(preserved_obligations):
        raise PortfolioConstructionError("pending exit obligations omitted")
    if set(removed_strategies) - {a.strategy.strategy_id for a in commitments.admissions}:
        raise PortfolioConstructionError("unknown removed strategy")
    if any(v.market_code != "US" or v.venue_code != "XNYS" or v.timezone_rule_ref.timezone_id.value != "America/New_York"
           for _, candidates in calendar_inputs for v in candidates.calendar_versions):
        raise PortfolioConstructionError("configured US XNYS calendar required")
    dates = tuple(query.local_date for query, _ in calendar_inputs)
    expected_dates = tuple(final_session.local_date + timedelta(days=i)
        for i in range((next_session.local_date - final_session.local_date).days + 1))
    if dates != expected_dates or len(dates) > 35:
        raise PortfolioConstructionError("calendar coverage omits or reorders civil dates")
    resolved = tuple(resolve_calendar_session(query=query, candidates=candidates) for query, candidates in calendar_inputs)
    if any(query.knowledge_cutoff.value > decision_time.value or query.evaluation_at != decision_time
           for query, _ in calendar_inputs):
        raise PortfolioConstructionError("calendar cutoff or currentness mismatch")
    if any(item.trading_day is None or item.selected_calendar_version_id != final_session.selected_calendar_version_id
           for item in resolved):
        raise PortfolioConstructionError("calendar date is ambiguous or version mismatch")
    actual = tuple(item for item in resolved if item.utc_open is not None)
    if actual != session_sequence or actual != (final_session, next_session):
        raise PortfolioConstructionError("next session is not immediately adjacent or month-end")
    for item in resolved:
        if item.utc_open is None and (item.trading_day is None or item.trading_day.day_kind is TradingDayKind.REGULAR_TRADING_DAY):
            raise PortfolioConstructionError("explicit non-trading evidence required")
    for value in (final_session, next_session):
        if type(value) is not CalendarSessionResolutionResult:
            raise TypeError("accepted calendar resolution results required")
        if (
            value.disposition.value != "ESTABLISHED"
            or value.trading_day is None
            or value.trading_day.day_kind is not TradingDayKind.REGULAR_TRADING_DAY
            or value.utc_close is None
            or value.utc_open is None
        ):
            raise PortfolioConstructionError("eligible established trading session required")
    if type(decision_time) is not Timestamp:
        raise TypeError("decision_time must be Timestamp")
    final_close = final_session.utc_close
    next_open = next_session.utc_open
    if final_close is None or next_open is None:
        raise PortfolioConstructionError("eligible session timestamps required")
    if type(session_sequence) is not tuple or len(session_sequence) < 2:
        raise PortfolioConstructionError("authoritative calendar sequence required")
    eligible = tuple(
        item
        for item in session_sequence
        if type(item) is CalendarSessionResolutionResult
        and item.disposition.value == "ESTABLISHED"
        and item.trading_day is not None
        and item.trading_day.day_kind is TradingDayKind.REGULAR_TRADING_DAY
    )
    if eligible != session_sequence or final_session not in eligible or next_session not in eligible:
        raise PortfolioConstructionError("calendar sequence contains missing or ambiguous sessions")
    final_index = eligible.index(final_session)
    if final_index + 1 >= len(eligible) or eligible[final_index + 1] != next_session:
        raise PortfolioConstructionError("next session is not immediately adjacent")
    if any(
        item.calendar_id != final_session.calendar_id
        or item.selected_calendar_version_id != final_session.selected_calendar_version_id
        for item in eligible
    ):
        raise PortfolioConstructionError("calendar identity or version mismatch")
    if any(
        item.local_date.month == final_session.local_date.month
        and item.local_date > final_session.local_date
        for item in eligible
    ):
        raise PortfolioConstructionError("final session is not the final eligible monthly session")
    if final_session.local_date.month == next_session.local_date.month:
        raise PortfolioConstructionError("next session must follow the month-end boundary")
    if decision_time.value < final_close.value:
        raise PortfolioConstructionError("rebalance decision precedes final completed session")
    if next_session.local_date <= final_session.local_date or next_open.value <= decision_time.value:
        raise PortfolioConstructionError("next-session release must be after decision")
    _texts(preserved_obligations, "preserved_obligations", nonempty=False)
    _texts(removed_strategies, "removed_strategies", nonempty=False)
    if set(removed_strategies) - set(preserved_obligations):
        raise PortfolioConstructionError("removed strategy obligations must remain preserved")
    body = {
        "calendar_inputs": calendar_inputs,
        "resolved": resolved,
        "commitments": commitments,
        "oms": _oms_digest(oms),
        "ledger": _ledger_digest(ledger),
        "decision": decision_time.value.isoformat(),
        "release": next_open.value.isoformat(),
        "final": final_session.local_date.isoformat(),
        "next": next_session.local_date.isoformat(),
        "obligations": list(preserved_obligations),
        "removed": list(removed_strategies),
    }
    return RebalanceSchedule(
        commitments, oms, ledger, calendar_inputs, final_session, next_session,
        decision_time,
        next_open,
        final_session.local_date.isoformat(),
        next_session.local_date.isoformat(),
        preserved_obligations,
        removed_strategies,
        _digest("S4-13-REBALANCE-V1", body),
    )


def carry_forward_rebalance(schedule: RebalanceSchedule) -> CommitmentBook:
    """Remove future participation without releasing cash, holdings or pending exits."""
    rebuilt = schedule_monthly_rebalance(final_session=schedule.final_session, next_session=schedule.next_session,
        session_sequence=(schedule.final_session, schedule.next_session), decision_time=schedule.decision_time,
        preserved_obligations=schedule.preserved_obligations, removed_strategies=schedule.removed_strategies,
        calendar_inputs=schedule.calendar_inputs, commitments=schedule.retained_book, oms=schedule.source_oms,
        ledger=schedule.source_ledger)
    if rebuilt != schedule:
        raise PortfolioConstructionError("rebalance schedule substitution")
    book = schedule.retained_book
    removed = tuple(sorted(set((*book.removed_strategies, *schedule.removed_strategies))))
    return CommitmentBook(book.allocation, book.admissions, book.records, removed_strategies=removed,
                          rebalance_transitions=book.rebalance_transitions + (schedule,))


def execution_cost_stress(
    *,
    scenario_id: str,
    baseline_evidence_digest: str,
    gross_pnl: Decimal,
    stressed_costs: tuple[Decimal, ...],
    oms: SimulatedOms | None = None,
    ledger: SimulationLedger | None = None,
    cost_model: ExecutionCostModel | None = None,
) -> StressResult:
    _text(scenario_id, "scenario_id")
    _text(baseline_evidence_digest, "baseline_evidence_digest")
    _finite(gross_pnl, "gross_pnl")
    if type(stressed_costs) is not tuple or not stressed_costs:
        raise PortfolioConstructionError("stressed cost components required")
    for value in stressed_costs:
        _finite(value, "stressed_cost")
        if value < 0:
            raise PortfolioConstructionError("stressed costs cannot be negative")
    if type(oms) is not SimulatedOms or type(ledger) is not SimulationLedger or type(cost_model) is not ExecutionCostModel:
        raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: reconciled execution sources and changed-cost model required")
    _verify_currency(oms, ledger, USD)
    performance = require_performance(reconcile(oms, ledger))
    calculated = tuple(cost_model.calculate(fill.quantity, fill.execution_price).total for fill in oms.fills)
    if gross_pnl != performance.gross_pnl or stressed_costs != calculated:
        raise PortfolioConstructionError("fabricated stress amounts or changed-cost replay mismatch")
    total = sum(calculated, _ZERO)
    metrics = (("gross_pnl", gross_pnl), ("cost_drag", total), ("net_pnl", gross_pnl - total))
    return replace(_stress_result(scenario_id, StressKind.EXECUTION_COST, baseline_evidence_digest, metrics, ("FIXED_FILL_COST_SENSITIVITY_NOT_A_RETURN_PATH",), (_oms_digest(oms), _ledger_digest(ledger), _digest("S4-13-COST-MODEL", cost_model))), source_cost_model=cost_model)


def correlation_stress(
    *,
    scenario_id: str,
    baseline_evidence_digest: str,
    strategy_ids: tuple[str, ...],
    correlation: tuple[tuple[Decimal, ...], ...],
) -> StressResult:
    _texts(strategy_ids, "strategy_ids", nonempty=True)
    if len(correlation) != len(strategy_ids) or len(set(strategy_ids)) != len(strategy_ids) or not _positive_semidefinite(correlation):
        raise PortfolioConstructionError("valid positive-semidefinite correlation required")
    order = sorted(range(len(strategy_ids)), key=lambda index: strategy_ids[index])
    correlation = tuple(tuple(correlation[i][j] for j in order) for i in order)
    strategy_ids = tuple(strategy_ids[i] for i in order)
    metrics = tuple(
        (f"correlation:{strategy_ids[row]}:{strategy_ids[col]}", correlation[row][col])
        for row in range(len(strategy_ids))
        for col in range(row, len(strategy_ids))
    )
    return replace(_stress_result(
        scenario_id,
        StressKind.CORRELATION,
        baseline_evidence_digest,
        metrics,
        ("CORRELATION_ASSUMPTIONS_DO_NOT_CREATE_RETURN_PATHS",),
    ), source_strategy_ids=strategy_ids, source_correlation=correlation)


def record_stress(
    *,
    scenario_id: str,
    kind: StressKind,
    baseline_evidence_digest: str,
    metrics: tuple[tuple[str, Decimal], ...],
    evidence_ids: tuple[str, ...],
    limitations: tuple[str, ...] = (),
) -> StressResult:
    if kind in {StressKind.EXECUTION_COST, StressKind.CORRELATION}:
        raise PortfolioConstructionError("use the validated specialized stress constructor")
    _texts(evidence_ids, "evidence_ids", nonempty=True)
    raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: unsupported stress calculation; evidence IDs do not qualify metrics")


def build_portfolio_evidence(
    *,
    configuration_id: str,
    allocation: PortfolioAllocation,
    commitments: CommitmentBook,
    oms: SimulatedOms,
    ledger: SimulationLedger,
    reconciliation: ReconciliationResult,
    attribution: PortfolioAttribution,
    stress_results: tuple[StressResult, ...],
    limitations: tuple[str, ...] = (),
) -> PortfolioEvidence:
    """Create the deterministic S4-14/S4-15 evidence handoff."""
    _text(configuration_id, "configuration_id")
    if ledger.events and current_reservation_issues(commitments, oms, ledger, ledger.events[-1].occurred_at):
        raise PortfolioConstructionError("INFEASIBLE_RESERVATION")
    if any(item.authority != PORTFOLIO_AUTHORITY for item in commitments.admissions):
        raise PortfolioConstructionError("admission authority exceeds research-only scope")
    if commitments != CommitmentBook(commitments.allocation, commitments.admissions, commitments.records, removed_strategies=commitments.removed_strategies, rebalance_transitions=commitments.rebalance_transitions):
        raise PortfolioConstructionError("commitment content substitution")
    if commitments.allocation != allocation:
        raise PortfolioConstructionError("allocation and commitment evidence conflict")
    _verify_currency(oms, ledger, allocation.currency)
    performance = require_performance(reconciliation)
    if reconcile(oms, ledger) != reconciliation:
        raise PortfolioConstructionError("evidence reconciliation substitution")
    admission_digests = tuple(sorted(item.evidence_digest for item in commitments.admissions))
    if (
        attribution.oms_digest != _oms_digest(oms)
        or attribution.ledger_digest != _ledger_digest(ledger)
        or attribution.reconciliation_digest != _reconciliation_digest(reconciliation)
        or attribution.admission_digests != admission_digests
    ):
        raise PortfolioConstructionError("foreign attribution evidence")
    rebuilt_attribution = attribute_portfolio(
        admissions=commitments.admissions, oms=oms, ledger=ledger, reconciliation=reconciliation,
        effects=attribution.source_effects, disclosed_adjustments=attribution.disclosed_adjustments,
        shared_adjustment_event_ids=attribution.shared_adjustment_event_ids,
    )
    if rebuilt_attribution != attribution:
        raise PortfolioConstructionError("foreign attribution content or digest")
    if allocation.hypothetical:
        limitations = tuple(sorted(set((*limitations, "HYPOTHETICAL_PARTICIPATION", "RESEARCH_ELIGIBILITY_NOT_ESTABLISHED"))))
    for record in commitments.records:
        if record.source_ledger is not None and ledger.events[:len(record.source_ledger.events)] != record.source_ledger.events:
            raise PortfolioConstructionError("final evidence omits commitment financial history")
    for record in _latest_records(commitments.records):
        order_record = oms.order(record.order_id)
        if order_record is None or order_record.order != record.original_order:
            raise PortfolioConstructionError("original order content substitution")
        if order_record.status != record.order_status or order_record.filled_quantity != record.filled_quantity:
            raise PortfolioConstructionError("commitment is not reconciled to current OMS")
    committed_order_ids = {item.order_id for item in _latest_records(commitments.records)}
    oms_order_ids = {item.order.order_id for item in oms.orders}
    if committed_order_ids != oms_order_ids:
        raise PortfolioConstructionError("OMS order set does not match commitments")
    if attribution.outcome is AttributionOutcome.INVALID:
        raise PortfolioConstructionError("invalid financial attribution cannot qualify final evidence")
    if attribution.outcome is not AttributionOutcome.COMPLETE:
        limitations = tuple(sorted(set((*limitations, *attribution.limitations))))
    _texts(limitations, "limitations", nonempty=False)
    if type(stress_results) is not tuple or any(type(item) is not StressResult for item in stress_results):
        raise TypeError("stress_results must be StressResult values")
    if len({item.scenario_id for item in stress_results}) != len(stress_results):
        raise PortfolioConstructionError("duplicate stress scenario identity")
    if any(item.baseline_evidence_digest != commitments.evidence_digest for item in stress_results):
        raise PortfolioConstructionError("foreign stress baseline evidence")
    for stress in stress_results:
        if stress.kind is StressKind.EXECUTION_COST and stress.source_cost_model is not None:
            rebuilt = execution_cost_stress(scenario_id=stress.scenario_id,
                baseline_evidence_digest=commitments.evidence_digest, gross_pnl=performance.gross_pnl,
                stressed_costs=tuple(stress.source_cost_model.calculate(f.quantity, f.execution_price).total for f in oms.fills),
                oms=oms, ledger=ledger, cost_model=stress.source_cost_model)
        elif stress.kind is StressKind.CORRELATION:
            if set(stress.source_strategy_ids) != {b.strategy_id for b in allocation.budgets}:
                raise PortfolioConstructionError("foreign stress strategy membership")
            rebuilt = correlation_stress(scenario_id=stress.scenario_id,
                baseline_evidence_digest=commitments.evidence_digest,
                strategy_ids=stress.source_strategy_ids, correlation=stress.source_correlation)
        else:
            raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: unsupported stress inputs")
        if rebuilt != stress:
            raise PortfolioConstructionError("stress content or digest substitution")
        if stress.kind is StressKind.EXECUTION_COST and (
            _oms_digest(oms) not in stress.input_evidence_ids or _ledger_digest(ledger) not in stress.input_evidence_ids
        ):
            raise PortfolioConstructionError("foreign stress input sources")
        limitations = tuple(sorted(set((*limitations, *stress.limitations))))
    stress_results = tuple(sorted(stress_results, key=lambda item: item.scenario_id))
    order_ids = tuple(item.order.order_id.value.hex for item in oms.orders)
    fill_ids = tuple(item.fill_id.value.hex for item in oms.fills)
    event_ids = tuple(item.event_id.value for item in ledger.events)
    replay_body = {
        "allocation": allocation.evidence_digest,
        "commitments": commitments.evidence_digest,
        "orders": list(order_ids),
        "fills": list(fill_ids),
        "events": list(event_ids),
        "attribution": attribution.evidence_digest,
        "stress": [item.evidence_digest for item in stress_results],
    }
    replay_digest = _digest("S4-13-REPLAY-V1", replay_body)
    body = {
        "configuration": configuration_id,
        **replay_body,
        "reconciliation": reconciliation.state.value,
        "performance": _canonical(performance),
        "limitations": list(limitations),
        "authority": PORTFOLIO_AUTHORITY,
        "replay": replay_digest,
    }
    return PortfolioEvidence(
        configuration_id,
        allocation.evidence_digest,
        tuple(item.evidence_digest for item in commitments.admissions),
        commitments.evidence_digest,
        order_ids,
        fill_ids,
        event_ids,
        reconciliation.state.value,
        performance,
        attribution.evidence_digest,
        tuple(item.evidence_digest for item in stress_results),
        replay_digest,
        limitations,
        _digest("S4-13-PORTFOLIO-EVIDENCE-V1", body),
    )


def _validate_scheduled_request(
    request: CommitmentRequest,
    allocation: PortfolioAllocation,
    admissions: tuple[PortfolioAdmission, ...],
    records: tuple[CommitmentRecord, ...],
) -> None:
    if request.schedule is not None:
        schedule = request.schedule
        rebuilt = schedule_monthly_rebalance(final_session=schedule.final_session, next_session=schedule.next_session,
            session_sequence=(schedule.final_session, schedule.next_session), decision_time=schedule.decision_time,
            preserved_obligations=schedule.preserved_obligations, removed_strategies=schedule.removed_strategies,
            calendar_inputs=schedule.calendar_inputs, commitments=schedule.retained_book, oms=schedule.source_oms,
            ledger=schedule.source_ledger)
        if rebuilt != schedule or allocation != schedule.retained_book.allocation or admissions != schedule.retained_book.admissions or records[:len(schedule.retained_book.records)] != schedule.retained_book.records:
            raise PortfolioConstructionError("schedule or pending obligations substituted")
        if request.order.decision_time != schedule.decision_time or request.order.release_at != schedule.release_time:
            raise PortfolioConstructionError("order release does not match calendar schedule")
        if request.strategy_id in schedule.removed_strategies:
            raise PortfolioConstructionError("removed strategy cannot add obligations")


def _reserve_one(book: CommitmentBook, request: CommitmentRequest) -> CommitmentBook:
    _text(request.strategy_id, "strategy_id")
    if request.strategy_id in book.removed_strategies:
        raise PortfolioConstructionError("removed strategy cannot add obligations")
    _validate_scheduled_request(request, book.allocation, book.admissions, book.records)
    if type(request.order) is not SimulatedOrder:
        raise TypeError("request order must be SimulatedOrder")
    admission = next(
        (item for item in book.admissions if item.strategy.strategy_id == request.strategy_id), None
    )
    budget = book.allocation.budget(request.strategy_id)
    if (
        admission is None
        or budget is None
        or (admission.admission_class is not AdmissionClass.EVIDENCE_SUPPORTED_RESEARCH_ELIGIBILITY
            and not book.allocation.hypothetical)
    ):
        raise PortfolioConstructionError("strategy is not allocated and evidence-supported")
    if request.order.research_run_id.value != admission.strategy.research_run_id:
        raise PortfolioConstructionError("order research-run attribution mismatch")
    if request.order.release_at.value <= request.order.decision_time.value:
        raise PortfolioConstructionError("same-close or retrospective order release")
    if any(record.order_id == request.order.order_id for record in book.records):
        raise PortfolioConstructionError("duplicate commitment order identity")
    if request.order.currency != book.allocation.currency:
        raise PortfolioConstructionError("commitment currency mismatch")
    active = _active_records(book.records)
    latest = _latest_records(book.records)
    if request.order.side.value == "BUY":
        ceiling = request.price_cost_ceiling
        if ceiling is None or not ceiling.current:
            raise PortfolioConstructionError("current qualified buy ceiling required")
        if ceiling.currency != request.order.currency or ceiling.available_at.value > request.order.decision_time.value:
            raise PortfolioConstructionError("buy ceiling timing or currency mismatch")
        approved_model = ceiling.cost_model
        costs = ceiling.cost_model.calculate(request.order.quantity, ceiling.upper_price)
        raw_maximum = request.order.quantity * ceiling.upper_price + costs.total
        maximum = _ceil_cents(raw_maximum)
        unit_cash_ceiling = raw_maximum / request.order.quantity
        strategy_used = sum(
            (
                item.reserved_cash + (item.actual_cash_effect if item.side is CommitmentSide.BUY else _ZERO)
                for item in latest
                if item.strategy_id == request.strategy_id
            ),
            _ZERO,
        )
        portfolio_used = sum(
            (item.reserved_cash + (item.actual_cash_effect if item.side is CommitmentSide.BUY else _ZERO) for item in latest), _ZERO
        )
        if strategy_used + maximum > budget.amount:
            raise PortfolioConstructionError("strategy budget overcommitment")
        if portfolio_used + maximum > Decimal(book.allocation.total_cents) / Decimal(100):
            raise PortfolioConstructionError("portfolio cash overcommitment")
        side = CommitmentSide.BUY
        cash = maximum
        holding = _ZERO
        upper = ceiling.upper_price
        model = ceiling.cost_model.model_id
        evidence = ceiling.evidence_digest.value
    elif request.order.side.value == "SELL":
        if type(request.holdings_ledger) is not SimulationLedger or type(
            request.holdings_as_of
        ) is not Timestamp:
            raise PortfolioConstructionError("sell requires authoritative holding ledger and as-of time")
        if request.holdings_oms is not None:
            _validate_financial_extension(book.records, request.holdings_oms, request.holdings_ledger, book.admissions, request.holdings_as_of)
        if request.holdings_as_of.value != request.order.decision_time.value:
            raise PortfolioConstructionError("sell holding evidence is stale")
        if type(request.execution_cost_model) is not ExecutionCostModel:
            raise PortfolioConstructionError("sell requires approved execution cost model")
        approved_model = request.execution_cost_model
        if request.holdings_ledger.currency != book.allocation.currency:
            raise PortfolioConstructionError("holding currency mismatch")
        if type(request.holdings_oms) is not SimulatedOms or type(request.holdings_reconciliation) is not ReconciliationResult:
            raise PortfolioConstructionError("sell requires reconciled OMS holding evidence")
        if reconcile(request.holdings_oms, request.holdings_ledger) != request.holdings_reconciliation:
            raise PortfolioConstructionError("holding reconciliation substitution")
        require_performance(request.holdings_reconciliation)
        if SimulationLedger.replay(request.holdings_ledger.currency, request.holdings_ledger.events) != request.holdings_ledger:
            raise PortfolioConstructionError("holding ledger replay mismatch")
        if any(event.occurred_at.value > request.holdings_as_of.value for event in request.holdings_ledger.events):
            raise PortfolioConstructionError("holding evidence is after decision cutoff")
        times = tuple(event.occurred_at.value for event in request.holdings_ledger.events)
        if times != tuple(sorted(times)):
            raise PortfolioConstructionError("holding event chronology mismatch")
        for known in book.records:
            if known.source_ledger is not None and request.holdings_ledger.events[:len(known.source_ledger.events)] != known.source_ledger.events:
                raise PortfolioConstructionError("stale holding ledger omits known financial history")
        if any(event.research_run_id.value not in {a.strategy.research_run_id for a in book.admissions}
               for event in request.holdings_ledger.events):
            raise PortfolioConstructionError("foreign holding research run")
        attributable_holding = _holding_at(
            request.holdings_ledger,
            request.order.research_run_id.value,
            request.order.instrument_id,
            request.holdings_as_of,
        )
        already = sum(
            (
                item.reserved_holding
                for item in active
                if item.strategy_id == request.strategy_id
                and item.instrument_id == request.order.instrument_id
                and item.side is CommitmentSide.SELL
            ),
            _ZERO,
        )
        if request.order.quantity + already > attributable_holding:
            raise PortfolioConstructionError("unsupported short sale or sell overcommitment")
        side = CommitmentSide.SELL
        cash = _ZERO
        holding = request.order.quantity
        upper = None
        model = None
        evidence = None
        unit_cash_ceiling = None
    else:
        raise PortfolioConstructionError("unsupported order side")
    predecessor = book.records[-1].record_digest if book.records else None
    record = _record(
        sequence=len(book.records) + 1,
        strategy_id=request.strategy_id,
        research_run_id=request.order.research_run_id.value,
        order_id=request.order.order_id,
        instrument_id=request.order.instrument_id,
        side=side,
        quantity=request.order.quantity,
        filled_quantity=_ZERO,
        reserved_cash=cash,
        reserved_holding=holding,
        actual_cash_effect=_ZERO,
        state=CommitmentState.RESERVED,
        upper_price=upper,
        unit_cash_ceiling=unit_cash_ceiling,
        cost_model_id=model,
        ceiling_evidence_digest=evidence,
        order_status=OrderStatus.ACCEPTED,
        predecessor_digest=predecessor,
        original_order=request.order,
        cost_model=approved_model,
        reservation_request=request,
        source_oms=request.holdings_oms,
        source_ledger=request.holdings_ledger,
    )
    return CommitmentBook(book.allocation, book.admissions, book.records + (record,), removed_strategies=book.removed_strategies, rebalance_transitions=book.rebalance_transitions)


def _holding_at(
    ledger: SimulationLedger,
    research_run_id: str,
    instrument_id: InstrumentId,
    as_of: Timestamp,
) -> Decimal:
    holding = _ZERO
    for event in ledger.events:
        if event.occurred_at.value > as_of.value:
            continue
        if event.instrument_id != instrument_id:
            continue
        if event.kind is FinancialEventKind.TRADE and event.research_run_id.value == research_run_id:
            if event.quantity is None:
                raise PortfolioConstructionError("trade holding event lacks quantity")
            holding += event.quantity
        elif event.kind is FinancialEventKind.SPLIT:
            if event.quantity is None or event.quantity <= 0:
                raise PortfolioConstructionError("split holding event lacks valid factor")
            holding *= event.quantity
    if holding < 0:
        raise PortfolioConstructionError("authoritative ledger contains unsupported short holding")
    return holding


def _oms_digest(oms: SimulatedOms) -> str:
    return _digest(
        "S4-13-OMS-SOURCE-V1",
        {"orders": oms.orders, "fills": oms.fills, "attempts": oms.attempts, "authority": oms.authority},
    )


def _ledger_digest(ledger: SimulationLedger) -> str:
    return _digest(
        "S4-13-LEDGER-SOURCE-V1",
        {"events": ledger.events, "state": ledger.state, "authority": ledger.authority},
    )


def _reconciliation_digest(reconciliation: ReconciliationResult) -> str:
    return _digest("S4-13-RECONCILIATION-SOURCE-V1", reconciliation)


def _record(**values: object) -> CommitmentRecord:
    values.setdefault("reservation_request", None)
    values.setdefault("source_oms", None)
    values.setdefault("source_ledger", None)
    body = _record_body(values)
    return CommitmentRecord(**values, record_digest=_digest("S4-13-COMMITMENT-RECORD-V1", body))  # type: ignore[arg-type]


def _verify_currency(oms: SimulatedOms, ledger: SimulationLedger, currency: Currency) -> None:
    """No conversion: currency-less performance inherits its verified source currency."""
    if currency != USD or ledger.currency != currency or ledger.state.currency != currency:
        raise PortfolioConstructionError("portfolio/ledger currency mismatch; conversion unsupported")
    if any(record.order.currency != currency for record in oms.orders) or any(fill.currency != currency for fill in oms.fills):
        raise PortfolioConstructionError("portfolio/OMS currency mismatch; conversion unsupported")
    if any(event.currency is not None and event.currency != currency for event in ledger.events):
        raise PortfolioConstructionError("portfolio/financial-event currency mismatch")


def _validate_rebalance_transitions(book: CommitmentBook) -> None:
    """Derive removal from retained, ordered simulation transitions, never metadata alone."""
    if type(book.rebalance_transitions) is not tuple or any(type(s) is not RebalanceSchedule for s in book.rebalance_transitions):
        raise PortfolioConstructionError("exact rebalance transition history required")
    removed: set[str] = set()
    previous_time: Timestamp | None = None
    for index, schedule in enumerate(book.rebalance_transitions):
        prior = schedule.retained_book
        if prior.rebalance_transitions != book.rebalance_transitions[:index] or set(prior.removed_strategies) != removed:
            raise PortfolioConstructionError("rebalance transition history is discontinuous")
        if prior.allocation != book.allocation or prior.admissions != book.admissions or book.records[:len(prior.records)] != prior.records:
            raise PortfolioConstructionError("rebalance transition replaces retained obligations")
        if previous_time is not None and schedule.decision_time.value <= previous_time.value:
            raise PortfolioConstructionError("rebalance effective time must increase")
        rebuilt = schedule_monthly_rebalance(final_session=schedule.final_session, next_session=schedule.next_session,
            session_sequence=(schedule.final_session, schedule.next_session), decision_time=schedule.decision_time,
            preserved_obligations=schedule.preserved_obligations, removed_strategies=schedule.removed_strategies,
            calendar_inputs=schedule.calendar_inputs, commitments=prior, oms=schedule.source_oms, ledger=schedule.source_ledger)
        if rebuilt != schedule:
            raise PortfolioConstructionError("rebalance transition content substitution")
        removed.update(schedule.removed_strategies)
        known_orders = {r.order_id for r in prior.records}
        for record in book.records[len(prior.records):]:
            if record.order_id not in known_orders and record.strategy_id in removed:
                raise PortfolioConstructionError("removed strategy cannot add reconstructed obligations")
            known_orders.add(record.order_id)
        previous_time = schedule.decision_time
    if tuple(sorted(removed)) != book.removed_strategies:
        raise PortfolioConstructionError("removed strategy state does not match retained rebalance transitions")


def _check_book(book: CommitmentBook) -> None:
    if book != CommitmentBook(book.allocation, book.admissions, book.records, removed_strategies=book.removed_strategies, rebalance_transitions=book.rebalance_transitions):
        raise PortfolioConstructionError("commitment book content substitution")


def _latest_record(records: tuple[CommitmentRecord, ...], order_id: OrderId) -> CommitmentRecord:
    value = next((item for item in reversed(records) if item.order_id == order_id), None)
    if value is None:
        raise PortfolioConstructionError("unknown commitment order")
    return value


def _active_records(records: tuple[CommitmentRecord, ...]) -> tuple[CommitmentRecord, ...]:
    return tuple(
        item for item in _latest_records(records) if item.state is not CommitmentState.RELEASED
    )


def _latest_records(records: tuple[CommitmentRecord, ...]) -> tuple[CommitmentRecord, ...]:
    latest: dict[OrderId, CommitmentRecord] = {}
    for item in records:
        latest[item.order_id] = item
    return tuple(latest.values())


def _validate_history(
    records: tuple[CommitmentRecord, ...],
    allocation: PortfolioAllocation,
    admissions: tuple[PortfolioAdmission, ...],
) -> None:
    known: dict[OrderId, CommitmentRecord] = {}
    previous: str | None = None
    for sequence, item in enumerate(records, start=1):
        if item.sequence != sequence or item.predecessor_digest != previous:
            raise PortfolioConstructionError("commitment history is reordered or discontinuous")
        expected = _digest(
            "S4-13-COMMITMENT-RECORD-V1",
            _record_body(
                {
                    key: getattr(item, key)
                    for key in item.__dataclass_fields__
                    if key != "record_digest"
                }
            ),
        )
        if item.record_digest != expected:
            raise PortfolioConstructionError("commitment record digest mismatch")
        order = item.original_order
        request = item.reservation_request
        if request is None or request.order != order or request.strategy_id != item.strategy_id:
            raise PortfolioConstructionError("commitment reservation source missing or substituted")
        if item.side is CommitmentSide.BUY:
            ceiling = request.price_cost_ceiling
            if ceiling is None or not ceiling.current or ceiling.currency != order.currency or ceiling.available_at.value > order.decision_time.value or ceiling.cost_model != item.cost_model or ceiling.upper_price != item.upper_price or ceiling.evidence_digest.value != item.ceiling_evidence_digest:
                raise PortfolioConstructionError("commitment ceiling source substitution")
        elif request.execution_cost_model != item.cost_model:
            raise PortfolioConstructionError("commitment sell cost source substitution")
        if (item.order_id, item.instrument_id, item.research_run_id, item.quantity, item.side.value) != (
            order.order_id, order.instrument_id, order.research_run_id.value, order.quantity, order.side.value
        ):
            raise PortfolioConstructionError("commitment original order identity mismatch")
        if not _ZERO <= item.filled_quantity <= item.quantity:
            raise PortfolioConstructionError("commitment quantity out of bounds")
        remaining = item.quantity - item.filled_quantity
        terminal = item.order_status in {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.UNFILLED, OrderStatus.FULLY_FILLED}
        expected_state = (CommitmentState.RELEASED if terminal else
                          CommitmentState.PARTIALLY_FILLED if item.filled_quantity else CommitmentState.RESERVED)
        if item.side is CommitmentSide.BUY:
            if item.upper_price is None or item.cost_model_id != item.cost_model.model_id:
                raise PortfolioConstructionError("commitment cost model binding mismatch")
            unit = (item.quantity * item.upper_price + item.cost_model.calculate(item.quantity, item.upper_price).total) / item.quantity
            if item.unit_cash_ceiling != unit:
                raise PortfolioConstructionError("commitment unit liability mismatch")
            cash, holding = (_ZERO if terminal else _ceil_cents(unit * remaining)), _ZERO
        else:
            cash, holding = _ZERO, (_ZERO if terminal else remaining)
        if (item.state, item.reserved_cash, item.reserved_holding) != (expected_state, cash, holding):
            raise PortfolioConstructionError("commitment reservation liability mismatch")
        prior = known.get(item.order_id)
        if order.currency != allocation.currency or order.release_at.value <= order.decision_time.value:
            raise PortfolioConstructionError("commitment currency or release chronology mismatch")
        if prior is None:
            _validate_scheduled_request(request, allocation, admissions, records[:sequence - 1])
            if item.side is CommitmentSide.SELL:
                ledger, oms = request.holdings_ledger, request.holdings_oms
                if ledger is None or oms is None or request.holdings_as_of != order.decision_time or request.holdings_reconciliation != reconcile(oms, ledger):
                    raise PortfolioConstructionError("sell reservation holding source missing or inconsistent")
                _validate_financial_extension(records[:sequence - 1], oms, ledger, admissions, order.decision_time)
                _verify_currency(oms, ledger, allocation.currency)
                require_performance(request.holdings_reconciliation)
                if ledger.currency != order.currency or SimulationLedger.replay(ledger.currency, ledger.events) != ledger:
                    raise PortfolioConstructionError("sell reservation ledger mismatch")
                already = sum((r.reserved_holding for r in known.values() if r.strategy_id == item.strategy_id and r.instrument_id == item.instrument_id and r.side is CommitmentSide.SELL), _ZERO)
                if item.quantity + already > _holding_at(ledger, item.research_run_id, item.instrument_id, order.decision_time):
                    raise PortfolioConstructionError("sell reservation liability exceeds holdings")
            if item.filled_quantity != 0 or item.actual_cash_effect != 0 or item.order_status is not OrderStatus.ACCEPTED or terminal:
                raise PortfolioConstructionError("invalid initial commitment")
        else:
            if item.source_oms is None or item.source_ledger is None:
                raise PortfolioConstructionError("commitment transition sources missing")
            _validate_financial_extension(records[:sequence - 1], item.source_oms, item.source_ledger, admissions)
            _verify_currency(item.source_oms, item.source_ledger, allocation.currency)
            require_performance(reconcile(item.source_oms, item.source_ledger))
            current = item.source_oms.order(item.order_id)
            if current is None or current.order != order or current.status != item.order_status or current.filled_quantity != item.filled_quantity:
                raise PortfolioConstructionError("commitment transition OMS mismatch")
            fills = tuple(f for f in item.source_oms.fills if f.order_id == item.order_id)
            actual = sum((f.quantity * f.execution_price + f.costs.total if item.side is CommitmentSide.BUY
                          else f.quantity * f.execution_price - f.costs.total for f in fills), _ZERO)
            if actual != item.actual_cash_effect or any(f.cost_model_id != item.cost_model.model_id or
                f.costs != item.cost_model.calculate(f.quantity, f.execution_price) or
                (item.side is CommitmentSide.BUY and (item.upper_price is None or f.execution_price > item.upper_price)) for f in fills):
                raise PortfolioConstructionError("commitment fill costs or cash mismatch")
            if prior.source_ledger is not None and item.source_ledger.events[:len(prior.source_ledger.events)] != prior.source_ledger.events:
                raise PortfolioConstructionError("commitment ledger history truncated or replaced")
        if prior is not None:
            if prior.state is CommitmentState.RELEASED or item.original_order != prior.original_order or item.cost_model != prior.cost_model or item.reservation_request != prior.reservation_request:
                raise PortfolioConstructionError("conflicting commitment transition")
            if (item.strategy_id, item.research_run_id, item.quantity, item.upper_price, item.unit_cash_ceiling) != (prior.strategy_id, prior.research_run_id, prior.quantity, prior.upper_price, prior.unit_cash_ceiling):
                raise PortfolioConstructionError("immutable commitment terms changed")
            if item.filled_quantity < prior.filled_quantity:
                raise PortfolioConstructionError("filled quantity cannot decrease")
        known[item.order_id] = item
        previous = item.record_digest


def _record_body(values: Mapping[str, object]) -> dict[str, object]:
    return {key: _canonical(value) for key, value in values.items()}


def _stress_result(
    scenario_id: str,
    kind: StressKind,
    baseline: str,
    metrics: tuple[tuple[str, Decimal], ...],
    limitations: tuple[str, ...],
    evidence_ids: tuple[str, ...] | None = None,
) -> StressResult:
    _text(scenario_id, "scenario_id")
    _text(baseline, "baseline_evidence_digest")
    if type(kind) is not StressKind or type(metrics) is not tuple or not metrics:
        raise PortfolioConstructionError("valid stress kind and metrics required")
    names: set[str] = set()
    for name, value in metrics:
        _text(name, "metric_name")
        _finite(value, "metric")
        if name in names:
            raise PortfolioConstructionError("duplicate stress metric")
        names.add(name)
    _texts(limitations, "limitations", nonempty=False)
    bound_evidence = tuple(sorted(evidence_ids or (baseline,)))
    _texts(bound_evidence, "input_evidence_ids", nonempty=True)
    canonical_metrics = tuple(sorted(metrics))
    body = {
        "scenario": scenario_id,
        "kind": kind.value,
        "baseline": baseline,
        "metrics": [[name, _canonical(value)] for name, value in canonical_metrics],
        "input_evidence_ids": list(bound_evidence),
        "limitations": list(limitations),
    }
    return StressResult(
        scenario_id,
        kind,
        baseline,
        canonical_metrics,
        bound_evidence,
        limitations,
        _digest("S4-13-STRESS-V1", body),
    )


def _positive_semidefinite(matrix: tuple[tuple[Decimal, ...], ...]) -> bool:
    if type(matrix) is not tuple or not matrix or any(type(row) is not tuple for row in matrix):
        return False
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        return False
    for row in range(size):
        for col in range(size):
            value = matrix[row][col]
            if type(value) is not Decimal or not value.is_finite() or value != matrix[col][row]:
                return False
            if value < -1 or value > 1 or (row == col and value != 1):
                return False
    # Semidefinite-safe LDL decomposition without taking square roots.
    lower = [[_ZERO for _ in range(size)] for _ in range(size)]
    diagonal = [_ZERO for _ in range(size)]
    for row in range(size):
        for col in range(row):
            residual = matrix[row][col] - sum(
                (lower[row][k] * lower[col][k] * diagonal[k] for k in range(col)), _ZERO
            )
            if diagonal[col] == 0:
                if residual != 0:
                    return False
                lower[row][col] = _ZERO
            else:
                lower[row][col] = residual / diagonal[col]
        diagonal[row] = matrix[row][row] - sum(
            (lower[row][k] * lower[row][k] * diagonal[k] for k in range(row)), _ZERO
        )
        if diagonal[row] < 0:
            return False
        lower[row][row] = Decimal(1)
    return True


def _unique_admissions(admissions: tuple[PortfolioAdmission, ...]) -> None:
    for item in admissions:
        if type(item) is not PortfolioAdmission:
            raise PortfolioConstructionError("exact admission type required")
        if item.admission_class is not AdmissionClass.HYPOTHETICAL_PARTICIPATION:
            raise InsufficientEvidenceError("INSUFFICIENT_EVIDENCE: research eligibility not established")
        rebuilt = PortfolioAdmission._from_assurance(
            item.source_context, item.admission_class, item.source_robustness, item.limitations,
        )
        if rebuilt != item:
            raise PortfolioConstructionError("admission content or authority substitution")
    strategies = [item.strategy.strategy_id for item in admissions]
    runs = [item.strategy.research_run_id for item in admissions]
    if len(set(strategies)) != len(strategies) or len(set(runs)) != len(runs):
        raise PortfolioConstructionError("strategy and research-run admissions must be unique")


def _exact_cents(value: Decimal, name: str) -> int:
    _finite(value, name)
    if value < 0 or value != value.quantize(_CENT):
        raise PortfolioConstructionError(f"{name} must be a nonnegative exact USD-cent amount")
    return int(value * 100)


def _ceil_cents(value: Decimal) -> Decimal:
    _finite(value, "financial_liability")
    if value < 0:
        raise PortfolioConstructionError("financial liability cannot be negative")
    return value.quantize(_CENT, rounding=ROUND_CEILING)


def _finite(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise PortfolioConstructionError(f"{name} must be finite")
    return value


def _positive(value: object, name: str) -> Decimal:
    result = _finite(value, name)
    if result <= 0:
        raise PortfolioConstructionError(f"{name} must be positive")
    return result


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise PortfolioConstructionError(f"{name} must be nonempty text")
    return value


def _texts(value: object, name: str, *, nonempty: bool) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str or not item.strip() for item in value):
        raise PortfolioConstructionError(f"{name} must be a text tuple")
    if nonempty and not value:
        raise PortfolioConstructionError(f"{name} cannot be empty")
    if len(set(value)) != len(value):
        raise PortfolioConstructionError(f"{name} contains duplicates")
    return value


def _canonical(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (Currency, InstrumentId, OrderId, Timestamp, EvidenceContentDigest)):
        return repr(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _canonical(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _digest(domain: str, body: object) -> str:
    payload = json.dumps(_canonical(body), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(domain.encode() + b"\0" + payload).hexdigest()


def _validate_financial_extension(
    records: tuple[CommitmentRecord, ...], oms: SimulatedOms, ledger: SimulationLedger,
    admissions: tuple[PortfolioAdmission, ...], cutoff: Timestamp | None = None,
) -> None:
    """The same known-history and chronology checks at producer and replay boundaries."""
    runs = {a.strategy.research_run_id for a in admissions}
    if any(event.research_run_id.value not in runs for event in ledger.events):
        raise PortfolioConstructionError("foreign holding research run")
    times = tuple(e.occurred_at.value for e in ledger.events)
    if times != tuple(sorted(times)) or (cutoff is not None and any(t > cutoff.value for t in times)):
        raise PortfolioConstructionError("financial event chronology or cutoff mismatch")
    for record in records:
        if record.source_ledger is not None and ledger.events[:len(record.source_ledger.events)] != record.source_ledger.events:
            raise PortfolioConstructionError("stale holding ledger omits known financial history")
        if record.source_oms is not None and oms.fills[:len(record.source_oms.fills)] != record.source_oms.fills:
            raise PortfolioConstructionError("immutable fill history truncated or substituted")
    for order in oms.orders:
        fill_times = tuple(f.execution_at.value for f in oms.fills if f.order_id == order.order.order_id)
        if fill_times != tuple(sorted(fill_times)):
            raise PortfolioConstructionError("fill chronology is not monotone")


def current_reservation_issues(
    book: CommitmentBook, oms: SimulatedOms, ledger: SimulationLedger, cutoff: Timestamp,
) -> tuple[str, ...]:
    """Detect infeasible obligations without deleting financial events or reservations.

    Currentness is supplied by the engineering controller, not inferred from hashes.
    Pure callers receive only a consistency calculation over their supplied state.
    """
    _check_book(book)
    _verify_currency(oms, ledger, book.allocation.currency)
    require_performance(reconcile(oms, ledger))
    if book.admissions:
        _validate_financial_extension(book.records, oms, ledger, book.admissions, cutoff)
    totals: dict[tuple[str, InstrumentId], Decimal] = {}
    aggregate: dict[InstrumentId, Decimal] = {}
    for record in _active_records(book.records):
        if record.side is CommitmentSide.SELL:
            key = (record.research_run_id, record.instrument_id)
            totals[key] = totals.get(key, _ZERO) + record.reserved_holding
            aggregate[record.instrument_id] = aggregate.get(record.instrument_id, _ZERO) + record.reserved_holding
    issues: list[str] = []
    reserved_cash = sum((r.reserved_cash for r in _active_records(book.records)
                         if r.side is CommitmentSide.BUY), _ZERO)
    if reserved_cash > ledger.state.cash:
        issues.append("INFEASIBLE_RESERVATION:cash")
    for (run, instrument), reserved in totals.items():
        if reserved > _holding_at(ledger, run, instrument, cutoff):
            issues.append("INFEASIBLE_RESERVATION:" + run + ":" + instrument.to_string())
    for instrument, reserved in aggregate.items():
        position = ledger.state.position(instrument)
        if reserved > (_ZERO if position is None else position.quantity):
            issues.append("INFEASIBLE_RESERVATION:portfolio:" + instrument.to_string())
    return tuple(sorted(issues))

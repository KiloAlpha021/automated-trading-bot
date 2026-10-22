"""Deterministic TREND-001 research-only portfolio intentions."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
)
from automated_trading_bot.research.identity import ResearchRun
from automated_trading_bot.research.pit_features import (
    DecisionInputEligibility,
    EligibilityState,
)
from automated_trading_bot.research.simulated_execution import CostScenario
from automated_trading_bot.research.simulation_ledger import SimulationLedger


class TrendError(ValueError):
    """TREND-001 input violates the frozen engineering contract."""


class AssetClass(StrEnum):
    EQUITY = "US_COMMON_EQUITY"
    ETF = "US_ETF"


class StrategyAuthority(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY_NO_EDGE_TRADING_OR_CAPITAL_AUTHORITY"


@dataclass(frozen=True, slots=True)
class TotalReturnEndpoint:
    instrument_id: InstrumentId
    month_end: date
    total_return_index: Decimal
    available_at: Timestamp
    dataset_id: str
    dataset_version: str
    eligibility: DecisionInputEligibility


@dataclass(frozen=True, slots=True)
class TrendCandidate:
    instrument_id: InstrumentId
    asset_class: AssetClass
    start: TotalReturnEndpoint
    end: TotalReturnEndpoint
    execution_price: Decimal


@dataclass(frozen=True, slots=True)
class TrendTarget:
    instrument_id: InstrumentId
    asset_class: AssetClass
    signal: Decimal
    weight: Decimal
    execution_price: Decimal
    whole_shares: int
    allocated_cash: Decimal


@dataclass(frozen=True, slots=True)
class TrendPortfolio:
    strategy_id: str
    research_run_id: str
    decision_time: Timestamp
    release_time: Timestamp
    fill_time: Timestamp
    equity_count: int
    etf_count: int
    positive_count: int
    classifications: tuple[tuple[InstrumentId, AssetClass], ...]
    targets: tuple[TrendTarget, ...]
    residual_cash: Decimal
    cost_scenarios: tuple[CostScenario, ...]
    economic_edge: str = "NOT_ESTABLISHED"
    authority: StrategyAuthority = StrategyAuthority.RESEARCH_ONLY


@dataclass(frozen=True, slots=True)
class TrendIntent:
    instrument_id: InstrumentId
    asset_class: AssetClass
    side: OrderSide
    quantity: Decimal
    current_quantity: Decimal
    target_quantity: Decimal
    reason: str


def _month_shift(value: date, months: int) -> tuple[int, int]:
    absolute = value.year * 12 + value.month - 1 + months
    return absolute // 12, absolute % 12 + 1


def _month_end(year_month: tuple[int, int]) -> date:
    year, month = year_month
    return date(year, month, calendar.monthrange(year, month)[1])


def expected_signal_months(decision_time: Timestamp) -> tuple[date, date]:
    """Return endpoints for the trailing twelve completed calendar months."""
    if type(decision_time) is not Timestamp:
        raise TypeError("decision_time must be Timestamp")
    anchor = decision_time.value.date().replace(day=1)
    return _month_end(_month_shift(anchor, -13)), _month_end(_month_shift(anchor, -1))


def _positive_decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise TrendError(f"{name} must be a positive finite Decimal")
    return value


def _validate_endpoint(
    endpoint: TotalReturnEndpoint,
    expected_month_end: date,
    candidate: TrendCandidate,
    qualification: DatasetQualification,
    decision_time: Timestamp,
) -> None:
    if type(endpoint) is not TotalReturnEndpoint:
        raise TypeError("endpoint must be TotalReturnEndpoint")
    if endpoint.instrument_id != candidate.instrument_id:
        raise TrendError("endpoint instrument substitution")
    if endpoint.month_end != expected_month_end:
        raise TrendError("endpoint is outside the canonical completed-month window")
    if endpoint.dataset_id != qualification.dataset_id or endpoint.dataset_version != qualification.dataset_version:
        raise TrendError("endpoint dataset substitution")
    _positive_decimal(endpoint.total_return_index, "total_return_index")
    if endpoint.available_at.value > decision_time.value:
        raise TrendError("future-known endpoint")
    if endpoint.eligibility.state is not EligibilityState.ELIGIBLE:
        raise TrendError("PIT-ineligible endpoint")
    if endpoint.eligibility.decision_time != decision_time:
        raise TrendError("endpoint decision-time substitution")
    if not (qualification.coverage.start <= endpoint.month_end <= qualification.coverage.end):
        raise TrendError("endpoint outside qualified coverage")


def construct_trend_portfolio(
    *,
    candidates: tuple[TrendCandidate, ...],
    qualification: DatasetQualification,
    research_run: ResearchRun,
    decision_time: Timestamp,
    release_time: Timestamp,
    fill_time: Timestamp,
    hypothetical_capital: Decimal = Decimal("100000"),
) -> TrendPortfolio:
    """Build targets only; execution and accounting remain predecessor concerns."""
    if type(candidates) is not tuple or not candidates:
        raise TrendError("nonempty candidate tuple required")
    if type(qualification) is not DatasetQualification or type(research_run) is not ResearchRun:
        raise TypeError("validated qualification and research run required")
    if qualification.lifecycle is not QualificationLifecycle.CURRENT or not permits_claim(
        qualification, ClaimCeiling.ENGINEERING_ONLY
    ):
        raise TrendError("current engineering-qualified dataset required")
    if not all(type(value) is Timestamp for value in (decision_time, release_time, fill_time)):
        raise TypeError("decision, release and fill times must be Timestamp")
    if not decision_time.value < release_time.value < fill_time.value:
        raise TrendError("require decision < release < fill")
    capital = _positive_decimal(hypothetical_capital, "hypothetical_capital")
    if len({candidate.instrument_id for candidate in candidates}) != len(candidates):
        raise TrendError("duplicate instrument")

    start_month, end_month = expected_signal_months(decision_time)
    scored: list[tuple[TrendCandidate, Decimal]] = []
    for candidate in candidates:
        if type(candidate) is not TrendCandidate or type(candidate.asset_class) is not AssetClass:
            raise TypeError("candidate and asset_class must use exact TREND-001 types")
        _validate_endpoint(candidate.start, start_month, candidate, qualification, decision_time)
        _validate_endpoint(candidate.end, end_month, candidate, qualification, decision_time)
        _positive_decimal(candidate.execution_price, "execution_price")
        signal = candidate.end.total_return_index / candidate.start.total_return_index - Decimal(1)
        scored.append((candidate, signal))

    positives = sorted(
        (item for item in scored if item[1] > 0),
        key=lambda item: item[0].instrument_id.to_string(),
    )
    targets: tuple[TrendTarget, ...]
    if positives:
        weight = Decimal(1) / Decimal(len(positives))
        cash_per_asset = capital / Decimal(len(positives))
        built: list[TrendTarget] = []
        for candidate, signal in positives:
            shares = int((cash_per_asset / candidate.execution_price).to_integral_value(rounding=ROUND_FLOOR))
            if shares <= 0:
                raise TrendError("capital cannot fund a positive-signal whole share")
            built.append(
                TrendTarget(
                    candidate.instrument_id,
                    candidate.asset_class,
                    signal,
                    weight,
                    candidate.execution_price,
                    shares,
                    candidate.execution_price * shares,
                )
            )
        targets = tuple(built)
    else:
        targets = ()
    invested = sum((target.allocated_cash for target in targets), Decimal(0))
    classifications = tuple(
        sorted(
            ((candidate.instrument_id, candidate.asset_class) for candidate in candidates),
            key=lambda item: item[0].to_string(),
        )
    )
    return TrendPortfolio(
        "TREND-001",
        research_run.run_id.value,
        decision_time,
        release_time,
        fill_time,
        sum(candidate.asset_class is AssetClass.EQUITY for candidate in candidates),
        sum(candidate.asset_class is AssetClass.ETF for candidate in candidates),
        len(positives),
        classifications,
        targets,
        capital - invested,
        tuple(CostScenario),
    )


def construct_transition_intents(
    portfolio: TrendPortfolio, ledger: SimulationLedger
) -> tuple[TrendIntent, ...]:
    """Compare targets with actual ledger holdings without mutating financial truth."""
    if type(portfolio) is not TrendPortfolio or type(ledger) is not SimulationLedger:
        raise TypeError("portfolio and ledger must use exact predecessor types")
    classifications = dict(portfolio.classifications)
    target_quantities = {
        target.instrument_id: Decimal(target.whole_shares) for target in portfolio.targets
    }
    held_ids = {position.instrument_id for position in ledger.state.positions if position.quantity != 0}
    missing = held_ids - classifications.keys()
    if missing:
        raise TrendError("held instrument missing classified universe evidence")
    intents: list[TrendIntent] = []
    for instrument_id in sorted(classifications, key=lambda value: value.to_string()):
        position = ledger.state.position(instrument_id)
        current = position.quantity if position is not None else Decimal(0)
        target = target_quantities.get(instrument_id, Decimal(0))
        difference = target - current
        if difference == 0:
            continue
        side = OrderSide.BUY if difference > 0 else OrderSide.SELL
        intents.append(
            TrendIntent(
                instrument_id,
                classifications[instrument_id],
                side,
                abs(difference),
                current,
                target,
                "POSITIVE_SIGNAL_TARGET" if side is OrderSide.BUY else "NONPOSITIVE_SIGNAL_EXIT",
            )
        )
    return tuple(intents)

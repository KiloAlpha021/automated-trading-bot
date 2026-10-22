"""Deterministic MOM-001 research-only portfolio construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum

from automated_trading_bot.domain.identifiers import InstrumentId
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


class MomentumError(ValueError):
    """MOM-001 input violates the frozen engineering contract."""


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
class MomentumCandidate:
    instrument_id: InstrumentId
    start: TotalReturnEndpoint
    end: TotalReturnEndpoint
    execution_price: Decimal


@dataclass(frozen=True, slots=True)
class MomentumTarget:
    instrument_id: InstrumentId
    rank: int
    signal: Decimal
    weight: Decimal
    execution_price: Decimal
    whole_shares: int
    allocated_cash: Decimal


@dataclass(frozen=True, slots=True)
class MomentumPortfolio:
    strategy_id: str
    research_run_id: str
    decision_time: Timestamp
    release_time: Timestamp
    fill_time: Timestamp
    eligible_count: int
    selected_count: int
    targets: tuple[MomentumTarget, ...]
    residual_cash: Decimal
    cost_scenarios: tuple[CostScenario, ...]
    fixture_minimum_applied: bool
    economic_edge: str = "NOT_ESTABLISHED"
    authority: StrategyAuthority = StrategyAuthority.RESEARCH_ONLY


def _month_shift(value: date, months: int) -> tuple[int, int]:
    absolute = value.year * 12 + value.month - 1 + months
    return absolute // 12, absolute % 12 + 1


def expected_signal_months(decision_time: Timestamp) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return the approved 12-1 start and end completed calendar months."""
    if type(decision_time) is not Timestamp:
        raise TypeError("decision_time must be Timestamp")
    anchor = decision_time.value.date().replace(day=1)
    return _month_shift(anchor, -13), _month_shift(anchor, -2)


def _valid_decimal(value: object, name: str, *, positive: bool = True) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise MomentumError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise MomentumError(f"{name} must be positive")
    return value


def _validate_endpoint(
    value: TotalReturnEndpoint,
    expected_month: tuple[int, int],
    candidate: MomentumCandidate,
    qualification: DatasetQualification,
    decision_time: Timestamp,
) -> None:
    if type(value) is not TotalReturnEndpoint:
        raise TypeError("endpoint must be TotalReturnEndpoint")
    if value.instrument_id != candidate.instrument_id:
        raise MomentumError("endpoint instrument substitution")
    if (value.month_end.year, value.month_end.month) != expected_month:
        raise MomentumError("endpoint is outside the canonical 12-1 month window")
    if value.dataset_id != qualification.dataset_id or value.dataset_version != qualification.dataset_version:
        raise MomentumError("endpoint dataset substitution")
    _valid_decimal(value.total_return_index, "total_return_index")
    if value.available_at.value > decision_time.value:
        raise MomentumError("future-known endpoint")
    if value.eligibility.state is not EligibilityState.ELIGIBLE:
        raise MomentumError("PIT-ineligible endpoint")
    if value.eligibility.decision_time != decision_time:
        raise MomentumError("endpoint decision-time substitution")
    if not (qualification.coverage.start <= value.month_end <= qualification.coverage.end):
        raise MomentumError("endpoint outside qualified coverage")


def construct_momentum_portfolio(
    *,
    candidates: tuple[MomentumCandidate, ...],
    qualification: DatasetQualification,
    research_run: ResearchRun,
    decision_time: Timestamp,
    release_time: Timestamp,
    fill_time: Timestamp,
    hypothetical_capital: Decimal = Decimal("100000"),
    fixture_minimum: bool = False,
) -> MomentumPortfolio:
    """Construct the frozen MOM-001 target portfolio without executing orders."""
    if type(candidates) is not tuple or not candidates:
        raise MomentumError("nonempty candidate tuple required")
    if type(qualification) is not DatasetQualification or type(research_run) is not ResearchRun:
        raise TypeError("validated qualification and research run required")
    if qualification.lifecycle is not QualificationLifecycle.CURRENT or not permits_claim(
        qualification, ClaimCeiling.ENGINEERING_ONLY
    ):
        raise MomentumError("current engineering-qualified dataset required")
    if not all(type(v) is Timestamp for v in (decision_time, release_time, fill_time)):
        raise TypeError("decision, release and fill times must be Timestamp")
    if not decision_time.value < release_time.value < fill_time.value:
        raise MomentumError("require decision < release < fill")
    capital = _valid_decimal(hypothetical_capital, "hypothetical_capital")
    if type(fixture_minimum) is not bool:
        raise TypeError("fixture_minimum must be bool")
    if fixture_minimum and len(candidates) < 20:
        raise MomentumError("engineering fixture requires at least 20 securities")
    if len({v.instrument_id for v in candidates}) != len(candidates):
        raise MomentumError("duplicate instrument")
    start_month, end_month = expected_signal_months(decision_time)
    scored: list[tuple[MomentumCandidate, Decimal]] = []
    for candidate in candidates:
        if type(candidate) is not MomentumCandidate:
            raise TypeError("candidate must be MomentumCandidate")
        _validate_endpoint(candidate.start, start_month, candidate, qualification, decision_time)
        _validate_endpoint(candidate.end, end_month, candidate, qualification, decision_time)
        price = _valid_decimal(candidate.execution_price, "execution_price")
        scored.append((candidate, candidate.end.total_return_index / candidate.start.total_return_index - Decimal(1)))
        if price != candidate.execution_price:
            raise AssertionError("unreachable")
    scored.sort(key=lambda item: (-item[1], item[0].instrument_id.to_string()))
    selected_count = int((Decimal("0.20") * len(scored)).to_integral_value(rounding=ROUND_FLOOR))
    if selected_count < 1:
        raise MomentumError("top-20-percent floor selects no securities")
    selected = scored[:selected_count]
    weight = Decimal(1) / Decimal(selected_count)
    cash_per_name = capital / Decimal(selected_count)
    targets = tuple(
        MomentumTarget(
            candidate.instrument_id,
            rank,
            signal,
            weight,
            candidate.execution_price,
            int((cash_per_name / candidate.execution_price).to_integral_value(rounding=ROUND_FLOOR)),
            candidate.execution_price * int((cash_per_name / candidate.execution_price).to_integral_value(rounding=ROUND_FLOOR)),
        )
        for rank, (candidate, signal) in enumerate(selected, start=1)
    )
    if any(target.whole_shares <= 0 for target in targets):
        raise MomentumError("capital cannot fund a selected whole share")
    invested = sum((target.allocated_cash for target in targets), Decimal(0))
    return MomentumPortfolio(
        "MOM-001",
        research_run.run_id.value,
        decision_time,
        release_time,
        fill_time,
        len(scored),
        selected_count,
        targets,
        capital - invested,
        tuple(CostScenario),
        fixture_minimum,
    )

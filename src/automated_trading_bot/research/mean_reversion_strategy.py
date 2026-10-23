"""Deterministic MR-001-R1 research-only portfolio and cohort controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum
import json
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

from automated_trading_bot.calendars import (
    CalendarSessionResolutionResult,
    TradingDayKind,
)
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import ResolutionDisposition
from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
)
from automated_trading_bot.research.identity import ResearchRun
from automated_trading_bot.research.evidence import ResearchEvidenceManifest
from automated_trading_bot.research.simulated_execution import (
    CostScenario,
    OrderRecord,
    OrderStatus,
    SimulatedOms,
)
from automated_trading_bot.research.simulation_ledger import SimulationLedger
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationState,
    reconcile,
)
from automated_trading_bot.research.research_governance import (
    AssuranceMode,
    FrozenResearchPlan,
    JournalCheckpoint,
    PitEligibilityEvidence,
    ResearchGovernor,
    TrustContext,
    require_qualification_chain,
)
from automated_trading_bot.research.simulation_state import SimulationStateJournal


class MeanReversionError(ValueError):
    """MR-001-R1 input or recovered state violates the frozen contract."""


class StrategyAuthority(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY_NO_EDGE_TRADING_OR_CAPITAL_AUTHORITY"


class CohortState(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class SessionReturnEndpoint:
    instrument_id: InstrumentId
    session_date: date
    total_return_index: Decimal
    available_at: Timestamp
    dataset_id: str
    dataset_version: str
    pit_evidence: PitEligibilityEvidence


@dataclass(frozen=True, slots=True)
class MeanReversionCandidate:
    instrument_id: InstrumentId
    start: SessionReturnEndpoint
    end: SessionReturnEndpoint
    allocation_price: Decimal
    allocation_price_evidence: PitEligibilityEvidence


@dataclass(frozen=True, slots=True)
class MeanReversionTarget:
    instrument_id: InstrumentId
    rank: int
    signal: Decimal
    weight: Decimal
    allocation_price: Decimal
    whole_shares: int
    allocated_cash: Decimal


@dataclass(frozen=True, slots=True)
class MeanReversionIntent:
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class MeanReversionPortfolio:
    strategy_id: str
    research_run_id: str
    decision_time: Timestamp
    release_time: Timestamp
    decision_session_date: date
    execution_session_date: date
    eligible_count: int
    selected_count: int
    targets: tuple[MeanReversionTarget, ...]
    entry_intents: tuple[MeanReversionIntent, ...]
    residual_cash: Decimal
    unfunded_instruments: tuple[InstrumentId, ...]
    cost_scenarios: tuple[CostScenario, ...]
    economic_edge: str = "NOT_ESTABLISHED"
    authority: StrategyAuthority = StrategyAuthority.RESEARCH_ONLY


@dataclass(frozen=True, slots=True)
class MeanReversionCohort:
    cohort_id: str
    research_run_id: str
    instrument_ids: tuple[InstrumentId, ...]
    entry_order_ids: tuple[OrderId, ...]
    exit_order_ids: tuple[OrderId, ...] = ()

    def __post_init__(self) -> None:
        if type(self.cohort_id) is not str or not self.cohort_id.strip():
            raise MeanReversionError("cohort_id must be nonempty")
        if type(self.research_run_id) is not str or not self.research_run_id.strip():
            raise MeanReversionError("research_run_id must be nonempty")
        if type(self.instrument_ids) is not tuple or any(
            type(value) is not InstrumentId for value in self.instrument_ids
        ):
            raise TypeError("instrument_ids must be an InstrumentId tuple")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise MeanReversionError("duplicate cohort instrument")
        for name in ("entry_order_ids", "exit_order_ids"):
            values = getattr(self, name)
            if type(values) is not tuple or any(
                type(value) is not OrderId for value in values
            ):
                raise TypeError(f"{name} must be an OrderId tuple")
            if len(set(values)) != len(values):
                raise MeanReversionError(f"duplicate {name}")
        if set(self.entry_order_ids) & set(self.exit_order_ids):
            raise MeanReversionError("entry and exit order identities overlap")


@dataclass(frozen=True, slots=True)
class PositionHoldingClock:
    instrument_id: InstrumentId
    first_fill_session: date
    completed_sessions: int
    exit_eligible: bool


@dataclass(frozen=True, slots=True)
class CohortAssessment:
    state: CohortState
    entry_orders_resolved: bool
    exit_orders_resolved: bool
    ledger_flat: bool
    oms_ledger_reconciled: bool
    clocks: tuple[PositionHoldingClock, ...]
    reasons: tuple[str, ...]


_RESOLVED_ORDER_STATES = {
    OrderStatus.REJECTED,
    OrderStatus.CANCELLED,
    OrderStatus.UNFILLED,
    OrderStatus.FULLY_FILLED,
}


def _positive_decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise MeanReversionError(f"{name} must be a positive finite Decimal")
    return value


def _decimal_digest(domain: bytes, value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise MeanReversionError("evidence value must be a finite Decimal")
    canonical = "0" if value == 0 else format(value.normalize(), "f")
    return EvidenceContentDigest.from_bytes(
        domain + b"\0" + canonical_json({"value": canonical})
    ).value


def _ordered_calendar_days(
    values: tuple[CalendarSessionResolutionResult, ...],
) -> tuple[CalendarSessionResolutionResult, ...]:
    if type(values) is not tuple or not values:
        raise MeanReversionError("calendar evidence must be a nonempty tuple")
    if any(type(value) is not CalendarSessionResolutionResult for value in values):
        raise TypeError("calendar evidence must use CalendarSessionResolutionResult")
    ordered = tuple(sorted(values, key=lambda value: value.local_date))
    if len({value.local_date for value in ordered}) != len(ordered):
        raise MeanReversionError("duplicate calendar date")
    calendar_id = ordered[0].calendar_id
    version_id = ordered[0].selected_calendar_version_id
    for value in ordered:
        if value.disposition is not ResolutionDisposition.ESTABLISHED:
            raise MeanReversionError("calendar session is not established")
        if (
            value.calendar_id != calendar_id
            or value.selected_calendar_version_id != version_id
        ):
            raise MeanReversionError("calendar identity or version is inconsistent")
        if value.trading_day is None:
            raise MeanReversionError(
                "established calendar result lacks trading-day evidence"
            )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        probe = previous.local_date + timedelta(days=1)
        while probe < current.local_date:
            if probe.weekday() < 5:
                raise MeanReversionError("weekday calendar evidence is incomplete")
            probe += timedelta(days=1)
    return ordered


def _trading_sessions(
    values: tuple[CalendarSessionResolutionResult, ...],
) -> tuple[CalendarSessionResolutionResult, ...]:
    return tuple(
        value
        for value in _ordered_calendar_days(values)
        if value.trading_day is not None
        and value.trading_day.day_kind is TradingDayKind.REGULAR_TRADING_DAY
        and value.session is not None
        and value.utc_open is not None
        and value.utc_close is not None
    )


def decision_and_execution_sessions(
    calendar_days: tuple[CalendarSessionResolutionResult, ...],
    decision_time: Timestamp,
    release_time: Timestamp,
) -> tuple[CalendarSessionResolutionResult, CalendarSessionResolutionResult]:
    """Resolve the Friday decision session and next established execution session."""
    if type(decision_time) is not Timestamp or type(release_time) is not Timestamp:
        raise TypeError("decision_time and release_time must be Timestamp")
    if release_time.value <= decision_time.value:
        raise MeanReversionError("release must be after decision")
    ordered = _ordered_calendar_days(calendar_days)
    decision_local_date = decision_time.value.date()
    week_monday = decision_local_date - timedelta(days=decision_local_date.weekday())
    week_friday = week_monday + timedelta(days=4)
    if decision_local_date != week_friday:
        raise MeanReversionError(
            "decision timestamp must belong to the Friday evaluation date"
        )
    week_days = tuple(
        value for value in ordered if week_monday <= value.local_date <= week_friday
    )
    if {value.local_date for value in week_days} != {
        week_monday + timedelta(days=offset) for offset in range(5)
    }:
        raise MeanReversionError("decision week calendar evidence is incomplete")
    eligible = tuple(
        value
        for value in week_days
        if value.trading_day is not None
        and value.trading_day.day_kind is TradingDayKind.REGULAR_TRADING_DAY
        and value.utc_close is not None
    )
    if not eligible:
        raise MeanReversionError(
            "decision week contains no established eligible session"
        )
    decision_session = eligible[-1]
    assert decision_session.utc_close is not None
    if decision_time.value <= decision_session.utc_close.value:
        raise MeanReversionError(
            "decision must occur after the selected session completes"
        )
    future = tuple(
        value
        for value in _trading_sessions(ordered)
        if value.local_date > decision_session.local_date
    )
    if not future:
        raise MeanReversionError("next eligible execution session is not established")
    execution_session = future[0]
    assert (
        execution_session.utc_open is not None
        and execution_session.utc_close is not None
    )
    if release_time.value < execution_session.utc_open.value:
        raise MeanReversionError("release precedes the next eligible session")
    if release_time.value > execution_session.utc_close.value:
        raise MeanReversionError("release is outside the next eligible session")
    return decision_session, execution_session


def _validate_endpoint(
    endpoint: SessionReturnEndpoint,
    expected_date: date,
    candidate: MeanReversionCandidate,
    qualification: DatasetQualification,
    plan: FrozenResearchPlan,
    manifest: ResearchEvidenceManifest,
    decision_time: Timestamp,
) -> None:
    if type(endpoint) is not SessionReturnEndpoint:
        raise TypeError("endpoint must be SessionReturnEndpoint")
    if endpoint.instrument_id != candidate.instrument_id:
        raise MeanReversionError("endpoint instrument substitution")
    if endpoint.session_date != expected_date:
        raise MeanReversionError("endpoint does not match the five-session window")
    if (
        endpoint.dataset_id != qualification.dataset_id
        or endpoint.dataset_version != qualification.dataset_version
    ):
        raise MeanReversionError("endpoint dataset substitution")
    _positive_decimal(endpoint.total_return_index, "total_return_index")
    evidence = endpoint.pit_evidence
    if type(evidence) is not PitEligibilityEvidence:
        raise TypeError("endpoint requires reconstructible PIT evidence")
    evidence.validate(plan, qualification, manifest)
    if (
        evidence.research_run_id != manifest.run_id
        or evidence.observation.instrument_id != endpoint.instrument_id
        or evidence.observation.dataset_id != endpoint.dataset_id
        or evidence.observation.qualification_id != qualification.qualification_id
        or evidence.observation.input_window_end.value.date() != endpoint.session_date
        or evidence.observation.available_at != endpoint.available_at
        or evidence.decision_time != decision_time
        or evidence.observation.value_digest
        != _decimal_digest(b"ATIS-S4-11-TOTAL-RETURN-INDEX-V1", endpoint.total_return_index)
    ):
        raise MeanReversionError("endpoint PIT identity or value binding is invalid")
    if endpoint.available_at.value > decision_time.value:
        raise MeanReversionError("future-known endpoint")
    if not (
        qualification.coverage.start
        <= endpoint.session_date
        <= qualification.coverage.end
    ):
        raise MeanReversionError("endpoint outside qualified coverage")


def construct_mean_reversion_portfolio(
    *,
    candidates: tuple[MeanReversionCandidate, ...],
    qualification: DatasetQualification,
    qualification_predecessors: tuple[DatasetQualification, ...],
    research_run: ResearchRun,
    plan: FrozenResearchPlan,
    manifest: ResearchEvidenceManifest,
    governor: ResearchGovernor,
    calendar_days: tuple[CalendarSessionResolutionResult, ...],
    decision_time: Timestamp,
    release_time: Timestamp,
    hypothetical_capital: Decimal = Decimal("100000"),
) -> MeanReversionPortfolio:
    """Create deterministic targets and entry intentions without executing orders."""
    if type(candidates) is not tuple or not candidates:
        raise MeanReversionError("nonempty candidate tuple required")
    if (
        type(qualification) is not DatasetQualification
        or type(research_run) is not ResearchRun
        or type(plan) is not FrozenResearchPlan
        or type(manifest) is not ResearchEvidenceManifest
        or type(governor) is not ResearchGovernor
    ):
        raise TypeError("validated qualification, run, plan, manifest, and governor required")
    require_qualification_chain(qualification, qualification_predecessors)
    governor.require_admitted(plan, research_run)
    if manifest.run_id != research_run.run_id or manifest.experiment_id != plan.experiment_id:
        raise MeanReversionError("research evidence manifest substitution")
    if (
        qualification.lifecycle is not QualificationLifecycle.CURRENT
        or not permits_claim(qualification, ClaimCeiling.ENGINEERING_ONLY)
    ):
        raise MeanReversionError("current engineering-qualified dataset required")
    capital = _positive_decimal(hypothetical_capital, "hypothetical_capital")
    if len({candidate.instrument_id for candidate in candidates}) != len(candidates):
        raise MeanReversionError("duplicate instrument")
    decision_session, execution_session = decision_and_execution_sessions(
        calendar_days, decision_time, release_time
    )
    trading = _trading_sessions(calendar_days)
    signal_sessions = tuple(
        value for value in trading if value.local_date <= decision_session.local_date
    )[-6:]
    if (
        len(signal_sessions) != 6
        or signal_sessions[-1].local_date != decision_session.local_date
    ):
        raise MeanReversionError(
            "exactly five completed eligible-session returns are required"
        )
    start_date = signal_sessions[0].local_date
    end_date = signal_sessions[-1].local_date
    scored: list[tuple[MeanReversionCandidate, Decimal]] = []
    for candidate in candidates:
        if type(candidate) is not MeanReversionCandidate:
            raise TypeError("candidate must be MeanReversionCandidate")
        _validate_endpoint(
            candidate.start, start_date, candidate, qualification, plan, manifest, decision_time
        )
        _validate_endpoint(
            candidate.end, end_date, candidate, qualification, plan, manifest, decision_time
        )
        price = _positive_decimal(candidate.allocation_price, "allocation_price")
        price_evidence = candidate.allocation_price_evidence
        if type(price_evidence) is not PitEligibilityEvidence:
            raise TypeError("allocation price requires reconstructible PIT evidence")
        price_evidence.validate(plan, qualification, manifest)
        if (
            price_evidence.research_run_id != research_run.run_id
            or price_evidence.observation.instrument_id != candidate.instrument_id
            or price_evidence.observation.dataset_id != qualification.dataset_id
            or price_evidence.observation.qualification_id != qualification.qualification_id
            or price_evidence.observation.input_window_end.value.date()
            != decision_session.local_date
            or price_evidence.observation.value_digest
            != _decimal_digest(b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", price)
            or price_evidence.decision_time != decision_time
            or price_evidence.observation.available_at.value > decision_time.value
        ):
            raise MeanReversionError("allocation price PIT identity or value binding is invalid")
        scored.append(
            (
                candidate,
                candidate.end.total_return_index / candidate.start.total_return_index
                - Decimal(1),
            )
        )
        if price != candidate.allocation_price:
            raise AssertionError("unreachable")
    scored.sort(key=lambda item: (item[1], item[0].instrument_id.to_string()))
    selected_count = int(
        (Decimal("0.20") * len(scored)).to_integral_value(rounding=ROUND_FLOOR)
    )
    selected = scored[:selected_count]
    targets: list[MeanReversionTarget] = []
    unfunded: list[InstrumentId] = []
    if selected_count:
        weight = Decimal(1) / Decimal(selected_count)
        cash_per_name = capital / Decimal(selected_count)
        for rank, (candidate, signal) in enumerate(selected, start=1):
            shares = int(
                (cash_per_name / candidate.allocation_price).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            if shares <= 0:
                unfunded.append(candidate.instrument_id)
                continue
            targets.append(
                MeanReversionTarget(
                    candidate.instrument_id,
                    rank,
                    signal,
                    weight,
                    candidate.allocation_price,
                    shares,
                    candidate.allocation_price * shares,
                )
            )
    invested = sum((target.allocated_cash for target in targets), Decimal(0))
    intents = tuple(
        MeanReversionIntent(
            target.instrument_id,
            OrderSide.BUY,
            Decimal(target.whole_shares),
            "BOTTOM_TWENTY_PERCENT_ENTRY",
        )
        for target in targets
    )
    return MeanReversionPortfolio(
        "MR-001-R1",
        research_run.run_id.value,
        decision_time,
        release_time,
        decision_session.local_date,
        execution_session.local_date,
        len(scored),
        selected_count,
        tuple(targets),
        intents,
        capital - invested,
        tuple(unfunded),
        tuple(CostScenario),
    )


def _cohort_order_records(
    cohort: MeanReversionCohort, oms: SimulatedOms
) -> tuple[OrderRecord, ...]:
    order_ids = cohort.entry_order_ids + cohort.exit_order_ids
    attributable = tuple(
        record.order.order_id
        for record in oms.orders
        if record.order.research_run_id.value == cohort.research_run_id
    )
    if set(attributable) != set(order_ids) or len(attributable) != len(order_ids):
        raise MeanReversionError("cohort does not account for complete same-run OMS history")
    records = []
    for order_id in order_ids:
        record = oms.order(order_id)
        if record is None:
            raise MeanReversionError("cohort order is missing from OMS history")
        if record.order.research_run_id.value != cohort.research_run_id:
            raise MeanReversionError("cohort order research identity mismatch")
        if record.order.instrument_id not in cohort.instrument_ids:
            raise MeanReversionError("cohort order instrument mismatch")
        expected_side = (
            OrderSide.BUY if order_id in cohort.entry_order_ids else OrderSide.SELL
        )
        if record.order.side is not expected_side:
            raise MeanReversionError("cohort order side mismatch")
        records.append(record)
    return tuple(records)


def _fill_session(
    fill_time: Timestamp,
    sessions: tuple[CalendarSessionResolutionResult, ...],
) -> date:
    matches = []
    for value in sessions:
        if value.utc_open is None or value.utc_close is None:
            continue
        if value.utc_open.value <= fill_time.value <= value.utc_close.value:
            matches.append(value.local_date)
    if len(matches) != 1:
        raise MeanReversionError("fill does not map to exactly one established session")
    return matches[0]


def assess_cohort(
    cohort: MeanReversionCohort,
    oms: SimulatedOms,
    ledger: SimulationLedger,
    calendar_days: tuple[CalendarSessionResolutionResult, ...],
    as_of: Timestamp,
) -> CohortAssessment:
    """Reconcile immutable OMS fills, ledger holdings, holding clocks, and closure."""
    if (
        type(cohort) is not MeanReversionCohort
        or type(oms) is not SimulatedOms
        or type(ledger) is not SimulationLedger
    ):
        raise TypeError("cohort, oms, and ledger must use exact predecessor types")
    if type(as_of) is not Timestamp:
        raise TypeError("as_of must be Timestamp")
    sessions = tuple(
        value
        for value in _trading_sessions(calendar_days)
        if value.utc_close is not None and value.utc_close.value < as_of.value
    )
    records = _cohort_order_records(cohort, oms)
    entry_records = records[: len(cohort.entry_order_ids)]
    exit_records = records[len(cohort.entry_order_ids) :]
    entry_resolved = all(
        record.status in _RESOLVED_ORDER_STATES for record in entry_records
    )
    exit_resolved = all(
        record.status in _RESOLVED_ORDER_STATES for record in exit_records
    )

    fills = tuple(
        fill
        for fill in oms.fills
        if fill.order_id in set(cohort.entry_order_ids + cohort.exit_order_ids)
    )
    net = {instrument_id: Decimal(0) for instrument_id in cohort.instrument_ids}
    first_fill: dict[InstrumentId, date] = {}
    for fill in fills:
        signed = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        net[fill.instrument_id] += signed
        if fill.side is OrderSide.BUY:
            local_date = _fill_session(fill.execution_at, sessions)
            prior = first_fill.get(fill.instrument_id)
            if prior is None or local_date < prior:
                first_fill[fill.instrument_id] = local_date
    ledger_quantities = {
        instrument_id: (
            Decimal(0)
            if ledger.state.position(instrument_id) is None
            else ledger.state.position(instrument_id).quantity  # type: ignore[union-attr]
        )
        for instrument_id in cohort.instrument_ids
    }
    reconciled = net == ledger_quantities
    if not reconciled:
        raise MeanReversionError("OMS fills and ledger positions do not reconcile")
    clocks = []
    for instrument_id, first_date in sorted(
        first_fill.items(), key=lambda item: item[0].to_string()
    ):
        count = sum(value.local_date >= first_date for value in sessions)
        clocks.append(
            PositionHoldingClock(instrument_id, first_date, count, count >= 5)
        )
    flat = all(quantity == 0 for quantity in ledger_quantities.values())
    reasons = []
    if not entry_resolved:
        reasons.append("OUTSTANDING_ENTRY_ORDER")
    if not exit_resolved:
        reasons.append("OUTSTANDING_EXIT_ORDER")
    if not flat:
        reasons.append("OPEN_LEDGER_POSITION")
    state = (
        CohortState.CLOSED
        if entry_resolved and exit_resolved and flat and reconciled
        else CohortState.ACTIVE
    )
    return CohortAssessment(
        state,
        entry_resolved,
        exit_resolved,
        flat,
        reconciled,
        tuple(clocks),
        tuple(reasons),
    )


def construct_exit_intents(
    cohort: MeanReversionCohort,
    oms: SimulatedOms,
    ledger: SimulationLedger,
    calendar_days: tuple[CalendarSessionResolutionResult, ...],
    as_of: Timestamp,
) -> tuple[MeanReversionIntent, ...]:
    """Create exit intentions only after each position's fifth session completes."""
    assessment = assess_cohort(cohort, oms, ledger, calendar_days, as_of)
    if not assessment.entry_orders_resolved:
        return ()
    clocks = {clock.instrument_id: clock for clock in assessment.clocks}
    intents = []
    for instrument_id in sorted(
        cohort.instrument_ids, key=lambda value: value.to_string()
    ):
        position = ledger.state.position(instrument_id)
        if position is None or position.quantity == 0:
            continue
        clock = clocks.get(instrument_id)
        if clock is None or not clock.exit_eligible:
            continue
        existing = [
            oms.order(order_id)
            for order_id in cohort.exit_order_ids
            if oms.order(order_id) is not None
            and oms.order(order_id).order.instrument_id == instrument_id  # type: ignore[union-attr]
        ]
        if any(
            record is not None
            and record.status
            in {
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FULLY_FILLED,
            }
            for record in existing
        ):
            continue
        intents.append(
            MeanReversionIntent(
                instrument_id,
                OrderSide.SELL,
                position.quantity,
                "FIVE_SESSION_HOLDING_COMPLETE",
            )
        )
    return tuple(intents)


def require_prior_cohort_closed(assessment: CohortAssessment | None) -> None:
    """Fail closed unless there is no predecessor or its closure is verified."""
    if assessment is None:
        raise MeanReversionError("trusted predecessor history is required")
    if type(assessment) is not CohortAssessment:
        raise TypeError("assessment must be CohortAssessment or None")
    if assessment.state is not CohortState.CLOSED:
        raise MeanReversionError("prior cohort is not verifiably closed")


class AdmissionState(StrEnum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class CohortHistoryState:
    sequence: int
    terminal_digest: str | None
    admission_state: AdmissionState | None
    cohorts: tuple[MeanReversionCohort, ...]
    pending_cohort: MeanReversionCohort | None = None
    pending_simulation_state_digest: str | None = None


def _cohort_body(value: MeanReversionCohort) -> dict[str, object]:
    return {
        "cohort_id": value.cohort_id,
        "research_run_id": value.research_run_id,
        "instrument_ids": [item.to_string() for item in value.instrument_ids],
        "entry_order_ids": [str(item.value) for item in value.entry_order_ids],
        "exit_order_ids": [str(item.value) for item in value.exit_order_ids],
    }


def _cohort_from_body(body: object) -> MeanReversionCohort:
    from uuid import UUID

    if type(body) is not dict:
        raise MeanReversionError("cohort record is invalid")
    return MeanReversionCohort(
        str(body["cohort_id"]),
        str(body["research_run_id"]),
        tuple(InstrumentId.parse(str(item)) for item in body["instrument_ids"]),
        tuple(OrderId(UUID(str(item))) for item in body["entry_order_ids"]),
        tuple(OrderId(UUID(str(item))) for item in body["exit_order_ids"]),
    )


def _history_digest(domain: bytes, body: object) -> str:
    return EvidenceContentDigest.from_bytes(domain + b"\0" + canonical_json(body)).value


class CohortHistory:
    """Checkpointed, append-only S4-11 cohort admission history."""

    def __init__(self, root: Path, *, journal_identity: str, epoch: str,
                 checkpoint: JournalCheckpoint | None, trust: TrustContext) -> None:
        if not isinstance(root, Path) or root.is_symlink():
            raise MeanReversionError("cohort history root is unsafe")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)
        self._identity = journal_identity
        self._epoch = epoch
        self._checkpoint = checkpoint
        self._trust = trust
        if trust.assurance_mode is not AssuranceMode.ENGINEERING_SIMULATION:
            raise MeanReversionError("cohort trust must be engineering-only")
        self.recover()

    def _paths(self) -> list[Path]:
        unexpected = [p for p in self._root.iterdir() if p.suffix != ".json" and p.name != ".cohort.lock"]
        if unexpected:
            raise MeanReversionError("cohort history contains incomplete publication")
        return sorted(self._root.glob("*.json"))

    def recover(self, *, continuity_verified: bool = False) -> CohortHistoryState:
        prior: str | None = None
        cohorts: list[MeanReversionCohort] = []
        prepared: MeanReversionCohort | None = None
        prepared_state_digest: str | None = None
        recovery_required = False
        for sequence, path in enumerate(self._paths(), 1):
            if path.name != f"{sequence:08d}.json" or path.is_symlink():
                raise MeanReversionError("cohort history is missing or reordered")
            raw = path.read_bytes()
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MeanReversionError("cohort history is unreadable") from error
            if type(record) is not dict or canonical_json(record) != raw:
                raise MeanReversionError("cohort record is not canonical")
            digest = record.get("content_digest")
            body = dict(record)
            body.pop("content_digest", None)
            if (
                set(record) != {"journal_identity", "epoch", "sequence", "state", "cohort", "simulation_state_digest", "previous_digest", "content_digest"}
                or record["journal_identity"] != self._identity
                or record["epoch"] != self._epoch
                or record["sequence"] != sequence
                or record["previous_digest"] != prior
                or digest != _history_digest(b"ATIS-S4-11-COHORT-HISTORY-V1", body)
            ):
                raise MeanReversionError("cohort history identity or chain is invalid")
            cohort = _cohort_from_body(record["cohort"])
            state = AdmissionState(record["state"])
            if state is AdmissionState.PREPARED:
                if prepared is not None or any(item.research_run_id == cohort.research_run_id for item in cohorts):
                    raise MeanReversionError("conflicting cohort preparation")
                prepared = cohort
                prepared_state_digest = str(record["simulation_state_digest"])
            elif state is AdmissionState.COMMITTED:
                if prepared != cohort:
                    raise MeanReversionError("cohort commit lacks exact preparation")
                cohorts.append(cohort)
                prepared = None
                prepared_state_digest = None
            elif state is AdmissionState.RECOVERY_REQUIRED:
                if prepared != cohort:
                    raise MeanReversionError("recovery marker lacks exact preparation")
                prepared = None
                prepared_state_digest = None
                recovery_required = True
            else:
                raise MeanReversionError("invalid persisted admission state")
            prior = str(digest)
        result = CohortHistoryState(
            len(self._paths()), prior,
            AdmissionState.RECOVERY_REQUIRED if prepared or recovery_required else (AdmissionState.COMMITTED if cohorts else None),
            tuple(cohorts), prepared, prepared_state_digest,
        )
        if continuity_verified:
            self.require_checkpointed(result)
        return result

    def require_checkpointed(self, state: CohortHistoryState | None = None) -> None:
        value = self.recover() if state is None else state
        checkpoint = self._checkpoint
        if checkpoint is None:
            raise MeanReversionError("trusted cohort checkpoint is NOT_ESTABLISHED")
        self._trust.require_checkpoint(checkpoint)
        if (
            checkpoint.journal_identity != self._identity
            or checkpoint.epoch != self._epoch
            or checkpoint.establishment_identity != self._trust.identity
            or checkpoint.accepted_sequence != value.sequence
            or checkpoint.terminal_digest != value.terminal_digest
        ):
            raise MeanReversionError("cohort checkpoint mismatch")
        if value.admission_state is AdmissionState.RECOVERY_REQUIRED:
            raise MeanReversionError("cohort admission recovery is required")

    def checkpoint_candidate(self, *, establishment_identity: str) -> JournalCheckpoint:
        state = self.recover()
        return JournalCheckpoint.create(journal_identity=self._identity, epoch=self._epoch, accepted_sequence=state.sequence, terminal_digest=state.terminal_digest, establishment_identity=establishment_identity)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        lock = self._root / ".cohort.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise MeanReversionError("cohort admission writer is already active") from error
        os.close(descriptor)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def _append(self, state: AdmissionState, cohort: MeanReversionCohort, simulation_state_digest: str) -> None:
        prior = self.recover()
        body = {
            "journal_identity": self._identity,
            "epoch": self._epoch,
            "sequence": prior.sequence + 1,
            "state": state.value,
            "cohort": _cohort_body(cohort),
            "simulation_state_digest": simulation_state_digest,
            "previous_digest": prior.terminal_digest,
        }
        record = dict(body)
        record["content_digest"] = _history_digest(b"ATIS-S4-11-COHORT-HISTORY-V1", body)
        target = self._root / f"{prior.sequence + 1:08d}.json"
        temporary = self._root / f".{target.name}.{os.getpid()}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(canonical_json(record))
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise MeanReversionError("conflicting cohort successor")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    def admit(self, simulation: SimulationStateJournal, cohort: MeanReversionCohort,
              calendar_days: tuple[CalendarSessionResolutionResult, ...], as_of: Timestamp) -> CohortHistoryState:
        if type(simulation) is not SimulationStateJournal or type(cohort) is not MeanReversionCohort:
            raise TypeError("authoritative simulation journal and cohort required")
        with simulation.exclusive_snapshot(continuity_verified=True) as snapshot:
            with self._exclusive():
                history = self.recover(continuity_verified=True)
                if history.admission_state is AdmissionState.RECOVERY_REQUIRED:
                    raise MeanReversionError("cohort admission recovery is required")
                if any(item.research_run_id == cohort.research_run_id for item in history.cohorts):
                    raise MeanReversionError("research run already owns a cohort")
                if history.cohorts:
                    prior = history.cohorts[-1]
                    require_prior_cohort_closed(assess_cohort(prior, snapshot.oms, snapshot.ledger, calendar_days, as_of))
                if not cohort.entry_order_ids:
                    raise MeanReversionError("empty cohort cannot be admitted")
                _cohort_order_records(cohort, snapshot.oms)
                if reconcile(snapshot.oms, snapshot.ledger).state is not ReconciliationState.RECONCILED:
                    raise MeanReversionError("simulation state is not reconciled")
                self._append(AdmissionState.PREPARED, cohort, snapshot.state_digest)
                self._append(AdmissionState.COMMITTED, cohort, snapshot.state_digest)
                return self.recover()

    def recover_prepared(
        self,
        simulation: SimulationStateJournal,
        calendar_days: tuple[CalendarSessionResolutionResult, ...],
        as_of: Timestamp,
    ) -> CohortHistoryState:
        """Deterministically commit an intact prepared admission or mark it blocked."""
        with simulation.exclusive_snapshot(continuity_verified=True) as snapshot:
            with self._exclusive():
                history = self.recover()
                pending = history.pending_cohort
                if pending is None or history.pending_simulation_state_digest is None:
                    raise MeanReversionError("no prepared cohort admission exists")
                checkpoint = self._checkpoint
                prepared_path = self._root / f"{history.sequence:08d}.json"
                prepared_record = json.loads(prepared_path.read_bytes())
                if checkpoint is None:
                    raise MeanReversionError("trusted predecessor checkpoint is NOT_ESTABLISHED")
                self._trust.require_checkpoint(checkpoint)
                if (
                    checkpoint.journal_identity != self._identity
                    or checkpoint.epoch != self._epoch
                    or checkpoint.establishment_identity != self._trust.identity
                    or checkpoint.accepted_sequence != history.sequence - 1
                    or checkpoint.terminal_digest != prepared_record["previous_digest"]
                ):
                    raise MeanReversionError("prepared admission base is not checkpointed")
                valid = history.pending_simulation_state_digest == snapshot.state_digest
                try:
                    _cohort_order_records(pending, snapshot.oms)
                    if reconcile(snapshot.oms, snapshot.ledger).state is not ReconciliationState.RECONCILED:
                        valid = False
                    if history.cohorts:
                        prior = history.cohorts[-1]
                        require_prior_cohort_closed(
                            assess_cohort(prior, snapshot.oms, snapshot.ledger, calendar_days, as_of)
                        )
                except MeanReversionError:
                    valid = False
                self._append(
                    AdmissionState.COMMITTED if valid else AdmissionState.RECOVERY_REQUIRED,
                    pending,
                    snapshot.state_digest,
                )
                result = self.recover()
                if not valid:
                    raise MeanReversionError("prepared admission outcome requires owner recovery")
                return result

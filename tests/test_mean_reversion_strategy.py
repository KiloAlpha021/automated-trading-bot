from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.calendars import (
    CalendarContentDigest,
    CalendarId,
    CalendarSessionResolutionReason,
    CalendarSessionResolutionResult,
    CalendarVersionId,
    LocalWallTime,
    SessionDefinition,
    SessionKind,
    SessionScheduleVariation,
    TimezoneId,
    TimezoneRuleId,
    TimezoneRuleRef,
    TradingDayDefinition,
    TradingDayKind,
)
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    ResolutionDisposition,
    SourceId,
    ValidationPolicyId,
    ValidationState,
    ListingId,
)
from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
    ClaimCeiling,
    DIMENSIONS,
    DatasetQualification,
    DimensionAssessment,
    EligibleCoverage,
    QualificationLevel,
    QualificationPolicy,
    qualify_dataset,
)
from automated_trading_bot.research.evidence import EvidenceDisposition, ResearchEvidenceManifest
from automated_trading_bot.research.identity import (
    Experiment,
    Hypothesis,
    ResearchProject,
    ResearchRun,
)
from automated_trading_bot.research.mean_reversion_strategy import (
    CohortState,
    AdmissionState,
    CohortHistory,
    MeanReversionCandidate,
    MeanReversionCohort,
    MeanReversionError,
    SessionReturnEndpoint,
    StrategyAuthority,
    assess_cohort,
    construct_exit_intents,
    construct_mean_reversion_portfolio,
    decision_and_execution_sessions,
    require_prior_cohort_closed,
)
from automated_trading_bot.research.pit_features import (
    AvailabilityPolicy,
    FeatureDefinition,
    EligibilityState,
    InputState,
    MissingDataPolicy,
    PitInput,
    assess_decision_input,
    create_feature_observation,
)
from automated_trading_bot.research.reproducibility import (
    PrngIdentity, PrngUsage, ReproducibilityRecord, RuntimeIdentity,
    SourceIdentity, WorkingTreeState,
)
from automated_trading_bot.research.research_governance import (
    ApprovalEvidence, AssuranceMode, FrozenResearchPlan, JournalError,
    PartitionRole, PitEligibilityEvidence, ResearchGovernanceError,
    ResearchGovernor, ResearchJournal,
)
from automated_trading_bot.datasets.provenance import canonical_json
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
from automated_trading_bot.research.simulation_state import SimulationStateJournal
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationState, reconcile,
)


USD = Currency("USD")
DIGEST = EvidenceContentDigest("sha256:" + "9" * 64)
CALENDAR_ID = CalendarId(UUID("00000000-0000-4000-8000-000000000100"))
VERSION_ID = CalendarVersionId(UUID("00000000-0000-4000-8000-000000000001"))
POLICY = ValidationPolicyId("validation-policy:atis/us-market-calendar-v1")
CORPUS = b"qualified-us-market-calendar"
TZ_EVIDENCE = EvidenceRef(
    SourceId("source:atis/calendar"),
    DatasetId("dataset:atis/calendar"),
    EvidenceId("evidence:atis/calendar-tz"),
    EvidenceContentDigest.from_bytes(CORPUS),
)
TZ = TimezoneRuleRef(
    TimezoneId("America/New_York"),
    TimezoneRuleId("timezone-rule:atis/america-new-york-v1"),
    EvidenceContentDigest.from_bytes(CORPUS),
    (TZ_EVIDENCE,),
    POLICY,
    ValidationState.VALID,
)
DECISION = Timestamp(datetime(2026, 10, 23, 21, 30, tzinfo=UTC))
RELEASE = Timestamp(datetime(2026, 10, 26, 14, 30, tzinfo=UTC))
HOLDING_AS_OF = Timestamp(datetime(2026, 10, 30, 21, 30, tzinfo=UTC))


def ts(day: date, hour: int, minute: int = 0) -> Timestamp:
    return Timestamp(datetime.combine(day, time(hour, minute), tzinfo=UTC))


def instrument(index: int) -> InstrumentId:
    return InstrumentId(UUID(f"00000000-0000-4000-8000-{index:012d}"))


def calendar_result(
    day: date,
    kind: TradingDayKind = TradingDayKind.REGULAR_TRADING_DAY,
) -> CalendarSessionResolutionResult:
    evidence = EvidenceRef(
        SourceId("source:atis/calendar"),
        DatasetId("dataset:atis/calendar"),
        EvidenceId(f"evidence:atis/calendar-{day.isoformat()}"),
        EvidenceContentDigest.from_bytes(day.isoformat().encode()),
    )
    trading_day = TradingDayDefinition(
        CALENDAR_ID, VERSION_ID, day, kind, (evidence,), POLICY, ValidationState.VALID
    )
    if kind is TradingDayKind.REGULAR_TRADING_DAY:
        local_open = LocalWallTime(14, 0, 0, 0, 0, False, False, None)
        local_close = LocalWallTime(21, 0, 0, 0, 0, False, False, None)
        session = SessionDefinition(
            CALENDAR_ID,
            VERSION_ID,
            day,
            SessionKind.REGULAR,
            SessionScheduleVariation.STANDARD,
            local_open,
            local_close,
            ts(day, 14),
            ts(day, 21),
            TZ,
            (evidence,),
            POLICY,
            ValidationState.VALID,
        )
        return CalendarSessionResolutionResult(
            ResolutionDisposition.ESTABLISHED,
            CALENDAR_ID,
            day,
            SessionKind.REGULAR,
            ts(day, 12),
            ts(day, 22),
            ts(day, 23),
            VERSION_ID,
            trading_day,
            session,
            session.utc_open,
            session.utc_close,
            TZ.timezone_rule_id,
            TZ.rule_content_digest,
            CalendarContentDigest("sha256:" + "7" * 64),
            (),
            (evidence,),
            CalendarSessionResolutionReason.UNIQUE_AUTHORITATIVE_SESSION,
        )
    return CalendarSessionResolutionResult(
        ResolutionDisposition.ESTABLISHED,
        CALENDAR_ID,
        day,
        SessionKind.REGULAR,
        ts(day, 12),
        ts(day, 22),
        ts(day, 23),
        VERSION_ID,
        trading_day,
        None,
        None,
        None,
        None,
        None,
        None,
        (),
        (evidence,),
        CalendarSessionResolutionReason.AUTHORITATIVE_NON_TRADING_DAY,
    )


def calendar_days(*, friday_holiday: bool = False, through_holding: bool = False):
    days = []
    current = date(2026, 10, 15) if friday_holiday else date(2026, 10, 16)
    end = date(2026, 10, 30) if through_holding else date(2026, 10, 26)
    while current <= end:
        if current.weekday() < 5:
            kind = (
                TradingDayKind.EXCHANGE_HOLIDAY
                if friday_holiday and current == date(2026, 10, 23)
                else TradingDayKind.REGULAR_TRADING_DAY
            )
            days.append(calendar_result(current, kind))
        current += timedelta(days=1)
    return tuple(days)


def qualification() -> DatasetQualification:
    return qualify_dataset(
        dataset_id="dataset:atis/mr",
        dataset_version="v1",
        manifest_digest="sha256:" + "1" * 64,
        provenance_digest="sha256:" + "2" * 64,
        policy=QualificationPolicy("policy", "v1", "evidence"),
        requested_level=QualificationLevel.G1,
        assessments=tuple(
            DimensionAssessment(value, AssessmentState.PASS, (f"evidence:{value.lower()}",))
            for value in DIMENSIONS
        ),
        coverage=EligibleCoverage(date(2026, 1, 1), date(2026, 12, 31)),
    )


def identity_context():
    reproducibility = ReproducibilityRecord.create(
        source=SourceIdentity("1" * 40, "2" * 40, WorkingTreeState.CLEAN),
        runtime=RuntimeIdentity("3.12.10", "python.exe", "Windows", "AMD64"),
        dependency_lock_digest="3" * 64,
        configuration={"strategy": "MR-001-R1"},
        prng=PrngIdentity(PrngUsage.NONE),
        dataset_identities=("dataset:atis/mr:v1",),
    )
    project = ResearchProject.create("MR-001-R1 engineering")
    hypothesis = Hypothesis.create(project.project_id, "mean reversion engineering")
    experiment = Experiment.create(
        hypothesis.hypothesis_id, reproducibility.configuration_digest, "v1", "synthetic fixtures"
    )
    run = ResearchRun.create(experiment.experiment_id, reproducibility.digest)
    return project, hypothesis, experiment, run, reproducibility


def run() -> ResearchRun:
    return identity_context()[3]


@dataclass(frozen=True, slots=True)
class EngineeringTrust:
    approval_digest: str
    checkpoint_digest: str | None = None
    identity: str = "test-trust:s4-11"
    assurance_mode: AssuranceMode = AssuranceMode.ENGINEERING_SIMULATION

    def require_approval(self, evidence, plan):
        evidence.validate_for(plan)
        if evidence.evidence_digest != self.approval_digest:
            raise ResearchGovernanceError("approval is not established")

    def require_checkpoint(self, checkpoint):
        if checkpoint.checkpoint_digest != self.checkpoint_digest:
            raise JournalError("checkpoint is not established")


@dataclass(frozen=True, slots=True)
class CheckpointTrust:
    checkpoint_digests: frozenset[str]
    identity: str = "test-trust:s4-11-state"
    assurance_mode: AssuranceMode = AssuranceMode.ENGINEERING_SIMULATION

    def require_approval(self, evidence, plan):
        raise AssertionError("approval is outside state fixture")

    def require_checkpoint(self, checkpoint):
        if checkpoint.checkpoint_digest not in self.checkpoint_digests:
            raise JournalError("checkpoint is not established")


_CTX = None


@pytest.fixture(autouse=True)
def authority_context(tmp_path):
    global _CTX
    project, hypothesis, experiment, current_run, reproducibility = identity_context()
    q = qualification()
    plan = FrozenResearchPlan.create(
        hypothesis=hypothesis, experiment=experiment,
        configuration_digest=reproducibility.configuration_digest,
        parameter_domain=("window=5",), objective="engineering falsification",
        dataset_id=q.dataset_id, dataset_version=q.dataset_version,
        partition=PartitionRole.DEVELOPMENT, maximum_admitted_trials=1,
        owner_approval_reference="owner:s4-11", policy_identity="policy:s4-11",
    )
    approval = ApprovalEvidence.create(
        plan=plan, actor="owner:test", authoritative_source="test:fixture",
        task_id="ATIS-S4-11", operation="S4_08_PLAN_APPROVAL",
        approval_reference=plan.owner_approval_reference,
    )
    trust = EngineeringTrust(approval.evidence_digest)
    journal = ResearchJournal(tmp_path / "governance", trust=trust)
    governor = ResearchGovernor(journal, trust)
    governor.accept_plan(plan, approval)
    governor.admit(plan, current_run)
    checkpoint = journal.checkpoint_candidate(establishment_identity=trust.identity)
    trusted = EngineeringTrust(approval.evidence_digest, checkpoint.checkpoint_digest)
    governor = ResearchGovernor(ResearchJournal(journal.root, checkpoint=checkpoint, trust=trusted), trusted)
    _CTX = (project, hypothesis, experiment, current_run, reproducibility, q, plan, governor)
    yield
    _CTX = None


def value_digest(domain: bytes, value: Decimal) -> str:
    canonical = "0" if value == 0 else format(value.normalize(), "f")
    return EvidenceContentDigest.from_bytes(domain + b"\0" + canonical_json({"value": canonical})).value


def pit(index: int, session: date, value: Decimal, domain: bytes, decision: Timestamp) -> PitEligibilityEvidence:
    assert _CTX is not None
    q, plan, current_run = _CTX[5], _CTX[6], _CTX[3]
    definition = FeatureDefinition.create(
        version="v1", algorithm_id="algorithm:atis/mr-value",
        configuration_digest=plan.configuration_digest,
        required_inputs=("value",), lookback=1, minimum_history=1,
        missing_data_policy=MissingDataPolicy.REJECT,
        availability_policy=AvailabilityPolicy.AT_KNOWLEDGE,
        output_semantics="decimal:value",
    )
    available = ts(session, 21)
    item = PitInput(
        f"input:mr:{index}:{session.isoformat()}:{domain.decode()}", "value", q.dataset_id,
        "source:atis/synthetic", DIGEST.value, available, available, InputState.VALID,
    )
    observation = create_feature_observation(
        definition=definition, instrument_id=instrument(index),
        listing_id=ListingId(UUID(f"10000000-0000-4000-8000-{index:012d}")),
        qualification=q, inputs=(item,), available_at=available,
        value_digest=value_digest(domain, value),
    )
    eligibility = assess_decision_input(
        definition=definition, observation=observation, qualification=q,
        inputs=(item,), decision_time=decision, requested_claim=ClaimCeiling.ENGINEERING_ONLY,
    )
    return PitEligibilityEvidence(current_run.run_id, definition, observation, (item,), decision, ClaimCeiling.ENGINEERING_ONLY, eligibility)


def candidates(
    count: int = 5,
    *,
    start_date: date = date(2026, 10, 16),
    end_date: date = date(2026, 10, 23),
    decision: Timestamp = DECISION,
) -> tuple[MeanReversionCandidate, ...]:
    values = []
    for index in range(1, count + 1):
        iid = instrument(index)
        start_pit = pit(index, start_date, Decimal("100"), b"ATIS-S4-11-TOTAL-RETURN-INDEX-V1", decision)
        start = SessionReturnEndpoint(
            iid,
            start_date,
            Decimal("100"),
            start_pit.observation.available_at,
            "dataset:atis/mr",
            "v1",
            start_pit,
        )
        end_value = Decimal(94 + index)
        end_pit = pit(index, end_date, end_value, b"ATIS-S4-11-TOTAL-RETURN-INDEX-V1", decision)
        end = SessionReturnEndpoint(
            iid,
            end_date,
            end_value,
            end_pit.observation.available_at,
            "dataset:atis/mr",
            "v1",
            end_pit,
        )
        values.append(MeanReversionCandidate(iid, start, end, Decimal("100"), pit(index, end_date, Decimal("100"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", decision)))
    return tuple(values)


def build(
    values: tuple[MeanReversionCandidate, ...] | None = None,
    *,
    days: tuple[CalendarSessionResolutionResult, ...] | None = None,
    decision: Timestamp = DECISION,
    release: Timestamp = RELEASE,
    **kwargs: object,
):
    assert _CTX is not None
    selected = candidates(decision=decision) if values is None else values
    inputs = []
    for candidate in selected:
        for evidence in (candidate.start.pit_evidence, candidate.end.pit_evidence, candidate.allocation_price_evidence):
            inputs.extend((evidence.observation.observation_id, *(item.input_id for item in evidence.inputs)))
    manifest = ResearchEvidenceManifest.create(
        project_id=_CTX[0].project_id, hypothesis_id=_CTX[1].hypothesis_id,
        experiment_id=_CTX[2].experiment_id, run_id=_CTX[3].run_id,
        reproducibility=_CTX[4], input_identities=tuple(sorted(set(inputs))),
        output_identities=("output:mr-001-r1",), disposition=EvidenceDisposition.VALID,
    )
    return construct_mean_reversion_portfolio(
        candidates=selected,
        qualification=_CTX[5], qualification_predecessors=(),
        research_run=_CTX[3], plan=_CTX[6], manifest=manifest, governor=_CTX[7],
        calendar_days=calendar_days() if days is None else days,
        decision_time=decision,
        release_time=release,
        **kwargs,
    )


def test_a01_five_completed_session_return_no_skip() -> None:
    result = build()
    assert result.targets[0].signal == Decimal("-0.05")
    missing = tuple(
        value for value in calendar_days() if value.local_date != date(2026, 10, 20)
    )
    with pytest.raises(MeanReversionError, match="weekday calendar evidence"):
        build(days=missing)


def test_a02_friday_and_holiday_fallback() -> None:
    direct, execution = decision_and_execution_sessions(
        calendar_days(), DECISION, RELEASE
    )
    assert direct.local_date == date(2026, 10, 23)
    assert execution.local_date == date(2026, 10, 26)
    holiday_decision = Timestamp(datetime(2026, 10, 23, 21, 30, tzinfo=UTC))
    holiday_candidates = candidates(
        start_date=date(2026, 10, 15),
        end_date=date(2026, 10, 22),
        decision=holiday_decision,
    )
    result = build(
        holiday_candidates,
        days=calendar_days(friday_holiday=True),
        decision=holiday_decision,
    )
    assert result.decision_session_date == date(2026, 10, 22)


def test_a03_calendar_missing_conflicting_or_unknown_fails_closed() -> None:
    bad = replace(
        calendar_days()[0],
        disposition=ResolutionDisposition.NOT_ESTABLISHED,
        selected_calendar_version_id=None,
        trading_day=None,
        session=None,
        utc_open=None,
        utc_close=None,
        timezone_rule_id=None,
        timezone_rule_content_digest=None,
        currentness_assessment_digest=None,
        evidence_refs=(),
        reason_code=CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_MISSING,
    )
    with pytest.raises(MeanReversionError, match="not established"):
        build(days=(bad,) + calendar_days()[1:])
    other = replace(
        calendar_days()[1],
        selected_calendar_version_id=CalendarVersionId(
            UUID("00000000-0000-4000-8000-000000000002")
        ),
    )
    with pytest.raises(MeanReversionError, match="inconsistent"):
        build(days=(calendar_days()[0], other) + calendar_days()[2:])


def test_a04_future_data_and_same_close_execution_rejected() -> None:
    first = candidates()[0]
    future = replace(
        first,
        end=replace(
            first.end, available_at=Timestamp(datetime(2026, 10, 24, tzinfo=UTC))
        ),
    )
    with pytest.raises(MeanReversionError, match="PIT identity|future-known"):
        build((future,) + candidates()[1:])
    with pytest.raises(MeanReversionError, match="next eligible session"):
        build(release=Timestamp(datetime(2026, 10, 24, tzinfo=UTC)))


def test_a05_pit_and_dataset_qualification_bound() -> None:
    first = candidates()[0]
    bad_decision = replace(first.end.pit_evidence.eligibility, state=EligibilityState.INELIGIBLE)
    bad_pit = replace(first.end.pit_evidence, eligibility=bad_decision)
    with pytest.raises(ResearchGovernanceError, match="PIT eligibility"):
        build(
            (replace(first, end=replace(first.end, pit_evidence=bad_pit)),)
            + candidates()[1:]
        )
    with pytest.raises(MeanReversionError, match="dataset substitution"):
        build(
            (replace(first, start=replace(first.start, dataset_version="v2")),)
            + candidates()[1:]
        )


def test_a06_ranking_ties_use_canonical_instrument_id() -> None:
    values = list(candidates())
    replacement_pit = pit(2, date(2026, 10, 23), Decimal("95"), b"ATIS-S4-11-TOTAL-RETURN-INDEX-V1", DECISION)
    values[1] = replace(
        values[1], end=replace(values[1].end, total_return_index=Decimal("95"), pit_evidence=replacement_pit)
    )
    assert build(tuple(reversed(values))).targets[0].instrument_id == instrument(1)


def test_a07_floor_rounding_and_zero_selection_cash() -> None:
    assert build().selected_count == 1
    result = build(candidates(4))
    assert result.selected_count == 0
    assert result.targets == result.entry_intents == ()
    assert result.residual_cash == Decimal("100000")


def test_a08_equal_weight_whole_shares_and_residual_cash() -> None:
    result = build(candidates(10))
    assert {target.weight for target in result.targets} == {Decimal("0.5")}
    assert all(type(target.whole_shares) is int for target in result.targets)
    assert result.residual_cash == 0


def test_a09_unaffordable_or_invalid_price_creates_no_executable_intent() -> None:
    first = replace(candidates()[0], allocation_price=Decimal("200000"), allocation_price_evidence=pit(1, date(2026, 10, 23), Decimal("200000"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", DECISION))
    result = build((first,) + candidates()[1:])
    assert result.targets == result.entry_intents == ()
    assert result.unfunded_instruments == (instrument(1),)
    with pytest.raises(MeanReversionError, match="positive finite"):
        build((replace(first, allocation_price=Decimal("NaN")),) + candidates()[1:])


def cost_model(scenario: CostScenario = CostScenario.EC1) -> ExecutionCostModel:
    rate = Decimal("0") if scenario is CostScenario.EC0 else Decimal("1")
    rates = tuple(
        CostRate(component, rate, CostProvenance.MODELLED, DIGEST)
        for component in CostComponent
    )
    return ExecutionCostModel(
        f"atis:simulation-cost-model:v1:{scenario.value.lower()}", scenario, rates,
        scenario is CostScenario.EC0,
    )


def funded_ledger(current_run: ResearchRun) -> SimulationLedger:
    return SimulationLedger.empty(USD).apply(
        FinancialEvent(
            LedgerEventId("atis:simulation-ledger-event:v1:opening-mr"),
            1,
            Timestamp(datetime(2026, 10, 23, 22, tzinfo=UTC)),
            FinancialEventKind.OPENING_CASH,
            current_run.run_id,
            DIGEST,
            currency=USD,
            cash_delta=Decimal("100000"),
        )
    )


def execution_snapshot(day: date, quantity: str) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        ts(day, 15),
        Decimal("100"),
        ts(day, 14, 45),
        ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE,
        Decimal(quantity),
        USD,
        DIGEST,
    )


def filled_entry(*, first: str = "4", second: str = "6"):
    current_run = run()
    order = create_order(
        instrument_id=instrument(1),
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        currency=USD,
        release_at=RELEASE,
        research_run_id=current_run.run_id,
        evidence_digest=DIGEST,
        eligibility=pit(1, date(2026, 10, 23), Decimal("100"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", DECISION).eligibility,
    )
    oms = SimulatedOms().submit(order)
    ledger = funded_ledger(current_run)
    oms = oms.execute(
        order.order_id,
        Decimal(first),
        execution_snapshot(date(2026, 10, 26), first),
        cost_model(),
    )
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    if Decimal(second) > 0:
        oms = oms.execute(
            order.order_id,
            Decimal(second),
            execution_snapshot(date(2026, 10, 27), second),
            cost_model(),
        )
        ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    cohort = MeanReversionCohort(
        "mr-cohort-1", current_run.run_id.value, (instrument(1),), (order.order_id,)
    )
    return current_run, order, oms, ledger, cohort


def test_a10_a11_first_fill_clock_and_later_partial_fill_no_reset() -> None:
    _, _, oms, ledger, cohort = filled_entry()
    assessment = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert assessment.clocks[0].first_fill_session == date(2026, 10, 26)
    assert assessment.clocks[0].completed_sessions == 5


def test_a12_exit_only_after_five_sessions() -> None:
    _, _, oms, ledger, cohort = filled_entry()
    early_days = tuple(
        value
        for value in calendar_days(through_holding=True)
        if value.local_date <= date(2026, 10, 29)
    )
    assert (
        construct_exit_intents(
            cohort,
            oms,
            ledger,
            early_days,
            Timestamp(datetime(2026, 10, 29, 21, 30, tzinfo=UTC)),
        )
        == ()
    )
    intents = construct_exit_intents(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert len(intents) == 1 and intents[0].side is OrderSide.SELL


def exit_order(current_run: ResearchRun, quantity: str = "10"):
    return create_order(
        instrument_id=instrument(1),
        side=OrderSide.SELL,
        quantity=Decimal(quantity),
        currency=USD,
        release_at=Timestamp(datetime(2026, 11, 2, 14, 30, tzinfo=UTC)),
        research_run_id=current_run.run_id,
        evidence_digest=DIGEST,
        eligibility=pit(2, date(2026, 10, 23), Decimal("100"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", DECISION).eligibility,
    )


def test_a13_exit_intent_does_not_fabricate_fill_or_cash() -> None:
    _, _, oms, ledger, cohort = filled_entry()
    before = ledger.state
    assert construct_exit_intents(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert ledger.state == before and len(oms.fills) == 2


@pytest.mark.parametrize("outcome", ["cancelled", "unfilled"])
def test_a14_failed_exit_retains_active_cohort(outcome: str) -> None:
    current_run, _, oms, ledger, cohort = filled_entry()
    sell = exit_order(current_run)
    oms = oms.submit(sell)
    oms = (
        oms.cancel(sell.order_id)
        if outcome == "cancelled"
        else oms.mark_unfilled(sell.order_id, "NO_LIQUIDITY")
    )
    cohort = replace(cohort, exit_order_ids=(sell.order_id,))
    assessment = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert assessment.state is CohortState.ACTIVE
    assert "OPEN_LEDGER_POSITION" in assessment.reasons


def test_a15_partial_exit_preserves_residual_position() -> None:
    current_run, _, oms, ledger, cohort = filled_entry()
    sell = exit_order(current_run)
    oms = oms.submit(sell).execute(
        sell.order_id,
        Decimal("4"),
        execution_snapshot(date(2026, 11, 2), "4"),
        cost_model(),
    )
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    cohort = replace(cohort, exit_order_ids=(sell.order_id,))
    assessment = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert ledger.state.position(instrument(1)).quantity == Decimal("6")  # type: ignore[union-attr]
    assert assessment.state is CohortState.ACTIVE


def test_a16_outstanding_entry_blocks_closure() -> None:
    _, _, oms, ledger, cohort = filled_entry(second="0")
    assessment = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert not assessment.entry_orders_resolved
    assert "OUTSTANDING_ENTRY_ORDER" in assessment.reasons


def test_a17_reconciled_zero_position_and_resolved_orders_close() -> None:
    current_run, _, oms, ledger, cohort = filled_entry()
    sell = exit_order(current_run)
    oms = oms.submit(sell).execute(
        sell.order_id,
        Decimal("10"),
        execution_snapshot(date(2026, 11, 2), "10"),
        cost_model(),
    )
    ledger = post_fill_to_ledger(ledger, oms.fills[-1])
    cohort = replace(cohort, exit_order_ids=(sell.order_id,))
    assessment = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    assert assessment.state is CohortState.CLOSED
    assert assessment.ledger_flat and assessment.oms_ledger_reconciled


def test_a17_unreconciled_oms_and_ledger_fails_closed() -> None:
    _, _, oms, ledger, cohort = filled_entry()
    broken = funded_ledger(run())
    with pytest.raises(MeanReversionError, match="do not reconcile"):
        assess_cohort(
            cohort, oms, broken, calendar_days(through_holding=True), HOLDING_AS_OF
        )


def test_a18_nonoverlapping_admission_requires_verified_closure() -> None:
    _, _, oms, ledger, cohort = filled_entry()
    active = assess_cohort(
        cohort, oms, ledger, calendar_days(through_holding=True), HOLDING_AS_OF
    )
    with pytest.raises(MeanReversionError, match="not verifiably closed"):
        require_prior_cohort_closed(active)
    with pytest.raises(MeanReversionError, match="trusted predecessor"):
        require_prior_cohort_closed(None)


def test_checkpointed_cohort_history_rejects_duplicate_run(tmp_path) -> None:
    current_run = run()
    order = create_order(
        instrument_id=instrument(1), side=OrderSide.BUY, quantity=Decimal("1"),
        currency=USD, release_at=RELEASE, research_run_id=current_run.run_id,
        evidence_digest=DIGEST,
        eligibility=pit(1, date(2026, 10, 23), Decimal("100"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", DECISION).eligibility,
    )
    empty_trust = CheckpointTrust(frozenset())
    sim_root = tmp_path / "simulation"
    sim = SimulationStateJournal(
        sim_root, journal_identity="simulation:mr", epoch="epoch:1",
        opening_cash=Decimal("100000"), currency=USD,
        research_run_id=current_run.run_id, opened_at=ts(date(2026, 10, 23), 22),
        evidence_digest=DIGEST, trust=empty_trust,
    )
    sim.submit("submit:1", order)
    sim_checkpoint = sim.checkpoint_candidate(establishment_identity=empty_trust.identity)
    history_root = tmp_path / "cohorts"
    history = CohortHistory(history_root, journal_identity="cohorts:mr", epoch="epoch:1", checkpoint=None, trust=empty_trust)
    history_checkpoint = history.checkpoint_candidate(establishment_identity=empty_trust.identity)
    trust = CheckpointTrust(frozenset({sim_checkpoint.checkpoint_digest, history_checkpoint.checkpoint_digest}))
    sim = SimulationStateJournal(
        sim_root, journal_identity="simulation:mr", epoch="epoch:1",
        opening_cash=Decimal("100000"), currency=USD,
        research_run_id=current_run.run_id, opened_at=ts(date(2026, 10, 23), 22),
        evidence_digest=DIGEST, checkpoint=sim_checkpoint, trust=trust,
    )
    history = CohortHistory(history_root, journal_identity="cohorts:mr", epoch="epoch:1", checkpoint=history_checkpoint, trust=trust)
    cohort = MeanReversionCohort("cohort:1", current_run.run_id.value, (instrument(1),), (order.order_id,))
    history._append(AdmissionState.PREPARED, cohort, sim.recover().state_digest)
    assert history.recover().admission_state is AdmissionState.RECOVERY_REQUIRED
    committed = history.recover_prepared(sim, calendar_days(through_holding=True), HOLDING_AS_OF)
    accepted_checkpoint = history.checkpoint_candidate(establishment_identity=trust.identity)
    accepted_trust = CheckpointTrust(frozenset({sim_checkpoint.checkpoint_digest, accepted_checkpoint.checkpoint_digest}))
    accepted = CohortHistory(history_root, journal_identity="cohorts:mr", epoch="epoch:1", checkpoint=accepted_checkpoint, trust=accepted_trust)
    assert committed.cohorts == (cohort,)
    with pytest.raises(MeanReversionError, match="already owns"):
        accepted.admit(sim, cohort, calendar_days(through_holding=True), HOLDING_AS_OF)


def test_a19_existing_ec0_ec3_models_are_reused() -> None:
    assert build().cost_scenarios == tuple(CostScenario)
    for scenario in CostScenario:
        current_run = run()
        order = create_order(
            instrument_id=instrument(1), side=OrderSide.BUY, quantity=Decimal("1"),
            currency=USD, release_at=RELEASE, research_run_id=current_run.run_id,
            evidence_digest=DIGEST,
            eligibility=pit(1, date(2026, 10, 23), Decimal("100"), b"ATIS-S4-11-DECISION-CLOSE-PRICE-V1", DECISION).eligibility,
        )
        oms = SimulatedOms().submit(order).execute(
            order.order_id, Decimal("1"),
            execution_snapshot(date(2026, 10, 26), "1"), cost_model(scenario),
        )
        ledger = post_fill_to_ledger(funded_ledger(current_run), oms.fills[0])
        result = reconcile(oms, ledger)
        assert result.state is ReconciliationState.RECONCILED
        assert result.performance is not None


def test_a20_deterministic_replay() -> None:
    assert build() == build(tuple(reversed(candidates())))


def test_a21_research_only_h6_and_holdout_excluded() -> None:
    result = build()
    assert result.authority is StrategyAuthority.RESEARCH_ONLY
    assert result.economic_edge == "NOT_ESTABLISHED"
    assert not hasattr(result, "broker") and not hasattr(result, "holdout")
    contract = json.loads(
        (
            Path(__file__).parents[1] / "docs/stage4/s4-11-mean-reversion-contract.json"
        ).read_text()
    )
    assert "H6-R1" in contract["prohibitions"]


def test_a22_failure_is_explicit_and_preserves_inputs() -> None:
    values = candidates()
    before = repr(values)
    with pytest.raises(MeanReversionError, match="duplicate"):
        build((values[0], values[0], *values[2:]))
    assert repr(values) == before


def test_a23_scope_and_contract_are_exact() -> None:
    contract = json.loads(
        (
            Path(__file__).parents[1] / "docs/stage4/s4-11-mean-reversion-contract.json"
        ).read_text()
    )
    assert list(contract["acceptance_checks"]) == [
        f"A{index:02d}" for index in range(1, 24)
    ]
    assert contract["allocation"]["selection_count"] == "floor(0.20 * eligible_count)"
    assert contract["authority"]["scope"] == "RESEARCH_ONLY_ENGINEERING_SIMULATION"

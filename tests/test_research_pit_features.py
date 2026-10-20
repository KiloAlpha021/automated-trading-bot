from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import ListingId
from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
    ClaimCeiling,
    DIMENSIONS,
    DimensionAssessment,
    EligibleCoverage,
    QualificationLevel,
    QualificationPolicy,
    invalidate_qualification,
    qualify_dataset,
)
from automated_trading_bot.research.pit_features import (
    AvailabilityPolicy,
    EligibilityState,
    FeatureContractError,
    FeatureDefinition,
    InputState,
    MissingDataPolicy,
    NO_TRADING_AUTHORITY,
    PitInput,
    assess_decision_input,
    create_feature_observation,
)

D = "sha256:" + "1" * 64


def t(day, hour=12):
    return Timestamp(datetime(2020, 1, day, hour, tzinfo=timezone.utc))


def definition(**kw):
    values = dict(
        version="v1",
        algorithm_id="algorithm:atis/mean",
        configuration_digest=D,
        required_inputs=("close",),
        lookback=3,
        minimum_history=2,
        missing_data_policy=MissingDataPolicy.REJECT,
        availability_policy=AvailabilityPolicy.AT_KNOWLEDGE,
        output_semantics="decimal:return",
    )
    values.update(kw)
    return FeatureDefinition.create(**values)


def qualification(level=QualificationLevel.G2):
    policy = QualificationPolicy("policy:atis/q", "v1", "evidence:atis/q")
    states = tuple(
        DimensionAssessment(d, AssessmentState.PASS, ("evidence:atis/x",))
        for d in DIMENSIONS
    )
    if level is QualificationLevel.G1:
        return qualify_dataset(
            dataset_id="dataset:atis/prices",
            dataset_version="v1",
            manifest_digest=D,
            provenance_digest=D,
            policy=policy,
            requested_level=level,
            assessments=states,
            coverage=EligibleCoverage(date(2020, 1, 1), date(2020, 1, 31)),
        )
    g1 = qualification(QualificationLevel.G1)
    return qualify_dataset(
        dataset_id="dataset:atis/prices",
        dataset_version="v1",
        manifest_digest=D,
        provenance_digest=D,
        policy=policy,
        requested_level=level,
        assessments=states,
        coverage=EligibleCoverage(date(2020, 1, 1), date(2020, 1, 31)),
        predecessor=g1,
    )


def inputs(**kw):
    base = dict(
        dataset_id="dataset:atis/prices",
        source_identity="source:atis/vendor",
        evidence_digest=D,
        state=InputState.VALID,
        retrospective_correction=False,
    )
    return tuple(
        PitInput(
            f"input:atis/{i}",
            "close",
            **(base | {"event_time": t(i), "knowledge_time": t(i, 13)} | kw),
        )
        for i in (1, 2)
    )


def observation(q=None, ins=None, d=None, available=None):
    q = q or qualification()
    ins = ins or inputs()
    d = d or definition()
    return create_feature_observation(
        definition=d,
        instrument_id=InstrumentId(UUID("12345678-1234-4234-8234-123456789abc")),
        listing_id=ListingId(UUID("22345678-1234-4234-8234-123456789abc")),
        qualification=q,
        inputs=ins,
        available_at=available or t(2, 14),
        value_digest=D,
    )


def assess(
    q=None,
    ins=None,
    d=None,
    o=None,
    decision=None,
    claim=ClaimCeiling.EXPLORATORY_RESEARCH,
    **kw,
):
    q = q or qualification()
    ins = ins or inputs()
    d = d or definition()
    o = o or observation(q, ins, d)
    return assess_decision_input(
        definition=d,
        observation=o,
        qualification=q,
        inputs=ins,
        decision_time=decision or t(3),
        requested_claim=claim,
        **kw,
    )


def test_valid_temporal_order_and_g2_use():
    assert assess().state is EligibilityState.ELIGIBLE


def test_feature_after_decision_is_computable_but_ineligible():
    assert "FEATURE_AFTER_DECISION" in assess(o=observation(available=t(4))).reasons


def test_unknown_knowledge_fails_closed():
    assert "UNKNOWN_KNOWLEDGE_TIME" in assess(ins=inputs(knowledge_time=None)).reasons


def test_future_knowledge_fails_closed():
    assert "FUTURE_KNOWLEDGE" in assess(ins=inputs(knowledge_time=t(5))).reasons


def test_retrospective_correction_misuse_rejected():
    assert (
        "RETROSPECTIVE_CORRECTION_NOT_KNOWN"
        in assess(
            ins=inputs(knowledge_time=t(5), retrospective_correction=True)
        ).reasons
    )


def test_same_instant_requires_causal_boundary():
    assert (
        "UNSUPPORTED_SAME_INSTANT_USE"
        in assess(decision=t(2), o=observation(available=t(2))).reasons
    )


def test_same_instant_can_be_explicitly_established():
    ins = inputs()
    ins = (ins[0], replace(ins[1], knowledge_time=t(2)))
    assert (
        assess(
            ins=ins,
            decision=t(2),
            o=observation(ins=ins, available=t(2)),
            causal_same_instant_established=True,
        ).state
        is EligibilityState.ELIGIBLE
    )


def test_g1_rejects_research_claim():
    q = qualification(QualificationLevel.G1)
    assert "CLAIM_NOT_PERMITTED" in assess(q=q, o=observation(q=q)).reasons


def test_g3_permits_economic_validation_without_trading_authority():
    g2 = qualification()
    policy = g2.policy
    states = g2.assessments
    q = qualify_dataset(
        dataset_id=g2.dataset_id,
        dataset_version=g2.dataset_version,
        manifest_digest=D,
        provenance_digest=D,
        policy=policy,
        requested_level=QualificationLevel.G3,
        assessments=states,
        coverage=g2.coverage,
        predecessor=g2,
    )
    result = assess(q=q, o=observation(q=q), claim=ClaimCeiling.ECONOMIC_VALIDATION)
    assert (
        result.state is EligibilityState.ELIGIBLE and "NO_TRADING" in result.authority
    )


def test_coverage_exceeded_rejected():
    outside = Timestamp(datetime(2020, 2, 1, 12, tzinfo=timezone.utc))
    ins = tuple(
        replace(v, event_time=t(1) if i == 0 else outside)
        for i, v in enumerate(inputs())
    )
    q = qualification()
    assert (
        "OUTSIDE_QUALIFICATION_COVERAGE"
        in assess(q=q, ins=ins, o=observation(q, ins)).reasons
    )


def test_invalidated_qualification_rejected():
    q = invalidate_qualification(qualification(), ("bad evidence",))
    assert "QUALIFICATION_NOT_CURRENT" in assess(q=q, o=observation(q=q)).reasons


def test_dataset_substitution_rejected_during_observation():
    with pytest.raises(FeatureContractError, match="dataset substitution"):
        observation(ins=inputs(dataset_id="dataset:atis/other"))


def test_qualification_substitution_rejected():
    q = qualification()
    other = replace(q, qualification_id=q.qualification_id + "x")
    assert "QUALIFICATION_SUBSTITUTION" in assess(q=other, o=observation(q=q)).reasons


def test_feature_identity_is_deterministic():
    assert definition().feature_id == definition().feature_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("algorithm_id", "algorithm:atis/median"),
        ("configuration_digest", "sha256:" + "2" * 64),
        ("lookback", 4),
        ("minimum_history", 1),
        ("missing_data_policy", MissingDataPolicy.EXPLICIT_MISSING),
        ("availability_policy", AvailabilityPolicy.AFTER_EVENT),
        ("output_semantics", "decimal:price"),
    ],
)
def test_material_definition_change_changes_identity(field, value):
    assert definition(**{field: value}).feature_id != definition().feature_id


def test_warmup_is_explicit_and_fails_closed():
    ins = inputs()[:1]
    assert "INSUFFICIENT_HISTORY" in assess(ins=ins, o=observation(ins=ins)).reasons


@pytest.mark.parametrize(
    "state",
    [InputState.MISSING, InputState.STALE, InputState.CONFLICTING, InputState.INVALID],
)
def test_bad_input_state_fails_closed(state):
    assert f"INPUT_{state.value}" in assess(ins=inputs(state=state)).reasons


def test_observation_identity_and_lineage_are_deterministic():
    assert observation() == observation() and len(observation().input_ids) == 2


def test_lineage_substitution_rejected():
    ins = inputs()
    frozen = observation(ins=ins)
    assert "INPUT_LINEAGE_SUBSTITUTION" in assess(ins=ins[:1], o=frozen).reasons


def test_instrument_and_listing_are_bound():
    o = observation()
    assert o.instrument_id.to_string().startswith(
        "atis:instrument:v1:"
    ) and o.listing_id.to_string().startswith("atis:listing:v1:")


def test_no_trading_authority():
    assert "NO_STRATEGY_ORDER_OR_CAPITAL_AUTHORITY" in NO_TRADING_AUTHORITY

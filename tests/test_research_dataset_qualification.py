from dataclasses import FrozenInstanceError
from datetime import date
import hashlib

import pytest

from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
    ClaimCeiling,
    DIMENSIONS,
    DimensionAssessment,
    EligibleCoverage,
    QualificationError,
    QualificationLevel,
    QualificationLifecycle,
    QualificationPolicy,
    invalidate_qualification,
    permits_claim,
    qualify_dataset,
    supersede_qualification,
)


SHA = "sha256:" + hashlib.sha256(b"x").hexdigest()
POLICY = QualificationPolicy("policy:s4-02", "1.0.0", "evidence:policy")


def assessments(**states):
    return tuple(
        DimensionAssessment(
            d,
            states.get(d, AssessmentState.PASS),
            (f"evidence:{d.lower()}",),
            ()
            if states.get(d, AssessmentState.PASS) is AssessmentState.PASS
            else ("bounded limitation",),
        )
        for d in DIMENSIONS
    )


def qualify(level, predecessor=None, values=None, limitations=()):
    return qualify_dataset(
        dataset_id="dataset:daily-us",
        dataset_version="dataset-version:v1",
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        requested_level=level,
        assessments=values or assessments(),
        coverage=EligibleCoverage(date(2020, 1, 1), date(2025, 12, 31)),
        limitations=limitations,
        predecessor=predecessor,
    )


def chain():
    g1 = qualify(QualificationLevel.G1)
    g2 = qualify(QualificationLevel.G2, g1)
    g3 = qualify(QualificationLevel.G3, g2)
    return g1, g2, g3


def test_valid_g1_g2_g3_and_claim_ceilings() -> None:
    g1, g2, g3 = chain()
    assert (g1.claim_ceiling, g2.claim_ceiling, g3.claim_ceiling) == (
        ClaimCeiling.ENGINEERING_ONLY,
        ClaimCeiling.EXPLORATORY_RESEARCH,
        ClaimCeiling.ECONOMIC_VALIDATION,
    )


def test_level_skipping_and_missing_predecessor_are_rejected() -> None:
    with pytest.raises(QualificationError, match="skipping"):
        qualify(QualificationLevel.G2)
    with pytest.raises(QualificationError, match="skipping"):
        qualify(QualificationLevel.G3, qualify(QualificationLevel.G1))


@pytest.mark.parametrize("state", [AssessmentState.FAIL, AssessmentState.UNKNOWN])
def test_material_fail_or_unknown_is_rejected(state) -> None:
    with pytest.raises(QualificationError, match="prohibits"):
        qualify(QualificationLevel.G1, values=assessments(Q01_IDENTITY=state))


def test_missing_predicate_and_bounded_coverage_are_enforced() -> None:
    with pytest.raises(QualificationError, match="ten unique"):
        qualify_dataset(
            dataset_id="dataset:x",
            dataset_version="v1",
            manifest_digest=SHA,
            provenance_digest=SHA,
            policy=POLICY,
            requested_level=QualificationLevel.G1,
            assessments=assessments()[:-1],
            coverage=EligibleCoverage(date(2020, 1, 1), date(2021, 1, 1)),
        )
    assert chain()[2].coverage.start == date(2020, 1, 1)


def test_restricted_assessment_requires_explicit_limitation() -> None:
    values = assessments(Q03_COVERAGE=AssessmentState.RESTRICTED)
    with pytest.raises(QualificationError, match="limitations"):
        qualify(QualificationLevel.G1, values=values)
    assert qualify(
        QualificationLevel.G1, values=values, limitations=("coverage starts 2020",)
    ).limitations


def test_identity_binds_dataset_manifest_provenance_policy_and_assessments() -> None:
    first = qualify(QualificationLevel.G1)
    changed = qualify_dataset(
        dataset_id="dataset:other",
        dataset_version="dataset-version:v1",
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        requested_level=QualificationLevel.G1,
        assessments=assessments(),
        coverage=first.coverage,
    )
    assert first.qualification_id != changed.qualification_id
    changed_policy = QualificationPolicy("policy:s4-02", "2.0.0", "evidence:policy-v2")
    other = qualify_dataset(
        dataset_id=first.dataset_id,
        dataset_version=first.dataset_version,
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=changed_policy,
        requested_level=QualificationLevel.G1,
        assessments=assessments(),
        coverage=first.coverage,
    )
    assert first.qualification_id != other.qualification_id


@pytest.mark.parametrize(
    "dimension",
    [
        "Q07_PIT_KNOWLEDGE_TIME",
        "Q08_SURVIVORSHIP_HISTORICAL_UNIVERSE",
        "Q09_CORPORATE_ACTIONS",
        "Q10_DELISTING_TERMINAL_TREATMENT",
    ],
)
def test_research_suitability_deficiencies_block_g2(dimension) -> None:
    g1 = qualify(QualificationLevel.G1)
    with pytest.raises(QualificationError):
        qualify(
            QualificationLevel.G2,
            g1,
            assessments(**{dimension: AssessmentState.UNKNOWN}),
        )


def test_promotion_invalidation_and_supersession_are_immutable() -> None:
    g1, g2, _ = chain()
    invalid = invalidate_qualification(g2, ("provider correction",))
    old = supersede_qualification(g1, g2)
    assert (
        invalid.lifecycle is QualificationLifecycle.INVALIDATED
        and not permits_claim(invalid, ClaimCeiling.ENGINEERING_ONLY)
    )
    assert (
        old.lifecycle is QualificationLifecycle.SUPERSEDED
        and old.superseded_by == g2.qualification_id
    )
    with pytest.raises(FrozenInstanceError):
        g1.level = QualificationLevel.G3  # type: ignore[misc]


def test_policy_change_cannot_silently_rewrite_history() -> None:
    g1 = qualify(QualificationLevel.G1)
    assert g1.policy.version == "1.0.0"
    assert g1.policy != QualificationPolicy("policy:s4-02", "2.0.0", "evidence:new")


def test_claim_authority_is_bounded_and_never_establishes_edge_or_trading() -> None:
    g1, g2, g3 = chain()
    assert not permits_claim(g1, ClaimCeiling.EXPLORATORY_RESEARCH)
    assert not permits_claim(g1, ClaimCeiling.ECONOMIC_VALIDATION)
    assert not permits_claim(g2, ClaimCeiling.ECONOMIC_VALIDATION)
    assert permits_claim(g3, ClaimCeiling.ECONOMIC_VALIDATION)
    assert not hasattr(g3, "trading_authority") and not hasattr(g3, "edge_established")


def test_no_silent_repair_surface_exists() -> None:
    value = chain()[0]
    assert not hasattr(value, "repair") and not hasattr(value, "mutate_source")

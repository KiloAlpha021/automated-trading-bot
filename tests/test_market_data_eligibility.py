from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationState,
)
from automated_trading_bot.market_data.eligibility import (
    ELIGIBILITY_DECISION_VERSION,
    MAX_C06_DECISIONS_PER_BATCH,
    MAX_C06_EVIDENCE_REFS,
    MAX_C06_LINEAGE_DEPTH,
    MAX_C06_LINEAGE_VERSIONS,
    QUARANTINE_DECISION_VERSION,
    QUARANTINE_SUBJECT_VERSION,
    RELEASE_ASSERTION_VERSION,
    RELEASE_POLICY_VERSION,
    AuthorityError,
    BindingError,
    ClaimId,
    EligibilityDisposition,
    EligibilityPolicy,
    EligibilityPolicyId,
    LineageError,
    QuarantineDecision,
    QuarantineDisposition,
    QuarantinePolicy,
    QuarantineSubject,
    ReleaseAuthorityAdmissionPolicy,
    ReleaseAuthorityAssertion,
    ReleaseAuthorityId,
    ReleaseAuthorityPolicyId,
    ResolvedC06Evidence,
    ResourceLimitError,
    atomic_decision_batch,
    c05_quality_result_digest,
    decide_eligibility,
    decide_quarantine,
    release_quarantine,
    releases_known_at,
    validate_lineage_edges,
)
from automated_trading_bot.market_data.quality import (
    OutlierState,
    ProductionQualityResult,
    SequenceCoverageState,
    SequenceOrderState,
)


def ts(day: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, day, tzinfo=UTC))


def evidence(
    name: str, content: bytes | None = None
) -> tuple[EvidenceRef, ResolvedC06Evidence]:
    body = name.encode() if content is None else content
    ref = EvidenceRef(
        SourceId("source:test/authority"),
        DatasetId("dataset:test/c06"),
        EvidenceId(f"evidence:test/{name}"),
        EvidenceContentDigest.from_bytes(body),
    )
    return ref, ResolvedC06Evidence(ref, body)


def quality_result(state: ValidationState, ref: EvidenceRef) -> ProductionQualityResult:
    return ProductionQualityResult(
        (EvidenceContentDigest.from_bytes(b"descriptor"),),
        SequenceCoverageState.COMPLETE,
        SequenceOrderState.CONSISTENT,
        (
            (
                EvidenceContentDigest.from_bytes(b"descriptor"),
                OutlierState.WITHIN_POLICY,
            ),
        ),
        (),
        ("PROTECTED_C05_RESULT",),
        (ref,),
        state,
    )


def setup_subject(
    state: ValidationState = ValidationState.VALID,
) -> tuple[
    QuarantineSubject,
    ProductionQualityResult,
    tuple[ResolvedC06Evidence, ...],
    dict[str, EvidenceRef],
]:
    indexed = {
        name: evidence(name)
        for name in (
            "subject",
            "quality",
            "quarantine-policy",
            "claim",
            "eligibility-policy",
            "prerequisite",
            "currentness",
            "assertion",
            "release-basis",
            "release-policy",
        )
    }
    refs = {name: pair[0] for name, pair in indexed.items()}
    resolved = tuple(pair[1] for pair in indexed.values())
    result = quality_result(state, refs["quality"])
    subject = QuarantineSubject(
        QUARANTINE_SUBJECT_VERSION,
        refs["subject"],
        c05_quality_result_digest(result),
        (refs["quality"], refs["subject"]),
    )
    return subject, result, resolved, refs


def quarantine(
    subject: QuarantineSubject,
    result: ProductionQualityResult,
    resolved: tuple[ResolvedC06Evidence, ...],
    refs: dict[str, EvidenceRef],
) -> QuarantineDecision:
    return decide_quarantine(
        subject,
        result,
        QuarantinePolicy("quarantine-policy:test/v1", refs["quarantine-policy"]),
        knowledge_from=ts(2),
        evidence_refs=(refs["quality"],),
        resolved_evidence=resolved,
    )


def eligibility_policy(
    refs: dict[str, EvidenceRef],
    claim: str = "claim:test/a",
    *,
    currentness: bool = False,
) -> EligibilityPolicy:
    return EligibilityPolicy(
        EligibilityPolicyId("eligibility-policy:test/v1"),
        refs["eligibility-policy"],
        ClaimId(claim),
        ContractVersion("ATIS_C06_TEST_CLAIM", 1),
        refs["claim"],
        currentness,
    )


def release_inputs(
    subject: QuarantineSubject,
    predecessor: QuarantineDecision,
    refs: dict[str, EvidenceRef],
) -> tuple[ReleaseAuthorityAssertion, ReleaseAuthorityAdmissionPolicy]:
    policy_id = ReleaseAuthorityPolicyId("release-policy:test/v1")
    authority_id = ReleaseAuthorityId("release-authority:test/operator")
    assertion = ReleaseAuthorityAssertion(
        RELEASE_ASSERTION_VERSION,
        refs["assertion"],
        authority_id,
        "RELEASE_QUARANTINE",
        predecessor.decision_id,
        subject.subject_id,
        (ClaimId("claim:test/a"),),
        refs["release-basis"],
        ts(3),
        None,
        policy_id,
    )
    policy = ReleaseAuthorityAdmissionPolicy(
        RELEASE_POLICY_VERSION,
        policy_id,
        refs["release-policy"],
        (authority_id,),
        (RELEASE_ASSERTION_VERSION,),
        ("RELEASE_QUARANTINE",),
        (ClaimId("claim:test/a"),),
        ts(1),
        ts(10),
    )
    return assertion, policy


def test_subject_and_decisions_are_exact_immutable_and_deterministic() -> None:
    subject, result, resolved, refs = setup_subject()
    permuted = QuarantineSubject(
        subject.contract_version,
        subject.subject_ref,
        subject.c05_quality_result_digest,
        tuple(reversed(subject.evidence_refs)),
    )
    assert permuted.subject_id == subject.subject_id
    assert permuted.content_digest == subject.content_digest
    first = quarantine(subject, result, resolved, refs)
    second = quarantine(subject, result, resolved, refs)
    assert first == second
    with pytest.raises(FrozenInstanceError):
        setattr(first, "disposition", QuarantineDisposition.QUARANTINED)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ValidationState.VALID, QuarantineDisposition.NOT_QUARANTINED),
        (ValidationState.INVALID, QuarantineDisposition.QUARANTINED),
        (ValidationState.NOT_ESTABLISHED, QuarantineDisposition.NOT_ESTABLISHED),
        (ValidationState.INCOMPATIBLE, QuarantineDisposition.INCOMPATIBLE),
    ],
)
def test_c05_state_maps_restrictively_without_reinterpretation(
    state: ValidationState, expected: QuarantineDisposition
) -> None:
    subject, result, resolved, refs = setup_subject(state)
    assert quarantine(subject, result, resolved, refs).disposition is expected


def test_not_quarantined_is_not_eligibility_and_claims_are_isolated() -> None:
    subject, result, resolved, refs = setup_subject()
    q = quarantine(subject, result, resolved, refs)
    assert q.disposition is QuarantineDisposition.NOT_QUARANTINED
    a = decide_eligibility(
        subject,
        q,
        result,
        eligibility_policy(refs),
        knowledge_from=ts(3),
        prerequisite_evidence_refs=(refs["prerequisite"],),
        resolved_evidence=resolved,
    )
    b = decide_eligibility(
        subject,
        q,
        result,
        eligibility_policy(refs, "claim:test/b"),
        knowledge_from=ts(3),
        prerequisite_evidence_refs=(refs["prerequisite"],),
        resolved_evidence=resolved,
    )
    assert a.disposition is EligibilityDisposition.ELIGIBLE
    assert a.claim_id != b.claim_id and a.decision_id != b.decision_id


def test_restrictive_c05_and_quarantine_prevent_eligibility() -> None:
    subject, c05_result, resolved, refs = setup_subject(ValidationState.INVALID)
    q = quarantine(subject, c05_result, resolved, refs)
    decision = decide_eligibility(
        subject,
        q,
        c05_result,
        eligibility_policy(refs),
        knowledge_from=ts(3),
        prerequisite_evidence_refs=(refs["prerequisite"],),
        resolved_evidence=resolved,
    )
    assert decision.disposition is EligibilityDisposition.INELIGIBLE


def test_required_external_currentness_is_consumed_but_never_created() -> None:
    subject, result, resolved, refs = setup_subject()
    q = quarantine(subject, result, resolved, refs)
    policy = eligibility_policy(refs, currentness=True)
    absent = decide_eligibility(
        subject,
        q,
        result,
        policy,
        knowledge_from=ts(3),
        prerequisite_evidence_refs=(refs["prerequisite"],),
        resolved_evidence=resolved,
    )
    present = decide_eligibility(
        subject,
        q,
        result,
        policy,
        knowledge_from=ts(3),
        prerequisite_evidence_refs=(refs["prerequisite"],),
        external_currentness_ref=refs["currentness"],
        resolved_evidence=resolved,
    )
    assert absent.disposition is EligibilityDisposition.NOT_ESTABLISHED
    assert present.disposition is EligibilityDisposition.ELIGIBLE


def test_exact_evidence_content_and_policy_bindings_are_enforced() -> None:
    subject, result, resolved, refs = setup_subject()
    q = quarantine(subject, result, resolved, refs)
    with pytest.raises(BindingError, match="MISSING_RESOLVED_EVIDENCE"):
        decide_eligibility(
            subject,
            q,
            result,
            eligibility_policy(refs),
            knowledge_from=ts(3),
            prerequisite_evidence_refs=(refs["prerequisite"],),
            resolved_evidence=(),
        )
    bad = replace(resolved[0], content=b"substitution")
    with pytest.raises(BindingError, match="DIGEST_MISMATCH"):
        decide_quarantine(
            subject,
            result,
            QuarantinePolicy("quarantine-policy:test/v1", refs["quarantine-policy"]),
            knowledge_from=ts(2),
            evidence_refs=(refs["quality"],),
            resolved_evidence=(bad,) + resolved[1:],
        )
    other = quality_result(ValidationState.INVALID, refs["quality"])
    with pytest.raises(BindingError, match="C05_QUALITY_RESULT_BINDING_MISMATCH"):
        decide_quarantine(
            subject,
            other,
            QuarantinePolicy("quarantine-policy:test/v1", refs["quarantine-policy"]),
            knowledge_from=ts(2),
            evidence_refs=(refs["quality"],),
            resolved_evidence=resolved,
        )


def test_effective_time_requires_independently_established_semantics() -> None:
    subject, result, resolved, refs = setup_subject()
    with pytest.raises(BindingError, match="EFFECTIVE_TIME"):
        decide_quarantine(
            subject,
            result,
            QuarantinePolicy("quarantine-policy:test/v1", refs["quarantine-policy"]),
            knowledge_from=ts(2),
            effective_from=ts(2),
            evidence_refs=(refs["quality"],),
            resolved_evidence=resolved,
        )


def test_valid_independently_admitted_release_is_attributable_successor() -> None:
    subject, result, resolved, refs = setup_subject(ValidationState.INVALID)
    q = quarantine(subject, result, resolved, refs)
    assertion, policy = release_inputs(subject, q, refs)
    release = release_quarantine(
        q, subject, assertion, policy, knowledge_from=ts(4), resolved_evidence=resolved
    )
    assert release.predecessor_quarantine_decision_id == q.decision_id
    assert release.supersedes_decision_id == q.decision_id
    assert q.disposition is QuarantineDisposition.QUARANTINED
    assert not hasattr(release, "eligibility_disposition")


def test_release_authority_is_external_exact_and_fail_closed() -> None:
    subject, result, resolved, refs = setup_subject(ValidationState.INVALID)
    q = quarantine(subject, result, resolved, refs)
    assertion, policy = release_inputs(subject, q, refs)
    forged = replace(
        assertion, authority_id=ReleaseAuthorityId("release-authority:test/forged")
    )
    with pytest.raises(AuthorityError, match="NOT_ADMITTED"):
        release_quarantine(
            q, subject, forged, policy, knowledge_from=ts(4), resolved_evidence=resolved
        )
    with pytest.raises(BindingError, match="MISSING_RESOLVED_EVIDENCE"):
        release_quarantine(
            q, subject, assertion, policy, knowledge_from=ts(4), resolved_evidence=()
        )
    stale = replace(assertion, knowledge_from=ts(10))
    with pytest.raises(AuthorityError, match="STALE_RELEASE_POLICY"):
        release_quarantine(
            q, subject, stale, policy, knowledge_from=ts(11), resolved_evidence=resolved
        )
    self_admitted = replace(policy, policy_ref=assertion.assertion_ref)
    with pytest.raises(AuthorityError, match="SELF_ADMISSION"):
        release_quarantine(
            q,
            subject,
            assertion,
            self_admitted,
            knowledge_from=ts(4),
            resolved_evidence=resolved,
        )


def test_release_replay_subject_policy_and_temporal_substitution_reject() -> None:
    subject, result, resolved, refs = setup_subject(ValidationState.INVALID)
    q = quarantine(subject, result, resolved, refs)
    assertion, policy = release_inputs(subject, q, refs)
    release = release_quarantine(
        q, subject, assertion, policy, knowledge_from=ts(4), resolved_evidence=resolved
    )
    with pytest.raises(LineageError, match="BRANCHING"):
        release_quarantine(
            q,
            subject,
            assertion,
            policy,
            knowledge_from=ts(5),
            resolved_evidence=resolved,
            existing_releases=(release,),
        )
    with pytest.raises(LineageError, match="KNOWLEDGE"):
        release_quarantine(
            q,
            subject,
            assertion,
            policy,
            knowledge_from=ts(2),
            resolved_evidence=resolved,
        )
    wrong_policy = replace(
        policy, policy_id=ReleaseAuthorityPolicyId("release-policy:test/other")
    )
    with pytest.raises(AuthorityError, match="SUBSTITUTION"):
        release_quarantine(
            q,
            subject,
            assertion,
            wrong_policy,
            knowledge_from=ts(4),
            resolved_evidence=resolved,
        )


def test_pit_reconstruction_excludes_later_release() -> None:
    subject, result, resolved, refs = setup_subject(ValidationState.INVALID)
    q = quarantine(subject, result, resolved, refs)
    assertion, policy = release_inputs(subject, q, refs)
    release = release_quarantine(
        q, subject, assertion, policy, knowledge_from=ts(4), resolved_evidence=resolved
    )
    assert releases_known_at((release,), ts(3)) == ()
    assert releases_known_at((release,), ts(4)) == (release,)


@pytest.mark.parametrize(
    "edges", [(("a", "a"),), (("a", "b"), ("b", "a")), (("b", "a"), ("c", "a"))]
)
def test_lineage_rejects_self_cycle_and_branch(
    edges: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(LineageError):
        validate_lineage_edges(edges)


def test_lineage_depth_and_version_limits_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import automated_trading_bot.market_data.eligibility as module

    monkeypatch.setattr(module, "MAX_C06_LINEAGE_DEPTH", 2)
    with pytest.raises(ResourceLimitError, match="DEPTH"):
        validate_lineage_edges((("d", "c"), ("c", "b"), ("b", "a")))
    monkeypatch.setattr(module, "MAX_C06_LINEAGE_VERSIONS", 1)
    with pytest.raises(ResourceLimitError, match="VERSION"):
        validate_lineage_edges((("b", "a"), ("c", "b")))


def test_resource_limits_reject_without_truncation_or_partial_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, _, _, refs = setup_subject()
    with pytest.raises(ResourceLimitError, match="EVIDENCE"):
        QuarantineSubject(
            QUARANTINE_SUBJECT_VERSION,
            subject.subject_ref,
            subject.c05_quality_result_digest,
            tuple(refs["quality"] for _ in range(MAX_C06_EVIDENCE_REFS + 1)),
        )
    with pytest.raises(ResourceLimitError, match="BATCH"):
        atomic_decision_batch(
            tuple(lambda: object() for _ in range(MAX_C06_DECISIONS_PER_BATCH + 1))
        )
    seen: list[int] = []

    def good() -> int:
        seen.append(1)
        return 1

    def bad() -> int:
        raise BindingError("restrictive")

    with pytest.raises(BindingError):
        atomic_decision_batch((good, bad))
    assert seen == [1]  # no result tuple was emitted
    with pytest.raises(ResourceLimitError, match="REASON"):
        replace(
            quarantine(
                subject,
                quality_result(ValidationState.VALID, refs["quality"]),
                tuple(
                    evidence(name)[1]
                    for name in ("subject", "quality", "quarantine-policy")
                ),
                refs,
            ),
            reasons=("X" * 256,),
        )


def test_unsupported_versions_and_identity_content_conflicts_reject() -> None:
    subject, _, _, refs = setup_subject()
    with pytest.raises(BindingError, match="UNSUPPORTED"):
        QuarantineSubject(
            ContractVersion("ATIS_C06_QUARANTINE_SUBJECT", 2),
            refs["subject"],
            subject.c05_quality_result_digest,
            subject.evidence_refs,
        )
    conflicting = EvidenceRef(
        refs["quality"].source_id,
        refs["quality"].dataset_id,
        refs["quality"].evidence_id,
        EvidenceContentDigest.from_bytes(b"other"),
    )
    with pytest.raises(BindingError, match="CONFLICT"):
        QuarantineSubject(
            QUARANTINE_SUBJECT_VERSION,
            refs["subject"],
            subject.c05_quality_result_digest,
            (refs["quality"], conflicting),
        )


def test_contract_constants_remain_exact() -> None:
    assert QUARANTINE_DECISION_VERSION == ContractVersion(
        "ATIS_C06_QUARANTINE_DECISION", 1
    )
    assert ELIGIBILITY_DECISION_VERSION == ContractVersion(
        "ATIS_C06_ELIGIBILITY_DECISION", 1
    )
    assert MAX_C06_EVIDENCE_REFS == 64
    assert MAX_C06_DECISIONS_PER_BATCH == 4096
    assert MAX_C06_LINEAGE_VERSIONS == 4096
    assert MAX_C06_LINEAGE_DEPTH == 256

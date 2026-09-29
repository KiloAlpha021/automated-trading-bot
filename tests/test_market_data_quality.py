from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationPolicyId,
    ValidationState,
)
from automated_trading_bot.market_data import (
    MAX_QUALITY_DESCRIPTORS,
    QUALITY_INPUT_FAMILY,
    QUALITY_POLICY_FAMILY,
    AbstractOutlierDecision,
    AbstractQualityPolicy,
    AbstractSemanticContent,
    IdentityState,
    OutlierState,
    PostSync2BoundaryError,
    PresenceState,
    QualityDimension,
    QualityEvaluationError,
    QualityInputDescriptor,
    QualityLogicalIdentity,
    ResolvedQualityEvidence,
    SequenceCoverageState,
    SequenceOrderState,
    StructureState,
    evaluate_quality,
)


SOURCE = SourceId("source:atis/quality-test")
DATASET = DatasetId("dataset:atis/quality-test")
POLICY_ID = ValidationPolicyId("validation-policy:atis/c05-pre-v1")
EVALUATED = Timestamp(datetime(2026, 1, 2, 12, tzinfo=timezone.utc))


class EvidenceBook:
    def __init__(self) -> None:
        self.values: dict[tuple[object, ...], ResolvedQualityEvidence] = {}

    def add(self, name: str, content: bytes | None = None) -> EvidenceRef:
        value = name.encode() if content is None else content
        ref = EvidenceRef(
            source_id=SOURCE,
            dataset_id=DATASET,
            evidence_id=EvidenceId(f"evidence:atis/{name}"),
            content_digest=EvidenceContentDigest.from_bytes(value),
        )
        self.values[ref.key] = ResolvedQualityEvidence(ref, value)
        return ref

    def resolved(self) -> tuple[ResolvedQualityEvidence, ...]:
        return tuple(self.values.values())


def descriptor(
    book: EvidenceBook,
    number: int,
    *,
    key: str = "slot-1",
    semantic: bytes | None = b"abstract-semantic-value",
    input_present: bool = True,
    malformed: bool = False,
    representation: EvidenceRef | None = None,
    policy_ref: EvidenceRef | None = None,
    metric: bool = False,
) -> QualityInputDescriptor:
    representation_ref = representation or book.add("abstract-contract", b"abstract-contract-v1")
    selected_policy_ref = policy_ref or book.add("policy", b"quality-policy-v1")
    semantic_content = None
    if semantic is not None:
        semantic_ref = book.add(f"semantic-{number}", semantic)
        semantic_content = AbstractSemanticContent(
            contract_ref=representation_ref,
            canonical_bytes_ref=semantic_ref,
            digest=semantic_ref.content_digest,
        )
    return QualityInputDescriptor(
        descriptor_version=ContractVersion(QUALITY_INPUT_FAMILY, 1),
        cohort_ref=book.add("cohort", b"complete-cohort"),
        evaluation_scope_ref=book.add("scope", b"presence-structure-identity-outlier"),
        expected_sequence_ref=book.add("expectation", b"abstract-expectation"),
        input_ref=book.add(f"input-{number}") if input_present else None,
        logical_identity=QualityLogicalIdentity(book.add("namespace"), key),
        metric_inputs_ref=book.add(f"metric-{number}") if metric else None,
        order_evidence_ref=None,
        representation_contract_ref=representation_ref,
        semantic_content=semantic_content,
        validation_policy_id=POLICY_ID,
        validation_policy_ref=selected_policy_ref,
        evaluated_at=EVALUATED,
        malformed_evidence_ref=book.add(f"malformed-{number}") if malformed else None,
    )


def policy(
    book: EvidenceBook,
    policy_ref: EvidenceRef,
    *,
    dimensions: tuple[QualityDimension, ...] = (
        QualityDimension.PRESENCE,
        QualityDimension.STRUCTURE,
        QualityDimension.IDENTITY,
    ),
    decisions: tuple[AbstractOutlierDecision, ...] = (),
) -> AbstractQualityPolicy:
    return AbstractQualityPolicy(
        policy_version=ContractVersion(QUALITY_POLICY_FAMILY, 1),
        validation_policy_id=POLICY_ID,
        validation_policy_ref=policy_ref,
        requested_dimensions=dimensions,
        outlier_decisions=decisions,
    )


def evaluate(
    book: EvidenceBook,
    values: tuple[QualityInputDescriptor, ...],
    value_policy: AbstractQualityPolicy,
    representation: EvidenceRef,
) -> tuple[object, ...]:
    return evaluate_quality(values, value_policy, book.resolved(), (representation,))


def test_unique_quality_result_is_deterministic_and_immutable() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    value = descriptor(book, 1, representation=representation, policy_ref=policy_ref)
    result = evaluate(book, (value,), policy(book, policy_ref), representation)[0]
    assert result.validation_state is ValidationState.VALID
    assert result.presence is PresenceState.PRESENT
    assert result.structure is StructureState.WELL_FORMED
    assert result.identity is IdentityState.UNIQUE
    assert result.sequence_coverage is SequenceCoverageState.NOT_ESTABLISHED
    assert result.sequence_order is SequenceOrderState.NOT_ESTABLISHED
    assert result == evaluate(book, (value,), policy(book, policy_ref), representation)[0]
    with pytest.raises(FrozenInstanceError):
        result.identity = IdentityState.EXACT_DUPLICATE  # type: ignore[misc]


def test_exact_duplicate_uses_semantics_not_delivery_identity() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    first = descriptor(book, 1, representation=representation, policy_ref=policy_ref)
    second = descriptor(book, 2, representation=representation, policy_ref=policy_ref)
    results = evaluate(book, (first, second), policy(book, policy_ref), representation)
    assert {result.identity for result in results} == {IdentityState.EXACT_DUPLICATE}
    assert all(len(result.logical_group_members) == 2 for result in results)


def test_materially_different_semantics_conflict_for_every_group_member() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    first = descriptor(book, 1, semantic=b"price=1", representation=representation, policy_ref=policy_ref)
    second = descriptor(book, 2, semantic=b"price=2", representation=representation, policy_ref=policy_ref)
    results = evaluate(book, (first, second), policy(book, policy_ref), representation)
    assert {result.identity for result in results} == {IdentityState.MATERIAL_CONFLICT}
    assert {result.validation_state for result in results} == {ValidationState.INVALID}


def test_result_is_permutation_independent() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    first = descriptor(book, 1, key="a", representation=representation, policy_ref=policy_ref)
    second = descriptor(book, 2, key="b", representation=representation, policy_ref=policy_ref)
    value_policy = policy(book, policy_ref)
    assert evaluate(book, (first, second), value_policy, representation) == evaluate(
        book, (second, first), value_policy, representation,
    )


def test_missing_and_malformed_are_distinct_restrictive_states() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    missing = descriptor(book, 1, input_present=False, semantic=None, representation=representation, policy_ref=policy_ref)
    malformed = descriptor(book, 2, malformed=True, representation=representation, policy_ref=policy_ref)
    results = {result.descriptor_digest: result for result in evaluate(book, (missing, malformed), policy(book, policy_ref), representation)}
    assert results[missing.descriptor_digest].presence is PresenceState.MISSING
    assert results[missing.descriptor_digest].structure is StructureState.NOT_APPLICABLE
    assert results[malformed.descriptor_digest].presence is PresenceState.PRESENT
    assert results[malformed.descriptor_digest].structure is StructureState.MALFORMED
    assert all(result.validation_state is ValidationState.INVALID for result in results.values())


def test_missing_prerequisites_and_unknown_contract_fail_closed() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    unsupported = book.add("unsupported-contract", b"unsupported")
    policy_ref = book.add("policy", b"quality-policy-v1")
    incomplete = descriptor(book, 1, semantic=None, representation=representation, policy_ref=policy_ref)
    incompatible = descriptor(book, 2, representation=unsupported, policy_ref=policy_ref)
    results = {result.descriptor_digest: result for result in evaluate(book, (incomplete, incompatible), policy(book, policy_ref), representation)}
    assert results[incomplete.descriptor_digest].validation_state is ValidationState.NOT_ESTABLISHED
    assert results[incomplete.descriptor_digest].identity is IdentityState.NOT_ESTABLISHED
    assert results[incompatible.descriptor_digest].validation_state is ValidationState.INCOMPATIBLE


def test_abstract_policy_invocation_is_exact_and_evidence_bound() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    value = descriptor(book, 1, representation=representation, policy_ref=policy_ref, metric=True)
    decision_evidence = book.add("outlier-decision", b"deterministic-result")
    decision = AbstractOutlierDecision(
        descriptor_digest=value.descriptor_digest,
        metric_inputs_ref=value.metric_inputs_ref,  # type: ignore[arg-type]
        state=OutlierState.OUTSIDE_POLICY,
        reason="ABSTRACT_POLICY_OUTSIDE",
        evidence_refs=(policy_ref, decision_evidence),
    )
    value_policy = policy(
        book,
        policy_ref,
        dimensions=(QualityDimension.PRESENCE, QualityDimension.STRUCTURE, QualityDimension.IDENTITY, QualityDimension.OUTLIER),
        decisions=(decision,),
    )
    result = evaluate(book, (value,), value_policy, representation)[0]
    assert result.outlier is OutlierState.OUTSIDE_POLICY
    assert result.validation_state is ValidationState.INVALID


def test_contradictory_or_unresolved_evidence_is_rejected() -> None:
    book = EvidenceBook()
    ref = book.add("evidence", b"right")
    with pytest.raises(QualityEvaluationError, match="DIGEST"):
        ResolvedQualityEvidence(ref, b"wrong")
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    value = descriptor(book, 1, representation=representation, policy_ref=policy_ref)
    partial = tuple(item for item in book.resolved() if item.ref != value.cohort_ref)
    with pytest.raises(QualityEvaluationError, match="MISSING_RESOLVED"):
        evaluate_quality((value,), policy(book, policy_ref), partial, (representation,))


def test_policy_binding_mismatch_rejects_without_fallback() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    descriptor_policy = book.add("descriptor-policy", b"descriptor")
    evaluator_policy = book.add("evaluator-policy", b"evaluator")
    value = descriptor(book, 1, representation=representation, policy_ref=descriptor_policy)
    with pytest.raises(QualityEvaluationError, match="POLICY_BINDING"):
        evaluate(book, (value,), policy(book, evaluator_policy), representation)


def test_post_sync_dimensions_are_rejected_and_remain_unimplemented() -> None:
    book = EvidenceBook()
    policy_ref = book.add("policy", b"quality-policy-v1")
    with pytest.raises(PostSync2BoundaryError, match="SYNC_2"):
        policy(book, policy_ref, dimensions=(QualityDimension.SEQUENCE_COVERAGE,))


def test_resource_limits_fail_closed_without_partial_results() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    value = descriptor(book, 1, representation=representation, policy_ref=policy_ref)
    with pytest.raises(ValueError, match="resource limit"):
        evaluate_quality(
            (value,) * (MAX_QUALITY_DESCRIPTORS + 1),
            policy(book, policy_ref),
            book.resolved(),
            (representation,),
        )


def test_no_c04_c06_c10_provider_or_promotion_authority_surface() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    value = descriptor(book, 1, representation=representation, policy_ref=policy_ref)
    result = evaluate(book, (value,), policy(book, policy_ref), representation)[0]
    for forbidden in (
        "canonical_observation",
        "canonical_bytes",
        "provider",
        "eligible",
        "quarantined",
        "fresh",
        "current",
        "invalidated",
        "materialized",
        "promoted",
        "persisted",
    ):
        assert not hasattr(result, forbidden)
    assert result.sequence_coverage is SequenceCoverageState.NOT_ESTABLISHED
    assert result.sequence_order is SequenceOrderState.NOT_ESTABLISHED


def test_descriptor_and_policy_content_identities_change_with_material_inputs() -> None:
    book = EvidenceBook()
    representation = book.add("abstract-contract", b"abstract-contract-v1")
    policy_ref = book.add("policy", b"quality-policy-v1")
    first = descriptor(book, 1, key="a", representation=representation, policy_ref=policy_ref)
    second = descriptor(book, 2, key="b", representation=representation, policy_ref=policy_ref)
    assert first.descriptor_digest != second.descriptor_digest
    baseline = policy(book, policy_ref)
    changed = policy(book, policy_ref, dimensions=(QualityDimension.PRESENCE,))
    assert baseline.content_digest != changed.content_digest
    with pytest.raises(FrozenInstanceError):
        first.input_ref = None  # type: ignore[misc]

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, fields, replace
from datetime import UTC, datetime
import inspect

import pytest

import automated_trading_bot.datasets.sync3 as sync3
from automated_trading_bot.datasets.currentness import (
    CurrentnessAssessment,
    CurrentnessAssessmentId,
    CurrentnessState,
    FreshnessPolicyId,
    InvalidationAssessment,
    InvalidationAssessmentId,
    InvalidationTriggerId,
    PropagationState,
)
from automated_trading_bot.datasets.lifecycle import (
    DatasetCurrentnessAssessment,
    DatasetCurrentnessAssessmentId,
    InvalidationEvent,
    LifecycleError,
    PropagationError,
    PropagationRecord,
    PropagationRecordState,
    advance_propagation,
    bind_dataset_currentness,
    create_affected_set,
    create_dataset_version_mapping,
    create_invalidation_event,
    create_propagation_record,
)
from automated_trading_bot.datasets.materialization import DatasetVersionId
from automated_trading_bot.datasets.provenance import (
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencySetId,
    ProvenanceGraphId,
    ProvenanceNodeId,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationState,
    canonicalize_evidence_refs,
)
from automated_trading_bot.market_data.compatibility import CompatibilityDescriptor
from automated_trading_bot.market_data.eligibility import (
    ELIGIBILITY_DECISION_VERSION,
    ClaimId,
    EligibilityDecision,
    EligibilityDecisionId,
    EligibilityDisposition,
    EligibilityPolicyId,
    QuarantineSubjectId,
    ResolvedC06Evidence,
    c05_quality_result_digest,
)
from automated_trading_bot.market_data.quality import (
    ApprovedMetricRule,
    ApprovedProductionQualityPolicy,
    OutlierState,
    ProductionEvidenceState,
    ProductionMetric,
    ProductionMetricEvidence,
    ProductionQualityResult,
    ProductionSequenceEvidence,
    ResolvedQualityEvidence,
    SequenceCoverageState,
    SequenceOrderState,
    evaluate_production_quality,
)

from test_market_data_compatibility import build as compatibility_descriptor


SOURCE = SourceId("source:test/sync3")
EVIDENCE_DATASET = DatasetId("dataset:test/sync3-evidence")
DATASET_VERSION = DatasetVersionId("dataset-version:test/sync3-v1")
CLAIM_ID = ClaimId("claim:test/sync3-v1")


class ValueProxy:
    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __getattr__(self, name: str) -> object:
        return getattr(self.value, name)

    def __eq__(self, other: object) -> bool:
        if type(other) is ValueProxy:
            return self.value == other.value
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)


def timestamp(hour: int, *, day: int = 1) -> Timestamp:
    return Timestamp(datetime(2026, 1, day, hour, tzinfo=UTC))


def evidence_ref(name: str, content: bytes) -> EvidenceRef:
    return EvidenceRef(
        SOURCE,
        EVIDENCE_DATASET,
        EvidenceId(f"evidence:test/sync3/{name}"),
        EvidenceContentDigest.from_bytes(content),
    )


class QualityEvidenceBook:
    def __init__(self) -> None:
        self.values: dict[tuple[object, ...], ResolvedQualityEvidence] = {}

    def add(self, name: str, content: bytes | None = None) -> EvidenceRef:
        body = name.encode() if content is None else content
        ref = evidence_ref(f"quality-{name}", body)
        self.values[ref.key] = ResolvedQualityEvidence(ref, body)
        return ref

    def add_ref(self, ref: EvidenceRef, content: bytes) -> None:
        self.values[ref.key] = ResolvedQualityEvidence(ref, content)

    def resolved(self) -> tuple[ResolvedQualityEvidence, ...]:
        return tuple(self.values.values())


class C06EvidenceBook:
    def __init__(self) -> None:
        self.values: dict[tuple[object, ...], ResolvedC06Evidence] = {}

    def add(self, name: str, content: bytes) -> EvidenceRef:
        ref = evidence_ref(f"c06-{name}", content)
        self.values[ref.key] = ResolvedC06Evidence(ref, content)
        return ref

    def add_ref(self, ref: EvidenceRef, content: bytes) -> None:
        self.values[ref.key] = ResolvedC06Evidence(ref, content)

    def resolved(self) -> tuple[ResolvedC06Evidence, ...]:
        return tuple(self.values.values())


def resource_policy() -> DatasetLifecycleResourcePolicy:
    names = (
        "MAX_MATERIALIZATION_EVIDENCE_REFS",
        "MAX_DEPENDENCIES_PER_DATASET_VERSION",
        "MAX_PROVENANCE_NODES",
        "MAX_PROVENANCE_EDGES",
        "MAX_AFFECTED_SET_MEMBERS",
        "MAX_INVALIDATION_TRAVERSAL_DEPTH",
        "MAX_REASONS_PER_RECORD",
        "MAX_REASON_LENGTH",
        "MAX_POLICY_REFS",
        "MAX_PUBLICATION_RECORDS_PER_BATCH",
        "MAX_DATASET_VERSIONS_PER_BATCH",
        "MAX_PROPAGATION_EVENTS_PER_RUN",
    )
    return DatasetLifecycleResourcePolicy(
        DatasetLifecycleResourcePolicyId("policy:sync3-test"),
        evidence_ref("resource-policy", b"resource-policy"),
        tuple((name, 64) for name in names),
    )


def make_currentness(
    state: CurrentnessState,
    *,
    dataset_version_id: DatasetVersionId = DATASET_VERSION,
    evaluated_at: Timestamp | None = None,
    reasons: tuple[str, ...] | None = None,
    invalidation_event: InvalidationEvent | None = None,
    propagation_record: PropagationRecord | None = None,
) -> DatasetCurrentnessAssessment:
    evaluation = evaluated_at or timestamp(12)
    expected_reasons = {
        CurrentnessState.CURRENT: ("EVALUATION_BEFORE_FRESHNESS_BOUNDARY",),
        CurrentnessState.STALE: ("EVALUATION_AT_OR_AFTER_FRESHNESS_BOUNDARY",),
        CurrentnessState.UNKNOWN: ("CURRENTNESS_EVIDENCE_UNAVAILABLE",),
    }
    base = CurrentnessAssessment(
        CurrentnessAssessmentId(f"currentness:test/{state.value.lower()}"),
        EvidenceContentDigest.from_bytes(f"base-{state.value}".encode()),
        "subject:test/sync3",
        evidence_ref("currentness-source", b"currentness-source"),
        timestamp(10),
        FreshnessPolicyId("freshness-policy:test/sync3"),
        evidence_ref("freshness-policy", b"freshness-policy"),
        evaluation,
        timestamp(13) if state is CurrentnessState.CURRENT else None,
        state,
        expected_reasons[state] if reasons is None else reasons,
    )
    return bind_dataset_currentness(
        contract_version="ATIS_C10_DATASET_CURRENTNESS_ASSESSMENT_V1",
        dataset_version_id=dataset_version_id,
        dependency_set_id=DependencySetId("dependency-set:test/sync3"),
        dependency_evidence_refs=(evidence_ref("dependency", b"dependency"),),
        dependency_state_digest=EvidenceContentDigest.from_bytes(
            b"dependency-state"
        ),
        assessment=base,
        evidence_refs=(evidence_ref("currentness-assessment", b"assessment"),),
        resource_policy=resource_policy(),
        invalidation_event=invalidation_event,
        propagation_record=propagation_record,
    )


def lifecycle_case(
    suffix: str,
) -> tuple[InvalidationEvent, PropagationRecord]:
    selected_policy = resource_policy()
    graph = ProvenanceGraphId(f"provenance-graph:test/sync3/{suffix}")
    nodes = (
        ProvenanceNodeId(f"provenance-node:test/sync3/{suffix}/0"),
        ProvenanceNodeId(f"provenance-node:test/sync3/{suffix}/1"),
    )
    versions = (
        DATASET_VERSION,
        DatasetVersionId(f"dataset-version:test/sync3-{suffix}-v2"),
    )
    invalidation = InvalidationAssessment(
        InvalidationAssessmentId(f"invalidation-assessment:test/sync3/{suffix}"),
        EvidenceContentDigest.from_bytes(f"invalidation-{suffix}".encode()),
        (InvalidationTriggerId(f"invalidation-trigger:test/sync3/{suffix}"),),
        graph,
        selected_policy.policy_id,
        nodes,
        PropagationState.COMPLETE,
        ("AFFECTED_SET_COMPLETE",),
    )
    mappings = tuple(
        create_dataset_version_mapping(
            contract_version="ATIS_C10_DATASET_VERSION_MAPPING_V1",
            provenance_node_id=node,
            dataset_version_id=version,
            evidence_refs=(
                evidence_ref(
                    f"mapping-{suffix}-{index}",
                    f"mapping-{suffix}-{index}".encode(),
                ),
            ),
        )
        for index, (node, version) in enumerate(zip(nodes, versions, strict=True))
    )
    affected = create_affected_set(
        contract_version="ATIS_C10_AFFECTED_SET_V1",
        assessment=invalidation,
        graph_identity=graph,
        mappings=mappings,
        scope_evidence_refs=(
            evidence_ref(f"scope-{suffix}", f"scope-{suffix}".encode()),
        ),
        resource_policy=selected_policy,
    )
    event = create_invalidation_event(
        contract_version="ATIS_C10_INVALIDATION_EVENT_V1",
        trigger_kind="CORRECTION",
        predecessor_evidence_ref=evidence_ref(
            f"predecessor-{suffix}", f"predecessor-{suffix}".encode()
        ),
        successor_or_correction_ref=evidence_ref(
            f"successor-{suffix}", f"successor-{suffix}".encode()
        ),
        knowledge_from=timestamp(9),
        effective_from=timestamp(8),
        dependency_graph_ref=evidence_ref(
            f"graph-{suffix}", f"graph-{suffix}".encode()
        ),
        affected_set=affected,
        scope_ref=evidence_ref(
            f"event-scope-{suffix}", f"event-scope-{suffix}".encode()
        ),
        reasons=("CORRECTION",),
        evidence_refs=(
            evidence_ref(f"event-{suffix}", f"event-{suffix}".encode()),
        ),
        resource_policy=selected_policy,
    )
    initial = create_propagation_record(
        contract_version="ATIS_C10_PROPAGATION_RECORD_V1",
        invalidation_event=event,
        affected_set=affected,
        evidence_refs=(
            evidence_ref(f"propagation-{suffix}-0", f"propagation-{suffix}-0".encode()),
        ),
        resource_policy=selected_policy,
    )
    progress = advance_propagation(
        predecessor=initial,
        invalidation_event=event,
        affected_set=affected,
        resource_policy=selected_policy,
        processed_dataset_version_ids=affected.dataset_version_ids[:1],
        next_state=PropagationRecordState.IN_PROGRESS,
        reasons=(),
        evidence_refs=(
            evidence_ref(f"propagation-{suffix}-1", f"propagation-{suffix}-1".encode()),
        ),
    )
    complete = advance_propagation(
        predecessor=progress,
        invalidation_event=event,
        affected_set=affected,
        resource_policy=selected_policy,
        processed_dataset_version_ids=affected.dataset_version_ids,
        next_state=PropagationRecordState.COMPLETE,
        reasons=(),
        evidence_refs=(
            evidence_ref(f"propagation-{suffix}-2", f"propagation-{suffix}-2".encode()),
        ),
    )
    return event, complete


def ref_body(value: EvidenceRef) -> dict[str, str]:
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def currentness_preimage(value: DatasetCurrentnessAssessment) -> bytes:
    body = {
        "contract_version": value.contract_version,
        "dataset_version_id": value.dataset_version_id.value,
        "freshness_policy_id": value.freshness_policy_id.value,
        "freshness_policy_ref": ref_body(value.freshness_policy_ref),
        "evaluated_at": value.evaluated_at.value.isoformat(),
        "dependency_set_id": value.dependency_set_id.value,
        "dependency_evidence_refs": [
            ref_body(item) for item in value.dependency_evidence_refs
        ],
        "dependency_state_digest": value.dependency_state_digest.value,
        "state": value.state.value,
        "reasons": list(value.reasons),
        "evidence_refs": [ref_body(item) for item in value.evidence_refs],
        "resource_policy_id": value.resource_policy_id.value,
        "invalidation_event_id": (
            None
            if value.invalidation_event_id is None
            else value.invalidation_event_id.value
        ),
        "affected_set_id": (
            None if value.affected_set_id is None else value.affected_set_id.value
        ),
        "propagation_run_id": (
            None
            if value.propagation_run_id is None
            else value.propagation_run_id.value
        ),
        "lifecycle_reasons": list(value.lifecycle_reasons),
    }
    return (
        b"ATIS:C10:DATASET_CURRENTNESS_ASSESSMENT:1\0"
        + canonical_json(body)
    )


def rebuild_currentness(
    value: DatasetCurrentnessAssessment,
    **changes: object,
) -> DatasetCurrentnessAssessment:
    rebuilt = object.__new__(DatasetCurrentnessAssessment)
    for item in fields(DatasetCurrentnessAssessment):
        if item.name in {"assessment_id", "content_digest"}:
            continue
        object.__setattr__(
            rebuilt,
            item.name,
            changes.get(item.name, getattr(value, item.name)),
        )
    digest = EvidenceContentDigest.from_bytes(currentness_preimage(rebuilt))
    object.__setattr__(rebuilt, "content_digest", digest)
    object.__setattr__(
        rebuilt,
        "assessment_id",
        DatasetCurrentnessAssessmentId(
            f"c10-dataset-currentness:{digest.value.removeprefix('sha256:')}"
        ),
    )
    rebuilt.__post_init__()
    return rebuilt


def claim_bytes(
    *,
    dataset_version_id: str = DATASET_VERSION.value,
    claim_id: str = CLAIM_ID.value,
) -> bytes:
    return canonical_json(
        {
            "contract_version": {
                "family": sync3.SYNC3_CLAIM_CONTRACT_VERSION.family,
                "version": sync3.SYNC3_CLAIM_CONTRACT_VERSION.version,
            },
            "claim_id": claim_id,
            "dataset_version_id": dataset_version_id,
        }
    )


def quality_inputs(
    descriptors: tuple[CompatibilityDescriptor, ...],
    *,
    quality_state: str,
) -> tuple[
    ProductionSequenceEvidence,
    ProductionMetricEvidence,
    ApprovedProductionQualityPolicy | None,
    tuple[ResolvedQualityEvidence, ...],
    ProductionQualityResult,
]:
    book = QualityEvidenceBook()
    expectation = descriptors[0].expected_sequence_evidence.ref
    assert expectation is not None
    book.add_ref(expectation, b"sequence")
    calendar = book.add("calendar", b"calendar")
    knowledge = book.add("sequence-knowledge", b"sequence-knowledge")
    keys = tuple(item.logical_identity.key for item in descriptors)
    expected = keys if quality_state != "invalid" else keys + ("MISSING@T",)
    sequence = ProductionSequenceEvidence(
        ProductionEvidenceState.AVAILABLE,
        ContractVersion("ATIS_C05_PRODUCTION_SEQUENCE_EVIDENCE", 1),
        expectation,
        calendar,
        knowledge,
        expected,
        keys,
        (expectation, calendar, knowledge),
    )
    metrics_ref = book.add("metrics", b"metrics")
    metric_knowledge = book.add("metric-knowledge", b"metric-knowledge")
    metrics = ProductionMetricEvidence(
        ProductionEvidenceState.AVAILABLE,
        ContractVersion("ATIS_C05_PRODUCTION_METRIC_INPUT", 1),
        metrics_ref,
        metric_knowledge,
        tuple(
            ProductionMetric(
                item.quality_input.descriptor_digest,
                "price",
                "101.25",
            )
            for item in descriptors
        ),
        (metrics_ref, metric_knowledge),
    )
    policy: ApprovedProductionQualityPolicy | None
    if quality_state == "unestablished":
        policy = None
    else:
        policy_ref = book.add("approved-policy", b"approved-policy")
        applicability = book.add("applicability", b"applicability")
        policy_knowledge = book.add("policy-knowledge", b"policy-knowledge")
        version = 2 if quality_state == "incompatible" else 1
        policy = ApprovedProductionQualityPolicy(
            ContractVersion("ATIS_C05_PRODUCTION_QUALITY_POLICY", version),
            policy_ref,
            applicability,
            policy_knowledge,
            (ApprovedMetricRule("price", "0", "200"),),
            (policy_ref, applicability, policy_knowledge),
        )
    resolved = book.resolved()
    result = evaluate_production_quality(
        descriptors,
        sequence,
        metrics,
        policy,
        resolved,
    )
    return sequence, metrics, policy, resolved, result


def disposition_reason(disposition: EligibilityDisposition) -> str:
    return {
        EligibilityDisposition.ELIGIBLE: "EXACT_CLAIM_PREREQUISITES_SATISFIED",
        EligibilityDisposition.INELIGIBLE: "C05_PREREQUISITE_RESTRICTIVE",
        EligibilityDisposition.NOT_ESTABLISHED: "QUARANTINE_STATE_NOT_ESTABLISHED",
        EligibilityDisposition.INCOMPATIBLE: "C05_RESULT_INCOMPATIBLE",
    }[disposition]


@dataclass(frozen=True, slots=True)
class Fixture:
    dataset_version_id: DatasetVersionId
    descriptors: tuple[CompatibilityDescriptor, ...]
    sequence: ProductionSequenceEvidence
    metric_evidence: ProductionMetricEvidence
    policy: ApprovedProductionQualityPolicy | None
    resolved_quality_evidence: tuple[ResolvedQualityEvidence, ...]
    supplied_c05_result: ProductionQualityResult
    eligibility_decision: EligibilityDecision
    resolved_c06_evidence: tuple[ResolvedC06Evidence, ...]
    dataset_currentness_assessment: DatasetCurrentnessAssessment | None
    claim_content: bytes

    def arguments(self) -> dict[str, object]:
        return {
            "dataset_version_id": self.dataset_version_id,
            "descriptors": self.descriptors,
            "sequence": self.sequence,
            "metric_evidence": self.metric_evidence,
            "policy": self.policy,
            "resolved_quality_evidence": self.resolved_quality_evidence,
            "supplied_c05_result": self.supplied_c05_result,
            "eligibility_decision": self.eligibility_decision,
            "resolved_c06_evidence": self.resolved_c06_evidence,
            "dataset_currentness_assessment": self.dataset_currentness_assessment,
        }


def make_fixture(
    *,
    quality_state: str = "valid",
    disposition: EligibilityDisposition | None = None,
    c10_state: CurrentnessState = CurrentnessState.CURRENT,
    c10_present: bool = True,
    dataset_version_id: DatasetVersionId = DATASET_VERSION,
    claim_content: bytes | None = None,
    external_currentness: str | None = None,
    knowledge_from: Timestamp | None = None,
    effective_from: Timestamp | None = None,
    descriptors: tuple[CompatibilityDescriptor, ...] | None = None,
    c10_evaluated_at: Timestamp | None = None,
) -> Fixture:
    selected_descriptors = descriptors or (compatibility_descriptor(),)
    sequence, metrics, policy, quality_evidence, c05_result = quality_inputs(
        selected_descriptors,
        quality_state=quality_state,
    )
    default_dispositions = {
        "valid": EligibilityDisposition.ELIGIBLE,
        "invalid": EligibilityDisposition.INELIGIBLE,
        "unestablished": EligibilityDisposition.NOT_ESTABLISHED,
        "incompatible": EligibilityDisposition.INCOMPATIBLE,
    }
    selected_disposition = disposition or default_dispositions[quality_state]
    currentness = (
        make_currentness(
            c10_state,
            dataset_version_id=dataset_version_id,
            evaluated_at=c10_evaluated_at,
        )
        if c10_present
        else None
    )
    c06 = C06EvidenceBook()
    selected_claim = claim_content or claim_bytes(
        dataset_version_id=dataset_version_id.value
    )
    claim_ref = c06.add("claim", selected_claim)
    policy_ref = c06.add("policy", b"eligibility-policy")
    prerequisite_ref = c06.add("prerequisite", b"prerequisite")
    external_ref: EvidenceRef | None = None
    if external_currentness is not None:
        if external_currentness == "exact":
            assert currentness is not None
            content = currentness_preimage(currentness)
        else:
            content = b"mismatched-currentness"
        external_ref = c06.add("external-currentness", content)
    refs: tuple[EvidenceRef, ...] = (claim_ref, policy_ref, prerequisite_ref)
    if external_ref is not None:
        refs += (external_ref,)
    decision = EligibilityDecision(
        ELIGIBILITY_DECISION_VERSION,
        CLAIM_ID,
        sync3.SYNC3_CLAIM_CONTRACT_VERSION,
        claim_ref,
        QuarantineSubjectId("quarantine-subject:test/sync3"),
        c05_quality_result_digest(c05_result),
        EligibilityPolicyId("eligibility-policy:test/sync3"),
        policy_ref,
        knowledge_from or timestamp(11),
        effective_from,
        (prerequisite_ref,),
        external_ref,
        selected_disposition,
        (disposition_reason(selected_disposition),),
        refs,
    )
    return Fixture(
        dataset_version_id,
        selected_descriptors,
        sequence,
        metrics,
        policy,
        quality_evidence,
        c05_result,
        decision,
        (c06.values[claim_ref.key],),
        currentness,
        selected_claim,
    )


def assess(value: Fixture, **overrides: object) -> sync3.Sync3ConsumabilityAssessment:
    arguments = value.arguments()
    arguments.update(overrides)
    return sync3.assess_dataset_consumability(**arguments)  # type: ignore[arg-type]


def test_model_contract_is_exact_frozen_and_slotted() -> None:
    assert [item.value for item in sync3.Sync3ConsumabilityState] == [
        "CONSUMABLE",
        "NOT_CONSUMABLE",
        "NOT_ESTABLISHED",
    ]
    assert [item.name for item in fields(sync3.Sync3DatasetEligibilityClaim)] == [
        "contract_version",
        "claim_id",
        "dataset_version_id",
    ]
    assert [item.name for item in fields(sync3.Sync3ConsumabilityAssessment)] == [
        "contract_version",
        "dataset_version_id",
        "eligibility_decision_id",
        "dataset_currentness_assessment_id",
        "state",
        "reasons",
        "content_digest",
        "assessment_id",
    ]
    value = assess(make_fixture())
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        value.state = sync3.Sync3ConsumabilityState.NOT_CONSUMABLE  # type: ignore[misc]


def test_contract_constants_and_reason_vocabulary_are_exact() -> None:
    assert sync3.SYNC3_CONTRACT_VERSION == (
        "ATIS_STAGE3_SYNC3_MINIMUM_INTEGRATION_CONTRACT_V1"
    )
    assert sync3.SYNC3_CONTENT_DIGEST_DOMAIN == (
        "ATIS:SYNC3:CONSUMABILITY_ASSESSMENT_CONTENT:1"
    )
    assert sync3.SYNC3_ASSESSMENT_ID_DOMAIN == (
        "ATIS:SYNC3:CONSUMABILITY_ASSESSMENT_ID:1"
    )
    assert sync3.SYNC3_REASONS == (
        "SYNC3_DATASET_CONTEXT_NOT_ESTABLISHED",
        "SYNC3_DATASET_CONTEXT_MISMATCH",
        "SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED",
        "SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH",
    )


def test_content_digest_and_assessment_id_reconstruct_independently() -> None:
    value = assess(make_fixture())
    projection = {
        "contract_version": value.contract_version,
        "dataset_version_id": value.dataset_version_id.value,
        "eligibility_decision_id": value.eligibility_decision_id.value,
        "dataset_currentness_assessment_id": (
            None
            if value.dataset_currentness_assessment_id is None
            else value.dataset_currentness_assessment_id.value
        ),
        "state": value.state.value,
        "reasons": list(value.reasons),
    }
    digest = EvidenceContentDigest.from_bytes(
        sync3.SYNC3_CONTENT_DIGEST_DOMAIN.encode()
        + b"\0"
        + canonical_json(projection)
    )
    assert value.content_digest == digest
    body = dict(projection)
    body["content_digest"] = digest.value
    identity_digest = EvidenceContentDigest.from_bytes(
        sync3.SYNC3_ASSESSMENT_ID_DOMAIN.encode()
        + b"\0"
        + canonical_json(body)
    )
    assert value.assessment_id == sync3.Sync3ConsumabilityAssessmentId(
        f"sync3-consumability:{identity_digest.value.removeprefix('sha256:')}"
    )


def test_replay_is_deterministic_and_inputs_remain_immutable() -> None:
    fixture = make_fixture()
    before = fixture
    assert assess(fixture) == assess(fixture)
    assert fixture == before


def test_authentic_replay_produces_consumable() -> None:
    value = assess(make_fixture())
    assert value.state is sync3.Sync3ConsumabilityState.CONSUMABLE
    assert value.dataset_currentness_assessment_id is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"descriptors": ()},
        {"descriptors": (object(),)},
        {"sequence": object()},
        {"metric_evidence": object()},
        {"policy": object()},
        {"supplied_c05_result": object()},
        {"eligibility_decision": object()},
        {"dataset_currentness_assessment": object()},
    ],
)
def test_wrong_input_types_fail_closed(overrides: dict[str, object]) -> None:
    with pytest.raises((TypeError, sync3.Sync3IntegrityError)):
        assess(make_fixture(), **overrides)


def mutated_c05_results(
    value: ProductionQualityResult,
) -> tuple[ProductionQualityResult, ...]:
    other_digest = EvidenceContentDigest.from_bytes(b"other-descriptor")
    other_ref = evidence_ref("other-c05", b"other-c05")
    return (
        replace(value, descriptor_digests=(other_digest,)),
        replace(value, sequence_coverage=SequenceCoverageState.GAP),
        replace(value, sequence_order=SequenceOrderState.REORDERED),
        replace(value, outlier_states=((other_digest, OutlierState.OUTSIDE_POLICY),)),
        replace(value, missing_logical_keys=("missing",)),
        replace(value, reasons=value.reasons + ("FABRICATED_REASON",)),
        replace(value, evidence_refs=(other_ref,)),
        replace(value, validation_state=ValidationState.INVALID),
    )


def test_each_c05_result_field_is_covered_by_exact_replay_equality() -> None:
    fixture = make_fixture()
    for changed in mutated_c05_results(fixture.supplied_c05_result):
        with pytest.raises(sync3.Sync3IntegrityError, match="REPLAY_RESULT_MISMATCH"):
            assess(fixture, supplied_c05_result=changed)


@pytest.mark.parametrize(
    "mutation",
    (
        "outer_content_digest",
        "semantic_bytes",
        "quality_descriptor_digest",
        "logical_identity",
        "nested_logical_identity",
        "semantic_contract",
        "semantic_content",
        "nested_semantic_inconsistency",
        "representation_contract",
        "expected_sequence",
        "input_ref",
        "metric_inputs_ref",
        "order_evidence_ref",
        "malformed_evidence_ref",
        "sequence_evidence",
    ),
)
def test_cpr_rejects_forged_compatibility_descriptor_graph(mutation: str) -> None:
    descriptor = compatibility_descriptor()
    fixture = make_fixture(descriptors=(descriptor,))
    quality_input = descriptor.quality_input
    if mutation == "outer_content_digest":
        object.__setattr__(
            descriptor,
            "content_digest",
            EvidenceContentDigest.from_bytes(b"forged-descriptor"),
        )
    elif mutation == "semantic_bytes":
        object.__setattr__(descriptor, "semantic_bytes", b"forged-semantic-bytes")
    elif mutation == "quality_descriptor_digest":
        object.__setattr__(
            quality_input,
            "descriptor_digest",
            EvidenceContentDigest.from_bytes(b"forged-quality-input"),
        )
    elif mutation == "logical_identity":
        quality_input = replace(
            quality_input,
            logical_identity=replace(
                descriptor.logical_identity,
                key="forged-logical-identity@T",
            ),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "nested_logical_identity":
        object.__setattr__(descriptor.logical_identity, "key", " padded-identity ")
        quality_input = replace(
            quality_input,
            logical_identity=descriptor.logical_identity,
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "semantic_contract":
        assert quality_input.semantic_content is not None
        quality_input = replace(
            quality_input,
            semantic_content=replace(
                quality_input.semantic_content,
                contract_ref=evidence_ref("forged-semantic-contract", b"contract"),
            ),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "semantic_content":
        assert quality_input.semantic_content is not None
        semantic_ref = evidence_ref("forged-semantic-content", b"semantic")
        quality_input = replace(
            quality_input,
            semantic_content=replace(
                quality_input.semantic_content,
                canonical_bytes_ref=semantic_ref,
                digest=semantic_ref.content_digest,
            ),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "nested_semantic_inconsistency":
        assert quality_input.semantic_content is not None
        semantic_ref = evidence_ref("inconsistent-semantic-ref", b"reference")
        semantic_bytes = b"different-semantic-content"
        semantic_digest = EvidenceContentDigest.from_bytes(semantic_bytes)
        object.__setattr__(
            quality_input.semantic_content,
            "canonical_bytes_ref",
            semantic_ref,
        )
        object.__setattr__(quality_input.semantic_content, "digest", semantic_digest)
        quality_input = replace(
            quality_input,
            semantic_content=quality_input.semantic_content,
        )
        descriptor = replace(
            descriptor,
            canonical_bytes_ref=semantic_ref,
            semantic_digest=semantic_digest,
            quality_input=quality_input,
            semantic_bytes=semantic_bytes,
        )
    elif mutation == "representation_contract":
        quality_input = replace(
            quality_input,
            representation_contract_ref=evidence_ref(
                "forged-representation-contract",
                b"representation",
            ),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "expected_sequence":
        quality_input = replace(
            quality_input,
            expected_sequence_ref=evidence_ref("forged-sequence", b"sequence"),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "input_ref":
        quality_input = replace(
            quality_input,
            input_ref=evidence_ref("forged-observation", b"observation"),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "metric_inputs_ref":
        quality_input = replace(
            quality_input,
            metric_inputs_ref=evidence_ref("forged-metrics", b"metrics"),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "order_evidence_ref":
        quality_input = replace(
            quality_input,
            order_evidence_ref=evidence_ref("forged-order", b"order"),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    elif mutation == "malformed_evidence_ref":
        quality_input = replace(
            quality_input,
            malformed_evidence_ref=evidence_ref("forged-malformed", b"malformed"),
        )
        descriptor = replace(descriptor, quality_input=quality_input)
    else:
        object.__setattr__(
            descriptor.expected_sequence_evidence,
            "evidence_refs",
            (),
        )

    with pytest.raises(sync3.Sync3IntegrityError, match="C05_REPLAY_DESCRIPTOR"):
        assess(fixture, descriptors=(descriptor,))


def test_cpr_rejects_outer_descriptor_reference_type_proxies() -> None:
    ref_fields = (
        "canonical_observation_ref",
        "logical_identity_mapping_ref",
        "semantic_projection_contract_ref",
        "canonical_bytes_ref",
        "material_field_policy_ref",
        "producer_contract_ref",
        "adapter_contract_ref",
    )
    for field_name in ref_fields:
        fixture = make_fixture()
        descriptor = fixture.descriptors[0]
        object.__setattr__(
            descriptor,
            field_name,
            ValueProxy(getattr(descriptor, field_name)),
        )
        with pytest.raises(
            sync3.Sync3IntegrityError,
            match="C05_REPLAY_DESCRIPTOR_BINDING_INVALID",
        ):
            assess(fixture)


def test_cpr_rejects_outer_descriptor_nonexact_field_types() -> None:
    for field_name in ("descriptor_version", "semantic_digest", "content_digest"):
        fixture = make_fixture()
        descriptor = fixture.descriptors[0]
        object.__setattr__(
            descriptor,
            field_name,
            ValueProxy(getattr(descriptor, field_name)),
        )
        with pytest.raises(
            sync3.Sync3IntegrityError,
            match="C05_REPLAY_DESCRIPTOR_BINDING_INVALID",
        ):
            assess(fixture)

    fixture = make_fixture()
    object.__setattr__(
        fixture.descriptors[0],
        "descriptor_evidence_refs",
        list(fixture.descriptors[0].descriptor_evidence_refs),
    )
    with pytest.raises(
        sync3.Sync3IntegrityError,
        match="C05_REPLAY_DESCRIPTOR_BINDING_INVALID",
    ):
        assess(fixture)

    fixture = make_fixture()
    object.__setattr__(
        fixture.descriptors[0],
        "semantic_bytes",
        bytearray(fixture.descriptors[0].semantic_bytes),
    )
    with pytest.raises(
        sync3.Sync3IntegrityError,
        match="C05_REPLAY_DESCRIPTOR_BINDING_INVALID",
    ):
        assess(fixture)


def test_cpr_rejects_same_value_proxy_for_quality_descriptor_digest() -> None:
    fixture = make_fixture()
    quality_input = fixture.descriptors[0].quality_input
    object.__setattr__(
        quality_input,
        "descriptor_digest",
        ValueProxy(quality_input.descriptor_digest),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="C05_REPLAY_DESCRIPTOR"):
        assess(fixture)


@pytest.mark.parametrize(
    "target",
    (
        "sequence_version",
        "metric_version",
        "policy_version",
        "quality_evidence_ref",
        "supplied_result_state",
        "eligibility_disposition",
        "c06_evidence_ref",
        "currentness_state",
    ),
)
def test_mandatory_protected_input_graphs_reject_same_value_proxies(
    target: str,
) -> None:
    fixture = make_fixture()
    if target == "sequence_version":
        object.__setattr__(
            fixture.sequence,
            "version",
            ValueProxy(fixture.sequence.version),
        )
    elif target == "metric_version":
        object.__setattr__(
            fixture.metric_evidence,
            "version",
            ValueProxy(fixture.metric_evidence.version),
        )
    elif target == "policy_version":
        assert fixture.policy is not None
        object.__setattr__(
            fixture.policy,
            "version",
            ValueProxy(fixture.policy.version),
        )
    elif target == "quality_evidence_ref":
        evidence = fixture.resolved_quality_evidence[0]
        object.__setattr__(evidence, "ref", ValueProxy(evidence.ref))
    elif target == "supplied_result_state":
        object.__setattr__(
            fixture.supplied_c05_result,
            "validation_state",
            ValueProxy(fixture.supplied_c05_result.validation_state),
        )
    elif target == "eligibility_disposition":
        object.__setattr__(
            fixture.eligibility_decision,
            "disposition",
            ValueProxy(fixture.eligibility_decision.disposition),
        )
    elif target == "c06_evidence_ref":
        evidence = fixture.resolved_c06_evidence[0]
        object.__setattr__(evidence, "ref", ValueProxy(evidence.ref))
    else:
        assert fixture.dataset_currentness_assessment is not None
        object.__setattr__(
            fixture.dataset_currentness_assessment,
            "state",
            ValueProxy(fixture.dataset_currentness_assessment.state),
        )

    with pytest.raises(
        sync3.Sync3IntegrityError,
        match=(
            "SYNC3_PROTECTED_INPUT_GRAPH_INVALID"
            "|C06_DECISION_IDENTITY_INVALID"
            "|C10_ASSESSMENT_TYPE_INVALID"
        ),
    ):
        assess(fixture)


def test_evaluator_exception_fails_before_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_fixture()

    def fail(*args: object, **kwargs: object) -> ProductionQualityResult:
        raise RuntimeError("evaluator failed")

    monkeypatch.setattr(sync3, "evaluate_production_quality", fail)
    with pytest.raises(sync3.Sync3IntegrityError, match="REPLAY_FAILED"):
        assess(fixture)


def test_missing_duplicate_and_unrelated_replay_evidence_fail() -> None:
    fixture = make_fixture()
    resolved = fixture.resolved_quality_evidence
    with pytest.raises(sync3.Sync3IntegrityError, match="MISSING"):
        assess(fixture, resolved_quality_evidence=resolved[1:])
    with pytest.raises(sync3.Sync3IntegrityError, match="DUPLICATE"):
        assess(fixture, resolved_quality_evidence=resolved + (resolved[0],))
    extra_ref = evidence_ref("unrelated-quality", b"unrelated")
    extra = ResolvedQualityEvidence(extra_ref, b"unrelated")
    with pytest.raises(sync3.Sync3IntegrityError, match="UNRELATED"):
        assess(fixture, resolved_quality_evidence=resolved + (extra,))


def test_digest_invalid_and_conflicting_replay_evidence_fail() -> None:
    fixture = make_fixture()
    original = fixture.resolved_quality_evidence[0]
    invalid = object.__new__(ResolvedQualityEvidence)
    object.__setattr__(invalid, "ref", original.ref)
    object.__setattr__(invalid, "content", b"wrong")
    with pytest.raises(sync3.Sync3IntegrityError, match="DIGEST_MISMATCH"):
        assess(
            fixture,
            resolved_quality_evidence=(invalid,)
            + fixture.resolved_quality_evidence[1:],
        )
    conflicting_ref = EvidenceRef(
        original.ref.source_id,
        original.ref.dataset_id,
        original.ref.evidence_id,
        EvidenceContentDigest.from_bytes(b"conflict"),
    )
    conflicting = ResolvedQualityEvidence(conflicting_ref, b"conflict")
    with pytest.raises(sync3.Sync3IntegrityError, match="CONFLICTING"):
        assess(
            fixture,
            resolved_quality_evidence=(conflicting,)
            + fixture.resolved_quality_evidence[1:],
        )


def test_c05_c06_digest_binding_fails_closed() -> None:
    fixture = make_fixture()
    changed = replace(
        fixture.eligibility_decision,
        c05_quality_result_digest=EvidenceContentDigest.from_bytes(b"wrong-c05"),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="DIGEST_BINDING"):
        assess(fixture, eligibility_decision=changed)


def test_claim_is_exact_canonical_three_field_contract() -> None:
    fixture = make_fixture()
    assert fixture.claim_content == claim_bytes()
    assert assess(fixture).dataset_version_id == DATASET_VERSION


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"not-json",
        b'{"claim_id":"claim:test/sync3-v1","claim_id":"duplicate","contract_version":{"family":"ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM","version":1},"dataset_version_id":"dataset-version:test/sync3-v1"}',
        canonical_json(
            {
                "contract_version": {
                    "family": "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM",
                    "version": 1,
                },
                "claim_id": CLAIM_ID.value,
                "dataset_version_id": DATASET_VERSION.value,
                "unknown": "field",
            }
        ),
        canonical_json(
            {
                "contract_version": {
                    "family": "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM",
                    "version": True,
                },
                "claim_id": CLAIM_ID.value,
                "dataset_version_id": DATASET_VERSION.value,
            }
        ),
        b'{"contract_version": {"family": "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM", "version": 1}, "claim_id": "claim:test/sync3-v1", "dataset_version_id": "dataset-version:test/sync3-v1"}',
    ],
)
def test_malformed_duplicate_unknown_boolean_and_noncanonical_claims_fail(
    content: bytes,
) -> None:
    fixture = make_fixture(claim_content=content)
    with pytest.raises(sync3.Sync3IntegrityError):
        assess(fixture)


def test_missing_claim_field_wrong_claim_and_dataset_fail() -> None:
    missing = canonical_json(
        {
            "contract_version": {
                "family": "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM",
                "version": 1,
            },
            "claim_id": CLAIM_ID.value,
        }
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="CLAIM_FIELDS"):
        assess(make_fixture(claim_content=missing))
    wrong_claim = make_fixture(claim_content=claim_bytes(claim_id="claim:test/other"))
    with pytest.raises(sync3.Sync3IntegrityError, match="CLAIM_ID_MISMATCH"):
        assess(wrong_claim)
    wrong_dataset = make_fixture(
        claim_content=claim_bytes(dataset_version_id="dataset-version:test/other")
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="DATASET_CONTEXT_MISMATCH"):
        assess(wrong_dataset)


def test_c06_claim_evidence_requires_exact_single_item() -> None:
    fixture = make_fixture()
    resolved = fixture.resolved_c06_evidence
    with pytest.raises(sync3.Sync3IntegrityError, match="MISSING"):
        assess(fixture, resolved_c06_evidence=())
    with pytest.raises(sync3.Sync3IntegrityError, match="DUPLICATE"):
        assess(fixture, resolved_c06_evidence=resolved + (resolved[0],))
    extra = ResolvedC06Evidence(evidence_ref("extra-c06", b"extra"), b"extra")
    with pytest.raises(sync3.Sync3IntegrityError, match="UNRELATED"):
        assess(fixture, resolved_c06_evidence=resolved + (extra,))
    invalid = object.__new__(ResolvedC06Evidence)
    object.__setattr__(invalid, "ref", resolved[0].ref)
    object.__setattr__(invalid, "content", b"digest-invalid-claim")
    with pytest.raises(sync3.Sync3IntegrityError, match="CLAIM_EVIDENCE_INVALID"):
        assess(fixture, resolved_c06_evidence=(invalid,))
    conflicting_ref = EvidenceRef(
        resolved[0].ref.source_id,
        resolved[0].ref.dataset_id,
        resolved[0].ref.evidence_id,
        EvidenceContentDigest.from_bytes(b"conflicting-claim"),
    )
    conflicting = ResolvedC06Evidence(conflicting_ref, b"conflicting-claim")
    with pytest.raises(sync3.Sync3IntegrityError, match="CONFLICTING"):
        assess(fixture, resolved_c06_evidence=(conflicting,))


def test_c06_identity_and_reason_attribution_substitution_fail() -> None:
    fixture = make_fixture()
    forged = replace(fixture.eligibility_decision)
    object.__setattr__(
        forged,
        "decision_id",
        EligibilityDecisionId("c06-eligibility:forged"),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="IDENTITY_INVALID"):
        assess(fixture, eligibility_decision=forged)
    fabricated = replace(
        fixture.eligibility_decision,
        reasons=("FABRICATED_REASON",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="REASON_ATTRIBUTION"):
        assess(fixture, eligibility_decision=fabricated)


def test_c06_evidence_structure_and_c05_reason_applicability_fail_closed() -> None:
    fixture = make_fixture()
    missing_policy_and_prerequisite_refs = replace(
        fixture.eligibility_decision,
        evidence_refs=(fixture.eligibility_decision.claim_contract_ref,),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="EVIDENCE_ATTRIBUTION"):
        assess(
            fixture,
            eligibility_decision=missing_policy_and_prerequisite_refs,
        )
    inapplicable_c05_reason = replace(
        fixture.eligibility_decision,
        disposition=EligibilityDisposition.INELIGIBLE,
        reasons=("C05_PREREQUISITE_RESTRICTIVE",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="REASON_APPLICABILITY"):
        assess(fixture, eligibility_decision=inapplicable_c05_reason)
    restrictive = make_fixture(quality_state="invalid")
    false_positive = replace(
        restrictive.eligibility_decision,
        disposition=EligibilityDisposition.ELIGIBLE,
        reasons=("EXACT_CLAIM_PREREQUISITES_SATISFIED",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="REASON_APPLICABILITY"):
        assess(restrictive, eligibility_decision=false_positive)


@pytest.mark.parametrize(
    ("quality_state", "disposition", "c10_state", "expected"),
    [
        (
            "valid",
            EligibilityDisposition.ELIGIBLE,
            CurrentnessState.CURRENT,
            sync3.Sync3ConsumabilityState.CONSUMABLE,
        ),
        (
            "invalid",
            EligibilityDisposition.INELIGIBLE,
            CurrentnessState.CURRENT,
            sync3.Sync3ConsumabilityState.NOT_CONSUMABLE,
        ),
        (
            "valid",
            EligibilityDisposition.NOT_ESTABLISHED,
            CurrentnessState.CURRENT,
            sync3.Sync3ConsumabilityState.NOT_ESTABLISHED,
        ),
        (
            "valid",
            EligibilityDisposition.ELIGIBLE,
            CurrentnessState.STALE,
            sync3.Sync3ConsumabilityState.NOT_CONSUMABLE,
        ),
        (
            "valid",
            EligibilityDisposition.ELIGIBLE,
            CurrentnessState.UNKNOWN,
            sync3.Sync3ConsumabilityState.NOT_ESTABLISHED,
        ),
        (
            "invalid",
            EligibilityDisposition.INELIGIBLE,
            CurrentnessState.UNKNOWN,
            sync3.Sync3ConsumabilityState.NOT_CONSUMABLE,
        ),
    ],
)
def test_complete_positive_negative_unknown_product_and_precedence(
    quality_state: str,
    disposition: EligibilityDisposition,
    c10_state: CurrentnessState,
    expected: sync3.Sync3ConsumabilityState,
) -> None:
    value = assess(
        make_fixture(
            quality_state=quality_state,
            disposition=disposition,
            c10_state=c10_state,
        )
    )
    assert value.state is expected


def test_c10_absent_exact_route_for_positive_and_restrictive_upstream() -> None:
    positive = assess(make_fixture(c10_present=False))
    restrictive = assess(make_fixture(quality_state="invalid", c10_present=False))
    for value in (positive, restrictive):
        assert value.dataset_currentness_assessment_id is None
        assert value.state is sync3.Sync3ConsumabilityState.NOT_ESTABLISHED
        assert value.reasons == (
            sync3.SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED,
        )


@pytest.mark.parametrize(
    "state",
    [CurrentnessState.CURRENT, CurrentnessState.UNKNOWN, CurrentnessState.STALE],
)
def test_present_c10_always_uses_exact_real_identity(
    state: CurrentnessState,
) -> None:
    fixture = make_fixture(c10_state=state)
    value = assess(fixture)
    assert fixture.dataset_currentness_assessment is not None
    assert value.dataset_currentness_assessment_id == (
        fixture.dataset_currentness_assessment.assessment_id
    )


def test_null_and_real_c10_are_identity_distinct() -> None:
    present = assess(make_fixture())
    absent = assess(make_fixture(c10_present=False))
    assert present.content_digest != absent.content_digest
    assert present.assessment_id != absent.assessment_id


def test_c10_dataset_and_identity_substitution_fail() -> None:
    wrong_dataset = make_currentness(
        CurrentnessState.CURRENT,
        dataset_version_id=DatasetVersionId("dataset-version:test/other"),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="DATASET_CONTEXT_MISMATCH"):
        assess(make_fixture(), dataset_currentness_assessment=wrong_dataset)
    fixture = make_fixture()
    assert fixture.dataset_currentness_assessment is not None
    forged = replace(fixture.dataset_currentness_assessment)
    object.__setattr__(
        forged,
        "assessment_id",
        DatasetCurrentnessAssessmentId("c10-dataset-currentness:forged"),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="IDENTITY_INVALID"):
        assess(fixture, dataset_currentness_assessment=forged)


def test_c10_reason_attribution_rejects_fabrication() -> None:
    fabricated = make_currentness(
        CurrentnessState.CURRENT,
        reasons=("FABRICATED_REASON",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="REASON_ATTRIBUTION"):
        assess(make_fixture(), dataset_currentness_assessment=fabricated)


def test_external_currentness_exact_digest_passes_and_mismatch_fails() -> None:
    assert assess(make_fixture(external_currentness="exact")).state is (
        sync3.Sync3ConsumabilityState.CONSUMABLE
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="EXTERNAL_CURRENTNESS"):
        assess(make_fixture(external_currentness="mismatch"))
    with pytest.raises(sync3.Sync3IntegrityError, match="EXTERNAL_CURRENTNESS"):
        assess(
            make_fixture(
                c10_present=False,
                external_currentness="mismatch",
            )
        )


def test_temporal_boundary_accepts_equality_and_rejects_later_c06() -> None:
    boundary = timestamp(12)
    equality = make_fixture(
        knowledge_from=boundary,
        effective_from=boundary,
        c10_evaluated_at=boundary,
    )
    assert assess(equality).state is sync3.Sync3ConsumabilityState.CONSUMABLE
    with pytest.raises(sync3.Sync3IntegrityError, match="KNOWLEDGE_AFTER"):
        assess(
            make_fixture(
                knowledge_from=timestamp(13),
                c10_evaluated_at=boundary,
            )
        )
    with pytest.raises(sync3.Sync3IntegrityError, match="EFFECTIVE_TIME_AFTER"):
        assess(
            make_fixture(
                effective_from=timestamp(13),
                c10_evaluated_at=boundary,
            )
        )


def test_later_boundary_requires_new_c10_and_new_assessment() -> None:
    first_fixture = make_fixture(c10_evaluated_at=timestamp(12))
    second_fixture = make_fixture(c10_evaluated_at=timestamp(13))
    first = assess(first_fixture)
    first_snapshot = first
    second = assess(second_fixture)
    assert first == first_snapshot
    assert first.dataset_currentness_assessment_id != (
        second.dataset_currentness_assessment_id
    )
    assert first.assessment_id != second.assessment_id


def test_c10_invalidation_and_propagation_remain_authoritative() -> None:
    event, complete = lifecycle_case("primary")
    base_fixture = make_fixture()
    original = base_fixture.dataset_currentness_assessment
    assert original is not None
    pending = make_currentness(
        CurrentnessState.CURRENT,
        invalidation_event=event,
    )
    pending_result = assess(
        base_fixture,
        dataset_currentness_assessment=pending,
    )
    assert pending.state is CurrentnessState.UNKNOWN
    assert pending.lifecycle_reasons == ("PROPAGATION_PENDING",)
    assert "PROPAGATION_PENDING" in pending_result.reasons
    assert pending_result.state is sync3.Sync3ConsumabilityState.NOT_ESTABLISHED

    completed = make_currentness(
        CurrentnessState.CURRENT,
        invalidation_event=event,
        propagation_record=complete,
    )
    completed_result = assess(
        base_fixture,
        dataset_currentness_assessment=completed,
    )
    assert completed.state is CurrentnessState.CURRENT
    assert completed.lifecycle_reasons == ("PROPAGATION_COMPLETE",)
    assert "PROPAGATION_COMPLETE" in completed_result.reasons
    assert completed_result.state is sync3.Sync3ConsumabilityState.CONSUMABLE
    assert pending_result.assessment_id != completed_result.assessment_id
    assert base_fixture.dataset_currentness_assessment == original

    substituted_event, _ = lifecycle_case("substituted")
    with pytest.raises(PropagationError, match="SUBSTITUTION"):
        make_currentness(
            CurrentnessState.CURRENT,
            invalidation_event=substituted_event,
            propagation_record=complete,
        )


def test_c10_lifecycle_reasons_require_coherent_lifecycle_identities() -> None:
    base = make_currentness(CurrentnessState.CURRENT)
    forged_complete = rebuild_currentness(
        base,
        reasons=tuple(sorted(base.reasons + ("PROPAGATION_COMPLETE",))),
        lifecycle_reasons=("PROPAGATION_COMPLETE",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="LIFECYCLE_IDENTITY"):
        assess(
            make_fixture(),
            dataset_currentness_assessment=forged_complete,
        )
    forged_pending = rebuild_currentness(
        base,
        state=CurrentnessState.UNKNOWN,
        reasons=tuple(sorted(base.reasons + ("PROPAGATION_PENDING",))),
        lifecycle_reasons=("PROPAGATION_PENDING",),
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="LIFECYCLE_IDENTITY"):
        assess(
            make_fixture(),
            dataset_currentness_assessment=forged_pending,
        )


def test_c10_evidence_refs_require_exact_protected_factory_invariants() -> None:
    base = make_currentness(CurrentnessState.CURRENT)
    dependency_ref = base.dependency_evidence_refs[0]
    assessment_ref = base.evidence_refs[0]
    extra_dependency = evidence_ref("extra-dependency", b"extra-dependency")
    canonical_dependencies = canonicalize_evidence_refs(
        (dependency_ref, extra_dependency)
    )
    conflicting_dependency = EvidenceRef(
        dependency_ref.source_id,
        dependency_ref.dataset_id,
        dependency_ref.evidence_id,
        EvidenceContentDigest.from_bytes(b"conflicting-dependency"),
    )
    invalid_changes: tuple[dict[str, object], ...] = (
        {"dependency_evidence_refs": ()},
        {"evidence_refs": ()},
        {"dependency_evidence_refs": (dependency_ref, dependency_ref)},
        {"evidence_refs": (assessment_ref, assessment_ref)},
        {"dependency_evidence_refs": list(base.dependency_evidence_refs)},
        {"dependency_evidence_refs": tuple(reversed(canonical_dependencies))},
        {
            "dependency_evidence_refs": (
                dependency_ref,
                conflicting_dependency,
            )
        },
    )
    for changes in invalid_changes:
        forged = rebuild_currentness(base, **changes)
        with pytest.raises(sync3.Sync3IntegrityError, match="EVIDENCE_REFS"):
            assess(
                make_fixture(),
                dataset_currentness_assessment=forged,
            )


def test_c10_rejects_same_value_wrong_component_typed_ids() -> None:
    base = make_currentness(CurrentnessState.CURRENT)
    wrong_policy_type = rebuild_currentness(
        base,
        freshness_policy_id=EligibilityPolicyId(base.freshness_policy_id.value),
    )
    wrong_dependency_type = rebuild_currentness(
        base,
        dependency_set_id=DatasetVersionId(base.dependency_set_id.value),
    )
    wrong_resource_policy_type = rebuild_currentness(
        base,
        resource_policy_id=EligibilityPolicyId(base.resource_policy_id.value),
    )
    for forged in (
        wrong_policy_type,
        wrong_dependency_type,
        wrong_resource_policy_type,
    ):
        with pytest.raises(sync3.Sync3IntegrityError, match="TYPE_INVALID"):
            assess(
                make_fixture(),
                dataset_currentness_assessment=forged,
            )


def test_reason_union_is_complete_sorted_distinct_and_order_independent() -> None:
    fixture = make_fixture(quality_state="invalid", c10_state=CurrentnessState.UNKNOWN)
    value = assess(fixture)
    expected = tuple(
        sorted(
            set(
                fixture.supplied_c05_result.reasons
                + fixture.eligibility_decision.reasons
                + fixture.dataset_currentness_assessment.reasons  # type: ignore[union-attr]
            )
        )
    )
    assert value.reasons == expected
    assert sync3._canonical_reasons(
        (
            "QUARANTINE_STATE_NOT_ESTABLISHED",
            "C05_PREREQUISITE_RESTRICTIVE",
            "QUARANTINE_STATE_NOT_ESTABLISHED",
        )
    ) == (
        "C05_PREREQUISITE_RESTRICTIVE",
        "QUARANTINE_STATE_NOT_ESTABLISHED",
    )


def test_descriptor_input_order_has_no_assessment_authority() -> None:
    first = compatibility_descriptor()
    second = compatibility_descriptor(
        logical_identity=replace(first.logical_identity, key="XYZ@2026-01-03")
    )
    forward_fixture = make_fixture(descriptors=(first, second))
    reverse_fixture = make_fixture(descriptors=(second, first))
    forward = assess(forward_fixture)
    reverse = assess(reverse_fixture)
    assert forward == reverse


def test_state_and_reason_changes_are_identity_sensitive() -> None:
    base = assess(make_fixture())
    assert base.dataset_currentness_assessment_id is not None
    c10_id = base.dataset_currentness_assessment_id.value
    changed_state_digest = sync3._content_digest(
        contract_version=base.contract_version,
        dataset_version_id=base.dataset_version_id,
        eligibility_decision_id=base.eligibility_decision_id,
        dataset_currentness_assessment_id=c10_id,
        state=sync3.Sync3ConsumabilityState.NOT_CONSUMABLE,
        reasons=base.reasons,
    )
    changed_reason_set = tuple(
        sorted(set(base.reasons + ("EVALUATION_AT_OR_AFTER_FRESHNESS_BOUNDARY",)))
    )
    changed_reasons_digest = sync3._content_digest(
        contract_version=base.contract_version,
        dataset_version_id=base.dataset_version_id,
        eligibility_decision_id=base.eligibility_decision_id,
        dataset_currentness_assessment_id=c10_id,
        state=base.state,
        reasons=changed_reason_set,
    )
    changed_state_id = sync3._assessment_id(
        contract_version=base.contract_version,
        dataset_version_id=base.dataset_version_id,
        eligibility_decision_id=base.eligibility_decision_id,
        dataset_currentness_assessment_id=c10_id,
        state=sync3.Sync3ConsumabilityState.NOT_CONSUMABLE,
        reasons=base.reasons,
        content_digest=changed_state_digest,
    )
    changed_reasons_id = sync3._assessment_id(
        contract_version=base.contract_version,
        dataset_version_id=base.dataset_version_id,
        eligibility_decision_id=base.eligibility_decision_id,
        dataset_currentness_assessment_id=c10_id,
        state=base.state,
        reasons=changed_reason_set,
        content_digest=changed_reasons_digest,
    )
    assert len(
        {base.content_digest, changed_state_digest, changed_reasons_digest}
    ) == 3
    assert len({base.assessment_id, changed_state_id, changed_reasons_id}) == 3


def test_integrity_failures_never_become_ordinary_reasons() -> None:
    fixture = make_fixture()
    bad = replace(
        fixture.eligibility_decision,
        c05_quality_result_digest=EvidenceContentDigest.from_bytes(b"bad"),
    )
    with pytest.raises(sync3.Sync3IntegrityError) as captured:
        assess(fixture, eligibility_decision=bad)
    assert not hasattr(captured.value, "reasons")


def test_c08_c09_c11_and_unrelated_objects_cannot_substitute() -> None:
    fixture = make_fixture()
    for field_name in (
        "eligibility_decision",
        "dataset_currentness_assessment",
        "dataset_version_id",
    ):
        with pytest.raises((TypeError, sync3.Sync3IntegrityError)):
            assess(fixture, **{field_name: object()})


def test_assessment_has_only_technical_authority_surface() -> None:
    value = assess(make_fixture())
    forbidden = {
        "promote",
        "provider",
        "storage",
        "persist",
        "publish",
        "trade",
        "broker",
        "capital",
        "stage4",
    }
    assert forbidden.isdisjoint(type(value).__dataclass_fields__)
    source = inspect.getsource(sync3)
    assert "requests" not in source
    assert "subprocess" not in source
    assert "socket" not in source


def test_no_file_or_global_state_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_fixture()

    def prohibited_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("file access is prohibited")

    monkeypatch.setattr("builtins.open", prohibited_open)
    first = assess(fixture)
    second = assess(fixture)
    assert first == second


def test_assessment_can_only_be_created_by_the_validated_semantic_entry_point() -> None:
    value = assess(make_fixture(c10_present=False))
    with pytest.raises(sync3.Sync3IntegrityError, match="MUST_BE_DERIVED"):
        replace(value, state=sync3.Sync3ConsumabilityState.CONSUMABLE)
    with pytest.raises(sync3.Sync3IntegrityError, match="MUST_BE_DERIVED"):
        replace(value, content_digest=EvidenceContentDigest.from_bytes(b"forged"))
    fabricated_reasons = ("FABRICATED_REASON",)
    fabricated_digest = sync3._content_digest(
        contract_version=value.contract_version,
        dataset_version_id=value.dataset_version_id,
        eligibility_decision_id=value.eligibility_decision_id,
        dataset_currentness_assessment_id=None,
        state=value.state,
        reasons=fabricated_reasons,
    )
    fabricated_id = sync3._assessment_id(
        contract_version=value.contract_version,
        dataset_version_id=value.dataset_version_id,
        eligibility_decision_id=value.eligibility_decision_id,
        dataset_currentness_assessment_id=None,
        state=value.state,
        reasons=fabricated_reasons,
        content_digest=fabricated_digest,
    )
    with pytest.raises(sync3.Sync3IntegrityError, match="MUST_BE_DERIVED"):
        sync3.Sync3ConsumabilityAssessment(
            value.contract_version,
            value.dataset_version_id,
            value.eligibility_decision_id,
            None,
            value.state,
            fabricated_reasons,
            fabricated_digest,
            fabricated_id,
        )


def test_canonical_claim_version_rejects_wrong_family_and_number() -> None:
    for version in (
        {"family": "OTHER", "version": 1},
        {"family": "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM", "version": 2},
    ):
        content = canonical_json(
            {
                "contract_version": version,
                "claim_id": CLAIM_ID.value,
                "dataset_version_id": DATASET_VERSION.value,
            }
        )
        with pytest.raises(sync3.Sync3IntegrityError, match="CLAIM"):
            assess(make_fixture(claim_content=content))


def test_c10_constructor_rejects_identity_content_conflict() -> None:
    value = make_currentness(CurrentnessState.CURRENT)
    with pytest.raises(LifecycleError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(
            value,
            assessment_id=DatasetCurrentnessAssessmentId(
                "c10-dataset-currentness:forged"
            ),
        )


def test_change_surface_exposes_one_semantic_entry_point() -> None:
    public_callables = {
        name
        for name in sync3.__all__
        if callable(getattr(sync3, name)) and name.startswith("assess_")
    }
    assert public_callables == {"assess_dataset_consumability"}

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

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
    AffectedSet,
    AffectedSetId,
    DatasetCurrentnessAssessment,
    DatasetVersionMapping,
    LifecycleError,
    PropagationError,
    PropagationRecord,
    PropagationRecordState,
    advance_propagation,
    affected_dataset_is_authoritatively_current,
    bind_dataset_currentness,
    create_affected_set,
    create_dataset_version_mapping,
    create_invalidation_event,
    create_propagation_record,
    restart_propagation,
)
from automated_trading_bot.datasets.materialization import DatasetVersionId
from automated_trading_bot.datasets.provenance import (
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencySetId,
    ProvenanceGraphId,
    ProvenanceNodeId,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId, EvidenceContentDigest, EvidenceId, EvidenceRef, SourceId,
)


def ref(name: str) -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:test/lifecycle"),
        DatasetId("dataset:test/lifecycle"),
        EvidenceId(f"evidence:test/{name}"),
        EvidenceContentDigest.from_bytes(name.encode()),
    )


def policy(**overrides: int) -> DatasetLifecycleResourcePolicy:
    names = (
        "MAX_MATERIALIZATION_EVIDENCE_REFS", "MAX_DEPENDENCIES_PER_DATASET_VERSION",
        "MAX_PROVENANCE_NODES", "MAX_PROVENANCE_EDGES", "MAX_AFFECTED_SET_MEMBERS",
        "MAX_INVALIDATION_TRAVERSAL_DEPTH", "MAX_REASONS_PER_RECORD", "MAX_REASON_LENGTH",
        "MAX_POLICY_REFS", "MAX_PUBLICATION_RECORDS_PER_BATCH",
        "MAX_DATASET_VERSIONS_PER_BATCH", "MAX_PROPAGATION_EVENTS_PER_RUN",
    )
    limits = tuple((name, overrides.get(name, 20)) for name in names)
    return DatasetLifecycleResourcePolicy(DatasetLifecycleResourcePolicyId("policy"), ref("policy-ref"), limits)


def timestamp(hour: int = 12) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, hour, tzinfo=timezone.utc))


def currentness(state: CurrentnessState = CurrentnessState.CURRENT) -> CurrentnessAssessment:
    digest = EvidenceContentDigest.from_bytes(b"c")
    return CurrentnessAssessment(CurrentnessAssessmentId("currentness"), digest, "subject", ref("source-evidence"), timestamp(10), FreshnessPolicyId("freshness"), ref("freshness-ref"), timestamp(), timestamp(13), state, (state.value,))


def bound_currentness(*, state: CurrentnessState = CurrentnessState.CURRENT, dataset: str = "dataset-v1", invalidation=None, propagation=None) -> DatasetCurrentnessAssessment:
    return bind_dataset_currentness(contract_version="v1", dataset_version_id=DatasetVersionId(dataset), dependency_set_id=DependencySetId("dependencies"), dependency_evidence_refs=(ref("dep"),), dependency_state_digest=EvidenceContentDigest.from_bytes(b"dep-state"), assessment=currentness(state), evidence_refs=(ref("assessment"),), resource_policy=policy(), invalidation_event=invalidation, propagation_record=propagation)


def invalidation_assessment(count: int = 2, *, graph: str = "graph") -> InvalidationAssessment:
    digest = EvidenceContentDigest.from_bytes(b"i")
    return InvalidationAssessment(InvalidationAssessmentId("assessment"), digest, (InvalidationTriggerId("trigger"),), ProvenanceGraphId(graph), DatasetLifecycleResourcePolicyId("policy"), tuple(ProvenanceNodeId(f"node-{i}") for i in range(count)), PropagationState.COMPLETE, ("AFFECTED_SET_COMPLETE",))


def mapping(index: int, *, dataset: str | None = None) -> DatasetVersionMapping:
    return create_dataset_version_mapping(
        contract_version="v1",
        provenance_node_id=ProvenanceNodeId(f"node-{index}"),
        dataset_version_id=DatasetVersionId(dataset or f"dataset-{index}"),
        evidence_refs=(ref(f"mapping-{index}-{dataset or index}"),),
    )


def affected_set(count: int = 2, *, graph: str = "graph", **policy_overrides: int) -> AffectedSet:
    return create_affected_set(
        contract_version="v1",
        assessment=invalidation_assessment(count, graph=graph),
        graph_identity=ProvenanceGraphId(graph),
        mappings=tuple(mapping(index) for index in range(count)),
        scope_evidence_refs=(ref("scope"),),
        resource_policy=policy(**policy_overrides),
    )


def event(count: int = 2, *, graph: str = "graph", predecessor: str = "before"):
    return create_invalidation_event(contract_version="v1", trigger_kind="CORRECTION", predecessor_evidence_ref=ref(predecessor), successor_or_correction_ref=ref("after"), knowledge_from=timestamp(), effective_from=timestamp(10), dependency_graph_ref=ref("graph-ref"), affected_set=affected_set(count, graph=graph), scope_ref=ref("scope"), reasons=("CORRECTION",), evidence_refs=(ref("event"),), resource_policy=policy())


def initial_record(count: int = 2) -> PropagationRecord:
    invalidation = event(count)
    return create_propagation_record(
        contract_version="v1", invalidation_event=invalidation,
        affected_set=affected_set(count), evidence_refs=(ref("run"),),
        resource_policy=policy(),
    )


def record(state: PropagationRecordState, processed: int, *, reasons: tuple[str, ...] = (), refs: tuple[EvidenceRef, ...] = (ref("run"),)) -> PropagationRecord:
    invalidation = event()
    scope = affected_set()
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    if state is PropagationRecordState.NOT_STARTED:
        return initial
    predecessor = initial
    if processed:
        progress_count = 1 if state is PropagationRecordState.COMPLETE else processed
        predecessor = advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:progress_count], next_state=PropagationRecordState.IN_PROGRESS, reasons=reasons if state is PropagationRecordState.IN_PROGRESS else (), evidence_refs=refs if state is PropagationRecordState.IN_PROGRESS else (ref("progress"),))
        if state is PropagationRecordState.IN_PROGRESS:
            return predecessor
    return advance_propagation(predecessor=predecessor, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:processed], next_state=state, reasons=reasons, evidence_refs=refs)


def test_dataset_currentness_binds_complete_semantic_body_deterministically() -> None:
    assert bound_currentness() == bound_currentness()


def test_dataset_currentness_identity_changes_with_dataset_dependency_and_evaluation() -> None:
    original = bound_currentness()
    changed = bind_dataset_currentness(contract_version="v1", dataset_version_id=DatasetVersionId("dataset-v2"), dependency_set_id=DependencySetId("dependencies"), dependency_evidence_refs=(ref("dep"),), dependency_state_digest=EvidenceContentDigest.from_bytes(b"dep-state"), assessment=currentness(), evidence_refs=(ref("assessment"),), resource_policy=policy())
    assert changed.assessment_id != original.assessment_id


def test_dataset_currentness_identity_body_conflict_rejects() -> None:
    value = bound_currentness()
    with pytest.raises(LifecycleError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(value, dataset_version_id=DatasetVersionId("forged"))


def test_dataset_currentness_requires_no_manifest_and_is_deterministic() -> None:
    assert "manifest_id" not in DatasetCurrentnessAssessment.__dataclass_fields__
    assert bound_currentness() == bound_currentness()


def test_invalidation_event_is_deterministic_and_exact() -> None:
    assert event() == event()
    assert len(event().affected_dataset_version_ids) == 2


def test_invalidation_event_reorders_affected_versions() -> None:
    first = event()
    reversed_set = create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(1), mapping(0)), scope_evidence_refs=(ref("scope"),), resource_policy=policy())
    second = create_invalidation_event(contract_version="v1", trigger_kind="CORRECTION", predecessor_evidence_ref=ref("before"), successor_or_correction_ref=ref("after"), knowledge_from=timestamp(), effective_from=timestamp(10), dependency_graph_ref=ref("graph-ref"), affected_set=reversed_set, scope_ref=ref("scope"), reasons=("CORRECTION",), evidence_refs=(ref("event"),), resource_policy=policy())
    assert first == second


def test_invalidation_requires_complete_affected_scope() -> None:
    value = replace(invalidation_assessment(), propagation_state=PropagationState.UNKNOWN)
    with pytest.raises(LifecycleError, match="EXACT_AFFECTED_SET_NOT_ESTABLISHED"):
        create_affected_set(contract_version="v1", assessment=value, graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0), mapping(1)), scope_evidence_refs=(ref("s"),), resource_policy=policy())


def test_dataset_version_mapping_is_deterministic() -> None:
    assert mapping(0) == mapping(0)


def test_dataset_version_mapping_identity_body_conflict_rejects() -> None:
    with pytest.raises(LifecycleError, match="DATASET_VERSION_MAPPING_IDENTITY_CONTENT_CONFLICT"):
        replace(mapping(0), dataset_version_id=DatasetVersionId("forged"))


def test_affected_set_resolves_exact_complete_mapping() -> None:
    value = affected_set()
    assert value.affected_node_ids == (ProvenanceNodeId("node-0"), ProvenanceNodeId("node-1"))
    assert value.dataset_version_ids == (DatasetVersionId("dataset-0"), DatasetVersionId("dataset-1"))


def test_affected_set_missing_mapping_rejects() -> None:
    with pytest.raises(LifecycleError, match="MISSING_DATASET_VERSION_MAPPING"):
        create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0),), scope_evidence_refs=(ref("scope"),), resource_policy=policy())


def test_affected_set_conflicting_same_node_mapping_rejects() -> None:
    with pytest.raises(LifecycleError, match="CONFLICTING_DATASET_VERSION_MAPPING"):
        create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0), mapping(0, dataset="other"), mapping(1)), scope_evidence_refs=(ref("scope"),), resource_policy=policy())


def test_affected_set_unrelated_mapping_rejects() -> None:
    with pytest.raises(LifecycleError, match="UNRELATED_DATASET_VERSION_MAPPING"):
        create_affected_set(contract_version="v1", assessment=invalidation_assessment(1), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0), mapping(1)), scope_evidence_refs=(ref("scope"),), resource_policy=policy())


def test_equal_count_wrong_dataset_version_substitution_rejects() -> None:
    with pytest.raises(LifecycleError, match="AFFECTED_SET_IDENTITY_CONTENT_CONFLICT"):
        replace(
            affected_set(),
            dataset_version_ids=(DatasetVersionId("wrong-0"), DatasetVersionId("wrong-1")),
        )


def test_affected_set_identity_is_deterministic() -> None:
    first = affected_set()
    second = create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(1), mapping(0)), scope_evidence_refs=(ref("scope"),), resource_policy=policy())
    assert first == second


def test_forged_affected_set_identity_body_rejects() -> None:
    with pytest.raises(LifecycleError, match="AFFECTED_SET_IDENTITY_CONTENT_CONFLICT"):
        replace(affected_set(), dataset_version_ids=(DatasetVersionId("forged"),))


def test_affected_set_graph_substitution_rejects() -> None:
    with pytest.raises(LifecycleError, match="INVALIDATION_GRAPH_SUBSTITUTION"):
        create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("other"), mappings=(mapping(0), mapping(1)), scope_evidence_refs=(ref("scope"),), resource_policy=policy())


def test_affected_set_resource_policy_identity_mismatch_rejects() -> None:
    other = replace(policy(), policy_id=DatasetLifecycleResourcePolicyId("other"))
    with pytest.raises(LifecycleError, match="AFFECTED_SET_RESOURCE_POLICY_IDENTITY_CONFLICT"):
        create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0), mapping(1)), scope_evidence_refs=(ref("scope"),), resource_policy=other)


def test_affected_set_member_limit_rejects() -> None:
    with pytest.raises(LifecycleError, match="MAX_AFFECTED_SET_MEMBERS_EXHAUSTED"):
        affected_set(2, MAX_AFFECTED_SET_MEMBERS=1)


def test_verified_empty_affected_set() -> None:
    value = affected_set(0)
    assert value.affected_node_ids == ()
    assert value.dataset_version_ids == ()


def test_invalidation_event_consumes_exact_affected_set() -> None:
    value = affected_set()
    invalidation = create_invalidation_event(contract_version="v1", trigger_kind="X", predecessor_evidence_ref=ref("a"), successor_or_correction_ref=ref("b"), knowledge_from=timestamp(), effective_from=None, dependency_graph_ref=ref("g"), affected_set=value, scope_ref=ref("s"), reasons=("R",), evidence_refs=(ref("e"),), resource_policy=policy())
    assert invalidation.affected_set_id == value.affected_set_id
    assert invalidation.affected_dataset_version_ids == value.dataset_version_ids


@pytest.mark.parametrize(("state", "processed"), [(PropagationRecordState.NOT_STARTED, 0), (PropagationRecordState.IN_PROGRESS, 1), (PropagationRecordState.COMPLETE, 2)])
def test_positive_propagation_states(state: PropagationRecordState, processed: int) -> None:
    assert record(state, processed).state is state


def test_failed_requires_reason_and_evidence() -> None:
    assert record(PropagationRecordState.FAILED, 1, reasons=("IO_FAILURE",)).reasons == ("IO_FAILURE",)
    with pytest.raises(PropagationError, match="RESTRICTIVE_STATE_REASONS_REQUIRED"):
        record(PropagationRecordState.FAILED, 1)
    with pytest.raises(LifecycleError, match="EVIDENCE_NOT_ESTABLISHED"):
        record(PropagationRecordState.FAILED, 1, reasons=("FAIL",), refs=())


def test_not_established_allows_absent_evidence_but_requires_reason() -> None:
    value = record(PropagationRecordState.NOT_ESTABLISHED, 1, reasons=("EVIDENCE_UNAVAILABLE",), refs=())
    assert value.evidence_refs == ()
    with pytest.raises(PropagationError, match="RESTRICTIVE_STATE_REASONS_REQUIRED"):
        record(PropagationRecordState.NOT_ESTABLISHED, 1, refs=())


def test_positive_states_reject_reasons() -> None:
    with pytest.raises(PropagationError, match="PROGRESSION_STATE_REASONS_PROHIBITED"):
        record(PropagationRecordState.IN_PROGRESS, 1, reasons=("NO",))


def test_reasons_collapse_sort_and_bind_identity() -> None:
    first = record(PropagationRecordState.FAILED, 1, reasons=("B", "A", "A"))
    second = record(PropagationRecordState.FAILED, 1, reasons=("A", "B"))
    assert first == second
    assert first.reasons == ("A", "B")


@pytest.mark.parametrize("reasons", [("",), (" padded",), ("padded ",), ("e\u0301",)])
def test_invalid_reason_rejects(reasons: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        record(PropagationRecordState.FAILED, 1, reasons=reasons)


def test_reason_resource_limits_reject_without_truncation() -> None:
    invalidation = event()
    scope = affected_set()
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    with pytest.raises(LifecycleError, match="MAX_REASONS_PER_RECORD_EXHAUSTED"):
        advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(MAX_REASONS_PER_RECORD=1), processed_dataset_version_ids=(), next_state=PropagationRecordState.FAILED, reasons=("A", "B"), evidence_refs=(ref("e"),))
    with pytest.raises(LifecycleError, match="MAX_REASON_LENGTH_EXHAUSTED"):
        advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(MAX_REASON_LENGTH=3), processed_dataset_version_ids=(), next_state=PropagationRecordState.FAILED, reasons=("LONG",), evidence_refs=(ref("e"),))


def test_processed_remaining_reconcile_exactly() -> None:
    value = record(PropagationRecordState.IN_PROGRESS, 1)
    assert set(value.processed_dataset_version_ids).isdisjoint(value.remaining_dataset_version_ids)
    assert set(value.processed_dataset_version_ids) | set(value.remaining_dataset_version_ids) == set(event().affected_dataset_version_ids)


def test_incomplete_never_complete() -> None:
    invalidation = event()
    scope = affected_set()
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    progress = advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:1], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("progress"),))
    with pytest.raises(PropagationError, match="INCOMPLETE_PROPAGATION_AS_COMPLETE"):
        advance_propagation(predecessor=progress, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:1], next_state=PropagationRecordState.COMPLETE, reasons=(), evidence_refs=(ref("e"),))


def test_exactly_processed_requires_complete_state() -> None:
    invalidation = event()
    scope = affected_set()
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    with pytest.raises(PropagationError, match="EXACT_COMPLETION_STATE_REQUIRED"):
        advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids, next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("e"),))


def test_restart_identity_is_bound_and_substitution_changes_identity() -> None:
    failed = record(PropagationRecordState.FAILED, 1, reasons=("FAIL",))
    restarted = restart_propagation(predecessor=failed, invalidation_event=event(), affected_set=affected_set(), resource_policy=policy(), restart_evidence_refs=(ref("restart"),))
    assert restarted.restart_identity == failed.propagation_run_id
    assert restarted.propagation_run_id != failed.propagation_run_id


def test_propagation_identity_body_conflict_rejects() -> None:
    value = record(PropagationRecordState.IN_PROGRESS, 1)
    with pytest.raises(PropagationError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(value, reasons=("FORGED",))


def test_graph_and_affected_set_substitution_reject() -> None:
    invalidation = event()
    initial = initial_record()
    with pytest.raises(PropagationError, match="SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=event(graph="other"), affected_set=affected_set(graph="other"), resource_policy=policy(), processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("e"),))
    with pytest.raises(PropagationError, match="AFFECTED_SET_SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=affected_set(1), resource_policy=policy(), processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("e"),))


def test_resource_event_exhaustion_rejects() -> None:
    invalidation = event()
    scope = affected_set()
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    progress = advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:1], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("progress"),))
    with pytest.raises(PropagationError, match="MAX_PROPAGATION_EVENTS_PER_RUN_EXHAUSTED"):
        advance_propagation(predecessor=progress, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(MAX_PROPAGATION_EVENTS_PER_RUN=1), processed_dataset_version_ids=scope.dataset_version_ids, next_state=PropagationRecordState.COMPLETE, reasons=(), evidence_refs=(ref("e"),))


def test_zero_member_affected_set_completes() -> None:
    value = initial_record(0)
    assert value.state is PropagationRecordState.COMPLETE


def test_unresolved_propagation_is_restrictive_without_mutating_currentness() -> None:
    invalidation = event()
    before = bound_currentness()
    assert not affected_dataset_is_authoritatively_current(dataset_version_id=invalidation.affected_dataset_version_ids[0], invalidation_event=invalidation, propagation_record=None)
    assert before == bound_currentness()


def test_complete_propagation_releases_restrictive_consequence() -> None:
    invalidation = event()
    complete = record(PropagationRecordState.COMPLETE, 2)
    assert affected_dataset_is_authoritatively_current(dataset_version_id=invalidation.affected_dataset_version_ids[0], invalidation_event=invalidation, propagation_record=complete)


def test_unaffected_dataset_remains_outside_consequence() -> None:
    assert affected_dataset_is_authoritatively_current(dataset_version_id=DatasetVersionId("unrelated"), invalidation_event=event(), propagation_record=None)


def test_records_are_immutable() -> None:
    with pytest.raises(AttributeError):
        record(PropagationRecordState.IN_PROGRESS, 1).state = PropagationRecordState.COMPLETE  # type: ignore[misc]


def test_no_downstream_authority_fields_exist() -> None:
    forbidden = {"quality", "eligibility", "promotion", "storage", "publication", "trading", "financial", "ai_authority"}
    assert forbidden.isdisjoint(DatasetCurrentnessAssessment.__dataclass_fields__)
    assert forbidden.isdisjoint(PropagationRecord.__dataclass_fields__)


def test_affected_set_identity_has_distinct_domain() -> None:
    assert isinstance(event().affected_set_id, AffectedSetId)


def test_not_started_to_in_progress_is_attributed() -> None:
    invalidation, scope = event(), affected_set()
    initial = initial_record()
    successor = advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:1], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("advance"),))
    assert successor.restart_identity == initial.propagation_run_id
    assert successor.processed_dataset_version_ids == scope.dataset_version_ids[:1]


def test_in_progress_requires_genuine_monotonic_progress() -> None:
    invalidation, scope = event(3), affected_set(3)
    initial = create_propagation_record(contract_version="v1", invalidation_event=invalidation, affected_set=scope, evidence_refs=(ref("initial"),), resource_policy=policy())
    first = advance_propagation(predecessor=initial, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:1], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("first"),))
    second = advance_propagation(predecessor=first, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:2], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("second"),))
    assert set(first.processed_dataset_version_ids) < set(second.processed_dataset_version_ids)
    with pytest.raises(PropagationError, match="IN_PROGRESS_REQUIRES_NEW_PROGRESS"):
        advance_propagation(predecessor=second, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[:2], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("churn"),))
    with pytest.raises(PropagationError, match="PROCESSED_MEMBER_DISAPPEARED"):
        advance_propagation(predecessor=second, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=scope.dataset_version_ids[1:2], next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("lost"),))
    with pytest.raises(PropagationError, match="FOREIGN_PROCESSED_MEMBER"):
        advance_propagation(predecessor=second, invalidation_event=invalidation, affected_set=scope, resource_policy=policy(), processed_dataset_version_ids=second.processed_dataset_version_ids + (DatasetVersionId("foreign"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("foreign"),))


def test_exact_complete_successor_and_terminal_states() -> None:
    complete = record(PropagationRecordState.COMPLETE, 2)
    assert complete.remaining_dataset_version_ids == ()
    assert complete.processed_dataset_version_ids == affected_set().dataset_version_ids
    for terminal in (
        complete,
        record(PropagationRecordState.FAILED, 1, reasons=("FAIL",)),
        record(PropagationRecordState.NOT_ESTABLISHED, 1, reasons=("UNKNOWN",), refs=()),
    ):
        with pytest.raises(PropagationError, match="TERMINAL_PROPAGATION_STATE"):
            advance_propagation(predecessor=terminal, invalidation_event=event(), affected_set=affected_set(), resource_policy=policy(), processed_dataset_version_ids=terminal.processed_dataset_version_ids, next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("illegal"),))


def test_restart_from_failed_and_established_not_established() -> None:
    failed = record(PropagationRecordState.FAILED, 1, reasons=("FAIL",))
    restarted_failed = restart_propagation(predecessor=failed, invalidation_event=event(), affected_set=affected_set(), resource_policy=policy(), restart_evidence_refs=(ref("restart-failed"),))
    assert restarted_failed.processed_dataset_version_ids == failed.processed_dataset_version_ids
    established = record(PropagationRecordState.NOT_ESTABLISHED, 1, reasons=("UNKNOWN",), refs=(ref("known-progress"),))
    restarted_established = restart_propagation(predecessor=established, invalidation_event=event(), affected_set=affected_set(), resource_policy=policy(), restart_evidence_refs=(ref("restart-established"),))
    assert restarted_established.processed_dataset_version_ids == established.processed_dataset_version_ids
    assert restarted_established.restart_identity == established.propagation_run_id


def test_restart_does_not_promote_uncertain_progress() -> None:
    uncertain = record(PropagationRecordState.NOT_ESTABLISHED, 1, reasons=("UNKNOWN",), refs=())
    restarted = restart_propagation(predecessor=uncertain, invalidation_event=event(), affected_set=affected_set(), resource_policy=policy(), restart_evidence_refs=(ref("restart"),))
    assert restarted.processed_dataset_version_ids == ()
    assert restarted.remaining_dataset_version_ids == affected_set().dataset_version_ids


def test_continuation_and_restart_reject_context_substitution() -> None:
    initial = initial_record()
    other_event = event(predecessor="different")
    with pytest.raises(PropagationError, match="PROPAGATION_EVENT_SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=other_event, affected_set=affected_set(), resource_policy=policy(), processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("event-sub"),))
    changed_scope = create_affected_set(contract_version="v1", assessment=invalidation_assessment(), graph_identity=ProvenanceGraphId("graph"), mappings=(mapping(0), mapping(1)), scope_evidence_refs=(ref("other-scope"),), resource_policy=policy())
    with pytest.raises(PropagationError, match="AFFECTED_SET_SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=event(), affected_set=changed_scope, resource_policy=policy(), processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("set-sub"),))
    with pytest.raises(PropagationError, match="SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=event(graph="other"), affected_set=affected_set(graph="other"), resource_policy=policy(), processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("graph-sub"),))
    other_policy = replace(policy(), policy_id=DatasetLifecycleResourcePolicyId("other"))
    with pytest.raises(PropagationError, match="RESOURCE_POLICY_SUBSTITUTION"):
        advance_propagation(predecessor=initial, invalidation_event=event(), affected_set=affected_set(), resource_policy=other_policy, processed_dataset_version_ids=(DatasetVersionId("dataset-0"),), next_state=PropagationRecordState.IN_PROGRESS, reasons=(), evidence_refs=(ref("policy-sub"),))
    failed = record(PropagationRecordState.FAILED, 1, reasons=("FAIL",))
    with pytest.raises(PropagationError, match="PROPAGATION_EVENT_SUBSTITUTION"):
        restart_propagation(predecessor=failed, invalidation_event=other_event, affected_set=affected_set(), resource_policy=policy(), restart_evidence_refs=(ref("restart-sub"),))


def test_changed_expected_digest_rejects() -> None:
    with pytest.raises(PropagationError, match="EXPECTED_AFFECTED_SET_DIGEST_CONFLICT"):
        replace(initial_record(), expected_affected_set_digest=EvidenceContentDigest.from_bytes(b"wrong"))


@pytest.mark.parametrize(
    ("propagation_state", "expected"),
    [
        (None, CurrentnessState.UNKNOWN),
        (PropagationRecordState.NOT_STARTED, CurrentnessState.UNKNOWN),
        (PropagationRecordState.IN_PROGRESS, CurrentnessState.UNKNOWN),
        (PropagationRecordState.FAILED, CurrentnessState.UNKNOWN),
        (PropagationRecordState.NOT_ESTABLISHED, CurrentnessState.UNKNOWN),
        (PropagationRecordState.COMPLETE, CurrentnessState.CURRENT),
    ],
)
def test_affected_currentness_is_restrictive(
    propagation_state: PropagationRecordState | None,
    expected: CurrentnessState,
) -> None:
    propagation = None
    if propagation_state is not None:
        reasons = ("RESTRICTIVE",) if propagation_state in (PropagationRecordState.FAILED, PropagationRecordState.NOT_ESTABLISHED) else ()
        propagation = record(propagation_state, 1 if propagation_state is not PropagationRecordState.COMPLETE else 2, reasons=reasons, refs=() if propagation_state is PropagationRecordState.NOT_ESTABLISHED else (ref("state"),))
    value = bound_currentness(dataset="dataset-0", invalidation=event(), propagation=propagation)
    assert value.state is expected


def test_unaffected_current_base_remains_current() -> None:
    assert bound_currentness(dataset="unaffected", invalidation=event()).state is CurrentnessState.CURRENT


@pytest.mark.parametrize("base", [CurrentnessState.STALE, CurrentnessState.UNKNOWN])
def test_restrictive_base_currentness_never_upgrades(base: CurrentnessState) -> None:
    assert bound_currentness(state=base, dataset="dataset-0", invalidation=event(), propagation=record(PropagationRecordState.COMPLETE, 2)).state is base


def test_complete_propagation_substitution_rejects_currentness() -> None:
    complete = record(PropagationRecordState.COMPLETE, 2)
    with pytest.raises(PropagationError, match="PROPAGATION_EVENT_SUBSTITUTION"):
        bound_currentness(dataset="dataset-0", invalidation=event(predecessor="different"), propagation=complete)
    with pytest.raises(PropagationError, match="SUBSTITUTION"):
        bound_currentness(dataset="dataset-0", invalidation=event(1), propagation=complete)
    with pytest.raises(PropagationError, match="SUBSTITUTION"):
        bound_currentness(dataset="dataset-0", invalidation=event(graph="other"), propagation=complete)


def test_lifecycle_evidence_changes_dataset_currentness_identity() -> None:
    unresolved = bound_currentness(dataset="dataset-0", invalidation=event())
    complete = bound_currentness(dataset="dataset-0", invalidation=event(), propagation=record(PropagationRecordState.COMPLETE, 2))
    assert unresolved.assessment_id != complete.assessment_id
    assert unresolved.invalidation_event_id == event().event_id
    assert unresolved.propagation_run_id is None
    assert complete.propagation_run_id is not None

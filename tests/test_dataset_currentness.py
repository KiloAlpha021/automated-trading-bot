from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from automated_trading_bot.datasets.currentness import (
    CurrentnessState, FreshnessHorizon, FreshnessPolicy, FreshnessPolicyError,
    InvalidationError, InvalidationTrigger, PropagationState,
    assess_currentness, assess_invalidation,
)
from automated_trading_bot.datasets.provenance import (
    REQUIRED_RESOURCE_LIMITS, DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId, DependencyId, DependencyRef,
    ProvenanceEdge, ProvenanceNode, build_dependency_graph,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId, EvidenceContentDigest, EvidenceId, EvidenceRef, SourceId,
)


def ref(name: str, content: bytes | None = None) -> EvidenceRef:
    return EvidenceRef(SourceId("source:test/authority"), DatasetId("dataset:test/currentness"), EvidenceId(f"evidence:test/{name}"), EvidenceContentDigest.from_bytes(name.encode() if content is None else content))


def ts(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, hour, tzinfo=UTC))


def policy(*horizons: FreshnessHorizon) -> FreshnessPolicy:
    return FreshnessPolicy.create(ref("freshness-policy"), tuple(horizons))


def horizon(subject: str = "dataset:test/a", hours: int = 2) -> FreshnessHorizon:
    return FreshnessHorizon(subject, timedelta(hours=hours), ref(f"horizon-{subject}"))


def resource_policy(**overrides: int) -> DatasetLifecycleResourcePolicy:
    limits = {name: 16 for name in REQUIRED_RESOURCE_LIMITS}
    limits.update(overrides)
    return DatasetLifecycleResourcePolicy(DatasetLifecycleResourcePolicyId("resource-policy:test/c10-v1"), ref("resource-policy"), tuple(limits.items()))


def chain_graph(
    *, resource: DatasetLifecycleResourcePolicy | None = None,
    reorder: bool = False, first_relation: str = "DERIVES",
    unrelated_relation: str = "DERIVES",
):
    selected = resource or resource_policy()
    nodes = tuple(ProvenanceNode.create(kind, subject, (ref(f"node-{name}"),)) for name, kind, subject in (
        ("a", "SOURCE", "dependency:test/a"), ("b", "DERIVED", "dataset:test/b"),
        ("c", "DERIVED", "dataset:test/c"), ("d", "DERIVED", "dataset:test/d"),
        ("x", "SOURCE", "dependency:test/x"), ("y", "DERIVED", "dataset:test/y"),
    ))
    by_subject = {node.subject_identity: node for node in nodes}
    edges = (
        ProvenanceEdge.create(by_subject["dependency:test/a"].node_id, by_subject["dataset:test/b"].node_id, first_relation, (ref("edge-ab"),)),
        ProvenanceEdge.create(by_subject["dataset:test/b"].node_id, by_subject["dataset:test/c"].node_id, "DERIVES", (ref("edge-bc"),)),
        ProvenanceEdge.create(by_subject["dataset:test/c"].node_id, by_subject["dataset:test/d"].node_id, "DERIVES", (ref("edge-cd"),)),
        ProvenanceEdge.create(by_subject["dependency:test/x"].node_id, by_subject["dataset:test/y"].node_id, unrelated_relation, (ref("edge-xy"),)),
    )
    dependencies = (
        DependencyRef(DependencyId("dependency:test/a"), EvidenceContentDigest.from_bytes(b"a"), (ref("dep-a"),)),
        DependencyRef(DependencyId("dependency:test/x"), EvidenceContentDigest.from_bytes(b"x"), (ref("dep-x"),)),
    )
    if reorder:
        nodes, edges, dependencies = nodes[::-1], edges[::-1], dependencies[::-1]
    return build_dependency_graph(nodes=nodes, edges=edges, dependencies=dependencies, required_dependencies=tuple(item.dependency_id for item in dependencies), resource_policy=selected)


def trigger_for_a() -> InvalidationTrigger:
    return InvalidationTrigger.create(changed_dependency_ids=(DependencyId("dependency:test/a"),), evidence_refs=(ref("trigger-a"),))


def test_current_before_boundary_and_stale_at_or_after_boundary() -> None:
    selected = policy(horizon())
    before = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(2))
    at = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(3))
    after = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(4))
    assert before.state is CurrentnessState.CURRENT
    assert at.state is CurrentnessState.STALE
    assert after.state is CurrentnessState.STALE


def test_missing_nonapplicable_horizon_and_missing_evidence_are_unknown() -> None:
    for selected, evidence_time in ((policy(), ts(1)), (policy(horizon("dataset:test/other")), ts(1)), (policy(horizon()), None)):
        result = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=evidence_time, policy=selected, evaluation_time=ts(2))
        assert result.state is CurrentnessState.UNKNOWN
        assert result.state is not CurrentnessState.CURRENT


def test_invalid_policy_and_malformed_timestamp_are_rejected() -> None:
    with pytest.raises(FreshnessPolicyError, match="POSITIVE"):
        horizon(hours=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        Timestamp(datetime(2026, 1, 1))
    with pytest.raises(TypeError, match="evaluation_time"):
        assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=policy(horizon()), evaluation_time=datetime.now(UTC))  # type: ignore[arg-type]


def test_conflicting_policy_identity_body_is_rejected() -> None:
    with pytest.raises(FreshnessPolicyError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(policy(horizon()), horizons=(horizon(hours=3),))


def test_exact_duplicate_horizon_collapses_before_policy_identity_derivation() -> None:
    selected = horizon()
    single = policy(selected)
    duplicate = policy(selected, selected)
    assert duplicate == single
    assert duplicate.policy_id == single.policy_id
    assert duplicate.horizons == (selected,)


def test_conflicting_duplicate_horizon_subject_is_rejected() -> None:
    with pytest.raises(FreshnessPolicyError, match="CONFLICTING_FRESHNESS_HORIZON"):
        policy(horizon(hours=2), horizon(hours=3))


def test_freshness_boundary_overflow_is_rejected() -> None:
    evidence_time = Timestamp(datetime.max.replace(tzinfo=UTC))
    selected = policy(FreshnessHorizon("dataset:test/a", timedelta(microseconds=1), ref("overflow-horizon")))
    with pytest.raises(FreshnessPolicyError, match="FRESHNESS_BOUNDARY_OUT_OF_RANGE"):
        assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=evidence_time, policy=selected, evaluation_time=evidence_time)


def test_non_utc_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="UTC offset of zero"):
        Timestamp(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))))


def test_currentness_is_deterministic_and_time_is_attributable() -> None:
    selected = policy(horizon())
    first = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(2))
    repeated = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(2))
    later = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=selected, evaluation_time=ts(4))
    assert first == repeated
    assert first.assessment_id != later.assessment_id
    assert first.evidence_ref == ref("data") and first.evidence_time == ts(1)


def test_currentness_records_are_immutable() -> None:
    result = assess_currentness(subject_identity="dataset:test/a", evidence_ref=ref("data"), evidence_time=ts(1), policy=policy(horizon()), evaluation_time=ts(2))
    with pytest.raises(FrozenInstanceError):
        result.state = CurrentnessState.STALE  # type: ignore[misc]


def test_one_and_multi_hop_invalidation_and_unrelated_branch() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    subjects = {node.node_id: node.subject_identity for node in graph.nodes}
    assert result.propagation_state is PropagationState.COMPLETE
    assert {subjects[item] for item in result.affected_node_ids} == {"dataset:test/b", "dataset:test/c", "dataset:test/d"}
    assert "dependency:test/a" not in {subjects[item] for item in result.affected_node_ids}
    assert "dataset:test/y" not in {subjects[item] for item in result.affected_node_ids}


def test_unsupported_reachable_relation_fails_closed_without_propagating() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected, first_relation="OBSERVES")
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    assert result.propagation_state is PropagationState.UNKNOWN
    assert not result.establishes_complete_scope
    assert result.affected_node_ids == ()
    assert result.reasons == ("INVALIDATION_RELATION_SEMANTICS_NOT_ESTABLISHED",)


def test_unsupported_unrelated_relation_does_not_hide_derives_completion() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected, unrelated_relation="OBSERVES")
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    subjects = {node.node_id: node.subject_identity for node in graph.nodes}
    assert result.propagation_state is PropagationState.COMPLETE
    assert {subjects[item] for item in result.affected_node_ids} == {
        "dataset:test/b", "dataset:test/c", "dataset:test/d",
    }


def test_known_leaf_root_is_complete_with_empty_affected_set() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    leaf = next(node for node in graph.nodes if node.subject_identity == "dataset:test/d")
    trigger = InvalidationTrigger.create(changed_node_ids=(leaf.node_id,), evidence_refs=(ref("trigger-leaf"),))
    result = assess_invalidation(triggers=(trigger,), graph=graph, resource_policy=selected)
    assert result.propagation_state is PropagationState.COMPLETE
    assert result.affected_node_ids == ()


def test_multiple_distinct_roots_produce_canonical_union() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    trigger = InvalidationTrigger.create(
        changed_dependency_ids=(DependencyId("dependency:test/x"), DependencyId("dependency:test/a")),
        evidence_refs=(ref("trigger-a-x"),),
    )
    result = assess_invalidation(triggers=(trigger,), graph=graph, resource_policy=selected)
    subjects = {node.node_id: node.subject_identity for node in graph.nodes}
    assert result.propagation_state is PropagationState.COMPLETE
    assert {subjects[item] for item in result.affected_node_ids} == {
        "dataset:test/b", "dataset:test/c", "dataset:test/d", "dataset:test/y",
    }
    assert result.affected_node_ids == tuple(sorted(result.affected_node_ids, key=lambda item: item.value))


def test_shared_dependency_is_deduplicated() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    root = next(node for node in graph.nodes if node.subject_identity == "dependency:test/a")
    node_trigger = InvalidationTrigger.create(changed_node_ids=(root.node_id,), evidence_refs=(ref("trigger-node-a"),))
    result = assess_invalidation(triggers=(trigger_for_a(), node_trigger), graph=graph, resource_policy=selected)
    assert result.propagation_state is PropagationState.COMPLETE
    assert len(result.affected_node_ids) == 3


def test_reordered_semantic_input_is_deterministic() -> None:
    selected = resource_policy()
    first_graph = chain_graph(resource=selected)
    second_graph = chain_graph(resource=selected, reorder=True)
    dependency_trigger = trigger_for_a()
    root = next(node for node in first_graph.nodes if node.subject_identity == "dependency:test/a")
    node_trigger = InvalidationTrigger.create(changed_node_ids=(root.node_id,), evidence_refs=(ref("trigger-node-a"),))
    first = assess_invalidation(triggers=(dependency_trigger, node_trigger), graph=first_graph, resource_policy=selected)
    second = assess_invalidation(triggers=(node_trigger, dependency_trigger), graph=second_graph, resource_policy=selected)
    assert first == second


def test_exact_duplicate_trigger_is_deterministic() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    trigger = trigger_for_a()
    assert assess_invalidation(triggers=(trigger,), graph=graph, resource_policy=selected) == assess_invalidation(triggers=(trigger, trigger), graph=graph, resource_policy=selected)


def test_invalidation_evidence_refs_are_nonempty_and_exact_duplicates_collapse() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        InvalidationTrigger.create(changed_dependency_ids=(DependencyId("dependency:test/a"),), evidence_refs=())
    evidence = ref("trigger-a")
    single = InvalidationTrigger.create(changed_dependency_ids=(DependencyId("dependency:test/a"),), evidence_refs=(evidence,))
    duplicate = InvalidationTrigger.create(changed_dependency_ids=(DependencyId("dependency:test/a"),), evidence_refs=(evidence, evidence))
    assert duplicate == single
    assert duplicate.evidence_refs == (evidence,)


def test_conflicting_trigger_identity_body_is_rejected() -> None:
    with pytest.raises(InvalidationError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(trigger_for_a(), changed_dependency_ids=(DependencyId("dependency:test/x"),))


@pytest.mark.parametrize(("override", "reason"), (
    ({"MAX_AFFECTED_SET_MEMBERS": 1}, "MAX_AFFECTED_SET_MEMBERS_EXHAUSTED"),
    ({"MAX_INVALIDATION_TRAVERSAL_DEPTH": 1}, "MAX_INVALIDATION_TRAVERSAL_DEPTH_EXHAUSTED"),
    ({"MAX_PROPAGATION_EVENTS_PER_RUN": 1}, "MAX_PROPAGATION_EVENTS_PER_RUN_EXHAUSTED"),
))
def test_resource_exhaustion_never_returns_complete(override: dict[str, int], reason: str) -> None:
    selected = resource_policy(**override)
    graph = chain_graph(resource=selected)
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    assert result.propagation_state is PropagationState.UNKNOWN
    assert result.establishes_complete_scope is False
    assert result.reasons == (reason,)


@pytest.mark.parametrize(("limit", "complete_value", "exhausted_value"), (
    ("MAX_AFFECTED_SET_MEMBERS", 3, 2),
    ("MAX_INVALIDATION_TRAVERSAL_DEPTH", 3, 2),
    ("MAX_PROPAGATION_EVENTS_PER_RUN", 3, 2),
))
def test_exact_resource_boundary_and_first_operation_beyond(
    limit: str, complete_value: int, exhausted_value: int,
) -> None:
    complete_policy = resource_policy(**{limit: complete_value})
    complete_graph = chain_graph(resource=complete_policy)
    complete = assess_invalidation(
        triggers=(trigger_for_a(),), graph=complete_graph,
        resource_policy=complete_policy,
    )
    assert complete.propagation_state is PropagationState.COMPLETE
    assert len(complete.affected_node_ids) == 3

    exhausted_policy = resource_policy(**{limit: exhausted_value})
    exhausted_graph = chain_graph(resource=exhausted_policy)
    exhausted = assess_invalidation(
        triggers=(trigger_for_a(),), graph=exhausted_graph,
        resource_policy=exhausted_policy,
    )
    assert exhausted.propagation_state is PropagationState.UNKNOWN
    assert not exhausted.establishes_complete_scope


def test_unknown_resource_exhaustion_preserves_partial_diagnostics() -> None:
    selected = resource_policy(MAX_INVALIDATION_TRAVERSAL_DEPTH=1)
    graph = chain_graph(resource=selected)
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    subjects = {node.node_id: node.subject_identity for node in graph.nodes}
    assert result.propagation_state is PropagationState.UNKNOWN
    assert result.establishes_complete_scope is False
    assert {subjects[item] for item in result.affected_node_ids} == {"dataset:test/b"}


def test_unresolved_valid_trigger_is_unknown_not_complete() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    trigger = InvalidationTrigger.create(changed_dependency_ids=(DependencyId("dependency:test/missing"),), evidence_refs=(ref("trigger-missing"),))
    result = assess_invalidation(triggers=(trigger,), graph=graph, resource_policy=selected)
    assert result.propagation_state is PropagationState.UNKNOWN
    assert not result.establishes_complete_scope


def test_graph_resource_policy_conflict_is_rejected() -> None:
    graph = chain_graph()
    other = DatasetLifecycleResourcePolicy(DatasetLifecycleResourcePolicyId("resource-policy:test/other"), ref("other-resource-policy"), resource_policy().limits)
    with pytest.raises(InvalidationError, match="POLICY_IDENTITY_CONFLICT"):
        assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=other)


def test_no_c09_mutation_or_downstream_authority_is_manufactured() -> None:
    selected = resource_policy()
    graph = chain_graph(resource=selected)
    original = graph
    result = assess_invalidation(triggers=(trigger_for_a(),), graph=graph, resource_policy=selected)
    assert graph == original
    forbidden = {"manifest", "materialization", "persistence", "publication", "promotion", "quality", "eligibility", "trading", "financial", "provider", "storage"}
    assert forbidden.isdisjoint(result.__dataclass_fields__)

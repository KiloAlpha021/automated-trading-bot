from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from automated_trading_bot.datasets.provenance import (
    REQUIRED_PHYSICAL_RESOURCE_LIMITS,
    REQUIRED_RESOURCE_LIMITS,
    CanonicalDatasetRepresentationId,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencyId,
    DependencyRef,
    EntitlementEvidenceId,
    EntitlementProvenance,
    EntitlementState,
    FoundationError,
    GraphValidationError,
    ManifestFoundationState,
    ProvenanceEdge,
    ProvenanceNode,
    ResourcePolicyError,
    TransformationExecutionId,
    TransformationId,
    TransformationImplementationId,
    TransformationLineage,
    TransformationVersionId,
    build_dependency_graph,
    build_manifest_foundation,
    canonical_json,
)


from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)


PHYSICAL_RESOURCE_LIMITS = {
    "MAX_PERSISTED_OBJECT_BYTES",
    "MAX_BUNDLE_BYTES",
    "MAX_OBJECTS_PER_VERSION",
    "MAX_METADATA_BYTES",
    "MAX_RECOVERY_STAGING_ENTRIES",
}


def ref(name: str, content: bytes | None = None) -> EvidenceRef:
    body = name.encode() if content is None else content
    return EvidenceRef(
        SourceId("source:test/authority"),
        DatasetId("dataset:test/provenance"),
        EvidenceId(f"evidence:test/{name}"),
        EvidenceContentDigest.from_bytes(body),
    )


def policy(**overrides: int) -> DatasetLifecycleResourcePolicy:
    # Test fixtures exercise externally supplied values; production defines no defaults.
    limits = {name: 8 for name in REQUIRED_RESOURCE_LIMITS}
    limits.update(overrides)
    return DatasetLifecycleResourcePolicy(
        DatasetLifecycleResourcePolicyId("resource-policy:test/fixture-v1"),
        ref("resource-policy"),
        tuple(limits.items()),
    )


def graph(order: tuple[int, ...] = (0, 1)):
    nodes = (
        ProvenanceNode.create("SOURCE", "source:test/a", (ref("node-a"),)),
        ProvenanceNode.create("DERIVED", "dataset:test/b", (ref("node-b"),)),
    )
    edge = ProvenanceEdge.create(
        nodes[0].node_id, nodes[1].node_id, "DERIVES", (ref("edge"),)
    )
    dependencies = (
        DependencyRef(DependencyId("dependency:test/a"), EvidenceContentDigest.from_bytes(b"a"), (ref("dep-a"),)),
        DependencyRef(DependencyId("dependency:test/b"), EvidenceContentDigest.from_bytes(b"b"), (ref("dep-b"),)),
    )
    return build_dependency_graph(
        nodes=tuple(nodes[index] for index in order),
        edges=(edge,),
        dependencies=tuple(dependencies[index] for index in order),
        required_dependencies=tuple(item.dependency_id for item in dependencies),
        resource_policy=policy(),
    )


def lineage() -> TransformationLineage:
    return TransformationLineage(
        TransformationId("transformation:test/canonicalize"),
        TransformationVersionId("transformation-version:test/v1"),
        TransformationImplementationId("transformation-implementation:test/sha256-abc"),
        TransformationExecutionId("transformation-execution:test/run-1"),
        ref("transformation-contract"),
        ref("implementation-policy"),
        (DependencyId("dependency:test/a"),),
    )


def test_graph_identity_is_deterministic_and_permutation_stable() -> None:
    first = graph((0, 1))
    second = graph((1, 0))
    assert first.graph_id == second.graph_id
    assert first.dependency_set_id == second.dependency_set_id
    assert first.content_digest == second.content_digest
    assert first.nodes == second.nodes
    assert first.dependencies == second.dependencies


def test_records_are_immutable() -> None:
    node = ProvenanceNode.create("SOURCE", "source:test/a", (ref("node"),))
    with pytest.raises(FrozenInstanceError):
        node.kind = "CHANGED"  # type: ignore[misc]


def test_cycle_and_missing_endpoint_are_rejected() -> None:
    first = ProvenanceNode.create("SOURCE", "source:test/a", (ref("a"),))
    second = ProvenanceNode.create("DERIVED", "dataset:test/b", (ref("b"),))
    forward = ProvenanceEdge.create(first.node_id, second.node_id, "DERIVES", (ref("forward"),))
    backward = ProvenanceEdge.create(second.node_id, first.node_id, "DERIVES", (ref("backward"),))
    with pytest.raises(GraphValidationError, match="CYCLE"):
        build_dependency_graph(
            nodes=(first, second), edges=(forward, backward), dependencies=(),
            required_dependencies=(), resource_policy=policy(),
        )
    with pytest.raises(GraphValidationError, match="ENDPOINT"):
        build_dependency_graph(
            nodes=(first,), edges=(forward,), dependencies=(),
            required_dependencies=(), resource_policy=policy(),
        )


def test_conflicting_dependency_identity_is_rejected() -> None:
    identity = DependencyId("dependency:test/conflict")
    one = DependencyRef(identity, EvidenceContentDigest.from_bytes(b"one"), (ref("one"),))
    two = DependencyRef(identity, EvidenceContentDigest.from_bytes(b"two"), (ref("two"),))
    with pytest.raises(GraphValidationError, match="DEPENDENCY_IDENTITY_CONFLICT"):
        build_dependency_graph(
            nodes=(), edges=(), dependencies=(one, two), required_dependencies=(identity,),
            resource_policy=policy(),
        )


def test_same_dependency_identity_and_content_with_different_evidence_is_rejected() -> None:
    identity = DependencyId("dependency:test/evidence-conflict")
    digest = EvidenceContentDigest.from_bytes(b"same-content")
    one = DependencyRef(identity, digest, (ref("evidence-one"),))
    two = DependencyRef(identity, digest, (ref("evidence-two"),))
    with pytest.raises(GraphValidationError, match="DEPENDENCY_IDENTITY_CONFLICT"):
        build_dependency_graph(
            nodes=(), edges=(), dependencies=(one, two), required_dependencies=(identity,),
            resource_policy=policy(),
        )


def test_exact_duplicate_dependency_has_identical_semantic_output() -> None:
    dependency = DependencyRef(
        DependencyId("dependency:test/exact-duplicate"),
        EvidenceContentDigest.from_bytes(b"same-content"),
        (ref("same-evidence"),),
    )
    single = build_dependency_graph(
        nodes=(), edges=(), dependencies=(dependency,),
        required_dependencies=(dependency.dependency_id,), resource_policy=policy(),
    )
    duplicated = build_dependency_graph(
        nodes=(), edges=(), dependencies=(dependency, dependency),
        required_dependencies=(dependency.dependency_id,), resource_policy=policy(),
    )
    assert duplicated.dependencies == single.dependencies
    assert duplicated.dependency_set_id == single.dependency_set_id
    assert duplicated.graph_id == single.graph_id


def test_missing_and_substituted_dependencies_are_rejected() -> None:
    declared = DependencyRef(
        DependencyId("dependency:test/declared"),
        EvidenceContentDigest.from_bytes(b"declared"),
        (ref("declared"),),
    )
    required = DependencyId("dependency:test/required")
    with pytest.raises(GraphValidationError, match="REQUIRED_DEPENDENCY_NOT_ESTABLISHED"):
        build_dependency_graph(
            nodes=(), edges=(), dependencies=(declared,),
            required_dependencies=(required,), resource_policy=policy(),
        )


def test_malformed_and_conflicting_node_or_edge_identity_is_rejected() -> None:
    node = ProvenanceNode.create("SOURCE", "source:test/a", (ref("node"),))
    with pytest.raises(GraphValidationError, match="NODE_IDENTITY_CONTENT_CONFLICT"):
        ProvenanceNode(node.node_id, "DERIVED", node.subject_identity, node.evidence_refs)
    other = ProvenanceNode.create("DERIVED", "dataset:test/b", (ref("other"),))
    edge = ProvenanceEdge.create(node.node_id, other.node_id, "DERIVES", (ref("edge"),))
    with pytest.raises(GraphValidationError, match="EDGE_IDENTITY_CONTENT_CONFLICT"):
        ProvenanceEdge(edge.edge_id, node.node_id, other.node_id, "COPIES", edge.evidence_refs)


def test_transformation_lineage_requires_attributable_immutable_inputs() -> None:
    value = lineage()
    assert value.implementation_id.value == "transformation-implementation:test/sha256-abc"
    assert value.input_dependencies == (DependencyId("dependency:test/a"),)
    with pytest.raises(GraphValidationError, match="INPUTS_NOT_ESTABLISHED"):
        TransformationLineage(
            value.transformation_id, value.version_id, value.implementation_id,
            value.execution_id, value.contract_ref, value.implementation_policy_ref, (),
        )


def test_entitlement_provenance_is_preserved_and_restrictive() -> None:
    established = EntitlementProvenance(
        EntitlementEvidenceId("entitlement:test/licence"), EntitlementState.ESTABLISHED,
        "provider:test/source", ref("licence"), None, ref("predecessor"), ("ATTRIBUTABLE",),
    )
    unknown = EntitlementProvenance(
        EntitlementEvidenceId("entitlement:test/unknown"), EntitlementState.UNKNOWN,
        None, None, None, established.evidence_ref, ("MATERIAL_EVIDENCE_MISSING",),
    )
    assert established.restrictive is False
    assert unknown.restrictive is True
    assert unknown.predecessor_ref == established.evidence_ref
    with pytest.raises(FoundationError, match="NOT_APPLICABLE_SCOPE_NOT_ESTABLISHED"):
        EntitlementProvenance(
            EntitlementEvidenceId("entitlement:test/na"), EntitlementState.NOT_APPLICABLE,
            None, None, None, None, ("CLAIMED_NOT_APPLICABLE",),
        )


def test_manifest_foundation_is_restrictive_and_has_no_other_authority() -> None:
    unknown = EntitlementProvenance(
        EntitlementEvidenceId("entitlement:test/unknown"), EntitlementState.UNKNOWN,
        None, None, None, None, ("MATERIAL_EVIDENCE_MISSING",),
    )
    result = build_manifest_foundation(
        graph=graph(), lineage=lineage(),
        representation_id=CanonicalDatasetRepresentationId("representation:test/v1"),
        entitlements=(unknown,), materialization_ref=None,
    )
    assert result.state is ManifestFoundationState.NOT_ESTABLISHED
    assert "C08_MATERIALIZATION_NOT_ESTABLISHED" in result.reasons
    assert "ENTITLEMENT_PROVENANCE_RESTRICTIVE" in result.reasons
    assert not result.creates_quality
    assert not result.creates_eligibility
    assert not result.creates_currentness
    assert not result.creates_legal_permission
    assert not result.creates_promotion
    assert not hasattr(result, "persistence_receipt")
    assert not hasattr(result, "publication_receipt")


def test_manifest_foundation_never_self_certifies_even_with_predecessor_ref() -> None:
    result = build_manifest_foundation(
        graph=graph(), lineage=lineage(),
        representation_id=CanonicalDatasetRepresentationId("representation:test/v1"),
        entitlements=(), materialization_ref=ref("external-materialization"),
    )
    assert result.state is ManifestFoundationState.NOT_ESTABLISHED
    assert result.reasons == ("C09_S1_POSITIVE_MANIFEST_NOT_AUTHORIZED",)


def test_resource_policy_is_required_and_exhaustion_never_truncates() -> None:
    with pytest.raises(ResourcePolicyError, match="RESOURCE_POLICY_NOT_ESTABLISHED"):
        build_dependency_graph(
            nodes=(), edges=(), dependencies=(), required_dependencies=(), resource_policy=None,
        )
    nodes = tuple(
        ProvenanceNode.create("SOURCE", f"source:test/{index}", (ref(f"node-{index}"),))
        for index in range(2)
    )
    with pytest.raises(ResourcePolicyError, match="MAX_PROVENANCE_NODES_EXHAUSTED"):
        build_dependency_graph(
            nodes=nodes, edges=(), dependencies=(), required_dependencies=(),
            resource_policy=policy(MAX_PROVENANCE_NODES=1),
        )


def test_resource_policy_has_no_defaults_or_partial_values() -> None:
    with pytest.raises(ResourcePolicyError, match="RESOURCE_POLICY_NOT_ESTABLISHED"):
        DatasetLifecycleResourcePolicy(
            DatasetLifecycleResourcePolicyId("resource-policy:test/incomplete"),
            ref("resource-policy"), (("MAX_PROVENANCE_NODES", 1),),
        )


def test_canonical_json_rejects_non_finite_and_non_nfc_text() -> None:
    with pytest.raises(ValueError, match="NaN and Infinity"):
        canonical_json({"value": math.nan})
    with pytest.raises(ValueError, match="NFC"):
        canonical_json({"value": "e\u0301"})


def test_unknown_fields_are_rejected_by_closed_constructors() -> None:
    with pytest.raises(TypeError):
        DependencyRef(  # type: ignore[call-arg]
            dependency_id=DependencyId("dependency:test/a"),
            content_digest=EvidenceContentDigest.from_bytes(b"a"),
            evidence_refs=(ref("a"),),
            unknown="forbidden",
        )
def test_physical_resource_limit_vocabulary_is_explicit_and_finite() -> None:
    assert PHYSICAL_RESOURCE_LIMITS == REQUIRED_PHYSICAL_RESOURCE_LIMITS
    value = policy(**{name: 8 for name in PHYSICAL_RESOURCE_LIMITS})
    for name in PHYSICAL_RESOURCE_LIMITS:
        assert type(value.value(name)) is int
        assert value.value(name) > 0

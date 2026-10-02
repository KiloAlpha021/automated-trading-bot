from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from automated_trading_bot.datasets.manifest import (
    DatasetManifest,
    DatasetManifestError,
    create_dataset_manifest,
)
from automated_trading_bot.datasets.materialization import (
    DatasetVersionId,
    HistoricalUniverseEvidence,
    LogicalContentId,
    PitMaterializationError,
    PitSufficiencyState,
    materialize_pit_dataset,
)
from automated_trading_bot.datasets.provenance import (
    CanonicalDatasetRepresentationId,
    DependencyId,
    EntitlementState,
    ManifestId,
    ProvenanceEdge,
    ProvenanceNode,
    TransformationExecutionId,
    TransformationId,
    TransformationImplementationId,
    TransformationLineage,
    TransformationVersionId,
    build_dependency_graph,
)
from test_dataset_materialization import ref, request, resource_policy


def manifest_fixture(**request_overrides: object):
    materialization_request = request(**request_overrides)
    result = materialize_pit_dataset(materialization_request)
    policy = materialization_request.resource_policy
    dependency_nodes = tuple(
        ProvenanceNode.create(
            "DEPENDENCY", item.dependency_id.value, (ref(f"node-{index}"),)
        )
        for index, item in enumerate(materialization_request.dependency_refs)
    )
    output = ProvenanceNode.create(
        "DATASET_VERSION", result.dataset_version_id.value, (ref("node-output"),)
    )
    edges = tuple(
        ProvenanceEdge.create(
            node.node_id, output.node_id, "DERIVES", (ref(f"edge-{index}"),)
        )
        for index, node in enumerate(dependency_nodes)
    )
    graph = build_dependency_graph(
        nodes=(output, *dependency_nodes),
        edges=edges,
        dependencies=materialization_request.dependency_refs,
        required_dependencies=tuple(
            item.dependency_id for item in materialization_request.dependency_refs
        ),
        resource_policy=policy,
    )
    transformation = materialization_request.transformation
    lineage = TransformationLineage(
        transformation.transformation_id,
        transformation.version_id,
        transformation.implementation_id,
        transformation.execution_id,
        transformation.contract_ref,
        transformation.implementation_policy_ref,
        tuple(item.dependency_id for item in materialization_request.dependency_refs),
    )
    kwargs = {
        "materialization_request": materialization_request,
        "materialization_result": result,
        "provenance_graph": graph,
        "transformation_lineage": lineage,
        "reference_identity_refs": (ref("reference-identity"),),
        "calendar_identity_refs": (ref("calendar-identity"),),
        "corporate_action_identity_refs": (ref("corporate-action-identity"),),
        "entitlement_provenance_scope_ref": ref("materialization-authority"),
        "entitlement_provenance_completeness_state": (
            EntitlementState.NOT_APPLICABLE
            if request_overrides.get("entitlement_material") is False
            else EntitlementState.ESTABLISHED
        ),
        "external_policy_refs": (ref("external-policy"),),
        "resource_policy": policy,
    }
    return create_dataset_manifest(**kwargs), kwargs


def rebuilt_graph(kwargs: dict[str, object], *, nodes, edges):
    materialization_request = kwargs["materialization_request"]
    return build_dependency_graph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        dependencies=materialization_request.dependency_refs,
        required_dependencies=tuple(
            item.dependency_id for item in materialization_request.dependency_refs
        ),
        resource_policy=kwargs["resource_policy"],
    )


def test_complete_manifest_is_deterministic_and_immutable() -> None:
    first, _ = manifest_fixture()
    second, _ = manifest_fixture()
    assert first == second
    assert isinstance(first.manifest_id, ManifestId)
    with pytest.raises(FrozenInstanceError):
        first.contract_version = "changed"  # type: ignore[misc]


def test_manifest_has_exact_protected_semantic_fields() -> None:
    names = {item.name for item in fields(DatasetManifest)}
    assert names == {
        "manifest_id", "contract_version", "dataset_version_id", "dataset_schema_ref",
        "logical_content_id", "canonical_dataset_representation_id",
        "canonical_representation_contract_ref", "materialization_record_id",
        "source_evidence_refs", "reference_identity_refs", "calendar_identity_refs",
        "corporate_action_identity_refs", "transformation_id",
        "transformation_version_id", "transformation_implementation_id",
        "transformation_execution_id", "dependency_set_id", "dependency_refs",
        "provenance_nodes", "provenance_edges", "pit_cutoff", "limitations",
        "entitlement_provenance_entries", "entitlement_provenance_scope_ref",
        "entitlement_provenance_completeness_state", "external_policy_refs",
        "resource_policy_id", "content_digest",
    }


def test_manifest_id_binds_dataset_version_but_dataset_version_excludes_manifest() -> None:
    value, _ = manifest_fixture()
    with pytest.raises(DatasetManifestError, match="CONTENT_DIGEST_CONFLICT"):
        replace(value, dataset_version_id=DatasetVersionId("c08-dataset-version:forged"))
    assert "manifest_id" not in value.dataset_version_id.__dataclass_fields__


@pytest.mark.parametrize(
    "replacement",
    [
        {"dataset_version_id": DatasetVersionId("c08-dataset-version:forged")},
        {"logical_content_id": LogicalContentId("c08-logical-content:forged")},
    ],
)
def test_exact_materialization_result_pairing_rejects_substitution(replacement: dict[str, object]) -> None:
    _, kwargs = manifest_fixture()
    kwargs["materialization_result"] = replace(kwargs["materialization_result"], **replacement)
    with pytest.raises(DatasetManifestError, match="MATERIALIZATION_RESULT_SUBSTITUTION"):
        create_dataset_manifest(**kwargs)


def test_pit_sufficiency_must_be_established() -> None:
    _, kwargs = manifest_fixture()
    changed_request = request(coverage_complete=False)
    kwargs["materialization_request"] = changed_request
    kwargs["materialization_result"] = materialize_pit_dataset(changed_request)
    with pytest.raises(DatasetManifestError, match="PIT_SUFFICIENCY_NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)


def test_exact_c08_fields_are_bound() -> None:
    value, kwargs = manifest_fixture()
    result = kwargs["materialization_result"]
    record = result.materialization_record
    assert value.dataset_version_id == result.dataset_version_id
    assert value.logical_content_id == result.logical_content_id == record.resulting_logical_content_id
    assert value.materialization_record_id == record.record_id
    assert value.dataset_schema_ref == record.dataset_schema_ref
    assert value.canonical_dataset_representation_id == record.canonical_dataset_representation_id
    assert value.canonical_representation_contract_ref == record.canonical_representation_contract_ref
    assert value.transformation_id == record.transformation_id
    assert value.transformation_version_id == record.transformation_version_id
    assert value.transformation_implementation_id == record.transformation_implementation_id
    assert value.transformation_execution_id == record.transformation_execution_id
    assert value.dependency_set_id == record.dependency_set_id
    assert value.dependency_refs == record.dependency_refs
    assert value.pit_cutoff == record.pit_cutoff
    assert value.source_evidence_refs == record.selected_evidence_refs


def test_provenance_graph_and_dependency_body_are_exact() -> None:
    value, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    assert value.provenance_nodes == graph.nodes
    assert value.provenance_edges == graph.edges
    assert value.dependency_set_id == graph.dependency_set_id
    other_request = request()
    changed_dependency = replace(
        other_request.dependency_refs[0], content_digest=ref("changed-dependency").content_digest
    )
    other_graph = build_dependency_graph(
        nodes=graph.nodes,
        edges=graph.edges,
        dependencies=(changed_dependency,),
        required_dependencies=(changed_dependency.dependency_id,),
        resource_policy=kwargs["resource_policy"],
    )
    kwargs["provenance_graph"] = replace(other_graph, dependency_set_id=graph.dependency_set_id)
    with pytest.raises(DatasetManifestError, match="DEPENDENCY_BODY_SUBSTITUTION"):
        create_dataset_manifest(**kwargs)


def test_provenance_input_permutation_is_deterministic() -> None:
    first, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    kwargs["provenance_graph"] = replace(
        graph, nodes=tuple(reversed(graph.nodes)), edges=tuple(reversed(graph.edges))
    )
    second = create_dataset_manifest(**kwargs)
    assert second == first


def test_valid_exact_provenance_lineage_passes() -> None:
    value, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    dependency_id = kwargs["materialization_request"].dependency_refs[0].dependency_id.value
    dataset_version_id = kwargs["materialization_result"].dataset_version_id.value
    assert any(node.subject_identity == dependency_id for node in graph.nodes)
    assert any(node.subject_identity == dataset_version_id for node in graph.nodes)
    assert value.provenance_nodes == graph.nodes


def test_unrelated_graph_with_same_dependencies_and_policy_rejects() -> None:
    _, kwargs = manifest_fixture()
    unrelated_source = ProvenanceNode.create("DEPENDENCY", "dependency:unrelated", (ref("u1"),))
    unrelated_output = ProvenanceNode.create("DATASET_VERSION", "dataset:unrelated", (ref("u2"),))
    edge = ProvenanceEdge.create(
        unrelated_source.node_id, unrelated_output.node_id, "DERIVES", (ref("ue"),)
    )
    kwargs["provenance_graph"] = rebuilt_graph(
        kwargs, nodes=(unrelated_source, unrelated_output), edges=(edge,)
    )
    with pytest.raises(DatasetManifestError, match="DEPENDENCY_PROVENANCE_NODE_NOT_EXACT"):
        create_dataset_manifest(**kwargs)


def test_graph_targeting_another_dataset_version_rejects() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    dependency = next(node for node in graph.nodes if node.kind == "DEPENDENCY")
    other_output = ProvenanceNode.create("DATASET_VERSION", "dataset-version:other", (ref("other"),))
    edge = ProvenanceEdge.create(dependency.node_id, other_output.node_id, "DERIVES", (ref("other-edge"),))
    kwargs["provenance_graph"] = rebuilt_graph(
        kwargs, nodes=(dependency, other_output), edges=(edge,)
    )
    with pytest.raises(DatasetManifestError, match="DATASET_OUTPUT_PROVENANCE_NODE_NOT_EXACT"):
        create_dataset_manifest(**kwargs)


def test_missing_exact_dataset_output_node_rejects() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    dependency = next(node for node in graph.nodes if node.kind == "DEPENDENCY")
    kwargs["provenance_graph"] = rebuilt_graph(kwargs, nodes=(dependency,), edges=())
    with pytest.raises(DatasetManifestError, match="DATASET_OUTPUT_PROVENANCE_NODE_NOT_EXACT"):
        create_dataset_manifest(**kwargs)


def test_missing_required_dependency_node_rejects() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    output = next(node for node in graph.nodes if node.kind == "DATASET_VERSION")
    kwargs["provenance_graph"] = rebuilt_graph(kwargs, nodes=(output,), edges=())
    with pytest.raises(DatasetManifestError, match="DEPENDENCY_PROVENANCE_NODE_NOT_EXACT"):
        create_dataset_manifest(**kwargs)


def test_required_dependency_without_path_to_output_rejects() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    kwargs["provenance_graph"] = rebuilt_graph(kwargs, nodes=graph.nodes, edges=())
    with pytest.raises(DatasetManifestError, match="DEPENDENCY_LINEAGE_NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)


def test_non_derives_relation_cannot_establish_lineage() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    dependency = next(node for node in graph.nodes if node.kind == "DEPENDENCY")
    output = next(node for node in graph.nodes if node.kind == "DATASET_VERSION")
    edge = ProvenanceEdge.create(dependency.node_id, output.node_id, "MENTIONS", (ref("mentions"),))
    kwargs["provenance_graph"] = rebuilt_graph(
        kwargs, nodes=(dependency, output), edges=(edge,)
    )
    with pytest.raises(DatasetManifestError, match="DEPENDENCY_LINEAGE_NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("transformation_id", TransformationId("transformation:other"), "TRANSFORMATION_IDENTITY_CONFLICT"),
        ("version_id", TransformationVersionId("transformation-version:other"), "TRANSFORMATION_VERSION_IDENTITY_CONFLICT"),
        ("implementation_id", TransformationImplementationId("transformation-implementation:other"), "TRANSFORMATION_IMPLEMENTATION_IDENTITY_CONFLICT"),
        ("execution_id", TransformationExecutionId("transformation-execution:other"), "TRANSFORMATION_EXECUTION_IDENTITY_CONFLICT"),
    ],
)
def test_transformation_lineage_identity_substitution_rejects(
    field: str, replacement: object, message: str
) -> None:
    _, kwargs = manifest_fixture()
    kwargs["transformation_lineage"] = replace(
        kwargs["transformation_lineage"], **{field: replacement}
    )
    with pytest.raises(DatasetManifestError, match=message):
        create_dataset_manifest(**kwargs)


def test_transformation_input_dependency_substitution_rejects() -> None:
    _, kwargs = manifest_fixture()
    kwargs["transformation_lineage"] = replace(
        kwargs["transformation_lineage"],
        input_dependencies=(DependencyId("dependency:other"),),
    )
    with pytest.raises(DatasetManifestError, match="TRANSFORMATION_INPUT_DEPENDENCY_CONFLICT"):
        create_dataset_manifest(**kwargs)


def test_canonical_representation_identity_substitution_rejects() -> None:
    _, kwargs = manifest_fixture()
    materialization_request = kwargs["materialization_request"]
    wrong_representation = replace(
        materialization_request.representation,
        representation_id=CanonicalDatasetRepresentationId("representation:other"),
    )
    kwargs["materialization_request"] = replace(
        materialization_request, representation=wrong_representation
    )
    with pytest.raises(PitMaterializationError, match="TRANSFORMATION_REPRESENTATION_SUBSTITUTION"):
        create_dataset_manifest(**kwargs)


def test_valid_multihop_derives_lineage_passes() -> None:
    _, kwargs = manifest_fixture()
    graph = kwargs["provenance_graph"]
    dependency = next(node for node in graph.nodes if node.kind == "DEPENDENCY")
    output = next(node for node in graph.nodes if node.kind == "DATASET_VERSION")
    intermediate = ProvenanceNode.create("DERIVED", "intermediate:test", (ref("middle"),))
    edges = (
        ProvenanceEdge.create(dependency.node_id, intermediate.node_id, "DERIVES", (ref("first"),)),
        ProvenanceEdge.create(intermediate.node_id, output.node_id, "DERIVES", (ref("second"),)),
    )
    kwargs["provenance_graph"] = rebuilt_graph(
        kwargs, nodes=(output, intermediate, dependency), edges=tuple(reversed(edges))
    )
    value = create_dataset_manifest(**kwargs)
    assert value.provenance_nodes == kwargs["provenance_graph"].nodes


def test_historical_universe_restriction_cannot_be_bypassed() -> None:
    universe = HistoricalUniverseEvidence(True, None, (), False)
    changed_request = request(universe=universe)
    result = materialize_pit_dataset(changed_request)
    assert result.materialization_record.pit_sufficiency_state is not PitSufficiencyState.ESTABLISHED
    _, kwargs = manifest_fixture()
    kwargs["materialization_request"] = changed_request
    kwargs["materialization_result"] = result
    with pytest.raises(DatasetManifestError, match="PIT_SUFFICIENCY_NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)


def test_entitlement_population_is_derived_from_c08_record() -> None:
    value, kwargs = manifest_fixture()
    assert value.entitlement_provenance_entries == (
        kwargs["materialization_result"].materialization_record.entitlement_provenance_inputs_where_material
    )
    assert value.entitlement_provenance_completeness_state is EntitlementState.ESTABLISHED


def test_entitlement_scope_must_be_materialization_bound() -> None:
    _, kwargs = manifest_fixture()
    kwargs["entitlement_provenance_scope_ref"] = ref("unrelated-scope")
    with pytest.raises(DatasetManifestError, match="ENTITLEMENT_SCOPE_NOT_BOUND"):
        create_dataset_manifest(**kwargs)


def test_entitlement_completeness_is_restrictive() -> None:
    _, kwargs = manifest_fixture()
    kwargs["entitlement_provenance_completeness_state"] = EntitlementState.UNKNOWN
    with pytest.raises(DatasetManifestError, match="ENTITLEMENT_COMPLETENESS_NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)
    nonmaterial_request = request(entitlements=(), entitlement_material=False)
    _, nonmaterial = manifest_fixture(entitlements=(), entitlement_material=False)
    nonmaterial["materialization_request"] = nonmaterial_request
    nonmaterial["materialization_result"] = materialize_pit_dataset(nonmaterial_request)
    nonmaterial["entitlement_provenance_completeness_state"] = EntitlementState.NOT_APPLICABLE
    assert create_dataset_manifest(**nonmaterial).entitlement_provenance_entries == ()


@pytest.mark.parametrize(
    "field",
    ["reference_identity_refs", "calendar_identity_refs", "corporate_action_identity_refs"],
)
def test_required_stage3_identity_evidence_fails_closed(field: str) -> None:
    _, kwargs = manifest_fixture()
    kwargs[field] = ()
    with pytest.raises(DatasetManifestError, match="NOT_ESTABLISHED"):
        create_dataset_manifest(**kwargs)


def test_external_policy_refs_are_canonical_and_bounded() -> None:
    first, kwargs = manifest_fixture()
    kwargs["external_policy_refs"] = (ref("policy-b"), ref("policy-a"))
    one = create_dataset_manifest(**kwargs)
    kwargs["external_policy_refs"] = tuple(reversed(kwargs["external_policy_refs"]))
    two = create_dataset_manifest(**kwargs)
    assert one == two
    limited = resource_policy(MAX_POLICY_REFS=1)
    changed_request = request(resource=limited)
    _, limited_kwargs = manifest_fixture(resource=limited)
    limited_kwargs["materialization_request"] = changed_request
    limited_kwargs["materialization_result"] = materialize_pit_dataset(changed_request)
    limited_kwargs["external_policy_refs"] = (ref("policy-a"), ref("policy-b"))
    with pytest.raises(DatasetManifestError, match="MAX_POLICY_REFS_EXHAUSTED"):
        create_dataset_manifest(**limited_kwargs)
    assert first.resource_policy_id != limited.policy_id


def test_resource_policy_identity_must_match_c08_and_c09() -> None:
    _, kwargs = manifest_fixture()
    kwargs["resource_policy"] = resource_policy(MAX_POLICY_REFS=1)
    with pytest.raises(DatasetManifestError, match="RESOURCE_POLICY_IDENTITY_CONFLICT"):
        create_dataset_manifest(**kwargs)


def test_content_digest_and_manifest_identity_conflicts_reject() -> None:
    value, _ = manifest_fixture()
    with pytest.raises(DatasetManifestError, match="CONTENT_DIGEST_CONFLICT"):
        replace(value, content_digest=ref("wrong").content_digest)
    with pytest.raises(DatasetManifestError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(value, manifest_id=ManifestId("c09-dataset-manifest:forged"))


def test_self_reference_rejects() -> None:
    value, _ = manifest_fixture()
    self_node = ProvenanceNode.create("MANIFEST", value.manifest_id.value, (ref("self"),))
    with pytest.raises(DatasetManifestError):
        replace(value, provenance_nodes=value.provenance_nodes + (self_node,))


def test_conflicting_duplicate_evidence_identity_rejects() -> None:
    _, kwargs = manifest_fixture()
    original = kwargs["reference_identity_refs"][0]
    conflict = replace(original, content_digest=ref("different-content").content_digest)
    kwargs["reference_identity_refs"] = (original, conflict)
    with pytest.raises(DatasetManifestError):
        create_dataset_manifest(**kwargs)


def test_input_permutations_are_deterministic() -> None:
    _, kwargs = manifest_fixture()
    kwargs["reference_identity_refs"] = (ref("reference-b"), ref("reference-a"))
    kwargs["calendar_identity_refs"] = (ref("calendar-b"), ref("calendar-a"))
    kwargs["corporate_action_identity_refs"] = (ref("action-b"), ref("action-a"))
    first = create_dataset_manifest(**kwargs)
    for name in (
        "reference_identity_refs", "calendar_identity_refs", "corporate_action_identity_refs"
    ):
        kwargs[name] = tuple(reversed(kwargs[name]))
    assert create_dataset_manifest(**kwargs) == first


def test_authority_firewall_has_no_downstream_fields() -> None:
    names = {item.name.lower() for item in fields(DatasetManifest)}
    forbidden = {
        "quality", "eligibility", "currentness", "freshness", "legal_permission",
        "promotion", "persistence", "publication", "provider_authority",
        "storage_authority", "trading", "financial", "ai_trading_authority",
    }
    assert names.isdisjoint(forbidden)


def test_protected_predecessor_objects_are_not_mutated() -> None:
    _, kwargs = manifest_fixture()
    result_before = kwargs["materialization_result"]
    graph_before = kwargs["provenance_graph"]
    policy_before = kwargs["resource_policy"]
    create_dataset_manifest(**kwargs)
    assert kwargs["materialization_result"] == result_before
    assert kwargs["provenance_graph"] == graph_before
    assert kwargs["resource_policy"] == policy_before

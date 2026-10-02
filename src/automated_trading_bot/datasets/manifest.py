"""C09 positive dataset manifests over protected C08/C09 evidence.

Manifest existence records exact construction evidence only.  It creates no
quality, eligibility, currentness, legal, promotion, persistence, publication,
provider, storage, trading, financial, or AI authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
import unicodedata

from automated_trading_bot.datasets.materialization import (
    DatasetVersionId,
    LogicalContentId,
    MaterializationRequest,
    MaterializationRecordId,
    MaterializationResult,
    PitSufficiencyState,
    materialize_pit_dataset,
)
from automated_trading_bot.datasets.provenance import (
    CanonicalDatasetRepresentationId,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencyId,
    DependencyRef,
    DependencySetId,
    EntitlementProvenance,
    EntitlementState,
    ManifestId,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceNodeId,
    TransformationExecutionId,
    TransformationId,
    TransformationImplementationId,
    TransformationLineage,
    TransformationVersionId,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    canonicalize_evidence_refs,
)


MANIFEST_DOMAIN = "ATIS:C09:DATASET_MANIFEST:1"


class DatasetManifestError(ValueError):
    """A positive dataset manifest cannot be established safely."""


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise DatasetManifestError(f"{name.upper().replace(' ', '_')}_NOT_ESTABLISHED")
    if unicodedata.normalize("NFC", value) != value:
        raise DatasetManifestError(f"{name.upper().replace(' ', '_')}_NOT_NFC")
    return value


def _digest(body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        MANIFEST_DOMAIN.encode("ascii") + b"\0" + canonical_json(body)
    )


def _derived(digest: EvidenceContentDigest) -> ManifestId:
    return ManifestId(f"c09-dataset-manifest:{digest.value.removeprefix('sha256:')}")


def _ref(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise TypeError("expected EvidenceRef")
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _refs(
    values: tuple[EvidenceRef, ...], name: str, *, allow_empty: bool = False
) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple or any(type(item) is not EvidenceRef for item in values):
        raise TypeError(f"{name} must contain EvidenceRef values")
    if not values and not allow_empty:
        raise DatasetManifestError(f"{name.upper().replace(' ', '_')}_NOT_ESTABLISHED")
    try:
        return canonicalize_evidence_refs(values) if values else ()
    except (ValueError, EvidenceIdentityConflict) as error:
        raise DatasetManifestError(str(error)) from error


def _dependency_refs(values: tuple[DependencyRef, ...]) -> tuple[DependencyRef, ...]:
    if type(values) is not tuple or any(type(item) is not DependencyRef for item in values):
        raise TypeError("dependency_refs must contain DependencyRef values")
    if not values:
        raise DatasetManifestError("DEPENDENCY_REFS_NOT_ESTABLISHED")
    found: dict[str, DependencyRef] = {}
    bodies: dict[str, bytes] = {}
    for item in values:
        key = item.dependency_id.value
        body = canonical_json(item.body())
        if key in found and bodies[key] != body:
            raise DatasetManifestError("DEPENDENCY_IDENTITY_CONFLICT")
        found[key], bodies[key] = item, body
    return tuple(found[key] for key in sorted(found))


def _provenance_values(
    values: tuple[ProvenanceNode, ...] | tuple[ProvenanceEdge, ...],
    *,
    identity_name: str,
) -> tuple[ProvenanceNode, ...] | tuple[ProvenanceEdge, ...]:
    kind = ProvenanceNode if identity_name == "node_id" else ProvenanceEdge
    if type(values) is not tuple or any(type(item) is not kind for item in values):
        raise TypeError(f"provenance values must contain {kind.__name__} values")
    found: dict[str, ProvenanceNode | ProvenanceEdge] = {}
    bodies: dict[str, bytes] = {}
    for item in values:
        key = getattr(item, identity_name).value
        body = canonical_json(item.body())
        if key in found and bodies[key] != body:
            raise DatasetManifestError("PROVENANCE_IDENTITY_CONFLICT")
        found[key], bodies[key] = item, body
    return cast(
        "tuple[ProvenanceNode, ...] | tuple[ProvenanceEdge, ...]",
        tuple(found[key] for key in sorted(found)),
    )


def _entitlement_body(value: EntitlementProvenance) -> dict[str, object]:
    if type(value) is not EntitlementProvenance:
        raise TypeError("entitlement entries must be EntitlementProvenance values")
    return {
        "entitlement_id": value.entitlement_id.value,
        "evidence_ref": None if value.evidence_ref is None else _ref(value.evidence_ref),
        "independent_scope_ref": (
            None if value.independent_scope_ref is None else _ref(value.independent_scope_ref)
        ),
        "predecessor_ref": None if value.predecessor_ref is None else _ref(value.predecessor_ref),
        "provider_or_source_id": value.provider_or_source_id,
        "reasons": list(value.reasons),
        "state": value.state.value,
    }


def _entitlements(values: tuple[EntitlementProvenance, ...]) -> tuple[EntitlementProvenance, ...]:
    if type(values) is not tuple or any(type(item) is not EntitlementProvenance for item in values):
        raise TypeError("entitlement entries must contain EntitlementProvenance values")
    found: dict[str, EntitlementProvenance] = {}
    bodies: dict[str, bytes] = {}
    for item in values:
        key = item.entitlement_id.value
        body = canonical_json(_entitlement_body(item))
        if key in found and bodies[key] != body:
            raise DatasetManifestError("ENTITLEMENT_IDENTITY_CONTENT_CONFLICT")
        found[key], bodies[key] = item, body
    return tuple(found[key] for key in sorted(found))


def _limitations(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise TypeError("limitations must be a tuple")
    return tuple(sorted({_text(item, "limitation") for item in values}))


def _validate_lineage(
    *,
    lineage: TransformationLineage,
    materialization_request: MaterializationRequest,
    materialization_result: MaterializationResult,
    provenance_graph: ProvenanceGraph,
) -> None:
    if type(lineage) is not TransformationLineage:
        raise TypeError("transformation_lineage must be TransformationLineage")
    record = materialization_result.materialization_record
    expected_dependencies = tuple(
        sorted((item.dependency_id for item in record.dependency_refs), key=lambda item: item.value)
    )
    if lineage.transformation_id != record.transformation_id:
        raise DatasetManifestError("TRANSFORMATION_IDENTITY_CONFLICT")
    if lineage.version_id != record.transformation_version_id:
        raise DatasetManifestError("TRANSFORMATION_VERSION_IDENTITY_CONFLICT")
    if lineage.implementation_id != record.transformation_implementation_id:
        raise DatasetManifestError("TRANSFORMATION_IMPLEMENTATION_IDENTITY_CONFLICT")
    if lineage.execution_id != record.transformation_execution_id:
        raise DatasetManifestError("TRANSFORMATION_EXECUTION_IDENTITY_CONFLICT")
    if lineage.input_dependencies != expected_dependencies:
        raise DatasetManifestError("TRANSFORMATION_INPUT_DEPENDENCY_CONFLICT")
    if lineage.contract_ref != materialization_request.transformation.contract_ref:
        raise DatasetManifestError("TRANSFORMATION_CONTRACT_REFERENCE_CONFLICT")
    if (
        lineage.implementation_policy_ref
        != materialization_request.transformation.implementation_policy_ref
    ):
        raise DatasetManifestError("TRANSFORMATION_IMPLEMENTATION_POLICY_CONFLICT")
    if (
        materialization_request.transformation.representation_id
        != record.canonical_dataset_representation_id
        or materialization_request.representation.representation_id
        != record.canonical_dataset_representation_id
    ):
        raise DatasetManifestError("CANONICAL_REPRESENTATION_IDENTITY_CONFLICT")

    nodes_by_subject: dict[str, list[ProvenanceNode]] = {}
    for node in provenance_graph.nodes:
        nodes_by_subject.setdefault(node.subject_identity, []).append(node)

    dependency_nodes: dict[DependencyId, ProvenanceNode] = {}
    for dependency_id in expected_dependencies:
        matches = nodes_by_subject.get(dependency_id.value, [])
        if len(matches) != 1:
            raise DatasetManifestError("DEPENDENCY_PROVENANCE_NODE_NOT_EXACT")
        dependency_nodes[dependency_id] = matches[0]

    output_matches = nodes_by_subject.get(materialization_result.dataset_version_id.value, [])
    if len(output_matches) != 1:
        raise DatasetManifestError("DATASET_OUTPUT_PROVENANCE_NODE_NOT_EXACT")
    output_node = output_matches[0]

    derives: dict[ProvenanceNodeId, set[ProvenanceNodeId]] = {}
    for edge in provenance_graph.edges:
        if edge.relation == "DERIVES":
            derives.setdefault(edge.source, set()).add(edge.target)

    for dependency_node in dependency_nodes.values():
        pending = [dependency_node.node_id]
        visited: set[ProvenanceNodeId] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(derives.get(current, ()))
        if output_node.node_id not in visited:
            raise DatasetManifestError("DEPENDENCY_LINEAGE_NOT_ESTABLISHED")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    manifest_id: ManifestId
    contract_version: str
    dataset_version_id: DatasetVersionId
    dataset_schema_ref: EvidenceRef
    logical_content_id: LogicalContentId
    canonical_dataset_representation_id: CanonicalDatasetRepresentationId
    canonical_representation_contract_ref: EvidenceRef
    materialization_record_id: MaterializationRecordId
    source_evidence_refs: tuple[EvidenceRef, ...]
    reference_identity_refs: tuple[EvidenceRef, ...]
    calendar_identity_refs: tuple[EvidenceRef, ...]
    corporate_action_identity_refs: tuple[EvidenceRef, ...]
    transformation_id: TransformationId
    transformation_version_id: TransformationVersionId
    transformation_implementation_id: TransformationImplementationId
    transformation_execution_id: TransformationExecutionId
    dependency_set_id: DependencySetId
    dependency_refs: tuple[DependencyRef, ...]
    provenance_nodes: tuple[ProvenanceNode, ...]
    provenance_edges: tuple[ProvenanceEdge, ...]
    pit_cutoff: Timestamp
    limitations: tuple[str, ...]
    entitlement_provenance_entries: tuple[EntitlementProvenance, ...]
    entitlement_provenance_scope_ref: EvidenceRef
    entitlement_provenance_completeness_state: EntitlementState
    external_policy_refs: tuple[EvidenceRef, ...]
    resource_policy_id: DatasetLifecycleResourcePolicyId
    content_digest: EvidenceContentDigest

    def semantic_body(self) -> dict[str, object]:
        return _manifest_body(self)

    def __post_init__(self) -> None:
        _validate_manifest_types(self)
        canonical = _canonicalized_manifest_values(self)
        if canonical != _stored_manifest_values(self):
            raise DatasetManifestError("NONCANONICAL_DATASET_MANIFEST")
        digest = _digest(self.semantic_body())
        if self.content_digest != digest:
            raise DatasetManifestError("DATASET_MANIFEST_CONTENT_DIGEST_CONFLICT")
        if self.manifest_id != _derived(digest):
            raise DatasetManifestError("DATASET_MANIFEST_IDENTITY_CONTENT_CONFLICT")
        _reject_self_reference(self)


def _validate_manifest_types(value: DatasetManifest) -> None:
    expected = (
        (value.manifest_id, ManifestId),
        (value.dataset_version_id, DatasetVersionId),
        (value.dataset_schema_ref, EvidenceRef),
        (value.logical_content_id, LogicalContentId),
        (value.canonical_dataset_representation_id, CanonicalDatasetRepresentationId),
        (value.canonical_representation_contract_ref, EvidenceRef),
        (value.materialization_record_id, MaterializationRecordId),
        (value.transformation_id, TransformationId),
        (value.transformation_version_id, TransformationVersionId),
        (value.transformation_implementation_id, TransformationImplementationId),
        (value.transformation_execution_id, TransformationExecutionId),
        (value.dependency_set_id, DependencySetId),
        (value.pit_cutoff, Timestamp),
        (value.entitlement_provenance_scope_ref, EvidenceRef),
        (value.entitlement_provenance_completeness_state, EntitlementState),
        (value.resource_policy_id, DatasetLifecycleResourcePolicyId),
        (value.content_digest, EvidenceContentDigest),
    )
    for item, kind in expected:
        if type(item) is not kind:
            raise TypeError(f"DatasetManifest requires {kind.__name__}")
    _text(value.contract_version, "contract version")


def _stored_manifest_values(value: DatasetManifest) -> tuple[object, ...]:
    return (
        value.source_evidence_refs,
        value.reference_identity_refs,
        value.calendar_identity_refs,
        value.corporate_action_identity_refs,
        value.dependency_refs,
        value.provenance_nodes,
        value.provenance_edges,
        value.limitations,
        value.entitlement_provenance_entries,
        value.external_policy_refs,
    )


def _canonicalized_manifest_values(value: DatasetManifest) -> tuple[object, ...]:
    return (
        _refs(value.source_evidence_refs, "source evidence"),
        _refs(value.reference_identity_refs, "reference identities"),
        _refs(value.calendar_identity_refs, "calendar identities"),
        _refs(value.corporate_action_identity_refs, "corporate action identities"),
        _dependency_refs(value.dependency_refs),
        _provenance_values(value.provenance_nodes, identity_name="node_id"),
        _provenance_values(value.provenance_edges, identity_name="edge_id"),
        _limitations(value.limitations),
        _entitlements(value.entitlement_provenance_entries),
        _refs(value.external_policy_refs, "external policy refs", allow_empty=True),
    )


def _manifest_body(value: DatasetManifest) -> dict[str, object]:
    return {
        "calendar_identity_refs": [_ref(item) for item in value.calendar_identity_refs],
        "canonical_dataset_representation_id": value.canonical_dataset_representation_id.value,
        "canonical_representation_contract_ref": _ref(value.canonical_representation_contract_ref),
        "contract_version": value.contract_version,
        "corporate_action_identity_refs": [
            _ref(item) for item in value.corporate_action_identity_refs
        ],
        "dataset_schema_ref": _ref(value.dataset_schema_ref),
        "dataset_version_id": value.dataset_version_id.value,
        "dependency_refs": [item.body() for item in value.dependency_refs],
        "dependency_set_id": value.dependency_set_id.value,
        "entitlement_provenance_completeness_state": (
            value.entitlement_provenance_completeness_state.value
        ),
        "entitlement_provenance_entries": [
            _entitlement_body(item) for item in value.entitlement_provenance_entries
        ],
        "entitlement_provenance_scope_ref": _ref(value.entitlement_provenance_scope_ref),
        "external_policy_refs": [_ref(item) for item in value.external_policy_refs],
        "limitations": list(value.limitations),
        "logical_content_id": value.logical_content_id.value,
        "materialization_record_id": value.materialization_record_id.value,
        "pit_cutoff": value.pit_cutoff.value.isoformat(),
        "provenance_edges": [item.body() for item in value.provenance_edges],
        "provenance_nodes": [item.body() for item in value.provenance_nodes],
        "reference_identity_refs": [_ref(item) for item in value.reference_identity_refs],
        "resource_policy_id": value.resource_policy_id.value,
        "source_evidence_refs": [_ref(item) for item in value.source_evidence_refs],
        "transformation_execution_id": value.transformation_execution_id.value,
        "transformation_id": value.transformation_id.value,
        "transformation_implementation_id": value.transformation_implementation_id.value,
        "transformation_version_id": value.transformation_version_id.value,
    }


def _all_evidence_refs(value: DatasetManifest) -> tuple[EvidenceRef, ...]:
    entitlement_refs = tuple(
        ref
        for entry in value.entitlement_provenance_entries
        for ref in (entry.evidence_ref, entry.independent_scope_ref, entry.predecessor_ref)
        if ref is not None
    )
    dependency_refs = tuple(ref for dependency in value.dependency_refs for ref in dependency.evidence_refs)
    graph_refs = tuple(
        ref
        for item in value.provenance_nodes + value.provenance_edges
        for ref in item.evidence_refs
    )
    return (
        value.source_evidence_refs
        + value.reference_identity_refs
        + value.calendar_identity_refs
        + value.corporate_action_identity_refs
        + (value.dataset_schema_ref, value.canonical_representation_contract_ref)
        + dependency_refs
        + graph_refs
        + entitlement_refs
        + (value.entitlement_provenance_scope_ref,)
        + value.external_policy_refs
    )


def _reject_self_reference(value: DatasetManifest) -> None:
    identity = value.manifest_id.value
    if any(
        identity in (ref.source_id.value, ref.dataset_id.value, ref.evidence_id.value)
        for ref in _all_evidence_refs(value)
    ) or any(node.subject_identity == identity for node in value.provenance_nodes):
        raise DatasetManifestError("DATASET_MANIFEST_SELF_REFERENCE")


def create_dataset_manifest(
    *,
    materialization_request: MaterializationRequest,
    materialization_result: MaterializationResult,
    provenance_graph: ProvenanceGraph,
    transformation_lineage: TransformationLineage,
    reference_identity_refs: tuple[EvidenceRef, ...],
    calendar_identity_refs: tuple[EvidenceRef, ...],
    corporate_action_identity_refs: tuple[EvidenceRef, ...],
    entitlement_provenance_scope_ref: EvidenceRef,
    entitlement_provenance_completeness_state: EntitlementState,
    external_policy_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> DatasetManifest:
    """Create one exact positive manifest or fail before emitting a partial result."""
    if type(materialization_request) is not MaterializationRequest:
        raise TypeError("materialization_request must be MaterializationRequest")
    if type(materialization_result) is not MaterializationResult:
        raise TypeError("materialization_result must be MaterializationResult")
    if type(provenance_graph) is not ProvenanceGraph:
        raise TypeError("provenance_graph must be ProvenanceGraph")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    if materialize_pit_dataset(materialization_request) != materialization_result:
        raise DatasetManifestError("MATERIALIZATION_RESULT_SUBSTITUTION")
    record = materialization_result.materialization_record
    if record.pit_sufficiency_state is not PitSufficiencyState.ESTABLISHED:
        raise DatasetManifestError("PIT_SUFFICIENCY_NOT_ESTABLISHED")
    if materialization_result.logical_content_id != record.resulting_logical_content_id:
        raise DatasetManifestError("LOGICAL_CONTENT_SUBSTITUTION")
    if materialization_result.semantic_dataset.logical_schema_identity == "" or not materialization_result.semantic_dataset.records:
        raise DatasetManifestError("SEMANTIC_DATASET_NOT_ESTABLISHED")
    if resource_policy.policy_id != record.resource_policy_id:
        raise DatasetManifestError("RESOURCE_POLICY_IDENTITY_CONFLICT")
    if provenance_graph.resource_policy_id != resource_policy.policy_id:
        raise DatasetManifestError("PROVENANCE_RESOURCE_POLICY_IDENTITY_CONFLICT")
    dependencies = _dependency_refs(record.dependency_refs)
    if provenance_graph.dependency_set_id != record.dependency_set_id:
        raise DatasetManifestError("DEPENDENCY_SET_IDENTITY_CONFLICT")
    if provenance_graph.dependencies != dependencies:
        raise DatasetManifestError("DEPENDENCY_BODY_SUBSTITUTION")
    _validate_lineage(
        lineage=transformation_lineage,
        materialization_request=materialization_request,
        materialization_result=materialization_result,
        provenance_graph=provenance_graph,
    )
    if len(dependencies) > resource_policy.value("MAX_DEPENDENCIES_PER_DATASET_VERSION"):
        raise DatasetManifestError("MAX_DEPENDENCIES_PER_DATASET_VERSION_EXHAUSTED")
    if len(provenance_graph.nodes) > resource_policy.value("MAX_PROVENANCE_NODES"):
        raise DatasetManifestError("MAX_PROVENANCE_NODES_EXHAUSTED")
    if len(provenance_graph.edges) > resource_policy.value("MAX_PROVENANCE_EDGES"):
        raise DatasetManifestError("MAX_PROVENANCE_EDGES_EXHAUSTED")
    source_refs = _refs(record.selected_evidence_refs, "source evidence")
    if len(source_refs) > resource_policy.value("MAX_MATERIALIZATION_EVIDENCE_REFS"):
        raise DatasetManifestError("MAX_MATERIALIZATION_EVIDENCE_REFS_EXHAUSTED")
    reference_refs = _refs(reference_identity_refs, "reference identities")
    calendar_refs = _refs(calendar_identity_refs, "calendar identities")
    action_refs = _refs(corporate_action_identity_refs, "corporate action identities")
    policy_refs = _refs(external_policy_refs, "external policy refs", allow_empty=True)
    if len(policy_refs) > resource_policy.value("MAX_POLICY_REFS"):
        raise DatasetManifestError("MAX_POLICY_REFS_EXHAUSTED")
    if type(entitlement_provenance_scope_ref) is not EvidenceRef:
        raise TypeError("entitlement_provenance_scope_ref must be EvidenceRef")
    if entitlement_provenance_scope_ref not in record.evidence_refs:
        raise DatasetManifestError("ENTITLEMENT_SCOPE_NOT_BOUND_BY_MATERIALIZATION")
    if type(entitlement_provenance_completeness_state) is not EntitlementState:
        raise TypeError("entitlement_provenance_completeness_state must be EntitlementState")
    entitlements = _entitlements(record.entitlement_provenance_inputs_where_material)
    if entitlements:
        if entitlement_provenance_completeness_state is not EntitlementState.ESTABLISHED:
            raise DatasetManifestError("ENTITLEMENT_COMPLETENESS_NOT_ESTABLISHED")
        if any(item.restrictive for item in entitlements):
            raise DatasetManifestError("ENTITLEMENT_PROVENANCE_RESTRICTIVE")
    elif entitlement_provenance_completeness_state is not EntitlementState.NOT_APPLICABLE:
        raise DatasetManifestError("ENTITLEMENT_COMPLETENESS_NOT_ESTABLISHED")
    nodes = _provenance_values(provenance_graph.nodes, identity_name="node_id")
    edges = _provenance_values(provenance_graph.edges, identity_name="edge_id")
    values: dict[str, object] = {
        "contract_version": record.contract_version,
        "dataset_version_id": materialization_result.dataset_version_id,
        "dataset_schema_ref": record.dataset_schema_ref,
        "logical_content_id": materialization_result.logical_content_id,
        "canonical_dataset_representation_id": record.canonical_dataset_representation_id,
        "canonical_representation_contract_ref": record.canonical_representation_contract_ref,
        "materialization_record_id": record.record_id,
        "source_evidence_refs": source_refs,
        "reference_identity_refs": reference_refs,
        "calendar_identity_refs": calendar_refs,
        "corporate_action_identity_refs": action_refs,
        "transformation_id": record.transformation_id,
        "transformation_version_id": record.transformation_version_id,
        "transformation_implementation_id": record.transformation_implementation_id,
        "transformation_execution_id": record.transformation_execution_id,
        "dependency_set_id": record.dependency_set_id,
        "dependency_refs": dependencies,
        "provenance_nodes": nodes,
        "provenance_edges": edges,
        "pit_cutoff": record.pit_cutoff,
        "limitations": _limitations(record.limitations),
        "entitlement_provenance_entries": entitlements,
        "entitlement_provenance_scope_ref": entitlement_provenance_scope_ref,
        "entitlement_provenance_completeness_state": entitlement_provenance_completeness_state,
        "external_policy_refs": policy_refs,
        "resource_policy_id": resource_policy.policy_id,
    }
    provisional = DatasetManifest.__new__(DatasetManifest)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    digest = _digest(_manifest_body(provisional))
    values["content_digest"] = digest
    values["manifest_id"] = _derived(digest)
    return DatasetManifest(**values)  # type: ignore[arg-type]

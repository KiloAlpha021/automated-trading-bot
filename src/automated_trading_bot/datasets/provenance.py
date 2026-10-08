"""C09-S1 immutable provenance and dependency-graph foundations.

This module records attributable structure.  It cannot create materialization,
quality, eligibility, currentness, persistence, publication, legal permission,
or promotion authority.  Positive graph construction requires an externally
supplied resource policy; this module deliberately defines no numeric defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import math
import re
from typing import TypeVar
import unicodedata

from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    canonicalize_evidence_refs,
)


REQUIRED_RESOURCE_LIMITS = frozenset(
    {
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
    }
)

REQUIRED_PHYSICAL_RESOURCE_LIMITS = frozenset(
    {
        "MAX_PERSISTED_OBJECT_BYTES",
        "MAX_BUNDLE_BYTES",
        "MAX_OBJECTS_PER_VERSION",
        "MAX_METADATA_BYTES",
        "MAX_RECOVERY_STAGING_ENTRIES",
    }
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)


class FoundationError(ValueError):
    """C09-S1 could not establish a result safely."""


class ResourcePolicyError(FoundationError):
    """An independently authorized finite resource policy is unavailable."""


class GraphValidationError(FoundationError):
    """A dependency/provenance graph is malformed or ambiguous."""


class ManifestFoundationState(StrEnum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    INCOMPATIBLE = "INCOMPATIBLE"


class EntitlementState(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    UNKNOWN = "UNKNOWN"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        _text(self.value, "identity")
        if _ID.fullmatch(self.value) is None:
            raise ValueError("identity must be a canonical attributable string")


@dataclass(frozen=True, slots=True)
class ProvenanceNodeId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ProvenanceEdgeId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ProvenanceGraphId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class DependencyId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class DependencySetId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ManifestId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class TransformationId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class TransformationVersionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class TransformationImplementationId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class TransformationExecutionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalDatasetRepresentationId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class EntitlementEvidenceId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class DatasetLifecycleResourcePolicyId(_OpaqueId):
    pass


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _canonical_value(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        if type(value) is str:
            _text(value, "canonical string")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are prohibited")
        return value
    if type(value) is list:
        return [_canonical_value(item) for item in value]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            checked = _text(key, "object key")
            result[checked] = _canonical_value(item)
        return result
    raise TypeError("canonical JSON contains an unsupported value")


def canonical_json(value: object) -> bytes:
    """Return protected compact canonical JSON without repairing input."""
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        domain.encode("ascii") + b"\0" + canonical_json(body)
    )


def _derived(prefix: str, digest: EvidenceContentDigest) -> str:
    return f"{prefix}:{digest.value.removeprefix('sha256:')}"


def _ref(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise TypeError("expected EvidenceRef")
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _refs(values: tuple[EvidenceRef, ...], name: str) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple or not values:
        raise GraphValidationError(f"{name.upper()}_NOT_ESTABLISHED")
    try:
        return canonicalize_evidence_refs(values)
    except (ValueError, EvidenceIdentityConflict) as error:
        raise GraphValidationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class DatasetLifecycleResourcePolicy:
    """Externally supplied values; no values are selected by C09-S1."""

    policy_id: DatasetLifecycleResourcePolicyId
    policy_ref: EvidenceRef
    limits: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.policy_id) is not DatasetLifecycleResourcePolicyId:
            raise TypeError("policy_id must be DatasetLifecycleResourcePolicyId")
        if type(self.policy_ref) is not EvidenceRef:
            raise TypeError("policy_ref must be EvidenceRef")
        if type(self.limits) is not tuple:
            raise TypeError("limits must be a tuple")
        mapping: dict[str, int] = {}
        for entry in self.limits:
            if type(entry) is not tuple or len(entry) != 2:
                raise TypeError("resource limits must be name/value tuples")
            name, value = entry
            _text(name, "resource limit name")
            if name in mapping:
                raise ResourcePolicyError("DUPLICATE_RESOURCE_LIMIT")
            if type(value) is not int or value < 1:
                raise ResourcePolicyError("RESOURCE_LIMIT_MUST_BE_POSITIVE")
            mapping[name] = value
        supplied = set(mapping)
        permitted = REQUIRED_RESOURCE_LIMITS | REQUIRED_PHYSICAL_RESOURCE_LIMITS
        if supplied not in (REQUIRED_RESOURCE_LIMITS, permitted):
            raise ResourcePolicyError("RESOURCE_POLICY_NOT_ESTABLISHED")
        object.__setattr__(self, "limits", tuple(sorted(mapping.items())))

    def value(self, name: str) -> int:
        return dict(self.limits)[name]


@dataclass(frozen=True, slots=True)
class DependencyRef:
    dependency_id: DependencyId
    content_digest: EvidenceContentDigest
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.dependency_id) is not DependencyId:
            raise TypeError("dependency_id must be DependencyId")
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "dependency evidence"))

    def body(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest.value,
            "dependency_id": self.dependency_id.value,
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
        }


@dataclass(frozen=True, slots=True)
class ProvenanceNode:
    node_id: ProvenanceNodeId
    kind: str
    subject_identity: str
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.node_id) is not ProvenanceNodeId:
            raise TypeError("node_id must be ProvenanceNodeId")
        _text(self.kind, "node kind")
        _text(self.subject_identity, "subject identity")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "node evidence"))
        expected = self.derived_id(self.kind, self.subject_identity, self.evidence_refs)
        if self.node_id != expected:
            raise GraphValidationError("PROVENANCE_NODE_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls, kind: str, subject_identity: str, evidence_refs: tuple[EvidenceRef, ...]
    ) -> ProvenanceNode:
        canonical_refs = _refs(evidence_refs, "node evidence")
        return cls(cls.derived_id(kind, subject_identity, canonical_refs), kind, subject_identity, canonical_refs)

    @staticmethod
    def derived_id(
        kind: str, subject_identity: str, evidence_refs: tuple[EvidenceRef, ...]
    ) -> ProvenanceNodeId:
        _text(kind, "node kind")
        _text(subject_identity, "subject identity")
        digest = _digest(
            "ATIS:C09:PROVENANCE_NODE:1",
            {
                "evidence_refs": [_ref(item) for item in evidence_refs],
                "kind": kind,
                "subject_identity": subject_identity,
            },
        )
        return ProvenanceNodeId(_derived("c09-node", digest))

    def body(self) -> dict[str, object]:
        return {
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
            "kind": self.kind,
            "node_id": self.node_id.value,
            "subject_identity": self.subject_identity,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    edge_id: ProvenanceEdgeId
    source: ProvenanceNodeId
    target: ProvenanceNodeId
    relation: str
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.edge_id) is not ProvenanceEdgeId:
            raise TypeError("edge_id must be ProvenanceEdgeId")
        if type(self.source) is not ProvenanceNodeId or type(self.target) is not ProvenanceNodeId:
            raise TypeError("edge endpoints must be ProvenanceNodeId values")
        if self.source == self.target:
            raise GraphValidationError("PROVENANCE_SELF_EDGE")
        _text(self.relation, "edge relation")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "edge evidence"))
        expected = self.derived_id(self.source, self.target, self.relation, self.evidence_refs)
        if self.edge_id != expected:
            raise GraphValidationError("PROVENANCE_EDGE_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls,
        source: ProvenanceNodeId,
        target: ProvenanceNodeId,
        relation: str,
        evidence_refs: tuple[EvidenceRef, ...],
    ) -> ProvenanceEdge:
        canonical_refs = _refs(evidence_refs, "edge evidence")
        return cls(cls.derived_id(source, target, relation, canonical_refs), source, target, relation, canonical_refs)

    @staticmethod
    def derived_id(
        source: ProvenanceNodeId,
        target: ProvenanceNodeId,
        relation: str,
        evidence_refs: tuple[EvidenceRef, ...],
    ) -> ProvenanceEdgeId:
        digest = _digest(
            "ATIS:C09:PROVENANCE_EDGE:1",
            {
                "evidence_refs": [_ref(item) for item in evidence_refs],
                "relation": _text(relation, "edge relation"),
                "source": source.value,
                "target": target.value,
            },
        )
        return ProvenanceEdgeId(_derived("c09-edge", digest))

    def body(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id.value,
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
            "relation": self.relation,
            "source": self.source.value,
            "target": self.target.value,
        }


@dataclass(frozen=True, slots=True)
class TransformationLineage:
    transformation_id: TransformationId
    version_id: TransformationVersionId
    implementation_id: TransformationImplementationId
    execution_id: TransformationExecutionId
    contract_ref: EvidenceRef
    implementation_policy_ref: EvidenceRef
    input_dependencies: tuple[DependencyId, ...]

    def __post_init__(self) -> None:
        expected = (
            (self.transformation_id, TransformationId),
            (self.version_id, TransformationVersionId),
            (self.implementation_id, TransformationImplementationId),
            (self.execution_id, TransformationExecutionId),
            (self.contract_ref, EvidenceRef),
            (self.implementation_policy_ref, EvidenceRef),
        )
        for value, kind in expected:
            if type(value) is not kind:
                raise TypeError(f"transformation lineage requires {kind.__name__}")
        if type(self.input_dependencies) is not tuple or not self.input_dependencies:
            raise GraphValidationError("TRANSFORMATION_INPUTS_NOT_ESTABLISHED")
        if any(type(item) is not DependencyId for item in self.input_dependencies):
            raise TypeError("input_dependencies must contain DependencyId values")
        if len(set(self.input_dependencies)) != len(self.input_dependencies):
            raise GraphValidationError("DUPLICATE_TRANSFORMATION_INPUT")
        object.__setattr__(self, "input_dependencies", tuple(sorted(self.input_dependencies, key=lambda item: item.value)))


@dataclass(frozen=True, slots=True)
class EntitlementProvenance:
    entitlement_id: EntitlementEvidenceId
    state: EntitlementState
    provider_or_source_id: str | None
    evidence_ref: EvidenceRef | None
    independent_scope_ref: EvidenceRef | None
    predecessor_ref: EvidenceRef | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.entitlement_id) is not EntitlementEvidenceId:
            raise TypeError("entitlement_id must be EntitlementEvidenceId")
        if type(self.state) is not EntitlementState:
            raise TypeError("state must be EntitlementState")
        if self.provider_or_source_id is not None:
            _text(self.provider_or_source_id, "provider_or_source_id")
        for value in (self.evidence_ref, self.independent_scope_ref, self.predecessor_ref):
            if value is not None and type(value) is not EvidenceRef:
                raise TypeError("entitlement references must be EvidenceRef values")
        if type(self.reasons) is not tuple or not self.reasons:
            raise ValueError("entitlement reasons must be a nonempty tuple")
        checked = tuple(_text(reason, "entitlement reason") for reason in self.reasons)
        if len(set(checked)) != len(checked):
            raise ValueError("entitlement reasons must be distinct")
        object.__setattr__(self, "reasons", tuple(sorted(checked)))
        if self.state is EntitlementState.ESTABLISHED and (
            self.provider_or_source_id is None or self.evidence_ref is None
        ):
            raise FoundationError("ENTITLEMENT_EVIDENCE_NOT_ESTABLISHED")
        if self.state is EntitlementState.NOT_APPLICABLE and self.independent_scope_ref is None:
            raise FoundationError("NOT_APPLICABLE_SCOPE_NOT_ESTABLISHED")

    @property
    def restrictive(self) -> bool:
        return self.state in (EntitlementState.UNKNOWN, EntitlementState.INCOMPATIBLE)


@dataclass(frozen=True, slots=True)
class ProvenanceGraph:
    graph_id: ProvenanceGraphId
    dependency_set_id: DependencySetId
    nodes: tuple[ProvenanceNode, ...]
    edges: tuple[ProvenanceEdge, ...]
    dependencies: tuple[DependencyRef, ...]
    resource_policy_id: DatasetLifecycleResourcePolicyId
    content_digest: EvidenceContentDigest


_T = TypeVar("_T")


def _unique_by_id(values: Sequence[_T], id_name: str, body_name: str) -> tuple[_T, ...]:
    found: dict[str, _T] = {}
    bodies: dict[str, bytes] = {}
    for value in values:
        identity = getattr(value, id_name).value
        body = canonical_json(getattr(value, body_name)())
        if identity in found and bodies[identity] != body:
            raise GraphValidationError("CONFLICTING_DUPLICATE_IDENTITY")
        found[identity] = value
        bodies[identity] = body
    return tuple(found[key] for key in sorted(found))


def _assert_acyclic(nodes: tuple[ProvenanceNode, ...], edges: tuple[ProvenanceEdge, ...]) -> None:
    node_ids = {node.node_id for node in nodes}
    outgoing: dict[ProvenanceNodeId, list[ProvenanceNodeId]] = {node_id: [] for node_id in node_ids}
    incoming: dict[ProvenanceNodeId, int] = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise GraphValidationError("PROVENANCE_EDGE_ENDPOINT_NOT_ESTABLISHED")
        outgoing[edge.source].append(edge.target)
        incoming[edge.target] += 1
    ready = sorted((node for node, count in incoming.items() if count == 0), key=lambda item: item.value)
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for target in sorted(outgoing[current], key=lambda item: item.value):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort(key=lambda item: item.value)
    if visited != len(nodes):
        raise GraphValidationError("PROVENANCE_GRAPH_CYCLE")


def build_dependency_graph(
    *,
    nodes: tuple[ProvenanceNode, ...],
    edges: tuple[ProvenanceEdge, ...],
    dependencies: tuple[DependencyRef, ...],
    required_dependencies: tuple[DependencyId, ...],
    resource_policy: DatasetLifecycleResourcePolicy | None,
) -> ProvenanceGraph:
    """Build a bounded graph or fail before emitting any partial result."""
    if resource_policy is None:
        raise ResourcePolicyError("RESOURCE_POLICY_NOT_ESTABLISHED")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    if type(nodes) is not tuple or type(edges) is not tuple or type(dependencies) is not tuple:
        raise TypeError("graph collections must be tuples")
    if type(required_dependencies) is not tuple:
        raise TypeError("required_dependencies must be a tuple")
    if len(nodes) > resource_policy.value("MAX_PROVENANCE_NODES"):
        raise ResourcePolicyError("MAX_PROVENANCE_NODES_EXHAUSTED")
    if len(edges) > resource_policy.value("MAX_PROVENANCE_EDGES"):
        raise ResourcePolicyError("MAX_PROVENANCE_EDGES_EXHAUSTED")
    if len(dependencies) > resource_policy.value("MAX_DEPENDENCIES_PER_DATASET_VERSION"):
        raise ResourcePolicyError("MAX_DEPENDENCIES_PER_DATASET_VERSION_EXHAUSTED")
    canonical_nodes = tuple(_unique_by_id(nodes, "node_id", "body"))
    canonical_edges = tuple(_unique_by_id(edges, "edge_id", "body"))
    dependency_map: dict[DependencyId, DependencyRef] = {}
    for dependency in dependencies:
        if type(dependency) is not DependencyRef:
            raise TypeError("dependencies must contain DependencyRef values")
        previous = dependency_map.get(dependency.dependency_id)
        if previous is not None:
            if canonical_json(previous.body()) != canonical_json(dependency.body()):
                raise GraphValidationError("DEPENDENCY_IDENTITY_CONFLICT")
        dependency_map[dependency.dependency_id] = dependency
    if any(type(item) is not DependencyId for item in required_dependencies):
        raise TypeError("required_dependencies must contain DependencyId values")
    if set(required_dependencies) - set(dependency_map):
        raise GraphValidationError("REQUIRED_DEPENDENCY_NOT_ESTABLISHED")
    canonical_dependencies = tuple(sorted(dependency_map.values(), key=lambda item: item.dependency_id.value))
    _assert_acyclic(canonical_nodes, canonical_edges)
    body = {
        "dependencies": [item.body() for item in canonical_dependencies],
        "edges": [item.body() for item in canonical_edges],
        "nodes": [item.body() for item in canonical_nodes],
        "resource_policy_id": resource_policy.policy_id.value,
    }
    digest = _digest("ATIS:C09:PROVENANCE_GRAPH:1", body)
    dependency_digest = _digest(
        "ATIS:C09:DEPENDENCY_SET:1", [item.body() for item in canonical_dependencies]
    )
    return ProvenanceGraph(
        ProvenanceGraphId(_derived("c09-graph", digest)),
        DependencySetId(_derived("c09-dependencies", dependency_digest)),
        canonical_nodes,
        canonical_edges,
        canonical_dependencies,
        resource_policy.policy_id,
        digest,
    )


@dataclass(frozen=True, slots=True)
class DatasetManifestFoundation:
    manifest_id: ManifestId
    state: ManifestFoundationState
    graph_id: ProvenanceGraphId
    transformation_execution_id: TransformationExecutionId
    representation_id: CanonicalDatasetRepresentationId
    materialization_ref: EvidenceRef | None
    reasons: tuple[str, ...]
    creates_quality: bool = False
    creates_eligibility: bool = False
    creates_currentness: bool = False
    creates_legal_permission: bool = False
    creates_promotion: bool = False


def build_manifest_foundation(
    *,
    graph: ProvenanceGraph,
    lineage: TransformationLineage,
    representation_id: CanonicalDatasetRepresentationId,
    entitlements: tuple[EntitlementProvenance, ...],
    materialization_ref: EvidenceRef | None,
) -> DatasetManifestFoundation:
    """Record a structural, always-restrictive C09-S1 manifest foundation."""
    if type(graph) is not ProvenanceGraph:
        raise TypeError("graph must be ProvenanceGraph")
    if type(lineage) is not TransformationLineage:
        raise TypeError("lineage must be TransformationLineage")
    if type(representation_id) is not CanonicalDatasetRepresentationId:
        raise TypeError("representation_id must be CanonicalDatasetRepresentationId")
    if type(entitlements) is not tuple or any(type(item) is not EntitlementProvenance for item in entitlements):
        raise TypeError("entitlements must contain EntitlementProvenance values")
    if materialization_ref is not None and type(materialization_ref) is not EvidenceRef:
        raise TypeError("materialization_ref must be EvidenceRef or None")
    reasons = ["C09_S1_POSITIVE_MANIFEST_NOT_AUTHORIZED"]
    if materialization_ref is None:
        reasons.append("C08_MATERIALIZATION_NOT_ESTABLISHED")
    if any(item.restrictive for item in entitlements):
        reasons.append("ENTITLEMENT_PROVENANCE_RESTRICTIVE")
    body = {
        "entitlements": [
            {
                "entitlement_id": item.entitlement_id.value,
                "state": item.state.value,
            }
            for item in sorted(entitlements, key=lambda item: item.entitlement_id.value)
        ],
        "graph_id": graph.graph_id.value,
        "materialization_ref": None if materialization_ref is None else _ref(materialization_ref),
        "reasons": sorted(reasons),
        "representation_id": representation_id.value,
        "transformation_execution_id": lineage.execution_id.value,
    }
    digest = _digest("ATIS:C09:MANIFEST_FOUNDATION:1", body)
    return DatasetManifestFoundation(
        ManifestId(_derived("c09-manifest-foundation", digest)),
        ManifestFoundationState.NOT_ESTABLISHED,
        graph.graph_id,
        lineage.execution_id,
        representation_id,
        materialization_ref,
        tuple(sorted(reasons)),
    )

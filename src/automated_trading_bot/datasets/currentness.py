"""C10-S1 deterministic currentness and dependency invalidation.

The module consumes protected C09 evidence and resource-policy structures. It
selects no freshness horizon or resource value and grants no downstream authority.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
import re
import unicodedata

from automated_trading_bot.datasets.provenance import (
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencyId,
    GraphValidationError,
    ProvenanceGraph,
    ProvenanceGraphId,
    ProvenanceNodeId,
    build_dependency_graph,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    canonicalize_evidence_refs,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)


class CurrentnessError(ValueError):
    """C10-S1 input is malformed or internally contradictory."""


class FreshnessPolicyError(CurrentnessError):
    """An attributable freshness policy is malformed."""


class InvalidationError(CurrentnessError):
    """Invalidation input is malformed or contradictory."""


class CurrentnessState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class PropagationState(StrEnum):
    COMPLETE = "COMPLETE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        _text(self.value, "identity")
        if _ID.fullmatch(self.value) is None:
            raise ValueError("identity must be a canonical attributable string")


@dataclass(frozen=True, slots=True)
class FreshnessPolicyId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class CurrentnessAssessmentId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class InvalidationTriggerId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class InvalidationAssessmentId(_OpaqueId):
    pass


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _ref(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise TypeError("expected EvidenceRef")
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _timestamp(value: Timestamp) -> str:
    if type(value) is not Timestamp:
        raise TypeError("expected Timestamp")
    return value.value.isoformat()


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        domain.encode("ascii") + b"\0" + canonical_json(body)
    )


def _derived(prefix: str, digest: EvidenceContentDigest) -> str:
    return f"{prefix}:{digest.value.removeprefix('sha256:')}"


def _duration_microseconds(value: timedelta) -> int:
    if type(value) is not timedelta:
        raise TypeError("horizon must be a timedelta")
    if value <= timedelta(0):
        raise FreshnessPolicyError("FRESHNESS_HORIZON_MUST_BE_POSITIVE")
    return ((value.days * 86_400) + value.seconds) * 1_000_000 + value.microseconds


@dataclass(frozen=True, slots=True)
class FreshnessHorizon:
    """An externally selected, attributable horizon for one exact subject."""

    subject_identity: str
    horizon: timedelta
    evidence_ref: EvidenceRef

    def __post_init__(self) -> None:
        _text(self.subject_identity, "subject identity")
        _duration_microseconds(self.horizon)
        if type(self.evidence_ref) is not EvidenceRef:
            raise TypeError("evidence_ref must be EvidenceRef")

    def body(self) -> dict[str, object]:
        return {
            "evidence_ref": _ref(self.evidence_ref),
            "horizon_microseconds": _duration_microseconds(self.horizon),
            "subject_identity": self.subject_identity,
        }


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Immutable attributable policy; C10-S1 supplies no default horizons."""

    policy_id: FreshnessPolicyId
    policy_ref: EvidenceRef
    horizons: tuple[FreshnessHorizon, ...]

    def __post_init__(self) -> None:
        if type(self.policy_id) is not FreshnessPolicyId:
            raise TypeError("policy_id must be FreshnessPolicyId")
        if type(self.policy_ref) is not EvidenceRef:
            raise TypeError("policy_ref must be EvidenceRef")
        if type(self.horizons) is not tuple:
            raise TypeError("horizons must be a tuple")
        by_subject: dict[str, FreshnessHorizon] = {}
        bodies: dict[str, bytes] = {}
        for horizon in self.horizons:
            if type(horizon) is not FreshnessHorizon:
                raise TypeError("horizons must contain FreshnessHorizon values")
            body = canonical_json(horizon.body())
            previous = bodies.get(horizon.subject_identity)
            if previous is not None and previous != body:
                raise FreshnessPolicyError("CONFLICTING_FRESHNESS_HORIZON")
            bodies[horizon.subject_identity] = body
            by_subject[horizon.subject_identity] = horizon
        canonical = tuple(by_subject[key] for key in sorted(by_subject))
        object.__setattr__(self, "horizons", canonical)
        if self.policy_id != self.derived_id(self.policy_ref, canonical):
            raise FreshnessPolicyError("FRESHNESS_POLICY_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls, policy_ref: EvidenceRef, horizons: tuple[FreshnessHorizon, ...]
    ) -> FreshnessPolicy:
        if type(policy_ref) is not EvidenceRef:
            raise TypeError("policy_ref must be EvidenceRef")
        if type(horizons) is not tuple:
            raise TypeError("horizons must be a tuple")
        by_subject: dict[str, FreshnessHorizon] = {}
        bodies: dict[str, bytes] = {}
        for horizon in horizons:
            if type(horizon) is not FreshnessHorizon:
                raise TypeError("horizons must contain FreshnessHorizon values")
            body = canonical_json(horizon.body())
            previous = bodies.get(horizon.subject_identity)
            if previous is not None and previous != body:
                raise FreshnessPolicyError("CONFLICTING_FRESHNESS_HORIZON")
            bodies[horizon.subject_identity] = body
            by_subject[horizon.subject_identity] = horizon
        canonical = tuple(by_subject[key] for key in sorted(by_subject))
        return cls(cls.derived_id(policy_ref, canonical), policy_ref, canonical)

    @staticmethod
    def derived_id(
        policy_ref: EvidenceRef, horizons: tuple[FreshnessHorizon, ...]
    ) -> FreshnessPolicyId:
        digest = _digest(
            "ATIS:C10:FRESHNESS_POLICY:1",
            {"horizons": [item.body() for item in horizons], "policy_ref": _ref(policy_ref)},
        )
        return FreshnessPolicyId(_derived("c10-freshness-policy", digest))

    def horizon_for(self, subject_identity: str) -> FreshnessHorizon | None:
        _text(subject_identity, "subject identity")
        return next((item for item in self.horizons if item.subject_identity == subject_identity), None)


@dataclass(frozen=True, slots=True)
class CurrentnessAssessment:
    assessment_id: CurrentnessAssessmentId
    content_digest: EvidenceContentDigest
    subject_identity: str
    evidence_ref: EvidenceRef
    evidence_time: Timestamp | None
    policy_id: FreshnessPolicyId
    policy_ref: EvidenceRef
    evaluation_time: Timestamp
    boundary: Timestamp | None
    state: CurrentnessState
    reasons: tuple[str, ...]


def assess_currentness(
    *, subject_identity: str, evidence_ref: EvidenceRef,
    evidence_time: Timestamp | None, policy: FreshnessPolicy,
    evaluation_time: Timestamp,
) -> CurrentnessAssessment:
    """Assess one subject at an explicit time; equality with boundary is STALE."""
    _text(subject_identity, "subject identity")
    if type(evidence_ref) is not EvidenceRef:
        raise TypeError("evidence_ref must be EvidenceRef")
    if evidence_time is not None and type(evidence_time) is not Timestamp:
        raise TypeError("evidence_time must be Timestamp or None")
    if type(policy) is not FreshnessPolicy:
        raise TypeError("policy must be FreshnessPolicy")
    if type(evaluation_time) is not Timestamp:
        raise TypeError("evaluation_time must be Timestamp")
    applicable = policy.horizon_for(subject_identity)
    boundary: Timestamp | None = None
    if applicable is None:
        state = CurrentnessState.UNKNOWN
        reasons = ("APPLICABLE_FRESHNESS_HORIZON_NOT_ESTABLISHED",)
    elif evidence_time is None:
        state = CurrentnessState.UNKNOWN
        reasons = ("CURRENTNESS_EVIDENCE_UNAVAILABLE",)
    else:
        try:
            boundary = Timestamp(evidence_time.value + applicable.horizon)
        except OverflowError as error:
            raise FreshnessPolicyError("FRESHNESS_BOUNDARY_OUT_OF_RANGE") from error
        if evaluation_time.value < boundary.value:
            state = CurrentnessState.CURRENT
            reasons = ("EVALUATION_BEFORE_FRESHNESS_BOUNDARY",)
        else:
            state = CurrentnessState.STALE
            reasons = ("EVALUATION_AT_OR_AFTER_FRESHNESS_BOUNDARY",)
    assessment_body = {
        "boundary": None if boundary is None else _timestamp(boundary),
        "evaluation_time": _timestamp(evaluation_time),
        "evidence_ref": _ref(evidence_ref),
        "evidence_time": None if evidence_time is None else _timestamp(evidence_time),
        "policy_id": policy.policy_id.value,
        "policy_ref": _ref(policy.policy_ref),
        "reasons": list(reasons), "state": state.value,
        "subject_identity": subject_identity,
    }
    digest = _digest("ATIS:C10:CURRENTNESS_ASSESSMENT:1", assessment_body)
    return CurrentnessAssessment(
        CurrentnessAssessmentId(_derived("c10-currentness", digest)), digest,
        subject_identity, evidence_ref, evidence_time, policy.policy_id,
        policy.policy_ref, evaluation_time, boundary, state, reasons,
    )


@dataclass(frozen=True, slots=True)
class InvalidationTrigger:
    trigger_id: InvalidationTriggerId
    changed_node_ids: tuple[ProvenanceNodeId, ...]
    changed_dependency_ids: tuple[DependencyId, ...]
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.trigger_id) is not InvalidationTriggerId:
            raise TypeError("trigger_id must be InvalidationTriggerId")
        if type(self.changed_node_ids) is not tuple or any(type(item) is not ProvenanceNodeId for item in self.changed_node_ids):
            raise TypeError("changed_node_ids must contain ProvenanceNodeId values")
        if type(self.changed_dependency_ids) is not tuple or any(type(item) is not DependencyId for item in self.changed_dependency_ids):
            raise TypeError("changed_dependency_ids must contain DependencyId values")
        if not self.changed_node_ids and not self.changed_dependency_ids:
            raise InvalidationError("INVALIDATION_TRIGGER_SCOPE_NOT_ESTABLISHED")
        if type(self.evidence_refs) is not tuple:
            raise TypeError("evidence_refs must be a tuple")
        try:
            refs = canonicalize_evidence_refs(self.evidence_refs)
        except (ValueError, EvidenceIdentityConflict) as error:
            raise InvalidationError(str(error)) from error
        nodes = tuple(sorted(set(self.changed_node_ids), key=lambda item: item.value))
        dependencies = tuple(sorted(set(self.changed_dependency_ids), key=lambda item: item.value))
        object.__setattr__(self, "changed_node_ids", nodes)
        object.__setattr__(self, "changed_dependency_ids", dependencies)
        object.__setattr__(self, "evidence_refs", refs)
        if self.trigger_id != self.derived_id(nodes, dependencies, refs):
            raise InvalidationError("INVALIDATION_TRIGGER_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls, *, changed_node_ids: tuple[ProvenanceNodeId, ...] = (),
        changed_dependency_ids: tuple[DependencyId, ...] = (),
        evidence_refs: tuple[EvidenceRef, ...],
    ) -> InvalidationTrigger:
        nodes = tuple(sorted(set(changed_node_ids), key=lambda item: item.value))
        dependencies = tuple(sorted(set(changed_dependency_ids), key=lambda item: item.value))
        refs = canonicalize_evidence_refs(evidence_refs)
        return cls(cls.derived_id(nodes, dependencies, refs), nodes, dependencies, refs)

    @staticmethod
    def derived_id(nodes: tuple[ProvenanceNodeId, ...], dependencies: tuple[DependencyId, ...], refs: tuple[EvidenceRef, ...]) -> InvalidationTriggerId:
        digest = _digest("ATIS:C10:INVALIDATION_TRIGGER:1", {
            "changed_dependency_ids": [item.value for item in dependencies],
            "changed_node_ids": [item.value for item in nodes],
            "evidence_refs": [_ref(item) for item in refs],
        })
        return InvalidationTriggerId(_derived("c10-invalidation-trigger", digest))

    def body(self) -> dict[str, object]:
        return {
            "changed_dependency_ids": [item.value for item in self.changed_dependency_ids],
            "changed_node_ids": [item.value for item in self.changed_node_ids],
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
            "trigger_id": self.trigger_id.value,
        }


@dataclass(frozen=True, slots=True)
class InvalidationAssessment:
    assessment_id: InvalidationAssessmentId
    content_digest: EvidenceContentDigest
    trigger_ids: tuple[InvalidationTriggerId, ...]
    graph_id: ProvenanceGraphId
    resource_policy_id: DatasetLifecycleResourcePolicyId
    affected_node_ids: tuple[ProvenanceNodeId, ...]
    propagation_state: PropagationState
    reasons: tuple[str, ...]

    @property
    def establishes_complete_scope(self) -> bool:
        return self.propagation_state is PropagationState.COMPLETE


def _validated_graph(graph: ProvenanceGraph, resource_policy: DatasetLifecycleResourcePolicy) -> ProvenanceGraph:
    if type(graph) is not ProvenanceGraph:
        raise TypeError("graph must be ProvenanceGraph")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    if graph.resource_policy_id != resource_policy.policy_id:
        raise InvalidationError("GRAPH_RESOURCE_POLICY_IDENTITY_CONFLICT")
    rebuilt = build_dependency_graph(
        nodes=graph.nodes, edges=graph.edges, dependencies=graph.dependencies,
        required_dependencies=tuple(item.dependency_id for item in graph.dependencies),
        resource_policy=resource_policy,
    )
    if rebuilt != graph:
        raise GraphValidationError("PROVENANCE_GRAPH_IDENTITY_CONTENT_CONFLICT")
    return rebuilt


def assess_invalidation(
    *, triggers: tuple[InvalidationTrigger, ...], graph: ProvenanceGraph,
    resource_policy: DatasetLifecycleResourcePolicy,
) -> InvalidationAssessment:
    """Traverse causal source-to-dependant edges without claiming partial completeness."""
    if type(triggers) is not tuple or not triggers:
        raise TypeError("triggers must be a nonempty tuple")
    checked_graph = _validated_graph(graph, resource_policy)
    trigger_map: dict[InvalidationTriggerId, InvalidationTrigger] = {}
    trigger_bodies: dict[InvalidationTriggerId, bytes] = {}
    for trigger in triggers:
        if type(trigger) is not InvalidationTrigger:
            raise TypeError("triggers must contain InvalidationTrigger values")
        body = canonical_json(trigger.body())
        previous = trigger_bodies.get(trigger.trigger_id)
        if previous is not None and previous != body:
            raise InvalidationError("INVALIDATION_TRIGGER_IDENTITY_CONFLICT")
        trigger_bodies[trigger.trigger_id] = body
        trigger_map[trigger.trigger_id] = trigger
    canonical_triggers = tuple(trigger_map[key] for key in sorted(trigger_map, key=lambda item: item.value))
    node_ids = {node.node_id for node in checked_graph.nodes}
    dependency_nodes = {
        dependency.dependency_id: tuple(sorted(
            (node.node_id for node in checked_graph.nodes if node.subject_identity == dependency.dependency_id.value),
            key=lambda item: item.value,
        )) for dependency in checked_graph.dependencies
    }
    roots: set[ProvenanceNodeId] = set()
    unresolved = False
    for trigger in canonical_triggers:
        for node_id in trigger.changed_node_ids:
            if node_id not in node_ids:
                unresolved = True
            else:
                roots.add(node_id)
        for dependency_id in trigger.changed_dependency_ids:
            mapped = dependency_nodes.get(dependency_id)
            if not mapped:
                unresolved = True
            else:
                roots.update(mapped)
    outgoing: dict[ProvenanceNodeId, list[tuple[ProvenanceNodeId, str]]] = {
        node_id: [] for node_id in node_ids
    }
    for edge in checked_graph.edges:
        outgoing[edge.source].append((edge.target, edge.relation))
    for values in outgoing.values():
        values.sort(key=lambda item: (item[0].value, item[1]))
    affected: set[ProvenanceNodeId] = set()
    visited = set(roots)
    queue: deque[tuple[ProvenanceNodeId, int]] = deque((node_id, 0) for node_id in sorted(roots, key=lambda item: item.value))
    max_members = resource_policy.value("MAX_AFFECTED_SET_MEMBERS")
    max_depth = resource_policy.value("MAX_INVALIDATION_TRAVERSAL_DEPTH")
    max_events = resource_policy.value("MAX_PROPAGATION_EVENTS_PER_RUN")
    events = 0
    exhausted_reason: str | None = None
    while queue and exhausted_reason is None:
        current, depth = queue.popleft()
        targets = outgoing[current]
        if targets and depth >= max_depth:
            exhausted_reason = "MAX_INVALIDATION_TRAVERSAL_DEPTH_EXHAUSTED"
            break
        for target, relation in targets:
            events += 1
            if events > max_events:
                exhausted_reason = "MAX_PROPAGATION_EVENTS_PER_RUN_EXHAUSTED"
                break
            if relation != "DERIVES":
                exhausted_reason = "INVALIDATION_RELATION_SEMANTICS_NOT_ESTABLISHED"
                break
            if target in visited:
                continue
            if len(affected) >= max_members:
                exhausted_reason = "MAX_AFFECTED_SET_MEMBERS_EXHAUSTED"
                break
            visited.add(target)
            affected.add(target)
            queue.append((target, depth + 1))
    if unresolved:
        state, reasons = PropagationState.UNKNOWN, ("INVALIDATION_TRIGGER_SCOPE_UNRESOLVED",)
    elif exhausted_reason is not None:
        state, reasons = PropagationState.UNKNOWN, (exhausted_reason,)
    else:
        state, reasons = PropagationState.COMPLETE, ("AFFECTED_SET_COMPLETE",)
    canonical_affected = tuple(sorted(affected, key=lambda item: item.value))
    assessment_body = {
        "affected_node_ids": [item.value for item in canonical_affected],
        "graph_id": checked_graph.graph_id.value,
        "propagation_state": state.value, "reasons": list(reasons),
        "resource_policy_id": resource_policy.policy_id.value,
        "trigger_ids": [item.trigger_id.value for item in canonical_triggers],
    }
    digest = _digest("ATIS:C10:INVALIDATION_ASSESSMENT:1", assessment_body)
    return InvalidationAssessment(
        InvalidationAssessmentId(_derived("c10-invalidation", digest)), digest,
        tuple(item.trigger_id for item in canonical_triggers), checked_graph.graph_id,
        resource_policy.policy_id, canonical_affected, state, reasons,
    )

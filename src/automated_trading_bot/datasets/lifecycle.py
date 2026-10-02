"""C10 dataset-bound currentness and interruption-safe invalidation records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import unicodedata

from automated_trading_bot.datasets.currentness import (
    CurrentnessAssessment,
    CurrentnessState,
    FreshnessPolicyId,
    InvalidationAssessment,
    InvalidationTriggerId,
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
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    canonicalize_evidence_refs,
)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)


class LifecycleError(ValueError):
    """A C10 lifecycle record is malformed or contradictory."""


class PropagationError(LifecycleError):
    """Propagation state cannot be established safely."""


class PropagationRecordState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        _text(self.value, "identity")
        if _ID.fullmatch(self.value) is None:
            raise ValueError("identity must be a canonical attributable string")


@dataclass(frozen=True, slots=True)
class DatasetCurrentnessAssessmentId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class DatasetVersionMappingId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class InvalidationEventId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class AffectedSetId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class PropagationRunId(_OpaqueId):
    pass


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(domain.encode("ascii") + b"\0" + canonical_json(body))


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


def _refs(values: tuple[EvidenceRef, ...], *, allow_empty: bool) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple:
        raise TypeError("evidence_refs must be a tuple")
    if not values:
        if allow_empty:
            return ()
        raise LifecycleError("EVIDENCE_NOT_ESTABLISHED")
    try:
        return canonicalize_evidence_refs(values)
    except (ValueError, EvidenceIdentityConflict) as error:
        raise LifecycleError(str(error)) from error


def _timestamp(value: Timestamp) -> str:
    if type(value) is not Timestamp:
        raise TypeError("expected Timestamp")
    return value.value.isoformat()


def _reasons(
    values: tuple[str, ...], policy: DatasetLifecycleResourcePolicy
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise TypeError("reasons must be a tuple")
    checked = tuple(_text(item, "reason") for item in values)
    canonical = tuple(sorted(set(checked)))
    if len(canonical) > policy.value("MAX_REASONS_PER_RECORD"):
        raise LifecycleError("MAX_REASONS_PER_RECORD_EXHAUSTED")
    if any(len(item) > policy.value("MAX_REASON_LENGTH") for item in canonical):
        raise LifecycleError("MAX_REASON_LENGTH_EXHAUSTED")
    return canonical


def _versions(values: tuple[DatasetVersionId, ...], name: str) -> tuple[DatasetVersionId, ...]:
    if type(values) is not tuple or any(type(item) is not DatasetVersionId for item in values):
        raise TypeError(f"{name} must contain DatasetVersionId values")
    return tuple(sorted(set(values), key=lambda item: item.value))


def _set_digest(domain: str, values: tuple[DatasetVersionId, ...]) -> EvidenceContentDigest:
    return _digest(domain, [item.value for item in values])


@dataclass(frozen=True, slots=True)
class DatasetVersionMapping:
    mapping_id: DatasetVersionMappingId
    content_digest: EvidenceContentDigest
    contract_version: str
    provenance_node_id: ProvenanceNodeId
    dataset_version_id: DatasetVersionId
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        body = _dataset_version_mapping_body(self)
        digest = _digest("ATIS:C10:DATASET_VERSION_MAPPING:1", body)
        if self.content_digest != digest or self.mapping_id != DatasetVersionMappingId(
            _derived("c10-dataset-version-mapping", digest)
        ):
            raise LifecycleError("DATASET_VERSION_MAPPING_IDENTITY_CONTENT_CONFLICT")


def _dataset_version_mapping_body(value: DatasetVersionMapping) -> dict[str, object]:
    if type(value.provenance_node_id) is not ProvenanceNodeId:
        raise TypeError("provenance_node_id must be ProvenanceNodeId")
    if type(value.dataset_version_id) is not DatasetVersionId:
        raise TypeError("dataset_version_id must be DatasetVersionId")
    refs = _refs(value.evidence_refs, allow_empty=False)
    return {
        "contract_version": _text(value.contract_version, "contract_version"),
        "provenance_node_id": value.provenance_node_id.value,
        "dataset_version_id": value.dataset_version_id.value,
        "evidence_refs": [_ref(item) for item in refs],
    }


def create_dataset_version_mapping(
    *, contract_version: str, provenance_node_id: ProvenanceNodeId,
    dataset_version_id: DatasetVersionId, evidence_refs: tuple[EvidenceRef, ...],
) -> DatasetVersionMapping:
    refs = _refs(evidence_refs, allow_empty=False)
    provisional = object.__new__(DatasetVersionMapping)
    assignments = {
        "contract_version": _text(contract_version, "contract_version"),
        "provenance_node_id": provenance_node_id,
        "dataset_version_id": dataset_version_id,
        "evidence_refs": refs,
    }
    for name, value in assignments.items():
        object.__setattr__(provisional, name, value)
    body = _dataset_version_mapping_body(provisional)
    digest = _digest("ATIS:C10:DATASET_VERSION_MAPPING:1", body)
    object.__setattr__(provisional, "content_digest", digest)
    object.__setattr__(
        provisional,
        "mapping_id",
        DatasetVersionMappingId(_derived("c10-dataset-version-mapping", digest)),
    )
    provisional.__post_init__()
    return provisional


@dataclass(frozen=True, slots=True)
class AffectedSet:
    affected_set_id: AffectedSetId
    content_digest: EvidenceContentDigest
    contract_version: str
    trigger_ids: tuple[InvalidationTriggerId, ...]
    provenance_graph_id: ProvenanceGraphId
    affected_node_ids: tuple[ProvenanceNodeId, ...]
    dataset_version_ids: tuple[DatasetVersionId, ...]
    mapping_ids: tuple[DatasetVersionMappingId, ...]
    mapping_evidence_refs: tuple[EvidenceRef, ...]
    scope_evidence_refs: tuple[EvidenceRef, ...]
    resource_policy_id: DatasetLifecycleResourcePolicyId

    def __post_init__(self) -> None:
        body = _affected_set_body(self)
        digest = _digest("ATIS:C10:AFFECTED_SET:1", body)
        if self.content_digest != digest or self.affected_set_id != AffectedSetId(
            _derived("c10-affected-set", digest)
        ):
            raise LifecycleError("AFFECTED_SET_IDENTITY_CONTENT_CONFLICT")


def _affected_set_body(value: AffectedSet) -> dict[str, object]:
    return {
        "contract_version": _text(value.contract_version, "contract_version"),
        "trigger_ids": [item.value for item in value.trigger_ids],
        "provenance_graph_id": value.provenance_graph_id.value,
        "affected_node_ids": [item.value for item in value.affected_node_ids],
        "dataset_version_ids": [item.value for item in value.dataset_version_ids],
        "mapping_ids": [item.value for item in value.mapping_ids],
        "mapping_evidence_refs": [_ref(item) for item in value.mapping_evidence_refs],
        "scope_evidence_refs": [_ref(item) for item in value.scope_evidence_refs],
        "resource_policy_id": value.resource_policy_id.value,
    }


def create_affected_set(
    *, contract_version: str, assessment: InvalidationAssessment,
    graph_identity: ProvenanceGraphId,
    mappings: tuple[DatasetVersionMapping, ...],
    scope_evidence_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> AffectedSet:
    if type(assessment) is not InvalidationAssessment:
        raise TypeError("assessment must be InvalidationAssessment")
    if not assessment.establishes_complete_scope:
        raise LifecycleError("EXACT_AFFECTED_SET_NOT_ESTABLISHED")
    if type(graph_identity) is not ProvenanceGraphId or graph_identity != assessment.graph_id:
        raise LifecycleError("INVALIDATION_GRAPH_SUBSTITUTION")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    if resource_policy.policy_id != assessment.resource_policy_id:
        raise LifecycleError("AFFECTED_SET_RESOURCE_POLICY_IDENTITY_CONFLICT")
    if type(mappings) is not tuple or any(type(item) is not DatasetVersionMapping for item in mappings):
        raise TypeError("mappings must contain DatasetVersionMapping values")
    nodes = tuple(sorted(assessment.affected_node_ids, key=lambda item: item.value))
    if len(nodes) > resource_policy.value("MAX_AFFECTED_SET_MEMBERS"):
        raise LifecycleError("MAX_AFFECTED_SET_MEMBERS_EXHAUSTED")
    by_node: dict[ProvenanceNodeId, DatasetVersionMapping] = {}
    for mapping in mappings:
        existing = by_node.get(mapping.provenance_node_id)
        if existing is not None and existing != mapping:
            raise LifecycleError("CONFLICTING_DATASET_VERSION_MAPPING")
        by_node[mapping.provenance_node_id] = mapping
    expected_nodes = set(nodes)
    if set(by_node) - expected_nodes:
        raise LifecycleError("UNRELATED_DATASET_VERSION_MAPPING")
    if set(by_node) != expected_nodes:
        raise LifecycleError("MISSING_DATASET_VERSION_MAPPING")
    canonical_mappings = tuple(by_node[node] for node in nodes)
    versions = _versions(
        tuple(item.dataset_version_id for item in canonical_mappings),
        "affected dataset versions",
    )
    if len(versions) != len(nodes):
        raise LifecycleError("CONFLICTING_DATASET_VERSION_MAPPING")
    mapping_refs = _refs(
        tuple(ref for mapping in canonical_mappings for ref in mapping.evidence_refs),
        allow_empty=not canonical_mappings,
    )
    scope_refs = _refs(scope_evidence_refs, allow_empty=False)
    provisional = object.__new__(AffectedSet)
    assignments = {
        "contract_version": _text(contract_version, "contract_version"),
        "trigger_ids": tuple(sorted(assessment.trigger_ids, key=lambda item: item.value)),
        "provenance_graph_id": graph_identity,
        "affected_node_ids": nodes,
        "dataset_version_ids": versions,
        "mapping_ids": tuple(item.mapping_id for item in canonical_mappings),
        "mapping_evidence_refs": mapping_refs,
        "scope_evidence_refs": scope_refs,
        "resource_policy_id": resource_policy.policy_id,
    }
    for name, value in assignments.items():
        object.__setattr__(provisional, name, value)
    body = _affected_set_body(provisional)
    digest = _digest("ATIS:C10:AFFECTED_SET:1", body)
    object.__setattr__(provisional, "content_digest", digest)
    object.__setattr__(provisional, "affected_set_id", AffectedSetId(
        _derived("c10-affected-set", digest)
    ))
    provisional.__post_init__()
    return provisional


@dataclass(frozen=True, slots=True)
class DatasetCurrentnessAssessment:
    assessment_id: DatasetCurrentnessAssessmentId
    contract_version: str
    dataset_version_id: DatasetVersionId
    freshness_policy_id: FreshnessPolicyId
    freshness_policy_ref: EvidenceRef
    evaluated_at: Timestamp
    dependency_set_id: DependencySetId
    dependency_evidence_refs: tuple[EvidenceRef, ...]
    dependency_state_digest: EvidenceContentDigest
    state: CurrentnessState
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    resource_policy_id: DatasetLifecycleResourcePolicyId
    invalidation_event_id: InvalidationEventId | None
    affected_set_id: AffectedSetId | None
    propagation_run_id: PropagationRunId | None
    lifecycle_reasons: tuple[str, ...]
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        if type(self.assessment_id) is not DatasetCurrentnessAssessmentId:
            raise TypeError("assessment_id must be DatasetCurrentnessAssessmentId")
        expected = _dataset_currentness_body(self, include_identity=False)
        digest = _digest("ATIS:C10:DATASET_CURRENTNESS_ASSESSMENT:1", expected)
        if self.content_digest != digest or self.assessment_id != DatasetCurrentnessAssessmentId(
            _derived("c10-dataset-currentness", digest)
        ):
            raise LifecycleError("DATASET_CURRENTNESS_IDENTITY_CONTENT_CONFLICT")


def _dataset_currentness_body(
    value: DatasetCurrentnessAssessment, *, include_identity: bool
) -> dict[str, object]:
    body: dict[str, object] = {
        "contract_version": _text(value.contract_version, "contract_version"),
        "dataset_version_id": value.dataset_version_id.value,
        "freshness_policy_id": value.freshness_policy_id.value,
        "freshness_policy_ref": _ref(value.freshness_policy_ref),
        "evaluated_at": _timestamp(value.evaluated_at),
        "dependency_set_id": value.dependency_set_id.value,
        "dependency_evidence_refs": [_ref(item) for item in value.dependency_evidence_refs],
        "dependency_state_digest": value.dependency_state_digest.value,
        "state": value.state.value,
        "reasons": list(value.reasons),
        "evidence_refs": [_ref(item) for item in value.evidence_refs],
        "resource_policy_id": value.resource_policy_id.value,
        "invalidation_event_id": None if value.invalidation_event_id is None else value.invalidation_event_id.value,
        "affected_set_id": None if value.affected_set_id is None else value.affected_set_id.value,
        "propagation_run_id": None if value.propagation_run_id is None else value.propagation_run_id.value,
        "lifecycle_reasons": list(value.lifecycle_reasons),
    }
    if include_identity:
        body["assessment_id"] = value.assessment_id.value
        body["content_digest"] = value.content_digest.value
    return body


def bind_dataset_currentness(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    dependency_set_id: DependencySetId,
    dependency_evidence_refs: tuple[EvidenceRef, ...],
    dependency_state_digest: EvidenceContentDigest,
    assessment: CurrentnessAssessment,
    evidence_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
    invalidation_event: InvalidationEvent | None = None,
    propagation_record: PropagationRecord | None = None,
) -> DatasetCurrentnessAssessment:
    """Bind a protected C10-S1 result to exact dataset lifecycle identities."""
    if type(dataset_version_id) is not DatasetVersionId:
        raise TypeError("dataset_version_id must be DatasetVersionId")
    if type(dependency_set_id) is not DependencySetId:
        raise TypeError("dependency_set_id must be DependencySetId")
    if type(dependency_state_digest) is not EvidenceContentDigest:
        raise TypeError("dependency_state_digest must be EvidenceContentDigest")
    if type(assessment) is not CurrentnessAssessment:
        raise TypeError("assessment must be CurrentnessAssessment")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    dependency_refs = _refs(dependency_evidence_refs, allow_empty=False)
    refs = _refs(evidence_refs, allow_empty=False)
    state, lifecycle_reasons = _lifecycle_currentness_state(
        dataset_version_id=dataset_version_id,
        base_state=assessment.state,
        invalidation_event=invalidation_event,
        propagation_record=propagation_record,
    )
    if (
        propagation_record is not None
        and propagation_record.resource_policy_id != resource_policy.policy_id
    ):
        raise PropagationError("PROPAGATION_RESOURCE_POLICY_SUBSTITUTION")
    reasons = _reasons(assessment.reasons + lifecycle_reasons, resource_policy)
    provisional = object.__new__(DatasetCurrentnessAssessment)
    values = (
        ("contract_version", _text(contract_version, "contract_version")),
        ("dataset_version_id", dataset_version_id),
        ("freshness_policy_id", assessment.policy_id),
        ("freshness_policy_ref", assessment.policy_ref),
        ("evaluated_at", assessment.evaluation_time),
        ("dependency_set_id", dependency_set_id),
        ("dependency_evidence_refs", dependency_refs),
        ("dependency_state_digest", dependency_state_digest),
        ("state", state), ("reasons", reasons), ("evidence_refs", refs),
        ("resource_policy_id", resource_policy.policy_id),
        ("invalidation_event_id", None if invalidation_event is None else invalidation_event.event_id),
        ("affected_set_id", None if invalidation_event is None else invalidation_event.affected_set_id),
        ("propagation_run_id", None if propagation_record is None else propagation_record.propagation_run_id),
        ("lifecycle_reasons", lifecycle_reasons),
    )
    for name, value in values:
        object.__setattr__(provisional, name, value)
    body = _dataset_currentness_body(provisional, include_identity=False)
    digest = _digest("ATIS:C10:DATASET_CURRENTNESS_ASSESSMENT:1", body)
    object.__setattr__(provisional, "content_digest", digest)
    object.__setattr__(provisional, "assessment_id", DatasetCurrentnessAssessmentId(
        _derived("c10-dataset-currentness", digest)
    ))
    provisional.__post_init__()
    return provisional


@dataclass(frozen=True, slots=True)
class InvalidationEvent:
    event_id: InvalidationEventId
    contract_version: str
    trigger_identity: InvalidationTriggerId
    trigger_kind: str
    predecessor_evidence_ref: EvidenceRef
    successor_or_correction_ref: EvidenceRef
    knowledge_from: Timestamp
    effective_from: Timestamp | None
    dependency_graph_ref: EvidenceRef
    graph_identity: ProvenanceGraphId
    affected_set_id: AffectedSetId
    affected_dataset_version_ids: tuple[DatasetVersionId, ...]
    scope_ref: EvidenceRef
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        body = _invalidation_body(self)
        digest = _digest("ATIS:C10:INVALIDATION_EVENT:1", body)
        if self.content_digest != digest or self.event_id != InvalidationEventId(
            _derived("c10-invalidation-event", digest)
        ):
            raise LifecycleError("INVALIDATION_EVENT_IDENTITY_CONTENT_CONFLICT")


def _invalidation_body(value: InvalidationEvent) -> dict[str, object]:
    return {
        "contract_version": _text(value.contract_version, "contract_version"),
        "trigger_identity": value.trigger_identity.value,
        "trigger_kind": _text(value.trigger_kind, "trigger_kind"),
        "predecessor_evidence_ref": _ref(value.predecessor_evidence_ref),
        "successor_or_correction_ref": _ref(value.successor_or_correction_ref),
        "knowledge_from": _timestamp(value.knowledge_from),
        "effective_from": None if value.effective_from is None else _timestamp(value.effective_from),
        "dependency_graph_ref": _ref(value.dependency_graph_ref),
        "graph_identity": value.graph_identity.value,
        "affected_set_id": value.affected_set_id.value,
        "affected_dataset_version_ids": [item.value for item in value.affected_dataset_version_ids],
        "scope_ref": _ref(value.scope_ref),
        "reasons": list(value.reasons),
        "evidence_refs": [_ref(item) for item in value.evidence_refs],
    }


def create_invalidation_event(
    *, contract_version: str, trigger_kind: str,
    predecessor_evidence_ref: EvidenceRef, successor_or_correction_ref: EvidenceRef,
    knowledge_from: Timestamp, effective_from: Timestamp | None,
    dependency_graph_ref: EvidenceRef, affected_set: AffectedSet,
    scope_ref: EvidenceRef,
    reasons: tuple[str, ...], evidence_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> InvalidationEvent:
    if type(affected_set) is not AffectedSet:
        raise TypeError("affected_set must be AffectedSet")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    canonical_reasons = _reasons(reasons, resource_policy)
    refs = _refs(evidence_refs, allow_empty=False)
    if affected_set.resource_policy_id != resource_policy.policy_id:
        raise LifecycleError("AFFECTED_SET_RESOURCE_POLICY_IDENTITY_CONFLICT")
    provisional = object.__new__(InvalidationEvent)
    assignments = {
        "contract_version": _text(contract_version, "contract_version"),
        "trigger_identity": affected_set.trigger_ids[0], "trigger_kind": _text(trigger_kind, "trigger_kind"),
        "predecessor_evidence_ref": predecessor_evidence_ref,
        "successor_or_correction_ref": successor_or_correction_ref,
        "knowledge_from": knowledge_from, "effective_from": effective_from,
        "dependency_graph_ref": dependency_graph_ref,
        "graph_identity": affected_set.provenance_graph_id,
        "affected_set_id": affected_set.affected_set_id,
        "affected_dataset_version_ids": affected_set.dataset_version_ids,
        "scope_ref": scope_ref, "reasons": canonical_reasons, "evidence_refs": refs,
    }
    for name, value in assignments.items():
        object.__setattr__(provisional, name, value)
    body = _invalidation_body(provisional)
    digest = _digest("ATIS:C10:INVALIDATION_EVENT:1", body)
    object.__setattr__(provisional, "content_digest", digest)
    object.__setattr__(provisional, "event_id", InvalidationEventId(_derived("c10-invalidation-event", digest)))
    provisional.__post_init__()
    return provisional


@dataclass(frozen=True, slots=True)
class PropagationRecord:
    propagation_run_id: PropagationRunId
    contract_version: str
    invalidation_event_id: InvalidationEventId
    affected_set_id: AffectedSetId
    graph_identity: ProvenanceGraphId
    resource_policy_id: DatasetLifecycleResourcePolicyId
    processed_dataset_version_ids: tuple[DatasetVersionId, ...]
    remaining_dataset_version_ids: tuple[DatasetVersionId, ...]
    expected_affected_set_digest: EvidenceContentDigest
    processed_set_digest: EvidenceContentDigest
    remaining_set_digest: EvidenceContentDigest
    state: PropagationRecordState
    restart_identity: PropagationRunId | None
    restart_evidence_refs: tuple[EvidenceRef, ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        processed = _versions(self.processed_dataset_version_ids, "processed set")
        remaining = _versions(self.remaining_dataset_version_ids, "remaining set")
        expected = _versions(processed + remaining, "expected affected set")
        if set(processed) & set(remaining):
            raise PropagationError("PROPAGATION_SET_RECONCILIATION_FAILED")
        if self.expected_affected_set_digest != _set_digest("ATIS:C10:EXPECTED_AFFECTED_SET:1", expected):
            raise PropagationError("EXPECTED_AFFECTED_SET_DIGEST_CONFLICT")
        if self.processed_set_digest != _set_digest("ATIS:C10:PROCESSED_SET:1", processed):
            raise PropagationError("PROCESSED_SET_DIGEST_CONFLICT")
        if self.remaining_set_digest != _set_digest("ATIS:C10:REMAINING_SET:1", remaining):
            raise PropagationError("REMAINING_SET_DIGEST_CONFLICT")
        body = _propagation_body(self)
        digest = _digest("ATIS:C10:PROPAGATION_RECORD:1", body)
        if self.content_digest != digest or self.propagation_run_id != PropagationRunId(
            _derived("c10-propagation", digest)
        ):
            raise PropagationError("PROPAGATION_IDENTITY_CONTENT_CONFLICT")


def _propagation_body(value: PropagationRecord) -> dict[str, object]:
    return {
        "contract_version": _text(value.contract_version, "contract_version"),
        "invalidation_event_id": value.invalidation_event_id.value,
        "affected_set_id": value.affected_set_id.value,
        "graph_identity": value.graph_identity.value,
        "resource_policy_id": value.resource_policy_id.value,
        "processed_dataset_version_ids": [item.value for item in value.processed_dataset_version_ids],
        "remaining_dataset_version_ids": [item.value for item in value.remaining_dataset_version_ids],
        "expected_affected_set_digest": value.expected_affected_set_digest.value,
        "processed_set_digest": value.processed_set_digest.value,
        "remaining_set_digest": value.remaining_set_digest.value,
        "state": value.state.value,
        "restart_identity": None if value.restart_identity is None else value.restart_identity.value,
        "restart_evidence_refs": [_ref(item) for item in value.restart_evidence_refs],
        "reasons": list(value.reasons),
        "evidence_refs": [_ref(item) for item in value.evidence_refs],
    }


def create_propagation_record(
    *, contract_version: str, invalidation_event: InvalidationEvent,
    affected_set: AffectedSet, evidence_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> PropagationRecord:
    """Create the initial propagation checkpoint for an exact affected set."""
    _validate_lifecycle_context(invalidation_event, affected_set, resource_policy)
    expected = affected_set.dataset_version_ids
    state = PropagationRecordState.COMPLETE if not expected else PropagationRecordState.NOT_STARTED
    return _build_propagation_record(
        contract_version=contract_version,
        invalidation_event=invalidation_event,
        affected_set=affected_set,
        processed=expected if state is PropagationRecordState.COMPLETE else (),
        remaining=() if state is PropagationRecordState.COMPLETE else expected,
        state=state,
        restart_identity=None,
        restart_evidence_refs=(),
        reasons=(),
        evidence_refs=evidence_refs,
        resource_policy=resource_policy,
    )


def _validate_lifecycle_context(
    invalidation_event: InvalidationEvent,
    affected_set: AffectedSet,
    resource_policy: DatasetLifecycleResourcePolicy,
) -> None:
    if type(invalidation_event) is not InvalidationEvent:
        raise TypeError("invalidation_event must be InvalidationEvent")
    if type(affected_set) is not AffectedSet:
        raise TypeError("affected_set must be AffectedSet")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise TypeError("resource_policy must be DatasetLifecycleResourcePolicy")
    if invalidation_event.affected_set_id != affected_set.affected_set_id:
        raise PropagationError("AFFECTED_SET_SUBSTITUTION")
    if invalidation_event.graph_identity != affected_set.provenance_graph_id:
        raise PropagationError("PROPAGATION_GRAPH_SUBSTITUTION")
    if invalidation_event.affected_dataset_version_ids != affected_set.dataset_version_ids:
        raise PropagationError("AFFECTED_SET_SUBSTITUTION")
    if affected_set.resource_policy_id != resource_policy.policy_id:
        raise PropagationError("PROPAGATION_RESOURCE_POLICY_SUBSTITUTION")


def _validate_predecessor_basis(
    predecessor: PropagationRecord,
    invalidation_event: InvalidationEvent,
    affected_set: AffectedSet,
    resource_policy: DatasetLifecycleResourcePolicy,
) -> None:
    if type(predecessor) is not PropagationRecord:
        raise TypeError("predecessor must be PropagationRecord")
    _validate_lifecycle_context(invalidation_event, affected_set, resource_policy)
    if predecessor.invalidation_event_id != invalidation_event.event_id:
        raise PropagationError("PROPAGATION_EVENT_SUBSTITUTION")
    if predecessor.affected_set_id != affected_set.affected_set_id:
        raise PropagationError("AFFECTED_SET_SUBSTITUTION")
    if predecessor.graph_identity != affected_set.provenance_graph_id:
        raise PropagationError("PROPAGATION_GRAPH_SUBSTITUTION")
    if predecessor.resource_policy_id != resource_policy.policy_id:
        raise PropagationError("PROPAGATION_RESOURCE_POLICY_SUBSTITUTION")
    expected = affected_set.dataset_version_ids
    if predecessor.expected_affected_set_digest != _set_digest("ATIS:C10:EXPECTED_AFFECTED_SET:1", expected):
        raise PropagationError("EXPECTED_AFFECTED_SET_DIGEST_CONFLICT")
    processed = _versions(predecessor.processed_dataset_version_ids, "processed set")
    remaining = _versions(predecessor.remaining_dataset_version_ids, "remaining set")
    if set(processed) & set(remaining) or set(processed) | set(remaining) != set(expected):
        raise PropagationError("PROPAGATION_SET_RECONCILIATION_FAILED")
    if predecessor.processed_set_digest != _set_digest("ATIS:C10:PROCESSED_SET:1", processed):
        raise PropagationError("PROCESSED_SET_DIGEST_CONFLICT")
    if predecessor.remaining_set_digest != _set_digest("ATIS:C10:REMAINING_SET:1", remaining):
        raise PropagationError("REMAINING_SET_DIGEST_CONFLICT")


def _build_propagation_record(
    *, contract_version: str, invalidation_event: InvalidationEvent,
    affected_set: AffectedSet, processed: tuple[DatasetVersionId, ...],
    remaining: tuple[DatasetVersionId, ...], state: PropagationRecordState,
    restart_identity: PropagationRunId | None,
    restart_evidence_refs: tuple[EvidenceRef, ...], reasons: tuple[str, ...],
    evidence_refs: tuple[EvidenceRef, ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> PropagationRecord:
    expected = affected_set.dataset_version_ids
    processed = _versions(processed, "processed set")
    remaining = _versions(remaining, "remaining set")
    if set(processed) & set(remaining) or set(processed) | set(remaining) != set(expected):
        raise PropagationError("PROPAGATION_SET_RECONCILIATION_FAILED")
    if len(processed) > resource_policy.value("MAX_PROPAGATION_EVENTS_PER_RUN"):
        raise PropagationError("MAX_PROPAGATION_EVENTS_PER_RUN_EXHAUSTED")
    canonical_reasons = _reasons(reasons, resource_policy)
    if state in (PropagationRecordState.NOT_STARTED, PropagationRecordState.IN_PROGRESS, PropagationRecordState.COMPLETE):
        if canonical_reasons:
            raise PropagationError("PROGRESSION_STATE_REASONS_PROHIBITED")
    elif not canonical_reasons:
        raise PropagationError("RESTRICTIVE_STATE_REASONS_REQUIRED")
    refs = _refs(evidence_refs, allow_empty=state is PropagationRecordState.NOT_ESTABLISHED)
    restart_refs = _refs(restart_evidence_refs, allow_empty=True)
    if restart_identity is None and restart_refs:
        raise PropagationError("RESTART_EVIDENCE_WITHOUT_PREDECESSOR")
    if state is PropagationRecordState.FAILED and not refs:
        raise PropagationError("FAILED_EVIDENCE_REQUIRED")
    if state is PropagationRecordState.NOT_STARTED and processed:
        raise PropagationError("NOT_STARTED_PROCESSED_SET_NOT_EMPTY")
    if state is PropagationRecordState.COMPLETE and (remaining or processed != expected):
        raise PropagationError("INCOMPLETE_PROPAGATION_AS_COMPLETE")
    if state is not PropagationRecordState.COMPLETE and processed == expected:
        raise PropagationError("EXACT_COMPLETION_STATE_REQUIRED")
    provisional = object.__new__(PropagationRecord)
    assignments = {
        "contract_version": _text(contract_version, "contract_version"),
        "invalidation_event_id": invalidation_event.event_id,
        "affected_set_id": affected_set.affected_set_id,
        "graph_identity": affected_set.provenance_graph_id,
        "resource_policy_id": resource_policy.policy_id,
        "processed_dataset_version_ids": processed,
        "remaining_dataset_version_ids": remaining,
        "expected_affected_set_digest": _set_digest("ATIS:C10:EXPECTED_AFFECTED_SET:1", expected),
        "processed_set_digest": _set_digest("ATIS:C10:PROCESSED_SET:1", processed),
        "remaining_set_digest": _set_digest("ATIS:C10:REMAINING_SET:1", remaining),
        "state": state, "restart_identity": restart_identity,
        "restart_evidence_refs": restart_refs,
        "reasons": canonical_reasons, "evidence_refs": refs,
    }
    for name, value in assignments.items():
        object.__setattr__(provisional, name, value)
    body = _propagation_body(provisional)
    digest = _digest("ATIS:C10:PROPAGATION_RECORD:1", body)
    object.__setattr__(provisional, "content_digest", digest)
    object.__setattr__(provisional, "propagation_run_id", PropagationRunId(_derived("c10-propagation", digest)))
    provisional.__post_init__()
    return provisional


def advance_propagation(
    *, predecessor: PropagationRecord, invalidation_event: InvalidationEvent,
    affected_set: AffectedSet, resource_policy: DatasetLifecycleResourcePolicy,
    processed_dataset_version_ids: tuple[DatasetVersionId, ...],
    next_state: PropagationRecordState, reasons: tuple[str, ...],
    evidence_refs: tuple[EvidenceRef, ...],
) -> PropagationRecord:
    """Advance an exact nonterminal propagation checkpoint monotonically."""
    _validate_predecessor_basis(predecessor, invalidation_event, affected_set, resource_policy)
    if predecessor.state in (
        PropagationRecordState.COMPLETE,
        PropagationRecordState.FAILED,
        PropagationRecordState.NOT_ESTABLISHED,
    ):
        raise PropagationError("TERMINAL_PROPAGATION_STATE")
    allowed = {
        PropagationRecordState.NOT_STARTED: {
            PropagationRecordState.IN_PROGRESS,
            PropagationRecordState.FAILED,
            PropagationRecordState.NOT_ESTABLISHED,
        },
        PropagationRecordState.IN_PROGRESS: {
            PropagationRecordState.IN_PROGRESS,
            PropagationRecordState.COMPLETE,
            PropagationRecordState.FAILED,
            PropagationRecordState.NOT_ESTABLISHED,
        },
    }
    if next_state not in allowed[predecessor.state]:
        raise PropagationError("INVALID_PROPAGATION_TRANSITION")
    processed = _versions(processed_dataset_version_ids, "processed set")
    expected = affected_set.dataset_version_ids
    if not set(processed).issubset(expected):
        raise PropagationError("FOREIGN_PROCESSED_MEMBER")
    old_processed = set(predecessor.processed_dataset_version_ids)
    if not old_processed.issubset(processed):
        raise PropagationError("PROCESSED_MEMBER_DISAPPEARED")
    if (
        predecessor.state is PropagationRecordState.IN_PROGRESS
        and next_state is PropagationRecordState.IN_PROGRESS
        and old_processed == set(processed)
    ):
        raise PropagationError("IN_PROGRESS_REQUIRES_NEW_PROGRESS")
    remaining = tuple(item for item in expected if item not in set(processed))
    return _build_propagation_record(
        contract_version=predecessor.contract_version,
        invalidation_event=invalidation_event,
        affected_set=affected_set,
        processed=processed,
        remaining=remaining,
        state=next_state,
        restart_identity=predecessor.propagation_run_id,
        restart_evidence_refs=(),
        reasons=reasons,
        evidence_refs=evidence_refs,
        resource_policy=resource_policy,
    )


def restart_propagation(
    *, predecessor: PropagationRecord, invalidation_event: InvalidationEvent,
    affected_set: AffectedSet, resource_policy: DatasetLifecycleResourcePolicy,
    restart_evidence_refs: tuple[EvidenceRef, ...],
) -> PropagationRecord:
    """Restart a restrictive terminal checkpoint with exact attribution."""
    _validate_predecessor_basis(predecessor, invalidation_event, affected_set, resource_policy)
    if predecessor.state not in (
        PropagationRecordState.FAILED,
        PropagationRecordState.NOT_ESTABLISHED,
    ):
        raise PropagationError("RESTART_STATE_NOT_PERMITTED")
    restart_refs = _refs(restart_evidence_refs, allow_empty=False)
    carry_progress = not (
        predecessor.state is PropagationRecordState.NOT_ESTABLISHED
        and not predecessor.evidence_refs
    )
    processed = predecessor.processed_dataset_version_ids if carry_progress else ()
    remaining = tuple(
        item for item in affected_set.dataset_version_ids if item not in set(processed)
    )
    return _build_propagation_record(
        contract_version=predecessor.contract_version,
        invalidation_event=invalidation_event,
        affected_set=affected_set,
        processed=processed,
        remaining=remaining,
        state=PropagationRecordState.IN_PROGRESS,
        restart_identity=predecessor.propagation_run_id,
        restart_evidence_refs=restart_refs,
        reasons=(),
        evidence_refs=restart_refs,
        resource_policy=resource_policy,
    )


def _validate_applicable_propagation(
    *, invalidation_event: InvalidationEvent,
    propagation_record: PropagationRecord,
) -> None:
    if type(propagation_record) is not PropagationRecord:
        raise TypeError("propagation_record must be PropagationRecord")
    if propagation_record.invalidation_event_id != invalidation_event.event_id:
        raise PropagationError("PROPAGATION_EVENT_SUBSTITUTION")
    if propagation_record.affected_set_id != invalidation_event.affected_set_id:
        raise PropagationError("AFFECTED_SET_SUBSTITUTION")
    if propagation_record.graph_identity != invalidation_event.graph_identity:
        raise PropagationError("PROPAGATION_GRAPH_SUBSTITUTION")
    expected = invalidation_event.affected_dataset_version_ids
    if propagation_record.expected_affected_set_digest != _set_digest(
        "ATIS:C10:EXPECTED_AFFECTED_SET:1", expected
    ):
        raise PropagationError("EXPECTED_AFFECTED_SET_DIGEST_CONFLICT")
    if set(propagation_record.processed_dataset_version_ids) | set(
        propagation_record.remaining_dataset_version_ids
    ) != set(expected):
        raise PropagationError("PROPAGATION_SET_RECONCILIATION_FAILED")
    if propagation_record.state is PropagationRecordState.COMPLETE and (
        propagation_record.processed_dataset_version_ids != expected
        or propagation_record.remaining_dataset_version_ids
    ):
        raise PropagationError("INCOMPLETE_PROPAGATION_AS_COMPLETE")


def _lifecycle_currentness_state(
    *, dataset_version_id: DatasetVersionId, base_state: CurrentnessState,
    invalidation_event: InvalidationEvent | None,
    propagation_record: PropagationRecord | None,
) -> tuple[CurrentnessState, tuple[str, ...]]:
    if type(dataset_version_id) is not DatasetVersionId:
        raise TypeError("dataset_version_id must be DatasetVersionId")
    if type(base_state) is not CurrentnessState:
        raise TypeError("base_state must be CurrentnessState")
    if invalidation_event is not None and type(invalidation_event) is not InvalidationEvent:
        raise TypeError("invalidation_event must be InvalidationEvent or None")
    if invalidation_event is None and propagation_record is not None:
        raise PropagationError("PROPAGATION_WITHOUT_INVALIDATION_EVENT")
    if invalidation_event is not None and propagation_record is not None:
        _validate_applicable_propagation(
            invalidation_event=invalidation_event,
            propagation_record=propagation_record,
        )
    if base_state is CurrentnessState.STALE:
        return CurrentnessState.STALE, ()
    if base_state is CurrentnessState.UNKNOWN:
        return CurrentnessState.UNKNOWN, ()
    if invalidation_event is None or dataset_version_id not in invalidation_event.affected_dataset_version_ids:
        return CurrentnessState.CURRENT, ()
    if propagation_record is None or propagation_record.state is not PropagationRecordState.COMPLETE:
        return CurrentnessState.UNKNOWN, ("PROPAGATION_PENDING",)
    return CurrentnessState.CURRENT, ("PROPAGATION_COMPLETE",)


def affected_dataset_is_authoritatively_current(
    *, dataset_version_id: DatasetVersionId, invalidation_event: InvalidationEvent,
    propagation_record: PropagationRecord | None,
) -> bool:
    """Convenience wrapper over the authoritative restrictive lifecycle rule."""
    state, _ = _lifecycle_currentness_state(
        dataset_version_id=dataset_version_id,
        base_state=CurrentnessState.CURRENT,
        invalidation_event=invalidation_event,
        propagation_record=propagation_record,
    )
    return state is CurrentnessState.CURRENT

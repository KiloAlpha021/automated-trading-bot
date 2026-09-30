"""Provider-independent C05 PRE-SYNC-2 quality mechanics.

The module evaluates explicit abstract descriptors and evidence.  It does not
adapt C04 observations, define canonical semantic bytes or material fields,
interpret production sequences, decide eligibility, or assess freshness.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import json
import re
import unicodedata

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
)


QUALITY_INPUT_FAMILY = "ATIS_C05_QUALITY_INPUT"
QUALITY_POLICY_FAMILY = "ATIS_C05_QUALITY_POLICY"
MAX_QUALITY_DESCRIPTORS = 4096
MAX_QUALITY_POLICY_DECISIONS = 4096
MAX_QUALITY_REASON_LENGTH = 255
MAX_PRODUCTION_COHORT = 4096
MAX_PRODUCTION_EVIDENCE_REFS = 64
MAX_PRODUCTION_METRICS = 256
PRODUCTION_POLICY_FAMILY = "ATIS_C05_PRODUCTION_QUALITY_POLICY"
PRODUCTION_SEQUENCE_FAMILY = "ATIS_C05_PRODUCTION_SEQUENCE_EVIDENCE"
PRODUCTION_METRIC_FAMILY = "ATIS_C05_PRODUCTION_METRIC_INPUT"

_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,254}", re.ASCII)


class QualityEvaluationError(ValueError):
    """The requested abstract quality evaluation cannot be established."""


class PostSync2BoundaryError(QualityEvaluationError):
    """A request crossed into concrete behavior reserved behind SYNC-2."""


class QualityDimension(StrEnum):
    PRESENCE = "PRESENCE"
    STRUCTURE = "STRUCTURE"
    IDENTITY = "IDENTITY"
    OUTLIER = "OUTLIER"
    SEQUENCE_COVERAGE = "SEQUENCE_COVERAGE"
    SEQUENCE_ORDER = "SEQUENCE_ORDER"


class PresenceState(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class StructureState(StrEnum):
    WELL_FORMED = "WELL_FORMED"
    MALFORMED = "MALFORMED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class IdentityState(StrEnum):
    UNIQUE = "UNIQUE"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    MATERIAL_CONFLICT = "MATERIAL_CONFLICT"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OutlierState(StrEnum):
    WITHIN_POLICY = "WITHIN_POLICY"
    OUTSIDE_POLICY = "OUTSIDE_POLICY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SequenceCoverageState(StrEnum):
    COMPLETE = "COMPLETE"
    GAP = "GAP"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SequenceOrderState(StrEnum):
    CONSISTENT = "CONSISTENT"
    REORDERED = "REORDERED"
    AMBIGUOUS_ORDER = "AMBIGUOUS_ORDER"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProductionEvidenceState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    AMBIGUOUS_CONFLICTING = "AMBIGUOUS_CONFLICTING"


@dataclass(frozen=True, slots=True)
class ProductionSequenceEvidence:
    """Externally authoritative historical expectation and order evidence."""

    state: ProductionEvidenceState
    version: ContractVersion | None
    expectation_ref: EvidenceRef | None
    calendar_ref: EvidenceRef | None
    knowledge_bound_ref: EvidenceRef | None
    expected_logical_keys: tuple[str, ...] = ()
    observed_logical_keys: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if type(self.state) is not ProductionEvidenceState:
            raise TypeError("state must be a ProductionEvidenceState")
        if len(self.expected_logical_keys) > MAX_PRODUCTION_COHORT or len(self.observed_logical_keys) > MAX_PRODUCTION_COHORT:
            raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
        for values in (self.expected_logical_keys, self.observed_logical_keys):
            if any(type(item) is not str or not item or item != item.strip() for item in values):
                raise QualityEvaluationError("MALFORMED_SEQUENCE_IDENTITY")
            if len(set(values)) != len(values):
                raise QualityEvaluationError("AMBIGUOUS_SEQUENCE_IDENTITY")
        refs = canonicalize_evidence_refs(self.evidence_refs)
        if len(refs) > MAX_PRODUCTION_EVIDENCE_REFS:
            raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
        object.__setattr__(self, "evidence_refs", refs)
        required = (self.expectation_ref, self.calendar_ref, self.knowledge_bound_ref)
        if self.state is ProductionEvidenceState.AVAILABLE:
            if self.version != ContractVersion(PRODUCTION_SEQUENCE_FAMILY, 1) or any(ref is None for ref in required):
                raise QualityEvaluationError("SEQUENCE_EVIDENCE_BINDING_INCOMPLETE")
            if not self.expected_logical_keys:
                raise QualityEvaluationError("EXPECTED_SEQUENCE_MISSING")
            if any(ref not in refs for ref in required):
                raise QualityEvaluationError("SEQUENCE_EVIDENCE_NOT_ATTRIBUTABLE")
        elif self.expected_logical_keys or self.observed_logical_keys:
            raise QualityEvaluationError("UNAVAILABLE_SEQUENCE_MUST_NOT_CARRY_TRUTH")


@dataclass(frozen=True, slots=True)
class ProductionMetric:
    descriptor_digest: EvidenceContentDigest
    name: str
    value: str

    def __post_init__(self) -> None:
        if type(self.descriptor_digest) is not EvidenceContentDigest:
            raise TypeError("descriptor_digest must be an EvidenceContentDigest")
        if type(self.name) is not str or not self.name or self.name != self.name.strip():
            raise QualityEvaluationError("MALFORMED_METRIC_NAME")
        if type(self.value) is not str:
            raise TypeError("value must be a decimal string")
        try:
            parsed = Decimal(self.value)
        except InvalidOperation as error:
            raise QualityEvaluationError("MALFORMED_METRIC_VALUE") from error
        if not parsed.is_finite():
            raise QualityEvaluationError("NON_FINITE_METRIC_VALUE")


@dataclass(frozen=True, slots=True)
class ProductionMetricEvidence:
    state: ProductionEvidenceState
    version: ContractVersion | None
    metrics_ref: EvidenceRef | None
    knowledge_bound_ref: EvidenceRef | None
    metrics: tuple[ProductionMetric, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if len(self.metrics) > MAX_PRODUCTION_METRICS:
            raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
        if any(type(item) is not ProductionMetric for item in self.metrics):
            raise TypeError("metrics contains an invalid value")
        keys = [(item.descriptor_digest, item.name) for item in self.metrics]
        if len(set(keys)) != len(keys):
            raise QualityEvaluationError("METRIC_IDENTITY_COLLISION")
        refs = canonicalize_evidence_refs(self.evidence_refs)
        if len(refs) > MAX_PRODUCTION_EVIDENCE_REFS:
            raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
        object.__setattr__(self, "metrics", tuple(sorted(self.metrics, key=lambda item: (item.descriptor_digest.value, item.name))))
        object.__setattr__(self, "evidence_refs", refs)
        if self.state is ProductionEvidenceState.AVAILABLE:
            if self.version != ContractVersion(PRODUCTION_METRIC_FAMILY, 1):
                return
            if self.metrics_ref is None or self.knowledge_bound_ref is None:
                raise QualityEvaluationError("METRIC_EVIDENCE_BINDING_INCOMPLETE")
            if self.metrics_ref not in refs or self.knowledge_bound_ref not in refs:
                raise QualityEvaluationError("METRIC_EVIDENCE_NOT_ATTRIBUTABLE")
        elif self.metrics:
            raise QualityEvaluationError("UNAVAILABLE_METRICS_MUST_NOT_CARRY_VALUES")


@dataclass(frozen=True, slots=True)
class ApprovedMetricRule:
    name: str
    minimum: str | None
    maximum: str | None

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise QualityEvaluationError("MALFORMED_POLICY_METRIC")
        if self.minimum is None and self.maximum is None:
            raise QualityEvaluationError("POLICY_THRESHOLD_MISSING")
        values = []
        for value in (self.minimum, self.maximum):
            if value is not None:
                try:
                    parsed = Decimal(value)
                except InvalidOperation as error:
                    raise QualityEvaluationError("MALFORMED_POLICY_THRESHOLD") from error
                if not parsed.is_finite():
                    raise QualityEvaluationError("NON_FINITE_POLICY_THRESHOLD")
                values.append(parsed)
        if len(values) == 2 and values[0] > values[1]:
            raise QualityEvaluationError("POLICY_THRESHOLD_ORDER_INVALID")


@dataclass(frozen=True, slots=True)
class ApprovedProductionQualityPolicy:
    version: ContractVersion
    policy_ref: EvidenceRef
    applicability_ref: EvidenceRef
    knowledge_bound_ref: EvidenceRef
    rules: tuple[ApprovedMetricRule, ...]
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if len(self.rules) > MAX_PRODUCTION_METRICS:
            raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
        if any(type(item) is not ApprovedMetricRule for item in self.rules):
            raise TypeError("rules contains an invalid value")
        if len({item.name for item in self.rules}) != len(self.rules):
            raise QualityEvaluationError("POLICY_METRIC_IDENTITY_COLLISION")
        refs = canonicalize_evidence_refs(self.evidence_refs)
        if any(ref not in refs for ref in (self.policy_ref, self.applicability_ref, self.knowledge_bound_ref)):
            raise QualityEvaluationError("POLICY_EVIDENCE_NOT_ATTRIBUTABLE")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda item: item.name)))
        object.__setattr__(self, "evidence_refs", refs)


@dataclass(frozen=True, slots=True)
class ProductionQualityResult:
    descriptor_digests: tuple[EvidenceContentDigest, ...]
    sequence_coverage: SequenceCoverageState
    sequence_order: SequenceOrderState
    outlier_states: tuple[tuple[EvidenceContentDigest, OutlierState], ...]
    missing_logical_keys: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    validation_state: ValidationState

    def __post_init__(self) -> None:
        object.__setattr__(self, "descriptor_digests", tuple(sorted(set(self.descriptor_digests), key=lambda item: item.value)))
        object.__setattr__(self, "outlier_states", tuple(sorted(self.outlier_states, key=lambda item: item[0].value)))
        object.__setattr__(self, "missing_logical_keys", tuple(sorted(set(self.missing_logical_keys))))
        object.__setattr__(self, "reasons", tuple(sorted(set(_reason(item) for item in self.reasons))))
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _ref_mapping(value: EvidenceRef | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _timestamp_text(value: Timestamp) -> str:
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _reason(value: str) -> str:
    if type(value) is not str or _REASON.fullmatch(value) is None:
        raise ValueError("reason must be a stable uppercase identifier")
    return value


@dataclass(frozen=True, slots=True)
class ResolvedQualityEvidence:
    """Exact immutable bytes for one EvidenceRef used by C05."""

    ref: EvidenceRef
    content: bytes

    def __post_init__(self) -> None:
        if type(self.ref) is not EvidenceRef:
            raise TypeError("ref must be an EvidenceRef")
        if type(self.content) is not bytes:
            raise TypeError("content must be bytes")
        if EvidenceContentDigest.from_bytes(self.content) != self.ref.content_digest:
            raise QualityEvaluationError("EVIDENCE_CONTENT_DIGEST_MISMATCH")


@dataclass(frozen=True, slots=True)
class QualityLogicalIdentity:
    namespace_ref: EvidenceRef
    key: str

    def __post_init__(self) -> None:
        if type(self.namespace_ref) is not EvidenceRef:
            raise TypeError("namespace_ref must be an EvidenceRef")
        if type(self.key) is not str or not self.key or self.key != self.key.strip():
            raise ValueError("key must be nonempty and unpadded")
        if unicodedata.normalize("NFC", self.key) != self.key:
            raise ValueError("key must already be NFC-normalized")

    @property
    def group_key(self) -> tuple[tuple[object, ...], str]:
        return self.namespace_ref.key, self.key


@dataclass(frozen=True, slots=True)
class AbstractSemanticContent:
    """Caller-supplied abstract semantics; never a C04 serialization rule."""

    contract_ref: EvidenceRef
    canonical_bytes_ref: EvidenceRef
    digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        if type(self.contract_ref) is not EvidenceRef:
            raise TypeError("contract_ref must be an EvidenceRef")
        if type(self.canonical_bytes_ref) is not EvidenceRef:
            raise TypeError("canonical_bytes_ref must be an EvidenceRef")
        if type(self.digest) is not EvidenceContentDigest:
            raise TypeError("digest must be an EvidenceContentDigest")
        if self.canonical_bytes_ref.content_digest != self.digest:
            raise QualityEvaluationError("SEMANTIC_DIGEST_REFERENCE_MISMATCH")


@dataclass(frozen=True, slots=True)
class QualityInputDescriptor:
    descriptor_version: ContractVersion
    cohort_ref: EvidenceRef
    evaluation_scope_ref: EvidenceRef
    expected_sequence_ref: EvidenceRef | None
    input_ref: EvidenceRef | None
    logical_identity: QualityLogicalIdentity | None
    metric_inputs_ref: EvidenceRef | None
    order_evidence_ref: EvidenceRef | None
    representation_contract_ref: EvidenceRef | None
    semantic_content: AbstractSemanticContent | None
    validation_policy_id: ValidationPolicyId
    validation_policy_ref: EvidenceRef
    evaluated_at: Timestamp
    malformed_evidence_ref: EvidenceRef | None = None
    descriptor_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if type(self.descriptor_version) is not ContractVersion:
            raise TypeError("descriptor_version must be a ContractVersion")
        if self.descriptor_version != ContractVersion(QUALITY_INPUT_FAMILY, 1):
            raise QualityEvaluationError("INCOMPATIBLE_QUALITY_INPUT_VERSION")
        for name in ("cohort_ref", "evaluation_scope_ref", "validation_policy_ref"):
            if type(getattr(self, name)) is not EvidenceRef:
                raise TypeError(f"{name} must be an EvidenceRef")
        optional_refs = (
            "expected_sequence_ref",
            "input_ref",
            "metric_inputs_ref",
            "order_evidence_ref",
            "representation_contract_ref",
            "malformed_evidence_ref",
        )
        for name in optional_refs:
            value = getattr(self, name)
            if value is not None and type(value) is not EvidenceRef:
                raise TypeError(f"{name} must be an EvidenceRef or None")
        if self.logical_identity is not None and type(self.logical_identity) is not QualityLogicalIdentity:
            raise TypeError("logical_identity must be a QualityLogicalIdentity or None")
        if self.semantic_content is not None and type(self.semantic_content) is not AbstractSemanticContent:
            raise TypeError("semantic_content must be AbstractSemanticContent or None")
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.evaluated_at) is not Timestamp:
            raise TypeError("evaluated_at must be a Timestamp")
        if self.input_ref is None and self.malformed_evidence_ref is not None:
            raise QualityEvaluationError("MALFORMED_INPUT_REQUIRES_RAW_INPUT_REF")
        object.__setattr__(self, "descriptor_digest", self._compute_digest())

    def _compute_digest(self) -> EvidenceContentDigest:
        logical = None
        if self.logical_identity is not None:
            logical = {
                "key": self.logical_identity.key,
                "namespace_ref": _ref_mapping(self.logical_identity.namespace_ref),
            }
        semantic = None
        if self.semantic_content is not None:
            semantic = {
                "canonical_bytes_ref": _ref_mapping(self.semantic_content.canonical_bytes_ref),
                "contract_ref": _ref_mapping(self.semantic_content.contract_ref),
                "digest": self.semantic_content.digest.value,
            }
        mapping = {
            "cohort_ref": _ref_mapping(self.cohort_ref),
            "descriptor_version": {
                "family": self.descriptor_version.family,
                "version": self.descriptor_version.version,
            },
            "evaluated_at": _timestamp_text(self.evaluated_at),
            "evaluation_scope_ref": _ref_mapping(self.evaluation_scope_ref),
            "expected_sequence_ref": _ref_mapping(self.expected_sequence_ref),
            "input_ref": _ref_mapping(self.input_ref),
            "logical_identity": logical,
            "malformed_evidence_ref": _ref_mapping(self.malformed_evidence_ref),
            "metric_inputs_ref": _ref_mapping(self.metric_inputs_ref),
            "order_evidence_ref": _ref_mapping(self.order_evidence_ref),
            "record_kind": "ATIS_C05_QUALITY_INPUT_DESCRIPTOR",
            "representation_contract_ref": _ref_mapping(self.representation_contract_ref),
            "semantic_content": semantic,
            "validation_policy_id": self.validation_policy_id.value,
            "validation_policy_ref": _ref_mapping(self.validation_policy_ref),
        }
        return EvidenceContentDigest.from_bytes(_canonical_json(mapping))


@dataclass(frozen=True, slots=True)
class AbstractOutlierDecision:
    descriptor_digest: EvidenceContentDigest
    metric_inputs_ref: EvidenceRef
    state: OutlierState
    reason: str
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.descriptor_digest) is not EvidenceContentDigest:
            raise TypeError("descriptor_digest must be an EvidenceContentDigest")
        if type(self.metric_inputs_ref) is not EvidenceRef:
            raise TypeError("metric_inputs_ref must be an EvidenceRef")
        if self.state not in (OutlierState.WITHIN_POLICY, OutlierState.OUTSIDE_POLICY):
            raise ValueError("abstract decision must be a conclusive outlier state")
        object.__setattr__(self, "reason", _reason(self.reason))
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class AbstractQualityPolicy:
    policy_version: ContractVersion
    validation_policy_id: ValidationPolicyId
    validation_policy_ref: EvidenceRef
    requested_dimensions: tuple[QualityDimension, ...]
    outlier_decisions: tuple[AbstractOutlierDecision, ...] = ()
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if type(self.policy_version) is not ContractVersion:
            raise TypeError("policy_version must be a ContractVersion")
        if self.policy_version != ContractVersion(QUALITY_POLICY_FAMILY, 1):
            raise QualityEvaluationError("INCOMPATIBLE_QUALITY_POLICY_VERSION")
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_policy_ref) is not EvidenceRef:
            raise TypeError("validation_policy_ref must be an EvidenceRef")
        if type(self.requested_dimensions) is not tuple or not self.requested_dimensions:
            raise ValueError("requested_dimensions must be a nonempty tuple")
        if any(type(item) is not QualityDimension for item in self.requested_dimensions):
            raise TypeError("requested_dimensions contains an invalid value")
        if len(set(self.requested_dimensions)) != len(self.requested_dimensions):
            raise ValueError("requested_dimensions contains duplicates")
        forbidden = {QualityDimension.SEQUENCE_COVERAGE, QualityDimension.SEQUENCE_ORDER}
        if forbidden.intersection(self.requested_dimensions):
            raise PostSync2BoundaryError("PRODUCTION_SEQUENCE_EVALUATION_REQUIRES_SYNC_2")
        decisions = self.outlier_decisions
        if type(decisions) is not tuple or any(type(item) is not AbstractOutlierDecision for item in decisions):
            raise TypeError("outlier_decisions must contain AbstractOutlierDecision values")
        if len(decisions) > MAX_QUALITY_POLICY_DECISIONS:
            raise ValueError("outlier_decisions exceeds the resource limit")
        keys = [item.descriptor_digest for item in decisions]
        if len(set(keys)) != len(keys):
            raise ValueError("outlier_decisions contains duplicate descriptor identities")
        ordered = tuple(sorted(decisions, key=lambda item: item.descriptor_digest.value))
        object.__setattr__(self, "requested_dimensions", tuple(sorted(self.requested_dimensions)))
        object.__setattr__(self, "outlier_decisions", ordered)
        object.__setattr__(self, "content_digest", self._compute_digest())

    def _compute_digest(self) -> EvidenceContentDigest:
        mapping = {
            "decisions": [
                {
                    "descriptor_digest": item.descriptor_digest.value,
                    "evidence_refs": [_ref_mapping(ref) for ref in item.evidence_refs],
                    "metric_inputs_ref": _ref_mapping(item.metric_inputs_ref),
                    "reason": item.reason,
                    "state": item.state.value,
                }
                for item in self.outlier_decisions
            ],
            "policy_version": {
                "family": self.policy_version.family,
                "version": self.policy_version.version,
            },
            "record_kind": "ATIS_C05_ABSTRACT_QUALITY_POLICY",
            "requested_dimensions": [item.value for item in self.requested_dimensions],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_policy_ref": _ref_mapping(self.validation_policy_ref),
        }
        return EvidenceContentDigest.from_bytes(_canonical_json(mapping))


@dataclass(frozen=True, slots=True)
class DimensionReason:
    dimension: QualityDimension
    reason: str

    def __post_init__(self) -> None:
        if type(self.dimension) is not QualityDimension:
            raise TypeError("dimension must be a QualityDimension")
        object.__setattr__(self, "reason", _reason(self.reason))


@dataclass(frozen=True, slots=True)
class DimensionEvidence:
    dimension: QualityDimension
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.dimension) is not QualityDimension:
            raise TypeError("dimension must be a QualityDimension")
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class QualityAssessmentResult:
    descriptor_digest: EvidenceContentDigest
    evaluation_scope_ref: EvidenceRef
    validation_policy_id: ValidationPolicyId
    validation_policy_ref: EvidenceRef
    validation_state: ValidationState
    presence: PresenceState
    structure: StructureState
    identity: IdentityState
    outlier: OutlierState
    sequence_coverage: SequenceCoverageState
    sequence_order: SequenceOrderState
    dimension_reasons: tuple[DimensionReason, ...]
    dimension_evidence_refs: tuple[DimensionEvidence, ...]
    logical_group_members: tuple[EvidenceContentDigest, ...]
    evaluated_at: Timestamp
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        expected = (
            ("descriptor_digest", EvidenceContentDigest),
            ("evaluation_scope_ref", EvidenceRef),
            ("validation_policy_id", ValidationPolicyId),
            ("validation_policy_ref", EvidenceRef),
            ("validation_state", ValidationState),
            ("evaluated_at", Timestamp),
        )
        for name, kind in expected:
            if type(getattr(self, name)) is not kind:
                raise TypeError(f"{name} must be a {kind.__name__}")
        if len({item.dimension for item in self.dimension_reasons}) != len(self.dimension_reasons):
            raise ValueError("dimension_reasons contains duplicates")
        if len({item.dimension for item in self.dimension_evidence_refs}) != len(self.dimension_evidence_refs):
            raise ValueError("dimension_evidence_refs contains duplicates")
        members = tuple(sorted(set(self.logical_group_members), key=lambda item: item.value))
        object.__setattr__(self, "logical_group_members", members)
        object.__setattr__(self, "dimension_reasons", tuple(sorted(self.dimension_reasons, key=lambda item: item.dimension.value)))
        object.__setattr__(self, "dimension_evidence_refs", tuple(sorted(self.dimension_evidence_refs, key=lambda item: item.dimension.value)))
        object.__setattr__(self, "content_digest", self._compute_digest())

    def _compute_digest(self) -> EvidenceContentDigest:
        mapping = {
            "descriptor_digest": self.descriptor_digest.value,
            "dimension_evidence_refs": [
                {"dimension": item.dimension.value, "evidence_refs": [_ref_mapping(ref) for ref in item.evidence_refs]}
                for item in self.dimension_evidence_refs
            ],
            "dimension_reasons": [
                {"dimension": item.dimension.value, "reason": item.reason}
                for item in self.dimension_reasons
            ],
            "evaluated_at": _timestamp_text(self.evaluated_at),
            "evaluation_scope_ref": _ref_mapping(self.evaluation_scope_ref),
            "identity": self.identity.value,
            "logical_group_members": [item.value for item in self.logical_group_members],
            "outlier": self.outlier.value,
            "presence": self.presence.value,
            "record_kind": "ATIS_C05_QUALITY_ASSESSMENT",
            "sequence_coverage": self.sequence_coverage.value,
            "sequence_order": self.sequence_order.value,
            "structure": self.structure.value,
            "validation_policy_id": self.validation_policy_id.value,
            "validation_policy_ref": _ref_mapping(self.validation_policy_ref),
            "validation_state": self.validation_state.value,
        }
        return EvidenceContentDigest.from_bytes(_canonical_json(mapping))


def _resolved_index(values: tuple[ResolvedQualityEvidence, ...]) -> dict[tuple[object, ...], bytes]:
    if type(values) is not tuple:
        raise TypeError("resolved_evidence must be a tuple")
    refs = canonicalize_evidence_refs(tuple(item.ref for item in values))
    if len(refs) != len(values):
        raise EvidenceIdentityConflict("DUPLICATE_RESOLVED_EVIDENCE")
    return {item.ref.key: item.content for item in values}


def _require_resolved(ref: EvidenceRef, resolved: dict[tuple[object, ...], bytes]) -> bytes:
    try:
        content = resolved[ref.key]
    except KeyError as error:
        raise QualityEvaluationError("MISSING_RESOLVED_EVIDENCE") from error
    if EvidenceContentDigest.from_bytes(content) != ref.content_digest:
        raise QualityEvaluationError("EVIDENCE_CONTENT_DIGEST_MISMATCH")
    return content


def _all_refs(value: QualityInputDescriptor) -> tuple[EvidenceRef, ...]:
    refs = [value.cohort_ref, value.evaluation_scope_ref, value.validation_policy_ref]
    for ref in (
        value.expected_sequence_ref,
        value.input_ref,
        value.metric_inputs_ref,
        value.order_evidence_ref,
        value.representation_contract_ref,
        value.malformed_evidence_ref,
    ):
        if ref is not None:
            refs.append(ref)
    if value.logical_identity is not None:
        refs.append(value.logical_identity.namespace_ref)
    if value.semantic_content is not None:
        refs.extend((value.semantic_content.contract_ref, value.semantic_content.canonical_bytes_ref))
    return canonicalize_evidence_refs(tuple(refs))


def evaluate_quality(
    descriptors: tuple[QualityInputDescriptor, ...],
    policy: AbstractQualityPolicy,
    resolved_evidence: tuple[ResolvedQualityEvidence, ...],
    supported_representation_contracts: tuple[EvidenceRef, ...],
) -> tuple[QualityAssessmentResult, ...]:
    """Evaluate bounded abstract C05 inputs without crossing SYNC-2."""

    if type(descriptors) is not tuple or not descriptors:
        raise ValueError("descriptors must be a nonempty tuple")
    if len(descriptors) > MAX_QUALITY_DESCRIPTORS:
        raise ValueError("descriptors exceeds the resource limit")
    if any(type(item) is not QualityInputDescriptor for item in descriptors):
        raise TypeError("descriptors contains an invalid value")
    if type(policy) is not AbstractQualityPolicy:
        raise TypeError("policy must be an AbstractQualityPolicy")
    if type(supported_representation_contracts) is not tuple:
        raise TypeError("supported_representation_contracts must be a tuple")
    supported = canonicalize_evidence_refs(supported_representation_contracts)
    resolved = _resolved_index(resolved_evidence)
    _require_resolved(policy.validation_policy_ref, resolved)
    supported_keys = {item.key for item in supported}
    for ref in supported:
        _require_resolved(ref, resolved)
    for descriptor in descriptors:
        if descriptor.validation_policy_id != policy.validation_policy_id or descriptor.validation_policy_ref != policy.validation_policy_ref:
            raise QualityEvaluationError("QUALITY_POLICY_BINDING_MISMATCH")
        for ref in _all_refs(descriptor):
            _require_resolved(ref, resolved)
        if descriptor.semantic_content is not None:
            content = _require_resolved(descriptor.semantic_content.canonical_bytes_ref, resolved)
            if EvidenceContentDigest.from_bytes(content) != descriptor.semantic_content.digest:
                raise QualityEvaluationError("SEMANTIC_CONTENT_DIGEST_MISMATCH")

    groups: dict[tuple[object, ...], list[QualityInputDescriptor]] = {}
    for descriptor in descriptors:
        if descriptor.logical_identity is not None:
            key = (descriptor.cohort_ref.key, *descriptor.logical_identity.group_key)
            groups.setdefault(key, []).append(descriptor)
    decisions = {item.descriptor_digest: item for item in policy.outlier_decisions}
    results: list[QualityAssessmentResult] = []
    requested = set(policy.requested_dimensions)
    for descriptor in descriptors:
        reasons: list[DimensionReason] = []
        evidence: list[DimensionEvidence] = []

        if QualityDimension.PRESENCE not in requested:
            presence = PresenceState.NOT_ESTABLISHED
            reasons.append(DimensionReason(QualityDimension.PRESENCE, "CHECK_NOT_REQUESTED"))
        elif descriptor.input_ref is None:
            if descriptor.expected_sequence_ref is None:
                presence = PresenceState.NOT_ESTABLISHED
                reasons.append(DimensionReason(QualityDimension.PRESENCE, "EXPECTATION_EVIDENCE_MISSING"))
            else:
                presence = PresenceState.MISSING
                reasons.append(DimensionReason(QualityDimension.PRESENCE, "EXPECTED_INPUT_ABSENT"))
                evidence.append(DimensionEvidence(QualityDimension.PRESENCE, (descriptor.expected_sequence_ref,)))
        else:
            presence = PresenceState.PRESENT
            reasons.append(DimensionReason(QualityDimension.PRESENCE, "INPUT_PRESENT"))
            evidence.append(DimensionEvidence(QualityDimension.PRESENCE, (descriptor.input_ref,)))

        incompatible = False
        if presence is PresenceState.MISSING:
            structure = StructureState.NOT_APPLICABLE
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "INPUT_ABSENT"))
        elif QualityDimension.STRUCTURE not in requested:
            structure = StructureState.NOT_ESTABLISHED
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "CHECK_NOT_REQUESTED"))
        elif descriptor.representation_contract_ref is None:
            structure = StructureState.NOT_ESTABLISHED
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "REPRESENTATION_CONTRACT_MISSING"))
        elif descriptor.representation_contract_ref.key not in supported_keys:
            structure = StructureState.NOT_ESTABLISHED
            incompatible = True
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "REPRESENTATION_CONTRACT_INCOMPATIBLE"))
            evidence.append(DimensionEvidence(QualityDimension.STRUCTURE, (descriptor.representation_contract_ref,)))
        elif descriptor.malformed_evidence_ref is not None:
            structure = StructureState.MALFORMED
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "MALFORMED_INPUT_EVIDENCE"))
            evidence.append(DimensionEvidence(QualityDimension.STRUCTURE, (descriptor.representation_contract_ref, descriptor.malformed_evidence_ref)))
        else:
            structure = StructureState.WELL_FORMED
            reasons.append(DimensionReason(QualityDimension.STRUCTURE, "REPRESENTATION_CONTRACT_SATISFIED"))
            evidence.append(DimensionEvidence(QualityDimension.STRUCTURE, (descriptor.representation_contract_ref,)))

        members: tuple[EvidenceContentDigest, ...] = ()
        if presence is PresenceState.MISSING:
            identity = IdentityState.NOT_APPLICABLE
            reasons.append(DimensionReason(QualityDimension.IDENTITY, "INPUT_ABSENT"))
        elif QualityDimension.IDENTITY not in requested:
            identity = IdentityState.NOT_ESTABLISHED
            reasons.append(DimensionReason(QualityDimension.IDENTITY, "CHECK_NOT_REQUESTED"))
        elif descriptor.logical_identity is None or descriptor.semantic_content is None:
            identity = IdentityState.NOT_ESTABLISHED
            reasons.append(DimensionReason(QualityDimension.IDENTITY, "SEMANTIC_BINDING_INCOMPLETE"))
        else:
            key = (descriptor.cohort_ref.key, *descriptor.logical_identity.group_key)
            group = groups[key]
            members = tuple(item.descriptor_digest for item in group)
            established = [item.semantic_content.digest for item in group if item.semantic_content is not None]
            distinct = set(established)
            if len(distinct) > 1:
                identity = IdentityState.MATERIAL_CONFLICT
                reasons.append(DimensionReason(QualityDimension.IDENTITY, "MATERIAL_SEMANTIC_CONFLICT"))
            elif len(established) != len(group):
                identity = IdentityState.NOT_ESTABLISHED
                reasons.append(DimensionReason(QualityDimension.IDENTITY, "GROUP_SEMANTICS_INCOMPLETE"))
            elif len(group) > 1:
                identity = IdentityState.EXACT_DUPLICATE
                reasons.append(DimensionReason(QualityDimension.IDENTITY, "EXACT_SEMANTIC_DUPLICATE"))
            else:
                identity = IdentityState.UNIQUE
                reasons.append(DimensionReason(QualityDimension.IDENTITY, "UNIQUE_IN_COMPLETE_COHORT"))
            evidence.append(DimensionEvidence(QualityDimension.IDENTITY, tuple(item.semantic_content.canonical_bytes_ref for item in group if item.semantic_content is not None)))

        if presence is PresenceState.MISSING:
            outlier = OutlierState.NOT_APPLICABLE
            reasons.append(DimensionReason(QualityDimension.OUTLIER, "INPUT_ABSENT"))
        elif QualityDimension.OUTLIER not in requested:
            outlier = OutlierState.NOT_APPLICABLE
            reasons.append(DimensionReason(QualityDimension.OUTLIER, "CHECK_NOT_REQUESTED"))
        else:
            decision = decisions.get(descriptor.descriptor_digest)
            if descriptor.metric_inputs_ref is None or decision is None:
                outlier = OutlierState.NOT_ESTABLISHED
                reasons.append(DimensionReason(QualityDimension.OUTLIER, "OUTLIER_EVIDENCE_MISSING"))
            elif decision.metric_inputs_ref != descriptor.metric_inputs_ref:
                outlier = OutlierState.NOT_ESTABLISHED
                reasons.append(DimensionReason(QualityDimension.OUTLIER, "OUTLIER_INPUT_BINDING_MISMATCH"))
            else:
                outlier = decision.state
                reasons.append(DimensionReason(QualityDimension.OUTLIER, decision.reason))
                evidence.append(DimensionEvidence(QualityDimension.OUTLIER, decision.evidence_refs))

        sequence_coverage = SequenceCoverageState.NOT_ESTABLISHED
        sequence_order = SequenceOrderState.NOT_ESTABLISHED
        reasons.append(DimensionReason(QualityDimension.SEQUENCE_COVERAGE, "SYNC_2_NOT_CONSUMABLE"))
        reasons.append(DimensionReason(QualityDimension.SEQUENCE_ORDER, "SYNC_2_NOT_CONSUMABLE"))

        invalid = (
            presence is PresenceState.MISSING
            or structure is StructureState.MALFORMED
            or identity is IdentityState.MATERIAL_CONFLICT
            or outlier is OutlierState.OUTSIDE_POLICY
        )
        requested_states = {
            QualityDimension.PRESENCE: presence,
            QualityDimension.STRUCTURE: structure,
            QualityDimension.IDENTITY: identity,
            QualityDimension.OUTLIER: outlier,
        }
        unresolved = any(
            requested_states[dimension] in (
                PresenceState.NOT_ESTABLISHED,
                StructureState.NOT_ESTABLISHED,
                IdentityState.NOT_ESTABLISHED,
                OutlierState.NOT_ESTABLISHED,
            )
            for dimension in requested
        )
        if invalid:
            validation_state = ValidationState.INVALID
        elif incompatible:
            validation_state = ValidationState.INCOMPATIBLE
        elif not requested:
            validation_state = ValidationState.NOT_VALIDATED
        elif unresolved:
            validation_state = ValidationState.NOT_ESTABLISHED
        else:
            validation_state = ValidationState.VALID
        results.append(
            QualityAssessmentResult(
                descriptor_digest=descriptor.descriptor_digest,
                evaluation_scope_ref=descriptor.evaluation_scope_ref,
                validation_policy_id=descriptor.validation_policy_id,
                validation_policy_ref=descriptor.validation_policy_ref,
                validation_state=validation_state,
                presence=presence,
                structure=structure,
                identity=identity,
                outlier=outlier,
                sequence_coverage=sequence_coverage,
                sequence_order=sequence_order,
                dimension_reasons=tuple(reasons),
                dimension_evidence_refs=tuple(evidence),
                logical_group_members=members,
                evaluated_at=descriptor.evaluated_at,
            )
        )
    return tuple(sorted(results, key=lambda item: item.descriptor_digest.value))


def evaluate_production_quality(
    descriptors: tuple[object, ...],
    sequence: ProductionSequenceEvidence,
    metric_evidence: ProductionMetricEvidence,
    policy: ApprovedProductionQualityPolicy | None,
    resolved_evidence: tuple[ResolvedQualityEvidence, ...],
) -> ProductionQualityResult:
    """Evaluate POST-SYNC-2 quality using only supplied historical authority."""

    from automated_trading_bot.market_data.compatibility import CompatibilityDescriptor

    if type(descriptors) is not tuple or not descriptors:
        raise ValueError("descriptors must be a nonempty tuple")
    if len(descriptors) > MAX_PRODUCTION_COHORT:
        raise QualityEvaluationError("RESOURCE_BOUND_EXHAUSTED")
    if any(type(item) is not CompatibilityDescriptor for item in descriptors):
        raise TypeError("descriptors must contain CompatibilityDescriptor values")
    if type(sequence) is not ProductionSequenceEvidence:
        raise TypeError("sequence must be ProductionSequenceEvidence")
    if type(metric_evidence) is not ProductionMetricEvidence:
        raise TypeError("metric_evidence must be ProductionMetricEvidence")
    if policy is not None and type(policy) is not ApprovedProductionQualityPolicy:
        raise TypeError("policy must be ApprovedProductionQualityPolicy or None")

    resolved = _resolved_index(resolved_evidence)
    refs = sequence.evidence_refs + metric_evidence.evidence_refs
    if policy is not None:
        refs += policy.evidence_refs
    for ref in canonicalize_evidence_refs(refs):
        _require_resolved(ref, resolved)

    quality_inputs = [item.quality_input for item in descriptors]  # type: ignore[attr-defined]
    descriptor_digests = tuple(item.descriptor_digest for item in quality_inputs)
    logical_keys = []
    for item in quality_inputs:
        if item.logical_identity is None:
            raise QualityEvaluationError("LOGICAL_IDENTITY_NOT_ESTABLISHED")
        logical_keys.append(item.logical_identity.key)
    if len(set(logical_keys)) != len(logical_keys):
        semantic_by_key: dict[str, set[EvidenceContentDigest]] = {}
        for item in quality_inputs:
            if item.logical_identity is None or item.semantic_content is None:
                raise QualityEvaluationError("LOGICAL_IDENTITY_NOT_ESTABLISHED")
            semantic_by_key.setdefault(item.logical_identity.key, set()).add(item.semantic_content.digest)
        if any(len(values) > 1 for values in semantic_by_key.values()):
            raise QualityEvaluationError("MATERIAL_SEMANTIC_CONFLICT")

    reasons: list[str] = []
    evidence_refs: list[EvidenceRef] = list(refs)
    missing: tuple[str, ...] = ()
    incompatible = False
    unresolved = False
    invalid = False

    if sequence.state is ProductionEvidenceState.AVAILABLE:
        compatibility_refs = {item.expected_sequence_evidence.ref for item in descriptors}  # type: ignore[attr-defined]
        if compatibility_refs != {sequence.expectation_ref}:
            raise QualityEvaluationError("EXPECTED_SEQUENCE_BINDING_MISMATCH")
        observed = tuple(sequence.observed_logical_keys)
        if set(observed) != set(logical_keys):
            raise QualityEvaluationError("OBSERVED_COHORT_BINDING_MISMATCH")
        missing = tuple(sorted(set(sequence.expected_logical_keys) - set(observed)))
        sequence_coverage = SequenceCoverageState.GAP if missing else SequenceCoverageState.COMPLETE
        sequence_order = (
            SequenceOrderState.CONSISTENT
            if observed == tuple(item for item in sequence.expected_logical_keys if item in set(observed))
            else SequenceOrderState.REORDERED
        )
        reasons.extend(("AUTHORITATIVE_SEQUENCE_EVALUATED", "AUTHORITATIVE_ORDER_EVALUATED"))
        invalid = bool(missing) or sequence_order is SequenceOrderState.REORDERED
    elif sequence.state is ProductionEvidenceState.NOT_APPLICABLE:
        sequence_coverage = SequenceCoverageState.NOT_APPLICABLE
        sequence_order = SequenceOrderState.NOT_APPLICABLE
        reasons.append("SEQUENCE_NOT_APPLICABLE")
    elif sequence.state is ProductionEvidenceState.INCOMPATIBLE:
        sequence_coverage = SequenceCoverageState.NOT_ESTABLISHED
        sequence_order = SequenceOrderState.NOT_ESTABLISHED
        reasons.append("SEQUENCE_EVIDENCE_INCOMPATIBLE")
        incompatible = True
    elif sequence.state is ProductionEvidenceState.AMBIGUOUS_CONFLICTING:
        sequence_coverage = SequenceCoverageState.NOT_ESTABLISHED
        sequence_order = SequenceOrderState.AMBIGUOUS_ORDER
        reasons.append("SEQUENCE_EVIDENCE_CONFLICTING")
        invalid = True
    else:
        sequence_coverage = SequenceCoverageState.NOT_ESTABLISHED
        sequence_order = SequenceOrderState.NOT_ESTABLISHED
        reasons.append("AUTHORITATIVE_SEQUENCE_UNAVAILABLE")
        unresolved = True

    states: list[tuple[EvidenceContentDigest, OutlierState]] = []
    if metric_evidence.state is ProductionEvidenceState.INCOMPATIBLE or (
        metric_evidence.state is ProductionEvidenceState.AVAILABLE
        and metric_evidence.version != ContractVersion(PRODUCTION_METRIC_FAMILY, 1)
    ):
        states = [(digest, OutlierState.NOT_ESTABLISHED) for digest in descriptor_digests]
        reasons.append("METRIC_VERSION_INCOMPATIBLE")
        incompatible = True
    elif metric_evidence.state is not ProductionEvidenceState.AVAILABLE:
        states = [(digest, OutlierState.NOT_ESTABLISHED) for digest in descriptor_digests]
        reasons.append("METRIC_EVIDENCE_NOT_ESTABLISHED")
        unresolved = True
    elif policy is None:
        states = [(digest, OutlierState.NOT_ESTABLISHED) for digest in descriptor_digests]
        reasons.append("APPROVED_POLICY_NOT_ESTABLISHED")
        unresolved = True
    elif policy.version != ContractVersion(PRODUCTION_POLICY_FAMILY, 1):
        states = [(digest, OutlierState.NOT_ESTABLISHED) for digest in descriptor_digests]
        reasons.append("POLICY_VERSION_INCOMPATIBLE")
        incompatible = True
    else:
        rules = {item.name: item for item in policy.rules}
        metrics: dict[EvidenceContentDigest, list[ProductionMetric]] = {}
        for item in metric_evidence.metrics:
            if item.descriptor_digest not in descriptor_digests:
                raise QualityEvaluationError("METRIC_DESCRIPTOR_BINDING_MISMATCH")
            metrics.setdefault(item.descriptor_digest, []).append(item)
        for digest in descriptor_digests:
            supplied = metrics.get(digest, [])
            if not supplied:
                states.append((digest, OutlierState.NOT_ESTABLISHED))
                unresolved = True
                continue
            outside = False
            for metric in supplied:
                rule = rules.get(metric.name)
                if rule is None:
                    raise QualityEvaluationError("UNAPPROVED_METRIC")
                value = Decimal(metric.value)
                if rule.minimum is not None and value < Decimal(rule.minimum):
                    outside = True
                if rule.maximum is not None and value > Decimal(rule.maximum):
                    outside = True
            state = OutlierState.OUTSIDE_POLICY if outside else OutlierState.WITHIN_POLICY
            states.append((digest, state))
            invalid = invalid or outside
        reasons.append("APPROVED_POLICY_EVALUATED")

    validation_state = (
        ValidationState.INVALID if invalid else
        ValidationState.INCOMPATIBLE if incompatible else
        ValidationState.NOT_ESTABLISHED if unresolved else
        ValidationState.VALID
    )
    return ProductionQualityResult(
        descriptor_digests=descriptor_digests,
        sequence_coverage=sequence_coverage,
        sequence_order=sequence_order,
        outlier_states=tuple(states),
        missing_logical_keys=missing,
        reasons=tuple(reasons),
        evidence_refs=tuple(evidence_refs),
        validation_state=validation_state,
    )

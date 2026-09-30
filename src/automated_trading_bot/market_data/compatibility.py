"""Bounded C04-to-C05 compatibility adapter.

This module owns the C04 semantic projection and maps it to the existing C05
provider-independent input descriptor.  It does not evaluate quality, infer
logical identity or sequence authority, promote SYNC-2, or confer downstream
authority.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import json

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    ValidationPolicyId,
    canonicalize_evidence_refs,
)
from automated_trading_bot.market_data.normalization import (
    CanonicalObservation,
    SupportedInterpretation,
    verify_normalization_content_digest,
)
from automated_trading_bot.market_data.quality import (
    QUALITY_INPUT_FAMILY,
    AbstractSemanticContent,
    QualityInputDescriptor,
    QualityLogicalIdentity,
)


SEMANTIC_PROJECTION_FAMILY = "ATIS_C04_C05_SEMANTIC_PROJECTION"
COMPATIBILITY_DESCRIPTOR_FAMILY = "ATIS_C04_C05_COMPATIBILITY_DESCRIPTOR"
PRODUCER_CONTRACT_FAMILY = "ATIS_STAGE3_C03_C04_IMPLEMENTATION_CONTRACT"
MATERIAL_FIELD_POLICY_FAMILY = "ATIS_C04_C05_MATERIAL_FIELD_POLICY"
ADAPTER_CONTRACT_FAMILY = "ATIS_C04_C05_COMPATIBILITY_ADAPTER"
MAX_MATERIAL_FIELDS = 256
MAX_SEMANTIC_BYTES = 1_048_576
MAX_DESCRIPTOR_EVIDENCE_REFS = 64


class CompatibilityError(ValueError):
    """The protected C04-to-C05 contract cannot be established exactly."""


class IncompatibleContractError(CompatibilityError):
    """An exact version or evidence binding is not admitted."""


class CompatibilityEvidenceState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    AMBIGUOUS_CONFLICTING = "AMBIGUOUS_CONFLICTING"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
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


def _version_mapping(value: ContractVersion) -> dict[str, object]:
    return {"family": value.family, "version": value.version}


@dataclass(frozen=True, slots=True)
class ResolvedCompatibilityEvidence:
    ref: EvidenceRef
    content: bytes

    def __post_init__(self) -> None:
        if type(self.ref) is not EvidenceRef:
            raise TypeError("ref must be an EvidenceRef")
        if type(self.content) is not bytes:
            raise TypeError("content must be bytes")
        if EvidenceContentDigest.from_bytes(self.content) != self.ref.content_digest:
            raise CompatibilityError("EVIDENCE_CONTENT_DIGEST_MISMATCH")


@dataclass(frozen=True, slots=True)
class MaterialFieldPolicy:
    policy_version: ContractVersion
    projection_version: ContractVersion
    source_interpretation_id: object
    material_fields: tuple[str, ...]
    policy_ref: EvidenceRef
    content: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from automated_trading_bot.market_data.acquisition import SourceInterpretationId

        if self.policy_version != ContractVersion(MATERIAL_FIELD_POLICY_FAMILY, 1):
            raise IncompatibleContractError("UNSUPPORTED_MATERIAL_POLICY")
        if self.projection_version != ContractVersion(SEMANTIC_PROJECTION_FAMILY, 1):
            raise IncompatibleContractError("SEMANTIC_PROJECTION_VERSION_UNSUPPORTED")
        if type(self.source_interpretation_id) is not SourceInterpretationId:
            raise TypeError("source_interpretation_id must be a SourceInterpretationId")
        if type(self.policy_ref) is not EvidenceRef:
            raise TypeError("policy_ref must be an EvidenceRef")
        if type(self.material_fields) is not tuple or not self.material_fields:
            raise CompatibilityError("MATERIAL_FIELD_POLICY_MISSING_OR_UNKNOWN")
        if len(self.material_fields) > MAX_MATERIAL_FIELDS:
            raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
        if any(type(name) is not str or not name for name in self.material_fields):
            raise CompatibilityError("MATERIAL_FIELD_POLICY_MISSING_OR_UNKNOWN")
        canonical = tuple(sorted(self.material_fields))
        if len(set(canonical)) != len(canonical):
            raise CompatibilityError("MATERIAL_FIELD_POLICY_MISSING_OR_UNKNOWN")
        object.__setattr__(self, "material_fields", canonical)
        content = _canonical_json(
            {
                "material_fields": list(canonical),
                "policy_version": _version_mapping(self.policy_version),
                "projection_contract_version": _version_mapping(self.projection_version),
                "record_kind": "ATIS_C04_C05_MATERIAL_FIELD_POLICY_V1",
                "source_interpretation_id": self.source_interpretation_id.value,
            }
        )
        if EvidenceContentDigest.from_bytes(content) != self.policy_ref.content_digest:
            raise CompatibilityError("MATERIAL_FIELD_POLICY_CONTENT_MISMATCH")
        object.__setattr__(self, "content", content)


@dataclass(frozen=True, slots=True)
class SequenceCalendarEvidence:
    state: CompatibilityEvidenceState
    ref: EvidenceRef | None
    contract_version: ContractVersion | None
    reason: str
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not CompatibilityEvidenceState:
            raise TypeError("state must be a CompatibilityEvidenceState")
        if self.ref is not None and type(self.ref) is not EvidenceRef:
            raise TypeError("ref must be an EvidenceRef or None")
        if self.contract_version is not None and type(self.contract_version) is not ContractVersion:
            raise TypeError("contract_version must be a ContractVersion or None")
        if type(self.reason) is not str or not self.reason or self.reason != self.reason.strip():
            raise ValueError("reason must be nonempty and unpadded")
        if len(self.evidence_refs) > MAX_DESCRIPTOR_EVIDENCE_REFS:
            raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
        refs = canonicalize_evidence_refs(self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        if self.state is CompatibilityEvidenceState.AVAILABLE:
            if self.ref is None or self.contract_version is None:
                raise CompatibilityError("SEQUENCE_CALENDAR_EVIDENCE_MISSING_WHEN_REQUIRED")
            if self.ref not in refs:
                raise CompatibilityError("AVAILABLE_SEQUENCE_REF_NOT_EVIDENCED")
        elif self.ref is not None:
            raise CompatibilityError("SEQUENCE_REF_ONLY_ALLOWED_WHEN_AVAILABLE")
        if self.state is CompatibilityEvidenceState.NOT_APPLICABLE and self.contract_version is None:
            raise CompatibilityError("NOT_APPLICABLE_REQUIRES_VERSION_BOUND_EVIDENCE")


@dataclass(frozen=True, slots=True)
class CompatibilityDescriptor:
    descriptor_version: ContractVersion
    canonical_observation_ref: EvidenceRef
    logical_identity: QualityLogicalIdentity
    logical_identity_mapping_ref: EvidenceRef
    semantic_projection_contract_ref: EvidenceRef
    canonical_bytes_ref: EvidenceRef
    semantic_digest: EvidenceContentDigest
    material_field_policy_ref: EvidenceRef
    producer_contract_ref: EvidenceRef
    adapter_contract_ref: EvidenceRef
    expected_sequence_evidence: SequenceCalendarEvidence
    descriptor_evidence_refs: tuple[EvidenceRef, ...]
    quality_input: QualityInputDescriptor
    semantic_bytes: bytes = field(repr=False)
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.descriptor_version != ContractVersion(COMPATIBILITY_DESCRIPTOR_FAMILY, 1):
            raise IncompatibleContractError("DESCRIPTOR_CONTRACT_MISMATCH")
        if len(self.descriptor_evidence_refs) > MAX_DESCRIPTOR_EVIDENCE_REFS:
            raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
        refs = canonicalize_evidence_refs(self.descriptor_evidence_refs)
        object.__setattr__(self, "descriptor_evidence_refs", refs)
        if EvidenceContentDigest.from_bytes(self.semantic_bytes) != self.semantic_digest:
            raise CompatibilityError("SEMANTIC_DIGEST_MISMATCH")
        mapping = {
            "adapter_contract_ref": _ref_mapping(self.adapter_contract_ref),
            "canonical_bytes_ref": _ref_mapping(self.canonical_bytes_ref),
            "canonical_observation_ref": _ref_mapping(self.canonical_observation_ref),
            "descriptor_evidence_refs": [_ref_mapping(ref) for ref in refs],
            "descriptor_version": _version_mapping(self.descriptor_version),
            "expected_sequence_evidence": {
                "contract_version": None if self.expected_sequence_evidence.contract_version is None else _version_mapping(self.expected_sequence_evidence.contract_version),
                "evidence_refs": [_ref_mapping(ref) for ref in self.expected_sequence_evidence.evidence_refs],
                "reason": self.expected_sequence_evidence.reason,
                "ref": _ref_mapping(self.expected_sequence_evidence.ref),
                "state": self.expected_sequence_evidence.state.value,
            },
            "logical_identity": {"key": self.logical_identity.key, "namespace_ref": _ref_mapping(self.logical_identity.namespace_ref)},
            "logical_identity_mapping_ref": _ref_mapping(self.logical_identity_mapping_ref),
            "material_field_policy_ref": _ref_mapping(self.material_field_policy_ref),
            "producer_contract_ref": _ref_mapping(self.producer_contract_ref),
            "record_kind": "ATIS_C04_C05_COMPATIBILITY_DESCRIPTOR_V1",
            "semantic_digest": self.semantic_digest.value,
            "semantic_projection_contract_ref": _ref_mapping(self.semantic_projection_contract_ref),
        }
        object.__setattr__(self, "content_digest", EvidenceContentDigest.from_bytes(_canonical_json(mapping)))


def _resolved_index(values: tuple[ResolvedCompatibilityEvidence, ...]) -> dict[tuple[object, ...], bytes]:
    if type(values) is not tuple:
        raise TypeError("resolved_evidence must be a tuple")
    if len(values) > MAX_DESCRIPTOR_EVIDENCE_REFS:
        raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
    if not values:
        raise CompatibilityError("MISSING_RESOLVED_EVIDENCE")
    refs = canonicalize_evidence_refs(tuple(item.ref for item in values))
    if len(refs) != len(values):
        raise EvidenceIdentityConflict("DUPLICATE_RESOLVED_EVIDENCE")
    return {item.ref.key: item.content for item in values}


def _require_resolved(ref: EvidenceRef, resolved: dict[tuple[object, ...], bytes]) -> bytes:
    try:
        content = resolved[ref.key]
    except KeyError as error:
        raise CompatibilityError("MISSING_RESOLVED_EVIDENCE") from error
    if EvidenceContentDigest.from_bytes(content) != ref.content_digest:
        raise CompatibilityError("EVIDENCE_CONTENT_DIGEST_MISMATCH")
    return content


def build_compatibility_descriptor(
    *,
    observation: CanonicalObservation,
    observation_ref: EvidenceRef,
    interpretation: SupportedInterpretation,
    material_policy: MaterialFieldPolicy,
    logical_identity: QualityLogicalIdentity,
    logical_identity_mapping_ref: EvidenceRef,
    producer_contract_version: ContractVersion,
    producer_contract_ref: EvidenceRef,
    semantic_projection_contract_ref: EvidenceRef,
    adapter_contract_version: ContractVersion,
    adapter_contract_ref: EvidenceRef,
    consumer_descriptor_version: ContractVersion,
    canonical_bytes_ref: EvidenceRef,
    sequence_evidence: SequenceCalendarEvidence,
    descriptor_evidence_refs: tuple[EvidenceRef, ...],
    resolved_evidence: tuple[ResolvedCompatibilityEvidence, ...],
    cohort_ref: EvidenceRef,
    evaluation_scope_ref: EvidenceRef,
    validation_policy_id: ValidationPolicyId,
    validation_policy_ref: EvidenceRef,
    evaluated_at: Timestamp,
) -> CompatibilityDescriptor:
    """Build the sole admitted v1 descriptor; every ambiguity rejects."""

    if type(observation) is not CanonicalObservation:
        raise TypeError("observation must be a CanonicalObservation")
    verify_normalization_content_digest(observation)
    if observation_ref.content_digest != observation.content_digest:
        raise CompatibilityError("CANONICAL_OBSERVATION_REFERENCE_MISMATCH")
    exact_tuple = (
        ContractVersion(PRODUCER_CONTRACT_FAMILY, 1),
        ContractVersion(SEMANTIC_PROJECTION_FAMILY, 1),
        ContractVersion(COMPATIBILITY_DESCRIPTOR_FAMILY, 1),
        ContractVersion(QUALITY_INPUT_FAMILY, 1),
        ContractVersion(ADAPTER_CONTRACT_FAMILY, 1),
    )
    supplied_tuple = (
        producer_contract_version,
        material_policy.projection_version,
        ContractVersion(COMPATIBILITY_DESCRIPTOR_FAMILY, 1),
        consumer_descriptor_version,
        adapter_contract_version,
    )
    if supplied_tuple != exact_tuple:
        raise IncompatibleContractError("PRODUCER_CONSUMER_VERSION_INCOMPATIBLE")
    if material_policy.source_interpretation_id != observation.acquisition.source_interpretation_id:
        raise CompatibilityError("MATERIAL_POLICY_INTERPRETATION_MISMATCH")
    if interpretation.interpretation_id != material_policy.source_interpretation_id:
        raise CompatibilityError("MATERIAL_POLICY_INTERPRETATION_MISMATCH")
    admitted = set(interpretation.required_fields + interpretation.optional_fields)
    if not set(material_policy.material_fields) <= admitted:
        raise CompatibilityError("UNKNOWN_MATERIAL_FIELD")
    fields = {item.name: item.value for item in observation.fields}
    if any(name not in fields for name in material_policy.material_fields):
        raise CompatibilityError("MATERIAL_FIELD_MISSING")
    semantic_bytes = _canonical_json(
        {
            "material_field_policy_content_digest": material_policy.policy_ref.content_digest.value,
            "material_field_policy_version": _version_mapping(material_policy.policy_version),
            "ordered_material_fields": [
                {"name": name, "value": fields[name]} for name in material_policy.material_fields
            ],
            "projection_contract_version": _version_mapping(material_policy.projection_version),
            "record_kind": "ATIS_C04_C05_CANONICAL_SEMANTIC_PROJECTION_V1",
        }
    )
    if len(semantic_bytes) > MAX_SEMANTIC_BYTES:
        raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
    semantic_digest = EvidenceContentDigest.from_bytes(semantic_bytes)
    if canonical_bytes_ref.content_digest != semantic_digest:
        raise CompatibilityError("SEMANTIC_DIGEST_MISMATCH")
    if sequence_evidence.state in (
        CompatibilityEvidenceState.INCOMPATIBLE,
        CompatibilityEvidenceState.AMBIGUOUS_CONFLICTING,
    ):
        raise CompatibilityError("SEQUENCE_CALENDAR_EVIDENCE_RESTRICTIVE")
    if len(descriptor_evidence_refs) > MAX_DESCRIPTOR_EVIDENCE_REFS:
        raise CompatibilityError("RESOURCE_BOUND_EXHAUSTED")
    resolved = _resolved_index(resolved_evidence)
    required_refs = canonicalize_evidence_refs(
        (
            material_policy.policy_ref,
            logical_identity.namespace_ref,
            logical_identity_mapping_ref,
            producer_contract_ref,
            semantic_projection_contract_ref,
            adapter_contract_ref,
            canonical_bytes_ref,
            cohort_ref,
            evaluation_scope_ref,
            validation_policy_ref,
        )
        + sequence_evidence.evidence_refs
        + descriptor_evidence_refs
    )
    for ref in required_refs:
        _require_resolved(ref, resolved)
    if _require_resolved(material_policy.policy_ref, resolved) != material_policy.content:
        raise CompatibilityError("MATERIAL_FIELD_POLICY_CONTENT_MISMATCH")
    if _require_resolved(canonical_bytes_ref, resolved) != semantic_bytes:
        raise CompatibilityError("SEMANTIC_BYTES_MISMATCH")
    quality_input = QualityInputDescriptor(
        descriptor_version=consumer_descriptor_version,
        cohort_ref=cohort_ref,
        evaluation_scope_ref=evaluation_scope_ref,
        expected_sequence_ref=sequence_evidence.ref if sequence_evidence.state is CompatibilityEvidenceState.AVAILABLE else None,
        input_ref=observation_ref,
        logical_identity=logical_identity,
        metric_inputs_ref=None,
        order_evidence_ref=None,
        representation_contract_ref=adapter_contract_ref,
        semantic_content=AbstractSemanticContent(semantic_projection_contract_ref, canonical_bytes_ref, semantic_digest),
        validation_policy_id=validation_policy_id,
        validation_policy_ref=validation_policy_ref,
        evaluated_at=evaluated_at,
    )
    return CompatibilityDescriptor(
        descriptor_version=ContractVersion(COMPATIBILITY_DESCRIPTOR_FAMILY, 1),
        canonical_observation_ref=observation_ref,
        logical_identity=logical_identity,
        logical_identity_mapping_ref=logical_identity_mapping_ref,
        semantic_projection_contract_ref=semantic_projection_contract_ref,
        canonical_bytes_ref=canonical_bytes_ref,
        semantic_digest=semantic_digest,
        material_field_policy_ref=material_policy.policy_ref,
        producer_contract_ref=producer_contract_ref,
        adapter_contract_ref=adapter_contract_ref,
        expected_sequence_evidence=sequence_evidence,
        descriptor_evidence_refs=descriptor_evidence_refs,
        quality_input=quality_input,
        semantic_bytes=semantic_bytes,
    )

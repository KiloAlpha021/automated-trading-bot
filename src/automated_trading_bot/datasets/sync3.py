"""Deterministic Stage-3 dataset consumability assessment.

SYNC-3 integrates exact protected C05, C06, and C10 evidence.  It creates an
immutable technical assessment only; it grants no promotion, persistence,
publication, provider, storage, trading, financial, or AI authority.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from functools import cache
import json
import re
import types
from typing import Any, Union, cast, get_args, get_origin, get_type_hints
import unicodedata

from automated_trading_bot.datasets.currentness import (
    CurrentnessState,
    FreshnessPolicyId,
)
from automated_trading_bot.datasets.lifecycle import (
    AffectedSetId,
    DatasetCurrentnessAssessment,
    DatasetCurrentnessAssessmentId,
    InvalidationEventId,
    PropagationRunId,
)
from automated_trading_bot.datasets.materialization import DatasetVersionId
from automated_trading_bot.datasets.provenance import (
    DatasetLifecycleResourcePolicyId,
    DependencySetId,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    ValidationState,
    canonicalize_evidence_refs,
)
from automated_trading_bot.market_data.compatibility import (
    CompatibilityDescriptor,
    SequenceCalendarEvidence,
)
from automated_trading_bot.market_data.eligibility import (
    ClaimId,
    EligibilityDecision,
    EligibilityDecisionId,
    EligibilityDisposition,
    ResolvedC06Evidence,
    c05_quality_result_digest,
    verify_evidence,
)
from automated_trading_bot.market_data.quality import (
    AbstractSemanticContent,
    ApprovedProductionQualityPolicy,
    ProductionMetricEvidence,
    ProductionQualityResult,
    ProductionSequenceEvidence,
    QualityLogicalIdentity,
    QualityInputDescriptor,
    ResolvedQualityEvidence,
    evaluate_production_quality,
)


SYNC3_CONTRACT_VERSION = "ATIS_STAGE3_SYNC3_MINIMUM_INTEGRATION_CONTRACT_V1"
SYNC3_CLAIM_CONTRACT_VERSION = ContractVersion(
    "ATIS_SYNC3_DATASET_ELIGIBILITY_CLAIM", 1
)
SYNC3_CONTENT_DIGEST_DOMAIN = "ATIS:SYNC3:CONSUMABILITY_ASSESSMENT_CONTENT:1"
SYNC3_ASSESSMENT_ID_DOMAIN = "ATIS:SYNC3:CONSUMABILITY_ASSESSMENT_ID:1"

SYNC3_DATASET_CONTEXT_NOT_ESTABLISHED = "SYNC3_DATASET_CONTEXT_NOT_ESTABLISHED"
SYNC3_DATASET_CONTEXT_MISMATCH = "SYNC3_DATASET_CONTEXT_MISMATCH"
SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED = "SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED"
SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH = (
    "SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH"
)
SYNC3_REASONS = (
    SYNC3_DATASET_CONTEXT_NOT_ESTABLISHED,
    SYNC3_DATASET_CONTEXT_MISMATCH,
    SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED,
    SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,254}", re.ASCII)
_CLAIM_FIELDS = frozenset({"contract_version", "claim_id", "dataset_version_id"})
_ASSESSMENT_CONSTRUCTION_TOKEN = object()


class Sync3IntegrityError(ValueError):
    """Protected SYNC-3 integrity or attribution could not be established."""


class Sync3ConsumabilityState(StrEnum):
    CONSUMABLE = "CONSUMABLE"
    NOT_CONSUMABLE = "NOT_CONSUMABLE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


@dataclass(frozen=True, slots=True)
class Sync3ConsumabilityAssessmentId:
    value: str

    def __post_init__(self) -> None:
        _identity(self.value, "assessment_id")


@dataclass(frozen=True, slots=True)
class Sync3DatasetEligibilityClaim:
    contract_version: ContractVersion
    claim_id: ClaimId
    dataset_version_id: DatasetVersionId

    def __post_init__(self) -> None:
        if type(self.contract_version) is not ContractVersion:
            raise TypeError("contract_version must be ContractVersion")
        if self.contract_version != SYNC3_CLAIM_CONTRACT_VERSION:
            raise Sync3IntegrityError("UNSUPPORTED_SYNC3_CLAIM_VERSION")
        if type(self.claim_id) is not ClaimId:
            raise TypeError("claim_id must be ClaimId")
        if type(self.dataset_version_id) is not DatasetVersionId:
            raise TypeError("dataset_version_id must be DatasetVersionId")


@dataclass(frozen=True, slots=True, init=False)
class Sync3ConsumabilityAssessment:
    contract_version: str
    dataset_version_id: DatasetVersionId
    eligibility_decision_id: EligibilityDecisionId
    dataset_currentness_assessment_id: DatasetCurrentnessAssessmentId | None
    state: Sync3ConsumabilityState
    reasons: tuple[str, ...]
    content_digest: EvidenceContentDigest
    assessment_id: Sync3ConsumabilityAssessmentId

    def __init__(
        self,
        contract_version: str,
        dataset_version_id: DatasetVersionId,
        eligibility_decision_id: EligibilityDecisionId,
        dataset_currentness_assessment_id: DatasetCurrentnessAssessmentId | None,
        state: Sync3ConsumabilityState,
        reasons: tuple[str, ...],
        content_digest: EvidenceContentDigest,
        assessment_id: Sync3ConsumabilityAssessmentId,
        *,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _ASSESSMENT_CONSTRUCTION_TOKEN:
            raise Sync3IntegrityError("SYNC3_ASSESSMENT_MUST_BE_DERIVED")
        object.__setattr__(self, "contract_version", contract_version)
        object.__setattr__(self, "dataset_version_id", dataset_version_id)
        object.__setattr__(self, "eligibility_decision_id", eligibility_decision_id)
        object.__setattr__(
            self,
            "dataset_currentness_assessment_id",
            dataset_currentness_assessment_id,
        )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "content_digest", content_digest)
        object.__setattr__(self, "assessment_id", assessment_id)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.contract_version != SYNC3_CONTRACT_VERSION:
            raise Sync3IntegrityError("UNSUPPORTED_SYNC3_CONTRACT_VERSION")
        if type(self.dataset_version_id) is not DatasetVersionId:
            raise TypeError("dataset_version_id must be DatasetVersionId")
        if type(self.eligibility_decision_id) is not EligibilityDecisionId:
            raise TypeError("eligibility_decision_id must be EligibilityDecisionId")
        if self.dataset_currentness_assessment_id is not None and type(
            self.dataset_currentness_assessment_id
        ) is not DatasetCurrentnessAssessmentId:
            raise TypeError(
                "dataset_currentness_assessment_id must be "
                "DatasetCurrentnessAssessmentId or None"
            )
        if type(self.state) is not Sync3ConsumabilityState:
            raise TypeError("state must be Sync3ConsumabilityState")
        canonical_reasons = _canonical_reasons(self.reasons)
        if canonical_reasons != self.reasons:
            raise Sync3IntegrityError("NONCANONICAL_SYNC3_REASONS")
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        if type(self.assessment_id) is not Sync3ConsumabilityAssessmentId:
            raise TypeError("assessment_id must be Sync3ConsumabilityAssessmentId")
        if self.dataset_currentness_assessment_id is None:
            if self.state is not Sync3ConsumabilityState.NOT_ESTABLISHED:
                raise Sync3IntegrityError("NULL_C10_ID_REQUIRES_NOT_ESTABLISHED")
            if SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED not in self.reasons:
                raise Sync3IntegrityError("NULL_C10_ID_REQUIRES_MISSING_C10_REASON")
        expected_digest = _content_digest(
            contract_version=self.contract_version,
            dataset_version_id=self.dataset_version_id,
            eligibility_decision_id=self.eligibility_decision_id,
            dataset_currentness_assessment_id=(
                None
                if self.dataset_currentness_assessment_id is None
                else self.dataset_currentness_assessment_id.value
            ),
            state=self.state,
            reasons=self.reasons,
        )
        if self.content_digest != expected_digest:
            raise Sync3IntegrityError("SYNC3_CONTENT_DIGEST_MISMATCH")
        expected_id = _assessment_id(
            contract_version=self.contract_version,
            dataset_version_id=self.dataset_version_id,
            eligibility_decision_id=self.eligibility_decision_id,
            dataset_currentness_assessment_id=(
                None
                if self.dataset_currentness_assessment_id is None
                else self.dataset_currentness_assessment_id.value
            ),
            state=self.state,
            reasons=self.reasons,
            content_digest=self.content_digest,
        )
        if self.assessment_id != expected_id:
            raise Sync3IntegrityError("SYNC3_ASSESSMENT_ID_MISMATCH")


def _identity(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if value != value.strip() or unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be unpadded NFC text")
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical attributable identity")
    return value


def _canonical_reasons(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple or not values:
        raise Sync3IntegrityError("SYNC3_REASONS_NOT_ESTABLISHED")
    for value in values:
        if type(value) is not str or _REASON.fullmatch(value) is None:
            raise Sync3IntegrityError("MALFORMED_OR_UNSUPPORTED_REASON")
        if unicodedata.normalize("NFC", value) != value:
            raise Sync3IntegrityError("MALFORMED_OR_UNSUPPORTED_REASON")
    return tuple(sorted(set(values)))


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        domain.encode("ascii") + b"\0" + canonical_json(body)
    )


def _projection(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    eligibility_decision_id: EligibilityDecisionId,
    dataset_currentness_assessment_id: str | None,
    state: Sync3ConsumabilityState,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "dataset_version_id": dataset_version_id.value,
        "eligibility_decision_id": eligibility_decision_id.value,
        "dataset_currentness_assessment_id": dataset_currentness_assessment_id,
        "state": state.value,
        "reasons": list(reasons),
    }


def _content_digest(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    eligibility_decision_id: EligibilityDecisionId,
    dataset_currentness_assessment_id: str | None,
    state: Sync3ConsumabilityState,
    reasons: tuple[str, ...],
) -> EvidenceContentDigest:
    return _digest(
        SYNC3_CONTENT_DIGEST_DOMAIN,
        _projection(
            contract_version=contract_version,
            dataset_version_id=dataset_version_id,
            eligibility_decision_id=eligibility_decision_id,
            dataset_currentness_assessment_id=dataset_currentness_assessment_id,
            state=state,
            reasons=reasons,
        ),
    )


def _assessment_id(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    eligibility_decision_id: EligibilityDecisionId,
    dataset_currentness_assessment_id: str | None,
    state: Sync3ConsumabilityState,
    reasons: tuple[str, ...],
    content_digest: EvidenceContentDigest,
) -> Sync3ConsumabilityAssessmentId:
    body = _projection(
        contract_version=contract_version,
        dataset_version_id=dataset_version_id,
        eligibility_decision_id=eligibility_decision_id,
        dataset_currentness_assessment_id=dataset_currentness_assessment_id,
        state=state,
        reasons=reasons,
    )
    body["content_digest"] = content_digest.value
    digest = _digest(SYNC3_ASSESSMENT_ID_DOMAIN, body)
    return Sync3ConsumabilityAssessmentId(
        f"sync3-consumability:{digest.value.removeprefix('sha256:')}"
    )


def _resolved_quality_closure(
    *,
    sequence: ProductionSequenceEvidence,
    metric_evidence: ProductionMetricEvidence,
    policy: ApprovedProductionQualityPolicy | None,
    resolved_evidence: tuple[ResolvedQualityEvidence, ...],
) -> None:
    if type(resolved_evidence) is not tuple:
        raise TypeError("resolved_quality_evidence must be a tuple")
    refs = sequence.evidence_refs + metric_evidence.evidence_refs
    if policy is not None:
        refs += policy.evidence_refs
    try:
        expected = canonicalize_evidence_refs(refs)
    except (TypeError, ValueError, EvidenceIdentityConflict) as error:
        raise Sync3IntegrityError("C05_REPLAY_EVIDENCE_CONFLICT") from error
    expected_by_key = {item.key: item for item in expected}
    actual_by_key: dict[tuple[object, ...], ResolvedQualityEvidence] = {}
    for item in resolved_evidence:
        if type(item) is not ResolvedQualityEvidence:
            raise TypeError(
                "resolved_quality_evidence must contain ResolvedQualityEvidence"
            )
        if item.ref.key in actual_by_key:
            raise Sync3IntegrityError("DUPLICATE_C05_REPLAY_EVIDENCE")
        if EvidenceContentDigest.from_bytes(item.content) != item.ref.content_digest:
            raise Sync3IntegrityError("C05_REPLAY_EVIDENCE_DIGEST_MISMATCH")
        actual_by_key[item.ref.key] = item
    missing = set(expected_by_key) - set(actual_by_key)
    unrelated = set(actual_by_key) - set(expected_by_key)
    if missing:
        raise Sync3IntegrityError("MISSING_C05_REPLAY_EVIDENCE")
    if unrelated:
        raise Sync3IntegrityError("UNRELATED_C05_REPLAY_EVIDENCE")
    if any(
        actual_by_key[key].ref != expected_ref
        for key, expected_ref in expected_by_key.items()
    ):
        raise Sync3IntegrityError("CONFLICTING_C05_REPLAY_EVIDENCE")


def _validate_exact_evidence_refs(
    values: tuple[EvidenceRef, ...],
    name: str,
) -> None:
    if type(values) is not tuple or not values:
        raise Sync3IntegrityError(f"{name}_NOT_ESTABLISHED")
    try:
        canonical = canonicalize_evidence_refs(values)
    except (TypeError, ValueError, EvidenceIdentityConflict) as error:
        raise Sync3IntegrityError(f"{name}_INVALID") from error
    if canonical != values:
        raise Sync3IntegrityError(f"{name}_NONCANONICAL")


def _validate_eligibility_decision(
    value: EligibilityDecision,
    replayed_c05_result: ProductionQualityResult,
) -> None:
    if type(value) is not EligibilityDecision:
        raise TypeError("eligibility_decision must be EligibilityDecision")
    if value.claim_contract_version != SYNC3_CLAIM_CONTRACT_VERSION:
        raise Sync3IntegrityError("UNSUPPORTED_SYNC3_CLAIM_CONTRACT")
    try:
        rebuilt = EligibilityDecision(
            value.contract_version,
            value.claim_id,
            value.claim_contract_version,
            value.claim_contract_ref,
            value.quarantine_subject_id,
            value.c05_quality_result_digest,
            value.eligibility_policy_id,
            value.eligibility_policy_ref,
            value.knowledge_from,
            value.effective_from,
            value.prerequisite_evidence_refs,
            value.external_currentness_ref,
            value.disposition,
            value.reasons,
            value.evidence_refs,
        )
    except (TypeError, ValueError) as error:
        raise Sync3IntegrityError("C06_DECISION_IDENTITY_INVALID") from error
    if rebuilt != value:
        raise Sync3IntegrityError("C06_DECISION_IDENTITY_INVALID")
    expected_evidence_refs = value.prerequisite_evidence_refs + (
        value.eligibility_policy_ref,
        value.claim_contract_ref,
    )
    if value.external_currentness_ref is not None:
        expected_evidence_refs += (value.external_currentness_ref,)
    try:
        expected_evidence_refs = canonicalize_evidence_refs(
            expected_evidence_refs
        )
    except (TypeError, ValueError, EvidenceIdentityConflict) as error:
        raise Sync3IntegrityError("C06_EVIDENCE_ATTRIBUTION_INVALID") from error
    if value.evidence_refs != expected_evidence_refs:
        raise Sync3IntegrityError("C06_EVIDENCE_ATTRIBUTION_INVALID")
    allowed_reasons = {
        EligibilityDisposition.ELIGIBLE: {
            ("EXACT_CLAIM_PREREQUISITES_SATISFIED",)
        },
        EligibilityDisposition.INELIGIBLE: {
            ("C05_PREREQUISITE_RESTRICTIVE",),
            ("SUBJECT_QUARANTINED",),
        },
        EligibilityDisposition.NOT_ESTABLISHED: {
            ("EXTERNAL_CURRENTNESS_NOT_ESTABLISHED",),
            ("QUARANTINE_STATE_NOT_ESTABLISHED",),
        },
        EligibilityDisposition.INCOMPATIBLE: {
            ("C05_RESULT_INCOMPATIBLE",)
        },
    }
    if value.reasons not in allowed_reasons[value.disposition]:
        raise Sync3IntegrityError("C06_REASON_ATTRIBUTION_INVALID")
    c05_state = replayed_c05_result.validation_state
    if c05_state is ValidationState.INCOMPATIBLE:
        if (
            value.disposition is not EligibilityDisposition.INCOMPATIBLE
            or value.reasons != ("C05_RESULT_INCOMPATIBLE",)
        ):
            raise Sync3IntegrityError("C06_C05_REASON_APPLICABILITY_INVALID")
    elif c05_state is not ValidationState.VALID:
        if (
            value.disposition is not EligibilityDisposition.INELIGIBLE
            or value.reasons != ("C05_PREREQUISITE_RESTRICTIVE",)
        ):
            raise Sync3IntegrityError("C06_C05_REASON_APPLICABILITY_INVALID")
    elif (
        value.disposition is EligibilityDisposition.INCOMPATIBLE
        or value.reasons
        in {
            ("C05_PREREQUISITE_RESTRICTIVE",),
            ("C05_RESULT_INCOMPATIBLE",),
        }
    ):
        raise Sync3IntegrityError("C06_C05_REASON_APPLICABILITY_INVALID")
    if (
        value.reasons == ("EXTERNAL_CURRENTNESS_NOT_ESTABLISHED",)
        and value.external_currentness_ref is not None
    ):
        raise Sync3IntegrityError("C06_REASON_ATTRIBUTION_INVALID")


def _resolved_claim_evidence(
    decision: EligibilityDecision,
    resolved_evidence: tuple[ResolvedC06Evidence, ...],
) -> ResolvedC06Evidence:
    if type(resolved_evidence) is not tuple:
        raise TypeError("resolved_c06_evidence must be a tuple")
    actual: dict[tuple[object, ...], ResolvedC06Evidence] = {}
    for item in resolved_evidence:
        if type(item) is not ResolvedC06Evidence:
            raise TypeError(
                "resolved_c06_evidence must contain ResolvedC06Evidence"
            )
        if item.ref.key in actual:
            raise Sync3IntegrityError("DUPLICATE_C06_RESOLVED_EVIDENCE")
        actual[item.ref.key] = item
    claim_key = decision.claim_contract_ref.key
    if claim_key not in actual:
        raise Sync3IntegrityError("MISSING_SYNC3_CLAIM_EVIDENCE")
    if set(actual) != {claim_key}:
        raise Sync3IntegrityError("UNRELATED_SYNC3_CLAIM_EVIDENCE")
    resolved = actual[claim_key]
    if resolved.ref != decision.claim_contract_ref:
        raise Sync3IntegrityError("CONFLICTING_SYNC3_CLAIM_EVIDENCE")
    try:
        verify_evidence((decision.claim_contract_ref,), (resolved,))
    except (TypeError, ValueError) as error:
        raise Sync3IntegrityError("SYNC3_CLAIM_EVIDENCE_INVALID") from error
    return resolved


def _pairs_without_duplicates(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Sync3IntegrityError("DUPLICATE_SYNC3_CLAIM_FIELD")
        result[key] = value
    return result


def _decode_claim(
    decision: EligibilityDecision,
    resolved: ResolvedC06Evidence,
) -> Sync3DatasetEligibilityClaim:
    if resolved.ref != decision.claim_contract_ref:
        raise Sync3IntegrityError("SYNC3_CLAIM_EVIDENCE_SUBSTITUTION")
    try:
        text = resolved.content.decode("utf-8", errors="strict")
        parsed = cast(
            object,
            json.loads(text, object_pairs_hook=_pairs_without_duplicates),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM") from error
    if type(parsed) is not dict:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM")
    payload = cast(dict[object, object], parsed)
    if set(payload) != _CLAIM_FIELDS:
        raise Sync3IntegrityError("INVALID_SYNC3_CLAIM_FIELDS")
    if any(type(key) is not str for key in payload):
        raise Sync3IntegrityError("INVALID_SYNC3_CLAIM_FIELDS")
    try:
        canonical_claim = canonical_json(payload)
    except (TypeError, ValueError) as error:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM") from error
    if canonical_claim != resolved.content:
        raise Sync3IntegrityError("NONCANONICAL_SYNC3_CLAIM")
    version = payload["contract_version"]
    claim_id = payload["claim_id"]
    dataset_version_id = payload["dataset_version_id"]
    if type(version) is not dict or type(claim_id) is not str or type(
        dataset_version_id
    ) is not str:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM")
    version_mapping = cast(dict[object, object], version)
    if set(version_mapping) != {"family", "version"}:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM")
    family = version_mapping["family"]
    number = version_mapping["version"]
    if type(family) is not str or type(number) is not int:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM")
    try:
        return Sync3DatasetEligibilityClaim(
            ContractVersion(family, number),
            ClaimId(claim_id),
            DatasetVersionId(dataset_version_id),
        )
    except (TypeError, ValueError) as error:
        raise Sync3IntegrityError("MALFORMED_SYNC3_CLAIM") from error


def _validate_currentness(value: DatasetCurrentnessAssessment) -> None:
    if type(value) is not DatasetCurrentnessAssessment:
        raise TypeError(
            "dataset_currentness_assessment must be "
            "DatasetCurrentnessAssessment or None"
        )
    required_types = (
        (value.assessment_id, DatasetCurrentnessAssessmentId),
        (value.contract_version, str),
        (value.dataset_version_id, DatasetVersionId),
        (value.freshness_policy_id, FreshnessPolicyId),
        (value.freshness_policy_ref, EvidenceRef),
        (value.evaluated_at, Timestamp),
        (value.dependency_set_id, DependencySetId),
        (value.dependency_state_digest, EvidenceContentDigest),
        (value.state, CurrentnessState),
        (value.reasons, tuple),
        (value.resource_policy_id, DatasetLifecycleResourcePolicyId),
        (value.lifecycle_reasons, tuple),
        (value.content_digest, EvidenceContentDigest),
    )
    if any(type(item) is not kind for item, kind in required_types):
        raise Sync3IntegrityError("C10_ASSESSMENT_TYPE_INVALID")
    optional_types = (
        (value.invalidation_event_id, InvalidationEventId),
        (value.affected_set_id, AffectedSetId),
        (value.propagation_run_id, PropagationRunId),
    )
    if any(item is not None and type(item) is not kind for item, kind in optional_types):
        raise Sync3IntegrityError("C10_ASSESSMENT_TYPE_INVALID")
    _validate_exact_evidence_refs(
        value.dependency_evidence_refs,
        "C10_DEPENDENCY_EVIDENCE_REFS",
    )
    _validate_exact_evidence_refs(value.evidence_refs, "C10_EVIDENCE_REFS")
    try:
        rebuilt = DatasetCurrentnessAssessment(
            value.assessment_id,
            value.contract_version,
            value.dataset_version_id,
            value.freshness_policy_id,
            value.freshness_policy_ref,
            value.evaluated_at,
            value.dependency_set_id,
            value.dependency_evidence_refs,
            value.dependency_state_digest,
            value.state,
            value.reasons,
            value.evidence_refs,
            value.resource_policy_id,
            value.invalidation_event_id,
            value.affected_set_id,
            value.propagation_run_id,
            value.lifecycle_reasons,
            value.content_digest,
        )
    except (TypeError, ValueError) as error:
        raise Sync3IntegrityError("C10_ASSESSMENT_IDENTITY_INVALID") from error
    if rebuilt != value:
        raise Sync3IntegrityError("C10_ASSESSMENT_IDENTITY_INVALID")
    expected: set[tuple[tuple[str, ...], tuple[str, ...]]]
    if value.state is CurrentnessState.CURRENT:
        expected = {
            (("EVALUATION_BEFORE_FRESHNESS_BOUNDARY",), ()),
            (
                (
                    "EVALUATION_BEFORE_FRESHNESS_BOUNDARY",
                    "PROPAGATION_COMPLETE",
                ),
                ("PROPAGATION_COMPLETE",),
            ),
        }
    elif value.state is CurrentnessState.STALE:
        expected = {
            (("EVALUATION_AT_OR_AFTER_FRESHNESS_BOUNDARY",), ())
        }
    elif value.state is CurrentnessState.UNKNOWN:
        expected = {
            (("APPLICABLE_FRESHNESS_HORIZON_NOT_ESTABLISHED",), ()),
            (("CURRENTNESS_EVIDENCE_UNAVAILABLE",), ()),
            (
                (
                    "EVALUATION_BEFORE_FRESHNESS_BOUNDARY",
                    "PROPAGATION_PENDING",
                ),
                ("PROPAGATION_PENDING",),
            ),
        }
    else:
        raise Sync3IntegrityError("UNSUPPORTED_C10_CURRENTNESS_STATE")
    if (value.reasons, value.lifecycle_reasons) not in expected:
        raise Sync3IntegrityError("C10_REASON_ATTRIBUTION_INVALID")
    has_invalidation = value.invalidation_event_id is not None
    has_affected_set = value.affected_set_id is not None
    has_propagation = value.propagation_run_id is not None
    if has_invalidation != has_affected_set:
        raise Sync3IntegrityError("C10_LIFECYCLE_IDENTITY_INVALID")
    if has_propagation and not has_invalidation:
        raise Sync3IntegrityError("C10_LIFECYCLE_IDENTITY_INVALID")
    if value.lifecycle_reasons == ("PROPAGATION_PENDING",) and not (
        has_invalidation and has_affected_set
    ):
        raise Sync3IntegrityError("C10_LIFECYCLE_IDENTITY_INVALID")
    if value.lifecycle_reasons == ("PROPAGATION_COMPLETE",) and not (
        has_invalidation and has_affected_set and has_propagation
    ):
        raise Sync3IntegrityError("C10_LIFECYCLE_IDENTITY_INVALID")


def _semantic_class_c05(state: ValidationState) -> str:
    if state is ValidationState.VALID:
        return "P"
    if state in (ValidationState.INVALID, ValidationState.INCOMPATIBLE):
        return "N"
    if state in (ValidationState.NOT_VALIDATED, ValidationState.NOT_ESTABLISHED):
        return "U"
    raise Sync3IntegrityError("UNSUPPORTED_C05_VALIDATION_STATE")


def _semantic_class_c06(state: EligibilityDisposition) -> str:
    if state is EligibilityDisposition.ELIGIBLE:
        return "P"
    if state in (
        EligibilityDisposition.INELIGIBLE,
        EligibilityDisposition.INCOMPATIBLE,
    ):
        return "N"
    if state is EligibilityDisposition.NOT_ESTABLISHED:
        return "U"
    raise Sync3IntegrityError("UNSUPPORTED_C06_ELIGIBILITY_STATE")


def _semantic_class_c10(state: CurrentnessState) -> str:
    if state is CurrentnessState.CURRENT:
        return "P"
    if state is CurrentnessState.STALE:
        return "N"
    if state is CurrentnessState.UNKNOWN:
        return "U"
    raise Sync3IntegrityError("UNSUPPORTED_C10_CURRENTNESS_STATE")


def _state(classes: tuple[str, ...]) -> Sync3ConsumabilityState:
    if "N" in classes:
        return Sync3ConsumabilityState.NOT_CONSUMABLE
    if "U" in classes:
        return Sync3ConsumabilityState.NOT_ESTABLISHED
    if classes and all(item == "P" for item in classes):
        return Sync3ConsumabilityState.CONSUMABLE
    raise Sync3IntegrityError("SYNC3_SEMANTIC_PRODUCT_NOT_ESTABLISHED")


def _assessment(
    *,
    dataset_version_id: DatasetVersionId,
    eligibility_decision_id: EligibilityDecisionId,
    currentness: DatasetCurrentnessAssessment | None,
    state: Sync3ConsumabilityState,
    reasons: tuple[str, ...],
) -> Sync3ConsumabilityAssessment:
    canonical_reasons = _canonical_reasons(reasons)
    c10_id = None if currentness is None else currentness.assessment_id
    c10_value = None if c10_id is None else c10_id.value
    content_digest = _content_digest(
        contract_version=SYNC3_CONTRACT_VERSION,
        dataset_version_id=dataset_version_id,
        eligibility_decision_id=eligibility_decision_id,
        dataset_currentness_assessment_id=c10_value,
        state=state,
        reasons=canonical_reasons,
    )
    assessment_id = _assessment_id(
        contract_version=SYNC3_CONTRACT_VERSION,
        dataset_version_id=dataset_version_id,
        eligibility_decision_id=eligibility_decision_id,
        dataset_currentness_assessment_id=c10_value,
        state=state,
        reasons=canonical_reasons,
        content_digest=content_digest,
    )
    return Sync3ConsumabilityAssessment(
        SYNC3_CONTRACT_VERSION,
        dataset_version_id,
        eligibility_decision_id,
        c10_id,
        state,
        canonical_reasons,
        content_digest,
        assessment_id,
        _construction_token=_ASSESSMENT_CONSTRUCTION_TOKEN,
    )


@cache
def _dataclass_hints(value_type: type[object]) -> dict[str, Any]:
    return get_type_hints(value_type)


def _matches_exact_annotation(value: object, annotation: Any) -> bool:
    if annotation is Any or annotation is object:
        return True
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (Union, types.UnionType):
        return any(_matches_exact_annotation(value, item) for item in arguments)
    if origin is tuple:
        if type(value) is not tuple:
            return False
        if not arguments:
            return True
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return all(
                _matches_exact_annotation(item, arguments[0]) for item in value
            )
        return len(value) == len(arguments) and all(
            _matches_exact_annotation(item, expected)
            for item, expected in zip(value, arguments, strict=True)
        )
    if origin is list:
        return type(value) is list and (
            not arguments
            or all(_matches_exact_annotation(item, arguments[0]) for item in value)
        )
    if origin is dict:
        return type(value) is dict and (
            not arguments
            or all(
                _matches_exact_annotation(key, arguments[0])
                and _matches_exact_annotation(item, arguments[1])
                for key, item in value.items()
            )
        )
    if isinstance(annotation, type):
        return type(value) is annotation
    return False


def _validate_dataclass_graph(value: object) -> None:
    if type(value) is tuple:
        for item in value:
            _validate_dataclass_graph(item)
        return
    if not is_dataclass(value) or isinstance(value, type):
        return
    hints = _dataclass_hints(type(value))
    for item in fields(value):
        field_value = getattr(value, item.name)
        annotation = hints.get(item.name)
        if annotation is None or not _matches_exact_annotation(
            field_value,
            annotation,
        ):
            raise TypeError("protected dataclass field type mismatch")
        _validate_dataclass_graph(field_value)
    reconstructed = replace(cast(Any, value))
    if any(
        type(getattr(reconstructed, item.name))
        is not type(getattr(value, item.name))
        for item in fields(value)
    ):
        raise TypeError("protected dataclass field type mismatch")
    if reconstructed != value:
        raise ValueError("protected dataclass reconstruction mismatch")


def _validate_protected_graph(*values: object) -> None:
    try:
        _validate_dataclass_graph(values)
    except Exception as error:
        raise Sync3IntegrityError("SYNC3_PROTECTED_INPUT_GRAPH_INVALID") from error


def _validate_compatibility_descriptor(value: CompatibilityDescriptor) -> None:
    """Reconstruct exact protected C04/C05 identity before C05 replay."""

    quality_input = value.quality_input
    sequence_evidence = value.expected_sequence_evidence
    exact_refs = (
        value.canonical_observation_ref,
        value.logical_identity_mapping_ref,
        value.semantic_projection_contract_ref,
        value.canonical_bytes_ref,
        value.material_field_policy_ref,
        value.producer_contract_ref,
        value.adapter_contract_ref,
    )
    if (
        type(value.descriptor_version) is not ContractVersion
        or any(type(item) is not EvidenceRef for item in exact_refs)
        or type(value.semantic_digest) is not EvidenceContentDigest
        or type(value.descriptor_evidence_refs) is not tuple
        or any(type(item) is not EvidenceRef for item in value.descriptor_evidence_refs)
        or type(value.semantic_bytes) is not bytes
        or type(value.content_digest) is not EvidenceContentDigest
        or type(sequence_evidence) is not SequenceCalendarEvidence
        or type(quality_input) is not QualityInputDescriptor
        or type(value.logical_identity) is not QualityLogicalIdentity
    ):
        raise Sync3IntegrityError("C05_REPLAY_DESCRIPTOR_BINDING_INVALID")
    semantic_content = quality_input.semantic_content
    if type(semantic_content) is not AbstractSemanticContent:
        raise Sync3IntegrityError("C05_REPLAY_DESCRIPTOR_BINDING_INVALID")
    try:
        _validate_dataclass_graph(value)
    except Exception as error:
        raise Sync3IntegrityError("C05_REPLAY_DESCRIPTOR_INVALID") from error
    if (
        quality_input.logical_identity != value.logical_identity
        or quality_input.input_ref != value.canonical_observation_ref
        or quality_input.expected_sequence_ref != sequence_evidence.ref
        or quality_input.representation_contract_ref != value.adapter_contract_ref
        or quality_input.metric_inputs_ref is not None
        or quality_input.order_evidence_ref is not None
        or quality_input.malformed_evidence_ref is not None
        or semantic_content.contract_ref != value.semantic_projection_contract_ref
        or semantic_content.canonical_bytes_ref != value.canonical_bytes_ref
        or semantic_content.digest != value.semantic_digest
    ):
        raise Sync3IntegrityError("C05_REPLAY_DESCRIPTOR_BINDING_INVALID")


def assess_dataset_consumability(
    *,
    dataset_version_id: DatasetVersionId,
    descriptors: tuple[CompatibilityDescriptor, ...],
    sequence: ProductionSequenceEvidence,
    metric_evidence: ProductionMetricEvidence,
    policy: ApprovedProductionQualityPolicy | None,
    resolved_quality_evidence: tuple[ResolvedQualityEvidence, ...],
    supplied_c05_result: ProductionQualityResult,
    eligibility_decision: EligibilityDecision,
    resolved_c06_evidence: tuple[ResolvedC06Evidence, ...],
    dataset_currentness_assessment: DatasetCurrentnessAssessment | None,
) -> Sync3ConsumabilityAssessment:
    """Assess technical consumability from exact protected component evidence."""

    if type(dataset_version_id) is not DatasetVersionId:
        raise TypeError("dataset_version_id must be DatasetVersionId")
    if type(descriptors) is not tuple or not descriptors:
        raise TypeError("descriptors must be a nonempty tuple")
    if any(type(item) is not CompatibilityDescriptor for item in descriptors):
        raise TypeError("descriptors must contain CompatibilityDescriptor values")
    if type(sequence) is not ProductionSequenceEvidence:
        raise TypeError("sequence must be ProductionSequenceEvidence")
    if type(metric_evidence) is not ProductionMetricEvidence:
        raise TypeError("metric_evidence must be ProductionMetricEvidence")
    if policy is not None and type(policy) is not ApprovedProductionQualityPolicy:
        raise TypeError("policy must be ApprovedProductionQualityPolicy or None")
    if type(supplied_c05_result) is not ProductionQualityResult:
        raise TypeError("supplied_c05_result must be ProductionQualityResult")
    if type(eligibility_decision) is not EligibilityDecision:
        raise TypeError("eligibility_decision must be EligibilityDecision")
    if type(resolved_quality_evidence) is not tuple or any(
        type(item) is not ResolvedQualityEvidence
        for item in resolved_quality_evidence
    ):
        raise TypeError(
            "resolved_quality_evidence must contain ResolvedQualityEvidence values"
        )
    if type(resolved_c06_evidence) is not tuple or any(
        type(item) is not ResolvedC06Evidence for item in resolved_c06_evidence
    ):
        raise TypeError("resolved_c06_evidence must contain ResolvedC06Evidence values")
    if dataset_currentness_assessment is not None and type(
        dataset_currentness_assessment
    ) is not DatasetCurrentnessAssessment:
        raise TypeError(
            "dataset_currentness_assessment must be "
            "DatasetCurrentnessAssessment or None"
        )

    for descriptor in descriptors:
        _validate_compatibility_descriptor(descriptor)
    _resolved_quality_closure(
        sequence=sequence,
        metric_evidence=metric_evidence,
        policy=policy,
        resolved_evidence=resolved_quality_evidence,
    )
    _validate_protected_graph(
        dataset_version_id,
        sequence,
        metric_evidence,
        resolved_quality_evidence,
        supplied_c05_result,
        *((policy,) if policy is not None else ()),
    )
    try:
        replayed_c05_result = evaluate_production_quality(
            descriptors,
            sequence,
            metric_evidence,
            policy,
            resolved_quality_evidence,
        )
    except Exception as error:
        raise Sync3IntegrityError("C05_AUTHORITATIVE_REPLAY_FAILED") from error
    if replayed_c05_result != supplied_c05_result:
        raise Sync3IntegrityError("C05_REPLAY_RESULT_MISMATCH")
    replayed_c05_digest = c05_quality_result_digest(replayed_c05_result)
    if replayed_c05_digest != eligibility_decision.c05_quality_result_digest:
        raise Sync3IntegrityError("C05_C06_DIGEST_BINDING_MISMATCH")
    _validate_eligibility_decision(eligibility_decision, replayed_c05_result)
    _validate_protected_graph(eligibility_decision)

    claim_evidence = _resolved_claim_evidence(
        eligibility_decision,
        resolved_c06_evidence,
    )
    _validate_protected_graph(resolved_c06_evidence)
    claim = _decode_claim(eligibility_decision, claim_evidence)
    if claim.claim_id != eligibility_decision.claim_id:
        raise Sync3IntegrityError("SYNC3_CLAIM_ID_MISMATCH")
    if claim.dataset_version_id != dataset_version_id:
        raise Sync3IntegrityError("SYNC3_DATASET_CONTEXT_MISMATCH")

    currentness = dataset_currentness_assessment
    if currentness is None:
        if eligibility_decision.external_currentness_ref is not None:
            raise Sync3IntegrityError("SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH")
        return _assessment(
            dataset_version_id=dataset_version_id,
            eligibility_decision_id=eligibility_decision.decision_id,
            currentness=None,
            state=Sync3ConsumabilityState.NOT_ESTABLISHED,
            reasons=(SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED,),
        )

    _validate_currentness(currentness)
    _validate_protected_graph(currentness)
    if currentness.dataset_version_id != dataset_version_id:
        raise Sync3IntegrityError("SYNC3_DATASET_CONTEXT_MISMATCH")
    if eligibility_decision.knowledge_from.value > currentness.evaluated_at.value:
        raise Sync3IntegrityError("C06_KNOWLEDGE_AFTER_C10_EVALUATION")
    if (
        eligibility_decision.effective_from is not None
        and eligibility_decision.effective_from.value > currentness.evaluated_at.value
    ):
        raise Sync3IntegrityError("C06_EFFECTIVE_TIME_AFTER_C10_EVALUATION")
    external_ref = eligibility_decision.external_currentness_ref
    if external_ref is not None and external_ref.content_digest != currentness.content_digest:
        raise Sync3IntegrityError("SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH")

    classes = (
        _semantic_class_c05(replayed_c05_result.validation_state),
        _semantic_class_c06(eligibility_decision.disposition),
        _semantic_class_c10(currentness.state),
    )
    state = _state(classes)
    reasons = (
        replayed_c05_result.reasons
        + eligibility_decision.reasons
        + currentness.reasons
    )
    return _assessment(
        dataset_version_id=dataset_version_id,
        eligibility_decision_id=eligibility_decision.decision_id,
        currentness=currentness,
        state=state,
        reasons=reasons,
    )


__all__ = [
    "SYNC3_ASSESSMENT_ID_DOMAIN",
    "SYNC3_C10_ASSESSMENT_NOT_ESTABLISHED",
    "SYNC3_CLAIM_CONTRACT_VERSION",
    "SYNC3_CONTRACT_VERSION",
    "SYNC3_CONTENT_DIGEST_DOMAIN",
    "SYNC3_DATASET_CONTEXT_MISMATCH",
    "SYNC3_DATASET_CONTEXT_NOT_ESTABLISHED",
    "SYNC3_EXTERNAL_CURRENTNESS_REF_MISMATCH",
    "SYNC3_REASONS",
    "Sync3ConsumabilityAssessment",
    "Sync3ConsumabilityAssessmentId",
    "Sync3ConsumabilityState",
    "Sync3DatasetEligibilityClaim",
    "Sync3IntegrityError",
    "assess_dataset_consumability",
]

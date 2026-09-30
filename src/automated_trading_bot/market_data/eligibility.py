"""C06 quarantine, claim-specific eligibility, and attributable release.

All positive decisions are immutable, content-derived, explicitly attributable,
and bounded.  This module consumes C05 results and external authority; it does
not create quality, currentness, release authority, or promotion authority.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Callable

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceRef,
    ValidationState,
    canonicalize_evidence_refs,
)
from automated_trading_bot.market_data.quality import ProductionQualityResult

MAX_C06_EVIDENCE_REFS = 64
MAX_C06_DECISIONS_PER_BATCH = 4096
MAX_C06_LINEAGE_VERSIONS = 4096
MAX_C06_LINEAGE_DEPTH = 256
MAX_C06_REASON_LENGTH = 255

QUARANTINE_SUBJECT_VERSION = ContractVersion("ATIS_C06_QUARANTINE_SUBJECT", 1)
QUARANTINE_DECISION_VERSION = ContractVersion("ATIS_C06_QUARANTINE_DECISION", 1)
ELIGIBILITY_DECISION_VERSION = ContractVersion("ATIS_C06_ELIGIBILITY_DECISION", 1)
RELEASE_ASSERTION_VERSION = ContractVersion("ATIS_C06_RELEASE_AUTHORITY_ASSERTION", 1)
RELEASE_POLICY_VERSION = ContractVersion(
    "ATIS_C06_RELEASE_AUTHORITY_ADMISSION_POLICY", 1
)
RELEASE_DECISION_VERSION = ContractVersion("ATIS_C06_RELEASE_DECISION", 1)


class C06Error(ValueError):
    """A C06 result could not be established safely."""


class ResourceLimitError(C06Error):
    pass


class AuthorityError(C06Error):
    pass


class LineageError(C06Error):
    pass


class BindingError(C06Error):
    pass


class QuarantineDisposition(StrEnum):
    QUARANTINED = "QUARANTINED"
    NOT_QUARANTINED = "NOT_QUARANTINED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    INCOMPATIBLE = "INCOMPATIBLE"


class EligibilityDisposition(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    INCOMPATIBLE = "INCOMPATIBLE"


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _ID.fullmatch(self.value) is None:
            raise ValueError("identity must be a canonical attributable string")
        if (
            self.value != self.value.strip()
            or unicodedata.normalize("NFC", self.value) != self.value
        ):
            raise ValueError("identity must be unpadded NFC text")


@dataclass(frozen=True, slots=True)
class QuarantineSubjectId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class QuarantineDecisionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ClaimId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class EligibilityDecisionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseDecisionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class EligibilityPolicyId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityPolicyId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedC06Evidence:
    ref: EvidenceRef
    content: bytes

    def __post_init__(self) -> None:
        if type(self.ref) is not EvidenceRef or type(self.content) is not bytes:
            raise TypeError("resolved evidence requires an EvidenceRef and bytes")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _version(value: ContractVersion) -> dict[str, object]:
    return {"family": value.family, "version": value.version}


def _timestamp(value: Timestamp | None) -> str | None:
    return None if value is None else value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ref(value: EvidenceRef | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "source_id": value.source_id.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "content_digest": value.content_digest.value,
    }


def _refs(
    values: tuple[EvidenceRef, ...], *, optional: bool = False
) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple:
        raise TypeError("evidence refs must be a tuple")
    if not values:
        if optional:
            return ()
        raise BindingError("EVIDENCE_NOT_ESTABLISHED")
    if len(values) > MAX_C06_EVIDENCE_REFS:
        raise ResourceLimitError("C06_EVIDENCE_LIMIT_EXHAUSTED")
    try:
        return canonicalize_evidence_refs(values)
    except (ValueError, EvidenceIdentityConflict) as error:
        raise BindingError(str(error)) from error


def _reason(value: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("reason must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("reason must be NFC-normalized")
    if len(value) > MAX_C06_REASON_LENGTH:
        raise ResourceLimitError("C06_REASON_LIMIT_EXHAUSTED")
    return value


def _reasons(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple or not values:
        raise ValueError("reasons must be a nonempty tuple")
    checked = tuple(_reason(value) for value in values)
    if len(set(checked)) != len(checked):
        raise ValueError("reasons must be distinct")
    return tuple(sorted(checked))


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        domain.encode("ascii") + b"\0" + _canonical_json(body)
    )


def c05_quality_result_digest(value: ProductionQualityResult) -> EvidenceContentDigest:
    """Bind the complete protected C05 result without re-evaluating it."""
    if type(value) is not ProductionQualityResult:
        raise TypeError("value must be a ProductionQualityResult")
    return _digest(
        "ATIS:C05:PRODUCTION_QUALITY_RESULT:1",
        {
            "descriptor_digests": [item.value for item in value.descriptor_digests],
            "sequence_coverage": value.sequence_coverage.value,
            "sequence_order": value.sequence_order.value,
            "outlier_states": [
                [digest.value, state.value] for digest, state in value.outlier_states
            ],
            "missing_logical_keys": list(value.missing_logical_keys),
            "reasons": list(value.reasons),
            "evidence_refs": [_ref(item) for item in value.evidence_refs],
            "validation_state": value.validation_state.value,
        },
    )


def _derived_id(prefix: str, digest: EvidenceContentDigest) -> str:
    return f"{prefix}:{digest.value.removeprefix('sha256:')}"


def _resolved(
    values: tuple[ResolvedC06Evidence, ...],
) -> dict[tuple[object, ...], bytes]:
    if type(values) is not tuple:
        raise TypeError("resolved_evidence must be a tuple")
    result: dict[tuple[object, ...], bytes] = {}
    for item in values:
        if type(item) is not ResolvedC06Evidence:
            raise TypeError("resolved_evidence contains an invalid value")
        if item.ref.key in result:
            raise BindingError("DUPLICATE_RESOLVED_EVIDENCE")
        if EvidenceContentDigest.from_bytes(item.content) != item.ref.content_digest:
            raise BindingError("EVIDENCE_CONTENT_DIGEST_MISMATCH")
        result[item.ref.key] = item.content
    return result


def verify_evidence(
    refs: tuple[EvidenceRef, ...], resolved_evidence: tuple[ResolvedC06Evidence, ...]
) -> None:
    index = _resolved(resolved_evidence)
    for ref in _refs(refs):
        if ref.key not in index:
            raise BindingError("MISSING_RESOLVED_EVIDENCE")


@dataclass(frozen=True, slots=True)
class QuarantineSubject:
    contract_version: ContractVersion
    subject_ref: EvidenceRef
    c05_quality_result_digest: EvidenceContentDigest
    evidence_refs: tuple[EvidenceRef, ...]
    subject_id: QuarantineSubjectId = field(init=False)
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != QUARANTINE_SUBJECT_VERSION:
            raise BindingError("UNSUPPORTED_QUARANTINE_SUBJECT_VERSION")
        if (
            type(self.subject_ref) is not EvidenceRef
            or type(self.c05_quality_result_digest) is not EvidenceContentDigest
        ):
            raise TypeError("invalid quarantine subject binding")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        digest = _digest("ATIS:C06:QUARANTINE_SUBJECT:1", self._body())
        object.__setattr__(self, "content_digest", digest)
        object.__setattr__(
            self, "subject_id", QuarantineSubjectId(_derived_id("c06-subject", digest))
        )

    def _body(self) -> dict[str, object]:
        return {
            "contract_version": _version(self.contract_version),
            "subject_ref": _ref(self.subject_ref),
            "c05_quality_result_digest": self.c05_quality_result_digest.value,
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
        }


@dataclass(frozen=True, slots=True)
class QuarantinePolicy:
    policy_id: str
    policy_ref: EvidenceRef
    supported_version: ContractVersion = QUARANTINE_DECISION_VERSION
    establishes_effective_time: bool = False

    def __post_init__(self) -> None:
        _OpaqueId(self.policy_id)
        if (
            type(self.policy_ref) is not EvidenceRef
            or type(self.supported_version) is not ContractVersion
        ):
            raise TypeError("invalid quarantine policy")
        if type(self.establishes_effective_time) is not bool:
            raise TypeError("establishes_effective_time must be a bool")


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    contract_version: ContractVersion
    quarantine_subject_id: QuarantineSubjectId
    c05_quality_result_digest: EvidenceContentDigest
    quarantine_policy_id: str
    quarantine_policy_ref: EvidenceRef
    knowledge_from: Timestamp
    effective_from: Timestamp | None
    disposition: QuarantineDisposition
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    decision_id: QuarantineDecisionId = field(init=False)
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != QUARANTINE_DECISION_VERSION:
            raise BindingError("UNSUPPORTED_QUARANTINE_DECISION_VERSION")
        if (
            type(self.quarantine_subject_id) is not QuarantineSubjectId
            or type(self.c05_quality_result_digest) is not EvidenceContentDigest
        ):
            raise TypeError("invalid quarantine decision subject")
        _OpaqueId(self.quarantine_policy_id)
        if (
            type(self.quarantine_policy_ref) is not EvidenceRef
            or type(self.knowledge_from) is not Timestamp
        ):
            raise TypeError("invalid quarantine policy or knowledge boundary")
        if (
            self.effective_from is not None
            and type(self.effective_from) is not Timestamp
        ):
            raise TypeError("effective_from must be a Timestamp or None")
        if type(self.disposition) is not QuarantineDisposition:
            raise TypeError("disposition must be a QuarantineDisposition")
        object.__setattr__(self, "reasons", _reasons(self.reasons))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        digest = _digest("ATIS:C06:QUARANTINE_DECISION:1", self._body())
        object.__setattr__(self, "content_digest", digest)
        object.__setattr__(
            self,
            "decision_id",
            QuarantineDecisionId(_derived_id("c06-quarantine", digest)),
        )

    def _body(self) -> dict[str, object]:
        return {
            "contract_version": _version(self.contract_version),
            "quarantine_subject_id": self.quarantine_subject_id.value,
            "c05_quality_result_digest": self.c05_quality_result_digest.value,
            "quarantine_policy_id": self.quarantine_policy_id,
            "quarantine_policy_ref": _ref(self.quarantine_policy_ref),
            "knowledge_from": _timestamp(self.knowledge_from),
            "effective_from": _timestamp(self.effective_from),
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
        }


def decide_quarantine(
    subject: QuarantineSubject,
    c05_result: ProductionQualityResult,
    policy: QuarantinePolicy,
    *,
    knowledge_from: Timestamp,
    evidence_refs: tuple[EvidenceRef, ...],
    resolved_evidence: tuple[ResolvedC06Evidence, ...],
    effective_from: Timestamp | None = None,
) -> QuarantineDecision:
    if (
        type(subject) is not QuarantineSubject
        or type(c05_result) is not ProductionQualityResult
        or type(policy) is not QuarantinePolicy
    ):
        raise TypeError("invalid quarantine input")
    if c05_quality_result_digest(c05_result) != subject.c05_quality_result_digest:
        raise BindingError("C05_QUALITY_RESULT_BINDING_MISMATCH")
    if effective_from is not None and not policy.establishes_effective_time:
        raise BindingError("EFFECTIVE_TIME_SEMANTICS_NOT_ESTABLISHED")
    refs = _refs(evidence_refs + (policy.policy_ref, subject.subject_ref))
    verify_evidence(refs, resolved_evidence)
    mapping = {
        ValidationState.VALID: (
            QuarantineDisposition.NOT_QUARANTINED,
            "C05_VALID_EXACTLY_BOUND",
        ),
        ValidationState.INVALID: (
            QuarantineDisposition.QUARANTINED,
            "C05_RESTRICTIVE_RESULT",
        ),
        ValidationState.INCOMPATIBLE: (
            QuarantineDisposition.INCOMPATIBLE,
            "C05_RESULT_INCOMPATIBLE",
        ),
        ValidationState.NOT_ESTABLISHED: (
            QuarantineDisposition.NOT_ESTABLISHED,
            "C05_RESULT_NOT_ESTABLISHED",
        ),
        ValidationState.NOT_VALIDATED: (
            QuarantineDisposition.NOT_ESTABLISHED,
            "C05_RESULT_NOT_VALIDATED",
        ),
    }
    disposition, reason = mapping[c05_result.validation_state]
    return QuarantineDecision(
        policy.supported_version,
        subject.subject_id,
        subject.c05_quality_result_digest,
        policy.policy_id,
        policy.policy_ref,
        knowledge_from,
        effective_from,
        disposition,
        (reason,),
        refs,
    )


@dataclass(frozen=True, slots=True)
class EligibilityPolicy:
    policy_id: EligibilityPolicyId
    policy_ref: EvidenceRef
    claim_id: ClaimId
    claim_contract_version: ContractVersion
    claim_contract_ref: EvidenceRef
    requires_external_currentness: bool
    supported_version: ContractVersion = ELIGIBILITY_DECISION_VERSION
    establishes_effective_time: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.policy_id) is not EligibilityPolicyId
            or type(self.claim_id) is not ClaimId
        ):
            raise TypeError("invalid eligibility policy identity")
        if (
            type(self.policy_ref) is not EvidenceRef
            or type(self.claim_contract_ref) is not EvidenceRef
        ):
            raise TypeError("invalid eligibility policy reference")
        if (
            type(self.claim_contract_version) is not ContractVersion
            or type(self.requires_external_currentness) is not bool
        ):
            raise TypeError("invalid eligibility policy contract")
        if self.supported_version != ELIGIBILITY_DECISION_VERSION:
            raise BindingError("UNSUPPORTED_ELIGIBILITY_DECISION_VERSION")
        if type(self.establishes_effective_time) is not bool:
            raise TypeError("establishes_effective_time must be a bool")


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    contract_version: ContractVersion
    claim_id: ClaimId
    claim_contract_version: ContractVersion
    claim_contract_ref: EvidenceRef
    quarantine_subject_id: QuarantineSubjectId
    c05_quality_result_digest: EvidenceContentDigest
    eligibility_policy_id: EligibilityPolicyId
    eligibility_policy_ref: EvidenceRef
    knowledge_from: Timestamp
    effective_from: Timestamp | None
    prerequisite_evidence_refs: tuple[EvidenceRef, ...]
    external_currentness_ref: EvidenceRef | None
    disposition: EligibilityDisposition
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    decision_id: EligibilityDecisionId = field(init=False)
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != ELIGIBILITY_DECISION_VERSION:
            raise BindingError("UNSUPPORTED_ELIGIBILITY_DECISION_VERSION")
        expected = (
            (self.claim_id, ClaimId),
            (self.quarantine_subject_id, QuarantineSubjectId),
            (self.c05_quality_result_digest, EvidenceContentDigest),
            (self.eligibility_policy_id, EligibilityPolicyId),
            (self.claim_contract_ref, EvidenceRef),
            (self.eligibility_policy_ref, EvidenceRef),
            (self.knowledge_from, Timestamp),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("invalid eligibility decision binding")
        if (
            self.effective_from is not None
            and type(self.effective_from) is not Timestamp
        ):
            raise TypeError("effective_from must be a Timestamp or None")
        if (
            self.external_currentness_ref is not None
            and type(self.external_currentness_ref) is not EvidenceRef
        ):
            raise TypeError("external_currentness_ref must be an EvidenceRef or None")
        if type(self.disposition) is not EligibilityDisposition:
            raise TypeError("invalid eligibility disposition")
        object.__setattr__(
            self, "prerequisite_evidence_refs", _refs(self.prerequisite_evidence_refs)
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "reasons", _reasons(self.reasons))
        digest = _digest("ATIS:C06:ELIGIBILITY_DECISION:1", self._body())
        object.__setattr__(self, "content_digest", digest)
        object.__setattr__(
            self,
            "decision_id",
            EligibilityDecisionId(_derived_id("c06-eligibility", digest)),
        )

    def _body(self) -> dict[str, object]:
        return {
            "contract_version": _version(self.contract_version),
            "claim_id": self.claim_id.value,
            "claim_contract_version": _version(self.claim_contract_version),
            "claim_contract_ref": _ref(self.claim_contract_ref),
            "quarantine_subject_id": self.quarantine_subject_id.value,
            "c05_quality_result_digest": self.c05_quality_result_digest.value,
            "eligibility_policy_id": self.eligibility_policy_id.value,
            "eligibility_policy_ref": _ref(self.eligibility_policy_ref),
            "knowledge_from": _timestamp(self.knowledge_from),
            "effective_from": _timestamp(self.effective_from),
            "prerequisite_evidence_refs": [
                _ref(item) for item in self.prerequisite_evidence_refs
            ],
            "external_currentness_ref": _ref(self.external_currentness_ref),
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
        }


def decide_eligibility(
    subject: QuarantineSubject,
    quarantine: QuarantineDecision,
    c05_result: ProductionQualityResult,
    policy: EligibilityPolicy,
    *,
    knowledge_from: Timestamp,
    prerequisite_evidence_refs: tuple[EvidenceRef, ...],
    resolved_evidence: tuple[ResolvedC06Evidence, ...],
    external_currentness_ref: EvidenceRef | None = None,
    effective_from: Timestamp | None = None,
) -> EligibilityDecision:
    if (
        type(subject) is not QuarantineSubject
        or type(quarantine) is not QuarantineDecision
        or type(c05_result) is not ProductionQualityResult
        or type(policy) is not EligibilityPolicy
    ):
        raise TypeError("invalid eligibility input")
    if c05_quality_result_digest(c05_result) != subject.c05_quality_result_digest:
        raise BindingError("C05_QUALITY_RESULT_BINDING_MISMATCH")
    if effective_from is not None and not policy.establishes_effective_time:
        raise BindingError("EFFECTIVE_TIME_SEMANTICS_NOT_ESTABLISHED")
    if (
        quarantine.quarantine_subject_id != subject.subject_id
        or quarantine.c05_quality_result_digest != subject.c05_quality_result_digest
    ):
        raise BindingError("QUARANTINE_SUBJECT_BINDING_MISMATCH")
    refs = prerequisite_evidence_refs + (policy.policy_ref, policy.claim_contract_ref)
    if external_currentness_ref is not None:
        refs += (external_currentness_ref,)
    refs = _refs(refs)
    verify_evidence(refs, resolved_evidence)
    if c05_result.validation_state is ValidationState.INCOMPATIBLE:
        state, reason = EligibilityDisposition.INCOMPATIBLE, "C05_RESULT_INCOMPATIBLE"
    elif c05_result.validation_state is not ValidationState.VALID:
        state, reason = (
            EligibilityDisposition.INELIGIBLE,
            "C05_PREREQUISITE_RESTRICTIVE",
        )
    elif quarantine.disposition is QuarantineDisposition.QUARANTINED:
        state, reason = EligibilityDisposition.INELIGIBLE, "SUBJECT_QUARANTINED"
    elif quarantine.disposition is not QuarantineDisposition.NOT_QUARANTINED:
        state, reason = (
            EligibilityDisposition.NOT_ESTABLISHED,
            "QUARANTINE_STATE_NOT_ESTABLISHED",
        )
    elif policy.requires_external_currentness and external_currentness_ref is None:
        state, reason = (
            EligibilityDisposition.NOT_ESTABLISHED,
            "EXTERNAL_CURRENTNESS_NOT_ESTABLISHED",
        )
    else:
        state, reason = (
            EligibilityDisposition.ELIGIBLE,
            "EXACT_CLAIM_PREREQUISITES_SATISFIED",
        )
    return EligibilityDecision(
        policy.supported_version,
        policy.claim_id,
        policy.claim_contract_version,
        policy.claim_contract_ref,
        subject.subject_id,
        subject.c05_quality_result_digest,
        policy.policy_id,
        policy.policy_ref,
        knowledge_from,
        effective_from,
        _refs(prerequisite_evidence_refs),
        external_currentness_ref,
        state,
        (reason,),
        refs,
    )


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityAssertion:
    contract_version: ContractVersion
    assertion_ref: EvidenceRef
    authority_id: ReleaseAuthorityId
    operation: str
    predecessor_quarantine_decision_id: QuarantineDecisionId
    quarantine_subject_id: QuarantineSubjectId
    authorized_claim_scope: tuple[ClaimId, ...]
    asserted_release_basis_ref: EvidenceRef
    knowledge_from: Timestamp
    effective_from: Timestamp | None
    admission_policy_id: ReleaseAuthorityPolicyId
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != RELEASE_ASSERTION_VERSION:
            raise AuthorityError("UNSUPPORTED_RELEASE_ASSERTION_VERSION")
        if self.operation != "RELEASE_QUARANTINE":
            raise AuthorityError("UNSUPPORTED_RELEASE_OPERATION")
        expected = (
            (self.assertion_ref, EvidenceRef),
            (self.authority_id, ReleaseAuthorityId),
            (self.predecessor_quarantine_decision_id, QuarantineDecisionId),
            (self.quarantine_subject_id, QuarantineSubjectId),
            (self.asserted_release_basis_ref, EvidenceRef),
            (self.knowledge_from, Timestamp),
            (self.admission_policy_id, ReleaseAuthorityPolicyId),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("invalid release authority assertion")
        if type(self.authorized_claim_scope) is not tuple or any(
            type(item) is not ClaimId for item in self.authorized_claim_scope
        ):
            raise TypeError("authorized_claim_scope must contain ClaimId values")
        if len(set(self.authorized_claim_scope)) != len(self.authorized_claim_scope):
            raise AuthorityError("DUPLICATE_AUTHORIZED_CLAIM")
        object.__setattr__(
            self,
            "authorized_claim_scope",
            tuple(sorted(self.authorized_claim_scope, key=lambda item: item.value)),
        )
        if (
            self.effective_from is not None
            and type(self.effective_from) is not Timestamp
        ):
            raise TypeError("effective_from must be a Timestamp or None")
        object.__setattr__(
            self,
            "content_digest",
            _digest("ATIS:C06:RELEASE_AUTHORITY_ASSERTION:1", self._body()),
        )

    def _body(self) -> dict[str, object]:
        return {
            "contract_version": _version(self.contract_version),
            "assertion_ref": _ref(self.assertion_ref),
            "authority_id": self.authority_id.value,
            "operation": self.operation,
            "predecessor_quarantine_decision_id": self.predecessor_quarantine_decision_id.value,
            "quarantine_subject_id": self.quarantine_subject_id.value,
            "authorized_claim_scope": [
                item.value for item in self.authorized_claim_scope
            ],
            "asserted_release_basis_ref": _ref(self.asserted_release_basis_ref),
            "knowledge_from": _timestamp(self.knowledge_from),
            "effective_from": _timestamp(self.effective_from),
            "admission_policy_id": self.admission_policy_id.value,
        }


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityAdmissionPolicy:
    contract_version: ContractVersion
    policy_id: ReleaseAuthorityPolicyId
    policy_ref: EvidenceRef
    admitted_authority_ids: tuple[ReleaseAuthorityId, ...]
    admitted_assertion_versions: tuple[ContractVersion, ...]
    admitted_operations: tuple[str, ...]
    admitted_claim_scope: tuple[ClaimId, ...]
    knowledge_from: Timestamp
    expires_at: Timestamp | None = None
    establishes_effective_time: bool = False

    def __post_init__(self) -> None:
        if self.contract_version != RELEASE_POLICY_VERSION:
            raise AuthorityError("UNSUPPORTED_RELEASE_POLICY_VERSION")
        if (
            type(self.policy_id) is not ReleaseAuthorityPolicyId
            or type(self.policy_ref) is not EvidenceRef
            or type(self.knowledge_from) is not Timestamp
        ):
            raise TypeError("invalid admission policy")
        for values, kind, label in (
            (self.admitted_authority_ids, ReleaseAuthorityId, "authority"),
            (self.admitted_assertion_versions, ContractVersion, "assertion version"),
            (self.admitted_claim_scope, ClaimId, "claim"),
        ):
            if (
                type(values) is not tuple
                or not values
                or any(type(item) is not kind for item in values)
            ):
                raise AuthorityError(f"admitted {label} values must be explicit")
            if len(set(values)) != len(values):
                raise AuthorityError(f"duplicate admitted {label}")
        if type(self.admitted_operations) is not tuple or self.admitted_operations != (
            "RELEASE_QUARANTINE",
        ):
            raise AuthorityError(
                "release operations must be exact; wildcard admission is prohibited"
            )
        if self.expires_at is not None and type(self.expires_at) is not Timestamp:
            raise TypeError("expires_at must be a Timestamp or None")
        if (
            self.expires_at is not None
            and self.expires_at.value <= self.knowledge_from.value
        ):
            raise AuthorityError("invalid policy interval")
        if type(self.establishes_effective_time) is not bool:
            raise TypeError("establishes_effective_time must be a bool")


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    contract_version: ContractVersion
    predecessor_quarantine_decision_id: QuarantineDecisionId
    quarantine_subject_id: QuarantineSubjectId
    release_authority_assertion_ref: EvidenceRef
    release_authority_admission_policy_id: ReleaseAuthorityPolicyId
    release_authority_admission_policy_ref: EvidenceRef
    release_basis_ref: EvidenceRef
    knowledge_from: Timestamp
    effective_from: Timestamp | None
    reasons: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    supersedes_decision_id: QuarantineDecisionId
    release_decision_id: ReleaseDecisionId = field(init=False)
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != RELEASE_DECISION_VERSION:
            raise BindingError("UNSUPPORTED_RELEASE_DECISION_VERSION")
        if self.predecessor_quarantine_decision_id != self.supersedes_decision_id:
            raise LineageError("PREDECESSOR_MISMATCH")
        if type(self.knowledge_from) is not Timestamp:
            raise TypeError("knowledge_from must be a Timestamp")
        if (
            self.effective_from is not None
            and type(self.effective_from) is not Timestamp
        ):
            raise TypeError("effective_from must be a Timestamp or None")
        object.__setattr__(self, "reasons", _reasons(self.reasons))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        digest = _digest("ATIS:C06:RELEASE_DECISION:1", self._body())
        object.__setattr__(self, "content_digest", digest)
        object.__setattr__(
            self,
            "release_decision_id",
            ReleaseDecisionId(_derived_id("c06-release", digest)),
        )

    def _body(self) -> dict[str, object]:
        return {
            "contract_version": _version(self.contract_version),
            "predecessor_quarantine_decision_id": self.predecessor_quarantine_decision_id.value,
            "quarantine_subject_id": self.quarantine_subject_id.value,
            "release_authority_assertion_ref": _ref(
                self.release_authority_assertion_ref
            ),
            "release_authority_admission_policy_id": self.release_authority_admission_policy_id.value,
            "release_authority_admission_policy_ref": _ref(
                self.release_authority_admission_policy_ref
            ),
            "release_basis_ref": _ref(self.release_basis_ref),
            "knowledge_from": _timestamp(self.knowledge_from),
            "effective_from": _timestamp(self.effective_from),
            "reasons": list(self.reasons),
            "evidence_refs": [_ref(item) for item in self.evidence_refs],
            "supersedes_decision_id": self.supersedes_decision_id.value,
        }


def release_quarantine(
    predecessor: QuarantineDecision,
    subject: QuarantineSubject,
    assertion: ReleaseAuthorityAssertion,
    admission_policy: ReleaseAuthorityAdmissionPolicy,
    *,
    knowledge_from: Timestamp,
    resolved_evidence: tuple[ResolvedC06Evidence, ...],
    existing_releases: tuple[ReleaseDecision, ...] = (),
    effective_from: Timestamp | None = None,
) -> ReleaseDecision:
    if predecessor.disposition is not QuarantineDisposition.QUARANTINED:
        raise LineageError("PREDECESSOR_NOT_QUARANTINED")
    if (
        predecessor.quarantine_subject_id != subject.subject_id
        or assertion.quarantine_subject_id != subject.subject_id
    ):
        raise LineageError("SUBJECT_MISMATCH")
    if assertion.predecessor_quarantine_decision_id != predecessor.decision_id:
        raise LineageError("PREDECESSOR_MISMATCH")
    if assertion.admission_policy_id != admission_policy.policy_id:
        raise AuthorityError("RELEASE_POLICY_SUBSTITUTION")
    if assertion.assertion_ref.key == admission_policy.policy_ref.key:
        raise AuthorityError("RELEASE_AUTHORITY_SELF_ADMISSION")
    if (
        assertion.effective_from is not None or effective_from is not None
    ) and not admission_policy.establishes_effective_time:
        raise AuthorityError("EFFECTIVE_TIME_SEMANTICS_NOT_ESTABLISHED")
    if assertion.authority_id not in admission_policy.admitted_authority_ids:
        raise AuthorityError("RELEASE_AUTHORITY_NOT_ADMITTED")
    if (
        assertion.contract_version not in admission_policy.admitted_assertion_versions
        or assertion.operation not in admission_policy.admitted_operations
    ):
        raise AuthorityError("RELEASE_ASSERTION_NOT_ADMITTED")
    if assertion.authorized_claim_scope and not set(
        assertion.authorized_claim_scope
    ).issubset(admission_policy.admitted_claim_scope):
        raise AuthorityError("RELEASE_SCOPE_NOT_ADMITTED")
    if assertion.knowledge_from.value < admission_policy.knowledge_from.value:
        raise AuthorityError("STALE_RELEASE_ASSERTION")
    if (
        admission_policy.expires_at is not None
        and assertion.knowledge_from.value >= admission_policy.expires_at.value
    ):
        raise AuthorityError("STALE_RELEASE_POLICY")
    if (
        knowledge_from.value <= predecessor.knowledge_from.value
        or knowledge_from.value < assertion.knowledge_from.value
    ):
        raise LineageError("SUCCESSOR_KNOWLEDGE_NOT_GREATER")
    if (
        type(existing_releases) is not tuple
        or len(existing_releases) > MAX_C06_LINEAGE_VERSIONS
    ):
        raise ResourceLimitError("C06_LINEAGE_VERSION_LIMIT_EXHAUSTED")
    if any(
        item.predecessor_quarantine_decision_id == predecessor.decision_id
        for item in existing_releases
    ):
        raise LineageError("BRANCHING_SUCCESSOR_CONFLICT")
    refs = _refs(
        (
            assertion.assertion_ref,
            assertion.asserted_release_basis_ref,
            admission_policy.policy_ref,
        )
    )
    verify_evidence(refs, resolved_evidence)
    return ReleaseDecision(
        RELEASE_DECISION_VERSION,
        predecessor.decision_id,
        subject.subject_id,
        assertion.assertion_ref,
        admission_policy.policy_id,
        admission_policy.policy_ref,
        assertion.asserted_release_basis_ref,
        knowledge_from,
        effective_from,
        ("EXTERNAL_RELEASE_AUTHORITY_ADMITTED",),
        refs,
        predecessor.decision_id,
    )


def atomic_decision_batch(
    builders: tuple[Callable[[], object], ...],
) -> tuple[object, ...]:
    """Evaluate a bounded batch atomically; no partial positive output escapes."""
    if type(builders) is not tuple or not builders:
        raise ValueError("builders must be a nonempty tuple")
    if len(builders) > MAX_C06_DECISIONS_PER_BATCH:
        raise ResourceLimitError("C06_BATCH_LIMIT_EXHAUSTED")
    if any(not callable(item) for item in builders):
        raise TypeError("builders must be callable")
    return tuple(builder() for builder in builders)


def validate_lineage_edges(edges: tuple[tuple[str, str], ...]) -> None:
    """Validate bounded successor->predecessor identities without order authority."""
    if type(edges) is not tuple:
        raise TypeError("edges must be a tuple")
    if len(edges) > MAX_C06_LINEAGE_VERSIONS:
        raise ResourceLimitError("C06_LINEAGE_VERSION_LIMIT_EXHAUSTED")
    parents: dict[str, str] = {}
    children: dict[str, str] = {}
    for successor, predecessor in edges:
        _OpaqueId(successor)
        _OpaqueId(predecessor)
        if successor == predecessor:
            raise LineageError("SELF_SUPERSESSION")
        if successor in parents and parents[successor] != predecessor:
            raise LineageError("IDENTITY_CONTENT_CONFLICT")
        if predecessor in children and children[predecessor] != successor:
            raise LineageError("BRANCHING_SUCCESSOR_CONFLICT")
        parents[successor] = predecessor
        children[predecessor] = successor
    for start in parents:
        current = start
        seen: set[str] = set()
        depth = 0
        while current in parents:
            if current in seen:
                raise LineageError("LINEAGE_CYCLE")
            seen.add(current)
            depth += 1
            if depth > MAX_C06_LINEAGE_DEPTH:
                raise ResourceLimitError("C06_LINEAGE_DEPTH_LIMIT_EXHAUSTED")
            current = parents[current]


def releases_known_at(
    values: tuple[ReleaseDecision, ...], cutoff: Timestamp
) -> tuple[ReleaseDecision, ...]:
    """Reconstruct immutable release evidence at an explicit PIT boundary."""
    if type(values) is not tuple or type(cutoff) is not Timestamp:
        raise TypeError("invalid PIT inputs")
    if len(values) > MAX_C06_LINEAGE_VERSIONS:
        raise ResourceLimitError("C06_LINEAGE_VERSION_LIMIT_EXHAUSTED")
    visible = tuple(
        item for item in values if item.knowledge_from.value <= cutoff.value
    )
    predecessors = [item.predecessor_quarantine_decision_id for item in visible]
    if len(predecessors) != len(set(predecessors)):
        raise LineageError("BRANCHING_SUCCESSOR_CONFLICT")
    return tuple(sorted(visible, key=lambda item: item.release_decision_id.value))

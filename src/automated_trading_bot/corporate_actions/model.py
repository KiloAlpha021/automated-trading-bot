"""Provider-neutral C07 corporate-action identity, version and lineage model.

This module records immutable action facts and affected-dependency declarations.
It does not map provider subtypes, adjust positions or cash, invalidate datasets,
materialize data, publish records, or perform any trading or financial action.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
import unicodedata

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
)
MAX_ACTION_LINEAGE_VERSIONS = 4096
MAX_ACTION_LINEAGE_DEPTH = 256
MAX_DEPENDENCY_ENTRIES = 256
_HEX = re.compile(r"[0-9a-f]{64}", re.ASCII)


class LineageValidationError(ValueError):
    """A complete candidate action lineage violates the protected contract."""


class ActionFamily(StrEnum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    MAPPED_MERGER_DELISTING = "MAPPED_MERGER_DELISTING"


class ActionOperation(StrEnum):
    ORIGINAL = "ORIGINAL"
    CORRECTION = "CORRECTION"
    CANCELLATION = "CANCELLATION"


class TemporalRoleState(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DependencyState(StrEnum):
    DECLARED = "DECLARED"
    KNOWN_EMPTY = "KNOWN_EMPTY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class DependencyKind(StrEnum):
    INSTRUMENT_REFERENCE = "INSTRUMENT_REFERENCE"
    CALENDAR_VERSION = "CALENDAR_VERSION"
    SOURCE_DATASET = "SOURCE_DATASET"
    DATASET_CONTENT = "DATASET_CONTENT"
    DATASET_MANIFEST = "DATASET_MANIFEST"
    DOWNSTREAM_EVIDENCE = "DOWNSTREAM_EVIDENCE"


def _required_text(value: object, name: str, *, maximum: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a str")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _timestamp_text(value: Timestamp | None) -> str | None:
    return None if value is None else value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _evidence(value: EvidenceRef) -> dict[str, str]:
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _instrument(value: InstrumentId) -> str:
    if type(value) is not InstrumentId:
        raise TypeError("instrument_id must be an InstrumentId")
    return value.to_string()


@dataclass(frozen=True, slots=True)
class CanonicalActionId:
    value: str
    _PREFIX = "atis:corporate-action:v1:"

    def __post_init__(self) -> None:
        if type(self.value) is not str or not self.value.startswith(self._PREFIX):
            raise ValueError("invalid canonical action identity namespace")
        if _HEX.fullmatch(self.value[len(self._PREFIX) :]) is None:
            raise ValueError("invalid canonical action identity digest")

    @classmethod
    def from_digest(cls, digest: EvidenceContentDigest) -> "CanonicalActionId":
        if type(digest) is not EvidenceContentDigest:
            raise TypeError("digest must be an EvidenceContentDigest")
        return cls(cls._PREFIX + digest.value.removeprefix("sha256:"))


@dataclass(frozen=True, slots=True)
class ActionVersionId:
    value: str
    _PREFIX = "atis:corporate-action-version:v1:"

    def __post_init__(self) -> None:
        if type(self.value) is not str or not self.value.startswith(self._PREFIX):
            raise ValueError("invalid action version identity namespace")
        if _HEX.fullmatch(self.value[len(self._PREFIX) :]) is None:
            raise ValueError("invalid action version identity digest")

    @classmethod
    def from_digest(cls, digest: EvidenceContentDigest) -> "ActionVersionId":
        if type(digest) is not EvidenceContentDigest:
            raise TypeError("digest must be an EvidenceContentDigest")
        return cls(cls._PREFIX + digest.value.removeprefix("sha256:"))


@dataclass(frozen=True, slots=True)
class IdentityBindingAssertion:
    root_instrument_id: InstrumentId
    family: ActionFamily
    atis_occurrence_key: EvidenceId
    identity_policy_id: ValidationPolicyId
    supporting_evidence_refs: tuple[EvidenceRef, ...]
    assertion_ref: EvidenceRef
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _instrument(self.root_instrument_id)
        if type(self.family) is not ActionFamily:
            raise TypeError("family must be an ActionFamily")
        if type(self.atis_occurrence_key) is not EvidenceId:
            raise TypeError("atis_occurrence_key must be an EvidenceId")
        if type(self.identity_policy_id) is not ValidationPolicyId:
            raise TypeError("identity_policy_id must be a ValidationPolicyId")
        if type(self.assertion_ref) is not EvidenceRef:
            raise TypeError("assertion_ref must be an EvidenceRef")
        refs = canonicalize_evidence_refs(self.supporting_evidence_refs)
        object.__setattr__(self, "supporting_evidence_refs", refs)
        digest = EvidenceContentDigest.from_bytes(_canonical_json(self._mapping()))
        if self.assertion_ref.content_digest != digest:
            raise ValueError("identity assertion evidence digest mismatch")
        object.__setattr__(self, "content_digest", digest)

    def _mapping(self) -> dict[str, object]:
        return {
            "atis_occurrence_key": self.atis_occurrence_key.value,
            "family": self.family.value,
            "identity_policy_id": self.identity_policy_id.value,
            "kind": "ATIS_C07_IDENTITY_BINDING_V1",
            "root_instrument_id": self.root_instrument_id.to_string(),
            "supporting_evidence_refs": [_evidence(value) for value in self.supporting_evidence_refs],
        }


@dataclass(frozen=True, slots=True)
class CanonicalCorporateAction:
    canonical_action_id: CanonicalActionId
    root_instrument_id: InstrumentId
    family: ActionFamily
    root_identity_binding_ref: EvidenceRef
    admitted_identity_authority_ref: EvidenceRef


def create_canonical_action(
    assertion: IdentityBindingAssertion,
    *,
    admitted_identity_authority_ref: EvidenceRef,
) -> CanonicalCorporateAction:
    """Create identity only after separate attributable authority is supplied."""
    if type(assertion) is not IdentityBindingAssertion:
        raise TypeError("assertion must be an IdentityBindingAssertion")
    if type(admitted_identity_authority_ref) is not EvidenceRef:
        raise TypeError("admitted_identity_authority_ref must be an EvidenceRef")
    if admitted_identity_authority_ref == assertion.assertion_ref:
        raise ValueError("identity assertion cannot admit its own authority")
    preimage = {
        "family": assertion.family.value,
        "kind": "ATIS_C07_CANONICAL_ACTION_V1",
        "root_identity_binding_ref": _evidence(assertion.assertion_ref),
        "root_instrument_id": assertion.root_instrument_id.to_string(),
    }
    digest = EvidenceContentDigest.from_bytes(_canonical_json(preimage))
    return CanonicalCorporateAction(
        canonical_action_id=CanonicalActionId.from_digest(digest),
        root_instrument_id=assertion.root_instrument_id,
        family=assertion.family,
        root_identity_binding_ref=assertion.assertion_ref,
        admitted_identity_authority_ref=admitted_identity_authority_ref,
    )


@dataclass(frozen=True, slots=True)
class ProviderNativeActionRef:
    source_id: SourceId
    dataset_id: DatasetId
    interpretation_ref: EvidenceRef
    native_event_id: str
    evidence_ref: EvidenceRef

    def __post_init__(self) -> None:
        if type(self.source_id) is not SourceId or type(self.dataset_id) is not DatasetId:
            raise TypeError("provider source and dataset identities have invalid types")
        if type(self.interpretation_ref) is not EvidenceRef or type(self.evidence_ref) is not EvidenceRef:
            raise TypeError("provider references must be EvidenceRef values")
        _required_text(self.native_event_id, "native_event_id", maximum=255)

    def canonical_bytes(self) -> bytes:
        return _canonical_json({
            "dataset_id": self.dataset_id.value,
            "evidence_ref": _evidence(self.evidence_ref),
            "interpretation_ref": _evidence(self.interpretation_ref),
            "native_event_id": self.native_event_id,
            "source_id": self.source_id.value,
        })


@dataclass(frozen=True, slots=True)
class TemporalRoleSlot:
    state: TemporalRoleState
    at: Timestamp | None
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not TemporalRoleState:
            raise TypeError("state must be a TemporalRoleState")
        if self.at is not None and type(self.at) is not Timestamp:
            raise TypeError("at must be a Timestamp or None")
        refs = canonicalize_evidence_refs(self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        if self.state is TemporalRoleState.ESTABLISHED:
            if self.at is None:
                raise ValueError("ESTABLISHED temporal role requires a Timestamp")
        elif self.at is not None:
            raise ValueError("non-established temporal role cannot carry a Timestamp")

    def mapping(self) -> dict[str, object]:
        return {
            "at": _timestamp_text(self.at),
            "evidence_refs": [_evidence(value) for value in self.evidence_refs],
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class TemporalRoles:
    knowledge_from: TemporalRoleSlot
    announced_at: TemporalRoleSlot
    effective_at: TemporalRoleSlot
    ex_at: TemporalRoleSlot
    record_at: TemporalRoleSlot
    payable_at: TemporalRoleSlot

    def __post_init__(self) -> None:
        if any(type(value) is not TemporalRoleSlot for value in self._values().values()):
            raise TypeError("all temporal roles must be TemporalRoleSlot values")

    def _values(self) -> dict[str, TemporalRoleSlot]:
        return {
            "announced_at": self.announced_at,
            "effective_at": self.effective_at,
            "ex_at": self.ex_at,
            "knowledge_from": self.knowledge_from,
            "payable_at": self.payable_at,
            "record_at": self.record_at,
        }

    def validate_for(self, family: ActionFamily, state: ValidationState) -> None:
        required = {
            ActionFamily.SPLIT: {"knowledge_from", "effective_at"},
            ActionFamily.DIVIDEND: {"knowledge_from", "ex_at"},
            ActionFamily.SYMBOL_CHANGE: {"knowledge_from", "effective_at"},
            ActionFamily.MAPPED_MERGER_DELISTING: {"knowledge_from", "effective_at"},
        }[family]
        optional = {
            ActionFamily.SPLIT: {"announced_at", "ex_at", "record_at"},
            ActionFamily.DIVIDEND: {"announced_at", "record_at", "payable_at", "effective_at"},
            ActionFamily.SYMBOL_CHANGE: {"announced_at"},
            ActionFamily.MAPPED_MERGER_DELISTING: {"announced_at"},
        }[family]
        for name, slot in self._values().items():
            if name not in required | optional and slot.state is not TemporalRoleState.NOT_APPLICABLE:
                raise ValueError(f"{name} must be NOT_APPLICABLE for {family.value}")
            if state is ValidationState.VALID and name in required and slot.state is not TemporalRoleState.ESTABLISHED:
                raise ValueError(f"VALID {family.value} requires established {name}")

    def mapping(self) -> dict[str, object]:
        return {name: slot.mapping() for name, slot in self._values().items()}


@dataclass(frozen=True, slots=True)
class AffectedDependency:
    dependency_kind: DependencyKind
    dependency_ref: EvidenceRef
    instrument_ids: tuple[InstrumentId, ...]
    effective_from: Timestamp | None
    effective_to: Timestamp | None
    reason: str
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.dependency_kind) is not DependencyKind or type(self.dependency_ref) is not EvidenceRef:
            raise TypeError("invalid dependency identity")
        if type(self.instrument_ids) is not tuple or not self.instrument_ids:
            raise ValueError("instrument_ids must be a nonempty tuple")
        if any(type(value) is not InstrumentId for value in self.instrument_ids):
            raise TypeError("instrument_ids must contain InstrumentId values")
        ordered = tuple(sorted(set(self.instrument_ids), key=lambda value: value.to_string()))
        object.__setattr__(self, "instrument_ids", ordered)
        for value in (self.effective_from, self.effective_to):
            if value is not None and type(value) is not Timestamp:
                raise TypeError("dependency boundaries must be Timestamp values or None")
        if self.effective_from is not None and self.effective_to is not None:
            if self.effective_to.value <= self.effective_from.value:
                raise ValueError("dependency interval must be increasing")
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))

    def canonical_bytes(self) -> bytes:
        return _canonical_json({
            "dependency_kind": self.dependency_kind.value,
            "dependency_ref": _evidence(self.dependency_ref),
            "effective_from": _timestamp_text(self.effective_from),
            "effective_to": _timestamp_text(self.effective_to),
            "evidence_refs": [_evidence(value) for value in self.evidence_refs],
            "instrument_ids": [value.to_string() for value in self.instrument_ids],
            "reason": self.reason,
        })


@dataclass(frozen=True, slots=True)
class AffectedDependencyDeclaration:
    state: DependencyState
    entries: tuple[AffectedDependency, ...]
    scope_ref: EvidenceRef | None

    def __post_init__(self) -> None:
        if type(self.state) is not DependencyState or type(self.entries) is not tuple:
            raise TypeError("invalid affected-dependency declaration")
        if any(type(value) is not AffectedDependency for value in self.entries):
            raise TypeError("entries must contain AffectedDependency values")
        if len(self.entries) > MAX_DEPENDENCY_ENTRIES:
            raise ValueError("affected dependencies exceed the resource limit")
        unique: dict[tuple[DependencyKind, tuple[SourceId, DatasetId, EvidenceId]], AffectedDependency] = {}
        for value in self.entries:
            key = value.dependency_kind, value.dependency_ref.key
            previous = unique.get(key)
            if previous is not None and previous.canonical_bytes() != value.canonical_bytes():
                raise ValueError("CONFLICTING_DEPENDENCY_DECLARATION")
            unique[key] = value
        ordered = tuple(sorted(unique.values(), key=AffectedDependency.canonical_bytes))
        object.__setattr__(self, "entries", ordered)
        if self.state is DependencyState.DECLARED and (not ordered or type(self.scope_ref) is not EvidenceRef):
            raise ValueError("DECLARED requires entries and a scope_ref")
        if self.state is DependencyState.KNOWN_EMPTY and (ordered or type(self.scope_ref) is not EvidenceRef):
            raise ValueError("KNOWN_EMPTY requires empty entries and independent scope evidence")
        if self.state is DependencyState.NOT_ESTABLISHED and self.scope_ref is not None:
            raise ValueError("NOT_ESTABLISHED requires scope_ref=None")

    def mapping(self) -> dict[str, object]:
        return {
            "entries": [json.loads(value.canonical_bytes()) for value in self.entries],
            "scope_ref": None if self.scope_ref is None else _evidence(self.scope_ref),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class LineageAuthorityAssertion:
    operation: ActionOperation
    canonical_action_id: CanonicalActionId
    predecessor_version_id: ActionVersionId
    successor_facts_digest: EvidenceContentDigest
    authority_policy_id: ValidationPolicyId
    decision_evidence_refs: tuple[EvidenceRef, ...]
    assertion_ref: EvidenceRef

    def __post_init__(self) -> None:
        if self.operation not in (ActionOperation.CORRECTION, ActionOperation.CANCELLATION):
            raise ValueError("lineage authority operation must be CORRECTION or CANCELLATION")
        if type(self.canonical_action_id) is not CanonicalActionId:
            raise TypeError("canonical_action_id must be a CanonicalActionId")
        if type(self.predecessor_version_id) is not ActionVersionId:
            raise TypeError("predecessor_version_id must be an ActionVersionId")
        if type(self.successor_facts_digest) is not EvidenceContentDigest:
            raise TypeError("successor_facts_digest must be an EvidenceContentDigest")
        if type(self.authority_policy_id) is not ValidationPolicyId:
            raise TypeError("authority_policy_id must be a ValidationPolicyId")
        if type(self.assertion_ref) is not EvidenceRef:
            raise TypeError("assertion_ref must be an EvidenceRef")
        refs = canonicalize_evidence_refs(self.decision_evidence_refs)
        object.__setattr__(self, "decision_evidence_refs", refs)
        digest = EvidenceContentDigest.from_bytes(_canonical_json(self.mapping()))
        if self.assertion_ref.content_digest != digest:
            raise ValueError("lineage authority assertion evidence digest mismatch")

    def mapping(self) -> dict[str, object]:
        return {
            "authority_policy_id": self.authority_policy_id.value,
            "canonical_action_id": self.canonical_action_id.value,
            "decision_evidence_refs": [_evidence(value) for value in self.decision_evidence_refs],
            "kind": "ATIS_C07_LINEAGE_AUTHORIZATION_V1",
            "operation": self.operation.value,
            "predecessor_version_id": self.predecessor_version_id.value,
            "successor_facts_digest": self.successor_facts_digest.value,
        }


@dataclass(frozen=True, slots=True)
class CorporateActionVersion:
    action_version_id: ActionVersionId
    facts_digest: EvidenceContentDigest
    canonical_action_id: CanonicalActionId
    root_identity_binding_ref: EvidenceRef
    root_instrument_id: InstrumentId
    family: ActionFamily
    operation: ActionOperation
    temporal_roles: TemporalRoles
    action_terms_ref: EvidenceRef
    provider_native_refs: tuple[ProviderNativeActionRef, ...]
    source_evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    predecessor_version_id: ActionVersionId | None
    lineage_reason: str | None
    lineage_authorities: tuple[LineageAuthorityAssertion, ...]
    affected_dependencies: AffectedDependencyDeclaration

    @property
    def knowledge_from(self) -> Timestamp | None:
        return self.temporal_roles.knowledge_from.at


def _provider_refs(values: tuple[ProviderNativeActionRef, ...]) -> tuple[ProviderNativeActionRef, ...]:
    if type(values) is not tuple or any(type(value) is not ProviderNativeActionRef for value in values):
        raise TypeError("provider_native_refs must be a tuple of ProviderNativeActionRef values")
    return tuple(sorted(set(values), key=ProviderNativeActionRef.canonical_bytes))


def _version_mapping(
    *, action: CanonicalCorporateAction, operation: ActionOperation,
    temporal_roles: TemporalRoles, action_terms_ref: EvidenceRef,
    provider_native_refs: tuple[ProviderNativeActionRef, ...],
    source_evidence_refs: tuple[EvidenceRef, ...], validation_policy_id: ValidationPolicyId,
    validation_state: ValidationState, predecessor_version_id: ActionVersionId | None,
    lineage_reason: str | None, lineage_authority_refs: tuple[EvidenceRef, ...],
    affected_dependencies: AffectedDependencyDeclaration,
) -> dict[str, object]:
    return {
        "action_terms_ref": _evidence(action_terms_ref),
        "affected_dependencies": affected_dependencies.mapping(),
        "canonical_action_id": action.canonical_action_id.value,
        "family": action.family.value,
        "kind": "ATIS_C07_ACTION_VERSION_V1",
        "lineage_authority_refs": [_evidence(value) for value in lineage_authority_refs],
        "lineage_reason": lineage_reason,
        "operation": operation.value,
        "predecessor_version_id": None if predecessor_version_id is None else predecessor_version_id.value,
        "provider_native_refs": [json.loads(value.canonical_bytes()) for value in provider_native_refs],
        "root_identity_binding_ref": _evidence(action.root_identity_binding_ref),
        "root_instrument_id": action.root_instrument_id.to_string(),
        "source_evidence_refs": [_evidence(value) for value in source_evidence_refs],
        "temporal_roles": temporal_roles.mapping(),
        "validation_policy_id": validation_policy_id.value,
        "validation_state": validation_state.value,
    }


def compute_action_facts_digest(
    *, action: CanonicalCorporateAction, operation: ActionOperation,
    temporal_roles: TemporalRoles, action_terms_ref: EvidenceRef,
    provider_native_refs: tuple[ProviderNativeActionRef, ...],
    source_evidence_refs: tuple[EvidenceRef, ...], validation_policy_id: ValidationPolicyId,
    validation_state: ValidationState, affected_dependencies: AffectedDependencyDeclaration,
    predecessor_version_id: ActionVersionId | None = None,
    lineage_reason: str | None = None,
) -> EvidenceContentDigest:
    """Compute the non-circular successor facts digest used by authority evidence."""
    providers = _provider_refs(provider_native_refs)
    sources = canonicalize_evidence_refs(source_evidence_refs)
    temporal_roles.validate_for(action.family, validation_state)
    base = _version_mapping(
        action=action, operation=operation, temporal_roles=temporal_roles,
        action_terms_ref=action_terms_ref, provider_native_refs=providers,
        source_evidence_refs=sources, validation_policy_id=validation_policy_id,
        validation_state=validation_state, predecessor_version_id=predecessor_version_id,
        lineage_reason=lineage_reason, lineage_authority_refs=(),
        affected_dependencies=affected_dependencies,
    )
    return EvidenceContentDigest.from_bytes(_canonical_json(base))


def create_action_version(
    *, action: CanonicalCorporateAction, operation: ActionOperation,
    temporal_roles: TemporalRoles, action_terms_ref: EvidenceRef,
    provider_native_refs: tuple[ProviderNativeActionRef, ...],
    source_evidence_refs: tuple[EvidenceRef, ...], validation_policy_id: ValidationPolicyId,
    validation_state: ValidationState, affected_dependencies: AffectedDependencyDeclaration,
    predecessor_version_id: ActionVersionId | None = None,
    lineage_reason: str | None = None,
    lineage_authorities: tuple[LineageAuthorityAssertion, ...] = (),
) -> CorporateActionVersion:
    if type(action) is not CanonicalCorporateAction or type(operation) is not ActionOperation:
        raise TypeError("invalid action or operation")
    if type(temporal_roles) is not TemporalRoles or type(action_terms_ref) is not EvidenceRef:
        raise TypeError("invalid temporal roles or action terms reference")
    if type(validation_policy_id) is not ValidationPolicyId or type(validation_state) is not ValidationState:
        raise TypeError("invalid validation policy or state")
    if type(affected_dependencies) is not AffectedDependencyDeclaration:
        raise TypeError("affected_dependencies must be an AffectedDependencyDeclaration")
    providers = _provider_refs(provider_native_refs)
    sources = canonicalize_evidence_refs(source_evidence_refs)
    temporal_roles.validate_for(action.family, validation_state)
    if operation is ActionOperation.ORIGINAL:
        if predecessor_version_id is not None or lineage_reason is not None or lineage_authorities:
            raise ValueError("ORIGINAL cannot carry predecessor or lineage authority")
    else:
        if type(predecessor_version_id) is not ActionVersionId:
            raise ValueError("successor requires predecessor_version_id")
        lineage_reason = _required_text(lineage_reason, "lineage_reason")
        if type(lineage_authorities) is not tuple or not lineage_authorities:
            raise ValueError("successor requires independent lineage authority")
    facts_digest = compute_action_facts_digest(
        action=action, operation=operation, temporal_roles=temporal_roles,
        action_terms_ref=action_terms_ref, provider_native_refs=providers,
        source_evidence_refs=sources, validation_policy_id=validation_policy_id,
        validation_state=validation_state, predecessor_version_id=predecessor_version_id,
        lineage_reason=lineage_reason, affected_dependencies=affected_dependencies,
    )
    authorities = tuple(sorted(lineage_authorities, key=lambda value: value.assertion_ref.canonical_bytes()))
    if operation is not ActionOperation.ORIGINAL:
        for authority in authorities:
            if type(authority) is not LineageAuthorityAssertion:
                raise TypeError("lineage_authorities contains an invalid value")
            if (
                authority.operation is not operation
                or authority.canonical_action_id != action.canonical_action_id
                or authority.predecessor_version_id != predecessor_version_id
                or authority.successor_facts_digest != facts_digest
            ):
                raise ValueError("lineage authority does not bind the exact successor facts")
    authority_refs = canonicalize_evidence_refs(tuple(value.assertion_ref for value in authorities)) if authorities else ()
    body = _version_mapping(
        action=action, operation=operation, temporal_roles=temporal_roles,
        action_terms_ref=action_terms_ref, provider_native_refs=providers,
        source_evidence_refs=sources, validation_policy_id=validation_policy_id,
        validation_state=validation_state, predecessor_version_id=predecessor_version_id,
        lineage_reason=lineage_reason, lineage_authority_refs=authority_refs,
        affected_dependencies=affected_dependencies,
    )
    version_digest = EvidenceContentDigest.from_bytes(_canonical_json(body))
    return CorporateActionVersion(
        action_version_id=ActionVersionId.from_digest(version_digest),
        facts_digest=facts_digest, canonical_action_id=action.canonical_action_id,
        root_identity_binding_ref=action.root_identity_binding_ref,
        root_instrument_id=action.root_instrument_id, family=action.family,
        operation=operation, temporal_roles=temporal_roles, action_terms_ref=action_terms_ref,
        provider_native_refs=providers, source_evidence_refs=sources,
        validation_policy_id=validation_policy_id, validation_state=validation_state,
        predecessor_version_id=predecessor_version_id, lineage_reason=lineage_reason,
        lineage_authorities=authorities, affected_dependencies=affected_dependencies,
    )


def validate_action_lineage(
    values: tuple[CorporateActionVersion, ...],
) -> tuple[CorporateActionVersion, ...]:
    if type(values) is not tuple or not values:
        raise LineageValidationError("lineage must be a nonempty tuple")
    if len(values) > MAX_ACTION_LINEAGE_VERSIONS:
        raise LineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
    if any(type(value) is not CorporateActionVersion for value in values):
        raise TypeError("lineage contains an invalid version")
    by_id: dict[ActionVersionId, CorporateActionVersion] = {}
    for value in values:
        if value.action_version_id in by_id:
            continue
        by_id[value.action_version_id] = value
    roots = [value for value in by_id.values() if value.operation is ActionOperation.ORIGINAL]
    if len(roots) != 1:
        raise LineageValidationError("EXACTLY_ONE_ORIGINAL_REQUIRED")
    root = roots[0]
    for value in by_id.values():
        if (
            value.canonical_action_id != root.canonical_action_id
            or value.root_instrument_id != root.root_instrument_id
            or value.family is not root.family
        ):
            raise LineageValidationError("CROSS_ACTION_OR_FAMILY_EDGE")
    successors: dict[ActionVersionId, ActionVersionId] = {}
    for value in by_id.values():
        predecessor_id = value.predecessor_version_id
        if predecessor_id is None:
            continue
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise LineageValidationError("MISSING_PREDECESSOR")
        if predecessor_id in successors and successors[predecessor_id] != value.action_version_id:
            raise LineageValidationError("BRANCHING_SUCCESSOR_CONFLICT")
        successors[predecessor_id] = value.action_version_id
        if value.knowledge_from is None or predecessor.knowledge_from is None:
            raise LineageValidationError("SUCCESSOR_KNOWLEDGE_NOT_ESTABLISHED")
        if value.knowledge_from.value <= predecessor.knowledge_from.value:
            raise LineageValidationError("SUCCESSOR_KNOWLEDGE_NOT_GREATER")
    for start in by_id:
        seen: set[ActionVersionId] = set()
        current = start
        depth = 0
        while by_id[current].predecessor_version_id is not None:
            prior_id = by_id[current].predecessor_version_id
            assert prior_id is not None
            if prior_id == start or prior_id in seen:
                raise LineageValidationError("LINEAGE_CYCLE")
            seen.add(prior_id)
            depth += 1
            if depth > MAX_ACTION_LINEAGE_DEPTH:
                raise LineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
            current = prior_id
    return tuple(sorted(by_id.values(), key=lambda value: value.action_version_id.value))

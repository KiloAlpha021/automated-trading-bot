"""C08-S1 deterministic point-in-time materialisation foundations.

The module constructs immutable semantic records only.  It does not persist,
publish, manifest, promote, select providers, or grant downstream authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import re
from typing import cast
import unicodedata

from automated_trading_bot.datasets.provenance import (
    CanonicalDatasetRepresentationId,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencyRef,
    DependencySetId,
    EntitlementProvenance,
    EntitlementState,
    TransformationExecutionId,
    TransformationId,
    TransformationImplementationId,
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


SHARED_CONTRACT_VERSION = "ATIS_STAGE3_SHARED_DATASET_LIFECYCLE_CONTRACT_V1"
REPRESENTATION_VERSION = "ATIS_C08_CANONICAL_DATASET_REPRESENTATION_V1"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)


class MaterializationError(ValueError):
    """A materialisation input is malformed or contradicts its identity."""


class CanonicalRepresentationError(MaterializationError):
    """Semantic content cannot be represented by the bounded V1 model."""


class PitMaterializationError(MaterializationError):
    """Point-in-time evaluation cannot safely process the supplied request."""


class PitSufficiencyState(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    INCOMPATIBLE = "INCOMPATIBLE"


class SemanticKind(StrEnum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    UTC_TIMESTAMP = "UTC_TIMESTAMP"
    LOCAL_DATE = "LOCAL_DATE"
    IDENTITY = "IDENTITY"
    DIGEST = "DIGEST"
    ENUM = "ENUM"
    RECORD = "RECORD"
    SEQUENCE = "SEQUENCE"
    SET = "SET"


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        _identity(self.value, "identity")


@dataclass(frozen=True, slots=True)
class LogicalContentId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class DatasetVersionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class MaterializationRecordId(_OpaqueId):
    pass


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalRepresentationError(f"{name.upper()}_NOT_NFC")
    return value


def _identity(value: object, name: str) -> str:
    checked = _text(value, name)
    if _ID.fullmatch(checked) is None:
        raise ValueError(f"{name} must be a canonical attributable identity")
    return checked


def _time(value: Timestamp) -> str:
    if type(value) is not Timestamp:
        raise TypeError("expected Timestamp")
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ref(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise TypeError("expected EvidenceRef")
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _refs(values: tuple[EvidenceRef, ...], name: str, *, allow_empty: bool = False) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if not values and allow_empty:
        return ()
    try:
        return canonicalize_evidence_refs(values)
    except (ValueError, EvidenceIdentityConflict) as error:
        raise PitMaterializationError(str(error)) from error


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(domain.encode("ascii") + b"\0" + canonical_json(body))


def _derived(prefix: str, digest: EvidenceContentDigest) -> str:
    return f"{prefix}:{digest.value.removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    schema_identity: str
    fields: tuple[tuple[str, SemanticValue], ...]

    def __post_init__(self) -> None:
        _identity(self.schema_identity, "record schema identity")
        if type(self.fields) is not tuple:
            raise TypeError("record fields must be a tuple")
        names: set[str] = set()
        for field in self.fields:
            if type(field) is not tuple or len(field) != 2:
                raise TypeError("record fields must be name/value tuples")
            name, value = field
            _text(name, "record field name")
            if name in names:
                raise CanonicalRepresentationError("DUPLICATE_RECORD_FIELD")
            if type(value) is not SemanticValue:
                raise TypeError("record field values must be SemanticValue values")
            names.add(name)

    def body(self) -> dict[str, object]:
        return {
            "fields": [{"name": name, "value": value.body()} for name, value in self.fields],
            "schema_identity": self.schema_identity,
        }


@dataclass(frozen=True, slots=True)
class SemanticValue:
    kind: SemanticKind
    value: object

    def __post_init__(self) -> None:
        if type(self.kind) is not SemanticKind:
            raise TypeError("kind must be SemanticKind")
        value = self.value
        if self.kind in (SemanticKind.TEXT, SemanticKind.ENUM):
            _text(value, self.kind.value.lower())
        elif self.kind is SemanticKind.INTEGER:
            if type(value) is not int:
                raise CanonicalRepresentationError("INTEGER_REQUIRES_EXACT_INT")
        elif self.kind is SemanticKind.BOOLEAN:
            if type(value) is not bool:
                raise CanonicalRepresentationError("BOOLEAN_REQUIRES_EXACT_BOOL")
        elif self.kind is SemanticKind.NULL:
            if value is not None:
                raise CanonicalRepresentationError("NULL_REQUIRES_NONE")
        elif self.kind is SemanticKind.UTC_TIMESTAMP:
            if type(value) is not Timestamp:
                raise CanonicalRepresentationError("UTC_TIMESTAMP_REQUIRES_TIMESTAMP")
        elif self.kind is SemanticKind.LOCAL_DATE:
            if type(value) is not date:
                raise CanonicalRepresentationError("LOCAL_DATE_REQUIRES_DATE")
        elif self.kind is SemanticKind.IDENTITY:
            _identity(value, "semantic identity")
        elif self.kind is SemanticKind.DIGEST:
            if type(value) is not EvidenceContentDigest:
                raise CanonicalRepresentationError("DIGEST_REQUIRES_PROTECTED_DIGEST")
        elif self.kind is SemanticKind.RECORD:
            if type(value) is not CanonicalRecord:
                raise CanonicalRepresentationError("RECORD_REQUIRES_CANONICAL_RECORD")
        elif self.kind in (SemanticKind.SEQUENCE, SemanticKind.SET):
            if type(value) is not tuple or any(type(item) is not SemanticValue for item in value):
                raise CanonicalRepresentationError(f"{self.kind.value}_REQUIRES_SEMANTIC_VALUES")
            if self.kind is SemanticKind.SET:
                bodies = {canonical_json(item.body()): item for item in value}
                object.__setattr__(self, "value", tuple(bodies[key] for key in sorted(bodies)))
        else:  # pragma: no cover - enum exhaustiveness guard
            raise CanonicalRepresentationError("UNSUPPORTED_SEMANTIC_KIND")

    def body(self) -> dict[str, object]:
        if self.kind is SemanticKind.UTC_TIMESTAMP:
            encoded: object = _time(cast(Timestamp, self.value))
        elif self.kind is SemanticKind.LOCAL_DATE:
            encoded = cast(date, self.value).isoformat()
        elif self.kind is SemanticKind.DIGEST:
            encoded = cast(EvidenceContentDigest, self.value).value
        elif self.kind is SemanticKind.RECORD:
            encoded = cast(CanonicalRecord, self.value).body()
        elif self.kind in (SemanticKind.SEQUENCE, SemanticKind.SET):
            encoded = [item.body() for item in cast(tuple[SemanticValue, ...], self.value)]
        else:
            encoded = self.value
        return {"kind": self.kind.value, "value": encoded}


@dataclass(frozen=True, slots=True)
class CanonicalDataset:
    logical_schema_identity: str
    logical_schema_version: str
    logical_key_fields: tuple[str, ...]
    records: tuple[CanonicalRecord, ...]

    def __post_init__(self) -> None:
        _identity(self.logical_schema_identity, "logical schema identity")
        _identity(self.logical_schema_version, "logical schema version")
        if type(self.logical_key_fields) is not tuple or not self.logical_key_fields:
            raise CanonicalRepresentationError("LOGICAL_RECORD_KEY_NOT_ESTABLISHED")
        keys = tuple(_text(item, "logical key field") for item in self.logical_key_fields)
        if len(set(keys)) != len(keys):
            raise CanonicalRepresentationError("DUPLICATE_LOGICAL_KEY_FIELD")
        if type(self.records) is not tuple or any(type(item) is not CanonicalRecord for item in self.records):
            raise TypeError("records must contain CanonicalRecord values")

    def body(self) -> dict[str, object]:
        return {
            "logical_key_fields": list(self.logical_key_fields),
            "logical_schema_identity": self.logical_schema_identity,
            "logical_schema_version": self.logical_schema_version,
            "records": [record.body() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class DatasetSchemaDescriptor:
    schema_identity: str
    schema_version: str
    field_names: tuple[str, ...]
    logical_key_fields: tuple[str, ...]
    schema_ref: EvidenceRef
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        _identity(self.schema_identity, "dataset schema identity")
        _identity(self.schema_version, "dataset schema version")
        if type(self.field_names) is not tuple or not self.field_names:
            raise CanonicalRepresentationError("SCHEMA_FIELDS_NOT_ESTABLISHED")
        fields = tuple(_text(item, "schema field") for item in self.field_names)
        if len(set(fields)) != len(fields):
            raise CanonicalRepresentationError("DUPLICATE_SCHEMA_FIELD")
        if type(self.logical_key_fields) is not tuple or not self.logical_key_fields:
            raise CanonicalRepresentationError("LOGICAL_RECORD_KEY_NOT_ESTABLISHED")
        keys = tuple(_text(item, "logical key field") for item in self.logical_key_fields)
        if len(set(keys)) != len(keys):
            raise CanonicalRepresentationError("DUPLICATE_LOGICAL_KEY_FIELD")
        if not set(keys).issubset(fields):
            raise CanonicalRepresentationError("LOGICAL_KEY_FIELD_NOT_DECLARED")
        if type(self.schema_ref) is not EvidenceRef:
            raise TypeError("schema_ref must be EvidenceRef")
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        object.__setattr__(self, "field_names", fields)
        object.__setattr__(self, "logical_key_fields", keys)
        if self.content_digest != self.derived_digest():
            raise CanonicalRepresentationError("SCHEMA_DESCRIPTOR_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls,
        *,
        schema_identity: str,
        schema_version: str,
        field_names: tuple[str, ...],
        logical_key_fields: tuple[str, ...],
        schema_ref: EvidenceRef,
    ) -> DatasetSchemaDescriptor:
        body = _schema_body(
            schema_identity, schema_version, field_names, logical_key_fields, schema_ref
        )
        return cls(
            schema_identity,
            schema_version,
            field_names,
            logical_key_fields,
            schema_ref,
            _digest("ATIS:C08:DATASET_SCHEMA_DESCRIPTOR:1", body),
        )

    def derived_digest(self) -> EvidenceContentDigest:
        return _digest(
            "ATIS:C08:DATASET_SCHEMA_DESCRIPTOR:1",
            _schema_body(
                self.schema_identity,
                self.schema_version,
                self.field_names,
                self.logical_key_fields,
                self.schema_ref,
            ),
        )


def _schema_body(
    schema_identity: str,
    schema_version: str,
    field_names: tuple[str, ...],
    logical_key_fields: tuple[str, ...],
    schema_ref: EvidenceRef,
) -> dict[str, object]:
    return {
        "field_names": list(field_names),
        "logical_key_fields": list(logical_key_fields),
        "schema_identity": schema_identity,
        "schema_ref": _ref(schema_ref),
        "schema_version": schema_version,
    }


@dataclass(frozen=True, slots=True)
class RepresentationContract:
    representation_id: CanonicalDatasetRepresentationId
    contract_ref: EvidenceRef
    content_digest: EvidenceContentDigest
    version: str = REPRESENTATION_VERSION

    def __post_init__(self) -> None:
        if type(self.representation_id) is not CanonicalDatasetRepresentationId:
            raise TypeError("representation_id must be CanonicalDatasetRepresentationId")
        if type(self.contract_ref) is not EvidenceRef:
            raise TypeError("contract_ref must be EvidenceRef")
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        if self.version != REPRESENTATION_VERSION:
            raise CanonicalRepresentationError("UNSUPPORTED_REPRESENTATION_VERSION")


@dataclass(frozen=True, slots=True)
class TransformationExecutionBinding:
    transformation_id: TransformationId
    version_id: TransformationVersionId
    implementation_id: TransformationImplementationId
    execution_id: TransformationExecutionId
    implementation_policy_ref: EvidenceRef
    contract_ref: EvidenceRef
    parameters_digest: EvidenceContentDigest
    input_evidence_refs: tuple[EvidenceRef, ...]
    dependency_ids: tuple[str, ...]
    representation_id: CanonicalDatasetRepresentationId
    pit_cutoff: Timestamp

    def __post_init__(self) -> None:
        expected = (
            (self.transformation_id, TransformationId),
            (self.version_id, TransformationVersionId),
            (self.implementation_id, TransformationImplementationId),
            (self.execution_id, TransformationExecutionId),
            (self.implementation_policy_ref, EvidenceRef),
            (self.contract_ref, EvidenceRef),
            (self.parameters_digest, EvidenceContentDigest),
            (self.representation_id, CanonicalDatasetRepresentationId),
            (self.pit_cutoff, Timestamp),
        )
        for value, kind in expected:
            if type(value) is not kind:
                raise TypeError(f"transformation binding requires {kind.__name__}")
        object.__setattr__(self, "input_evidence_refs", _refs(self.input_evidence_refs, "transformation inputs"))
        if type(self.dependency_ids) is not tuple or not self.dependency_ids:
            raise PitMaterializationError("TRANSFORMATION_DEPENDENCIES_NOT_ESTABLISHED")
        identities = tuple(sorted({_identity(item, "transformation dependency") for item in self.dependency_ids}))
        object.__setattr__(self, "dependency_ids", identities)
        if any(token in self.implementation_id.value.lower() for token in ("latest", "branch", "tag", "alias")):
            raise PitMaterializationError("MUTABLE_IMPLEMENTATION_REFERENCE_NOT_AUTHORITATIVE")
        if self.execution_id != self.derived_id():
            raise PitMaterializationError("TRANSFORMATION_EXECUTION_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls,
        *,
        transformation_id: TransformationId,
        version_id: TransformationVersionId,
        implementation_id: TransformationImplementationId,
        implementation_policy_ref: EvidenceRef,
        contract_ref: EvidenceRef,
        parameters_digest: EvidenceContentDigest,
        input_evidence_refs: tuple[EvidenceRef, ...],
        dependency_ids: tuple[str, ...],
        representation_id: CanonicalDatasetRepresentationId,
        pit_cutoff: Timestamp,
    ) -> TransformationExecutionBinding:
        canonical_inputs = _refs(input_evidence_refs, "transformation inputs")
        canonical_dependencies = tuple(sorted({_identity(item, "transformation dependency") for item in dependency_ids}))
        body = _transformation_body(
            transformation_id, version_id, implementation_id, implementation_policy_ref,
            contract_ref, parameters_digest, canonical_inputs, canonical_dependencies,
            representation_id, pit_cutoff,
        )
        digest = _digest("ATIS:C08:TRANSFORMATION_EXECUTION:1", body)
        execution_id = TransformationExecutionId(_derived("c08-transformation-execution", digest))
        return cls(
            transformation_id, version_id, implementation_id, execution_id,
            implementation_policy_ref, contract_ref, parameters_digest,
            canonical_inputs, canonical_dependencies, representation_id, pit_cutoff,
        )

    def derived_id(self) -> TransformationExecutionId:
        digest = _digest(
            "ATIS:C08:TRANSFORMATION_EXECUTION:1",
            _transformation_body(
                self.transformation_id, self.version_id, self.implementation_id,
                self.implementation_policy_ref, self.contract_ref,
                self.parameters_digest, self.input_evidence_refs,
                self.dependency_ids, self.representation_id, self.pit_cutoff,
            ),
        )
        return TransformationExecutionId(_derived("c08-transformation-execution", digest))


def _transformation_body(
    transformation_id: TransformationId,
    version_id: TransformationVersionId,
    implementation_id: TransformationImplementationId,
    implementation_policy_ref: EvidenceRef,
    contract_ref: EvidenceRef,
    parameters_digest: EvidenceContentDigest,
    inputs: tuple[EvidenceRef, ...],
    dependencies: tuple[str, ...],
    representation_id: CanonicalDatasetRepresentationId,
    cutoff: Timestamp,
) -> dict[str, object]:
    return {
        "contract_ref": _ref(contract_ref),
        "dependency_ids": list(dependencies),
        "implementation_id": implementation_id.value,
        "implementation_policy_ref": _ref(implementation_policy_ref),
        "input_evidence_refs": [_ref(item) for item in inputs],
        "parameters_digest": parameters_digest.value,
        "pit_cutoff": _time(cutoff),
        "representation_id": representation_id.value,
        "transformation_id": transformation_id.value,
        "version_id": version_id.value,
    }


@dataclass(frozen=True, slots=True)
class MaterializationEvidence:
    evidence_ref: EvidenceRef
    logical_key: tuple[SemanticValue, ...]
    record: CanonicalRecord
    knowledge_time: Timestamp | None
    effective_from: Timestamp | None
    effective_to: Timestamp | None
    acquisition_time: Timestamp | None = None
    multiplicity_established: bool = True

    def __post_init__(self) -> None:
        if type(self.evidence_ref) is not EvidenceRef:
            raise TypeError("evidence_ref must be EvidenceRef")
        if type(self.logical_key) is not tuple or not self.logical_key:
            raise PitMaterializationError("LOGICAL_RECORD_KEY_NOT_ESTABLISHED")
        if any(type(item) is not SemanticValue for item in self.logical_key):
            raise TypeError("logical_key must contain SemanticValue values")
        if type(self.record) is not CanonicalRecord:
            raise TypeError("record must be CanonicalRecord")
        for value in (self.knowledge_time, self.effective_from, self.effective_to, self.acquisition_time):
            if value is not None and type(value) is not Timestamp:
                raise TypeError("evidence times must be Timestamp values or None")
        if self.effective_from is not None and self.effective_to is not None and self.effective_to.value <= self.effective_from.value:
            raise PitMaterializationError("INVALID_EFFECTIVE_INTERVAL")
        if type(self.multiplicity_established) is not bool:
            raise TypeError("multiplicity_established must be bool")


@dataclass(frozen=True, slots=True)
class CandidateCoverage:
    requested_scope_ref: EvidenceRef
    coverage_evidence_ref: EvidenceRef
    candidate_evidence_refs: tuple[EvidenceRef, ...]
    complete: bool
    candidate_population_digest: EvidenceContentDigest
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        if type(self.requested_scope_ref) is not EvidenceRef:
            raise TypeError("requested_scope_ref must be EvidenceRef")
        if type(self.coverage_evidence_ref) is not EvidenceRef:
            raise TypeError("coverage_evidence_ref must be EvidenceRef")
        object.__setattr__(self, "candidate_evidence_refs", _refs(self.candidate_evidence_refs, "candidate evidence"))
        if type(self.complete) is not bool:
            raise TypeError("complete must be bool")
        if type(self.candidate_population_digest) is not EvidenceContentDigest:
            raise TypeError("candidate_population_digest must be EvidenceContentDigest")
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        expected_population = _digest(
            "ATIS:C08:CANDIDATE_POPULATION:1",
            [_ref(item) for item in self.candidate_evidence_refs],
        )
        if self.candidate_population_digest != expected_population:
            raise PitMaterializationError("CANDIDATE_POPULATION_DIGEST_CONFLICT")
        if self.content_digest != self.derived_digest():
            raise PitMaterializationError("CANDIDATE_COVERAGE_IDENTITY_CONTENT_CONFLICT")

    @classmethod
    def create(
        cls,
        *,
        requested_scope_ref: EvidenceRef,
        coverage_evidence_ref: EvidenceRef,
        candidate_evidence_refs: tuple[EvidenceRef, ...],
        complete: bool,
    ) -> CandidateCoverage:
        canonical = _refs(candidate_evidence_refs, "candidate evidence")
        population = _digest(
            "ATIS:C08:CANDIDATE_POPULATION:1", [_ref(item) for item in canonical]
        )
        body = _coverage_body(
            requested_scope_ref, coverage_evidence_ref, canonical, complete, population
        )
        return cls(
            requested_scope_ref,
            coverage_evidence_ref,
            canonical,
            complete,
            population,
            _digest("ATIS:C08:CANDIDATE_COVERAGE:1", body),
        )

    def derived_digest(self) -> EvidenceContentDigest:
        return _digest(
            "ATIS:C08:CANDIDATE_COVERAGE:1",
            _coverage_body(
                self.requested_scope_ref,
                self.coverage_evidence_ref,
                self.candidate_evidence_refs,
                self.complete,
                self.candidate_population_digest,
            ),
        )


def _coverage_body(
    requested_scope_ref: EvidenceRef,
    coverage_evidence_ref: EvidenceRef,
    candidate_evidence_refs: tuple[EvidenceRef, ...],
    complete: bool,
    population_digest: EvidenceContentDigest,
) -> dict[str, object]:
    return {
        "candidate_evidence_refs": [_ref(item) for item in candidate_evidence_refs],
        "candidate_population_digest": population_digest.value,
        "complete": complete,
        "coverage_evidence_ref": _ref(coverage_evidence_ref),
        "requested_scope_ref": _ref(requested_scope_ref),
    }


@dataclass(frozen=True, slots=True)
class HistoricalUniverseEvidence:
    required: bool
    policy_ref: EvidenceRef | None
    evidence_refs: tuple[EvidenceRef, ...]
    supported: bool
    uses_current_constituents: bool = False

    def __post_init__(self) -> None:
        if type(self.required) is not bool or type(self.supported) is not bool or type(self.uses_current_constituents) is not bool:
            raise TypeError("historical-universe flags must be bool")
        if self.policy_ref is not None and type(self.policy_ref) is not EvidenceRef:
            raise TypeError("policy_ref must be EvidenceRef or None")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "historical universe evidence", allow_empty=True))


@dataclass(frozen=True, slots=True)
class MaterializationRequest:
    requested_claim_or_scope_ref: EvidenceRef
    dataset_schema: DatasetSchemaDescriptor
    pit_cutoff: Timestamp
    evaluation_time: Timestamp
    effective_as_of: Timestamp
    coverage: CandidateCoverage
    evidence: tuple[MaterializationEvidence, ...]
    dependency_set_id: DependencySetId
    dependency_refs: tuple[DependencyRef, ...]
    transformation: TransformationExecutionBinding
    representation: RepresentationContract
    historical_universe: HistoricalUniverseEvidence
    entitlements: tuple[EntitlementProvenance, ...]
    entitlement_material: bool
    semantic_policy_refs: tuple[EvidenceRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    resource_policy: DatasetLifecycleResourcePolicy

    def __post_init__(self) -> None:
        if type(self.requested_claim_or_scope_ref) is not EvidenceRef:
            raise TypeError("requested_claim_or_scope_ref must be EvidenceRef")
        if any(type(value) is not kind for value, kind in (
            (self.pit_cutoff, Timestamp), (self.evaluation_time, Timestamp),
            (self.effective_as_of, Timestamp), (self.coverage, CandidateCoverage),
            (self.dataset_schema, DatasetSchemaDescriptor),
            (self.dependency_set_id, DependencySetId),
            (self.transformation, TransformationExecutionBinding),
            (self.representation, RepresentationContract),
            (self.historical_universe, HistoricalUniverseEvidence),
            (self.resource_policy, DatasetLifecycleResourcePolicy),
        )):
            raise TypeError("request contains an invalid protected value")
        if type(self.evidence) is not tuple or any(type(item) is not MaterializationEvidence for item in self.evidence):
            raise TypeError("evidence must contain MaterializationEvidence values")
        if type(self.dependency_refs) is not tuple or any(type(item) is not DependencyRef for item in self.dependency_refs):
            raise TypeError("dependency_refs must contain DependencyRef values")
        if type(self.entitlements) is not tuple or any(type(item) is not EntitlementProvenance for item in self.entitlements):
            raise TypeError("entitlements must contain EntitlementProvenance values")
        if type(self.entitlement_material) is not bool:
            raise TypeError("entitlement_material must be bool")
        object.__setattr__(self, "semantic_policy_refs", _refs(self.semantic_policy_refs, "semantic policies", allow_empty=True))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "materialization evidence"))
        entitlements: dict[str, EntitlementProvenance] = {}
        for item in self.entitlements:
            key = item.entitlement_id.value
            previous = entitlements.get(key)
            if previous is not None and _entitlement_body(previous) != _entitlement_body(item):
                raise PitMaterializationError("ENTITLEMENT_IDENTITY_CONTENT_CONFLICT")
            entitlements[key] = item
        object.__setattr__(self, "entitlements", tuple(entitlements[key] for key in sorted(entitlements)))


@dataclass(frozen=True, slots=True)
class MaterializationRecord:
    record_id: MaterializationRecordId
    contract_version: str
    requested_claim_or_scope_ref: EvidenceRef
    dataset_schema_ref: EvidenceRef
    pit_cutoff: Timestamp
    evaluation_time: Timestamp
    selected_evidence_refs: tuple[EvidenceRef, ...]
    dependency_set_id: DependencySetId
    dependency_refs: tuple[DependencyRef, ...]
    historical_universe_policy_and_evidence_where_required: HistoricalUniverseEvidence
    transformation_id: TransformationId
    transformation_version_id: TransformationVersionId
    transformation_implementation_id: TransformationImplementationId
    transformation_execution_id: TransformationExecutionId
    transformation_parameters_digest: EvidenceContentDigest
    canonical_dataset_representation_id: CanonicalDatasetRepresentationId
    canonical_representation_contract_ref: EvidenceRef
    resulting_logical_content_id: LogicalContentId
    pit_sufficiency_state: PitSufficiencyState
    limitations: tuple[str, ...]
    entitlement_provenance_inputs_where_material: tuple[EntitlementProvenance, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    resource_policy_id: DatasetLifecycleResourcePolicyId
    content_digest: EvidenceContentDigest

    def semantic_body(self) -> dict[str, object]:
        return _record_body(self, include_identity=False)

    def __post_init__(self) -> None:
        if type(self.record_id) is not MaterializationRecordId:
            raise TypeError("record_id must be MaterializationRecordId")
        if type(self.pit_sufficiency_state) is not PitSufficiencyState:
            raise TypeError("pit_sufficiency_state must be PitSufficiencyState")
        expected_digest = _digest("ATIS:C08:MATERIALIZATION_RECORD:1", self.semantic_body())
        if self.content_digest != expected_digest:
            raise PitMaterializationError("MATERIALIZATION_RECORD_IDENTITY_CONTENT_CONFLICT")
        expected_id = MaterializationRecordId(_derived("c08-materialization", expected_digest))
        if self.record_id != expected_id:
            raise PitMaterializationError("MATERIALIZATION_RECORD_IDENTITY_CONTENT_CONFLICT")


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    dataset_version_id: DatasetVersionId
    logical_content_id: LogicalContentId
    materialization_record: MaterializationRecord
    semantic_dataset: CanonicalDataset


def materialize_pit_dataset(request: MaterializationRequest) -> MaterializationResult:
    """Construct a deterministic C08-S1 record without external side effects."""
    if type(request) is not MaterializationRequest:
        raise TypeError("request must be MaterializationRequest")
    if request.pit_cutoff.value > request.evaluation_time.value:
        raise PitMaterializationError("PIT_CUTOFF_AFTER_EVALUATION_TIME")
    _validate_policy_binding(request)
    reasons: set[str] = set()
    incompatible: set[str] = set()
    if request.coverage.requested_scope_ref != request.requested_claim_or_scope_ref:
        reasons.add("CANDIDATE_COVERAGE_SCOPE_NOT_ESTABLISHED")
    if not request.coverage.complete:
        reasons.add("CANDIDATE_SCOPE_COMPLETENESS_NOT_ESTABLISHED")
    supplied = {item.evidence_ref.key: item for item in request.evidence}
    candidate_keys = {item.key for item in request.coverage.candidate_evidence_refs}
    if set(supplied) != candidate_keys:
        reasons.add("CANDIDATE_POPULATION_NOT_ESTABLISHED")
    selected: list[MaterializationEvidence] = []
    for item in request.evidence:
        if item.knowledge_time is None:
            reasons.add("EVIDENCE_KNOWLEDGE_TIME_NOT_ESTABLISHED")
            continue
        if item.knowledge_time.value > request.pit_cutoff.value:
            continue
        if item.effective_from is not None and item.effective_from.value > request.effective_as_of.value:
            continue
        if item.effective_to is not None and request.effective_as_of.value >= item.effective_to.value:
            continue
        if not item.multiplicity_established:
            reasons.add("RECORD_MULTIPLICITY_NOT_ESTABLISHED")
        selected.append(item)
    canonical_records: dict[bytes, CanonicalRecord] = {}
    by_key: dict[bytes, bytes] = {}
    for item in selected:
        canonical_record, derived_key = _validated_record_and_key(item, request.dataset_schema)
        key = canonical_json([value.body() for value in derived_key])
        body = canonical_json(canonical_record.body())
        previous = by_key.get(key)
        if previous is not None and previous != body:
            incompatible.add("CONFLICTING_LOGICAL_KEY_CONTENT")
        by_key[key] = body
        canonical_records[body] = canonical_record
    records = tuple(canonical_records[key] for key in sorted(canonical_records))
    _historical_universe_state(request.historical_universe, reasons, incompatible)
    _entitlement_state(request, reasons, incompatible)
    _resource_state(request, selected, reasons)
    if incompatible:
        state = PitSufficiencyState.INCOMPATIBLE
    elif reasons:
        state = PitSufficiencyState.NOT_ESTABLISHED
    else:
        state = PitSufficiencyState.ESTABLISHED
    all_reasons = tuple(sorted(incompatible | reasons))
    if len(all_reasons) > request.resource_policy.value("MAX_REASONS_PER_RECORD"):
        raise PitMaterializationError("MAX_REASONS_PER_RECORD_EXHAUSTED")
    if any(len(reason) > request.resource_policy.value("MAX_REASON_LENGTH") for reason in all_reasons):
        raise PitMaterializationError("MAX_REASON_LENGTH_EXHAUSTED")
    dataset = CanonicalDataset(
        request.dataset_schema.schema_identity,
        request.dataset_schema.schema_version,
        request.dataset_schema.logical_key_fields,
        records if state is PitSufficiencyState.ESTABLISHED else (),
    )
    logical_id = _logical_content_id(request, dataset)
    dataset_id = _dataset_version_id(request, logical_id)
    selected_refs = tuple(sorted((item.evidence_ref for item in selected), key=EvidenceRef.canonical_bytes))
    return MaterializationResult(
        dataset_id,
        logical_id,
        _make_record(request, selected_refs, logical_id, state, all_reasons),
        dataset,
    )


def _validate_policy_binding(request: MaterializationRequest) -> None:
    if request.representation.representation_id != request.transformation.representation_id:
        raise PitMaterializationError("TRANSFORMATION_REPRESENTATION_SUBSTITUTION")
    if request.pit_cutoff != request.transformation.pit_cutoff:
        raise PitMaterializationError("TRANSFORMATION_CUTOFF_SUBSTITUTION")
    dependency_ids = tuple(sorted(item.dependency_id.value for item in request.dependency_refs))
    if dependency_ids != request.transformation.dependency_ids:
        raise PitMaterializationError("TRANSFORMATION_DEPENDENCY_SUBSTITUTION")
    supplied_inputs = tuple(sorted((_ref(item.evidence_ref) for item in request.evidence), key=lambda item: canonical_json(item)))
    bound_inputs = tuple(sorted((_ref(item) for item in request.transformation.input_evidence_refs), key=lambda item: canonical_json(item)))
    if supplied_inputs != bound_inputs:
        raise PitMaterializationError("TRANSFORMATION_INPUT_SUBSTITUTION")
    expected_dependency_set = _digest(
        "ATIS:C09:DEPENDENCY_SET:1", [item.body() for item in sorted(request.dependency_refs, key=lambda item: item.dependency_id.value)]
    )
    expected_id = DependencySetId(_derived("c09-dependencies", expected_dependency_set))
    if request.dependency_set_id != expected_id:
        raise PitMaterializationError("DEPENDENCY_SET_IDENTITY_CONTENT_CONFLICT")


def _validated_record_and_key(
    evidence: MaterializationEvidence,
    schema: DatasetSchemaDescriptor,
) -> tuple[CanonicalRecord, tuple[SemanticValue, ...]]:
    if evidence.record.schema_identity != schema.schema_identity:
        raise CanonicalRepresentationError("RECORD_SCHEMA_IDENTITY_CONFLICT")
    field_map = {name: value for name, value in evidence.record.fields}
    if set(field_map) != set(schema.field_names):
        raise CanonicalRepresentationError("SCHEMA_FIELD_SET_MISMATCH")
    canonical_record = CanonicalRecord(
        schema.schema_identity,
        tuple((name, field_map[name]) for name in schema.field_names),
    )
    derived_key = tuple(field_map[name] for name in schema.logical_key_fields)
    if len(evidence.logical_key) != len(schema.logical_key_fields):
        raise PitMaterializationError("LOGICAL_KEY_ARITY_MISMATCH")
    if evidence.logical_key != derived_key:
        raise PitMaterializationError("LOGICAL_KEY_RECORD_MISMATCH")
    return canonical_record, derived_key


def _historical_universe_state(
    value: HistoricalUniverseEvidence, reasons: set[str], incompatible: set[str]
) -> None:
    if value.uses_current_constituents:
        raise PitMaterializationError("CURRENT_CONSTITUENTS_NOT_HISTORICAL_PROOF")
    if not value.required:
        return
    if value.policy_ref is None or not value.evidence_refs:
        reasons.add("HISTORICAL_UNIVERSE_NOT_ESTABLISHED")
    elif not value.supported:
        incompatible.add("HISTORICAL_UNIVERSE_POLICY_INCOMPATIBLE")


def _entitlement_state(
    request: MaterializationRequest, reasons: set[str], incompatible: set[str]
) -> None:
    if not request.entitlement_material:
        return
    if not request.entitlements:
        reasons.add("ENTITLEMENT_PROVENANCE_NOT_ESTABLISHED")
    for item in request.entitlements:
        if item.state is EntitlementState.UNKNOWN:
            reasons.add("ENTITLEMENT_UNKNOWN")
        elif item.state is EntitlementState.INCOMPATIBLE:
            incompatible.add("ENTITLEMENT_INCOMPATIBLE")


def _resource_state(
    request: MaterializationRequest,
    selected: list[MaterializationEvidence],
    reasons: set[str],
) -> None:
    policy = request.resource_policy
    if len(selected) > policy.value("MAX_MATERIALIZATION_EVIDENCE_REFS"):
        reasons.add("MAX_MATERIALIZATION_EVIDENCE_REFS_EXHAUSTED")
    if len(request.dependency_refs) > policy.value("MAX_DEPENDENCIES_PER_DATASET_VERSION"):
        reasons.add("MAX_DEPENDENCIES_PER_DATASET_VERSION_EXHAUSTED")
    if len(request.semantic_policy_refs) > policy.value("MAX_POLICY_REFS"):
        reasons.add("MAX_POLICY_REFS_EXHAUSTED")


def _logical_content_id(request: MaterializationRequest, dataset: CanonicalDataset) -> LogicalContentId:
    body = {
        "canonical_dataset": dataset.body(),
        "canonical_representation_id": request.representation.representation_id.value,
        "logical_schema_identity": request.dataset_schema.schema_identity,
        "logical_schema_version": request.dataset_schema.schema_version,
        "representation_contract_content_digest": request.representation.content_digest.value,
        "representation_version": request.representation.version,
        "shared_contract_version": SHARED_CONTRACT_VERSION,
    }
    return LogicalContentId(_derived("c08-logical-content", _digest("ATIS:C08:LOGICAL_CONTENT:1", body)))


def _dataset_version_id(request: MaterializationRequest, logical_id: LogicalContentId) -> DatasetVersionId:
    body = {
        "canonical_representation_id": request.representation.representation_id.value,
        "dataset_schema_descriptor_content_digest": request.dataset_schema.content_digest.value,
        "dataset_schema_identity": request.dataset_schema.schema_identity,
        "dataset_schema_version": request.dataset_schema.schema_version,
        "dependency_set_id": request.dependency_set_id.value,
        "logical_content_id": logical_id.value,
        "pit_cutoff": _time(request.pit_cutoff),
        "representation_contract_content_digest": request.representation.content_digest.value,
        "semantic_construction_policy_refs": [_ref(item) for item in request.semantic_policy_refs],
        "shared_contract_version": SHARED_CONTRACT_VERSION,
        "transformation_execution_id": request.transformation.execution_id.value,
    }
    return DatasetVersionId(_derived("c08-dataset-version", _digest("ATIS:C08:DATASET_VERSION:1", body)))


def _make_record(
    request: MaterializationRequest,
    selected_refs: tuple[EvidenceRef, ...],
    logical_id: LogicalContentId,
    state: PitSufficiencyState,
    limitations: tuple[str, ...],
) -> MaterializationRecord:
    values: dict[str, object] = {
        "contract_version": SHARED_CONTRACT_VERSION,
        "requested_claim_or_scope_ref": request.requested_claim_or_scope_ref,
        "dataset_schema_ref": request.dataset_schema.schema_ref,
        "pit_cutoff": request.pit_cutoff,
        "evaluation_time": request.evaluation_time,
        "selected_evidence_refs": selected_refs,
        "dependency_set_id": request.dependency_set_id,
        "dependency_refs": request.dependency_refs,
        "historical_universe_policy_and_evidence_where_required": request.historical_universe,
        "transformation_id": request.transformation.transformation_id,
        "transformation_version_id": request.transformation.version_id,
        "transformation_implementation_id": request.transformation.implementation_id,
        "transformation_execution_id": request.transformation.execution_id,
        "transformation_parameters_digest": request.transformation.parameters_digest,
        "canonical_dataset_representation_id": request.representation.representation_id,
        "canonical_representation_contract_ref": request.representation.contract_ref,
        "resulting_logical_content_id": logical_id,
        "pit_sufficiency_state": state,
        "limitations": limitations,
        "entitlement_provenance_inputs_where_material": request.entitlements if request.entitlement_material else (),
        "evidence_refs": _refs(
            request.evidence_refs
            + (request.coverage.coverage_evidence_ref, request.dataset_schema.schema_ref),
            "materialization evidence",
        ),
        "resource_policy_id": request.resource_policy.policy_id,
    }
    provisional = MaterializationRecord.__new__(MaterializationRecord)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    body = _record_body(provisional, include_identity=False)
    digest = _digest("ATIS:C08:MATERIALIZATION_RECORD:1", body)
    values["content_digest"] = digest
    values["record_id"] = MaterializationRecordId(_derived("c08-materialization", digest))
    return MaterializationRecord(**values)  # type: ignore[arg-type]


def _record_body(value: MaterializationRecord, *, include_identity: bool) -> dict[str, object]:
    universe = value.historical_universe_policy_and_evidence_where_required
    body: dict[str, object] = {
        "canonical_dataset_representation_id": value.canonical_dataset_representation_id.value,
        "canonical_representation_contract_ref": _ref(value.canonical_representation_contract_ref),
        "contract_version": value.contract_version,
        "dataset_schema_ref": _ref(value.dataset_schema_ref),
        "dependency_refs": [item.body() for item in value.dependency_refs],
        "dependency_set_id": value.dependency_set_id.value,
        "entitlement_provenance_inputs_where_material": [
            _entitlement_body(item) for item in value.entitlement_provenance_inputs_where_material
        ],
        "evaluation_time": _time(value.evaluation_time),
        "evidence_refs": [_ref(item) for item in value.evidence_refs],
        "historical_universe_policy_and_evidence_where_required": {
            "evidence_refs": [_ref(item) for item in universe.evidence_refs],
            "policy_ref": None if universe.policy_ref is None else _ref(universe.policy_ref),
            "required": universe.required,
            "supported": universe.supported,
            "uses_current_constituents": universe.uses_current_constituents,
        },
        "limitations": list(value.limitations),
        "pit_cutoff": _time(value.pit_cutoff),
        "pit_sufficiency_state": value.pit_sufficiency_state.value,
        "requested_claim_or_scope_ref": _ref(value.requested_claim_or_scope_ref),
        "resource_policy_id": value.resource_policy_id.value,
        "resulting_logical_content_id": value.resulting_logical_content_id.value,
        "selected_evidence_refs": [_ref(item) for item in value.selected_evidence_refs],
        "transformation_execution_id": value.transformation_execution_id.value,
        "transformation_id": value.transformation_id.value,
        "transformation_implementation_id": value.transformation_implementation_id.value,
        "transformation_parameters_digest": value.transformation_parameters_digest.value,
        "transformation_version_id": value.transformation_version_id.value,
    }
    if include_identity:
        body["record_id"] = value.record_id.value
    return body


def _entitlement_body(item: EntitlementProvenance) -> dict[str, object]:
    return {
        "entitlement_id": item.entitlement_id.value,
        "evidence_ref": None if item.evidence_ref is None else _ref(item.evidence_ref),
        "independent_scope_ref": None if item.independent_scope_ref is None else _ref(item.independent_scope_ref),
        "predecessor_ref": None if item.predecessor_ref is None else _ref(item.predecessor_ref),
        "provider_or_source_id": item.provider_or_source_id,
        "reasons": list(item.reasons),
        "state": item.state.value,
    }

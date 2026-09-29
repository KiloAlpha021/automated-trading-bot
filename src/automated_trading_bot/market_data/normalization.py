"""Deterministic provider-neutral Stage-3 market-data normalization.

This module binds already interpreted semantic fields to exact C03 acquisition
evidence and an explicit normalization contract.  It does not interpret a
provider payload, assess quality, decide eligibility, materialize datasets, or
confer freshness, persistence, or trading authority.
"""

from dataclasses import dataclass, field
import json
import re
import unicodedata

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceRef,
    ValidationPolicyId,
    canonicalize_evidence_refs,
)
from automated_trading_bot.market_data.acquisition import (
    AcquisitionRecord,
    SourceInterpretationId,
    SourceOrderAuthority,
    TemporalCapability,
)


MAX_NORMALIZATION_FIELDS = 256
MAX_NORMALIZATION_INPUTS = 4096
NORMALIZATION_CONTRACT_FAMILY = "ATIS_C04_NORMALIZATION"

_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}", re.ASCII)


class NormalizationError(ValueError):
    """A normalization request cannot produce authoritative canonical output."""


class UnsupportedInterpretationError(NormalizationError):
    """The exact C03 interpretation is not supported by the contract."""


class MalformedNormalizationInputError(NormalizationError):
    """The interpreted semantic input violates its declared schema."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _required_nfc(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a str")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _evidence_mapping(value: EvidenceRef) -> dict[str, str]:
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _time_text(value: Timestamp | None) -> str | None:
    if value is None:
        return None
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class CanonicalField:
    """One immutable, provider-neutral semantic field."""

    name: str
    value: str | int | bool | None

    def __post_init__(self) -> None:
        if type(self.name) is not str or _FIELD_NAME.fullmatch(self.name) is None:
            raise ValueError("field name must be canonical lower snake case")
        if type(self.value) not in (str, int, bool, type(None)):
            raise TypeError("field value must be str, int, bool, or None")
        if type(self.value) is str:
            _required_nfc(self.value, "field value")


def _canonical_fields(values: tuple[CanonicalField, ...]) -> tuple[CanonicalField, ...]:
    if type(values) is not tuple:
        raise TypeError("fields must be a tuple")
    if not values:
        raise MalformedNormalizationInputError("fields must not be empty")
    if len(values) > MAX_NORMALIZATION_FIELDS:
        raise MalformedNormalizationInputError("fields exceed the resource limit")
    if any(type(value) is not CanonicalField for value in values):
        raise TypeError("fields must contain CanonicalField values")
    names = [value.name for value in values]
    if len(set(names)) != len(names):
        raise MalformedNormalizationInputError("duplicate semantic field")
    return tuple(sorted(values, key=lambda value: value.name))


@dataclass(frozen=True, slots=True)
class SupportedInterpretation:
    """Exact admitted semantic schema for one C03 interpretation identity."""

    interpretation_id: SourceInterpretationId
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.interpretation_id) is not SourceInterpretationId:
            raise TypeError("interpretation_id must be a SourceInterpretationId")
        required = self._field_names(self.required_fields, "required_fields")
        optional = self._field_names(self.optional_fields, "optional_fields", empty=True)
        if set(required) & set(optional):
            raise ValueError("required and optional fields must be disjoint")
        object.__setattr__(self, "required_fields", required)
        object.__setattr__(self, "optional_fields", optional)

    @staticmethod
    def _field_names(
        values: tuple[str, ...], name: str, *, empty: bool = False,
    ) -> tuple[str, ...]:
        if type(values) is not tuple:
            raise TypeError(f"{name} must be a tuple")
        if not values and not empty:
            raise ValueError(f"{name} must not be empty")
        if len(values) > MAX_NORMALIZATION_FIELDS:
            raise ValueError(f"{name} exceeds the resource limit")
        if any(type(value) is not str or _FIELD_NAME.fullmatch(value) is None for value in values):
            raise ValueError(f"{name} contains a noncanonical field name")
        if len(set(values)) != len(values):
            raise ValueError(f"{name} contains duplicates")
        return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class NormalizationContract:
    """Explicit policy, code, version, and interpretation binding for C04."""

    contract_version: ContractVersion
    validation_policy_id: ValidationPolicyId
    validation_policy_ref: EvidenceRef
    normalization_code_ref: EvidenceRef
    supported_interpretations: tuple[SupportedInterpretation, ...]
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if type(self.contract_version) is not ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")
        if self.contract_version.family != NORMALIZATION_CONTRACT_FAMILY:
            raise ValueError("unsupported normalization contract family")
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_policy_ref) is not EvidenceRef:
            raise TypeError("validation_policy_ref must be an EvidenceRef")
        if type(self.normalization_code_ref) is not EvidenceRef:
            raise TypeError("normalization_code_ref must be an EvidenceRef")
        if self.validation_policy_ref.key == self.normalization_code_ref.key:
            raise ValueError("policy and code identities must remain distinct")
        values = self.supported_interpretations
        if type(values) is not tuple:
            raise TypeError("supported_interpretations must be a tuple")
        if not values:
            raise ValueError("supported_interpretations must not be empty")
        if any(type(value) is not SupportedInterpretation for value in values):
            raise TypeError("supported_interpretations contains an invalid value")
        by_id = {value.interpretation_id: value for value in values}
        if len(by_id) != len(values):
            raise ValueError("supported_interpretations contains duplicate identities")
        canonical = tuple(sorted(values, key=lambda value: value.interpretation_id.value))
        object.__setattr__(self, "supported_interpretations", canonical)
        object.__setattr__(self, "content_digest", self._compute_content_digest())

    def schema_for(self, value: SourceInterpretationId) -> SupportedInterpretation:
        if type(value) is not SourceInterpretationId:
            raise TypeError("interpretation identity must be a SourceInterpretationId")
        for schema in self.supported_interpretations:
            if schema.interpretation_id == value:
                return schema
        raise UnsupportedInterpretationError(value.value)

    def _compute_content_digest(self) -> EvidenceContentDigest:
        mapping = {
            "contract_version": {
                "family": self.contract_version.family,
                "version": self.contract_version.version,
            },
            "normalization_code_ref": _evidence_mapping(self.normalization_code_ref),
            "record_kind": "ATIS_C04_NORMALIZATION_CONTRACT",
            "supported_interpretations": [
                {
                    "interpretation_id": value.interpretation_id.value,
                    "optional_fields": list(value.optional_fields),
                    "required_fields": list(value.required_fields),
                }
                for value in self.supported_interpretations
            ],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_policy_ref": _evidence_mapping(self.validation_policy_ref),
        }
        return EvidenceContentDigest.from_bytes(_canonical_json(mapping))


@dataclass(frozen=True, slots=True)
class NormalizationInput:
    """Already interpreted semantic input bound to one immutable C03 receipt."""

    acquisition: AcquisitionRecord
    fields: tuple[CanonicalField, ...]
    source_payload_ref: EvidenceRef

    def __post_init__(self) -> None:
        if type(self.acquisition) is not AcquisitionRecord:
            raise TypeError("acquisition must be an AcquisitionRecord")
        if type(self.source_payload_ref) is not EvidenceRef:
            raise TypeError("source_payload_ref must be an EvidenceRef")
        if self.source_payload_ref != self.acquisition.acquisition_evidence_ref:
            raise MalformedNormalizationInputError(
                "source payload evidence is not the exact acquisition evidence",
            )
        object.__setattr__(self, "fields", _canonical_fields(self.fields))


@dataclass(frozen=True, slots=True)
class CanonicalObservation:
    """Canonical C04 observation with provenance and temporal limits intact."""

    acquisition: AcquisitionRecord
    source_payload_ref: EvidenceRef
    fields: tuple[CanonicalField, ...]
    contract_version: ContractVersion
    normalization_contract_digest: EvidenceContentDigest
    validation_policy_id: ValidationPolicyId
    validation_policy_ref: EvidenceRef
    normalization_code_ref: EvidenceRef
    provenance_refs: tuple[EvidenceRef, ...]
    content_digest: EvidenceContentDigest

    @property
    def temporal_capabilities(self) -> tuple[TemporalCapability, ...]:
        return self.acquisition.temporal_capabilities

    @property
    def observation_time(self) -> Timestamp | None:
        return self.acquisition.observation_time

    @property
    def effective_time(self) -> Timestamp | None:
        return self.acquisition.effective_time

    @property
    def publication_time(self) -> Timestamp | None:
        return self.acquisition.publication_time

    @property
    def knowledge_time(self) -> Timestamp | None:
        return self.acquisition.knowledge_time

    @property
    def source_order_authority(self) -> SourceOrderAuthority:
        return self.acquisition.source_order_authority

    @property
    def source_sequence(self) -> int | None:
        return self.acquisition.source_sequence


def _field_mapping(value: CanonicalField) -> dict[str, str | int | bool | None]:
    return {"name": value.name, "value": value.value}


def _observation_mapping(
    value: NormalizationInput, contract: NormalizationContract,
) -> dict[str, object]:
    acquisition = value.acquisition
    provenance = canonicalize_evidence_refs(
        acquisition.evidence_refs
        + (contract.validation_policy_ref, contract.normalization_code_ref),
    )
    return {
        "acquisition_content_digest": acquisition.content_digest.value,
        "contract_version": {
            "family": contract.contract_version.family,
            "version": contract.contract_version.version,
        },
        "fields": [_field_mapping(item) for item in value.fields],
        "normalization_code_ref": _evidence_mapping(contract.normalization_code_ref),
        "normalization_contract_digest": contract.content_digest.value,
        "provenance_refs": [_evidence_mapping(item) for item in provenance],
        "record_kind": "ATIS_C04_CANONICAL_OBSERVATION",
        "source_dataset_id": acquisition.source_dataset_id.value,
        "source_id": acquisition.source_id.value,
        "source_interpretation_id": acquisition.source_interpretation_id.value,
        "source_order_authority": acquisition.source_order_authority.value,
        "source_payload_ref": _evidence_mapping(value.source_payload_ref),
        "source_sequence": acquisition.source_sequence,
        "temporal_capabilities": [item.value for item in acquisition.temporal_capabilities],
        "temporal_values": {
            "acquired_at": _time_text(acquisition.acquired_at),
            "effective_time": _time_text(acquisition.effective_time),
            "knowledge_time": _time_text(acquisition.knowledge_time),
            "observation_time": _time_text(acquisition.observation_time),
            "publication_time": _time_text(acquisition.publication_time),
        },
        "validation_policy_id": contract.validation_policy_id.value,
        "validation_policy_ref": _evidence_mapping(contract.validation_policy_ref),
    }


def normalize_observation(
    value: NormalizationInput, contract: NormalizationContract,
) -> CanonicalObservation:
    """Normalize one supported interpreted input without authority promotion."""

    if type(value) is not NormalizationInput:
        raise TypeError("value must be a NormalizationInput")
    if type(contract) is not NormalizationContract:
        raise TypeError("contract must be a NormalizationContract")
    schema = contract.schema_for(value.acquisition.source_interpretation_id)
    names = {item.name for item in value.fields}
    missing = set(schema.required_fields) - names
    unknown = names - set(schema.required_fields) - set(schema.optional_fields)
    if missing:
        raise MalformedNormalizationInputError(
            f"missing required fields: {','.join(sorted(missing))}",
        )
    if unknown:
        raise MalformedNormalizationInputError(
            f"unsupported fields: {','.join(sorted(unknown))}",
        )
    mapping = _observation_mapping(value, contract)
    provenance = canonicalize_evidence_refs(
        value.acquisition.evidence_refs
        + (contract.validation_policy_ref, contract.normalization_code_ref),
    )
    return CanonicalObservation(
        acquisition=value.acquisition,
        source_payload_ref=value.source_payload_ref,
        fields=value.fields,
        contract_version=contract.contract_version,
        normalization_contract_digest=contract.content_digest,
        validation_policy_id=contract.validation_policy_id,
        validation_policy_ref=contract.validation_policy_ref,
        normalization_code_ref=contract.normalization_code_ref,
        provenance_refs=provenance,
        content_digest=EvidenceContentDigest.from_bytes(_canonical_json(mapping)),
    )


def normalize_observations(
    values: tuple[NormalizationInput, ...], contract: NormalizationContract,
) -> tuple[CanonicalObservation, ...]:
    """Normalize one homogeneous bounded cohort with explicit order semantics."""

    if type(values) is not tuple:
        raise TypeError("values must be a tuple")
    if not values:
        raise MalformedNormalizationInputError("values must not be empty")
    if len(values) > MAX_NORMALIZATION_INPUTS:
        raise MalformedNormalizationInputError("values exceed the resource limit")
    if any(type(value) is not NormalizationInput for value in values):
        raise TypeError("values must contain NormalizationInput values")
    first = values[0].acquisition
    cohort_key = (
        first.source_id,
        first.source_dataset_id,
        first.source_interpretation_id,
        first.source_order_authority,
    )
    if any(
        (
            value.acquisition.source_id,
            value.acquisition.source_dataset_id,
            value.acquisition.source_interpretation_id,
            value.acquisition.source_order_authority,
        )
        != cohort_key
        for value in values
    ):
        raise MalformedNormalizationInputError("normalization cohort is not homogeneous")
    normalized = tuple(normalize_observation(value, contract) for value in values)
    if first.source_order_authority is SourceOrderAuthority.AUTHORITATIVE:
        sequences = [value.source_sequence for value in normalized]
        if len(set(sequences)) != len(sequences):
            raise MalformedNormalizationInputError("authoritative source sequence is ambiguous")
        return tuple(sorted(normalized, key=lambda value: value.source_sequence))  # type: ignore[arg-type,return-value]
    return tuple(
        sorted(
            normalized,
            key=lambda value: (
                value.content_digest.value,
                value.acquisition.acquisition_id.to_string(),
            ),
        )
    )


def verify_normalization_content_digest(value: CanonicalObservation) -> None:
    """Recompute canonical content identity and reject any mismatch."""

    if type(value) is not CanonicalObservation:
        raise TypeError("value must be a CanonicalObservation")
    request = NormalizationInput(
        acquisition=value.acquisition,
        fields=value.fields,
        source_payload_ref=value.source_payload_ref,
    )
    mapping = {
        **_observation_mapping(
            request,
            NormalizationContract(
                contract_version=value.contract_version,
                validation_policy_id=value.validation_policy_id,
                validation_policy_ref=value.validation_policy_ref,
                normalization_code_ref=value.normalization_code_ref,
                supported_interpretations=(
                    SupportedInterpretation(
                        interpretation_id=value.acquisition.source_interpretation_id,
                        required_fields=tuple(field.name for field in value.fields),
                    ),
                ),
            ),
        ),
        "normalization_contract_digest": value.normalization_contract_digest.value,
    }
    if EvidenceContentDigest.from_bytes(_canonical_json(mapping)) != value.content_digest:
        raise ValueError("content_digest does not match canonical observation content")

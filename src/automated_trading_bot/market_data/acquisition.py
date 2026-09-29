"""Provider-neutral Stage-3 acquisition and source evidence contracts.

The values in this module attribute immutable acquisition facts. They do not
parse provider payloads, normalize observations, validate market data, select
providers, implement fallback, or confer downstream eligibility.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
import unicodedata
from uuid import UUID

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceRef,
    SourceId,
    canonicalize_evidence_refs,
)


MAX_ACQUISITION_EVIDENCE_REFS = 64
MAX_ENTITLEMENT_EVIDENCE_REFS = 64

_AUTHORITY = r"[a-z0-9](?:[a-z0-9]|[.-](?=[a-z0-9])){0,62}"
_LOCAL = r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,190}"
_INTERPRETATION = re.compile(fr"interpretation:({_AUTHORITY})/({_LOCAL})", re.ASCII)


def _require_uuid4(value: object) -> None:
    if type(value) is not UUID:
        raise TypeError("value must be a UUID")
    if value.int == 0 or value.version != 4:
        raise ValueError("value must be a non-nil RFC UUIDv4")


def _required_text(value: object, name: str, *, maximum: int = 255) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a str")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


@dataclass(frozen=True, slots=True)
class AcquisitionId:
    """Stable identity for one immutable ATIS acquisition receipt."""

    value: UUID
    _PREFIX = "atis:acquisition:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> "AcquisitionId":
        if type(value) is not str or not value.startswith(cls._PREFIX):
            raise ValueError("value has the wrong or missing acquisition namespace")
        try:
            parsed = UUID(value[len(cls._PREFIX) :])
        except (ValueError, AttributeError) as error:
            raise ValueError("value contains an invalid acquisition UUID") from error
        result = cls(parsed)
        if value != result.to_string():
            raise ValueError("value is not in canonical form")
        return result

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


@dataclass(frozen=True, slots=True)
class ProviderNativeId:
    """Opaque provider-native identity that never becomes an ATIS identity."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _required_text(self.value, "provider_native_id"))


@dataclass(frozen=True, slots=True)
class SourceInterpretationId:
    """Exact source schema/interpretation identity used before normalization."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if len(self.value) > 255 or _INTERPRETATION.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical interpretation identifier")


class TemporalCapability(StrEnum):
    OBSERVATION_TIME = "OBSERVATION_TIME"
    EFFECTIVE_TIME = "EFFECTIVE_TIME"
    PUBLICATION_TIME = "PUBLICATION_TIME"
    KNOWLEDGE_TIME = "KNOWLEDGE_TIME"
    ACQUISITION_TIME = "ACQUISITION_TIME"


class SourceOrderAuthority(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    NON_AUTHORITATIVE = "NON_AUTHORITATIVE"


def _timestamp(value: object, name: str, *, nullable: bool = False) -> Timestamp | None:
    if value is None and nullable:
        return None
    if type(value) is not Timestamp:
        suffix = " or None" if nullable else ""
        raise TypeError(f"{name} must be a Timestamp{suffix}")
    return value


def _time_text(value: Timestamp | None) -> str | None:
    if value is None:
        return None
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _evidence_mapping(value: EvidenceRef) -> dict[str, str]:
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _canonical_capabilities(
    values: tuple[TemporalCapability, ...],
) -> tuple[TemporalCapability, ...]:
    if type(values) is not tuple:
        raise TypeError("temporal_capabilities must be a tuple")
    if not values:
        raise ValueError("temporal_capabilities must not be empty")
    if any(type(value) is not TemporalCapability for value in values):
        raise TypeError("temporal_capabilities must contain TemporalCapability values")
    if len(set(values)) != len(values):
        raise ValueError("temporal_capabilities must not contain duplicates")
    if TemporalCapability.ACQUISITION_TIME not in values:
        raise ValueError("ACQUISITION_TIME capability is required")
    return tuple(sorted(values, key=lambda value: value.value))


@dataclass(frozen=True, slots=True)
class AcquisitionRecord:
    """One bounded, attributable, provider-neutral acquisition receipt."""

    acquisition_id: AcquisitionId
    source_id: SourceId
    source_dataset_id: DatasetId
    provider_native_source_id: ProviderNativeId
    provider_native_dataset_id: ProviderNativeId
    source_interpretation_id: SourceInterpretationId
    temporal_capabilities: tuple[TemporalCapability, ...]
    acquired_at: Timestamp
    source_order_authority: SourceOrderAuthority
    acquisition_evidence_ref: EvidenceRef
    evidence_refs: tuple[EvidenceRef, ...]
    provider_native_record_id: ProviderNativeId | None = None
    observation_time: Timestamp | None = None
    effective_time: Timestamp | None = None
    publication_time: Timestamp | None = None
    publication_time_evidence_ref: EvidenceRef | None = None
    knowledge_time: Timestamp | None = None
    knowledge_time_evidence_ref: EvidenceRef | None = None
    source_sequence: int | None = None
    entitlement_evidence_refs: tuple[EvidenceRef, ...] = ()
    content_digest: EvidenceContentDigest = field(init=False)

    def __post_init__(self) -> None:
        self._validate_identities()
        capabilities = _canonical_capabilities(self.temporal_capabilities)
        object.__setattr__(self, "temporal_capabilities", capabilities)
        _timestamp(self.acquired_at, "acquired_at")
        self._validate_temporal_values(capabilities)
        self._validate_ordering()
        evidence = canonicalize_evidence_refs(self.evidence_refs)
        if len(evidence) > MAX_ACQUISITION_EVIDENCE_REFS:
            raise ValueError("acquisition evidence exceeds the resource limit")
        object.__setattr__(self, "evidence_refs", evidence)
        self._validate_evidence(evidence)
        entitlement = self._canonical_entitlement_evidence()
        object.__setattr__(self, "entitlement_evidence_refs", entitlement)
        object.__setattr__(self, "content_digest", self._compute_content_digest())

    def _validate_identities(self) -> None:
        expected = (
            ("acquisition_id", AcquisitionId),
            ("source_id", SourceId),
            ("source_dataset_id", DatasetId),
            ("provider_native_source_id", ProviderNativeId),
            ("provider_native_dataset_id", ProviderNativeId),
            ("source_interpretation_id", SourceInterpretationId),
            ("source_order_authority", SourceOrderAuthority),
            ("acquisition_evidence_ref", EvidenceRef),
        )
        for name, kind in expected:
            if type(getattr(self, name)) is not kind:
                raise TypeError(f"{name} must be a {kind.__name__}")
        if self.provider_native_record_id is not None and type(self.provider_native_record_id) is not ProviderNativeId:
            raise TypeError("provider_native_record_id must be a ProviderNativeId or None")

    def _validate_temporal_values(
        self, capabilities: tuple[TemporalCapability, ...],
    ) -> None:
        temporal_fields = (
            ("observation_time", TemporalCapability.OBSERVATION_TIME),
            ("effective_time", TemporalCapability.EFFECTIVE_TIME),
            ("publication_time", TemporalCapability.PUBLICATION_TIME),
            ("knowledge_time", TemporalCapability.KNOWLEDGE_TIME),
        )
        for name, capability in temporal_fields:
            value = _timestamp(getattr(self, name), name, nullable=True)
            if value is not None and capability not in capabilities:
                raise ValueError(f"{name} is present without its declared temporal capability")
        self._validate_attributed_time(
            "publication_time", self.publication_time, self.publication_time_evidence_ref,
        )
        self._validate_attributed_time(
            "knowledge_time", self.knowledge_time, self.knowledge_time_evidence_ref,
        )

    def _validate_attributed_time(
        self, name: str, value: Timestamp | None, evidence: EvidenceRef | None,
    ) -> None:
        if value is None and evidence is not None:
            raise ValueError(f"{name}_evidence_ref is present without {name}")
        if value is not None and type(evidence) is not EvidenceRef:
            raise ValueError(f"{name} requires independent attributable evidence")
        if evidence is not None and evidence == self.acquisition_evidence_ref:
            raise ValueError("ACQUISITION_TIME_SUBSTITUTION")

    def _validate_ordering(self) -> None:
        if self.source_order_authority is SourceOrderAuthority.AUTHORITATIVE:
            if type(self.source_sequence) is not int or self.source_sequence < 0:
                raise ValueError("authoritative source order requires a nonnegative sequence")
        elif self.source_sequence is not None:
            raise ValueError("non-authoritative source order cannot carry an authority sequence")

    def _validate_evidence(self, evidence: tuple[EvidenceRef, ...]) -> None:
        if self.acquisition_evidence_ref not in evidence:
            raise ValueError("acquisition evidence is missing from evidence_refs")
        for item in evidence:
            if item.source_id != self.source_id or item.dataset_id != self.source_dataset_id:
                raise ValueError("evidence does not match the exact source and source dataset")
        for name in ("publication_time_evidence_ref", "knowledge_time_evidence_ref"):
            item = getattr(self, name)
            if item is not None and item not in evidence:
                raise ValueError(f"{name} is missing from evidence_refs")

    def _canonical_entitlement_evidence(self) -> tuple[EvidenceRef, ...]:
        values = self.entitlement_evidence_refs
        if type(values) is not tuple:
            raise TypeError("entitlement_evidence_refs must be a tuple")
        if not values:
            return ()
        if len(values) > MAX_ENTITLEMENT_EVIDENCE_REFS:
            raise ValueError("entitlement evidence exceeds the resource limit")
        return canonicalize_evidence_refs(values)

    def _compute_content_digest(self) -> EvidenceContentDigest:
        mapping = {
            "record_kind": "PROVIDER_NEUTRAL_ACQUISITION",
            "source_id": self.source_id.value,
            "source_dataset_id": self.source_dataset_id.value,
            "provider_native_source_id": self.provider_native_source_id.value,
            "provider_native_dataset_id": self.provider_native_dataset_id.value,
            "provider_native_record_id": (
                None if self.provider_native_record_id is None else self.provider_native_record_id.value
            ),
            "source_interpretation_id": self.source_interpretation_id.value,
            "temporal_capabilities": [value.value for value in self.temporal_capabilities],
            "acquired_at": _time_text(self.acquired_at),
            "observation_time": _time_text(self.observation_time),
            "effective_time": _time_text(self.effective_time),
            "publication_time": _time_text(self.publication_time),
            "knowledge_time": _time_text(self.knowledge_time),
            "source_order_authority": self.source_order_authority.value,
            "source_sequence": self.source_sequence,
            "acquisition_evidence_ref": _evidence_mapping(self.acquisition_evidence_ref),
            "publication_time_evidence_ref": (
                None if self.publication_time_evidence_ref is None
                else _evidence_mapping(self.publication_time_evidence_ref)
            ),
            "knowledge_time_evidence_ref": (
                None if self.knowledge_time_evidence_ref is None
                else _evidence_mapping(self.knowledge_time_evidence_ref)
            ),
            "evidence_refs": [_evidence_mapping(value) for value in self.evidence_refs],
            "entitlement_evidence_refs": [
                _evidence_mapping(value) for value in self.entitlement_evidence_refs
            ],
        }
        return EvidenceContentDigest.from_bytes(_canonical_json(mapping))

    @property
    def has_attributable_knowledge_time(self) -> bool:
        return self.knowledge_time is not None


def verify_acquisition_content_digest(
    value: AcquisitionRecord, claimed: EvidenceContentDigest,
) -> None:
    if type(value) is not AcquisitionRecord:
        raise TypeError("value must be an AcquisitionRecord")
    if type(claimed) is not EvidenceContentDigest:
        raise TypeError("claimed must be an EvidenceContentDigest")
    if value.content_digest != claimed:
        raise ValueError("content_digest does not match canonical acquisition content")

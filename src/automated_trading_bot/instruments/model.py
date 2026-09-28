"""Pure RB-1 Slice-1 identity, reference-version and lineage contracts.

These immutable values record attributable reference facts.  They provide no
point-in-time adjudication, persistence, provider integration or trading
authority.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping, TypeAlias
import unicodedata
from uuid import UUID

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp


MAX_EVIDENCE_REFS = 64
MAX_LINEAGE_VERSIONS = 4096
MAX_LINEAGE_DEPTH = 256


class EvidenceIdentityConflict(ValueError):
    """One attributable evidence key was reused with different content."""


class LineageValidationError(ValueError):
    """A complete candidate lineage violates the protected contract."""


def _require_uuid4(value: object) -> None:
    if type(value) is not UUID:
        raise TypeError("value must be a UUID")
    if value.int == 0 or value.version != 4:
        raise ValueError("value must be a non-nil RFC UUIDv4")


def _parse_uuid4(value: object, prefix: str) -> UUID:
    if type(value) is not str:
        raise TypeError("value must be a str")
    if not value.startswith(prefix):
        raise ValueError("value has the wrong or missing identity namespace")
    token = value[len(prefix):]
    try:
        parsed = UUID(token)
    except (ValueError, AttributeError) as error:
        raise ValueError("value contains an invalid UUID") from error
    _require_uuid4(parsed)
    if value != f"{prefix}{parsed}":
        raise ValueError("value is not in canonical form")
    return parsed


@dataclass(frozen=True, slots=True)
class ListingId:
    value: UUID
    _PREFIX = "atis:listing:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> "ListingId":
        return cls(_parse_uuid4(value, cls._PREFIX))

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


@dataclass(frozen=True, slots=True)
class ReferenceVersionId:
    value: UUID
    _PREFIX = "atis:reference-version:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> "ReferenceVersionId":
        return cls(_parse_uuid4(value, cls._PREFIX))

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


_AUTHORITY = r"[a-z0-9](?:[a-z0-9]|[.-](?=[a-z0-9])){0,62}"
_LOCAL = r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,190}"


def _validate_attributable_id(value: object, kind: str) -> None:
    if type(value) is not str:
        raise TypeError("value must be a str")
    if len(value) > 255 or re.fullmatch(fr"{kind}:({_AUTHORITY})/({_LOCAL})", value, re.ASCII) is None:
        raise ValueError(f"value must be a canonical {kind} identifier")


@dataclass(frozen=True, slots=True)
class SourceId:
    value: str

    def __post_init__(self) -> None:
        _validate_attributable_id(self.value, "source")

    def to_string(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DatasetId:
    value: str

    def __post_init__(self) -> None:
        _validate_attributable_id(self.value, "dataset")

    def to_string(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EvidenceId:
    value: str

    def __post_init__(self) -> None:
        _validate_attributable_id(self.value, "evidence")

    def to_string(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ValidationPolicyId:
    value: str

    def __post_init__(self) -> None:
        _validate_attributable_id(self.value, "validation-policy")

    def to_string(self) -> str:
        return self.value


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True, slots=True)
class EvidenceContentDigest:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if _DIGEST.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical SHA-256 digest")

    @classmethod
    def from_bytes(cls, content: bytes) -> "EvidenceContentDigest":
        if type(content) is not bytes:
            raise TypeError("content must be bytes")
        return cls(f"sha256:{sha256(content).hexdigest()}")


@dataclass(frozen=True, slots=True)
class ReferenceContentDigest:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if _DIGEST.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical SHA-256 digest")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_id: SourceId
    dataset_id: DatasetId
    evidence_id: EvidenceId
    content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        expected = (
            ("source_id", SourceId),
            ("dataset_id", DatasetId),
            ("evidence_id", EvidenceId),
            ("content_digest", EvidenceContentDigest),
        )
        for name, kind in expected:
            if type(getattr(self, name)) is not kind:
                raise TypeError(f"{name} must be a {kind.__name__}")

    @property
    def key(self) -> tuple[SourceId, DatasetId, EvidenceId]:
        return self.source_id, self.dataset_id, self.evidence_id

    def canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "content_digest": self.content_digest.value,
                "dataset_id": self.dataset_id.value,
                "evidence_id": self.evidence_id.value,
                "source_id": self.source_id.value,
            }
        )


def canonicalize_evidence_refs(values: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple:
        raise TypeError("evidence_refs must be a tuple")
    if not values:
        raise ValueError("evidence_refs must not be empty")
    if len(values) > MAX_EVIDENCE_REFS:
        raise ValueError("evidence_refs exceeds the limit of 64")
    by_key: dict[tuple[SourceId, DatasetId, EvidenceId], EvidenceRef] = {}
    for value in values:
        if type(value) is not EvidenceRef:
            raise TypeError("evidence_refs must contain EvidenceRef values")
        previous = by_key.get(value.key)
        if previous is not None and previous.content_digest != value.content_digest:
            raise EvidenceIdentityConflict("EVIDENCE_IDENTITY_CONFLICT")
        by_key[value.key] = value
    return tuple(sorted(by_key.values(), key=EvidenceRef.canonical_bytes))


class ValidationState(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    NOT_VALIDATED = "NOT_VALIDATED"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class TradabilityState(StrEnum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"
    DELISTED = "DELISTED"


def _semantic_text(value: object, name: str, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise TypeError(f"{name} must be a str or None")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _external_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise TypeError("external_identifiers must be a tuple")
    checked = tuple(_required_text(value, "external_identifier") for value in values)
    if len(set(checked)) != len(checked):
        raise ValueError("external_identifiers must be distinct")
    return tuple(sorted(checked))


def _required_text(value: object, name: str) -> str:
    checked = _semantic_text(value, name, nullable=False)
    assert checked is not None
    return checked


def _timestamp(value: object, name: str, *, nullable: bool = False) -> Timestamp | None:
    if value is None and nullable:
        return None
    if type(value) is not Timestamp:
        raise TypeError(f"{name} must be a Timestamp")
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
        "source_id": value.source_id.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "content_digest": value.content_digest.value,
    }


@dataclass(frozen=True, slots=True)
class InstrumentReferenceVersion:
    reference_version_id: ReferenceVersionId
    instrument_id: InstrumentId
    effective_from: Timestamp
    effective_to: Timestamp | None
    knowledge_from: Timestamp
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: ReferenceContentDigest = field(init=False)
    supersedes_version_id: ReferenceVersionId | None = None
    currency: str | None = None
    security_type: str | None = None
    external_identifiers: tuple[str, ...] = ()
    source_observed_at: Timestamp | None = None
    correction_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_common(self)
        object.__setattr__(self, "currency", _semantic_text(self.currency, "currency"))
        object.__setattr__(self, "security_type", _semantic_text(self.security_type, "security_type"))
        object.__setattr__(self, "correction_reason", _semantic_text(self.correction_reason, "correction_reason"))
        object.__setattr__(self, "external_identifiers", _external_ids(self.external_identifiers))
        _timestamp(self.source_observed_at, "source_observed_at", nullable=True)
        object.__setattr__(self, "content_digest", _content_digest(self))

    @classmethod
    def create(
        cls, *, reference_version_id: ReferenceVersionId,
        instrument_id: InstrumentId, effective_from: Timestamp,
        effective_to: Timestamp | None, knowledge_from: Timestamp,
        evidence_refs: tuple[EvidenceRef, ...],
        validation_policy_id: ValidationPolicyId,
        validation_state: ValidationState,
        supersedes_version_id: ReferenceVersionId | None = None,
        currency: str | None = None, security_type: str | None = None,
        external_identifiers: tuple[str, ...] = (),
        source_observed_at: Timestamp | None = None,
        correction_reason: str | None = None,
    ) -> "InstrumentReferenceVersion":
        return cls(
            reference_version_id=reference_version_id,
            instrument_id=instrument_id,
            effective_from=effective_from,
            effective_to=effective_to,
            knowledge_from=knowledge_from,
            evidence_refs=evidence_refs,
            validation_policy_id=validation_policy_id,
            validation_state=validation_state,
            supersedes_version_id=supersedes_version_id,
            currency=currency,
            security_type=security_type,
            external_identifiers=external_identifiers,
            source_observed_at=source_observed_at,
            correction_reason=correction_reason,
        )

    @property
    def is_authoritative(self) -> bool:
        return self.validation_state is ValidationState.VALID

    def contains_effective(self, instant: Timestamp) -> bool:
        _timestamp(instant, "instant")
        return self.effective_from.value <= instant.value and (
            self.effective_to is None or instant.value < self.effective_to.value
        )


@dataclass(frozen=True, slots=True)
class ListingReferenceVersion:
    reference_version_id: ReferenceVersionId
    instrument_id: InstrumentId
    listing_id: ListingId
    effective_from: Timestamp
    effective_to: Timestamp | None
    knowledge_from: Timestamp
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    tradability_state: TradabilityState
    tradability_reason: str
    content_digest: ReferenceContentDigest = field(init=False)
    supersedes_version_id: ReferenceVersionId | None = None
    symbol: str | None = None
    mic: str | None = None
    currency: str | None = None
    primary_listing_indicator: bool | None = None
    external_identifiers: tuple[str, ...] = ()
    source_observed_at: Timestamp | None = None
    correction_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_common(self)
        if type(self.listing_id) is not ListingId:
            raise TypeError("listing_id must be a ListingId")
        if type(self.tradability_state) is not TradabilityState:
            raise TypeError("tradability_state must be a TradabilityState")
        object.__setattr__(self, "tradability_reason", _required_text(
            self.tradability_reason, "tradability_reason",
        ))
        for name in ("symbol", "mic", "currency", "correction_reason"):
            object.__setattr__(self, name, _semantic_text(getattr(self, name), name))
        if self.primary_listing_indicator is not None and type(self.primary_listing_indicator) is not bool:
            raise TypeError("primary_listing_indicator must be a bool or None")
        object.__setattr__(self, "external_identifiers", _external_ids(self.external_identifiers))
        _timestamp(self.source_observed_at, "source_observed_at", nullable=True)
        object.__setattr__(self, "content_digest", _content_digest(self))

    @classmethod
    def create(
        cls, *, reference_version_id: ReferenceVersionId,
        instrument_id: InstrumentId, listing_id: ListingId,
        effective_from: Timestamp, effective_to: Timestamp | None,
        knowledge_from: Timestamp, evidence_refs: tuple[EvidenceRef, ...],
        validation_policy_id: ValidationPolicyId,
        validation_state: ValidationState,
        tradability_state: TradabilityState, tradability_reason: str,
        supersedes_version_id: ReferenceVersionId | None = None,
        symbol: str | None = None, mic: str | None = None,
        currency: str | None = None,
        primary_listing_indicator: bool | None = None,
        external_identifiers: tuple[str, ...] = (),
        source_observed_at: Timestamp | None = None,
        correction_reason: str | None = None,
    ) -> "ListingReferenceVersion":
        return cls(
            reference_version_id=reference_version_id,
            instrument_id=instrument_id,
            listing_id=listing_id,
            effective_from=effective_from,
            effective_to=effective_to,
            knowledge_from=knowledge_from,
            evidence_refs=evidence_refs,
            validation_policy_id=validation_policy_id,
            validation_state=validation_state,
            tradability_state=tradability_state,
            tradability_reason=tradability_reason,
            supersedes_version_id=supersedes_version_id,
            symbol=symbol,
            mic=mic,
            currency=currency,
            primary_listing_indicator=primary_listing_indicator,
            external_identifiers=external_identifiers,
            source_observed_at=source_observed_at,
            correction_reason=correction_reason,
        )

    @property
    def is_authoritative(self) -> bool:
        return self.validation_state is ValidationState.VALID

    def contains_effective(self, instant: Timestamp) -> bool:
        _timestamp(instant, "instant")
        return self.effective_from.value <= instant.value and (
            self.effective_to is None or instant.value < self.effective_to.value
        )


ReferenceVersion: TypeAlias = InstrumentReferenceVersion | ListingReferenceVersion


def _validate_common(value: ReferenceVersion) -> None:
    if type(value.reference_version_id) is not ReferenceVersionId:
        raise TypeError("reference_version_id must be a ReferenceVersionId")
    if type(value.instrument_id) is not InstrumentId:
        raise TypeError("instrument_id must be an InstrumentId")
    _timestamp(value.effective_from, "effective_from")
    _timestamp(value.effective_to, "effective_to", nullable=True)
    _timestamp(value.knowledge_from, "knowledge_from")
    if value.effective_to is not None and value.effective_to.value <= value.effective_from.value:
        raise ValueError("effective interval must have a positive duration")
    object.__setattr__(value, "evidence_refs", canonicalize_evidence_refs(value.evidence_refs))
    if type(value.validation_policy_id) is not ValidationPolicyId:
        raise TypeError("validation_policy_id must be a ValidationPolicyId")
    if type(value.validation_state) is not ValidationState:
        raise TypeError("validation_state must be a ValidationState")
    if value.supersedes_version_id is not None and type(value.supersedes_version_id) is not ReferenceVersionId:
        raise TypeError("supersedes_version_id must be a ReferenceVersionId or None")
    if value.supersedes_version_id == value.reference_version_id:
        raise LineageValidationError("SELF_SUPERSESSION")


def _semantic_mapping(value: ReferenceVersion) -> Mapping[str, object]:
    common: dict[str, object] = {
        "record_kind": "INSTRUMENT_REFERENCE_VERSION"
        if type(value) is InstrumentReferenceVersion else "LISTING_REFERENCE_VERSION",
        "instrument_id": value.instrument_id.to_string(),
        "effective_from": _time_text(value.effective_from),
        "effective_to": _time_text(value.effective_to),
        "knowledge_from": _time_text(value.knowledge_from),
        "evidence_refs": [_evidence_mapping(item) for item in value.evidence_refs],
        "validation_policy_id": value.validation_policy_id.value,
        "validation_state": value.validation_state.value,
        "supersedes_version_id": (
            None if value.supersedes_version_id is None
            else value.supersedes_version_id.to_string()
        ),
        "currency": value.currency,
        "external_identifiers": list(value.external_identifiers),
        "source_observed_at": _time_text(value.source_observed_at),
        "correction_reason": value.correction_reason,
    }
    if isinstance(value, InstrumentReferenceVersion):
        common["security_type"] = value.security_type
    else:
        common.update({
            "listing_id": value.listing_id.to_string(),
            "symbol": value.symbol,
            "mic": value.mic,
            "primary_listing_indicator": value.primary_listing_indicator,
            "tradability_state": value.tradability_state.value,
            "tradability_reason": value.tradability_reason,
        })
    return common


def _content_digest(value: ReferenceVersion) -> ReferenceContentDigest:
    return ReferenceContentDigest(f"sha256:{sha256(_canonical_json(_semantic_mapping(value))).hexdigest()}")


def verify_content_digest(value: ReferenceVersion, claimed: ReferenceContentDigest) -> None:
    if type(value) not in (InstrumentReferenceVersion, ListingReferenceVersion):
        raise TypeError("value must be a reference version")
    if type(claimed) is not ReferenceContentDigest:
        raise TypeError("claimed must be a ReferenceContentDigest")
    if claimed != _content_digest(value):
        raise ValueError("content_digest does not match canonical semantic content")


def validate_lineage(values: tuple[ReferenceVersion, ...]) -> tuple[ReferenceVersion, ...]:
    if type(values) is not tuple:
        raise TypeError("lineage must be a tuple")
    if not values:
        raise LineageValidationError("lineage must not be empty")
    if len(values) > MAX_LINEAGE_VERSIONS:
        raise LineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
    if any(type(item) not in (InstrumentReferenceVersion, ListingReferenceVersion) for item in values):
        raise TypeError("lineage contains an invalid reference-version type")

    by_id: dict[ReferenceVersionId, ReferenceVersion] = {}
    for item in values:
        if item.reference_version_id in by_id:
            raise LineageValidationError("DUPLICATE_REFERENCE_VERSION_ID")
        by_id[item.reference_version_id] = item

    kind = type(values[0])
    if any(type(item) is not kind for item in values):
        raise LineageValidationError("CROSS_KIND_EDGE")
    subject = _subject(values[0])
    if any(_subject(item) != subject for item in values):
        raise LineageValidationError("CROSS_SUBJECT_EDGE")

    successors: dict[ReferenceVersionId, ReferenceVersionId] = {}
    for item in values:
        predecessor_id = item.supersedes_version_id
        if predecessor_id is None:
            continue
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise LineageValidationError("MISSING_PREDECESSOR")
        if predecessor_id in successors:
            raise LineageValidationError("BRANCHING_SUCCESSOR_CONFLICT")
        successors[predecessor_id] = item.reference_version_id

    for start in by_id:
        seen: set[ReferenceVersionId] = set()
        current = start
        depth = 0
        while True:
            item = by_id[current]
            predecessor_id = item.supersedes_version_id
            if predecessor_id is None:
                break
            if predecessor_id in seen or predecessor_id == start:
                raise LineageValidationError("LINEAGE_CYCLE")
            seen.add(predecessor_id)
            depth += 1
            if depth > MAX_LINEAGE_DEPTH:
                raise LineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
            current = predecessor_id
    for item in values:
        predecessor_id = item.supersedes_version_id
        if predecessor_id is not None:
            predecessor = by_id[predecessor_id]
            if item.knowledge_from.value <= predecessor.knowledge_from.value:
                raise LineageValidationError("SUCCESSOR_KNOWLEDGE_NOT_GREATER")
    return tuple(sorted(values, key=lambda item: item.reference_version_id.to_string()))


def _subject(value: ReferenceVersion) -> tuple[object, ...]:
    if isinstance(value, InstrumentReferenceVersion):
        return (value.instrument_id,)
    return value.instrument_id, value.listing_id

"""Deterministic, storage-neutral point-in-time reference resolution.

Resolution consumes an explicit finite candidate set and separately attributable
coverage evidence.  Reference versions cannot certify that their own candidate
set is complete, and absence requires an additional negative-coverage record.
"""

from dataclasses import dataclass, field
from collections.abc import Set
from enum import StrEnum
from hashlib import sha256
import json
from typing import Generic, TypeVar, cast

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceRef,
    InstrumentReferenceVersion,
    LineageValidationError,
    ListingId,
    ListingReferenceVersion,
    MAX_LINEAGE_VERSIONS,
    ReferenceVersion,
    ReferenceVersionId,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
    validate_lineage,
)


class ResolutionDisposition(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    ABSENT = "ABSENT"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class ReferenceRecordKind(StrEnum):
    INSTRUMENT = "INSTRUMENT_REFERENCE_VERSION"
    LISTING = "LISTING_REFERENCE_VERSION"


def _timestamp_text(value: Timestamp) -> str:
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_bytes(value: object) -> bytes:
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
class CandidateSetCoverage:
    """Independent evidence that enumerates the complete bounded candidate set."""

    record_kind: ReferenceRecordKind
    instrument_id: InstrumentId
    listing_id: ListingId | None
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    candidate_version_ids: tuple[ReferenceVersionId, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.record_kind) is not ReferenceRecordKind:
            raise TypeError("record_kind must be a ReferenceRecordKind")
        if type(self.instrument_id) is not InstrumentId:
            raise TypeError("instrument_id must be an InstrumentId")
        if self.listing_id is not None and type(self.listing_id) is not ListingId:
            raise TypeError("listing_id must be a ListingId or None")
        if self.record_kind is ReferenceRecordKind.INSTRUMENT and self.listing_id is not None:
            raise ValueError("instrument coverage must not specify a listing_id")
        if self.record_kind is ReferenceRecordKind.LISTING and self.listing_id is None:
            raise ValueError("listing coverage must specify a listing_id")
        if type(self.effective_as_of) is not Timestamp or type(self.knowledge_cutoff) is not Timestamp:
            raise TypeError("coverage times must be Timestamp values")
        if type(self.candidate_version_ids) is not tuple:
            raise TypeError("candidate_version_ids must be a tuple")
        if len(self.candidate_version_ids) > MAX_LINEAGE_VERSIONS:
            raise ValueError("candidate_version_ids exceeds the lineage resource limit")
        if any(type(value) is not ReferenceVersionId for value in self.candidate_version_ids):
            raise TypeError("candidate_version_ids must contain ReferenceVersionId values")
        if len(set(self.candidate_version_ids)) != len(self.candidate_version_ids):
            raise ValueError("candidate_version_ids must be distinct")
        ordered_ids = tuple(sorted(self.candidate_version_ids, key=ReferenceVersionId.to_string))
        object.__setattr__(self, "candidate_version_ids", ordered_ids)
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_state) is not ValidationState:
            raise TypeError("validation_state must be a ValidationState")
        semantic = {
            "record_kind": self.record_kind.value,
            "instrument_id": self.instrument_id.to_string(),
            "listing_id": None if self.listing_id is None else self.listing_id.to_string(),
            "effective_as_of": _timestamp_text(self.effective_as_of),
            "knowledge_cutoff": _timestamp_text(self.knowledge_cutoff),
            "candidate_version_ids": [item.to_string() for item in ordered_ids],
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_state": self.validation_state.value,
        }
        object.__setattr__(self, "content_digest", f"sha256:{sha256(_canonical_bytes(semantic)).hexdigest()}")

    @property
    def is_authoritative(self) -> bool:
        return self.validation_state is ValidationState.VALID


@dataclass(frozen=True, slots=True)
class NegativeCoverageEvidence:
    """Independent evidence that no applicable version exists for one query."""

    record_kind: ReferenceRecordKind
    instrument_id: InstrumentId
    listing_id: ListingId | None
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    candidate_set_digest: str
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState

    def __post_init__(self) -> None:
        if type(self.record_kind) is not ReferenceRecordKind:
            raise TypeError("record_kind must be a ReferenceRecordKind")
        if type(self.instrument_id) is not InstrumentId:
            raise TypeError("instrument_id must be an InstrumentId")
        if self.listing_id is not None and type(self.listing_id) is not ListingId:
            raise TypeError("listing_id must be a ListingId or None")
        if self.record_kind is ReferenceRecordKind.INSTRUMENT and self.listing_id is not None:
            raise ValueError("instrument negative coverage must not specify a listing_id")
        if self.record_kind is ReferenceRecordKind.LISTING and self.listing_id is None:
            raise ValueError("listing negative coverage must specify a listing_id")
        if type(self.effective_as_of) is not Timestamp or type(self.knowledge_cutoff) is not Timestamp:
            raise TypeError("negative-coverage times must be Timestamp values")
        if type(self.candidate_set_digest) is not str or not self.candidate_set_digest.startswith("sha256:"):
            raise ValueError("candidate_set_digest must be a canonical SHA-256 digest")
        if len(self.candidate_set_digest) != 71 or any(
            character not in "0123456789abcdef" for character in self.candidate_set_digest[7:]
        ):
            raise ValueError("candidate_set_digest must be a canonical SHA-256 digest")
        object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_state) is not ValidationState:
            raise TypeError("validation_state must be a ValidationState")

    @property
    def is_authoritative(self) -> bool:
        return self.validation_state is ValidationState.VALID


T = TypeVar("T", bound=ReferenceVersion, covariant=True)


@dataclass(frozen=True, slots=True)
class ResolutionResult(Generic[T]):
    disposition: ResolutionDisposition
    value: T | None
    reason: str

    def __post_init__(self) -> None:
        if type(self.disposition) is not ResolutionDisposition:
            raise TypeError("disposition must be a ResolutionDisposition")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("reason must be a nonempty string")
        if (self.disposition is ResolutionDisposition.ESTABLISHED) != (self.value is not None):
            raise ValueError("only ESTABLISHED results may contain a value")


def resolve_instrument_reference(
    *,
    instrument_id: InstrumentId,
    effective_as_of: Timestamp,
    knowledge_cutoff: Timestamp,
    candidates: tuple[InstrumentReferenceVersion, ...],
    coverage: CandidateSetCoverage | None,
    negative_coverage: NegativeCoverageEvidence | None = None,
) -> ResolutionResult[InstrumentReferenceVersion]:
    result = _resolve(
        record_kind=ReferenceRecordKind.INSTRUMENT,
        instrument_id=instrument_id,
        listing_id=None,
        effective_as_of=effective_as_of,
        knowledge_cutoff=knowledge_cutoff,
        candidates=cast(tuple[ReferenceVersion, ...], candidates),
        coverage=coverage,
        negative_coverage=negative_coverage,
    )
    return cast(ResolutionResult[InstrumentReferenceVersion], result)


def resolve_listing_reference(
    *,
    instrument_id: InstrumentId,
    listing_id: ListingId,
    effective_as_of: Timestamp,
    knowledge_cutoff: Timestamp,
    candidates: tuple[ListingReferenceVersion, ...],
    coverage: CandidateSetCoverage | None,
    negative_coverage: NegativeCoverageEvidence | None = None,
) -> ResolutionResult[ListingReferenceVersion]:
    result = _resolve(
        record_kind=ReferenceRecordKind.LISTING,
        instrument_id=instrument_id,
        listing_id=listing_id,
        effective_as_of=effective_as_of,
        knowledge_cutoff=knowledge_cutoff,
        candidates=cast(tuple[ReferenceVersion, ...], candidates),
        coverage=coverage,
        negative_coverage=negative_coverage,
    )
    return cast(ResolutionResult[ListingReferenceVersion], result)


def _resolve(
    *,
    record_kind: ReferenceRecordKind,
    instrument_id: InstrumentId,
    listing_id: ListingId | None,
    effective_as_of: Timestamp,
    knowledge_cutoff: Timestamp,
    candidates: tuple[ReferenceVersion, ...],
    coverage: CandidateSetCoverage | None,
    negative_coverage: NegativeCoverageEvidence | None,
) -> ResolutionResult[ReferenceVersion]:
    if type(instrument_id) is not InstrumentId:
        raise TypeError("instrument_id must be an InstrumentId")
    if listing_id is not None and type(listing_id) is not ListingId:
        raise TypeError("listing_id must be a ListingId or None")
    if type(effective_as_of) is not Timestamp or type(knowledge_cutoff) is not Timestamp:
        raise TypeError("query times must be Timestamp values")
    if type(candidates) is not tuple:
        raise TypeError("candidates must be a tuple")
    if len(candidates) > MAX_LINEAGE_VERSIONS:
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_RESOURCE_LIMIT_EXCEEDED")

    expected_type: type[ReferenceVersion] = (
        InstrumentReferenceVersion
        if record_kind is ReferenceRecordKind.INSTRUMENT
        else ListingReferenceVersion
    )
    if any(type(value) is not expected_type for value in candidates):
        return _result(ResolutionDisposition.CONFLICTING, "WRONG_RECORD_KIND")
    if any(value.instrument_id != instrument_id for value in candidates):
        return _result(ResolutionDisposition.CONFLICTING, "WRONG_INSTRUMENT_SUBJECT")
    if record_kind is ReferenceRecordKind.LISTING and any(
        cast(ListingReferenceVersion, value).listing_id != listing_id for value in candidates
    ):
        return _result(ResolutionDisposition.CONFLICTING, "WRONG_LISTING_SUBJECT")
    candidate_ids = tuple(value.reference_version_id for value in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        return _result(ResolutionDisposition.CONFLICTING, "DUPLICATE_REFERENCE_VERSION_ID")

    if coverage is None:
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_SET_COVERAGE_MISSING")
    if not _coverage_matches(
        coverage,
        record_kind,
        instrument_id,
        listing_id,
        effective_as_of,
        knowledge_cutoff,
    ):
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_SET_COVERAGE_MISMATCH")
    if not coverage.is_authoritative:
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_SET_COVERAGE_NOT_AUTHORITATIVE")

    actual_ids = tuple(sorted(candidate_ids, key=ReferenceVersionId.to_string))
    if actual_ids != coverage.candidate_version_ids:
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_SET_INCOMPLETE")
    candidate_evidence = {item.key for value in candidates for item in value.evidence_refs}
    if candidate_evidence.intersection(item.key for item in coverage.evidence_refs):
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "CANDIDATE_SELF_CERTIFIED_COVERAGE")

    if candidates:
        try:
            validate_lineage(candidates)
        except (LineageValidationError, ValueError):
            return _result(ResolutionDisposition.CONFLICTING, "INVALID_OR_CONFLICTING_LINEAGE")

    qualifying = tuple(
        value
        for value in candidates
        if value.contains_effective(effective_as_of)
        and value.knowledge_from.value <= knowledge_cutoff.value
    )
    if not qualifying:
        if _negative_matches(
            negative_coverage,
            coverage,
            record_kind,
            instrument_id,
            listing_id,
            effective_as_of,
            knowledge_cutoff,
            candidate_evidence,
        ):
            return _result(ResolutionDisposition.ABSENT, "AUTHORITATIVE_NEGATIVE_COVERAGE")
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "NO_APPLICABLE_VERSION")

    if any(not value.is_authoritative for value in qualifying):
        return _result(ResolutionDisposition.NOT_ESTABLISHED, "QUALIFYING_VERSION_NOT_AUTHORITATIVE")

    superseded_ids = {
        value.supersedes_version_id
        for value in qualifying
        if value.supersedes_version_id is not None
    }
    unresolved = tuple(value for value in qualifying if value.reference_version_id not in superseded_ids)
    if len(unresolved) == 1:
        return ResolutionResult(ResolutionDisposition.ESTABLISHED, unresolved[0], "UNIQUE_AUTHORITATIVE_VERSION")
    if len({value.content_digest for value in unresolved}) > 1:
        return _result(ResolutionDisposition.CONFLICTING, "MATERIAL_QUALIFYING_CONFLICT")
    return _result(ResolutionDisposition.AMBIGUOUS, "MULTIPLE_PLAUSIBLE_VERSIONS")


def _coverage_matches(
    value: CandidateSetCoverage,
    record_kind: ReferenceRecordKind,
    instrument_id: InstrumentId,
    listing_id: ListingId | None,
    effective_as_of: Timestamp,
    knowledge_cutoff: Timestamp,
) -> bool:
    return (
        type(value) is CandidateSetCoverage
        and value.record_kind is record_kind
        and value.instrument_id == instrument_id
        and value.listing_id == listing_id
        and value.effective_as_of == effective_as_of
        and value.knowledge_cutoff == knowledge_cutoff
    )


def _negative_matches(
    value: NegativeCoverageEvidence | None,
    coverage: CandidateSetCoverage,
    record_kind: ReferenceRecordKind,
    instrument_id: InstrumentId,
    listing_id: ListingId | None,
    effective_as_of: Timestamp,
    knowledge_cutoff: Timestamp,
    candidate_evidence: Set[tuple[object, ...]],
) -> bool:
    if value is None or type(value) is not NegativeCoverageEvidence:
        return False
    return (
        value.is_authoritative
        and value.record_kind is record_kind
        and value.instrument_id == instrument_id
        and value.listing_id == listing_id
        and value.effective_as_of == effective_as_of
        and value.knowledge_cutoff == knowledge_cutoff
        and value.candidate_set_digest == coverage.content_digest
        and not any(item.key in candidate_evidence for item in value.evidence_refs)
    )


def _result(
    disposition: ResolutionDisposition,
    reason: str,
) -> ResolutionResult[ReferenceVersion]:
    return ResolutionResult(disposition, None, reason)

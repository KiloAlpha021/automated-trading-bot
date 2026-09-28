"""Deterministic point-in-time Calendar S2 resolution.

The resolver consumes a finite, explicitly complete, knowledge-visible domain.
It never consults a clock, host timezone, provider, database, or implicit latest
calendar.  All non-established results are deliberately value-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable, cast

from automated_trading_bot.calendars.model import (
    MAX_EVIDENCE_REFS,
    MAX_LINEAGE_VERSIONS,
    CalendarContentDigest,
    CalendarCurrentness,
    CalendarFactConflict,
    CalendarId,
    CalendarLineageValidationError,
    CalendarVersion,
    CalendarVersionId,
    HistoricalCoverage,
    SessionDefinition,
    SessionKind,
    TimezoneRuleId,
    TradingDayDefinition,
    TradingDayKind,
    coverage_disposition,
    validate_calendar_lineage,
    validate_fact_bindings,
    verify_calendar_content_digest,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceRef,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
)
from automated_trading_bot.instruments.resolution import ResolutionDisposition


def _timestamp_text(value: Timestamp | None) -> str | None:
    return None if value is None else value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _evidence_mapping(value: EvidenceRef) -> dict[str, str]:
    return {
        "source_id": value.source_id.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "content_digest": value.content_digest.value,
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> CalendarContentDigest:
    return CalendarContentDigest(f"sha256:{sha256(_canonical_bytes(value)).hexdigest()}")


def _evidence(values: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    return canonicalize_evidence_refs(values)


def _policy(policy: object, state: object) -> None:
    if type(policy) is not ValidationPolicyId:
        raise TypeError("validation_policy_id must be a ValidationPolicyId")
    if type(state) is not ValidationState:
        raise TypeError("validation_state must be a ValidationState")


def _calendar(value: object) -> CalendarId:
    if type(value) is not CalendarId:
        raise TypeError("calendar_id must be a CalendarId")
    return value


def _version_id(value: object, name: str = "calendar_version_id") -> CalendarVersionId:
    if type(value) is not CalendarVersionId:
        raise TypeError(f"{name} must be a CalendarVersionId")
    return value


def _time(value: object, name: str, *, optional: bool = False) -> Timestamp | None:
    if value is None and optional:
        return None
    if type(value) is not Timestamp:
        raise TypeError(f"{name} must be a Timestamp")
    return value


class CalendarCorrectionAuthorityDecision(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class CalendarNegativeFact(StrEnum):
    NO_SUPPORTED_SESSION = "NO_SUPPORTED_SESSION"


class CalendarSessionResolutionReason(StrEnum):
    UNIQUE_AUTHORITATIVE_SESSION = "UNIQUE_AUTHORITATIVE_SESSION"
    AUTHORITATIVE_NON_TRADING_DAY = "AUTHORITATIVE_NON_TRADING_DAY"
    AUTHORITATIVE_NEGATIVE_COVERAGE = "AUTHORITATIVE_NEGATIVE_COVERAGE"
    UNSUPPORTED_SESSION_KIND = "UNSUPPORTED_SESSION_KIND"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    EFFECTIVE_BOUNDARY_MISMATCH = "EFFECTIVE_BOUNDARY_MISMATCH"
    CANDIDATE_SET_COVERAGE_MISSING = "CANDIDATE_SET_COVERAGE_MISSING"
    CANDIDATE_SET_COVERAGE_MISMATCH = "CANDIDATE_SET_COVERAGE_MISMATCH"
    CANDIDATE_SET_COVERAGE_FUTURE = "CANDIDATE_SET_COVERAGE_FUTURE"
    CANDIDATE_SET_COVERAGE_NOT_AUTHORITATIVE = "CANDIDATE_SET_COVERAGE_NOT_AUTHORITATIVE"
    CANDIDATE_SET_INCOMPLETE = "CANDIDATE_SET_INCOMPLETE"
    CANDIDATE_SET_SURPLUS = "CANDIDATE_SET_SURPLUS"
    CANDIDATE_SELF_CERTIFIED_COVERAGE = "CANDIDATE_SELF_CERTIFIED_COVERAGE"
    WRONG_CALENDAR_SUBJECT = "WRONG_CALENDAR_SUBJECT"
    INVALID_OR_CONFLICTING_LINEAGE = "INVALID_OR_CONFLICTING_LINEAGE"
    NO_APPLICABLE_VERSION = "NO_APPLICABLE_VERSION"
    COVERAGE_NOT_ESTABLISHED = "COVERAGE_NOT_ESTABLISHED"
    COVERAGE_CONFLICT = "COVERAGE_CONFLICT"
    CORRECTION_AUTHORITY_MISSING = "CORRECTION_AUTHORITY_MISSING"
    CORRECTION_AUTHORITY_NOT_ESTABLISHED = "CORRECTION_AUTHORITY_NOT_ESTABLISHED"
    CORRECTION_AUTHORITY_SELF_CERTIFIED = "CORRECTION_AUTHORITY_SELF_CERTIFIED"
    CORRECTION_AUTHORITY_CONFLICT = "CORRECTION_AUTHORITY_CONFLICT"
    MULTIPLE_PLAUSIBLE_VERSIONS = "MULTIPLE_PLAUSIBLE_VERSIONS"
    MATERIAL_QUALIFYING_CONFLICT = "MATERIAL_QUALIFYING_CONFLICT"
    CURRENTNESS_ASSESSMENT_MISSING = "CURRENTNESS_ASSESSMENT_MISSING"
    CURRENTNESS_ASSESSMENT_NOT_AUTHORITATIVE = "CURRENTNESS_ASSESSMENT_NOT_AUTHORITATIVE"
    CURRENTNESS_ASSESSMENT_FUTURE = "CURRENTNESS_ASSESSMENT_FUTURE"
    CURRENTNESS_ASSESSMENT_OUTSIDE_VALIDITY = "CURRENTNESS_ASSESSMENT_OUTSIDE_VALIDITY"
    CALENDAR_VERSION_STALE = "CALENDAR_VERSION_STALE"
    CALENDAR_CURRENTNESS_UNKNOWN = "CALENDAR_CURRENTNESS_UNKNOWN"
    CURRENTNESS_ASSESSMENT_CONFLICT = "CURRENTNESS_ASSESSMENT_CONFLICT"
    TRADING_DAY_NOT_ESTABLISHED = "TRADING_DAY_NOT_ESTABLISHED"
    TRADING_DAY_CONFLICT = "TRADING_DAY_CONFLICT"
    SESSION_NOT_ESTABLISHED = "SESSION_NOT_ESTABLISHED"
    SESSION_CONFLICT = "SESSION_CONFLICT"
    POSITIVE_NEGATIVE_SESSION_CONFLICT = "POSITIVE_NEGATIVE_SESSION_CONFLICT"
    TIMEZONE_RULE_NOT_ESTABLISHED = "TIMEZONE_RULE_NOT_ESTABLISHED"
    TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH = "TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH"
    LOCAL_TIME_NOT_ESTABLISHED = "LOCAL_TIME_NOT_ESTABLISHED"
    UTC_BOUNDARY_CONFLICT = "UTC_BOUNDARY_CONFLICT"


@dataclass(frozen=True, slots=True)
class CalendarSessionResolutionQuery:
    calendar_id: CalendarId
    local_date: date
    session_kind: SessionKind
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    evaluation_at: Timestamp

    def __post_init__(self) -> None:
        _calendar(self.calendar_id)
        if type(self.local_date) is not date:
            raise TypeError("local_date must be a date")
        if type(self.session_kind) is not SessionKind:
            raise TypeError("session_kind must be a SessionKind")
        _time(self.effective_as_of, "effective_as_of")
        _time(self.knowledge_cutoff, "knowledge_cutoff")
        _time(self.evaluation_at, "evaluation_at")
        if self.knowledge_cutoff.value > self.evaluation_at.value:
            raise ValueError("knowledge_cutoff must not be after evaluation_at")


@dataclass(frozen=True, slots=True)
class CalendarCorrectionAuthorityAssessment:
    calendar_id: CalendarId
    successor_version_id: CalendarVersionId
    predecessor_version_id: CalendarVersionId
    knowledge_from: Timestamp
    evaluated_at: Timestamp
    authority_policy_id: ValidationPolicyId
    decision: CalendarCorrectionAuthorityDecision
    evidence_refs: tuple[EvidenceRef, ...]
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _calendar(self.calendar_id)
        _version_id(self.successor_version_id, "successor_version_id")
        _version_id(self.predecessor_version_id, "predecessor_version_id")
        if self.successor_version_id == self.predecessor_version_id:
            raise ValueError("correction assessment cannot bind a self-supersession")
        _time(self.knowledge_from, "knowledge_from")
        _time(self.evaluated_at, "evaluated_at")
        if type(self.decision) is not CalendarCorrectionAuthorityDecision:
            raise TypeError("decision must be a CalendarCorrectionAuthorityDecision")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _policy(self.authority_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest({
            "record_kind": "CALENDAR_CORRECTION_AUTHORITY_ASSESSMENT",
            "calendar_id": self.calendar_id.to_string(),
            "successor_version_id": self.successor_version_id.to_string(),
            "predecessor_version_id": self.predecessor_version_id.to_string(),
            "knowledge_from": _timestamp_text(self.knowledge_from),
            "evaluated_at": _timestamp_text(self.evaluated_at),
            "authority_policy_id": self.authority_policy_id.value,
            "decision": self.decision.value,
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_state": self.validation_state.value,
        }))


@dataclass(frozen=True, slots=True)
class CalendarCurrentnessAssessment:
    calendar_id: CalendarId
    calendar_version_id: CalendarVersionId
    knowledge_from: Timestamp
    evaluation_at: Timestamp
    valid_from: Timestamp
    valid_to: Timestamp | None
    state: CalendarCurrentness
    validation_policy_id: ValidationPolicyId
    evidence_refs: tuple[EvidenceRef, ...]
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _calendar(self.calendar_id)
        _version_id(self.calendar_version_id)
        _time(self.knowledge_from, "knowledge_from")
        _time(self.evaluation_at, "evaluation_at")
        _time(self.valid_from, "valid_from")
        _time(self.valid_to, "valid_to", optional=True)
        if self.valid_to is not None and self.valid_to.value <= self.valid_from.value:
            raise ValueError("currentness validity interval must have positive duration")
        if type(self.state) is not CalendarCurrentness:
            raise TypeError("state must be a CalendarCurrentness")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest({
            "record_kind": "CALENDAR_CURRENTNESS_ASSESSMENT",
            "calendar_id": self.calendar_id.to_string(),
            "calendar_version_id": self.calendar_version_id.to_string(),
            "knowledge_from": _timestamp_text(self.knowledge_from),
            "evaluation_at": _timestamp_text(self.evaluation_at),
            "valid_from": _timestamp_text(self.valid_from),
            "valid_to": _timestamp_text(self.valid_to),
            "state": self.state.value,
            "validation_policy_id": self.validation_policy_id.value,
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_state": self.validation_state.value,
        }))

    def applies(self, query: CalendarSessionResolutionQuery) -> bool:
        return (
            self.knowledge_from.value <= query.knowledge_cutoff.value
            and self.valid_from.value <= query.evaluation_at.value
            and (self.valid_to is None or query.evaluation_at.value < self.valid_to.value)
        )


@dataclass(frozen=True, slots=True)
class CalendarFactDescriptor:
    fact_kind: str
    calendar_version_id: CalendarVersionId
    date_or_range_identity: str
    session_kind: SessionKind | None
    fact_content_digest: CalendarContentDigest

    def __post_init__(self) -> None:
        if self.fact_kind not in {"HISTORICAL_COVERAGE", "TRADING_DAY", "SESSION"}:
            raise ValueError("fact_kind is unsupported")
        _version_id(self.calendar_version_id)
        if type(self.date_or_range_identity) is not str or not self.date_or_range_identity:
            raise ValueError("date_or_range_identity must be a nonempty str")
        if self.session_kind is not None and type(self.session_kind) is not SessionKind:
            raise TypeError("session_kind must be a SessionKind or None")
        if (self.fact_kind == "SESSION") != (self.session_kind is not None):
            raise ValueError("only SESSION descriptors bind session_kind")
        if type(self.fact_content_digest) is not CalendarContentDigest:
            raise TypeError("fact_content_digest must be a CalendarContentDigest")

    @classmethod
    def from_fact(cls, value: HistoricalCoverage | TradingDayDefinition | SessionDefinition) -> CalendarFactDescriptor:
        if type(value) is HistoricalCoverage:
            return cls(
                "HISTORICAL_COVERAGE", value.calendar_version_id,
                f"{value.local_date_from.isoformat()}/{value.local_date_to.isoformat()}",
                None, value.content_digest,
            )
        if type(value) is TradingDayDefinition:
            return cls("TRADING_DAY", value.calendar_version_id, value.local_date.isoformat(), None, value.content_digest)
        if type(value) is SessionDefinition:
            return cls("SESSION", value.calendar_version_id, value.local_date.isoformat(), value.session_kind, value.content_digest)
        raise TypeError("value must be a Calendar S1 fact")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "fact_kind": self.fact_kind,
            "calendar_version_id": self.calendar_version_id.to_string(),
            "date_or_range_identity": self.date_or_range_identity,
            "session_kind": None if self.session_kind is None else self.session_kind.value,
            "fact_content_digest": self.fact_content_digest.value,
        }


@dataclass(frozen=True, slots=True)
class CalendarCandidateSetCoverage:
    calendar_id: CalendarId
    local_date: date
    session_kind: SessionKind
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    evaluation_at: Timestamp
    knowledge_from: Timestamp
    candidate_version_ids: tuple[CalendarVersionId, ...]
    candidate_fact_descriptors: tuple[CalendarFactDescriptor, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _calendar(self.calendar_id)
        if type(self.local_date) is not date:
            raise TypeError("local_date must be a date")
        if type(self.session_kind) is not SessionKind:
            raise TypeError("session_kind must be a SessionKind")
        for name in ("effective_as_of", "knowledge_cutoff", "evaluation_at", "knowledge_from"):
            _time(getattr(self, name), name)
        if type(self.candidate_version_ids) is not tuple or type(self.candidate_fact_descriptors) is not tuple:
            raise TypeError("candidate identities and descriptors must be tuples")
        if len(self.candidate_version_ids) > MAX_LINEAGE_VERSIONS or len(self.candidate_fact_descriptors) > MAX_LINEAGE_VERSIONS:
            raise ValueError("candidate-set resource limit exceeded")
        if any(type(item) is not CalendarVersionId for item in self.candidate_version_ids):
            raise TypeError("candidate_version_ids must contain CalendarVersionId values")
        if any(type(item) is not CalendarFactDescriptor for item in self.candidate_fact_descriptors):
            raise TypeError("candidate_fact_descriptors must contain CalendarFactDescriptor values")
        versions = tuple(sorted(self.candidate_version_ids, key=CalendarVersionId.to_string))
        if len(set(versions)) != len(versions):
            raise ValueError("candidate_version_ids must be distinct")
        descriptors = tuple(sorted(
            self.candidate_fact_descriptors,
            key=lambda item: _canonical_bytes(item.semantic_mapping()),
        ))
        if len(set(_canonical_bytes(item.semantic_mapping()) for item in descriptors)) != len(descriptors):
            raise ValueError("candidate_fact_descriptors must be distinct")
        object.__setattr__(self, "candidate_version_ids", versions)
        object.__setattr__(self, "candidate_fact_descriptors", descriptors)
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest({
            "record_kind": "CALENDAR_CANDIDATE_SET_COVERAGE",
            "calendar_id": self.calendar_id.to_string(),
            "local_date": self.local_date.isoformat(),
            "session_kind": self.session_kind.value,
            "effective_as_of": _timestamp_text(self.effective_as_of),
            "knowledge_cutoff": _timestamp_text(self.knowledge_cutoff),
            "evaluation_at": _timestamp_text(self.evaluation_at),
            "knowledge_from": _timestamp_text(self.knowledge_from),
            "candidate_version_ids": [item.to_string() for item in versions],
            "candidate_fact_descriptors": [item.semantic_mapping() for item in descriptors],
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_state": self.validation_state.value,
        }))


@dataclass(frozen=True, slots=True)
class CalendarNegativeCoverageEvidence:
    calendar_id: CalendarId
    local_date: date
    session_kind: SessionKind
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    evaluation_at: Timestamp
    candidate_set_digest: CalendarContentDigest
    negative_fact: CalendarNegativeFact
    knowledge_from: Timestamp
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _calendar(self.calendar_id)
        if type(self.local_date) is not date:
            raise TypeError("local_date must be a date")
        if type(self.session_kind) is not SessionKind:
            raise TypeError("session_kind must be a SessionKind")
        for name in ("effective_as_of", "knowledge_cutoff", "evaluation_at", "knowledge_from"):
            _time(getattr(self, name), name)
        if type(self.candidate_set_digest) is not CalendarContentDigest:
            raise TypeError("candidate_set_digest must be a CalendarContentDigest")
        if type(self.negative_fact) is not CalendarNegativeFact:
            raise TypeError("negative_fact must be a CalendarNegativeFact")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest({
            "record_kind": "CALENDAR_NEGATIVE_COVERAGE_EVIDENCE",
            "calendar_id": self.calendar_id.to_string(),
            "local_date": self.local_date.isoformat(),
            "session_kind": self.session_kind.value,
            "effective_as_of": _timestamp_text(self.effective_as_of),
            "knowledge_cutoff": _timestamp_text(self.knowledge_cutoff),
            "evaluation_at": _timestamp_text(self.evaluation_at),
            "candidate_set_digest": self.candidate_set_digest.value,
            "negative_fact": self.negative_fact.value,
            "knowledge_from": _timestamp_text(self.knowledge_from),
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_state": self.validation_state.value,
        }))


@dataclass(frozen=True, slots=True)
class TimezoneRuleCorpusInput:
    timezone_rule_id: TimezoneRuleId
    corpus: bytes

    def __post_init__(self) -> None:
        if type(self.timezone_rule_id) is not TimezoneRuleId:
            raise TypeError("timezone_rule_id must be a TimezoneRuleId")
        if type(self.corpus) is not bytes:
            raise TypeError("corpus must be bytes")

    @property
    def content_digest(self) -> EvidenceContentDigest:
        return EvidenceContentDigest.from_bytes(self.corpus)


@dataclass(frozen=True, slots=True)
class CalendarSessionResolutionCandidates:
    calendar_versions: tuple[CalendarVersion, ...]
    historical_coverages: tuple[HistoricalCoverage, ...]
    trading_day_definitions: tuple[TradingDayDefinition, ...]
    session_definitions: tuple[SessionDefinition, ...]
    timezone_rule_corpora: tuple[TimezoneRuleCorpusInput, ...]
    candidate_set_coverage: CalendarCandidateSetCoverage | None
    negative_coverage: CalendarNegativeCoverageEvidence | None = None
    currentness_assessments: tuple[CalendarCurrentnessAssessment, ...] = ()
    correction_authority_assessments: tuple[CalendarCorrectionAuthorityAssessment, ...] = ()

    def __post_init__(self) -> None:
        expected = (
            ("calendar_versions", CalendarVersion),
            ("historical_coverages", HistoricalCoverage),
            ("trading_day_definitions", TradingDayDefinition),
            ("session_definitions", SessionDefinition),
            ("timezone_rule_corpora", TimezoneRuleCorpusInput),
            ("currentness_assessments", CalendarCurrentnessAssessment),
            ("correction_authority_assessments", CalendarCorrectionAuthorityAssessment),
        )
        for name, kind in expected:
            values = getattr(self, name)
            if type(values) is not tuple or any(type(item) is not kind for item in values):
                raise TypeError(f"{name} must be a tuple of {kind.__name__} values")


@dataclass(frozen=True, slots=True)
class CalendarSessionResolutionResult:
    disposition: ResolutionDisposition
    calendar_id: CalendarId
    local_date: date
    session_kind: SessionKind
    effective_as_of: Timestamp
    knowledge_cutoff: Timestamp
    evaluation_at: Timestamp
    selected_calendar_version_id: CalendarVersionId | None
    trading_day: TradingDayDefinition | None
    session: SessionDefinition | None
    utc_open: Timestamp | None
    utc_close: Timestamp | None
    timezone_rule_id: TimezoneRuleId | None
    timezone_rule_content_digest: EvidenceContentDigest | None
    currentness_assessment_digest: CalendarContentDigest | None
    correction_authority_assessment_digests: tuple[CalendarContentDigest, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    reason_code: CalendarSessionResolutionReason

    def __post_init__(self) -> None:
        if type(self.disposition) is not ResolutionDisposition:
            raise TypeError("disposition must be a ResolutionDisposition")
        _calendar(self.calendar_id)
        if type(self.reason_code) is not CalendarSessionResolutionReason:
            raise TypeError("reason_code must be a CalendarSessionResolutionReason")
        if type(self.correction_authority_assessment_digests) is not tuple:
            raise TypeError("correction_authority_assessment_digests must be a tuple")
        if any(type(item) is not CalendarContentDigest for item in self.correction_authority_assessment_digests):
            raise TypeError("correction authority digests must be CalendarContentDigest values")
        if self.evidence_refs:
            object.__setattr__(self, "evidence_refs", canonicalize_evidence_refs(self.evidence_refs))
        if len(self.evidence_refs) > MAX_EVIDENCE_REFS:
            raise ValueError("result evidence exceeds MAX_EVIDENCE_REFS")
        values = (
            self.selected_calendar_version_id, self.trading_day, self.session,
            self.utc_open, self.utc_close, self.timezone_rule_id,
            self.timezone_rule_content_digest, self.currentness_assessment_digest,
        )
        if self.disposition is not ResolutionDisposition.ESTABLISHED and (
            any(value is not None for value in values) or self.correction_authority_assessment_digests
        ):
            raise ValueError("non-ESTABLISHED results must not contain authoritative values")
        if self.disposition is ResolutionDisposition.ESTABLISHED:
            if self.selected_calendar_version_id is None or self.trading_day is None:
                raise ValueError("ESTABLISHED results require a selected version and trading day")
            if self.trading_day.day_kind is TradingDayKind.REGULAR_TRADING_DAY:
                if any(value is None for value in (
                    self.session, self.utc_open, self.utc_close,
                    self.timezone_rule_id, self.timezone_rule_content_digest,
                    self.currentness_assessment_digest,
                )):
                    raise ValueError("ESTABLISHED regular sessions require complete value fields")
            elif any(value is not None for value in (
                self.session, self.utc_open, self.utc_close,
                self.timezone_rule_id, self.timezone_rule_content_digest,
            )):
                raise ValueError("ESTABLISHED non-trading days must not contain a session")


def _query_matches(query: CalendarSessionResolutionQuery, value: object) -> bool:
    return all(getattr(value, name) == getattr(query, name) for name in (
        "calendar_id", "local_date", "session_kind", "effective_as_of",
        "knowledge_cutoff", "evaluation_at",
    ))


def _keys(values: Iterable[EvidenceRef]) -> set[tuple[object, ...]]:
    return {item.key for item in values}


def _minimal_evidence(*groups: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    by_key: dict[tuple[object, ...], EvidenceRef] = {}
    for group in groups:
        for item in group:
            previous = by_key.get(item.key)
            if previous is not None and previous.content_digest != item.content_digest:
                continue
            by_key[item.key] = item
    ordered = tuple(sorted(by_key.values(), key=EvidenceRef.canonical_bytes))
    if len(ordered) > MAX_EVIDENCE_REFS:
        raise ValueError("result evidence resource limit exceeded")
    return ordered


def _empty_result(
    query: CalendarSessionResolutionQuery,
    disposition: ResolutionDisposition,
    reason: CalendarSessionResolutionReason,
    evidence: tuple[EvidenceRef, ...] = (),
) -> CalendarSessionResolutionResult:
    return CalendarSessionResolutionResult(
        disposition, query.calendar_id, query.local_date, query.session_kind,
        query.effective_as_of, query.knowledge_cutoff, query.evaluation_at,
        None, None, None, None, None, None, None, None, (), evidence, reason,
    )


def resolve_calendar_session(
    *, query: CalendarSessionResolutionQuery,
    candidates: CalendarSessionResolutionCandidates,
) -> CalendarSessionResolutionResult:
    """Resolve one historical session without implicit authority or state."""
    if type(query) is not CalendarSessionResolutionQuery:
        raise TypeError("query must be a CalendarSessionResolutionQuery")
    if type(candidates) is not CalendarSessionResolutionCandidates:
        raise TypeError("candidates must be CalendarSessionResolutionCandidates")
    if query.session_kind is not SessionKind.REGULAR:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.UNSUPPORTED_SESSION_KIND)

    collections = (
        candidates.calendar_versions, candidates.historical_coverages,
        candidates.trading_day_definitions, candidates.session_definitions,
        candidates.timezone_rule_corpora, candidates.currentness_assessments,
        candidates.correction_authority_assessments,
    )
    if any(len(values) > MAX_LINEAGE_VERSIONS for values in collections):
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.RESOURCE_LIMIT_EXCEEDED)

    visible_versions = tuple(
        value for value in candidates.calendar_versions
        if value.knowledge_from.value <= query.knowledge_cutoff.value
    )
    visible_ids = {value.calendar_version_id for value in visible_versions}
    visible_coverages = tuple(value for value in candidates.historical_coverages if value.calendar_version_id in visible_ids)
    visible_days = tuple(value for value in candidates.trading_day_definitions if value.calendar_version_id in visible_ids)
    visible_sessions = tuple(value for value in candidates.session_definitions if value.calendar_version_id in visible_ids)
    visible_facts: tuple[
        HistoricalCoverage | TradingDayDefinition | SessionDefinition, ...
    ] = (*visible_coverages, *visible_days, *visible_sessions)

    coverage = candidates.candidate_set_coverage
    if coverage is None:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_MISSING)
    if not _query_matches(query, coverage):
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_MISMATCH)
    if coverage.knowledge_from.value > query.knowledge_cutoff.value:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_FUTURE)
    if coverage.validation_state is not ValidationState.VALID:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_NOT_AUTHORITATIVE)

    expected_ids = tuple(sorted(visible_ids, key=CalendarVersionId.to_string))
    expected_descriptors = tuple(sorted(
        (CalendarFactDescriptor.from_fact(item) for item in visible_facts),
        key=lambda item: _canonical_bytes(item.semantic_mapping()),
    ))
    if coverage.candidate_version_ids != expected_ids:
        reason = CalendarSessionResolutionReason.CANDIDATE_SET_INCOMPLETE
        if set(coverage.candidate_version_ids) - set(expected_ids):
            reason = CalendarSessionResolutionReason.CANDIDATE_SET_SURPLUS
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, reason)
    if coverage.candidate_fact_descriptors != expected_descriptors:
        reason = CalendarSessionResolutionReason.CANDIDATE_SET_INCOMPLETE
        if set(_canonical_bytes(item.semantic_mapping()) for item in coverage.candidate_fact_descriptors) - set(
            _canonical_bytes(item.semantic_mapping()) for item in expected_descriptors
        ):
            reason = CalendarSessionResolutionReason.CANDIDATE_SET_SURPLUS
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, reason)
    candidate_evidence = _keys(
        item
        for value in (*visible_versions, *visible_facts)
        for item in value.evidence_refs
    )
    if _keys(coverage.evidence_refs).intersection(candidate_evidence):
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SELF_CERTIFIED_COVERAGE)

    if any(
        value.calendar_id != query.calendar_id
        for value in (*visible_versions, *visible_facts)
    ):
        return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.WRONG_CALENDAR_SUBJECT)
    if visible_versions:
        try:
            ordered_versions = validate_calendar_lineage(visible_versions)
            by_id = {value.calendar_version_id: value for value in ordered_versions}
            for version in ordered_versions:
                facts = tuple(
                    item
                    for item in visible_facts
                    if item.calendar_version_id == version.calendar_version_id
                )
                validate_fact_bindings(version, facts)
                verify_calendar_content_digest(version, version.content_digest)
                for fact in facts:
                    verify_calendar_content_digest(fact, fact.content_digest)
        except (CalendarLineageValidationError, CalendarFactConflict, ValueError, TypeError):
            return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.INVALID_OR_CONFLICTING_LINEAGE)
    else:
        by_id = {}

    effective = tuple(value for value in visible_versions if value.contains_effective(query.effective_as_of))
    if visible_versions and not effective:
        return _empty_result(
            query,
            ResolutionDisposition.NOT_ESTABLISHED,
            CalendarSessionResolutionReason.EFFECTIVE_BOUNDARY_MISMATCH,
        )
    if not effective:
        return _negative_or_missing(query, candidates, coverage, candidate_evidence)
    if any(not value.is_authoritative for value in effective):
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.NO_APPLICABLE_VERSION)

    admissible: list[CalendarVersion] = []
    authority_used: list[CalendarCorrectionAuthorityAssessment] = []
    for version in effective:
        predecessor_id = version.supersedes_version_id
        if predecessor_id is None:
            admissible.append(version)
            continue
        assessments = tuple(
            item for item in candidates.correction_authority_assessments
            if item.calendar_id == query.calendar_id
            and item.successor_version_id == version.calendar_version_id
            and item.predecessor_version_id == predecessor_id
            and item.knowledge_from.value <= query.knowledge_cutoff.value
            and item.evaluated_at.value <= query.evaluation_at.value
        )
        if not assessments:
            return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_MISSING)
        valid = tuple(item for item in assessments if item.validation_state is ValidationState.VALID)
        if not valid:
            return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_NOT_ESTABLISHED)
        excluded = _keys(version.evidence_refs + version.correction_evidence_refs)
        predecessor = by_id.get(predecessor_id)
        if predecessor is not None:
            excluded.update(_keys(predecessor.evidence_refs + predecessor.correction_evidence_refs))
        excluded.update(_keys(
            ref for fact in visible_facts
            if fact.calendar_version_id == version.calendar_version_id for ref in fact.evidence_refs
        ))
        if any(not (_keys(item.evidence_refs) - excluded) for item in valid):
            return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_SELF_CERTIFIED)
        decisions = {item.decision for item in valid}
        if len(decisions) != 1:
            return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_CONFLICT)
        if next(iter(decisions)) is CalendarCorrectionAuthorityDecision.AUTHORIZED:
            admissible.append(version)
            authority_used.extend(valid)

    superseded = {value.supersedes_version_id for value in admissible if value.supersedes_version_id is not None}
    tips = tuple(value for value in admissible if value.calendar_version_id not in superseded)
    if len(tips) != 1:
        if len({item.content_digest for item in tips}) > 1:
            return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.MATERIAL_QUALIFYING_CONFLICT)
        return _empty_result(query, ResolutionDisposition.AMBIGUOUS, CalendarSessionResolutionReason.MULTIPLE_PLAUSIBLE_VERSIONS)
    selected = tips[0]

    applicable_currentness = tuple(
        item for item in candidates.currentness_assessments
        if item.calendar_id == query.calendar_id
        and item.calendar_version_id == selected.calendar_version_id
        and item.knowledge_from.value <= query.knowledge_cutoff.value
    )
    if not applicable_currentness:
        future = any(
            item.calendar_id == query.calendar_id
            and item.calendar_version_id == selected.calendar_version_id
            and item.knowledge_from.value > query.knowledge_cutoff.value
            for item in candidates.currentness_assessments
        )
        reason = CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_FUTURE if future else CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_MISSING
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, reason)
    valid_time = tuple(item for item in applicable_currentness if item.applies(query))
    if not valid_time:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_OUTSIDE_VALIDITY)
    authoritative_currentness = tuple(item for item in valid_time if item.validation_state is ValidationState.VALID)
    if not authoritative_currentness:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_NOT_AUTHORITATIVE)
    states = {item.state for item in authoritative_currentness}
    if len(states) != 1:
        return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_CONFLICT)
    currentness = authoritative_currentness[0]
    if currentness.state is CalendarCurrentness.STALE:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CALENDAR_VERSION_STALE)
    if currentness.state is CalendarCurrentness.UNKNOWN:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CALENDAR_CURRENTNESS_UNKNOWN)

    coverages = tuple(item for item in visible_coverages if item.calendar_version_id == selected.calendar_version_id and item.contains(query.local_date))
    if len(coverages) != 1:
        reason = CalendarSessionResolutionReason.COVERAGE_CONFLICT if len(coverages) > 1 else CalendarSessionResolutionReason.COVERAGE_NOT_ESTABLISHED
        disposition = ResolutionDisposition.CONFLICTING if len(coverages) > 1 else ResolutionDisposition.NOT_ESTABLISHED
        return _empty_result(query, disposition, reason)
    historical = coverages[0]
    if historical.coverage_knowledge_from is not None and historical.coverage_knowledge_from.value > query.knowledge_cutoff.value:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.COVERAGE_NOT_ESTABLISHED)
    day_and_session_facts = cast(
        tuple[TradingDayDefinition | SessionDefinition, ...],
        (*visible_days, *visible_sessions),
    )
    related_facts: tuple[TradingDayDefinition | SessionDefinition, ...] = tuple(
        item
        for item in day_and_session_facts
        if item.calendar_version_id == selected.calendar_version_id
    )
    coverage_state = coverage_disposition(historical, query.local_date, facts=related_facts)
    if coverage_state is ResolutionDisposition.CONFLICTING:
        return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.COVERAGE_CONFLICT)
    if coverage_state is not ResolutionDisposition.ESTABLISHED:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.COVERAGE_NOT_ESTABLISHED)

    days = tuple(item for item in visible_days if item.calendar_version_id == selected.calendar_version_id and item.local_date == query.local_date)
    if not days:
        return _negative_or_missing(query, candidates, coverage, candidate_evidence)
    if len({item.content_digest for item in days}) != 1:
        return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.TRADING_DAY_CONFLICT)
    day = days[0]
    if day.validation_state is not ValidationState.VALID:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.TRADING_DAY_NOT_ESTABLISHED)

    negative = _valid_negative(query, candidates.negative_coverage, coverage, candidate_evidence)
    sessions = tuple(
        item for item in visible_sessions
        if item.calendar_version_id == selected.calendar_version_id
        and item.local_date == query.local_date
        and item.session_kind is query.session_kind
    )
    if day.day_kind is not TradingDayKind.REGULAR_TRADING_DAY:
        if sessions:
            return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.SESSION_CONFLICT)
        return _established(query, selected, day, None, currentness, authority_used, historical.evidence_refs + day.evidence_refs)
    if negative is not None:
        try:
            conflict_evidence = _minimal_evidence(day.evidence_refs, negative.evidence_refs)
        except ValueError:
            return _empty_result(
                query,
                ResolutionDisposition.NOT_ESTABLISHED,
                CalendarSessionResolutionReason.RESOURCE_LIMIT_EXCEEDED,
            )
        return _empty_result(
            query, ResolutionDisposition.CONFLICTING,
            CalendarSessionResolutionReason.POSITIVE_NEGATIVE_SESSION_CONFLICT,
            conflict_evidence,
        )
    if not sessions:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.SESSION_NOT_ESTABLISHED)
    if len({item.content_digest for item in sessions}) != 1:
        return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.SESSION_CONFLICT)
    session = sessions[0]
    if not session.is_authoritative or session.timezone_rule_ref != selected.timezone_rule_ref:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.TIMEZONE_RULE_NOT_ESTABLISHED)
    corpora = tuple(item for item in candidates.timezone_rule_corpora if item.timezone_rule_id == selected.timezone_rule_ref.timezone_rule_id)
    if len(corpora) != 1:
        disposition = ResolutionDisposition.CONFLICTING if len(corpora) > 1 else ResolutionDisposition.NOT_ESTABLISHED
        return _empty_result(query, disposition, CalendarSessionResolutionReason.TIMEZONE_RULE_NOT_ESTABLISHED)
    corpus = corpora[0]
    if corpus.content_digest != selected.timezone_rule_ref.rule_content_digest:
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH)
    try:
        session.verify_utc_boundaries(corpus.corpus)
    except ValueError as error:
        if "UTC_CONVERSION_DISAGREEMENT" in str(error):
            return _empty_result(query, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.UTC_BOUNDARY_CONFLICT)
        return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.LOCAL_TIME_NOT_ESTABLISHED)
    return _established(
        query, selected, day, session, currentness, authority_used,
        historical.evidence_refs + day.evidence_refs + session.evidence_refs
        + selected.timezone_rule_ref.evidence_refs,
    )


def _valid_negative(
    query: CalendarSessionResolutionQuery,
    value: CalendarNegativeCoverageEvidence | None,
    coverage: CalendarCandidateSetCoverage,
    candidate_evidence: set[tuple[object, ...]],
) -> CalendarNegativeCoverageEvidence | None:
    if value is None or type(value) is not CalendarNegativeCoverageEvidence:
        return None
    if not _query_matches(query, value):
        return None
    if value.knowledge_from.value > query.knowledge_cutoff.value:
        return None
    if value.validation_state is not ValidationState.VALID:
        return None
    if value.candidate_set_digest != coverage.content_digest:
        return None
    if _keys(value.evidence_refs).intersection(candidate_evidence):
        return None
    return value


def _negative_or_missing(
    query: CalendarSessionResolutionQuery,
    candidates: CalendarSessionResolutionCandidates,
    coverage: CalendarCandidateSetCoverage,
    candidate_evidence: set[tuple[object, ...]],
) -> CalendarSessionResolutionResult:
    negative = _valid_negative(query, candidates.negative_coverage, coverage, candidate_evidence)
    if negative is not None:
        return _empty_result(
            query, ResolutionDisposition.ABSENT,
            CalendarSessionResolutionReason.AUTHORITATIVE_NEGATIVE_COVERAGE,
            negative.evidence_refs,
        )
    return _empty_result(query, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.NO_APPLICABLE_VERSION)


def _established(
    query: CalendarSessionResolutionQuery,
    version: CalendarVersion,
    day: TradingDayDefinition,
    session: SessionDefinition | None,
    currentness: CalendarCurrentnessAssessment,
    authorities: list[CalendarCorrectionAuthorityAssessment],
    evidence: tuple[EvidenceRef, ...],
) -> CalendarSessionResolutionResult:
    reason = (
        CalendarSessionResolutionReason.UNIQUE_AUTHORITATIVE_SESSION
        if session is not None else CalendarSessionResolutionReason.AUTHORITATIVE_NON_TRADING_DAY
    )
    try:
        supporting_evidence = _minimal_evidence(
            evidence,
            currentness.evidence_refs,
            *(item.evidence_refs for item in authorities),
        )
    except ValueError:
        return _empty_result(
            query,
            ResolutionDisposition.NOT_ESTABLISHED,
            CalendarSessionResolutionReason.RESOURCE_LIMIT_EXCEEDED,
        )
    return CalendarSessionResolutionResult(
        ResolutionDisposition.ESTABLISHED,
        query.calendar_id, query.local_date, query.session_kind,
        query.effective_as_of, query.knowledge_cutoff, query.evaluation_at,
        version.calendar_version_id, day, session,
        None if session is None else session.utc_open,
        None if session is None else session.utc_close,
        None if session is None else version.timezone_rule_ref.timezone_rule_id,
        None if session is None else version.timezone_rule_ref.rule_content_digest,
        currentness.content_digest,
        tuple(sorted((item.content_digest for item in authorities), key=lambda item: item.value)),
        supporting_evidence,
        reason,
    )

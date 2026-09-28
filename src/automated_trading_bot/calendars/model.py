"""Calendar S1 immutable identity, authority, fact, and lineage contracts.

The module is deliberately provider- and storage-neutral.  It records factual
calendar evidence and structural correction lineage; it does not resolve a
historical calendar, adjudicate corrections, or grant trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping, TypeAlias
import unicodedata
from uuid import UUID

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceRef,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
)
from automated_trading_bot.instruments.resolution import ResolutionDisposition


MAX_EVIDENCE_REFS = 64
MAX_LINEAGE_VERSIONS = 4096
MAX_LINEAGE_DEPTH = 256

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_SCOPE_CODE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}", re.ASCII)
_TIMEZONE = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)+", re.ASCII)
_AUTHORITY = r"[a-z0-9](?:[a-z0-9]|[.-](?=[a-z0-9])){0,62}"
_LOCAL = r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,190}"


class CalendarLineageValidationError(ValueError):
    """A complete finite CalendarVersion lineage is invalid."""


class CalendarFactConflict(ValueError):
    """The same calendar fact identity has incompatible semantic content."""


class CalendarCoverageError(ValueError):
    """Historical coverage is not independently attributable."""


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
    try:
        parsed = UUID(value[len(prefix):])
    except (ValueError, AttributeError) as error:
        raise ValueError("value contains an invalid UUID") from error
    _require_uuid4(parsed)
    if value != f"{prefix}{parsed}":
        raise ValueError("value is not in canonical form")
    return parsed


@dataclass(frozen=True, slots=True)
class CalendarId:
    value: UUID
    _PREFIX = "atis:calendar:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> CalendarId:
        return cls(_parse_uuid4(value, cls._PREFIX))

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


@dataclass(frozen=True, slots=True)
class CalendarVersionId:
    value: UUID
    _PREFIX = "atis:calendar-version:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> CalendarVersionId:
        return cls(_parse_uuid4(value, cls._PREFIX))

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


@dataclass(frozen=True, slots=True)
class TimezoneId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if _TIMEZONE.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical IANA-style timezone identity")


@dataclass(frozen=True, slots=True)
class TimezoneRuleId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if re.fullmatch(fr"timezone-rule:({_AUTHORITY})/({_LOCAL})", self.value, re.ASCII) is None:
            raise ValueError("value must be a canonical timezone-rule identity")


@dataclass(frozen=True, slots=True)
class CalendarContentDigest:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if _DIGEST.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical SHA-256 digest")


class CalendarCoverageState(StrEnum):
    COVERED = "COVERED"
    INCOMPLETE = "INCOMPLETE"
    CONFLICTING = "CONFLICTING"


class TradingDayKind(StrEnum):
    REGULAR_TRADING_DAY = "REGULAR_TRADING_DAY"
    EXCHANGE_HOLIDAY = "EXCHANGE_HOLIDAY"
    SPECIAL_NON_TRADING_DAY = "SPECIAL_NON_TRADING_DAY"


class SessionKind(StrEnum):
    REGULAR = "REGULAR"


class SessionScheduleVariation(StrEnum):
    STANDARD = "STANDARD"
    EARLY_CLOSE = "EARLY_CLOSE"


class CalendarCurrentness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Mapping[str, object]) -> CalendarContentDigest:
    return CalendarContentDigest(f"sha256:{sha256(_canonical_json(value)).hexdigest()}")


def _timestamp_text(value: Timestamp | None) -> str | None:
    if value is None:
        return None
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _evidence_mapping(value: EvidenceRef) -> dict[str, str]:
    return {
        "source_id": value.source_id.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "content_digest": value.content_digest.value,
    }


def _evidence(values: tuple[EvidenceRef, ...], *, optional: bool = False) -> tuple[EvidenceRef, ...]:
    if optional and values == ():
        return ()
    return canonicalize_evidence_refs(values)


def _required_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a str")
    if not value or value != value.strip() or unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be nonempty, unpadded, and NFC-normalized")
    return value


def _scope_code(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a str")
    if _SCOPE_CODE.fullmatch(value) is None:
        raise ValueError(f"{name} must use canonical descriptive scope syntax")
    return value


def _date(value: object, name: str) -> date:
    if type(value) is not date:
        raise TypeError(f"{name} must be a date")
    return value


def _timestamp(value: object, name: str, *, optional: bool = False) -> Timestamp | None:
    if value is None and optional:
        return None
    if type(value) is not Timestamp:
        raise TypeError(f"{name} must be a Timestamp")
    return value


@dataclass(frozen=True, slots=True)
class TimezoneRuleRef:
    timezone_id: TimezoneId
    timezone_rule_id: TimezoneRuleId
    rule_content_digest: EvidenceContentDigest
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState

    def __post_init__(self) -> None:
        if type(self.timezone_id) is not TimezoneId:
            raise TypeError("timezone_id must be a TimezoneId")
        if type(self.timezone_rule_id) is not TimezoneRuleId:
            raise TypeError("timezone_rule_id must be a TimezoneRuleId")
        if type(self.rule_content_digest) is not EvidenceContentDigest:
            raise TypeError("rule_content_digest must be an EvidenceContentDigest")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_state) is not ValidationState:
            raise TypeError("validation_state must be a ValidationState")

    @property
    def is_authoritative(self) -> bool:
        return self.validation_state is ValidationState.VALID

    def verify_rule_corpus(self, corpus: bytes) -> None:
        if type(corpus) is not bytes:
            raise TypeError("corpus must be bytes")
        if EvidenceContentDigest.from_bytes(corpus) != self.rule_content_digest:
            raise ValueError("TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "timezone_id": self.timezone_id.value,
            "timezone_rule_id": self.timezone_rule_id.value,
            "rule_content_digest": self.rule_content_digest.value,
            "evidence_refs": [_evidence_mapping(item) for item in self.evidence_refs],
            "validation_policy_id": self.validation_policy_id.value,
            "validation_state": self.validation_state.value,
        }


@dataclass(frozen=True, slots=True)
class LocalWallTime:
    hour: int
    minute: int
    second: int = 0
    microsecond: int = 0
    utc_offset_minutes: int | None = None
    ambiguous: bool = False
    nonexistent: bool = False
    fold: int | None = None

    def __post_init__(self) -> None:
        for name, value, lower, upper in (
            ("hour", self.hour, 0, 23),
            ("minute", self.minute, 0, 59),
            ("second", self.second, 0, 59),
            ("microsecond", self.microsecond, 0, 999999),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} is outside its canonical range")
        if self.utc_offset_minutes is not None:
            if type(self.utc_offset_minutes) is not int or not -24 * 60 < self.utc_offset_minutes < 24 * 60:
                raise ValueError("utc_offset_minutes is outside its canonical range")
        if type(self.ambiguous) is not bool or type(self.nonexistent) is not bool:
            raise TypeError("ambiguous and nonexistent must be bool values")
        if self.ambiguous and self.nonexistent:
            raise ValueError("a local time cannot be both ambiguous and nonexistent")
        if self.fold is not None and self.fold not in (0, 1):
            raise ValueError("fold must be 0, 1, or None")
        if not self.ambiguous and self.fold is not None:
            raise ValueError("fold is only valid for an ambiguous local time")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "hour": self.hour,
            "minute": self.minute,
            "second": self.second,
            "microsecond": self.microsecond,
            "utc_offset_minutes": self.utc_offset_minutes,
            "ambiguous": self.ambiguous,
            "nonexistent": self.nonexistent,
            "fold": self.fold,
        }

    def to_utc(self, local_date: date, rule_ref: TimezoneRuleRef, rule_corpus: bytes) -> Timestamp:
        _date(local_date, "local_date")
        if type(rule_ref) is not TimezoneRuleRef:
            raise TypeError("rule_ref must be a TimezoneRuleRef")
        rule_ref.verify_rule_corpus(rule_corpus)
        if not rule_ref.is_authoritative:
            raise ValueError("TIMEZONE_RULE_NOT_AUTHORITATIVE")
        if self.nonexistent or self.utc_offset_minutes is None:
            raise ValueError("NONEXISTENT_OR_UNBOUND_LOCAL_TIME")
        if self.ambiguous and self.fold is None:
            raise ValueError("AMBIGUOUS_LOCAL_TIME")
        local = datetime.combine(
            local_date,
            datetime.min.time().replace(
                hour=self.hour,
                minute=self.minute,
                second=self.second,
                microsecond=self.microsecond,
            ),
        )
        utc_value = (local - timedelta(minutes=self.utc_offset_minutes)).replace(tzinfo=UTC)
        return Timestamp(utc_value)


@dataclass(frozen=True, slots=True)
class CalendarVersion:
    calendar_version_id: CalendarVersionId
    calendar_id: CalendarId
    market_code: str
    venue_code: str
    effective_from: Timestamp
    effective_to: Timestamp | None
    knowledge_from: Timestamp
    timezone_rule_ref: TimezoneRuleRef
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    supersedes_version_id: CalendarVersionId | None = None
    correction_reason: str | None = None
    correction_evidence_refs: tuple[EvidenceRef, ...] = ()
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        if type(self.calendar_version_id) is not CalendarVersionId:
            raise TypeError("calendar_version_id must be a CalendarVersionId")
        if type(self.calendar_id) is not CalendarId:
            raise TypeError("calendar_id must be a CalendarId")
        object.__setattr__(self, "market_code", _scope_code(self.market_code, "market_code"))
        object.__setattr__(self, "venue_code", _scope_code(self.venue_code, "venue_code"))
        _timestamp(self.effective_from, "effective_from")
        _timestamp(self.effective_to, "effective_to", optional=True)
        _timestamp(self.knowledge_from, "knowledge_from")
        if self.effective_to is not None and self.effective_to.value <= self.effective_from.value:
            raise ValueError("effective interval must have a positive duration")
        if type(self.timezone_rule_ref) is not TimezoneRuleRef:
            raise TypeError("timezone_rule_ref must be a TimezoneRuleRef")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        if type(self.validation_policy_id) is not ValidationPolicyId:
            raise TypeError("validation_policy_id must be a ValidationPolicyId")
        if type(self.validation_state) is not ValidationState:
            raise TypeError("validation_state must be a ValidationState")
        if self.supersedes_version_id is not None and type(self.supersedes_version_id) is not CalendarVersionId:
            raise TypeError("supersedes_version_id must be a CalendarVersionId or None")
        if self.supersedes_version_id == self.calendar_version_id:
            raise CalendarLineageValidationError("SELF_SUPERSESSION")
        correction_refs = _evidence(self.correction_evidence_refs, optional=True)
        object.__setattr__(self, "correction_evidence_refs", correction_refs)
        if self.supersedes_version_id is None:
            if self.correction_reason is not None or correction_refs:
                raise CalendarLineageValidationError("ROOT_HAS_CORRECTION_METADATA")
        else:
            object.__setattr__(self, "correction_reason", _required_text(
                self.correction_reason, "correction_reason",
            ))
            if not correction_refs:
                raise CalendarLineageValidationError("CORRECTION_EVIDENCE_REQUIRED")
        object.__setattr__(self, "content_digest", _digest(_calendar_version_semantics(self)))

    @property
    def is_authoritative(self) -> bool:
        return (
            self.validation_state is ValidationState.VALID
            and self.timezone_rule_ref.is_authoritative
        )

    def contains_effective(self, instant: Timestamp) -> bool:
        _timestamp(instant, "instant")
        return self.effective_from.value <= instant.value and (
            self.effective_to is None or instant.value < self.effective_to.value
        )

    def is_known_at(self, instant: Timestamp) -> bool:
        _timestamp(instant, "instant")
        return self.knowledge_from.value <= instant.value


def _calendar_version_semantics(value: CalendarVersion) -> Mapping[str, object]:
    return {
        "record_kind": "CALENDAR_VERSION",
        "calendar_id": value.calendar_id.to_string(),
        "market_code": value.market_code,
        "venue_code": value.venue_code,
        "effective_from": _timestamp_text(value.effective_from),
        "effective_to": _timestamp_text(value.effective_to),
        "knowledge_from": _timestamp_text(value.knowledge_from),
        "timezone_rule_ref": value.timezone_rule_ref.semantic_mapping(),
        "evidence_refs": [_evidence_mapping(item) for item in value.evidence_refs],
        "validation_policy_id": value.validation_policy_id.value,
        "validation_state": value.validation_state.value,
        "supersedes_version_id": None if value.supersedes_version_id is None else value.supersedes_version_id.to_string(),
        "correction_reason": value.correction_reason,
        "correction_evidence_refs": [_evidence_mapping(item) for item in value.correction_evidence_refs],
    }


@dataclass(frozen=True, slots=True)
class HistoricalCoverage:
    calendar_id: CalendarId
    calendar_version_id: CalendarVersionId
    local_date_from: date
    local_date_to: date
    coverage_state: CalendarCoverageState
    coverage_knowledge_from: Timestamp | None
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _validate_child_binding(self.calendar_id, self.calendar_version_id)
        _date(self.local_date_from, "local_date_from")
        _date(self.local_date_to, "local_date_to")
        if self.local_date_to <= self.local_date_from:
            raise ValueError("coverage interval must have a positive duration")
        if type(self.coverage_state) is not CalendarCoverageState:
            raise TypeError("coverage_state must be a CalendarCoverageState")
        _timestamp(self.coverage_knowledge_from, "coverage_knowledge_from", optional=True)
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _validate_policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest(_coverage_semantics(self)))

    def contains(self, local_date: date) -> bool:
        _date(local_date, "local_date")
        return self.local_date_from <= local_date < self.local_date_to

    def knowledge_from(self, version: CalendarVersion, *, same_atomic_publication: bool) -> Timestamp:
        if self.coverage_knowledge_from is not None:
            return self.coverage_knowledge_from
        _require_version_binding(self.calendar_id, self.calendar_version_id, version)
        if type(same_atomic_publication) is not bool or not same_atomic_publication:
            raise CalendarCoverageError("COVERAGE_KNOWLEDGE_NOT_ATTRIBUTABLE")
        return version.knowledge_from


def _coverage_semantics(value: HistoricalCoverage) -> Mapping[str, object]:
    return {
        "record_kind": "HISTORICAL_COVERAGE",
        "calendar_id": value.calendar_id.to_string(),
        "calendar_version_id": value.calendar_version_id.to_string(),
        "local_date_from": value.local_date_from.isoformat(),
        "local_date_to": value.local_date_to.isoformat(),
        "coverage_state": value.coverage_state.value,
        "coverage_knowledge_from": _timestamp_text(value.coverage_knowledge_from),
        "evidence_refs": [_evidence_mapping(item) for item in value.evidence_refs],
        "validation_policy_id": value.validation_policy_id.value,
        "validation_state": value.validation_state.value,
    }


@dataclass(frozen=True, slots=True)
class TradingDayDefinition:
    calendar_id: CalendarId
    calendar_version_id: CalendarVersionId
    local_date: date
    day_kind: TradingDayKind
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _validate_child_binding(self.calendar_id, self.calendar_version_id)
        _date(self.local_date, "local_date")
        if type(self.day_kind) is not TradingDayKind:
            raise TypeError("day_kind must be a TradingDayKind")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _validate_policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest(_day_semantics(self)))


def _day_semantics(value: TradingDayDefinition) -> Mapping[str, object]:
    return {
        "record_kind": "TRADING_DAY_DEFINITION",
        "calendar_id": value.calendar_id.to_string(),
        "calendar_version_id": value.calendar_version_id.to_string(),
        "local_date": value.local_date.isoformat(),
        "day_kind": value.day_kind.value,
        "evidence_refs": [_evidence_mapping(item) for item in value.evidence_refs],
        "validation_policy_id": value.validation_policy_id.value,
        "validation_state": value.validation_state.value,
    }


@dataclass(frozen=True, slots=True)
class SessionDefinition:
    calendar_id: CalendarId
    calendar_version_id: CalendarVersionId
    local_date: date
    session_kind: SessionKind
    schedule_variation: SessionScheduleVariation
    local_open: LocalWallTime
    local_close: LocalWallTime
    utc_open: Timestamp
    utc_close: Timestamp
    timezone_rule_ref: TimezoneRuleRef
    evidence_refs: tuple[EvidenceRef, ...]
    validation_policy_id: ValidationPolicyId
    validation_state: ValidationState
    content_digest: CalendarContentDigest = field(init=False)

    def __post_init__(self) -> None:
        _validate_child_binding(self.calendar_id, self.calendar_version_id)
        _date(self.local_date, "local_date")
        if type(self.session_kind) is not SessionKind:
            raise TypeError("session_kind must be a SessionKind")
        if type(self.schedule_variation) is not SessionScheduleVariation:
            raise TypeError("schedule_variation must be a SessionScheduleVariation")
        if type(self.local_open) is not LocalWallTime or type(self.local_close) is not LocalWallTime:
            raise TypeError("session boundaries must be LocalWallTime values")
        if self.local_open.nonexistent or self.local_close.nonexistent:
            raise ValueError("NONEXISTENT_LOCAL_TIME")
        if (self.local_open.ambiguous and self.local_open.fold is None) or (
            self.local_close.ambiguous and self.local_close.fold is None
        ):
            raise ValueError("AMBIGUOUS_LOCAL_TIME")
        if self.local_close.semantic_mapping() == self.local_open.semantic_mapping() or (
            self.local_close.hour,
            self.local_close.minute,
            self.local_close.second,
            self.local_close.microsecond,
        ) <= (
            self.local_open.hour,
            self.local_open.minute,
            self.local_open.second,
            self.local_open.microsecond,
        ):
            raise ValueError("local_close must be after local_open for REGULAR V1 sessions")
        _timestamp(self.utc_open, "utc_open")
        _timestamp(self.utc_close, "utc_close")
        if self.utc_close.value <= self.utc_open.value:
            raise ValueError("utc_close must be after utc_open")
        if type(self.timezone_rule_ref) is not TimezoneRuleRef:
            raise TypeError("timezone_rule_ref must be a TimezoneRuleRef")
        object.__setattr__(self, "evidence_refs", _evidence(self.evidence_refs))
        _validate_policy(self.validation_policy_id, self.validation_state)
        object.__setattr__(self, "content_digest", _digest(_session_semantics(self)))

    def verify_utc_boundaries(self, rule_corpus: bytes) -> None:
        expected_open = self.local_open.to_utc(self.local_date, self.timezone_rule_ref, rule_corpus)
        expected_close = self.local_close.to_utc(self.local_date, self.timezone_rule_ref, rule_corpus)
        if expected_open != self.utc_open or expected_close != self.utc_close:
            raise ValueError("UTC_CONVERSION_DISAGREEMENT")

    @property
    def is_authoritative(self) -> bool:
        return (
            self.validation_state is ValidationState.VALID
            and self.timezone_rule_ref.is_authoritative
        )


def _session_semantics(value: SessionDefinition) -> Mapping[str, object]:
    return {
        "record_kind": "SESSION_DEFINITION",
        "calendar_id": value.calendar_id.to_string(),
        "calendar_version_id": value.calendar_version_id.to_string(),
        "local_date": value.local_date.isoformat(),
        "session_kind": value.session_kind.value,
        "schedule_variation": value.schedule_variation.value,
        "local_open": value.local_open.semantic_mapping(),
        "local_close": value.local_close.semantic_mapping(),
        "utc_open": _timestamp_text(value.utc_open),
        "utc_close": _timestamp_text(value.utc_close),
        "timezone_rule_ref": value.timezone_rule_ref.semantic_mapping(),
        "evidence_refs": [_evidence_mapping(item) for item in value.evidence_refs],
        "validation_policy_id": value.validation_policy_id.value,
        "validation_state": value.validation_state.value,
    }


CalendarFact: TypeAlias = HistoricalCoverage | TradingDayDefinition | SessionDefinition


def _validate_child_binding(calendar_id: object, version_id: object) -> None:
    if type(calendar_id) is not CalendarId:
        raise TypeError("calendar_id must be a CalendarId")
    if type(version_id) is not CalendarVersionId:
        raise TypeError("calendar_version_id must be a CalendarVersionId")


def _validate_policy(policy: object, state: object) -> None:
    if type(policy) is not ValidationPolicyId:
        raise TypeError("validation_policy_id must be a ValidationPolicyId")
    if type(state) is not ValidationState:
        raise TypeError("validation_state must be a ValidationState")


def _require_version_binding(calendar_id: CalendarId, version_id: CalendarVersionId, version: CalendarVersion) -> None:
    if type(version) is not CalendarVersion:
        raise TypeError("version must be a CalendarVersion")
    if version.calendar_id != calendar_id or version.calendar_version_id != version_id:
        raise CalendarFactConflict("CHILD_BOUND_TO_WRONG_CALENDAR_VERSION")


def validate_fact_bindings(version: CalendarVersion, facts: tuple[CalendarFact, ...]) -> tuple[CalendarFact, ...]:
    if type(facts) is not tuple:
        raise TypeError("facts must be a tuple")
    if len(facts) > MAX_LINEAGE_VERSIONS:
        raise CalendarFactConflict("FACT_RESOURCE_LIMIT_EXCEEDED")
    seen: dict[tuple[object, ...], CalendarContentDigest] = {}
    for fact in facts:
        if type(fact) not in (HistoricalCoverage, TradingDayDefinition, SessionDefinition):
            raise TypeError("facts contains an invalid calendar fact")
        _require_version_binding(fact.calendar_id, fact.calendar_version_id, version)
        if isinstance(fact, SessionDefinition) and fact.timezone_rule_ref != version.timezone_rule_ref:
            raise CalendarFactConflict("TIMEZONE_RULE_MISMATCH")
        identity = _fact_identity(fact)
        previous = seen.get(identity)
        if previous is not None and previous != fact.content_digest:
            raise CalendarFactConflict("DUPLICATE_FACT_IDENTITY_DIFFERENT_CONTENT")
        seen[identity] = fact.content_digest
    return tuple(sorted(facts, key=lambda value: (_fact_identity(value), value.content_digest.value)))


def _fact_identity(value: CalendarFact) -> tuple[object, ...]:
    if isinstance(value, HistoricalCoverage):
        return ("COVERAGE", value.local_date_from.isoformat(), value.local_date_to.isoformat())
    if isinstance(value, TradingDayDefinition):
        return ("DAY", value.local_date.isoformat())
    return ("SESSION", value.local_date.isoformat(), value.session_kind.value)


def validate_coverage_independence(
    coverage: HistoricalCoverage,
    facts: tuple[TradingDayDefinition | SessionDefinition, ...],
) -> None:
    if type(coverage) is not HistoricalCoverage or type(facts) is not tuple:
        raise TypeError("coverage and facts have invalid types")
    fact_keys = {item.key for fact in facts for item in fact.evidence_refs}
    if all(item.key in fact_keys for item in coverage.evidence_refs):
        raise CalendarCoverageError("COVERAGE_SELF_CERTIFICATION")


def coverage_disposition(
    coverage: HistoricalCoverage | None,
    local_date: date,
    *,
    facts: tuple[TradingDayDefinition | SessionDefinition, ...] = (),
) -> ResolutionDisposition:
    _date(local_date, "local_date")
    if coverage is None or not coverage.contains(local_date):
        return ResolutionDisposition.NOT_ESTABLISHED
    if coverage.validation_state is not ValidationState.VALID:
        return ResolutionDisposition.NOT_ESTABLISHED
    if coverage.coverage_state is CalendarCoverageState.CONFLICTING:
        return ResolutionDisposition.CONFLICTING
    if coverage.coverage_state is CalendarCoverageState.INCOMPLETE:
        return ResolutionDisposition.NOT_ESTABLISHED
    try:
        validate_coverage_independence(coverage, facts)
    except CalendarCoverageError:
        return ResolutionDisposition.NOT_ESTABLISHED
    return ResolutionDisposition.ESTABLISHED


def validate_calendar_lineage(values: tuple[CalendarVersion, ...]) -> tuple[CalendarVersion, ...]:
    if type(values) is not tuple:
        raise TypeError("lineage must be a tuple")
    if not values:
        raise CalendarLineageValidationError("lineage must not be empty")
    if len(values) > MAX_LINEAGE_VERSIONS:
        raise CalendarLineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
    if any(type(item) is not CalendarVersion for item in values):
        raise TypeError("lineage contains an invalid CalendarVersion")
    by_id: dict[CalendarVersionId, CalendarVersion] = {}
    for item in values:
        if item.calendar_version_id in by_id:
            raise CalendarLineageValidationError("DUPLICATE_CALENDAR_VERSION_ID")
        by_id[item.calendar_version_id] = item
    subject = values[0].calendar_id
    if any(item.calendar_id != subject for item in values):
        raise CalendarLineageValidationError("CROSS_CALENDAR_EDGE")
    scope = (values[0].market_code, values[0].venue_code)
    if any((item.market_code, item.venue_code) != scope for item in values):
        raise CalendarLineageValidationError("CONFLICTING_SCOPE")
    successors: dict[CalendarVersionId, CalendarVersionId] = {}
    for item in values:
        predecessor_id = item.supersedes_version_id
        if predecessor_id is None:
            continue
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise CalendarLineageValidationError("MISSING_PREDECESSOR")
        if predecessor_id in successors:
            raise CalendarLineageValidationError("BRANCHING_SUCCESSOR_CONFLICT")
        successors[predecessor_id] = item.calendar_version_id
    for start in by_id:
        seen: set[CalendarVersionId] = set()
        current = start
        depth = 0
        while True:
            predecessor_id = by_id[current].supersedes_version_id
            if predecessor_id is None:
                break
            if predecessor_id in seen or predecessor_id == start:
                raise CalendarLineageValidationError("LINEAGE_CYCLE")
            seen.add(predecessor_id)
            depth += 1
            if depth > MAX_LINEAGE_DEPTH:
                raise CalendarLineageValidationError("LINEAGE_RESOURCE_LIMIT_EXCEEDED")
            current = predecessor_id
    for item in values:
        predecessor_id = item.supersedes_version_id
        if predecessor_id is None:
            continue
        predecessor = by_id[predecessor_id]
        if item.knowledge_from.value <= predecessor.knowledge_from.value:
            raise CalendarLineageValidationError("SUCCESSOR_KNOWLEDGE_NOT_GREATER")
    return tuple(sorted(values, key=lambda item: item.calendar_version_id.to_string()))


def verify_calendar_content_digest(value: CalendarVersion | CalendarFact, claimed: CalendarContentDigest) -> None:
    if type(claimed) is not CalendarContentDigest:
        raise TypeError("claimed must be a CalendarContentDigest")
    if isinstance(value, CalendarVersion):
        expected = _digest(_calendar_version_semantics(value))
    elif isinstance(value, HistoricalCoverage):
        expected = _digest(_coverage_semantics(value))
    elif isinstance(value, TradingDayDefinition):
        expected = _digest(_day_semantics(value))
    elif isinstance(value, SessionDefinition):
        expected = _digest(_session_semantics(value))
    else:
        raise TypeError("value must be an immutable calendar record")
    if claimed != expected:
        raise ValueError("content_digest does not match canonical semantic content")

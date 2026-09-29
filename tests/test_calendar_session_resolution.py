from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from typing import Any, Literal, cast
from uuid import UUID

import pytest

from automated_trading_bot.calendars import (
    CalendarCandidateSetCoverage,
    CalendarCorrectionAuthorityAssessment,
    CalendarCorrectionAuthorityDecision,
    CalendarCoverageState,
    CalendarCurrentness,
    CalendarCurrentnessAssessment,
    CalendarFactDescriptor,
    CalendarId,
    CalendarNegativeCoverageEvidence,
    CalendarNegativeFact,
    CalendarSessionResolutionCandidates,
    CalendarSessionResolutionQuery,
    CalendarSessionResolutionReason,
    CalendarSessionResolutionResult,
    CalendarVersion,
    CalendarVersionId,
    HistoricalCoverage,
    LocalWallTime,
    SessionDefinition,
    SessionKind,
    SessionScheduleVariation,
    TimezoneId,
    TimezoneRuleCorpusInput,
    TimezoneRuleId,
    TimezoneRuleRef,
    TradingDayDefinition,
    TradingDayKind,
    resolve_calendar_session,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    ResolutionDisposition,
    SourceId,
    ValidationPolicyId,
    ValidationState,
)


DAY = date(2026, 3, 30)
CORPUS = b"calendar-s2-rule-corpus"


def uid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def instant(seconds: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def evidence(number: int, content: bytes | None = None) -> EvidenceRef:
    body = f"evidence-{number}".encode() if content is None else content
    return EvidenceRef(
        SourceId("source:atis/calendar-s2"),
        DatasetId("dataset:atis/calendar-s2"),
        EvidenceId(f"evidence:atis/calendar-s2-{number}"),
        EvidenceContentDigest.from_bytes(body),
    )


def policy() -> ValidationPolicyId:
    return ValidationPolicyId("validation-policy:atis/calendar-s2-v1")


def timezone_ref(corpus: bytes = CORPUS) -> TimezoneRuleRef:
    return TimezoneRuleRef(
        TimezoneId("Europe/London"),
        TimezoneRuleId("timezone-rule:atis/europe-london-v1"),
        EvidenceContentDigest.from_bytes(corpus),
        (evidence(90, corpus),),
        policy(),
        ValidationState.VALID,
    )


def version(
    number: int = 1,
    *,
    knowledge: int = 1,
    supersedes: int | None = None,
    effective_from: int = 0,
    effective_to: int | None = None,
) -> CalendarVersion:
    return CalendarVersion(
        CalendarVersionId(uid(number)),
        CalendarId(uid(100)),
        "GB",
        "XLON",
        instant(effective_from),
        None if effective_to is None else instant(effective_to),
        instant(knowledge),
        timezone_ref(),
        (evidence(number),),
        policy(),
        ValidationState.VALID,
        None if supersedes is None else CalendarVersionId(uid(supersedes)),
        None if supersedes is None else "SOURCE_CORRECTION",
        () if supersedes is None else (evidence(1000 + number),),
    )


def historical(version_number: int = 1, *, state: CalendarCoverageState = CalendarCoverageState.COVERED) -> HistoricalCoverage:
    return HistoricalCoverage(
        CalendarId(uid(100)),
        CalendarVersionId(uid(version_number)),
        date(2026, 1, 1),
        date(2027, 1, 1),
        state,
        instant(2),
        (evidence(200 + version_number),),
        policy(),
        ValidationState.VALID,
    )


def day(kind: TradingDayKind = TradingDayKind.REGULAR_TRADING_DAY, version_number: int = 1) -> TradingDayDefinition:
    return TradingDayDefinition(
        CalendarId(uid(100)),
        CalendarVersionId(uid(version_number)),
        DAY,
        kind,
        (evidence(300 + version_number),),
        policy(),
        ValidationState.VALID,
    )


def wall(hour: int, minute: int = 0, *, offset: int | None = 60, ambiguous: bool = False, nonexistent: bool = False, fold: int | None = None) -> LocalWallTime:
    return LocalWallTime(hour, minute, 0, 0, offset, ambiguous, nonexistent, fold)


def session(version_number: int = 1, *, variation: SessionScheduleVariation = SessionScheduleVariation.STANDARD, corpus: bytes = CORPUS) -> SessionDefinition:
    opening = wall(8)
    closing = wall(12, 30) if variation is SessionScheduleVariation.EARLY_CLOSE else wall(16, 30)
    rule = timezone_ref(corpus)
    return SessionDefinition(
        CalendarId(uid(100)),
        CalendarVersionId(uid(version_number)),
        DAY,
        SessionKind.REGULAR,
        variation,
        opening,
        closing,
        opening.to_utc(DAY, rule, corpus),
        closing.to_utc(DAY, rule, corpus),
        rule,
        (evidence(400 + version_number),),
        policy(),
        ValidationState.VALID,
    )


def query(*, effective: int = 10, knowledge: int = 20, evaluation: int = 30) -> CalendarSessionResolutionQuery:
    return CalendarSessionResolutionQuery(
        CalendarId(uid(100)), DAY, SessionKind.REGULAR,
        instant(effective), instant(knowledge), instant(evaluation),
    )


def currentness(version_number: int = 1, *, state: CalendarCurrentness = CalendarCurrentness.CURRENT, knowledge: int = 3, valid_from: int = 0, valid_to: int | None = None) -> CalendarCurrentnessAssessment:
    return CalendarCurrentnessAssessment(
        CalendarId(uid(100)), CalendarVersionId(uid(version_number)),
        instant(knowledge), instant(3), instant(valid_from),
        None if valid_to is None else instant(valid_to), state, policy(),
        (evidence(500 + version_number),), ValidationState.VALID,
    )


def candidate_manifest(
    q: CalendarSessionResolutionQuery,
    versions: tuple[CalendarVersion, ...],
    coverages: tuple[HistoricalCoverage, ...],
    days: tuple[TradingDayDefinition, ...],
    sessions: tuple[SessionDefinition, ...],
    *,
    knowledge: int = 4,
    refs: tuple[EvidenceRef, ...] = (evidence(600),),
) -> CalendarCandidateSetCoverage:
    visible = tuple(item for item in versions if item.knowledge_from.value <= q.knowledge_cutoff.value)
    visible_ids = {item.calendar_version_id for item in visible}
    all_facts = cast(
        tuple[HistoricalCoverage | TradingDayDefinition | SessionDefinition, ...],
        (*coverages, *days, *sessions),
    )
    facts = tuple(
        item for item in all_facts
        if item.calendar_version_id in visible_ids
    )
    return CalendarCandidateSetCoverage(
        q.calendar_id, q.local_date, q.session_kind, q.effective_as_of,
        q.knowledge_cutoff, q.evaluation_at, instant(knowledge),
        tuple(item.calendar_version_id for item in visible),
        tuple(CalendarFactDescriptor.from_fact(item) for item in facts),
        refs, policy(), ValidationState.VALID,
    )


def candidates(
    *,
    q: CalendarSessionResolutionQuery | None = None,
    versions: tuple[CalendarVersion, ...] | None = None,
    coverages: tuple[HistoricalCoverage, ...] | None = None,
    days: tuple[TradingDayDefinition, ...] | None = None,
    sessions: tuple[SessionDefinition, ...] | None = None,
    manifest: CalendarCandidateSetCoverage | None | Literal[False] = False,
    negative: CalendarNegativeCoverageEvidence | None = None,
    current: tuple[CalendarCurrentnessAssessment, ...] | None = None,
    authorities: tuple[CalendarCorrectionAuthorityAssessment, ...] = (),
    corpora: tuple[TimezoneRuleCorpusInput, ...] = (TimezoneRuleCorpusInput(timezone_ref().timezone_rule_id, CORPUS),),
) -> CalendarSessionResolutionCandidates:
    resolution_query = query() if q is None else q
    calendar_versions = (version(),) if versions is None else versions
    historical_coverages = (historical(),) if coverages is None else coverages
    trading_days = (day(),) if days is None else days
    calendar_sessions = (session(),) if sessions is None else sessions
    complete: CalendarCandidateSetCoverage | None
    if manifest is False:
        complete = candidate_manifest(
            resolution_query, calendar_versions, historical_coverages,
            trading_days, calendar_sessions,
        )
    else:
        complete = manifest
    return CalendarSessionResolutionCandidates(
        calendar_versions, historical_coverages, trading_days, calendar_sessions,
        corpora, complete, negative,
        (currentness(),) if current is None else current,
        authorities,
    )


def resolve(
    q: CalendarSessionResolutionQuery | None = None,
    **changes: Any,
) -> CalendarSessionResolutionResult:
    actual_query = query() if q is None else q
    return resolve_calendar_session(query=actual_query, candidates=candidates(q=actual_query, **changes))


def assert_restrictive(
    result: CalendarSessionResolutionResult,
    disposition: ResolutionDisposition,
    reason: CalendarSessionResolutionReason,
) -> None:
    assert result.disposition is disposition
    assert result.reason_code is reason
    assert result.selected_calendar_version_id is None
    assert result.trading_day is None
    assert result.session is None
    assert result.utc_open is None
    assert result.utc_close is None
    assert result.timezone_rule_id is None
    assert result.timezone_rule_content_digest is None


def test_direct_standard_and_early_close_resolution_is_deterministic() -> None:
    standard = resolve()
    early = resolve(sessions=(session(variation=SessionScheduleVariation.EARLY_CLOSE),))
    for result in (standard, early):
        assert result.disposition is ResolutionDisposition.ESTABLISHED
        assert result.reason_code is CalendarSessionResolutionReason.UNIQUE_AUTHORITATIVE_SESSION
        assert result.selected_calendar_version_id == CalendarVersionId(uid(1))
        assert result.utc_open is not None and result.utc_close is not None
    assert standard.utc_close != early.utc_close
    assert standard == resolve()


@pytest.mark.parametrize("kind", [TradingDayKind.EXCHANGE_HOLIDAY, TradingDayKind.SPECIAL_NON_TRADING_DAY])
def test_direct_positive_non_trading_day_is_established_without_session(kind: TradingDayKind) -> None:
    result = resolve(days=(day(kind),), sessions=())
    assert result.disposition is ResolutionDisposition.ESTABLISHED
    assert result.reason_code is CalendarSessionResolutionReason.AUTHORITATIVE_NON_TRADING_DAY
    assert result.trading_day is not None
    assert result.session is None and result.utc_open is None and result.utc_close is None


def test_direct_authorized_correction_selects_visible_tip() -> None:
    root = version(1, knowledge=1)
    successor = version(2, knowledge=5, supersedes=1)
    q = query()
    authority = CalendarCorrectionAuthorityAssessment(
        q.calendar_id, successor.calendar_version_id, root.calendar_version_id,
        instant(6), instant(7), policy(), CalendarCorrectionAuthorityDecision.AUTHORIZED,
        (evidence(700),), ValidationState.VALID,
    )
    result = resolve(
        q, versions=(root, successor), coverages=(historical(1), historical(2)),
        days=(day(version_number=1), day(version_number=2)),
        sessions=(session(1), session(2)), current=(currentness(2),),
        authorities=(authority,),
    )
    assert result.disposition is ResolutionDisposition.ESTABLISHED
    assert result.selected_calendar_version_id == successor.calendar_version_id
    assert result.correction_authority_assessment_digests == (authority.content_digest,)


def test_direct_unauthorized_correction_preserves_predecessor() -> None:
    root = version(1, knowledge=1)
    successor = version(2, knowledge=5, supersedes=1)
    authority = CalendarCorrectionAuthorityAssessment(
        root.calendar_id, successor.calendar_version_id, root.calendar_version_id,
        instant(6), instant(7), policy(), CalendarCorrectionAuthorityDecision.NOT_AUTHORIZED,
        (evidence(701),), ValidationState.VALID,
    )
    result = resolve(
        versions=(root, successor), coverages=(historical(1), historical(2)),
        days=(day(version_number=1), day(version_number=2)),
        sessions=(session(1), session(2)), authorities=(authority,),
    )
    assert result.disposition is ResolutionDisposition.ESTABLISHED
    assert result.selected_calendar_version_id == root.calendar_version_id


def test_direct_authoritative_absence_requires_independent_negative_evidence() -> None:
    q = query()
    manifest = candidate_manifest(q, (), (), (), ())
    negative = CalendarNegativeCoverageEvidence(
        q.calendar_id, q.local_date, q.session_kind, q.effective_as_of,
        q.knowledge_cutoff, q.evaluation_at, manifest.content_digest,
        CalendarNegativeFact.NO_SUPPORTED_SESSION, instant(5),
        (evidence(800),), policy(), ValidationState.VALID,
    )
    result = resolve(
        q, versions=(), coverages=(), days=(), sessions=(), manifest=manifest,
        negative=negative, current=(), corpora=(),
    )
    assert_restrictive(
        result, ResolutionDisposition.ABSENT,
        CalendarSessionResolutionReason.AUTHORITATIVE_NEGATIVE_COVERAGE,
    )


@pytest.mark.parametrize(
    ("case_id", "mutation", "disposition", "reason"),
    [
        ("S2-A01", {"manifest": None}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_MISSING),
        ("S2-A02", {"coverages": ()}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.COVERAGE_NOT_ESTABLISHED),
        ("S2-A03", {"days": ()}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.NO_APPLICABLE_VERSION),
        ("S2-A04", {"sessions": ()}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.SESSION_NOT_ESTABLISHED),
        ("S2-A05", {"current": ()}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_MISSING),
        ("S2-A06", {"current": (currentness(state=CalendarCurrentness.STALE),)}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CALENDAR_VERSION_STALE),
        ("S2-A07", {"current": (currentness(state=CalendarCurrentness.UNKNOWN),)}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CALENDAR_CURRENTNESS_UNKNOWN),
        ("S2-A08", {"corpora": ()}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.TIMEZONE_RULE_NOT_ESTABLISHED),
        ("S2-A09", {"coverages": (historical(state=CalendarCoverageState.INCOMPLETE),)}, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.COVERAGE_NOT_ESTABLISHED),
        ("S2-A10", {"coverages": (historical(state=CalendarCoverageState.CONFLICTING),)}, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.COVERAGE_CONFLICT),
    ],
)
def test_s2_a01_a10_restrictive_phase_outcomes(case_id: str, mutation: dict[str, object], disposition: ResolutionDisposition, reason: CalendarSessionResolutionReason) -> None:
    del case_id
    result = resolve(**cast(Any, mutation))
    assert_restrictive(result, disposition, reason)


def test_s2_a11_a16_query_manifest_and_effective_boundaries() -> None:
    with pytest.raises(ValueError, match="knowledge_cutoff"):
        query(knowledge=40, evaluation=30)
    q = query(effective=100)
    assert_restrictive(
        resolve(q, versions=(version(effective_to=50),)),
        ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.EFFECTIVE_BOUNDARY_MISMATCH,
    )
    base = candidates()
    assert base.candidate_set_coverage is not None
    future = replace(base.candidate_set_coverage, knowledge_from=instant(21))
    assert_restrictive(
        resolve(manifest=future), ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_FUTURE,
    )
    mismatched = replace(base.candidate_set_coverage, local_date=date(2026, 3, 31))
    assert_restrictive(
        resolve(manifest=mismatched), ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_MISMATCH,
    )
    incomplete = replace(base.candidate_set_coverage, candidate_fact_descriptors=())
    assert_restrictive(
        resolve(manifest=incomplete), ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.CANDIDATE_SET_INCOMPLETE,
    )
    surplus = replace(base.candidate_set_coverage, candidate_version_ids=(CalendarVersionId(uid(1)), CalendarVersionId(uid(9))))
    assert_restrictive(
        resolve(manifest=surplus), ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.CANDIDATE_SET_SURPLUS,
    )


def test_s2_a17_a22_currentness_validation_and_conflict() -> None:
    future = currentness(knowledge=21)
    assert_restrictive(resolve(current=(future,)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_FUTURE)
    expired = currentness(valid_to=29)
    assert_restrictive(resolve(current=(expired,)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_OUTSIDE_VALIDITY)
    invalid = replace(currentness(), validation_state=ValidationState.INVALID)
    assert_restrictive(resolve(current=(invalid,)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_NOT_AUTHORITATIVE)
    conflicting = (currentness(), currentness(state=CalendarCurrentness.STALE))
    assert_restrictive(resolve(current=conflicting), ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_CONFLICT)
    value = currentness()
    with pytest.raises(FrozenInstanceError):
        value.state = CalendarCurrentness.STALE  # type: ignore[misc]


def test_s2_a23_a29_negative_and_positive_conflicts() -> None:
    q = query()
    base = candidates(q=q)
    assert base.candidate_set_coverage is not None
    negative = CalendarNegativeCoverageEvidence(
        q.calendar_id, q.local_date, q.session_kind, q.effective_as_of,
        q.knowledge_cutoff, q.evaluation_at, base.candidate_set_coverage.content_digest,
        CalendarNegativeFact.NO_SUPPORTED_SESSION, instant(5), (evidence(801),),
        policy(), ValidationState.VALID,
    )
    result = resolve(q, negative=negative)
    assert_restrictive(result, ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.POSITIVE_NEGATIVE_SESSION_CONFLICT)
    wrong_digest = replace(negative, candidate_set_digest=historical().content_digest)
    assert resolve(q, negative=wrong_digest).disposition is ResolutionDisposition.ESTABLISHED
    future = replace(negative, knowledge_from=instant(21))
    assert resolve(q, negative=future).disposition is ResolutionDisposition.ESTABLISHED
    invalid = replace(negative, validation_state=ValidationState.INVALID)
    assert resolve(q, negative=invalid).disposition is ResolutionDisposition.ESTABLISHED


def test_s2_a30_a36_correction_authority_fail_closed() -> None:
    root = version(1)
    successor = version(2, knowledge=5, supersedes=1)
    common: dict[str, Any] = {
        "versions": (root, successor),
        "coverages": (historical(1), historical(2)),
        "days": (day(version_number=1), day(version_number=2)),
        "sessions": (session(1), session(2)),
    }
    assert_restrictive(resolve(**common), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_MISSING)
    self_certified = CalendarCorrectionAuthorityAssessment(
        root.calendar_id, successor.calendar_version_id, root.calendar_version_id,
        instant(6), instant(7), policy(), CalendarCorrectionAuthorityDecision.AUTHORIZED,
        successor.evidence_refs, ValidationState.VALID,
    )
    assert_restrictive(resolve(**common, authorities=(self_certified,)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_SELF_CERTIFIED)
    authorized = replace(self_certified, evidence_refs=(evidence(900),))
    unauthorized = replace(authorized, decision=CalendarCorrectionAuthorityDecision.NOT_AUTHORIZED)
    assert_restrictive(resolve(**common, authorities=(authorized, unauthorized)), ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_CONFLICT)


def test_s2_a37_a40_timezone_and_utc_fail_closed() -> None:
    wrong = TimezoneRuleCorpusInput(timezone_ref().timezone_rule_id, b"wrong")
    assert_restrictive(resolve(corpora=(wrong,)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH)
    duplicate = (TimezoneRuleCorpusInput(timezone_ref().timezone_rule_id, CORPUS), wrong)
    assert_restrictive(resolve(corpora=duplicate), ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.TIMEZONE_RULE_NOT_ESTABLISHED)
    bad_session = replace(session(), utc_close=instant(10_000_000))
    assert_restrictive(resolve(sessions=(bad_session,)), ResolutionDisposition.CONFLICTING, CalendarSessionResolutionReason.UTC_BOUNDARY_CONFLICT)


def test_s2_a41_effective_boundary_inconsistency() -> None:
    assert_restrictive(
        resolve(query(effective=100), versions=(version(effective_to=50),)),
        ResolutionDisposition.NOT_ESTABLISHED,
        CalendarSessionResolutionReason.EFFECTIVE_BOUNDARY_MISMATCH,
    )


def test_s2_a42_future_candidate_set_manifest() -> None:
    base = candidates()
    assert base.candidate_set_coverage is not None
    future = replace(base.candidate_set_coverage, knowledge_from=instant(21))
    assert_restrictive(resolve(manifest=future), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CANDIDATE_SET_COVERAGE_FUTURE)


def test_s2_a43_future_candidate_identity_cannot_change_history() -> None:
    future = version(2, knowledge=21, supersedes=1)
    baseline = resolve()
    with_future = resolve(
        versions=(version(), future),
        coverages=(historical(), historical(2)),
        days=(day(), day(version_number=2)),
        sessions=(session(), session(2)),
    )
    assert with_future == baseline


def test_s2_a44_future_or_out_of_validity_currentness() -> None:
    assert_restrictive(resolve(current=(currentness(knowledge=21),)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_FUTURE)
    assert_restrictive(resolve(current=(currentness(valid_from=31),)), ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_OUTSIDE_VALIDITY)


def test_s2_a45_correction_authority_self_certification() -> None:
    root = version(1)
    successor = version(2, knowledge=5, supersedes=1)
    assessment = CalendarCorrectionAuthorityAssessment(
        root.calendar_id, successor.calendar_version_id, root.calendar_version_id,
        instant(6), instant(7), policy(), CalendarCorrectionAuthorityDecision.AUTHORIZED,
        successor.correction_evidence_refs, ValidationState.VALID,
    )
    result = resolve(
        versions=(root, successor), coverages=(historical(1), historical(2)),
        days=(day(version_number=1), day(version_number=2)),
        sessions=(session(1), session(2)), authorities=(assessment,),
    )
    assert_restrictive(result, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CORRECTION_AUTHORITY_SELF_CERTIFIED)


def test_s2_a46_diagnostic_evidence_cannot_leak_authority() -> None:
    result = resolve(current=())
    assert_restrictive(result, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.CURRENTNESS_ASSESSMENT_MISSING)
    assert result.evidence_refs == ()


def test_deterministic_permutation_independence_and_no_mutation() -> None:
    base = candidates()
    expected = resolve_calendar_session(query=query(), candidates=base)
    before = repr(base)
    for ordered in permutations(base.calendar_versions):
        result = resolve_calendar_session(query=query(), candidates=replace(base, calendar_versions=ordered))
        assert result == expected
    assert repr(base) == before


def test_resource_exhaustion_is_explicit_and_never_truncated() -> None:
    many = tuple(version(number) for number in range(1, 4098))
    result = resolve(
        versions=many, coverages=(), days=(), sessions=(), current=(),
        corpora=(), manifest=None,
    )
    assert_restrictive(result, ResolutionDisposition.NOT_ESTABLISHED, CalendarSessionResolutionReason.RESOURCE_LIMIT_EXCEEDED)

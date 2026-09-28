from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from automated_trading_bot.calendars import (
    MAX_EVIDENCE_REFS,
    MAX_LINEAGE_DEPTH,
    MAX_LINEAGE_VERSIONS,
    CalendarContentDigest,
    CalendarCoverageError,
    CalendarCoverageState,
    CalendarCurrentness,
    CalendarFactConflict,
    CalendarId,
    CalendarLineageValidationError,
    CalendarVersion,
    CalendarVersionId,
    HistoricalCoverage,
    LocalWallTime,
    SessionDefinition,
    SessionKind,
    SessionScheduleVariation,
    TimezoneId,
    TimezoneRuleId,
    TimezoneRuleRef,
    TradingDayDefinition,
    TradingDayKind,
    coverage_disposition,
    validate_calendar_lineage,
    validate_coverage_independence,
    validate_fact_bindings,
    verify_calendar_content_digest,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceIdentityConflict,
    EvidenceRef,
    ResolutionDisposition,
    SourceId,
    ValidationPolicyId,
    ValidationState,
)


RULE_CORPUS = b"tz-rule-corpus-v1"
DAY = date(2026, 3, 30)


def uuid4(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def instant(seconds: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def evidence(number: int = 1, *, content: bytes = b"evidence") -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:atis/calendar"),
        DatasetId("dataset:atis/calendar-v1"),
        EvidenceId(f"evidence:atis/calendar-{number}"),
        EvidenceContentDigest.from_bytes(content),
    )


def policy() -> ValidationPolicyId:
    return ValidationPolicyId("validation-policy:atis/calendar-s1-v1")


def rule_ref(
    *,
    corpus: bytes = RULE_CORPUS,
    state: ValidationState = ValidationState.VALID,
    rule_id: str = "timezone-rule:atis/europe-london-2026a",
) -> TimezoneRuleRef:
    return TimezoneRuleRef(
        timezone_id=TimezoneId("Europe/London"),
        timezone_rule_id=TimezoneRuleId(rule_id),
        rule_content_digest=EvidenceContentDigest.from_bytes(corpus),
        evidence_refs=(evidence(90, content=corpus),),
        validation_policy_id=policy(),
        validation_state=state,
    )


def calendar_version(
    number: int = 1,
    *,
    calendar_number: int = 1,
    knowledge: int = 0,
    supersedes: int | None = None,
    market_code: str = "GB",
    venue_code: str = "XLON",
    timezone: TimezoneRuleRef | None = None,
    state: ValidationState = ValidationState.VALID,
) -> CalendarVersion:
    return CalendarVersion(
        calendar_version_id=CalendarVersionId(uuid4(number)),
        calendar_id=CalendarId(uuid4(calendar_number)),
        market_code=market_code,
        venue_code=venue_code,
        effective_from=instant(),
        effective_to=None,
        knowledge_from=instant(knowledge),
        timezone_rule_ref=rule_ref() if timezone is None else timezone,
        evidence_refs=(evidence(number),),
        validation_policy_id=policy(),
        validation_state=state,
        supersedes_version_id=None if supersedes is None else CalendarVersionId(uuid4(supersedes)),
        correction_reason=None if supersedes is None else "SOURCE_CORRECTION",
        correction_evidence_refs=() if supersedes is None else (evidence(1000 + number),),
    )


def coverage(
    *,
    state: CalendarCoverageState = CalendarCoverageState.COVERED,
    validation: ValidationState = ValidationState.VALID,
    refs: tuple[EvidenceRef, ...] = (evidence(70),),
    version_number: int = 1,
) -> HistoricalCoverage:
    return HistoricalCoverage(
        calendar_id=CalendarId(uuid4(1)),
        calendar_version_id=CalendarVersionId(uuid4(version_number)),
        local_date_from=date(2026, 1, 1),
        local_date_to=date(2027, 1, 1),
        coverage_state=state,
        coverage_knowledge_from=instant(5),
        evidence_refs=refs,
        validation_policy_id=policy(),
        validation_state=validation,
    )


def day_fact(
    kind: TradingDayKind = TradingDayKind.REGULAR_TRADING_DAY,
    *,
    refs: tuple[EvidenceRef, ...] = (evidence(1),),
    version_number: int = 1,
    calendar_number: int = 1,
) -> TradingDayDefinition:
    return TradingDayDefinition(
        calendar_id=CalendarId(uuid4(calendar_number)),
        calendar_version_id=CalendarVersionId(uuid4(version_number)),
        local_date=DAY,
        day_kind=kind,
        evidence_refs=refs,
        validation_policy_id=policy(),
        validation_state=ValidationState.VALID,
    )


def wall(hour: int, minute: int = 0, *, offset: int | None = 60, ambiguous: bool = False,
         nonexistent: bool = False, fold: int | None = None) -> LocalWallTime:
    return LocalWallTime(
        hour=hour,
        minute=minute,
        utc_offset_minutes=offset,
        ambiguous=ambiguous,
        nonexistent=nonexistent,
        fold=fold,
    )


def session(
    *,
    variation: SessionScheduleVariation = SessionScheduleVariation.STANDARD,
    local_open: LocalWallTime | None = None,
    local_close: LocalWallTime | None = None,
    utc_open: Timestamp | None = None,
    utc_close: Timestamp | None = None,
    timezone: TimezoneRuleRef | None = None,
    rule_corpus: bytes = RULE_CORPUS,
    refs: tuple[EvidenceRef, ...] = (evidence(2),),
    version_number: int = 1,
) -> SessionDefinition:
    opening = wall(8) if local_open is None else local_open
    closing = wall(16, 30) if local_close is None else local_close
    tz = rule_ref() if timezone is None else timezone
    return SessionDefinition(
        calendar_id=CalendarId(uuid4(1)),
        calendar_version_id=CalendarVersionId(uuid4(version_number)),
        local_date=DAY,
        session_kind=SessionKind.REGULAR,
        schedule_variation=variation,
        local_open=opening,
        local_close=closing,
        utc_open=opening.to_utc(DAY, tz, rule_corpus) if utc_open is None else utc_open,
        utc_close=closing.to_utc(DAY, tz, rule_corpus) if utc_close is None else utc_close,
        timezone_rule_ref=tz,
        evidence_refs=refs,
        validation_policy_id=policy(),
        validation_state=ValidationState.VALID,
    )


def test_direct_identity_round_trip_and_namespace_separation() -> None:
    calendar_id = CalendarId(uuid4(1))
    version_id = CalendarVersionId(uuid4(1))
    assert CalendarId.parse(calendar_id.to_string()) == calendar_id
    assert CalendarVersionId.parse(version_id.to_string()) == version_id
    assert calendar_id != version_id
    with pytest.raises(ValueError):
        CalendarId.parse(version_id.to_string())


@pytest.mark.parametrize("kind", [CalendarId, CalendarVersionId])
@pytest.mark.parametrize("value", [UUID(int=0), UUID(int=1), UUID("00000000-0000-1000-8000-000000000001")])
def test_direct_identity_rejects_nil_and_non_v4(kind: type, value: UUID) -> None:
    with pytest.raises(ValueError):
        kind(value)


@pytest.mark.parametrize("value", ["gb", "GB ", "G B", "ÉU", "A" * 33])
def test_direct_scope_codes_are_canonical_ascii(value: str) -> None:
    with pytest.raises(ValueError, match="canonical descriptive scope"):
        calendar_version(market_code=value)


def test_direct_version_interval_knowledge_authority_and_currentness_separation() -> None:
    value = replace(calendar_version(), effective_from=instant(10), effective_to=instant(20))
    assert value.contains_effective(instant(10))
    assert not value.contains_effective(instant(20))
    assert not value.is_known_at(instant(-1))
    assert value.is_known_at(instant())
    assert value.is_authoritative
    assert not hasattr(value, "currentness")
    assert set(CalendarCurrentness) == {
        CalendarCurrentness.CURRENT,
        CalendarCurrentness.STALE,
        CalendarCurrentness.UNKNOWN,
    }


def test_direct_factual_records_are_immutable_and_digest_is_deterministic() -> None:
    first = calendar_version()
    second = replace(first, calendar_version_id=CalendarVersionId(uuid4(2)))
    assert first.content_digest == second.content_digest
    assert first.calendar_version_id != second.calendar_version_id
    with pytest.raises(FrozenInstanceError):
        first.market_code = "US"
    verify_calendar_content_digest(first, first.content_digest)
    with pytest.raises(ValueError, match="does not match"):
        verify_calendar_content_digest(first, CalendarContentDigest("sha256:" + "f" * 64))


@pytest.mark.parametrize(
    "kind",
    [
        TradingDayKind.REGULAR_TRADING_DAY,
        TradingDayKind.EXCHANGE_HOLIDAY,
        TradingDayKind.SPECIAL_NON_TRADING_DAY,
    ],
)
def test_direct_day_fact_vocabulary(kind: TradingDayKind) -> None:
    value = day_fact(kind)
    assert value.day_kind is kind
    assert value.validation_state is ValidationState.VALID


def test_direct_standard_and_early_close_are_explicit_distinct_facts() -> None:
    standard = session()
    early = session(
        variation=SessionScheduleVariation.EARLY_CLOSE,
        local_close=wall(12, 30),
    )
    assert standard.session_kind is SessionKind.REGULAR
    assert early.schedule_variation is SessionScheduleVariation.EARLY_CLOSE
    assert standard.content_digest != early.content_digest
    with pytest.raises(CalendarFactConflict, match="DUPLICATE_FACT_IDENTITY"):
        validate_fact_bindings(calendar_version(), (standard, early))


def test_direct_timezone_rule_content_binding_and_utc_conversion() -> None:
    value = session()
    value.timezone_rule_ref.verify_rule_corpus(RULE_CORPUS)
    value.verify_utc_boundaries(RULE_CORPUS)
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        value.timezone_rule_ref.verify_rule_corpus(b"other")


def test_direct_dst_forward_and_backward_fail_closed_or_bind_exactly() -> None:
    with pytest.raises(ValueError, match="NONEXISTENT"):
        session(local_open=wall(1, 30, nonexistent=True, offset=None))
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        session(local_open=wall(1, 30, ambiguous=True, fold=None))
    bound = wall(1, 30, offset=0, ambiguous=True, fold=1)
    assert bound.to_utc(DAY, rule_ref(), RULE_CORPUS) == Timestamp(
        datetime(2026, 3, 30, 1, 30, tzinfo=UTC)
    )


def test_direct_coverage_is_half_open_restrictive_and_independent() -> None:
    value = coverage()
    assert value.contains(date(2026, 1, 1))
    assert not value.contains(date(2027, 1, 1))
    assert coverage_disposition(value, DAY, facts=(day_fact(),)) is ResolutionDisposition.ESTABLISHED
    assert coverage_disposition(None, DAY) is ResolutionDisposition.NOT_ESTABLISHED
    assert coverage_disposition(coverage(state=CalendarCoverageState.INCOMPLETE), DAY) is ResolutionDisposition.NOT_ESTABLISHED
    assert coverage_disposition(coverage(state=CalendarCoverageState.CONFLICTING), DAY) is ResolutionDisposition.CONFLICTING


def test_direct_coverage_knowledge_inheritance_is_explicit() -> None:
    value = replace(coverage(), coverage_knowledge_from=None)
    version = calendar_version()
    with pytest.raises(CalendarCoverageError, match="NOT_ATTRIBUTABLE"):
        value.knowledge_from(version, same_atomic_publication=False)
    assert value.knowledge_from(version, same_atomic_publication=True) == version.knowledge_from


def test_direct_lineage_is_complete_finite_and_input_order_independent() -> None:
    root = calendar_version(1, knowledge=1)
    second = calendar_version(2, knowledge=2, supersedes=1)
    third = calendar_version(3, knowledge=3, supersedes=2)
    assert validate_calendar_lineage((third, root, second)) == validate_calendar_lineage((second, third, root))


def test_direct_resource_bounds_are_exact_and_no_truncation_occurs() -> None:
    assert (MAX_EVIDENCE_REFS, MAX_LINEAGE_VERSIONS, MAX_LINEAGE_DEPTH) == (64, 4096, 256)
    TimezoneRuleRef(
        TimezoneId("Europe/London"),
        TimezoneRuleId("timezone-rule:atis/rules"),
        EvidenceContentDigest.from_bytes(RULE_CORPUS),
        tuple(evidence(number) for number in range(1, 65)),
        policy(),
        ValidationState.VALID,
    )
    with pytest.raises(ValueError, match="limit of 64"):
        TimezoneRuleRef(
            TimezoneId("Europe/London"),
            TimezoneRuleId("timezone-rule:atis/rules"),
            EvidenceContentDigest.from_bytes(RULE_CORPUS),
            tuple(evidence(number) for number in range(1, 66)),
            policy(),
            ValidationState.VALID,
        )


def test_a01_missing_calendar_id() -> None:
    with pytest.raises(TypeError, match="calendar_id"):
        replace(calendar_version(), calendar_id=None)


def test_a02_missing_calendar_version_id() -> None:
    with pytest.raises(TypeError, match="calendar_version_id"):
        replace(calendar_version(), calendar_version_id=None)


def test_a03_wrong_calendar_subject() -> None:
    with pytest.raises(CalendarFactConflict, match="WRONG_CALENDAR_VERSION"):
        validate_fact_bindings(calendar_version(), (day_fact(calendar_number=2),))


def test_a04_wrong_market_or_venue() -> None:
    with pytest.raises(CalendarLineageValidationError, match="CONFLICTING_SCOPE"):
        validate_calendar_lineage((calendar_version(1), calendar_version(2, venue_code="XNYS")))


def test_a05_outside_historical_coverage() -> None:
    assert coverage_disposition(coverage(), date(2028, 1, 1)) is ResolutionDisposition.NOT_ESTABLISHED


def test_a06_incomplete_historical_coverage() -> None:
    value = coverage(state=CalendarCoverageState.INCOMPLETE)
    assert coverage_disposition(value, DAY) is ResolutionDisposition.NOT_ESTABLISHED


def test_a07_fabricated_negative_coverage() -> None:
    fact = day_fact(refs=(evidence(7),))
    value = coverage(refs=(evidence(7),))
    with pytest.raises(CalendarCoverageError, match="SELF_CERTIFICATION"):
        validate_coverage_independence(value, (fact,))


def test_a08_missing_holiday_evidence() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        day_fact(TradingDayKind.EXCHANGE_HOLIDAY, refs=())


def test_a09_conflicting_holiday_evidence() -> None:
    first = evidence(9, content=b"holiday")
    second = evidence(9, content=b"open")
    with pytest.raises(EvidenceIdentityConflict):
        day_fact(TradingDayKind.EXCHANGE_HOLIDAY, refs=(first, second))


@pytest.mark.parametrize(
    "left,right",
    [
        (session(), session(variation=SessionScheduleVariation.EARLY_CLOSE, local_close=wall(12))),
        (session(variation=SessionScheduleVariation.EARLY_CLOSE, local_close=wall(12)), session()),
    ],
    ids=["A10", "A11"],
)
def test_a10_a11_no_standard_early_close_substitution(left: SessionDefinition, right: SessionDefinition) -> None:
    with pytest.raises(CalendarFactConflict, match="DUPLICATE_FACT_IDENTITY"):
        validate_fact_bindings(calendar_version(), (left, right))


def test_a12_missing_close() -> None:
    with pytest.raises(TypeError, match="LocalWallTime"):
        replace(session(), local_close=None)


def test_a13_conflicting_close() -> None:
    with pytest.raises(CalendarFactConflict, match="DUPLICATE_FACT_IDENTITY"):
        validate_fact_bindings(calendar_version(), (session(), session(local_close=wall(15))))


def test_a14_malformed_open_or_close() -> None:
    with pytest.raises(ValueError, match="canonical range"):
        wall(24)


def test_a15_close_not_after_open() -> None:
    with pytest.raises(ValueError, match="after local_open"):
        session(local_open=wall(16), local_close=wall(8))


def test_a16_missing_timezone() -> None:
    with pytest.raises(TypeError, match="timezone_rule_ref"):
        replace(calendar_version(), timezone_rule_ref=None)


def test_a17_timezone_mismatch() -> None:
    different = rule_ref(rule_id="timezone-rule:atis/different")
    with pytest.raises(CalendarFactConflict, match="TIMEZONE_RULE_MISMATCH"):
        validate_fact_bindings(calendar_version(), (session(timezone=different),))


def test_a18_missing_timezone_rule_id() -> None:
    with pytest.raises(ValueError, match="timezone-rule"):
        TimezoneRuleId("")


def test_a19_dst_forward_transition() -> None:
    with pytest.raises(ValueError, match="NONEXISTENT"):
        session(local_open=wall(1, 30, offset=None, nonexistent=True))


def test_a20_dst_backward_transition() -> None:
    value = wall(1, 30, offset=0, ambiguous=True, fold=1)
    assert value.to_utc(DAY, rule_ref(), RULE_CORPUS).value.tzinfo is UTC


def test_a21_ambiguous_local_time() -> None:
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        session(local_open=wall(1, 30, ambiguous=True))


def test_a22_nonexistent_local_time() -> None:
    with pytest.raises(ValueError, match="NONEXISTENT"):
        wall(1, 30, offset=None, nonexistent=True).to_utc(DAY, rule_ref(), RULE_CORPUS)


def test_a23_utc_conversion_disagreement() -> None:
    value = session(utc_open=instant())
    with pytest.raises(ValueError, match="UTC_CONVERSION_DISAGREEMENT"):
        value.verify_utc_boundaries(RULE_CORPUS)


def test_a24_future_knowledge() -> None:
    assert not calendar_version(knowledge=20).is_known_at(instant(19))


def test_a25_current_calendar_substitution() -> None:
    with pytest.raises(CalendarFactConflict, match="WRONG_CALENDAR_VERSION"):
        validate_fact_bindings(calendar_version(2), (day_fact(version_number=1),))


def test_a26_stale_evidence_is_not_factual_content() -> None:
    value = calendar_version()
    assert CalendarCurrentness.STALE.value == "STALE"
    assert not hasattr(value, "currentness")


def test_a27_invalid_evidence() -> None:
    assert not calendar_version(state=ValidationState.INVALID).is_authoritative


def test_a28_correction_without_predecessor() -> None:
    with pytest.raises(CalendarLineageValidationError, match="MISSING_PREDECESSOR"):
        validate_calendar_lineage((calendar_version(2, knowledge=2, supersedes=1),))


def test_a29_cross_calendar_correction() -> None:
    with pytest.raises(CalendarLineageValidationError, match="CROSS_CALENDAR"):
        validate_calendar_lineage((calendar_version(1), calendar_version(2, calendar_number=2)))


def test_a30_correction_cycle() -> None:
    first = calendar_version(1, knowledge=1, supersedes=2)
    second = calendar_version(2, knowledge=2, supersedes=1)
    with pytest.raises(CalendarLineageValidationError, match="LINEAGE_CYCLE"):
        validate_calendar_lineage((first, second))


def test_a31_branching_correction() -> None:
    values = (
        calendar_version(1, knowledge=1),
        calendar_version(2, knowledge=2, supersedes=1),
        calendar_version(3, knowledge=3, supersedes=1),
    )
    with pytest.raises(CalendarLineageValidationError, match="BRANCHING"):
        validate_calendar_lineage(values)


def test_a32_in_place_mutation_attempt() -> None:
    value = calendar_version()
    with pytest.raises(FrozenInstanceError):
        value.venue_code = "XNYS"
    with pytest.raises(ValueError, match="does not match"):
        verify_calendar_content_digest(
            replace(value, venue_code="XNYS"),
            value.content_digest,
        )


def test_a33_input_order_dependence() -> None:
    root = calendar_version(1, knowledge=1)
    successor = calendar_version(2, knowledge=2, supersedes=1)
    assert validate_calendar_lineage((root, successor)) == validate_calendar_lineage((successor, root))


def test_a34_resource_exhaustion() -> None:
    with pytest.raises(ValueError, match="limit of 64"):
        replace(calendar_version(), evidence_refs=tuple(evidence(number) for number in range(1, 66)))


def test_a35_timezone_rule_corpus_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        rule_ref().verify_rule_corpus(b"wrong")


def test_a36_timezone_rule_label_reused_with_different_content() -> None:
    left = session()
    right = session(timezone=rule_ref(corpus=b"different"), rule_corpus=b"different")
    with pytest.raises(CalendarFactConflict, match="TIMEZONE_RULE_MISMATCH"):
        validate_fact_bindings(calendar_version(), (left, right))


def test_a37_coverage_self_certification() -> None:
    shared = evidence(37)
    value = coverage(refs=(shared,))
    assert coverage_disposition(value, DAY, facts=(day_fact(refs=(shared,)),)) is ResolutionDisposition.NOT_ESTABLISHED


def test_a38_currentness_change_cannot_mutate_factual_version() -> None:
    value = calendar_version()
    digest = value.content_digest
    currentness = CalendarCurrentness.CURRENT
    currentness = CalendarCurrentness.STALE
    assert currentness is CalendarCurrentness.STALE
    assert value.content_digest == digest


def test_a39_duplicate_fact_identity_different_content() -> None:
    with pytest.raises(CalendarFactConflict, match="DUPLICATE_FACT_IDENTITY"):
        validate_fact_bindings(
            calendar_version(),
            (day_fact(TradingDayKind.REGULAR_TRADING_DAY), day_fact(TradingDayKind.EXCHANGE_HOLIDAY)),
        )


def test_a40_child_fact_bound_to_wrong_calendar_version() -> None:
    with pytest.raises(CalendarFactConflict, match="WRONG_CALENDAR_VERSION"):
        validate_fact_bindings(calendar_version(1), (day_fact(version_number=2),))

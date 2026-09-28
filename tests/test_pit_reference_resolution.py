from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments import (
    CandidateSetCoverage,
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    InstrumentReferenceVersion,
    ListingId,
    ListingReferenceVersion,
    NegativeCoverageEvidence,
    ReferenceRecordKind,
    ReferenceVersionId,
    ResolutionDisposition,
    SourceId,
    TradabilityState,
    ValidationPolicyId,
    ValidationState,
    resolve_instrument_reference,
    resolve_listing_reference,
)


def uuid4(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def instant(seconds: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def evidence(number: int, *, role: str = "candidate") -> EvidenceRef:
    return EvidenceRef(
        SourceId(f"source:atis/{role}"),
        DatasetId(f"dataset:atis/{role}"),
        EvidenceId(f"evidence:atis/{role}-{number}"),
        EvidenceContentDigest.from_bytes(f"{role}-{number}".encode()),
    )


def instrument_version(
    number: int,
    *,
    instrument_number: int = 1,
    effective_from: int = 0,
    effective_to: int | None = None,
    knowledge_from: int = 0,
    supersedes: int | None = None,
    state: ValidationState = ValidationState.VALID,
    currency: str = "USD",
) -> InstrumentReferenceVersion:
    return InstrumentReferenceVersion.create(
        reference_version_id=ReferenceVersionId(uuid4(number)),
        instrument_id=InstrumentId(uuid4(instrument_number)),
        effective_from=instant(effective_from),
        effective_to=None if effective_to is None else instant(effective_to),
        knowledge_from=instant(knowledge_from),
        evidence_refs=(evidence(number),),
        validation_policy_id=ValidationPolicyId("validation-policy:atis/rb1-s2"),
        validation_state=state,
        supersedes_version_id=None if supersedes is None else ReferenceVersionId(uuid4(supersedes)),
        currency=currency,
        security_type="COMMON_STOCK",
    )


def listing_version(
    number: int,
    *,
    instrument_number: int = 1,
    listing_number: int = 2,
    effective_from: int = 0,
    effective_to: int | None = None,
    knowledge_from: int = 0,
    supersedes: int | None = None,
    state: ValidationState = ValidationState.VALID,
    tradability: TradabilityState = TradabilityState.ACTIVE,
) -> ListingReferenceVersion:
    return ListingReferenceVersion.create(
        reference_version_id=ReferenceVersionId(uuid4(number)),
        instrument_id=InstrumentId(uuid4(instrument_number)),
        listing_id=ListingId(uuid4(listing_number)),
        effective_from=instant(effective_from),
        effective_to=None if effective_to is None else instant(effective_to),
        knowledge_from=instant(knowledge_from),
        evidence_refs=(evidence(number),),
        validation_policy_id=ValidationPolicyId("validation-policy:atis/rb1-s2"),
        validation_state=state,
        tradability_state=tradability,
        tradability_reason="ATTRIBUTABLE_HISTORY",
        supersedes_version_id=None if supersedes is None else ReferenceVersionId(uuid4(supersedes)),
        symbol="ABC",
        mic="XLON",
    )


def coverage(
    candidates: tuple[InstrumentReferenceVersion | ListingReferenceVersion, ...],
    *,
    kind: ReferenceRecordKind = ReferenceRecordKind.INSTRUMENT,
    instrument_number: int = 1,
    listing_number: int | None = None,
    as_of: int = 10,
    cutoff: int = 10,
    state: ValidationState = ValidationState.VALID,
    evidence_ref: EvidenceRef | None = None,
) -> CandidateSetCoverage:
    return CandidateSetCoverage(
        record_kind=kind,
        instrument_id=InstrumentId(uuid4(instrument_number)),
        listing_id=None if listing_number is None else ListingId(uuid4(listing_number)),
        effective_as_of=instant(as_of),
        knowledge_cutoff=instant(cutoff),
        candidate_version_ids=tuple(item.reference_version_id for item in candidates),
        evidence_refs=(evidence(900, role="coverage") if evidence_ref is None else evidence_ref,),
        validation_policy_id=ValidationPolicyId("validation-policy:atis/rb1-s2-coverage"),
        validation_state=state,
    )


def negative(value: CandidateSetCoverage, *, state: ValidationState = ValidationState.VALID) -> NegativeCoverageEvidence:
    return NegativeCoverageEvidence(
        record_kind=value.record_kind,
        instrument_id=value.instrument_id,
        listing_id=value.listing_id,
        effective_as_of=value.effective_as_of,
        knowledge_cutoff=value.knowledge_cutoff,
        candidate_set_digest=value.content_digest,
        evidence_refs=(evidence(901, role="negative-coverage"),),
        validation_policy_id=ValidationPolicyId("validation-policy:atis/rb1-s2-negative"),
        validation_state=state,
    )


def resolve_instrument(
    candidates: tuple[InstrumentReferenceVersion, ...],
    *,
    as_of: int = 10,
    cutoff: int = 10,
    supplied_coverage: CandidateSetCoverage | None = None,
    negative_coverage: NegativeCoverageEvidence | None = None,
):
    return resolve_instrument_reference(
        instrument_id=InstrumentId(uuid4(1)),
        effective_as_of=instant(as_of),
        knowledge_cutoff=instant(cutoff),
        candidates=candidates,
        coverage=coverage(candidates, as_of=as_of, cutoff=cutoff) if supplied_coverage is None else supplied_coverage,
        negative_coverage=negative_coverage,
    )


@pytest.mark.parametrize(
    "as_of,expected",
    [(10, ResolutionDisposition.ESTABLISHED), (19, ResolutionDisposition.ESTABLISHED), (20, ResolutionDisposition.NOT_ESTABLISHED)],
)
def test_half_open_effective_boundaries(as_of: int, expected: ResolutionDisposition) -> None:
    value = instrument_version(1, effective_from=10, effective_to=20)
    assert resolve_instrument((value,), as_of=as_of, cutoff=30).disposition is expected


def test_open_ended_and_historical_closed_intervals_resolve() -> None:
    closed = instrument_version(1, effective_from=0, effective_to=10)
    opened = instrument_version(2, effective_from=10, knowledge_from=5)
    values = (opened, closed)
    assert resolve_instrument(values, as_of=9, cutoff=10).value is closed
    assert resolve_instrument(values, as_of=10, cutoff=10).value is opened


def test_knowledge_cutoff_and_historical_correction_visibility() -> None:
    original = instrument_version(1, knowledge_from=1)
    correction = instrument_version(2, knowledge_from=5, supersedes=1, currency="GBP")
    values = (correction, original)
    assert resolve_instrument(values, cutoff=4).value is original
    assert resolve_instrument(values, cutoff=5).value is correction
    assert resolve_instrument(values, cutoff=9).value is correction


@pytest.mark.parametrize("state", list(TradabilityState))
def test_each_historical_tradability_state_is_factual_not_authority(state: TradabilityState) -> None:
    value = listing_version(1, tradability=state)
    values = (value,)
    result = resolve_listing_reference(
        instrument_id=value.instrument_id,
        listing_id=value.listing_id,
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=values,
        coverage=coverage(values, kind=ReferenceRecordKind.LISTING, listing_number=2),
    )
    assert result.disposition is ResolutionDisposition.ESTABLISHED
    assert result.value is not None and result.value.tradability_state is state
    assert not hasattr(result, "trading_authorized")


def test_resolution_is_repeatable_and_input_order_independent() -> None:
    old = instrument_version(1, effective_to=10)
    current = instrument_version(2, effective_from=10)
    values = (old, current)
    expected = resolve_instrument(values)
    for candidate_order in (values, tuple(reversed(values)), values):
        actual = resolve_instrument(candidate_order)
        assert actual == expected


def test_empty_candidates_without_negative_coverage_are_not_absent() -> None:
    result = resolve_instrument(())
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED
    assert result.reason == "NO_APPLICABLE_VERSION"


def test_authoritative_negative_coverage_establishes_absence() -> None:
    complete = coverage(())
    result = resolve_instrument((), supplied_coverage=complete, negative_coverage=negative(complete))
    assert result.disposition is ResolutionDisposition.ABSENT
    assert result.value is None


def test_fabricated_or_non_authoritative_absence_fails_closed() -> None:
    complete = coverage(())
    untrusted = negative(complete, state=ValidationState.NOT_VALIDATED)
    assert resolve_instrument((), supplied_coverage=complete, negative_coverage=untrusted).disposition is ResolutionDisposition.NOT_ESTABLISHED
    mismatched = replace(negative(complete), candidate_set_digest="sha256:" + "f" * 64)
    assert resolve_instrument((), supplied_coverage=complete, negative_coverage=mismatched).disposition is ResolutionDisposition.NOT_ESTABLISHED


def test_missing_or_non_authoritative_completeness_fails_closed() -> None:
    value = instrument_version(1)
    without = resolve_instrument_reference(
        instrument_id=value.instrument_id,
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=(value,),
        coverage=None,
    )
    assert without.disposition is ResolutionDisposition.NOT_ESTABLISHED
    untrusted = coverage((value,), state=ValidationState.NOT_VALIDATED)
    assert resolve_instrument((value,), supplied_coverage=untrusted).disposition is ResolutionDisposition.NOT_ESTABLISHED


def test_candidate_cannot_self_certify_coverage() -> None:
    value = instrument_version(1)
    self_certified = coverage((value,), evidence_ref=value.evidence_refs[0])
    result = resolve_instrument((value,), supplied_coverage=self_certified)
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED
    assert result.reason == "CANDIDATE_SELF_CERTIFIED_COVERAGE"


def test_incomplete_candidate_set_or_silent_truncation_fails_closed() -> None:
    first, second = instrument_version(1), instrument_version(2, effective_from=20)
    complete = coverage((first, second))
    result = resolve_instrument((first,), supplied_coverage=complete)
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED
    assert result.reason == "CANDIDATE_SET_INCOMPLETE"


def test_future_knowledge_does_not_leak_or_fall_back_to_current() -> None:
    future = instrument_version(1, knowledge_from=11)
    result = resolve_instrument((future,), cutoff=10)
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED
    assert result.value is None


def test_overlapping_material_candidates_conflict_without_order_resolution() -> None:
    first = instrument_version(1, currency="USD")
    second = instrument_version(2, currency="GBP")
    for values in ((first, second), (second, first)):
        result = resolve_instrument(values)
        assert result.disposition is ResolutionDisposition.CONFLICTING


def test_semantically_identical_multiple_candidates_are_ambiguous() -> None:
    first = instrument_version(1)
    second = replace(first, reference_version_id=ReferenceVersionId(uuid4(2)))
    assert first.content_digest == second.content_digest
    assert resolve_instrument((first, second)).disposition is ResolutionDisposition.AMBIGUOUS


def test_invalid_qualifying_candidate_prevents_establishment() -> None:
    invalid = instrument_version(1, state=ValidationState.INVALID)
    result = resolve_instrument((invalid,))
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED


def test_wrong_subject_and_wrong_kind_fail_closed() -> None:
    wrong_subject = instrument_version(1, instrument_number=2)
    assert resolve_instrument((wrong_subject,)).disposition is ResolutionDisposition.CONFLICTING
    listing = listing_version(1)
    result = resolve_instrument_reference(
        instrument_id=listing.instrument_id,
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=(listing,),  # type: ignore[arg-type]
        coverage=coverage((listing,), kind=ReferenceRecordKind.LISTING, listing_number=2),
    )
    assert result.disposition is ResolutionDisposition.CONFLICTING


def test_cross_listing_substitution_fails_closed() -> None:
    value = listing_version(1, listing_number=2)
    result = resolve_listing_reference(
        instrument_id=value.instrument_id,
        listing_id=ListingId(uuid4(3)),
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=(value,),
        coverage=None,
    )
    assert result.disposition is ResolutionDisposition.CONFLICTING
    assert result.reason == "WRONG_LISTING_SUBJECT"


@pytest.mark.parametrize(
    "values",
    [
        (instrument_version(2, supersedes=1, knowledge_from=2),),
        (instrument_version(1), instrument_version(1)),
        (instrument_version(1, knowledge_from=1), instrument_version(2, knowledge_from=2, supersedes=1), instrument_version(3, knowledge_from=3, supersedes=1)),
    ],
)
def test_missing_predecessor_duplicate_and_branching_lineage_conflict(values: tuple[InstrumentReferenceVersion, ...]) -> None:
    if len({value.reference_version_id for value in values}) != len(values):
        result = resolve_instrument_reference(
            instrument_id=InstrumentId(uuid4(1)),
            effective_as_of=instant(10),
            knowledge_cutoff=instant(10),
            candidates=values,
            coverage=None,
        )
    else:
        result = resolve_instrument(values)
    assert result.disposition is ResolutionDisposition.CONFLICTING


def test_cross_subject_and_cross_kind_lineage_are_rejected_before_selection() -> None:
    assert resolve_instrument((instrument_version(1), instrument_version(2, instrument_number=2))).disposition is ResolutionDisposition.CONFLICTING
    listing = listing_version(2)
    result = resolve_instrument_reference(
        instrument_id=InstrumentId(uuid4(1)),
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=(instrument_version(1), listing),  # type: ignore[arg-type]
        coverage=None,
    )
    assert result.disposition is ResolutionDisposition.CONFLICTING


def test_successor_knowledge_must_be_strictly_later() -> None:
    predecessor = instrument_version(1, knowledge_from=2)
    successor = instrument_version(2, knowledge_from=1, supersedes=1)
    result = resolve_instrument((predecessor, successor), cutoff=10)
    assert result.disposition is ResolutionDisposition.CONFLICTING
    assert result.reason == "INVALID_OR_CONFLICTING_LINEAGE"


def test_self_supersession_cycle_and_malformed_interval_are_rejected_at_construction() -> None:
    with pytest.raises(Exception, match="SELF_SUPERSESSION"):
        instrument_version(1, supersedes=1)
    first = instrument_version(1, supersedes=2, knowledge_from=1)
    second = instrument_version(2, supersedes=1, knowledge_from=2)
    assert resolve_instrument((first, second)).disposition is ResolutionDisposition.CONFLICTING
    with pytest.raises(ValueError, match="positive duration"):
        instrument_version(3, effective_from=10, effective_to=10)


def test_mutation_attempts_fail_and_resolution_does_not_mutate_inputs() -> None:
    value = instrument_version(1)
    complete = coverage((value,))
    before = (value, complete)
    result = resolve_instrument((value,), supplied_coverage=complete)
    assert (value, complete) == before
    assert result.value is value
    with pytest.raises(FrozenInstanceError):
        complete.validation_state = ValidationState.INVALID
    with pytest.raises(FrozenInstanceError):
        result.value = None


def test_resource_exhaustion_is_restrictive_without_truncation() -> None:
    value = instrument_version(1)
    candidates = (value,) * 4097
    result = resolve_instrument_reference(
        instrument_id=value.instrument_id,
        effective_as_of=instant(10),
        knowledge_cutoff=instant(10),
        candidates=candidates,
        coverage=None,
    )
    assert result.disposition is ResolutionDisposition.NOT_ESTABLISHED
    assert result.reason == "CANDIDATE_RESOURCE_LIMIT_EXCEEDED"


def test_coverage_query_binding_rejects_wrong_time_subject_kind_and_listing() -> None:
    value = instrument_version(1)
    variants = (
        coverage((value,), as_of=11),
        coverage((value,), cutoff=11),
        coverage((value,), instrument_number=2),
    )
    for mismatched in variants:
        assert resolve_instrument((value,), supplied_coverage=mismatched).disposition is ResolutionDisposition.NOT_ESTABLISHED


def test_no_implicit_time_latest_provider_storage_or_hidden_lookup_exists() -> None:
    value = instrument_version(1)
    result = resolve_instrument((value,))
    assert result.disposition is ResolutionDisposition.ESTABLISHED
    assert not hasattr(result, "provider")
    assert not hasattr(result, "storage")
    assert not hasattr(result, "fallback")

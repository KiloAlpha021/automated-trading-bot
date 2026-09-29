from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import UUID

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationPolicyId,
)
from automated_trading_bot.market_data import (
    MAX_NORMALIZATION_FIELDS,
    NORMALIZATION_CONTRACT_FAMILY,
    AcquisitionId,
    AcquisitionRecord,
    CanonicalField,
    MalformedNormalizationInputError,
    NormalizationContract,
    NormalizationInput,
    ProviderNativeId,
    SourceInterpretationId,
    SourceOrderAuthority,
    SupportedInterpretation,
    TemporalCapability,
    UnsupportedInterpretationError,
    normalize_observation,
    normalize_observations,
    verify_normalization_content_digest,
)


SOURCE = SourceId("source:atis/test-source")
DATASET = DatasetId("dataset:atis/test-dataset")
INTERPRETATION = SourceInterpretationId("interpretation:atis/semantic-v1")
ACQUIRED = Timestamp(datetime(2026, 1, 2, 12, tzinfo=timezone.utc))
OBSERVED = Timestamp(datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc))
PUBLISHED = Timestamp(datetime(2026, 1, 2, 10, tzinfo=timezone.utc))
KNOWN = Timestamp(datetime(2026, 1, 2, 11, tzinfo=timezone.utc))


def evidence(number: int, *, source: SourceId = SOURCE, dataset: DatasetId = DATASET) -> EvidenceRef:
    return EvidenceRef(
        source_id=source,
        dataset_id=dataset,
        evidence_id=EvidenceId(f"evidence:atis/normalization-{number}"),
        content_digest=EvidenceContentDigest.from_bytes(f"evidence-{number}".encode()),
    )


def acquisition(
    number: int = 1,
    *,
    interpretation: SourceInterpretationId = INTERPRETATION,
    order: SourceOrderAuthority = SourceOrderAuthority.NON_AUTHORITATIVE,
    sequence: int | None = None,
) -> AcquisitionRecord:
    payload = evidence(number)
    return AcquisitionRecord(
        acquisition_id=AcquisitionId(UUID(f"00000000-0000-4000-8000-{number:012d}")),
        source_id=SOURCE,
        source_dataset_id=DATASET,
        provider_native_source_id=ProviderNativeId("vendor-source"),
        provider_native_dataset_id=ProviderNativeId("vendor-dataset"),
        provider_native_record_id=ProviderNativeId(f"vendor-record-{number}"),
        source_interpretation_id=interpretation,
        temporal_capabilities=(
            TemporalCapability.ACQUISITION_TIME,
            TemporalCapability.KNOWLEDGE_TIME,
            TemporalCapability.OBSERVATION_TIME,
            TemporalCapability.PUBLICATION_TIME,
        ),
        acquired_at=ACQUIRED,
        observation_time=OBSERVED,
        publication_time=PUBLISHED,
        publication_time_evidence_ref=evidence(90),
        knowledge_time=KNOWN,
        knowledge_time_evidence_ref=evidence(91),
        source_order_authority=order,
        source_sequence=sequence,
        acquisition_evidence_ref=payload,
        evidence_refs=(payload, evidence(90), evidence(91)),
    )


def contract(**overrides: object) -> NormalizationContract:
    values: dict[str, object] = {
        "contract_version": ContractVersion(NORMALIZATION_CONTRACT_FAMILY, 1),
        "validation_policy_id": ValidationPolicyId("validation-policy:atis/c04-v1"),
        "validation_policy_ref": evidence(200),
        "normalization_code_ref": evidence(201),
        "supported_interpretations": (
            SupportedInterpretation(
                interpretation_id=INTERPRETATION,
                required_fields=("symbol", "price"),
                optional_fields=("volume",),
            ),
        ),
    }
    values.update(overrides)
    return NormalizationContract(**values)  # type: ignore[arg-type]


def request(
    number: int = 1,
    *,
    fields: tuple[CanonicalField, ...] | None = None,
    record: AcquisitionRecord | None = None,
) -> NormalizationInput:
    value = acquisition(number) if record is None else record
    return NormalizationInput(
        acquisition=value,
        fields=fields or (
            CanonicalField("symbol", "XYZ"),
            CanonicalField("price", "101.25"),
        ),
        source_payload_ref=evidence(number),
    )


def test_normalization_is_deterministic_and_field_order_independent() -> None:
    first = normalize_observation(request(), contract())
    second = normalize_observation(
        request(fields=(CanonicalField("price", "101.25"), CanonicalField("symbol", "XYZ"))),
        contract(),
    )
    assert first.fields == second.fields
    assert first.content_digest == second.content_digest
    verify_normalization_content_digest(first)


def test_exact_c03_identity_interpretation_and_provenance_are_bound() -> None:
    value = normalize_observation(request(), contract())
    assert value.acquisition.source_id == SOURCE
    assert value.acquisition.source_dataset_id == DATASET
    assert value.acquisition.source_interpretation_id == INTERPRETATION
    assert value.source_payload_ref in value.provenance_refs
    assert value.validation_policy_ref in value.provenance_refs
    assert value.normalization_code_ref in value.provenance_refs
    assert value.normalization_contract_digest == contract().content_digest


def test_policy_code_and_contract_version_change_content_identity() -> None:
    baseline = normalize_observation(request(), contract())
    policy_changed = normalize_observation(
        request(),
        contract(validation_policy_id=ValidationPolicyId("validation-policy:atis/c04-v2")),
    )
    code_changed = normalize_observation(request(), contract(normalization_code_ref=evidence(202)))
    version_changed = normalize_observation(
        request(), contract(contract_version=ContractVersion(NORMALIZATION_CONTRACT_FAMILY, 2)),
    )
    assert len({baseline.content_digest, policy_changed.content_digest, code_changed.content_digest, version_changed.content_digest}) == 4


def test_temporal_capabilities_and_limitations_are_preserved_without_promotion() -> None:
    value = normalize_observation(request(), contract())
    assert value.temporal_capabilities == request().acquisition.temporal_capabilities
    assert value.observation_time == OBSERVED
    assert value.publication_time == PUBLISHED
    assert value.knowledge_time == KNOWN
    assert not hasattr(value, "fresh")
    assert not hasattr(value, "current")


def test_authoritative_source_order_is_preserved_by_sequence() -> None:
    later = request(2, record=acquisition(2, order=SourceOrderAuthority.AUTHORITATIVE, sequence=20))
    earlier = request(1, record=acquisition(1, order=SourceOrderAuthority.AUTHORITATIVE, sequence=10))
    result = normalize_observations((later, earlier), contract())
    assert [item.source_sequence for item in result] == [10, 20]
    assert all(item.source_order_authority is SourceOrderAuthority.AUTHORITATIVE for item in result)


def test_non_authoritative_cohort_is_permutation_independent() -> None:
    first = request(1, fields=(CanonicalField("symbol", "AAA"), CanonicalField("price", "1")))
    second = request(2, fields=(CanonicalField("symbol", "BBB"), CanonicalField("price", "2")))
    assert normalize_observations((first, second), contract()) == normalize_observations(
        (second, first), contract(),
    )


def test_unsupported_interpretation_fails_closed_without_fallback() -> None:
    unsupported = SourceInterpretationId("interpretation:atis/unknown-v1")
    with pytest.raises(UnsupportedInterpretationError):
        normalize_observation(request(record=acquisition(interpretation=unsupported)), contract())


@pytest.mark.parametrize(
    "fields,match",
    [
        ((CanonicalField("symbol", "XYZ"),), "missing required"),
        (
            (
                CanonicalField("symbol", "XYZ"),
                CanonicalField("price", "1"),
                CanonicalField("provider_guess", "x"),
            ),
            "unsupported fields",
        ),
    ],
)
def test_missing_or_unknown_fields_fail_closed(
    fields: tuple[CanonicalField, ...], match: str,
) -> None:
    with pytest.raises(MalformedNormalizationInputError, match=match):
        normalize_observation(request(fields=fields), contract())


def test_duplicate_fields_malformed_values_and_resource_exhaustion_reject() -> None:
    with pytest.raises(MalformedNormalizationInputError, match="duplicate"):
        request(fields=(CanonicalField("price", "1"), CanonicalField("price", "2")))
    with pytest.raises(TypeError, match="field value"):
        CanonicalField("price", 1.5)  # type: ignore[arg-type]
    too_many = tuple(CanonicalField(f"f{number}", number) for number in range(MAX_NORMALIZATION_FIELDS + 1))
    with pytest.raises(MalformedNormalizationInputError, match="resource limit"):
        request(fields=too_many)


def test_source_payload_must_be_exact_acquisition_evidence() -> None:
    value = acquisition()
    with pytest.raises(MalformedNormalizationInputError, match="not the exact"):
        NormalizationInput(
            acquisition=value,
            fields=(CanonicalField("symbol", "XYZ"), CanonicalField("price", "1")),
            source_payload_ref=evidence(999),
        )


def test_mixed_or_ambiguous_authoritative_cohort_rejects() -> None:
    authoritative = request(
        1, record=acquisition(1, order=SourceOrderAuthority.AUTHORITATIVE, sequence=1),
    )
    non_authoritative = request(2)
    with pytest.raises(MalformedNormalizationInputError, match="not homogeneous"):
        normalize_observations((authoritative, non_authoritative), contract())
    duplicate_sequence = request(
        2, record=acquisition(2, order=SourceOrderAuthority.AUTHORITATIVE, sequence=1),
    )
    with pytest.raises(MalformedNormalizationInputError, match="ambiguous"):
        normalize_observations((authoritative, duplicate_sequence), contract())


def test_contract_and_results_are_immutable() -> None:
    policy = contract()
    value = normalize_observation(request(), policy)
    with pytest.raises(FrozenInstanceError):
        policy.contract_version = ContractVersion(NORMALIZATION_CONTRACT_FAMILY, 2)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        value.fields = ()  # type: ignore[misc]


def test_content_digest_mismatch_is_visible() -> None:
    value = normalize_observation(request(), contract())
    changed = replace(value, content_digest=EvidenceContentDigest.from_bytes(b"wrong"))
    with pytest.raises(ValueError, match="does not match"):
        verify_normalization_content_digest(changed)


def test_no_provider_quality_eligibility_or_freshness_authority_surface() -> None:
    value = normalize_observation(request(), contract())
    for forbidden in (
        "provider",
        "quality_state",
        "eligible",
        "quarantined",
        "fresh",
        "materialized",
        "persisted",
    ):
        assert not hasattr(value, forbidden)

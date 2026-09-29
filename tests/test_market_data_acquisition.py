from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)
from automated_trading_bot.market_data import (
    MAX_ACQUISITION_EVIDENCE_REFS,
    AcquisitionId,
    AcquisitionRecord,
    ProviderNativeId,
    SourceInterpretationId,
    SourceOrderAuthority,
    TemporalCapability,
    verify_acquisition_content_digest,
)


SOURCE = SourceId("source:atis/test-source")
DATASET = DatasetId("dataset:atis/test-dataset")
OTHER_SOURCE = SourceId("source:atis/other-source")
OTHER_DATASET = DatasetId("dataset:atis/other-dataset")
ACQUIRED = Timestamp(datetime(2026, 1, 2, 12, tzinfo=timezone.utc))
PUBLISHED = Timestamp(datetime(2026, 1, 2, 10, tzinfo=timezone.utc))
KNOWN = Timestamp(datetime(2026, 1, 2, 11, tzinfo=timezone.utc))


def acquisition_id(number: int = 1) -> AcquisitionId:
    return AcquisitionId(UUID(f"00000000-0000-4000-8000-{number:012d}"))


def evidence(
    number: int,
    *,
    source_id: SourceId = SOURCE,
    dataset_id: DatasetId = DATASET,
) -> EvidenceRef:
    return EvidenceRef(
        source_id=source_id,
        dataset_id=dataset_id,
        evidence_id=EvidenceId(f"evidence:atis/item-{number}"),
        content_digest=EvidenceContentDigest.from_bytes(f"item-{number}".encode()),
    )


def record(**overrides: object) -> AcquisitionRecord:
    acquisition = evidence(1)
    values: dict[str, object] = {
        "acquisition_id": acquisition_id(),
        "source_id": SOURCE,
        "source_dataset_id": DATASET,
        "provider_native_source_id": ProviderNativeId("vendor-source"),
        "provider_native_dataset_id": ProviderNativeId("vendor-dataset"),
        "provider_native_record_id": ProviderNativeId("vendor-record"),
        "source_interpretation_id": SourceInterpretationId("interpretation:atis/schema-v1"),
        "temporal_capabilities": (TemporalCapability.ACQUISITION_TIME,),
        "acquired_at": ACQUIRED,
        "source_order_authority": SourceOrderAuthority.NON_AUTHORITATIVE,
        "acquisition_evidence_ref": acquisition,
        "evidence_refs": (acquisition,),
    }
    values.update(overrides)
    return AcquisitionRecord(**values)  # type: ignore[arg-type]


def test_exact_source_dataset_and_provider_native_identities_remain_separate() -> None:
    value = record()
    assert value.source_id is SOURCE
    assert value.source_dataset_id is DATASET
    assert value.provider_native_source_id == ProviderNativeId("vendor-source")
    assert value.provider_native_dataset_id == ProviderNativeId("vendor-dataset")
    assert value.provider_native_record_id == ProviderNativeId("vendor-record")
    assert type(value.source_id) is SourceId
    assert type(value.provider_native_source_id) is ProviderNativeId


def test_acquisition_identity_round_trips_exact_canonical_form() -> None:
    value = acquisition_id()
    assert AcquisitionId.parse(value.to_string()) == value


def test_interpretation_and_temporal_capabilities_are_explicit_and_canonical() -> None:
    value = record(
        temporal_capabilities=(
            TemporalCapability.KNOWLEDGE_TIME,
            TemporalCapability.ACQUISITION_TIME,
            TemporalCapability.PUBLICATION_TIME,
        ),
        publication_time=PUBLISHED,
        publication_time_evidence_ref=evidence(2),
        knowledge_time=KNOWN,
        knowledge_time_evidence_ref=evidence(3),
        evidence_refs=(evidence(3), evidence(1), evidence(2)),
    )
    assert value.source_interpretation_id.value == "interpretation:atis/schema-v1"
    assert value.temporal_capabilities == (
        TemporalCapability.ACQUISITION_TIME,
        TemporalCapability.KNOWLEDGE_TIME,
        TemporalCapability.PUBLICATION_TIME,
    )
    assert value.has_attributable_knowledge_time


def test_unavailable_knowledge_and_publication_time_remain_unavailable() -> None:
    value = record()
    assert value.publication_time is None
    assert value.knowledge_time is None
    assert not value.has_attributable_knowledge_time
    assert value.acquired_at == ACQUIRED


def test_entitlement_evidence_is_represented_without_deciding_entitlement() -> None:
    entitlement = evidence(9, source_id=OTHER_SOURCE, dataset_id=OTHER_DATASET)
    value = record(entitlement_evidence_refs=(entitlement,))
    assert value.entitlement_evidence_refs == (entitlement,)
    assert not hasattr(value, "entitled")


def test_authoritative_source_order_requires_and_preserves_sequence() -> None:
    value = record(
        source_order_authority=SourceOrderAuthority.AUTHORITATIVE,
        source_sequence=17,
    )
    assert value.source_sequence == 17


def test_non_authoritative_source_order_carries_no_sequence_authority() -> None:
    value = record()
    assert value.source_order_authority is SourceOrderAuthority.NON_AUTHORITATIVE
    assert value.source_sequence is None


def test_repeated_construction_is_deterministic_and_identity_independent() -> None:
    first = record(acquisition_id=acquisition_id(1))
    second = record(acquisition_id=acquisition_id(2))
    assert first.content_digest == second.content_digest
    verify_acquisition_content_digest(first, first.content_digest)


def test_evidence_order_is_not_authoritative() -> None:
    first = record(evidence_refs=(evidence(1), evidence(2)))
    second = record(evidence_refs=(evidence(2), evidence(1)))
    assert first.evidence_refs == second.evidence_refs
    assert first.content_digest == second.content_digest


def test_records_and_inputs_are_immutable() -> None:
    value = record()
    with pytest.raises(FrozenInstanceError):
        value.source_sequence = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        value.provider_native_source_id.value = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "override", "error"),
    [
        ("wrong source type", {"source_id": ProviderNativeId("native")}, TypeError),
        ("missing source", {"source_id": None}, TypeError),
        ("native substituted", {"provider_native_source_id": SOURCE}, TypeError),
        ("wrong dataset", {"source_dataset_id": OTHER_DATASET}, ValueError),
        ("missing interpretation", {"source_interpretation_id": None}, TypeError),
        (
            "unsupported interpretation identity",
            {"source_interpretation_id": SourceInterpretationId},
            TypeError,
        ),
        ("missing capability", {"temporal_capabilities": ()}, ValueError),
        (
            "arbitrary arrival order",
            {"source_order_authority": SourceOrderAuthority.NON_AUTHORITATIVE, "source_sequence": 2},
            ValueError,
        ),
    ],
)
def test_identity_and_authority_failures_are_restrictive(
    name: str, override: dict[str, object], error: type[Exception],
) -> None:
    del name
    with pytest.raises(error):
        record(**override)


def test_invalid_interpretation_identity_fails_closed() -> None:
    with pytest.raises(ValueError):
        SourceInterpretationId("vendor-schema-v1")


def test_time_without_declared_capability_is_rejected() -> None:
    with pytest.raises(ValueError, match="knowledge_time is present"):
        record(knowledge_time=KNOWN, knowledge_time_evidence_ref=evidence(2), evidence_refs=(evidence(1), evidence(2)))


def test_fabricated_knowledge_time_without_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="independent attributable evidence"):
        record(
            temporal_capabilities=(TemporalCapability.ACQUISITION_TIME, TemporalCapability.KNOWLEDGE_TIME),
            knowledge_time=KNOWN,
        )


def test_acquisition_receipt_cannot_substitute_for_knowledge_evidence() -> None:
    with pytest.raises(ValueError, match="ACQUISITION_TIME_SUBSTITUTION"):
        record(
            temporal_capabilities=(TemporalCapability.ACQUISITION_TIME, TemporalCapability.KNOWLEDGE_TIME),
            knowledge_time=ACQUIRED,
            knowledge_time_evidence_ref=evidence(1),
        )


def test_wrong_source_or_dataset_evidence_is_rejected_without_mutation() -> None:
    mismatched = evidence(4, source_id=OTHER_SOURCE)
    supplied = (evidence(1), mismatched)
    with pytest.raises(ValueError, match="exact source and source dataset"):
        record(evidence_refs=supplied)
    assert supplied == (evidence(1), mismatched)


def test_conflicting_evidence_identity_is_rejected() -> None:
    original = evidence(1)
    conflicting = EvidenceRef(
        source_id=SOURCE,
        dataset_id=DATASET,
        evidence_id=original.evidence_id,
        content_digest=EvidenceContentDigest.from_bytes(b"different"),
    )
    with pytest.raises(ValueError, match="EVIDENCE_IDENTITY_CONFLICT"):
        record(evidence_refs=(original, conflicting))


def test_malformed_evidence_is_rejected() -> None:
    with pytest.raises(TypeError):
        record(evidence_refs=(evidence(1), "not-evidence"))


def test_resource_exhaustion_is_explicit_and_never_truncated() -> None:
    values = tuple(evidence(number) for number in range(1, MAX_ACQUISITION_EVIDENCE_REFS + 2))
    with pytest.raises(ValueError, match="exceeds the limit"):
        record(evidence_refs=values)
    assert len(values) == MAX_ACQUISITION_EVIDENCE_REFS + 1


def test_no_silent_fallback_or_authority_promotion_surface_exists() -> None:
    value = record()
    assert not hasattr(value, "fallback_source")
    assert not hasattr(value, "normalized_observation")
    assert not hasattr(value, "quality_state")
    assert not hasattr(value, "eligible")


def test_content_digest_mismatch_is_visible() -> None:
    value = record()
    with pytest.raises(ValueError, match="does not match"):
        verify_acquisition_content_digest(value, EvidenceContentDigest.from_bytes(b"wrong"))

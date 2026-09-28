from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceIdentityConflict,
    EvidenceId,
    EvidenceRef,
    InstrumentReferenceVersion,
    LineageValidationError,
    ListingId,
    ListingReferenceVersion,
    MAX_EVIDENCE_REFS,
    MAX_LINEAGE_DEPTH,
    MAX_LINEAGE_VERSIONS,
    ReferenceContentDigest,
    ReferenceVersionId,
    SourceId,
    TradabilityState,
    ValidationPolicyId,
    ValidationState,
    canonicalize_evidence_refs,
    validate_lineage,
    verify_content_digest,
)


def uuid4(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def instant(seconds: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def evidence(number: int = 1, *, digest_byte: bytes = b"x") -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:atis/source"),
        DatasetId("dataset:atis/reference"),
        EvidenceId(f"evidence:atis/item-{number}"),
        EvidenceContentDigest.from_bytes(digest_byte),
    )


def instrument_version(
    number: int = 1,
    *,
    instrument_number: int = 1,
    knowledge: int = 0,
    supersedes: int | None = None,
    evidence_refs: tuple[EvidenceRef, ...] | None = None,
    validation_state: ValidationState = ValidationState.VALID,
) -> InstrumentReferenceVersion:
    values: dict[str, object] = {
        "reference_version_id": ReferenceVersionId(uuid4(number)),
        "instrument_id": InstrumentId(uuid4(instrument_number)),
        "effective_from": instant(),
        "effective_to": None,
        "knowledge_from": instant(knowledge),
        "evidence_refs": (evidence(),) if evidence_refs is None else evidence_refs,
        "validation_policy_id": ValidationPolicyId("validation-policy:atis/rb1-v1"),
        "validation_state": validation_state,
        "supersedes_version_id": None if supersedes is None else ReferenceVersionId(uuid4(supersedes)),
        "currency": "USD",
        "security_type": "COMMON_STOCK",
    }
    return InstrumentReferenceVersion.create(**values)


def listing_version(number: int = 1, *, listing_number: int = 2) -> ListingReferenceVersion:
    return ListingReferenceVersion.create(
        reference_version_id=ReferenceVersionId(uuid4(number)),
        instrument_id=InstrumentId(uuid4(1)),
        listing_id=ListingId(uuid4(listing_number)),
        effective_from=instant(),
        effective_to=None,
        knowledge_from=instant(),
        evidence_refs=(evidence(),),
        validation_policy_id=ValidationPolicyId("validation-policy:atis/rb1-v1"),
        validation_state=ValidationState.VALID,
        tradability_state=TradabilityState.ACTIVE,
        tradability_reason="NORMAL",
        symbol="ABC",
        mic="XLON",
    )


@pytest.mark.parametrize(
    "identity,prefix",
    [
        (ListingId(uuid4(1)), "atis:listing:v1:"),
        (ReferenceVersionId(uuid4(1)), "atis:reference-version:v1:"),
    ],
)
def test_typed_uuid_identity_round_trip(identity: object, prefix: str) -> None:
    text = identity.to_string()  # type: ignore[attr-defined]
    assert text == prefix + str(uuid4(1))
    assert type(identity).parse(text) == identity


@pytest.mark.parametrize("kind", [ListingId, ReferenceVersionId])
@pytest.mark.parametrize("value", [UUID(int=0), UUID(int=1), UUID("00000000-0000-1000-8000-000000000001")])
def test_typed_uuid_identity_rejects_nil_and_non_v4(kind: type, value: UUID) -> None:
    with pytest.raises(ValueError):
        kind(value)


def test_identity_namespaces_are_non_substitutable() -> None:
    value = uuid4(1)
    assert InstrumentId(value) != ListingId(value)
    assert ListingId(value) != ReferenceVersionId(value)
    with pytest.raises(ValueError):
        ListingId.parse(InstrumentId(value).to_string())


@pytest.mark.parametrize(
    "kind,value",
    [
        (SourceId, "source:OpenAI/id"),
        (SourceId, "source:a../id"),
        (DatasetId, "dataset:atis/has space"),
        (EvidenceId, "evidence:atis/é"),
        (ValidationPolicyId, "validation-policy:atis/"),
    ],
)
def test_attributable_identifiers_reject_noncanonical_syntax(kind: type, value: str) -> None:
    with pytest.raises(ValueError):
        kind(value)


def test_evidence_ref_is_immutable_and_digest_is_canonical() -> None:
    item = evidence()
    with pytest.raises(FrozenInstanceError):
        item.evidence_id = EvidenceId("evidence:atis/other")
    with pytest.raises(ValueError):
        EvidenceContentDigest("sha256:" + "A" * 64)


def test_evidence_replay_is_idempotent_and_order_independent() -> None:
    first, second = evidence(1), evidence(2)
    assert canonicalize_evidence_refs((second, first, first)) == canonicalize_evidence_refs((first, second))


def test_conflicting_evidence_identity_fails_closed() -> None:
    with pytest.raises(EvidenceIdentityConflict, match="EVIDENCE_IDENTITY_CONFLICT"):
        canonicalize_evidence_refs((evidence(1, digest_byte=b"a"), evidence(1, digest_byte=b"b")))


def test_evidence_limit_accepts_64_and_rejects_65() -> None:
    canonicalize_evidence_refs(tuple(evidence(number) for number in range(1, 65)))
    with pytest.raises(ValueError, match="limit of 64"):
        canonicalize_evidence_refs(tuple(evidence(number) for number in range(1, 66)))


def test_reference_version_requires_provenance_and_explicit_validation() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        instrument_version(evidence_refs=())
    with pytest.raises(TypeError, match="ValidationState"):
        instrument_version(validation_state="VALID")  # type: ignore[arg-type]
    for state in ValidationState:
        assert instrument_version(validation_state=state).is_authoritative is (state is ValidationState.VALID)


def test_half_open_effective_interval_and_open_end() -> None:
    original = instrument_version()
    values = {name: getattr(original, name) for name in original.__dataclass_fields__ if name != "content_digest"}
    values.update(effective_from=instant(10), effective_to=instant(20))
    value = InstrumentReferenceVersion.create(**values)
    assert value.contains_effective(instant(10))
    assert value.contains_effective(instant(19))
    assert not value.contains_effective(instant(20))
    assert instrument_version().contains_effective(instant(999))
    values["effective_to"] = instant(10)
    with pytest.raises(ValueError, match="positive duration"):
        InstrumentReferenceVersion.create(**values)


def test_temporal_roles_require_timestamp_and_utc() -> None:
    with pytest.raises(TypeError, match="knowledge_from"):
        replace(instrument_version(), knowledge_from=instrument_version().effective_from.value)
    with pytest.raises(ValueError, match="timezone-aware"):
        Timestamp(datetime(2026, 1, 1))


def test_listing_foundation_is_separate_and_immutable() -> None:
    value = listing_version()
    assert value.tradability_state is TradabilityState.ACTIVE
    assert not hasattr(value, "knowledge_to")
    with pytest.raises(FrozenInstanceError):
        value.symbol = "OTHER"


def test_content_identity_is_deterministic_order_independent_and_excludes_ids() -> None:
    left = instrument_version(evidence_refs=(evidence(2), evidence(1)))
    right = instrument_version(2, evidence_refs=(evidence(1), evidence(2)))
    assert left.content_digest == right.content_digest
    assert left.reference_version_id != right.reference_version_id


@pytest.mark.parametrize(
    "change",
    [
        {"currency": "GBP"},
        {"knowledge_from": instant(2)},
        {"validation_policy_id": ValidationPolicyId("validation-policy:atis/rb1-v2")},
        {"validation_state": ValidationState.INVALID},
        {"supersedes_version_id": ReferenceVersionId(uuid4(99))},
        {"evidence_refs": (evidence(2),)},
    ],
)
def test_each_semantic_change_changes_content_identity(change: dict[str, object]) -> None:
    original = instrument_version()
    values = {name: getattr(original, name) for name in original.__dataclass_fields__ if name != "content_digest"}
    values.update(change)
    changed = InstrumentReferenceVersion.create(**values)
    assert changed.content_digest != original.content_digest


def test_content_digest_cannot_self_certify_or_be_incorrect() -> None:
    value = instrument_version()
    verify_content_digest(value, value.content_digest)
    with pytest.raises(ValueError, match="does not match"):
        verify_content_digest(value, ReferenceContentDigest("sha256:" + "f" * 64))


def test_lineage_valid_chain_is_input_order_independent() -> None:
    root = instrument_version(1, knowledge=1)
    second = instrument_version(2, knowledge=2, supersedes=1)
    third = instrument_version(3, knowledge=3, supersedes=2)
    assert validate_lineage((third, root, second)) == validate_lineage((second, third, root))


@pytest.mark.parametrize(
    "values,message",
    [
        ((instrument_version(2, knowledge=2, supersedes=1),), "MISSING_PREDECESSOR"),
        ((instrument_version(1, instrument_number=1), instrument_version(2, instrument_number=2)), "CROSS_SUBJECT_EDGE"),
        ((instrument_version(1), listing_version(2)), "CROSS_KIND_EDGE"),
        ((instrument_version(1), instrument_version(1)), "DUPLICATE_REFERENCE_VERSION_ID"),
        ((instrument_version(1, knowledge=1), instrument_version(2, knowledge=2, supersedes=1), instrument_version(3, knowledge=3, supersedes=1)), "BRANCHING_SUCCESSOR_CONFLICT"),
        ((instrument_version(1, knowledge=2), instrument_version(2, knowledge=1, supersedes=1)), "SUCCESSOR_KNOWLEDGE_NOT_GREATER"),
    ],
)
def test_lineage_rejects_invalid_graph(values: tuple[object, ...], message: str) -> None:
    with pytest.raises(LineageValidationError, match=message):
        validate_lineage(values)  # type: ignore[arg-type]


def test_self_supersession_is_rejected_during_construction() -> None:
    with pytest.raises(LineageValidationError, match="SELF_SUPERSESSION"):
        instrument_version(1, supersedes=1)


def test_cycle_is_rejected() -> None:
    first = instrument_version(1, knowledge=1, supersedes=2)
    second = instrument_version(2, knowledge=2, supersedes=1)
    with pytest.raises(LineageValidationError, match="LINEAGE_CYCLE"):
        validate_lineage((first, second))


def test_lineage_depth_256_is_accepted_and_257_rejected() -> None:
    accepted = tuple(
        instrument_version(number, knowledge=number, supersedes=None if number == 1 else number - 1)
        for number in range(1, 258)
    )
    validate_lineage(accepted)
    rejected = accepted + (instrument_version(258, knowledge=258, supersedes=257),)
    with pytest.raises(LineageValidationError, match="LINEAGE_RESOURCE_LIMIT_EXCEEDED"):
        validate_lineage(rejected)


def test_lineage_size_4096_is_accepted_and_4097_rejected() -> None:
    accepted = tuple(instrument_version(number) for number in range(1, 4097))
    validate_lineage(accepted)
    with pytest.raises(LineageValidationError, match="LINEAGE_RESOURCE_LIMIT_EXCEEDED"):
        validate_lineage(accepted + (instrument_version(4097),))


def test_traceability_contract_ids_remain_attributable() -> None:
    assert (MAX_EVIDENCE_REFS, MAX_LINEAGE_VERSIONS, MAX_LINEAGE_DEPTH) == (64, 4096, 256)

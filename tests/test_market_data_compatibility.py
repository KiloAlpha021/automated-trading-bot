from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
from uuid import UUID

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import ContractVersion
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceIdentityConflict,
    EvidenceRef,
    SourceId,
    ValidationPolicyId,
)
from automated_trading_bot.market_data import (
    ADAPTER_CONTRACT_FAMILY,
    COMPATIBILITY_DESCRIPTOR_FAMILY,
    MATERIAL_FIELD_POLICY_FAMILY,
    MAX_DESCRIPTOR_EVIDENCE_REFS,
    PRODUCER_CONTRACT_FAMILY,
    QUALITY_INPUT_FAMILY,
    SEMANTIC_PROJECTION_FAMILY,
    AcquisitionId,
    AcquisitionRecord,
    CanonicalField,
    CompatibilityError,
    CompatibilityEvidenceState,
    IncompatibleContractError,
    MaterialFieldPolicy,
    NormalizationContract,
    NormalizationInput,
    ProviderNativeId,
    QualityLogicalIdentity,
    ResolvedCompatibilityEvidence,
    SequenceCalendarEvidence,
    SourceInterpretationId,
    SourceOrderAuthority,
    SupportedInterpretation,
    TemporalCapability,
    build_compatibility_descriptor,
    normalize_observation,
)


SOURCE = SourceId("source:atis/compatibility-test")
DATASET = DatasetId("dataset:atis/compatibility-test")
INTERPRETATION = SourceInterpretationId("interpretation:atis/compatibility-v1")
NOW = Timestamp(datetime(2026, 1, 2, 12, tzinfo=timezone.utc))


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def ref(name: str, content: bytes | None = None) -> tuple[EvidenceRef, bytes]:
    payload = name.encode() if content is None else content
    return (
        EvidenceRef(SOURCE, DATASET, EvidenceId(f"evidence:atis/{name}"), EvidenceContentDigest.from_bytes(payload)),
        payload,
    )


def observation(
    provider: str = "vendor-a",
    acquired: Timestamp = NOW,
    fields: tuple[CanonicalField, ...] | None = None,
):
    payload, _ = ref("payload")
    acquisition = AcquisitionRecord(
        acquisition_id=AcquisitionId(UUID("00000000-0000-4000-8000-000000000001")),
        source_id=SOURCE,
        source_dataset_id=DATASET,
        provider_native_source_id=ProviderNativeId(provider),
        provider_native_dataset_id=ProviderNativeId("native-dataset"),
        provider_native_record_id=ProviderNativeId("native-record"),
        source_interpretation_id=INTERPRETATION,
        temporal_capabilities=(TemporalCapability.ACQUISITION_TIME,),
        acquired_at=acquired,
        observation_time=None,
        publication_time=None,
        publication_time_evidence_ref=None,
        knowledge_time=None,
        knowledge_time_evidence_ref=None,
        source_order_authority=SourceOrderAuthority.NON_AUTHORITATIVE,
        source_sequence=None,
        acquisition_evidence_ref=payload,
        evidence_refs=(payload,),
    )
    policy_ref, _ = ref("normalization-policy")
    code_ref, _ = ref("normalization-code")
    contract = NormalizationContract(
        ContractVersion("ATIS_C04_NORMALIZATION", 1),
        ValidationPolicyId("validation-policy:atis/c04-v1"),
        policy_ref,
        code_ref,
        (SupportedInterpretation(INTERPRETATION, ("price", "symbol"), ("volume",)),),
    )
    return normalize_observation(
        NormalizationInput(
            acquisition,
            fields or (CanonicalField("symbol", "XYZ"), CanonicalField("price", "101.25"), CanonicalField("volume", 9)),
            payload,
        ),
        contract,
    )


def policy(fields: tuple[str, ...] = ("price", "symbol"), version: int = 1) -> MaterialFieldPolicy:
    content = canonical(
        {
            "material_fields": sorted(fields),
            "policy_version": {"family": MATERIAL_FIELD_POLICY_FAMILY, "version": version},
            "projection_contract_version": {"family": SEMANTIC_PROJECTION_FAMILY, "version": 1},
            "record_kind": "ATIS_C04_C05_MATERIAL_FIELD_POLICY_V1",
            "source_interpretation_id": INTERPRETATION.value,
        }
    )
    policy_ref, _ = ref(f"material-policy-{version}-{'-'.join(fields)}", content)
    return MaterialFieldPolicy(
        ContractVersion(MATERIAL_FIELD_POLICY_FAMILY, version),
        ContractVersion(SEMANTIC_PROJECTION_FAMILY, 1),
        INTERPRETATION,
        fields,
        policy_ref,
    )


def build(**overrides: object):
    obs = overrides.pop("observation", observation())
    material = overrides.pop("material_policy", policy())
    observation_ref = EvidenceRef(SOURCE, DATASET, EvidenceId("evidence:atis/observation"), obs.content_digest)
    names = (
        "namespace", "mapping", "producer-contract", "projection-contract", "adapter-contract",
        "cohort", "scope", "quality-policy", "sequence", "descriptor-evidence",
    )
    pairs = {name: ref(name) for name in names}
    field_values = {item.name: item.value for item in obs.fields}
    semantic_bytes = canonical(
        {
            "material_field_policy_content_digest": material.policy_ref.content_digest.value,
            "material_field_policy_version": {"family": material.policy_version.family, "version": material.policy_version.version},
            "ordered_material_fields": [{"name": name, "value": field_values[name]} for name in material.material_fields if name in field_values],
            "projection_contract_version": {"family": SEMANTIC_PROJECTION_FAMILY, "version": 1},
            "record_kind": "ATIS_C04_C05_CANONICAL_SEMANTIC_PROJECTION_V1",
        }
    )
    semantic_ref, _ = ref("semantic-bytes", semantic_bytes)
    sequence = overrides.get("sequence_evidence") or SequenceCalendarEvidence(
        CompatibilityEvidenceState.AVAILABLE,
        pairs["sequence"][0],
        ContractVersion("ATIS_CALENDAR_SEQUENCE", 1),
        "EXACT_EXPECTATION_AVAILABLE",
        (pairs["sequence"][0],),
    )
    descriptor_refs = overrides.get("descriptor_evidence_refs") or (pairs["descriptor-evidence"][0],)
    resolved_pairs = list(pairs.values()) + [(material.policy_ref, material.content), (semantic_ref, semantic_bytes)]
    for extra in tuple(sequence.evidence_refs) + tuple(descriptor_refs):
        if all(existing.key != extra.key for existing, _ in resolved_pairs):
            resolved_pairs.append((extra, extra.evidence_id.value.removeprefix("evidence:atis/").encode()))
    values: dict[str, object] = {
        "observation": obs,
        "observation_ref": observation_ref,
        "interpretation": SupportedInterpretation(INTERPRETATION, ("price", "symbol"), ("volume",)),
        "material_policy": material,
        "logical_identity": QualityLogicalIdentity(pairs["namespace"][0], "XYZ@2026-01-02"),
        "logical_identity_mapping_ref": pairs["mapping"][0],
        "producer_contract_version": ContractVersion(PRODUCER_CONTRACT_FAMILY, 1),
        "producer_contract_ref": pairs["producer-contract"][0],
        "semantic_projection_contract_ref": pairs["projection-contract"][0],
        "adapter_contract_version": ContractVersion(ADAPTER_CONTRACT_FAMILY, 1),
        "adapter_contract_ref": pairs["adapter-contract"][0],
        "consumer_descriptor_version": ContractVersion(QUALITY_INPUT_FAMILY, 1),
        "canonical_bytes_ref": semantic_ref,
        "sequence_evidence": sequence,
        "descriptor_evidence_refs": descriptor_refs,
        "resolved_evidence": tuple(ResolvedCompatibilityEvidence(*item) for item in resolved_pairs),
        "cohort_ref": pairs["cohort"][0],
        "evaluation_scope_ref": pairs["scope"][0],
        "validation_policy_id": ValidationPolicyId("validation-policy:atis/c05-v1"),
        "validation_policy_ref": pairs["quality-policy"][0],
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return build_compatibility_descriptor(**values)  # type: ignore[arg-type]


def test_exact_compatibility_projection_and_c05_mapping() -> None:
    value = build()
    assert value.descriptor_version == ContractVersion(COMPATIBILITY_DESCRIPTOR_FAMILY, 1)
    assert value.quality_input.logical_identity is value.logical_identity
    assert value.quality_input.semantic_content.digest == value.semantic_digest
    assert value.quality_input.expected_sequence_ref == value.expected_sequence_evidence.ref
    assert value.quality_input.representation_contract_ref == value.adapter_contract_ref
    assert value.semantic_digest != value.canonical_observation_ref.content_digest


def test_projection_ignores_provider_acquisition_and_provenance_context() -> None:
    first = build(observation=observation("vendor-a", NOW))
    later = Timestamp(datetime(2026, 1, 3, tzinfo=timezone.utc))
    second = build(observation=observation("vendor-b", later))
    assert first.semantic_bytes == second.semantic_bytes
    assert first.semantic_digest == second.semantic_digest


def test_material_change_and_policy_change_change_semantic_digest() -> None:
    baseline = build()
    with_volume = build(material_policy=policy(("price", "symbol", "volume")))
    assert baseline.semantic_digest != with_volume.semantic_digest
    changed = observation(fields=(CanonicalField("price", "102"), CanonicalField("symbol", "XYZ"), CanonicalField("volume", 9)))
    assert baseline.semantic_digest != build(observation=changed).semantic_digest


def test_external_logical_identity_is_preserved_and_required() -> None:
    value = build()
    assert value.logical_identity.key == "XYZ@2026-01-02"
    with pytest.raises(ValueError, match="nonempty"):
        QualityLogicalIdentity(value.logical_identity.namespace_ref, "")


@pytest.mark.parametrize(
    "name,value",
    [
        ("producer_contract_version", ContractVersion(PRODUCER_CONTRACT_FAMILY, 2)),
        ("adapter_contract_version", ContractVersion(ADAPTER_CONTRACT_FAMILY, 2)),
        ("consumer_descriptor_version", ContractVersion(QUALITY_INPUT_FAMILY, 2)),
    ],
)
def test_only_exact_admitted_version_tuple_is_accepted(name: str, value: ContractVersion) -> None:
    with pytest.raises(IncompatibleContractError, match="VERSION_INCOMPATIBLE"):
        build(**{name: value})


def test_unknown_or_missing_material_fields_fail_closed() -> None:
    with pytest.raises(CompatibilityError, match="UNKNOWN_MATERIAL_FIELD"):
        build(material_policy=policy(("price", "provider_guess")))
    with pytest.raises(CompatibilityError, match="MATERIAL_FIELD_MISSING"):
        build(material_policy=policy(("price", "symbol", "volume")), observation=observation(fields=(CanonicalField("price", "101.25"), CanonicalField("symbol", "XYZ"))))


@pytest.mark.parametrize("state", [CompatibilityEvidenceState.INCOMPATIBLE, CompatibilityEvidenceState.AMBIGUOUS_CONFLICTING])
def test_conflicting_or_incompatible_sequence_evidence_rejects_promotion(state: CompatibilityEvidenceState) -> None:
    evidence, payload = ref(f"sequence-{state.value}")
    sequence = SequenceCalendarEvidence(state, None, ContractVersion("ATIS_CALENDAR_SEQUENCE", 1), state.value, (evidence,))
    with pytest.raises(CompatibilityError, match="RESTRICTIVE"):
        build(sequence_evidence=sequence, resolved_evidence=(ResolvedCompatibilityEvidence(evidence, payload),))


def test_unavailable_and_not_applicable_preserve_state_without_fabricating_ref() -> None:
    evidence, payload = ref("sequence-state")
    for state in (CompatibilityEvidenceState.UNAVAILABLE, CompatibilityEvidenceState.NOT_APPLICABLE):
        value = build(sequence_evidence=SequenceCalendarEvidence(state, None, ContractVersion("ATIS_CALENDAR_SEQUENCE", 1), state.value, (evidence,)))
        assert value.expected_sequence_evidence.state is state
        assert value.quality_input.expected_sequence_ref is None


def test_semantic_bytes_and_digest_mismatch_reject() -> None:
    wrong, _ = ref("wrong-semantic")
    with pytest.raises(CompatibilityError, match="SEMANTIC_DIGEST_MISMATCH"):
        build(canonical_bytes_ref=wrong)


def test_missing_resolved_evidence_and_identity_collision_reject() -> None:
    with pytest.raises(CompatibilityError, match="MISSING_RESOLVED_EVIDENCE"):
        build(resolved_evidence=())
    one, content = ref("collision")
    other = replace(one, content_digest=EvidenceContentDigest.from_bytes(b"different"))
    with pytest.raises(EvidenceIdentityConflict):
        build(descriptor_evidence_refs=(one, other), resolved_evidence=(ResolvedCompatibilityEvidence(one, content),))


def test_evidence_order_is_permutation_independent_and_exact_duplicates_collapse() -> None:
    one, _ = ref("one")
    two, _ = ref("two")
    first = build(descriptor_evidence_refs=(one, two, one))
    second = build(descriptor_evidence_refs=(two, one))
    assert first.descriptor_evidence_refs == second.descriptor_evidence_refs
    assert first.content_digest == second.content_digest


def test_collection_bound_rejects_without_truncation() -> None:
    refs = tuple(ref(f"bound-{index}")[0] for index in range(MAX_DESCRIPTOR_EVIDENCE_REFS + 1))
    with pytest.raises(CompatibilityError, match="RESOURCE_BOUND_EXHAUSTED"):
        build(descriptor_evidence_refs=refs)


def test_records_are_immutable_and_create_no_downstream_authority() -> None:
    value = build()
    with pytest.raises(FrozenInstanceError):
        value.semantic_bytes = b"changed"  # type: ignore[misc]
    for forbidden in ("sync_2_consumable", "eligible", "fresh", "current", "promote", "execute"):
        assert not hasattr(value, forbidden)

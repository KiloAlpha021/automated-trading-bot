from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime

import pytest

from automated_trading_bot.datasets.materialization import (
    CandidateCoverage,
    CanonicalDataset,
    CanonicalRecord,
    CanonicalRepresentationError,
    DatasetSchemaDescriptor,
    DatasetVersionId,
    HistoricalUniverseEvidence,
    LogicalContentId,
    MaterializationEvidence,
    MaterializationRequest,
    PitMaterializationError,
    PitSufficiencyState,
    RepresentationContract,
    SemanticKind,
    SemanticValue,
    TransformationExecutionBinding,
    materialize_pit_dataset,
)
from automated_trading_bot.datasets.provenance import (
    REQUIRED_RESOURCE_LIMITS,
    CanonicalDatasetRepresentationId,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    DependencyId,
    DependencyRef,
    EntitlementEvidenceId,
    EntitlementProvenance,
    EntitlementState,
    TransformationId,
    TransformationImplementationId,
    TransformationVersionId,
    build_dependency_graph,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)


def ref(name: str, content: bytes | None = None) -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:test/c08"),
        DatasetId("dataset:test/c08"),
        EvidenceId(f"evidence:test/{name}"),
        EvidenceContentDigest.from_bytes(name.encode() if content is None else content),
    )


def ts(day: int, hour: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, day, hour, tzinfo=UTC))


def resource_policy(**overrides: int) -> DatasetLifecycleResourcePolicy:
    limits = {name: 128 for name in REQUIRED_RESOURCE_LIMITS}
    limits.update(overrides)
    return DatasetLifecycleResourcePolicy(
        DatasetLifecycleResourcePolicyId(
            "resource-policy:test/c08-v1" if not overrides else "resource-policy:test/c08-custom"
        ),
        ref("resource-policy"),
        tuple(limits.items()),
    )


def semantic_record(value: str = "alpha", identity_value: str = "instrument:test/1") -> CanonicalRecord:
    return CanonicalRecord(
        "schema:test/row-v1",
        (
            ("id", SemanticValue(SemanticKind.IDENTITY, identity_value)),
            ("value", SemanticValue(SemanticKind.TEXT, value)),
            ("missing", SemanticValue(SemanticKind.NULL, None)),
        ),
    )


def entitlement(state: EntitlementState = EntitlementState.ESTABLISHED) -> EntitlementProvenance:
    return EntitlementProvenance(
        EntitlementEvidenceId(f"entitlement:test/{state.value.lower()}"),
        state,
        "provider:test" if state is EntitlementState.ESTABLISHED else None,
        ref(f"entitlement-{state.value}") if state is EntitlementState.ESTABLISHED else None,
        ref("entitlement-scope") if state is EntitlementState.NOT_APPLICABLE else None,
        None,
        (f"ENTITLEMENT_{state.value}",),
    )


def request(
    *,
    evidence: tuple[MaterializationEvidence, ...] | None = None,
    coverage_complete: bool = True,
    coverage_refs: tuple[EvidenceRef, ...] | None = None,
    coverage_scope_ref: EvidenceRef | None = None,
    cutoff: Timestamp | None = None,
    evaluation: Timestamp | None = None,
    effective_as_of: Timestamp | None = None,
    entitlements: tuple[EntitlementProvenance, ...] | None = None,
    entitlement_material: bool = True,
    universe: HistoricalUniverseEvidence | None = None,
    semantic_policies: tuple[EvidenceRef, ...] = (),
    resource: DatasetLifecycleResourcePolicy | None = None,
) -> MaterializationRequest:
    selected_cutoff = cutoff or ts(2)
    selected_evidence = evidence or (
        MaterializationEvidence(
            ref("input-a"),
            (SemanticValue(SemanticKind.IDENTITY, "instrument:test/1"),),
            semantic_record(),
            ts(2),
            ts(1),
            None,
            ts(1),
        ),
    )
    selected_resource = resource or resource_policy()
    requested_scope_ref = ref("requested-scope")
    dependency = DependencyRef(
        DependencyId("dependency:test/input"),
        EvidenceContentDigest.from_bytes(b"dependency"),
        (ref("dependency"),),
    )
    graph = build_dependency_graph(
        nodes=(),
        edges=(),
        dependencies=(dependency,),
        required_dependencies=(dependency.dependency_id,),
        resource_policy=selected_resource,
    )
    representation_id = CanonicalDatasetRepresentationId("representation:test/c08-v1")
    representation = RepresentationContract(
        representation_id,
        ref("representation-contract"),
        EvidenceContentDigest.from_bytes(b"representation-contract-v1"),
    )
    transformation = TransformationExecutionBinding.create(
        transformation_id=TransformationId("transformation:test/materialize"),
        version_id=TransformationVersionId("transformation-version:test/v1"),
        implementation_id=TransformationImplementationId("implementation:test/sha256-1234"),
        implementation_policy_ref=ref("implementation-policy"),
        contract_ref=ref("transformation-contract"),
        parameters_digest=EvidenceContentDigest.from_bytes(b"parameters"),
        input_evidence_refs=tuple(item.evidence_ref for item in selected_evidence),
        dependency_ids=(dependency.dependency_id.value,),
        representation_id=representation_id,
        pit_cutoff=selected_cutoff,
    )
    schema = DatasetSchemaDescriptor.create(
        schema_identity="schema:test/row-v1",
        schema_version="schema-version:test/v1",
        field_names=("id", "value", "missing"),
        logical_key_fields=("id",),
        schema_ref=ref("dataset-schema"),
    )
    return MaterializationRequest(
        requested_claim_or_scope_ref=requested_scope_ref,
        dataset_schema=schema,
        pit_cutoff=selected_cutoff,
        evaluation_time=evaluation or ts(3),
        effective_as_of=effective_as_of or ts(2),
        coverage=CandidateCoverage.create(
            requested_scope_ref=coverage_scope_ref or requested_scope_ref,
            coverage_evidence_ref=ref("candidate-coverage"),
            candidate_evidence_refs=(
                coverage_refs
                if coverage_refs is not None
                else tuple(item.evidence_ref for item in selected_evidence)
            ),
            complete=coverage_complete,
        ),
        evidence=selected_evidence,
        dependency_set_id=graph.dependency_set_id,
        dependency_refs=graph.dependencies,
        transformation=transformation,
        representation=representation,
        historical_universe=universe or HistoricalUniverseEvidence(False, None, (), True),
        entitlements=entitlements if entitlements is not None else (entitlement(),),
        entitlement_material=entitlement_material,
        semantic_policy_refs=semantic_policies,
        evidence_refs=(ref("materialization-authority"),),
        resource_policy=selected_resource,
    )


def test_canonical_type_tags_are_distinct_and_null_is_explicit() -> None:
    values = (
        SemanticValue(SemanticKind.TEXT, "1"),
        SemanticValue(SemanticKind.INTEGER, 1),
        SemanticValue(SemanticKind.TEXT, "true"),
        SemanticValue(SemanticKind.BOOLEAN, True),
        SemanticValue(SemanticKind.IDENTITY, "identity:test/1"),
        SemanticValue(SemanticKind.DIGEST, EvidenceContentDigest.from_bytes(b"x")),
        SemanticValue(SemanticKind.ENUM, "VALUE"),
        SemanticValue(SemanticKind.NULL, None),
    )
    bodies = {repr(item.body()) for item in values}
    assert len(bodies) == len(values)
    with pytest.raises(CanonicalRepresentationError, match="INTEGER_REQUIRES_EXACT_INT"):
        SemanticValue(SemanticKind.INTEGER, True)


def test_nfc_and_unknown_runtime_kinds_are_rejected() -> None:
    with pytest.raises(CanonicalRepresentationError, match="NOT_NFC"):
        SemanticValue(SemanticKind.TEXT, "e\u0301")
    with pytest.raises(TypeError, match="SemanticKind"):
        SemanticValue("TEXT", "value")  # type: ignore[arg-type]


def test_record_sequence_set_timestamp_and_date_canonicalization() -> None:
    first = SemanticValue(SemanticKind.TEXT, "a")
    second = SemanticValue(SemanticKind.TEXT, "b")
    sequence = SemanticValue(SemanticKind.SEQUENCE, (second, first))
    set_one = SemanticValue(SemanticKind.SET, (second, first, first))
    set_two = SemanticValue(SemanticKind.SET, (first, second))
    assert sequence.body()["value"] == [second.body(), first.body()]
    assert set_one == set_two
    assert SemanticValue(SemanticKind.UTC_TIMESTAMP, ts(1)).body()["value"] == "2026-01-01T00:00:00.000000Z"
    assert SemanticValue(SemanticKind.LOCAL_DATE, date(2026, 1, 1)).body()["value"] == "2026-01-01"
    assert SemanticValue(SemanticKind.RECORD, semantic_record()).body()["value"] == semantic_record().body()


def test_decimal_looking_text_remains_text() -> None:
    value = SemanticValue(SemanticKind.TEXT, "1.25")
    assert value.body() == {"kind": "TEXT", "value": "1.25"}


def test_deterministic_ids_and_record_identity_binding() -> None:
    first = materialize_pit_dataset(request())
    second = materialize_pit_dataset(request())
    assert first == second
    assert isinstance(first.logical_content_id, LogicalContentId)
    assert isinstance(first.dataset_version_id, DatasetVersionId)
    assert first.materialization_record.pit_sufficiency_state is PitSufficiencyState.ESTABLISHED
    with pytest.raises(PitMaterializationError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(first.materialization_record, limitations=("FORGED",))


def test_dataset_version_excludes_resource_policy_and_includes_semantic_policy() -> None:
    baseline = materialize_pit_dataset(request())
    other_resource = materialize_pit_dataset(request(resource=resource_policy(MAX_REASONS_PER_RECORD=31)))
    semantic = materialize_pit_dataset(request(semantic_policies=(ref("semantic-policy"),)))
    assert baseline.dataset_version_id == other_resource.dataset_version_id
    assert baseline.materialization_record.record_id != other_resource.materialization_record.record_id
    assert baseline.dataset_version_id != semantic.dataset_version_id


def test_dataset_version_is_outside_materialization_record_body() -> None:
    result = materialize_pit_dataset(request())
    assert "dataset_version_id" not in result.materialization_record.__dataclass_fields__
    assert result.dataset_version_id.value.startswith("c08-dataset-version:")


def test_cutoff_equality_is_allowed_and_after_cutoff_is_excluded() -> None:
    at = materialize_pit_dataset(request())
    future_evidence = MaterializationEvidence(
        ref("input-future"),
        (SemanticValue(SemanticKind.IDENTITY, "instrument:test/1"),),
        semantic_record("future"),
        ts(3),
        ts(1),
        None,
    )
    future = materialize_pit_dataset(request(evidence=(future_evidence,)))
    assert at.materialization_record.pit_sufficiency_state is PitSufficiencyState.ESTABLISHED
    assert len(at.semantic_dataset.records) == 1
    assert future.materialization_record.pit_sufficiency_state is PitSufficiencyState.ESTABLISHED
    assert future.semantic_dataset.records == ()


def test_cutoff_after_evaluation_is_rejected() -> None:
    with pytest.raises(PitMaterializationError, match="CUTOFF_AFTER"):
        materialize_pit_dataset(request(cutoff=ts(3), evaluation=ts(2)))


def test_missing_knowledge_is_not_replaced_by_acquisition() -> None:
    item = MaterializationEvidence(
        ref("input-missing-knowledge"),
        (SemanticValue(SemanticKind.IDENTITY, "instrument:test/1"),),
        semantic_record(),
        None,
        ts(1),
        None,
        ts(1),
    )
    result = materialize_pit_dataset(request(evidence=(item,)))
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert "EVIDENCE_KNOWLEDGE_TIME_NOT_ESTABLISHED" in result.materialization_record.limitations


def test_known_but_ineffective_evidence_is_not_selected() -> None:
    item = MaterializationEvidence(
        ref("input-not-effective"),
        (SemanticValue(SemanticKind.IDENTITY, "instrument:test/1"),),
        semantic_record(),
        ts(1),
        ts(3),
        None,
    )
    result = materialize_pit_dataset(request(evidence=(item,), effective_as_of=ts(2)))
    assert result.semantic_dataset.records == ()
    assert result.materialization_record.selected_evidence_refs == ()


def test_independent_candidate_scope_is_required() -> None:
    result = materialize_pit_dataset(request(coverage_complete=False))
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert result.semantic_dataset.records == ()
    assert "CANDIDATE_SCOPE_COMPLETENESS_NOT_ESTABLISHED" in result.materialization_record.limitations


def test_transformation_substitution_is_rejected() -> None:
    base = request()
    with pytest.raises(PitMaterializationError, match="IDENTITY_CONTENT_CONFLICT"):
        replace(
            base.transformation,
            parameters_digest=EvidenceContentDigest.from_bytes(b"different"),
        )


def test_mutable_implementation_reference_is_rejected() -> None:
    base = request()
    with pytest.raises(PitMaterializationError, match="MUTABLE"):
        TransformationExecutionBinding.create(
            transformation_id=base.transformation.transformation_id,
            version_id=base.transformation.version_id,
            implementation_id=TransformationImplementationId("implementation:test/latest"),
            implementation_policy_ref=base.transformation.implementation_policy_ref,
            contract_ref=base.transformation.contract_ref,
            parameters_digest=base.transformation.parameters_digest,
            input_evidence_refs=base.transformation.input_evidence_refs,
            dependency_ids=base.transformation.dependency_ids,
            representation_id=base.transformation.representation_id,
            pit_cutoff=base.transformation.pit_cutoff,
        )


def test_exact_duplicates_collapse_and_provenance_is_retained() -> None:
    first = request().evidence[0]
    duplicate = replace(first, evidence_ref=ref("input-b"))
    result = materialize_pit_dataset(request(evidence=(duplicate, first)))
    assert len(result.semantic_dataset.records) == 1
    assert result.materialization_record.selected_evidence_refs == (first.evidence_ref, duplicate.evidence_ref)


def test_conflicting_logical_key_is_incompatible() -> None:
    first = request().evidence[0]
    conflict = replace(first, evidence_ref=ref("input-conflict"), record=semantic_record("beta"))
    result = materialize_pit_dataset(request(evidence=(first, conflict)))
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.INCOMPATIBLE
    assert result.semantic_dataset.records == ()


def test_unresolved_multiplicity_is_not_established() -> None:
    item = replace(request().evidence[0], multiplicity_established=False)
    result = materialize_pit_dataset(request(evidence=(item,)))
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED


@pytest.mark.parametrize(
    ("universe", "state"),
    (
        (HistoricalUniverseEvidence(True, None, (), True), PitSufficiencyState.NOT_ESTABLISHED),
        (HistoricalUniverseEvidence(True, ref("universe-policy"), (ref("universe-evidence"),), False), PitSufficiencyState.INCOMPATIBLE),
    ),
)
def test_historical_universe_is_restrictive(
    universe: HistoricalUniverseEvidence, state: PitSufficiencyState
) -> None:
    assert materialize_pit_dataset(request(universe=universe)).materialization_record.pit_sufficiency_state is state


def test_current_constituents_cannot_prove_historical_universe() -> None:
    universe = HistoricalUniverseEvidence(True, ref("universe-policy"), (ref("universe-evidence"),), True, True)
    with pytest.raises(PitMaterializationError, match="CURRENT_CONSTITUENTS"):
        materialize_pit_dataset(request(universe=universe))


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (EntitlementState.ESTABLISHED, PitSufficiencyState.ESTABLISHED),
        (EntitlementState.NOT_APPLICABLE, PitSufficiencyState.ESTABLISHED),
        (EntitlementState.UNKNOWN, PitSufficiencyState.NOT_ESTABLISHED),
        (EntitlementState.INCOMPATIBLE, PitSufficiencyState.INCOMPATIBLE),
    ),
)
def test_entitlement_consequences_and_provenance_are_preserved(
    state: EntitlementState, expected: PitSufficiencyState
) -> None:
    entry = entitlement(state)
    result = materialize_pit_dataset(request(entitlements=(entry,)))
    assert result.materialization_record.pit_sufficiency_state is expected
    assert result.materialization_record.entitlement_provenance_inputs_where_material == (entry,)


def test_resource_exhaustion_is_restrictive_without_truncation() -> None:
    first = request().evidence[0]
    second = replace(first, evidence_ref=ref("input-second"), logical_key=(SemanticValue(SemanticKind.IDENTITY, "instrument:test/2"),), record=semantic_record("second", "instrument:test/2"))
    result = materialize_pit_dataset(
        request(evidence=(first, second), resource=resource_policy(MAX_MATERIALIZATION_EVIDENCE_REFS=1))
    )
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert result.semantic_dataset.records == ()
    assert len(result.materialization_record.selected_evidence_refs) == 2


def test_policy_and_reason_resource_bounds_fail_closed() -> None:
    policy_result = materialize_pit_dataset(
        request(
            semantic_policies=(ref("policy-a"), ref("policy-b")),
            resource=resource_policy(MAX_POLICY_REFS=1),
        )
    )
    assert policy_result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert "MAX_POLICY_REFS_EXHAUSTED" in policy_result.materialization_record.limitations

    with pytest.raises(PitMaterializationError, match="MAX_REASONS_PER_RECORD"):
        materialize_pit_dataset(
            request(
                coverage_complete=False,
                entitlements=(entitlement(EntitlementState.UNKNOWN),),
                resource=resource_policy(MAX_REASONS_PER_RECORD=1),
            )
        )
    with pytest.raises(PitMaterializationError, match="MAX_REASON_LENGTH"):
        materialize_pit_dataset(
            request(coverage_complete=False, resource=resource_policy(MAX_REASON_LENGTH=1))
        )


def test_dependency_omission_and_scope_substitution_are_rejected() -> None:
    base = request()
    with pytest.raises(PitMaterializationError, match="DEPENDENCY_SET_IDENTITY_CONTENT_CONFLICT"):
        materialize_pit_dataset(
            replace(base, dependency_set_id=type(base.dependency_set_id)("dependency-set:test/forged"))
        )
    with pytest.raises(PitMaterializationError, match="TRANSFORMATION_DEPENDENCY_SUBSTITUTION"):
        materialize_pit_dataset(replace(base, dependency_refs=()))


def test_later_correction_creates_a_distinct_immutable_successor() -> None:
    original = materialize_pit_dataset(request())
    prior = request().evidence[0]
    correction = replace(
        prior,
        evidence_ref=ref("input-correction"),
        record=semantic_record("corrected"),
        knowledge_time=ts(3),
    )
    corrected = materialize_pit_dataset(request(evidence=(correction,), cutoff=ts(3)))
    assert original.semantic_dataset.records == (semantic_record(),)
    assert corrected.semantic_dataset.records == (semantic_record("corrected"),)
    assert original.logical_content_id != corrected.logical_content_id


def test_incompatible_precedes_not_established_and_reasons_are_canonical() -> None:
    first = replace(request().evidence[0], multiplicity_established=False)
    conflict = replace(first, evidence_ref=ref("input-conflict"), record=semantic_record("conflict"))
    result = materialize_pit_dataset(request(evidence=(conflict, first), coverage_complete=False))
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.INCOMPATIBLE
    assert result.materialization_record.limitations == tuple(sorted(result.materialization_record.limitations))
    assert "RECORD_MULTIPLICITY_NOT_ESTABLISHED" in result.materialization_record.limitations
    assert "CONFLICTING_LOGICAL_KEY_CONTENT" in result.materialization_record.limitations


def test_permutation_determinism_and_immutability() -> None:
    first = request().evidence[0]
    second = replace(first, evidence_ref=ref("input-second"), logical_key=(SemanticValue(SemanticKind.IDENTITY, "instrument:test/2"),), record=semantic_record("second", "instrument:test/2"))
    one = materialize_pit_dataset(request(evidence=(first, second)))
    two = materialize_pit_dataset(request(evidence=(second, first)))
    assert one == two
    with pytest.raises(FrozenInstanceError):
        one.materialization_record.limitations = ("MUTATED",)  # type: ignore[misc]


def test_predecessors_and_authority_are_not_mutated_or_created() -> None:
    selected = request()
    original = selected
    result = materialize_pit_dataset(selected)
    assert selected == original
    forbidden = {
        "manifest_id", "provider", "storage", "publication", "persistence",
        "promotion", "quality", "eligibility", "trading", "financial",
    }
    assert forbidden.isdisjoint(result.__dataclass_fields__)
    assert forbidden.isdisjoint(result.materialization_record.__dataclass_fields__)


def test_physical_bytes_are_not_part_of_semantic_dataset() -> None:
    dataset = CanonicalDataset("schema:test/a", "schema-version:test/v1", ("id",), (semantic_record(),))
    assert "parquet" not in repr(dataset.body()).lower()
    assert "arrow" not in repr(dataset.body()).lower()


def test_candidate_coverage_binds_scope_evidence_and_exact_population() -> None:
    first = request().evidence[0]
    second = replace(
        first,
        evidence_ref=ref("input-second"),
        logical_key=(SemanticValue(SemanticKind.IDENTITY, "instrument:test/2"),),
        record=semantic_record("second", "instrument:test/2"),
    )
    missing = materialize_pit_dataset(
        request(evidence=(first,), coverage_refs=(first.evidence_ref, second.evidence_ref))
    )
    excess = materialize_pit_dataset(
        request(evidence=(first, second), coverage_refs=(first.evidence_ref,))
    )
    wrong_scope = materialize_pit_dataset(request(coverage_scope_ref=ref("other-scope")))
    assert missing.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert excess.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert wrong_scope.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert "CANDIDATE_POPULATION_NOT_ESTABLISHED" in missing.materialization_record.limitations
    assert "CANDIDATE_POPULATION_NOT_ESTABLISHED" in excess.materialization_record.limitations
    assert "CANDIDATE_COVERAGE_SCOPE_NOT_ESTABLISHED" in wrong_scope.materialization_record.limitations

    one = CandidateCoverage.create(
        requested_scope_ref=ref("requested-scope"),
        coverage_evidence_ref=ref("coverage"),
        candidate_evidence_refs=(first.evidence_ref, second.evidence_ref),
        complete=True,
    )
    two = CandidateCoverage.create(
        requested_scope_ref=ref("requested-scope"),
        coverage_evidence_ref=ref("coverage"),
        candidate_evidence_refs=(second.evidence_ref, first.evidence_ref),
        complete=True,
    )
    assert one == two
    with pytest.raises(PitMaterializationError, match="CANDIDATE_POPULATION_DIGEST_CONFLICT"):
        replace(one, candidate_population_digest=EvidenceContentDigest.from_bytes(b"forged"))
    with pytest.raises(PitMaterializationError, match="CANDIDATE_COVERAGE_IDENTITY_CONTENT_CONFLICT"):
        replace(one, complete=False)


def test_full_material_entitlement_provenance_changes_record_identity_only() -> None:
    base_entry = EntitlementProvenance(
        EntitlementEvidenceId("entitlement:test/full"),
        EntitlementState.ESTABLISHED,
        "provider:test/a",
        ref("entitlement-evidence-a"),
        ref("entitlement-scope-a"),
        ref("entitlement-predecessor-a"),
        ("ENTITLEMENT_ESTABLISHED",),
    )
    baseline = materialize_pit_dataset(request(entitlements=(base_entry,)))
    variants = (
        replace(base_entry, provider_or_source_id="provider:test/b"),
        replace(base_entry, evidence_ref=ref("entitlement-evidence-b")),
        replace(base_entry, independent_scope_ref=ref("entitlement-scope-b")),
        replace(base_entry, predecessor_ref=ref("entitlement-predecessor-b")),
    )
    for variant in variants:
        changed = materialize_pit_dataset(request(entitlements=(variant,)))
        assert changed.materialization_record.content_digest != baseline.materialization_record.content_digest
        assert changed.materialization_record.record_id != baseline.materialization_record.record_id
        assert changed.dataset_version_id == baseline.dataset_version_id


def test_logical_key_is_derived_from_record_and_schema_fields_are_exact() -> None:
    base = request()
    item = base.evidence[0]
    with pytest.raises(PitMaterializationError, match="LOGICAL_KEY_RECORD_MISMATCH"):
        materialize_pit_dataset(
            request(
                evidence=(
                    replace(
                        item,
                        logical_key=(SemanticValue(SemanticKind.IDENTITY, "instrument:test/other"),),
                    ),
                )
            )
        )
    explicit_null = materialize_pit_dataset(base)
    assert explicit_null.materialization_record.pit_sufficiency_state is PitSufficiencyState.ESTABLISHED
    absent = CanonicalRecord(
        "schema:test/row-v1",
        (("id", SemanticValue(SemanticKind.IDENTITY, "instrument:test/1")),
         ("value", SemanticValue(SemanticKind.TEXT, "alpha"))),
    )
    with pytest.raises(CanonicalRepresentationError, match="SCHEMA_FIELD_SET_MISMATCH"):
        materialize_pit_dataset(request(evidence=(replace(item, record=absent),)))


@pytest.mark.parametrize(
    "field_names",
    (
        ("id", "value", "missing", "extra"),
        ("id", "value"),
        ("id", "value", "substitute"),
    ),
)
def test_schema_field_addition_removal_and_substitution_reject(
    field_names: tuple[str, ...],
) -> None:
    base = request()
    changed_schema = DatasetSchemaDescriptor.create(
        schema_identity=base.dataset_schema.schema_identity,
        schema_version=base.dataset_schema.schema_version,
        field_names=field_names,
        logical_key_fields=("id",),
        schema_ref=base.dataset_schema.schema_ref,
    )
    with pytest.raises(CanonicalRepresentationError, match="SCHEMA_FIELD_SET_MISMATCH"):
        materialize_pit_dataset(replace(base, dataset_schema=changed_schema))


def test_schema_descriptor_is_attributable_and_rejects_invalid_key_catalogue() -> None:
    base = request()
    with pytest.raises(CanonicalRepresentationError, match="LOGICAL_KEY_FIELD_NOT_DECLARED"):
        DatasetSchemaDescriptor.create(
            schema_identity="schema:test/row-v1",
            schema_version="schema-version:test/v1",
            field_names=("id", "value"),
            logical_key_fields=("missing",),
            schema_ref=ref("dataset-schema"),
        )
    with pytest.raises(CanonicalRepresentationError, match="SCHEMA_DESCRIPTOR_IDENTITY_CONTENT_CONFLICT"):
        replace(base.dataset_schema, schema_ref=ref("other-schema"))


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("transformation_id", TransformationId("transformation:test/other")),
        ("version_id", TransformationVersionId("transformation-version:test/v2")),
        ("implementation_id", TransformationImplementationId("implementation:test/sha256-5678")),
        ("implementation_policy_ref", ref("other-implementation-policy")),
        ("contract_ref", ref("other-transformation-contract")),
        ("parameters_digest", EvidenceContentDigest.from_bytes(b"other-parameters")),
        ("input_evidence_refs", (ref("other-input"),)),
        ("dependency_ids", ("dependency:test/other",)),
        ("representation_id", CanonicalDatasetRepresentationId("representation:test/other")),
        ("pit_cutoff", ts(1)),
    ),
)
def test_each_transformation_execution_substitution_rejects_claimed_identity(
    field: str, replacement: object
) -> None:
    with pytest.raises(PitMaterializationError, match="TRANSFORMATION_EXECUTION_IDENTITY_CONTENT_CONFLICT"):
        replace(request().transformation, **{field: replacement})


def test_dependency_body_substitution_invalidates_dependency_set() -> None:
    base = request()
    changed = replace(
        base.dependency_refs[0],
        content_digest=EvidenceContentDigest.from_bytes(b"changed-dependency"),
    )
    with pytest.raises(PitMaterializationError, match="DEPENDENCY_SET_IDENTITY_CONTENT_CONFLICT"):
        materialize_pit_dataset(replace(base, dependency_refs=(changed,)))


def test_dependency_count_exhaustion_is_restrictive() -> None:
    base = request()
    second = DependencyRef(
        DependencyId("dependency:test/second"),
        EvidenceContentDigest.from_bytes(b"second-dependency"),
        (ref("second-dependency"),),
    )
    dependencies = base.dependency_refs + (second,)
    graph = build_dependency_graph(
        nodes=(), edges=(), dependencies=dependencies,
        required_dependencies=tuple(item.dependency_id for item in dependencies),
        resource_policy=resource_policy(),
    )
    transformation = TransformationExecutionBinding.create(
        transformation_id=base.transformation.transformation_id,
        version_id=base.transformation.version_id,
        implementation_id=base.transformation.implementation_id,
        implementation_policy_ref=base.transformation.implementation_policy_ref,
        contract_ref=base.transformation.contract_ref,
        parameters_digest=base.transformation.parameters_digest,
        input_evidence_refs=base.transformation.input_evidence_refs,
        dependency_ids=tuple(item.dependency_id.value for item in dependencies),
        representation_id=base.transformation.representation_id,
        pit_cutoff=base.transformation.pit_cutoff,
    )
    result = materialize_pit_dataset(
        replace(
            base,
            dependency_set_id=graph.dependency_set_id,
            dependency_refs=graph.dependencies,
            transformation=transformation,
            resource_policy=resource_policy(MAX_DEPENDENCIES_PER_DATASET_VERSION=1),
        )
    )
    assert result.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert "MAX_DEPENDENCIES_PER_DATASET_VERSION_EXHAUSTED" in result.materialization_record.limitations


def test_material_entitlement_empty_is_restrictive_and_nonmaterial_is_ignored() -> None:
    required = materialize_pit_dataset(request(entitlements=()))
    nonmaterial = materialize_pit_dataset(request(entitlements=(), entitlement_material=False))
    assert required.materialization_record.pit_sufficiency_state is PitSufficiencyState.NOT_ESTABLISHED
    assert nonmaterial.materialization_record.pit_sufficiency_state is PitSufficiencyState.ESTABLISHED
    assert nonmaterial.materialization_record.entitlement_provenance_inputs_where_material == ()


def test_invalid_identity_and_digest_syntax_reject() -> None:
    with pytest.raises(ValueError):
        LogicalContentId("invalid identity")
    with pytest.raises(ValueError):
        EvidenceContentDigest("not-a-protected-digest")


def test_wrong_logical_key_arity_is_rejected_directly() -> None:
    item = request().evidence[0]
    wrong_arity = replace(
        item,
        logical_key=(
            SemanticValue(SemanticKind.IDENTITY, "instrument:test/1"),
            SemanticValue(SemanticKind.TEXT, "unexpected-second-key-part"),
        ),
    )
    with pytest.raises(PitMaterializationError, match="LOGICAL_KEY_ARITY_MISMATCH"):
        materialize_pit_dataset(request(evidence=(wrong_arity,)))


def test_missing_declared_logical_key_field_is_rejected_directly() -> None:
    item = request().evidence[0]
    missing_logical_key_field = CanonicalRecord(
        "schema:test/row-v1",
        (
            ("value", SemanticValue(SemanticKind.TEXT, "alpha")),
            ("missing", SemanticValue(SemanticKind.NULL, None)),
        ),
    )
    with pytest.raises(CanonicalRepresentationError, match="SCHEMA_FIELD_SET_MISMATCH"):
        materialize_pit_dataset(
            request(evidence=(replace(item, record=missing_logical_key_field),))
        )


def test_extra_unknown_record_field_is_rejected_directly() -> None:
    item = request().evidence[0]
    extra_unknown_field = CanonicalRecord(
        "schema:test/row-v1",
        item.record.fields
        + (("unexpected", SemanticValue(SemanticKind.TEXT, "not-declared")),),
    )
    with pytest.raises(CanonicalRepresentationError, match="SCHEMA_FIELD_SET_MISMATCH"):
        materialize_pit_dataset(
            request(evidence=(replace(item, record=extra_unknown_field),))
        )

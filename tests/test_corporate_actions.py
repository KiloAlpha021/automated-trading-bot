from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import UUID

import pytest

from automated_trading_bot.corporate_actions import (
    ActionFamily,
    ActionOperation,
    AffectedDependency,
    AffectedDependencyDeclaration,
    DependencyKind,
    DependencyState,
    IdentityBindingAssertion,
    LineageAuthorityAssertion,
    LineageValidationError,
    ProviderNativeActionRef,
    TemporalRoleSlot,
    TemporalRoleState,
    TemporalRoles,
    compute_action_facts_digest,
    create_action_version,
    create_canonical_action,
    validate_action_lineage,
)
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
    ValidationPolicyId,
    ValidationState,
)


INSTRUMENT = InstrumentId(UUID("00000000-0000-4000-8000-000000000001"))
OTHER_INSTRUMENT = InstrumentId(UUID("00000000-0000-4000-8000-000000000002"))
SOURCE = SourceId("source:atis/actions")
DATASET = DatasetId("dataset:atis/actions")
POLICY = ValidationPolicyId("validation-policy:atis/c07-v1")
T1 = Timestamp(datetime(2026, 1, 2, 10, tzinfo=timezone.utc))
T2 = Timestamp(datetime(2026, 1, 3, 10, tzinfo=timezone.utc))


def evidence(number: int, content: bytes | None = None) -> EvidenceRef:
    payload = content if content is not None else f"evidence-{number}".encode()
    return EvidenceRef(SOURCE, DATASET, EvidenceId(f"evidence:atis/action-{number}"), EvidenceContentDigest.from_bytes(payload))


def assertion(
    family: ActionFamily = ActionFamily.SPLIT,
    occurrence: str = "evidence:atis/occurrence-1",
) -> IdentityBindingAssertion:
    support = (evidence(1),)
    mapping = {
        "atis_occurrence_key": occurrence,
        "family": family.value,
        "identity_policy_id": POLICY.value,
        "kind": "ATIS_C07_IDENTITY_BINDING_V1",
        "root_instrument_id": INSTRUMENT.to_string(),
        "supporting_evidence_refs": [{
            "content_digest": support[0].content_digest.value,
            "dataset_id": DATASET.value,
            "evidence_id": support[0].evidence_id.value,
            "source_id": SOURCE.value,
        }],
    }
    import json
    digest = EvidenceContentDigest.from_bytes(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode())
    return IdentityBindingAssertion(
        INSTRUMENT, family, EvidenceId(occurrence),
        POLICY, support, EvidenceRef(SOURCE, DATASET, EvidenceId("evidence:atis/identity-binding"), digest),
    )


def action(
    family: ActionFamily = ActionFamily.SPLIT,
    occurrence: str = "evidence:atis/occurrence-1",
):
    return create_canonical_action(
        assertion(family, occurrence), admitted_identity_authority_ref=evidence(2),
    )


def slot(state: TemporalRoleState, at: Timestamp | None = None, number: int = 3) -> TemporalRoleSlot:
    return TemporalRoleSlot(state, at, (evidence(number),))


def roles(knowledge: Timestamp = T1, effective: Timestamp = T1) -> TemporalRoles:
    na = slot(TemporalRoleState.NOT_APPLICABLE, number=8)
    return TemporalRoles(
        knowledge_from=slot(TemporalRoleState.ESTABLISHED, knowledge, 3),
        announced_at=slot(TemporalRoleState.NOT_ESTABLISHED, number=4),
        effective_at=slot(TemporalRoleState.ESTABLISHED, effective, 5),
        ex_at=slot(TemporalRoleState.NOT_ESTABLISHED, number=6),
        record_at=slot(TemporalRoleState.NOT_ESTABLISHED, number=7),
        payable_at=na,
    )


def family_roles(family: ActionFamily) -> TemporalRoles:
    established_knowledge = slot(TemporalRoleState.ESTABLISHED, T1, 3)
    not_established = slot(TemporalRoleState.NOT_ESTABLISHED, number=4)
    not_applicable = slot(TemporalRoleState.NOT_APPLICABLE, number=8)
    if family is ActionFamily.DIVIDEND:
        return TemporalRoles(
            established_knowledge, not_established, not_established,
            slot(TemporalRoleState.ESTABLISHED, T1, 6), not_established, not_established,
        )
    return TemporalRoles(
        established_knowledge, not_established,
        slot(TemporalRoleState.ESTABLISHED, T1, 5),
        not_established if family is ActionFamily.SPLIT else not_applicable,
        not_established if family is ActionFamily.SPLIT else not_applicable,
        not_applicable,
    )


def dependencies() -> AffectedDependencyDeclaration:
    entry = AffectedDependency(
        DependencyKind.DATASET_CONTENT, evidence(20), (INSTRUMENT,), T1, None,
        "potentially affected; declaration only", (evidence(21),),
    )
    return AffectedDependencyDeclaration(DependencyState.DECLARED, (entry,), evidence(22))


def original(**overrides: object):
    values = dict(
        action=action(), operation=ActionOperation.ORIGINAL, temporal_roles=roles(),
        action_terms_ref=evidence(10), provider_native_refs=(),
        source_evidence_refs=(evidence(11),), validation_policy_id=POLICY,
        validation_state=ValidationState.VALID, affected_dependencies=dependencies(),
    )
    values.update(overrides)
    return create_action_version(**values)  # type: ignore[arg-type]


def lineage_authority(predecessor, facts_digest, operation=ActionOperation.CORRECTION):
    import json
    decision = (evidence(30),)
    mapping = {
        "authority_policy_id": POLICY.value,
        "canonical_action_id": predecessor.canonical_action_id.value,
        "decision_evidence_refs": [{
            "content_digest": decision[0].content_digest.value,
            "dataset_id": DATASET.value,
            "evidence_id": decision[0].evidence_id.value,
            "source_id": SOURCE.value,
        }],
        "kind": "ATIS_C07_LINEAGE_AUTHORIZATION_V1",
        "operation": operation.value,
        "predecessor_version_id": predecessor.action_version_id.value,
        "successor_facts_digest": facts_digest.value,
    }
    digest = EvidenceContentDigest.from_bytes(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode())
    return LineageAuthorityAssertion(
        operation, predecessor.canonical_action_id, predecessor.action_version_id,
        facts_digest, POLICY, decision,
        EvidenceRef(SOURCE, DATASET, EvidenceId("evidence:atis/lineage-authority"), digest),
    )


def test_canonical_identity_is_deterministic_and_provider_native_identity_is_separate() -> None:
    first = action()
    second = action()
    assert first.canonical_action_id == second.canonical_action_id
    provider = ProviderNativeActionRef(SOURCE, DATASET, evidence(40), "vendor-event-123", evidence(41))
    version = original(provider_native_refs=(provider,))
    assert provider.native_event_id not in version.canonical_action_id.value
    assert version.provider_native_refs == (provider,)


def test_identity_assertion_cannot_self_authorize() -> None:
    value = assertion()
    with pytest.raises(ValueError, match="cannot admit its own authority"):
        create_canonical_action(value, admitted_identity_authority_ref=value.assertion_ref)


@pytest.mark.parametrize("family", list(ActionFamily))
def test_supported_families_are_closed_and_family_temporal_roles_are_enforced(family: ActionFamily) -> None:
    value = action(family)
    assert value.family is family
    version = original(action=value, temporal_roles=family_roles(family))
    assert version.family is family
    with pytest.raises(ValueError):
        ActionFamily("vendor-nearest-family")


def test_valid_split_requires_exact_family_specific_roles() -> None:
    bad = replace(roles(), effective_at=slot(TemporalRoleState.NOT_ESTABLISHED, number=5))
    with pytest.raises(ValueError, match="requires established effective_at"):
        original(temporal_roles=bad)
    bad_payable = replace(roles(), payable_at=slot(TemporalRoleState.ESTABLISHED, T1, 8))
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        original(temporal_roles=bad_payable)


def test_temporal_roles_never_promote_missing_or_date_only_evidence() -> None:
    with pytest.raises(ValueError, match="requires a Timestamp"):
        slot(TemporalRoleState.ESTABLISHED)
    with pytest.raises(TypeError):
        slot(TemporalRoleState.ESTABLISHED, "2026-01-02")  # type: ignore[arg-type]


def test_version_identity_and_evidence_order_are_deterministic() -> None:
    a = original(source_evidence_refs=(evidence(12), evidence(11)))
    b = original(source_evidence_refs=(evidence(11), evidence(12)))
    assert a.action_version_id == b.action_version_id
    assert a.facts_digest == b.facts_digest


def test_records_are_immutable() -> None:
    value = original()
    with pytest.raises(FrozenInstanceError):
        value.operation = ActionOperation.CANCELLATION  # type: ignore[misc]


def test_dependency_declaration_is_deterministic_and_does_not_execute_effects() -> None:
    first = dependencies()
    second = AffectedDependencyDeclaration(first.state, tuple(reversed(first.entries)), first.scope_ref)
    assert first.mapping() == second.mapping()
    assert not hasattr(first, "invalidate")
    assert not hasattr(first, "materialize")
    assert not hasattr(first, "adjust_position")


def test_dependency_states_fail_closed() -> None:
    with pytest.raises(ValueError, match="KNOWN_EMPTY"):
        AffectedDependencyDeclaration(DependencyState.KNOWN_EMPTY, (), None)
    with pytest.raises(ValueError, match="NOT_ESTABLISHED"):
        AffectedDependencyDeclaration(DependencyState.NOT_ESTABLISHED, (), evidence(1))


def test_dependency_resource_bound_fails_without_truncation() -> None:
    base = dependencies().entries[0]
    entries = tuple(
        replace(base, dependency_ref=evidence(1000 + number)) for number in range(257)
    )
    with pytest.raises(ValueError, match="resource limit"):
        AffectedDependencyDeclaration(DependencyState.DECLARED, entries, evidence(22))


def test_correction_requires_independent_exact_authority_and_preserves_predecessor() -> None:
    root = original()
    kwargs = dict(
        action=action(), operation=ActionOperation.CORRECTION, temporal_roles=roles(T2, T1),
        action_terms_ref=evidence(10), provider_native_refs=(), source_evidence_refs=(evidence(13),),
        validation_policy_id=POLICY, validation_state=ValidationState.VALID,
        affected_dependencies=dependencies(), predecessor_version_id=root.action_version_id,
        lineage_reason="corrected split terms",
    )
    with pytest.raises(ValueError, match="independent lineage authority"):
        create_action_version(**kwargs)
    facts = compute_action_facts_digest(**kwargs)
    authority = lineage_authority(root, facts)
    correction = create_action_version(**kwargs, lineage_authorities=(authority,))
    assert correction.predecessor_version_id == root.action_version_id
    assert correction.action_version_id != root.action_version_id
    assert validate_action_lineage((correction, root)) == validate_action_lineage((root, correction))


def test_wrong_or_self_declared_correction_authority_rejects() -> None:
    root = original()
    fake = lineage_authority(root, root.facts_digest)
    with pytest.raises(ValueError, match="exact successor facts"):
        create_action_version(
            action=action(), operation=ActionOperation.CORRECTION, temporal_roles=roles(T2, T1),
            action_terms_ref=evidence(10), provider_native_refs=(), source_evidence_refs=(evidence(13),),
            validation_policy_id=POLICY, validation_state=ValidationState.VALID,
            affected_dependencies=dependencies(), predecessor_version_id=root.action_version_id,
            lineage_reason="provider says correction", lineage_authorities=(fake,),
        )


def test_cancellation_is_an_immutable_successor_and_does_not_erase_history() -> None:
    root = original()
    kwargs = dict(
        action=action(), operation=ActionOperation.CANCELLATION,
        temporal_roles=roles(T2, T1), action_terms_ref=evidence(10),
        provider_native_refs=(), source_evidence_refs=(evidence(14),),
        validation_policy_id=POLICY, validation_state=ValidationState.VALID,
        affected_dependencies=dependencies(), predecessor_version_id=root.action_version_id,
        lineage_reason="independently authorized cancellation",
    )
    facts = compute_action_facts_digest(**kwargs)
    cancellation = create_action_version(
        **kwargs,
        lineage_authorities=(lineage_authority(root, facts, ActionOperation.CANCELLATION),),
    )
    lineage = validate_action_lineage((cancellation, root))
    assert set(lineage) == {root, cancellation}
    assert cancellation.predecessor_version_id == root.action_version_id
    assert cancellation.action_version_id != root.action_version_id


def test_ambiguous_successor_branch_fails_closed() -> None:
    root = original()

    def successor(reason: str, evidence_number: int):
        kwargs = dict(
            action=action(), operation=ActionOperation.CORRECTION,
            temporal_roles=roles(T2, T1), action_terms_ref=evidence(10),
            provider_native_refs=(), source_evidence_refs=(evidence(evidence_number),),
            validation_policy_id=POLICY, validation_state=ValidationState.VALID,
            affected_dependencies=dependencies(), predecessor_version_id=root.action_version_id,
            lineage_reason=reason,
        )
        facts = compute_action_facts_digest(**kwargs)
        return create_action_version(**kwargs, lineage_authorities=(lineage_authority(root, facts),))

    with pytest.raises(LineageValidationError, match="BRANCHING_SUCCESSOR_CONFLICT"):
        validate_action_lineage((root, successor("first", 15), successor("second", 16)))


def test_lineage_rejects_missing_predecessor_branching_and_cross_action_edges() -> None:
    root = original()
    alien = original(action=action(occurrence="evidence:atis/occurrence-2"))
    with pytest.raises(LineageValidationError, match="EXACTLY_ONE_ORIGINAL"):
        validate_action_lineage((root, alien))
    with pytest.raises(LineageValidationError, match="lineage must"):
        validate_action_lineage(())


def test_no_downstream_or_financial_authority_surface_exists() -> None:
    value = original()
    for forbidden in (
        "invalidate", "materialize", "publish", "rematerialize", "adjust_cash",
        "adjust_position", "submit_order", "execute_trade", "select_provider", "select_storage",
    ):
        assert not hasattr(value, forbidden)

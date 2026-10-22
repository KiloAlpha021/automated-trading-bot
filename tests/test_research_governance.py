from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import ListingId
from automated_trading_bot.research import research_governance as governance
from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
    ClaimCeiling,
    DIMENSIONS,
    DimensionAssessment,
    EligibleCoverage,
    QualificationLevel,
    QualificationPolicy,
    qualify_dataset,
)
from automated_trading_bot.research.evidence import (
    EvidenceDisposition,
    ResearchEvidenceManifest,
)
from automated_trading_bot.research.identity import (
    Experiment,
    Hypothesis,
    ResearchProject,
    ResearchRun,
)
from automated_trading_bot.research.pit_features import (
    AvailabilityPolicy,
    FeatureDefinition,
    InputState,
    MissingDataPolicy,
    PitInput,
    assess_decision_input,
    create_feature_observation,
)
from automated_trading_bot.research.reconciliation_performance import (
    CostDrag,
    MetricAvailability,
    PerformanceTruth,
    ReconciliationResult,
    ReconciliationState,
)
from automated_trading_bot.research.reproducibility import (
    PrngIdentity,
    PrngUsage,
    ReproducibilityRecord,
    RuntimeIdentity,
    SourceIdentity,
    WorkingTreeState,
)
from automated_trading_bot.research.research_governance import (
    ApprovalEvidence,
    CONTRACT_ID,
    EconomicEdgeState,
    EligibilityState,
    EventKind,
    FrozenResearchPlan,
    HistoryTrustState,
    HoldoutDecision,
    JournalError,
    PartitionRole,
    PitEligibilityEvidence,
    RESEARCH_ONLY_AUTHORITY,
    ResearchGovernanceError,
    ResearchGovernor,
    ResearchJournal,
    TrialState,
    TrustedEstablishment,
    reconciliation_evidence_identity,
)

SHA = "sha256:" + hashlib.sha256(b"x").hexdigest()
TASK = "ATIS-S4-08-RESEARCH-GOVERNANCE-001"
POLICY = QualificationPolicy("policy:s4-02", "1.0.0", "evidence:policy")


def research_fixture():
    reproducibility = ReproducibilityRecord.create(
        source=SourceIdentity("1" * 40, "2" * 40, WorkingTreeState.CLEAN),
        runtime=RuntimeIdentity("3.12.10", "python.exe", "Windows", "AMD64"),
        dependency_lock_digest=hashlib.sha256(b"lock").hexdigest(),
        configuration={"window": 20},
        prng=PrngIdentity(PrngUsage.NONE),
        dataset_identities=("dataset:daily-us:v1",),
    )
    project = ResearchProject.create("controlled strategy research")
    hypothesis = Hypothesis.create(project.project_id, "signal has explanatory value")
    experiment = Experiment.create(
        hypothesis.hypothesis_id,
        reproducibility.configuration_digest,
        "1.0.0",
        "falsify signal",
    )
    run = ResearchRun.create(experiment.experiment_id, reproducibility.digest)
    return project, hypothesis, experiment, run, reproducibility


def plan(*, maximum=2, partition=PartitionRole.PROTECTED_HOLDOUT):
    _, hypothesis, experiment, _, reproducibility = research_fixture()
    return FrozenResearchPlan.create(
        hypothesis=hypothesis,
        experiment=experiment,
        configuration_digest=reproducibility.configuration_digest,
        parameter_domain=("window=20", "threshold=0.5"),
        objective="falsify signal before confirmation",
        dataset_id="dataset:daily-us",
        dataset_version="dataset-version:v1",
        partition=partition,
        maximum_admitted_trials=maximum,
        owner_approval_reference="owner:issue-100:comment-1",
        policy_identity="policy:s4-08:v1",
    )


def approval(value, *, operation="S4_08_PLAN_APPROVAL"):
    return ApprovalEvidence.create(
        plan=value,
        actor="owner:KiloAlpha21",
        authoritative_source="github:issue-100",
        task_id=TASK,
        operation=operation,
        approval_reference=value.owner_approval_reference,
    )


def holdout_approval(value):
    return approval(value, operation="S4_08_HOLDOUT_ACCESS")


def trust_for(*approvals, checkpoints=()):
    return TrustedEstablishment.non_production_test_fixture(
        identity="test-trust:s4-08",
        approvals=tuple(approvals),
        checkpoints=tuple(checkpoints),
    )


def governed(root: Path, value=None, *, approvals=()):
    value = value or plan()
    evidence = approval(value)
    all_approvals = approvals or (evidence,)
    trust = trust_for(*all_approvals)
    journal = ResearchJournal(root)
    governor = ResearchGovernor(journal, trust)
    governor.accept_plan(value, evidence)
    return governor, value, evidence, trust


def run_for(value, salt=b"run", replay_of=None):
    return ResearchRun.create(
        value.experiment_id, "sha256:" + hashlib.sha256(salt).hexdigest(), replay_of
    )


def completed(root: Path, value=None):
    governor, value, evidence, trust = governed(root, value)
    run = research_fixture()[3]
    governor.admit(value, run)
    governor.transition(value, run.run_id, TrialState.RUNNING)
    governor.transition(value, run.run_id, TrialState.COMPLETED)
    return governor, value, run, evidence, trust


def qualification_chain(level=QualificationLevel.G3):
    assessments = tuple(
        DimensionAssessment(d, AssessmentState.PASS, (f"evidence:{d.lower()}",))
        for d in DIMENSIONS
    )
    common = dict(
        dataset_id="dataset:daily-us",
        dataset_version="dataset-version:v1",
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        assessments=assessments,
        coverage=EligibleCoverage(date(2020, 1, 1), date(2026, 1, 1)),
    )
    g1 = qualify_dataset(**common, requested_level=QualificationLevel.G1)
    if level is QualificationLevel.G1:
        return (), g1
    g2 = qualify_dataset(
        **common, requested_level=QualificationLevel.G2, predecessor=g1
    )
    if level is QualificationLevel.G2:
        return (g1,), g2
    g3 = qualify_dataset(
        **common, requested_level=QualificationLevel.G3, predecessor=g2
    )
    return (g1, g2), g3


def pit_evidence(q, value, run):
    def stamp(day, hour=12):
        return Timestamp(datetime(2020, 1, day, hour, tzinfo=timezone.utc))

    definition = FeatureDefinition.create(
        version="v1",
        algorithm_id="algorithm:atis/mean",
        configuration_digest=value.configuration_digest,
        required_inputs=("close",),
        lookback=3,
        minimum_history=2,
        missing_data_policy=MissingDataPolicy.REJECT,
        availability_policy=AvailabilityPolicy.AT_KNOWLEDGE,
        output_semantics="decimal:return",
    )
    inputs = tuple(
        PitInput(
            f"input:atis/{i}",
            "close",
            q.dataset_id,
            "source:atis/vendor",
            SHA,
            stamp(i),
            stamp(i, 13),
            InputState.VALID,
        )
        for i in (1, 2)
    )
    observation = create_feature_observation(
        definition=definition,
        instrument_id=InstrumentId(UUID("12345678-1234-4234-8234-123456789abc")),
        listing_id=ListingId(UUID("22345678-1234-4234-8234-123456789abc")),
        qualification=q,
        inputs=inputs,
        available_at=stamp(2, 14),
        value_digest=SHA,
    )
    eligibility = assess_decision_input(
        definition=definition,
        observation=observation,
        qualification=q,
        inputs=inputs,
        decision_time=stamp(3),
        requested_claim=ClaimCeiling.ECONOMIC_VALIDATION,
    )
    return PitEligibilityEvidence(
        run.run_id,
        definition,
        observation,
        inputs,
        stamp(3),
        ClaimCeiling.ECONOMIC_VALIDATION,
        eligibility,
    )


def reconciliation():
    zero = Decimal("0")
    performance = PerformanceTruth(
        zero,
        CostDrag(zero, zero, zero, zero, zero),
        zero,
        zero,
        zero,
        zero,
        zero,
        MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )
    return ReconciliationResult(ReconciliationState.RECONCILED, (), performance)


def manifest_for(value, run, reconciled, pit):
    project, hypothesis, experiment, _, reproducibility = research_fixture()
    assert hypothesis == value.hypothesis and experiment == value.experiment
    identity = reconciliation_evidence_identity(run.run_id, reconciled)
    return ResearchEvidenceManifest.create(
        project_id=project.project_id,
        hypothesis_id=value.hypothesis_id,
        experiment_id=value.experiment_id,
        run_id=run.run_id,
        reproducibility=reproducibility,
        input_identities=(
            "dataset:daily-us:v1",
            pit.observation.observation_id,
            *(item.input_id for item in pit.inputs),
        ),
        output_identities=(identity,),
        replay_of=run.replay_of,
        disposition=EvidenceDisposition.VALID,
    )


def checkpointed(governor, evidence):
    checkpoint = governor._journal.checkpoint_candidate(
        establishment_identity="test-trust:s4-08"
    )
    trust = trust_for(evidence, checkpoints=(checkpoint,))
    journal = ResearchJournal(
        governor._journal.root, checkpoint=checkpoint, trust=trust
    )
    return ResearchGovernor(journal, trust), checkpoint, trust


def test_contract_is_canonical_and_declares_trust_boundaries():
    path = Path("docs/stage4/s4-08-research-governance-search-holdout-contract.json")
    raw = path.read_bytes()
    value = json.loads(raw)
    assert canonical_json(value) == raw
    assert value["contract_id"] == CONTRACT_ID
    assert (
        value["trust_boundaries"]["production_missing_trust"]
        == "NOT_ESTABLISHED_FAIL_CLOSED"
    )
    assert "TRADING_AUTHORITY" in value["prohibited_capabilities"]


def test_frozen_plan_reconstructs_identity_and_is_immutable():
    assert plan() == plan()
    with pytest.raises(FrozenInstanceError):
        plan().objective = "changed"  # type: ignore[misc]
    with pytest.raises(ResearchGovernanceError, match="positive integer"):
        plan(maximum=0)
    bad = replace(plan().hypothesis, proposition="forged")
    with pytest.raises(ResearchGovernanceError, match="hypothesis identity"):
        replace(plan(), hypothesis=bad)


def test_production_missing_trust_and_caller_forgery_fail_closed(tmp_path):
    value = plan()
    evidence = approval(value)
    journal = ResearchJournal(tmp_path / "j")
    with pytest.raises(ResearchGovernanceError, match="NOT_ESTABLISHED"):
        ResearchGovernor(journal).accept_plan(value, evidence)
    forged = replace(trust_for(evidence), _capability=object())
    with pytest.raises(ResearchGovernanceError, match="NOT_ESTABLISHED"):
        ResearchGovernor(journal, forged).accept_plan(value, evidence)


def test_wrong_operation_and_replayed_approval_are_rejected(tmp_path):
    value = plan()
    wrong = approval(value, operation="S4_08_HOLDOUT_ACCESS")
    governor = ResearchGovernor(ResearchJournal(tmp_path / "j"), trust_for(wrong))
    with pytest.raises(ResearchGovernanceError, match="operation"):
        governor.accept_plan(value, wrong)
    other = plan(maximum=3)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "j2"), trust_for(wrong))
    with pytest.raises(ResearchGovernanceError, match="binding"):
        governor.accept_plan(other, wrong)


def test_admission_is_durable_and_direct_append_is_prohibited(tmp_path):
    governor, value, _, _ = governed(tmp_path / "j")
    run = run_for(value)
    event = governor.admit(value, run)
    assert event.kind is EventKind.TRIAL_ADMITTED
    with pytest.raises(JournalError, match="direct journal append"):
        governor._journal.append(event_id="forged")


def test_exact_duplicate_admission_is_idempotent(tmp_path):
    governor, value, _, _ = governed(tmp_path / "j")
    run = run_for(value)
    assert governor.admit(value, run) == governor.admit(value, run)


@pytest.mark.parametrize(
    "target", [TrialState.COMPLETED, TrialState.FAILED, TrialState.INTERRUPTED]
)
def test_invalid_terminal_transition_is_rejected(tmp_path, target):
    governor, value, _, _ = governed(tmp_path / "j")
    run = run_for(value)
    governor.admit(value, run)
    with pytest.raises(ResearchGovernanceError, match="transition"):
        governor.transition(value, run.run_id, target, reasons=("invalid",))


def test_semantically_invalid_hash_linked_completion_is_rejected(tmp_path):
    governor, value, _, _ = governed(tmp_path / "j")
    run = run_for(value)
    with pytest.raises(JournalError, match="transition"):
        governor._journal._append(
            event_id=f"trial:{run.run_id.value}:completed",
            kind=EventKind.TRIAL_COMPLETED,
            experiment_id=value.experiment_id,
            run_id=run.run_id,
            plan=value,
            disposition=TrialState.COMPLETED.value,
        )


def test_transition_cannot_use_another_plan(tmp_path):
    governor, value, _, _ = governed(tmp_path / "j")
    run = run_for(value)
    governor.admit(value, run)
    expanded = FrozenResearchPlan.expand(
        value,
        maximum_admitted_trials=3,
        owner_approval_reference="owner:issue-100:comment-2",
        policy_identity="policy:s4-08:v2",
    )
    ev = approval(expanded, operation="S4_08_BUDGET_EXPANSION")
    trust = trust_for(approval(value), ev)
    other = ResearchGovernor(governor._journal, trust)
    other.accept_plan(expanded, ev)
    with pytest.raises(ResearchGovernanceError, match="plan substitution"):
        other.transition(expanded, run.run_id, TrialState.RUNNING)


def test_budget_and_replay_are_lineage_wide(tmp_path):
    value = plan(maximum=1)
    governor, value, base, _ = governed(tmp_path / "j", value)
    first = run_for(value, b"one")
    governor.admit(value, first)
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        governor.admit(value, run_for(value, b"two"))
    expanded = FrozenResearchPlan.expand(
        value,
        maximum_admitted_trials=2,
        owner_approval_reference="owner:issue-100:comment-2",
        policy_identity="policy:s4-08:v2",
    )
    successor = approval(expanded, operation="S4_08_BUDGET_EXPANSION")
    gov = ResearchGovernor(governor._journal, trust_for(base, successor))
    gov.accept_plan(expanded, successor)
    replay = run_for(expanded, b"replay", first.run_id)
    gov.admit(expanded, replay)
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        gov.admit(expanded, run_for(expanded, b"three"))
    unrelated = ResearchRun.create(
        expanded.experiment_id,
        SHA,
        ResearchRun.create(
            expanded.experiment_id, SHA + b"0" if False else "sha256:" + "9" * 64
        ).run_id,
    )
    with pytest.raises(ResearchGovernanceError, match="replay target"):
        gov.admit(expanded, unrelated)


def test_successor_cannot_change_frozen_scope(tmp_path):
    base = plan()
    gov, base, base_ev, _ = governed(tmp_path / "j", base)
    with pytest.raises(ResearchGovernanceError, match="digest"):
        replace(
            FrozenResearchPlan.expand(
                base,
                maximum_admitted_trials=3,
                owner_approval_reference="owner:new",
                policy_identity="policy:new",
            ),
            objective="changed",
        )
    assert base_ev


def test_holdout_reuse_and_contamination_survive_successor(tmp_path):
    gov, base, _, base_ev, _ = completed(tmp_path / "j")
    predecessors, q = qualification_chain()
    app = holdout_approval(base)
    gov = ResearchGovernor(gov._journal, trust_for(base_ev, app))
    first = gov.request_holdout(
        plan=base,
        qualification=q,
        qualification_predecessors=predecessors,
        request_id="request:first",
        approval=app,
        purpose="confirmation",
    )
    assert first.disclose
    gov.record_contamination(
        plan=base, contamination_id="retune:one", reasons=("retuned",)
    )
    successor = FrozenResearchPlan.expand(
        base,
        maximum_admitted_trials=3,
        owner_approval_reference="owner:new",
        policy_identity="policy:new",
    )
    successor_ev = approval(successor, operation="S4_08_BUDGET_EXPANSION")
    successor_holdout = holdout_approval(successor)
    next_gov = ResearchGovernor(
        gov._journal, trust_for(base_ev, app, successor_ev, successor_holdout)
    )
    next_gov.accept_plan(successor, successor_ev)
    repeated = next_gov.request_holdout(
        plan=successor,
        qualification=q,
        qualification_predecessors=predecessors,
        request_id="request:second",
        approval=successor_holdout,
        purpose="confirmation",
    )
    assert (
        repeated.decision is HoldoutDecision.REPEATED_ACCESS and not repeated.disclose
    )
    assert any(
        e.kind is EventKind.CONTAMINATION_RECORDED
        for e in next_gov._plan_events(successor)
    )


def test_nonprotected_holdout_is_recorded_without_disclosure(tmp_path):
    value = plan(partition=PartitionRole.VALIDATION)
    gov, value, _, base, _ = completed(tmp_path / "j", value)
    predecessors, q = qualification_chain()
    access = holdout_approval(value)
    gov = ResearchGovernor(gov._journal, trust_for(base, access))
    result = gov.request_holdout(
        plan=value,
        qualification=q,
        qualification_predecessors=predecessors,
        request_id="request:denied",
        approval=access,
        purpose="confirmation",
    )
    assert result.decision is HoldoutDecision.UNAUTHORIZED_ACCESS
    assert not result.disclose


def test_checkpoint_distinguishes_missing_tail_and_complete_history(tmp_path):
    gov, value, run, evidence, _ = completed(tmp_path / "j")
    assert gov._journal.history_trust_state() is HistoryTrustState.UNCHECKPOINTED_TAIL
    with pytest.raises(JournalError, match="checkpointed"):
        gov._journal.require_checkpointed()
    reopened, checkpoint, _ = checkpointed(gov, evidence)
    assert (
        reopened._journal.history_trust_state()
        is HistoryTrustState.CHECKPOINTED_HISTORY
    )
    reopened.record_contamination(
        plan=value, contamination_id="later", reasons=("later",)
    )
    assert (
        reopened._journal.history_trust_state() is HistoryTrustState.UNCHECKPOINTED_TAIL
    )
    assert checkpoint.accepted_sequence > 0 and run


def test_uncheckpointed_tail_cannot_support_evidence_authority(tmp_path):
    gov, value, run, _, _ = completed(tmp_path / "j")
    predecessors, q = qualification_chain()
    rec = reconciliation()
    pit = pit_evidence(q, value, run)
    with pytest.raises(JournalError, match="checkpointed"):
        gov.assess_evidence(
            plan=value,
            manifest=manifest_for(value, run, rec, pit),
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=pit,
        )


def test_checkpointed_technical_evidence_without_holdout_is_not_confirmatory(tmp_path):
    gov, value, run, evidence, _ = completed(tmp_path / "j")
    gov, _, _ = checkpointed(gov, evidence)
    predecessors, q = qualification_chain()
    rec = reconciliation()
    pit = pit_evidence(q, value, run)
    result = gov.assess_evidence(
        plan=value,
        manifest=manifest_for(value, run, rec, pit),
        qualification=q,
        qualification_predecessors=predecessors,
        reconciliation=rec,
        pit_evidence=pit,
    )
    assert result.state is EligibilityState.TECHNICALLY_VALID_EVIDENCE
    assert result.economic_edge is EconomicEdgeState.NOT_ESTABLISHED


def test_truncation_and_whole_replacement_fail_checkpoint(tmp_path):
    gov, _, _, evidence, _ = completed(tmp_path / "j")
    _, checkpoint, trust = checkpointed(gov, evidence)
    paths = sorted(gov._journal.root.glob("*.json"))
    paths[-1].unlink()
    journal = ResearchJournal(gov._journal.root, checkpoint=checkpoint, trust=trust)
    assert journal.history_trust_state() is HistoryTrustState.INVALID_HISTORY
    with pytest.raises(JournalError):
        journal.require_checkpointed()


def test_forged_checkpoint_and_wrong_epoch_fail(tmp_path):
    gov, _, _, evidence, _ = completed(tmp_path / "j")
    checkpoint = gov._journal.checkpoint_candidate(
        establishment_identity="test-trust:s4-08"
    )
    forged = replace(checkpoint, epoch="epoch:wrong")
    trust = trust_for(evidence, checkpoints=(checkpoint,))
    journal = ResearchJournal(gov._journal.root, checkpoint=forged, trust=trust)
    assert journal.history_trust_state() is HistoryTrustState.INVALID_HISTORY


def test_whole_self_consistent_journal_replacement_is_rejected(tmp_path):
    gov, _, _, evidence, _ = completed(tmp_path / "accepted")
    _, checkpoint, trust = checkpointed(gov, evidence)
    replacement, _, _, _, _ = completed(tmp_path / "replacement", plan(maximum=3))
    for path in gov._journal.root.glob("*.json"):
        path.unlink()
    for path in replacement._journal.root.glob("*.json"):
        (gov._journal.root / path.name).write_bytes(path.read_bytes())
    journal = ResearchJournal(gov._journal.root, checkpoint=checkpoint, trust=trust)
    assert journal.history_trust_state() is HistoryTrustState.INVALID_HISTORY


@pytest.mark.parametrize("mutation", ["missing", "altered", "reordered", "incomplete"])
def test_corrupt_journal_variants_fail_closed(tmp_path, mutation):
    gov, _, _, _, _ = completed(tmp_path / "j")
    root = gov._journal.root
    paths = sorted(root.glob("*.json"))
    if mutation == "missing":
        paths[1].unlink()
    elif mutation == "altered":
        paths[0].write_bytes(paths[0].read_bytes().replace(b"ACCEPTED", b"REJECTED"))
    elif mutation == "reordered":
        paths[0].rename(root / "00000009.json")
    else:
        (root / ".dead.tmp").write_bytes(b"partial")
    with pytest.raises(JournalError):
        ResearchJournal(root)


def test_manifest_and_qualification_are_reconstructed(tmp_path):
    gov, value, run, evidence, _ = completed(tmp_path / "j")
    gov, _, _ = checkpointed(gov, evidence)
    predecessors, q = qualification_chain()
    rec = reconciliation()
    pit = pit_evidence(q, value, run)
    manifest = manifest_for(value, run, rec, pit)
    with pytest.raises(ResearchGovernanceError, match="manifest identity"):
        gov.assess_evidence(
            plan=value,
            manifest=replace(manifest, manifest_digest=SHA),
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=pit,
        )
    forged_q = replace(q, qualification_id="atis:forged")
    with pytest.raises(ResearchGovernanceError, match="qualification identity"):
        gov.assess_evidence(
            plan=value,
            manifest=manifest,
            qualification=forged_q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=pit,
        )


def test_pit_and_reconciliation_fail_closed(tmp_path):
    gov, value, run, evidence, _ = completed(tmp_path / "j")
    gov, _, _ = checkpointed(gov, evidence)
    predecessors, q = qualification_chain()
    rec = reconciliation()
    pit = pit_evidence(q, value, run)
    manifest = manifest_for(value, run, rec, pit)
    with pytest.raises(ResearchGovernanceError, match="PIT"):
        gov.assess_evidence(
            plan=value,
            manifest=manifest,
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=replace(
                pit, eligibility=replace(pit.eligibility, reasons=("forged",))
            ),
        )
    unrelated_run = run_for(value, b"unrelated")
    with pytest.raises(ResearchGovernanceError, match="run substitution"):
        gov.assess_evidence(
            plan=value,
            manifest=manifest,
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=replace(pit, research_run_id=unrelated_run.run_id),
        )
    unbound = ResearchEvidenceManifest.create(
        project_id=manifest.project_id,
        hypothesis_id=manifest.hypothesis_id,
        experiment_id=manifest.experiment_id,
        run_id=manifest.run_id,
        reproducibility=manifest.reproducibility,
        input_identities=("dataset:daily-us:v1",),
        output_identities=manifest.output_identities,
        replay_of=manifest.replay_of,
        disposition=manifest.disposition,
    )
    with pytest.raises(ResearchGovernanceError, match="bound to manifest"):
        gov.assess_evidence(
            plan=value,
            manifest=unbound,
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=rec,
            pit_evidence=pit,
        )
    malformed = ReconciliationResult(ReconciliationState.RECONCILED, (), None)
    with pytest.raises(ResearchGovernanceError, match="performance truth"):
        gov.assess_evidence(
            plan=value,
            manifest=manifest,
            qualification=q,
            qualification_predecessors=predecessors,
            reconciliation=malformed,
            pit_evidence=pit,
        )


def test_clean_checkpointed_evidence_is_confirmatory_but_never_trading_authority(
    tmp_path,
):
    gov, value, run, evidence, _ = completed(tmp_path / "j")
    predecessors, q = qualification_chain()
    holdout = holdout_approval(value)
    gov = ResearchGovernor(gov._journal, trust_for(evidence, holdout))
    gov.request_holdout(
        plan=value,
        qualification=q,
        qualification_predecessors=predecessors,
        request_id="request:one",
        approval=holdout,
        purpose="confirmation",
    )
    gov, _, _ = checkpointed(gov, evidence)
    rec = reconciliation()
    pit = pit_evidence(q, value, run)
    manifest = manifest_for(value, run, rec, pit)
    result = gov.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=q,
        qualification_predecessors=predecessors,
        reconciliation=rec,
        pit_evidence=pit,
    )
    assert result.state is EligibilityState.ELIGIBLE_CONFIRMATORY_EVIDENCE
    assert result.economic_edge is EconomicEdgeState.NOT_ESTABLISHED
    assert result.authority == RESEARCH_ONLY_AUTHORITY and not hasattr(
        result, "trading_authority"
    )


@pytest.mark.parametrize("terminal", [TrialState.FAILED, TrialState.INTERRUPTED])
def test_failed_trials_consume_budget_after_restart(tmp_path, terminal):
    value = plan(maximum=1)
    gov, value, evidence, trust = governed(tmp_path / "j", value)
    run = run_for(value)
    gov.admit(value, run)
    gov.transition(value, run.run_id, TrialState.RUNNING)
    gov.transition(value, run.run_id, terminal, reasons=("bounded failure",))
    restarted = ResearchGovernor(ResearchJournal(gov._journal.root), trust)
    assert restarted.trial_state(run.run_id) is terminal
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        restarted.admit(value, run_for(value, b"replacement"))
    assert evidence


def test_corrupt_interrupted_and_unsafe_journal_fail_closed(tmp_path, monkeypatch):
    gov, _, _, _, _ = completed(tmp_path / "j")
    root = gov._journal.root
    (root / ".dead.tmp").write_bytes(b"partial")
    with pytest.raises(JournalError, match="incomplete"):
        ResearchJournal(root)
    (root / ".dead.tmp").unlink()
    original = governance._is_reparse
    monkeypatch.setattr(governance, "_is_reparse", lambda p: p == root or original(p))
    with pytest.raises(JournalError, match="link or reparse"):
        ResearchJournal(root)


def test_journal_records_are_canonical_and_content_addressed(tmp_path):
    gov, _, _, _, _ = completed(tmp_path / "j")
    for path in gov._journal.root.glob("*.json"):
        raw = path.read_bytes()
        assert canonical_json(json.loads(raw)) == raw
        assert b"content_digest" in raw

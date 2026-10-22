from dataclasses import FrozenInstanceError, replace
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from automated_trading_bot.research import research_governance as governance
from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
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
from automated_trading_bot.research.reconciliation_performance import (
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
    CONTRACT_ID,
    EconomicEdgeState,
    EligibilityState,
    EventKind,
    FrozenResearchPlan,
    HoldoutDecision,
    JournalError,
    PartitionRole,
    RESEARCH_ONLY_AUTHORITY,
    ResearchGovernanceError,
    ResearchGovernor,
    ResearchJournal,
    TrialState,
)


SHA = "sha256:" + hashlib.sha256(b"x").hexdigest()
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


def plan(*, maximum: int = 2, partition: PartitionRole = PartitionRole.PROTECTED_HOLDOUT):
    _, hypothesis, experiment, _, reproducibility = research_fixture()
    return FrozenResearchPlan.create(
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
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


def run_for(value: FrozenResearchPlan, salt: bytes = b"run", replay_of=None):
    return ResearchRun.create(
        value.experiment_id,
        "sha256:" + hashlib.sha256(salt).hexdigest(),
        replay_of,
    )


def assessments():
    return tuple(
        DimensionAssessment(item, AssessmentState.PASS, (f"evidence:{item.lower()}",))
        for item in DIMENSIONS
    )


def qualification(level: QualificationLevel = QualificationLevel.G3):
    g1 = qualify_dataset(
        dataset_id="dataset:daily-us",
        dataset_version="dataset-version:v1",
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        requested_level=QualificationLevel.G1,
        assessments=assessments(),
        coverage=EligibleCoverage(date(2020, 1, 1), date(2026, 1, 1)),
    )
    if level is QualificationLevel.G1:
        return g1
    g2 = qualify_dataset(
        dataset_id=g1.dataset_id,
        dataset_version=g1.dataset_version,
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        requested_level=QualificationLevel.G2,
        assessments=assessments(),
        coverage=g1.coverage,
        predecessor=g1,
    )
    if level is QualificationLevel.G2:
        return g2
    return qualify_dataset(
        dataset_id=g2.dataset_id,
        dataset_version=g2.dataset_version,
        manifest_digest=SHA,
        provenance_digest=SHA,
        policy=POLICY,
        requested_level=QualificationLevel.G3,
        assessments=assessments(),
        coverage=g2.coverage,
        predecessor=g2,
    )


def manifest_for(value: FrozenResearchPlan, run, reproducibility):
    project, hypothesis, experiment, _, _ = research_fixture()
    assert hypothesis.hypothesis_id == value.hypothesis_id
    assert experiment.experiment_id == value.experiment_id
    return ResearchEvidenceManifest.create(
        project_id=project.project_id,
        hypothesis_id=value.hypothesis_id,
        experiment_id=value.experiment_id,
        run_id=run.run_id,
        reproducibility=reproducibility,
        input_identities=("dataset:daily-us:v1",),
        output_identities=("result:v1",),
        replay_of=run.replay_of,
        disposition=EvidenceDisposition.VALID,
    )


def completed(tmp_path: Path, value: FrozenResearchPlan | None = None):
    selected = value or plan()
    run = run_for(selected)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(selected, run)
    governor.transition(selected, run.run_id, TrialState.RUNNING)
    governor.transition(selected, run.run_id, TrialState.COMPLETED)
    return governor, selected, run


def test_contract_is_canonical_and_bounded() -> None:
    path = Path("docs/stage4/s4-08-research-governance-search-holdout-contract.json")
    raw = path.read_bytes()
    value = json.loads(raw)
    assert canonical_json(value) == raw
    assert value["contract_id"] == CONTRACT_ID
    assert "TRADING_AUTHORITY" in value["prohibited_capabilities"]


def test_frozen_plan_is_deterministic_immutable_and_requires_positive_budget() -> None:
    assert plan() == plan()
    with pytest.raises(FrozenInstanceError):
        plan().objective = "changed"  # type: ignore[misc]
    with pytest.raises(ResearchGovernanceError, match="positive integer"):
        plan(maximum=0)
    with pytest.raises(ResearchGovernanceError, match="approval"):
        replace(plan(), owner_approval_reference="")


def test_valid_admission_is_durable_before_execution(tmp_path: Path) -> None:
    value = plan()
    run = run_for(value)
    journal = ResearchJournal(tmp_path / "journal")
    event = ResearchGovernor(journal).admit(value, run)
    assert event.kind is EventKind.TRIAL_ADMITTED
    assert ResearchJournal(journal.root).recover() == (event,)


def test_plan_or_experiment_substitution_is_rejected(tmp_path: Path) -> None:
    value = plan()
    other = FrozenResearchPlan.create(
        hypothesis_id=value.hypothesis_id,
        experiment_id=value.experiment_id,
        configuration_digest="sha256:" + "9" * 64,
        parameter_domain=value.parameter_domain,
        objective=value.objective,
        dataset_id=value.dataset_id,
        dataset_version=value.dataset_version,
        partition=value.partition,
        maximum_admitted_trials=value.maximum_admitted_trials,
        owner_approval_reference=value.owner_approval_reference,
        policy_identity=value.policy_identity,
    )
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    run = run_for(value)
    governor.admit(value, run)
    with pytest.raises(ResearchGovernanceError, match="another plan"):
        governor.admit(other, run)


def test_budget_exhaustion_fails_before_second_execution(tmp_path: Path) -> None:
    value = plan(maximum=1)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(value, run_for(value, b"one"))
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        governor.admit(value, run_for(value, b"two"))


def test_budget_cannot_be_reset_by_substituting_a_new_plan(tmp_path: Path) -> None:
    value = plan(maximum=1)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(value, run_for(value, b"one"))
    substituted = FrozenResearchPlan.create(
        hypothesis_id=value.hypothesis_id,
        experiment_id=value.experiment_id,
        configuration_digest=value.configuration_digest,
        parameter_domain=("window=99",),
        objective=value.objective,
        dataset_id=value.dataset_id,
        dataset_version=value.dataset_version,
        partition=value.partition,
        maximum_admitted_trials=2,
        owner_approval_reference="owner:substituted",
        policy_identity="policy:substituted",
    )
    with pytest.raises(ResearchGovernanceError, match="unauthorized budget expansion"):
        governor.admit(substituted, run_for(value, b"two"))


def test_owner_approved_expansion_preserves_and_counts_history(tmp_path: Path) -> None:
    value = plan(maximum=1)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(value, run_for(value, b"one"))
    expanded = FrozenResearchPlan.expand(
        value,
        maximum_admitted_trials=2,
        owner_approval_reference="owner:issue-100:comment-2",
        policy_identity="policy:s4-08:v2",
    )
    governor.admit(expanded, run_for(value, b"two"))
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        governor.admit(expanded, run_for(value, b"three"))
    with pytest.raises(ResearchGovernanceError, match="strictly increase"):
        FrozenResearchPlan.expand(
            value,
            maximum_admitted_trials=1,
            owner_approval_reference="owner:issue-100:comment-3",
            policy_identity="policy:s4-08:v3",
        )


@pytest.mark.parametrize("terminal", [TrialState.FAILED, TrialState.INTERRUPTED])
def test_failed_and_interrupted_trials_consume_budget_after_restart(
    tmp_path: Path, terminal: TrialState
) -> None:
    value = plan(maximum=1)
    root = tmp_path / "journal"
    governor = ResearchGovernor(ResearchJournal(root))
    run = run_for(value)
    governor.admit(value, run)
    governor.transition(value, run.run_id, TrialState.RUNNING)
    governor.transition(value, run.run_id, terminal, reasons=("bounded failure",))
    restarted = ResearchGovernor(ResearchJournal(root))
    assert restarted.trial_state(run.run_id) is terminal
    with pytest.raises(ResearchGovernanceError, match="exhausted"):
        restarted.admit(value, run_for(value, b"replacement"))


def test_exact_duplicate_is_idempotent_and_conflict_fails_closed(tmp_path: Path) -> None:
    value = plan()
    run = run_for(value)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    assert governor.admit(value, run) == governor.admit(value, run)
    with pytest.raises(JournalError, match="conflicting duplicate"):
        governor._journal.append(  # noqa: SLF001
            event_id=f"trial:{run.run_id.value}:admitted",
            kind=EventKind.TRIAL_ADMITTED,
            experiment_id=value.experiment_id,
            run_id=run.run_id,
            plan=value,
            disposition=TrialState.ADMITTED.value,
            details=("different",),
        )


def test_replay_requires_new_identity_and_consumes_allocation(tmp_path: Path) -> None:
    value = plan(maximum=2)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    original = run_for(value, b"original")
    replay = run_for(value, b"replay", original.run_id)
    governor.admit(value, original)
    governor.admit(value, replay)
    assert replay.run_id != original.run_id
    assert sum(e.kind is EventKind.TRIAL_ADMITTED for e in governor._journal.recover()) == 2  # noqa: SLF001


@pytest.mark.parametrize("target", [TrialState.COMPLETED, TrialState.FAILED, TrialState.INTERRUPTED])
def test_invalid_lifecycle_transitions_are_rejected(tmp_path: Path, target: TrialState) -> None:
    value = plan()
    run = run_for(value)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(value, run)
    with pytest.raises(ResearchGovernanceError, match="transition"):
        governor.transition(value, run.run_id, target, reasons=("reason",))


def test_terminal_outcome_is_immutable_and_negative_reason_preserved(tmp_path: Path) -> None:
    value = plan()
    run = run_for(value)
    governor = ResearchGovernor(ResearchJournal(tmp_path / "journal"))
    governor.admit(value, run)
    governor.transition(value, run.run_id, TrialState.RUNNING)
    failed = governor.transition(value, run.run_id, TrialState.FAILED, reasons=("no edge",))
    assert failed.reasons == ("no edge",)
    with pytest.raises(ResearchGovernanceError, match="transition"):
        governor.transition(value, run.run_id, TrialState.RUNNING)


@pytest.mark.parametrize("mutation", ["missing", "altered", "reordered", "incomplete"])
def test_missing_reordered_tampered_or_incomplete_journal_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    governor, value, run = completed(tmp_path)
    root = governor._journal.root  # noqa: SLF001
    paths = sorted(root.glob("*.json"))
    if mutation == "missing":
        paths[1].unlink()
    elif mutation == "altered":
        paths[0].write_bytes(paths[0].read_bytes().replace(b"ADMITTED", b"RUNNING"))
    elif mutation == "reordered":
        paths[0].rename(root / "00000009.json")
    else:
        (root / ".00000004.dead.tmp").write_bytes(b"partial")
    with pytest.raises(JournalError):
        ResearchJournal(root)
    assert value and run


def test_authorized_holdout_exposure_is_recorded_before_disclosure(tmp_path: Path) -> None:
    governor, value, _ = completed(tmp_path)
    result = governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id="request:one",
        owner_approval_reference=value.owner_approval_reference,
        purpose="independent confirmation",
    )
    assert result.decision is HoldoutDecision.AUTHORIZED_ACCESS and result.disclose
    assert result.event.kind is EventKind.HOLDOUT_AUTHORIZED


@pytest.mark.parametrize("bad_approval", ["owner:wrong", "owner:forged"])
def test_missing_or_forged_approval_rejects_without_disclosure(
    tmp_path: Path, bad_approval: str
) -> None:
    governor, value, _ = completed(tmp_path)
    result = governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id=f"request:{bad_approval.split(':')[1]}",
        owner_approval_reference=bad_approval,
        purpose="confirmation",
    )
    assert result.decision is HoldoutDecision.UNAUTHORIZED_ACCESS
    assert not result.disclose


def test_repeated_holdout_exposure_is_recorded_and_denied(tmp_path: Path) -> None:
    governor, value, _ = completed(tmp_path)
    first = governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id="request:first",
        owner_approval_reference=value.owner_approval_reference,
        purpose="confirmation",
    )
    second = governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id="request:second",
        owner_approval_reference=value.owner_approval_reference,
        purpose="repeat",
    )
    assert first.disclose and not second.disclose
    assert second.decision is HoldoutDecision.REPEATED_ACCESS


def test_nonprotected_or_insufficiently_qualified_holdout_is_denied(tmp_path: Path) -> None:
    value = plan(partition=PartitionRole.VALIDATION)
    governor, _, _ = completed(tmp_path, value)
    result = governor.request_holdout(
        plan=value,
        qualification=qualification(QualificationLevel.G2),
        request_id="request:invalid",
        owner_approval_reference=value.owner_approval_reference,
        purpose="confirmation",
    )
    assert not result.disclose


def evidence_inputs(tmp_path: Path):
    governor, value, run = completed(tmp_path)
    _, _, _, _, reproducibility = research_fixture()
    manifest = manifest_for(value, run, reproducibility)
    reconciled = ReconciliationResult(ReconciliationState.RECONCILED, (), None)
    return governor, value, manifest, reconciled


def test_technically_valid_evidence_is_distinct_from_confirmatory(tmp_path: Path) -> None:
    governor, value, manifest, reconciled = evidence_inputs(tmp_path)
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(),
        reconciliation=reconciled,
    )
    assert result.state is EligibilityState.TECHNICALLY_VALID_EVIDENCE
    assert result.economic_edge is EconomicEdgeState.NOT_ESTABLISHED


def test_authorized_clean_holdout_evidence_is_confirmatory_eligible(tmp_path: Path) -> None:
    governor, value, manifest, reconciled = evidence_inputs(tmp_path)
    governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id="request:confirmation",
        owner_approval_reference=value.owner_approval_reference,
        purpose="confirmation",
    )
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(),
        reconciliation=reconciled,
    )
    assert result.state is EligibilityState.ELIGIBLE_CONFIRMATORY_EVIDENCE
    assert result.economic_edge is EconomicEdgeState.NOT_ESTABLISHED


def test_post_holdout_retuning_contaminates_confirmation_lineage(tmp_path: Path) -> None:
    governor, value, manifest, reconciled = evidence_inputs(tmp_path)
    governor.request_holdout(
        plan=value,
        qualification=qualification(),
        request_id="request:confirmation",
        owner_approval_reference=value.owner_approval_reference,
        purpose="confirmation",
    )
    governor.record_contamination(
        plan=value,
        contamination_id="retuning:one",
        reasons=("threshold changed after holdout result",),
    )
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(),
        reconciliation=reconciled,
    )
    assert result.state is EligibilityState.TECHNICALLY_VALID_EVIDENCE
    assert "contaminated" in result.reasons[-1]


@pytest.mark.parametrize(
    "state",
    [ReconciliationState.DISCREPANCY, ReconciliationState.INVALID_OR_UNKNOWN],
)
def test_unreconciled_financial_evidence_is_ineligible(
    tmp_path: Path, state: ReconciliationState
) -> None:
    governor, value, manifest, _ = evidence_inputs(tmp_path)
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(),
        reconciliation=ReconciliationResult(state, (), None),
    )
    assert result.state is EligibilityState.INELIGIBLE_EVIDENCE


def test_g2_can_be_technical_but_never_confirmatory(tmp_path: Path) -> None:
    governor, value, manifest, reconciled = evidence_inputs(tmp_path)
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(QualificationLevel.G2),
        reconciliation=reconciled,
    )
    assert result.state is EligibilityState.TECHNICALLY_VALID_EVIDENCE
    assert "G3" in result.reasons[0]


def test_profitable_financial_truth_cannot_establish_edge(tmp_path: Path) -> None:
    governor, value, manifest, reconciled = evidence_inputs(tmp_path)
    result = governor.assess_evidence(
        plan=value,
        manifest=manifest,
        qualification=qualification(),
        reconciliation=reconciled,
    )
    assert result.economic_edge is EconomicEdgeState.NOT_ESTABLISHED
    assert result.authority == RESEARCH_ONLY_AUTHORITY
    assert not hasattr(result, "trading_authority")


def test_journal_rejects_unexpected_files_and_does_not_reset_history(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    root.mkdir()
    (root / "partial.tmp").write_bytes(b"partial")
    with pytest.raises(JournalError, match="incomplete"):
        ResearchJournal(root)
    assert (root / "partial.tmp").exists()


def test_symlink_or_reparse_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    original = governance._is_reparse
    monkeypatch.setattr(
        governance,
        "_is_reparse",
        lambda path: path == target or original(path),
    )
    with pytest.raises(JournalError, match="link or reparse"):
        ResearchJournal(target)


def test_journal_records_are_canonical_and_content_addressed(tmp_path: Path) -> None:
    governor, _, _ = completed(tmp_path)
    for path in governor._journal.root.glob("*.json"):  # noqa: SLF001
        raw = path.read_bytes()
        assert canonical_json(json.loads(raw)) == raw
        assert b"content_digest" in raw

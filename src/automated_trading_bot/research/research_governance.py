"""S4-08 bounded research governance, search budgets and holdout control."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import stat
from collections.abc import Mapping
from typing import Final
from uuid import uuid4

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
    qualify_dataset,
)
from automated_trading_bot.research.evidence import (
    EvidenceDisposition,
    ResearchEvidenceManifest,
)
from automated_trading_bot.research.identity import (
    Experiment,
    ExperimentId,
    Hypothesis,
    HypothesisId,
    ResearchProjectId,
    ResearchRun,
    ResearchRunId,
)
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationResult,
    ReconciliationState,
    require_performance,
)
from automated_trading_bot.research.pit_features import (
    DecisionInputEligibility,
    EligibilityState as PitEligibilityState,
    FeatureDefinition,
    FeatureObservation,
    PitInput,
    assess_decision_input,
    create_feature_observation,
)


CONTRACT_ID: Final = "ATIS-S4-08-RESEARCH-GOVERNANCE-V1"
RESEARCH_ONLY_AUTHORITY: Final = (
    "RESEARCH_EVIDENCE_ELIGIBILITY_ONLY_NO_EDGE_OR_TRADING_AUTHORITY"
)
_SHA256: Final = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)
_REPARSE_POINT: Final = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ResearchGovernanceError(ValueError):
    """The supplied research-governance evidence fails closed."""


class JournalError(RuntimeError):
    """The authoritative local journal is unsafe, incomplete or corrupt."""


class TrialState(StrEnum):
    ADMITTED = "ADMITTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class PartitionRole(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    PROTECTED_HOLDOUT = "PROTECTED_HOLDOUT"


class HoldoutDecision(StrEnum):
    AUTHORIZED_ACCESS = "AUTHORIZED_ACCESS"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"
    REPEATED_ACCESS = "REPEATED_ACCESS"


class EligibilityState(StrEnum):
    TECHNICALLY_VALID_EVIDENCE = "TECHNICALLY_VALID_EVIDENCE"
    ELIGIBLE_CONFIRMATORY_EVIDENCE = "ELIGIBLE_CONFIRMATORY_EVIDENCE"
    INELIGIBLE_EVIDENCE = "INELIGIBLE_EVIDENCE"


class EconomicEdgeState(StrEnum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class EventKind(StrEnum):
    PLAN_ACCEPTED = "PLAN_ACCEPTED"
    TRIAL_ADMITTED = "TRIAL_ADMITTED"
    TRIAL_RUNNING = "TRIAL_RUNNING"
    TRIAL_COMPLETED = "TRIAL_COMPLETED"
    TRIAL_FAILED = "TRIAL_FAILED"
    TRIAL_INTERRUPTED = "TRIAL_INTERRUPTED"
    HOLDOUT_AUTHORIZED = "HOLDOUT_AUTHORIZED"
    HOLDOUT_REJECTED = "HOLDOUT_REJECTED"
    HOLDOUT_CONSUMED = "HOLDOUT_CONSUMED"
    CONTAMINATION_RECORDED = "CONTAMINATION_RECORDED"


class HistoryTrustState(StrEnum):
    CHECKPOINTED_HISTORY = "CHECKPOINTED_HISTORY"
    UNCHECKPOINTED_TAIL = "UNCHECKPOINTED_TAIL"
    INVALID_HISTORY = "INVALID_HISTORY"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ResearchGovernanceError(f"{name} must be nonempty unpadded text")
    return value


def _safe_id(value: object, name: str) -> str:
    value = _text(value, name)
    if _SAFE_ID.fullmatch(value) is None:
        raise ResearchGovernanceError(f"{name} is not canonical")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if _SHA256.fullmatch(value) is None:
        raise ResearchGovernanceError(f"{name} must be a canonical SHA-256 digest")
    return value


def _unique_text(values: object, name: str) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) != len(set(values)):
        raise ResearchGovernanceError(f"{name} must be a unique tuple")
    return tuple(_text(item, name) for item in values)


def _content_digest(domain: bytes, body: Mapping[str, object]) -> str:
    return EvidenceContentDigest.from_bytes(domain + b"\0" + canonical_json(body)).value


def _validate_manifest(manifest: ResearchEvidenceManifest) -> None:
    rebuilt = ResearchEvidenceManifest.create(
        project_id=manifest.project_id,
        hypothesis_id=manifest.hypothesis_id,
        experiment_id=manifest.experiment_id,
        run_id=manifest.run_id,
        reproducibility=manifest.reproducibility,
        input_identities=manifest.input_identities,
        output_identities=manifest.output_identities,
        replay_of=manifest.replay_of,
        disposition=manifest.disposition,
        reasons=manifest.reasons,
        authority=manifest.authority,
    )
    if rebuilt != manifest:
        raise ResearchGovernanceError("research evidence manifest identity is invalid")


def _validate_qualification_chain(
    qualification: DatasetQualification,
    predecessors: tuple[DatasetQualification, ...],
) -> None:
    current: DatasetQualification | None = None
    for supplied in (*predecessors, qualification):
        rebuilt = qualify_dataset(
            dataset_id=supplied.dataset_id,
            dataset_version=supplied.dataset_version,
            manifest_digest=supplied.manifest_digest,
            provenance_digest=supplied.provenance_digest,
            policy=supplied.policy,
            requested_level=supplied.level,
            assessments=supplied.assessments,
            coverage=supplied.coverage,
            limitations=supplied.limitations,
            predecessor=current,
        )
        if rebuilt != supplied:
            raise ResearchGovernanceError("dataset qualification identity is invalid")
        current = supplied


def reconciliation_evidence_identity(
    run_id: ResearchRunId, result: ReconciliationResult
) -> str:
    performance = require_performance(result)
    numeric = (
        performance.gross_pnl,
        performance.execution_cost_drag.commission_fees,
        performance.execution_cost_drag.bid_ask_spread,
        performance.execution_cost_drag.slippage,
        performance.execution_cost_drag.market_impact,
        performance.execution_cost_drag.additional_friction,
        performance.net_pnl,
        performance.realised_pnl,
        performance.unrealised_pnl,
        performance.portfolio_value,
        performance.turnover,
    )
    if any(type(value) is not Decimal or not value.is_finite() for value in numeric):
        raise ResearchGovernanceError(
            "S4-06 performance contains invalid numeric evidence"
        )
    if (
        performance.net_pnl
        != performance.gross_pnl - performance.execution_cost_drag.total
    ):
        raise ResearchGovernanceError("S4-06 performance arithmetic is inconsistent")
    body = {
        "authority": result.authority,
        "performance": [str(value) for value in numeric],
        "run_id": run_id.value,
        "state": result.state.value,
    }
    return "reconciliation:" + _content_digest(b"ATIS-S4-08-S4-06-BINDING-V1", body)


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    actor: str
    authoritative_source: str
    task_id: str
    operation: str
    plan_digest: str
    predecessor_plan_digest: str | None
    hypothesis_id: str
    experiment_id: str
    configuration_digest: str
    dataset_id: str
    dataset_version: str
    partition: str
    maximum_admitted_trials: int
    policy_identity: str
    approval_reference: str
    evidence_digest: str

    @classmethod
    def create(
        cls,
        *,
        plan: "FrozenResearchPlan",
        actor: str,
        authoritative_source: str,
        task_id: str,
        operation: str,
        approval_reference: str,
    ) -> "ApprovalEvidence":
        actor = _safe_id(actor, "actor")
        authoritative_source = _safe_id(authoritative_source, "authoritative_source")
        task_id = _safe_id(task_id, "task_id")
        operation = _safe_id(operation, "operation")
        approval_reference = _safe_id(approval_reference, "approval_reference")
        values: dict[str, object] = {
            "actor": actor,
            "authoritative_source": authoritative_source,
            "task_id": task_id,
            "operation": operation,
            "plan_digest": plan.plan_digest,
            "predecessor_plan_digest": plan.predecessor_plan_digest,
            "hypothesis_id": plan.hypothesis_id.value,
            "experiment_id": plan.experiment_id.value,
            "configuration_digest": plan.configuration_digest,
            "dataset_id": plan.dataset_id,
            "dataset_version": plan.dataset_version,
            "partition": plan.partition.value,
            "maximum_admitted_trials": plan.maximum_admitted_trials,
            "policy_identity": plan.policy_identity,
            "approval_reference": approval_reference,
        }
        digest = _content_digest(b"ATIS-S4-08-OWNER-APPROVAL-V1", values)
        return cls(
            actor,
            authoritative_source,
            task_id,
            operation,
            plan.plan_digest,
            plan.predecessor_plan_digest,
            plan.hypothesis_id.value,
            plan.experiment_id.value,
            plan.configuration_digest,
            plan.dataset_id,
            plan.dataset_version,
            plan.partition.value,
            plan.maximum_admitted_trials,
            plan.policy_identity,
            approval_reference,
            digest,
        )

    def validate_for(self, plan: "FrozenResearchPlan") -> None:
        rebuilt = ApprovalEvidence.create(
            plan=plan,
            actor=self.actor,
            authoritative_source=self.authoritative_source,
            task_id=self.task_id,
            operation=self.operation,
            approval_reference=self.approval_reference,
        )
        if self != rebuilt:
            raise ResearchGovernanceError("approval evidence binding is invalid")

    def canonical_body(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in (
                "actor",
                "authoritative_source",
                "task_id",
                "operation",
                "plan_digest",
                "predecessor_plan_digest",
                "hypothesis_id",
                "experiment_id",
                "configuration_digest",
                "dataset_id",
                "dataset_version",
                "partition",
                "maximum_admitted_trials",
                "policy_identity",
                "approval_reference",
                "evidence_digest",
            )
        }

    @classmethod
    def from_canonical_body(cls, body: object) -> "ApprovalEvidence":
        if type(body) is not dict or set(body) != set(cls.__dataclass_fields__):
            raise JournalError("approval record schema mismatch")
        try:
            return cls(**body)
        except TypeError as error:
            raise JournalError("approval record is invalid") from error


@dataclass(frozen=True, slots=True)
class JournalCheckpoint:
    journal_identity: str
    epoch: str
    accepted_sequence: int
    terminal_digest: str | None
    establishment_identity: str
    checkpoint_digest: str

    @classmethod
    def create(
        cls,
        *,
        journal_identity: str,
        epoch: str,
        accepted_sequence: int,
        terminal_digest: str | None,
        establishment_identity: str,
    ) -> "JournalCheckpoint":
        journal_identity = _safe_id(journal_identity, "journal_identity")
        epoch = _safe_id(epoch, "epoch")
        establishment_identity = _safe_id(
            establishment_identity, "establishment_identity"
        )
        if type(accepted_sequence) is not int or accepted_sequence < 0:
            raise ResearchGovernanceError(
                "accepted_sequence must be a nonnegative integer"
            )
        if (accepted_sequence == 0) != (terminal_digest is None):
            raise ResearchGovernanceError(
                "checkpoint sequence and terminal digest disagree"
            )
        if terminal_digest is not None:
            terminal_digest = _digest(terminal_digest, "terminal_digest")
        body = {
            "accepted_sequence": accepted_sequence,
            "epoch": epoch,
            "establishment_identity": establishment_identity,
            "journal_identity": journal_identity,
            "terminal_digest": terminal_digest,
        }
        return cls(
            journal_identity,
            epoch,
            accepted_sequence,
            terminal_digest,
            establishment_identity,
            _content_digest(b"ATIS-S4-08-JOURNAL-CHECKPOINT-V1", body),
        )


_TEST_TRUST_CAPABILITY: Final = object()


@dataclass(frozen=True, slots=True)
class TrustedEstablishment:
    """An injected trust boundary; production roots are established outside S4-08."""

    identity: str
    approval_digests: frozenset[str]
    checkpoint_digests: frozenset[str]
    non_production: bool
    _capability: object

    @classmethod
    def non_production_test_fixture(
        cls,
        *,
        identity: str,
        approvals: tuple[ApprovalEvidence, ...] = (),
        checkpoints: tuple[JournalCheckpoint, ...] = (),
    ) -> "TrustedEstablishment":
        if not identity.startswith("test-trust:"):
            raise ResearchGovernanceError(
                "test trust identity must be explicitly non-production"
            )
        return cls(
            _safe_id(identity, "identity"),
            frozenset(item.evidence_digest for item in approvals),
            frozenset(item.checkpoint_digest for item in checkpoints),
            True,
            _TEST_TRUST_CAPABILITY,
        )

    @classmethod
    def production_not_established(cls) -> "TrustedEstablishment":
        return cls(
            "production:not-established", frozenset(), frozenset(), False, object()
        )

    def _valid(self) -> bool:
        return self.non_production and self._capability is _TEST_TRUST_CAPABILITY

    def require_approval(
        self, evidence: ApprovalEvidence, plan: "FrozenResearchPlan"
    ) -> None:
        evidence.validate_for(plan)
        if not self._valid() or evidence.evidence_digest not in self.approval_digests:
            raise ResearchGovernanceError("owner approval is NOT_ESTABLISHED")

    def require_checkpoint(self, checkpoint: JournalCheckpoint) -> None:
        if (
            not self._valid()
            or checkpoint.checkpoint_digest not in self.checkpoint_digests
        ):
            raise JournalError("trusted journal checkpoint is NOT_ESTABLISHED")


@dataclass(frozen=True, slots=True)
class PitEligibilityEvidence:
    research_run_id: ResearchRunId
    definition: FeatureDefinition
    observation: FeatureObservation
    inputs: tuple[PitInput, ...]
    decision_time: Timestamp
    requested_claim: ClaimCeiling
    eligibility: DecisionInputEligibility
    causal_same_instant_established: bool = False

    def validate(
        self,
        plan: "FrozenResearchPlan",
        qualification: DatasetQualification,
        manifest: ResearchEvidenceManifest,
    ) -> None:
        if self.research_run_id != manifest.run_id:
            raise ResearchGovernanceError("S4-03 PIT evidence run substitution")
        if self.definition.configuration_digest != plan.configuration_digest:
            raise ResearchGovernanceError("S4-03 feature configuration substitution")
        rebuilt_observation = create_feature_observation(
            definition=self.definition,
            instrument_id=self.observation.instrument_id,
            listing_id=self.observation.listing_id,
            qualification=qualification,
            inputs=self.inputs,
            available_at=self.observation.available_at,
            value_digest=self.observation.value_digest,
        )
        if rebuilt_observation != self.observation:
            raise ResearchGovernanceError("S4-03 observation identity is invalid")
        rebuilt = assess_decision_input(
            definition=self.definition,
            observation=self.observation,
            qualification=qualification,
            inputs=self.inputs,
            decision_time=self.decision_time,
            requested_claim=self.requested_claim,
            causal_same_instant_established=self.causal_same_instant_established,
        )
        if (
            rebuilt != self.eligibility
            or rebuilt.state is not PitEligibilityState.ELIGIBLE
        ):
            raise ResearchGovernanceError("S4-03 PIT eligibility is not established")
        required_inputs = {
            self.observation.observation_id,
            *(item.input_id for item in self.inputs),
        }
        if not required_inputs.issubset(manifest.input_identities):
            raise ResearchGovernanceError("S4-03 PIT evidence is not bound to manifest")


@dataclass(frozen=True, slots=True)
class FrozenResearchPlan:
    hypothesis: Hypothesis
    experiment: Experiment
    configuration_digest: str
    parameter_domain: tuple[str, ...]
    objective: str
    dataset_id: str
    dataset_version: str
    partition: PartitionRole
    maximum_admitted_trials: int
    owner_approval_reference: str
    policy_identity: str
    predecessor_plan_digest: str | None
    plan_digest: str

    @property
    def hypothesis_id(self) -> HypothesisId:
        return self.hypothesis.hypothesis_id

    @property
    def experiment_id(self) -> ExperimentId:
        return self.experiment.experiment_id

    def __post_init__(self) -> None:
        if (
            type(self.hypothesis) is not Hypothesis
            or type(self.experiment) is not Experiment
        ):
            raise TypeError("protected research objects are required")
        if (
            Hypothesis.create(self.hypothesis.project_id, self.hypothesis.proposition)
            != self.hypothesis
        ):
            raise ResearchGovernanceError("hypothesis identity is not reconstructed")
        if (
            Experiment.create(
                self.experiment.hypothesis_id,
                self.experiment.configuration_digest,
                self.experiment.version,
                self.experiment.purpose,
            )
            != self.experiment
        ):
            raise ResearchGovernanceError("experiment identity is not reconstructed")
        if self.experiment.hypothesis_id != self.hypothesis.hypothesis_id:
            raise ResearchGovernanceError("experiment does not belong to hypothesis")
        if self.experiment.configuration_digest != self.configuration_digest:
            raise ResearchGovernanceError("experiment configuration substitution")
        _digest(self.configuration_digest, "configuration_digest")
        domain = _unique_text(self.parameter_domain, "parameter_domain")
        if not domain:
            raise ResearchGovernanceError("parameter_domain cannot be empty")
        _text(self.objective, "objective")
        _safe_id(self.dataset_id, "dataset_id")
        _safe_id(self.dataset_version, "dataset_version")
        if type(self.partition) is not PartitionRole:
            raise TypeError("partition must be PartitionRole")
        if (
            type(self.maximum_admitted_trials) is not int
            or self.maximum_admitted_trials <= 0
        ):
            raise ResearchGovernanceError(
                "maximum_admitted_trials must be a positive integer"
            )
        _safe_id(self.owner_approval_reference, "owner_approval_reference")
        _safe_id(self.policy_identity, "policy_identity")
        if self.predecessor_plan_digest is not None:
            _digest(self.predecessor_plan_digest, "predecessor_plan_digest")
        body = {
            "configuration_digest": self.configuration_digest,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "experiment_id": self.experiment_id.value,
            "hypothesis_id": self.hypothesis_id.value,
            "hypothesis_project_id": self.hypothesis.project_id.value,
            "hypothesis_proposition": self.hypothesis.proposition,
            "experiment_version": self.experiment.version,
            "experiment_purpose": self.experiment.purpose,
            "maximum_admitted_trials": self.maximum_admitted_trials,
            "objective": self.objective,
            "owner_approval_reference": self.owner_approval_reference,
            "parameter_domain": list(self.parameter_domain),
            "partition": self.partition.value,
            "policy_identity": self.policy_identity,
            "predecessor_plan_digest": self.predecessor_plan_digest,
        }
        expected = EvidenceContentDigest.from_bytes(
            b"ATIS-S4-08-FROZEN-PLAN-V1\0" + canonical_json(body)
        ).value
        if self.plan_digest != expected:
            raise ResearchGovernanceError("plan digest does not match frozen content")

    @classmethod
    def create(
        cls,
        *,
        hypothesis: Hypothesis,
        experiment: Experiment,
        configuration_digest: str,
        parameter_domain: tuple[str, ...],
        objective: str,
        dataset_id: str,
        dataset_version: str,
        partition: PartitionRole,
        maximum_admitted_trials: int,
        owner_approval_reference: str,
        policy_identity: str,
    ) -> FrozenResearchPlan:
        if type(hypothesis) is not Hypothesis or type(experiment) is not Experiment:
            raise TypeError("protected research objects are required")
        hypothesis_id, experiment_id = (
            hypothesis.hypothesis_id,
            experiment.experiment_id,
        )
        if experiment.hypothesis_id != hypothesis_id:
            raise ResearchGovernanceError("experiment does not belong to hypothesis")
        configuration_digest = _digest(configuration_digest, "configuration_digest")
        if experiment.configuration_digest != configuration_digest:
            raise ResearchGovernanceError("experiment configuration substitution")
        parameter_domain = _unique_text(parameter_domain, "parameter_domain")
        if not parameter_domain:
            raise ResearchGovernanceError("parameter_domain cannot be empty")
        objective = _text(objective, "objective")
        dataset_id = _safe_id(dataset_id, "dataset_id")
        dataset_version = _safe_id(dataset_version, "dataset_version")
        if type(partition) is not PartitionRole:
            raise TypeError("partition must be PartitionRole")
        if type(maximum_admitted_trials) is not int or maximum_admitted_trials <= 0:
            raise ResearchGovernanceError(
                "maximum_admitted_trials must be a positive integer"
            )
        owner_approval_reference = _safe_id(
            owner_approval_reference, "owner_approval_reference"
        )
        policy_identity = _safe_id(policy_identity, "policy_identity")
        body = {
            "configuration_digest": configuration_digest,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "experiment_id": experiment_id.value,
            "hypothesis_id": hypothesis_id.value,
            "hypothesis_project_id": hypothesis.project_id.value,
            "hypothesis_proposition": hypothesis.proposition,
            "experiment_version": experiment.version,
            "experiment_purpose": experiment.purpose,
            "maximum_admitted_trials": maximum_admitted_trials,
            "objective": objective,
            "owner_approval_reference": owner_approval_reference,
            "parameter_domain": list(parameter_domain),
            "partition": partition.value,
            "policy_identity": policy_identity,
            "predecessor_plan_digest": None,
        }
        plan_digest = EvidenceContentDigest.from_bytes(
            b"ATIS-S4-08-FROZEN-PLAN-V1\0" + canonical_json(body)
        ).value
        return cls(
            hypothesis,
            experiment,
            configuration_digest,
            parameter_domain,
            objective,
            dataset_id,
            dataset_version,
            partition,
            maximum_admitted_trials,
            owner_approval_reference,
            policy_identity,
            None,
            plan_digest,
        )

    @classmethod
    def expand(
        cls,
        predecessor: FrozenResearchPlan,
        *,
        maximum_admitted_trials: int,
        owner_approval_reference: str,
        policy_identity: str,
    ) -> FrozenResearchPlan:
        if type(predecessor) is not FrozenResearchPlan:
            raise TypeError("predecessor must be FrozenResearchPlan")
        if (
            type(maximum_admitted_trials) is not int
            or maximum_admitted_trials <= predecessor.maximum_admitted_trials
        ):
            raise ResearchGovernanceError("expanded budget must strictly increase")
        owner_approval_reference = _safe_id(
            owner_approval_reference, "owner_approval_reference"
        )
        policy_identity = _safe_id(policy_identity, "policy_identity")
        if (
            owner_approval_reference == predecessor.owner_approval_reference
            or policy_identity == predecessor.policy_identity
        ):
            raise ResearchGovernanceError(
                "budget expansion requires new approval and policy identities"
            )
        body = {
            "configuration_digest": predecessor.configuration_digest,
            "dataset_id": predecessor.dataset_id,
            "dataset_version": predecessor.dataset_version,
            "experiment_id": predecessor.experiment_id.value,
            "hypothesis_id": predecessor.hypothesis_id.value,
            "hypothesis_project_id": predecessor.hypothesis.project_id.value,
            "hypothesis_proposition": predecessor.hypothesis.proposition,
            "experiment_version": predecessor.experiment.version,
            "experiment_purpose": predecessor.experiment.purpose,
            "maximum_admitted_trials": maximum_admitted_trials,
            "objective": predecessor.objective,
            "owner_approval_reference": owner_approval_reference,
            "parameter_domain": list(predecessor.parameter_domain),
            "partition": predecessor.partition.value,
            "policy_identity": policy_identity,
            "predecessor_plan_digest": predecessor.plan_digest,
        }
        plan_digest = EvidenceContentDigest.from_bytes(
            b"ATIS-S4-08-FROZEN-PLAN-V1\0" + canonical_json(body)
        ).value
        return cls(
            predecessor.hypothesis,
            predecessor.experiment,
            predecessor.configuration_digest,
            predecessor.parameter_domain,
            predecessor.objective,
            predecessor.dataset_id,
            predecessor.dataset_version,
            predecessor.partition,
            maximum_admitted_trials,
            owner_approval_reference,
            policy_identity,
            predecessor.plan_digest,
            plan_digest,
        )

    def canonical_body(self) -> dict[str, object]:
        return {
            "configuration_digest": self.configuration_digest,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "experiment_id": self.experiment_id.value,
            "experiment_purpose": self.experiment.purpose,
            "experiment_version": self.experiment.version,
            "hypothesis_id": self.hypothesis_id.value,
            "hypothesis_project_id": self.hypothesis.project_id.value,
            "hypothesis_proposition": self.hypothesis.proposition,
            "maximum_admitted_trials": self.maximum_admitted_trials,
            "objective": self.objective,
            "owner_approval_reference": self.owner_approval_reference,
            "parameter_domain": list(self.parameter_domain),
            "partition": self.partition.value,
            "policy_identity": self.policy_identity,
            "predecessor_plan_digest": self.predecessor_plan_digest,
        }

    @classmethod
    def from_canonical_body(cls, body: object) -> "FrozenResearchPlan":
        if type(body) is not dict:
            raise JournalError("plan record is not an object")
        expected = {
            "configuration_digest",
            "dataset_id",
            "dataset_version",
            "experiment_id",
            "experiment_purpose",
            "experiment_version",
            "hypothesis_id",
            "hypothesis_project_id",
            "hypothesis_proposition",
            "maximum_admitted_trials",
            "objective",
            "owner_approval_reference",
            "parameter_domain",
            "partition",
            "policy_identity",
            "predecessor_plan_digest",
        }
        if set(body) != expected:
            raise JournalError("plan record schema mismatch")
        try:
            hypothesis = Hypothesis(
                HypothesisId(body["hypothesis_id"]),
                ResearchProjectId(body["hypothesis_project_id"]),
                body["hypothesis_proposition"],
            )
            experiment = Experiment(
                ExperimentId(body["experiment_id"]),
                hypothesis.hypothesis_id,
                body["configuration_digest"],
                body["experiment_version"],
                body["experiment_purpose"],
            )
            raw_domain = body["parameter_domain"]
            if type(raw_domain) is not list:
                raise TypeError
            candidate = cls(
                hypothesis,
                experiment,
                body["configuration_digest"],
                tuple(raw_domain),
                body["objective"],
                body["dataset_id"],
                body["dataset_version"],
                PartitionRole(body["partition"]),
                body["maximum_admitted_trials"],
                body["owner_approval_reference"],
                body["policy_identity"],
                body["predecessor_plan_digest"],
                _content_digest(b"ATIS-S4-08-FROZEN-PLAN-V1", body),
            )
        except (KeyError, TypeError, ValueError, ResearchGovernanceError) as error:
            raise JournalError("plan record contains invalid values") from error
        return candidate


@dataclass(frozen=True, slots=True)
class JournalEvent:
    sequence: int
    event_id: str
    kind: EventKind
    experiment_id: str
    run_id: str | None
    plan_digest: str
    policy_identity: str
    approval_reference: str
    disposition: str
    reasons: tuple[str, ...]
    details: tuple[str, ...]
    previous_digest: str | None
    content_digest: str


def _event_body(event: JournalEvent, *, include_digest: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "approval_reference": event.approval_reference,
        "details": list(event.details),
        "disposition": event.disposition,
        "event_id": event.event_id,
        "experiment_id": event.experiment_id,
        "kind": event.kind.value,
        "plan_digest": event.plan_digest,
        "policy_identity": event.policy_identity,
        "previous_digest": event.previous_digest,
        "reasons": list(event.reasons),
        "run_id": event.run_id,
        "sequence": event.sequence,
    }
    if include_digest:
        body["content_digest"] = event.content_digest
    return body


def _is_reparse(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _assert_safe_chain(path: Path) -> None:
    current = path
    while True:
        if (current.exists() or current.is_symlink()) and _is_reparse(current):
            raise JournalError("journal path contains a link or reparse point")
        if current.parent == current:
            return
        current = current.parent


class ResearchJournal:
    """Single-host, write-once canonical event journal."""

    def __init__(
        self,
        root: Path,
        *,
        journal_identity: str = "journal:s4-08:local",
        epoch: str = "epoch:1",
        checkpoint: JournalCheckpoint | None = None,
        trust: TrustedEstablishment | None = None,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be pathlib.Path")
        _assert_safe_chain(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise JournalError("journal root cannot be created") from error
        _assert_safe_chain(root)
        self._root = root.resolve(strict=True)
        self._journal_identity = _safe_id(journal_identity, "journal_identity")
        self._epoch = _safe_id(epoch, "epoch")
        self._checkpoint = checkpoint
        self._trust = trust or TrustedEstablishment.production_not_established()
        if _is_reparse(self._root):
            raise JournalError("journal root is unsafe")
        self.recover()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def journal_identity(self) -> str:
        return self._journal_identity

    @property
    def epoch(self) -> str:
        return self._epoch

    def _paths(self) -> list[Path]:
        paths = list(self._root.glob("*.json"))
        unexpected = [item for item in self._root.iterdir() if item.suffix != ".json"]
        if unexpected:
            raise JournalError("journal contains incomplete or unexpected files")
        return sorted(paths)

    @staticmethod
    def _decode(path: Path) -> JournalEvent:
        def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in values:
                if key in result:
                    raise JournalError("duplicate JSON key")
                result[key] = value
            return result

        try:
            raw = path.read_bytes()
            value = json.loads(raw, object_pairs_hook=pairs)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JournalError("journal record is unreadable") from error
        if type(value) is not dict or canonical_json(value) != raw:
            raise JournalError("journal record is not canonical JSON")
        expected = {
            "approval_reference",
            "content_digest",
            "details",
            "disposition",
            "event_id",
            "experiment_id",
            "kind",
            "plan_digest",
            "policy_identity",
            "previous_digest",
            "reasons",
            "run_id",
            "sequence",
        }
        if set(value) != expected:
            raise JournalError("journal record schema mismatch")
        try:
            event = JournalEvent(
                sequence=value["sequence"],
                event_id=value["event_id"],
                kind=EventKind(value["kind"]),
                experiment_id=value["experiment_id"],
                run_id=value["run_id"],
                plan_digest=value["plan_digest"],
                policy_identity=value["policy_identity"],
                approval_reference=value["approval_reference"],
                disposition=value["disposition"],
                reasons=tuple(value["reasons"]),
                details=tuple(value["details"]),
                previous_digest=value["previous_digest"],
                content_digest=value["content_digest"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise JournalError("journal record contains invalid values") from error
        if type(event.sequence) is not int or event.sequence <= 0:
            raise JournalError("journal sequence is invalid")
        _safe_id(event.event_id, "event_id")
        _text(event.experiment_id, "experiment_id")
        if event.run_id is not None:
            _text(event.run_id, "run_id")
        _digest(event.plan_digest, "plan_digest")
        _safe_id(event.policy_identity, "policy_identity")
        _safe_id(event.approval_reference, "approval_reference")
        _text(event.disposition, "disposition")
        _unique_text(event.reasons, "reasons")
        _unique_text(event.details, "details")
        if event.previous_digest is not None:
            _digest(event.previous_digest, "previous_digest")
        expected_digest = EvidenceContentDigest.from_bytes(
            b"ATIS-S4-08-JOURNAL-EVENT-V1\0"
            + canonical_json(_event_body(event, include_digest=False))
        ).value
        if event.content_digest != expected_digest:
            raise JournalError("journal record digest mismatch")
        return event

    @staticmethod
    def _detail_map(event: JournalEvent) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in event.details:
            key, marker, value = item.partition("=")
            if not marker or not key or key in result:
                raise JournalError("event details are not canonical key-value pairs")
            result[key] = value
        return result

    @classmethod
    def _validate_semantics(cls, events: tuple[JournalEvent, ...]) -> None:
        plans: dict[str, FrozenResearchPlan] = {}
        states: dict[str, TrialState] = {}
        run_plans: dict[str, str] = {}
        admitted_by_lineage: dict[str, int] = {}
        holdout_authorizations: dict[str, JournalEvent] = {}
        consumed_authorizations: set[str] = set()
        for event in events:
            details = cls._detail_map(event)
            if event.kind is EventKind.PLAN_ACCEPTED:
                if (
                    event.run_id is not None
                    or event.disposition != "ACCEPTED"
                    or event.reasons
                ):
                    raise JournalError("plan acceptance event semantics are invalid")
                if set(details) != {"approval", "plan"}:
                    raise JournalError("plan acceptance details are incomplete")
                try:
                    plan = FrozenResearchPlan.from_canonical_body(
                        json.loads(details["plan"])
                    )
                except json.JSONDecodeError as error:
                    raise JournalError("plan event contains malformed JSON") from error
                if (
                    plan.plan_digest != event.plan_digest
                    or plan.experiment_id.value != event.experiment_id
                ):
                    raise JournalError("plan event identity substitution")
                try:
                    approval = ApprovalEvidence.from_canonical_body(
                        json.loads(details["approval"])
                    )
                    approval.validate_for(plan)
                except (json.JSONDecodeError, ResearchGovernanceError) as error:
                    raise JournalError("plan approval evidence is invalid") from error
                if event.plan_digest in plans and plans[event.plan_digest] != plan:
                    raise JournalError("conflicting plan identity")
                if plan.predecessor_plan_digest is not None:
                    predecessor = plans.get(plan.predecessor_plan_digest)
                    if predecessor is None:
                        raise JournalError("plan predecessor is missing")
                    immutable = (
                        "hypothesis",
                        "experiment",
                        "configuration_digest",
                        "parameter_domain",
                        "objective",
                        "dataset_id",
                        "dataset_version",
                        "partition",
                    )
                    if any(
                        getattr(plan, name) != getattr(predecessor, name)
                        for name in immutable
                    ):
                        raise JournalError("successor plan changes frozen scope")
                    if (
                        plan.maximum_admitted_trials
                        <= predecessor.maximum_admitted_trials
                    ):
                        raise JournalError("successor budget does not increase")
                plans[event.plan_digest] = plan
                admitted_by_lineage.setdefault(event.plan_digest, 0)
                continue
            current_plan = plans.get(event.plan_digest)
            if (
                current_plan is None
                or event.experiment_id != current_plan.experiment_id.value
            ):
                raise JournalError("event references an unaccepted plan")
            lineage: set[str] = set()
            cursor: FrozenResearchPlan | None = current_plan
            while cursor is not None:
                if cursor.plan_digest in lineage:
                    raise JournalError("plan lineage cycle")
                lineage.add(cursor.plan_digest)
                cursor = (
                    plans.get(cursor.predecessor_plan_digest)
                    if cursor.predecessor_plan_digest
                    else None
                )
            if event.kind is EventKind.TRIAL_ADMITTED:
                if (
                    event.run_id is None
                    or event.disposition != TrialState.ADMITTED.value
                    or event.reasons
                ):
                    raise JournalError("trial admission semantics are invalid")
                if set(details) != {"maximum", "replay_of", "reproducibility"}:
                    raise JournalError("trial admission details are incomplete")
                _digest(details["reproducibility"], "reproducibility")
                replay = details["replay_of"]
                if replay != "none":
                    if replay == event.run_id or replay not in run_plans:
                        raise JournalError(
                            "replay target is not an attributable admitted run"
                        )
                    if run_plans[replay] not in lineage:
                        raise JournalError("replay target is outside plan lineage")
                if event.run_id in states:
                    raise JournalError("run was admitted more than once")
                count = sum(1 for value in run_plans.values() if value in lineage)
                try:
                    maximum = int(details["maximum"])
                except ValueError as error:
                    raise JournalError("trial admission maximum is invalid") from error
                if (
                    maximum != current_plan.maximum_admitted_trials
                    or count >= current_plan.maximum_admitted_trials
                ):
                    raise JournalError("trial admission exceeds cumulative budget")
                states[event.run_id] = TrialState.ADMITTED
                run_plans[event.run_id] = current_plan.plan_digest
            elif event.kind in {
                EventKind.TRIAL_RUNNING,
                EventKind.TRIAL_COMPLETED,
                EventKind.TRIAL_FAILED,
                EventKind.TRIAL_INTERRUPTED,
            }:
                if event.run_id is None or details:
                    raise JournalError("trial transition details are invalid")
                if run_plans.get(event.run_id) != current_plan.plan_digest:
                    raise JournalError("trial transition uses another plan")
                target = TrialState(event.disposition)
                prior_state = states.get(event.run_id)
                if (
                    prior_state is None
                    or _STATE_KIND[target] is not event.kind
                    or target not in _TRANSITIONS[prior_state]
                ):
                    raise JournalError("invalid recovered trial transition")
                if (target in {TrialState.FAILED, TrialState.INTERRUPTED}) != bool(
                    event.reasons
                ):
                    raise JournalError("trial transition reasons are invalid")
                states[event.run_id] = target
            elif event.kind in {
                EventKind.HOLDOUT_AUTHORIZED,
                EventKind.HOLDOUT_REJECTED,
            }:
                required = {"dataset", "partition", "purpose", "version"}
                if event.run_id is not None or set(details) != required:
                    raise JournalError("holdout event semantics are invalid")
                if (
                    details["dataset"] != current_plan.dataset_id
                    or details["version"] != current_plan.dataset_version
                    or details["partition"] != current_plan.partition.value
                ):
                    raise JournalError("holdout event dataset substitution")
                decision = HoldoutDecision(event.disposition)
                if (event.kind is EventKind.HOLDOUT_AUTHORIZED) != (
                    decision is HoldoutDecision.AUTHORIZED_ACCESS
                ):
                    raise JournalError("holdout disposition mismatch")
                if event.kind is EventKind.HOLDOUT_AUTHORIZED:
                    holdout_authorizations[event.content_digest] = event
            elif event.kind is EventKind.HOLDOUT_CONSUMED:
                required = {
                    "authorization_event_digest",
                    "authorization_event_id",
                    "dataset",
                    "partition",
                    "version",
                }
                if (
                    event.run_id is not None
                    or set(details) != required
                    or event.disposition != "CONSUMED_NO_DELIVERY"
                    or event.reasons != ("production delivery not established",)
                ):
                    raise JournalError("holdout consumption semantics are invalid")
                authorization_digest = details["authorization_event_digest"]
                _digest(authorization_digest, "authorization_event_digest")
                authorization = holdout_authorizations.get(authorization_digest)
                if (
                    authorization is None
                    or authorization.event_id != details["authorization_event_id"]
                    or authorization.plan_digest != event.plan_digest
                    or authorization.experiment_id != event.experiment_id
                ):
                    raise JournalError("holdout consumption authorization mismatch")
                authorization_details = cls._detail_map(authorization)
                if any(
                    details[name] != authorization_details[name]
                    for name in ("dataset", "partition", "version")
                ):
                    raise JournalError("holdout consumption dataset substitution")
                if authorization_digest in consumed_authorizations:
                    raise JournalError("holdout authorization already consumed")
                consumed_authorizations.add(authorization_digest)
            elif event.kind is EventKind.CONTAMINATION_RECORDED:
                if (
                    event.run_id is not None
                    or details
                    or event.disposition != "CONTAMINATED_EVIDENCE"
                    or not event.reasons
                ):
                    raise JournalError("contamination event semantics are invalid")
            else:
                raise JournalError("unknown journal event kind")

    def recover(self, *, authoritative: bool = False) -> tuple[JournalEvent, ...]:
        events: list[JournalEvent] = []
        previous: str | None = None
        identifiers: set[str] = set()
        for position, path in enumerate(self._paths(), start=1):
            if _is_reparse(path) or path.resolve(strict=True).parent != self._root:
                raise JournalError("journal record path is unsafe")
            if path.name != f"{position:08d}.json":
                raise JournalError("journal sequence is missing or reordered")
            event = self._decode(path)
            if event.sequence != position or event.previous_digest != previous:
                raise JournalError("journal hash chain is incomplete")
            if event.event_id in identifiers:
                raise JournalError("journal event identity is duplicated")
            identifiers.add(event.event_id)
            previous = event.content_digest
            events.append(event)
        result = tuple(events)
        self._validate_semantics(result)
        if authoritative:
            self.require_checkpointed(result)
        return result

    def history_trust_state(
        self, events: tuple[JournalEvent, ...] | None = None
    ) -> HistoryTrustState:
        values = self.recover() if events is None else events
        if self._checkpoint is None:
            return HistoryTrustState.UNCHECKPOINTED_TAIL
        try:
            self._trust.require_checkpoint(self._checkpoint)
            if (
                self._checkpoint.journal_identity != self._journal_identity
                or self._checkpoint.epoch != self._epoch
            ):
                raise JournalError("checkpoint journal identity mismatch")
            if self._checkpoint.establishment_identity != self._trust.identity:
                raise JournalError("checkpoint establishment identity mismatch")
            if self._checkpoint.accepted_sequence > len(values):
                raise JournalError("accepted journal records are missing")
            actual = (
                values[self._checkpoint.accepted_sequence - 1].content_digest
                if self._checkpoint.accepted_sequence
                else None
            )
            if actual != self._checkpoint.terminal_digest:
                raise JournalError("checkpoint terminal digest mismatch")
        except JournalError:
            return HistoryTrustState.INVALID_HISTORY
        return (
            HistoryTrustState.CHECKPOINTED_HISTORY
            if self._checkpoint.accepted_sequence == len(values)
            else HistoryTrustState.UNCHECKPOINTED_TAIL
        )

    def require_checkpointed(
        self, events: tuple[JournalEvent, ...] | None = None
    ) -> None:
        if (
            self.history_trust_state(events)
            is not HistoryTrustState.CHECKPOINTED_HISTORY
        ):
            raise JournalError(
                "authoritative history is not independently checkpointed"
            )

    def checkpoint_candidate(self, *, establishment_identity: str) -> JournalCheckpoint:
        events = self.recover()
        return JournalCheckpoint.create(
            journal_identity=self._journal_identity,
            epoch=self._epoch,
            accepted_sequence=len(events),
            terminal_digest=events[-1].content_digest if events else None,
            establishment_identity=establishment_identity,
        )

    def append(self, **_: object) -> JournalEvent:
        raise JournalError("direct journal append is prohibited")

    def _append(
        self,
        *,
        event_id: str,
        kind: EventKind,
        experiment_id: ExperimentId,
        run_id: ResearchRunId | None,
        plan: FrozenResearchPlan,
        disposition: str,
        reasons: tuple[str, ...] = (),
        details: tuple[str, ...] = (),
    ) -> JournalEvent:
        _assert_safe_chain(self._root)
        if self._root.resolve(strict=True) != self._root or _is_reparse(self._root):
            raise JournalError("journal root identity changed")
        event_id = _safe_id(event_id, "event_id")
        if type(kind) is not EventKind or type(experiment_id) is not ExperimentId:
            raise TypeError("protected event types are required")
        if run_id is not None and type(run_id) is not ResearchRunId:
            raise TypeError("run_id must be ResearchRunId")
        if type(plan) is not FrozenResearchPlan:
            raise TypeError("plan must be FrozenResearchPlan")
        disposition = _text(disposition, "disposition")
        reasons = _unique_text(reasons, "reasons")
        details = _unique_text(details, "details")
        events = self.recover()
        semantic = (
            kind,
            experiment_id.value,
            run_id.value if run_id else None,
            plan.plan_digest,
            plan.policy_identity,
            plan.owner_approval_reference,
            disposition,
            reasons,
            details,
        )
        for existing in events:
            if existing.event_id != event_id:
                continue
            existing_semantic = (
                existing.kind,
                existing.experiment_id,
                existing.run_id,
                existing.plan_digest,
                existing.policy_identity,
                existing.approval_reference,
                existing.disposition,
                existing.reasons,
                existing.details,
            )
            if existing_semantic == semantic:
                return existing
            raise JournalError("conflicting duplicate event")
        sequence = len(events) + 1
        previous = events[-1].content_digest if events else None
        provisional = JournalEvent(
            sequence,
            event_id,
            kind,
            experiment_id.value,
            run_id.value if run_id else None,
            plan.plan_digest,
            plan.policy_identity,
            plan.owner_approval_reference,
            disposition,
            reasons,
            details,
            previous,
            "",
        )
        digest = EvidenceContentDigest.from_bytes(
            b"ATIS-S4-08-JOURNAL-EVENT-V1\0"
            + canonical_json(_event_body(provisional, include_digest=False))
        ).value
        event = JournalEvent(
            provisional.sequence,
            provisional.event_id,
            provisional.kind,
            provisional.experiment_id,
            provisional.run_id,
            provisional.plan_digest,
            provisional.policy_identity,
            provisional.approval_reference,
            provisional.disposition,
            provisional.reasons,
            provisional.details,
            provisional.previous_digest,
            digest,
        )
        self._validate_semantics(events + (event,))
        content = canonical_json(_event_body(event, include_digest=True))
        target = self._root / f"{sequence:08d}.json"
        temporary = self._root / f".{sequence:08d}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise JournalError("concurrent journal writer detected") from error
            if target.read_bytes() != content:
                raise JournalError("journal read-back verification failed")
        except OSError as error:
            raise JournalError("journal write failed") from error
        finally:
            temporary.unlink(missing_ok=True)
        recovered = self.recover()
        if recovered[event.sequence - 1] != event:
            raise JournalError("published journal record identity changed")
        return event


@dataclass(frozen=True, slots=True)
class HoldoutAccessRecord:
    decision: HoldoutDecision
    disclose: bool
    event: JournalEvent


@dataclass(frozen=True, slots=True)
class EvidenceEligibility:
    state: EligibilityState
    reasons: tuple[str, ...]
    economic_edge: EconomicEdgeState = EconomicEdgeState.NOT_ESTABLISHED
    authority: str = RESEARCH_ONLY_AUTHORITY


_TRANSITIONS: Final = {
    TrialState.ADMITTED: frozenset({TrialState.RUNNING}),
    TrialState.RUNNING: frozenset(
        {TrialState.COMPLETED, TrialState.FAILED, TrialState.INTERRUPTED}
    ),
    TrialState.COMPLETED: frozenset(),
    TrialState.FAILED: frozenset(),
    TrialState.INTERRUPTED: frozenset(),
}
_STATE_KIND: Final = {
    TrialState.ADMITTED: EventKind.TRIAL_ADMITTED,
    TrialState.RUNNING: EventKind.TRIAL_RUNNING,
    TrialState.COMPLETED: EventKind.TRIAL_COMPLETED,
    TrialState.FAILED: EventKind.TRIAL_FAILED,
    TrialState.INTERRUPTED: EventKind.TRIAL_INTERRUPTED,
}


class ResearchGovernor:
    def __init__(
        self, journal: ResearchJournal, trust: TrustedEstablishment | None = None
    ) -> None:
        if type(journal) is not ResearchJournal:
            raise TypeError("journal must be ResearchJournal")
        self._journal = journal
        self._trust = trust or TrustedEstablishment.production_not_established()

    def _accepted_plans(self) -> dict[str, FrozenResearchPlan]:
        plans: dict[str, FrozenResearchPlan] = {}
        for event in self._journal.recover():
            if event.kind is EventKind.PLAN_ACCEPTED:
                details = self._journal._detail_map(event)
                raw = details["plan"]
                plans[event.plan_digest] = FrozenResearchPlan.from_canonical_body(
                    json.loads(raw)
                )
                approval = ApprovalEvidence.from_canonical_body(
                    json.loads(details["approval"])
                )
                self._trust.require_approval(approval, plans[event.plan_digest])
        return plans

    def _lineage(self, plan: FrozenResearchPlan) -> tuple[FrozenResearchPlan, ...]:
        plans = self._accepted_plans()
        if plan.plan_digest not in plans or plans[plan.plan_digest] != plan:
            raise ResearchGovernanceError("plan is not accepted durable evidence")
        result: list[FrozenResearchPlan] = []
        cursor: FrozenResearchPlan | None = plan
        while cursor is not None:
            if any(item.plan_digest == cursor.plan_digest for item in result):
                raise ResearchGovernanceError("plan lineage cycle")
            result.append(cursor)
            cursor = (
                plans.get(cursor.predecessor_plan_digest)
                if cursor.predecessor_plan_digest
                else None
            )
        return tuple(result)

    def accept_plan(
        self, plan: FrozenResearchPlan, approval: ApprovalEvidence
    ) -> JournalEvent:
        if (
            type(plan) is not FrozenResearchPlan
            or type(approval) is not ApprovalEvidence
        ):
            raise TypeError("protected plan and approval evidence are required")
        self._trust.require_approval(approval, plan)
        required_operation = (
            "S4_08_PLAN_APPROVAL"
            if plan.predecessor_plan_digest is None
            else "S4_08_BUDGET_EXPANSION"
        )
        if approval.operation != required_operation:
            raise ResearchGovernanceError("approval operation does not authorize plan")
        if (
            plan.predecessor_plan_digest is not None
            and plan.predecessor_plan_digest not in self._accepted_plans()
        ):
            raise ResearchGovernanceError("exact predecessor plan is not accepted")
        return self._journal._append(
            event_id=f"plan:{plan.plan_digest}",
            kind=EventKind.PLAN_ACCEPTED,
            experiment_id=plan.experiment_id,
            run_id=None,
            plan=plan,
            disposition="ACCEPTED",
            details=(
                "approval=" + canonical_json(approval.canonical_body()).decode("utf-8"),
                "plan=" + canonical_json(plan.canonical_body()).decode("utf-8"),
            ),
        )

    def _plan_events(self, plan: FrozenResearchPlan) -> tuple[JournalEvent, ...]:
        lineage = {item.plan_digest for item in self._lineage(plan)}
        return tuple(
            item for item in self._journal.recover() if item.plan_digest in lineage
        )

    def trial_state(self, run_id: ResearchRunId) -> TrialState | None:
        self._accepted_plans()
        states = [
            TrialState(item.disposition)
            for item in self._journal.recover()
            if item.run_id == run_id.value and item.kind in set(_STATE_KIND.values())
        ]
        return states[-1] if states else None

    def admit(self, plan: FrozenResearchPlan, run: ResearchRun) -> JournalEvent:
        if type(plan) is not FrozenResearchPlan or type(run) is not ResearchRun:
            raise TypeError("plan and run use protected types")
        if run.experiment_id != plan.experiment_id:
            raise ResearchGovernanceError("experiment substitution")
        lineage = self._lineage(plan)
        if self.trial_state(run.run_id) is not None:
            existing = next(
                item
                for item in self._journal.recover()
                if item.run_id == run.run_id.value
                and item.kind is EventKind.TRIAL_ADMITTED
            )
            if existing.plan_digest == plan.plan_digest:
                return existing
            raise ResearchGovernanceError(
                "run identity is already bound to another plan"
            )
        experiment_admissions = tuple(
            item
            for item in self._journal.recover()
            if item.experiment_id == plan.experiment_id.value
            and item.kind is EventKind.TRIAL_ADMITTED
        )
        lineage_digests = {item.plan_digest for item in lineage}
        if any(
            item.plan_digest not in lineage_digests for item in experiment_admissions
        ):
            raise ResearchGovernanceError("conflicting plan lineage")
        if run.replay_of is not None:
            target = next(
                (
                    item
                    for item in experiment_admissions
                    if item.run_id == run.replay_of.value
                ),
                None,
            )
            if target is None or target.plan_digest not in lineage_digests:
                raise ResearchGovernanceError(
                    "replay target is not an attributable admitted run"
                )
        admitted = len(experiment_admissions)
        if admitted >= plan.maximum_admitted_trials:
            raise ResearchGovernanceError("search budget exhausted")
        return self._journal._append(
            event_id=f"trial:{run.run_id.value}:admitted",
            kind=EventKind.TRIAL_ADMITTED,
            experiment_id=plan.experiment_id,
            run_id=run.run_id,
            plan=plan,
            disposition=TrialState.ADMITTED.value,
            details=(
                f"replay_of={run.replay_of.value if run.replay_of else 'none'}",
                f"maximum={plan.maximum_admitted_trials}",
                f"reproducibility={run.reproducibility_digest}",
            ),
        )

    def transition(
        self,
        plan: FrozenResearchPlan,
        run_id: ResearchRunId,
        target: TrialState,
        *,
        reasons: tuple[str, ...] = (),
    ) -> JournalEvent:
        if type(target) is not TrialState:
            raise TypeError("target must be TrialState")
        self._lineage(plan)
        current = self.trial_state(run_id)
        if current is None or target not in _TRANSITIONS[current]:
            raise ResearchGovernanceError("invalid trial lifecycle transition")
        reasons = _unique_text(reasons, "reasons")
        if target in {TrialState.FAILED, TrialState.INTERRUPTED} and not reasons:
            raise ResearchGovernanceError("failure and interruption require reasons")
        if target not in {TrialState.FAILED, TrialState.INTERRUPTED} and reasons:
            raise ResearchGovernanceError(
                "successful lifecycle states cannot carry reasons"
            )
        admitted = next(
            (
                item
                for item in self._journal.recover()
                if item.run_id == run_id.value and item.kind is EventKind.TRIAL_ADMITTED
            ),
            None,
        )
        if admitted is None or admitted.plan_digest != plan.plan_digest:
            raise ResearchGovernanceError("trial transition plan substitution")
        return self._journal._append(
            event_id=f"trial:{run_id.value}:{target.value.lower()}",
            kind=_STATE_KIND[target],
            experiment_id=plan.experiment_id,
            run_id=run_id,
            plan=plan,
            disposition=target.value,
            reasons=reasons,
        )

    def request_holdout(
        self,
        *,
        plan: FrozenResearchPlan,
        qualification: DatasetQualification,
        qualification_predecessors: tuple[DatasetQualification, ...],
        request_id: str,
        approval: ApprovalEvidence,
        purpose: str,
    ) -> HoldoutAccessRecord:
        request_id = _safe_id(request_id, "request_id")
        purpose = _text(purpose, "purpose")
        if type(qualification) is not DatasetQualification:
            raise TypeError("qualification must be DatasetQualification")
        _validate_qualification_chain(qualification, qualification_predecessors)
        self._trust.require_approval(approval, plan)
        if approval.operation != "S4_08_HOLDOUT_ACCESS":
            raise ResearchGovernanceError(
                "approval operation does not authorize holdout"
            )
        prior = tuple(
            event
            for event in self._plan_events(plan)
            if event.kind is EventKind.HOLDOUT_AUTHORIZED
            and f"dataset={plan.dataset_id}" in event.details
        )
        valid = (
            plan.partition is PartitionRole.PROTECTED_HOLDOUT
            and qualification.dataset_id == plan.dataset_id
            and qualification.dataset_version == plan.dataset_version
            and qualification.lifecycle is QualificationLifecycle.CURRENT
            and permits_claim(qualification, ClaimCeiling.ECONOMIC_VALIDATION)
            and approval.approval_reference == plan.owner_approval_reference
        )
        reason: tuple[str, ...]
        if prior:
            decision = HoldoutDecision.REPEATED_ACCESS
            disclose = False
            reason = ("independent confirmatory exposure already consumed",)
        elif not valid:
            decision = HoldoutDecision.UNAUTHORIZED_ACCESS
            disclose = False
            reason = ("holdout access policy not satisfied",)
        else:
            decision = HoldoutDecision.AUTHORIZED_ACCESS
            disclose = False
            reason = ()
        event = self._journal._append(
            event_id=f"holdout:{request_id}",
            kind=(
                EventKind.HOLDOUT_AUTHORIZED
                if decision is HoldoutDecision.AUTHORIZED_ACCESS
                else EventKind.HOLDOUT_REJECTED
            ),
            experiment_id=plan.experiment_id,
            run_id=None,
            plan=plan,
            disposition=decision.value,
            reasons=reason,
            details=(
                f"dataset={plan.dataset_id}",
                f"version={plan.dataset_version}",
                f"partition={plan.partition.value}",
                f"purpose={purpose}",
            ),
        )
        return HoldoutAccessRecord(decision, disclose, event)

    def consume_holdout(
        self,
        *,
        plan: FrozenResearchPlan,
        authorization_event: JournalEvent,
    ) -> HoldoutAccessRecord:
        """Consume a checkpointed request without granting protected-data delivery."""
        if (
            type(plan) is not FrozenResearchPlan
            or type(authorization_event) is not JournalEvent
        ):
            raise TypeError("protected plan and authorization event are required")
        events = self._journal.recover()
        matching_consumption = tuple(
            item
            for item in events
            if item.kind is EventKind.HOLDOUT_CONSUMED
            and f"authorization_event_digest={authorization_event.content_digest}"
            in item.details
        )
        if matching_consumption:
            raise ResearchGovernanceError("holdout authorization already consumed")
        if (
            authorization_event.kind is not EventKind.HOLDOUT_AUTHORIZED
            or authorization_event.disposition
            != HoldoutDecision.AUTHORIZED_ACCESS.value
            or authorization_event.plan_digest != plan.plan_digest
            or authorization_event not in events
        ):
            raise ResearchGovernanceError("holdout authorization is invalid")
        self._journal.require_checkpointed(events)
        checkpoint = self._journal._checkpoint
        if (
            checkpoint is None
            or checkpoint.accepted_sequence != authorization_event.sequence
            or checkpoint.terminal_digest != authorization_event.content_digest
        ):
            raise ResearchGovernanceError(
                "checkpoint does not bind the exact holdout authorization"
            )
        details = self._journal._detail_map(authorization_event)
        event = self._journal._append(
            event_id=f"holdout-consumption:{authorization_event.event_id}",
            kind=EventKind.HOLDOUT_CONSUMED,
            experiment_id=plan.experiment_id,
            run_id=None,
            plan=plan,
            disposition="CONSUMED_NO_DELIVERY",
            reasons=("production delivery not established",),
            details=(
                f"authorization_event_digest={authorization_event.content_digest}",
                f"authorization_event_id={authorization_event.event_id}",
                f"dataset={details['dataset']}",
                f"partition={details['partition']}",
                f"version={details['version']}",
            ),
        )
        return HoldoutAccessRecord(HoldoutDecision.AUTHORIZED_ACCESS, False, event)

    def record_contamination(
        self,
        *,
        plan: FrozenResearchPlan,
        contamination_id: str,
        reasons: tuple[str, ...],
    ) -> JournalEvent:
        self._lineage(plan)
        reasons = _unique_text(reasons, "reasons")
        if not reasons:
            raise ResearchGovernanceError("contamination requires reasons")
        return self._journal._append(
            event_id=f"contamination:{_safe_id(contamination_id, 'contamination_id')}",
            kind=EventKind.CONTAMINATION_RECORDED,
            experiment_id=plan.experiment_id,
            run_id=None,
            plan=plan,
            disposition="CONTAMINATED_EVIDENCE",
            reasons=reasons,
        )

    def assess_evidence(
        self,
        *,
        plan: FrozenResearchPlan,
        manifest: ResearchEvidenceManifest,
        qualification: DatasetQualification,
        qualification_predecessors: tuple[DatasetQualification, ...],
        reconciliation: ReconciliationResult,
        pit_evidence: PitEligibilityEvidence,
    ) -> EvidenceEligibility:
        reasons: list[str] = []
        if type(manifest) is not ResearchEvidenceManifest:
            raise TypeError("manifest must be ResearchEvidenceManifest")
        if type(qualification) is not DatasetQualification:
            raise TypeError("qualification must be DatasetQualification")
        if type(reconciliation) is not ReconciliationResult:
            raise TypeError("reconciliation must be ReconciliationResult")
        self._journal.require_checkpointed()
        _validate_manifest(manifest)
        _validate_qualification_chain(qualification, qualification_predecessors)
        pit_evidence.validate(plan, qualification, manifest)
        try:
            require_performance(reconciliation)
        except (TypeError, ValueError) as error:
            raise ResearchGovernanceError(
                "S4-06 performance truth is not established"
            ) from error
        events = self._plan_events(plan)
        admission = next(
            (
                item
                for item in events
                if item.kind is EventKind.TRIAL_ADMITTED
                and item.run_id == manifest.run_id.value
            ),
            None,
        )
        admission_details = self._journal._detail_map(admission) if admission else {}
        reconciliation_identity = reconciliation_evidence_identity(
            manifest.run_id, reconciliation
        )
        if manifest.hypothesis_id != plan.hypothesis_id:
            reasons.append("hypothesis mismatch")
        if manifest.experiment_id != plan.experiment_id:
            reasons.append("experiment mismatch")
        if admission_details.get("reproducibility") != manifest.reproducibility.digest:
            reasons.append("run reproducibility mismatch")
        replay = admission_details.get("replay_of")
        expected_replay = manifest.replay_of.value if manifest.replay_of else "none"
        if replay != expected_replay:
            reasons.append("replay identity mismatch")
        if manifest.disposition is not EvidenceDisposition.VALID:
            reasons.append("research evidence is not VALID")
        if not manifest.reproducibility.authoritative:
            reasons.append("source identity is not authoritative")
        if (
            qualification.dataset_id != plan.dataset_id
            or qualification.dataset_version != plan.dataset_version
        ):
            reasons.append("dataset qualification substitution")
        if qualification.lifecycle is not QualificationLifecycle.CURRENT:
            reasons.append("dataset qualification is not current")
        if (
            reconciliation.state is not ReconciliationState.RECONCILED
            or reconciliation.performance is None
        ):
            reasons.append("financial evidence is not reconciled")
        if reconciliation_identity not in manifest.output_identities:
            reasons.append("financial evidence is not bound to research run")
        if not any(
            item.kind is EventKind.TRIAL_COMPLETED
            and item.run_id == manifest.run_id.value
            for item in events
        ):
            reasons.append("trial is not completed")
        if reasons:
            return EvidenceEligibility(
                EligibilityState.INELIGIBLE_EVIDENCE, tuple(reasons)
            )
        technically_valid = True
        confirmatory_reasons: list[str] = []
        if not permits_claim(qualification, ClaimCeiling.ECONOMIC_VALIDATION):
            confirmatory_reasons.append("G3 economic-validation qualification required")
        consumed = [item for item in events if item.kind is EventKind.HOLDOUT_CONSUMED]
        if len(consumed) != 1:
            confirmatory_reasons.append(
                "exactly one checkpointed holdout consumption required"
            )
        if any(item.kind is EventKind.HOLDOUT_REJECTED for item in events):
            confirmatory_reasons.append(
                "repeated or unauthorized holdout access recorded"
            )
        if any(item.kind is EventKind.CONTAMINATION_RECORDED for item in events):
            confirmatory_reasons.append("confirmation lineage is contaminated")
        if plan.partition is not PartitionRole.PROTECTED_HOLDOUT:
            confirmatory_reasons.append("protected holdout partition required")
        if confirmatory_reasons:
            assert technically_valid
            return EvidenceEligibility(
                EligibilityState.TECHNICALLY_VALID_EVIDENCE,
                tuple(confirmatory_reasons),
            )
        return EvidenceEligibility(EligibilityState.ELIGIBLE_CONFIRMATORY_EVIDENCE, ())

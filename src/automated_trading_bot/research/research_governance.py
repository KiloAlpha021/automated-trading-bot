"""S4-08 bounded research governance, search budgets and holdout control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import stat
from typing import Final
from uuid import uuid4

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
)
from automated_trading_bot.research.evidence import (
    EvidenceDisposition,
    ResearchEvidenceManifest,
)
from automated_trading_bot.research.identity import (
    ExperimentId,
    HypothesisId,
    ResearchRun,
    ResearchRunId,
)
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationResult,
    ReconciliationState,
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
    TRIAL_ADMITTED = "TRIAL_ADMITTED"
    TRIAL_RUNNING = "TRIAL_RUNNING"
    TRIAL_COMPLETED = "TRIAL_COMPLETED"
    TRIAL_FAILED = "TRIAL_FAILED"
    TRIAL_INTERRUPTED = "TRIAL_INTERRUPTED"
    HOLDOUT_AUTHORIZED = "HOLDOUT_AUTHORIZED"
    HOLDOUT_REJECTED = "HOLDOUT_REJECTED"
    CONTAMINATION_RECORDED = "CONTAMINATION_RECORDED"


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


@dataclass(frozen=True, slots=True)
class FrozenResearchPlan:
    hypothesis_id: HypothesisId
    experiment_id: ExperimentId
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

    def __post_init__(self) -> None:
        if type(self.hypothesis_id) is not HypothesisId or type(self.experiment_id) is not ExperimentId:
            raise TypeError("protected research identities are required")
        _digest(self.configuration_digest, "configuration_digest")
        domain = _unique_text(self.parameter_domain, "parameter_domain")
        if not domain:
            raise ResearchGovernanceError("parameter_domain cannot be empty")
        _text(self.objective, "objective")
        _safe_id(self.dataset_id, "dataset_id")
        _safe_id(self.dataset_version, "dataset_version")
        if type(self.partition) is not PartitionRole:
            raise TypeError("partition must be PartitionRole")
        if type(self.maximum_admitted_trials) is not int or self.maximum_admitted_trials <= 0:
            raise ResearchGovernanceError("maximum_admitted_trials must be a positive integer")
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
        hypothesis_id: HypothesisId,
        experiment_id: ExperimentId,
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
        if type(hypothesis_id) is not HypothesisId or type(experiment_id) is not ExperimentId:
            raise TypeError("protected research identities are required")
        configuration_digest = _digest(configuration_digest, "configuration_digest")
        parameter_domain = _unique_text(parameter_domain, "parameter_domain")
        if not parameter_domain:
            raise ResearchGovernanceError("parameter_domain cannot be empty")
        objective = _text(objective, "objective")
        dataset_id = _safe_id(dataset_id, "dataset_id")
        dataset_version = _safe_id(dataset_version, "dataset_version")
        if type(partition) is not PartitionRole:
            raise TypeError("partition must be PartitionRole")
        if type(maximum_admitted_trials) is not int or maximum_admitted_trials <= 0:
            raise ResearchGovernanceError("maximum_admitted_trials must be a positive integer")
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
            hypothesis_id,
            experiment_id,
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
            predecessor.hypothesis_id,
            predecessor.experiment_id,
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

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be pathlib.Path")
        _assert_safe_chain(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise JournalError("journal root cannot be created") from error
        _assert_safe_chain(root)
        self._root = root.resolve(strict=True)
        if _is_reparse(self._root):
            raise JournalError("journal root is unsafe")
        self.recover()

    @property
    def root(self) -> Path:
        return self._root

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

    def recover(self) -> tuple[JournalEvent, ...]:
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
        return tuple(events)

    def append(
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
        return self.recover()[-1]


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
    def __init__(self, journal: ResearchJournal) -> None:
        if type(journal) is not ResearchJournal:
            raise TypeError("journal must be ResearchJournal")
        self._journal = journal

    def _plan_events(self, plan: FrozenResearchPlan) -> tuple[JournalEvent, ...]:
        return tuple(
            item for item in self._journal.recover() if item.plan_digest == plan.plan_digest
        )

    def trial_state(self, run_id: ResearchRunId) -> TrialState | None:
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
        if self.trial_state(run.run_id) is not None:
            existing = next(
                item
                for item in self._journal.recover()
                if item.run_id == run.run_id.value and item.kind is EventKind.TRIAL_ADMITTED
            )
            if existing.plan_digest == plan.plan_digest:
                return existing
            raise ResearchGovernanceError("run identity is already bound to another plan")
        experiment_admissions = tuple(
            item
            for item in self._journal.recover()
            if item.experiment_id == plan.experiment_id.value
            and item.kind is EventKind.TRIAL_ADMITTED
        )
        prior_plan_digests = tuple(dict.fromkeys(item.plan_digest for item in experiment_admissions))
        if prior_plan_digests and plan.plan_digest not in prior_plan_digests:
            if plan.predecessor_plan_digest != prior_plan_digests[-1]:
                raise ResearchGovernanceError("unauthorized budget expansion")
        admitted = len(experiment_admissions)
        if admitted >= plan.maximum_admitted_trials:
            raise ResearchGovernanceError("search budget exhausted")
        return self._journal.append(
            event_id=f"trial:{run.run_id.value}:admitted",
            kind=EventKind.TRIAL_ADMITTED,
            experiment_id=plan.experiment_id,
            run_id=run.run_id,
            plan=plan,
            disposition=TrialState.ADMITTED.value,
            details=(
                (f"replay_of={run.replay_of.value}" if run.replay_of else "original"),
                f"maximum={plan.maximum_admitted_trials}",
                f"predecessor={plan.predecessor_plan_digest or 'none'}",
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
        current = self.trial_state(run_id)
        if current is None or target not in _TRANSITIONS[current]:
            raise ResearchGovernanceError("invalid trial lifecycle transition")
        reasons = _unique_text(reasons, "reasons")
        if target in {TrialState.FAILED, TrialState.INTERRUPTED} and not reasons:
            raise ResearchGovernanceError("failure and interruption require reasons")
        if target not in {TrialState.FAILED, TrialState.INTERRUPTED} and reasons:
            raise ResearchGovernanceError("successful lifecycle states cannot carry reasons")
        return self._journal.append(
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
        request_id: str,
        owner_approval_reference: str,
        purpose: str,
    ) -> HoldoutAccessRecord:
        request_id = _safe_id(request_id, "request_id")
        purpose = _text(purpose, "purpose")
        owner_approval_reference = _safe_id(
            owner_approval_reference, "owner_approval_reference"
        )
        if type(qualification) is not DatasetQualification:
            raise TypeError("qualification must be DatasetQualification")
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
            and owner_approval_reference == plan.owner_approval_reference
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
            disclose = True
            reason = ()
        event = self._journal.append(
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

    def record_contamination(
        self,
        *,
        plan: FrozenResearchPlan,
        contamination_id: str,
        reasons: tuple[str, ...],
    ) -> JournalEvent:
        reasons = _unique_text(reasons, "reasons")
        if not reasons:
            raise ResearchGovernanceError("contamination requires reasons")
        return self._journal.append(
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
        reconciliation: ReconciliationResult,
    ) -> EvidenceEligibility:
        reasons: list[str] = []
        if type(manifest) is not ResearchEvidenceManifest:
            raise TypeError("manifest must be ResearchEvidenceManifest")
        if type(qualification) is not DatasetQualification:
            raise TypeError("qualification must be DatasetQualification")
        if type(reconciliation) is not ReconciliationResult:
            raise TypeError("reconciliation must be ReconciliationResult")
        events = self._plan_events(plan)
        if manifest.hypothesis_id != plan.hypothesis_id:
            reasons.append("hypothesis mismatch")
        if manifest.experiment_id != plan.experiment_id:
            reasons.append("experiment mismatch")
        if manifest.disposition is not EvidenceDisposition.VALID:
            reasons.append("research evidence is not VALID")
        if not manifest.reproducibility.authoritative:
            reasons.append("source identity is not authoritative")
        if qualification.dataset_id != plan.dataset_id or qualification.dataset_version != plan.dataset_version:
            reasons.append("dataset qualification substitution")
        if qualification.lifecycle is not QualificationLifecycle.CURRENT:
            reasons.append("dataset qualification is not current")
        if reconciliation.state is not ReconciliationState.RECONCILED:
            reasons.append("financial evidence is not reconciled")
        if not any(
            item.kind is EventKind.TRIAL_COMPLETED and item.run_id == manifest.run_id.value
            for item in events
        ):
            reasons.append("trial is not completed")
        if reasons:
            return EvidenceEligibility(EligibilityState.INELIGIBLE_EVIDENCE, tuple(reasons))
        technically_valid = True
        confirmatory_reasons: list[str] = []
        if not permits_claim(qualification, ClaimCeiling.ECONOMIC_VALIDATION):
            confirmatory_reasons.append("G3 economic-validation qualification required")
        authorized = [item for item in events if item.kind is EventKind.HOLDOUT_AUTHORIZED]
        if len(authorized) != 1:
            confirmatory_reasons.append("exactly one authorized holdout exposure required")
        if any(item.kind is EventKind.HOLDOUT_REJECTED for item in events):
            confirmatory_reasons.append("repeated or unauthorized holdout access recorded")
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

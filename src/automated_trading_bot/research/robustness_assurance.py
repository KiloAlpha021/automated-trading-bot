"""Research-only robustness orchestration for Stage 4 strategy evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
)
from automated_trading_bot.research.mean_reversion_strategy import MeanReversionPortfolio
from automated_trading_bot.research.momentum_strategy import MomentumPortfolio
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.reconciliation_performance import (
    PerformanceTruth,
    ReconciliationResult,
    require_performance,
)
from automated_trading_bot.research.research_governance import (
    EventKind,
    FrozenResearchPlan,
    PartitionRole,
    ResearchGovernor,
    ResearchJournal,
)
from automated_trading_bot.research.identity import ResearchRun
from automated_trading_bot.research.statistical_assurance import AssuranceOutcome
from automated_trading_bot.research.trend_strategy import TrendPortfolio


class RobustnessAssuranceError(ValueError):
    """Raised when robustness evidence violates its frozen contract."""


class RobustnessTest(StrEnum):
    ROB_01_REPLAY = "ROB-01"
    ROB_02_COST_STRESS = "ROB-02"
    ROB_03_EXECUTION_STRESS = "ROB-03"
    ROB_04_PARAMETER_NEIGHBOURHOOD = "ROB-04"
    ROB_05_UNIVERSE_PERTURBATION = "ROB-05"
    ROB_06_SUBPERIODS = "ROB-06"
    ROB_07_REGIMES = "ROB-07"
    ROB_08_ADVERSE_PERIODS = "ROB-08"
    ROB_09_CONCENTRATION = "ROB-09"
    ROB_10_REMOVE_CONTRIBUTORS = "ROB-10"
    ROB_11_OUT_OF_SAMPLE = "ROB-11"
    ROB_12_PROTECTED_HOLDOUT = "ROB-12"
    ROB_13_ADVERSARIAL_CHALLENGE = "ROB-13"


class ResearchAuthority(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY_NO_BROKER_OR_TRADING_AUTHORITY"


@dataclass(frozen=True, slots=True)
class StrategyBinding:
    strategy_id: str
    research_run_id: str
    decision_time: str
    output_digest: str
    authority: ResearchAuthority = ResearchAuthority.RESEARCH_ONLY


@dataclass(frozen=True, slots=True)
class AssuranceContext:
    strategy: StrategyBinding
    plan_digest: str
    experiment_id: str
    dataset_id: str
    dataset_version: str
    qualification_id: str
    admission_event_id: str
    trial_ids: tuple[str, ...]
    performance: PerformanceTruth
    context_digest: str


@dataclass(frozen=True, slots=True)
class RobustnessCase:
    test: RobustnessTest
    outcome: AssuranceOutcome
    finding: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.test) is not RobustnessTest or type(self.outcome) is not AssuranceOutcome:
            raise TypeError("invalid robustness case")
        _text(self.finding, "finding")
        _text_tuple(self.evidence_ids, "evidence_ids", nonempty=True)
        _text_tuple(self.limitations, "limitations", nonempty=False)
        if self.outcome is AssuranceOutcome.TEST_PASS and self.limitations:
            raise RobustnessAssuranceError("limited evidence cannot be reported as TEST_PASS")


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    context_digest: str
    cases: tuple[RobustnessCase, ...]
    outcome: AssuranceOutcome
    economic_edge: str
    permitted_conclusions: tuple[str, ...]
    evidence_digest: str
    authority: ResearchAuthority = ResearchAuthority.RESEARCH_ONLY


def bind_strategy_output(
    output: MomentumPortfolio | TrendPortfolio | MeanReversionPortfolio,
) -> StrategyBinding:
    """Bind one existing research-only strategy output without executing it."""
    if type(output) not in (MomentumPortfolio, TrendPortfolio, MeanReversionPortfolio):
        raise TypeError("supported Stage 4 strategy output required")
    if (
        output.authority.value != "RESEARCH_ONLY_NO_EDGE_TRADING_OR_CAPITAL_AUTHORITY"
        or output.economic_edge != "NOT_ESTABLISHED"
    ):
        raise RobustnessAssuranceError("strategy authority exceeds research-only scope")
    body = {
        "type": type(output).__name__,
        "strategy_id": output.strategy_id,
        "research_run_id": output.research_run_id,
        "decision_time": output.decision_time.value.isoformat(),
        "release_time": output.release_time.value.isoformat(),
        "fill_time": (
            output.fill_time.value.isoformat()
            if isinstance(output, (MomentumPortfolio, TrendPortfolio))
            else None
        ),
        "residual_cash": _decimal_text(output.residual_cash),
        "targets": [repr(item) for item in output.targets],
    }
    return StrategyBinding(
        output.strategy_id,
        output.research_run_id,
        output.decision_time.value.isoformat(),
        _digest("STRATEGY-BINDING-V1", body),
    )


def establish_context(
    *,
    strategy_output: MomentumPortfolio | TrendPortfolio | MeanReversionPortfolio,
    governor: ResearchGovernor,
    journal: ResearchJournal,
    plan: FrozenResearchPlan,
    run: ResearchRun,
    qualification: DatasetQualification,
    pit_eligibility: tuple[DecisionInputEligibility, ...],
    reconciliation: ReconciliationResult,
) -> AssuranceContext:
    """Establish attributable engineering evidence through predecessor validators."""
    if type(governor) is not ResearchGovernor or type(journal) is not ResearchJournal:
        raise TypeError("governor and journal must use protected types")
    admission = governor.require_admitted(plan, run)
    events = journal.recover(continuity_verified=True)
    if qualification.lifecycle is not QualificationLifecycle.CURRENT or not permits_claim(
        qualification, ClaimCeiling.ENGINEERING_ONLY
    ):
        raise RobustnessAssuranceError("current engineering-qualified dataset required")
    if (
        qualification.dataset_id != plan.dataset_id
        or qualification.dataset_version != plan.dataset_version
    ):
        raise RobustnessAssuranceError("dataset qualification substitution")
    if type(pit_eligibility) is not tuple or not pit_eligibility:
        raise RobustnessAssuranceError("PIT eligibility evidence required")
    if any(
        type(item) is not DecisionInputEligibility
        or item.state is not EligibilityState.ELIGIBLE
        or item.decision_time.value.isoformat() != strategy_output.decision_time.value.isoformat()
        for item in pit_eligibility
    ):
        raise RobustnessAssuranceError("invalid or future-known PIT evidence")
    performance = require_performance(reconciliation)
    strategy = bind_strategy_output(strategy_output)
    if strategy.research_run_id != run.run_id.value:
        raise RobustnessAssuranceError("strategy research-run substitution")
    trial_ids = tuple(
        item.run_id
        for item in events
        if item.kind is EventKind.TRIAL_ADMITTED
        and item.experiment_id == plan.experiment_id.value
        and item.run_id is not None
    )
    if run.run_id.value not in trial_ids:
        raise RobustnessAssuranceError("complete trial history is missing current run")
    body = {
        "strategy": strategy.output_digest,
        "plan": plan.plan_digest,
        "experiment": plan.experiment_id.value,
        "dataset": [qualification.dataset_id, qualification.dataset_version],
        "qualification": qualification.qualification_id,
        "admission": admission.content_digest,
        "trials": list(trial_ids),
        "pit": [item.observation_id for item in pit_eligibility],
        "performance": repr(performance),
    }
    return AssuranceContext(
        strategy,
        plan.plan_digest,
        plan.experiment_id.value,
        qualification.dataset_id,
        qualification.dataset_version,
        qualification.qualification_id,
        admission.event_id,
        trial_ids,
        performance,
        _digest("ASSURANCE-CONTEXT-V1", body),
    )


def assess_robustness(
    context: AssuranceContext, cases: tuple[RobustnessCase, ...]
) -> RobustnessReport:
    """Aggregate exactly ROB-01 through ROB-13 with fail-closed precedence."""
    if type(context) is not AssuranceContext:
        raise TypeError("established assurance context required")
    if type(cases) is not tuple or any(type(item) is not RobustnessCase for item in cases):
        raise TypeError("robustness cases must be a tuple")
    if len(cases) != len(RobustnessTest) or {item.test for item in cases} != set(RobustnessTest):
        raise RobustnessAssuranceError("exact ROB-01 through ROB-13 coverage required")
    outcomes = {item.outcome for item in cases}
    if AssuranceOutcome.INVALID in outcomes:
        outcome = AssuranceOutcome.INVALID
    elif AssuranceOutcome.TEST_FAIL in outcomes:
        outcome = AssuranceOutcome.TEST_FAIL
    elif AssuranceOutcome.INSUFFICIENT_EVIDENCE in outcomes:
        outcome = AssuranceOutcome.INSUFFICIENT_EVIDENCE
    else:
        outcome = AssuranceOutcome.TEST_PASS
    permitted = (
        "ENGINEERING_BEHAVIOUR_AS_SPECIFIED",
        "ECONOMIC_EDGE_NOT_ESTABLISHED",
        "NO_BROKER_OR_TRADING_AUTHORITY",
    )
    body = {
        "context": context.context_digest,
        "cases": [
            [item.test.value, item.outcome.value, item.finding, list(item.evidence_ids), list(item.limitations)]
            for item in cases
        ],
        "outcome": outcome.value,
        "permitted": list(permitted),
    }
    return RobustnessReport(
        context.context_digest,
        cases,
        outcome,
        "NOT_ESTABLISHED",
        permitted,
        _digest("ROBUSTNESS-REPORT-V1", body),
    )


def require_oos_partition(role: PartitionRole, *, contaminated: bool) -> AssuranceOutcome:
    if type(role) is not PartitionRole or type(contaminated) is not bool:
        raise TypeError("partition role and contamination flag required")
    if role is not PartitionRole.OUT_OF_SAMPLE:
        return AssuranceOutcome.INVALID
    return AssuranceOutcome.INVALID if contaminated else AssuranceOutcome.TEST_PASS


def protected_holdout_denial(role: PartitionRole, *, authorized: bool) -> AssuranceOutcome:
    if type(role) is not PartitionRole or type(authorized) is not bool:
        raise TypeError("partition role and authorization flag required")
    if role is not PartitionRole.PROTECTED_HOLDOUT:
        raise RobustnessAssuranceError("protected holdout role required")
    return AssuranceOutcome.INVALID if authorized else AssuranceOutcome.TEST_PASS


def contributor_removal(
    contributions: tuple[tuple[str, Decimal], ...], remove: tuple[str, ...]
) -> Decimal:
    if type(contributions) is not tuple or not contributions:
        raise RobustnessAssuranceError("contributions required")
    _text_tuple(remove, "remove", nonempty=False)
    seen: set[str] = set()
    result = Decimal("0")
    for identity, amount in contributions:
        _text(identity, "contributor")
        if identity in seen or type(amount) is not Decimal or not amount.is_finite():
            raise RobustnessAssuranceError("invalid contribution evidence")
        seen.add(identity)
        if identity not in remove:
            result += amount
    if not set(remove) <= seen:
        raise RobustnessAssuranceError("unknown contributor removal")
    return result


def _digest(domain: str, body: object) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(domain.encode() + b"\x00" + raw).hexdigest()


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise RobustnessAssuranceError(f"{name} must be nonempty text")
    return value


def _text_tuple(value: object, name: str, *, nonempty: bool) -> tuple[str, ...]:
    if type(value) is not tuple or (nonempty and not value):
        raise RobustnessAssuranceError(f"{name} must be a tuple")
    for item in value:
        _text(item, name)
    return value

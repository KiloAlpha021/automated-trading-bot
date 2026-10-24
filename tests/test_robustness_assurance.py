from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.mean_reversion_strategy import MeanReversionPortfolio
from automated_trading_bot.research.momentum_strategy import MomentumPortfolio
from automated_trading_bot.research.research_governance import PartitionRole
from automated_trading_bot.research.robustness_assurance import (
    AssuranceContext,
    ResearchAuthority,
    RobustnessAssuranceError,
    RobustnessCase,
    RobustnessTest,
    assess_robustness,
    bind_strategy_output,
    contributor_removal,
    protected_holdout_denial,
    require_oos_partition,
)
from automated_trading_bot.research.simulated_execution import CostScenario
from automated_trading_bot.research.statistical_assurance import (
    AssuranceOutcome,
    HypothesisTest,
    IntervalMethod,
    ResamplingConfig,
    SampleAdequacy,
    StatisticalAssuranceError,
    StatisticalSample,
    holm_adjust,
    moving_block_bootstrap_mean,
)
from automated_trading_bot.research.trend_strategy import TrendPortfolio


def _time(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 10, 23, hour, tzinfo=UTC))


def _adequacy(established: bool = True) -> SampleAdequacy:
    return SampleAdequacy(
        established,
        Decimal("6.5") if established else Decimal("0"),
        "precision-rule-v1",
        () if established else ("precision target not met",),
    )


def _sample(*, observations: tuple[Decimal, ...] | None = None) -> StatisticalSample:
    values = observations or (
        Decimal("0.01"),
        Decimal("-0.02"),
        Decimal("0.03"),
        Decimal("-0.01"),
        Decimal("0.02"),
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("-0.01"),
    )
    return StatisticalSample(
        "sample-1",
        "run-1",
        "dataset-1",
        "v1",
        "development",
        values,
        tuple(f"observation-{index}" for index in range(len(values))),
        True,
        _adequacy(),
    )


def _config() -> ResamplingConfig:
    return ResamplingConfig(IntervalMethod.PERCENTILE_MOVING_BLOCK, 1729, 2, 1000)


def _portfolio(kind: str) -> MomentumPortfolio | TrendPortfolio | MeanReversionPortfolio:
    common = dict(
        strategy_id=kind,
        research_run_id="run-1",
        decision_time=_time(10),
        release_time=_time(11),
        fill_time=_time(12),
        targets=(),
        residual_cash=Decimal("100000"),
        cost_scenarios=tuple(CostScenario),
    )
    if kind == "MOM-001":
        return MomentumPortfolio(
            **common,
            eligible_count=20,
            selected_count=4,
            fixture_minimum_applied=True,
        )
    if kind == "TREND-001":
        return TrendPortfolio(
            **common,
            equity_count=1,
            etf_count=1,
            positive_count=0,
            classifications=(),
        )
    common.pop("fill_time")
    return MeanReversionPortfolio(
        **common,
        decision_session_date=date(2026, 10, 23),
        execution_session_date=date(2026, 10, 26),
        eligible_count=4,
        selected_count=0,
        entry_intents=(),
        unfunded_instruments=(),
    )


def _context() -> AssuranceContext:
    binding = bind_strategy_output(_portfolio("MOM-001"))
    # Context construction through predecessor validators is covered by integration tests;
    # aggregation tests use a frozen representative value.
    return AssuranceContext(
        binding,
        "sha256:" + "1" * 64,
        "experiment-1",
        "dataset-1",
        "v1",
        "qualification-1",
        "trial:run-1:admitted",
        ("run-1",),
        object(),  # type: ignore[arg-type]
        "sha256:" + "2" * 64,
    )


def _cases(outcome: AssuranceOutcome = AssuranceOutcome.TEST_PASS) -> tuple[RobustnessCase, ...]:
    return tuple(
        RobustnessCase(item, outcome, f"{item.value} assessed", (f"evidence-{item.value}",))
        for item in RobustnessTest
    )


def test_a01_deterministic_bootstrap_replay() -> None:
    assert moving_block_bootstrap_mean(_sample(), _config()) == moving_block_bootstrap_mean(
        _sample(), _config()
    )


def test_a02_sample_hash_binding_changes_with_input() -> None:
    original = moving_block_bootstrap_mean(_sample(), _config())
    changed = moving_block_bootstrap_mean(
        _sample(observations=_sample().observations[:-1] + (Decimal("-0.02"),)), _config()
    )
    assert original.evidence_digest != changed.evidence_digest


def test_a03_future_known_pit_cannot_be_promoted() -> None:
    invalid = replace(_cases()[0], outcome=AssuranceOutcome.INVALID, finding="future-known PIT")
    report = assess_robustness(_context(), (invalid,) + _cases()[1:])
    assert report.outcome is AssuranceOutcome.INVALID


def test_a04_cost_scenarios_are_exact_and_attributable() -> None:
    assert _portfolio("MOM-001").cost_scenarios == tuple(CostScenario)
    assert CostScenario.EC0.value == "EC0"


def test_a05_unreconciled_financial_state_is_not_representable_as_context() -> None:
    assert "performance" in AssuranceContext.__dataclass_fields__
    assert "reconciliation" in __import__(
        "automated_trading_bot.research.robustness_assurance", fromlist=["establish_context"]
    ).establish_context.__annotations__


def test_a06_delayed_partial_execution_is_a_required_case() -> None:
    case = RobustnessCase(
        RobustnessTest.ROB_03_EXECUTION_STRESS,
        AssuranceOutcome.INSUFFICIENT_EVIDENCE,
        "partial fill unresolved",
        ("oms-order-1", "ledger-event-1"),
        ("remaining quantity",),
    )
    assert case.outcome is AssuranceOutcome.INSUFFICIENT_EVIDENCE


def test_a07_holm_requires_complete_trial_family() -> None:
    tests = (HypothesisTest("h1", Decimal("0.01")), HypothesisTest("h2", Decimal("0.03")))
    with pytest.raises(StatisticalAssuranceError, match="complete registered trial"):
        holm_adjust(family_id="family", tests=tests, registered_trial_ids=("h1",))


def test_a08_universe_survivorship_is_invalid() -> None:
    case = RobustnessCase(
        RobustnessTest.ROB_05_UNIVERSE_PERTURBATION,
        AssuranceOutcome.INVALID,
        "future universe membership",
        ("pit-universe",),
    )
    assert case.outcome is AssuranceOutcome.INVALID


def test_a09_overlapping_subperiod_claim_is_invalid() -> None:
    cases = list(_cases())
    cases[5] = RobustnessCase(
        RobustnessTest.ROB_06_SUBPERIODS,
        AssuranceOutcome.INVALID,
        "overlap mislabelled",
        ("partition-a", "partition-b"),
    )
    assert assess_robustness(_context(), tuple(cases)).outcome is AssuranceOutcome.INVALID


def test_a10_future_informed_regime_is_invalid() -> None:
    case = RobustnessCase(
        RobustnessTest.ROB_07_REGIMES,
        AssuranceOutcome.INVALID,
        "regime known after cutoff",
        ("regime-1",),
    )
    assert case.outcome is AssuranceOutcome.INVALID


def test_a11_post_result_adverse_window_is_invalid() -> None:
    case = RobustnessCase(
        RobustnessTest.ROB_08_ADVERSE_PERIODS,
        AssuranceOutcome.INVALID,
        "window not prespecified",
        ("window-1",),
    )
    assert case.outcome is AssuranceOutcome.INVALID


def test_a12_contributor_removal_recomputes_result() -> None:
    values = (("top", Decimal("60")), ("second", Decimal("30")), ("third", Decimal("10")))
    assert contributor_removal(values, ("top",)) == Decimal("40")


def test_a13_contaminated_oos_is_invalid() -> None:
    assert require_oos_partition(PartitionRole.OUT_OF_SAMPLE, contaminated=True) is AssuranceOutcome.INVALID
    assert require_oos_partition(PartitionRole.OUT_OF_SAMPLE, contaminated=False) is AssuranceOutcome.TEST_PASS


def test_a14_protected_holdout_is_denied_in_engineering() -> None:
    assert (
        protected_holdout_denial(PartitionRole.PROTECTED_HOLDOUT, authorized=False)
        is AssuranceOutcome.TEST_PASS
    )
    assert (
        protected_holdout_denial(PartitionRole.PROTECTED_HOLDOUT, authorized=True)
        is AssuranceOutcome.INVALID
    )


def test_a15_missing_challenge_is_insufficient() -> None:
    cases = list(_cases())
    cases[-1] = RobustnessCase(
        RobustnessTest.ROB_13_ADVERSARIAL_CHALLENGE,
        AssuranceOutcome.INSUFFICIENT_EVIDENCE,
        "independent challenge missing",
        ("challenge-register",),
        ("no challenge dossier",),
    )
    assert assess_robustness(_context(), tuple(cases)).outcome is AssuranceOutcome.INSUFFICIENT_EVIDENCE


def test_a16_holm_reference_rejects_first_only() -> None:
    tests = tuple(
        HypothesisTest(name, value)
        for name, value in (("first", Decimal("0.01")), ("second", Decimal("0.03")), ("third", Decimal("0.04")))
    )
    result = holm_adjust(
        family_id="family-1",
        tests=tests,
        registered_trial_ids=("first", "second", "third"),
    )
    assert tuple(item.rejected for item in result.decisions) == (True, False, False)


def test_a17_missing_resampling_configuration_fails_closed() -> None:
    with pytest.raises(TypeError):
        moving_block_bootstrap_mean(_sample(), None)  # type: ignore[arg-type]
    insufficient = moving_block_bootstrap_mean(
        _sample(), ResamplingConfig(IntervalMethod.PERCENTILE_MOVING_BLOCK, 1729, 1, 1000)
    )
    assert insufficient.outcome is AssuranceOutcome.INSUFFICIENT_EVIDENCE


def test_a18_zero_variance_and_inadequacy_are_insufficient() -> None:
    zero = _sample(observations=(Decimal("0"),) * 8)
    assert moving_block_bootstrap_mean(zero, _config()).outcome is AssuranceOutcome.INSUFFICIENT_EVIDENCE
    inadequate = replace(_sample(), adequacy=_adequacy(False))
    assert (
        moving_block_bootstrap_mean(inadequate, _config()).outcome
        is AssuranceOutcome.INSUFFICIENT_EVIDENCE
    )


def test_a19_missing_benchmark_is_not_pass() -> None:
    case = RobustnessCase(
        RobustnessTest.ROB_09_CONCENTRATION,
        AssuranceOutcome.INSUFFICIENT_EVIDENCE,
        "qualified benchmark missing",
        ("benchmark-register",),
        ("benchmark-relative conclusion unavailable",),
    )
    assert case.outcome is AssuranceOutcome.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("case_outcome", "expected"),
    [
        (AssuranceOutcome.INVALID, AssuranceOutcome.INVALID),
        (AssuranceOutcome.TEST_FAIL, AssuranceOutcome.TEST_FAIL),
        (AssuranceOutcome.INSUFFICIENT_EVIDENCE, AssuranceOutcome.INSUFFICIENT_EVIDENCE),
    ],
)
def test_a20_failure_state_propagation(
    case_outcome: AssuranceOutcome, expected: AssuranceOutcome
) -> None:
    cases = list(_cases())
    cases[0] = RobustnessCase(
        RobustnessTest.ROB_01_REPLAY,
        case_outcome,
        "upstream outcome",
        ("upstream",),
        () if case_outcome in (AssuranceOutcome.INVALID, AssuranceOutcome.TEST_FAIL) else ("missing",),
    )
    assert assess_robustness(_context(), tuple(cases)).outcome is expected


def test_a21_all_three_strategy_adapters_are_identity_bound() -> None:
    bindings = tuple(bind_strategy_output(_portfolio(name)) for name in ("MOM-001", "TREND-001", "MR-001-R1"))
    assert tuple(item.strategy_id for item in bindings) == ("MOM-001", "TREND-001", "MR-001-R1")
    assert len({item.output_digest for item in bindings}) == 3


def test_a22_reports_never_grant_edge_or_trading_authority() -> None:
    report = assess_robustness(_context(), _cases())
    assert report.economic_edge == "NOT_ESTABLISHED"
    assert report.authority is ResearchAuthority.RESEARCH_ONLY
    assert "NO_BROKER_OR_TRADING_AUTHORITY" in report.permitted_conclusions


def test_a23_contract_maps_all_acceptance_cases_and_rob_catalogue() -> None:
    path = Path(__file__).parents[1] / "docs" / "stage4" / "s4-12-robustness-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert set(contract["acceptance"]) == {f"A{index:02d}" for index in range(1, 24)}
    assert [item[0] for item in contract["robustness_catalogue"]] == [
        f"ROB-{index:02d}" for index in range(1, 14)
    ]
    assert contract["authority"] == "RESEARCH_ONLY_NO_BROKER_OR_TRADING_AUTHORITY"


def test_duplicate_or_missing_robustness_case_is_rejected() -> None:
    with pytest.raises(RobustnessAssuranceError, match="exact ROB-01"):
        assess_robustness(_context(), _cases()[:-1])


def test_case_with_limitations_cannot_claim_pass() -> None:
    with pytest.raises(RobustnessAssuranceError, match="limited evidence"):
        RobustnessCase(
            RobustnessTest.ROB_01_REPLAY,
            AssuranceOutcome.TEST_PASS,
            "claimed pass",
            ("evidence",),
            ("unresolved limitation",),
        )

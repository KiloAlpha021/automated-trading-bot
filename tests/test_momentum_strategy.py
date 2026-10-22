from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    EligibleCoverage,
    QualificationLevel,
    QualificationLifecycle,
    QualificationPolicy,
)
from automated_trading_bot.research.identity import Experiment, Hypothesis, ResearchProject, ResearchRun
from automated_trading_bot.research.momentum_strategy import (
    MomentumCandidate,
    MomentumError,
    StrategyAuthority,
    TotalReturnEndpoint,
    construct_momentum_portfolio,
    expected_signal_months,
)
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.simulated_execution import CostScenario


def ts(year: int, month: int, day: int, hour: int = 12) -> Timestamp:
    return Timestamp(datetime(year, month, day, hour, tzinfo=timezone.utc))


DECISION = ts(2026, 4, 15)
RELEASE = ts(2026, 4, 16)
FILL = ts(2026, 4, 16, 13)


def instrument(index: int) -> InstrumentId:
    return InstrumentId(UUID(f"00000000-0000-4000-8000-{index:012d}"))


def qualification() -> DatasetQualification:
    return DatasetQualification(
        "q", "dataset:atis/mom", "v1", "sha256:" + "1" * 64,
        "sha256:" + "2" * 64, QualificationPolicy("policy", "v1", "evidence"),
        QualificationLevel.G1, ClaimCeiling.ENGINEERING_ONLY, (),
        EligibleCoverage(date(2025, 1, 1), date(2026, 3, 31)), (), None,
        QualificationLifecycle.CURRENT, (), None,
    )


def run() -> ResearchRun:
    project = ResearchProject.create("MOM-001 engineering")
    hypothesis = Hypothesis.create(project.project_id, "momentum engineering contract")
    experiment = Experiment.create(hypothesis.hypothesis_id, "sha256:" + "3" * 64, "v1", "synthetic fixtures")
    return ResearchRun.create(experiment.experiment_id, "sha256:" + "4" * 64)


def eligibility(index: int) -> DecisionInputEligibility:
    return DecisionInputEligibility(EligibilityState.ELIGIBLE, (), f"obs:{index}", DECISION, ClaimCeiling.ENGINEERING_ONLY)


def candidates(count: int = 20) -> tuple[MomentumCandidate, ...]:
    values = []
    for index in range(1, count + 1):
        iid = instrument(index)
        start = TotalReturnEndpoint(iid, date(2025, 3, 31), Decimal("100"), ts(2025, 4, 1), "dataset:atis/mom", "v1", eligibility(index))
        end = TotalReturnEndpoint(iid, date(2026, 2, 28), Decimal(100 + index), ts(2026, 3, 1), "dataset:atis/mom", "v1", eligibility(index))
        values.append(MomentumCandidate(iid, start, end, Decimal("101")))
    return tuple(values)


def build(values: tuple[MomentumCandidate, ...] | None = None, **kwargs: object):
    return construct_momentum_portfolio(candidates=values or candidates(), qualification=qualification(), research_run=run(), decision_time=DECISION, release_time=RELEASE, fill_time=FILL, fixture_minimum=True, **kwargs)


def test_canonical_12_minus_1_window_and_excluded_month():
    assert expected_signal_months(DECISION) == ((2025, 3), (2026, 2))
    with pytest.raises(MomentumError, match="canonical"):
        build((replace(candidates()[0], end=replace(candidates()[0].end, month_end=date(2026, 3, 31))),) + candidates()[1:])


def test_future_known_and_pit_ineligible_fail_closed():
    first = candidates()[0]
    with pytest.raises(MomentumError, match="future-known"):
        build((replace(first, end=replace(first.end, available_at=ts(2026, 5, 1))),) + candidates()[1:])
    with pytest.raises(MomentumError, match="PIT-ineligible"):
        bad = replace(first.end, eligibility=replace(first.end.eligibility, state=EligibilityState.INELIGIBLE))
        build((replace(first, end=bad),) + candidates()[1:])


def test_dataset_qualification_and_identity_are_bound():
    first = candidates()[0]
    with pytest.raises(MomentumError, match="dataset substitution"):
        build((replace(first, start=replace(first.start, dataset_version="other")),) + candidates()[1:])
    with pytest.raises(MomentumError, match="current engineering-qualified"):
        construct_momentum_portfolio(candidates=candidates(), qualification=replace(qualification(), lifecycle=QualificationLifecycle.INVALIDATED), research_run=run(), decision_time=DECISION, release_time=RELEASE, fill_time=FILL)


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("NaN")])
def test_missing_or_invalid_total_return_fails_closed(value):
    first = candidates()[0]
    with pytest.raises(MomentumError):
        build((replace(first, start=replace(first.start, total_return_index=value)),) + candidates()[1:])


def test_ranking_ties_use_canonical_instrument_identity():
    values = list(candidates())
    values[-1] = replace(values[-1], end=replace(values[-1].end, total_return_index=Decimal("119")))
    result = build(tuple(values))
    assert result.targets[0].instrument_id == instrument(19)
    assert result.targets[1].instrument_id == instrument(20)


def test_top_twenty_percent_floor_at_20_and_21():
    assert build().selected_count == 4
    assert build(candidates(21)).selected_count == 4


def test_fixture_minimum_is_not_live_universe_rule():
    with pytest.raises(MomentumError, match="at least 20"):
        build(candidates(19))
    result = construct_momentum_portfolio(candidates=candidates(5), qualification=qualification(), research_run=run(), decision_time=DECISION, release_time=RELEASE, fill_time=FILL)
    assert result.selected_count == 1 and not result.fixture_minimum_applied


def test_equal_weight_whole_shares_and_residual_cash():
    result = build()
    assert {target.weight for target in result.targets} == {Decimal("0.25")}
    assert all(type(target.whole_shares) is int and target.whole_shares > 0 for target in result.targets)
    assert result.residual_cash == Decimal("212")


@pytest.mark.parametrize("release,fill", [(DECISION, FILL), (RELEASE, RELEASE)])
def test_decision_release_fill_are_strictly_ordered(release, fill):
    with pytest.raises(MomentumError, match="decision < release < fill"):
        construct_momentum_portfolio(candidates=candidates(), qualification=qualification(), research_run=run(), decision_time=DECISION, release_time=release, fill_time=fill)


def test_existing_cost_scenarios_are_reused_and_ec0_not_redefined():
    assert build().cost_scenarios == tuple(CostScenario)
    assert CostScenario.EC0.value == "EC0"


def test_duplicate_instrument_and_insufficient_capital_fail_closed():
    with pytest.raises(MomentumError, match="duplicate"):
        build((candidates()[0], candidates()[0]) + candidates()[2:])
    with pytest.raises(MomentumError, match="fund"):
        build(hypothetical_capital=Decimal("1"))


def test_research_identity_and_no_edge_or_trading_authority():
    result = build()
    assert result.research_run_id == run().run_id.value
    assert result.economic_edge == "NOT_ESTABLISHED"
    assert result.authority is StrategyAuthority.RESEARCH_ONLY
    assert not hasattr(result, "broker") and not hasattr(result, "holdout")


def test_source_uses_predecessor_contracts_without_duplicate_engines():
    import automated_trading_bot.research.momentum_strategy as module
    source = open(module.__file__, encoding="utf-8").read()
    assert "SimulationLedger" not in source
    assert "SimulatedOms" not in source
    assert "ResearchGovernor" not in source
    assert "MOM-F01" not in source

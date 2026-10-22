from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    EligibleCoverage,
    QualificationLevel,
    QualificationLifecycle,
    QualificationPolicy,
)
from automated_trading_bot.research.identity import Experiment, Hypothesis, ResearchProject, ResearchRun
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.simulated_execution import CostScenario
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerEventId,
    SimulationLedger,
)
from automated_trading_bot.research.trend_strategy import (
    AssetClass,
    StrategyAuthority,
    TotalReturnEndpoint,
    TrendCandidate,
    TrendError,
    construct_transition_intents,
    construct_trend_portfolio,
    expected_signal_months,
)


def ts(year: int, month: int, day: int, hour: int = 12) -> Timestamp:
    return Timestamp(datetime(year, month, day, hour, tzinfo=timezone.utc))


DECISION = ts(2026, 4, 15)
RELEASE = ts(2026, 4, 16)
FILL = ts(2026, 4, 16, 13)
USD = Currency("USD")
DIGEST = EvidenceContentDigest("sha256:" + "9" * 64)


def instrument(index: int) -> InstrumentId:
    return InstrumentId(UUID(f"00000000-0000-4000-8000-{index:012d}"))


def qualification() -> DatasetQualification:
    return DatasetQualification(
        "q", "dataset:atis/trend", "v1", "sha256:" + "1" * 64,
        "sha256:" + "2" * 64, QualificationPolicy("policy", "v1", "evidence"),
        QualificationLevel.G1, ClaimCeiling.ENGINEERING_ONLY, (),
        EligibleCoverage(date(2025, 1, 1), date(2026, 3, 31)), (), None,
        QualificationLifecycle.CURRENT, (), None,
    )


def run() -> ResearchRun:
    project = ResearchProject.create("TREND-001 engineering")
    hypothesis = Hypothesis.create(project.project_id, "time-series trend engineering contract")
    experiment = Experiment.create(hypothesis.hypothesis_id, "sha256:" + "3" * 64, "v1", "synthetic fixtures")
    return ResearchRun.create(experiment.experiment_id, "sha256:" + "4" * 64)


def eligibility(index: int) -> DecisionInputEligibility:
    return DecisionInputEligibility(EligibilityState.ELIGIBLE, (), f"obs:{index}", DECISION, ClaimCeiling.ENGINEERING_ONLY)


def candidate(index: int, end_index: str, asset_class: AssetClass = AssetClass.EQUITY) -> TrendCandidate:
    iid = instrument(index)
    start = TotalReturnEndpoint(iid, date(2025, 3, 31), Decimal("100"), ts(2025, 4, 1), "dataset:atis/trend", "v1", eligibility(index))
    end = TotalReturnEndpoint(iid, date(2026, 3, 31), Decimal(end_index), ts(2026, 4, 1), "dataset:atis/trend", "v1", eligibility(index))
    return TrendCandidate(iid, asset_class, start, end, Decimal("100"))


def candidates() -> tuple[TrendCandidate, ...]:
    return (
        candidate(1, "120", AssetClass.EQUITY),
        candidate(2, "100", AssetClass.ETF),
        candidate(3, "80", AssetClass.EQUITY),
    )


def build(values: tuple[TrendCandidate, ...] | None = None, **kwargs: object):
    return construct_trend_portfolio(
        candidates=values or candidates(), qualification=qualification(), research_run=run(),
        decision_time=DECISION, release_time=RELEASE, fill_time=FILL, **kwargs,
    )


def ledger_with_position(quantity: str = "10") -> SimulationLedger:
    current_run = run()
    ledger = SimulationLedger.empty(USD).apply(FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:opening"), 1, ts(2026, 4, 1),
        FinancialEventKind.OPENING_CASH, current_run.run_id, DIGEST, currency=USD,
        cash_delta=Decimal("100000"),
    ))
    return ledger.apply(FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:buy"), 2, ts(2026, 4, 2),
        FinancialEventKind.TRADE, current_run.run_id, DIGEST, currency=USD,
        instrument_id=instrument(1), quantity=Decimal(quantity), unit_amount=Decimal("100"),
    ))


def test_exact_trailing_twelve_completed_month_window() -> None:
    assert expected_signal_months(DECISION) == (date(2025, 3, 31), date(2026, 3, 31))
    first = candidates()[0]
    with pytest.raises(TrendError, match="completed-month"):
        build((replace(first, end=replace(first.end, month_end=date(2026, 3, 30))),) + candidates()[1:])


@pytest.mark.parametrize("index,expected", [("120", Decimal("0.2")), ("100", Decimal("0")), ("80", Decimal("-0.2"))])
def test_positive_zero_and_negative_signals(index: str, expected: Decimal) -> None:
    value = candidate(1, index)
    result = build((value,))
    if expected > 0:
        assert result.targets[0].signal == expected
    else:
        assert result.targets == () and result.residual_cash == Decimal("100000")


def test_missing_stale_future_known_and_pit_invalid_fail_closed() -> None:
    first = candidates()[0]
    with pytest.raises(TrendError):
        build((replace(first, start=replace(first.start, total_return_index=Decimal("NaN"))),) + candidates()[1:])
    with pytest.raises(TrendError, match="completed-month"):
        build((replace(first, start=replace(first.start, month_end=date(2025, 2, 28))),) + candidates()[1:])
    with pytest.raises(TrendError, match="future-known"):
        build((replace(first, end=replace(first.end, available_at=ts(2026, 5, 1))),) + candidates()[1:])
    bad = replace(first.end, eligibility=replace(first.end.eligibility, state=EligibilityState.INELIGIBLE))
    with pytest.raises(TrendError, match="PIT-ineligible"):
        build((replace(first, end=bad),) + candidates()[1:])


def test_dataset_qualification_and_identity_are_bound() -> None:
    first = candidates()[0]
    with pytest.raises(TrendError, match="dataset substitution"):
        build((replace(first, start=replace(first.start, dataset_version="other")),) + candidates()[1:])
    with pytest.raises(TrendError, match="current engineering-qualified"):
        construct_trend_portfolio(candidates=candidates(), qualification=replace(qualification(), lifecycle=QualificationLifecycle.INVALIDATED), research_run=run(), decision_time=DECISION, release_time=RELEASE, fill_time=FILL)


def test_equity_and_etf_are_classified_without_pooling_identity() -> None:
    result = build()
    assert result.equity_count == 2 and result.etf_count == 1
    assert dict(result.classifications)[instrument(2)] is AssetClass.ETF


def test_equal_weight_whole_shares_and_residual_cash() -> None:
    result = build((candidate(1, "120"), candidate(2, "110", AssetClass.ETF)))
    assert {target.weight for target in result.targets} == {Decimal("0.5")}
    assert {target.whole_shares for target in result.targets} == {500}
    assert result.residual_cash == 0


def test_all_nonpositive_universe_is_all_cash() -> None:
    result = build((candidate(1, "100"), candidate(2, "90", AssetClass.ETF)))
    assert result.targets == () and result.positive_count == 0
    assert result.residual_cash == Decimal("100000")


def test_positive_negative_positive_creates_exit_and_reentry_intents() -> None:
    held = ledger_with_position("10")
    positive = build((candidate(1, "120"),))
    buy = construct_transition_intents(positive, held)[0]
    assert buy.side is OrderSide.BUY and buy.current_quantity == Decimal("10") and buy.target_quantity == Decimal("1000")
    negative = build((candidate(1, "80"),))
    sell = construct_transition_intents(negative, held)[0]
    assert sell.side is OrderSide.SELL and sell.quantity == Decimal("10") and sell.target_quantity == 0
    assert construct_transition_intents(positive, held)[0].side is OrderSide.BUY


def test_intents_do_not_mutate_ledger_or_treat_unfilled_exit_as_truth() -> None:
    held = ledger_with_position("10")
    before = held.state
    intents = construct_transition_intents(build((candidate(1, "80"),)), held)
    assert intents[0].side is OrderSide.SELL
    assert held.state == before and held.state.position(instrument(1)).quantity == Decimal("10")  # type: ignore[union-attr]


def test_partial_actual_fill_preserves_remaining_actual_holding() -> None:
    held = ledger_with_position("10")
    partial = held.apply(FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:partial-sell"), 3, ts(2026, 4, 17),
        FinancialEventKind.TRADE, run().run_id, DIGEST, currency=USD,
        instrument_id=instrument(1), quantity=Decimal("-4"), unit_amount=Decimal("100"),
    ))
    assert partial.state.position(instrument(1)).quantity == Decimal("6")  # type: ignore[union-attr]
    remaining = construct_transition_intents(build((candidate(1, "80"),)), partial)[0]
    assert remaining.side is OrderSide.SELL and remaining.quantity == Decimal("6")


@pytest.mark.parametrize("release,fill", [(DECISION, FILL), (RELEASE, RELEASE)])
def test_decision_release_fill_are_strictly_ordered(release: Timestamp, fill: Timestamp) -> None:
    with pytest.raises(TrendError, match="decision < release < fill"):
        construct_trend_portfolio(candidates=candidates(), qualification=qualification(), research_run=run(), decision_time=DECISION, release_time=release, fill_time=fill)


def test_cost_scenarios_and_existing_financial_controls_are_reused() -> None:
    assert build().cost_scenarios == tuple(CostScenario)
    assert CostScenario.EC0.value == "EC0"


def test_held_instrument_without_current_classification_fails_closed() -> None:
    with pytest.raises(TrendError, match="classified universe"):
        construct_transition_intents(build((candidate(2, "120", AssetClass.ETF),)), ledger_with_position())


def test_research_identity_no_edge_and_no_trading_authority() -> None:
    result = build()
    assert result.research_run_id == run().run_id.value
    assert result.economic_edge == "NOT_ESTABLISHED"
    assert result.authority is StrategyAuthority.RESEARCH_ONLY
    assert not hasattr(result, "broker") and not hasattr(result, "holdout")


def test_source_reuses_predecessors_without_duplicate_engines() -> None:
    import automated_trading_bot.research.trend_strategy as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "class SimulatedOms" not in source
    assert "class SimulationLedger" not in source
    assert "class ResearchGovernor" not in source
    assert "TR-F01" not in source


def test_contract_preserves_acceptance_and_empirical_catalogues() -> None:
    contract_path = Path(__file__).parents[1] / "docs/stage4/s4-10-trend-001-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert len(contract["acceptance_categories"]) == 12
    assert contract["falsification_catalogue"] == [f"TR-F{index:02d}" for index in range(1, 16)]
    assert contract["falsification_outcomes"] == "NOT_RUN_NOT_FABRICATED"
    assert contract["authority"] == {
        "broker": False,
        "capital": False,
        "economic_edge": "NOT_ESTABLISHED",
        "live_trading": False,
        "protected_holdout": False,
        "scope": "RESEARCH_ONLY_ENGINEERING_SIMULATION",
    }

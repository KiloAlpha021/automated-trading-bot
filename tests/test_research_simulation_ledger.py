from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.corporate_actions.model import ActionVersionId
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerContractError,
    LedgerEventId,
    SIMULATION_ONLY_AUTHORITY,
    SimulationLedger,
)


DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))
ACTION = ActionVersionId("atis:corporate-action-version:v1:" + "3" * 64)
USD = Currency("USD")


def event(sequence: int, kind: FinancialEventKind, **values: object) -> FinancialEvent:
    return FinancialEvent(
        event_id=LedgerEventId(f"atis:simulation-ledger-event:v1:event-{sequence}"),
        sequence=sequence,
        occurred_at=Timestamp(datetime(2026, 1, sequence, tzinfo=timezone.utc)),
        kind=kind,
        research_run_id=RUN,
        evidence_digest=DIGEST,
        **values,
    )


def opening(amount: str = "1000") -> FinancialEvent:
    return event(1, FinancialEventKind.OPENING_CASH, cash_delta=Decimal(amount))


def funded() -> SimulationLedger:
    return SimulationLedger.empty(USD).apply(opening())


def bought() -> SimulationLedger:
    return funded().apply(
        event(
            2,
            FinancialEventKind.TRADE,
            instrument_id=INSTRUMENT,
            quantity=Decimal("10"),
            unit_amount=Decimal("20"),
        )
    )


def test_initial_state_requires_attributable_opening_event() -> None:
    ledger = SimulationLedger.empty(USD)
    assert ledger.state.cash == 0 and ledger.events == ()
    with pytest.raises(LedgerContractError, match="first event"):
        ledger.apply(
            event(1, FinancialEventKind.CASH_ADJUSTMENT, cash_delta=Decimal("1"))
        )


def test_attributable_cash_mutation_and_no_silent_cash_creation() -> None:
    ledger = funded().apply(
        event(2, FinancialEventKind.CASH_ADJUSTMENT, cash_delta=Decimal("25"))
    )
    assert ledger.state.cash == Decimal("1025")
    with pytest.raises(LedgerContractError, match="requires nonzero"):
        event(3, FinancialEventKind.CASH_ADJUSTMENT, cash_delta=Decimal(0))


def test_position_acquisition_and_weighted_average_cost_basis() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.TRADE,
            instrument_id=INSTRUMENT,
            quantity=Decimal("5"),
            unit_amount=Decimal("30"),
        )
    )
    position = ledger.state.position(INSTRUMENT)
    assert position is not None
    assert (position.quantity, position.cost_basis) == (Decimal("15"), Decimal("350"))
    assert ledger.state.cash == Decimal("650")


def test_sale_realises_pnl_and_reduces_cost_at_average() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.TRADE,
            instrument_id=INSTRUMENT,
            quantity=Decimal("-4"),
            unit_amount=Decimal("25"),
        )
    )
    position = ledger.state.position(INSTRUMENT)
    assert position is not None
    assert (position.quantity, position.cost_basis, position.realised_pnl) == (
        Decimal("6"), Decimal("120"), Decimal("20")
    )
    assert ledger.state.realised_pnl == Decimal("20")


def test_mark_updates_unrealised_pnl_and_equity_reconciles() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.MARK,
            instrument_id=INSTRUMENT,
            unit_amount=Decimal("25"),
        )
    )
    assert ledger.state.unrealised_pnl == Decimal("50")
    assert ledger.state.portfolio_value == Decimal("1050")
    assert ledger.state.portfolio_value == ledger.state.cash + Decimal("250")


def test_deterministic_replay_reconstructs_identical_state() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.MARK,
            instrument_id=INSTRUMENT,
            unit_amount=Decimal("21"),
        )
    )
    assert SimulationLedger.replay(USD, ledger.events) == ledger


def test_exact_duplicate_is_idempotent_but_conflict_fails_closed() -> None:
    ledger = funded()
    assert ledger.apply(opening()) is ledger
    with pytest.raises(LedgerContractError, match="conflicts"):
        ledger.apply(replace(opening(), cash_delta=Decimal("999")))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_financial_state_rejected(value: Decimal) -> None:
    with pytest.raises(LedgerContractError, match="finite"):
        event(1, FinancialEventKind.OPENING_CASH, cash_delta=value)


def test_malformed_and_unknown_position_events_fail_closed() -> None:
    with pytest.raises(LedgerContractError, match="positive unit_amount"):
        event(
            2,
            FinancialEventKind.TRADE,
            instrument_id=INSTRUMENT,
            quantity=Decimal("1"),
            unit_amount=Decimal("-1"),
        )
    with pytest.raises(LedgerContractError, match="unknown"):
        funded().apply(
            event(
                2,
                FinancialEventKind.MARK,
                instrument_id=INSTRUMENT,
                unit_amount=Decimal("10"),
            )
        )


def test_short_position_creation_is_rejected() -> None:
    with pytest.raises(LedgerContractError, match="short"):
        bought().apply(
            event(
                3,
                FinancialEventKind.TRADE,
                instrument_id=INSTRUMENT,
                quantity=Decimal("-11"),
                unit_amount=Decimal("20"),
            )
        )


def test_split_preserves_cost_basis_and_market_value_with_attribution() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.SPLIT,
            instrument_id=INSTRUMENT,
            quantity=Decimal("2"),
            corporate_action_version_id=ACTION,
        )
    )
    position = ledger.state.position(INSTRUMENT)
    assert position is not None
    assert (position.quantity, position.cost_basis, position.mark_price) == (
        Decimal("20"), Decimal("200"), Decimal("10")
    )
    assert position.market_value == Decimal("200")


def test_dividend_is_attributable_cash_and_realised_pnl() -> None:
    ledger = bought().apply(
        event(
            3,
            FinancialEventKind.CASH_DIVIDEND,
            instrument_id=INSTRUMENT,
            unit_amount=Decimal("1.50"),
            corporate_action_version_id=ACTION,
        )
    )
    assert ledger.state.cash == Decimal("815.00")
    assert ledger.state.realised_pnl == Decimal("15.00")


def test_corporate_action_without_source_attribution_is_rejected() -> None:
    with pytest.raises(LedgerContractError, match="corporate-action attribution"):
        event(
            3,
            FinancialEventKind.SPLIT,
            instrument_id=INSTRUMENT,
            quantity=Decimal("2"),
        )


def test_history_and_state_are_immutable() -> None:
    ledger = bought()
    with pytest.raises(FrozenInstanceError):
        ledger.state.cash = Decimal(0)  # type: ignore[misc]
    with pytest.raises(TypeError):
        ledger.events[0] = opening()  # type: ignore[index]


def test_sequence_gap_and_second_opening_are_rejected() -> None:
    with pytest.raises(LedgerContractError, match="contiguous"):
        funded().apply(
            event(3, FinancialEventKind.CASH_ADJUSTMENT, cash_delta=Decimal("1"))
        )
    with pytest.raises(LedgerContractError, match="only once"):
        funded().apply(replace(opening(), event_id=LedgerEventId("atis:simulation-ledger-event:v1:other"), sequence=2))


def test_ledger_has_no_trading_authority_or_execution_methods() -> None:
    ledger = funded()
    assert ledger.authority == SIMULATION_ONLY_AUTHORITY
    assert not any(hasattr(ledger, name) for name in ("submit_order", "send_order", "execute", "connect_broker"))

from uuid import uuid4

from automated_trading_bot.domain.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)


def test_order_id_stores_uuid() -> None:
    value = uuid4()

    order_id = OrderId(value)

    assert order_id.value == value


def test_trade_id_stores_uuid() -> None:
    value = uuid4()

    trade_id = TradeId(value)

    assert trade_id.value == value


def test_strategy_id_stores_string() -> None:
    strategy_id = StrategyId("strategy-001")

    assert strategy_id.value == "strategy-001"


def test_instrument_id_stores_string() -> None:
    instrument_id = InstrumentId("XAUUSD")

    assert instrument_id.value == "XAUUSD"

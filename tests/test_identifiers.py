from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

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


@pytest.mark.parametrize("identifier_type", [OrderId, TradeId])
@pytest.mark.parametrize("invalid_value", ["00000000-0000-0000-0000-000000000000", 0, True, None])
def test_uuid_identifiers_reject_non_uuid(identifier_type, invalid_value) -> None:
    with pytest.raises(TypeError, match="^value must be a UUID$"):
        identifier_type(invalid_value)


@pytest.mark.parametrize("identifier_type", [StrategyId, InstrumentId])
@pytest.mark.parametrize("invalid_value", [UUID(int=0), 0, True, None, b"identifier"])
def test_string_identifiers_reject_non_string(identifier_type, invalid_value) -> None:
    with pytest.raises(TypeError, match="^value must be a str$"):
        identifier_type(invalid_value)


@pytest.mark.parametrize("identifier_type", [StrategyId, InstrumentId])
@pytest.mark.parametrize("value", ["", "  Mixed Case  ", "策略-α"])
def test_string_identifiers_preserve_text(identifier_type, value: str) -> None:
    assert identifier_type(value).value == value


@pytest.mark.parametrize("identifier_type", [OrderId, TradeId])
def test_uuid_identifiers_preserve_uuid(identifier_type) -> None:
    value = UUID(int=0)
    assert identifier_type(value).value is value


@pytest.mark.parametrize(
    "identifier_type, value, replacement",
    [
        (OrderId, UUID(int=0), UUID(int=1)),
        (TradeId, UUID(int=0), UUID(int=1)),
        (StrategyId, "strategy", "replacement"),
        (InstrumentId, "instrument", "replacement"),
    ],
)
def test_identifiers_remain_immutable(identifier_type, value, replacement) -> None:
    identifier = identifier_type(value)
    with pytest.raises(FrozenInstanceError):
        identifier.value = replacement
    assert identifier.value == value


def test_identifier_classes_remain_distinct() -> None:
    value = UUID(int=0)
    assert OrderId(value) != TradeId(value)
    assert StrategyId("same") != InstrumentId("same")

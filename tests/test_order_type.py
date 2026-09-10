from automated_trading_bot.domain.order_type import OrderType


def test_order_type_market_value() -> None:
    assert OrderType.MARKET.value == "MARKET"


def test_order_type_limit_value() -> None:
    assert OrderType.LIMIT.value == "LIMIT"


def test_order_type_is_string_compatible() -> None:
    assert str(OrderType.MARKET) == "MARKET"

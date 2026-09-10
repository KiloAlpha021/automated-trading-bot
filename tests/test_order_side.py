from automated_trading_bot.domain.order_side import OrderSide


def test_order_side_buy_value() -> None:
    assert OrderSide.BUY.value == "BUY"


def test_order_side_sell_value() -> None:
    assert OrderSide.SELL.value == "SELL"


def test_order_side_is_string_compatible() -> None:
    assert str(OrderSide.BUY) == "BUY"

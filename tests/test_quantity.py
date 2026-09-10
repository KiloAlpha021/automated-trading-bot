from decimal import Decimal

from automated_trading_bot.domain.quantity import Quantity


def test_quantity_stores_decimal_value() -> None:
    quantity = Quantity(value=Decimal("2.50"))

    assert quantity.value == Decimal("2.50")


def test_quantity_is_immutable() -> None:
    quantity = Quantity(value=Decimal("2.50"))

    try:
        quantity.value = Decimal("3.00")
    except AttributeError:
        pass
    else:
        raise AssertionError("Quantity should be immutable")

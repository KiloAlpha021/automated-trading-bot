from decimal import Decimal

import pytest

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


@pytest.mark.parametrize("invalid_value", [0.1, 1, True, "0.1", None])
def test_quantity_rejects_non_decimal_values(invalid_value: object) -> None:
    with pytest.raises(TypeError, match="^value must be a Decimal$"):
        Quantity(value=invalid_value)


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-2.50"), Decimal("0.12345678901234567890123456789")])
def test_quantity_preserves_decimal_input(value: Decimal) -> None:
    result = Quantity(value=value)

    assert result.value is value

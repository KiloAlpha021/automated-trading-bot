from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from automated_trading_bot.domain.money import Money


def test_money_stores_decimal_amount() -> None:
    money = Money(amount=Decimal("10.50"), currency="GBP")

    assert money.amount == Decimal("10.50")


def test_money_stores_currency() -> None:
    money = Money(amount=Decimal("10.50"), currency="GBP")

    assert money.currency == "GBP"


def test_money_is_immutable() -> None:
    money = Money(amount=Decimal("10.50"), currency="GBP")

    try:
        money.amount = Decimal("20.00")
    except AttributeError:
        pass
    else:
        raise AssertionError("Money should be immutable")


@pytest.mark.parametrize("invalid_value", [0.1, 1, True, "0.1", None])
def test_money_rejects_non_decimal_values(invalid_value: object) -> None:
    with pytest.raises(TypeError, match="^amount must be a Decimal$"):
        Money(amount=invalid_value, currency="GBP")


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-2.50"), Decimal("0.12345678901234567890123456789")])
def test_money_preserves_decimal_input(value: Decimal) -> None:
    result = Money(amount=value, currency="GBP")

    assert result.amount is value


@pytest.mark.parametrize("invalid_currency", [None, 1, True, 1.0, Decimal("1"), b"GBP", []])
def test_money_rejects_non_string_currency(invalid_currency: object) -> None:
    with pytest.raises(TypeError, match="^currency must be a str$"):
        Money(amount=Decimal("10.50"), currency=invalid_currency)


@pytest.mark.parametrize("currency", ["GBP", "gbp", "", "  GBP  ", "custom-unit"])
def test_money_preserves_currency_string(currency: str) -> None:
    money = Money(amount=Decimal("10.50"), currency=currency)

    assert money.currency == currency


def test_money_currency_is_immutable() -> None:
    money = Money(amount=Decimal("10.50"), currency="GBP")

    with pytest.raises(FrozenInstanceError):
        money.currency = "USD"

    assert money.currency == "GBP"

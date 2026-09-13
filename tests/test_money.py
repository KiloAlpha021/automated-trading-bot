from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.money import Money


def test_money_stores_decimal_amount() -> None:
    money = Money(amount=Decimal("10.50"), currency=Currency("GBP"))

    assert money.amount == Decimal("10.50")


def test_money_stores_currency() -> None:
    money = Money(amount=Decimal("10.50"), currency=Currency("GBP"))

    assert money.currency == Currency("GBP")


def test_money_is_immutable() -> None:
    money = Money(amount=Decimal("10.50"), currency=Currency("GBP"))

    try:
        money.amount = Decimal("20.00")
    except AttributeError:
        pass
    else:
        raise AssertionError("Money should be immutable")


@pytest.mark.parametrize("invalid_value", [0.1, 1, True, "0.1", None])
def test_money_rejects_non_decimal_values(invalid_value: object) -> None:
    with pytest.raises(TypeError, match="^amount must be a Decimal$"):
        Money(amount=invalid_value, currency=Currency("GBP"))


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1.25"),
        Decimal("0"),
        Decimal("-0"),
        Decimal("-2.50"),
        Decimal("0.12345678901234567890123456789"),
    ],
)
def test_money_preserves_decimal_input(value: Decimal) -> None:
    result = Money(amount=value, currency=Currency("GBP"))

    assert result.amount is value


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_money_rejects_non_finite_decimal_values(value: Decimal) -> None:
    with pytest.raises(ValueError, match="^amount must be finite$"):
        Money(amount=value, currency=Currency("GBP"))


@pytest.mark.parametrize("invalid_currency", [None, 1, True, 1.0, Decimal("1"), b"GBP", [], "GBP", "gbp", "", "  GBP  ", "custom-unit"])
def test_money_rejects_non_currency(invalid_currency: object) -> None:
    with pytest.raises(TypeError, match="^currency must be a Currency$"):
        Money(amount=Decimal("10.50"), currency=invalid_currency)


@pytest.mark.parametrize("code", ["GBP", "USD", "EUR", "AAA", "ZZZ"])
def test_money_preserves_currency_object(code: str) -> None:
    currency = Currency(code)
    money = Money(amount=Decimal("10.50"), currency=currency)

    assert money.currency is currency
    assert money.currency.code == code


def test_money_currency_is_immutable() -> None:
    money = Money(amount=Decimal("10.50"), currency=Currency("GBP"))

    with pytest.raises(FrozenInstanceError):
        money.currency = Currency("USD")

    assert money.currency == Currency("GBP")


def test_money_value_equality_is_stable() -> None:
    assert Money(Decimal("10.50"), Currency("GBP")) == Money(
        Decimal("10.50"), Currency("GBP")
    )
    assert Money(Decimal("10.50"), Currency("GBP")) != Money(
        Decimal("10.50"), Currency("USD")
    )
    assert Money(Decimal("10.50"), Currency("GBP")) != Money(
        Decimal("10.51"), Currency("GBP")
    )

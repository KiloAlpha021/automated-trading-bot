from decimal import Decimal

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

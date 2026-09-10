from dataclasses import FrozenInstanceError

import pytest

from automated_trading_bot.domain.currency import Currency


@pytest.mark.parametrize("code", ["GBP", "USD", "EUR", "AAA", "ZZZ"])
def test_currency_value_and_serialization_invariants(code: str) -> None:
    currency = Currency(code)

    assert currency.code is code
    assert currency == Currency(code)
    assert currency.to_string() == code
    assert Currency(currency.to_string()) == currency


def test_different_currency_codes_are_unequal() -> None:
    assert Currency("GBP") != Currency("USD")


def test_currency_is_immutable() -> None:
    currency = Currency("GBP")

    with pytest.raises(FrozenInstanceError):
        currency.code = "USD"

    assert currency.code == "GBP"


@pytest.mark.parametrize("code", [None, True, 826, 1.0, b"GBP", []])
def test_currency_rejects_non_string(code: object) -> None:
    with pytest.raises(TypeError, match="^code must be a str$"):
        Currency(code)


@pytest.mark.parametrize(
    "code",
    ["", "G", "GB", "GBPP", "GBPUSD", "gbp", "Gbp", "826", "GB1",
     " GBP", "GBP ", "G P", "GB\t", "GB\n", "GB!", "ÉUR", "ＧＢＰ"],
)
def test_currency_rejects_invalid_structure(code: str) -> None:
    with pytest.raises(ValueError, match="^code must be exactly three ASCII uppercase letters$"):
        Currency(code)

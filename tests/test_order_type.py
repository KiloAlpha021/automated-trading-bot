import pytest

from automated_trading_bot.domain.order_type import OrderType


def test_order_type_market_value() -> None:
    assert OrderType.MARKET.value == "MARKET"


def test_order_type_limit_value() -> None:
    assert OrderType.LIMIT.value == "LIMIT"


def test_order_type_is_string_compatible() -> None:
    assert str(OrderType.MARKET) == "MARKET"


def test_order_type_has_only_established_members() -> None:
    assert dict(OrderType.__members__) == {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
    }


@pytest.mark.parametrize(
    "value, expected",
    [("MARKET", OrderType.MARKET), ("LIMIT", OrderType.LIMIT)],
)
def test_order_type_resolves_exact_values(value: str, expected: OrderType) -> None:
    assert OrderType(value) is expected


@pytest.mark.parametrize("value", ["", "UNKNOWN", "market", "limit", " MARKET ", " LIMIT "])
def test_order_type_rejects_unestablished_strings(value: str) -> None:
    with pytest.raises(ValueError):
        OrderType(value)

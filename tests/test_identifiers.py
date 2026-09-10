from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)


UUID_IDENTIFIER_TYPES = [
    OrderId,
    TradeId,
    EventId,
    CorrelationId,
    CausationId,
]

STRING_IDENTIFIER_TYPES = [
    StrategyId,
    InstrumentId,
    IdempotencyKey,
]


def test_order_id_stores_uuid() -> None:
    value = uuid4()

    order_id = OrderId(value)

    assert order_id.value == value


def test_trade_id_stores_uuid() -> None:
    value = uuid4()

    trade_id = TradeId(value)

    assert trade_id.value == value


def test_event_id_stores_uuid() -> None:
    value = uuid4()

    event_id = EventId(value)

    assert event_id.value == value


def test_correlation_id_stores_uuid() -> None:
    value = uuid4()

    correlation_id = CorrelationId(value)

    assert correlation_id.value == value


def test_causation_id_stores_uuid() -> None:
    value = uuid4()

    causation_id = CausationId(value)

    assert causation_id.value == value


def test_strategy_id_stores_string() -> None:
    strategy_id = StrategyId("strategy-001")

    assert strategy_id.value == "strategy-001"


def test_instrument_id_stores_string() -> None:
    instrument_id = InstrumentId("XAUUSD")

    assert instrument_id.value == "XAUUSD"


def test_idempotency_key_stores_string() -> None:
    key = IdempotencyKey("submit-order:authority-7:request-42")

    assert key.value == "submit-order:authority-7:request-42"


@pytest.mark.parametrize("identifier_type", UUID_IDENTIFIER_TYPES)
@pytest.mark.parametrize(
    "invalid_value",
    [
        "00000000-0000-0000-0000-000000000000",
        0,
        True,
        None,
    ],
)
def test_uuid_identifiers_reject_non_uuid(
    identifier_type,
    invalid_value,
) -> None:
    with pytest.raises(TypeError, match="^value must be a UUID$"):
        identifier_type(invalid_value)


@pytest.mark.parametrize("identifier_type", STRING_IDENTIFIER_TYPES)
@pytest.mark.parametrize(
    "invalid_value",
    [
        UUID(int=0),
        0,
        True,
        None,
        b"identifier",
    ],
)
def test_string_identifiers_reject_non_string(
    identifier_type,
    invalid_value,
) -> None:
    with pytest.raises(TypeError, match="^value must be a str$"):
        identifier_type(invalid_value)


@pytest.mark.parametrize("identifier_type", STRING_IDENTIFIER_TYPES)
@pytest.mark.parametrize(
    "value",
    [
        "",
        "  Mixed Case  ",
        "策略-α",
    ],
)
def test_string_identifiers_preserve_text(
    identifier_type,
    value: str,
) -> None:
    assert identifier_type(value).value == value


@pytest.mark.parametrize("identifier_type", UUID_IDENTIFIER_TYPES)
def test_uuid_identifiers_preserve_uuid(identifier_type) -> None:
    value = UUID(int=0)

    assert identifier_type(value).value is value


@pytest.mark.parametrize(
    "identifier_type, value, replacement",
    [
        (OrderId, UUID(int=0), UUID(int=1)),
        (TradeId, UUID(int=0), UUID(int=1)),
        (EventId, UUID(int=0), UUID(int=1)),
        (CorrelationId, UUID(int=0), UUID(int=1)),
        (CausationId, UUID(int=0), UUID(int=1)),
        (StrategyId, "strategy", "replacement"),
        (InstrumentId, "instrument", "replacement"),
        (IdempotencyKey, "key", "replacement"),
    ],
)
def test_identifiers_remain_immutable(
    identifier_type,
    value,
    replacement,
) -> None:
    identifier = identifier_type(value)

    with pytest.raises(FrozenInstanceError):
        identifier.value = replacement

    assert identifier.value == value


def test_uuid_identifier_classes_remain_distinct() -> None:
    value = UUID(int=0)

    identifiers = [
        OrderId(value),
        TradeId(value),
        EventId(value),
        CorrelationId(value),
        CausationId(value),
    ]

    for left_index, left in enumerate(identifiers):
        for right_index, right in enumerate(identifiers):
            if left_index != right_index:
                assert left != right


def test_string_identifier_classes_remain_distinct() -> None:
    value = "same"

    assert StrategyId(value) != InstrumentId(value)
    assert StrategyId(value) != IdempotencyKey(value)
    assert InstrumentId(value) != IdempotencyKey(value)
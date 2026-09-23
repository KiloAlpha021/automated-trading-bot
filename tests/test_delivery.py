from dataclasses import FrozenInstanceError, fields
from itertools import product
from uuid import uuid4

import pytest

from automated_trading_bot.domain.delivery import (
    DeliveryTransitionDisposition,
    DestinationId,
    HandlerId,
    HandlerVersion,
    InboxIdentity,
    InboxRecord,
    InboxState,
    OutboxIdentity,
    OutboxRecord,
    OutboxState,
    transition_inbox,
    transition_outbox,
)
from automated_trading_bot.domain.identifiers import EventId


def inbox(state: InboxState) -> InboxRecord:
    return InboxRecord(InboxIdentity(EventId(uuid4()), HandlerId("handler.main")), HandlerVersion("v1"), state)


def outbox(state: OutboxState) -> OutboxRecord:
    return OutboxRecord(OutboxIdentity(EventId(uuid4()), DestinationId("destination.primary")), state)


@pytest.mark.parametrize("contract", [HandlerId, HandlerVersion, DestinationId])
@pytest.mark.parametrize("value", ["a", "stable.v1", "route:primary", "x" * 64])
def test_stable_identity_contracts(contract: type[object], value: str) -> None:
    item = contract(value)  # type: ignore[call-arg]
    assert item.to_string() == value  # type: ignore[attr-defined]
    assert not hasattr(item, "__dict__")


@pytest.mark.parametrize("contract", [HandlerId, HandlerVersion, DestinationId])
@pytest.mark.parametrize("value", ["", "-bad", "with space", "x" * 65, 1, b"x"])
def test_stable_identities_reject_invalid_values(contract: type[object], value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        contract(value)  # type: ignore[call-arg]


def expected_inbox(current: InboxState, target: InboxState) -> tuple[DeliveryTransitionDisposition, bool]:
    if current in (InboxState.APPLIED, InboxState.QUARANTINED):
        return DeliveryTransitionDisposition.UNCHANGED, False
    if target is InboxState.PENDING:
        return DeliveryTransitionDisposition.ILLEGAL, False
    if current is InboxState.RETRY and target is InboxState.RETRY:
        return DeliveryTransitionDisposition.UNCHANGED, False
    return DeliveryTransitionDisposition.TRANSITIONED, True


@pytest.mark.parametrize("current,target", list(product(InboxState, repeat=2)))
def test_complete_inbox_matrix(current: InboxState, target: InboxState) -> None:
    record = inbox(current)
    result = transition_inbox(record, target)
    disposition, changed = expected_inbox(current, target)
    assert result.previous_record is record
    assert result.disposition is disposition
    if changed:
        assert result.next_record is not record
        assert result.next_record.state is target
        assert result.next_record.identity is record.identity
        assert result.next_record.handler_version is record.handler_version
    else:
        assert result.next_record is record


def expected_outbox(current: OutboxState, target: OutboxState) -> tuple[DeliveryTransitionDisposition, bool]:
    if current in (OutboxState.DELIVERED, OutboxState.QUARANTINED):
        return DeliveryTransitionDisposition.UNCHANGED, False
    if target is OutboxState.PENDING:
        return DeliveryTransitionDisposition.ILLEGAL, False
    if current is OutboxState.RETRY and target is OutboxState.RETRY:
        return DeliveryTransitionDisposition.UNCHANGED, False
    return DeliveryTransitionDisposition.TRANSITIONED, True


@pytest.mark.parametrize("current,target", list(product(OutboxState, repeat=2)))
def test_complete_outbox_matrix(current: OutboxState, target: OutboxState) -> None:
    record = outbox(current)
    result = transition_outbox(record, target)
    disposition, changed = expected_outbox(current, target)
    assert result.previous_record is record
    assert result.disposition is disposition
    if changed:
        assert result.next_record is not record
        assert result.next_record.state is target
        assert result.next_record.identity is record.identity
    else:
        assert result.next_record is record


def test_handler_version_is_not_part_of_inbox_identity() -> None:
    identity = InboxIdentity(EventId(uuid4()), HandlerId("handler"))
    assert InboxRecord(identity, HandlerVersion("v1"), InboxState.PENDING).identity == InboxRecord(identity, HandlerVersion("v2"), InboxState.PENDING).identity


def test_records_have_exact_fields_and_are_frozen() -> None:
    item = inbox(InboxState.PENDING)
    assert [field.name for field in fields(item)] == ["identity", "handler_version", "state"]
    with pytest.raises(FrozenInstanceError):
        item.state = InboxState.APPLIED  # type: ignore[misc]
    assert not hasattr(item, "__dict__")


def test_delivery_contracts_are_inert() -> None:
    values = [HandlerId("handler"), DestinationId("destination"), inbox(InboxState.PENDING), outbox(OutboxState.PENDING)]
    for value in values:
        assert not any(hasattr(value, name) for name in ("execute", "dispatch", "send", "publish", "connect"))


def test_exact_enum_vocabularies() -> None:
    assert [item.value for item in InboxState] == ["PENDING", "RETRY", "APPLIED", "QUARANTINED"]
    assert [item.value for item in OutboxState] == ["PENDING", "RETRY", "DELIVERED", "QUARANTINED"]
    assert [item.value for item in DeliveryTransitionDisposition] == ["TRANSITIONED", "UNCHANGED", "ILLEGAL"]

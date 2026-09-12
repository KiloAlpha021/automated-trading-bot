from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from automated_trading_bot.domain.clock import TestClock as ControlledClock
from automated_trading_bot.domain.decision import (
    Approval, ApprovalScope, ApprovalStatus, DecisionLineage, DecisionStatus,
    NoTrade, Proposal,
)
from automated_trading_bot.domain.identifiers import InstrumentId, StrategyId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.quantity import Quantity
from automated_trading_bot.domain.timestamp import Timestamp


def instant(seconds: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 9, 10, tzinfo=UTC) + timedelta(seconds=seconds))


def lineage() -> DecisionLineage:
    return DecisionLineage(**{item.name: f"{item.name}:v1" for item in fields(DecisionLineage)})


def scope() -> ApprovalScope:
    return ApprovalScope(
        InstrumentId("TEST"), StrategyId("benchmark"), "account:test", "test",
        OrderSide.BUY, Quantity(Decimal("2")), lineage(), 1,
    )


def proposal() -> Proposal:
    return Proposal(UUID(int=1), scope(), instant(), instant(60))


def approval() -> Approval:
    return Approval(UUID(int=1), scope(), instant(), instant(60), ApprovalStatus.APPROVED)


def no_trade() -> NoTrade:
    return NoTrade(UUID(int=1), "Insufficient evidence", lineage(), instant())


def test_matching_approval_is_only_a_contract_check() -> None:
    record = approval()
    assert record.matches_current_proposal(proposal(), scope(), instant())
    assert record.matches_current_proposal(proposal(), scope(), instant(59))


@pytest.mark.parametrize("status", [s for s in ApprovalStatus if s is not ApprovalStatus.APPROVED])
def test_nonapproved_status_cannot_match(status: ApprovalStatus) -> None:
    assert not replace(approval(), status=status).matches_current_proposal(proposal(), scope(), instant())


def test_approval_defaults_to_unknown() -> None:
    record = Approval(UUID(int=1), scope(), instant(), instant(60))
    assert record.status is ApprovalStatus.UNKNOWN
    assert not record.matches_current_proposal(proposal(), scope(), instant())


@pytest.mark.parametrize("status", [DecisionStatus.REJECTED, DecisionStatus.EXPIRED, DecisionStatus.INVALIDATED])
def test_invalid_decision_cannot_match(status: DecisionStatus) -> None:
    intent = replace(proposal(), status=status)
    assert not approval().matches_current_proposal(intent, scope(), instant())


@pytest.mark.parametrize("seconds", [-1, 60, 61])
def test_invalid_time_cannot_match(seconds: int) -> None:
    assert not approval().matches_current_proposal(proposal(), scope(), instant(seconds))


def test_proposal_expiry_independently_limits_approval() -> None:
    intent = replace(proposal(), expires_at=instant(10))
    assert not approval().matches_current_proposal(intent, scope(), instant(10))


def test_approval_expiry_independently_limits_proposal() -> None:
    record = replace(approval(), expires_at=instant(10))
    assert not record.matches_current_proposal(proposal(), scope(), instant(10))


def test_future_approval_cannot_match() -> None:
    record = replace(approval(), issued_at=instant(10))
    assert not record.matches_current_proposal(proposal(), scope(), instant())


def test_approval_predating_proposal_cannot_match() -> None:
    intent = replace(proposal(), issued_at=instant(10))
    assert not approval().matches_current_proposal(intent, scope(), instant(20))


def test_approval_cannot_transfer_to_another_decision() -> None:
    intent = replace(proposal(), decision_id=UUID(int=2))
    assert not approval().matches_current_proposal(intent, scope(), instant())


@pytest.mark.parametrize("name,value", [
    ("instrument_id", InstrumentId("OTHER")), ("strategy_id", StrategyId("other")),
    ("account", "account:other"), ("environment", "other"), ("side", OrderSide.SELL),
    ("quantity", Quantity(Decimal("1"))), ("quantity", Quantity(Decimal("3"))),
    ("control_epoch", 2),
])
def test_scope_changes_fail_closed(name: str, value: object) -> None:
    changed = replace(scope(), **{name: value})
    assert not approval().matches_current_proposal(proposal(), changed, instant())
    assert not approval().matches_current_proposal(replace(proposal(), scope=changed), changed, instant())


@pytest.mark.parametrize("name", [item.name for item in fields(DecisionLineage)])
def test_each_lineage_change_invalidates_match(name: str) -> None:
    changed = replace(scope(), lineage=replace(lineage(), **{name: "changed:v2"}))
    assert not approval().matches_current_proposal(proposal(), changed, instant())


def test_no_trade_is_terminal_and_cannot_match_approval() -> None:
    decision = no_trade()
    for seconds in (0, 59, 60, 100):
        assert not approval().matches_current_proposal(decision, scope(), instant(seconds))
    assert decision.status is DecisionStatus.NO_TRADE
    assert decision.reason == "Insufficient evidence"
    with pytest.raises(ValueError):
        replace(decision, status=DecisionStatus.TRADE_PROPOSED)
    with pytest.raises(ValueError, match="NO_TRADE must use"):
        replace(proposal(), status=DecisionStatus.NO_TRADE)


@pytest.mark.parametrize("record", [lineage(), scope(), proposal(), approval(), no_trade()])
def test_contracts_are_immutable(record: object) -> None:
    name = fields(record)[0].name
    with pytest.raises(FrozenInstanceError):
        setattr(record, name, None)


@pytest.mark.parametrize("name", [item.name for item in fields(DecisionLineage)])
@pytest.mark.parametrize("invalid,error", [(None, TypeError), ([], TypeError), ("", ValueError), ("  ", ValueError)])
def test_lineage_rejects_missing_or_mutable_identities(name: str, invalid: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        replace(lineage(), **{name: invalid})


@pytest.mark.parametrize("name,invalid,error", [
    ("instrument_id", "TEST", TypeError), ("strategy_id", "benchmark", TypeError),
    ("instrument_id", InstrumentId(""), ValueError), ("strategy_id", StrategyId(" "), ValueError),
    ("account", None, TypeError), ("account", "", ValueError),
    ("environment", None, TypeError), ("environment", " ", ValueError),
    ("side", "BUY", TypeError), ("quantity", Decimal("2"), TypeError),
    ("lineage", {}, TypeError), ("control_epoch", True, TypeError),
    ("control_epoch", 1.0, TypeError), ("control_epoch", -1, ValueError),
])
def test_scope_rejects_invalid_fields(name: str, invalid: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        replace(scope(), **{name: invalid})


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_scope_rejects_invalid_quantity(value: str) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        replace(scope(), quantity=Quantity(Decimal(value)))


@pytest.mark.parametrize("factory", [proposal, approval])
@pytest.mark.parametrize("name,invalid,error", [
    ("decision_id", "1", TypeError), ("scope", None, TypeError),
    ("status", "APPROVED", TypeError), ("issued_at", None, TypeError),
    ("expires_at", None, TypeError), ("expires_at", instant(), ValueError),
    ("expires_at", instant(-1), ValueError),
])
def test_proposal_and_approval_reject_invalid_fields(factory, name: str, invalid: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        replace(factory(), **{name: invalid})


@pytest.mark.parametrize("name,invalid,error", [
    ("decision_id", "1", TypeError), ("reason", None, TypeError),
    ("reason", "", ValueError), ("reason", "  ", ValueError),
    ("lineage", {}, TypeError), ("occurred_at", None, TypeError),
])
def test_no_trade_rejects_invalid_fields(name: str, invalid: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        replace(no_trade(), **{name: invalid})


@pytest.mark.parametrize("name", ["proposal", "current_scope", "now"])
def test_unknown_check_inputs_rejected_explicitly(name: str) -> None:
    inputs = {"proposal": proposal(), "current_scope": scope(), "now": instant()}
    inputs[name] = None
    with pytest.raises(TypeError):
        approval().matches_current_proposal(**inputs)


@pytest.mark.parametrize("expiring_contract", ["proposal", "approval"])
def test_expiry_across_utc_date_boundary(expiring_contract: str) -> None:
    issued = Timestamp(datetime(2026, 9, 10, 23, 59, 59, tzinfo=UTC))
    expiry = Timestamp(datetime(2026, 9, 11, 0, 0, 1, tzinfo=UTC))
    later = Timestamp(datetime(2026, 9, 11, 0, 1, tzinfo=UTC))
    intent = replace(proposal(), issued_at=issued, expires_at=later)
    record = replace(approval(), issued_at=issued, expires_at=later)
    if expiring_contract == "proposal":
        intent = replace(intent, expires_at=expiry)
    else:
        record = replace(record, expires_at=expiry)
    clock = ControlledClock(issued)

    assert record.matches_current_proposal(intent, scope(), clock.now())
    clock.advance(timedelta(seconds=1))
    assert clock.now().value == datetime(2026, 9, 11, tzinfo=UTC)
    assert record.matches_current_proposal(intent, scope(), clock.now())
    clock.advance(timedelta(seconds=1))
    assert clock.now() == expiry
    assert not record.matches_current_proposal(intent, scope(), clock.now())
    clock.advance(timedelta(seconds=1))
    assert not record.matches_current_proposal(intent, scope(), clock.now())

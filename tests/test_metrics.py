"""Behavioral M1 metrics acceptance, using existing decision fixture builders."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from owner_disposition_evidence import assert_owner_disposition

from test_decision import approval, proposal, no_trade, scope, instant
from automated_trading_bot.domain.clock import TestClock as ControlledClock
from automated_trading_bot.domain.decision import Approval, ApprovalStatus
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.monitoring.metrics import (
    ClockObservation, Metric, MetricsRegistry, MetricsSnapshot,
)
from automated_trading_bot.recovery.replay import ReplayContext


def test_owner_metrics_disposition_is_attributable() -> None:
    assert_owner_disposition(Path(__file__).resolve().parents[1], "M1-SCOPE-2026-09-11-03")


def test_lifecycle_and_existing_no_trade_observation() -> None:
    registry = MetricsRegistry()
    empty = MetricsSnapshot(None, 0, 0)
    assert registry.snapshot() == empty
    decision = no_trade()
    for count in range(1, 4):
        assert registry.observe(Metric.NO_TRADE_DECISION_COUNT, no_trade=decision) is None
        assert registry.snapshot().no_trade_decision_count == count
    assert MetricsRegistry().snapshot() == empty
    assert decision == no_trade()
    assert not approval().matches_current_proposal(decision, scope(), instant())
    with pytest.raises(FrozenInstanceError):
        registry.snapshot().no_trade_decision_count = -1
    assert not hasattr(registry, "reset")


@pytest.mark.parametrize("expiring", ["approval", "proposal", "both"])
def test_count_only_canonical_expiry_rejection(expiring) -> None:
    registry = MetricsRegistry()
    record, intent = approval(), proposal()
    if expiring in ("approval", "both"):
        record = replace(record, expires_at=instant(10))
    if expiring in ("proposal", "both"):
        intent = replace(intent, expires_at=instant(10))
    for seconds, expected, count in [(9, True, 0), (10, False, 1), (11, False, 2)]:
        result = registry.observe(Metric.EXPIRED_DECISION_COUNT, approval=record,
            proposal=intent, current_scope=scope(), now=instant(seconds))
        assert result is record.matches_current_proposal(intent, scope(), instant(seconds))
        assert result is expected
        assert registry.snapshot().expired_decision_count == count


@pytest.mark.parametrize("reason", ["unknown", "scope", "future", "no_trade"])
def test_other_rejection_reasons_do_not_count_as_expiry(reason) -> None:
    registry = MetricsRegistry()
    record, intent, current, now = approval(), proposal(), scope(), instant(100)
    if reason == "unknown":
        record = replace(record, status=ApprovalStatus.UNKNOWN)
    elif reason == "scope":
        current = replace(current, control_epoch=2)
    elif reason == "future":
        now = instant(-1)
    else:
        intent = no_trade()
        with pytest.raises(TypeError, match="invalid metric inputs"):
            registry.observe(Metric.EXPIRED_DECISION_COUNT, approval=record,
                proposal=intent, current_scope=current, now=now)
        assert registry.snapshot().expired_decision_count == 0
        return
    assert registry.observe(Metric.EXPIRED_DECISION_COUNT, approval=record,
        proposal=intent, current_scope=current, now=now) is False
    assert registry.snapshot().expired_decision_count == 0


def test_expiry_observation_delegates_without_inventing_timestamp_logic(monkeypatch) -> None:
    calls = []
    def canonical(self, intent, current, now, *, on_expiry_rejection=None):
        calls.append(1)
        return False  # an unspecified rejection is never inferred to be expiry
    monkeypatch.setattr(Approval, "matches_current_proposal", canonical)
    registry = MetricsRegistry()
    assert registry.observe(Metric.EXPIRED_DECISION_COUNT, approval=approval(),
        proposal=proposal(), current_scope=scope(), now=instant(100)) is False
    assert calls == [1]
    assert registry.snapshot().expired_decision_count == 0


def test_clock_observation_uses_canonical_clock_without_health_claim() -> None:
    registry = MetricsRegistry()
    clock = ControlledClock(instant())
    assert registry.observe(Metric.CLOCK_HEALTH_OBSERVATION, clock=clock) is ClockObservation.AVAILABLE
    clock.advance(timedelta(seconds=1))
    assert registry.observe(Metric.CLOCK_HEALTH_OBSERVATION, clock=clock) is ClockObservation.AVAILABLE
    assert registry.snapshot() == MetricsSnapshot(ClockObservation.AVAILABLE, 0, 0)
    assert not hasattr(registry.snapshot(), "healthy")


@pytest.mark.parametrize("kind", ["unavailable", "wrong_type", "naive", "non_utc"])
def test_clock_negative_observations_are_not_healthy(kind, capsys) -> None:
    class Source:
        def now(self):
            if kind == "unavailable":
                raise RuntimeError("synthetic" + "-private-detail")
            if kind == "wrong_type":
                return object()
            stamp = instant()
            value = datetime(2026, 1, 1)
            if kind == "non_utc":
                value = value.replace(tzinfo=timezone(timedelta(hours=1)))
            object.__setattr__(stamp, "value", value)
            return stamp
    registry = MetricsRegistry()
    expected = ClockObservation.UNAVAILABLE if kind == "unavailable" else ClockObservation.INVALID
    assert registry.observe(Metric.CLOCK_HEALTH_OBSERVATION, clock=Source()) is expected
    assert registry.snapshot() == MetricsSnapshot(expected, 0, 0)
    assert capsys.readouterr() == ("", "")


class Protected:
    def __repr__(self):
        raise AssertionError("protected representation accessed")
    def __str__(self):
        raise AssertionError("protected string accessed")


@pytest.mark.parametrize("field", ["labels", "dimensions", "payload", "account", "increment", "value", "reset"])
def test_arbitrary_fields_and_decrements_rejected_without_mutation(field, capsys) -> None:
    registry = MetricsRegistry()
    registry.observe(Metric.NO_TRADE_DECISION_COUNT, no_trade=no_trade())
    before = registry.snapshot()
    for value in (Protected(), -1, 0, True, {}):
        with pytest.raises(TypeError) as caught:
            registry.observe(Metric.NO_TRADE_DECISION_COUNT, no_trade=no_trade(), **{field: value})
        assert str(caught.value) == "unsupported metric observation"
        assert registry.snapshot() == before
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("metric", list(Metric))
def test_invalid_typed_observations_preserve_state(metric) -> None:
    registry = MetricsRegistry()
    before = registry.snapshot()
    with pytest.raises(TypeError, match="invalid metric inputs"):
        registry.observe(metric, no_trade=Protected())
    assert registry.snapshot() == before


def test_unknown_metric_is_not_created() -> None:
    registry = MetricsRegistry()
    for invalid in ("no_trade_decision_count", Protected(), None, 1):
        with pytest.raises(TypeError, match="unsupported metric observation"):
            registry.observe(invalid)
    assert registry.snapshot() == MetricsSnapshot(None, 0, 0)


def test_metric_failure_cannot_change_rejection_or_execute_financial_effect(monkeypatch, capsys) -> None:
    calls = []
    def failed(self):
        calls.append(1)
        raise RuntimeError("synthetic" + "-private-detail")
    monkeypatch.setattr(MetricsRegistry, "_record_expiry", failed)
    registry = MetricsRegistry()
    assert registry.observe(Metric.EXPIRED_DECISION_COUNT, approval=approval(),
        proposal=proposal(), current_scope=scope(), now=instant(60)) is False
    assert calls == [1]
    assert registry.snapshot().expired_decision_count == 0
    financial = []
    with pytest.raises(PermissionError):
        ReplayContext().invoke_financial_effect(lambda: financial.append(1))
    with pytest.raises(TypeError):
        registry.observe(Metric.NO_TRADE_DECISION_COUNT, no_trade=Protected())
    with pytest.raises(PermissionError):
        ReplayContext().invoke_financial_effect(lambda: financial.append(1))
    assert financial == []
    assert capsys.readouterr() == ("", "")

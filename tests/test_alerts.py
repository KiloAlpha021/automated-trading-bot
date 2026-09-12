"""Executable M1 alert acceptance; no external delivery or control effects."""

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner_disposition_evidence import assert_owner_disposition

from test_decision import instant, approval, proposal, scope
from automated_trading_bot.domain.clock import TestClock as ControlledClock
from automated_trading_bot.domain.operational_mode import OperationalState
from automated_trading_bot.monitoring.alerts import (
    AlertIdentity, AlertSeverity, AlertRecord, evaluate_alerts,
)
from automated_trading_bot.monitoring.diagnostics import (
    Component, DiagnosticCode, DiagnosticContext, DiagnosticLogger, EmissionResult,
)
from automated_trading_bot.monitoring.health import HealthState, evaluate_health
from automated_trading_bot.monitoring.metrics import ClockObservation, Metric, MetricsRegistry
from automated_trading_bot.recovery.replay import ReplayContext
from test_health import observations


def test_owner_alert_contract_is_verbatim_and_attributable() -> None:
    assert_owner_disposition(Path(__file__).resolve().parents[1], "M1-SCOPE-2026-09-11-05")


@pytest.mark.parametrize("condition,identity,severity", [
    (ClockObservation.INVALID, AlertIdentity.CLOCK_CONDITION_ALERT, AlertSeverity.CRITICAL),
    (ClockObservation.UNAVAILABLE, AlertIdentity.CLOCK_CONDITION_ALERT, AlertSeverity.CRITICAL),
    (EmissionResult.SINK_FAILED, AlertIdentity.TELEMETRY_DELIVERY_ALERT, AlertSeverity.WARNING),
    (EmissionResult.CLOCK_FAILED, AlertIdentity.TELEMETRY_DELIVERY_ALERT, AlertSeverity.WARNING),
    (HealthState.DEGRADED, AlertIdentity.HEALTH_STATE_ALERT, AlertSeverity.WARNING),
])
def test_exact_trigger_mapping_and_record_fields(condition, identity, severity) -> None:
    clock = ControlledClock(instant())
    result = evaluate_alerts((condition,), clock=clock)
    assert len(result.active) == 1
    record = result.active[0]
    assert record.identity is identity
    assert record.severity is severity
    assert record.condition is condition
    assert record.evaluated_at == clock.now()
    assert record.active is True
    assert result.cleared == ()
    assert set(result.unresolved) == set(AlertIdentity) - {identity}
    assert {f.name for f in fields(record)} == {"identity", "severity", "evaluated_at", "condition", "active"}
    with pytest.raises(FrozenInstanceError):
        record.active = False


@pytest.mark.parametrize("condition,identity", [
    (ClockObservation.AVAILABLE, AlertIdentity.CLOCK_CONDITION_ALERT),
    (EmissionResult.EMITTED, AlertIdentity.TELEMETRY_DELIVERY_ALERT),
    (HealthState.HEALTHY, AlertIdentity.HEALTH_STATE_ALERT),
])
def test_only_explicit_positive_condition_permits_clearance(condition, identity) -> None:
    result = evaluate_alerts((condition,), clock=ControlledClock(instant()))
    assert result.active == ()
    assert result.cleared == (identity,)
    assert identity not in result.unresolved


def test_unknown_and_missing_evidence_never_clear_prior_alerts() -> None:
    clock = ControlledClock(instant())
    old = evaluate_alerts((ClockObservation.INVALID, EmissionResult.SINK_FAILED, HealthState.DEGRADED), clock=clock)
    assert len(old.active) == 3
    for conditions in ((), (HealthState.UNKNOWN,)):
        result = evaluate_alerts(conditions, clock=clock)
        assert result.active == ()
        assert result.cleared == ()
        assert result.unresolved == tuple(AlertIdentity)
        # A retained prior record cannot be cleared by an unresolved result.
        assert all(record.identity not in result.cleared for record in old.active)
    clock.advance(timedelta(seconds=1))
    current = evaluate_alerts((ClockObservation.AVAILABLE, EmissionResult.EMITTED,
        evaluate_health(observations(clock.now()), clock=clock)), clock=clock)
    assert current.active == () and current.unresolved == ()
    assert current.cleared == tuple(AlertIdentity)
    assert all(record.active for record in old.active)  # historical records are immutable


def test_duplicate_inputs_and_output_order_are_deterministic() -> None:
    conditions = (HealthState.DEGRADED, EmissionResult.SINK_FAILED, ClockObservation.INVALID)
    clock = ControlledClock(instant())
    expected = evaluate_alerts(conditions, clock=clock)
    assert tuple(record.identity for record in expected.active) == tuple(AlertIdentity)
    assert evaluate_alerts(conditions * 4, clock=clock) == expected
    assert evaluate_alerts(tuple(reversed(conditions)), clock=clock) == expected
    clock.advance(timedelta(seconds=61))
    current = evaluate_alerts(conditions, clock=clock)
    assert len(current.active) == 3  # no copied health freshness rule for current conditions
    assert all(record.evaluated_at == clock.now() for record in current.active)


@pytest.mark.parametrize("pair", [
    (ClockObservation.INVALID, ClockObservation.AVAILABLE),
    (EmissionResult.SINK_FAILED, EmissionResult.EMITTED),
    (HealthState.DEGRADED, HealthState.HEALTHY),
    (HealthState.DEGRADED, HealthState.UNKNOWN),
])
def test_conflicting_observations_cannot_choose_a_cleared_state(pair) -> None:
    with pytest.raises(ValueError, match="^conflicting alert conditions$"):
        evaluate_alerts(pair, clock=ControlledClock(instant()))


class Protected:
    def __repr__(self):
        raise AssertionError("protected representation accessed")
    def __str__(self):
        raise AssertionError("protected text accessed")


@pytest.mark.parametrize("field", ["severity", "active", "payload", "exception", "account", "labels"])
def test_overrides_and_arbitrary_fields_rejected(field, capsys) -> None:
    with pytest.raises(TypeError) as error:
        AlertRecord(identity=AlertIdentity.CLOCK_CONDITION_ALERT, evaluated_at=instant(),
            condition=ClockObservation.INVALID, **{field: Protected()})
    assert str(error.value) == "unsupported alert record fields"
    with pytest.raises(TypeError, match="unsupported alert evaluation inputs"):
        evaluate_alerts((), clock=ControlledClock(instant()), **{field: Protected()})
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("field", ["identity", "condition", "evaluated_at"])
def test_protected_values_cannot_enter_record(field, capsys) -> None:
    args = dict(identity=AlertIdentity.CLOCK_CONDITION_ALERT, evaluated_at=instant(), condition=ClockObservation.INVALID)
    args[field] = Protected()
    with pytest.raises(TypeError) as error:
        AlertRecord(**args)
    assert str(error.value) in {"invalid alert identity", "unsupported alert condition", "invalid alert timestamp"}
    assert capsys.readouterr() == ("", "")


def test_unsupported_inputs_and_raw_exceptions_rejected() -> None:
    for invalid in (Protected(), "DEGRADED", RuntimeError("synthetic" + "-private-detail"), None):
        with pytest.raises(TypeError, match="^unsupported alert condition$"):
            evaluate_alerts((invalid,), clock=ControlledClock(instant()))
    with pytest.raises(TypeError, match="invalid alert identity"):
        AlertRecord(identity="CLOCK_CONDITION_ALERT", evaluated_at=instant(), condition=ClockObservation.INVALID)
    with pytest.raises(TypeError, match="unsupported alert evaluation inputs"):
        evaluate_alerts({}, clock=ControlledClock(instant()))
    with pytest.raises(TypeError, match="invalid active alert condition"):
        AlertRecord(identity=AlertIdentity.CLOCK_CONDITION_ALERT, evaluated_at=instant(), condition=HealthState.DEGRADED)
    record = evaluate_alerts((ClockObservation.INVALID,), clock=ControlledClock(instant())).active[0]
    assert repr(record) == "AlertRecord(<restricted>)"


@pytest.mark.parametrize("kind", ["naive", "non_utc", "wrong_type", "exception"])
def test_bad_evaluation_clock_cannot_fabricate_records_or_clearance(kind, capsys) -> None:
    class BadClock:
        def now(self):
            if kind == "exception":
                raise RuntimeError("synthetic" + "-private-detail")
            if kind == "wrong_type":
                return Protected()
            stamp = instant()
            value = datetime(2026, 1, 1)
            if kind == "non_utc":
                value = value.replace(tzinfo=timezone(timedelta(hours=1)))
            object.__setattr__(stamp, "value", value)
            return stamp
    with pytest.raises(ValueError) as error:
        evaluate_alerts((ClockObservation.AVAILABLE,), clock=BadClock())
    assert str(error.value) == "alert evaluation time unavailable"
    assert error.value.__suppress_context__ is True
    assert capsys.readouterr() == ("", "")


def test_existing_canonical_outputs_drive_alerts_without_reinterpretation() -> None:
    clock = ControlledClock(instant())
    class UnavailableClock:
        def now(self):
            raise RuntimeError()
    condition = MetricsRegistry().observe(Metric.CLOCK_HEALTH_OBSERVATION, clock=UnavailableClock())
    def failed_sink(record):
        raise RuntimeError()
    delivery = DiagnosticLogger(clock, failed_sink).emit(DiagnosticCode.OPERATION_FAILED,
        DiagnosticContext(component=Component.DOMAIN))
    health = evaluate_health(observations(clock.now(), condition, delivery), clock=clock)
    result = evaluate_alerts((condition, delivery, health), clock=clock)
    assert len(result.active) == 3
    unknown = evaluate_health((), clock=clock)
    assert evaluate_alerts((unknown,), clock=clock).unresolved == tuple(AlertIdentity)


def test_alerts_have_no_financial_or_operational_authority() -> None:
    operational = OperationalState()
    record, intent = approval(), proposal()
    before = (operational, record, intent)
    effects = []
    for _ in range(3):
        result = evaluate_alerts((ClockObservation.INVALID, HealthState.DEGRADED), clock=ControlledClock(instant()))
        assert result.active[0].severity is AlertSeverity.CRITICAL
        assert record.matches_current_proposal(intent, scope(), instant())
        assert not record.matches_current_proposal(intent, scope(), instant(60))
        assert (operational, record, intent) == before
        with pytest.raises(PermissionError):
            ReplayContext().invoke_financial_effect(lambda: effects.append(1))
        with pytest.raises(TypeError):
            OperationalState(result.active[0])
    assert effects == []

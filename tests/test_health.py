"""Owner-scoped health semantics; no trading or runtime integration."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner_disposition_evidence import assert_owner_disposition

from test_decision import instant, approval, proposal, scope
from automated_trading_bot.domain.clock import TestClock as ControlledClock
from automated_trading_bot.domain.operational_mode import OperationalState
from automated_trading_bot.monitoring.diagnostics import (
    Component, DiagnosticCode, DiagnosticContext, DiagnosticLogger, EmissionResult,
)
from automated_trading_bot.monitoring.health import (
    HealthInput, HealthObservation, HealthState, evaluate_health,
)
from automated_trading_bot.monitoring.metrics import ClockObservation, Metric, MetricsRegistry
from automated_trading_bot.recovery.replay import ReplayContext


def observations(at=None, clock_status=ClockObservation.AVAILABLE, delivery=EmissionResult.EMITTED):
    at = instant() if at is None else at
    return (
        HealthObservation(identity=HealthInput.CLOCK, observed_at=at, status=clock_status),
        HealthObservation(identity=HealthInput.TELEMETRY_DELIVERY, observed_at=at, status=delivery),
    )


def test_owner_health_disposition_is_verbatim_and_attributable() -> None:
    assert_owner_disposition(Path(__file__).resolve().parents[1], "M1-SCOPE-2026-09-11-04")


@pytest.mark.parametrize("retained", [0, 1, 2])
def test_missing_input_is_unknown(retained) -> None:
    supplied = () if retained == 0 else (observations()[retained - 1],)
    assert evaluate_health(supplied, clock=ControlledClock(instant())) is HealthState.UNKNOWN


@pytest.mark.parametrize("age,state", [
    (-0.000001, HealthState.UNKNOWN), (0, HealthState.HEALTHY),
    (60, HealthState.HEALTHY), (60.000001, HealthState.UNKNOWN),
])
@pytest.mark.parametrize("input_index", [0, 1])
def test_each_input_freshness_boundary(age, state, input_index) -> None:
    now = instant(120)
    supplied = list(observations(now))
    original = supplied[input_index]
    supplied[input_index] = HealthObservation(identity=original.identity,
        observed_at=type(now)(now.value - timedelta(seconds=age)), status=original.status)
    assert evaluate_health(tuple(supplied), clock=ControlledClock(now)) is state


@pytest.mark.parametrize("clock_status", list(ClockObservation))
@pytest.mark.parametrize("delivery", list(EmissionResult))
def test_explicit_condition_matrix(clock_status, delivery) -> None:
    result = evaluate_health(observations(clock_status=clock_status, delivery=delivery),
        clock=ControlledClock(instant()))
    expected = HealthState.HEALTHY if (clock_status is ClockObservation.AVAILABLE
        and delivery is EmissionResult.EMITTED) else HealthState.DEGRADED
    assert result is expected


def test_real_monitoring_outcomes_supply_typed_inputs() -> None:
    clock = ControlledClock(instant())
    output = []
    clock_status = MetricsRegistry().observe(Metric.CLOCK_HEALTH_OBSERVATION, clock=clock)
    result = DiagnosticLogger(clock, output.append).emit(DiagnosticCode.COMPONENT_READY,
        DiagnosticContext(component=Component.DOMAIN))
    assert evaluate_health(observations(clock.now(), clock_status, result), clock=clock) is HealthState.HEALTHY
    assert len(output) == 1
    def failed_sink(record):
        raise RuntimeError("synthetic" + "-private-detail")
    result = DiagnosticLogger(clock, failed_sink).emit(DiagnosticCode.OPERATION_FAILED,
        DiagnosticContext(component=Component.DOMAIN))
    assert evaluate_health(observations(clock.now(), clock_status, result), clock=clock) is HealthState.DEGRADED


def test_recovery_requires_complete_fresh_positive_observations() -> None:
    clock = ControlledClock(instant())
    failed = observations(clock_status=ClockObservation.UNAVAILABLE, delivery=EmissionResult.SINK_FAILED)
    assert evaluate_health(failed, clock=clock) is HealthState.DEGRADED
    clock.advance(timedelta(seconds=1))
    new = observations(clock.now())
    assert evaluate_health((new[0], failed[1]), clock=clock) is HealthState.DEGRADED
    assert evaluate_health((failed[0], new[1]), clock=clock) is HealthState.DEGRADED
    assert evaluate_health(new, clock=clock) is HealthState.HEALTHY
    assert evaluate_health(failed, clock=clock) is HealthState.DEGRADED
    clock.advance(timedelta(seconds=61))
    assert evaluate_health(new, clock=clock) is HealthState.UNKNOWN
    refreshed = observations(clock.now())
    assert evaluate_health((refreshed[0], new[1]), clock=clock) is HealthState.UNKNOWN
    assert evaluate_health(refreshed, clock=clock) is HealthState.HEALTHY
    with pytest.raises(FrozenInstanceError):
        failed[0].status = ClockObservation.AVAILABLE


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicates_and_conflicts_are_unknown(conflict) -> None:
    pair = observations()
    duplicate = observations(clock_status=ClockObservation.INVALID)[0] if conflict else pair[0]
    for supplied in [(pair[0], duplicate), (*pair, duplicate), (duplicate, *pair)]:
        assert evaluate_health(supplied, clock=ControlledClock(instant())) is HealthState.UNKNOWN


def test_uncertainty_dominates_failure() -> None:
    old_failure = observations(clock_status=ClockObservation.INVALID)[0]
    fresh_delivery = observations(instant(61), delivery=EmissionResult.SINK_FAILED)[1]
    assert evaluate_health((old_failure, fresh_delivery), clock=ControlledClock(instant(61))) is HealthState.UNKNOWN


class Protected:
    def __repr__(self):
        raise AssertionError("protected representation accessed")
    def __str__(self):
        raise AssertionError("protected string accessed")


@pytest.mark.parametrize("field", ["identity", "observed_at", "status", "payload", "exception", "labels"])
def test_protected_input_rejected_without_leakage(field, capsys) -> None:
    args = dict(identity=HealthInput.CLOCK, observed_at=instant(), status=ClockObservation.AVAILABLE)
    args[field] = Protected()
    with pytest.raises(TypeError) as caught:
        HealthObservation(**args)
    assert str(caught.value) in {"invalid health observation", "unsupported health observation fields"}
    assert capsys.readouterr() == ("", "")


def test_raw_exception_and_cross_input_status_cannot_enter() -> None:
    for value in (RuntimeError("synthetic" + "-private-detail"), "AVAILABLE", EmissionResult.EMITTED):
        with pytest.raises(TypeError, match="^invalid health observation$"):
            HealthObservation(identity=HealthInput.CLOCK, observed_at=instant(), status=value)
    assert repr(observations()[0]) == "HealthObservation(<restricted>)"


def test_unsupported_or_malformed_observations_are_unknown() -> None:
    malformed = object.__new__(HealthObservation)
    pair = observations()
    for supplied in (None, {}, list(pair), (pair[0], Protected()), (pair[0], malformed)):
        assert evaluate_health(supplied, clock=ControlledClock(instant())) is HealthState.UNKNOWN
    object.__setattr__(pair[1], "status", "HEALTHY")
    assert evaluate_health(pair, clock=ControlledClock(instant())) is HealthState.UNKNOWN


@pytest.mark.parametrize("kind", ["naive", "non_utc", "wrong_type", "exception"])
def test_canonical_time_validation_and_clock_failure(kind, capsys) -> None:
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
    assert evaluate_health(observations(), clock=BadClock()) is HealthState.UNKNOWN
    if kind in ("naive", "non_utc", "wrong_type"):
        with pytest.raises(TypeError, match="invalid health observation"):
            HealthObservation(identity=HealthInput.CLOCK, observed_at=BadClock().now(), status=ClockObservation.AVAILABLE)
    assert capsys.readouterr() == ("", "")


def test_health_is_inert_and_separate_from_authority() -> None:
    operational = OperationalState()
    record, intent = approval(), proposal()
    before = (operational, record, intent)
    effects = []
    for _ in range(3):
        result = evaluate_health(observations(), clock=ControlledClock(instant()))
        assert result is HealthState.HEALTHY
        assert not record.matches_current_proposal(intent, scope(), instant(60))
        with pytest.raises(PermissionError):
            ReplayContext().invoke_financial_effect(lambda: effects.append(1))
        with pytest.raises(TypeError):
            OperationalState(result)
    assert effects == []
    assert (operational, record, intent) == before
    assert set(HealthState.__members__) == {"UNKNOWN", "HEALTHY", "DEGRADED"}

def test_owner_telemetry_degradation_disposition_is_verbatim_and_attributable() -> None:
    assert_owner_disposition(Path(__file__).resolve().parents[1], "M1-SCOPE-2026-09-12-01")

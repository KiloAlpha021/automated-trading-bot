"""Behavioral proof of the inert, restricted diagnostic boundary."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automated_trading_bot.domain.clock import TestClock as DeterministicClock
from automated_trading_bot.domain.identifiers import CausationId, CorrelationId, EventId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.monitoring.diagnostics import (
    Component, DiagnosticCode, DiagnosticContext, DiagnosticLogger, EmissionResult,
)
from automated_trading_bot.recovery.replay import ReplayContext


def test_structured_diagnostic_and_clock() -> None:
    clock = DeterministicClock(Timestamp(datetime(2026, 9, 11, 23, 59, 59, tzinfo=UTC)))
    output = []
    logger = DiagnosticLogger(clock, output.append)
    context = DiagnosticContext(component=Component.DOMAIN,
        correlation_id=CorrelationId(UUID(int=1)), causation_id=CausationId(UUID(int=2)),
        event_id=EventId(UUID(int=3)))
    assert logger.emit(DiagnosticCode.INPUT_REJECTED, context) is EmissionResult.EMITTED
    assert json.loads(output[0]) == {
        "kind": "operational_diagnostic", "authoritative": False,
        "code": "INPUT_REJECTED", "severity": "WARNING",
        "timestamp": "2026-09-11T23:59:59+00:00",
        "context": {"component": "domain", "correlation_id": str(UUID(int=1)),
                    "causation_id": str(UUID(int=2)), "event_id": str(UUID(int=3))},
    }
    logger.emit(DiagnosticCode.INPUT_REJECTED, context)
    assert output[0] == output[1]
    clock.advance(timedelta(seconds=1))
    logger.emit(DiagnosticCode.INPUT_REJECTED, context)
    assert json.loads(output[-1])["timestamp"] == "2026-09-12T00:00:00+00:00"
    assert output[0] == json.dumps(json.loads(output[0]), sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("code,severity", [
    (DiagnosticCode.COMPONENT_READY, "INFO"),
    (DiagnosticCode.INPUT_REJECTED, "WARNING"),
    (DiagnosticCode.OPERATION_FAILED, "ERROR"),
])
def test_fixed_codes_and_severities(code, severity) -> None:
    output = []
    logger = DiagnosticLogger(DeterministicClock(Timestamp(datetime(2026, 1, 1, tzinfo=UTC))), output.append)
    assert logger.emit(code, DiagnosticContext(component=Component.EVIDENCE)) is EmissionResult.EMITTED
    assert json.loads(output[0])["severity"] == severity
    assert json.loads(output[0])["code"] == code.value
    assert json.loads(output[0])["context"] == {"component": "evidence"}


class Unrepresentable:
    def __repr__(self):
        raise AssertionError("uncontrolled representation accessed")

    def __str__(self):
        raise AssertionError("uncontrolled string accessed")


@pytest.mark.parametrize("field", ["payload", "message", "exception", "traceback", "severity", "credentials"])
def test_unknown_fields_rejected_without_leakage(field) -> None:
    with pytest.raises(TypeError) as caught:
        DiagnosticContext(component=Component.DOMAIN, **{field: Unrepresentable()})
    assert str(caught.value) == "unsupported diagnostic context fields"


@pytest.mark.parametrize("field", ["component", "correlation_id", "causation_id", "event_id"])
def test_context_types_rejected_without_representation(field) -> None:
    fields = {"component": Component.DOMAIN, field: Unrepresentable()}
    with pytest.raises(TypeError) as caught:
        DiagnosticContext(**fields)
    assert str(caught.value) in {"invalid diagnostic component", "invalid diagnostic identity"}


@pytest.mark.parametrize("field", ["component", "correlation_id", "causation_id", "event_id"])
def test_protected_text_not_accepted(field, capsys) -> None:
    # Synthetic inert text only; never read credentials from the environment.
    value = "protected" + "-value-sentinel"
    with pytest.raises(TypeError) as caught:
        DiagnosticContext(**{"component": Component.DOMAIN, field: value})
    assert value not in str(caught.value)
    assert value not in repr(caught.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("invalid", [None, {}, "INPUT_REJECTED", Unrepresentable()], ids=["none", "dict", "string", "opaque"])
def test_invalid_code_or_payload_never_reaches_clock_or_sink(invalid) -> None:
    logger = DiagnosticLogger(Unrepresentable(), Unrepresentable())
    with pytest.raises(TypeError, match="^invalid diagnostic code$"):
        logger.emit(invalid, DiagnosticContext(component=Component.DOMAIN))
    with pytest.raises(TypeError, match="^invalid diagnostic context$"):
        logger.emit(DiagnosticCode.OPERATION_FAILED, invalid)


def test_representations_and_immutability() -> None:
    context = DiagnosticContext(component=Component.DOMAIN)
    assert repr(context) == "DiagnosticContext(<restricted>)"
    assert repr(DiagnosticLogger(Unrepresentable(), Unrepresentable())) == "DiagnosticLogger(<injected dependencies>)"
    with pytest.raises(FrozenInstanceError):
        context.component = Component.REPLAY
    with pytest.raises(TypeError, match="invalid diagnostic identity"):
        DiagnosticContext(component=Component.DOMAIN, event_id=CorrelationId(UUID(int=1)))


class UnsafeFailure(Exception):
    def __str__(self):
        raise AssertionError("exception text accessed")

    def __repr__(self):
        raise AssertionError("exception representation accessed")


def test_sink_failure_preserves_replay_denial_without_retry_or_leakage(capsys) -> None:
    delivered = []
    financial_calls = []
    def sink(record):
        delivered.append(record)
        raise UnsafeFailure("synthetic" + "-private-detail")
    logger = DiagnosticLogger(DeterministicClock(Timestamp(datetime(2026, 1, 1, tzinfo=UTC))), sink)
    replay = ReplayContext()
    for _ in range(2):
        with pytest.raises(PermissionError) as denied:
            replay.invoke_financial_effect(lambda: financial_calls.append(1))
        before = str(denied.value)
        assert logger.emit(DiagnosticCode.OPERATION_FAILED,
            DiagnosticContext(component=Component.REPLAY)) is EmissionResult.SINK_FAILED
        assert str(denied.value) == before
    assert financial_calls == []
    assert len(delivered) == 2  # exactly one attempt per call, including partial delivery
    assert delivered[0] == delivered[1]
    assert "private-detail" not in delivered[0]
    assert set(json.loads(delivered[0])) == {"kind", "authoritative", "code", "severity", "timestamp", "context"}
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure", ["exception", "wrong_type", "naive"])
def test_invalid_clock_cannot_emit_or_leak(failure, capsys) -> None:
    output = []
    class BadClock:
        def now(self):
            if failure == "exception":
                raise UnsafeFailure()
            if failure == "wrong_type":
                return Unrepresentable()
            stamp = Timestamp(datetime(2026, 1, 1, tzinfo=UTC))
            object.__setattr__(stamp, "value", datetime(2026, 1, 1))
            return stamp
    assert DiagnosticLogger(BadClock(), output.append).emit(DiagnosticCode.OPERATION_FAILED,
        DiagnosticContext(component=Component.DOMAIN)) is EmissionResult.CLOCK_FAILED
    assert output == []
    assert capsys.readouterr() == ("", "")


def test_diagnostic_cannot_supply_canonical_event_state() -> None:
    from automated_trading_bot.domain.event import EventEnvelope
    output = []
    logger = DiagnosticLogger(DeterministicClock(Timestamp(datetime(2026, 1, 1, tzinfo=UTC))), output.append)
    logger.emit(DiagnosticCode.COMPONENT_READY, DiagnosticContext(component=Component.DOMAIN))
    record = json.loads(output[0])
    assert record["kind"] == "operational_diagnostic"
    assert record["authoritative"] is False
    with pytest.raises(TypeError):
        EventEnvelope(**record)
    assert not {"approved", "authorization", "balance", "ledger", "audit", "payload"} & record.keys()

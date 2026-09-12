"""Local non-authoritative diagnostics; see M1-foundation-logging.md."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from uuid import UUID

from automated_trading_bot.domain.clock import Clock
from automated_trading_bot.domain.identifiers import CausationId, CorrelationId, EventId
from automated_trading_bot.domain.timestamp import Timestamp


class DiagnosticCode(Enum):
    COMPONENT_READY = "COMPONENT_READY"  # INFO: local initialization completed
    INPUT_REJECTED = "INPUT_REJECTED"  # WARNING: an existing validation rejected input
    OPERATION_FAILED = "OPERATION_FAILED"  # ERROR: a local operation failed

    @property
    def severity(self) -> str:
        return {
            DiagnosticCode.COMPONENT_READY: "INFO",
            DiagnosticCode.INPUT_REJECTED: "WARNING",
            DiagnosticCode.OPERATION_FAILED: "ERROR",
        }[self]


class Component(Enum):
    DOMAIN = "domain"
    REPLAY = "replay"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class DiagnosticContext:
    component: Component
    correlation_id: CorrelationId | None
    causation_id: CausationId | None
    event_id: EventId | None

    def __init__(
        self, *, component: Component,
        correlation_id: CorrelationId | None = None,
        causation_id: CausationId | None = None,
        event_id: EventId | None = None,
        **unsupported: object,
    ) -> None:
        # Unknown names as well as values may be sensitive; never interpolate them.
        if unsupported:
            raise TypeError("unsupported diagnostic context fields")
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "causation_id", causation_id)
        object.__setattr__(self, "event_id", event_id)
        self._validate()

    def _validate(self) -> None:
        if type(self.component) is not Component:
            raise TypeError("invalid diagnostic component")
        for value, expected in (
            (self.correlation_id, CorrelationId),
            (self.causation_id, CausationId), (self.event_id, EventId),
        ):
            if value is not None and (type(value) is not expected or type(value.value) is not UUID):
                raise TypeError("invalid diagnostic identity")

    def __repr__(self) -> str:
        return "DiagnosticContext(<restricted>)"


class EmissionResult(Enum):
    EMITTED = "EMITTED"
    CLOCK_FAILED = "CLOCK_FAILED"
    SINK_FAILED = "SINK_FAILED"


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticLogger:
    clock: Clock
    sink: Callable[[str], None]

    def __repr__(self) -> str:
        return "DiagnosticLogger(<injected dependencies>)"

    def emit(self, code: DiagnosticCode, context: DiagnosticContext) -> EmissionResult:
        if type(code) is not DiagnosticCode:
            raise TypeError("invalid diagnostic code")
        if type(context) is not DiagnosticContext:
            raise TypeError("invalid diagnostic context")
        context._validate()
        try:
            timestamp = self.clock.now()
            if type(timestamp) is not Timestamp or type(timestamp.value) is not datetime:
                return EmissionResult.CLOCK_FAILED
            if timestamp.value.utcoffset() != timedelta(0):
                return EmissionResult.CLOCK_FAILED
            instant = timestamp.value.astimezone(UTC).isoformat()
        except Exception:
            return EmissionResult.CLOCK_FAILED
        fields = {"component": context.component.value}
        for name in ("correlation_id", "causation_id", "event_id"):
            identity = getattr(context, name)
            if identity is not None:
                fields[name] = str(identity.value)
        record = json.dumps({
            "kind": "operational_diagnostic", "authoritative": False,
            "code": code.value, "severity": code.severity,
            "timestamp": instant, "context": fields,
        }, sort_keys=True, separators=(",", ":"))
        try:
            self.sink(record)
        except Exception:
            # No retry: the sink could have written before failing.
            return EmissionResult.SINK_FAILED
        return EmissionResult.EMITTED

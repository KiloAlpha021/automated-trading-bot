"""Local observational alert evaluation; no transport, history or authority."""

from dataclasses import dataclass
from enum import Enum

from automated_trading_bot.domain.clock import Clock
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.monitoring.diagnostics import EmissionResult
from automated_trading_bot.monitoring.health import HealthState
from automated_trading_bot.monitoring.metrics import ClockObservation


class AlertIdentity(Enum):
    CLOCK_CONDITION_ALERT = "CLOCK_CONDITION_ALERT"
    TELEMETRY_DELIVERY_ALERT = "TELEMETRY_DELIVERY_ALERT"
    HEALTH_STATE_ALERT = "HEALTH_STATE_ALERT"


class AlertSeverity(Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


Condition = ClockObservation | EmissionResult | HealthState


def _identity(condition: Condition) -> AlertIdentity:
    if type(condition) is ClockObservation:
        return AlertIdentity.CLOCK_CONDITION_ALERT
    if type(condition) is EmissionResult:
        return AlertIdentity.TELEMETRY_DELIVERY_ALERT
    if type(condition) is HealthState:
        return AlertIdentity.HEALTH_STATE_ALERT
    raise TypeError("unsupported alert condition")


def _triggered(condition: Condition) -> bool:
    return condition in (
        ClockObservation.INVALID, ClockObservation.UNAVAILABLE,
        EmissionResult.SINK_FAILED, EmissionResult.CLOCK_FAILED, HealthState.DEGRADED,
    )


def _validate_time(value: Timestamp) -> None:
    if type(value) is not Timestamp:
        raise TypeError("invalid alert timestamp")
    try:
        Timestamp(value.value)
    except Exception:
        raise TypeError("invalid alert timestamp") from None


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AlertRecord:
    identity: AlertIdentity
    severity: AlertSeverity
    evaluated_at: Timestamp
    condition: Condition
    active: bool

    def __init__(self, *, identity: AlertIdentity, evaluated_at: Timestamp,
                 condition: Condition, **unsupported: object) -> None:
        if unsupported:
            raise TypeError("unsupported alert record fields")
        if type(identity) is not AlertIdentity:
            raise TypeError("invalid alert identity")
        if _identity(condition) is not identity or not _triggered(condition):
            raise TypeError("invalid active alert condition")
        _validate_time(evaluated_at)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "severity", AlertSeverity.CRITICAL
                           if identity is AlertIdentity.CLOCK_CONDITION_ALERT else AlertSeverity.WARNING)
        object.__setattr__(self, "evaluated_at", evaluated_at)
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "active", True)

    def __repr__(self) -> str:
        return "AlertRecord(<restricted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AlertEvaluation:
    active: tuple[AlertRecord, ...]
    cleared: tuple[AlertIdentity, ...]
    unresolved: tuple[AlertIdentity, ...]


def evaluate_alerts(conditions: tuple[Condition, ...], *, clock: Clock,
                    **unsupported: object) -> AlertEvaluation:
    """Absence/UNKNOWN is unresolved, never positive clearance evidence."""
    if unsupported or type(conditions) is not tuple:
        raise TypeError("unsupported alert evaluation inputs")
    current: dict[AlertIdentity, Condition] = {}
    for condition in conditions:
        identity = _identity(condition)
        if identity in current and current[identity] is not condition:
            raise ValueError("conflicting alert conditions")
        current[identity] = condition
    try:
        now = clock.now()
        _validate_time(now)
    except Exception:
        raise ValueError("alert evaluation time unavailable") from None
    active: list[AlertRecord] = []
    cleared: list[AlertIdentity] = []
    unresolved: list[AlertIdentity] = []
    for identity in AlertIdentity:
        condition = current.get(identity)
        if condition is None or condition is HealthState.UNKNOWN:
            unresolved.append(identity)
        elif _triggered(condition):
            active.append(AlertRecord(identity=identity, evaluated_at=now, condition=condition))
        else:
            cleared.append(identity)
    return AlertEvaluation(tuple(active), tuple(cleared), tuple(unresolved))

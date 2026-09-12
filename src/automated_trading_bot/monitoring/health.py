"""M1 observational health only; no readiness, authority or side-effect ports."""

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from automated_trading_bot.domain.clock import Clock
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.monitoring.diagnostics import EmissionResult
from automated_trading_bot.monitoring.metrics import ClockObservation


class HealthState(Enum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


class HealthInput(Enum):
    CLOCK = "CLOCK"
    TELEMETRY_DELIVERY = "TELEMETRY_DELIVERY"


def _valid_timestamp(value: object) -> bool:
    if type(value) is not Timestamp:
        return False
    try:
        Timestamp(value.value)
    except Exception:
        return False
    return True


@dataclass(frozen=True, slots=True, init=False, repr=False)
class HealthObservation:
    identity: HealthInput
    observed_at: Timestamp
    status: ClockObservation | EmissionResult

    def __init__(
        self, *, identity: HealthInput, observed_at: Timestamp,
        status: ClockObservation | EmissionResult, **unsupported: object,
    ) -> None:
        if unsupported:
            raise TypeError("unsupported health observation fields")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "status", status)
        if not self._valid():
            raise TypeError("invalid health observation")

    def _valid(self) -> bool:
        try:
            if type(self.identity) is not HealthInput or not _valid_timestamp(self.observed_at):
                return False
            expected = ClockObservation if self.identity is HealthInput.CLOCK else EmissionResult
            return type(self.status) is expected
        except Exception:
            return False

    def __repr__(self) -> str:
        return "HealthObservation(<restricted>)"


def evaluate_health(
    observations: tuple[HealthObservation, ...], *, clock: Clock,
) -> HealthState:
    """Compute only from a complete fresh set. Never grant trading permission."""
    if type(observations) is not tuple or len(observations) != 2:
        return HealthState.UNKNOWN
    seen: set[HealthInput] = set()
    for observation in observations:
        if type(observation) is not HealthObservation or not observation._valid():
            return HealthState.UNKNOWN
        if observation.identity in seen:
            return HealthState.UNKNOWN
        seen.add(observation.identity)
    try:
        now = clock.now()
        if not _valid_timestamp(now):
            return HealthState.UNKNOWN
        for observation in observations:
            age = now.value - observation.observed_at.value
            if not timedelta(0) <= age <= timedelta(seconds=60):
                return HealthState.UNKNOWN
    except Exception:
        return HealthState.UNKNOWN
    if all(observation.status in (ClockObservation.AVAILABLE, EmissionResult.EMITTED)
           for observation in observations):
        return HealthState.HEALTHY
    return HealthState.DEGRADED

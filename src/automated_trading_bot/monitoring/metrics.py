"""Process-local observational metrics. No financial authority or transport."""

from dataclasses import dataclass
from enum import Enum

from automated_trading_bot.domain.clock import Clock
from automated_trading_bot.domain.decision import Approval, ApprovalScope, NoTrade, Proposal
from automated_trading_bot.domain.timestamp import Timestamp


class Metric(Enum):
    CLOCK_HEALTH_OBSERVATION = "clock_health_observation"
    EXPIRED_DECISION_COUNT = "expired_decision_count"
    NO_TRADE_DECISION_COUNT = "no_trade_decision_count"


class ClockObservation(Enum):
    AVAILABLE = "AVAILABLE"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    clock_health_observation: ClockObservation | None
    expired_decision_count: int
    no_trade_decision_count: int


class MetricsRegistry:
    __slots__ = ("_clock", "_expired", "_no_trade")

    def __init__(self) -> None:
        self._clock: ClockObservation | None = None
        self._expired = 0
        self._no_trade = 0

    def snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(self._clock, self._expired, self._no_trade)

    def _record_expiry(self) -> None:
        self._expired += 1

    def observe(
        self, metric: Metric, *, clock: Clock | None = None,
        approval: Approval | None = None, proposal: Proposal | None = None,
        current_scope: ApprovalScope | None = None, now: Timestamp | None = None,
        no_trade: NoTrade | None = None, **unsupported: object,
    ) -> ClockObservation | bool | None:
        if type(metric) is not Metric or unsupported:
            raise TypeError("unsupported metric observation")
        if metric is Metric.CLOCK_HEALTH_OBSERVATION:
            if any(v is not None for v in (approval, proposal, current_scope, now, no_trade)):
                raise TypeError("invalid metric inputs")
            if clock is None or not callable(getattr(clock, "now", None)):
                raise TypeError("invalid metric inputs")
            try:
                stamp = clock.now()
            except Exception:
                outcome = ClockObservation.UNAVAILABLE
            else:
                try:
                    if type(stamp) is not Timestamp:
                        raise TypeError("invalid timestamp")
                    Timestamp(stamp.value)
                except Exception:
                    outcome = ClockObservation.INVALID
                else:
                    outcome = ClockObservation.AVAILABLE
            self._clock = outcome
            return outcome
        if metric is Metric.EXPIRED_DECISION_COUNT:
            if (clock is not None or no_trade is not None or type(approval) is not Approval
                    or type(proposal) is not Proposal or type(current_scope) is not ApprovalScope
                    or type(now) is not Timestamp):
                raise TypeError("invalid metric inputs")
            return approval.matches_current_proposal(
                proposal, current_scope, now, on_expiry_rejection=self._record_expiry,
            )
        if (any(v is not None for v in (clock, approval, proposal, current_scope, now))
                or type(no_trade) is not NoTrade):
            raise TypeError("invalid metric inputs")
        self._no_trade += 1
        return None

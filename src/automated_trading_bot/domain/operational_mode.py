"""M1.7 operational mode data; no execution or operational authorization gate."""

from dataclasses import dataclass
from enum import StrEnum


class OperationalMode(StrEnum):
    """Canonical vocabulary from Implementation Specification v1.0 section 6.3."""

    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    NO_NEW_ORDERS = "NO_NEW_ORDERS"
    READ_ONLY = "READ_ONLY"
    CANCEL_ONLY = "CANCEL_ONLY"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"
    INSTRUMENT_DISABLED = "INSTRUMENT_DISABLED"
    BROKER_DISABLED = "BROKER_DISABLED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class OperationalState:
    """Start closed; retain restrictive mode data without granting any authority.

    M1.7 has no independent operational authorization/reconciliation gate.
    Consequently NORMAL and DEGRADED cannot be selected in this kernel slice.
    Approval metadata, saved state and configuration cannot activate this model.
    Restrictive labels do not authorize cancellation, de-risking or activity
    outside a disabled scope; those require later independent controls.
    No mode provides a route to financial side effects.
    """

    mode: OperationalMode = OperationalMode.NO_NEW_ORDERS

    def __post_init__(self) -> None:
        if not isinstance(self.mode, OperationalMode):
            raise TypeError("mode must be an OperationalMode")
        if self.mode in (OperationalMode.NORMAL, OperationalMode.DEGRADED):
            raise ValueError("operational authorization gate is not implemented")

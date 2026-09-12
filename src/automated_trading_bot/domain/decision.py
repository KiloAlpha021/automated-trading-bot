"""M1.6 inert decision contracts; these records never grant execution authority."""

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from enum import StrEnum
from uuid import UUID

from automated_trading_bot.domain.identifiers import InstrumentId, StrategyId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.quantity import Quantity
from automated_trading_bot.domain.timestamp import Timestamp


def _text(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class DecisionLineage:
    """Exact immutable identities; use an explicit identity for inapplicable inputs."""

    data: str
    calendar: str
    corporate_action: str
    strategy: str
    model: str
    feature: str
    config: str
    release: str
    evidence: str

    def __post_init__(self) -> None:
        for item in fields(self):
            _text(getattr(self, item.name), item.name)


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    """Exact action scope, deliberately narrower than a transferable quantity cap."""

    instrument_id: InstrumentId
    strategy_id: StrategyId
    account: str
    environment: str
    side: OrderSide
    quantity: Quantity
    lineage: DecisionLineage
    control_epoch: int

    def __post_init__(self) -> None:
        for name, expected in (
            ("instrument_id", InstrumentId), ("strategy_id", StrategyId),
            ("side", OrderSide), ("quantity", Quantity), ("lineage", DecisionLineage),
        ):
            if not isinstance(getattr(self, name), expected):
                raise TypeError(f"{name} must be a {expected.__name__}")
        _text(self.instrument_id.value, "instrument_id")
        _text(self.strategy_id.value, "strategy_id")
        _text(self.account, "account")
        _text(self.environment, "environment")
        if not self.quantity.value.is_finite() or self.quantity.value <= 0:
            raise ValueError("quantity must be finite and positive")
        if type(self.control_epoch) is not int:
            raise TypeError("control_epoch must be an int")
        if self.control_epoch < 0:
            raise ValueError("control_epoch must be nonnegative")


class DecisionStatus(StrEnum):
    TRADE_PROPOSED = "TRADE_PROPOSED"
    NO_TRADE = "NO_TRADE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


def _window(issued_at: Timestamp, expires_at: Timestamp) -> None:
    if not isinstance(issued_at, Timestamp) or not isinstance(expires_at, Timestamp):
        raise TypeError("issued_at and expires_at must be Timestamp values")
    if expires_at.value <= issued_at.value:
        raise ValueError("expires_at must be after issued_at")


@dataclass(frozen=True, slots=True)
class Proposal:
    """A proposal is evidence of intent, never an order or authorization."""

    decision_id: UUID
    scope: ApprovalScope
    issued_at: Timestamp
    expires_at: Timestamp
    status: DecisionStatus = DecisionStatus.TRADE_PROPOSED

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, UUID):
            raise TypeError("decision_id must be a UUID")
        if not isinstance(self.scope, ApprovalScope):
            raise TypeError("scope must be an ApprovalScope")
        if not isinstance(self.status, DecisionStatus):
            raise TypeError("status must be a DecisionStatus")
        if self.status is DecisionStatus.NO_TRADE:
            raise ValueError("NO_TRADE must use the reasoned NoTrade contract")
        _window(self.issued_at, self.expires_at)


@dataclass(frozen=True, slots=True)
class NoTrade:
    """Terminal, reasoned evidence with no action scope or conversion method."""

    decision_id: UUID
    reason: str
    lineage: DecisionLineage
    occurred_at: Timestamp
    status: DecisionStatus = field(default=DecisionStatus.NO_TRADE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, UUID):
            raise TypeError("decision_id must be a UUID")
        _text(self.reason, "reason")
        if not isinstance(self.lineage, DecisionLineage):
            raise TypeError("lineage must be a DecisionLineage")
        if not isinstance(self.occurred_at, Timestamp):
            raise TypeError("occurred_at must be a Timestamp")


@dataclass(frozen=True, slots=True)
class Approval:
    """Scoped evidence from an independent authority, not an executable permit.

    Matching is a necessary contract check only. Future independent risk,
    capital, operational and OMS gates remain mandatory. The caller supplies
    current scope/evidence and time; no cached record can establish their truth.
    """

    decision_id: UUID
    scope: ApprovalScope
    issued_at: Timestamp
    expires_at: Timestamp
    status: ApprovalStatus = ApprovalStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, UUID):
            raise TypeError("decision_id must be a UUID")
        if not isinstance(self.scope, ApprovalScope):
            raise TypeError("scope must be an ApprovalScope")
        if not isinstance(self.status, ApprovalStatus):
            raise TypeError("status must be an ApprovalStatus")
        _window(self.issued_at, self.expires_at)

    def matches_current_proposal(
        self, proposal: Proposal | NoTrade, current_scope: ApprovalScope,
        now: Timestamp, *, on_expiry_rejection: Callable[[], None] | None = None,
    ) -> bool:
        """Fail closed on nonapproval, stale scope, invalid intent or expired time.

        Validity windows are [issued_at, expires_at). No wall clock is read.
        A historical approval cannot match a different decision identity.
        """
        if not isinstance(proposal, (Proposal, NoTrade)):
            raise TypeError("proposal must be a Proposal or NoTrade")
        if not isinstance(current_scope, ApprovalScope):
            raise TypeError("current_scope must be an ApprovalScope")
        if not isinstance(now, Timestamp):
            raise TypeError("now must be a Timestamp")
        if isinstance(proposal, NoTrade):
            return False
        eligible = (
            self.status is ApprovalStatus.APPROVED
            and proposal.status is DecisionStatus.TRADE_PROPOSED
            and self.decision_id == proposal.decision_id
            and self.scope == proposal.scope == current_scope
            and proposal.issued_at.value <= self.issued_at.value
        )
        if not eligible:
            return False
        matches = (
            self.issued_at.value <= now.value < self.expires_at.value
            and proposal.issued_at.value <= now.value < proposal.expires_at.value
        )
        if (not matches and now.value >= self.issued_at.value
                and now.value >= proposal.issued_at.value and on_expiry_rejection is not None):
            # Expiry is the rejecting predicate; diagnostics cannot change False.
            try:
                on_expiry_rejection()
            except Exception:
                pass
        return matches

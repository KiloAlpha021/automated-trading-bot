"""Stage 5 intent and coherent capital-snapshot contracts.

These immutable values carry evidence into later Stage 5 controls.  They do
not reserve capital, approve risk, grant operational permission, submit an
order, or establish broker/ledger truth.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.portfolio_construction import PortfolioAllocation


class CapitalSnapshotError(ValueError):
    """Raised when mandatory financial evidence is missing or incoherent."""


class ProviderAuthority(StrEnum):
    ENGINEERING_ONLY = "ENGINEERING_ONLY"
    AUTHORITATIVE_FINANCIAL = "AUTHORITATIVE_FINANCIAL"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _amount(value: Decimal, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
    return value


def _digest(domain: str, body: object) -> str:
    return sha256(domain.encode() + b"\0" + canonical_json(body)).hexdigest()


@dataclass(frozen=True, slots=True)
class PortfolioIntent:
    intent_id: str
    portfolio_id: str
    run_scope: str
    decision_lineage: str
    configuration_id: str
    currency: Currency
    proposed_capital: Decimal
    decision_at: Timestamp
    expires_at: Timestamp
    source_provenance: str
    source_allocation_digest: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in (
            "intent_id", "portfolio_id", "run_scope", "decision_lineage",
            "configuration_id", "source_provenance", "source_allocation_digest",
        ):
            _text(getattr(self, name), name)
        if type(self.currency) is not Currency or type(self.decision_at) is not Timestamp or type(self.expires_at) is not Timestamp:
            raise TypeError("currency and timestamps must use canonical domain types")
        _amount(self.proposed_capital, "proposed_capital")
        if self.expires_at.value <= self.decision_at.value:
            raise ValueError("intent expiry must follow its decision time")
        expected = _digest("ATIS-STAGE5-PORTFOLIO-INTENT-V1", self._body())
        if self.content_digest != expected:
            raise ValueError("portfolio intent identity/content mismatch")

    def _body(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id, "portfolio_id": self.portfolio_id,
            "run_scope": self.run_scope, "decision_lineage": self.decision_lineage,
            "configuration_id": self.configuration_id, "currency": self.currency.code,
            "proposed_capital": str(self.proposed_capital),
            "decision_at": self.decision_at.value.isoformat(),
            "expires_at": self.expires_at.value.isoformat(),
            "source_provenance": self.source_provenance,
            "source_allocation_digest": self.source_allocation_digest,
        }

    def require_current(self, *, portfolio_id: str, run_scope: str, at: Timestamp) -> None:
        if portfolio_id != self.portfolio_id or run_scope != self.run_scope:
            raise CapitalSnapshotError("foreign portfolio or run intent")
        if at.value < self.decision_at.value or at.value >= self.expires_at.value:
            raise CapitalSnapshotError("stale or expired portfolio intent")


def adapt_portfolio_intent(
    allocation: PortfolioAllocation, *, intent_id: str, portfolio_id: str,
    run_scope: str, decision_lineage: str, configuration_id: str,
    decision_at: Timestamp, expires_at: Timestamp, source_provenance: str,
) -> PortfolioIntent:
    if type(allocation) is not PortfolioAllocation:
        raise TypeError("allocation must be a protected PortfolioAllocation")
    body = {
        "intent_id": intent_id, "portfolio_id": portfolio_id, "run_scope": run_scope,
        "decision_lineage": decision_lineage, "configuration_id": configuration_id,
        "currency": allocation.currency.code,
        "proposed_capital": str(Decimal(allocation.total_cents) / 100),
        "decision_at": decision_at.value.isoformat(), "expires_at": expires_at.value.isoformat(),
        "source_provenance": source_provenance,
        "source_allocation_digest": allocation.evidence_digest,
    }
    return PortfolioIntent(
        intent_id, portfolio_id, run_scope, decision_lineage, configuration_id,
        allocation.currency, Decimal(allocation.total_cents) / 100, decision_at,
        expires_at, source_provenance, allocation.evidence_digest,
        _digest("ATIS-STAGE5-PORTFOLIO-INTENT-V1", body),
    )


@dataclass(frozen=True, slots=True)
class FinancialComponent:
    kind: str
    portfolio_id: str
    run_scope: str
    state_version: int
    currency: Currency
    amount: Decimal
    source_identity: str
    complete: bool

    def __post_init__(self) -> None:
        for name in ("kind", "portfolio_id", "run_scope", "source_identity"):
            _text(getattr(self, name), name)
        if type(self.state_version) is not int or self.state_version < 0:
            raise ValueError("state_version must be non-negative")
        if type(self.currency) is not Currency:
            raise TypeError("currency must be Currency")
        _amount(self.amount, "amount")


_KINDS = (
    "POSITIONS", "CASH", "ACTIVE_RESERVATIONS", "PENDING_EFFECTS",
    "UNRESOLVED_LIABILITIES", "CORPORATE_ACTIONS",
)


@dataclass(frozen=True, slots=True)
class CapitalSnapshot:
    snapshot_id: str
    portfolio_id: str
    run_scope: str
    state_version: int
    currency: Currency
    components: tuple[FinancialComponent, ...]
    cutoff: Timestamp
    expires_at: Timestamp
    provenance_identity: str
    provider_authority: ProviderAuthority
    content_digest: str

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "portfolio_id", "run_scope", "provenance_identity"):
            _text(getattr(self, name), name)
        if type(self.state_version) is not int or self.state_version < 0:
            raise CapitalSnapshotError("invalid snapshot version")
        if type(self.currency) is not Currency or type(self.cutoff) is not Timestamp or type(self.expires_at) is not Timestamp:
            raise TypeError("snapshot uses canonical currency and timestamps")
        if type(self.provider_authority) is not ProviderAuthority:
            raise TypeError("provider authority classification required")
        if self.expires_at.value <= self.cutoff.value:
            raise CapitalSnapshotError("snapshot expiry must follow cutoff")
        if type(self.components) is not tuple or any(type(item) is not FinancialComponent for item in self.components):
            raise TypeError("components must be FinancialComponent values")
        by_kind = {item.kind: item for item in self.components}
        if len(by_kind) != len(self.components) or set(by_kind) != set(_KINDS):
            raise CapitalSnapshotError("mandatory financial component missing or duplicated")
        for item in self.components:
            if not item.complete:
                raise CapitalSnapshotError(f"mandatory {item.kind} evidence is incomplete")
            if (item.portfolio_id, item.run_scope, item.state_version, item.currency) != (
                self.portfolio_id, self.run_scope, self.state_version, self.currency
            ):
                raise CapitalSnapshotError("split-version, foreign-scope, or currency mismatch")
        expected = _digest("ATIS-STAGE5-CAPITAL-SNAPSHOT-V1", self._body())
        if self.content_digest != expected:
            raise CapitalSnapshotError("snapshot identity/content mismatch")

    def _body(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id, "portfolio_id": self.portfolio_id,
            "run_scope": self.run_scope, "state_version": self.state_version,
            "currency": self.currency.code,
            "components": [
                {"kind": c.kind, "amount": str(c.amount), "source": c.source_identity,
                 "complete": c.complete} for c in self.components
            ],
            "cutoff": self.cutoff.value.isoformat(), "expires_at": self.expires_at.value.isoformat(),
            "provenance_identity": self.provenance_identity,
            "provider_authority": self.provider_authority.value,
        }

    def maximum_new_reservation(self) -> Decimal:
        amounts = {item.kind: item.amount for item in self.components}
        available = amounts["CASH"] - amounts["ACTIVE_RESERVATIONS"] - amounts["UNRESOLVED_LIABILITIES"]
        return max(Decimal("0"), available)

    def require_current(self, *, portfolio_id: str, run_scope: str, at: Timestamp,
                        operational: bool) -> None:
        if (portfolio_id, run_scope) != (self.portfolio_id, self.run_scope):
            raise CapitalSnapshotError("foreign portfolio or run snapshot")
        if at.value < self.cutoff.value or at.value >= self.expires_at.value:
            raise CapitalSnapshotError("stale capital snapshot")
        if operational:
            raise CapitalSnapshotError(
                "snapshot cannot self-certify operational financial authority"
            )


@dataclass(frozen=True, slots=True)
class CapitalSnapshotRequest:
    portfolio_id: str
    run_scope: str
    currency: Currency
    at: Timestamp
    operational: bool = False


class CapitalSnapshotPort(Protocol):
    @property
    def authority(self) -> ProviderAuthority: ...

    @property
    def provider_identity(self) -> str: ...

    @property
    def authority_policy_id(self) -> str: ...

    def snapshot(self, request: CapitalSnapshotRequest) -> CapitalSnapshot: ...


@dataclass(frozen=True, slots=True)
class EngineeringCapitalSnapshotProvider:
    value: CapitalSnapshot

    @property
    def authority(self) -> ProviderAuthority:
        return ProviderAuthority.ENGINEERING_ONLY

    @property
    def provider_identity(self) -> str:
        return self.value.provenance_identity

    @property
    def authority_policy_id(self) -> str:
        return "ENGINEERING_ONLY"

    def snapshot(self, request: CapitalSnapshotRequest) -> CapitalSnapshot:
        if self.value.provider_authority is not self.authority:
            raise CapitalSnapshotError("provider/snapshot authority mismatch")
        if request.operational:
            raise CapitalSnapshotError(
                "engineering provider cannot establish operational truth"
            )
        if request.currency != self.value.currency:
            raise CapitalSnapshotError("incompatible requested currency")
        self.value.require_current(
            portfolio_id=request.portfolio_id, run_scope=request.run_scope,
            at=request.at, operational=request.operational,
        )
        return self.value

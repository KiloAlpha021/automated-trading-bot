from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.research.portfolio_construction import allocate_equal_budgets
from automated_trading_bot.stage5.contracts import (
    CapitalSnapshot,
    CapitalSnapshotError,
    CapitalSnapshotRequest,
    EngineeringCapitalSnapshotProvider,
    FinancialComponent,
    ProviderAuthority,
    _digest,
    adapt_portfolio_intent,
)


NOW = Timestamp(datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
LATER = Timestamp(NOW.value + timedelta(hours=1))
USD = Currency("USD")
KINDS = {
    "POSITIONS": "0",
    "CASH": "10000",
    "ACTIVE_RESERVATIONS": "2000",
    "PENDING_EFFECTS": "0",
    "UNRESOLVED_LIABILITIES": "3000",
    "CORPORATE_ACTIONS": "0",
}


def component(kind: str, *, version: int = 7, portfolio: str = "P1",
              run: str = "R1", currency: Currency = USD, complete: bool = True) -> FinancialComponent:
    return FinancialComponent(kind, portfolio, run, version, currency, Decimal(KINDS[kind]), f"source:{kind}", complete)


def snapshot(*, components: tuple[FinancialComponent, ...] | None = None,
             authority: ProviderAuthority = ProviderAuthority.ENGINEERING_ONLY) -> CapitalSnapshot:
    values = components or tuple(component(kind) for kind in KINDS)
    body = {
        "snapshot_id": "S1", "portfolio_id": "P1", "run_scope": "R1", "state_version": 7,
        "currency": "USD", "components": [
            {"kind": c.kind, "amount": str(c.amount), "source": c.source_identity, "complete": c.complete}
            for c in values
        ], "cutoff": NOW.value.isoformat(), "expires_at": LATER.value.isoformat(),
        "provenance_identity": "prov:1", "provider_authority": authority.value,
    }
    return CapitalSnapshot("S1", "P1", "R1", 7, USD, values, NOW, LATER, "prov:1", authority,
                           _digest("ATIS-STAGE5-CAPITAL-SNAPSHOT-V1", body))


def test_portfolio_intent_adapts_protected_allocation_as_intent_only() -> None:
    allocation = allocate_equal_budgets((), total_capital=Decimal("10000"), currency=USD)
    intent = adapt_portfolio_intent(
        allocation, intent_id="I1", portfolio_id="P1", run_scope="R1",
        decision_lineage="decision:1", configuration_id="config:1",
        decision_at=NOW, expires_at=LATER, source_provenance="source:stage4",
    )
    assert intent.proposed_capital == Decimal("10000")
    intent.require_current(portfolio_id="P1", run_scope="R1", at=NOW)
    assert not hasattr(intent, "reserve")
    assert not hasattr(intent, "approve")


def test_portfolio_intent_rejects_foreign_stale_and_altered_content() -> None:
    allocation = allocate_equal_budgets((), total_capital=Decimal("10000"), currency=USD)
    intent = adapt_portfolio_intent(
        allocation, intent_id="I1", portfolio_id="P1", run_scope="R1",
        decision_lineage="decision:1", configuration_id="config:1",
        decision_at=NOW, expires_at=LATER, source_provenance="source:stage4",
    )
    with pytest.raises(CapitalSnapshotError, match="foreign"):
        intent.require_current(portfolio_id="P2", run_scope="R1", at=NOW)
    with pytest.raises(CapitalSnapshotError, match="stale"):
        intent.require_current(portfolio_id="P1", run_scope="R1", at=LATER)
    with pytest.raises(ValueError, match="identity/content"):
        replace(intent, proposed_capital=Decimal("12000"))


def test_ac_port_01_complete_snapshot_and_independent_exposure_oracle() -> None:
    value = snapshot()
    assert value.maximum_new_reservation() == Decimal("5000")
    assert EngineeringCapitalSnapshotProvider(value).snapshot(
        CapitalSnapshotRequest("P1", "R1", USD, NOW)
    ) is value


@pytest.mark.parametrize("missing", ["POSITIONS", "PENDING_EFFECTS", "UNRESOLVED_LIABILITIES"])
def test_ac_port_02_missing_mandatory_exposure_rejects(missing: str) -> None:
    with pytest.raises(CapitalSnapshotError, match="missing"):
        snapshot(components=tuple(component(kind) for kind in KINDS if kind != missing))


def test_ac_port_03_stale_state_rejects_without_side_effect() -> None:
    provider = EngineeringCapitalSnapshotProvider(snapshot())
    with pytest.raises(CapitalSnapshotError, match="stale"):
        provider.snapshot(CapitalSnapshotRequest("P1", "R1", USD, LATER))
    assert provider.value.state_version == 7


def test_ac_port_04_foreign_portfolio_or_run_rejects() -> None:
    provider = EngineeringCapitalSnapshotProvider(snapshot())
    with pytest.raises(CapitalSnapshotError, match="foreign"):
        provider.snapshot(CapitalSnapshotRequest("P2", "R1", USD, NOW))
    with pytest.raises(CapitalSnapshotError, match="foreign"):
        provider.snapshot(CapitalSnapshotRequest("P1", "R2", USD, NOW))


def test_ac_port_05_wrong_currency_rejects() -> None:
    with pytest.raises(CapitalSnapshotError, match="currency"):
        EngineeringCapitalSnapshotProvider(snapshot()).snapshot(
            CapitalSnapshotRequest("P1", "R1", Currency("GBP"), NOW)
        )


def test_ac_port_06_split_version_rejects() -> None:
    values = tuple(component(kind, version=8 if kind == "CASH" else 7) for kind in KINDS)
    with pytest.raises(CapitalSnapshotError, match="split-version"):
        snapshot(components=values)


def test_ac_port_07_corporate_action_component_is_mandatory_and_bound() -> None:
    value = snapshot()
    action = next(item for item in value.components if item.kind == "CORPORATE_ACTIONS")
    assert action.complete and action.source_identity == "source:CORPORATE_ACTIONS"
    altered = replace(action, amount=Decimal("1"))
    with pytest.raises(CapitalSnapshotError, match="identity/content"):
        replace(value, components=tuple(altered if item is action else item for item in value.components))


def test_engineering_provider_cannot_establish_operational_authority() -> None:
    provider = EngineeringCapitalSnapshotProvider(snapshot())
    with pytest.raises(CapitalSnapshotError, match="operational truth"):
        provider.snapshot(CapitalSnapshotRequest("P1", "R1", USD, NOW, operational=True))
    assert not hasattr(provider, "reserve")
    assert not hasattr(provider.value, "approve")


class AuthoritativeProvider:
    def __init__(self, value: CapitalSnapshot) -> None:
        self.value = value

    @property
    def authority(self) -> ProviderAuthority:
        return ProviderAuthority.AUTHORITATIVE_FINANCIAL

    @property
    def provider_identity(self) -> str:
        return "provider:stage6"

    @property
    def authority_policy_id(self) -> str:
        return "policy:financial:v1"

    def snapshot(self, request: CapitalSnapshotRequest) -> CapitalSnapshot:
        self.value.require_current(
            portfolio_id=request.portfolio_id,
            run_scope=request.run_scope,
            at=request.at,
            operational=request.operational,
        )
        return self.value


def authoritative_snapshot() -> CapitalSnapshot:
    return replace(
        snapshot(authority=ProviderAuthority.AUTHORITATIVE_FINANCIAL),
        provenance_identity="provider:stage6",
        content_digest=_digest(
            "ATIS-STAGE5-CAPITAL-SNAPSHOT-V1",
            {
                "snapshot_id": "S1", "portfolio_id": "P1", "run_scope": "R1",
                "state_version": 7, "currency": "USD",
                "components": [
                    {"kind": c.kind, "amount": str(c.amount), "source": c.source_identity,
                     "complete": c.complete}
                    for c in tuple(component(kind) for kind in KINDS)
                ],
                "cutoff": NOW.value.isoformat(), "expires_at": LATER.value.isoformat(),
                "provenance_identity": "provider:stage6",
                "provider_authority": "AUTHORITATIVE_FINANCIAL",
            },
        ),
    )


def test_c06a_direct_snapshot_cannot_self_certify_authority() -> None:
    forged = snapshot(authority=ProviderAuthority.AUTHORITATIVE_FINANCIAL)
    with pytest.raises(CapitalSnapshotError, match="cannot self-certify"):
        forged.require_current(
            portfolio_id="P1", run_scope="R1", at=NOW, operational=True
        )
    assert not hasattr(forged, "reserve")
    assert not hasattr(forged, "approve")


def test_c06b_rehashed_snapshot_cannot_self_certify_authority() -> None:
    forged = authoritative_snapshot()
    with pytest.raises(CapitalSnapshotError, match="cannot self-certify"):
        forged.require_current(
            portfolio_id="P1", run_scope="R1", at=NOW, operational=True
        )
    assert not hasattr(forged, "reserve")
    assert not hasattr(forged, "approve")

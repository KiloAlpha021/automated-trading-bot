from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from automated_trading_bot.stage5.identity_scope import (
    CanonicalScopeEnvelopeV1,
    IdentityCapabilityV1,
    IdentityScopeError,
    ScopeIdentityV1,
    canonical_bytes,
)
from automated_trading_bot.stage5.authority_service.persistence import AuthorityDatabase
from automated_trading_bot.stage5.authority_service.protocol import AuthorityServiceError


def _scopes() -> tuple[ScopeIdentityV1, ...]:
    return (
        ScopeIdentityV1("ACCOUNT", "ATIS:ACCOUNT", "A-1"),
        ScopeIdentityV1("GLOBAL", "ATIS:GLOBAL", "GLOBAL"),
        ScopeIdentityV1("OPERATION", "ATIS:OPERATION", "OP-1"),
        ScopeIdentityV1("PORTFOLIO", "ATIS:PORTFOLIO", "P-1"),
    )


def test_scope_envelope_has_deterministic_canonical_bytes_and_digest() -> None:
    value = CanonicalScopeEnvelopeV1.create(
        deployment_identity="DEPLOY-1", catalogue_epoch=7,
        operation_class="CAPITAL_RESERVATION", operation_id="OP-1",
        operation_core_digest="core-digest", scopes=reversed(_scopes()),
    )
    assert canonical_bytes(value.body()) == canonical_bytes(value.body())
    assert len(value.digest) == 64
    assert [scope.kind for scope in value.scopes] == [
        "ACCOUNT", "GLOBAL", "OPERATION", "PORTFOLIO"
    ]


def test_missing_account_or_portfolio_fails_closed() -> None:
    with pytest.raises(IdentityScopeError, match="mandatory scope"):
        CanonicalScopeEnvelopeV1.create(
            deployment_identity="DEPLOY-1", catalogue_epoch=1,
            operation_class="CAPITAL_RESERVATION", operation_id="OP-1",
            operation_core_digest="core", scopes=(
                ScopeIdentityV1("GLOBAL", "ATIS:GLOBAL", "GLOBAL"),
                ScopeIdentityV1("OPERATION", "ATIS:OPERATION", "OP-1"),
            ),
        )


def test_account_and_portfolio_capabilities_are_not_substitutable() -> None:
    account = IdentityCapabilityV1(
        "ACCOUNT_IDENTITY_V1", "A-1", "ATIS:ACCOUNT", "DEPLOY-1",
        "ACCOUNT-STATE-AUTHORITY", 4, "ACTIVE",
    )
    portfolio = IdentityCapabilityV1(
        "PORTFOLIO_IDENTITY_V1", "P-1", "ATIS:PORTFOLIO", "DEPLOY-1",
        "PORTFOLIO-STATE-AUTHORITY", 9, "ACTIVE", "A-1",
    )
    assert account.signing_bytes() != portfolio.signing_bytes()
    with pytest.raises(IdentityScopeError, match="parent account"):
        IdentityCapabilityV1(
            "ACCOUNT_IDENTITY_V1", "A-1", "ATIS:ACCOUNT", "DEPLOY-1",
            "ACCOUNT-STATE-AUTHORITY", 4, "ACTIVE", "A-1",
        )


def test_noncanonical_scope_order_is_rejected() -> None:
    with pytest.raises(IdentityScopeError, match="canonically ordered"):
        CanonicalScopeEnvelopeV1(
            "DEPLOY-1", 1, "CAPITAL_RESERVATION", "OP-1", "core",
            tuple(reversed(_scopes())),
        )


def test_identity_nonce_replay_is_durably_rejected(tmp_path: Path) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    # Initialisation owns schema creation; the state is supplied by the existing
    # authority tests, so this test exercises the isolated replay table directly.
    import sqlite3

    with sqlite3.connect(tmp_path / "authority.sqlite3") as connection:
        connection.executescript(
            "CREATE TABLE identity_nonces (capability TEXT NOT NULL, nonce TEXT NOT NULL, "
            "payload_digest TEXT NOT NULL, consumed_at TEXT NOT NULL, "
            "PRIMARY KEY(capability,nonce));"
        )
    now = datetime.now(timezone.utc)
    database.consume_identity_nonce(
        capability="ACCOUNT_IDENTITY_V1", nonce="N-1",
        payload_digest="digest-1", consumed_at=now,
    )
    with pytest.raises(AuthorityServiceError, match="replay"):
        database.consume_identity_nonce(
            capability="ACCOUNT_IDENTITY_V1", nonce="N-1",
            payload_digest="digest-1", consumed_at=now,
        )

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
from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
)


def _scopes() -> tuple[ScopeIdentityV1, ...]:
    return (
        ScopeIdentityV1("ACCOUNT", "ATIS:ACCOUNT", "A-1"),
        ScopeIdentityV1("GLOBAL", "ATIS:GLOBAL", "GLOBAL"),
        ScopeIdentityV1("OPERATION", "ATIS:OPERATION", "OP-1"),
        ScopeIdentityV1("PORTFOLIO", "ATIS:PORTFOLIO", "P-1"),
    )


def test_scope_envelope_has_deterministic_canonical_bytes_and_digest() -> None:
    value = CanonicalScopeEnvelopeV1.create(
        deployment_identity="DEPLOY-1",
        catalogue_epoch=7,
        operation_class="CAPITAL_RESERVATION",
        operation_id="OP-1",
        operation_core_digest="core-digest",
        scopes=reversed(_scopes()),
    )
    assert canonical_bytes(value.body()) == canonical_bytes(value.body())
    assert len(value.digest) == 64
    assert [scope.kind for scope in value.scopes] == [
        "ACCOUNT",
        "GLOBAL",
        "OPERATION",
        "PORTFOLIO",
    ]


def test_missing_account_or_portfolio_fails_closed() -> None:
    with pytest.raises(IdentityScopeError, match="mandatory scope"):
        CanonicalScopeEnvelopeV1.create(
            deployment_identity="DEPLOY-1",
            catalogue_epoch=1,
            operation_class="CAPITAL_RESERVATION",
            operation_id="OP-1",
            operation_core_digest="core",
            scopes=(
                ScopeIdentityV1("GLOBAL", "ATIS:GLOBAL", "GLOBAL"),
                ScopeIdentityV1("OPERATION", "ATIS:OPERATION", "OP-1"),
            ),
        )


def test_account_and_portfolio_capabilities_are_not_substitutable() -> None:
    account = IdentityCapabilityV1(
        "ACCOUNT_IDENTITY_V1",
        "A-1",
        "ATIS:ACCOUNT",
        "DEPLOY-1",
        "ACCOUNT-STATE-AUTHORITY",
        4,
        "ACTIVE",
    )
    portfolio = IdentityCapabilityV1(
        "PORTFOLIO_IDENTITY_V1",
        "P-1",
        "ATIS:PORTFOLIO",
        "DEPLOY-1",
        "PORTFOLIO-STATE-AUTHORITY",
        9,
        "ACTIVE",
        "A-1",
    )
    assert account.signing_bytes() != portfolio.signing_bytes()
    with pytest.raises(IdentityScopeError, match="parent account"):
        IdentityCapabilityV1(
            "ACCOUNT_IDENTITY_V1",
            "A-1",
            "ATIS:ACCOUNT",
            "DEPLOY-1",
            "ACCOUNT-STATE-AUTHORITY",
            4,
            "ACTIVE",
            "A-1",
        )


def test_noncanonical_scope_order_is_rejected() -> None:
    with pytest.raises(IdentityScopeError, match="canonically ordered"):
        CanonicalScopeEnvelopeV1(
            "DEPLOY-1",
            1,
            "CAPITAL_RESERVATION",
            "OP-1",
            "core",
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
        capability="ACCOUNT_IDENTITY_V1",
        nonce="N-1",
        payload_digest="digest-1",
        consumed_at=now,
    )
    with pytest.raises(AuthorityServiceError, match="replay"):
        database.consume_identity_nonce(
            capability="ACCOUNT_IDENTITY_V1",
            nonce="N-1",
            payload_digest="digest-1",
            consumed_at=now,
        )


# Contract013 actual issuing/verification paths with non-persisted CNG test keys.
@pytest.fixture
def u2(tmp_path):
    from automated_trading_bot.stage5.authority_service.cng import (
        CngIdentityCapabilitySigner,
    )
    from automated_trading_bot.stage5.authority_service.service import (
        IdentityCapabilityService,
    )
    from automated_trading_bot.stage5.authority_service.client import (
        IdentityConsumer,
        IdentityKeyPolicy,
    )
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.ipc import (
        IdentityCapabilityHandler,
    )
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    keys = [
        CngIdentityCapabilitySigner.engineering_identity(
            dimension=d, key_id="TEST-" + d
        )
        for d in ("ACCOUNT", "PORTFOLIO")
    ]
    # Isolated test-owned DDL is not authenticated operational bootstrap.
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(tmp_path / "identities.sqlite")) as connection:
        connection.executescript(IdentityLifecycleStore._SCHEMA)
    store = IdentityLifecycleStore(tmp_path / "identities.sqlite")
    policies = tuple(
        IdentityKeyPolicy(
            d,
            "TEST-DEPLOYMENT",
            k.key_id,
            1,
            1,
            k.public_blob(),
            now - timedelta(days=1),
            now + timedelta(days=1),
        )
        for d, k in zip(("ACCOUNT", "PORTFOLIO"), keys)
    )
    consumer = IdentityConsumer(policies, store.current, lambda: now)
    service = IdentityCapabilityService(
        store,
        dict(zip(("ACCOUNT", "PORTFOLIO"), keys)),
        consumer,
        lambda: now,
        "TEST-DEPLOYMENT",
    )
    yield service, consumer, store, keys, now, IdentityCapabilityHandler
    for key in keys:
        key.close()


def u2_command(
    dimension="ACCOUNT",
    value="A",
    epoch=1,
    version=1,
    state="ACTIVE",
    nonce=1,
    parent=None,
):
    import automated_trading_bot.stage5.identity_scope as identities
    from datetime import timedelta

    record = (
        identities.AccountIdentityV1
        if dimension == "ACCOUNT"
        else identities.PortfolioIdentityV1
    )(value, "TEST-DEPLOYMENT", epoch)
    now = datetime.now(timezone.utc)
    command = {
        "command_id": "C-" + str(nonce),
        "record": record.body(),
        "lifecycle_version": version,
        "state": state,
        "valid_from": identities.timestamp(now - timedelta(minutes=1)),
        "valid_until": identities.timestamp(now + timedelta(hours=1)),
        "policy_version": 1,
        "nonce": f"{nonce:064x}",
        "parent": parent,
    }
    return record, command


def u2_issue(u2, dimension="ACCOUNT", **kwargs):
    service, consumer, store, keys, now, handler = u2
    record, command = u2_command(dimension, **kwargs)
    result = handler(service, dimension, management=True)(
        {"action": "issue", "command": command}
    )
    assertion = bytes.fromhex(result["assertion_hex"])
    return record, assertion, command


def test_u2_actual_endpoint_account_portfolio_and_restart(u2):
    import json
    import os
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    account, assertion, command = u2_issue(u2)
    portfolio, passertion, _ = u2_issue(
        u2,
        "PORTFOLIO",
        value="P",
        nonce=2,
        parent={"record": account.body(), "assertion_hex": assertion.hex()},
    )
    assert u2[1].verify(account, assertion)["state"] == "ACTIVE"
    assert (
        u2[1].verify(portfolio, passertion)["parent_account_identity_digest"]
        == account.digest
    )
    before = u2[2].snapshot()
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2[5](u2[0], "ACCOUNT", management=True)(
            {"action": "issue", "command": command}
        )
    assert before == u2[2].snapshot() == IdentityLifecycleStore(u2[2].path).snapshot()
    if os.environ.get("ATIS_U2_EVIDENCE"):
        (Path(os.environ["ATIS_U2_EVIDENCE"]) / "lifecycle-positive.json").write_text(
            json.dumps(before, indent=2)
        )


@pytest.mark.parametrize(
    "mutation", ["signature", "deployment", "key", "nonce", "unknown", "domain"]
)
def test_u2_consumer_rejects_distinct_forgery(u2, mutation):
    import json
    import base64
    from automated_trading_bot.stage5.identity_scope import (
        canonical_bytes,
        AccountIdentityV1,
    )

    record, raw, _ = u2_issue(u2)
    before = u2[2].snapshot()
    value = json.loads(raw)
    if mutation == "signature":
        value["signature_b64"] = base64.b64encode(bytes(64)).decode()
    if mutation == "key":
        value["payload"]["producer_key_id"] = "TEST-PORTFOLIO"
    if mutation == "nonce":
        value["payload"]["nonce"] = "f" * 64
    if mutation == "unknown":
        value["payload"]["extra"] = 1
    if mutation == "domain":
        value["payload"]["schema"] = "portfolio-identity-assertion/v1"
    if mutation == "deployment":
        record = AccountIdentityV1("A", "FOREIGN", 1)
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2[1].verify(record, canonical_bytes(value))
    assert before == u2[2].snapshot()


def test_u2_lifecycle_revocation_parent_and_epoch(u2):
    a, raw, _ = u2_issue(u2)
    parent = {"record": a.body(), "assertion_hex": raw.hex()}
    p, praw, _ = u2_issue(u2, "PORTFOLIO", value="P", nonce=2, parent=parent)
    _, suspended, _ = u2_issue(u2, version=2, state="SUSPENDED", nonce=3)
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2[1].verify(a, raw)
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2[1].verify(p, praw)
    before = u2[2].snapshot()
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2_issue(u2, "PORTFOLIO", value="P2", nonce=4, parent=parent)
    assert before == u2[2].snapshot()
    u2_issue(u2, version=3, state="REVOKED", nonce=5)
    before = u2[2].snapshot()
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2_issue(u2, version=4, nonce=6)
    assert before == u2[2].snapshot()
    # New producer epoch is monotonic; old identity cannot be reactivated.
    u2_issue(u2, epoch=2, nonce=7)
    before = u2[2].snapshot()
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2_issue(u2, epoch=1, nonce=8)
    assert before == u2[2].snapshot()


def test_u2_parent_substitution_and_runtime_issue_denial(u2):
    a, raw, _ = u2_issue(u2)
    p, pr, _ = u2_issue(
        u2,
        "PORTFOLIO",
        value="P",
        nonce=2,
        parent={"record": a.body(), "assertion_hex": raw.hex()},
    )
    other, orr, _ = u2_issue(u2, value="OTHER", nonce=3)
    before = u2[2].snapshot()
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2_issue(
            u2,
            "PORTFOLIO",
            value="P",
            version=2,
            nonce=4,
            parent={"record": other.body(), "assertion_hex": orr.hex()},
        )
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2[5](u2[0], "ACCOUNT", management=False)(
            {"action": "issue", "command": u2_command(nonce=5)[1]}
        )
    assert before == u2[2].snapshot()


def test_u2_concurrent_duplicate_and_failed_persistence(u2):
    from concurrent.futures import ThreadPoolExecutor

    record, command = u2_command()

    def issue(_):
        try:
            return u2[5](u2[0], "ACCOUNT", management=True)(
                {"action": "issue", "command": command}
            )
        except (IdentityScopeError, AuthorityServiceError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(issue, range(2)))
    assert sum(r is not None for r in results) == 1
    assert len(u2[2].snapshot()["assertions"]) == 1


def test_u2_cross_capability_signing(u2):
    a, raw, _ = u2_issue(u2)
    from automated_trading_bot.stage5.identity_scope import (
        assertion_signing_bytes,
        parse_canonical,
    )

    payload = parse_canonical(raw)["payload"]
    before = u2[2].snapshot()
    with pytest.raises(AuthorityServiceError):
        u2[3][1].sign_identity(assertion_signing_bytes(payload))
    with pytest.raises(AuthorityServiceError):
        u2[3][0].sign_identity(b"ATIS-S5-ACCOUNT-IDENTITY-ASSERTION-V1\0{}")
    assert before == u2[2].snapshot()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":1.0}',
        b'{"x": 1}',
        b'{"x":"\\u00e9"}',
        b"\xef\xbb\xbf{}",
        b'{"x":"e\xcc\x81"}',
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
    ],
)
def test_u2_strict_canonical_negative_vectors(raw):
    from automated_trading_bot.stage5.identity_scope import parse_canonical

    with pytest.raises(IdentityScopeError):
        parse_canonical(raw)


def test_u2_utf8_and_operation_core(u2):
    from automated_trading_bot.stage5.identity_scope import (
        OperationCoreV1,
        parse_canonical,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )

    assert canonical_bytes({"x": "\u00e9"}) == b'{"x":"\xc3\xa9"}'
    req = OperationRequest.create(
        operation_id="OP",
        request_type="CAPITAL_RESERVATION",
        scope="legacy",
        amount_minor=1,
        currency="GBP",
        expected_generation=1,
        expected_epoch=1,
        immutable_parameters={"x": "\u00e9"},
    )
    core = OperationCoreV1.from_request(req, "TEST-DEPLOYMENT", "CAPITAL_RESERVATION")
    assert parse_canonical(core.raw)["immutable_business_parameters"] == {"x": "\u00e9"}
    assert (
        req.digest
        == OperationRequest.create(
            operation_id="OP",
            request_type="CAPITAL_RESERVATION",
            scope="legacy",
            amount_minor=1,
            currency="GBP",
            expected_generation=1,
            expected_epoch=1,
            immutable_parameters={"x": "\u00e9"},
        ).digest
    )
    with pytest.raises(IdentityScopeError):
        req.bind_scope(core, b"{}", None)


def test_u2_persistence_failure_rolls_back_without_losing_prior_replay(u2):
    import sqlite3

    u2_issue(u2)
    before = u2[2].snapshot()
    with sqlite3.connect(u2[2].path) as c:
        c.execute(
            "CREATE TRIGGER fail_u2 BEFORE INSERT ON u2_account_commands BEGIN SELECT RAISE(ABORT,'injected persistence failure'); END"
        )
    with pytest.raises(AuthorityServiceError):
        u2_issue(u2, value="FAIL", nonce=2)
    assert before == u2[2].snapshot()
    with sqlite3.connect(u2[2].path) as c:
        c.execute("DROP TRIGGER fail_u2")
    # Prior accepted nonce is still burned. Failed transaction returned no
    # assertion and cannot leave a partially accepted identity.
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        u2_issue(u2)
    assert before == u2[2].snapshot()


def test_u2_concurrent_lifecycle_one_successor(u2):
    from concurrent.futures import ThreadPoolExecutor

    u2_issue(u2)

    def transition(nonce):
        try:
            return u2_issue(u2, version=2, state="SUSPENDED", nonce=nonce)
        except (IdentityScopeError, AuthorityServiceError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        out = list(pool.map(transition, [2, 3]))
    assert sum(v is not None for v in out) == 1
    assert len(u2[2].snapshot()["assertions"]) == 2


def test_u2_identity_frame_strictness_and_peer_barrier(u2, monkeypatch):
    import struct
    import automated_trading_bot.stage5.authority_service.ipc as ipc

    for raw in [
        b'{"action":"read","action":"issue"}',
        b'{"action": "read"}',
        b'{"action":1.0}',
    ]:
        with pytest.raises((IdentityScopeError, AuthorityServiceError)):
            ipc.decode_identity_frame(struct.pack("!I", len(raw)) + raw)
    called = []

    class DeniedPeer:
        def __init__(self, *args):
            pass

        def __enter__(self):
            raise AuthorityServiceError("peer SID denied")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(ipc, "ImpersonatedPipeClient", DeniedPeer)
    monkeypatch.setattr(ipc, "_security_attributes", lambda *args: None)
    monkeypatch.setattr(ipc.win32pipe, "CreateNamedPipe", lambda *args: 1)
    monkeypatch.setattr(ipc.win32pipe, "ConnectNamedPipe", lambda *args: None)
    monkeypatch.setattr(ipc.win32pipe, "DisconnectNamedPipe", lambda *args: None)
    monkeypatch.setattr(ipc.win32file, "CloseHandle", lambda *args: None)
    monkeypatch.setattr(
        ipc.win32file,
        "ReadFile",
        lambda *args: (0, ipc.encode_frame({"action": "issue"})),
    )
    endpoint = ipc.NamedPipeEndpoint(
        pipe_name="TEST-ONLY",
        service_sid="TEST",
        client_sid="ISSUER",
        handler=lambda r: called.append(r),
        decoder=ipc.decode_identity_frame,
    )
    before = u2[2].snapshot()
    with pytest.raises(AuthorityServiceError, match="peer SID denied"):
        endpoint.serve_once()
    assert not called and before == u2[2].snapshot()


def test_u2_missing_wrong_epoch_and_legacy_issuer_rejected(u2):
    service, consumer, store, keys, now, handler = u2
    before = store.snapshot()
    record, command = u2_command()
    for replacement in [
        None,
        {},
        dict(record.body(), producer_epoch=True),
        dict(record.body(), deployment_id="OTHER"),
    ]:
        with pytest.raises((IdentityScopeError, AuthorityServiceError)):
            handler(service, "ACCOUNT", management=True)(
                {"action": "issue", "command": dict(command, record=replacement)}
            )
    assert before == store.snapshot()
    from automated_trading_bot.stage5.authority_service.service import AuthorityService

    # Existing signing-only surface must stay closed even with a signer tuple.
    legacy = AuthorityService(
        service_identity="T",
        deployment_identity="TEST-DEPLOYMENT",
        authority=None,
        signer=None,
        snapshot_source=None,
        clock=None,
        identity_signers=tuple(keys),
    )
    with pytest.raises(
        AuthorityServiceError, match="legacy identity issuance unavailable"
    ):
        legacy.issue_identity(
            IdentityCapabilityV1(
                "ACCOUNT_IDENTITY_V1",
                "A",
                "TEST",
                "TEST-DEPLOYMENT",
                "TEST",
                1,
                "ACTIVE",
            ),
            request_nonce="N",
        )


def test_u2_scope_signature_binding_and_missing_registry(u2):
    import json
    import os
    from datetime import timedelta
    from automated_trading_bot.stage5.identity_scope import (
        IdentityRecordV1,
        OperationCoreV1,
        AuthenticatedScopeEnvelopeV1,
        digest_list,
        timestamp,
        signed_container,
    )
    from automated_trading_bot.stage5.authority_service.cng import CngAdmissionSigner
    from automated_trading_bot.stage5.authority_service.client import (
        IdentityConsumer,
        IdentityKeyPolicy,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )

    account, araw, _ = u2_issue(u2)
    portfolio, praw, _ = u2_issue(
        u2,
        "PORTFOLIO",
        value="P",
        nonce=2,
        parent={"record": account.body(), "assertion_hex": araw.hex()},
    )
    req = OperationRequest.create(
        operation_id="SCOPE-OP",
        request_type="CAPITAL_RESERVATION",
        scope="hint",
        amount_minor=5,
        currency="GBP",
        expected_generation=1,
        expected_epoch=1,
        immutable_parameters={"business": "x"},
    )
    core = OperationCoreV1.from_request(req, "TEST-DEPLOYMENT", "CAPITAL_RESERVATION")
    records = [
        IdentityRecordV1(
            "scope-identity/v1",
            "GLOBAL",
            "test.global",
            "TEST-DEPLOYMENT",
            "GLOBAL",
            "TEST-REGISTRY",
            1,
        ),
        account,
        portfolio,
        IdentityRecordV1(
            "scope-identity/v1",
            "OPERATION",
            "test.operation",
            "TEST-DEPLOYMENT",
            "SCOPE-OP",
            "TEST-REGISTRY",
            1,
        ),
    ]
    closure = tuple(r.digest for r in records)
    # Test-owned registry proof for non-account/portfolio leaves; this is not
    # qualification of U3's absent production registry or database verifier.
    opaque = {"test_registry_assertion": "synthetic-only"}
    payload = {
        "schema": "canonical-scope-envelope/v1",
        "operation_id": req.operation_id,
        "core_digest": core.digest,
        "deployment_id": "TEST-DEPLOYMENT",
        "operation_class": "CAPITAL_RESERVATION",
        "catalogue_epoch": 7,
        "leaf_identities": [r.body() for r in records],
        "identity_assertions": [opaque, json.loads(araw), json.loads(praw), opaque],
        "leaf_set_digest": digest_list(closure),
        "closure_digest": digest_list(closure),
        "issued_at": timestamp(u2[4] - timedelta(minutes=1)),
        "expires_at": timestamp(u2[4] + timedelta(minutes=10)),
        "nonce": "f" * 64,
        "signer_id": "atis.scope-envelope-verifier",
        "signer_key_id": "TEST-SCOPE",
        "signer_key_version": 1,
        "policy_version": 1,
    }

    class TestRegistry:
        def verify_scope(self, p):
            if (
                p["leaf_identities"] != [r.body() for r in records]
                or p["identity_assertions"][0] != opaque
                or p["identity_assertions"][-1] != opaque
            ):
                raise IdentityScopeError("registry evidence mismatch")
            return 7, closure

    with CngAdmissionSigner.engineering_ephemeral(key_id="TEST-SCOPE") as scope_key:
        policy = IdentityKeyPolicy(
            "SCOPE",
            "TEST-DEPLOYMENT",
            "TEST-SCOPE",
            1,
            1,
            scope_key.public_blob(),
            u2[4] - timedelta(days=1),
            u2[4] + timedelta(days=1),
        )
        consumer = IdentityConsumer(
            (*u2[1].policies, policy), u2[2].current, lambda: u2[4], TestRegistry()
        )

        def sign(p):
            return signed_container(
                p,
                scope_key._sign_canonical(
                    b"ATIS-S5-CANONICAL-SCOPE-ENVELOPE-V1\0" + canonical_bytes(p),
                    "synthetic-scope-test",
                ),
            )

        raw = sign(payload)
        bound = req.bind_scope(core, raw, consumer)
        assert (
            bound.immutable_parameters["atis_scope_envelope_digest"]
            == AuthenticatedScopeEnvelopeV1(raw).digest
        )
        assert "atis_scope_envelope_digest" not in req.immutable_parameters
        assert bound.digest != req.digest
        with pytest.raises(IdentityScopeError):
            bound.bind_scope(core, raw, consumer)
        unavailable = IdentityConsumer(
            (*u2[1].policies, policy), u2[2].current, lambda: u2[4]
        )
        with pytest.raises(IdentityScopeError, match="registry unavailable"):
            req.bind_scope(core, raw, unavailable)
        for changes in [
            {"catalogue_epoch": 6},
            {"core_digest": "0" * 64},
            {"closure_digest": "0" * 64},
            {"operation_id": "OTHER"},
            {
                "leaf_identities": [r.body() for r in records[1:]],
                "identity_assertions": payload["identity_assertions"][1:],
            },
        ]:
            with pytest.raises(IdentityScopeError):
                req.bind_scope(core, sign(dict(payload, **changes)), consumer)
        forged = json.loads(raw)
        forged["payload"]["nonce"] = "e" * 64
        with pytest.raises(IdentityScopeError, match="signature"):
            req.bind_scope(core, canonical_bytes(forged), consumer)
        if os.environ.get("ATIS_U2_EVIDENCE"):
            (
                Path(os.environ["ATIS_U2_EVIDENCE"]) / "canonical-vectors.json"
            ).write_text(
                json.dumps(
                    {
                        "unicode_hex": canonical_bytes({"x": "\u00e9"}).hex(),
                        "core_hex": core.raw.hex(),
                        "core_digest": core.digest,
                        "signed_scope_hex": raw.hex(),
                        "scope_digest": AuthenticatedScopeEnvelopeV1(raw).digest,
                        "original_request_digest": req.digest,
                        "bound_request_digest": bound.digest,
                        "registry": "explicit synthetic test provider; U3 not qualified",
                    },
                    indent=2,
                )
            )


def test_u2_consumer_rejects_caller_fabricated_currentness(u2):
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer

    a, raw, _ = u2_issue(u2)
    u2_issue(u2, version=2, state="REVOKED", nonce=2)
    forged_current = IdentityConsumer(
        u2[1].policies,
        lambda record: (canonical_bytes(record.body()), raw),
        lambda: u2[4],
    )
    with pytest.raises(IdentityScopeError, match="atomic current-state provider"):
        forged_current.verify(a, raw)


def test_u2_malformed_endpoint_is_service_contained(u2):
    import struct
    from automated_trading_bot.stage5.authority_service.ipc import decode_identity_frame

    # Host's serve loop catches AuthorityServiceError. Invalid identity input
    # must not escape as an uncaught codec exception and kill that endpoint.
    with pytest.raises(AuthorityServiceError):
        u2[5](u2[0], "ACCOUNT", management=True)({"action": "issue", "command": {}})
    raw = b'{"action":"read","action":"issue"}'
    with pytest.raises(AuthorityServiceError):
        decode_identity_frame(struct.pack("!I", len(raw)) + raw)


def test_u2_service_reopen_preserves_nonce_and_lifecycle(u2):
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer
    from automated_trading_bot.stage5.authority_service.service import (
        IdentityCapabilityService,
    )

    record, raw, command = u2_issue(u2)
    before = u2[2].snapshot()
    reopened = IdentityLifecycleStore(u2[2].path)
    verifier = IdentityConsumer(u2[1].policies, reopened.current, lambda: u2[4])
    service = IdentityCapabilityService(
        reopened,
        dict(zip(("ACCOUNT", "PORTFOLIO"), u2[3])),
        verifier,
        lambda: u2[4],
        "TEST-DEPLOYMENT",
    )
    with pytest.raises((IdentityScopeError, AuthorityServiceError)):
        service.issue_account_identity(command)
    assert verifier.verify(record, raw)["state"] == "ACTIVE"
    assert reopened.snapshot() == before
    # The persistence connection and service are reopened; ephemeral test key
    # handles remain in the test owner. This is not a Windows service restart.


def test_u2_operation_core_preserves_signed_business_integers():
    from automated_trading_bot.stage5.identity_scope import (
        OperationCoreV1,
        parse_canonical,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )

    req = OperationRequest.create(
        operation_id="NEG-BUSINESS",
        request_type="CAPITAL_RESERVATION",
        scope="hint",
        amount_minor=1,
        currency="GBP",
        expected_generation=1,
        expected_epoch=1,
        immutable_parameters={"offset": -1},
    )
    core = OperationCoreV1.from_request(req, "TEST-DEPLOYMENT", "CAPITAL_RESERVATION")
    assert parse_canonical(core.raw)["immutable_business_parameters"] == {"offset": -1}


# Correction030: actual capability handler, issuer and consumer with disposable state.
def _u030_snapshot(store):
    import sqlite3

    with sqlite3.connect(store.path) as connection:
        return "\n".join(connection.iterdump())


def _u030_record(name, before, after):
    import os
    import json

    if os.environ.get("ATIS_U030_EVIDENCE"):
        (Path(os.environ["ATIS_U030_EVIDENCE"]) / (name + ".json")).write_text(
            json.dumps(
                {"before": before, "after": after, "unchanged": before == after},
                indent=2,
            )
        )


@pytest.mark.parametrize(
    "command", [None, [], "bad", 7, {"lifecycle_version": {}}, {"record": []}]
)
def test_u030_malformed_transition_contained(u2, command, request):
    import json
    import struct
    from automated_trading_bot.stage5.authority_service.ipc import decode_identity_frame

    handler = u2[5](u2[0], "ACCOUNT", management=True)
    before = _u030_snapshot(u2[2])
    raw = json.dumps(
        {"action": "transition", "command": command},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    try:
        with pytest.raises(AuthorityServiceError):
            handler(decode_identity_frame(struct.pack("!I", len(raw)) + raw))
    finally:
        after = _u030_snapshot(u2[2])
        _u030_record(request.node.name, before, after)
        assert after == before
    # The same authenticated dispatcher remains usable after rejection.
    record, assertion, _ = u2_issue(u2)
    assert u2[1].verify(record, assertion)["state"] == "ACTIVE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("producer_epoch", 2**63),
        ("producer_epoch", -1),
        ("producer_epoch", True),
        ("lifecycle_version", 2**63),
        ("policy_version", 2**63),
    ],
)
def test_u030_integer_rejected_before_transaction(
    u2, field, value, monkeypatch, request
):
    _, command = u2_command()
    (command["record"] if field == "producer_epoch" else command)[field] = value
    before = _u030_snapshot(u2[2])
    calls = []
    original = u2[2].transaction

    def traced():
        calls.append(True)
        return original()

    monkeypatch.setattr(u2[2], "transaction", traced)
    try:
        with pytest.raises(AuthorityServiceError):
            u2[5](u2[0], "ACCOUNT", management=True)(
                {"action": "issue", "command": command}
            )
    finally:
        after = _u030_snapshot(u2[2])
        _u030_record(request.node.name, before, after)
        assert before == after
    assert calls == []


def test_u030_max_epoch_is_exact_and_restart_safe(u2):
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    record, assertion, command = u2_issue(u2, epoch=2**63 - 1)
    assert u2[1].verify(record, assertion)["state"] == "ACTIVE"
    assert u2[2].snapshot()["assertions"][0][7] == 2**63 - 1
    assert IdentityLifecycleStore(u2[2].path).snapshot() == u2[2].snapshot()
    with pytest.raises(AuthorityServiceError):
        u2[5](u2[0], "ACCOUNT", management=True)(
            {"action": "issue", "command": command}
        )


@pytest.mark.parametrize(
    "damage",
    [
        "u2_account_assertions",
        "u2_account_current",
        "u2_account_commands",
        "all",
        "partial",
        "lost_nonce",
        "lost_current",
    ],
)
def test_u030_missing_state_never_reinitializes(u2, damage, request):
    import sqlite3
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    record, assertion, command = u2_issue(u2)
    u2_issue(u2, version=2, state="REVOKED", nonce=2)
    with sqlite3.connect(u2[2].path) as connection:
        if damage == "lost_nonce":
            connection.execute("DELETE FROM u2_account_commands")
        elif damage == "lost_current":
            connection.execute("DELETE FROM u2_account_current")
        else:
            for table in (
                ("u2_account_current", "u2_account_commands", "u2_account_assertions")
                if damage == "all"
                else (("u2_account_commands",) if damage == "partial" else (damage,))
            ):
                connection.execute("DROP TABLE " + table)
            if damage == "partial":
                connection.execute("CREATE TABLE u2_account_commands (dimension TEXT)")
    before = _u030_snapshot(u2[2])
    try:
        with pytest.raises(AuthorityServiceError):
            IdentityLifecycleStore(u2[2].path)
        with pytest.raises(AuthorityServiceError):
            u2[5](u2[0], "ACCOUNT", management=True)(
                {"action": "issue", "command": command}
            )
        with pytest.raises((AuthorityServiceError, IdentityScopeError)):
            u2[1].verify(record, assertion)
    finally:
        after = _u030_snapshot(u2[2])
        _u030_record(request.node.name, before, after)
        assert after == before


def test_u030_explicit_initialization_only(tmp_path):
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    path = tmp_path / "new.sqlite"
    with pytest.raises(AuthorityServiceError):
        IdentityLifecycleStore(path)
    assert not path.exists()
    # Owner033: unavailable bootstrap authority must deny installation.
    with pytest.raises(AuthorityServiceError, match="BLOCKED_DEPENDENCY"):
        IdentityLifecycleStore.initialize(path)
    assert not path.exists()
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(IdentityLifecycleStore._SCHEMA)
    store = IdentityLifecycleStore(path)
    assert store.snapshot() == {"assertions": [], "current": [], "commands": []}
    assert IdentityLifecycleStore(path).snapshot() == store.snapshot()
    with pytest.raises(AuthorityServiceError):
        IdentityLifecycleStore.initialize(path)


def test_u030_revocation_and_nonce_survive_restart(u2):
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer

    record, assertion, command = u2_issue(u2)
    u2_issue(u2, version=2, state="REVOKED", nonce=2)
    before = u2[2].snapshot()
    reopened = IdentityLifecycleStore(u2[2].path)
    consumer = IdentityConsumer(u2[1].policies, reopened.current, lambda: u2[4])
    with pytest.raises((AuthorityServiceError, IdentityScopeError)):
        consumer.verify(record, assertion)
    with pytest.raises(AuthorityServiceError):
        u2[5](u2[0], "ACCOUNT", management=True)(
            {"action": "issue", "command": command}
        )
    assert before == reopened.snapshot()


def test_u030_host_thread_survives_rejected_transition(u2):
    from threading import Event, Thread
    from types import SimpleNamespace
    from automated_trading_bot.stage5.authority_service.composition import (
        ProtectedAuthorityServiceHost,
    )

    stop = Event()
    reached = Event()
    handler = u2[5](u2[0], "ACCOUNT", management=True)
    record, command = u2_command()
    results = []

    class IdleEndpoint:
        def serve_once(self):
            stop.wait(0.02)

    class IdentityEndpoint:
        calls = 0

        def serve_once(self):
            self.calls += 1
            if self.calls == 1:
                handler({"action": "transition", "command": None})
            else:
                results.append(handler({"action": "issue", "command": command}))
                reached.set()
                stop.set()

    endpoint = IdentityEndpoint()
    host = ProtectedAuthorityServiceHost(
        endpoints=SimpleNamespace(runtime=IdleEndpoint(), management=IdleEndpoint()),
        signer=None,
        ownership=None,
        identity_endpoints=(endpoint,),
    )
    thread = Thread(target=host.serve_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        assert reached.wait(5), (
            "same service thread did not process the next valid request"
        )
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive() and endpoint.calls == 2
    assert (
        u2[1].verify(record, bytes.fromhex(results[0]["assertion_hex"]))["state"]
        == "ACTIVE"
    )
    _u030_record(
        "service-availability",
        {"issued": 0},
        {
            "issued": len(u2[2].snapshot()["assertions"]),
            "thread_stopped_cleanly": not thread.is_alive(),
        },
    )


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize("command", [None, [], "bad", 7])
def test_u030_both_transition_callables_typed(u2, dimension, command):
    before = u2[2].snapshot()
    with pytest.raises((AuthorityServiceError, IdentityScopeError)):
        getattr(u2[0], "transition_" + dimension.lower() + "_identity")(command)
    assert u2[2].snapshot() == before


# Owner033 / U2-LOCAL-INPUT-v1: actual issuer controls accompany codec negatives.
@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize("action", ["issue", "transition", "read"])
@pytest.mark.parametrize("bad", [None, [], 1, True, "bad"])
def test_u033_all_actions_reject_shapes(u2, dimension, action, bad):
    record, raw, _ = u2_issue(u2)
    if dimension == "PORTFOLIO":
        record, raw, _ = u2_issue(
            u2,
            dimension,
            value="P",
            nonce=2,
            parent={"record": record.body(), "assertion_hex": raw.hex()},
        )
    assert u2[1].verify(record, raw)["state"] == "ACTIVE"
    before = u2[2].snapshot()
    key = "record" if action == "read" else "command"
    endpoint = u2[5](u2[0], dimension, management=True)
    with pytest.raises(AuthorityServiceError):
        endpoint({"action": action, key: bad})
    assert u2[2].snapshot() == before
    assert (
        endpoint({"action": "read", "record": record.body()})["assertion_hex"]
        == raw.hex()
    )


@pytest.mark.parametrize("case", ["depth", "collection", "nodes", "string", "bytes"])
def test_u033_input_limits_actual_frame(u2, case, request):
    import json
    import struct
    from automated_trading_bot.stage5.authority_service.ipc import decode_identity_frame
    from automated_trading_bot.stage5.identity_scope import parse_canonical

    record, raw, _ = u2_issue(u2)
    before = _u030_snapshot(u2[2])
    if case == "depth":
        body = b'{"x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
    else:
        value = (
            {"x": list(range(257))}
            if case == "collection"
            else (
                {str(i): [0] * 64 for i in range(64)}
                if case == "nodes"
                else {"x": "x" * 65537}
            )
        )
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if case == "bytes":
            body = b" " * 1048577
    try:
        with pytest.raises(AuthorityServiceError):
            decode_identity_frame(struct.pack("!I", len(body)) + body)
    finally:
        after = _u030_snapshot(u2[2])
        _u030_record(request.node.name, before, after)
        assert before == after
    assert parse_canonical(b'{"x":1}') == {"x": 1}
    assert u2[1].verify(record, raw)["state"] == "ACTIVE"


def test_u033_corrupt_payload_issuer_consumer_symmetry(u2):
    import sqlite3
    import base64

    record, raw, _ = u2_issue(u2)
    assert u2[1].verify(record, raw)["state"] == "ACTIVE"
    corrupt = canonical_bytes(
        {
            "algorithm": "ECDSA_P256_SHA256",
            "payload": None,
            "signature_b64": base64.b64encode(bytes(64)).decode(),
        }
    )
    with sqlite3.connect(u2[2].path) as connection:
        connection.execute("UPDATE u2_account_assertions SET assertion=?", (corrupt,))
    before = _u030_snapshot(u2[2])
    _, command = u2_command(value="OTHER", nonce=2)
    with pytest.raises(AuthorityServiceError):
        u2[5](u2[0], "ACCOUNT", management=True)(
            {"action": "issue", "command": command}
        )
    with pytest.raises(AuthorityServiceError):
        u2[1].verify(record, raw)
    assert before == _u030_snapshot(u2[2])


def test_u033_installation_requires_unavailable_bootstrap_authority(tmp_path):
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    path = tmp_path / "no-authority.sqlite"
    with pytest.raises(AuthorityServiceError, match="bootstrap"):
        IdentityLifecycleStore.initialize(path)
    assert not path.exists()


def test_u033_capability_tables_are_physically_separate(u2):
    import sqlite3

    account, raw, _ = u2_issue(u2)
    u2_issue(
        u2,
        "PORTFOLIO",
        value="P",
        nonce=2,
        parent={"record": account.body(), "assertion_hex": raw.hex()},
    )
    with sqlite3.connect(u2[2].path) as connection:
        names = {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "u2_" + d + "_" + t
            for d in ("account", "portfolio")
            for t in ("assertions", "current", "commands")
        } <= names
        for dimension in ("account", "portfolio"):
            assert (
                connection.execute(
                    "SELECT count(*) FROM u2_" + dimension + "_assertions"
                ).fetchone()[0]
                == 1
            )


def test_u033_same_handler_one_inflight(u2, monkeypatch):
    from threading import Thread, Event

    record, raw, _ = u2_issue(u2)
    endpoint = u2[5](u2[0], "ACCOUNT", management=False)
    entered = Event()
    release = Event()
    results = []
    original = u2[0].read

    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(u2[0], "read", delayed)
    thread = Thread(
        target=lambda: results.append(
            endpoint({"action": "read", "record": record.body()})
        )
    )
    before = u2[2].snapshot()
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(AuthorityServiceError, match="in.flight|busy"):
            endpoint({"action": "read", "record": record.body()})
    finally:
        release.set()
        thread.join(4)
    assert not thread.is_alive() and len(results) == 1
    assert before == u2[2].snapshot()


def _u033_forensic(path):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as connection:
        return "\n".join(connection.iterdump())


def _u033_evidence(name, before, after, **extra):
    import json
    import os
    from pathlib import Path

    destination = os.environ.get("ATIS_U033_EVIDENCE")
    if destination:
        (Path(destination) / (name + ".json")).write_text(
            json.dumps(dict(before=before, after=after, **extra), indent=2)
        )


@pytest.mark.parametrize("case", ["depth", "collection", "nodes", "string", "bytes"])
def test_u033_exact_codec_boundary_with_actual_control(u2, case):
    import json
    import struct
    from automated_trading_bot.stage5.identity_scope import (
        canonical_bytes,
        parse_canonical,
    )
    from automated_trading_bot.stage5.authority_service.ipc import decode_identity_frame

    record, raw, _ = u2_issue(u2)
    assert u2[1].verify(record, raw)["state"] == "ACTIVE"
    before = u2[2].snapshot()
    if case == "depth":
        good = 0
        for _ in range(15):
            good = [good]
        good = {"x": good}
        bad = {"x": good}
    elif case == "collection":
        good = {"x": list(range(256))}
        bad = {"x": list(range(257))}
    elif case == "nodes":
        # root + key + list + 15*(list+256 scalars) + (list+237 scalars) =4096
        good = {"x": [[0] * 256 for _ in range(15)] + [[0] * 237]}
        bad = {"x": [[0] * 256 for _ in range(15)] + [[0] * 238]}
    elif case == "string":
        good = {"x": "a" * 65536}
        bad = {"x": "a" * 65537}
    else:
        good = {"x": ["a" * 65536 for _ in range(15)] + ["a" * 65481]}
        base = canonical_bytes(good)
        good["x"][-1] += "a" * (1048576 - len(base))
        bad = {"x": good["x"][:-1] + [good["x"][-1] + "a"]}
    positive = canonical_bytes(good)
    assert parse_canonical(positive) == good
    if case == "bytes":
        assert len(positive) == 1048576
    negative = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(AuthorityServiceError, match="INPUT_LIMIT"):
        decode_identity_frame(struct.pack(">I", len(negative)) + negative)
    assert u2[2].snapshot() == before
    _u033_evidence(
        "boundary-" + case,
        before,
        u2[2].snapshot(),
        positive_bytes=len(positive),
        negative_bytes=len(negative),
    )


@pytest.mark.parametrize(
    "damage",
    [
        "portfolio_assertions",
        "portfolio_current",
        "portfolio_commands",
        "metadata",
        "blob_integer",
        "counter",
        "lineage",
        "nonce",
        "corrupt_file",
    ],
)
def test_u033_storage_damage_all_boundaries(u2, damage):
    import sqlite3
    from contextlib import closing
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    record, raw, _ = u2_issue(u2)
    assert u2[1].verify(record, raw)["state"] == "ACTIVE"
    if damage == "corrupt_file":
        u2[2].path.write_bytes(b"not a SQLite database")
        before = u2[2].path.read_bytes().hex()
    else:
        with closing(sqlite3.connect(u2[2].path)) as connection:
            if damage.startswith("portfolio_"):
                connection.execute("DROP TABLE u2_" + damage)
            elif damage == "metadata":
                connection.execute("DELETE FROM u2_schema_metadata")
            elif damage == "blob_integer":
                connection.execute(
                    "UPDATE u2_account_assertions SET record=9223372036854775807"
                )
            elif damage == "counter":
                connection.execute("UPDATE u2_account_assertions SET epoch=0")
            elif damage == "lineage":
                connection.execute("UPDATE u2_account_assertions SET version=9")
            elif damage == "nonce":
                connection.execute("DELETE FROM u2_account_commands")
            connection.commit()
        before = _u033_forensic(u2[2].path)
    for call in [
        lambda: IdentityLifecycleStore(u2[2].path),
        lambda: u2_issue(u2, value="B", nonce=2),
        lambda: u2[1].verify(record, raw),
    ]:
        with pytest.raises(AuthorityServiceError, match="STATE_UNAVAILABLE"):
            call()
    after = (
        u2[2].path.read_bytes().hex()
        if damage == "corrupt_file"
        else _u033_forensic(u2[2].path)
    )
    assert after == before
    _u033_evidence("storage-" + damage, before, after)


def test_u033_legacy_nonempty_readonly_preserves_signed_history(u2, tmp_path):
    import sqlite3
    from contextlib import closing
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer
    from automated_trading_bot.stage5.authority_service.service import (
        IdentityCapabilityService,
    )

    record, raw, _ = u2_issue(u2)
    path = tmp_path / "legacy.sqlite"
    with (
        closing(sqlite3.connect(path)) as target,
        closing(sqlite3.connect(u2[2].path)) as source,
    ):
        target.executescript(IdentityLifecycleStore._LEGACY_SCHEMA)
        for suffix in ["assertions", "current", "commands"]:
            rows = source.execute("SELECT * FROM u2_account_" + suffix).fetchall()
            target.executemany(
                "INSERT INTO u2_"
                + suffix
                + " VALUES ("
                + ",".join("?" for _ in rows[0])
                + ")",
                rows,
            )
        target.commit()
    before = _u033_forensic(path)
    store = IdentityLifecycleStore(path)
    consumer = IdentityConsumer(u2[1].policies, store.current, lambda: u2[4])
    service = IdentityCapabilityService(
        store,
        dict(zip(("ACCOUNT", "PORTFOLIO"), u2[3])),
        consumer,
        lambda: u2[4],
        "TEST-DEPLOYMENT",
    )
    assert service.read("ACCOUNT", record.body()) == raw
    assert consumer.verify(record, raw)["state"] == "ACTIVE"
    with pytest.raises(AuthorityServiceError, match="BLOCKED_DEPENDENCY"):
        service.issue_account_identity(u2_command(value="B", nonce=2)[1])
    assert _u033_forensic(path) == before
    _u033_evidence("legacy-preservation", before, _u033_forensic(path))


def test_u033_shared_i01_database_no_bootstrap_mutation(tmp_path):
    import sqlite3
    from contextlib import closing
    from test_stage5_authority_service import _state, AuthorityDatabase
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    path = tmp_path / "protected.sqlite"
    db = AuthorityDatabase(path)
    state = _state()
    db.initialise(state)
    before = _u033_forensic(path)
    with pytest.raises(AuthorityServiceError, match="BLOCKED_DEPENDENCY"):
        IdentityLifecycleStore.initialize(path)
    assert _u033_forensic(path) == before
    # Test-owned DDL demonstrates co-location compatibility only, not deployment authority.
    with closing(sqlite3.connect(path)) as c:
        c.executescript(IdentityLifecycleStore._SCHEMA)
    assert IdentityLifecycleStore(path).snapshot() == {
        "assertions": [],
        "current": [],
        "commands": [],
    }
    assert db.read() == state
    _u033_evidence(
        "i01-colocation", before, _u033_forensic(path), i01_digest=state.state_digest
    )


def test_u033_busy_wait_cleanup_and_recovery(u2):
    import sqlite3
    import time
    from contextlib import closing

    record, raw, _ = u2_issue(u2)
    before = u2[2].snapshot()
    with closing(sqlite3.connect(u2[2].path)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        with pytest.raises(AuthorityServiceError, match="STATE_UNAVAILABLE"):
            u2_issue(u2, value="B", nonce=2)
        elapsed = time.monotonic() - start
        lock.rollback()
    assert elapsed < 5.0
    assert u2[2].snapshot() == before
    new, newraw, _ = u2_issue(u2, value="B", nonce=2)
    assert u2[1].verify(new, newraw)["state"] == "ACTIVE"
    _u033_evidence("busy-wait", before, u2[2].snapshot(), seconds=elapsed)


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_u033_deadline_uncertainty_and_exact_readback(u2, stage, monkeypatch):
    import time

    record, raw, _ = u2_issue(u2)
    before = u2[2].snapshot()
    connect = u2[2]._connect

    class Connection:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, n):
            return getattr(self.c, n)

        def commit(self):
            if stage == "before_commit":
                time.sleep(5.05)
            self.c.commit()
            if stage == "after_commit":
                time.sleep(5.05)

    monkeypatch.setattr(u2[2], "_connect", lambda: Connection(connect()))
    endpoint = u2[5](u2[0], "ACCOUNT", management=True)
    new, command = u2_command(value="B", nonce=2)
    with pytest.raises(AuthorityServiceError, match="DEADLINE_UNCERTAIN"):
        endpoint({"action": "issue", "command": command})
    monkeypatch.setattr(u2[2], "_connect", connect)
    after = u2[2].snapshot()
    # Native commit cannot be cancelled: exact durable result resolves uncertainty.
    assert len(after["assertions"]) == len(before["assertions"]) + 1
    read = endpoint({"action": "read", "record": new.body()})
    assert u2[1].verify(new, bytes.fromhex(read["assertion_hex"]))["state"] == "ACTIVE"
    with pytest.raises(AuthorityServiceError, match="REPLAY_REJECTED"):
        endpoint({"action": "issue", "command": command})
    assert u2[2].snapshot() == after
    _u033_evidence("deadline-" + stage, before, after)


def _u033_crash_child(directory, stage):
    import os
    import json
    from pathlib import Path

    directory = Path(directory)
    generator = u2.__wrapped__(directory)
    fixture = next(generator)
    record, raw, _ = u2_issue(fixture)
    (directory / "control.json").write_text(
        json.dumps(
            {
                "record": record.body(),
                "raw": raw.hex(),
                "now": fixture[4].isoformat(),
                "keys": [
                    {
                        "dimension": p.dimension,
                        "deployment": p.deployment,
                        "key_id": p.key_id,
                        "key_version": p.key_version,
                        "policy_version": p.policy_version,
                        "public_blob": p.public_blob.hex(),
                        "valid_from": p.valid_from.isoformat(),
                        "valid_until": p.valid_until.isoformat(),
                    }
                    for p in fixture[1].policies
                ],
            }
        )
    )
    connect = fixture[2]._connect

    class Connection:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, n):
            return getattr(self.c, n)

        def execute(self, sql, *args):
            if (
                sql.startswith("INSERT INTO u2_account_assertions")
                and stage == "before_write"
            ):
                os._exit(73)
            value = self.c.execute(sql, *args)
            if (
                sql.startswith("INSERT INTO u2_account_assertions")
                and stage == "between_rows"
            ):
                os._exit(73)
            return value

        def commit(self):
            if stage == "commit_entry":
                os._exit(73)
            self.c.commit()
            if stage == "after_commit":
                os._exit(73)

    fixture[2]._connect = lambda: Connection(connect())
    u2_issue(fixture, version=2, nonce=2, state="SUSPENDED")
    os._exit(74)


@pytest.mark.parametrize(
    "stage", ["before_write", "between_rows", "commit_entry", "after_commit"]
)
def test_u033_actual_process_crash_restart(u2, tmp_path, stage):
    import subprocess
    import sys
    import json
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.client import (
        IdentityConsumer,
        IdentityKeyPolicy,
    )
    from automated_trading_bot.stage5.identity_scope import (
        IdentityRecordV1,
    )

    child = tmp_path / "child"
    child.mkdir()
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import runpy,sys; module=runpy.run_path(sys.argv[1]); module['_u033_crash_child'](sys.argv[2],sys.argv[3])",
            str(Path(__file__).resolve()),
            str(child),
            stage,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 73, process.stdout + process.stderr
    control = json.loads((child / "control.json").read_text())
    policies = []
    for original_policy in control["keys"]:
        policy = dict(original_policy)
        policy["public_blob"] = bytes.fromhex(policy["public_blob"])
        for field in ["valid_from", "valid_until"]:
            policy[field] = datetime.fromisoformat(policy[field])
        policies.append(IdentityKeyPolicy(**policy))
    store = IdentityLifecycleStore(child / "identities.sqlite")
    consumer = IdentityConsumer(
        tuple(policies), store.current, lambda: datetime.fromisoformat(control["now"])
    )
    record = IdentityRecordV1.parse(control["record"])
    raw = bytes.fromhex(control["raw"])
    assert consumer.verify_signature(record, raw)["state"] == "ACTIVE"
    state = store.snapshot()
    expected = 2 if stage == "after_commit" else 1
    assert len(state["assertions"]) == len(state["commands"]) == expected
    assert len(state["current"]) == 1
    retained = store.current(record)[1]
    assert consumer.verify_signature(record, retained)["lifecycle_version"] == expected
    if expected == 2:
        with pytest.raises(IdentityScopeError):
            consumer.verify(record, raw)
    else:
        assert consumer.verify(record, raw)["state"] == "ACTIVE"
    assert IdentityLifecycleStore(store.path).snapshot() == state
    _u033_evidence(
        "crash-" + stage,
        control,
        state,
        exit_code=process.returncode,
        scope="commit-entry interruption is not a native mid-commit injection",
    )


@pytest.mark.parametrize("committed", [False, True])
def test_u033_commit_exception_reports_uncertainty(u2, committed, monkeypatch):
    import sqlite3

    record, raw, _ = u2_issue(u2)
    before = u2[2].snapshot()
    connect = u2[2]._connect

    class Connection:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, n):
            return getattr(self.c, n)

        def commit(self):
            if committed:
                self.c.commit()
            raise sqlite3.OperationalError("synthetic uncertain native commit return")

    monkeypatch.setattr(u2[2], "_connect", lambda: Connection(connect()))
    new, command = u2_command(value="B", nonce=2)
    endpoint = u2[5](u2[0], "ACCOUNT", management=True)
    with pytest.raises(AuthorityServiceError, match="DEADLINE_UNCERTAIN"):
        endpoint({"action": "issue", "command": command})
    monkeypatch.setattr(u2[2], "_connect", connect)
    after = u2[2].snapshot()
    assert len(after["assertions"]) == len(before["assertions"]) + int(committed)
    assert len(after["commands"]) == len(before["commands"]) + int(committed)
    if committed:
        returned = endpoint({"action": "read", "record": new.body()})
        assert (
            u2[1].verify(new, bytes.fromhex(returned["assertion_hex"]))["state"]
            == "ACTIVE"
        )
        with pytest.raises(AuthorityServiceError, match="REPLAY_REJECTED"):
            endpoint({"action": "issue", "command": command})
    else:
        assert after == before
    _u033_evidence("commit-exception-" + str(committed), before, after)


def test_u033_unexpected_fault_quarantines_endpoint(u2, monkeypatch):
    record, raw, _ = u2_issue(u2)
    before = u2[2].snapshot()
    endpoint = u2[5](u2[0], "ACCOUNT", management=True)
    assert (
        endpoint({"action": "read", "record": record.body()})["assertion_hex"]
        == raw.hex()
    )
    original = u2[0].read

    def fail(*args):
        raise RuntimeError("synthetic unexpected service fault")

    monkeypatch.setattr(u2[0], "read", fail)
    with pytest.raises(AuthorityServiceError, match="INTERNAL_UNAVAILABLE"):
        endpoint({"action": "read", "record": record.body()})
    monkeypatch.setattr(u2[0], "read", original)
    with pytest.raises(AuthorityServiceError, match="INTERNAL_UNAVAILABLE"):
        endpoint({"action": "read", "record": record.body()})
    assert u2[2].snapshot() == before


def test_u033_connections_closed_after_success_and_rejection(u2, monkeypatch):
    import sqlite3

    record, raw, _ = u2_issue(u2)
    connections = []
    connect = u2[2]._connect

    def capture():
        connection = connect()
        connections.append(connection)
        return connection

    monkeypatch.setattr(u2[2], "_connect", capture)
    endpoint = u2[5](u2[0], "ACCOUNT", management=True)
    before = u2[2].snapshot()
    for _ in range(8):
        assert (
            endpoint({"action": "read", "record": record.body()})["assertion_hex"]
            == raw.hex()
        )
        with pytest.raises(AuthorityServiceError):
            endpoint({"action": "issue", "command": None})
    assert u2[2].snapshot() == before
    assert len(connections) >= 16
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    _u033_evidence("connections", before, before, closed=len(connections))


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize(
    "field", ["producer_epoch", "lifecycle_version", "policy_version"]
)
@pytest.mark.parametrize("bad", [0, -1, True, "1", 2**63])
def test_u033_counter_cross_product_before_transaction(
    u2, dimension, field, bad, monkeypatch
):
    account, araw, _ = u2_issue(u2)
    parent = {"record": account.body(), "assertion_hex": araw.hex()}
    if dimension == "PORTFOLIO":
        control, control_raw, _ = u2_issue(
            u2, dimension, value="P", nonce=2, parent=parent
        )
    else:
        control, control_raw = account, araw
    assert u2[1].verify(control, control_raw)["state"] == "ACTIVE"
    _, command = u2_command(
        dimension,
        value="NEW",
        nonce=3,
        parent=parent if dimension == "PORTFOLIO" else None,
    )
    if field == "producer_epoch":
        command["record"][field] = bad
    else:
        command[field] = bad
    before = u2[2].snapshot()
    entered = []

    def forbidden():
        entered.append(True)
        raise AssertionError("invalid counter reached transaction")

    with monkeypatch.context() as patch:
        patch.setattr(u2[2], "transaction", forbidden)
        with pytest.raises(
            IdentityScopeError,
            match="negative canonical integer" if bad == -1 else "signed-64-bit",
        ):
            u2[0].issue(dimension, command)
    assert not entered and u2[2].snapshot() == before


def test_u033_maximum_typed_counter_vectors(u2):
    from dataclasses import replace
    from automated_trading_bot.stage5.identity_scope import (
        parse_container,
        assertion_signing_bytes,
    )

    record, raw, _ = u2_issue(u2)
    payload, _ = parse_container(raw)
    before = u2[2].snapshot()
    for field in ["lifecycle_version", "policy_version", "producer_key_version"]:
        vector = dict(payload, **{field: 2**63 - 1})
        if field == "lifecycle_version":
            from automated_trading_bot.stage5.identity_scope import assertion_digest

            vector["predecessor_digest"] = assertion_digest(raw)
        assert assertion_signing_bytes(vector)
        with pytest.raises(IdentityScopeError, match="signed-64-bit"):
            assertion_signing_bytes(dict(payload, **{field: 2**63}))
    for field in ["key_version", "policy_version"]:
        assert (
            getattr(replace(u2[1].policies[0], **{field: 2**63 - 1}), field)
            == 2**63 - 1
        )
        with pytest.raises(IdentityScopeError):
            replace(u2[1].policies[0], **{field: 2**63})
    assert u2[2].snapshot() == before
    # Typed vector is not a claim of issuance with a nonexistent huge lineage.


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize(
    "case",
    [
        "assertion_expired",
        "assertion_future",
        "policy_expired",
        "policy_future",
        "policy_version",
    ],
)
def test_u033_independent_validity_policy_oracles(u2, dimension, case):
    from dataclasses import replace
    from datetime import timedelta
    from automated_trading_bot.stage5.identity_scope import (
        parse_container,
        assertion_signing_bytes,
        signed_container,
        timestamp,
    )
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer

    account, araw, _ = u2_issue(u2)
    record, raw = account, araw
    if dimension == "PORTFOLIO":
        record, raw, _ = u2_issue(
            u2,
            dimension,
            value="P",
            nonce=2,
            parent={"record": account.body(), "assertion_hex": araw.hex()},
        )
    assert u2[1].verify_signature(record, raw)["state"] == "ACTIVE"
    before = u2[2].snapshot()
    consumer = u2[1]
    if case.startswith("assertion"):
        payload, _ = parse_container(raw)
        offset = -3 if case == "assertion_expired" else 2
        payload["valid_from"] = timestamp(u2[4] + timedelta(hours=offset))
        payload["valid_until"] = timestamp(u2[4] + timedelta(hours=offset + 1))
        signer = u2[3][0 if dimension == "ACCOUNT" else 1]
        raw = signed_container(
            payload, signer.sign_identity(assertion_signing_bytes(payload))
        )
        expected = "outside validity"
    else:
        policies = []
        for p in consumer.policies:
            if p.dimension == dimension:
                if case == "policy_expired":
                    p = replace(
                        p,
                        valid_from=u2[4] - timedelta(days=2),
                        valid_until=u2[4] - timedelta(days=1),
                    )
                elif case == "policy_future":
                    p = replace(
                        p,
                        valid_from=u2[4] + timedelta(days=1),
                        valid_until=u2[4] + timedelta(days=2),
                    )
                else:
                    p = replace(p, policy_version=2)
            policies.append(p)
        consumer = IdentityConsumer(tuple(policies), u2[2].current, lambda: u2[4])
        expected = "missing/stale"
    with pytest.raises(IdentityScopeError, match=expected):
        consumer.verify_signature(record, raw)
    assert u2[2].snapshot() == before


@pytest.mark.parametrize("first", ["parent", "child"])
def test_u033_parent_transition_race_serializes(u2, first, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    account, araw, _ = u2_issue(u2)
    parent = {"record": account.body(), "assertion_hex": araw.hex()}
    portfolio, praw, _ = u2_issue(
        u2, "PORTFOLIO", value="CONTROL", nonce=2, parent=parent
    )
    assert u2[1].verify(portfolio, praw)["state"] == "ACTIVE"
    before = u2[2].snapshot()
    entered = Event()
    release = Event()
    second_started = Event()
    signer = u2[3][0 if first == "parent" else 1]
    original = signer.sign_identity

    def pause(raw):
        entered.set()
        assert release.wait(3)
        return original(raw)

    monkeypatch.setattr(signer, "sign_identity", pause)

    def parent_change():
        return u2_issue(u2, version=2, state="REVOKED", nonce=3)

    def child_create():
        return u2_issue(u2, "PORTFOLIO", value="RACE", nonce=4, parent=parent)

    first_call, second_call = (
        (parent_change, child_create)
        if first == "parent"
        else (child_create, parent_change)
    )

    def second():
        second_started.set()
        return second_call()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first_call)
        assert entered.wait(3)
        b = pool.submit(second)
        assert second_started.wait(3)
        release.set()
        first_result = a.result(timeout=5)
        if first == "parent":
            with pytest.raises(AuthorityServiceError, match="LIFECYCLE_CONFLICT"):
                b.result(timeout=5)
        else:
            b.result(timeout=5)
            with pytest.raises(IdentityScopeError):
                u2[1].verify(first_result[0], first_result[1])
    after = u2[2].snapshot()
    assert len(after["assertions"]) == len(before["assertions"]) + (
        1 if first == "parent" else 2
    )
    assert len(after["commands"]) == len(before["commands"]) + (
        1 if first == "parent" else 2
    )
    with pytest.raises(IdentityScopeError):
        u2[1].verify(portfolio, praw)
    _u033_evidence(
        "parent-race-" + first, before, after, serialization=first + " committed first"
    )


# Owner038: persisted public digests are consistency evidence, never authenticity.
def _u038_endpoint(env, dimension, action, command):
    import struct
    from automated_trading_bot.stage5.authority_service.ipc import decode_identity_frame
    from automated_trading_bot.stage5.identity_scope import canonical_bytes

    body = canonical_bytes({"action": action, "command": command})
    return env[5](env[0], dimension, management=True)(
        decode_identity_frame(struct.pack("!I", len(body)) + body)
    )


def _u038_rewrite_current(env, dimension, raw, damaged, key_id=None):
    import sqlite3
    from contextlib import closing
    from automated_trading_bot.stage5.identity_scope import assertion_digest

    old_digest, new_digest = assertion_digest(raw), assertion_digest(damaged)
    tables = env[2].tables_for(dimension)
    with closing(sqlite3.connect(env[2].path)) as connection:
        connection.execute(
            "UPDATE " + tables[0] + " SET assertion=?,digest=? WHERE digest=?",
            (damaged, new_digest, old_digest),
        )
        connection.execute(
            "UPDATE " + tables[1] + " SET digest=? WHERE digest=?",
            (new_digest, old_digest),
        )
        connection.execute(
            "UPDATE " + tables[2] + " SET assertion_digest=? WHERE assertion_digest=?",
            (new_digest, old_digest),
        )
        if key_id is not None:
            connection.execute(
                "UPDATE " + tables[2] + " SET key_id=? WHERE assertion_digest=?",
                (key_id, new_digest),
            )
        connection.commit()


def _u038_setup(env, dimension):
    account, account_raw, _ = u2_issue(env)
    parent = {"record": account.body(), "assertion_hex": account_raw.hex()}
    if dimension == "ACCOUNT":
        record, raw = account, account_raw
        parent = None
    else:
        record, raw, _ = u2_issue(env, dimension, value="P", nonce=2, parent=parent)
    assert env[1].verify(record, raw)["state"] == "ACTIVE"
    return record, raw, parent


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize("route", ["issue", "transition", "new_epoch"])
@pytest.mark.parametrize(
    "mutation", ["signature", "wrong_capability_key", "unknown_issuer"]
)
def test_u038_predecessor_authentication_actual_paths(u2, dimension, route, mutation):
    import json
    import base64
    from automated_trading_bot.stage5.identity_scope import (
        canonical_bytes,
        assertion_signing_bytes,
    )
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )

    record, raw, parent = _u038_setup(u2, dimension)
    # Positive control reaches the exact entry/action and successor/epoch branch.
    control, control_raw, _ = u2_issue(
        u2, dimension, value="CONTROL", nonce=10, parent=parent
    )
    _, good = u2_command(
        dimension,
        value=control.value,
        version=1 if route == "new_epoch" else 2,
        epoch=2 if route == "new_epoch" else 1,
        nonce=11,
        parent=parent,
    )
    response = _u038_endpoint(
        u2, dimension, "issue" if route == "new_epoch" else route, good
    )
    from automated_trading_bot.stage5.identity_scope import IdentityRecordV1

    assert (
        u2[1].verify(
            IdentityRecordV1.parse(good["record"]),
            bytes.fromhex(response["assertion_hex"]),
        )["state"]
        == "ACTIVE"
    )
    original = u2[2].snapshot()
    value = json.loads(raw)
    key_id = None
    if mutation == "signature":
        signature = bytearray(base64.b64decode(value["signature_b64"]))
        signature[0] ^= 1
        value["signature_b64"] = base64.b64encode(signature).decode()
    elif mutation == "wrong_capability_key":
        wrong = u2[3][1 if dimension == "ACCOUNT" else 0]
        # Test-only private primitive creates a genuine wrong-key signature.
        value["signature_b64"] = base64.b64encode(
            wrong._sign_canonical(
                assertion_signing_bytes(value["payload"]), "u038-wrong-capability"
            )
        ).decode()
    else:
        key_id = "UNKNOWN-ISSUER"
        value["payload"]["producer_key_id"] = key_id
    damaged = canonical_bytes(value)
    _u038_rewrite_current(u2, dimension, raw, damaged, key_id)
    before = u2[2].snapshot()
    assert IdentityLifecycleStore(u2[2].path).snapshot() == before
    expected = (
        "missing/stale/cross-capability policy"
        if mutation == "unknown_issuer"
        else "invalid identity signature"
    )
    with pytest.raises(IdentityScopeError, match=expected):
        u2[1].verify(record, damaged)
    _, command = u2_command(
        dimension,
        value=record.value,
        version=1 if route == "new_epoch" else 2,
        epoch=2 if route == "new_epoch" else 1,
        nonce=100,
        parent=parent,
    )
    error = None
    try:
        _u038_endpoint(
            u2, dimension, "issue" if route == "new_epoch" else route, command
        )
    except AuthorityServiceError as exc:
        error = str(exc)
    after = u2[2].snapshot()
    _u033_evidence(
        "u038-" + dimension + "-" + route + "-" + mutation,
        before,
        after,
        original=original,
        error=error,
        consumer_rejection=expected,
    )
    assert error is not None and error.startswith("AUTHENTICATION_REJECTED"), error
    assert after == before
    # Rejection does not burn the command/nonce or make healthy identities unavailable.
    healthy, healthy_raw, _ = u2_issue(
        u2, dimension, value="HEALTHY", nonce=200, parent=parent
    )
    assert u2[1].verify(healthy, healthy_raw)["state"] == "ACTIVE"


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
def test_u038_valid_suspension_resume_and_terminal_revocation(u2, dimension):
    record, raw, parent = _u038_setup(u2, dimension)
    for version, state in [(2, "SUSPENDED"), (3, "ACTIVE"), (4, "REVOKED")]:
        _, command = u2_command(
            dimension,
            value=record.value,
            version=version,
            state=state,
            nonce=version + 10,
            parent=parent,
        )
        response = _u038_endpoint(u2, dimension, "transition", command)
        signed = bytes.fromhex(response["assertion_hex"])
        assert u2[1].verify_signature(record, signed)["state"] == state
        if state == "ACTIVE":
            assert u2[1].verify(record, signed)["state"] == state
        else:
            with pytest.raises(IdentityScopeError):
                u2[1].verify(record, signed)
    before = u2[2].snapshot()
    _, command = u2_command(
        dimension, value=record.value, version=5, nonce=20, parent=parent
    )
    with pytest.raises(AuthorityServiceError, match="LIFECYCLE_CONFLICT"):
        _u038_endpoint(u2, dimension, "transition", command)
    assert u2[2].snapshot() == before
    _u033_evidence("u038-revoked-" + dimension, before, u2[2].snapshot())


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
@pytest.mark.parametrize(
    "case", ["missing", "stale_version", "stale_epoch", "wrong_deployment"]
)
def test_u038_predecessor_context_rejects_without_effect(u2, dimension, case):
    record, raw, parent = _u038_setup(u2, dimension)
    # Authenticated current successor is the positive control.
    _, good = u2_command(
        dimension, value=record.value, version=2, nonce=10, parent=parent
    )
    response = _u038_endpoint(u2, dimension, "transition", good)
    assert (
        u2[1].verify(record, bytes.fromhex(response["assertion_hex"]))[
            "lifecycle_version"
        ]
        == 2
    )
    if case == "stale_epoch":
        record, raw, _ = u2_issue(
            u2, dimension, value=record.value, epoch=2, nonce=11, parent=parent
        )
    _, command = u2_command(
        dimension,
        value="MISSING" if case == "missing" else record.value,
        version=2 if case in ("missing", "stale_version") else 3,
        epoch=1,
        nonce=20,
        parent=parent,
    )
    if case == "wrong_deployment":
        command["record"]["deployment_id"] = "FOREIGN"
    before = u2[2].snapshot()
    code = (
        "AUTHENTICATION_REJECTED"
        if case == "wrong_deployment"
        else "LIFECYCLE_CONFLICT"
    )
    with pytest.raises(AuthorityServiceError, match=code):
        _u038_endpoint(u2, dimension, "transition", command)
    assert u2[2].snapshot() == before
    _u033_evidence("u038-context-" + dimension + "-" + case, before, u2[2].snapshot())


def test_u038_portfolio_wrong_authenticated_parent_cannot_advance(u2):
    import json
    import base64
    from automated_trading_bot.stage5.identity_scope import (
        canonical_bytes,
        assertion_signing_bytes,
    )

    record, raw, parent = _u038_setup(u2, "PORTFOLIO")
    control, control_raw, _ = u2_issue(
        u2, "PORTFOLIO", value="CONTROL", nonce=10, parent=parent
    )
    _, control_command = u2_command(
        "PORTFOLIO", value=control.value, version=2, nonce=11, parent=parent
    )
    control_response = _u038_endpoint(u2, "PORTFOLIO", "transition", control_command)
    assert (
        u2[1].verify(control, bytes.fromhex(control_response["assertion_hex"]))[
            "lifecycle_version"
        ]
        == 2
    )
    value = json.loads(raw)
    value["payload"]["parent_account_identity_digest"] = "f" * 64
    value["signature_b64"] = base64.b64encode(
        u2[3][1].sign_identity(assertion_signing_bytes(value["payload"]))
    ).decode()
    damaged = canonical_bytes(value)
    _u038_rewrite_current(u2, "PORTFOLIO", raw, damaged)
    assert (
        u2[1].verify_signature(record, damaged)["parent_account_identity_digest"]
        == "f" * 64
    )
    with pytest.raises(IdentityScopeError, match="parent unavailable"):
        u2[1].verify(record, damaged)
    before = u2[2].snapshot()
    _, command = u2_command(
        "PORTFOLIO", value=record.value, version=2, nonce=100, parent=parent
    )
    with pytest.raises(AuthorityServiceError, match="AUTHENTICATION_REJECTED"):
        _u038_endpoint(u2, "PORTFOLIO", "transition", command)
    assert u2[2].snapshot() == before
    _u033_evidence("u038-authenticated-parent-conflict", before, u2[2].snapshot())


@pytest.mark.parametrize("dimension", ["ACCOUNT", "PORTFOLIO"])
def test_u038_concurrent_reopened_issuers_reject_damaged_predecessor(u2, dimension):
    import json
    import base64
    from concurrent.futures import ThreadPoolExecutor
    from automated_trading_bot.stage5.identity_scope import canonical_bytes
    from automated_trading_bot.stage5.authority_service.persistence import (
        IdentityLifecycleStore,
    )
    from automated_trading_bot.stage5.authority_service.service import (
        IdentityCapabilityService,
    )
    from automated_trading_bot.stage5.authority_service.client import IdentityConsumer

    record, raw, parent = _u038_setup(u2, dimension)
    control, control_raw, _ = u2_issue(
        u2, dimension, value="CONTROL", nonce=10, parent=parent
    )
    _, control_command = u2_command(
        dimension, value=control.value, version=2, nonce=11, parent=parent
    )
    control_response = _u038_endpoint(u2, dimension, "transition", control_command)
    assert (
        u2[1].verify(control, bytes.fromhex(control_response["assertion_hex"]))[
            "lifecycle_version"
        ]
        == 2
    )
    value = json.loads(raw)
    value["signature_b64"] = base64.b64encode(bytes(64)).decode()
    damaged = canonical_bytes(value)
    _u038_rewrite_current(u2, dimension, raw, damaged)
    before = u2[2].snapshot()
    store = IdentityLifecycleStore(u2[2].path)
    consumer = IdentityConsumer(u2[1].policies, store.current, lambda: u2[4])
    service = IdentityCapabilityService(
        store,
        dict(zip(("ACCOUNT", "PORTFOLIO"), u2[3])),
        consumer,
        lambda: u2[4],
        "TEST-DEPLOYMENT",
    )
    reopened = (service, consumer, store, u2[3], u2[4], u2[5])
    with pytest.raises(IdentityScopeError, match="invalid identity signature"):
        consumer.verify(record, damaged)

    def attempt(nonce):
        _, command = u2_command(
            dimension, value=record.value, version=2, nonce=nonce, parent=parent
        )
        try:
            _u038_endpoint(reopened, dimension, "transition", command)
        except AuthorityServiceError as exc:
            return str(exc)
        return "ACCEPTED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [100, 101]))
    after = store.snapshot()
    _u033_evidence("u038-concurrent-" + dimension, before, after, responses=results)
    assert all(r.startswith("AUTHENTICATION_REJECTED") for r in results)
    assert after == before

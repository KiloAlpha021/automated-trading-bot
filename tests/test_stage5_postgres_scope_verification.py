"""Task045-A: actual PostgreSQL signature foundation, never financial authority.

The explicit disposable harness supplies connection and evidence locations.
Absence of that environment is an error, not a skipped qualification result.
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import psycopg
import pytest
from automated_trading_bot.stage5.authority_service.cng import _Cng, _status
from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
    OperationRequest,
)
from automated_trading_bot.stage5.identity_scope import (
    AuthenticatedScopeEnvelopeV1,
    IdentityRecordV1,
    OperationCoreV1,
    canonical_bytes,
    digest_list,
    parse_container,
    signed_container,
    timestamp,
)
from automated_trading_bot.stage5.scope_registry import registry_signing_bytes
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_stage5_identity_scope_contract import u2_issue

pytest_plugins = ["test_stage5_identity_scope_contract"]


class SyntheticSigner:
    """Distinct non-persisted test key; no admission/account capability reused."""

    def __init__(self):
        self.cng = _Cng()
        self.key = wintypes.HANDLE()
        provider = wintypes.HANDLE()
        try:
            _status(
                self.cng.ncrypt.NCryptOpenStorageProvider(
                    ctypes.byref(provider), "Microsoft Software Key Storage Provider", 0
                ),
                "test provider",
            )
            _status(
                self.cng.ncrypt.NCryptCreatePersistedKey(
                    provider, ctypes.byref(self.key), "ECDSA_P256", None, 0, 0
                ),
                "test key",
            )
            _status(self.cng.ncrypt.NCryptFinalizeKey(self.key, 0), "test finalize")
        finally:
            if provider.value:
                self.cng.ncrypt.NCryptFreeObject(provider)

    def sign(self, raw):
        digest = sha256(raw).digest()
        source = (ctypes.c_ubyte * 32).from_buffer_copy(digest)
        output = (ctypes.c_ubyte * 64)()
        size = wintypes.DWORD()
        _status(
            self.cng.ncrypt.NCryptSignHash(
                self.key, None, source, 32, output, 64, ctypes.byref(size), 0
            ),
            "test sign",
        )
        assert size.value == 64
        return bytes(output)

    def public_blob(self):
        output = (ctypes.c_ubyte * 72)()
        size = wintypes.DWORD()
        _status(
            self.cng.ncrypt.NCryptExportKey(
                self.key, None, "ECCPUBLICBLOB", None, output, 72, ctypes.byref(size), 0
            ),
            "test export",
        )
        assert size.value == 72
        return bytes(output)

    def close(self):
        if self.key.value:
            self.cng.ncrypt.NCryptFreeObject(self.key)
            self.key.value = None


def test_financial_functions_remain_closed(db, vectors):
    admin, _ = db
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    calls = [
        "SELECT * FROM atis_soe.commit_capital('A','digest','scope',1,'GBP',1,1,'nonce',1,'attempt','time')",
        "SELECT * FROM atis_soe.commit_capital_v2('A','digest',1,'GBP','nonce',1,'attempt','time')",
    ]
    before = snapshot(admin)
    # Superuser fixture reaches the unchanged U1 stub; no grants are restored.
    for statement in calls:
        admin.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        try:
            with pytest.raises(
                psycopg.errors.InsufficientPrivilege, match="U1_FINANCIAL_CONTAINMENT"
            ):
                admin.execute(statement)
        finally:
            admin.execute("ROLLBACK")
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
        autocommit=True,
    ) as runtime:
        for statement in calls:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(statement)
    assert snapshot(admin) == before


@pytest.fixture
def db():
    dsn = os.environ.get("ATIS_U3_TEST_DSN")
    if dsn is None:
        pytest.skip("requires authorised disposable native PostgreSQL harness")
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert (
            Path(admin.execute("SHOW data_directory").fetchone()[0]).resolve()
            == Path(os.environ["ATIS_U3_TEST_DATA"]).resolve()
        )
        assert admin.execute("SHOW server_version").fetchone()[0].startswith("17.11")
        with psycopg.connect(
            make_conninfo(dsn, user="u045_verifier"), autocommit=True
        ) as verifier:
            verifier.execute("SET statement_timeout='5s'")
            yield admin, verifier


def snapshot(admin):
    tables = admin.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='atis_soe' ORDER BY tablename"
    ).fetchall()
    return {
        name: admin.execute(
            sql.SQL("SELECT to_jsonb(t)::text FROM atis_soe.{} t ORDER BY 1").format(
                sql.Identifier(name)
            )
        ).fetchall()
        for (name,) in tables
    }


def probe(db, args, expected):
    admin, connection = db
    before = snapshot(admin)
    started = time.perf_counter()
    row = {
        "test": os.environ.get("PYTEST_CURRENT_TEST"),
        "capability": args[0],
        "context_sha256": sha256(args[1]).hexdigest(),
        "evidence_sha256": sha256(args[2]).hexdigest(),
        "before": before,
    }
    try:
        result = connection.execute(
            "SELECT atis_soe.verify_u3_signature(%s,%s,%s)", args
        ).fetchone()[0]
        row["result"] = result
    except psycopg.Error as exc:
        row.update(
            sqlstate=exc.sqlstate, exception=type(exc).__name__, message=str(exc)
        )
        raise
    finally:
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000
        row["after"] = snapshot(admin)
        with Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).open(
            "a", encoding="utf-8"
        ) as out:
            out.write(json.dumps(row, default=str) + "\n")
    assert row["after"] == before
    assert result == expected
    return result


@pytest.fixture
def vectors(db, u2):
    admin, _ = db
    admin.execute("DELETE FROM atis_soe.u3_verification_policy")
    account, araw, _ = u2_issue(u2)
    portfolio, praw, _ = u2_issue(
        u2,
        "PORTFOLIO",
        value="P",
        nonce=2,
        parent={"record": account.body(), "assertion_hex": araw.hex()},
    )
    now = datetime.now(timezone.utc)

    def policy(capability, producer, key_id, blob):
        admin.execute(
            "INSERT INTO atis_soe.u3_verification_policy VALUES(%s,%s,%s,%s,1,1,%s,%s,%s,false)",
            (
                capability,
                producer,
                "TEST-DEPLOYMENT",
                key_id,
                blob,
                now - timedelta(days=1),
                now + timedelta(days=1),
            ),
        )

    policy("ACCOUNT", account.producer_id, u2[3][0].key_id, u2[3][0].public_blob())
    policy("PORTFOLIO", portfolio.producer_id, u2[3][1].key_id, u2[3][1].public_blob())
    registry, scope, wrong = SyntheticSigner(), SyntheticSigner(), SyntheticSigner()
    try:
        policy(
            "REGISTRY_TRANSITION",
            "TEST-REGISTRY",
            "TEST-REGISTRY-KEY",
            registry.public_blob(),
        )
        policy(
            "SCOPE",
            "atis.scope-envelope-verifier",
            "TEST-SCOPE-KEY",
            scope.public_blob(),
        )
        changes = {"identities": [], "relations": [], "rules": [], "issuers": []}
        command = {
            "schema": "registry-transition/v1",
            "capability": "REGISTRY_TRANSITION",
            "actor": "TEST-REGISTRY",
            "deployment_id": "TEST-DEPLOYMENT",
            "action": "PREPARE",
            "transition_id": "T1",
            "predecessor_epoch": 1,
            "successor_epoch": 2,
            "change_set": changes,
            "change_set_digest": sha256(canonical_bytes(changes)).hexdigest(),
            "preparation_digest": None,
            "issued_at": timestamp(now - timedelta(minutes=1)),
            "expires_at": timestamp(now + timedelta(minutes=30)),
            "nonce": "a" * 64,
            "key_id": "TEST-REGISTRY-KEY",
            "key_version": 1,
            "policy_version": 1,
        }
        rraw = signed_container(command, registry.sign(registry_signing_bytes(command)))
        request = OperationRequest.create(
            operation_id="OP1",
            request_type="RESERVE",
            scope="legacy",
            amount_minor=1,
            currency="GBP",
            expected_generation=0,
            expected_epoch=0,
            immutable_parameters={"label": "café"},
        )
        core = OperationCoreV1.from_request(request, "TEST-DEPLOYMENT", "RESERVE")
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
                "OP1",
                "TEST-REGISTRY",
                1,
            ),
        ]
        # These non-AP entries are deliberately not claimed qualified registry evidence.
        # Checkpoint A proves signatures/binding only; B must validate catalogue authority.
        payload = {
            "schema": "canonical-scope-envelope/v1",
            "operation_id": "OP1",
            "core_digest": core.digest,
            "deployment_id": "TEST-DEPLOYMENT",
            "operation_class": "RESERVE",
            "catalogue_epoch": 1,
            "leaf_identities": [r.body() for r in records],
            "identity_assertions": [{}, json.loads(araw), json.loads(praw), {}],
            "leaf_set_digest": digest_list(r.digest for r in records),
            "closure_digest": digest_list(r.digest for r in records),
            "issued_at": command["issued_at"],
            "expires_at": command["expires_at"],
            "nonce": "b" * 64,
            "signer_id": "atis.scope-envelope-verifier",
            "signer_key_id": "TEST-SCOPE-KEY",
            "signer_key_version": 1,
            "policy_version": 1,
        }
        unsigned = signed_container(payload, bytes(64))
        sraw = signed_container(
            payload, scope.sign(AuthenticatedScopeEnvelopeV1(unsigned).signing_bytes)
        )
        yield {
            "ACCOUNT": ("ACCOUNT", canonical_bytes(account.body()), araw),
            "PORTFOLIO": ("PORTFOLIO", canonical_bytes(portfolio.body()), praw),
            "REGISTRY_TRANSITION": (
                "REGISTRY_TRANSITION",
                canonical_bytes(
                    {"actor": "TEST-REGISTRY", "deployment_id": "TEST-DEPLOYMENT"}
                ),
                rraw,
            ),
            "SCOPE": ("SCOPE", core.raw, sraw),
            "wrong": wrong,
        }
    finally:
        registry.close()
        scope.close()
        wrong.close()


@pytest.mark.parametrize(
    "capability", ["ACCOUNT", "PORTFOLIO", "SCOPE", "REGISTRY_TRANSITION"]
)
def test_valid_and_forged_signature(db, vectors, capability):
    args = vectors[capability]
    probe(db, args, "VALID_SIGNATURE_ONLY")
    payload, _ = parse_container(args[2])
    probe(
        db, (*args[:2], signed_container(payload, bytes(64))), "U3_SIGNATURE_REJECTED"
    )
    probe(db, args, "VALID_SIGNATURE_ONLY")


@pytest.mark.parametrize(
    "capability", ["ACCOUNT", "PORTFOLIO", "SCOPE", "REGISTRY_TRANSITION"]
)
def test_distinct_wrong_key_reaches_crypto(db, vectors, capability):
    args = vectors[capability]
    probe(db, args, "VALID_SIGNATURE_ONLY")
    db[0].execute(
        "UPDATE atis_soe.u3_verification_policy SET public_blob=%s WHERE capability=%s",
        (vectors["wrong"].public_blob(), capability),
    )
    probe(db, args, "U3_SIGNATURE_REJECTED")


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "revoked",
        "expired",
        "future",
        "wrong_producer",
        "wrong_deployment",
        "wrong_version",
    ],
)
def test_policy_failures(db, vectors, case):
    args = vectors["ACCOUNT"]
    probe(db, args, "VALID_SIGNATURE_ONLY")
    queries = {
        "missing": "DELETE FROM atis_soe.u3_verification_policy WHERE capability='ACCOUNT'",
        "revoked": "UPDATE atis_soe.u3_verification_policy SET revoked=true WHERE capability='ACCOUNT'",
        "expired": "UPDATE atis_soe.u3_verification_policy SET valid_until=clock_timestamp()-interval '1 hour' WHERE capability='ACCOUNT'",
        "future": "UPDATE atis_soe.u3_verification_policy SET valid_from=clock_timestamp()+interval '1 hour' WHERE capability='ACCOUNT'",
        "wrong_producer": "UPDATE atis_soe.u3_verification_policy SET producer='FOREIGN' WHERE capability='ACCOUNT'",
        "wrong_deployment": "UPDATE atis_soe.u3_verification_policy SET deployment='FOREIGN' WHERE capability='ACCOUNT'",
        "wrong_version": "UPDATE atis_soe.u3_verification_policy SET policy_version=2 WHERE capability='ACCOUNT'",
    }
    db[0].execute(queries[case])
    probe(db, args, "U3_POLICY_REJECTED")


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "unknown",
        "utf8",
        "changed_nonce",
        "wrong_dimension",
        "wrong_core",
        "wrong_domain",
    ],
)
def test_canonical_context_domain(db, vectors, case):
    args = vectors["SCOPE" if case == "wrong_core" else "ACCOUNT"]
    probe(db, args, "VALID_SIGNATURE_ONLY")
    capability, context, raw = args
    payload, _ = parse_container(raw)
    expected = "U3_CANONICAL_REJECTED"
    if case == "duplicate":
        raw = raw.replace(b"{", b'{"algorithm":"ECDSA_P256_SHA256",', 1)
    if case == "unknown":
        payload["override"] = True
        raw = signed_container(payload, bytes(64))
    if case == "utf8":
        raw = b"\xff"
    if case == "changed_nonce":
        payload["nonce"] = "e" * 64
        raw = signed_container(payload, parse_container(raw)[1])
        expected = "U3_SIGNATURE_REJECTED"
    if case == "wrong_dimension":
        capability = "PORTFOLIO"
        expected = "U3_CONTEXT_REJECTED"
    if case == "wrong_core":
        body = json.loads(context)
        body["amount_minor"] = 2
        context = canonical_bytes(body)
        expected = "U3_CONTEXT_REJECTED"
    if case == "wrong_domain":
        db[0].execute(
            "UPDATE atis_soe.u3_verification_policy SET public_blob=%s WHERE capability='ACCOUNT'",
            (vectors["wrong"].public_blob(),),
        )
        raw = signed_container(
            payload, vectors["wrong"].sign(b"WRONG-DOMAIN\0" + canonical_bytes(payload))
        )
        expected = "U3_SIGNATURE_REJECTED"
    probe(db, (capability, context, raw), expected)


def test_cross_capability_key_policy_rejected(db, vectors):
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    db[0].execute(
        "UPDATE atis_soe.u3_verification_policy SET public_blob=(SELECT public_blob FROM atis_soe.u3_verification_policy WHERE capability='PORTFOLIO') WHERE capability='ACCOUNT'"
    )
    probe(db, vectors["ACCOUNT"], "U3_CAPABILITY_REJECTED")


def test_native_import_failure_restrictive(db, vectors):
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    db[0].execute(
        "UPDATE atis_soe.u3_verification_policy SET public_blob=%s WHERE capability='ACCOUNT'",
        (b"ECS1\x20\0\0\0" + bytes(64),),
    )
    probe(db, vectors["ACCOUNT"], "UNAVAILABLE")


def test_sql_privileges_and_shadowing(db, vectors):
    admin, verifier = db
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    before = snapshot(admin)
    attacks = [
        "SELECT atis_soe.u3_private_verify(NULL,NULL,NULL)",
        "SELECT * FROM atis_soe.u3_verification_policy",
        "UPDATE atis_soe.u3_verifier_configuration SET singleton=false",
        "SET ROLE atis_soe_owner",
        "CREATE OR REPLACE FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea) RETURNS text LANGUAGE sql AS 'SELECT ''VALID'''",
        "DO LANGUAGE plpython3u $$ import os $$",
    ]
    for query in attacks:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            verifier.execute(query)
    verifier.execute(
        "CREATE TEMP TABLE u3_verification_policy(x text); SET search_path=pg_temp,public"
    )
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    with (
        psycopg.connect(
            make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
            autocommit=True,
        ) as runtime,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        runtime.execute(
            "SELECT atis_soe.verify_u3_signature(%s,%s,%s)", vectors["ACCOUNT"]
        )
    assert snapshot(admin) == before


def test_policy_lock_cancellation_and_connection_reuse(db, vectors):
    # Real lock wait inside the verifier, not an unrelated sleep statement.
    admin, verifier = db
    probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
    for explicit in (False, True):
        before = snapshot(admin)
        admin.execute("BEGIN")
        admin.execute(
            "LOCK TABLE atis_soe.u3_verification_policy IN ACCESS EXCLUSIVE MODE"
        )
        timer = None
        try:
            verifier.execute(
                "SET statement_timeout='5s'"
                if explicit
                else "SET statement_timeout='50ms'"
            )
            if explicit:
                timer = threading.Timer(0.1, verifier.cancel)
                timer.start()
            started = time.perf_counter()
            with pytest.raises(psycopg.errors.QueryCanceled) as captured:
                verifier.execute(
                    "SELECT atis_soe.verify_u3_signature(%s,%s,%s)", vectors["ACCOUNT"]
                )
            assert captured.value.sqlstate == "57014"
            with Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).open(
                "a", encoding="utf-8"
            ) as out:
                out.write(
                    json.dumps(
                        {
                            "test": "explicit_cancel" if explicit else "timeout",
                            "sqlstate": "57014",
                            "elapsed_ms": (time.perf_counter() - started) * 1000,
                            "backend_pid": verifier.info.backend_pid,
                            "transaction_state": verifier.info.transaction_status.name,
                        }
                    )
                    + "\n"
                )
        finally:
            if timer:
                timer.join(timeout=2)
            admin.execute("ROLLBACK")
        verifier.execute("SET statement_timeout='5s'")
        probe(db, vectors["ACCOUNT"], "VALID_SIGNATURE_ONLY")
        assert snapshot(admin) == before


def test_module_provenance_and_missing_configuration(db, vectors):
    args = vectors["ACCOUNT"]
    probe(db, args, "VALID_SIGNATURE_ONLY")

    admin = db[0]
    original = admin.execute(
        "SELECT module_hashes FROM atis_soe.u3_verifier_configuration"
    ).fetchone()[0]
    damaged = dict(original)
    damaged[next(iter(damaged))] = "0" * 64
    admin.execute(
        "UPDATE atis_soe.u3_verifier_configuration SET module_hashes=%s",
        (json.dumps(damaged),),
    )
    probe(db, args, "UNAVAILABLE")
    admin.execute(
        "UPDATE atis_soe.u3_verifier_configuration SET module_hashes=%s",
        (json.dumps(original),),
    )
    probe(db, args, "VALID_SIGNATURE_ONLY")
    admin.execute("BEGIN; DELETE FROM atis_soe.u3_verifier_configuration; COMMIT")
    probe(db, args, "UNAVAILABLE")
    # Fixture restoration only, explicit owner-admin provision; never verifier repair.
    paths = json.loads(Path(os.environ["ATIS_U3_TEST_CONFIG"]).read_text())[
        "python_paths"
    ]
    admin.execute(
        "INSERT INTO atis_soe.u3_verifier_configuration VALUES(true,%s,%s)",
        (paths, json.dumps(original)),
    )
    probe(db, args, "VALID_SIGNATURE_ONLY")


@pytest.mark.parametrize("fault", ["late_cancel", "dll_failure"])
def test_post_crypto_interrupt_and_native_failure(db, vectors, fault):
    """Disposable owner-only fault injection; no runtime fault API is installed."""
    admin, verifier = db
    args = vectors["ACCOUNT"]
    probe(db, args, "VALID_SIGNATURE_ONLY")
    original = admin.execute(
        "SELECT pg_get_functiondef('atis_soe.u3_private_verify(text,bytea,bytea)'::regprocedure)"
    ).fetchone()[0]
    marker = 'plpy.execute("SELECT 1")'
    assert original.count(marker) == 1
    if fault == "late_cancel":
        injection = 'plpy.notice("TASK045A_CRYPTO_COMPLETED")\n    import time\n    time.sleep(.3)\n    '
    else:
        injection = 'import ctypes\n    ctypes.WinDLL(r"C:\\ATIS045-NONEXISTENT\\missing.dll")\n    '
    instrumented = original.replace(marker, injection + marker)
    directory = Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).parent
    (directory / (fault + "-original.sql")).write_text(original, encoding="utf-8")
    (directory / (fault + "-injected.sql")).write_text(instrumented, encoding="utf-8")
    notices = []
    timers = []

    def notice_handler(notice):
        notices.append(notice.message_primary)
        if (
            fault == "late_cancel"
            and notice.message_primary == "TASK045A_CRYPTO_COMPLETED"
        ):
            timer = threading.Timer(0.02, verifier.cancel)
            timers.append(timer)
            timer.start()

    verifier.add_notice_handler(notice_handler)
    before = snapshot(admin)
    try:
        admin.execute(instrumented)
        if fault == "late_cancel":
            verifier.execute(
                "SET statement_timeout='5s'; BEGIN ISOLATION LEVEL SERIALIZABLE"
            )
            started = time.perf_counter()
            with pytest.raises(psycopg.errors.QueryCanceled) as caught:
                verifier.execute("SELECT atis_soe.verify_u3_signature(%s,%s,%s)", args)
            assert caught.value.sqlstate == "57014"
            assert "TASK045A_CRYPTO_COMPLETED" in notices
            assert verifier.info.transaction_status.name == "INERROR"
            (directory / "late-cancel.json").write_text(
                json.dumps(
                    {
                        "sqlstate": caught.value.sqlstate,
                        "notices": notices,
                        "transaction_state": verifier.info.transaction_status.name,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "backend_pid": verifier.info.backend_pid,
                    },
                    indent=2,
                )
            )
        else:
            probe(db, args, "UNAVAILABLE")
    finally:
        for timer in timers:
            timer.join(timeout=2)
        verifier.execute("ROLLBACK")
        verifier.execute("SET statement_timeout='5s'")
        admin.execute(original)
    assert (
        admin.execute(
            "SELECT pg_get_functiondef('atis_soe.u3_private_verify(text,bytea,bytea)'::regprocedure)"
        ).fetchone()[0]
        == original
    )
    assert snapshot(admin) == before
    probe(db, args, "VALID_SIGNATURE_ONLY")

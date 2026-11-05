"""Actual wire-entry oracles; parsing alone never authenticates a caller."""

from copy import deepcopy

import pytest
from automated_trading_bot.stage5.identity_scope import (
    IdentityScopeError,
    canonical_bytes,
    signed_container,
)
from automated_trading_bot.stage5.scope_registry import (
    RegistryTransitionV1,
    registry_signing_bytes,
)


def command():
    from hashlib import sha256

    changes = {"identities": [], "relations": [], "rules": [], "issuers": []}
    return {
        "schema": "registry-transition/v1",
        "capability": "REGISTRY_TRANSITION",
        "actor": "SYNTHETIC-REGISTRY",
        "deployment_id": "SYNTHETIC-DEPLOYMENT",
        "action": "PREPARE",
        "transition_id": "T-1",
        "predecessor_epoch": 1,
        "successor_epoch": 2,
        "change_set": changes,
        "change_set_digest": sha256(canonical_bytes(changes)).hexdigest(),
        "preparation_digest": None,
        "issued_at": "2026-10-31T00:00:00.000000Z",
        "expires_at": "2026-11-01T00:00:00.000000Z",
        "nonce": "1" * 64,
        "key_id": "SYNTHETIC-REGISTRY-KEY",
        "key_version": 1,
        "policy_version": 1,
    }


def test_registry_carrier_retains_exact_signed_bytes():
    payload = command()
    raw = signed_container(payload, bytes(64))
    carrier = RegistryTransitionV1(raw)
    assert carrier.raw == raw
    assert carrier.signing_bytes == registry_signing_bytes(payload)
    assert carrier.signing_bytes.startswith(b"ATIS-S5-REGISTRY-TRANSITION-V1\0")
    # Structurally valid zero signatures are not claimed authenticated here.


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability", "ACCOUNT"),
        ("action", "OPEN"),
        ("predecessor_epoch", True),
        ("successor_epoch", 1),
        ("successor_epoch", 2**63),
        ("change_set_digest", "0" * 64),
        ("preparation_digest", "0" * 64),
        ("nonce", "short"),
        ("key_version", 0),
        ("actor", ""),
        ("expires_at", "2026-10-30T00:00:00.000000Z"),
    ],
)
def test_registry_wire_rejects_invalid_binding(field, value):
    original = command()
    RegistryTransitionV1(signed_container(original, bytes(64)))
    altered = deepcopy(original)
    altered[field] = value
    with pytest.raises(IdentityScopeError):
        RegistryTransitionV1(signed_container(altered, bytes(64)))


def test_registry_rejects_unknown_and_duplicate_fields():
    raw = signed_container(command(), bytes(64))
    RegistryTransitionV1(raw)
    unknown = command() | {"authority_override": True}
    with pytest.raises(IdentityScopeError):
        RegistryTransitionV1(signed_container(unknown, bytes(64)))
    duplicate = raw.replace(b"{", b'{"algorithm":"ECDSA_P256_SHA256",', 1)
    with pytest.raises(IdentityScopeError):
        RegistryTransitionV1(duplicate)


def test_activation_requires_exact_preparation_digest():
    payload = command() | {"action": "ACTIVATE", "preparation_digest": "a" * 64}
    RegistryTransitionV1(signed_container(payload, bytes(64)))
    with pytest.raises(IdentityScopeError):
        RegistryTransitionV1(
            signed_container(payload | {"preparation_digest": None}, bytes(64))
        )


# Checkpoint B: real PostgreSQL paths. The A plugin supplies its unchanged
# disposable connection, real U2 issuance and native synthetic signing fixtures.
pytest_plugins = ["test_stage5_postgres_scope_verification"]


@pytest.fixture
def registry(db, vectors):
    import json
    import os
    from datetime import datetime, timedelta, timezone

    import psycopg
    from automated_trading_bot.stage5.identity_scope import (
        SCOPE_KINDS,
        IdentityRecordV1,
        timestamp,
    )
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import SyntheticSigner

    admin = db[0]
    if admin.execute("SELECT to_regclass('atis_soe.scope_catalogue_state')").fetchone()[
        0
    ]:
        admin.execute(
            "TRUNCATE atis_soe.scope_catalogue_state,atis_soe.registry_transition_command,atis_soe.scope_catalogue_transition_history,atis_soe.registry_nonce,atis_soe.scope_reservation,atis_soe.scope_barrier,atis_soe.scope_identity,atis_soe.scope_relation,atis_soe.operation_class_rule,atis_soe.scope_required_issuer CASCADE"
        )
        admin.execute(
            "INSERT INTO atis_soe.scope_catalogue_state(singleton,catalogue_epoch,status) VALUES(true,1,'ACTIVE')"
        )
        admin.execute(
            "INSERT INTO atis_soe.scope_required_issuer VALUES('TEST-DEPLOYMENT','OPS',0),('TEST-DEPLOYMENT','RISK',1)"
        )
    admin.execute("DELETE FROM atis_soe.deployment_integrity")
    admin.execute(
        "INSERT INTO atis_soe.deployment_integrity VALUES(true,'TEST-DEPLOYMENT',1)"
    )
    key = SyntheticSigner()
    admin.execute(
        "UPDATE atis_soe.u3_verification_policy SET public_blob=%s WHERE capability='REGISTRY_TRANSITION'",
        (key.public_blob(),),
    )
    account = IdentityRecordV1.parse(json.loads(vectors["ACCOUNT"][1]))
    portfolio = IdentityRecordV1.parse(json.loads(vectors["PORTFOLIO"][1]))
    root = IdentityRecordV1(
        "scope-identity/v1",
        "GLOBAL",
        "test.global",
        "TEST-DEPLOYMENT",
        "TEST-DEPLOYMENT",
        "TEST-REGISTRY",
        1,
    )
    operation = IdentityRecordV1(
        "scope-identity/v1",
        "OPERATION",
        "test.operation",
        "TEST-DEPLOYMENT",
        "OP1",
        "TEST-REGISTRY",
        1,
    )
    entries = [
        {"record": root.body(), "assertion": None},
        {"record": account.body(), "assertion": json.loads(vectors["ACCOUNT"][2])},
        {"record": portfolio.body(), "assertion": json.loads(vectors["PORTFOLIO"][2])},
        {"record": operation.body(), "assertion": None},
    ]
    entries.sort(
        key=lambda entry: (
            SCOPE_KINDS.index(entry["record"]["dimension"]),
            canonical_bytes(entry["record"]),
        )
    )
    changes = {
        "identities": entries,
        "relations": sorted(
            [
                {
                    "child": portfolio.digest,
                    "parent": account.digest,
                    "type": "PORTFOLIO_ACCOUNT",
                },
                {
                    "child": operation.digest,
                    "parent": portfolio.digest,
                    "type": "OPERATION_LEAF",
                },
            ],
            key=lambda value: (value["child"], value["parent"], value["type"]),
        ),
        "rules": [
            {
                "operation_class": "RESERVE",
                "dimension": kind,
                "minimum": 1,
                "maximum": 1,
            }
            for kind in ("GLOBAL", "ACCOUNT", "PORTFOLIO", "OPERATION")
        ],
        "issuers": ["OPS", "RISK"],
    }
    now = datetime.now(timezone.utc)

    def sign(
        action="PREPARE",
        transition="T1",
        nonce=1,
        predecessor=1,
        preparation=None,
        change=None,
        **overrides,
    ):
        from hashlib import sha256

        body = command()
        body.update(
            actor="TEST-REGISTRY",
            deployment_id="TEST-DEPLOYMENT",
            key_id="TEST-REGISTRY-KEY",
            action=action,
            transition_id=transition,
            nonce=f"{nonce:064x}",
            predecessor_epoch=predecessor,
            successor_epoch=predecessor + 1,
            preparation_digest=preparation,
            issued_at=timestamp(now - timedelta(minutes=1)),
            expires_at=timestamp(now + timedelta(minutes=30)),
            change_set=changes if change is None else change,
        )
        body["change_set_digest"] = sha256(
            canonical_bytes(body["change_set"])
        ).hexdigest()
        body.update(overrides)
        return signed_container(body, key.sign(registry_signing_bytes(body)))

    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_registry"),
        autocommit=True,
    ) as manager:
        yield {
            "admin": admin,
            "connection": manager,
            "sign": sign,
            "changes": changes,
            "account": account,
            "portfolio": portfolio,
            "operation": operation,
        }
    key.close()


def pg_call(registry, raw, action="prepare", expected=None):
    import json
    import os
    from pathlib import Path

    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    admin, connection = registry["admin"], registry["connection"]
    before = snapshot(admin)
    query = {
        "prepare": "SELECT atis_soe.prepare_scope_catalogue_transition(%s)",
        "activate": "SELECT atis_soe.transition_scope_catalogue(%s)",
    }[action]
    record = {
        "test": os.environ.get("PYTEST_CURRENT_TEST"),
        "action": action,
        "raw_hex": raw.hex(),
        "before": before,
    }
    connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    try:
        result = connection.execute(query, (raw,)).fetchone()[0]
        connection.execute("COMMIT")
        record["result"] = result
        assert expected is None, ("expected rejection", expected, result)
        return result
    except psycopg.Error as exc:
        connection.execute("ROLLBACK")
        record.update(sqlstate=exc.sqlstate, message=str(exc))
        if expected is None or expected not in str(exc):
            raise
        assert snapshot(admin) == before
        return None
    finally:
        record["after"] = snapshot(admin)
        with Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(record, default=str) + "\n")


def test_b_actual_prepare_activate_and_readback(registry):
    preparation = registry["sign"]()
    prepared = pg_call(registry, preparation)
    assert prepared["status"] == "PREPARED"
    rows = (
        registry["admin"]
        .execute("SELECT lifecycle,gate_status FROM atis_soe.scope_barrier")
        .fetchall()
    )
    assert len(rows) == 8 and set(rows) == {("RESERVED", "DENY")}
    activation = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(preparation).digest
    )
    result = pg_call(registry, activation, "activate")
    assert result["status"] == "ACTIVATED" and result["catalogue_epoch"] == 2
    assert pg_call(registry, activation, "activate") == result
    assert pg_call(registry, preparation) == prepared


def test_b_forged_transition_rejects_at_signature(registry):
    from automated_trading_bot.stage5.identity_scope import parse_container

    valid = registry["sign"]()
    pg_call(registry, valid)
    payload, _ = parse_container(registry["sign"](transition="OTHER", nonce=3))
    pg_call(
        registry, signed_container(payload, bytes(64)), expected="U3_SIGNATURE_REJECTED"
    )


@pytest.mark.parametrize(
    "field,value",
    [("deployment_id", "FOREIGN"), ("actor", "FOREIGN"), ("key_id", "FOREIGN")],
)
def test_b_authority_context_rejection(registry, field, value):
    pg_call(registry, registry["sign"]())
    pg_call(
        registry,
        registry["sign"](transition="T2", nonce=3, **{field: value}),
        expected="U3_POLICY_REJECTED",
    )


def test_b_nonce_transition_and_epoch_conflicts(registry):
    raw = registry["sign"]()
    prepared = pg_call(registry, raw)
    assert pg_call(registry, raw) == prepared
    pg_call(registry, registry["sign"](transition="T2"), expected="U3_NONCE_CONFLICT")
    pg_call(registry, registry["sign"](nonce=4), expected="U3_TRANSITION_CONFLICT")
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    pg_call(registry, active, "activate")
    pg_call(
        registry, registry["sign"](transition="T2", nonce=3), expected="U3_STALE_EPOCH"
    )
    # Reuse of already ACTIVE keys is preparation metadata, not an L02 update.
    next_raw = registry["sign"](transition="T2", nonce=5, predecessor=2)
    pg_call(registry, next_raw)
    next_active = registry["sign"](
        "ACTIVATE",
        transition="T2",
        nonce=6,
        predecessor=2,
        preparation=RegistryTransitionV1(next_raw).digest,
    )
    assert pg_call(registry, next_active, "activate")["catalogue_epoch"] == 3


@pytest.mark.parametrize("fault", ["missing", "foreign", "extra_foreign"])
def test_b_incomplete_reservations_leave_prior_state(registry, fault):
    raw = registry["sign"]()
    pg_call(registry, raw)
    admin = registry["admin"]
    if fault == "missing":
        admin.execute(
            "DELETE FROM atis_soe.scope_reservation WHERE issuer_domain='OPS'"
        )
    elif fault == "foreign":
        admin.execute(
            "UPDATE atis_soe.scope_barrier SET reservation_transition_id='FOREIGN' WHERE issuer_domain='OPS'"
        )
    else:
        admin.execute(
            "INSERT INTO atis_soe.scope_barrier SELECT repeat('f',64),issuer_domain,deployment_id,scope_rank,canonical_bytes,issuer_rank,'RESERVED','FOREIGN','DENY' FROM atis_soe.scope_barrier ORDER BY identity_digest,issuer_domain LIMIT 1"
        )
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    pg_call(
        registry,
        active,
        "activate",
        expected="U3_MISSING_RESERVATION"
        if fault == "missing"
        else "U3_FOREIGN_RESERVATION",
    )
    assert admin.execute(
        "SELECT catalogue_epoch FROM atis_soe.scope_catalogue_state"
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM atis_soe.scope_barrier WHERE lifecycle='RESERVED' AND gate_status='DENY'"
    ).fetchone() == ((9,) if fault == "extra_foreign" else (8,))


@pytest.mark.parametrize(
    "fault", ["parent", "cycle", "missing_parent", "issuer", "applicability"]
)
def test_b_authenticated_bad_graph_rejects(registry, fault):
    pg_call(registry, registry["sign"]())
    change = deepcopy(registry["changes"])
    if fault == "parent":
        target = next(
            r for r in change["relations"] if r["type"] == "PORTFOLIO_ACCOUNT"
        )
        target["parent"] = registry["operation"].digest
        expected = "U3_CYCLE"  # OP->PORTFOLIO->OP is an authenticated cycle.
    elif fault == "cycle":
        change["relations"].append(
            {
                "child": registry["account"].digest,
                "parent": registry["operation"].digest,
                "type": "OPERATION_LEAF",
            }
        )
        expected = "U3_CYCLE"
    elif fault == "missing_parent":
        change["relations"][0]["parent"] = "f" * 64
        expected = "U3_MISSING_PARENT"
    elif fault == "issuer":
        change["issuers"] = ["OPS"]
        expected = "U3_ISSUER_SET"
    else:
        change["rules"] = change["rules"][:-1]
        expected = "U3_INCOMPLETE_APPLICABILITY"
    change["relations"].sort(key=lambda r: (r["child"], r["parent"], r["type"]))
    pg_call(
        registry,
        registry["sign"](transition="T2", nonce=3, change=change),
        expected=expected,
    )


@pytest.mark.parametrize("fault", ["catalogue", "reservation_table", "history"])
def test_b_missing_state_never_recreated(registry, fault):
    raw = registry["sign"]()
    pg_call(registry, raw)
    admin = registry["admin"]
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    if fault == "catalogue":
        admin.execute("DELETE FROM atis_soe.scope_catalogue_state")
        pg_call(registry, active, "activate", expected="U3_REGISTRY_STATE")
        assert admin.execute(
            "SELECT count(*) FROM atis_soe.scope_catalogue_state"
        ).fetchone() == (0,)
    elif fault == "history":
        # Explicit owner-only corruption injection, not a runtime privilege grant.
        admin.execute(
            "ALTER TABLE atis_soe.scope_catalogue_transition_history DISABLE TRIGGER immutable_history"
        )
        try:
            admin.execute("DELETE FROM atis_soe.scope_catalogue_transition_history")
        finally:
            admin.execute(
                "ALTER TABLE atis_soe.scope_catalogue_transition_history ENABLE TRIGGER immutable_history"
            )
        pg_call(registry, active, "activate", expected="U3_REGISTRY_STATE")
    else:
        admin.execute(
            "ALTER TABLE atis_soe.scope_reservation RENAME TO missing_scope_reservation"
        )
        try:
            pg_call(registry, active, "activate", expected="U3_REGISTRY_UNAVAILABLE")
        finally:
            admin.execute(
                "ALTER TABLE atis_soe.missing_scope_reservation RENAME TO scope_reservation"
            )


@pytest.mark.parametrize("sqlstate", ["P0001", "40001", "40P01"])
def test_b_activation_failure_rolls_back_all_rows(registry, sqlstate):
    raw = registry["sign"]()
    pg_call(registry, raw)
    admin = registry["admin"]
    notices = []
    registry["connection"].add_notice_handler(
        lambda notice: notices.append(notice.message_primary)
    )
    admin.execute(
        "CREATE FUNCTION atis_soe.test_b_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.dimension='PORTFOLIO' THEN RAISE NOTICE 'B_FAULT_AFTER_%',(SELECT count(*) FROM atis_soe.scope_identity); RAISE NOTICE 'B_INTENT_%_%',(SELECT status FROM atis_soe.scope_catalogue_state),(SELECT count(*) FROM atis_soe.scope_catalogue_transition_history WHERE action='ACTIVATE'); RAISE EXCEPTION USING ERRCODE='"
        + sqlstate
        + "',MESSAGE='B_INJECTED_ACTIVATION_FAILURE'; END IF; RETURN NEW; END $$; REVOKE ALL ON FUNCTION atis_soe.test_b_fault() FROM PUBLIC; CREATE TRIGGER b_fault BEFORE INSERT ON atis_soe.scope_identity FOR EACH ROW EXECUTE FUNCTION atis_soe.test_b_fault()"
    )
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    try:
        pg_call(
            registry,
            active,
            "activate",
            expected="U3_REGISTRY_UNAVAILABLE"
            if sqlstate == "P0001"
            else "B_INJECTED_ACTIVATION_FAILURE",
        )
        assert "B_FAULT_AFTER_2" in notices
        assert "B_INTENT_TRANSITION_PENDING_1" in notices, notices
        assert admin.execute(
            "SELECT count(*) FROM atis_soe.scope_identity"
        ).fetchone() == (0,)
        assert admin.execute(
            "SELECT count(*) FROM atis_soe.registry_nonce"
        ).fetchone() == (1,)
    finally:
        admin.execute(
            "DROP TRIGGER b_fault ON atis_soe.scope_identity; DROP FUNCTION atis_soe.test_b_fault()"
        )
    assert pg_call(registry, active, "activate")["status"] == "ACTIVATED"


def test_b_runtime_and_legacy_bypass_rejected(registry):
    import os

    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    raw = registry["sign"]()
    pg_call(registry, raw)
    before = snapshot(registry["admin"])
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
        autocommit=True,
    ) as runtime:
        for statement in (
            "SELECT atis_soe.prepare_scope_catalogue_transition(NULL)",
            "UPDATE atis_soe.scope_barrier SET gate_status='DENY'",
            "SET ROLE atis_soe_registry_owner",
            "SELECT atis_soe.registry_private_entry('READ',NULL,'T1','x')",
            "SELECT atis_soe.register_scope_envelope_v1('A','B','C',1,'D','E','{}'::bytea)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(statement)
    with pytest.raises(psycopg.Error, match="U3_APPEND_ONLY_HISTORY"):
        registry["admin"].execute(
            "UPDATE atis_soe.registry_transition_command SET transition_id='ALTERED'"
        )
    assert snapshot(registry["admin"]) == before


def b_log(name, **values):
    import json
    import os
    from pathlib import Path

    with Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write(json.dumps(dict(test=name, **values), default=str) + "\n")


def read_b(connection, raw):
    from automated_trading_bot.stage5.identity_scope import parse_container

    payload, _ = parse_container(raw)
    connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    try:
        result = connection.execute(
            "SELECT atis_soe.read_scope_catalogue_transition(%s,%s)",
            (payload["transition_id"], RegistryTransitionV1(raw).digest),
        ).fetchone()[0]
        connection.execute("COMMIT")
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def test_b_exact_readback_and_missing_digest(registry):
    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    raw = registry["sign"]()
    expected = pg_call(registry, raw)
    before = snapshot(registry["admin"])
    assert read_b(registry["connection"], raw) == expected
    wrong = registry["sign"](nonce=90)
    with pytest.raises(psycopg.Error, match="U3_READBACK_UNRESOLVED"):
        read_b(registry["connection"], wrong)
    assert before == snapshot(registry["admin"])
    b_log(
        "exact-readback",
        before=before,
        after=snapshot(registry["admin"]),
        result=expected,
    )


def test_b_signed_parent_substitution(registry, u2):
    import json

    from automated_trading_bot.stage5.identity_scope import SCOPE_KINDS
    from test_stage5_identity_scope_contract import u2_issue

    pg_call(registry, registry["sign"]())
    record, assertion, _ = u2_issue(u2, value="A2", nonce=40)
    assert u2[1].verify(record, assertion)["state"] == "ACTIVE"
    changes = deepcopy(registry["changes"])
    changes["identities"].append(
        {"record": record.body(), "assertion": json.loads(assertion)}
    )
    changes["identities"].sort(
        key=lambda e: (
            SCOPE_KINDS.index(e["record"]["dimension"]),
            canonical_bytes(e["record"]),
        )
    )
    for relation in changes["relations"]:
        if relation["type"] == "PORTFOLIO_ACCOUNT":
            relation["parent"] = record.digest
    changes["relations"].sort(key=lambda r: (r["child"], r["parent"], r["type"]))
    pg_call(
        registry,
        registry["sign"](transition="T2", nonce=41, change=changes),
        expected="U3_PARENT_SUBSTITUTION",
    )


@pytest.mark.parametrize("fault", ["nonce", "result"])
def test_b_corrupt_ledger_restricts(registry, fault):
    raw = registry["sign"]()
    expected = pg_call(registry, raw)
    assert read_b(registry["connection"], raw) == expected
    admin = registry["admin"]
    table = "registry_nonce" if fault == "nonce" else "registry_transition_command"
    trigger = "immutable_nonce" if fault == "nonce" else "immutable_command"
    admin.execute("ALTER TABLE atis_soe." + table + " DISABLE TRIGGER " + trigger)
    try:
        if fault == "nonce":
            admin.execute("UPDATE atis_soe.registry_nonce SET nonce=repeat('f',64)")
        else:
            admin.execute(
                "UPDATE atis_soe.registry_transition_command SET result='{}'::jsonb"
            )
    finally:
        admin.execute("ALTER TABLE atis_soe." + table + " ENABLE TRIGGER " + trigger)
    pg_call(registry, raw, expected="U3_REGISTRY_STATE")


def test_b_concurrent_conflicting_preparations(registry):
    import os
    import threading
    import time

    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    # Same-route success, then race two distinct authenticated next transitions.
    first = registry["sign"]()
    pg_call(registry, first)
    pg_call(
        registry,
        registry["sign"](
            "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(first).digest
        ),
        "activate",
    )
    one = registry["sign"](transition="R1", nonce=30, predecessor=2)
    two = registry["sign"](transition="R2", nonce=31, predecessor=2)
    winner = registry["connection"]
    result = {}
    before = snapshot(registry["admin"])
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_registry"),
        autocommit=True,
    ) as other:
        pid = other.info.backend_pid
        winner.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        winner.execute("SELECT atis_soe.prepare_scope_catalogue_transition(%s)", (one,))

        def attempt():
            try:
                other.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
                other.execute("SET LOCAL statement_timeout='8s'")
                other.execute(
                    "SELECT atis_soe.prepare_scope_catalogue_transition(%s)", (two,)
                )
                other.execute("COMMIT")
                result["state"] = "UNEXPECTED_SUCCESS"
            except psycopg.Error as exc:
                result.update(sqlstate=exc.sqlstate, error=str(exc))
                other.execute("ROLLBACK")

        worker = threading.Thread(target=attempt)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                wait = (
                    registry["admin"]
                    .execute(
                        "SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s",
                        (pid,),
                    )
                    .fetchone()
                )
                if wait and wait[0] == "Lock":
                    break
                time.sleep(0.02)
            else:
                pytest.fail("second transition did not reach intended database lock")
            locks = (
                registry["admin"]
                .execute(
                    "SELECT relation::regclass::text,locktype,mode,granted FROM pg_locks WHERE pid=%s",
                    (pid,),
                )
                .fetchall()
            )
            winner.execute("COMMIT")
        finally:
            if winner.info.transaction_status.name != "IDLE":
                winner.execute("ROLLBACK")
            worker.join(10)
            if worker.is_alive():
                other.cancel()
                worker.join(3)
        assert not worker.is_alive()
        assert result.get("sqlstate") == "40001", result
        assert other.info.transaction_status.name == "IDLE"
    assert registry["admin"].execute(
        "SELECT transition_id FROM atis_soe.registry_transition_command WHERE action='PREPARE' ORDER BY transition_id"
    ).fetchall() == [("R1",), ("T1",)]
    b_log(
        "native-serialization-race",
        before=before,
        after=snapshot(registry["admin"]),
        wait=wait,
        locks=locks,
        loser=result,
    )


def test_b_native_lock_timeout_restricts(registry):
    import os
    import time

    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    pg_call(registry, registry["sign"]())
    before = snapshot(registry["admin"])
    connection = registry["connection"]
    with psycopg.connect(os.environ["ATIS_U3_TEST_DSN"], autocommit=True) as holder:
        holder.execute("BEGIN")
        holder.execute("SELECT * FROM atis_soe.deployment_integrity FOR UPDATE")
        connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        connection.execute(
            "SET LOCAL lock_timeout='100ms'; SET LOCAL statement_timeout='3s'"
        )
        start = time.monotonic()
        try:
            with pytest.raises(psycopg.Error) as error:
                connection.execute(
                    "SELECT atis_soe.prepare_scope_catalogue_transition(%s)",
                    (registry["sign"](),),
                )
            assert error.value.sqlstate == "55P03"
        finally:
            connection.execute("ROLLBACK")
            holder.execute("ROLLBACK")
    assert before == snapshot(registry["admin"])
    b_log(
        "native-lock-timeout",
        elapsed=time.monotonic() - start,
        sqlstate=error.value.sqlstate,
        before=before,
        after=snapshot(registry["admin"]),
    )


def test_b_commit_ack_loss_requires_exact_readback(registry):
    import os

    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    # The actual libpq COMMIT is sent; its reply is deliberately not consumed.
    # Exact readback resolves committed vs aborted; no mutation is retried.
    raw = registry["sign"]()
    pg_call(registry, raw)
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    before = snapshot(registry["admin"])
    lost = psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_registry"),
        autocommit=True,
    )
    lost.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    expected = lost.execute(
        "SELECT atis_soe.transition_scope_catalogue(%s)", (active,)
    ).fetchone()[0]
    pid = lost.info.backend_pid
    lost.pgconn.send_query(b"COMMIT")
    # Drain the outgoing command only, not its acknowledgement.
    while lost.pgconn.flush():
        pass
    lost.close()
    import time

    deadline = time.monotonic() + 4
    while (
        registry["admin"]
        .execute("SELECT 1 FROM pg_stat_activity WHERE pid=%s", (pid,))
        .fetchone()
    ):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    try:
        actual = read_b(registry["connection"], active)
    except psycopg.Error as exc:
        assert "U3_READBACK_UNRESOLVED" in str(exc)
        assert registry["admin"].execute(
            "SELECT catalogue_epoch FROM atis_soe.scope_catalogue_state"
        ).fetchone() == (1,)
        actual = "UNRESOLVED_RESTRICTIVE"
    else:
        assert actual == expected
    b_log(
        "commit-ack-not-consumed",
        backend_pid=pid,
        result=actual,
        before=before,
        after=snapshot(registry["admin"]),
    )


def test_b_python_entry_and_u1_preservation(registry):
    import os

    import psycopg
    from automated_trading_bot.stage5.scope_registry import submit_catalogue
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    admin = registry["admin"]
    # Synthetic historical fixture only; no financial-grant function creates it.
    admin.execute("INSERT INTO atis_soe.scope_control VALUES('B-HISTORY',1,1,0,0,0)")
    admin.execute("INSERT INTO atis_soe.capital_state VALUES('B-HISTORY',900)")
    admin.execute(
        "INSERT INTO atis_soe.operation_outcome(operation_id,request_digest,scope,status,result_code,capital_effect_minor,liability_effect_minor,replay_identity,ticket_sequence) VALUES('B-HISTORIC','B-DIGEST','B-HISTORY','COMMITTED','RESERVED',900,900,'B-NONCE',0)"
    )
    admin.execute(
        "INSERT INTO atis_soe.liability VALUES('B-HISTORIC',900,'OUTSTANDING')"
    )
    before = snapshot(admin)
    raw = registry["sign"]()
    assert (
        submit_catalogue(registry["connection"], raw, timeout_ms=30000)["status"]
        == "PREPARED"
    )
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    assert (
        submit_catalogue(registry["connection"], active, timeout_ms=30000)["status"]
        == "ACTIVATED"
    )
    assert submit_catalogue(registry["connection"], active, timeout_ms=30000) == read_b(
        registry["connection"], active
    )
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
        autocommit=True,
    ) as runtime:
        assert runtime.execute(
            "SELECT * FROM atis_soe.lookup_outcome('B-HISTORIC','B-DIGEST')"
        ).fetchone() == ("B-HISTORIC", "B-DIGEST", "COMMITTED", "RESERVED", 900, 900)
        for statement in (
            "SELECT * FROM atis_soe.commit_capital('NEW','D','B-HISTORY',1,'GBP',1,1,'N',1,'A','T')",
            "SELECT * FROM atis_soe.commit_capital_v2('NEW','D',1,'GBP','N',1,'A','T')",
        ):
            runtime.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            with pytest.raises(psycopg.Error) as error:
                runtime.execute(statement)
            assert error.value.sqlstate == "42501"
            runtime.execute("ROLLBACK")
    after = snapshot(admin)
    for table in (
        "capital_state",
        "scope_capital_state_v2",
        "liability",
        "operation_outcome",
        "scope_control",
    ):
        assert before[table] == after[table]
    b_log("u1-financial-and-historical", before=before, after=after)
    # The statement budget affects availability only, never authentication.
    bounded_before = snapshot(admin)
    with pytest.raises(psycopg.Error) as cancelled:
        submit_catalogue(registry["connection"], active, timeout_ms=1)
    assert cancelled.value.sqlstate == "57014"
    assert registry["connection"].info.transaction_status.name == "IDLE"
    assert snapshot(admin) == bounded_before
    with pytest.raises(IdentityScopeError, match="U3_INVALID_STATEMENT_BUDGET"):
        submit_catalogue(registry["connection"], active, timeout_ms=0)
    assert snapshot(admin) == bounded_before
    b_log(
        "python-budget-cancellation",
        sqlstate=cancelled.value.sqlstate,
        before=bounded_before,
        after=snapshot(admin),
    )


def test_b_ordered_activation_locks(registry):
    import os
    import threading
    import time

    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    raw = registry["sign"]()
    pg_call(registry, raw)
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    admin = registry["admin"]
    keys = admin.execute(
        "SELECT identity_digest,issuer_domain FROM atis_soe.scope_barrier ORDER BY scope_rank,canonical_bytes,issuer_rank"
    ).fetchall()
    before = snapshot(admin)
    result = {}
    connection = registry["connection"]
    pid = connection.info.backend_pid
    with psycopg.connect(os.environ["ATIS_U3_TEST_DSN"], autocommit=True) as holder:
        holder.execute("BEGIN")
        holder.execute(
            "SELECT 1 FROM atis_soe.scope_barrier WHERE identity_digest=%s AND issuer_domain=%s FOR UPDATE",
            keys[-1],
        )

        def activate():
            try:
                connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
                connection.execute("SET LOCAL statement_timeout='30s'")
                result["value"] = connection.execute(
                    "SELECT atis_soe.transition_scope_catalogue(%s)", (active,)
                ).fetchone()[0]
                connection.execute("COMMIT")
            except psycopg.Error as exc:
                result.update(error=str(exc), sqlstate=exc.sqlstate)
                connection.execute("ROLLBACK")

        worker = threading.Thread(target=activate)
        worker.start()
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                wait = admin.execute(
                    "SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s",
                    (pid,),
                ).fetchone()
                if wait and wait[0] == "Lock":
                    break
                time.sleep(0.02)
            else:
                pytest.fail("activation did not reach held last L02 key")
            locks = admin.execute(
                "SELECT relation::regclass::text,locktype,mode,granted FROM pg_locks WHERE pid=%s",
                (pid,),
            ).fetchall()
            # The first key is already locked while the last key is waiting.
            with pytest.raises(psycopg.errors.LockNotAvailable):
                admin.execute(
                    "SELECT 1 FROM atis_soe.scope_barrier WHERE identity_digest=%s AND issuer_domain=%s FOR UPDATE NOWAIT",
                    keys[0],
                )
            assert snapshot(admin) == before
        finally:
            holder.execute("ROLLBACK")
            worker.join(25)
            if worker.is_alive():
                connection.cancel()
                worker.join(3)
        assert not worker.is_alive()
    assert result.get("value", {}).get("status") == "ACTIVATED", result
    b_log(
        "activation-ordered-locks",
        keys=keys,
        pid=pid,
        locks=locks,
        result=result,
        before=before,
        after=snapshot(admin),
    )


def test_b_restart_seed(registry):
    import json
    import os
    from pathlib import Path

    from test_stage5_postgres_scope_verification import snapshot

    raw = registry["sign"]()
    result = pg_call(registry, raw)
    active = registry["sign"](
        "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(raw).digest
    )
    Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).with_name("restart-seed.json").write_text(
        json.dumps(
            {
                "raw": raw.hex(),
                "active": active.hex(),
                "conflict": registry["sign"](transition="REPLAY-AFTER-RESTART").hex(),
                "result": result,
                "state": snapshot(registry["admin"]),
            },
            default=str,
        ),
        encoding="utf-8",
    )


# Checkpoint C: actual protected SQL entry, before implementation.
def test_c_operation_identity_entry_is_restricted(db, vectors):
    import os

    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import probe, snapshot

    probe(db, vectors["SCOPE"], "VALID_SIGNATURE_ONLY")
    admin, _ = db
    before = snapshot(admin)
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
        autocommit=True,
    ) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(
                "SELECT atis_soe.prepare_operation_identity_v1(%s,%s,%s,%s,%s)",
                ("OP1", "0" * 64, b"{}", b"{}", b"{}"),
            )
    assert snapshot(admin) == before


@pytest.fixture
def registration(registry, db, vectors):
    import json
    from automated_trading_bot.stage5.identity_scope import (
        AuthenticatedScopeEnvelopeV1,
        IdentityRecordV1,
        OperationCoreV1,
        SCOPE_KINDS,
        digest_list,
        parse_container,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )
    from automated_trading_bot.stage5.scope_registry import U3RegistrationEvidenceV1
    from test_stage5_postgres_scope_verification import SyntheticSigner

    admin, verifier = db
    # Separate bounded initialization; measured requests retain the A fixture's 5s.
    verifier.execute("SET statement_timeout='30s'")
    assert (
        verifier.execute(
            "SELECT atis_soe.verify_u3_signature('ACCOUNT','{}'::bytea,'{}'::bytea)"
        ).fetchone()[0]
        == "U3_CANONICAL_REJECTED"
    )
    verifier.execute("SET statement_timeout='5s'")
    assert verifier.execute("SHOW statement_timeout").fetchone()[0] == "5s"
    if admin.execute("SELECT to_regclass('atis_soe.u3_operation_identity')").fetchone()[
        0
    ]:
        admin.execute("TRUNCATE atis_soe.u3_operation_identity CASCADE")
    # Two authenticated operation identities permit a real cross-operation replay probe.
    op2 = dict(registry["operation"].body(), value="OP2")
    registry["changes"]["identities"].append({"record": op2, "assertion": None})
    registry["changes"]["identities"].sort(
        key=lambda e: (
            SCOPE_KINDS.index(e["record"]["dimension"]),
            canonical_bytes(e["record"]),
        )
    )
    registry["changes"]["relations"].append(
        {
            "child": IdentityRecordV1.parse(op2).digest,
            "parent": registry["portfolio"].digest,
            "type": "OPERATION_LEAF",
        }
    )
    registry["changes"]["relations"].sort(
        key=lambda v: (v["child"], v["parent"], v["type"])
    )
    prep = registry["sign"]()
    pg_call(registry, prep)
    pg_call(
        registry,
        registry["sign"](
            "ACTIVATE", nonce=2, preparation=RegistryTransitionV1(prep).digest
        ),
        "activate",
    )
    signer = SyntheticSigner()
    admin.execute(
        "UPDATE atis_soe.u3_verification_policy SET public_blob=%s WHERE capability='SCOPE'",
        (signer.public_blob(),),
    )
    base = parse_container(vectors["SCOPE"][2])[0]
    entries = sorted(
        [
            e
            for e in registry["changes"]["identities"]
            if e["record"]["dimension"] != "OPERATION" or e["record"]["value"] == "OP1"
        ],
        key=lambda e: (
            SCOPE_KINDS.index(e["record"]["dimension"]),
            IdentityRecordV1.parse(e["record"]).digest,
        ),
    )
    base.update(
        catalogue_epoch=2,
        leaf_identities=[e["record"] for e in entries],
        identity_assertions=[e["assertion"] or {} for e in entries],
    )
    ids = [IdentityRecordV1.parse(e["record"]).digest for e in entries]
    base.update(leaf_set_digest=digest_list(ids), closure_digest=digest_list(ids))

    def make(payload=None, core=None, forged=False):
        core = OperationCoreV1(vectors["SCOPE"][1] if core is None else core)
        body = json.loads(core.raw)
        p = dict(base if payload is None else payload)
        p.update(core_digest=core.digest)
        unsigned = signed_container(p, bytes(64))
        raw = signed_container(
            p, signer.sign(AuthenticatedScopeEnvelopeV1(unsigned).signing_bytes)
        )
        if forged:
            parsed, sig = parse_container(raw)
            raw = signed_container(parsed, bytes([sig[0] ^ 1]) + sig[1:])
        envelope = AuthenticatedScopeEnvelopeV1(raw)
        parameters = dict(body["immutable_business_parameters"])
        parameters["atis_scope_envelope_digest"] = envelope.digest
        request = OperationRequest.create(
            operation_id=body["operation_id"],
            request_type=body["request_type"],
            scope=body["legacy_scope_hint"],
            amount_minor=body["amount_minor"],
            currency=body["currency"],
            expected_generation=body["expected_generation"],
            expected_epoch=body["expected_epoch"],
            immutable_parameters=parameters,
        )
        final = json.dumps(
            request._body(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        return U3RegistrationEvidenceV1(
            "u3-registration-evidence/v1",
            body["operation_id"],
            request.digest,
            core.raw,
            raw,
            final,
        )

    try:
        yield dict(
            registry=registry,
            admin=admin,
            connection=verifier,
            make=make,
            payload=base,
            core=vectors["SCOPE"][1],
            signer=signer,
        )
    finally:
        signer.close()


def c_call(fixture, evidence, action="PREPARE", expected=None, connection=None):
    import json
    import os
    import time
    from pathlib import Path
    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    connection = connection or fixture["connection"]
    names = {
        "PREPARE": "prepare_operation_identity_v1",
        "REGISTER": "register_signed_scope_v1",
        "READ": "read_signed_scope_v1",
    }
    args = evidence.sql_arguments() if hasattr(evidence, "sql_arguments") else evidence
    before = snapshot(fixture["admin"])
    entry = dict(
        test=os.environ.get("PYTEST_CURRENT_TEST"),
        action=action,
        before=before,
        args=[v.hex() if isinstance(v, bytes) else v for v in args],
    )
    started = time.perf_counter()
    connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    try:
        result = connection.execute(
            "SELECT atis_soe." + names[action] + "(%s,%s,%s,%s,%s)", args
        ).fetchone()[0]
        connection.execute("COMMIT")
        entry.update(result=result, sqlstate="00000")
        assert expected is None, ("expected rejection", expected, result)
        return result
    except psycopg.Error as exc:
        entry.update(
            sqlstate=exc.sqlstate,
            message=str(exc),
            failed_state=connection.info.transaction_status.name,
        )
        connection.execute("ROLLBACK")
        assert expected is not None and expected in str(exc), str(exc)
        assert exc.sqlstate == "22000"
        assert snapshot(fixture["admin"]) == before
    finally:
        entry.update(
            after=snapshot(fixture["admin"]),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            transaction_state=connection.info.transaction_status.name,
        )
        with Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).open(
            "a", encoding="utf-8"
        ) as out:
            out.write(json.dumps(entry, default=str) + "\n")


def test_c_identity_registration_retry_and_ascii_digest(registration):
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    before = snapshot(f["admin"])
    assert b"caf\\u00e9" in e.final_request_bytes
    identity = c_call(f, e)
    assert identity["status"] == "IDENTITY_PREPARED"
    prepared = snapshot(f["admin"])
    assert c_call(f, e) == identity and snapshot(f["admin"]) == prepared
    registered = c_call(f, e, "REGISTER")
    assert registered["status"] == "REGISTERED"
    after = snapshot(f["admin"])
    assert c_call(f, e, "REGISTER") == registered
    assert c_call(f, e, "READ") == registered
    assert snapshot(f["admin"]) == after
    for name in (
        "capital_state",
        "liability",
        "operation_outcome",
        "scope_capital_state_v2",
    ):
        assert after[name] == before[name]


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("signature", "U3_SIGNATURE_REJECTED"),
        ("epoch", "U3_STALE_EPOCH"),
        ("closure", "U3_INCOMPLETE_CLOSURE"),
        ("digest", "U3_REGISTRATION_BINDING"),
        ("core", "U3_CONTEXT_REJECTED"),
        ("final", "U3_REGISTRATION_BINDING"),
        ("nonce", "U3_OPERATION_CONFLICT"),
        ("authority", "U3_POLICY_REJECTED"),
    ],
)
def test_c_registration_negatives(registration, fault, expected):
    import json

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    p = dict(f["payload"])
    if fault == "signature":
        bad = f["make"](forged=True)
    elif fault == "epoch":
        p["catalogue_epoch"] = 1
        bad = f["make"](p)
    elif fault == "closure":
        p["closure_digest"] = "0" * 64
        bad = f["make"](p)
    elif fault == "nonce":
        p["nonce"] = "c" * 64
        bad = f["make"](p)
    elif fault == "authority":
        p["signer_key_id"] = "FOREIGN"
        bad = f["make"](p)
    else:
        bad = list(e.sql_arguments())
        if fault == "digest":
            bad[1] = "0" * 64
        elif fault == "core":
            b = json.loads(e.operation_core_bytes)
            b["amount_minor"] = 2
            bad[2] = canonical_bytes(b)
        else:
            bad[4] = e.final_request_bytes + b" "
    c_call(f, bad, "REGISTER", expected)


def test_c_missing_identity_and_revocation(registration):
    f = registration
    e = f["make"]()
    c_call(f, e, "REGISTER", "U3_OPERATION_IDENTITY_MISSING")
    c_call(f, e)
    c_call(f, e, "REGISTER")
    f["admin"].execute(
        "UPDATE atis_soe.u3_verification_policy SET revoked=true WHERE capability='SCOPE'"
    )
    c_call(f, e, "REGISTER", "U3_POLICY_REJECTED")


@pytest.mark.parametrize(
    "table", ["u3_operation_nonce", "u3_registration_history", "u3_registration_member"]
)
def test_c_damaged_state_restricts(registration, table):
    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    # Deliberate superuser corruption fixture, never runtime authority.
    f["admin"].execute("ALTER TABLE atis_soe." + table + " DISABLE TRIGGER USER")
    try:
        f["admin"].execute("DELETE FROM atis_soe." + table)
        c_call(f, e, "REGISTER", "U3_REGISTRATION_STATE")
    finally:
        f["admin"].execute("ALTER TABLE atis_soe." + table + " ENABLE TRIGGER USER")


def test_c_native_failure_and_barrier_loss(registration):
    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    admin = f["admin"]
    config = admin.execute(
        "SELECT module_hashes FROM atis_soe.u3_verifier_configuration"
    ).fetchone()[0]
    admin.execute("UPDATE atis_soe.u3_verifier_configuration SET module_hashes='{}'")
    try:
        c_call(f, e, "REGISTER", "UNAVAILABLE")
    finally:
        import json

        admin.execute(
            "UPDATE atis_soe.u3_verifier_configuration SET module_hashes=%s",
            (json.dumps(config),),
        )
    c_call(f, e, "REGISTER")
    admin.execute("DELETE FROM atis_soe.scope_barrier WHERE issuer_domain='OPS'")
    c_call(f, e, "REGISTER", "U3_MISSING_BARRIER")


def test_c_concurrent_duplicate_registration(registration):
    import os
    import threading
    import time
    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    before = snapshot(f["admin"])
    first = f["connection"]
    second = psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_verifier"),
        autocommit=True,
    )
    first.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    value = first.execute(
        "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)", e.sql_arguments()
    ).fetchone()[0]
    result = {}

    def contender():
        second.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        try:
            result["value"] = second.execute(
                "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)",
                e.sql_arguments(),
            ).fetchone()[0]
            second.execute("COMMIT")
        except psycopg.Error as exc:
            result.update(
                sqlstate=exc.sqlstate,
                message=str(exc),
                transaction_state=second.info.transaction_status.name,
            )
            second.execute("ROLLBACK")

    t = threading.Thread(target=contender)
    t.start()
    try:
        deadline = time.monotonic() + 15
        while True:
            wait = (
                f["admin"]
                .execute(
                    "SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s",
                    (second.info.backend_pid,),
                )
                .fetchone()
            )
            if wait and wait[0] == "Lock":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        first.execute("COMMIT")
        t.join(15)
        assert not t.is_alive()
        assert result.get("sqlstate") == "40001", result
        assert c_call(f, e, "READ") == value
        after = snapshot(f["admin"])
        assert c_call(f, e, "REGISTER") == value and snapshot(f["admin"]) == after
        assert len(after["u3_scope_registration"]) == 1
        b_log(
            "c-concurrent-duplicate",
            before=before,
            after=after,
            wait=wait,
            loser=result,
        )
    finally:
        first.execute("ROLLBACK")
        if t.is_alive():
            second.cancel()
            t.join(15)
        second.close()


def test_c_commit_ack_loss_and_restart_seed(registration):
    import json
    import os
    import time
    from pathlib import Path
    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    before = snapshot(f["admin"])
    lost = psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_verifier"),
        autocommit=True,
    )
    lost.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    expected = lost.execute(
        "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)", e.sql_arguments()
    ).fetchone()[0]
    pid = lost.info.backend_pid
    lost.pgconn.send_query(b"COMMIT")
    while lost.pgconn.flush():
        pass
    lost.close()
    deadline = time.monotonic() + 10
    while (
        f["admin"]
        .execute("SELECT 1 FROM pg_stat_activity WHERE pid=%s", (pid,))
        .fetchone()
    ):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    actual = c_call(f, e, "READ")
    assert actual in (expected, dict(expected, status="IDENTITY_PREPARED"))
    after = snapshot(f["admin"])
    b_log("c-commit-ack-not-consumed", before=before, after=after, result=actual)
    # Exact state and wire inputs for the external crash/restart qualification.
    Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).with_name(
        "c-restart-seed.json"
    ).write_text(
        json.dumps(
            dict(
                args=[
                    x.hex() if isinstance(x, bytes) else x for x in e.sql_arguments()
                ],
                result=actual,
                state=after,
            ),
            default=str,
        )
    )


def test_c_cancellation_is_restrictive(registration):
    import threading
    import time
    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    admin = f["admin"]
    v = f["connection"]
    before = snapshot(admin)
    result = {}
    admin.execute("BEGIN")
    admin.execute(
        "SELECT * FROM atis_soe.u3_operation_identity WHERE operation_id='OP1' FOR UPDATE"
    )

    def attempt():
        v.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        try:
            result["value"] = v.execute(
                "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)",
                e.sql_arguments(),
            ).fetchone()[0]
        except psycopg.Error as exc:
            result.update(
                sqlstate=exc.sqlstate,
                message=str(exc),
                transaction_state=v.info.transaction_status.name,
            )
        finally:
            v.execute("ROLLBACK")

    t = threading.Thread(target=attempt)
    t.start()
    try:
        deadline = time.monotonic() + 4
        while True:
            wait = admin.execute(
                "SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s",
                (v.info.backend_pid,),
            ).fetchone()
            if wait and wait[0] == "Lock":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        v.cancel()
        t.join(10)
        assert (
            not t.is_alive() and result["sqlstate"] == "57014" and "value" not in result
        )
    finally:
        admin.execute("ROLLBACK")
        if t.is_alive():
            v.cancel()
            t.join(10)
    assert snapshot(admin) == before
    b_log("c-cancellation", before=before, after=snapshot(admin), result=result)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("request", "U3_OPERATION_CONFLICT"),
        ("parent", "U3_IDENTITY_CONTEXT"),
        ("deployment", "U3_CONTEXT_REJECTED"),
        ("account_revoked", "U3_IDENTITY_AUTHENTICATION:U3_POLICY_REJECTED"),
    ],
)
def test_c_binding_and_currentness(registration, fault, expected):
    import copy
    import json

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    p = copy.deepcopy(f["payload"])
    if fault == "request":
        core = json.loads(e.operation_core_bytes)
        core["amount_minor"] = 2
        bad = f["make"](core=canonical_bytes(core))
    elif fault == "parent":
        p["identity_assertions"][2]["payload"]["parent_account_identity_digest"] = (
            "0" * 64
        )
        bad = f["make"](p)
    elif fault == "deployment":
        # Keep the exact original signed envelope and substitute the operation core.
        core = json.loads(e.operation_core_bytes)
        core["deployment_id"] = "FOREIGN"
        bad = list(e.sql_arguments())
        bad[2] = canonical_bytes(core)
    else:
        f["admin"].execute(
            "UPDATE atis_soe.u3_verification_policy SET revoked=true WHERE capability='ACCOUNT'"
        )
        bad = e
    c_call(f, bad, "REGISTER", expected)


def test_c_runtime_and_legacy_bypass(registration):
    import os
    import psycopg
    from psycopg.conninfo import make_conninfo
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    before = snapshot(f["admin"])
    results = []
    with psycopg.connect(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_runtime"),
        autocommit=True,
    ) as runtime:
        for name in (
            "prepare_operation_identity_v1",
            "register_signed_scope_v1",
            "read_signed_scope_v1",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
                runtime.execute(
                    "SELECT atis_soe." + name + "(%s,%s,%s,%s,%s)", e.sql_arguments()
                )
            results.append(dict(query=name, sqlstate=caught.value.sqlstate))
        for query in [
            "SET ROLE atis_soe_registry_migrator",
            "DELETE FROM atis_soe.u3_operation_identity",
            "UPDATE atis_soe.u3_verification_policy SET revoked=false",
            "SELECT atis_soe.u3_registration_private('PREPARE','OP1','x','{}'::bytea,'{}'::bytea,'{}'::bytea)",
            "SELECT atis_soe.register_scope_envelope_v1('OP1','x','RESERVE',2,'x','x','{}'::bytea)",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
                runtime.execute(query)
            results.append(dict(query=query, sqlstate=caught.value.sqlstate))
    assert snapshot(f["admin"]) == before
    b_log(
        "c-runtime-denials", before=before, after=snapshot(f["admin"]), results=results
    )


@pytest.mark.parametrize("code", ["P0001", "40001", "40P01", "55P03"])
def test_c_registration_failure_atomicity(registration, code):
    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    # Reset only registration fixture rows; retain the authenticated prepared L08.
    f["admin"].execute("TRUNCATE atis_soe.u3_scope_registration CASCADE")
    f["admin"].execute(
        "ALTER TABLE atis_soe.u3_registration_history DISABLE TRIGGER USER"
    )
    f["admin"].execute(
        "DELETE FROM atis_soe.u3_registration_history WHERE action='REGISTER'"
    )
    f["admin"].execute(
        "ALTER TABLE atis_soe.u3_registration_history ENABLE TRIGGER USER"
    )
    f["admin"].execute(
        "CREATE FUNCTION atis_soe.c_test_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='"
        + code
        + "',MESSAGE='C_TEST_INSERT_FAILURE'; END $$"
    )
    f["admin"].execute(
        "CREATE TRIGGER c_test_fault BEFORE INSERT ON atis_soe.u3_registration_member FOR EACH ROW EXECUTE FUNCTION atis_soe.c_test_fault()"
    )
    before = snapshot(f["admin"])
    v = f["connection"]
    result = {}
    try:
        v.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        with pytest.raises(psycopg.Error) as caught:
            v.execute(
                "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)",
                e.sql_arguments(),
            )
        result = dict(
            sqlstate=caught.value.sqlstate,
            message=str(caught.value),
            transaction_state=v.info.transaction_status.name,
        )
        assert result["sqlstate"] == ("55000" if code == "P0001" else code)
        v.execute("ROLLBACK")
        assert snapshot(f["admin"]) == before
        b_log(
            "c-atomic-rollback-" + code,
            before=before,
            after=snapshot(f["admin"]),
            result=result,
        )
    finally:
        v.execute("ROLLBACK")
        f["admin"].execute(
            "DROP TRIGGER c_test_fault ON atis_soe.u3_registration_member"
        )
        f["admin"].execute("DROP FUNCTION atis_soe.c_test_fault()")


def test_c_adapter_and_financial_containment(registration):
    import os
    import psycopg
    from psycopg.conninfo import make_conninfo
    from automated_trading_bot.stage5.commit_boundary.psycopg_adapter import (
        ProtectedU3Registrar,
    )
    from automated_trading_bot.stage5.scope_registry import (
        RegistryCommitUncertain,
        submit_registration,
    )
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    before = snapshot(f["admin"])
    registrar = ProtectedU3Registrar(
        make_conninfo(os.environ["ATIS_U3_TEST_DSN"], user="u045_verifier")
    )
    try:
        # Explicit bounded initialization, no changed default request budget.
        registrar._raw.execute("SET statement_timeout='30s'")
        registrar._raw.execute(
            "SELECT atis_soe.verify_u3_signature('ACCOUNT','{}'::bytea,'{}'::bytea)"
        )
        registrar._raw.execute("SET statement_timeout='5s'")
        assert registrar.prepare_identity(e)["status"] == "IDENTITY_PREPARED"
        assert registrar.register_scope(e) == registrar.read_exact(e)
    finally:
        registrar.close()

    class LostAcknowledgement:
        info = f["connection"].info

        def execute(self, query, args=None):
            result = f["connection"].execute(query, args)
            if query == "COMMIT":
                raise psycopg.OperationalError(
                    "synthetic lost acknowledgement after actual commit"
                )
            return result

    with pytest.raises(RegistryCommitUncertain):
        submit_registration(LostAcknowledgement(), e, action="REGISTER")
    assert c_call(f, e, "READ")["status"] == "REGISTERED"
    for query in [
        "SELECT * FROM atis_soe.commit_capital('A','digest','scope',1,'GBP',1,1,'nonce',1,'attempt','time')",
        "SELECT * FROM atis_soe.commit_capital_v2('A','digest',1,'GBP','nonce',1,'attempt','time')",
    ]:
        f["admin"].execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        try:
            with pytest.raises(
                psycopg.errors.InsufficientPrivilege, match="U1_FINANCIAL_CONTAINMENT"
            ):
                f["admin"].execute(query)
        finally:
            f["admin"].execute("ROLLBACK")
    assert snapshot(f["admin"]) == before
    b_log("c-adapter-financial", before=before, after=snapshot(f["admin"]))


def test_c_nonce_cannot_cross_operation(registration):
    import copy
    import json
    from automated_trading_bot.stage5.identity_scope import (
        IdentityRecordV1,
        SCOPE_KINDS,
        digest_list,
    )

    f = registration
    e = f["make"]()
    c_call(f, e)
    core = json.loads(e.operation_core_bytes)
    core["operation_id"] = "OP2"
    p = copy.deepcopy(f["payload"])
    p["operation_id"] = "OP2"
    op = next(
        x["record"]
        for x in f["registry"]["changes"]["identities"]
        if x["record"]["dimension"] == "OPERATION" and x["record"]["value"] == "OP2"
    )
    p["leaf_identities"][-1] = op
    identities = [IdentityRecordV1.parse(x) for x in p["leaf_identities"]]
    assert identities == sorted(
        identities, key=lambda x: (SCOPE_KINDS.index(x.dimension), x.digest)
    )
    p["leaf_set_digest"] = digest_list(x.digest for x in identities)
    p["closure_digest"] = p["leaf_set_digest"]
    bad = f["make"](p, core=canonical_bytes(core))
    c_call(f, bad, "PREPARE", "U3_OPERATION_NONCE_CONFLICT")
    p["nonce"] = "e" * 64
    valid = f["make"](p, core=canonical_bytes(core))
    c_call(f, valid)
    c_call(f, valid, "REGISTER")


@pytest.mark.parametrize(
    "key",
    [
        "scope_envelope",
        "scope_envelope_digest",
        "operation_core_digest",
        "operation_class",
        "catalogue_epoch",
    ],
)
def test_c_mixed_metadata_sql_rejection(registration, key):
    import copy
    import json
    from automated_trading_bot.stage5.identity_scope import (
        AuthenticatedScopeEnvelopeV1,
        OperationCoreV1,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    body = json.loads(e.operation_core_bytes)
    body["immutable_business_parameters"][key] = "legacy"
    core = OperationCoreV1(canonical_bytes(body))
    p = copy.deepcopy(f["payload"])
    p["core_digest"] = core.digest
    unsigned = signed_container(p, bytes(64))
    raw = signed_container(
        p, f["signer"].sign(AuthenticatedScopeEnvelopeV1(unsigned).signing_bytes)
    )
    params = dict(body["immutable_business_parameters"])
    params["atis_scope_envelope_digest"] = AuthenticatedScopeEnvelopeV1(raw).digest
    request = OperationRequest.create(
        operation_id=body["operation_id"],
        request_type=body["request_type"],
        scope=body["legacy_scope_hint"],
        amount_minor=body["amount_minor"],
        currency=body["currency"],
        expected_generation=body["expected_generation"],
        expected_epoch=body["expected_epoch"],
        immutable_parameters=params,
    )
    final = json.dumps(
        request._body(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    c_call(
        f,
        (e.operation_id, request.digest, core.raw, raw, final),
        "REGISTER",
        "U3_REGISTRATION_BINDING",
    )


def test_c_restart_seed(registration):
    import json
    import os
    from pathlib import Path
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    prepared = c_call(f, e)
    p = dict(f["payload"], nonce="f" * 64)
    conflict = f["make"](p)
    c_call(f, conflict, "PREPARE", "U3_OPERATION_CONFLICT")
    Path(os.environ["ATIS_U3_TEST_EVIDENCE"]).with_name(
        "c-prepared-seed.json"
    ).write_text(
        json.dumps(
            dict(
                args=[
                    x.hex() if isinstance(x, bytes) else x for x in e.sql_arguments()
                ],
                conflict=[
                    x.hex() if isinstance(x, bytes) else x
                    for x in conflict.sql_arguments()
                ],
                prepared=prepared,
                state=snapshot(f["admin"]),
            ),
            default=str,
        )
    )

def test_c_actual_native_dll_failure(registration):
    from hashlib import sha256

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    query = "SELECT pg_get_functiondef('atis_soe.u3_private_verify(text,bytea,bytea)'::regprocedure)"
    original = f["admin"].execute(query).fetchone()[0]
    marker = "        result=verify_signature("
    assert original.count(marker) == 1
    fault = original.replace(
        marker,
        "        import ctypes\n        ctypes.WinDLL('ATIS_TASK045C_deliberately_missing.dll')\n"
        + marker,
    )
    f["admin"].execute(fault)
    try:
        c_call(f, e, "REGISTER", "UNAVAILABLE")
    finally:
        f["admin"].execute(original)
    restored = f["admin"].execute(query).fetchone()[0]
    assert restored == original
    c_call(f, e, "REGISTER")
    b_log(
        "c-native-dll-failure",
        original_definition_sha256=sha256(original.encode()).hexdigest(),
        restored_definition_sha256=sha256(restored.encode()).hexdigest(),
    )


def test_c_foreign_scope_issuer(registration):
    import json
    from hashlib import sha256
    from automated_trading_bot.stage5.identity_scope import (
        AuthenticatedScopeEnvelopeV1,
        parse_container,
    )
    from automated_trading_bot.stage5.commit_boundary.operation_envelope import (
        OperationRequest,
    )

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    payload, _ = parse_container(e.signed_scope_envelope_bytes)
    signing = AuthenticatedScopeEnvelopeV1(e.signed_scope_envelope_bytes).signing_bytes
    domain = signing[: -len(canonical_bytes(payload))]
    payload["signer_id"] = "FOREIGN"
    raw = signed_container(payload, f["signer"].sign(domain + canonical_bytes(payload)))
    body = json.loads(e.operation_core_bytes)
    parameters = dict(body["immutable_business_parameters"])
    parameters["atis_scope_envelope_digest"] = sha256(
        b"ATIS-S5-SIGNED-SCOPE-ENVELOPE-V1\0" + raw
    ).hexdigest()
    request = OperationRequest.create(
        operation_id=body["operation_id"],
        request_type=body["request_type"],
        scope=body["legacy_scope_hint"],
        amount_minor=body["amount_minor"],
        currency=body["currency"],
        expected_generation=body["expected_generation"],
        expected_epoch=body["expected_epoch"],
        immutable_parameters=parameters,
    )
    final = json.dumps(
        request._body(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    c_call(
        f,
        (e.operation_id, request.digest, e.operation_core_bytes, raw, final),
        "REGISTER",
        "U3_CANONICAL_REJECTED",
    )

def test_c_preparation_requires_prior_commit(registration):
    import psycopg
    from test_stage5_postgres_scope_verification import snapshot

    f = registration
    e = f["make"]()
    c_call(f, e)
    c_call(f, e, "REGISTER")
    f["admin"].execute("TRUNCATE atis_soe.u3_operation_identity CASCADE")
    before = snapshot(f["admin"])
    v = f["connection"]
    record = {}
    v.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    try:
        record["preparation"] = v.execute(
            "SELECT atis_soe.prepare_operation_identity_v1(%s,%s,%s,%s,%s)",
            e.sql_arguments(),
        ).fetchone()[0]
        try:
            record["registration"] = v.execute(
                "SELECT atis_soe.register_signed_scope_v1(%s,%s,%s,%s,%s)",
                e.sql_arguments(),
            ).fetchone()[0]
        except psycopg.Error as exc:
            record.update(
                sqlstate=exc.sqlstate,
                message=str(exc),
                transaction_state=v.info.transaction_status.name,
            )
        assert record.get(
            "sqlstate"
        ) == "25001" and "U3_STANDALONE_TRANSACTION_REQUIRED" in record.get(
            "message", ""
        ), record
    finally:
        v.execute("ROLLBACK")
        after = snapshot(f["admin"])
        b_log("c-prior-commit-boundary", before=before, after=after, result=record)
        assert after == before
    c_call(f, e)
    c_call(f, e, "REGISTER")

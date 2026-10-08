"""Static migration/design conformance; no PostgreSQL execution."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_LF_SHA256 = "b4c1603ea6b26342aa129f4dd57cacf988815327637750689728da01fd01a5fa"
HISTORICAL_CRLF_SHA256 = "e7e5563c061d7a7b3afffba98b492d93acc4c8c2d2b5b6c1dbcfbd3bb937eebb"
PROTECTED_LF_LINE_COUNT = 351


def _load(name):
    text = (ROOT / "docs/m1-closure" / name).read_text(encoding="utf-8")
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def _validate_design_identity(
    migration, source_bytes, *,
    current_sha256=PROTECTED_LF_SHA256,
    historical_sha256=HISTORICAL_CRLF_SHA256,
):
    assert isinstance(source_bytes, bytes)
    assert b"\r" not in source_bytes
    assert source_bytes.count(b"\n") == PROTECTED_LF_LINE_COUNT
    assert source_bytes.decode("utf-8").encode("utf-8") == source_bytes
    assert hashlib.sha256(source_bytes).hexdigest() == current_sha256
    historical_bytes = source_bytes.replace(b"\n", b"\r\n")
    assert historical_bytes.replace(b"\r\n", b"\n") == source_bytes
    assert hashlib.sha256(historical_bytes).hexdigest() == historical_sha256
    assert migration["design_sha256"] == historical_sha256


def _validate(migration, design):
    source = ROOT / "docs/m1-closure/M1-event-storage-design.md"
    _validate_design_identity(migration, source.read_bytes())
    assert migration["source_sha256"] == design["source_sha256"]
    assert migration["design_id"] == design["design_id"]
    assert migration["migration_id"] == "M1-EVENT-STORAGE-0001"
    assert migration["schema_version"] == 1 and migration["predecessor"] is None
    assert migration["status"] == "SPECIFICATION_ONLY"
    order = migration["creation_order"]
    assert order == ["event_history", "outbox", "inbox"]
    assert set(migration["tables"]) == set(design["tables"])
    assert migration["extra_indexes"] == []  # Approved design needs key-backed indexes only.
    for name, expected in design["tables"].items():
        table = migration["tables"][name]
        assert table["columns"] == [
            {"name": key, "type": value, "not_null": key not in expected["nullable"]}
            for key, value in expected["columns"].items()
        ]
        assert table["primary_key"] == expected["primary_key"]
        assert table["unique"] == expected["unique"]
        assert table["foreign_keys"] == [
            {"column": col, "table": target, "target": key,
             "on_delete": "RESTRICT", "on_update": "RESTRICT"}
            for col, target, key in expected["foreign_keys"]
        ]
        assert migration["ownership"][name] == expected["owner"]
        assert table["defaults"] == ({} if name == "event_history" else {"attempt_count": 0, "status": "PENDING"})
        for fk in table["foreign_keys"]:
            assert order.index(fk["table"]) <= order.index(name)
            assert migration["tables"][fk["table"]]["primary_key"] == [fk["target"]]
        checks = migration["checks"][name]
        for col, typ in expected["columns"].items():
            if typ == "text":
                expression = f"length(btrim({col})) > 0"
                if col in expected["nullable"]:
                    expression = f"{col} IS NULL OR " + expression
                assert checks["nonblank_" + col] == expression
    history_checks = migration["checks"]["event_history"]
    for key, expression in {
        "positive_stream": "stream_version > 0", "positive_schema": "schema_version > 0",
        "nonnegative_epoch": "authority_epoch >= 0", "hash_size": "octet_length(content_sha256) = 32",
        "previous_hash_size": "previous_sha256 IS NULL OR octet_length(previous_sha256) = 32",
        "integrity_scheme": "integrity_scheme = 'SHA256-LP-UTF8-v1'",
        "predecessor_shape": "(stream_version = 1 AND previous_event_id IS NULL AND previous_sha256 IS NULL) OR (stream_version > 1 AND previous_event_id IS NOT NULL AND previous_sha256 IS NOT NULL)",
    }.items():
        assert history_checks[key] == expression
    for name, terminal, stamp in [("outbox", "DELIVERED", "delivered_at"), ("inbox", "APPLIED", "applied_at")]:
        checks = migration["checks"][name]
        assert checks["allowed_status"] == "status IN (" + ", ".join(repr(x) for x in design["statuses"][name]) + ")"
        assert checks["nonnegative_attempts"] == "attempt_count >= 0"
        assert checks["retry_time"] == "status <> 'RETRY' OR next_attempt_at IS NOT NULL"
        assert checks["attempt_time"] == "attempt_count = 0 OR last_attempt_at IS NOT NULL"
        assert checks["success_time"] == f"(status = '{terminal}') = ({stamp} IS NOT NULL)"
    assert migration["apply"] == {
        "single_transaction": True, "initial_only": True, "existing_objects": "FAIL_CLOSED",
        "same_identity_different_checksum": "REJECT", "record_version_after_validation": True,
    }
    assert migration["rollback"] == {
        "destructive_down_allowed": False, "before_commit": "ROLLBACK_TRANSACTION",
        "after_commit": "REVIEWED_FORWARD_CORRECTION",
    }
    assert set(migration["future_enforcement"]) == {
        "append_only_history", "immutable_workflow_identity", "terminal_state_nonregression",
        "attributable_attempt_history", "same_stream_contiguous_predecessor",
        "producer_state_event_outbox_atomicity", "consumer_inbox_effect_atomicity",
        "no_replay_financial_authority",
    }


def _assert_design_identity_adversaries(source_bytes, migration):
    for mutation in (
        "design_content", "current_digest", "historical_digest",
        "specification_digest", "mixed_endings", "unsupported_normalization",
        "historical_only",
    ):
        variant = source_bytes
        variant_migration = copy.deepcopy(migration)
        expected = {}
        if mutation == "design_content":
            variant = variant.replace(b"PostgreSQL", b"SQLite", 1)
        elif mutation == "current_digest":
            expected["current_sha256"] = "0" * 64
        elif mutation == "historical_digest":
            expected["historical_sha256"] = "0" * 64
        elif mutation == "specification_digest":
            variant_migration["design_sha256"] = "0" * 64
        elif mutation == "mixed_endings":
            variant = variant.replace(b"\n", b"\r\n", 1)
        elif mutation == "unsupported_normalization":
            variant = variant.rstrip(b"\n")
        else:
            variant = variant.replace(b"\n", b"\r\n")
            assert hashlib.sha256(variant).hexdigest() == HISTORICAL_CRLF_SHA256
        with pytest.raises(AssertionError):
            _validate_design_identity(variant_migration, variant, **expected)


def test_initial_migration_matches_approved_design():
    migration = _load("M1-initial-migration-specification.md")
    _validate(migration, _load("M1-event-storage-design.md"))
    source = ROOT / "docs/m1-closure/M1-event-storage-design.md"
    source_bytes = source.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == PROTECTED_LF_SHA256
    assert hashlib.sha256(source_bytes.replace(b"\n", b"\r\n")).hexdigest() == HISTORICAL_CRLF_SHA256
    _assert_design_identity_adversaries(source_bytes, migration)


@pytest.mark.parametrize("mutation", ["event_key", "stream_unique", "outbox_key", "handler_key", "causality_null", "type", "fk", "check", "retry", "order", "rollback", "drift", "immutability", "ownership"])
def test_migration_rejects_weakened_constraint(mutation):
    migration = copy.deepcopy(_load("M1-initial-migration-specification.md"))
    tables = migration["tables"]
    if mutation == "event_key":
        tables["event_history"]["primary_key"] = ["idempotency_key"]
    elif mutation == "stream_unique":
        tables["event_history"]["unique"] = []
    elif mutation == "outbox_key":
        tables["outbox"]["primary_key"] = ["destination"]
    elif mutation == "handler_key":
        tables["inbox"]["primary_key"].append("handler_version")
    elif mutation == "causality_null":
        next(x for x in tables["event_history"]["columns"] if x["name"] == "causation_id")["not_null"] = False
    elif mutation == "type":
        tables["event_history"]["columns"][0]["type"] = "text"
    elif mutation == "fk":
        tables["outbox"]["foreign_keys"][0]["on_delete"] = "CASCADE"
    elif mutation == "check":
        migration["checks"]["event_history"]["hash_size"] = "TRUE"
    elif mutation == "retry":
        migration["checks"]["inbox"]["retry_time"] = "TRUE"
    elif mutation == "order":
        migration["creation_order"].reverse()
    elif mutation == "rollback":
        migration["rollback"]["destructive_down_allowed"] = True
    elif mutation == "drift":
        migration["apply"]["same_identity_different_checksum"] = "ACCEPT"
    elif mutation == "immutability":
        migration["future_enforcement"].remove("append_only_history")
    else:
        migration["ownership"]["inbox"] = "transport grants execution authority"
    with pytest.raises(AssertionError):
        _validate(migration, _load("M1-event-storage-design.md"))

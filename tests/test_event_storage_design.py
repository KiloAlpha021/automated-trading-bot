"""Static specification controls, not PostgreSQL/runtime acceptance tests."""
import copy
import json
from pathlib import Path

import pytest


def _design():
    text = (Path(__file__).resolve().parents[1] / "docs/m1-closure/M1-event-storage-design.md").read_text(encoding="utf-8")
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def _validate(design):
    assert design["status"] == "SPECIFICATION_ONLY"
    assert design["source_sha256"] == "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
    tables = design["tables"]
    assert set(tables) == {"event_history", "outbox", "inbox"}
    for table in tables.values():
        assert table["owner"]
        assert set(table["primary_key"]) <= table["columns"].keys()
        assert not set(table["primary_key"]) & set(table["nullable"])
        for field, target, key in table["foreign_keys"]:
            assert table["columns"][field] == tables[target]["columns"][key]
            assert tables[target]["primary_key"] == [key]
    history = tables["event_history"]
    assert history["primary_key"] == ["event_id"]
    assert history["append_only"] is True
    assert ["environment", "stream_kind", "stream_id", "stream_version"] in history["unique"]
    expected = {
        "event_id": "uuid", "stream_id": "uuid", "stream_version": "bigint",
        "correlation_id": "uuid", "causation_id": "uuid", "schema_version": "integer",
        "occurred_at": "timestamptz", "recorded_at": "timestamptz",
        "payload_bytes": "bytea", "content_sha256": "bytea", "previous_sha256": "bytea",
        "event_type": "text", "idempotency_key": "text", "operation_scope": "text",
        "authority_epoch": "bigint", "source_id": "text", "actor_id": "text",
        "release_id": "text", "config_id": "text", "integrity_scheme": "text",
    }
    assert expected.items() <= history["columns"].items()
    assert tables["outbox"]["primary_key"] == ["event_id", "destination"]
    assert tables["inbox"]["primary_key"] == ["event_id", "handler_id"]
    assert tables["inbox"]["columns"]["handler_version"] == "text"
    for name in ("outbox", "inbox"):
        assert ["event_id", "event_history", "event_id"] in tables[name]["foreign_keys"]
        assert {"status", "attempt_count", "next_attempt_at", "last_attempt_at", "last_error_code"} <= tables[name]["columns"].keys()
        assert "QUARANTINED" in design["statuses"][name]
    assert design["controls"] == {
        "event_identity": "EventId", "handler_upgrade_reapplies": False,
        "global_order": False, "delivery": "at-least-once", "history_mutable": False,
        "replay_financial_authority": False, "atomic_state_and_outbox": True,
        "atomic_inbox_and_effect": True,
    }
    assert set(design["source_map"].values()) == {"6.1", "6.2", "7 IMP-026", "8", "8.1", "17.1"}


def test_initial_event_storage_design_controls():
    _validate(_design())


@pytest.mark.parametrize("mutation", ["identity", "stream", "causality", "handler", "foreign_key", "replay", "atomicity", "integrity"])
def test_design_rejects_weakened_control(mutation):
    design = copy.deepcopy(_design())
    tables = design["tables"]
    if mutation == "identity":
        tables["event_history"]["primary_key"] = ["idempotency_key"]
    elif mutation == "stream":
        tables["event_history"]["unique"] = []
    elif mutation == "causality":
        del tables["event_history"]["columns"]["causation_id"]
    elif mutation == "handler":
        tables["inbox"]["primary_key"].append("handler_version")
    elif mutation == "foreign_key":
        tables["outbox"]["foreign_keys"] = []
    elif mutation == "replay":
        design["controls"]["replay_financial_authority"] = True
    elif mutation == "atomicity":
        design["controls"]["atomic_inbox_and_effect"] = False
    else:
        del tables["event_history"]["columns"]["content_sha256"]
    with pytest.raises(AssertionError):
        _validate(design)

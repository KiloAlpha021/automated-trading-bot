"""Behavioral validation for the bounded ATIS programme-control baseline."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
import json
from pathlib import Path
import re
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "docs/programme/programme-control.json"
SCHEMA_PATH = ROOT / "docs/programme/programme-control.schema.json"
GIT_ID = re.compile(r"^[0-9a-f]{40}$")
SHA256_ID = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_BASELINE = {
    "commit": "12560025bbc87ee237bb2252215305f8d2fc9e47",
    "tree": "404ad9a9ae0454ad36040c7bc690d7ec3eeaf968",
}
EXPECTED_SOURCE_IDENTITIES = {
    "docs/m1-closure/closure-manifest.json": "9c653dc2183700c86191e8f14bfd4db020f38a5b",
    "docs/stage2/gate-s02-01.json": "850ce45a88d30b9348686493355fb681a464759f",
    "docs/stage2/stage2-freeze-record.json": "5a4425eeb79c87072e8c498981c55565c386c677",
}
POSITIVE_EVIDENCE_STATES = {"CURRENT"}
FORBIDDEN_AUTHORITY = {
    "STAGE3_IMPLEMENTATION",
    "SHADOW_TRADING",
    "PAPER_TRADING",
    "LIVE_TRADING",
    "BROKER_EXECUTION",
    "OMS_EXECUTION",
    "RISK_APPROVAL",
    "LEDGER_AUTHORITY",
    "CAPITAL_ALLOCATION",
    "FINANCIAL_EFFECTS",
    "AI_TRADING_AUTHORITY",
}


class ControlValidationError(ValueError):
    """Raised when programme-control semantics are unsafe or incoherent."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ControlValidationError("top-level value must be an object")
    return value


def _resolve_ref(schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ControlValidationError("only local schema references are permitted")
    value: Any = schema
    for part in reference[2:].split("/"):
        value = value[part]
    if type(value) is not dict:
        raise ControlValidationError("schema reference must resolve to an object")
    return value


def _schema_validate(value: Any, rule: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in rule:
        _schema_validate(value, _resolve_ref(root, rule["$ref"]), root, path)
        return
    if "anyOf" in rule:
        failures = []
        for option in rule["anyOf"]:
            try:
                _schema_validate(value, option, root, path)
                return
            except ControlValidationError as exc:
                failures.append(str(exc))
        raise ControlValidationError(f"{path}: no anyOf branch matched: {failures}")
    expected_type = rule.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "integer": int,
        "null": type(None),
    }
    if expected_type is not None and type(value) is not type_map[expected_type]:
        raise ControlValidationError(f"{path}: expected {expected_type}")
    if "const" in rule and value != rule["const"]:
        raise ControlValidationError(f"{path}: value differs from const")
    if "enum" in rule and value not in rule["enum"]:
        raise ControlValidationError(f"{path}: value is outside enum")
    if type(value) is str:
        if len(value) < rule.get("minLength", 0):
            raise ControlValidationError(f"{path}: string is too short")
        if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
            raise ControlValidationError(f"{path}: string does not match pattern")
    if type(value) is list:
        if len(value) < rule.get("minItems", 0):
            raise ControlValidationError(f"{path}: array has too few items")
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            raise ControlValidationError(f"{path}: array has too many items")
        if rule.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ControlValidationError(f"{path}: array items are not unique")
        if "items" in rule:
            for index, item in enumerate(value):
                _schema_validate(item, rule["items"], root, f"{path}[{index}]")
    if type(value) is dict:
        required = set(rule.get("required", []))
        missing = required - value.keys()
        if missing:
            raise ControlValidationError(f"{path}: missing keys {sorted(missing)}")
        properties = rule.get("properties", {})
        if rule.get("additionalProperties") is False:
            extras = value.keys() - properties.keys()
            if extras:
                raise ControlValidationError(f"{path}: unknown keys {sorted(extras)}")
        for key, item in value.items():
            if key in properties:
                _schema_validate(item, properties[key], root, f"{path}.{key}")


def _all_records(control: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *control["programme_control_ledger"]["records"],
        *control["milestone_gate_register"]["records"],
        *control["decision_register"]["records"],
        *control["risk_blocker_deferral_register"]["deferred_work"],
        *control["evidence_invalidation_register"]["records"],
        *control["provenance_gap_register"]["records"],
        *control["reality_assumption_register"]["records"],
    ]


def _record_id(record: dict[str, Any]) -> str:
    for key in ("record_id", "gate_id", "gap_id"):
        if key in record:
            return str(record[key])
    raise ControlValidationError("record has no stable identity")


def _git_blob(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def validate_control(control: dict[str, Any]) -> None:
    schema = _load(SCHEMA_PATH)
    _schema_validate(control, schema, schema)
    if control["source_baseline"] != EXPECTED_BASELINE:
        raise ControlValidationError("protected baseline identity mismatch")
    records = _all_records(control)
    ids = [_record_id(record) for record in records]
    if len(ids) != len(set(ids)):
        raise ControlValidationError("duplicate record ID")

    ledger = control["programme_control_ledger"]["records"]
    ledger_ids = {record["record_id"] for record in ledger}
    deferral_ids = {
        record["record_id"]
        for record in control["risk_blocker_deferral_register"]["deferred_work"]
    }
    evidence = control["evidence_invalidation_register"]["records"]
    evidence_ids = {record["record_id"] for record in evidence}
    gate_ids = {record["gate_id"] for record in control["milestone_gate_register"]["records"]}
    decision_ids = {record["record_id"] for record in control["decision_register"]["records"]}
    resolvable = ledger_ids | deferral_ids | evidence_ids | gate_ids | decision_ids

    for record in ledger:
        if not record["source_authority"]:
            raise ControlValidationError("ledger record lacks source authority")
        if record["control_lifecycle_status"] not in {"PROPOSED", "BLOCKED", "DEFERRED"} and not record["owner"]:
            raise ControlValidationError("active control lacks owner")
        for reference in record["dependencies"] + record["deferred_work_references"] + record["gate_consumers"]:
            if reference not in resolvable:
                raise ControlValidationError(f"unresolved internal reference: {reference}")

    evidence_by_id = {record["record_id"]: record for record in evidence}
    for record in evidence:
        identity = record["exact_identity"]
        identity_kind = record["identity_kind"]
        if identity_kind in {"GIT_BLOB", "GIT_COMMIT"} and (
            not isinstance(identity, str) or GIT_ID.fullmatch(identity) is None
        ):
            raise ControlValidationError("Git evidence identity mismatch")
        if identity_kind == "SHA256" and (
            not isinstance(identity, str) or SHA256_ID.fullmatch(identity) is None
        ):
            raise ControlValidationError("SHA-256 evidence identity mismatch")
        if identity_kind == "NOT_RECORDED" and identity is not None:
            raise ControlValidationError("unrecorded evidence has an identity")
        expected_identity = EXPECTED_SOURCE_IDENTITIES.get(record["source"])
        if expected_identity is not None and identity != expected_identity:
            raise ControlValidationError("protected evidence identity mismatch")
        if record["record_id"] in record["dependencies"]:
            raise ControlValidationError("self-referential evidence")
        if record["supersedes"] == record["record_id"]:
            raise ControlValidationError("evidence cannot supersede itself")
        for reference in record["dependencies"]:
            if reference.startswith("PC-") and reference not in evidence_by_id:
                raise ControlValidationError(f"unresolved evidence dependency: {reference}")
        if record["evidence_state"] in {"UNKNOWN", "STALE", "INVALIDATED"}:
            for gate in control["milestone_gate_register"]["records"]:
                if gate["decision"] == "PASS" and record["record_id"] in gate["required_evidence"]:
                    raise ControlValidationError("non-current evidence satisfies a positive gate")

    active_decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for record in control["decision_register"]["records"]:
        if record["state"] == "APPROVED":
            key = (record["governed_question"], record["scope"])
            if key in active_decisions and active_decisions[key]["decision"] != record["decision"]:
                raise ControlValidationError("contradictory active decisions")
            active_decisions[key] = record
        if record["supersedes"] is not None:
            if record["supersedes"] == record["record_id"]:
                raise ControlValidationError("decision cannot supersede itself")
            if record["supersedes"] not in decision_ids:
                raise ControlValidationError("unresolved decision supersession")

    for gate in control["milestone_gate_register"]["records"]:
        for reference in gate["required_evidence"]:
            if reference not in evidence_ids:
                raise ControlValidationError(f"unresolved gate evidence: {reference}")
        if gate["decision"] == "PASS" and not gate["authority_granted"]:
            raise ControlValidationError("PASS cannot infer unlisted authority")
        if FORBIDDEN_AUTHORITY & set(gate["authority_granted"]):
            raise ControlValidationError("gate grants prohibited authority")

    for record in control["risk_blocker_deferral_register"]["deferred_work"]:
        if not record["owner"] or not record["reconsideration_closure_trigger"]:
            raise ControlValidationError("deferred item lacks owner or trigger")

    for record in control["reality_assumption_register"]["records"]:
        prefix = "PC-ASM-" if record["record_type"] == "ASSUMPTION" else "PC-RG-"
        if not record["record_id"].startswith(prefix):
            raise ControlValidationError("assumption/reality-gap identity mismatch")
        if record["record_type"] == "ASSUMPTION" and record["status"] == "VALIDATED" and not record["evidence"]:
            raise ControlValidationError("assumption represented as fact without evidence")
        if record["record_type"] == "REALITY_GAP" and record["status"] == "CLOSED" and not record["evidence"]:
            raise ControlValidationError("reality gap closed without evidence")


def _control() -> dict[str, Any]:
    return _load(CONTROL_PATH)


def test_json_and_schema_parse_with_duplicate_key_rejection() -> None:
    control = _control()
    schema = _load(SCHEMA_PATH)
    assert control["schema_version"] == 1
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    with pytest.raises(ControlValidationError, match="duplicate JSON key"):
        json.loads('{"id":"one","id":"two"}', object_pairs_hook=_unique_object)


def test_schema_validates_canonical_artifact() -> None:
    validate_control(_control())


def test_serialization_is_exact_and_deterministic() -> None:
    raw = CONTROL_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    rendered = json.dumps(_control(), ensure_ascii=False, indent=2) + "\n"
    assert raw == rendered.encode("utf-8")


def test_stable_ids_are_unique_and_canonical_ids_are_preserved() -> None:
    control = _control()
    ids = [_record_id(record) for record in _all_records(control)]
    assert len(ids) == len(set(ids))
    requirements = control["programme_control_ledger"]["records"][2]["requirement_ids"]
    assert requirements == [f"ATIS-R-{number:03d}" for number in range(1, 24)]
    deferrals = control["risk_blocker_deferral_register"]["deferred_work"]
    assert [record["record_id"] for record in deferrals] == [f"S2-DEF-{number:03d}" for number in range(1, 12)]


def test_cross_references_resolve_and_active_controls_have_authority() -> None:
    validate_control(_control())


def test_control_and_evidence_lifecycles_are_distinct() -> None:
    control = _control()
    assert control["control_lifecycle_vocabulary"] != control["evidence_lifecycle_vocabulary"]
    local = control["programme_control_ledger"]["records"][-1]
    assert local["control_lifecycle_status"] == "IMPLEMENTED"
    assert local["evidence_lifecycle_status"] == "UNKNOWN"


@pytest.mark.parametrize("state", ["UNKNOWN", "STALE", "INVALIDATED"])
def test_noncurrent_evidence_cannot_satisfy_positive_gate(state: str) -> None:
    candidate = deepcopy(_control())
    target = candidate["evidence_invalidation_register"]["records"][2]
    target["evidence_state"] = state
    with pytest.raises(ControlValidationError, match="non-current evidence"):
        validate_control(candidate)


def test_supersession_is_resolved_and_non_self_referential() -> None:
    candidate = deepcopy(_control())
    evidence = candidate["evidence_invalidation_register"]["records"][0]
    evidence["supersedes"] = evidence["record_id"]
    with pytest.raises(ControlValidationError, match="supersede itself"):
        validate_control(candidate)


def test_contradictory_active_decisions_reject() -> None:
    candidate = deepcopy(_control())
    duplicate = deepcopy(candidate["decision_register"]["records"][0])
    duplicate["record_id"] = "PC-DEC-002"
    duplicate["decision"] = "LOCAL_IMPLEMENTATION_REJECTED"
    candidate["decision_register"]["records"].append(duplicate)
    with pytest.raises(ControlValidationError, match="contradictory active decisions"):
        validate_control(candidate)


def test_deferred_items_require_owner_and_trigger() -> None:
    candidate = deepcopy(_control())
    candidate["risk_blocker_deferral_register"]["deferred_work"][0]["owner"] = ""
    with pytest.raises(ControlValidationError):
        validate_control(candidate)


def test_gate_pass_does_not_manufacture_authority() -> None:
    candidate = deepcopy(_control())
    candidate["milestone_gate_register"]["records"][1]["authority_granted"].append("LIVE_TRADING")
    with pytest.raises(ControlValidationError, match="prohibited authority"):
        validate_control(candidate)


def test_protected_records_are_referenced_and_exact() -> None:
    assert _git_blob(ROOT / "docs/stage2/s20-control.json") == "48c94c3c411913c608740a1905449ec5511aba26"
    assert _git_blob(ROOT / "docs/stage2/s27-evidence.json") == "ae1dbcca0c19b13a634ed6c503b975a40a9b4bc8"
    assert _git_blob(ROOT / "tests/test_stage2_integration.py") == "e6dd482363ee0b5dcf898632570af99f04eb8a55"
    assert _git_blob(ROOT / "docs/stage2/gate-s02-01.json") == "850ce45a88d30b9348686493355fb681a464759f"
    assert _git_blob(ROOT / "docs/stage2/stage2-freeze-record.json") == "5a4425eeb79c87072e8c498981c55565c386c677"


def test_unknown_historical_timestamps_are_not_fabricated() -> None:
    control = _control()
    assert all(record["valid_from"] is None and record["review_by"] is None for record in control["evidence_invalidation_register"]["records"])
    assert all(record["decided_at"] is None for record in control["decision_register"]["records"])


def test_assumption_and_reality_gap_semantics_reject_false_promotion() -> None:
    candidate = deepcopy(_control())
    candidate["reality_assumption_register"]["records"] = [{
        "record_id": "PC-ASM-001", "record_type": "ASSUMPTION", "domain": "example",
        "statement": "unverified", "source": "test", "owner": "owner", "status": "VALIDATED",
        "evidence": [], "testability": "testable", "validation_method": "future",
        "reality_gap_relationship": None, "affected_controls": [], "invalidation_conditions": [],
        "promotion_impact": "BLOCKS", "review_horizon": None, "notes": ""
    }]
    with pytest.raises(ControlValidationError, match="represented as fact"):
        validate_control(candidate)
    candidate["reality_assumption_register"]["records"][0]["record_id"] = "PC-RG-001"
    candidate["reality_assumption_register"]["records"][0]["record_type"] = "REALITY_GAP"
    candidate["reality_assumption_register"]["records"][0]["status"] = "CLOSED"
    with pytest.raises(ControlValidationError, match="closed without evidence"):
        validate_control(candidate)


def test_programme_control_cannot_prove_itself() -> None:
    control = _control()
    local = control["programme_control_ledger"]["records"][-1]
    assert local["verification"]["state"] == "PENDING_INDEPENDENT_REVIEW"
    assert local["independent_verification_references"] == []
    assert control["evidence_invalidation_register"]["records"][-1]["evidence_state"] == "UNKNOWN"


def test_unresolved_reference_rejects() -> None:
    candidate = deepcopy(_control())
    candidate["programme_control_ledger"]["records"][0]["dependencies"].append("PC-LEDGER-999")
    with pytest.raises(ControlValidationError, match="unresolved internal reference"):
        validate_control(candidate)


def test_duplicate_record_identity_rejects() -> None:
    candidate = deepcopy(_control())
    candidate["programme_control_ledger"]["records"][1]["record_id"] = "PC-LEDGER-001"
    with pytest.raises(ControlValidationError, match="duplicate record ID"):
        validate_control(candidate)


def test_invalid_git_identity_rejects() -> None:
    candidate = deepcopy(_control())
    candidate["source_baseline"]["commit"] = "not-a-git-identity"
    with pytest.raises(ControlValidationError, match="pattern"):
        validate_control(candidate)


def test_no_machine_local_paths_or_secret_material() -> None:
    text = CONTROL_PATH.read_text(encoding="utf-8")
    machine_path_pattern = r"[A-Za-z]:\\|/" + "Users/|/home/"
    assert not re.search(machine_path_pattern, text)
    assert not re.search(r"(?i)(password|private[_-]?key|api[_-]?key|client[_-]?secret)\s*[:=]", text)


def test_no_later_stage_or_trading_authority_is_granted() -> None:
    control = _control()
    granted = {
        authority
        for gate in control["milestone_gate_register"]["records"]
        for authority in gate["authority_granted"]
    }
    assert not (FORBIDDEN_AUTHORITY & granted)
    assert FORBIDDEN_AUTHORITY <= set(control["milestone_gate_register"]["records"][1]["authority_not_granted"])


def test_mutating_protected_stage2_identity_rejects_expected_contract() -> None:
    candidate = deepcopy(_control())
    candidate["source_baseline"]["commit"] = "0" * 40
    with pytest.raises(ControlValidationError, match="protected baseline identity mismatch"):
        validate_control(candidate)


def test_mutating_protected_stage2_evidence_identity_rejects() -> None:
    candidate = deepcopy(_control())
    evidence = candidate["evidence_invalidation_register"]["records"][3]
    evidence["exact_identity"] = "0" * 40
    with pytest.raises(ControlValidationError, match="protected evidence identity mismatch"):
        validate_control(candidate)


def test_unauthorized_lifecycle_promotion_is_detectable() -> None:
    candidate = deepcopy(_control())
    gate = candidate["milestone_gate_register"]["records"][-1]
    gate["state"] = "ACCEPTED"
    gate["decision"] = "PASS"
    gate["required_evidence"] = []
    gate["authority_granted"] = []
    with pytest.raises(ControlValidationError, match="cannot infer unlisted authority"):
        validate_control(candidate)

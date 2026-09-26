"""Behavioral validation for the bounded ATIS programme-control baseline."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


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
    "STAGE4_IMPLEMENTATION",
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
EXPECTED_HOUSEKEEPING_EVIDENCE = {
    "PC-EVID-007": (
        "GitHub Actions run 35989535696 job 107600048807 conclusion FAILURE",
        "6cc1896bf3fe5d3a2152ae7411c22a50510d5cf3",
    ),
    "PC-EVID-008": (
        "GitHub Actions run 35989535711 job 107600048942 conclusion SUCCESS",
        "6cc1896bf3fe5d3a2152ae7411c22a50510d5cf3",
    ),
    "PC-EVID-009": (
        "GitHub Actions run 35992405088 job 107609355605 conclusion SUCCESS",
        "914e2752bdc5cb134e7f4ffb7d6cbb6e3370a1d7",
    ),
    "PC-EVID-010": (
        "GitHub Actions run 35992405044 job 107609354939 conclusion SUCCESS",
        "914e2752bdc5cb134e7f4ffb7d6cbb6e3370a1d7",
    ),
    "PC-EVID-011": (
        "GitHub Actions run 35992405088 artifact m1-engineering-foundation-evidence ID 10804472325",
        "ac177283adcbf0ca23990d908ff7d55115b7bca4653e649a5cbf3c7f9d564b4a",
    ),
    "PC-EVID-012": (
        "GitHub pull request 20 protected merge; base 12560025bbc87ee237bb2252215305f8d2fc9e47; "
        "head 914e2752bdc5cb134e7f4ffb7d6cbb6e3370a1d7; tree "
        "9297dded6ea3ec8d47a63f97c5e81662f4453ff5",
        "750b64c2f6bc8bbc48dc3e3d25648896006f1a72",
    ),
    "PC-EVID-013": (
        "GitHub Actions run 35993161133 job 107611796435 artifact "
        "m1-engineering-foundation-evidence ID 10804988129; event push; branch master; "
        "head 750b64c2f6bc8bbc48dc3e3d25648896006f1a72; tree "
        "9297dded6ea3ec8d47a63f97c5e81662f4453ff5; conclusion SUCCESS",
        "f48296b6d8bfa12429979da4c402d68b9acb650044299063f74ad13befd9d49f",
    ),
}
EXPECTED_PROVENANCE_GAP_SHA256 = {'GAP-REVIEW-5': '423ca344c46c299b7a11a687a33629ee2590efcc130d29dcd2e9221e3e905e23', 'GAP-FROZEN-VERBATIM': '92a6e3718cc02d6e1651f8f38a23fe9991ce581e8395790fa23d34c80502702c', 'GAP-RESEARCH-DEFINITIONS': 'a7c8f55a020bc25adad4991467ff6b09d7e3c6b3bd6a3903c3e4708f63305a97', 'GAP-RESEARCH-ORDER': 'edb575d2570c3b437253e408e3ae211662d3f2065a6fcc2877b0e6578df29a29'}

RECOVERED_IDS = {f"PC-EVID-{number:03d}" for number in range(14, 27)}
EXPECTED_GAP_EVIDENCE = {
    "GAP-REVIEW-5": ["PC-EVID-014", "PC-EVID-015"],
    "GAP-FROZEN-VERBATIM": ["PC-EVID-016", "PC-EVID-017", "PC-EVID-018"],
    "GAP-RESEARCH-DEFINITIONS": ["PC-EVID-019", "PC-EVID-020", "PC-EVID-021", "PC-EVID-022", "PC-EVID-023", "PC-EVID-024"],
    "GAP-RESEARCH-ORDER": ["PC-EVID-021", "PC-EVID-022", "PC-EVID-025", "PC-EVID-026"],
}
EXPECTED_RECOVERED_TUPLES = {
    "PC-EVID-014": ("PRIMARY_ASSISTANT_SOURCE", "CODEX_SESSION", "INDEPENDENT_REVIEW_FINDING", "4155a35b7d9aa593c61287fb326870a11e7059e32f722c714a88853f1e44ca92", []),
    "PC-EVID-015": ("PRIMARY_OWNER_SOURCE", "CODEX_SESSION", "OWNER_DIRECT", "adfbb40b8869ea1c6817aa6a5901b6f170b437c929b9e18b02279184ff811255", ["PC-EVID-014"]),
    "PC-EVID-016": ("PRIMARY_OWNER_SOURCE", "CHATGPT_ACCOUNT", "OWNER_DIRECT", "e585aa493391ce159f5a561ef1cde938f2d497d119a29f049b905329f8330052", []),
    "PC-EVID-017": ("PRIMARY_ASSISTANT_SOURCE", "CHATGPT_ACCOUNT", "ASSISTANT_DEFINED_NOT_SEPARATELY_APPROVED", "9dba6f93027f651b5ab1d0e62f1f08e0724fca7387fe47b4a9b530aa49f8fa9d", ["PC-EVID-016"]),
    "PC-EVID-018": ("PRIMARY_OWNER_SOURCE", "CHATGPT_ACCOUNT", "ASSISTANT_DEFINED_OWNER_PRESERVED", "44187ee5ceaab64c515b8b8e24b0ba48d66226df9582e5b12a76251b003300be", ["PC-EVID-016", "PC-EVID-017"]),
    "PC-EVID-019": ("PRIMARY_ASSISTANT_SOURCE", "CHATGPT_ACCOUNT", "ASSISTANT_DEFINED_NOT_SEPARATELY_APPROVED", "a006935615ccc2774cdcf9fc9271c13da294233e26b5e6e90c038e6e2fe5288c", []),
    "PC-EVID-020": ("PRIMARY_OWNER_SOURCE", "CHATGPT_ACCOUNT", "ASSISTANT_DEFINED_OWNER_PRESERVED", "ccd7f284940d6bfa8f8eba2104c93df0a192c6ecef9519955bd92a856b42e298", ["PC-EVID-019"]),
    "PC-EVID-021": ("PRIMARY_OWNER_SOURCE", "CHATGPT_ACCOUNT", "OWNER_DIRECT", "78b399541c87fef08d70fe3b06347b15095406280d774c075c319d2801756bfb", []),
    "PC-EVID-022": ("AUTHENTICATED_HISTORICAL_ARTIFACT", "OFFICE_DOCUMENT", "OFFICE_ARTIFACT_ONLY", "eb21b79fd06c381c62a25838f629a6ea03bf6857228ae786f532115327556750", []),
    "PC-EVID-023": ("AUTHENTICATED_HISTORICAL_ARTIFACT", "OFFICE_DOCUMENT", "OFFICE_ARTIFACT_ONLY", "aa7dc3882b8b5418e65c8dd984ff46b1d7c3ba53fb97584fcca5dbc69f8a9bec", []),
    "PC-EVID-024": ("AUTHENTICATED_HISTORICAL_ARTIFACT", "OFFICE_DOCUMENT", "OFFICE_ARTIFACT_ONLY", "123fd79991434bfdd101d9bbc2150eadd629f045495f1b3b29985235c460da89", []),
    "PC-EVID-025": ("PRIMARY_ASSISTANT_SOURCE", "CHATGPT_ACCOUNT", "ASSISTANT_DEFINED_NOT_SEPARATELY_APPROVED", "91f899e809ffd15483817b07684d572833af7a060459c385f40ff9f43828ea6d", []),
    "PC-EVID-026": ("PRIMARY_OWNER_SOURCE", "CHATGPT_ACCOUNT", "OWNER_DIRECT", "a76b836adc572ae7a16e3e96ce5b08d533a550c26e5e36e20d08d8b441bc555f", ["PC-EVID-025"]),
}
EXPECTED_RECOVERED_LOCATORS = {
    "PC-EVID-014": {"source_role": "PRIMARY_ASSISTANT_SOURCE", "source_system": "CODEX_SESSION", "session_id": "01a08d3f-e474-72b1-ac46-a12c865268fc", "turn_id": "01a0cf9f-f41a-70b3-beaf-2bdd41ca9d89", "message_id": "msg_0d407a9e2a4f3561016ab4214e38fc87d2adc612795fc72c4a"},
    "PC-EVID-015": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CODEX_SESSION", "session_id": "01a08d3f-e474-72b1-ac46-a12c865268fc", "turn_id": "01a0cfa5-3103-7721-a214-da00793d4e1c", "message_id": "msg_01a0cfa6-88b2-7910-9d97-2889e28aefed"},
    "PC-EVID-016": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa7b836-d77c-83ed-9bd5-895e47434eac", "turn_id": "0bc2bae5-608c-42b9-bfe7-ca42ced7a58d", "message_id": "0bc2bae5-608c-42b9-bfe7-ca42ced7a58d"},
    "PC-EVID-017": {"source_role": "PRIMARY_ASSISTANT_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa7b836-d77c-83ed-9bd5-895e47434eac", "turn_id": "0bc2bae5-608c-42b9-bfe7-ca42ced7a58d", "message_id": "c138115c-4420-4a22-8e0d-ceb133697d9f"},
    "PC-EVID-018": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa7b836-d77c-83ed-9bd5-895e47434eac", "turn_id": "98bcd948-3645-4536-9c7b-c63f1fc8c99e", "message_id": "98bcd948-3645-4536-9c7b-c63f1fc8c99e"},
    "PC-EVID-019": {"source_role": "PRIMARY_ASSISTANT_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa19f14-f764-83ed-90a6-c56d55ac345d", "turn_id": "746aad62-4dbe-48f3-98c5-a9e7f09902bf", "message_id": "08f2ae04-0ef9-4969-b63b-bf6c6ede45bc"},
    "PC-EVID-020": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa19f14-f764-83ed-90a6-c56d55ac345d", "turn_id": "a5c79fa6-da2b-4f9f-87b4-1c3ccb068845", "message_id": "a5c79fa6-da2b-4f9f-87b4-1c3ccb068845"},
    "PC-EVID-021": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa1bbe2-ab24-83eb-a175-cced218812ef", "turn_id": "0deccebf-57e8-4dd4-8141-5288a55c2a0a", "message_id": "0deccebf-57e8-4dd4-8141-5288a55c2a0a"},
    "PC-EVID-022": {"source_role": "AUTHENTICATED_HISTORICAL_ARTIFACT", "source_system": "OFFICE_DOCUMENT", "artifact_name": "Trading_Bot_Master_Backup_2026-09-09_v1.3.docx"},
    "PC-EVID-023": {"source_role": "AUTHENTICATED_HISTORICAL_ARTIFACT", "source_system": "OFFICE_DOCUMENT", "artifact_name": "Automated_Trading_Bot_Master_Backup_2026-09-10_FINAL.docx"},
    "PC-EVID-024": {"source_role": "AUTHENTICATED_HISTORICAL_ARTIFACT", "source_system": "OFFICE_DOCUMENT", "artifact_name": "Automated_Trading_Bot_Master_Audit_Closure_v1.4_Corrected.docx"},
    "PC-EVID-025": {"source_role": "PRIMARY_ASSISTANT_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa19f14-f764-83ed-90a6-c56d55ac345d", "turn_id": "fd1456ff-56a7-46ec-a531-03a3f9a5bd73", "message_id": "b9bab15e-f941-45f2-bdf9-e62b34dae5d2"},
    "PC-EVID-026": {"source_role": "PRIMARY_OWNER_SOURCE", "source_system": "CHATGPT_ACCOUNT", "conversation_id": "6aa19f14-f764-83ed-90a6-c56d55ac345d", "turn_id": "47e1de6a-ec3f-4cf3-a4eb-b1541b3eb6c0", "message_id": "47e1de6a-ec3f-4cf3-a4eb-b1541b3eb6c0"},
}
EXPECTED_ATTRIBUTABLE_TIMESTAMPS = {
    "PC-EVID-014": ("2026-09-23T19:00:00.511Z", "TURN_COMPLETED_AT"),
    "PC-EVID-015": ("2026-09-23T19:03:12.818Z", "TURN_STARTED_AT"),
    "PC-EVID-016": ("2026-09-23T17:48:58.220Z", "TURN_STARTED_AT"),
    "PC-EVID-017": ("2026-09-23T17:49:04.632Z", "TURN_COMPLETED_AT"),
    "PC-EVID-018": ("2026-09-23T17:52:03.185Z", "TURN_STARTED_AT"),
    "PC-EVID-020": ("2026-09-09T18:21:53.425Z", "TURN_STARTED_AT"),
    "PC-EVID-021": ("2026-09-09T20:04:53.813Z", "TURN_STARTED_AT"),
    "PC-EVID-026": ("2026-09-09T18:25:25.495Z", "TURN_STARTED_AT"),
}
EXPECTED_RECOVERED_RECORD_SHA256 = {
    "PC-EVID-014": "bb0d0b56e6eb9d673068dea99377d36dba0fcf952f8bcff00cb387c044edcbb3",
    "PC-EVID-015": "29ad7ccedd04294dfa49d13ca3b705ad517e4db8c4e7ae4674be846b1b4c083d",
    "PC-EVID-016": "e1dd044d64ff7abfee12b43d454b2e024f16f685c0c1999aaaa03c1b117871a1",
    "PC-EVID-017": "f0a5db90621c85d04e8b4fb435acc9036d355d1ae3fc005b8dd93db333054b65",
    "PC-EVID-018": "da2582218a113839f621a7c4954eea44d7e445b1294ac7d785744026c43a06dd",
    "PC-EVID-019": "b3d2a2e651265cb7e68e01dbf56159d16ca5bed5fa479766275a140f4edc2b28",
    "PC-EVID-020": "5c7aece6ceb4656c3055d2a0c77d9b521887fc709e0a82ff9edae1fb4bdfc709",
    "PC-EVID-021": "e71faad2e259a00a7ee90b6f23c614a067f498615e13a740bfc858087b6a71e0",
    "PC-EVID-022": "d0ee0625328e069f781ec181d2b3a85c92a690df26e6143765f71a4c4bda191e",
    "PC-EVID-023": "f30761aefa6ead15374eb5cdd14e944f185a13b25ff38222ae106b388e939e9e",
    "PC-EVID-024": "cab3dc621493960e4c883becdec490b0657f39f810236de73e04d88390c7c732",
    "PC-EVID-025": "d6801777307a9ce85c17d3879f94194c18332da078bb69df438d1d5c3179e6d5",
    "PC-EVID-026": "5085578f2b212702a36466a90ea40cea8da4d7bcb1ec0e1e7d556843ae6ade14",
}
ROLE_ATTRIBUTION = {
    "PRIMARY_OWNER_SOURCE": {"OWNER_DIRECT", "ASSISTANT_DEFINED_OWNER_PRESERVED"},
    "PRIMARY_ASSISTANT_SOURCE": {"ASSISTANT_DEFINED_NOT_SEPARATELY_APPROVED", "INDEPENDENT_REVIEW_FINDING"},
    "AUTHENTICATED_HISTORICAL_ARTIFACT": {"OFFICE_ARTIFACT_ONLY"},
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


def _canonical_record_sha256(record: dict[str, Any]) -> str:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_control(control: dict[str, Any]) -> None:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(control)
    except ValidationError as exc:
        raise ControlValidationError(f"schema validation failed (pattern/structure): {exc.message}") from exc
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
        if record["supersedes"] is not None:
            if record["supersedes"] not in evidence_by_id:
                raise ControlValidationError("unresolved evidence supersession")
            if evidence_by_id[record["supersedes"]]["evidence_state"] != "SUPERSEDED":
                raise ControlValidationError("superseded evidence remains current")
        for reference in record["dependencies"]:
            if reference.startswith("PC-") and reference not in evidence_by_id:
                raise ControlValidationError(f"unresolved evidence dependency: {reference}")
        if record["evidence_state"] in {"UNKNOWN", "STALE", "INVALIDATED", "SUPERSEDED"}:
            for gate in control["milestone_gate_register"]["records"]:
                if gate["decision"] == "PASS" and record["record_id"] in gate["required_evidence"]:
                    raise ControlValidationError("non-current evidence satisfies a positive gate")

    recovered = {
        record["record_id"]: record
        for record in evidence
        if record["evidence_class"] == "RECOVERED_HISTORICAL_SOURCE_EVIDENCE"
    }
    if recovered.keys() != RECOVERED_IDS:
        raise ControlValidationError("recovered evidence inventory mismatch")
    for record_id, expected in EXPECTED_RECOVERED_TUPLES.items():
        record = recovered[record_id]
        source = record["recovered_source"]
        actual = (
            source["source_role"], source["source_system"], record["claim_attribution"],
            record["exact_identity"], record["dependencies"],
        )
        if actual != expected or source != EXPECTED_RECOVERED_LOCATORS[record_id]:
            raise ControlValidationError("recovered source tuple mismatch")
        if record_id in EXPECTED_ATTRIBUTABLE_TIMESTAMPS and (record.get("source_timestamp"), record.get("timestamp_basis")) != EXPECTED_ATTRIBUTABLE_TIMESTAMPS[record_id]:
            raise ControlValidationError("recovered source timestamp tuple mismatch")
        if _canonical_record_sha256(record) != EXPECTED_RECOVERED_RECORD_SHA256[record_id]:
            raise ControlValidationError("recovered source semantic record mismatch")
        if record["identity_kind"] != "SHA256" or record["gate_consumers"] or record["supersedes"] is not None:
            raise ControlValidationError("recovered evidence violates provenance-only constraints")
        if record["claim_attribution"] not in ROLE_ATTRIBUTION[source["source_role"]]:
            raise ControlValidationError("recovered source role/attribution mismatch")
        if any(reference not in RECOVERED_IDS for reference in record["dependencies"]):
            raise ControlValidationError("recovered evidence has an unapproved dependency")
    not_recovered = {record_id for record_id, record in recovered.items() if record.get("timestamp_status") == "NOT_RECOVERED"}
    if not_recovered != {"PC-EVID-019", "PC-EVID-025"}:
        raise ControlValidationError("recovered timestamp-status inventory mismatch")
    for record_id, record in recovered.items():
        if record["recovered_source"]["source_system"] == "OFFICE_DOCUMENT":
            if record["dependencies"]:
                raise ControlValidationError("Office artifacts must remain independently authenticated")
            continue
        if record["timestamp_status"] == "ATTRIBUTABLE":
            if "source_timestamp" not in record or "timestamp_basis" not in record:
                raise ControlValidationError("attributable timestamp metadata is incomplete")
        else:
            if "source_timestamp" in record or "timestamp_basis" in record or not record.get("known_chronology"):
                raise ControlValidationError("unrecovered timestamp is being inferred or lacks chronology")

    gap_by_id = {record["gap_id"]: record for record in control["provenance_gap_register"]["records"]}
    for gap_id, expected_references in EXPECTED_GAP_EVIDENCE.items():
        if gap_by_id[gap_id]["later_evidence"] != expected_references:
            raise ControlValidationError("GAP recovered-evidence mapping mismatch")
        if any(reference not in recovered for reference in expected_references):
            raise ControlValidationError("GAP later_evidence is unresolved")

    allowed_recovered_paths = {
        ("evidence_invalidation_register", "records", record_id, "dependencies")
        for record_id in RECOVERED_IDS
    } | {
        ("provenance_gap_register", "records", gap_id, "later_evidence")
        for gap_id in EXPECTED_GAP_EVIDENCE
    }

    def scan(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            identity = value.get("record_id") or value.get("gate_id") or value.get("gap_id")
            for key, item in value.items():
                if key in {"record_id", "gate_id", "gap_id"}:
                    continue
                next_path = path + ((str(identity),) if key in {"dependencies", "later_evidence"} and identity else ()) + (key,)
                scan(item, next_path)
        elif isinstance(value, list):
            for item in value:
                scan(item, path)
        elif isinstance(value, str) and value in RECOVERED_IDS:
            normalized = path[-4:] if len(path) >= 4 else path
            if normalized not in allowed_recovered_paths:
                raise ControlValidationError("recovered evidence entered an authority-bearing path")

    scan(control, ())

    for record_id, (source, identity) in EXPECTED_HOUSEKEEPING_EVIDENCE.items():
        record = evidence_by_id.get(record_id)
        if record is None or record["source"] != source or record["exact_identity"] != identity:
            raise ControlValidationError("housekeeping evidence identity mismatch")

    for start in evidence_by_id:
        visited: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in visited:
                raise ControlValidationError("evidence supersession cycle")
            visited.add(current)
            current = evidence_by_id[current]["supersedes"]

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

    gaps = control["provenance_gap_register"]["records"]
    gap_ids = {record["gap_id"] for record in gaps}
    if gap_ids != EXPECTED_PROVENANCE_GAP_SHA256.keys():
        raise ControlValidationError("protected provenance gap inventory mismatch")
    for record in gaps:
        canonical = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if sha256(canonical).hexdigest() != EXPECTED_PROVENANCE_GAP_SHA256[record["gap_id"]]:
            raise ControlValidationError("protected provenance gap mismatch")


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
    assert local["control_lifecycle_status"] == "ACCEPTED"
    assert local["evidence_lifecycle_status"] == "CURRENT"


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
    assert local["verification"]["state"] == "ATTRIBUTABLE_PROTECTED_EVIDENCE"
    assert local["independent_verification_references"] == ["PC-EVID-010", "PC-EVID-013"]
    assert "PC-EVID-006" not in local["acceptance_evidence_references"]


def test_housekeeping_preserves_failure_and_protected_successor_chain() -> None:
    control = _control()
    evidence = {
        record["record_id"]: record
        for record in control["evidence_invalidation_register"]["records"]
    }
    assert evidence["PC-EVID-006"]["evidence_state"] == "SUPERSEDED"
    assert evidence["PC-EVID-007"]["source"].endswith("conclusion FAILURE")
    assert evidence["PC-EVID-009"]["supersedes"] == "PC-EVID-006"
    assert evidence["PC-EVID-012"]["exact_identity"] == (
        "750b64c2f6bc8bbc48dc3e3d25648896006f1a72"
    )
    assert "head 750b64c2f6bc8bbc48dc3e3d25648896006f1a72" in evidence["PC-EVID-013"]["source"]


def test_housekeeping_gate_uses_only_current_attributable_evidence() -> None:
    control = _control()
    gate = control["milestone_gate_register"]["records"][-1]
    evidence = {
        record["record_id"]: record
        for record in control["evidence_invalidation_register"]["records"]
    }
    assert gate["state"] == "ACCEPTED"
    assert gate["decision"] == "PASS"
    assert gate["protected_baseline"] == "750b64c2f6bc8bbc48dc3e3d25648896006f1a72"
    assert all(evidence[record_id]["evidence_state"] == "CURRENT" for record_id in gate["required_evidence"])
    assert "STAGE3_IMPLEMENTATION" in gate["authority_not_granted"]
    assert "STAGE4_IMPLEMENTATION" in gate["authority_not_granted"]


def test_housekeeping_evidence_identity_mutations_reject() -> None:
    for record_id in EXPECTED_HOUSEKEEPING_EVIDENCE:
        candidate = deepcopy(_control())
        evidence = next(
            record
            for record in candidate["evidence_invalidation_register"]["records"]
            if record["record_id"] == record_id
        )
        evidence["source"] += " altered"
        with pytest.raises(ControlValidationError, match="housekeeping evidence identity mismatch"):
            validate_control(candidate)


def test_unresolved_evidence_supersession_rejects() -> None:
    candidate = deepcopy(_control())
    record = next(
        item
        for item in candidate["evidence_invalidation_register"]["records"]
        if item["record_id"] == "PC-EVID-013"
    )
    record["supersedes"] = "PC-EVID-999"
    with pytest.raises(ControlValidationError, match="unresolved evidence supersession"):
        validate_control(candidate)


def test_evidence_supersession_cycle_rejects() -> None:
    candidate = deepcopy(_control())
    evidence = candidate["evidence_invalidation_register"]["records"]
    evidence[5]["supersedes"] = "PC-EVID-009"
    evidence[8]["evidence_state"] = "SUPERSEDED"
    for gate in candidate["milestone_gate_register"]["records"]:
        gate["decision"] = "NOT_EVALUATED"
    with pytest.raises(ControlValidationError, match="supersession cycle"):
        validate_control(candidate)


@pytest.mark.parametrize("gap_id", sorted(EXPECTED_PROVENANCE_GAP_SHA256))
def test_protected_provenance_gap_fabricated_closure_rejects(gap_id: str) -> None:
    candidate = deepcopy(_control())
    gap = next(
        record
        for record in candidate["provenance_gap_register"]["records"]
        if record["gap_id"] == gap_id
    )
    gap["historical_state"] = "CLOSED"
    gap["classification"] = "CONFIRMED"
    gap["verbatim_source_recovered"] = True
    gap["current_substantive_status"] = "CLOSED"
    with pytest.raises(ControlValidationError, match="protected provenance gap mismatch"):
        validate_control(candidate)


def test_superseded_evidence_cannot_satisfy_positive_gate() -> None:
    candidate = deepcopy(_control())
    evidence = next(
        record
        for record in candidate["evidence_invalidation_register"]["records"]
        if record["record_id"] == "PC-EVID-009"
    )
    evidence["evidence_state"] = "SUPERSEDED"
    with pytest.raises(ControlValidationError, match="non-current evidence"):
        validate_control(candidate)


def test_valid_format_housekeeping_evidence_identity_mutation_rejects() -> None:
    candidate = deepcopy(_control())
    evidence = next(
        record
        for record in candidate["evidence_invalidation_register"]["records"]
        if record["record_id"] == "PC-EVID-007"
    )
    evidence["exact_identity"] = "0" * 40
    with pytest.raises(ControlValidationError, match="housekeeping evidence identity mismatch"):
        validate_control(candidate)


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
    assert (FORBIDDEN_AUTHORITY - {"STAGE4_IMPLEMENTATION"}) <= set(
        control["milestone_gate_register"]["records"][1]["authority_not_granted"]
    )
    assert "STAGE4_IMPLEMENTATION" in control["milestone_gate_register"]["records"][-1][
        "authority_not_granted"
    ]


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


def test_real_draft_202012_schema_validates_recovered_candidate() -> None:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(_control())


def test_exact_recovered_evidence_inventory_and_tuples() -> None:
    control = _control()
    recovered = {record["record_id"]: record for record in control["evidence_invalidation_register"]["records"] if record["evidence_class"] == "RECOVERED_HISTORICAL_SOURCE_EVIDENCE"}
    assert recovered.keys() == RECOVERED_IDS
    for record_id, expected in EXPECTED_RECOVERED_TUPLES.items():
        record = recovered[record_id]
        source = record["recovered_source"]
        assert (source["source_role"], source["source_system"], record["claim_attribution"], record["exact_identity"], record["dependencies"]) == expected
        assert source == EXPECTED_RECOVERED_LOCATORS[record_id]
        if record_id in EXPECTED_ATTRIBUTABLE_TIMESTAMPS:
            assert (record["source_timestamp"], record["timestamp_basis"]) == EXPECTED_ATTRIBUTABLE_TIMESTAMPS[record_id]
        assert record["identity_kind"] == "SHA256"
        assert record["gate_consumers"] == []
        assert record["supersedes"] is None


@pytest.mark.parametrize("record_id", ["PC-EVID-019", "PC-EVID-025"])
def test_not_recovered_timestamps_never_masquerade_as_source_timestamps(record_id: str) -> None:
    evidence = {r["record_id"]: r for r in _control()["evidence_invalidation_register"]["records"]}
    record = evidence[record_id]
    assert record["timestamp_status"] == "NOT_RECOVERED"
    assert "source_timestamp" not in record
    assert "timestamp_basis" not in record
    assert record["known_chronology"]
    assert "not source_timestamp" in record["provenance_limitation"]


def test_only_authenticated_not_recovered_timestamp_records_are_allowed() -> None:
    mutated = _control()
    record = next(r for r in mutated["evidence_invalidation_register"]["records"] if r["record_id"] == "PC-EVID-020")
    record["timestamp_status"] = "NOT_RECOVERED"
    record.pop("source_timestamp")
    record.pop("timestamp_basis")
    record["known_chronology"] = ["syntactically valid but unauthenticated chronology"]
    with pytest.raises(ControlValidationError, match="timestamp"):
        validate_control(mutated)


@pytest.mark.parametrize("record_id", sorted(RECOVERED_IDS - {"PC-EVID-019", "PC-EVID-025"}))
def test_attributable_message_timestamps_or_office_artifacts_are_exact(record_id: str) -> None:
    evidence = {r["record_id"]: r for r in _control()["evidence_invalidation_register"]["records"]}
    record = evidence[record_id]
    if record["recovered_source"]["source_system"] == "OFFICE_DOCUMENT":
        assert "timestamp_status" not in record
    else:
        assert record["timestamp_status"] == "ATTRIBUTABLE"
        assert record["timestamp_basis"] in {"TURN_STARTED_AT", "TURN_COMPLETED_AT"}
        assert record["source_timestamp"].endswith("Z")


def test_source_role_and_claim_attribution_cannot_be_promoted() -> None:
    mutated = _control()
    record = next(r for r in mutated["evidence_invalidation_register"]["records"] if r["record_id"] == "PC-EVID-017")
    record["recovered_source"]["source_role"] = "PRIMARY_OWNER_SOURCE"
    with pytest.raises(ControlValidationError, match="tuple mismatch"):
        validate_control(mutated)


def test_right_hash_with_wrong_locator_or_timestamp_rejects() -> None:
    mutated = _control()
    record = next(r for r in mutated["evidence_invalidation_register"]["records"] if r["record_id"] == "PC-EVID-021")
    record["recovered_source"]["conversation_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ControlValidationError, match="tuple mismatch"):
        validate_control(mutated)

    mutated = _control()
    record = next(r for r in mutated["evidence_invalidation_register"]["records"] if r["record_id"] == "PC-EVID-021")
    record["source_timestamp"] = "2026-09-09T20:04:54.813Z"
    with pytest.raises(ControlValidationError, match="timestamp tuple mismatch"):
        validate_control(mutated)


def test_review5_failure_findings_and_correction_chronology_are_preserved() -> None:
    evidence = {r["record_id"]: r for r in _control()["evidence_invalidation_register"]["records"]}
    failed = evidence["PC-EVID-014"]
    correction = evidence["PC-EVID-015"]
    assert "FAILED" in failed["notes"]
    assert len(failed["recovered_findings"]) == 5
    assert [item.split()[0] for item in failed["recovered_findings"]] == [f"S2-CLOSURE-REVIEW-{number:03d}" for number in range(1, 6)]
    assert correction["dependencies"] == ["PC-EVID-014"]
    assert correction["source_timestamp"] > failed["source_timestamp"]
    assert failed["supersedes"] is None


def test_frozen_context_is_exact_and_claims_no_exhaustive_reopening_contract() -> None:
    evidence = {r["record_id"]: r for r in _control()["evidence_invalidation_register"]["records"]}
    assert evidence["PC-EVID-016"]["dependencies"] == []
    assert evidence["PC-EVID-017"]["dependencies"] == ["PC-EVID-016"]
    assert evidence["PC-EVID-018"]["dependencies"] == ["PC-EVID-016", "PC-EVID-017"]
    assert evidence["PC-EVID-016"]["source_timestamp"] < evidence["PC-EVID-017"]["source_timestamp"] < evidence["PC-EVID-018"]["source_timestamp"]
    gap = next(g for g in _control()["provenance_gap_register"]["records"] if g["gap_id"] == "GAP-FROZEN-VERBATIM")
    assert "NO EXHAUSTIVE OWNER-VERBATIM REOPENING CONTRACT IS CLAIMED" in gap["notes"]


def test_frozen_context_detachment_or_substitution_rejects() -> None:
    mutated = _control()
    evidence = {r["record_id"]: r for r in mutated["evidence_invalidation_register"]["records"]}
    evidence["PC-EVID-018"]["dependencies"] = ["PC-EVID-016"]
    with pytest.raises(ControlValidationError, match="tuple mismatch"):
        validate_control(mutated)


def test_office_artifacts_remain_independently_authenticated() -> None:
    evidence = {r["record_id"]: r for r in _control()["evidence_invalidation_register"]["records"]}
    for record_id in ("PC-EVID-022", "PC-EVID-023", "PC-EVID-024"):
        assert evidence[record_id]["dependencies"] == []
        assert "unavailable as a standalone file for rehashing" in evidence[record_id]["provenance_limitation"]


def test_four_gap_transitions_preserve_historical_state_and_exact_evidence() -> None:
    gaps = {g["gap_id"]: g for g in _control()["provenance_gap_register"]["records"]}
    for gap_id, references in EXPECTED_GAP_EVIDENCE.items():
        assert gaps[gap_id]["historical_state"] == "UNRESOLVED"
        assert gaps[gap_id]["later_evidence"] == references
    assert gaps["GAP-REVIEW-5"]["classification"] == "CONFIRMED"
    assert gaps["GAP-REVIEW-5"]["verbatim_source_recovered"] is True
    assert gaps["GAP-FROZEN-VERBATIM"]["classification"] == "CONFIRMED"
    assert gaps["GAP-FROZEN-VERBATIM"]["verbatim_source_recovered"] is True
    for gap_id in ("GAP-RESEARCH-DEFINITIONS", "GAP-RESEARCH-ORDER"):
        assert gaps[gap_id]["classification"] == "RECONSTRUCTED"
        assert gaps[gap_id]["verbatim_source_recovered"] is False


@pytest.mark.parametrize(("section", "index", "field"), [("milestone_gate_register", 0, "required_evidence"), ("programme_control_ledger", 0, "acceptance_evidence_references"), ("programme_control_ledger", 0, "implementation_references"), ("programme_control_ledger", 0, "direct_test_references"), ("programme_control_ledger", 0, "independent_verification_references")])
def test_recovered_evidence_rejects_direct_authority_paths(section: str, index: int, field: str) -> None:
    mutated = _control()
    mutated[section]["records"][index][field].append("PC-EVID-014")
    with pytest.raises(ControlValidationError, match="authority-bearing path"):
        validate_control(mutated)


def test_deferred_work_closure_evidence_cannot_consume_recovered_history() -> None:
    mutated = _control()
    mutated["risk_blocker_deferral_register"]["deferred_work"][0]["closure_evidence"].append("PC-EVID-014")
    with pytest.raises(ControlValidationError, match="authority-bearing path"):
        validate_control(mutated)


def test_nonrecovered_evidence_cannot_transitively_depend_on_recovered_history() -> None:
    mutated = _control()
    evidence = {r["record_id"]: r for r in mutated["evidence_invalidation_register"]["records"]}
    evidence["PC-EVID-013"]["dependencies"].append("PC-EVID-014")
    with pytest.raises(ControlValidationError, match="authority-bearing path"):
        validate_control(mutated)


def test_supersession_cannot_launder_recovered_history() -> None:
    mutated = _control()
    evidence = {r["record_id"]: r for r in mutated["evidence_invalidation_register"]["records"]}
    evidence["PC-EVID-014"]["supersedes"] = "PC-EVID-006"
    with pytest.raises(ControlValidationError):
        validate_control(mutated)


def test_later_reconstructed_labels_are_not_historical_owner_authority() -> None:
    evidence_text = json.dumps(_control()["evidence_invalidation_register"]["records"], ensure_ascii=False)
    for label in ("Hypothesis Lock", "Signal Dependency Graph", "Edge Decay Protocol", "formal three-ledger doctrine", "named six-stream programme"):
        assert label not in evidence_text


def test_recovered_evidence_never_grants_later_stage_or_trading_authority() -> None:
    control = _control()
    recovered_text = json.dumps([r for r in control["evidence_invalidation_register"]["records"] if r["record_id"] in RECOVERED_IDS])
    assert not FORBIDDEN_AUTHORITY.intersection(re.findall(r"[A-Z][A-Z0-9_]+", recovered_text))
    validate_control(control)


def test_recovered_record_semantic_digests_are_independently_fixed() -> None:
    evidence = {
        record["record_id"]: record
        for record in _control()["evidence_invalidation_register"]["records"]
        if record["record_id"] in RECOVERED_IDS
    }
    assert EXPECTED_RECOVERED_RECORD_SHA256.keys() == RECOVERED_IDS
    assert {
        record_id: _canonical_record_sha256(record)
        for record_id, record in evidence.items()
    } == EXPECTED_RECOVERED_RECORD_SHA256


@pytest.mark.parametrize("record_id", sorted(RECOVERED_IDS))
def test_every_recovered_record_rejects_semantic_claim_rewriting(record_id: str) -> None:
    mutated = _control()
    record = next(
        item
        for item in mutated["evidence_invalidation_register"]["records"]
        if item["record_id"] == record_id
    )
    record["claim_property"] += " altered historical meaning"
    with pytest.raises(ControlValidationError, match="semantic record mismatch"):
        validate_control(mutated)


@pytest.mark.parametrize(
    "mutation",
    [
        "review_failure_to_pass",
        "review_finding_deleted",
        "review_finding_replaced",
        "v13_promoted_to_owner_verbatim",
        "research_order_levels_collapsed",
        "later_label_promoted",
        "known_chronology_fabricated",
        "provenance_limitation_falsified",
    ],
)
def test_independent_review_semantic_mutations_reject(mutation: str) -> None:
    mutated = _control()
    evidence = {
        record["record_id"]: record
        for record in mutated["evidence_invalidation_register"]["records"]
    }
    if mutation == "review_failure_to_pass":
        evidence["PC-EVID-014"]["notes"] = "Conclusion: STAGE2_CLOSURE_RECORD_REVIEW_PASS."
    elif mutation == "review_finding_deleted":
        evidence["PC-EVID-014"]["recovered_findings"].pop()
    elif mutation == "review_finding_replaced":
        evidence["PC-EVID-014"]["recovered_findings"][0] = (
            "S2-CLOSURE-REVIEW-001 fabricated replacement"
        )
    elif mutation == "v13_promoted_to_owner_verbatim":
        evidence["PC-EVID-022"]["claim_property"] = (
            "Owner-verbatim detailed order and Stage-4 authority"
        )
    elif mutation == "research_order_levels_collapsed":
        evidence["PC-EVID-025"]["claim_property"] = (
            "Owner macro order: audit, design, Python"
        )
    elif mutation == "later_label_promoted":
        evidence["PC-EVID-021"]["claim_property"] = (
            "OWNER_DIRECT Hypothesis Lock and named six-stream programme"
        )
    elif mutation == "known_chronology_fabricated":
        evidence["PC-EVID-019"]["known_chronology"] = [
            "Fabricated chronology 2099-01-01T00:00:00Z"
        ]
    else:
        evidence["PC-EVID-019"]["provenance_limitation"] = (
            "Exact timestamp recovered and authoritative."
        )
    with pytest.raises(ControlValidationError, match="semantic record mismatch"):
        validate_control(mutated)


@pytest.mark.parametrize(
    "field",
    [
        "source",
        "notes",
        "provenance_limitation",
        "evidence_state",
        "invalidation_triggers",
    ],
)
def test_other_material_recovered_fields_are_tamper_evident(field: str) -> None:
    mutated = _control()
    record = next(
        item
        for item in mutated["evidence_invalidation_register"]["records"]
        if item["record_id"] == "PC-EVID-020"
    )
    if field == "evidence_state":
        record[field] = "STALE"
    elif isinstance(record[field], list):
        record[field].append("fabricated semantic assertion")
    else:
        record[field] = f"{record[field]} fabricated semantic assertion"
    with pytest.raises(ControlValidationError, match="semantic record mismatch"):
        validate_control(mutated)

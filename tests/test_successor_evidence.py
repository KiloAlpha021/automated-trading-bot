from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs/programme/successor-evidence.json"
SCHEMA = ROOT / "docs/programme/successor-evidence.schema.json"
POLICY = "ATIS_STAGE3_SUCCESSOR_EVIDENCE_AND_RECOVERY_POLICY_V1"
BASE_COMMIT = "b5782f41bf2e687d9ee6ffe55c62ca2e9daa28b5"
BASE_TREE = "d8c90c91cc24f9b581ee4321389767d53fae5c3e"
PROGRAMME_BLOB = "aa7c01cfe3690021c3f6c86fa581f7076967c419"
DENIALS = ["RB1_IMPLEMENTATION_RESUMPTION", "STAGE3_IMPLEMENTATION", "SLICE1_CANDIDATE_PUBLICATION", "AI_TRADING_AUTHORITY"]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def candidate() -> dict[str, Any]:
    return {
        "record_id": "SE-CAND-001",
        "record_type": "CANDIDATE_SUCCESSOR",
        "governing_stage": "STAGE3",
        "governing_rb": "RB1",
        "governing_slice": "ATIS-S3-RB1-S1",
        "predecessor_evidence": ["docs/stage2/s27-evidence.json"],
        "predecessor_commit": BASE_COMMIT,
        "predecessor_tree": BASE_TREE,
        "authorization_references": ["PC-DEC-999"],
        "path_transitions": [
            {
                "path": "docs/programme/programme-control.json",
                "predecessor_blob": PROGRAMME_BLOB,
                "successor_candidate_blob": "1" * 40,
                "role": "CONSISTENCY_ENVELOPE",
                "requirement_references": [POLICY],
            },
            {
                "path": "src/example/new.py",
                "predecessor_blob": "ABSENT",
                "successor_candidate_blob": "2" * 40,
                "role": "IMPLEMENTATION",
                "requirement_references": ["S3-REQ-001"],
            },
        ],
        "transition_reason": "Synthetic contract test only",
        "verification_evidence": ["SYNTHETIC_TEST_EVIDENCE"],
        "candidate_state": "VERIFIED_LOCAL_UNPUBLISHED",
        "authority": {"granted": [], "not_granted": DENIALS},
    }


def publication() -> dict[str, Any]:
    c = candidate()
    return {
        "record_id": "SE-PUB-001",
        "record_type": "PROTECTED_PUBLICATION",
        "candidate_record_id": c["record_id"],
        "protected_commit": "3" * 40,
        "protected_tree": "4" * 40,
        "ordered_merge_parents": [BASE_COMMIT, "5" * 40],
        "publication_reference": "PR-SYNTHETIC",
        "protected_path_blobs": [
            {"path": row["path"], "blob": row["successor_candidate_blob"]}
            for row in c["path_transitions"]
        ],
        "hosted_checks": [{"name": "synthetic", "conclusion": "SUCCESS"}],
        "post_publication_verification": "PASS",
        "open_obligations_preserved": True,
        "authority": {"granted": [], "not_granted": DENIALS},
    }


def validate_cross(register: dict[str, Any], candidate_paths: set[str] | None = None) -> None:
    records = register["records"]
    ids = [record["record_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate successor record identity")
    candidates = {record["record_id"]: record for record in records if record["record_type"] == "CANDIDATE_SUCCESSOR"}
    for record in candidates.values():
        transitions = record["path_transitions"]
        paths = [row["path"] for row in transitions]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate path transition")
        if candidate_paths is not None and set(paths) != candidate_paths:
            raise ValueError("candidate changed paths differ from successor evidence")
        if git("rev-parse", f'{record["predecessor_commit"]}^{{tree}}') != record["predecessor_tree"]:
            raise ValueError("predecessor commit/tree mismatch")
        for evidence in record["predecessor_evidence"]:
            if not (ROOT / evidence).is_file():
                raise ValueError("missing predecessor evidence")
        for row in transitions:
            historical = git("rev-parse", f'{record["predecessor_tree"]}:{row["path"]}') if row["predecessor_blob"] != "ABSENT" else None
            if historical != (None if row["predecessor_blob"] == "ABSENT" else row["predecessor_blob"]):
                raise ValueError("wrong predecessor blob")
    for record in (row for row in records if row["record_type"] == "PROTECTED_PUBLICATION"):
        if record["candidate_record_id"] not in candidates:
            raise ValueError("publication without candidate")
        expected = {
            row["path"]: row["successor_candidate_blob"]
            for row in candidates[record["candidate_record_id"]]["path_transitions"]
        }
        actual = {row["path"]: row["blob"] for row in record["protected_path_blobs"]}
        if actual != expected:
            raise ValueError("publication differs from candidate")
    for record in records:
        if record["authority"]["granted"] or not set(DENIALS) <= set(record["authority"]["not_granted"]):
            raise ValueError("authority escalation")


def synthetic(*records: dict[str, Any]) -> dict[str, Any]:
    value = load(REGISTER)
    value["records"] = list(records)
    Draft202012Validator(load(SCHEMA)).validate(value)
    return value


def test_register_and_closed_schema_validate() -> None:
    schema = load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load(REGISTER))
    records = load(REGISTER)["records"]
    assert len(records) == 1
    record = records[0]
    assert record["record_id"] == "SE-CAND-001"
    assert record["candidate_state"] == "VERIFIED_LOCAL_UNPUBLISHED"
    assert record["authorization_references"] == ["PC-DEC-005"]
    assert record["predecessor_commit"] == BASE_COMMIT
    assert record["predecessor_tree"] == BASE_TREE
    assert {
        row["path"]: (row["predecessor_blob"], row["successor_candidate_blob"], row["role"])
        for row in record["path_transitions"]
    } == {
        "src/automated_trading_bot/domain/decision.py": ("ef9f26caac51a9dbb50161699a7f3d71840efc10", "23dd735bf239e529a9863ee66a044ec12bd40712", "IMPLEMENTATION"),
        "src/automated_trading_bot/domain/identifiers.py": ("97f27cb0fa456f58f54a5a3f5e9093a2ac70152c", "e03e3bc2b934bd639477dc717fd47af4842339f7", "IMPLEMENTATION"),
        "src/automated_trading_bot/instruments/__init__.py": ("ABSENT", "1f1ee477e4d682d76c8a568fcccddcf9c6e29faa", "IMPLEMENTATION"),
        "src/automated_trading_bot/instruments/model.py": ("ABSENT", "348f4ce64a32a5a43445011a4a0028a875f18170", "IMPLEMENTATION"),
        "tests/test_decision.py": ("3a7bb65314c05fbcfb99d8641f8dac72eb4f6f52", "a43a21de51e7ef3c2a032e87be0a907b41f8fae3", "TEST"),
        "tests/test_identifiers.py": ("a71dba9fcd447f82626e75f393395adb866beda4", "aec0d60d52b3ac9f5698f55153c2d880d6d01bf0", "TEST"),
        "tests/test_instrument_reference_model.py": ("ABSENT", "789fc96a11438463ebd8ad15ffce0be75352c2b8", "TEST"),
    }
    validate_cross(load(REGISTER), set(row["path"] for row in record["path_transitions"]))
    assert load(REGISTER)["authority"] == {
        "grants_authority": False,
        "implementation_authorized": False,
        "publication_authorized": False,
        "ai_trading_authority": "NONE",
    }
    damaged = load(REGISTER)
    damaged["unexpected"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(damaged)


def test_candidate_binds_historical_snapshot_and_exact_paths() -> None:
    value = synthetic(candidate())
    validate_cross(value, {"docs/programme/programme-control.json", "src/example/new.py"})


@pytest.mark.parametrize("attack", ["duplicate", "wrong_predecessor", "missing_evidence", "path_mismatch", "authority"])
def test_candidate_adversarial_failures(attack: str) -> None:
    value = synthetic(candidate())
    record = value["records"][0]
    if attack == "duplicate":
        record["path_transitions"].append(deepcopy(record["path_transitions"][0]))
    elif attack == "wrong_predecessor":
        record["path_transitions"][0]["predecessor_blob"] = "0" * 40
    elif attack == "missing_evidence":
        record["predecessor_evidence"] = ["docs/missing.json"]
    elif attack == "path_mismatch":
        record["path_transitions"].pop()
    else:
        record["authority"]["granted"] = ["RB1_IMPLEMENTATION"]
    with pytest.raises((ValueError, ValidationError)):
        Draft202012Validator(load(SCHEMA)).validate(value)
        validate_cross(value, {"docs/programme/programme-control.json", "src/example/new.py"})


def test_candidate_schema_cannot_contain_future_publication_identity() -> None:
    record = candidate()
    record["protected_commit"] = "0" * 40
    with pytest.raises(ValidationError):
        synthetic(record)


def test_publication_must_match_existing_candidate() -> None:
    value = synthetic(candidate(), publication())
    validate_cross(value)
    missing = synthetic(publication())
    with pytest.raises(ValueError, match="without candidate"):
        validate_cross(missing)
    changed = synthetic(candidate(), publication())
    changed["records"][1]["protected_path_blobs"][0]["blob"] = "9" * 40
    with pytest.raises(ValueError, match="differs from candidate"):
        validate_cross(changed)


def test_transition_order_has_no_semantic_effect() -> None:
    first = synthetic(candidate())
    second_record = candidate()
    second_record["path_transitions"].reverse()
    second = synthetic(second_record)
    left = sorted(first["records"][0]["path_transitions"], key=lambda row: row["path"])
    right = sorted(second["records"][0]["path_transitions"], key=lambda row: row["path"])
    assert left == right

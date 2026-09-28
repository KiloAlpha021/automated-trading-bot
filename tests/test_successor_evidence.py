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
PROTECTED_COMMIT = "a72b4ae90c4a02154f7c81cdce010f69996d8e77"
PROTECTED_TREE = "c7b8b30e9cb6deb4a1ac4c89933a8d3ccb79796d"
PUBLICATION_HEAD = "f983f96c3290620bc45d24af37d53d928b742d8f"
SLICE2_BASE_COMMIT = "a7b90227f6817bb7276ec30f90891ca3262d5f03"
SLICE2_BASE_TREE = "f973639c1892fdc44b538daa4e3003d4e61a1392"
SLICE2_HEAD = "f1b0d5978964e5dc8a67e60dc453b0403ca15659"
SLICE2_PROTECTED_COMMIT = "43150c1eaf80899b9309b36aeceb686cf3b83443"
SLICE2_PROTECTED_TREE = "2a0372b4aa6c94d003f8d13ce2f8ed449dc4e421"
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


def validate_cross(
    register: dict[str, Any],
    candidate_paths: set[str] | dict[str, set[str]] | None = None,
) -> None:
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
        expected_paths = (
            candidate_paths.get(record["record_id"])
            if isinstance(candidate_paths, dict)
            else candidate_paths
        )
        if expected_paths is not None and set(paths) != expected_paths:
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
    assert len(records) == 6
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
    publication_record = records[1]
    assert publication_record == {
        "record_id": "SE-PUB-001",
        "record_type": "PROTECTED_PUBLICATION",
        "candidate_record_id": "SE-CAND-001",
        "protected_commit": PROTECTED_COMMIT,
        "protected_tree": PROTECTED_TREE,
        "ordered_merge_parents": [BASE_COMMIT, PUBLICATION_HEAD],
        "publication_reference": "GitHub pull request 29",
        "protected_path_blobs": [
            {"path": row["path"], "blob": row["successor_candidate_blob"]}
            for row in record["path_transitions"]
        ],
        "hosted_checks": [
            {
                "name": "m1-engineering-foundation run 36396898552 job 108845332338",
                "conclusion": "SUCCESS",
            }
        ],
        "post_publication_verification": "PASS",
        "open_obligations_preserved": True,
        "authority": {
            "granted": [],
            "not_granted": [
                "RB1_IMPLEMENTATION_RESUMPTION",
                "STAGE3_IMPLEMENTATION",
                "SLICE1_CANDIDATE_PUBLICATION",
                "RB1_IMPLEMENTATION_BEYOND_SLICE1",
                "RB2_IMPLEMENTATION",
                "RB3_IMPLEMENTATION",
                "STAGE4_IMPLEMENTATION",
                "PROVIDER_SELECTION",
                "STORAGE_SELECTION",
                "PAPER_TRADING",
                "LIVE_TRADING",
                "FINANCIAL_EFFECTS",
                "AI_TRADING_AUTHORITY",
            ],
        },
    }
    assert git("rev-parse", f"{PROTECTED_COMMIT}^{{tree}}") == PROTECTED_TREE
    assert git("rev-list", "--parents", "-n", "1", PROTECTED_COMMIT).split() == [
        PROTECTED_COMMIT,
        BASE_COMMIT,
        PUBLICATION_HEAD,
    ]
    for protected in publication_record["protected_path_blobs"]:
        assert git(
            "rev-parse", f'{PROTECTED_COMMIT}:{protected["path"]}'
        ) == protected["blob"]
    slice2_candidate = records[2]
    assert slice2_candidate["record_id"] == "SE-CAND-002"
    assert slice2_candidate["governing_slice"] == "ATIS-S3-RB1-S2"
    assert slice2_candidate["predecessor_commit"] == SLICE2_BASE_COMMIT
    assert slice2_candidate["predecessor_tree"] == SLICE2_BASE_TREE
    assert slice2_candidate["authorization_references"] == ["PC-DEC-007"]
    assert {
        row["path"]: (
            row["predecessor_blob"],
            row["successor_candidate_blob"],
            row["role"],
            row["requirement_references"],
        )
        for row in slice2_candidate["path_transitions"]
    } == {
        "src/automated_trading_bot/instruments/__init__.py": (
            "1f1ee477e4d682d76c8a568fcccddcf9c6e29faa",
            "9594f8fa3660bd4731c96cc73f60709336eed6c3",
            "IMPLEMENTATION",
            ["S3-REQ-002", "S3-REQ-003", "S3-REQ-004"],
        ),
        "src/automated_trading_bot/instruments/resolution.py": (
            "ABSENT",
            "95c91ffb35ec44f0819afa6277f2521b1cc04d6d",
            "IMPLEMENTATION",
            ["S3-REQ-002", "S3-REQ-003", "S3-REQ-004"],
        ),
        "tests/test_pit_reference_resolution.py": (
            "ABSENT",
            "6ea87aa9348443596c08c686474dec5020436d64",
            "TEST",
            ["S3-REQ-002", "S3-REQ-003", "S3-REQ-004"],
        ),
    }
    slice2_publication = records[3]
    assert slice2_publication["record_id"] == "SE-PUB-002"
    assert slice2_publication["candidate_record_id"] == "SE-CAND-002"
    assert slice2_publication["protected_commit"] == SLICE2_PROTECTED_COMMIT
    assert slice2_publication["protected_tree"] == SLICE2_PROTECTED_TREE
    assert slice2_publication["ordered_merge_parents"] == [
        SLICE2_BASE_COMMIT,
        SLICE2_HEAD,
    ]
    assert slice2_publication["publication_reference"] == "GitHub pull request 31"
    assert {
        row["path"]: row["blob"]
        for row in slice2_publication["protected_path_blobs"]
    } == {
        row["path"]: row["successor_candidate_blob"]
        for row in slice2_candidate["path_transitions"]
    }
    assert len(slice2_publication["hosted_checks"]) == 2
    assert all(row["conclusion"] == "SUCCESS" for row in slice2_publication["hosted_checks"])
    assert slice2_publication["post_publication_verification"] == "PASS"
    assert slice2_publication["open_obligations_preserved"] is True
    assert git("rev-parse", f"{SLICE2_PROTECTED_COMMIT}^{{tree}}") == SLICE2_PROTECTED_TREE
    assert git("rev-list", "--parents", "-n", "1", SLICE2_PROTECTED_COMMIT).split() == [
        SLICE2_PROTECTED_COMMIT,
        SLICE2_BASE_COMMIT,
        SLICE2_HEAD,
    ]
    for protected in slice2_publication["protected_path_blobs"]:
        assert git(
            "rev-parse", f'{SLICE2_PROTECTED_COMMIT}:{protected["path"]}'
        ) == protected["blob"]
    validate_cross(
        load(REGISTER),
        {
            "SE-CAND-001": {row["path"] for row in record["path_transitions"]},
            "SE-CAND-002": {row["path"] for row in slice2_candidate["path_transitions"]},
            "SE-CAND-003": {row["path"] for row in records[4]["path_transitions"]},
        },
    )
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


def test_calendar_s1_candidate_and_publication_are_exact_and_bounded() -> None:
    records = load(REGISTER)["records"]
    assert [record["record_id"] for record in records[-2:]] == ["SE-CAND-003", "SE-PUB-003"]
    candidate_record, publication_record = records[-2:]
    assert candidate_record["governing_rb"] is None
    assert candidate_record["governing_slice"] == "S3-CMP-002-CALENDAR-S1"
    assert candidate_record["predecessor_commit"] == "80ad13f74112d145674d901b27922101c76a967f"
    assert candidate_record["predecessor_tree"] == "4766c97d55e7753d860fe2f16bc5cb14a3545c06"
    assert candidate_record["authorization_references"] == ["PC-DEC-009"]
    expected = {
        "src/automated_trading_bot/calendars/__init__.py": "3554703bd0f56a827df67f4b53427b17200000db",
        "src/automated_trading_bot/calendars/model.py": "4505295dc3bb5014caea18d347cd51b10e0906ee",
        "tests/test_calendar_session_model.py": "8c8e07bbc2f3b26f98f49beb282455a42cdded03",
    }
    assert {row["path"]: row["successor_candidate_blob"] for row in candidate_record["path_transitions"]} == expected
    assert publication_record["candidate_record_id"] == "SE-CAND-003"
    assert publication_record["protected_commit"] == "2cdd03ead05489bba3bee3cdc94339f8a8a53aa1"
    assert publication_record["protected_tree"] == "1d3b4ce7a2a9b20863844060fc47fecdbe445e14"
    assert publication_record["ordered_merge_parents"] == [
        "80ad13f74112d145674d901b27922101c76a967f",
        "3bdf741b8486e66f5aa6a922a6259a17ebe0b4f0",
    ]
    assert {row["path"]: row["blob"] for row in publication_record["protected_path_blobs"]} == expected
    assert len(publication_record["hosted_checks"]) == 3
    assert all(row["conclusion"] == "SUCCESS" for row in publication_record["hosted_checks"])
    assert publication_record["post_publication_verification"] == "PASS"
    assert publication_record["open_obligations_preserved"] is True
    assert not publication_record["authority"]["granted"]
    assert {"CALENDAR_S2_IMPLEMENTATION", "S3_CMP_002_COMPLETION", "AI_TRADING_AUTHORITY"} <= set(publication_record["authority"]["not_granted"])
    assert git("rev-list", "--parents", "-n", "1", publication_record["protected_commit"]).split() == [
        publication_record["protected_commit"],
        *publication_record["ordered_merge_parents"],
    ]

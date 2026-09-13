"""Machine validation for the current formal, non-authoritative M1 re-closure."""

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/m1-closure/closure-manifest-reclosed-2026-09-12.json"
REOPENED = ROOT / "docs/m1-closure/closure-manifest-reopened-2026-09-13.json"
HISTORICAL = ROOT / "docs/m1-closure/closure-manifest-historical-2026-09-12.json"
AUDITED_COMPLETION = "4a4bfb6879624b1f78cff69b06bba56a8707e66f"
BASELINE = "db1cd10d3aaf9d20a90b043fd754e4e65145bf3f"
HISTORICAL_BASELINE = "8470735a37372a1c5060c59adfcd44d3348f35bb"
SOURCE = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
HISTORICAL_SHA256 = "bd201775ed5d9fd9d7cd1f42bfe0f5418259622071f1102f1cfd71a6c84ce3d0"
ORIGINAL_SOURCE = (
    "db1cd10d3aaf9d20a90b043fd754e4e65145bf3f:docs/m1-closure/closure-manifest.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_at_audited_completion(path: str) -> dict:
    return json.loads(_git_bytes("show", f"{AUDITED_COMPLETION}:{path}"))


def _validate(data: dict) -> None:
    checkpoint = _load_at_audited_completion(
        "docs/m1-closure/atomic-assessment-checkpoint.json"
    )
    traceability = _load_at_audited_completion("docs/m1-closure/traceability.json")
    stage0b = _load_at_audited_completion(
        "docs/m1-closure/stage0b-phase39-67-coverage.json"
    )
    hosted = _load_at_audited_completion(
        "docs/m1-closure/hosted-branch-protection-evidence.json"
    )
    assert data["schema_version"] == 2
    assert data["milestone_id"] == "M1"
    assert data["lifecycle_state"] == "COMPLETE"
    assert data["exit_gate"] == "PASS"
    assert data["baseline_revision"] == BASELINE
    assert data["governing_specification"]["sha256"] == SOURCE
    completed = datetime.fromisoformat(data["completed_at"].replace("Z", "+00:00"))
    assert completed.tzinfo == timezone.utc and data["completed_at"].endswith("Z")
    assert data["authorization"] == "NONE"
    authority = data["authority_statement"].lower()
    for denied in ("trading", "broker", "execution", "stage 2"):
        assert f"no {denied}" in authority

    rows = traceability["rows"]
    records = checkpoint["records"]
    row_ids = [row["id"] for row in rows]
    record_ids = [record["id"] for record in records]
    assert len(row_ids) == len(set(row_ids)) == 50
    assert len(record_ids) == len(set(record_ids)) == 50
    assert set(row_ids) == set(record_ids) == set(data["coverage"]["atom_ids"])
    assert all(record["status"] == "VERIFIED" for record in records)
    assert data["coverage"]["traceability_atoms"] == 50
    assert data["coverage"]["assessment_records"] == 50
    assert data["coverage"]["required_parent_imps"] == checkpoint["required_parent_set"]
    expected_deferrals = {
        record["id"]: {
            "id": record["id"],
            "requirement": record["requirement"],
            "source": record["explicit_deferral_source"],
            "limits": record["verification_limits"],
        }
        for record in records
        if record.get("explicit_deferral_source")
    }
    declared_deferrals = data["coverage"]["explicitly_deferred_atoms"]
    assert len(declared_deferrals) == len(expected_deferrals)
    assert {item["id"]: item for item in declared_deferrals} == expected_deferrals
    assert data["coverage"]["assessment_totals"] == {
        "VERIFIED": 50,
        "INSUFFICIENT_EVIDENCE": 0,
        "FAILED": 0,
        "unassessed": 0,
    }
    assert checkpoint["assessment"] == "COMPLETED"
    assert checkpoint["complete"] is True
    assert checkpoint["assessment_status"] == "COMPLETED"
    assert checkpoint["authorization"] == "NONE"
    assert checkpoint["unresolved_traceability_ids"] == []
    assert checkpoint["unassessed_requirement_ids"] == []
    assert checkpoint["reclosure"]["baseline_revision"] == BASELINE
    assert checkpoint["reclosure"]["stage2_authorized"] is False

    stage = data["stage0b"]
    assert (
        stage["classification"]
        == stage0b["classification"]
        == "PASS_WITH_CONTROLLED_LEGACY_PROVENANCE"
    )
    assert (
        stage["mandatory_disclosure"]
        == stage0b["historical_disclosure"]
        == "ORIGINAL R39 INDIVIDUAL SEMANTICS NOT RECOVERED"
    )
    assert stage["original_r39_semantics_recovered"] is False
    assert stage["semantic_equivalence_claimed"] is False
    assert len(stage0b["legacy_provenance"]) == stage["legacy_id_count"] == 36
    assert all(
        item["wording_status"] == "UNRECOVERED" for item in stage0b["legacy_provenance"]
    )
    assert all(
        item["semantic_equivalence_claimed"] is False
        for item in stage0b["legacy_provenance"]
    )
    future = [item for item in stage0b["atoms"] if item["applicable_stage"] != "M1"]
    assert len(future) == stage["future_not_implemented_atoms"] == 9
    assert all(
        item["implementation_status"] == "NOT_YET_IMPLEMENTED" for item in future
    )
    assert data["future_controlled_obligations_implemented"] is False

    protection = data["hosted_protection"]
    assert protection["pull_request_required"] is True
    assert protection["required_approving_review_count"] == 0
    assert protection["required_status_check"] == "m1-engineering-foundation"
    assert protection["strict"] is True
    assert protection["ordinary_contributor_bypass_allowed"] is False
    assert protection["force_pushes_allowed"] is False
    assert protection["deletions_allowed"] is False
    assert (
        protection["evidence"]
        == "docs/m1-closure/hosted-branch-protection-evidence.json"
    )
    assert hosted["repository"] == protection["repository"]
    exact = data["hosted_exact_head_ci"]
    assert exact["head_sha"] == BASELINE
    assert exact["check"] == "m1-engineering-foundation"
    assert exact["status"] == "completed" and exact["conclusion"] == "success"
    assert exact["run_id"] == 34711110258
    assert exact["source"].startswith("Authenticated GitHub REST API")

    owner_manifest = _load_at_audited_completion(
        "docs/m1-closure/owner-dispositions.json"
    )
    assert data["evidence"]["owner_dispositions"] == [
        item["decision_id"] for item in owner_manifest["decisions"]
    ]
    for reference in data["evidence"]["repository_artifacts"]:
        assert not Path(reference).is_absolute()
        assert (ROOT / reference).is_file()
    verification = data["verification"]
    assert verification["engineering_foundation"] == "PASS"
    assert verification["fresh_environment"]["result"] == "PASS"
    assert verification["fresh_environment"]["exact_revision"] is True
    assert verification["pip_check"] == verification["pip_audit"] == "PASS"
    assert verification["m1_3"] == {"passed": 152, "failed": 0}
    assert verification["m1_7"] == {"passed": 33, "failed": 0}
    assert verification["m1_acceptance"] == {"passed": 758, "failed": 0}
    assert verification["full_regression"] == {"passed": 791, "failed": 0}
    assert verification["closure_provenance"] == {"passed": 116, "failed": 0}
    assert verification["ruff"] == verification["strict_mypy"] == "PASS"

    lineage = data["lineage"]
    original = lineage["original_closure"]
    assert original["manifest"] == HISTORICAL.relative_to(ROOT).as_posix()
    assert original["baseline_revision"] == HISTORICAL_BASELINE
    assert original["sha256"] == HISTORICAL_SHA256
    assert original["status"] == "HISTORICAL_SUPERSEDED"
    assert hashlib.sha256(HISTORICAL.read_bytes()).hexdigest() == HISTORICAL_SHA256
    assert _load(HISTORICAL)["baseline_revision"] == HISTORICAL_BASELINE
    assert lineage["reopened_remediation"]["classification"] == "REOPEN_M1"
    assert lineage["current_reclosure"] == {
        "baseline_revision": BASELINE,
        "authority": "CURRENT_M1_COMPLETION",
    }
    assert data["residual_blockers"] == []
    restrictions = " ".join(data["restrictions"]).lower()
    for denied in (
        "stage 2",
        "paper trading",
        "live trading",
        "broker execution",
        "future functionality",
        "recovery of original r39",
    ):
        assert denied in restrictions


def _git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout


def test_historical_manifest_byte_identity_is_checkout_invariant() -> None:
    historical_path = HISTORICAL.relative_to(ROOT).as_posix()
    working = HISTORICAL.read_bytes()
    original = _git_bytes("show", ORIGINAL_SOURCE)
    committed = _git_bytes("show", f"HEAD:{historical_path}")
    assert working == original == committed
    assert hashlib.sha256(working).hexdigest() == HISTORICAL_SHA256
    attributes = _git_bytes(
        "check-attr", "binary", "text", "--", historical_path
    ).decode()
    assert f"{historical_path}: binary: set" in attributes
    assert f"{historical_path}: text: unset" in attributes
    changed = working[:-1] + bytes([working[-1] ^ 1])
    assert hashlib.sha256(changed).hexdigest() != HISTORICAL_SHA256
    crlf = working.replace(b"\n", b"\r\n")
    assert crlf != working
    assert hashlib.sha256(crlf).hexdigest() != HISTORICAL_SHA256


def test_m1_completion_manifest_is_valid_and_attributable() -> None:
    assert MANIFEST.read_bytes() == _git_bytes(
        "show", f"{AUDITED_COMPLETION}:docs/m1-closure/closure-manifest.json"
    )
    _validate(_load(MANIFEST))


def test_reopened_cycle_manifest_remains_historical() -> None:
    data = _load(REOPENED)
    assert data["lifecycle_state"] == "REOPENED"
    assert data["exit_gate"] == "NOT_PASS"
    assert data["authorization"] == "NONE"
    assert data["residual_blockers"][0]["id"] == "M1-AUDIT-NONFINITE-DECIMAL-SCOPE"
    assert data["residual_blockers"][0]["status"] == "OWNER_DECISION_REQUIRED"


@pytest.mark.parametrize(
    "mutation",
    [
        "baseline",
        "source",
        "disclosure",
        "recovered",
        "coverage",
        "hosted",
        "exact_ci",
        "authority",
        "stage2",
        "future",
        "history",
    ],
)
def test_m1_completion_manifest_rejects_false_closure(mutation: str) -> None:
    data = copy.deepcopy(_load(MANIFEST))
    if mutation == "baseline":
        data["baseline_revision"] = "0" * 40
    elif mutation == "source":
        data["governing_specification"]["sha256"] = "0" * 64
    elif mutation == "disclosure":
        data["stage0b"]["mandatory_disclosure"] = "recovered"
    elif mutation == "recovered":
        data["stage0b"]["original_r39_semantics_recovered"] = True
    elif mutation == "coverage":
        data["coverage"]["assessment_totals"]["VERIFIED"] = 49
    elif mutation == "hosted":
        data["hosted_protection"]["evidence"] = ""
    elif mutation == "exact_ci":
        data["hosted_exact_head_ci"]["head_sha"] = "0" * 40
    elif mutation == "authority":
        data["authorization"] = "LIVE"
    elif mutation == "stage2":
        data["authority_statement"] = data["authority_statement"].replace(
            "no Stage 2", "Stage 2"
        )
    elif mutation == "future":
        data["future_controlled_obligations_implemented"] = True
    else:
        data["lineage"]["original_closure"]["status"] = "CURRENT"
    with pytest.raises(AssertionError):
        _validate(data)

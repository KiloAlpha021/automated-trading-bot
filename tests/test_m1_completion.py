"""Machine validation for the formal, non-authoritative M1 closure record."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/m1-closure/closure-manifest.json"
BASELINE = "8470735a37372a1c5060c59adfcd44d3348f35bb"
SOURCE = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
HASH_MODEL = "sha256:utf8:newlines-lf:v1"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_locked_dependencies() -> dict[str, str]:
    result = {}
    lock = subprocess.run(
        ["git", "show", f"{BASELINE}:requirements-dev.lock"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    for line in lock.splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split()[0].split("==", 1)
        result[name.lower()] = version
    return result


def _baseline_owner_ids() -> list[str]:
    content = subprocess.run(
        ["git", "show", f"{BASELINE}:docs/m1-closure/owner-dispositions.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [item["decision_id"] for item in json.loads(content)["decisions"]]


def _baseline_json(path: str) -> dict:
    content = subprocess.run(
        ["git", "show", f"{BASELINE}:{path}"], cwd=ROOT, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return json.loads(content)


def _validate(data: dict) -> None:
    checkpoint = _load(ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json")
    traceability = _load(ROOT / "docs/m1-closure/traceability.json")
    assert set(data) == {
        "schema_version", "milestone_id", "milestone_status", "exit_gate",
        "completed_at", "baseline_revision", "governing_specification",
        "authorization", "authority_statement", "coverage", "owner_dispositions",
        "evidence", "reproducibility", "test_results", "residual_blockers",
        "restrictions",
    }
    assert data["schema_version"] == 1
    assert data["milestone_id"] == "M1"
    assert data["milestone_status"] == "COMPLETE"
    assert data["exit_gate"] == "PASS"
    assert data["baseline_revision"] == BASELINE
    assert data["governing_specification"] == {
        "title": "Implementation Specification v1.0",
        "sha256": SOURCE,
    }
    completed = datetime.fromisoformat(data["completed_at"].replace("Z", "+00:00"))
    assert completed.tzinfo == timezone.utc and data["completed_at"].endswith("Z")
    assert data["authorization"] == "NONE"
    assert "no trading authority" in data["authority_statement"].lower()

    rows = traceability["rows"]
    records = checkpoint["records"]
    row_ids = [row["id"] for row in rows]
    record_ids = [record["id"] for record in records]
    assert len(row_ids) == len(set(row_ids)) >= 30
    assert set(row_ids) == set(record_ids)
    current = _load(ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json")
    assert current["complete"] is False
    assert current["assessment_status"] == "BLOCKED"
    assert checkpoint["authorization"] == "NONE"
    assert checkpoint["unassessed_requirement_ids"] == []
    assert "IMP-001-M1-01" in current["unresolved_traceability_ids"]
    assert current["post_closure_audit"]["classification"] == "REOPEN_M1"
    assert current["post_closure_audit"]["historical_completion_manifest"] == (
        "docs/m1-closure/closure-manifest.json"
    )

    coverage = data["coverage"]
    recorded_coverage = _load(MANIFEST)["coverage"]
    assert coverage["m1_required_atom_ids"] == recorded_coverage["m1_required_atom_ids"]
    historical_ids = set(coverage["m1_required_atom_ids"]) | {
        item["id"] for item in coverage["explicitly_deferred_atoms"]
    }
    historical_records = [r for r in records if r["id"] in historical_ids]
    implemented = sorted(
        r["id"] for r in historical_records if r["applicability"] == "M1_REQUIRED"
    )
    deferred = {
        r["id"]: r for r in historical_records
        if r["applicability"] == "EXPLICITLY_DEFERRED"
    }
    assert coverage["traceability_atoms"] == 30
    assert coverage["assessment_records"] == 30
    assert coverage["assessment_totals"] == {
        "VERIFIED": 30, "INSUFFICIENT_EVIDENCE": 0, "unassessed": 0, "FAILED": 0,
    }
    assert coverage["required_parent_imps"] == checkpoint["required_parent_set"]
    assert coverage["m1_required_atom_ids"] == implemented
    declared_deferred = {r["id"]: r for r in coverage["explicitly_deferred_atoms"]}
    assert len(declared_deferred) == len(coverage["explicitly_deferred_atoms"])
    assert set(declared_deferred) == set(deferred)
    for identity, record in deferred.items():
        declared = declared_deferred[identity]
        assert declared["requirement"] == record["requirement"]
        assert declared["source"] == record["explicit_deferral_source"]
        assert declared["limits"] == record["verification_limits"]

    assert data["owner_dispositions"] == _baseline_owner_ids()
    assert data["evidence"]["hash_model"] == HASH_MODEL
    for reference in data["evidence"]["repository_artifacts"]:
        assert not Path(reference).is_absolute()
        assert (ROOT / reference).is_file()
    assert data["reproducibility"]["python"] == "3.12.10"
    assert data["reproducibility"]["lockfile"] == "requirements-dev.lock"
    assert data["reproducibility"]["dependencies"] == _baseline_locked_dependencies()
    assert data["reproducibility"]["pip_check"] == "PASS"
    assert data["test_results"] == {
        "m1_acceptance": {"passed": 585, "failed": 0},
        "full_regression": {"passed": 670, "failed": 0},
        "closure_evidence_provenance": {"passed": 99, "failed": 0},
    }
    assert data["residual_blockers"] == []
    restrictions = " ".join(data["restrictions"]).lower()
    for denied in ("stage 2", "paper trading", "live trading", "broker execution", "lifecycle promotion"):
        assert denied in restrictions


def test_m1_completion_manifest_is_valid_and_attributable():
    _validate(_load(MANIFEST))


@pytest.mark.parametrize("mutation", ["baseline", "authority", "coverage", "duplicate", "evidence", "deferral"])
def test_m1_completion_manifest_rejects_false_closure(mutation):
    data = copy.deepcopy(_load(MANIFEST))
    if mutation == "baseline":
        data["baseline_revision"] = "0" * 40
    elif mutation == "authority":
        data["authorization"] = "LIVE"
    elif mutation == "coverage":
        data["coverage"]["m1_required_atom_ids"].pop()
    elif mutation == "duplicate":
        data["coverage"]["explicitly_deferred_atoms"].append(
            copy.deepcopy(data["coverage"]["explicitly_deferred_atoms"][0])
        )
    elif mutation == "evidence":
        data["evidence"]["repository_artifacts"].append("docs/m1-closure/missing.json")
    else:
        data["coverage"]["explicitly_deferred_atoms"][0]["limits"] = "Implemented"
    with pytest.raises(AssertionError):
        _validate(data)

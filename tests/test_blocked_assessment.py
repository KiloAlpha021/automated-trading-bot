"""Verify the M1 assessment state using fresh, non-authoritative evidence."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELECTION = [
    "tests/test_alerts.py",
    "tests/test_health.py",
    "tests/test_metrics.py",
    "tests/test_diagnostics.py",
    "tests/test_secret_provider_contract.py",
    "tests/test_initial_migration_specification.py",
    "tests/test_event_storage_design.py",
    "tests/test_decision.py::test_expiry_across_utc_date_boundary",
    "tests/test_clock.py",
    "tests/test_timestamp.py",
    "tests/test_closure_security.py",
    "tests/test_architecture.py",
    "tests/test_closure_traceability.py::test_required_parents_and_explicit_dispositions",
    "tests/test_closure_traceability.py::test_identity_precedence_rejects_changed_evidence",
]

INPUT_HASH_MODEL = "sha256:utf8:newlines-lf:v1"


def _checkout_independent_text_sha256(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError(f"Evidence input is not text: {path}")
    text = raw.decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _run_evidence() -> dict:
    with tempfile.TemporaryDirectory(prefix="m1-evidence-") as directory:
        report = Path(directory) / "execution.xml"
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *SELECTION, f"--junitxml={report}"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        assert report.is_file(), "Evidence execution produced no report"
        cases = []
        for case in ET.parse(report).iter("testcase"):
            outcome = "passed"
            for kind in ("failure", "error", "skipped"):
                if case.find(kind) is not None:
                    outcome = kind
                    break
            # Parameter labels can contain synthetic secrets; retain identity by hash.
            identity = case.get("classname", "") + "::" + case.get("name", "")
            cases.append([hashlib.sha256(identity.encode()).hexdigest(), outcome])
        assert run.returncode == 0, "Underlying evidence tests failed"
        assert cases and all(outcome == "passed" for _, outcome in cases)
    inputs = sorted((ROOT / "src").rglob("*.py")) + [
        ROOT / "tests/test_alerts.py",
        ROOT / "docs/m1-closure/M1-alert-scope-clarification.md",
        ROOT / "tests/test_health.py",
        ROOT / "docs/m1-closure/M1-health-scope-clarification.md",
        ROOT / "docs/m1-closure/M1-telemetry-degradation-scope-clarification.md",
        ROOT / "tests/test_metrics.py",
        ROOT / "docs/m1-closure/M1-metrics-scope-clarification.md",
        ROOT / "tests/test_diagnostics.py",
        ROOT / "docs/m1-closure/M1-foundation-logging.md",
        ROOT / "tests/test_secret_provider_contract.py",
        ROOT / "docs/m1-closure/M1-secret-provider-contract.md",
        ROOT / "tests/test_initial_migration_specification.py",
        ROOT / "docs/m1-closure/M1-initial-migration-specification.md",
        ROOT / "tests/test_event_storage_design.py",
        ROOT / "docs/m1-closure/M1-event-storage-design.md",
        ROOT / "tests/test_decision.py",
        ROOT / "tests/test_clock.py",
        ROOT / "tests/test_timestamp.py",
        ROOT / "docs/m1-closure/M1-calendar-scope-clarification.md",
        ROOT / "tests/test_closure_security.py",
        ROOT / "tests/test_architecture.py",
        ROOT / "tests/test_closure_traceability.py",
        ROOT / "docs/M1.8-evidence.md",
        ROOT / "docs/m1-closure/M1-scope-clarification.md",
        ROOT / "docs/m1-closure/traceability.json",
    ]
    return {
        "selection": SELECTION,
        "test_cases": sorted(cases),
        "passed": len(cases), "failed": 0,
        "input_hash_model": INPUT_HASH_MODEL,
        "input_sha256": {
            p.relative_to(ROOT).as_posix(): _checkout_independent_text_sha256(p)
            for p in inputs
        },
    }


def _verify(checkpoint: dict, traceability: dict, actual: dict) -> None:
    assert checkpoint["complete"] is False
    assert checkpoint["assessment_status"] == "BLOCKED"
    assert checkpoint["authorization"] == "NONE"
    assert checkpoint["current_evidence"] == actual, "Recorded evidence differs from fresh execution"
    assert checkpoint["historical_assessments"], "Historical observations lost"
    rows = {row["id"]: row for row in traceability["rows"]}
    assessed = {row["id"]: row for row in checkpoint["records"]}
    # Unresolved scope and explicit missing M1 evidence both block closure.
    unresolved = sorted(
        {key for key, row in rows.items()
         if row["disposition"] == "UNRESOLVED"
         or (row["disposition"] == "M1" and row["residual_gap"])}
        | {key for key, row in assessed.items()
           if row["status"] in {"INSUFFICIENT_EVIDENCE", "NOT_IMPLEMENTED"}}
    )
    assert checkpoint["unresolved_traceability_ids"] == unresolved, "Unresolved blocker omitted"
    assert unresolved == ["IMP-001-M1-01"]
    expected_unassessed = sorted(rows.keys() - assessed.keys())
    assert checkpoint["unassessed_requirement_ids"] == expected_unassessed
    assert checkpoint["parent_set_is_assessed"] is (not expected_unassessed)
    for key in unresolved:
        assert key in checkpoint["stop_reason"], "Stop reason lost actual blocker"
        if key in assessed:
            assert assessed[key]["status"] in {"INSUFFICIENT_EVIDENCE", "NOT_IMPLEMENTED"}
    assert assessed["IMP-001-M1-01"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert all(record["status"] == "VERIFIED" for key, record in assessed.items()
               if key != "IMP-001-M1-01")
    assert "specification-provenance" in checkpoint["stop_reason"]
    assert (ROOT / "docs/m1-closure/closure-manifest.json").is_file()
    assert checkpoint["post_closure_audit"]["classification"] == "REOPEN_M1"


@pytest.fixture(scope="module")
def evidence():
    return _run_evidence()


def _inputs():
    return (
        json.loads((ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text()),
        json.loads((ROOT / "docs/m1-closure/traceability.json").read_text()),
    )


def test_blocked_checkpoint_matches_current_evidence(evidence):
    _verify(*_inputs(), evidence)


def test_evidence_input_hash_is_checkout_independent_and_content_sensitive(tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    changed = tmp_path / "changed.txt"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")
    changed.write_bytes(b"first\nchanged\n")

    assert _checkout_independent_text_sha256(lf) == _checkout_independent_text_sha256(crlf)
    assert _checkout_independent_text_sha256(lf) != _checkout_independent_text_sha256(changed)


@pytest.mark.parametrize("content,error", [(b"bad\x00text", ValueError), (b"\xff", UnicodeDecodeError)])
def test_evidence_input_hash_rejects_non_text(content, error, tmp_path):
    evidence_input = tmp_path / "input"
    evidence_input.write_bytes(content)
    with pytest.raises(error):
        _checkout_independent_text_sha256(evidence_input)


@pytest.mark.parametrize("mutation", ["completion", "blocker", "test_count", "test_outcome", "unassessed", "authority", "unverified"])
def test_blocked_checkpoint_rejects_false_observation(evidence, mutation):
    checkpoint, traceability = _inputs()
    checkpoint = copy.deepcopy(checkpoint)
    if mutation == "completion":
        checkpoint["complete"] = True
    elif mutation == "blocker":
        checkpoint["unresolved_traceability_ids"] = (
            [] if checkpoint["unresolved_traceability_ids"] else ["IMP-001-M1-02"]
        )
    elif mutation == "test_count":
        checkpoint["current_evidence"]["passed"] += 1
    elif mutation == "test_outcome":
        checkpoint["current_evidence"]["test_cases"][0][1] = "skipped"
    elif mutation == "unassessed":
        checkpoint["unassessed_requirement_ids"] = ["IMP-033-M1-03"] if not checkpoint["unassessed_requirement_ids"] else []
    elif mutation == "authority":
        checkpoint["authorization"] = "APPROVED"
    else:
        target = next(row for row in checkpoint["records"] if row["id"] == "IMP-001-M1-01")
        target["status"] = "VERIFIED"
    with pytest.raises(AssertionError):
        _verify(checkpoint, traceability, evidence)

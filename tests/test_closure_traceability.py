"""Reference-integrity evidence only; semantic atomic coverage needs review."""

import ast
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from owner_disposition_evidence import (
    BEGIN, END, EXPECTED_IDS, validate_owner_dispositions,
)


def _check_reference(root: Path, reference: str, *, test: bool = False) -> None:
    parts = reference.split("::")
    path = (root / parts[0]).resolve()
    assert path.is_relative_to(root.resolve()), f"Reference escapes repository: {reference}"
    assert path.is_file(), f"Missing evidence file: {reference}"
    if test:
        assert len(parts) == 2, f"Expected file::test_function: {reference}"
        names = {
            node.name for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        }
        assert parts[1] in names, f"Missing test function: {reference}"


def test_m1_references_exist():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/m1-closure/traceability.json").read_text())
    rows = [row for row in data["rows"] if row["disposition"] == "M1"]
    assert rows, "No M1 mappings to verify"
    for row in rows:
        assert row["artifacts"] and row["tests"], f"Empty mapping: {row['id']}"
        try:
            for reference in row["artifacts"]:
                _check_reference(root, reference)
            for reference in row["tests"]:
                _check_reference(root, reference, test=True)
        except AssertionError as error:
            raise AssertionError(f"{row['id']}: {error}") from error


def test_reference_check_accepts_existing_function(tmp_path):
    (tmp_path / "test_example.py").write_text("def test_example(): pass\n")
    _check_reference(tmp_path, "test_example.py::test_example", test=True)


def test_reference_check_rejects_missing_file(tmp_path):
    with pytest.raises(AssertionError, match="Missing evidence file"):
        _check_reference(tmp_path, "absent.py::test_example", test=True)


def test_reference_check_rejects_missing_function(tmp_path):
    (tmp_path / "test_example.py").write_text("def test_other(): pass\n")
    with pytest.raises(AssertionError, match="Missing test function"):
        _check_reference(tmp_path, "test_example.py::test_example", test=True)


# Trusted values checked against the governing source and repository owner record.
_SOURCE = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
_OWNER = "298ce22148c2f7b917c75fac2b5f6773e409a9ae089a2614c5a3dc2919b969d4"
_SCOPE_TEXT_SHA256 = "afe38321d55cdfc1b1122010c3ee132091b5f87c74bcc866207f3b6ec4319237"
_PRECEDENCE = "This decision governs the affected M1 applicability questions over historical interpretations."


def _check_identity_precedence(data, evidence, clarification):
    assert data["source_sha256"] == _SOURCE, "Wrong specification identity"
    assert _SOURCE in evidence and _SOURCE in clarification, "Missing source lineage"
    row = next(row for row in data["rows"] if row["id"] == "IMP-001-M1-01")
    assert row["provenance_status"] == "INFERRED", "Engineering mapping relabelled canonical"
    assert "engineering choices, not verbatim source" in evidence
    assert "explicit project-owner governance instruction" in clarification
    assert _OWNER in clarification
    assert _PRECEDENCE in clarification, "Missing governing precedence"
    block = clarification.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0].strip() + "\n"
    assert hashlib.sha256(block.encode()).hexdigest() == _SCOPE_TEXT_SHA256, "Owner decision changed"


def test_owner_dispositions_are_repository_contained_and_hash_bound():
    root = Path(__file__).resolve().parents[1]
    records = validate_owner_dispositions(root)
    assert set(records) == EXPECTED_IDS


@pytest.mark.parametrize("mutation", ["text", "hash", "missing"])
def test_owner_disposition_validation_rejects_mutation_or_removal(tmp_path, mutation):
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "repository"
    shutil.copytree(root / "docs/m1-closure", target / "docs/m1-closure")
    manifest_path = target / "docs/m1-closure/owner-dispositions.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["decisions"][0]
    artifact = target / record["artifact"]
    if mutation == "text":
        artifact.write_text(artifact.read_text(encoding="utf-8").replace("Event duplicate rejection", "Event acceptance"), encoding="utf-8")
    elif mutation == "hash":
        record["canonical_text_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        artifact.unlink()
    with pytest.raises((AssertionError, FileNotFoundError)):
        validate_owner_dispositions(target)


def test_normative_closure_evidence_has_no_external_path_dependency():
    root = Path(__file__).resolve().parents[1]
    paths = list((root / "tests").glob("*.py")) + [
        root / "docs/m1-closure/traceability.json",
        root / "docs/m1-closure/atomic-assessment-checkpoint.json",
        root / "docs/m1-closure/owner-dispositions.json",
    ]
    forbidden = (
        ".codex" + "/attachments", ".codex" + "\\attachments",
        "C:" + "/" + "Users" + "/", "C:" + "\\" + "Users" + "\\",
        "/" + "Users" + "/",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in forbidden), path


def test_traceability_uses_repository_owner_disposition_manifest():
    root = Path(__file__).resolve().parents[1]
    traceability = json.loads((root / "docs/m1-closure/traceability.json").read_text())
    checkpoint = json.loads((root / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text())
    evidence = "docs/m1-closure/owner-dispositions.json"
    for record in traceability["rows"] + checkpoint["records"]:
        if "M1-SCOPE-" in json.dumps(record):
            assert evidence in record["evidence"]
    assert set(checkpoint["scope_decisions"]) == EXPECTED_IDS


def _identity_inputs():
    root = Path(__file__).resolve().parents[1]
    return (
        json.loads((root / "docs/m1-closure/traceability.json").read_text()),
        (root / "docs/M1.8-evidence.md").read_text(encoding="utf-8"),
        (root / "docs/m1-closure/M1-scope-clarification.md").read_text(encoding="utf-8"),
    )


def test_required_parents_and_explicit_dispositions():
    # Historical reference name retained; this proves identity and precedence,
    # not complete parent/atomic coverage or runtime implementation.
    _check_identity_precedence(*_identity_inputs())


@pytest.mark.parametrize("mutation", ["identity", "provenance", "precedence"])
def test_identity_precedence_rejects_changed_evidence(mutation):
    data, evidence, clarification = _identity_inputs()
    if mutation == "identity":
        data["source_sha256"] = "0" * 64
    elif mutation == "provenance":
        next(row for row in data["rows"] if row["id"] == "IMP-001-M1-01")["provenance_status"] = "CONFIRMED"
    else:
        clarification = clarification.replace(_PRECEDENCE, "Historical interpretations govern.")
    with pytest.raises(AssertionError):
        _check_identity_precedence(data, evidence, clarification)

def test_imp_029_atomic_scope_decomposition():
    data, _, clarification = _identity_inputs()
    rows = {row["id"]: row for row in data["rows"]}
    expected = {
        "IMP-029-M1-02": ("M1", "application- and authority-level least privilege"),
        "IMP-029-M1-03": ("M1", "secret-provider interface contract"),
        "IMP-029-M1-04": ("DEFERRED", "executable secret providers"),
    }
    for identity, (disposition, statement) in expected.items():
        assert rows[identity]["disposition"] == disposition
        assert statement in rows[identity]["requirement"]
    assert "Application/authority-level least privilege" in clarification
    assert "Development/research/replay cannot possess production trading authority" in clarification
    assert "Administrative privilege cannot itself grant trading authority" in clarification
    assert "Unverified supply-chain integrity cannot create production financial authority" in clarification
    assert "Production/service-identity least-privilege implementation details until those identities actually exist" in clarification


def test_m13_owner_contract_and_atomic_decomposition():
    root = Path(__file__).resolve().parents[1]
    records = validate_owner_dispositions(root)
    assert "M1-SCOPE-2026-09-12-03" in records
    assert "M1-SCOPE-2026-09-13-01" in records
    data = json.loads((root / "docs/m1-closure/traceability.json").read_text())
    rows = {row["id"]: row for row in data["rows"]}
    expected = {
        "IMP-001-M1-15": "Currency core contract",
        "IMP-001-M1-16": "Money core contract",
        "IMP-001-M1-17": "Quantity core contract",
        "IMP-001-M1-18": "serialization through existing M1 core-contract boundaries",
        "IMP-001-M1-19": "property and invariant acceptance",
    }
    for identity, statement in expected.items():
        row = rows[identity]
        assert row["disposition"] == "M1"
        assert statement in row["requirement"]
        assert "M1-SCOPE-2026-09-12-03" in row["source"]
        if identity in {"IMP-001-M1-16", "IMP-001-M1-17", "IMP-001-M1-19"}:
            assert "M1-SCOPE-2026-09-13-01" in row["source"]
        assert "docs/m1-closure/owner-dispositions.json" in row["evidence"]

    decision = (root / "docs/m1-closure/M1-core-contracts-scope-clarification.md").read_text()
    finite_decision = (
        root / "docs/m1-closure/M1-financial-primitives-scope-clarification.md"
    ).read_text()
    assert "do not gain new public\nserialization APIs solely for M1" in decision
    assert "Money and Quantity therefore require no new\nstandalone serializers" in decision
    assert "Hypothesis is not required" in decision
    assert "Money.amount and Quantity.value must each be a Decimal and must be finite" in finite_decision
    assert "positive zero, signed negative zero" in finite_decision
    assert "defines no\npositivity, non-negativity" in finite_decision
    assert "does not authorize M1.7 remediation" in decision


def test_m17_has_dedicated_atomic_traceability():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/m1-closure/traceability.json").read_text())
    rows = {row["id"]: row for row in data["rows"]}
    expected = {
        "IMP-001-M1-20": "canonical M1.7 operational-mode contract",
        "IMP-001-M1-21": "NO_NEW_ORDERS deterministically",
        "IMP-001-M1-22": "cannot create operational or financial authority",
    }
    for identity, statement in expected.items():
        row = rows[identity]
        assert row["disposition"] == "M1"
        assert statement in row["requirement"]
        assert "Implementation Specification v1.0 section 11 M1.7" in row["source"]
        assert "docs/M1.7-operational-mode.md" in row["artifacts"]
        assert row["tests"]

def test_unrecovered_provenance_is_not_substantive_authority():
    data, _, _ = _identity_inputs()
    rows = {row["id"]: row for row in data["rows"]}
    persistence = rows["IMP-026-M1-02"]
    assert _SOURCE in persistence["source"]
    assert "M1-SCOPE-2026-09-11-01" in persistence["source"]
    assert "standalone ADR-005 artifact was not recovered" in persistence["source"]
    telemetry = rows["IMP-030-M1-07"]
    assert all(identity not in telemetry["source"] for identity in (
        "R41-CI049", "R41-CI050", "R41-CI051",
    ))
    assert telemetry["residual_gap"] == ""
    decision = (Path(__file__).resolve().parents[1] /
        "docs/m1-closure/M1-telemetry-degradation-scope-clarification.md").read_text()
    assert "unrecoverable text is not substantive acceptance authority" in decision
    assert "is not used by this decision" in decision

def _verify_complete_atomic_mapping(data, checkpoint):
    rows = data["rows"]
    records = checkpoint["records"]
    row_ids = [row["id"] for row in rows]
    record_ids = [record["id"] for record in records]
    assert len(row_ids) == len(set(row_ids)), "Duplicate atomic traceability identity"
    assert len(record_ids) == len(set(record_ids)), "Duplicate atomic assessment identity"
    assert set(row_ids) == set(record_ids), "Orphan or omitted atomic mapping"
    assert set(checkpoint["required_parent_set"]) == {row["parent"] for row in rows}
    assert checkpoint["parent_set_is_assessed"] is True
    assert checkpoint["unassessed_requirement_ids"] == []
    assessed = {record["id"]: record for record in records}
    for row in rows:
        record = assessed[row["id"]]
        assert row["parent"] == record["parent_imp"]
        assert row["requirement"] == record["requirement"]
        assert row["source"].strip() and record["source"].strip()
        assert row["provenance_status"] == "INFERRED"
        expected_status = (
            "INSUFFICIENT_EVIDENCE"
            if row["id"] == "IMP-001-M1-14"
            else "VERIFIED"
        )
        assert record["status"] == expected_status
        assert record["verification_evidence"]
        if row["disposition"] == "M1":
            assert record["applicability"] == "M1_REQUIRED"
            assert row["artifacts"] and row["tests"]
            if expected_status == "INSUFFICIENT_EVIDENCE":
                assert row["residual_gap"]
            else:
                assert row["residual_gap"] == ""
            for reference in row["artifacts"]:
                _check_reference(Path(__file__).resolve().parents[1], reference)
            for reference in row["tests"]:
                _check_reference(Path(__file__).resolve().parents[1], reference, test=True)
        else:
            assert row["disposition"] == "DEFERRED"
            assert record["applicability"] == "EXPLICITLY_DEFERRED"
            assert record["explicit_deferral_source"]
            assert row["residual_gap"]
            assert record["verification_limits"].strip()
    requirements = [row["requirement"] for row in rows]
    assert len(requirements) == len(set(requirements)), "Duplicate atomic obligation"
    assert all(marker not in row["source"] for row in rows for marker in (
        "R41-CI049", "R41-CI050", "R41-CI051",
    ))


def test_complete_atomic_mapping_is_attributable_and_consistent():
    root = Path(__file__).resolve().parents[1]
    _verify_complete_atomic_mapping(
        json.loads((root / "docs/m1-closure/traceability.json").read_text()),
        json.loads((root / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text()),
    )


@pytest.mark.parametrize("mutation", ["omitted", "duplicate", "status", "deferral", "source"])
def test_complete_atomic_mapping_rejects_invalid_coverage(mutation):
    import copy
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/m1-closure/traceability.json").read_text())
    checkpoint = json.loads((root / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text())
    data, checkpoint = copy.deepcopy(data), copy.deepcopy(checkpoint)
    if mutation == "omitted":
        checkpoint["records"].pop()
    elif mutation == "duplicate":
        data["rows"].append(copy.deepcopy(data["rows"][0]))
    elif mutation == "status":
        target = next(row for row in checkpoint["records"] if row["id"] == "IMP-001-M1-14")
        target["status"] = "VERIFIED"
    elif mutation == "deferral":
        target = next(row for row in checkpoint["records"] if row["applicability"] == "EXPLICITLY_DEFERRED")
        target["explicit_deferral_source"] = None
    else:
        data["rows"][0]["source"] = ""
    with pytest.raises(AssertionError):
        _verify_complete_atomic_mapping(data, checkpoint)

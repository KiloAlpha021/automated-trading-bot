import copy
import json
from pathlib import Path

import pytest

from automated_trading_bot.audit.evidence import export_manifest, manifest_skeleton, validate_manifest

REVISION = "d72f45339a78125621ca48b4f15a9811fde81920"
SOURCE = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"


def record():
    return {
        "requirement_id": "IMP-033-M1.8-01", "source": "Implementation Specification v1.0 sections 3.1, 11",
        "requirement": "Export a structurally validated milestone manifest.",
        "component": "audit.evidence", "schema_impact": "Milestone manifest v1; no runtime schema",
        "tests": {"positive": ["test_export_round_trip"], "negative": ["test_missing_record_field"],
                  "failure": ["test_stale_binding_rejected"]},
        "control": "Reject invalid manifests before export", "evidence": ["tests/test_evidence.py"],
        "status": "Specified", "dependencies": [], "gate": "M1.8 non-promotional completion",
        "provenance_status": "INFERRED",
    }


def manifest():
    data = manifest_skeleton("M1.8", REVISION, SOURCE)
    data["records"] = [record()]
    return data


def validate(data, **kwargs):
    return validate_manifest(data, expected_revision=REVISION, expected_source_sha256=SOURCE, **kwargs)


def test_skeleton_is_valid_without_claiming_completion():
    data = manifest_skeleton("M1.8", REVISION, SOURCE)
    validate(data)
    assert data["authorization"] == "NONE"
    assert data["records"] == []


def test_export_round_trip():
    data = manifest()
    before = copy.deepcopy(data)
    output = export_manifest(data, expected_revision=REVISION, expected_source_sha256=SOURCE)
    assert json.loads(output) == data
    validate(json.loads(output), required_ids=("IMP-033-M1.8-01",))
    assert data == before
    assert output == export_manifest(dict(reversed(list(data.items()))), expected_revision=REVISION, expected_source_sha256=SOURCE)


def test_export_record_order_is_deterministic():
    data = manifest()
    other = dict(record(), requirement_id="IMP-033-M1.8-02")
    data["records"].append(other)
    first = export_manifest(data, expected_revision=REVISION, expected_source_sha256=SOURCE)
    data["records"].reverse()
    assert first == export_manifest(data, expected_revision=REVISION, expected_source_sha256=SOURCE)


@pytest.mark.parametrize("field", ["schema_version", "milestone_id", "baseline_revision", "source_sha256", "authorization", "records"])
def test_missing_manifest_field(field):
    data = manifest()
    del data[field]
    with pytest.raises(ValueError):
        validate(data)


@pytest.mark.parametrize("field", ["requirement_id", "source", "requirement", "component", "schema_impact", "tests", "control", "evidence", "status", "dependencies", "gate", "provenance_status"])
def test_missing_record_field(field):
    data = manifest()
    del data["records"][0][field]
    with pytest.raises(ValueError):
        validate(data)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("schema_version", 2), ("authorization", "APPROVED"), ("records", {}), ("milestone_id", ""), ("source_sha256", "unknown"), ("baseline_revision", "HEAD")])
def test_invalid_manifest_values(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises((TypeError, ValueError)):
        validate(data)


@pytest.mark.parametrize("field,value", [("status", "Unknown"), ("provenance_status", "GUESSED"), ("requirement", " "), ("component", None), ("evidence", []), ("dependencies", "IMP-001"), ("tests", {}), ("source", []), ("requirement_id", "")])
def test_invalid_record_values(field, value):
    data = manifest()
    data["records"][0][field] = value
    with pytest.raises((TypeError, ValueError)):
        validate(data)


@pytest.mark.parametrize("field", ["positive", "negative", "failure"])
def test_missing_test_category_rejected(field):
    data = manifest()
    data["records"][0]["tests"][field] = []
    with pytest.raises(ValueError):
        validate(data)


@pytest.mark.parametrize("field,value", [("baseline_revision", "a" * 40), ("source_sha256", "b" * 64)])
def test_stale_binding_rejected(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(ValueError, match="binding mismatch"):
        validate(data)


def test_duplicate_requirement_rejected():
    data = manifest()
    data["records"].append(record())
    with pytest.raises(ValueError, match="duplicate requirement"):
        validate(data)


def test_required_coverage_cannot_be_assumed_from_skeleton():
    data = manifest_skeleton("M1.8", REVISION, SOURCE)
    with pytest.raises(ValueError, match="missing required"):
        validate(data, required_ids=("IMP-033-M1.8-01",))


def test_required_ids_are_strict():
    with pytest.raises(TypeError):
        validate(manifest(), required_ids="IMP-033")


@pytest.mark.parametrize("location", ["manifest", "record", "tests"])
def test_unknown_fields_rejected(location):
    data = manifest()
    target = data if location == "manifest" else data["records"][0]
    if location == "tests":
        target = target["tests"]
    target["unexpected"] = True
    with pytest.raises(ValueError):
        validate(data)


def test_invalid_data_cannot_be_exported():
    data = manifest()
    data["authorization"] = "LIVE"
    with pytest.raises(ValueError):
        export_manifest(data, expected_revision=REVISION, expected_source_sha256=SOURCE)


def test_skeleton_instances_do_not_share_state():
    first = manifest_skeleton("M1.8", REVISION, SOURCE)
    second = manifest_skeleton("M1.8", REVISION, SOURCE)
    first["records"].append(record())
    assert second["records"] == []


def test_checked_in_skeleton_is_machine_validated():
    path = Path(__file__).resolve().parents[1] / "docs" / "M1.8-manifest-skeleton.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    validate(data)
    assert data == manifest_skeleton("M1.8", REVISION, SOURCE)

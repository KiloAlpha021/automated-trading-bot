"""Deterministic M1.8 traceability export, not an evidence or promotion authority.

Validation checks declared structure and caller-supplied source/revision bindings.
It does not execute tests, verify evidence contents, resolve dependencies, or grant
lifecycle authorization. Empty skeletons make no coverage or completion claim.
"""

import json
import re
from typing import Any


def _text(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    if not value.strip():
        raise ValueError("expected a nonblank string")


def _keys(value: object, expected: set[str]) -> None:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    if set(value) != expected:
        raise ValueError("missing or unknown fields")


def _strings(value: object, *, nonempty: bool = True) -> None:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    if nonempty and not value:
        raise ValueError("expected a nonempty list")
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise ValueError("duplicate list entries")


def _hex(value: object, length: int) -> None:
    _text(value)
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is None:
        raise ValueError("invalid hexadecimal identity")


def manifest_skeleton(
    milestone_id: str, baseline_revision: str, source_sha256: str,
) -> dict[str, Any]:
    """Create fresh, structurally valid planning data with no completion claim."""
    data = {
        "schema_version": 1,
        "milestone_id": milestone_id,
        "baseline_revision": baseline_revision,
        "source_sha256": source_sha256,
        "authorization": "NONE",
        "records": [],
    }
    validate_manifest(
        data, expected_revision=baseline_revision,
        expected_source_sha256=source_sha256,
    )
    return data


def validate_manifest(
    data: dict[str, Any], *, expected_revision: str,
    expected_source_sha256: str, required_ids: tuple[str, ...] = (),
) -> None:
    """Reject invalid declarations; required_ids is explicit coverage scope.

    The caller must supply trusted identities, not simply copy them from untrusted
    input. Revision binds the declared baseline, not uncommitted code or evidence.
    Status/provenance values are declarations, never independently certified here.
    External dependency references remain recorded without claiming resolution.
    """
    _hex(expected_revision, 40)
    _hex(expected_source_sha256, 64)
    if not isinstance(required_ids, tuple):
        raise TypeError("required_ids must be a tuple")
    _strings(list(required_ids), nonempty=False)
    _keys(data, {"schema_version", "milestone_id", "baseline_revision",
                 "source_sha256", "authorization", "records"})
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported schema_version")
    _text(data["milestone_id"])
    _hex(data["baseline_revision"], 40)
    _hex(data["source_sha256"], 64)
    if data["baseline_revision"] != expected_revision or data["source_sha256"] != expected_source_sha256:
        raise ValueError("source or revision binding mismatch")
    if data["authorization"] != "NONE":
        raise ValueError("manifest cannot grant authorization")
    if not isinstance(data["records"], list):
        raise TypeError("records must be a list")
    seen: set[str] = set()
    for record in data["records"]:
        _keys(record, {"requirement_id", "source", "requirement", "component",
                       "schema_impact", "tests", "control", "evidence", "status",
                       "dependencies", "gate", "provenance_status"})
        for name in ("requirement_id", "source", "requirement", "component",
                     "schema_impact", "control", "status", "gate", "provenance_status"):
            _text(record[name])
        if record["requirement_id"] in seen:
            raise ValueError("duplicate requirement identity")
        seen.add(record["requirement_id"])
        if record["status"] not in ("Proposed", "Specified", "Implemented", "Verified", "Accepted", "Blocked"):
            raise ValueError("unknown requirement status")
        if record["provenance_status"] not in ("CONFIRMED", "RECONSTRUCTED", "INFERRED", "UNRESOLVED"):
            raise ValueError("unknown provenance status")
        _keys(record["tests"], {"positive", "negative", "failure"})
        for references in record["tests"].values():
            _strings(references)
        _strings(record["evidence"])
        _strings(record["dependencies"], nonempty=False)
    if set(required_ids) - seen:
        raise ValueError("missing required traceability records")


def export_manifest(
    data: dict[str, Any], *, expected_revision: str,
    expected_source_sha256: str, required_ids: tuple[str, ...] = (),
) -> str:
    """Export validated traceability as stable JSON without I/O or input mutation."""
    validate_manifest(
        data, expected_revision=expected_revision,
        expected_source_sha256=expected_source_sha256, required_ids=required_ids,
    )
    result = dict(data, records=sorted(data["records"], key=lambda item: item["requirement_id"]))
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"

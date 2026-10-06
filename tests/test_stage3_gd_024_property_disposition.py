from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs" / "stage3" / "s3-gd-024-property-disposition.json"
PHYSICAL = {"P02", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "P12", "P15", "P16"}


def load() -> tuple[dict[str, object], bytes]:
    content = PATH.read_bytes()
    return json.loads(content.decode("utf-8")), content


def test_disposition_is_exact_complete_and_deterministically_serialized() -> None:
    record, content = load()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in content
    assert content.endswith(b"\n") and not content.endswith(b"\n\n")
    assert record["schema_version"] == 1
    properties = record["properties"]
    assert isinstance(properties, list)
    assert [item["id"] for item in properties] == [f"P{number:02d}" for number in range(1, 17)]
    assert all(item["applicability"] == "APPLICABLE" for item in properties)
    assert all(item["result"] == "PASS" for item in properties)
    assert all(item["currentness"] == "CURRENT" for item in properties)
    assert all(item["semantic_status"] == "CURRENT" for item in properties)
    assert all(item["physical_status"] == "CURRENT" for item in properties)
    assert all(item["evidence"] and item["invalidation_conditions"] for item in properties)


def test_physical_properties_have_physical_or_recovery_evidence() -> None:
    record, _ = load()
    properties = {item["id"]: item for item in record["properties"]}
    assert PHYSICAL.issubset(properties)
    assert all(
        properties[item]["evidence_class"]
        in {"PHYSICAL_STORAGE", "PHYSICAL_PUBLICATION", "CRASH_RECOVERY"}
        for item in PHYSICAL
    )


def test_ctl_009_and_ctl_010_are_effective_without_authority_expansion() -> None:
    record, _ = load()
    controls = record["controls"]
    assert set(controls) == {"S3-CTL-009", "S3-CTL-010"}
    assert all(item["control_effective"] is True for item in controls.values())
    assert all(item["forbidden_outcome_observed"] is False for item in controls.values())
    denied = set(record["authority_not_granted"])
    assert {"DATASET_PROMOTION_AUTHORITY", "STAGE4_IMPLEMENTATION", "TRADING_OR_FINANCIAL_AUTHORITY", "AI_TRADING_AUTHORITY"}.issubset(denied)

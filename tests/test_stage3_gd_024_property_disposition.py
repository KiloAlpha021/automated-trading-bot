from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs" / "stage3" / "s3-gd-024-property-disposition.json"
PHYSICAL = {"P02", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "P12", "P15", "P16"}
TRACEABILITY = {
    "P01": ("DETERMINISTIC_MATERIALIZATION", ("S3-REQ-014", "S3-REQ-033"), ("S3-GD-024",)),
    "P02": ("HISTORICAL_RECONSTRUCTION_WITHOUT_REWRITE", ("S3-REQ-028",), ("S3-GD-024",)),
    "P03": ("DATASET_CONTENT_IDENTITY", ("S3-REQ-031",), ("S3-GD-024",)),
    "P04": ("TRANSFORMATION_LINEAGE", ("S3-REQ-032",), ("S3-GD-024",)),
    "P05": ("REPRODUCIBLE_MATERIALIZATION", ("S3-REQ-033",), ("S3-GD-024",)),
    "P06": ("IMMUTABLE_ANALYTICAL_PERSISTENCE", ("S3-REQ-028", "S3-REQ-031"), ("S3-CTL-009",)),
    "P07": ("EXACT_VERSION_PERSISTENCE_RETRIEVAL", ("S3-REQ-028", "S3-REQ-033"), ("S3-GD-024",)),
    "P08": ("PERSISTENCE_INTEGRITY", ("S3-REQ-028", "S3-REQ-031"), ("S3-CTL-009",)),
    "P09": ("ATOMIC_PROMOTED_PUBLICATION", ("S3-REQ-030", "S3-REQ-031"), ("S3-CTL-010",)),
    "P10": ("PARTIAL_MIXED_PUBLICATION_PREVENTION", ("S3-REQ-030", "S3-REQ-031"), ("S3-CTL-010",)),
    "P11": ("INTERRUPTED_MATERIALIZATION_HANDLING", ("S3-REQ-028", "S3-REQ-033"), ("S3-GD-024",)),
    "P12": ("RESTART_RECOVERY", ("S3-REQ-028", "S3-REQ-033"), ("S3-GD-024",)),
    "P13": ("CORRECTION_PROPAGATION", ("S3-REQ-026", "S3-REQ-035"), ("S3-CTL-007", "S3-CTL-008", "S3-GD-024")),
    "P14": ("INVALIDATION_PROPAGATION", ("S3-REQ-035",), ("S3-CTL-008", "S3-GD-024")),
    "P15": ("RETAINED_SOURCE_EVIDENCE", ("S3-REQ-030",), ("S3-GD-024",)),
    "P16": ("POST_RECOVERY_REPRODUCIBILITY", ("S3-REQ-028", "S3-REQ-033"), ("S3-GD-024",)),
}


def load() -> tuple[dict[str, object], bytes]:
    content = PATH.read_bytes()
    return json.loads(content.decode("utf-8")), content


def assert_traceability(record: dict[str, object]) -> None:
    properties = record["properties"]
    assert isinstance(properties, list)
    actual = {
        item["id"]: (
            item["property"],
            tuple(item["requirements"]),
            tuple(item["controls"]),
        )
        for item in properties
    }
    assert actual == TRACEABILITY


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


def test_property_traceability_matches_protected_authority_exactly() -> None:
    record, _ = load()
    assert_traceability(record)


def test_property_traceability_rejects_substitution_swap_omission_and_expansion() -> None:
    record, _ = load()
    variants: list[dict[str, object]] = []

    wrong_requirement = deepcopy(record)
    wrong_requirement["properties"][1]["requirements"] = ["S3-REQ-033"]
    variants.append(wrong_requirement)

    wrong_control = deepcopy(record)
    wrong_control["properties"][8]["controls"] = ["S3-GD-024"]
    variants.append(wrong_control)

    swapped = deepcopy(record)
    swapped["properties"][1]["requirements"], swapped["properties"][2]["requirements"] = (
        swapped["properties"][2]["requirements"],
        swapped["properties"][1]["requirements"],
    )
    variants.append(swapped)

    missing_mapping = deepcopy(record)
    del missing_mapping["properties"][3]["requirements"]
    variants.append(missing_mapping)

    missing_control = deepcopy(record)
    missing_control["properties"][12]["controls"] = ["S3-CTL-007", "S3-CTL-008"]
    variants.append(missing_control)

    unauthorized_extra = deepcopy(record)
    unauthorized_extra["properties"][14]["requirements"].append("S3-REQ-035")
    variants.append(unauthorized_extra)

    for variant in variants:
        with pytest.raises((AssertionError, KeyError)):
            assert_traceability(variant)


def test_ctl_009_and_ctl_010_are_effective_without_authority_expansion() -> None:
    record, _ = load()
    controls = record["controls"]
    assert set(controls) == {"S3-CTL-009", "S3-CTL-010"}
    assert all(item["control_effective"] is True for item in controls.values())
    assert all(item["forbidden_outcome_observed"] is False for item in controls.values())
    denied = set(record["authority_not_granted"])
    assert {"DATASET_PROMOTION_AUTHORITY", "STAGE4_IMPLEMENTATION", "TRADING_OR_FINANCIAL_AUTHORITY", "AI_TRADING_AUTHORITY"}.issubset(denied)

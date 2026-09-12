"""Adversarial validation for Phase 39.67 forward governance."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "docs/m1-closure/stage0b-phase39-67-coverage.json"
SPEC = importlib.util.spec_from_file_location(
    "stage0b_validate", ROOT / "scripts/stage0b_validate.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def model() -> dict[str, object]:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def test_stage0b_model_is_complete_and_zero_orphan() -> None:
    MODULE.validate_model(model(), ROOT)


def mutations(source: dict[str, object]):
    def changed() -> dict[str, object]:
        return copy.deepcopy(source)

    item = changed()
    item["legacy_provenance"].pop()
    yield item
    item = changed()
    item["legacy_provenance"][0]["wording"] = "fabricated"
    yield item
    item = changed()
    item["legacy_provenance"][0]["semantic_equivalence_claimed"] = True
    yield item
    item = changed()
    item["legacy_provenance"][0]["blanket_r41_supersession_claimed"] = True
    yield item
    item = changed()
    item["forward_governance_requirements"].pop()
    yield item
    item = changed()
    item["atoms"][0]["applicable_stage"] = ""
    yield item
    item = changed()
    item["atoms"][0]["implementation"] = []
    yield item
    item = changed()
    item["atoms"][0]["positive_acceptance"] = []
    yield item
    item = changed()
    item["atoms"][0]["negative_acceptance"] = []
    yield item
    item = changed()
    item["atoms"][0]["owner"] = ""
    yield item
    item = changed()
    item["atoms"][0]["lifecycle_gate"] = ""
    yield item
    future_index = next(
        i for i, atom in enumerate(item["atoms"]) if atom["applicable_stage"] != "M1"
    )
    item = changed()
    item["atoms"][future_index]["implementation_status"] = "VERIFIED_EXISTING"
    yield item
    item = changed()
    item["atoms"][future_index]["deferral_authority"] = ""
    yield item
    item = changed()
    item["atoms"][future_index]["negative_acceptance_plan"] = []
    yield item
    item = changed()
    item["zero_orphan_definition"]["current_mandatory_requirements"].pop()
    yield item
    item = changed()
    item["zero_orphan_definition"]["orphan_current_requirement_count"] = 1
    yield item


@pytest.mark.parametrize("weakened", list(mutations(model())))
def test_stage0b_validator_rejects_weakened_models(weakened: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MODULE.validate_model(weakened, ROOT)

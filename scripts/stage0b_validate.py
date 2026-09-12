"""Validate the controlled Phase 39.67 Stage 0B coverage model."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEGACY_IDS = {f"R39-I{number}" for number in range(353, 389)}
P39R_IDS = {f"P39R-{number:03d}" for number in range(1, 9)}
LEGACY_DISPOSITION = {
    "LEGACY_SEMANTICS_UNRECOVERABLE",
    "RETAINED_HISTORICAL_PROVENANCE",
    "RANGE_REMAINS_BINDING",
    "FORWARD_GOVERNANCE_REPLACED_BY_APPROVED_PHASE_39_67_REQUIREMENT_SET",
}


def load_model(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_model(model: dict[str, Any], root: Path) -> None:
    if model.get("classification") != "PASS_WITH_CONTROLLED_LEGACY_PROVENANCE":
        raise ValueError("invalid Stage 0B classification")
    if model.get("historical_disclosure") != "ORIGINAL R39 INDIVIDUAL SEMANTICS NOT RECOVERED":
        raise ValueError("unrecovered historical semantics must remain disclosed")

    legacy = model.get("legacy_provenance", [])
    if {item.get("historical_id") for item in legacy} != LEGACY_IDS or len(legacy) != len(LEGACY_IDS):
        raise ValueError("all 36 legacy R39 identifiers must appear exactly once")
    for item in legacy:
        if item.get("wording_status") != "UNRECOVERED" or "wording" in item:
            raise ValueError("legacy wording must remain unrecovered and absent")
        if set(item.get("disposition", [])) != LEGACY_DISPOSITION:
            raise ValueError("legacy disposition is incomplete")
        if item.get("semantic_equivalence_claimed") is not False:
            raise ValueError("semantic equivalence must not be claimed")
        if item.get("blanket_r41_supersession_claimed") is not False:
            raise ValueError("blanket R41 supersession must not be claimed")
        if set(item.get("forward_governance_set", [])) != P39R_IDS:
            raise ValueError("legacy record must link to the complete forward-governance set")

    requirements = model.get("forward_governance_requirements", [])
    if {item.get("requirement_id") for item in requirements} != P39R_IDS or len(requirements) != len(P39R_IDS):
        raise ValueError("all eight P39R requirements must appear exactly once")

    atoms = model.get("atoms", [])
    if not atoms or len({item.get("atom_id") for item in atoms}) != len(atoms):
        raise ValueError("replacement atoms must be present and uniquely identified")
    parents = {item.get("parent_id") for item in atoms}
    if parents != P39R_IDS:
        raise ValueError("every P39R requirement must have atoms")
    for atom in atoms:
        for field in ("atom_id", "parent_id", "requirement", "applicable_stage", "owner", "implementation_status", "control_evidence", "lifecycle_gate", "provenance"):
            if not atom.get(field):
                raise ValueError(f"{atom.get('atom_id')}: missing {field}")
        if atom["applicable_stage"] == "M1":
            if atom["implementation_status"] != "VERIFIED_EXISTING":
                raise ValueError("every M1 atom must be verified")
            for field in ("implementation", "positive_acceptance", "negative_acceptance"):
                if not atom.get(field):
                    raise ValueError(f"{atom['atom_id']}: missing M1 {field}")
            for reference in atom["implementation"] + atom["positive_acceptance"] + atom["negative_acceptance"]:
                file_name = reference.split("::", 1)[0]
                if not (root / file_name).is_file():
                    raise ValueError(f"{atom['atom_id']}: missing evidence file {file_name}")
        else:
            if atom["implementation_status"] != "NOT_YET_IMPLEMENTED":
                raise ValueError("future atoms must not be marked implemented")
            if atom.get("disposition") != "CONTROLLED_FUTURE_OBLIGATION" or not atom.get("deferral_authority"):
                raise ValueError("future atom lacks controlled assignment authority")
            if not atom.get("positive_acceptance_plan") or not atom.get("negative_acceptance_plan"):
                raise ValueError("future atom lacks acceptance plans")

    zero = model.get("zero_orphan_definition", {})
    if set(zero.get("current_mandatory_requirements", [])) != P39R_IDS:
        raise ValueError("zero-orphan universe is incomplete")
    if zero.get("orphan_current_requirement_count") != 0:
        raise ValueError("zero-orphan cannot pass with unresolved current requirements")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    validate_model(load_model(root / "docs/m1-closure/stage0b-phase39-67-coverage.json"), root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

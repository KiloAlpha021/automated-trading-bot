"""Validation for the bounded ATIS Master Recovery Packet."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "docs/programme/master-recovery-packet.json"
SCHEMA_PATH = ROOT / "docs/programme/master-recovery-packet.schema.json"
EXPECTED_PACKET_SHA256 = (
    "c57503f586c54122cf7a20e76632a9fb7197c11320c77bdda7549234c683a6e6"
)
EXPECTED_BASIS = {
    "commit": "119ab879fd96c64a4f04441da03945b5a22ed03d",
    "tree": "48e59fcc69b91c60d102d8236c0dbfeed64feaf5",
    "ordered_parents": [
        "622790404a9eec25dae3f41fa19f15a5e5319342",
        "e379c329499a35e22911c596e67968fcf875916d",
    ],
    "verification_decision": "ATIS_PR23_FINAL_POST_MERGE_VERIFICATION_PASS",
    "meaning": "RECOVERY_BASIS_NOT_PACKET_PUBLICATION_IDENTITY",
}
EXPECTED_REQUIRED = {
    ".python-version": "3b2cfc0e6904f4f3bfefaadc28451ba1f2818332",
    "docs/baseline/sources/Automated_Trading_Bot_Implementation_Specification_v1.0_Baseline_and_Build_Decomposition.docx": "8e116c3f3a75b0a038d947c8f66c1c57f1a4ff2e",
    "docs/baseline/specification-provenance.json": "30ea05b9c395fc1051a7c2d3a25ec7a01fb08179",
    "docs/m1-closure/closure-manifest.json": "9c653dc2183700c86191e8f14bfd4db020f38a5b",
    "docs/programme/programme-control.json": "c05c702f4bacc6c8edf6434ee4669d8de5d9fc85",
    "docs/programme/programme-control.schema.json": "923075fe52ec7f1b531a27b07a94ba86d200fee9",
    "docs/stage2/gate-s02-01.json": "850ce45a88d30b9348686493355fb681a464759f",
    "docs/stage2/stage2-freeze-record.json": "5a4425eeb79c87072e8c498981c55565c386c677",
    "docs/stage3/stage3-specification.json": "26c2089cf5ec9cde26757397cae2a52ea48ad475",
    "docs/stage3/stage3-specification.schema.json": "4505fd388115277eb4390bc2e9ada441a040d02d",
    "pyproject.toml": "f7cf0df47ffc4e1309803b48dc26114851dfb0ee",
    "requirements-dev.lock": "d64b631ccb01ca95a917a144c065a6f25de149b5",
    "scripts/bootstrap.ps1": "78c82833f387ce94f786f58f93947917076fd61a",
    "tests/test_programme_control.py": "99119ca06981fa1098dabb54faf14b0394c2c03b",
    "tests/test_stage3_specification.py": "daf9418dc5c212e7850a8121ab2017d5e55b556a",
}
EXPECTED_SUPPLEMENTARY = {
    "docs/baseline/sources/Automated_Trading_Bot_Implementation_Specification_v1.0.txt": "f0e4f657069facdda9741411a9566c9fe6b36fa9",
    "docs/m1-closure/stage0b-phase39-67-coverage.json": "8652bffe48add6cb6b8e63d64e9b2647b48e1739",
    "docs/m1-closure/traceability.json": "4261e1ab17f1d839f07f1a355340f54f0d8beb8c",
    "docs/stage2/s27-evidence.json": "ae1dbcca0c19b13a634ed6c503b975a40a9b4bc8",
}
EXPECTED_PRECEDENCE = [
    "RECOVERY_BASIS_AND_INDEX",
    "CURRENT_PROGRAMME_CONTROL",
    "CURRENT_MILESTONE_CLOSURE_AND_PROTECTION",
    "PROTECTED_STAGE3_SPECIFICATION",
    "GOVERNING_BASELINE_SPECIFICATION",
    "SUPPLEMENTARY_TRACEABILITY_AND_EVIDENCE",
    "HISTORICAL_OR_SUPERSEDED_EVIDENCE",
]
EXPECTED_AUTHORITY = {
    "STAGE3_IMPLEMENTATION",
    "STAGE4_IMPLEMENTATION",
    "PROVIDER_SELECTION",
    "SHADOW_TRADING",
    "PAPER_TRADING",
    "CANARY",
    "LIVE_TRADING",
    "BROKER_EXECUTION",
    "OMS_EXECUTION",
    "RISK_APPROVAL",
    "ACCOUNTING_AUTHORITY",
    "LEDGER_AUTHORITY",
    "CAPITAL_ALLOCATION",
    "FINANCIAL_EFFECTS",
    "AI_TRADING_AUTHORITY",
    "SUCCESSOR_PROGRAMME_OPERATION",
}
EXPECTED_GAPS = [
    ("GAP-REVIEW-5", "UNRESOLVED", "CONFIRMED", True),
    ("GAP-FROZEN-VERBATIM", "UNRESOLVED", "CONFIRMED", True),
    ("GAP-RESEARCH-DEFINITIONS", "UNRESOLVED", "RECONSTRUCTED", False),
    ("GAP-RESEARCH-ORDER", "UNRESOLVED", "RECONSTRUCTED", False),
]
EXPECTED_EXCLUSIONS = {
    "HISTORICAL_M1_CLOSURE_MANIFESTS",
    "STAGE2_GATE_NOT_YET_FROZEN_FIELDS",
    "STAGE2_FREEZE_CANDIDATE_WORDING",
    "STAGE3_EMBEDDED_HISTORICAL_ANSWERS",
    "README_CANONICAL_DOCUMENT_ABSENCE_CLAIM",
    "PC_HOUSEKEEPING_001_FEATURE_BRANCH",
    "PROGRAMME_CONTROL_SOURCE_BASELINE",
    "STAGE3_INTERNAL_BASELINE",
    "BASELINE_TXT_EXTRACTION",
    "SERVER_REPORTED_ARTIFACT_DIGEST",
}
EXPECTED_LIMITATIONS = {
    "HISTORICAL_UNIVERSE_NOT_CLAIMED_COMPLETE",
    "FOUR_GAPS_RECONCILED_WITH_EXPLICIT_LIMITATIONS",
    "HISTORICAL_RECORDS_PRESERVE_CREATION_TIME_STATE",
    "RESEARCH_DEFINITIONS_AND_ORDER_RECONSTRUCTED_NOT_VERBATIM",
    "PC_EVID_019_AND_025_ASSISTANT_TIMESTAMPS_NOT_RECOVERED",
    "OFFICE_ARTIFACTS_NOT_REHASHED_IN_LATEST_INVENTORY",
    "NO_OFFICE_ARTIFACT_DERIVATION_CHAIN_ASSERTED",
    "ORIGINAL_R39_INDIVIDUAL_SEMANTICS_NOT_RECOVERED",
    "STAGE3_DECISIONS_REMAIN_UNRESOLVED",
    "STAGE3_CORPUS_DIGEST_NOT_ESTABLISHED",
    "STAGE3_IMPLEMENTATION_AND_ASSURANCE_NOT_ESTABLISHED",
    "NO_PROVIDER_SELECTED",
    "NO_LATER_STAGE_TRADING_OR_FINANCIAL_AUTHORITY",
    "PR23_ARTIFACT_DIGEST_SERVER_REPORTED_ONLY",
    "POST_MERGE_RUN_IS_EXTERNAL_VERIFICATION_NOT_NEW_PROGRAMME_EVIDENCE",
    "NO_SUCCESSOR_PROGRAMME_OPERATION_AUTHORIZED",
    "PACKET_PUBLICATION_IDENTITY_REQUIRES_EXTERNAL_ESTABLISHMENT",
}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _load(path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique),
    )


def _packet() -> dict[str, Any]:
    return _load(PACKET_PATH)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _blob(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    return subprocess.run(
        ["git", "hash-object", f"--path={relative}", relative],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate(packet: dict[str, Any]) -> None:
    Draft202012Validator(_load(SCHEMA_PATH), format_checker=FormatChecker()).validate(
        packet
    )
    assert sha256(_canonical(packet)).hexdigest() == EXPECTED_PACKET_SHA256


def _must_reject(mutator: Any) -> None:
    value = deepcopy(_packet())
    mutator(value)
    with pytest.raises((ValidationError, AssertionError)):
        _validate(value)


def test_schema_and_packet_validate_and_duplicate_keys_reject() -> None:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    _validate(_packet())
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads('{"a":1,"a":2}', object_pairs_hook=_unique)


def test_fixed_recovery_contract() -> None:
    packet = _packet()
    assert packet["recovery_basis"] == EXPECTED_BASIS
    assert packet["reading_precedence"] == EXPECTED_PRECEDENCE
    assert set(packet["known_limitations"]) == EXPECTED_LIMITATIONS
    assert set(packet["authority_not_granted"]) == EXPECTED_AUTHORITY
    assert {
        x["subject"] for x in packet["stale_source_exclusions"]
    } == EXPECTED_EXCLUSIONS
    assert all(not x["current_authority"] for x in packet["stale_source_exclusions"])
    assert "publication_commit" not in _canonical(packet).decode()
    assert "publication_blob" not in _canonical(packet).decode()


def test_exact_source_inventories_exist_and_match_git_blobs() -> None:
    packet = _packet()
    required = {x["path"]: x["git_blob"] for x in packet["required_sources"]}
    supplementary = {x["path"]: x["git_blob"] for x in packet["supplementary_sources"]}
    assert required == EXPECTED_REQUIRED
    assert supplementary == EXPECTED_SUPPLEMENTARY
    for path, expected in required.items() | supplementary.items():
        assert _blob(ROOT / path) == expected


def test_current_state_and_gap_summary_are_exact() -> None:
    state = _packet()["current_state"]
    assert {
        k: state[k]
        for k in (
            "m1",
            "stage2",
            "stage3_specification",
            "stage3_implementation",
            "stage4_implementation",
            "provider",
            "trading_or_financial_authority",
            "successor_operation_authorized",
        )
    } == {
        "m1": "PROTECTED",
        "stage2": "CLOSED / AUDITED / FROZEN / PROTECTED",
        "stage3_specification": "PROTECTED",
        "stage3_implementation": "NOT_AUTHORIZED",
        "stage4_implementation": "NOT_AUTHORIZED",
        "provider": "NONE_SELECTED",
        "trading_or_financial_authority": "NONE",
        "successor_operation_authorized": False,
    }
    assert [
        (
            g["gap_id"],
            g["historical_state"],
            g["classification"],
            g["verbatim_source_recovered"],
        )
        for g in state["four_gap_summary"]
    ] == EXPECTED_GAPS


def test_authoritative_sources_validate_and_align() -> None:
    pc = _load(ROOT / "docs/programme/programme-control.json")
    pcs = _load(ROOT / "docs/programme/programme-control.schema.json")
    s3 = _load(ROOT / "docs/stage3/stage3-specification.json")
    s3s = _load(ROOT / "docs/stage3/stage3-specification.schema.json")
    Draft202012Validator.check_schema(pcs)
    Draft202012Validator(pcs).validate(pc)
    Draft202012Validator.check_schema(s3s)
    Draft202012Validator(s3s).validate(s3)
    assert (
        _load(ROOT / "docs/m1-closure/closure-manifest.json")["lineage"][
            "current_reclosure"
        ]["authority"]
        == "CURRENT_M1_COMPLETION"
    )
    gaps = {g["gap_id"]: g for g in pc["provenance_gap_register"]["records"]}
    for gap_id, historical, classification, verbatim in EXPECTED_GAPS:
        assert (
            gaps[gap_id]["historical_state"],
            gaps[gap_id]["classification"],
            gaps[gap_id]["verbatim_source_recovered"],
        ) == (historical, classification, verbatim)
    assert s3["authority"]["stage3_implementation_authorized"] is False
    assert s3["authority"]["provider_selected"] is None
    provenance = _load(ROOT / "docs/baseline/specification-provenance.json")
    docx = ROOT / provenance["canonical_artifact"]
    assert (
        sha256(docx.read_bytes()).hexdigest() == provenance["canonical_artifact_sha256"]
    )


@pytest.mark.parametrize(
    "attack",
    [
        "commit",
        "tree",
        "parents",
        "source",
        "missing",
        "extra",
        "precedence",
        "historical",
        "exclusion",
        "limitation",
        "gap",
        "stage3",
        "provider",
        "trading",
        "successor",
        "rehash",
        "self_identity",
    ],
)
def test_adversarial_mutations_reject(attack: str) -> None:
    def mutate(p: dict[str, Any]) -> None:
        if attack == "commit":
            p["recovery_basis"]["commit"] = "0" * 40
        elif attack == "tree":
            p["recovery_basis"]["tree"] = "0" * 40
        elif attack == "parents":
            p["recovery_basis"]["ordered_parents"].reverse()
        elif attack == "source":
            p["required_sources"][0]["git_blob"] = "0" * 40
        elif attack == "missing":
            p["required_sources"].pop()
        elif attack == "extra":
            p["required_sources"].append(deepcopy(p["required_sources"][0]))
        elif attack == "precedence":
            p["reading_precedence"].reverse()
        elif attack == "historical":
            p["historical_sources"][0]["current_authority"] = True
        elif attack == "exclusion":
            p["stale_source_exclusions"].pop()
        elif attack == "limitation":
            p["known_limitations"].pop()
        elif attack == "gap":
            p["current_state"]["four_gap_summary"][0]["classification"] = (
                "RECONSTRUCTED"
            )
        elif attack == "stage3":
            p["current_state"]["stage3_implementation"] = "AUTHORIZED"
        elif attack == "provider":
            p["current_state"]["provider"] = "BROKER"
        elif attack == "trading":
            p["current_state"]["trading_or_financial_authority"] = "GRANTED"
        elif attack == "successor":
            p["current_state"]["successor_operation_authorized"] = True
        elif attack == "rehash":
            p["stale_source_exclusions"][-1]["reason"] = (
                "ZIP bytes independently rehashed."
            )
        elif attack == "self_identity":
            p["publication_commit"] = "0" * 40

    _must_reject(mutate)

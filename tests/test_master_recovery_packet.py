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
    "d4c5b75f3a5732505addf73c9a48c086dbc98d083ea628f5399df660abb25b2d"
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
    "docs/programme/programme-control.json": "5f56c24a9746c0dca2d8f0037138b436b6afe2cc",
    "docs/programme/programme-control.schema.json": "573205a85273424dabbab5fe8720de3a94b7a583",
    "docs/stage2/gate-s02-01.json": "850ce45a88d30b9348686493355fb681a464759f",
    "docs/stage2/stage2-freeze-record.json": "5a4425eeb79c87072e8c498981c55565c386c677",
    "docs/stage3/stage3-specification.json": "8a0c95a08432ef65da4211c819ec436fc0cac4ee",
    "docs/stage3/stage3-specification.schema.json": "249e90997a668194373cd31e1bb35241ed0c6975",
    "pyproject.toml": "f7cf0df47ffc4e1309803b48dc26114851dfb0ee",
    "requirements-dev.lock": "cfb3245eef00c6f51e619aa15a5432dfc8893d89",
    "scripts/bootstrap.ps1": "78c82833f387ce94f786f58f93947917076fd61a",
    "tests/test_programme_control.py": "58409e1a7638d2151eb011391e3ebec0f11df48d",
    "tests/test_stage3_specification.py": "e6a35a19c8e54854f5ec07cf590c0481bb394311",
}
EXPECTED_SUPPLEMENTARY = {
    "docs/programme/successor-evidence.json": "af6af201dcac63d420a046fcbc2622348f56776e",
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
    "STAGE3_GENERAL_IMPLEMENTATION",
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
    "RECOVERY_PACKET_CREATION_TIME_STAGE3_STATE",
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
    "STAGE3_IMPLEMENTATION_PARTIAL_BOUNDED_AND_STAGE3_ASSURANCE_NOT_COMPLETE",
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
    assert (len(required), len(supplementary), len(packet["historical_sources"])) == (15, 5, 3)
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
        "stage3_implementation": "BOUNDED_COMPONENTS_PROTECTED / GENERAL_STAGE3_NOT_CLOSED",
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
    successor = _load(ROOT / "docs/programme/successor-evidence.json")
    successors = _load(ROOT / "docs/programme/successor-evidence.schema.json")
    Draft202012Validator.check_schema(pcs)
    Draft202012Validator(pcs).validate(pc)
    Draft202012Validator.check_schema(s3s)
    Draft202012Validator(s3s).validate(s3)
    Draft202012Validator.check_schema(successors)
    Draft202012Validator(successors).validate(successor)
    assert [record["record_id"] for record in successor["records"]] == [
        "SE-CAND-001",
        "SE-PUB-001",
        "SE-CAND-002",
        "SE-PUB-002",
        "SE-CAND-003",
        "SE-PUB-003",
        "SE-CAND-004",
        "SE-PUB-004",
        "SE-CAND-005",
        "SE-PUB-005",
        "SE-CAND-006",
        "SE-PUB-006",
        "SE-CAND-007",
        "SE-PUB-007",
        "SE-CAND-008",
        "SE-PUB-008",
    ]
    assert successor["records"][0]["candidate_state"] == (
        "VERIFIED_LOCAL_UNPUBLISHED"
    )
    assert successor["authority"]["grants_authority"] is False
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


def test_recovery_packet_resolves_current_c08_closure_state() -> None:
    packet = _packet()
    assert packet["current_state"]["details_source"] == "docs/programme/programme-control.json"
    programme = _load(ROOT / "docs/programme/programme-control.json")
    decisions = {row["record_id"]: row for row in programme["decision_register"]["records"]}
    assert decisions["PC-DEC-022"]["decision"] == "ATIS_STAGE3_C08_PIT_MATERIALIZATION_CLOSURE_RECORD_V1"
    assert decisions["PC-DEC-023"]["decision"] == "ATIS_C11_S1_MINIMUM_SEMANTIC_CONTRACT_V1"
    successor = _load(ROOT / "docs/programme/successor-evidence.json")
    by_id = {row["record_id"]: row for row in successor["records"]}
    assert by_id["SE-CAND-008"]["candidate_state"] == "VERIFIED_LOCAL_UNPUBLISHED"
    assert by_id["SE-PUB-008"]["post_publication_verification"] == "PASS"
    denied = set(decisions["PC-DEC-022"]["authority_not_granted"])
    assert {"C08_IMPLEMENTATION_RESUMPTION", "C09_REENTRY", "C10_REENTRY", "C11_IMPLEMENTATION", "SYNC_3_CONSUMABILITY", "STAGE4_IMPLEMENTATION", "AI_TRADING_AUTHORITY"} <= denied

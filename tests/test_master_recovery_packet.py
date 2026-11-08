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
DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-s4-07.json"
)
DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-s4-07.schema.json"
)
S4_12_DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-s4-12.json"
)
S4_12_DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-s4-12.schema.json"
)
MSGPACK_DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-msgpack-006.json"
)
MSGPACK_DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-msgpack-006.schema.json"
)
STAGE5_DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-stage5-f0-u1.json"
)
STAGE5_DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-stage5-f0-u1.schema.json"
)
STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-stage5-u3-060.json"
)
STAGE5_U3_DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-stage5-u3-060.schema.json"
)
OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-dependency-successor-operator-console-075.json"
)
OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_SCHEMA_PATH = (
    ROOT
    / "docs/programme/master-recovery-dependency-successor-operator-console-075.schema.json"
)
EXECUTION_POINTER_SUCCESSOR_PATH = (
    ROOT / "docs/programme/master-recovery-successor-execution-pointer-001.json"
)
EXECUTION_POINTER_SUCCESSOR_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-successor-execution-pointer-001.schema.json"
)
EXECUTION_POINTER_CLOSURE_PATH = (
    ROOT / "docs/programme/master-recovery-successor-execution-pointer-closure-002.json"
)
EXECUTION_POINTER_CLOSURE_SCHEMA_PATH = (
    ROOT / "docs/programme/master-recovery-successor-execution-pointer-closure-002.schema.json"
)
LIFECYCLE_VALIDATOR_PATH = "tests/test_programme_control.py"
EXPECTED_LIFECYCLE_VALIDATOR_TRANSITION = {
    "historical_blob": "563015bc65b4b6a3c876924e56f2f80190caa2a6",
    "successor_blob": "3196221ee39cf1c20f8be25e45d588869ad8c4c2",
    "publication_receipt_sha256": (
        "5ca5f9b361e5e4217a9ea6b330ed950528eb6da86992082b7f9b821073b9081e"
    ),
}
EXPECTED_PACKET_SHA256 = (
    "bd39fd6768ab3fb5608418343858137f759875537e4b9c1f5d956a5451d37e43"
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
    "docs/programme/programme-control.json": "2bb69105bbd2199ce8e114a1aed018216559bf34",
    "docs/programme/programme-control.schema.json": "573205a85273424dabbab5fe8720de3a94b7a583",
    "docs/stage2/gate-s02-01.json": "850ce45a88d30b9348686493355fb681a464759f",
    "docs/stage2/stage2-freeze-record.json": "5a4425eeb79c87072e8c498981c55565c386c677",
    "docs/stage3/stage3-specification.json": "15a51e0349c3e983e0da86d928a383dc25ed1cf4",
    "docs/stage3/stage3-specification.schema.json": "a68f3aba5bff62beabe11bb14328d37a99165380",
    "pyproject.toml": "f7cf0df47ffc4e1309803b48dc26114851dfb0ee",
    "requirements-dev.lock": "cfb3245eef00c6f51e619aa15a5432dfc8893d89",
    "scripts/bootstrap.ps1": "78c82833f387ce94f786f58f93947917076fd61a",
    "tests/test_programme_control.py": "4e42082f53252fee6132ddb67251b1409dceeb4e",
    "tests/test_stage3_specification.py": "7cb5fa5d266f9e06f18190e4d1a55b1014c36884",
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
        timeout=10,
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
    successor = _validate_successor(_load(SUCCESSOR_PATH))
    successor = _resolve_programme_control_successor(
        successor,
        [
            _load(EXECUTION_POINTER_SUCCESSOR_PATH),
            _load(EXECUTION_POINTER_CLOSURE_PATH),
        ],
    )
    successor.update(
        _resolve_dependency_successor(
            required,
            [
                _load(DEPENDENCY_SUCCESSOR_PATH),
                _load(S4_12_DEPENDENCY_SUCCESSOR_PATH),
                _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH),
                _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH),
                _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH),
                _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH),
            ],
        )
    )
    for path, expected in required.items() | supplementary.items():
        assert _blob(ROOT / path) == successor.get(path, expected)


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


SUCCESSOR_PATH = ROOT / "docs/programme/master-recovery-successor-kf04.json"


def _validate_successor(overlay: dict[str, Any]) -> dict[str, str]:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator(schema["$defs"]["kf04Successor"]).validate(overlay)
    packet = _packet()
    _validate(packet)
    assert _blob(PACKET_PATH) == overlay["historical_packet"]["git_blob"]
    assert sha256(_canonical(packet)).hexdigest() == (
        overlay["historical_packet"]["canonical_sha256"]
    )
    programme = _load(ROOT / "docs/programme/programme-control.json")
    amendment = programme["stage3_acceptance_amendment"]
    assert amendment["amendment_id"] == overlay["amendment_id"]
    assert amendment["protected_identity"] == overlay["protected_baseline"]
    assert amendment["scope"] == "STAGE3_ONLY"
    historical = {row["path"]: row["git_blob"] for row in packet["required_sources"]}
    result: dict[str, str] = {}
    for row in overlay["transitions"]:
        assert row["path"] not in result
        assert historical[row["path"]] == row["historical_blob"]
        retained = subprocess.run(
            ["git", "cat-file", "-t", row["historical_blob"]],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
        )
        assert retained.stdout.strip() == "blob"
        candidate = subprocess.run(
            ["git", "cat-file", "-t", row["candidate_blob"]],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
        )
        assert candidate.stdout.strip() == "blob"
        result[row["path"]] = row["candidate_blob"]
    return result


def _validate_execution_pointer_successor(
    record: dict[str, Any], predecessor: dict[str, str]
) -> dict[str, str]:
    closure = record["record_id"] == (
        "ATIS-MASTER-RECOVERY-SUCCESSOR-EXECUTION-POINTER-CLOSURE-002"
    )
    schema = _load(
        EXECUTION_POINTER_CLOSURE_SCHEMA_PATH
        if closure
        else EXECUTION_POINTER_SUCCESSOR_SCHEMA_PATH
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)

    for evidence in record["predecessor_evidence"].values():
        path = ROOT / evidence["path"]
        assert _blob(path) == evidence["git_blob"]
        canonical = subprocess.run(
            ["git", "cat-file", "blob", evidence["git_blob"]],
            cwd=ROOT, check=True, capture_output=True, timeout=10,
        ).stdout
        assert sha256(canonical).hexdigest() == evidence["sha256"]

    expected_predecessor = (
        {
            "commit": "e553a715f80f1bb5704861c04af47f3336e8fd11",
            "tree": "e0eb50506e168904ebb8f07f77a6fce685b4d85b",
        }
        if closure
        else {
            "commit": "0f813481407ae8c19b26f4b8055ee2be5a31933d",
            "tree": "b289c4b3848747e61611bbdf689e92a62ec29690",
        }
    )
    assert record["protected_predecessor"] == expected_predecessor
    assert record["protected_active"] is False
    assert record["grants_authority"] is False

    if closure:
        pointers = record["pointer_transitions"]
        assert [row["last_material_transition"]["sequence"] for row in pointers] == [2, 3, 4, 5]
        assert [row["latest_verified_gate"] for row in pointers] == [
            "BOUNDED_ENGINEERING",
            "REAL_ENVIRONMENT_QUALIFICATION",
            "INDEPENDENT_ASSURANCE",
            "PROTECTED_PUBLICATION",
        ]
        assert pointers[-1]["disposition"]["state"] == "CLOSED"
        assert pointers[-1]["last_material_transition"]["reason"] == "PROTECTED_PUBLICATION"

    result: dict[str, str] = {}
    for transition in record["transitions"]:
        path = transition["path"]
        assert path not in result
        assert predecessor[path] == transition["historical_blob"]
        _retained_blob(transition["historical_blob"])
        _retained_blob(transition["candidate_blob"])
        if closure:
            current_blob = _blob(ROOT / path)
            if path == LIFECYCLE_VALIDATOR_PATH:
                _validate_lifecycle_validator_transition(transition, current_blob)
                result[path] = current_blob
                continue
            else:
                assert current_blob == transition["candidate_blob"]
        result[path] = transition["candidate_blob"]
    assert result.keys() == predecessor.keys()
    return result


def _resolve_programme_control_successor(
    historical: dict[str, str], records: list[dict[str, Any]]
) -> dict[str, str]:
    if not records:
        raise AssertionError("programme-control successor is missing")
    current = dict(historical)
    remaining = list(records)
    while remaining:
        matches = [
            record
            for record in remaining
            if {
                transition["path"]: transition["historical_blob"]
                for transition in record.get("transitions", [])
            }
            == current
        ]
        if len(matches) != 1:
            raise AssertionError("programme-control successor must be uniquely applicable")
        selected = matches[0]
        current = _validate_execution_pointer_successor(selected, current)
        remaining.remove(selected)
    return current


def _validate_lifecycle_validator_transition(
    historical_transition: dict[str, Any], current_blob: str
) -> None:
    expected = EXPECTED_LIFECYCLE_VALIDATOR_TRANSITION
    assert historical_transition["path"] == LIFECYCLE_VALIDATOR_PATH
    assert historical_transition["candidate_blob"] == expected["historical_blob"]
    assert current_blob == expected["successor_blob"]
    source = (ROOT / LIFECYCLE_VALIDATOR_PATH).read_text(encoding="utf-8")
    assert expected["publication_receipt_sha256"] in source


def _dependency_successor_schema(record: dict[str, Any]) -> Path:
    schemas = {
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-S4-07-001": (
            DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-S4-12-001": (
            S4_12_DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-MSGPACK-006-001": (
            MSGPACK_DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-STAGE5-F0-U1-001": (
            STAGE5_DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-STAGE5-U3-060-001": (
            STAGE5_U3_DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
        "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-OPERATOR-CONSOLE-075-001": (
            OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_SCHEMA_PATH
        ),
    }
    try:
        return schemas[record["record_id"]]
    except (KeyError, TypeError) as error:
        raise AssertionError("unknown dependency successor record") from error


def _retained_blob(blob: str) -> None:
    retained = subprocess.run(
        ["git", "cat-file", "-t", blob],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    assert retained.stdout.strip() == "blob"


def _canonical_git_blob_bytes(path: Path, expected_blob: str) -> bytes:
    relative_path = path.relative_to(ROOT).as_posix()
    checkout_blob = subprocess.run(
        ["git", "hash-object", f"--path={relative_path}", str(path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    ).stdout.strip()
    assert checkout_blob == expected_blob

    canonical = subprocess.run(
        ["git", "cat-file", "blob", expected_blob],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return canonical.stdout


def _validate_dependency_successor(record: dict[str, Any]) -> dict[str, str]:
    schema = _load(_dependency_successor_schema(record))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)

    evidence_group = record.get("historical_evidence", record.get("predecessor_evidence"))
    assert isinstance(evidence_group, dict)
    for evidence in evidence_group.values():
        path = ROOT / evidence["path"]
        assert _blob(path) == evidence["git_blob"]
        canonical_bytes = _canonical_git_blob_bytes(path, evidence["git_blob"])
        assert sha256(canonical_bytes).hexdigest() == evidence["sha256"]

    transition = record["dependency_transition"]
    assert transition["path"] == "requirements-dev.lock"
    assert record["dependency_surface"][transition["path"]] == transition["candidate_blob"]
    assert record["trusted_profile"]["lock_blob"] == transition["candidate_blob"]
    assert record["grants_authority"] is False

    for expected in record["dependency_surface"].values():
        _retained_blob(expected)
    return {transition["path"]: transition["candidate_blob"]}


def _resolve_dependency_successor(
    historical: dict[str, str], records: list[dict[str, Any]]
) -> dict[str, str]:
    validated: list[tuple[dict[str, Any], dict[str, str]]] = []
    for record in records:
        validated.append((record, _validate_dependency_successor(record)))

    transition_path = "requirements-dev.lock"
    current = historical[transition_path]
    ordered: list[tuple[dict[str, Any], dict[str, str]]] = []
    remaining = list(validated)
    while remaining:
        matches = [
            item for item in remaining
            if item[0]["dependency_transition"]["historical_blob"] == current
        ]
        if len(matches) != 1:
            raise AssertionError("dependency successor must be uniquely applicable")
        selected = matches[0]
        ordered.append(selected)
        remaining.remove(selected)
        current = selected[0]["dependency_transition"]["candidate_blob"]

    if [record["record_id"] for record, _ in ordered] != [
        record["record_id"] for record, _ in validated
    ]:
        raise AssertionError("dependency successor chain must be ordered")
    terminal = ordered[-1][0]
    for path, expected in terminal["dependency_surface"].items():
        assert _blob(ROOT / path) == expected
    if _blob(ROOT / transition_path) != current:
        raise AssertionError("dependency successor must be uniquely applicable")
    return dict(terminal["dependency_surface"])


def test_s4_07_dependency_successor_is_non_authorising_and_exact() -> None:
    record = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    terminal = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    assert _validate_dependency_successor(record) == {
        "requirements-dev.lock": "30c531abc4b183d7e6114128b1f12be0d954b034"
    }
    resolved = _resolve_dependency_successor(
        EXPECTED_REQUIRED,
        [record, current, terminal, stage5, stage5_u3, operator_console],
    )
    assert resolved == {
        "pyproject.toml": "52b1c8301a49e9adac27cc3a5f1d16ab18165f1c",
        "requirements-dev.in": "14079f7d3b82dc1da3d0b410cc1e2964257afb05",
        "requirements-dev.lock": "a4d6fa8518a4104431d07729654c5d3eef3ba5dc",
        "tests/test_dependency_lock.py": "da29d8681f8ff82ca5d9f847f4ef45e52d3f0454",
    }
    assert record["approval_provenance"] == {
        "repository": "KiloAlpha021/automated-trading-bot",
        "issue": 100,
        "comment_id": 6314374361,
        "url": "https://github.com/KiloAlpha021/automated-trading-bot/issues/100#issuecomment-6314374361",
        "author": "KiloAlpha21",
        "created_at": "2026-10-21T11:04:19Z",
        "decision": "APPROVED_FOR_BOUNDED_LOCAL_DEPENDENCY_SUCCESSOR_VALIDATION",
        "body_sha256": "393e6270fbe8be3f04f951ed9049da7a004308c547a6579769e7a15a2b15429d",
    }


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "substituted_lock",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_approval",
        "forged_approval",
        "wrong_owner",
        "stale_approval",
        "protected_active",
        "authority",
        "historical_packet",
        "kf04",
        "historical_schema",
    ],
)
def test_s4_07_dependency_successor_rejects_invalid_evidence(attack: str) -> None:
    record = deepcopy(_load(DEPENDENCY_SUCCESSOR_PATH))
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    terminal = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    if attack == "wrong_predecessor":
        record["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        record["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "substituted_lock":
        record["dependency_surface"]["requirements-dev.lock"] = "0" * 40
    elif attack == "wrong_surface":
        record["dependency_surface"]["pyproject.toml"] = "0" * 40
    elif attack == "missing_profile":
        del record["trusted_profile"]
    elif attack == "profile_lock":
        record["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_approval":
        del record["approval_provenance"]
    elif attack == "forged_approval":
        record["approval_provenance"]["comment_id"] = 1
    elif attack == "wrong_owner":
        record["approval_provenance"]["author"] = "not-the-owner"
    elif attack == "stale_approval":
        record["approval_provenance"]["created_at"] = "2026-10-20T00:00:00Z"
    elif attack == "protected_active":
        record["protected_active"] = True
        record["trust_state"] = "PROTECTED_ACTIVE"
    elif attack == "authority":
        record["grants_authority"] = True
    elif attack == "historical_packet":
        record["historical_evidence"]["recovery_packet"]["git_blob"] = "0" * 40
    elif attack == "kf04":
        record["historical_evidence"]["kf04_successor"]["git_blob"] = "0" * 40
    else:
        record["historical_evidence"]["recovery_schema"]["git_blob"] = "0" * 40
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [record, current, terminal, stage5, stage5_u3, operator_console],
        )


def test_s4_07_dependency_successor_rejects_conflicting_records() -> None:
    record = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    terminal = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    with pytest.raises(AssertionError, match="uniquely applicable"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [
                record,
                current,
                deepcopy(current),
                terminal,
                stage5,
                stage5_u3,
                operator_console,
            ],
        )


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_authorization",
        "authority",
        "predecessor_record",
        "predecessor_schema",
    ],
)
def test_s4_12_dependency_successor_rejects_invalid_evidence(attack: str) -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = deepcopy(_load(S4_12_DEPENDENCY_SUCCESSOR_PATH))
    terminal = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    if attack == "wrong_predecessor":
        current["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        current["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "wrong_surface":
        current["dependency_surface"]["pyproject.toml"] = "0" * 40
    elif attack == "missing_profile":
        del current["trusted_profile"]
    elif attack == "profile_lock":
        current["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_authorization":
        del current["authorization_reference"]
    elif attack == "authority":
        current["grants_authority"] = True
    elif attack == "predecessor_record":
        current["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    else:
        current["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, terminal, stage5, stage5_u3, operator_console],
        )


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_authorization",
        "protected_active",
        "authority",
        "predecessor_record",
        "predecessor_schema",
    ],
)
def test_msgpack_dependency_successor_rejects_invalid_evidence(attack: str) -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    terminal = deepcopy(_load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH))
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    if attack == "wrong_predecessor":
        terminal["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        terminal["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "wrong_surface":
        terminal["dependency_surface"]["tests/test_dependency_lock.py"] = "0" * 40
    elif attack == "missing_profile":
        del terminal["trusted_profile"]
    elif attack == "profile_lock":
        terminal["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_authorization":
        del terminal["authorization_reference"]
    elif attack == "protected_active":
        terminal["protected_active"] = True
    elif attack == "authority":
        terminal["grants_authority"] = True
    elif attack == "predecessor_record":
        terminal["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    else:
        terminal["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, terminal, stage5, stage5_u3, operator_console],
        )


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_authorization",
        "protected_active",
        "authority",
        "predecessor_record",
        "predecessor_schema",
        "false_approval",
    ],
)
def test_stage5_dependency_successor_rejects_invalid_evidence(attack: str) -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    msgpack = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = deepcopy(_load(STAGE5_DEPENDENCY_SUCCESSOR_PATH))
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    if attack == "wrong_predecessor":
        stage5["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        stage5["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "wrong_surface":
        stage5["dependency_surface"]["tests/test_dependency_lock.py"] = "0" * 40
    elif attack == "missing_profile":
        del stage5["trusted_profile"]
    elif attack == "profile_lock":
        stage5["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_authorization":
        del stage5["authorization_reference"]
    elif attack == "protected_active":
        stage5["protected_active"] = True
    elif attack == "authority":
        stage5["grants_authority"] = True
    elif attack == "predecessor_record":
        stage5["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    elif attack == "predecessor_schema":
        stage5["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    else:
        stage5["approval_status"] = "PROTECTED_ACTIVE"
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, msgpack, stage5, stage5_u3, operator_console],
        )


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_authorization",
        "protected_active",
        "authority",
        "predecessor_record",
        "predecessor_schema",
        "false_approval",
    ],
)
def test_stage5_u3_dependency_successor_rejects_invalid_evidence(
    attack: str,
) -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    msgpack = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = deepcopy(_load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH))
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    if attack == "wrong_predecessor":
        stage5_u3["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        stage5_u3["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "wrong_surface":
        stage5_u3["dependency_surface"]["tests/test_dependency_lock.py"] = "0" * 40
    elif attack == "missing_profile":
        del stage5_u3["trusted_profile"]
    elif attack == "profile_lock":
        stage5_u3["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_authorization":
        del stage5_u3["authorization_reference"]
    elif attack == "protected_active":
        stage5_u3["protected_active"] = True
    elif attack == "authority":
        stage5_u3["grants_authority"] = True
    elif attack == "predecessor_record":
        stage5_u3["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    elif attack == "predecessor_schema":
        stage5_u3["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    else:
        stage5_u3["approval_status"] = "PROTECTED_ACTIVE"
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, msgpack, stage5, stage5_u3, operator_console],
        )


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_predecessor",
        "wrong_successor",
        "wrong_surface",
        "missing_profile",
        "profile_lock",
        "missing_authorization",
        "protected_active",
        "authority",
        "predecessor_record",
        "predecessor_schema",
        "false_approval",
    ],
)
def test_operator_console_dependency_successor_rejects_invalid_evidence(
    attack: str,
) -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    msgpack = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = deepcopy(_load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH))
    if attack == "wrong_predecessor":
        operator_console["dependency_transition"]["historical_blob"] = "0" * 40
    elif attack == "wrong_successor":
        operator_console["dependency_transition"]["candidate_blob"] = "0" * 40
    elif attack == "wrong_surface":
        operator_console["dependency_surface"]["tests/test_dependency_lock.py"] = (
            "0" * 40
        )
    elif attack == "missing_profile":
        del operator_console["trusted_profile"]
    elif attack == "profile_lock":
        operator_console["trusted_profile"]["lock_blob"] = "0" * 40
    elif attack == "missing_authorization":
        del operator_console["authorization_reference"]
    elif attack == "protected_active":
        operator_console["protected_active"] = True
    elif attack == "authority":
        operator_console["grants_authority"] = True
    elif attack == "predecessor_record":
        operator_console["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    elif attack == "predecessor_schema":
        operator_console["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    else:
        operator_console["approval_status"] = "PROTECTED_ACTIVE"
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, msgpack, stage5, stage5_u3, operator_console],
        )


def test_dependency_successor_rejects_unknown_record() -> None:
    unknown = deepcopy(_load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH))
    unknown["record_id"] = "ATIS-MASTER-RECOVERY-DEPENDENCY-SUCCESSOR-UNKNOWN"
    with pytest.raises(AssertionError, match="unknown dependency successor record"):
        _validate_dependency_successor(unknown)


def test_dependency_successor_chain_rejects_missing_or_reordered_records() -> None:
    historical = _load(DEPENDENCY_SUCCESSOR_PATH)
    current = _load(S4_12_DEPENDENCY_SUCCESSOR_PATH)
    terminal = _load(MSGPACK_DEPENDENCY_SUCCESSOR_PATH)
    stage5 = _load(STAGE5_DEPENDENCY_SUCCESSOR_PATH)
    stage5_u3 = _load(STAGE5_U3_DEPENDENCY_SUCCESSOR_PATH)
    operator_console = _load(OPERATOR_CONSOLE_DEPENDENCY_SUCCESSOR_PATH)
    with pytest.raises(AssertionError):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED, [historical, current, terminal, stage5]
        )
    with pytest.raises(AssertionError, match="uniquely applicable"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [current, terminal, stage5, stage5_u3, operator_console],
        )
    with pytest.raises(AssertionError, match="must be ordered"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, terminal, current, stage5, stage5_u3, operator_console],
        )
    with pytest.raises(AssertionError, match="uniquely applicable"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [
                historical,
                current,
                terminal,
                stage5,
                deepcopy(stage5_u3),
                stage5_u3,
                operator_console,
            ],
        )
    with pytest.raises(AssertionError):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [historical, current, terminal, stage5, stage5_u3],
        )
    with pytest.raises(AssertionError, match="must be ordered"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [
                historical,
                current,
                terminal,
                stage5,
                operator_console,
                stage5_u3,
            ],
        )
    with pytest.raises(AssertionError, match="uniquely applicable"):
        _resolve_dependency_successor(
            EXPECTED_REQUIRED,
            [
                historical,
                current,
                terminal,
                stage5,
                stage5_u3,
                deepcopy(operator_console),
                operator_console,
            ],
        )


def test_kf04_successor_preserves_historical_schema_and_packet() -> None:
    historical_schema = json.loads(subprocess.run(
        ["git", "cat-file", "blob", "b534bf02a0dd1736ba2b824c33ec6f2fb1597b4f"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
        timeout=10,
    ).stdout)
    schema = deepcopy(_load(SCHEMA_PATH))
    del schema["$defs"]["kf04Successor"]
    assert schema == historical_schema
    Draft202012Validator(historical_schema).validate(_packet())
    _validate(_packet())
    kf04 = _validate_successor(_load(SUCCESSOR_PATH))
    assert len(kf04) == 3
    terminal = _resolve_programme_control_successor(
        kf04, [_load(EXECUTION_POINTER_SUCCESSOR_PATH)]
    )
    assert terminal == {
        "docs/programme/programme-control.json": "77e36465556e54c654be25ea36dafd7376d1485a",
        "docs/programme/programme-control.schema.json": "3250ee8ddfd695b4d2db2d9d633967dac4945e7a",
        "tests/test_programme_control.py": "88998f4ed850d5be2e7f1ad3081073fe9ba37b44",
    }


def test_execution_pointer_successor_is_append_only_non_authorising_and_exact() -> None:
    record = _load(EXECUTION_POINTER_SUCCESSOR_PATH)
    schema = _load(EXECUTION_POINTER_SUCCESSOR_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)
    kf04 = _validate_successor(_load(SUCCESSOR_PATH))
    assert _resolve_programme_control_successor(kf04, [record]) == {
        "docs/programme/programme-control.json": "77e36465556e54c654be25ea36dafd7376d1485a",
        "docs/programme/programme-control.schema.json": "3250ee8ddfd695b4d2db2d9d633967dac4945e7a",
        "tests/test_programme_control.py": "88998f4ed850d5be2e7f1ad3081073fe9ba37b44",
    }
    assert record["protected_active"] is False
    assert record["grants_authority"] is False


def test_execution_pointer_closure_successor_is_unique_closed_and_non_authorising() -> None:
    kf04 = _validate_successor(_load(SUCCESSOR_PATH))
    records = [
        _load(EXECUTION_POINTER_SUCCESSOR_PATH),
        _load(EXECUTION_POINTER_CLOSURE_PATH),
    ]
    assert _resolve_programme_control_successor(kf04, records) == {
        "docs/programme/programme-control.json": "71921c6dafbbed7be9e423e9df2ec9e11cf6c0ae",
        "docs/programme/programme-control.schema.json": "3250ee8ddfd695b4d2db2d9d633967dac4945e7a",
        "tests/test_programme_control.py": "3196221ee39cf1c20f8be25e45d588869ad8c4c2",
    }
    closure = records[-1]
    assert closure["protected_active"] is False
    assert closure["grants_authority"] is False
    assert closure["closure_evidence"]["sha256"] == (
        "6db1ec76c8f2789a8f88a68b1e4cf51579b323a3707919fbbb2922912ec50b61"
    )


def test_lifecycle_validator_successor_preserves_authenticated_history() -> None:
    closure = _load(EXECUTION_POINTER_CLOSURE_PATH)
    transition = next(
        row
        for row in closure["transitions"]
        if row["path"] == LIFECYCLE_VALIDATOR_PATH
    )
    _retained_blob(EXPECTED_LIFECYCLE_VALIDATOR_TRANSITION["historical_blob"])
    _validate_lifecycle_validator_transition(
        transition, EXPECTED_LIFECYCLE_VALIDATOR_TRANSITION["successor_blob"]
    )


def test_lifecycle_validator_successor_rejects_unauthorised_substitution() -> None:
    closure = _load(EXECUTION_POINTER_CLOSURE_PATH)
    transition = next(
        row
        for row in closure["transitions"]
        if row["path"] == LIFECYCLE_VALIDATOR_PATH
    )
    with pytest.raises(AssertionError):
        _validate_lifecycle_validator_transition(transition, "0" * 40)


@pytest.mark.parametrize(
    "attack",
    [
        "predecessor_record",
        "predecessor_schema",
        "protected_commit",
        "pointer_predecessor",
        "missing_pointer_transition",
        "sequence_gap",
        "wrong_pointer_hash",
        "forged_evidence",
        "missing_readback",
        "open_terminal",
        "authority_grant",
        "historical_blob",
        "candidate_blob",
    ],
)
def test_execution_pointer_closure_successor_rejects_forgery(attack: str) -> None:
    closure = deepcopy(_load(EXECUTION_POINTER_CLOSURE_PATH))
    if attack == "predecessor_record":
        closure["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    elif attack == "predecessor_schema":
        closure["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    elif attack == "protected_commit":
        closure["protected_predecessor"]["commit"] = "0" * 40
    elif attack == "pointer_predecessor":
        closure["pointer_predecessor"]["canonical_sha256"] = "0" * 64
    elif attack == "missing_pointer_transition":
        closure["pointer_transitions"].pop()
    elif attack == "sequence_gap":
        closure["pointer_transitions"][1]["last_material_transition"]["sequence"] = 4
    elif attack == "wrong_pointer_hash":
        closure["pointer_transitions"][1]["last_material_transition"]["predecessor_pointer_sha256"] = "0" * 64
    elif attack == "forged_evidence":
        closure["pointer_transitions"][1]["evidence"]["sha256"] = "0" * 64
    elif attack == "missing_readback":
        closure["closure_evidence"]["receiver_role"] = ""
    elif attack == "open_terminal":
        closure["pointer_transitions"][-1]["disposition"]["state"] = "ACTIVE"
    elif attack == "authority_grant":
        closure["grants_authority"] = True
    elif attack == "historical_blob":
        closure["transitions"][0]["historical_blob"] = "0" * 40
    else:
        closure["transitions"][0]["candidate_blob"] = "0" * 40
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_programme_control_successor(
            _validate_successor(_load(SUCCESSOR_PATH)),
            [_load(EXECUTION_POINTER_SUCCESSOR_PATH), closure],
        )


def test_execution_pointer_closure_preserves_historical_successor_bytes() -> None:
    identities = {
        EXECUTION_POINTER_SUCCESSOR_PATH: (
            "2265a49cdab31c93b6dc26f7cbe309c8c3c3a1eb",
            "c4424f4e1b0edd19568d6d4705ed98e2c859878166574cf7b72db8661f87e186",
        ),
        EXECUTION_POINTER_SUCCESSOR_SCHEMA_PATH: (
            "0f4cb48e71db89c1bfd5866b621758a99642af18",
            "0eb117cc51baa8e2b4909a320c5c47510c5c30c9f1386f84113b00c823b77d1c",
        ),
    }
    for path, (blob, digest) in identities.items():
        assert _blob(path) == blob
        assert sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "attack",
    [
        "predecessor_record_blob",
        "predecessor_record_sha256",
        "predecessor_schema_blob",
        "predecessor_schema_sha256",
        "protected_commit",
        "protected_tree",
        "missing_transition",
        "additional_transition",
        "duplicate_transition",
        "reordered_transition",
        "wrong_path",
        "wrong_historical_blob",
        "arbitrary_candidate_blob",
        "protected_active",
        "authority",
        "forged_authorization",
    ],
)
def test_execution_pointer_successor_rejects_forged_or_unauthorised_transition(
    attack: str,
) -> None:
    record = deepcopy(_load(EXECUTION_POINTER_SUCCESSOR_PATH))
    if attack == "predecessor_record_blob":
        record["predecessor_evidence"]["record"]["git_blob"] = "0" * 40
    elif attack == "predecessor_record_sha256":
        record["predecessor_evidence"]["record"]["sha256"] = "0" * 64
    elif attack == "predecessor_schema_blob":
        record["predecessor_evidence"]["schema"]["git_blob"] = "0" * 40
    elif attack == "predecessor_schema_sha256":
        record["predecessor_evidence"]["schema"]["sha256"] = "0" * 64
    elif attack == "protected_commit":
        record["protected_predecessor"]["commit"] = "0" * 40
    elif attack == "protected_tree":
        record["protected_predecessor"]["tree"] = "0" * 40
    elif attack == "missing_transition":
        record["transitions"].pop()
    elif attack == "additional_transition":
        record["transitions"].append(
            {
                "path": "README.md",
                "historical_blob": "0" * 40,
                "candidate_blob": "1" * 40,
            }
        )
    elif attack == "duplicate_transition":
        record["transitions"].append(deepcopy(record["transitions"][0]))
    elif attack == "reordered_transition":
        record["transitions"].reverse()
    elif attack == "wrong_path":
        record["transitions"][0]["path"] = "tests/test_master_recovery_packet.py"
    elif attack == "wrong_historical_blob":
        record["transitions"][0]["historical_blob"] = "0" * 40
    elif attack == "arbitrary_candidate_blob":
        record["transitions"][0]["candidate_blob"] = "1" * 40
    elif attack == "protected_active":
        record["protected_active"] = True
    elif attack == "authority":
        record["grants_authority"] = True
    else:
        record["authorization_reference"]["decision"] = "OWNER_ACCEPTED_AND_MERGED"
    with pytest.raises((ValidationError, AssertionError, KeyError)):
        _resolve_programme_control_successor(
            _validate_successor(_load(SUCCESSOR_PATH)), [record]
        )


def test_execution_pointer_successor_rejects_missing_conflicting_and_replayed_chain() -> None:
    kf04 = _validate_successor(_load(SUCCESSOR_PATH))
    record = _load(EXECUTION_POINTER_SUCCESSOR_PATH)
    with pytest.raises(AssertionError, match="missing"):
        _resolve_programme_control_successor(kf04, [])
    with pytest.raises(AssertionError, match="uniquely applicable"):
        _resolve_programme_control_successor(kf04, [record, deepcopy(record)])
    replay = deepcopy(record)
    for transition in replay["transitions"]:
        transition["historical_blob"] = transition["candidate_blob"]
    with pytest.raises((ValidationError, AssertionError)):
        _resolve_programme_control_successor(kf04, [replay])


@pytest.mark.parametrize("attack", [
    "predecessor", "successor", "missing", "duplicate", "additional", "path",
    "amendment", "baseline", "historical_authority", "self_reference",
    "authority", "arbitrary_blob",
])
def test_kf04_successor_rejects_unsupported_transitions(attack: str) -> None:
    value = deepcopy(_load(SUCCESSOR_PATH))
    if attack == "predecessor":
        value["transitions"][0]["historical_blob"] = "0" * 40
    elif attack in {"successor", "arbitrary_blob"}:
        value["transitions"][0]["candidate_blob"] = "1" * 40
    elif attack == "missing":
        value["transitions"].pop()
    elif attack in {"duplicate", "additional"}:
        value["transitions"].append(deepcopy(value["transitions"][0]))
    elif attack == "path":
        value["transitions"][0]["path"] = "tests/test_stage3_gate_s03_01.py"
    elif attack == "amendment":
        value["amendment_id"] = "WRONG"
    elif attack == "baseline":
        value["protected_baseline"]["atis_master"] = "0" * 40
    elif attack == "historical_authority":
        value["historical_authority"] = "CURRENT_ACCEPTANCE"
    elif attack == "self_reference":
        value["publication_commit"] = "0" * 40
    elif attack == "authority":
        value["grants_authority"] = True
    with pytest.raises((ValidationError, AssertionError)):
        _validate_successor(value)

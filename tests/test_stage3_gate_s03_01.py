from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import pytest


ROOT = Path(__file__).parents[1]
RECORD_PATH = ROOT / "docs" / "stage3" / "gate-s03-01.json"

TOP_LEVEL_KEYS = [
    "schema_version",
    "record_id",
    "milestone_id",
    "gate_id",
    "candidate_status",
    "evaluated_baseline",
    "requirement_completeness",
    "evidence_assurance",
    "gate_domains",
    "evidence_state_policy",
    "authority",
    "completeness_evaluation",
    "self_reference",
    "serialization_contract",
    "next_permitted_activity",
]
EXPECTED_BASELINE = {
    "repository": "KiloAlpha021/automated-trading-bot",
    "protected_master": "a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325",
    "protected_tree": "e0ada9e8434b2122b547a323b83e1306553e9042",
    "protected_trust_main": "110c66530176795ce85c5cedf834992f06a68a30",
    "protected_trust_tree": "92bccbe5e9c9e896cd72bf42ce388841fe60a80c",
}
REQUIREMENT_IDS = [f"S3-REQ-{number:03d}" for number in range(1, 42)]
SUBSTANTIVE_IDS = [
    requirement_id for requirement_id in REQUIREMENT_IDS if requirement_id != "S3-REQ-036"
]
ASSURANCE_DIMENSIONS = [
    "ATTRIBUTABLE_EVIDENCE",
    "DIRECT_TESTS",
    "ADVERSARIAL_TESTS",
    "INDEPENDENT_REVIEW",
    "PROTECTED_IDENTITY_ATTRIBUTION",
]
GATE_DOMAINS = [
    "INSTRUMENT_PIT",
    "CALENDAR_PIT",
    "MARKET_DATA_INTEGRITY",
    "CORPORATE_ACTION_PIT",
    "FRESHNESS",
    "QUARANTINE",
]
EVIDENCE_STATES = [
    "CURRENT",
    "UNKNOWN",
    "STALE",
    "INVALIDATED",
    "INSUFFICIENT",
    "UNASSESSED",
    "FAILED",
    "CONTRADICTORY",
]
AUTHORITY_DENIALS = [
    "NO_AUTOMATIC_STAGE4_IMPLEMENTATION",
    "NO_AUTOMATIC_RESEARCH_BACKTEST_EXECUTION",
    "NO_SHADOW_TRADING",
    "NO_PAPER_TRADING",
    "NO_BROKER_AUTHORITY",
    "NO_OMS_AUTHORITY",
    "NO_RISK_APPROVAL",
    "NO_DATASET_PROMOTION_AUTHORITY",
    "NO_FINANCIAL_EFFECTS",
    "NO_LIVE_TRADING",
    "NO_LIVE_CAPITAL",
    "NO_AI_TRADING_AUTHORITY",
]
SELF_REFERENCE_KEYS = [
    "candidate_blob",
    "candidate_commit",
    "pr_number",
    "merge_commit",
    "future_protected_master",
    "future_protected_tree",
    "hosted_workflow_evidence",
]
GIT_IDENTITY = re.compile(r"[0-9a-f]{40}", re.ASCII)


EXPECTED_ASSURANCE_EVIDENCE = MappingProxyType(
    {
        "ATTRIBUTABLE_EVIDENCE": (
            "PC-DEC-007",
            "PC-DEC-011",
            "PC-DEC-015",
            "PC-DEC-022",
            "SE-PUB-008",
        ),
        "DIRECT_TESTS": (
            "ATIS:a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325:STAGE3_COMPONENT_INTEGRATION:577_PASS",
        ),
        "ADVERSARIAL_TESTS": (
            "ATIS:a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325:STAGE3_GOVERNANCE_SPEC_RECOVERY:356_PASS",
        ),
        "INDEPENDENT_REVIEW": (
            "ATIS-STAGE3-40-OF-40-SUBSTANTIVE-REQUIREMENTS-RECONCILIATION:STAGE3_SUBSTANTIVE_REQUIREMENTS_40_OF_40_VERIFIED",
        ),
        "PROTECTED_IDENTITY_ATTRIBUTION": (
            "ATIS:a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325:e0ada9e8434b2122b547a323b83e1306553e9042",
            "TRUST:110c66530176795ce85c5cedf834992f06a68a30:92bccbe5e9c9e896cd72bf42ce388841fe60a80c",
        ),
    }
)
EXPECTED_GATE_DOMAIN_EVIDENCE = MappingProxyType(
    {
        "INSTRUMENT_PIT": (
            "PC-DEC-007",
            "SE-PUB-002",
        ),
        "CALENDAR_PIT": (
            "PC-DEC-011",
            "SE-PUB-004",
        ),
        "MARKET_DATA_INTEGRITY": (
            "PC-DEC-012",
            "PC-DEC-014",
            "PC-DEC-017",
        ),
        "CORPORATE_ACTION_PIT": (
            "PC-DEC-015",
            "ATIS:a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325:tests/test_corporate_actions.py",
        ),
        "FRESHNESS": (
            "PC-DEC-018",
            "PC-DEC-020",
            "SE-PUB-006",
        ),
        "QUARANTINE": (
            "PC-DEC-017",
            "ATIS:a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325:tests/test_market_data_eligibility.py",
        ),
    }
)


def record() -> dict[str, Any]:
    return json.loads(RECORD_PATH.read_text(encoding="utf-8"))


def validate(candidate: dict[str, Any]) -> None:
    assert list(candidate) == TOP_LEVEL_KEYS
    assert candidate["schema_version"] == 1
    assert candidate["record_id"] == "STAGE3-CLOSURE-GATE-S03-01"
    assert candidate["milestone_id"] == "STAGE-3"
    assert candidate["gate_id"] == "GATE-S03-01"
    assert candidate["candidate_status"] == "LOCAL_CANDIDATE_NOT_AUTHORITATIVE"

    baseline = candidate["evaluated_baseline"]
    assert baseline == EXPECTED_BASELINE
    for name in (
        "protected_master",
        "protected_tree",
        "protected_trust_main",
        "protected_trust_tree",
    ):
        assert GIT_IDENTITY.fullmatch(baseline[name])

    completeness = candidate["requirement_completeness"]
    assert list(completeness) == [
        "canonical_count",
        "substantive_count",
        "substantive_satisfied_count",
        "partial_count",
        "blocked_count",
        "not_applicable_count",
        "requirements",
        "material_blockers",
    ]
    requirements = completeness["requirements"]
    assert [item["requirement_id"] for item in requirements] == REQUIREMENT_IDS
    assert len({item["requirement_id"] for item in requirements}) == 41
    assert all(list(item) == ["requirement_id", "disposition"] for item in requirements)
    dispositions = {item["requirement_id"]: item["disposition"] for item in requirements}
    assert all(dispositions[item] == "SATISFIED" for item in SUBSTANTIVE_IDS)
    assert dispositions["S3-REQ-036"] == "GATE_COMPLETENESS_CONTROL"
    assert completeness["canonical_count"] == len(requirements) == 41
    assert completeness["substantive_count"] == len(SUBSTANTIVE_IDS) == 40
    assert completeness["substantive_satisfied_count"] == 40
    assert completeness["partial_count"] == 0
    assert completeness["blocked_count"] == 0
    assert completeness["not_applicable_count"] == 0
    assert completeness["material_blockers"] == []

    policy = candidate["evidence_state_policy"]
    assert policy == {
        "closed_vocabulary": EVIDENCE_STATES,
        "readiness_permitted_state": "CURRENT",
        "all_other_states_prevent_readiness": True,
    }
    assurance = candidate["evidence_assurance"]
    assert [item["dimension"] for item in assurance] == ASSURANCE_DIMENSIONS
    assert len({item["dimension"] for item in assurance}) == len(ASSURANCE_DIMENSIONS)
    for item in assurance:
        assert list(item) == ["dimension", "state", "result", "evidence_refs"]
        assert item["state"] in EVIDENCE_STATES
        assert item["state"] == "CURRENT"
        assert item["result"] == "PASS"
        assert isinstance(item["evidence_refs"], list) and item["evidence_refs"]
        assert all(isinstance(value, str) and value for value in item["evidence_refs"])
        assert tuple(item["evidence_refs"]) == EXPECTED_ASSURANCE_EVIDENCE[item["dimension"]]

    domains = candidate["gate_domains"]
    assert [item["domain"] for item in domains] == GATE_DOMAINS
    assert len({item["domain"] for item in domains}) == len(GATE_DOMAINS)
    for item in domains:
        assert list(item) == ["domain", "state", "result", "evidence_refs"]
        assert item["state"] in EVIDENCE_STATES
        assert item["state"] == "CURRENT"
        assert item["result"] == "PASS"
        assert isinstance(item["evidence_refs"], list) and item["evidence_refs"]
        assert all(isinstance(value, str) and value for value in item["evidence_refs"])
        assert tuple(item["evidence_refs"]) == EXPECTED_GATE_DOMAIN_EVIDENCE[item["domain"]]

    assert candidate["authority"] == {
        "maximum_positive_consequence": (
            "ELIGIBLE_FOR_SEPARATELY_AUTHORIZED_STAGE4_RESEARCH_STRATEGY_INTEGRATION"
        ),
        "denials": AUTHORITY_DENIALS,
    }
    assert list(candidate["self_reference"]) == SELF_REFERENCE_KEYS
    assert all(candidate["self_reference"][name] is None for name in SELF_REFERENCE_KEYS)
    assert candidate["completeness_evaluation"] == "READY_FOR_GATE_EXECUTION"
    assert candidate["completeness_evaluation"] != "PASS"
    assert candidate["next_permitted_activity"] == (
        "INDEPENDENT-S3-REQ-036-CANDIDATE-ACCEPTANCE"
    )
    assert candidate["serialization_contract"] == {
        "encoding": "UTF-8",
        "bom": False,
        "indent_spaces": 2,
        "trailing_newline_count": 1,
        "git_identity_format": "LOWERCASE_40_HEX",
        "repository_path_separator": "/",
        "random_values": False,
        "machine_local_paths": False,
        "timestamps": False,
    }


def mutate(path: tuple[object, ...], value: object) -> dict[str, Any]:
    candidate = deepcopy(record())
    target: Any = candidate
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return candidate


def assert_rejected(candidate: dict[str, Any]) -> None:
    with pytest.raises((AssertionError, KeyError, TypeError)):
        validate(candidate)


def test_exact_candidate_derives_readiness_without_executing_gate() -> None:
    candidate = record()
    validate(candidate)
    assert candidate["completeness_evaluation"] == "READY_FOR_GATE_EXECUTION"
    assert candidate["candidate_status"] == "LOCAL_CANDIDATE_NOT_AUTHORITATIVE"
    assert "gate_result" not in candidate
    assert "GATE-S03-01 = PASS" not in RECORD_PATH.read_text(encoding="utf-8")


def test_serialization_is_exactly_bounded() -> None:
    raw = RECORD_PATH.read_bytes()
    text = raw.decode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert "\r" not in text and "\t" not in text
    assert all((len(line) - len(line.lstrip(" "))) % 2 == 0 for line in text.splitlines())
    assert "\\" not in text
    assert "C:/" not in text and "C:\\" not in text


def test_missing_duplicate_unknown_and_wrong_requirement_counts_reject() -> None:
    missing = deepcopy(record())
    missing["requirement_completeness"]["requirements"].pop()
    assert_rejected(missing)
    duplicate = deepcopy(record())
    duplicate["requirement_completeness"]["requirements"][1] = deepcopy(
        duplicate["requirement_completeness"]["requirements"][0]
    )
    assert_rejected(duplicate)
    unknown = deepcopy(record())
    unknown["requirement_completeness"]["requirements"][0]["requirement_id"] = "S3-REQ-999"
    assert_rejected(unknown)
    assert_rejected(mutate(("requirement_completeness", "canonical_count"), 40))
    assert_rejected(mutate(("requirement_completeness", "substantive_count"), 39))


@pytest.mark.parametrize("disposition", ["PARTIAL", "BLOCKED", "NOT_APPLICABLE"])
def test_non_satisfied_substantive_requirement_rejects(disposition: str) -> None:
    assert_rejected(
        mutate(("requirement_completeness", "requirements", 0, "disposition"), disposition)
    )


def test_circular_s3_req_036_satisfaction_rejects() -> None:
    assert_rejected(
        mutate(("requirement_completeness", "requirements", 35, "disposition"), "SATISFIED")
    )


@pytest.mark.parametrize("state", EVIDENCE_STATES[1:] + ["FUTURE"])
def test_non_current_or_unknown_evidence_state_rejects(state: str) -> None:
    assert_rejected(mutate(("evidence_assurance", 0, "state"), state))


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_assurance_failure_or_missing_reference_rejects(index: int) -> None:
    assert_rejected(mutate(("evidence_assurance", index, "result"), "FAIL"))
    assert_rejected(mutate(("evidence_assurance", index, "evidence_refs"), []))


def test_non_empty_material_blockers_reject() -> None:
    assert_rejected(
        mutate(("requirement_completeness", "material_blockers"), ["MATERIAL_BLOCKER"])
    )


def test_missing_duplicate_and_additional_gate_domain_reject() -> None:
    missing = deepcopy(record())
    missing["gate_domains"].pop()
    assert_rejected(missing)
    duplicate = deepcopy(record())
    duplicate["gate_domains"][1] = deepcopy(duplicate["gate_domains"][0])
    assert_rejected(duplicate)
    additional = deepcopy(record())
    additional["gate_domains"].append(
        {"domain": "EXTRA", "state": "CURRENT", "result": "PASS", "evidence_refs": ["x"]}
    )
    assert_rejected(additional)


def test_failed_non_current_or_unattributed_gate_domain_rejects() -> None:
    assert_rejected(mutate(("gate_domains", 0, "result"), "FAIL"))
    assert_rejected(mutate(("gate_domains", 0, "state"), "STALE"))
    assert_rejected(mutate(("gate_domains", 0, "evidence_refs"), []))


@pytest.mark.parametrize(
    "field,value",
    [
        ("protected_master", "A" * 40),
        ("protected_tree", "0" * 39),
        ("protected_trust_main", "92bccbe5e9c9e896cd72bf42ce388841fe60a80c"),
        ("protected_trust_tree", "110c66530176795ce85c5cedf834992f06a68a30"),
    ],
)
def test_malformed_wrong_or_swapped_git_identity_rejects(field: str, value: str) -> None:
    assert_rejected(mutate(("evaluated_baseline", field), value))


def test_authority_expansion_removal_reordering_and_automatic_authority_reject() -> None:
    expanded = deepcopy(record())
    expanded["authority"]["denials"].append("NO_EXTRA_AUTHORITY")
    assert_rejected(expanded)
    removed = deepcopy(record())
    removed["authority"]["denials"].pop()
    assert_rejected(removed)
    reordered = deepcopy(record())
    reordered["authority"]["denials"].reverse()
    assert_rejected(reordered)
    assert_rejected(
        mutate(("authority", "maximum_positive_consequence"), "STAGE4_IMPLEMENTATION_AUTHORIZED")
    )


@pytest.mark.parametrize("field", SELF_REFERENCE_KEYS)
def test_fabricated_future_identity_rejects(field: str) -> None:
    assert_rejected(mutate(("self_reference", field), "fabricated"))


def test_missing_or_unknown_top_level_field_rejects() -> None:
    missing = deepcopy(record())
    del missing["gate_domains"]
    assert_rejected(missing)
    unknown = deepcopy(record())
    unknown["unexpected"] = True
    assert_rejected(unknown)


def test_readiness_value_cannot_be_declared_when_record_is_not_exact() -> None:
    candidate = mutate(("requirement_completeness", "partial_count"), 1)
    candidate["completeness_evaluation"] = "READY_FOR_GATE_EXECUTION"
    assert_rejected(candidate)


def test_record_has_no_runtime_or_downstream_authority_surface() -> None:
    forbidden = {
        "broker",
        "oms",
        "risk_approval",
        "trading",
        "financial_effects",
        "live_capital",
        "ai_authority",
        "dataset_promotion",
        "gate_execution",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not (keys(record()) & forbidden)


@pytest.mark.parametrize(
    "section,name",
    [("evidence_assurance", name) for name in EXPECTED_ASSURANCE_EVIDENCE]
    + [("gate_domains", name) for name in EXPECTED_GATE_DOMAIN_EVIDENCE],
)
def test_exact_evidence_binding_rejects_mutations(section: str, name: str) -> None:
    canonical = record()
    validate(canonical)
    key = "dimension" if section == "evidence_assurance" else "domain"
    index = next(i for i, item in enumerate(canonical[section]) if item[key] == name)
    refs = canonical[section][index]["evidence_refs"]
    other_section = "gate_domains" if section == "evidence_assurance" else "evidence_assurance"
    cross_reference = next(
        ref
        for item in canonical[other_section]
        for ref in item["evidence_refs"]
        if ref not in refs
    )
    wrong_reference = next(
        ref
        for item in canonical[section]
        for ref in item["evidence_refs"]
        if ref not in refs
    )
    mutations: list[object] = [
        [], [""], [" "], ["\t"], ["\n"], ["garbage"], ["UNKNOWN-REFERENCE"],
        [refs[0] + "-ALTERED", *refs[1:]],
        [wrong_reference, *refs[1:]],
        [cross_reference, *refs[1:]],
        [*refs, refs[0]],
        refs[:-1],
        [*refs, "EXTRA-REFERENCE"],
        [123, *refs[1:]],
        [None, *refs[1:]],
        [{"reference": refs[0]}, *refs[1:]],
    ]
    if len(refs) > 1:
        mutations.append(list(reversed(refs)))
    for replacement in mutations:
        assert_rejected(mutate((section, index, "evidence_refs"), replacement))


# Prospective consumer for the existing GATE-S03-01, not a second gate.
# Historical validate() and its record remain unchanged.
def validate_owner_acceptance(
    control: dict[str, Any],
    assessment: dict[str, Any],
    protected_identity: dict[str, str],
) -> str:
    from hashlib import sha256

    from test_programme_control import validate_control

    validate_control(control)
    amendment = control["stage3_acceptance_amendment"]
    assert amendment["protected_identity"] == protected_identity
    assert amendment["owner_verdict"] == "PASS"
    verdict = amendment["owner_verdict_evidence"]
    assert verdict is not None and verdict["producer"] == "ATIS_OWNER"
    assert set(assessment) == {
        "gate_id", "protected_identity", "requirements", "gate_domains",
        "material_blockers", "authority_denials",
    }
    assert assessment["gate_id"] == "GATE-S03-01"
    assert assessment["protected_identity"] == protected_identity
    assert assessment["material_blockers"] == []
    assert assessment["authority_denials"] == AUTHORITY_DENIALS
    encoded = json.dumps(assessment, sort_keys=True, separators=(",", ":")).encode()
    assert verdict["assessment_sha256"] == sha256(encoded).hexdigest()
    rows = assessment["requirements"]
    assert isinstance(rows, list)
    assert [row["requirement_id"] for row in rows] == SUBSTANTIVE_IDS
    for row in rows:
        assert set(row) == {
            "requirement_id", "state", "result", "source_blobs", "direct_tests",
            "adversarial_tests", "evidence",
        }
        assert row["state"] == "CURRENT" and row["result"] == "PASS"
        for key in ("source_blobs", "direct_tests", "adversarial_tests"):
            assert isinstance(row[key], dict) and row[key]
            for identity, blob in row[key].items():
                assert isinstance(identity, str) and identity.strip()
                assert isinstance(blob, str) and GIT_IDENTITY.fullmatch(blob)
        evidence = row["evidence"]
        assert isinstance(evidence, dict)
        assert set(evidence) == {"producer", "reference", "sha256", "protected_identity"}
        assert isinstance(evidence["producer"], str) and evidence["producer"].strip()
        assert isinstance(evidence["reference"], str) and evidence["reference"].strip()
        assert re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])
        assert evidence["protected_identity"] == protected_identity
    domains = assessment["gate_domains"]
    assert isinstance(domains, dict) and set(domains) == set(GATE_DOMAINS)
    for refs in domains.values():
        assert isinstance(refs, list) and refs
        assert len(refs) == len(set(refs)) and set(refs) <= set(SUBSTANTIVE_IDS)
    return "OWNER_ACCEPTANCE_RECORD_VALID_NOT_GATE_PASS"


def _kf04_synthetic_case() -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    from hashlib import sha256

    from test_programme_control import _control

    control = deepcopy(_control())
    amendment = control["stage3_acceptance_amendment"]
    identity = deepcopy(amendment["protected_identity"])
    assessment = {
        "gate_id": "GATE-S03-01",
        "protected_identity": identity,
        "requirements": [
            {
                "requirement_id": requirement,
                "state": "CURRENT", "result": "PASS",
                "source_blobs": {"synthetic/source": "1" * 40},
                "direct_tests": {"synthetic::positive": "2" * 40},
                "adversarial_tests": {"synthetic::negative": "3" * 40},
                "evidence": {
                    "producer": "SYNTHETIC_TEST_ONLY",
                    "reference": "SYNTHETIC_NOT_ACCEPTANCE",
                    "sha256": "4" * 64, "protected_identity": identity,
                },
            } for requirement in SUBSTANTIVE_IDS
        ],
        "gate_domains": {domain: [SUBSTANTIVE_IDS[0]] for domain in GATE_DOMAINS},
        "material_blockers": [], "authority_denials": AUTHORITY_DENIALS,
    }
    amendment["owner_verdict"] = "PASS"
    amendment["owner_verdict_evidence"] = {
        "producer": "ATIS_OWNER", "record_id": "ATIS-S3-OWNER-VERDICT-SYNTHETIC",
        "sha256": "5" * 64,
        "assessment_sha256": sha256(
            json.dumps(assessment, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    return control, assessment, identity


def test_kf04_synthetic_positive_is_not_actual_acceptance() -> None:
    control, assessment, identity = _kf04_synthetic_case()
    assert validate_owner_acceptance(control, assessment, identity) == (
        "OWNER_ACCEPTANCE_RECORD_VALID_NOT_GATE_PASS"
    )
    from test_programme_control import _control
    assert _control()["stage3_acceptance_amendment"]["owner_verdict"] == "PENDING"
    validate(record())


@pytest.mark.parametrize("verdict", ["PENDING", "FAIL", "INSUFFICIENT", None])
def test_kf04_nonpass_owner_verdict_rejects(verdict: object) -> None:
    control, assessment, identity = _kf04_synthetic_case()
    if verdict is None:
        del control["stage3_acceptance_amendment"]["owner_verdict"]
    else:
        control["stage3_acceptance_amendment"]["owner_verdict"] = verdict
    with pytest.raises((AssertionError, ValueError)):
        validate_owner_acceptance(control, assessment, identity)


@pytest.mark.parametrize("mutation", [
    "missing_authority", "missing_verdict_evidence", "historical_review",
    "missing_current_evidence", "stale", "invalidated", "contradictory",
    "missing_requirement", "identity", "missing_identity", "expansion",
])
def test_kf04_adversarial_current_acceptance_rejects(mutation: str) -> None:
    from hashlib import sha256

    control, assessment, identity = _kf04_synthetic_case()
    amendment = control["stage3_acceptance_amendment"]
    row = assessment["requirements"][0]
    if mutation == "missing_authority":
        del amendment["acceptance_authority"]
    elif mutation == "missing_verdict_evidence":
        amendment["owner_verdict_evidence"] = None
    elif mutation == "historical_review":
        amendment["owner_verdict_evidence"]["producer"] = "HISTORICAL_40_OF_40"
    elif mutation == "missing_current_evidence":
        row["evidence"] = {}
    elif mutation in ("stale", "invalidated"):
        row["state"] = mutation.upper()
    elif mutation == "contradictory":
        row["result"] = "FAIL"
    elif mutation == "missing_requirement":
        assessment["requirements"].pop()
    elif mutation == "identity":
        identity = {**identity, "atis_master": "0" * 40}
    elif mutation == "missing_identity":
        assessment.pop("protected_identity")
    elif mutation == "expansion":
        assessment["authority_denials"] = []
    if amendment["owner_verdict_evidence"] is not None:
        amendment["owner_verdict_evidence"]["assessment_sha256"] = sha256(
            json.dumps(assessment, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    with pytest.raises((AssertionError, ValueError, KeyError)):
        validate_owner_acceptance(control, assessment, identity)


def test_prospective_consumer_does_not_grant_gate_authority(tmp_path):
    from automated_trading_bot.governance.stage3_acceptance import AcceptanceError, evaluate_gate

    with pytest.raises(AcceptanceError):
        evaluate_gate(tmp_path, tmp_path / "absent-assessment.json", tmp_path / "absent-verdict.json", "0" * 64, "0" * 64)


def test_prospective_authenticated_consumer_starts_without_owner_approval(tmp_path):
    from automated_trading_bot.governance.stage3_acceptance import evaluate_authenticated_gate

    result = evaluate_authenticated_gate(tmp_path, tmp_path / "assessment", tmp_path / "verdict", None, None)
    assert result["owner_acceptance"] == "NO_APPROVED_OWNER_VERDICT"
    assert result["gate_result"] == "NOT_ESTABLISHED"

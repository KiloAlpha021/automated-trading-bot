"""Validate attributable hosted trust-boundary evidence for IMP-001-M1-14."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/m1-closure/hosted-branch-protection-evidence.json"
TRADING_REPOSITORY = "KiloAlpha021/automated-trading-bot"
TRUSTED_REPOSITORY = "KiloAlpha021/security-workflows"
TRUSTED_COMMIT = "506d3de1bfc203a0bed585a7005c949f3f0efbe4"
TRUSTED_WORKFLOW = ".github/workflows/m1-trusted.yml"


def _load() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def _validate(data: dict) -> None:
    assert data["schema_version"] == 2
    assert data["requirement_id"] == "IMP-001-M1-14"
    assert data["organization"] == "KiloAlpha021"
    assert data["repository"] == TRADING_REPOSITORY
    assert data["repository_url"] == f"https://github.com/{TRADING_REPOSITORY}"
    assert data["default_branch"] == data["protected_branch"] == "master"

    rule = data["organization_ruleset"]
    assert rule["id"] == 23106039
    assert rule["name"] == "M1 trusted evaluator protection"
    assert rule["source_type"] == "Organization"
    assert rule["enforcement"] == "active"
    assert rule["target_repository"] == TRADING_REPOSITORY
    assert rule["target_ref"] == "refs/heads/master"
    assert rule["bypass_actors"] == []
    assert rule["current_user_can_bypass"] == "never"
    assert rule["pull_request_required"] is True
    assert rule["required_approving_review_count"] == 0
    assert rule["force_pushes_allowed"] is False
    assert rule["deletions_allowed"] is False

    trusted = data["trusted_workflow"]
    assert trusted["repository"] == TRUSTED_REPOSITORY
    assert trusted["repository_id"] == 1367762816
    assert trusted["default_branch"] == "main"
    assert trusted["ref"] == "refs/heads/main"
    assert trusted["path"] == TRUSTED_WORKFLOW
    assert trusted["commit"] == TRUSTED_COMMIT
    assert trusted["required_before_merge"] is True
    assert trusted["check_name"] == "trusted-m1-evaluator"
    assert trusted["permissions"] == {"contents": "read"}
    assert trusted["actions_pinned_to_full_commit_sha"] is True
    assert trusted["ordinary_trading_contributor_can_modify"] is False
    assert trusted["organization_default_repository_permission"] == "read"
    protection = trusted["branch_protection"]
    assert protection == {
        "pull_request_required": True,
        "enforce_admins": True,
        "force_pushes_allowed": False,
        "deletions_allowed": False,
        "required_conversation_resolution": True,
    }

    negative = data["negative_control"]
    assert negative["pull_request"] == 5
    assert negative["state"] == "closed" and negative["merged"] is False
    assert negative["base_branch"] == "master"
    assert negative["head_sha"] == "12646ada09b7cd01d22a5ab5003a9df2398f121c"
    assert negative["weakened_path"] == ".github/workflows/m1-engineering-foundation.yml"
    assert negative["preserved_local_check_name"] == "m1-engineering-foundation"
    assert negative["local_check"] == {
        "run_id": 34726265744,
        "check_run_id": 103640796637,
        "conclusion": "success",
    }
    assert negative["trusted_check"] == {
        "run_id": 34726265759,
        "check_run_id": 103640796497,
        "name": "trusted-m1-evaluator",
        "conclusion": "success",
    }
    assert negative["external_gate_ran_independently"] is True
    assert negative["merge_without_trusted_workflow_allowed"] is False
    assert negative["could_modify_trusted_workflow"] is False

    normal = data["normal_control"]
    assert normal["pull_request"] == 6
    assert normal["state"] == "closed" and normal["merged"] is False
    assert normal["base_branch"] == "master"
    assert normal["head_sha"] == "c8fcecd9838ac95dfae1b5a02299d1da4da99b3f"
    assert normal["changed_path"] == "docs/m1-closure/trusted-workflow-normal-smoke.md"
    assert normal["change_classification"] == "harmless documentation-only control"
    assert normal["trusted_check"] == {
        "run_id": 34726492411,
        "check_run_id": 103641401283,
        "name": "trusted-m1-evaluator",
        "conclusion": "success",
    }
    assert normal["required_gate_passed"] is True

    observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
    assert observed.tzinfo == timezone.utc and data["observed_at"].endswith("Z")
    source = data["evidence_source"]
    assert source["method"] == "Authenticated GitHub REST API"
    assert source["authenticated"] is True
    assert source["credentials_recorded"] is False
    assert data["classification"] == "VERIFIED"
    forbidden = {"authorization", "password", "token", "api_token"}
    assert forbidden.isdisjoint(key.lower() for key in source)


def test_hosted_branch_protection_evidence() -> None:
    _validate(_load())


@pytest.mark.parametrize(
    ("section", "field", "weakened"),
    [
        ("organization_ruleset", "enforcement", "evaluate"),
        ("organization_ruleset", "target_ref", "refs/heads/other"),
        ("organization_ruleset", "bypass_actors", [{"actor_id": 1}]),
        ("organization_ruleset", "pull_request_required", False),
        ("organization_ruleset", "force_pushes_allowed", True),
        ("organization_ruleset", "deletions_allowed", True),
        ("trusted_workflow", "repository", TRADING_REPOSITORY),
        ("trusted_workflow", "ref", "refs/heads/unprotected"),
        ("trusted_workflow", "path", ".github/workflows/other.yml"),
        ("trusted_workflow", "required_before_merge", False),
        ("trusted_workflow", "actions_pinned_to_full_commit_sha", False),
        ("trusted_workflow", "ordinary_trading_contributor_can_modify", True),
        ("negative_control", "external_gate_ran_independently", False),
        ("negative_control", "merge_without_trusted_workflow_allowed", True),
        ("negative_control", "could_modify_trusted_workflow", True),
        ("normal_control", "required_gate_passed", False),
        ("evidence_source", "credentials_recorded", True),
    ],
)
def test_hosted_branch_protection_evidence_rejects_weakened_control(
    section: str, field: str, weakened: object
) -> None:
    data = copy.deepcopy(_load())
    data[section][field] = weakened
    with pytest.raises(AssertionError):
        _validate(data)


def test_hosted_branch_protection_evidence_rejects_embedded_credentials() -> None:
    data = copy.deepcopy(_load())
    data["evidence_source"]["token"] = "not-a-real-credential"
    with pytest.raises(AssertionError):
        _validate(data)

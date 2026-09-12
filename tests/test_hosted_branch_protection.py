"""Validate attributable hosted protection evidence for IMP-001-M1-14."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/m1-closure/hosted-branch-protection-evidence.json"
EXPECTED_REPOSITORY = "KiloAlpha21/automated-trading-bot"
EXPECTED_CHECK = "m1-engineering-foundation"
EXPECTED_MERGE_SHA = "5f69f8e595556960c9b7509ce58bfd74e9058741"
EXPECTED_PR_HEAD_SHA = "8c8ba39cfd3445a1e839bffc90ea364c08495167"


def _load() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def _validate(data: dict) -> None:
    assert data["schema_version"] == 1
    assert data["requirement_id"] == "IMP-001-M1-14"
    assert data["repository"] == EXPECTED_REPOSITORY
    assert data["repository_url"] == f"https://github.com/{EXPECTED_REPOSITORY}"
    assert data["default_branch"] == data["protected_branch"] == "master"

    policy = data["approved_operating_model"]
    assert policy["authority"] == "explicit owner approval received 2026-09-12"
    assert policy["pull_request_required"] is True
    assert policy["required_approving_review_count"] == 0
    assert policy["required_status_check"] == EXPECTED_CHECK

    check = data["required_status_check"]
    assert check["context"] == EXPECTED_CHECK
    assert check["app_id"] == 15368
    assert check["strict"] is True
    assert check["required_before_merge"] is True

    pull_request = data["pull_request_requirement"]
    assert pull_request["enabled"] is True
    assert pull_request["required_approving_review_count"] == 0

    enforcement = data["enforcement"]
    assert enforcement == {
        "enabled": True,
        "enforce_admins": True,
        "ordinary_contributor_bypass_allowed": False,
        "force_pushes_allowed": False,
        "deletions_allowed": False,
    }

    merged = data["merged_pull_request"]
    assert merged["number"] == 1
    assert merged["state"] == "closed"
    assert merged["merged"] is True
    assert merged["base_branch"] == "master"
    assert merged["head_sha"] == EXPECTED_PR_HEAD_SHA
    assert merged["merge_commit_sha"] == EXPECTED_MERGE_SHA

    pre_merge = data["pre_merge_workflow_run"]
    assert pre_merge["workflow"] == EXPECTED_CHECK
    assert pre_merge["event"] == "pull_request"
    assert pre_merge["status"] == "completed"
    assert pre_merge["conclusion"] == "success"
    assert pre_merge["head_sha"] == EXPECTED_PR_HEAD_SHA
    assert pre_merge["run_id"] == 34709588267

    run = data["hosted_workflow_run"]
    assert run["workflow"] == EXPECTED_CHECK
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"
    assert run["head_sha"] == EXPECTED_MERGE_SHA
    assert run["run_id"] == 34709827020
    assert run["url"].startswith(
        f"https://github.com/{EXPECTED_REPOSITORY}/actions/runs/"
    )

    observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
    assert observed.tzinfo == timezone.utc
    assert data["observed_at"].endswith("Z")
    source = data["evidence_source"]
    assert source["method"] == "GitHub REST API"
    assert source["protection_endpoint_authenticated"] is True
    assert source["credentials_recorded"] is False
    forbidden_credential_keys = {"authorization", "password", "token", "api_token"}
    assert forbidden_credential_keys.isdisjoint(key.lower() for key in source)


def test_hosted_branch_protection_evidence() -> None:
    _validate(_load())


@pytest.mark.parametrize(
    ("section", "field", "weakened"),
    [
        ("required_status_check", "context", "other-check"),
        ("required_status_check", "strict", False),
        ("required_status_check", "required_before_merge", False),
        ("pull_request_requirement", "enabled", False),
        ("pull_request_requirement", "required_approving_review_count", 1),
        ("enforcement", "enforce_admins", False),
        ("enforcement", "ordinary_contributor_bypass_allowed", True),
        ("enforcement", "force_pushes_allowed", True),
        ("enforcement", "deletions_allowed", True),
        ("pre_merge_workflow_run", "conclusion", "failure"),
        ("hosted_workflow_run", "conclusion", "failure"),
        ("hosted_workflow_run", "head_sha", "0" * 40),
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

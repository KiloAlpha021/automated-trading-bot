"""Validate the authoritative current M1 re-closure without replacing historical checks."""

import copy
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = "841d903cb5670777f8ce23fe8433a0c5dd7dac49"
SPEC = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
TRUSTED = "506d3de1bfc203a0bed585a7005c949f3f0efbe4"
OWNER = "2db8dcef413612e21d9d9b95674b983f0c321b7554c4aad1fb7d753f5ca744d7"


def load(name):
    return json.loads((ROOT / name).read_text())


def validate(m):
    c = load("docs/m1-closure/atomic-assessment-checkpoint.json")
    o = load("docs/m1-closure/owner-dispositions.json")
    assert (
        m["milestone_id"] == "M1"
        and m["lifecycle_state"] == "COMPLETE"
        and m["exit_gate"] == "PASS"
    )
    assert (
        m["baseline_revision"] == BASE
        and m["governing_specification"]["sha256"] == SPEC
    )
    assert m["authorization"] == "NONE" and m["coverage"]["assessment_totals"] == {
        "VERIFIED": 50,
        "INSUFFICIENT_EVIDENCE": 0,
        "FAILED": 0,
        "unassessed": 0,
    }
    assert (
        m["stage0b"]["mandatory_disclosure"]
        == "ORIGINAL R39 INDIVIDUAL SEMANTICS NOT RECOVERED"
        and not m["stage0b"]["original_r39_semantics_recovered"]
    )
    assert (
        m["hosted_protection"]["organization_ruleset_id"] == 23106039
        and m["hosted_protection"]["trusted_workflow_observed_commit"] == TRUSTED
    )
    assert (
        m["hosted_exact_head_ci"]["head_sha"] == BASE
        and m["hosted_exact_head_ci"]["trusted_revision"] == TRUSTED
    )
    assert m["financial_primitives_owner_decision"] == {
        "decision_id": "M1-SCOPE-2026-09-13-01",
        "artifact": "docs/m1-closure/M1-financial-primitives-scope-clarification.md",
        "canonical_text_sha256": OWNER,
    }
    assert any(
        x["decision_id"] == "M1-SCOPE-2026-09-13-01"
        and x["canonical_text_sha256"] == OWNER
        for x in o["decisions"]
    )
    assert (
        c["assessment"] == c["assessment_status"] == "COMPLETED"
        and c["complete"] is True
    )
    assert c["authorization"] == "NONE" and c["reclosure"]["stage2_authorized"] is False
    assert c["unresolved_traceability_ids"] == c[
        "unassessed_requirement_ids"
    ] == [] and all(x["status"] == "VERIFIED" for x in c["records"])
    assert m["lineage"]["prior_reclosure"]["status"] == "HISTORICAL_SUPERSEDED"
    assert m["lineage"]["current_reclosure"] == {
        "baseline_revision": BASE,
        "authority": "CURRENT_M1_COMPLETION",
    }
    auth = m["authority_statement"].lower()
    [
        (_ for _ in ()).throw(AssertionError(x))
        for x in (
            "no trading",
            "no broker",
            "no oms",
            "no execution",
            "no paper-trading",
            "no live-trading",
            "no stage 2",
        )
        if x not in auth
    ]


@pytest.mark.parametrize(
    "field", ["baseline", "spec", "trusted", "owner", "authorization", "r39", "stage2"]
)
def test_current_reclosure_rejects_false_authority(field):
    m = copy.deepcopy(load("docs/m1-closure/closure-manifest.json"))
    if field == "baseline":
        m["baseline_revision"] = "0" * 40
    elif field == "spec":
        m["governing_specification"]["sha256"] = "0" * 64
    elif field == "trusted":
        m["hosted_exact_head_ci"]["trusted_revision"] = "0" * 40
    elif field == "owner":
        m["financial_primitives_owner_decision"]["canonical_text_sha256"] = "0" * 64
    elif field == "authorization":
        m["authorization"] = "LIVE"
    elif field == "r39":
        m["stage0b"]["original_r39_semantics_recovered"] = True
    else:
        m["authority_statement"] = m["authority_statement"].replace(
            "no Stage 2", "Stage 2"
        )
    with pytest.raises(AssertionError):
        validate(m)


def test_current_reclosure_is_valid():
    validate(load("docs/m1-closure/closure-manifest.json"))

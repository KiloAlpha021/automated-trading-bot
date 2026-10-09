"""Synthetic structural evidence only; no owner decision is created."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
from jsonschema import Draft202012Validator

from automated_trading_bot.governance import stage3_acceptance as consumer

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args]).decode().strip()


@pytest.fixture(scope="module")
def records():
    commit = git("rev-parse", "HEAD")
    subject = {"commit": commit, "tree": git("rev-parse", "HEAD^{tree}")}

    def ref(path):
        return {"path": path, "blob": git("rev-parse", commit + ":" + path)}

    history = json.loads(
        subprocess.check_output(
            ["git", "-C", str(ROOT), "show", commit + ":" + consumer.HISTORY]
        )
    )
    evidence = ref(consumer.REVIEW)
    assessment = {
        "schema_version": 1,
        "subject": subject,
        "requirements": [
            {
                "id": identity,
                "state": "CURRENT",
                **{
                    group: [evidence]
                    for group in (
                        "sources",
                        "direct_tests",
                        "adversarial_tests",
                        "execution_evidence",
                    )
                },
            }
            for identity in consumer.REQUIREMENTS
        ],
        "properties": [
            {
                "id": row["id"],
                "state": "CURRENT",
                "historical_record": ref(consumer.HISTORY),
                "invalidation_conditions": row["invalidation_conditions"],
                "reconciled_invalidators": row["invalidation_conditions"],
                "requirements": row["requirements"],
                "limitations": ["Synthetic structural fixture, not semantic evidence"],
            }
            for row in history["properties"]
        ],
        "historical_review": {
            "record": evidence,
            "state": "CURRENT",
            "limitations": ["Historical only"],
            "independent_acceptance": False,
        },
        "material_blockers": [],
        "independent_review": "DEFERRED_BY_OWNER",
    }
    verdict = {
        "schema_version": 1,
        "subject": subject,
        "amendment": "ATIS-S3-GOV-AMD-KF04-001",
        "assessment_sha256": consumer.canonical_digest(assessment),
        "state": "PENDING",
        "owner": "KiloAlpha21",
        "claimed_event_id": None,
        "independent_review": "DEFERRED_BY_OWNER",
    }
    return assessment, verdict


def evaluate(tmp_path, assessment, verdict):
    a, v = tmp_path / "assessment.json", tmp_path / "verdict.json"
    a.write_text(json.dumps(assessment), encoding="utf-8")
    v.write_text(json.dumps(verdict), encoding="utf-8")
    return consumer.evaluate_gate(
        ROOT,
        a,
        v,
        consumer.canonical_digest(assessment),
        consumer.canonical_digest(verdict),
    )


@pytest.mark.parametrize("state", ["PENDING", "PASS", "FAIL", "INSUFFICIENT"])
def test_no_candidate_verdict_can_enable_acceptance(records, tmp_path, state):
    assessment, verdict = deepcopy(records)
    verdict["state"] = state
    result = evaluate(tmp_path, assessment, verdict)
    assert result["gate_result"] == "NOT_ESTABLISHED"
    assert result["owner_acceptance"] == "NO_APPROVED_OWNER_VERDICT"
    schema = json.loads(
        (consumer.SCHEMAS / "stage3-gate-execution.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(result)


@pytest.mark.parametrize(
    "case",
    [
        "missing_requirement",
        "duplicate_requirement",
        "stale",
        "invalidated",
        "contradictory",
        "missing_direct",
        "missing_adversarial",
        "wrong_blob",
        "missing_object",
        "wrong_tree",
        "missing_property",
        "property_invalidated",
        "invalidators",
        "historical_promotion",
        "blocker",
        "authority",
        "self_reference",
        "wrong_amendment",
    ],
)
def test_reject_unsupported_structural_evidence(records, tmp_path, case):
    assessment, verdict = deepcopy(records)
    row = assessment["requirements"][0]
    if case == "missing_requirement":
        assessment["requirements"].pop()
    elif case == "duplicate_requirement":
        assessment["requirements"][1] = deepcopy(row)
    elif case in {"stale", "invalidated", "contradictory"}:
        row["state"] = case.upper()
    elif case == "missing_direct":
        row["direct_tests"] = []
    elif case == "missing_adversarial":
        row["adversarial_tests"] = []
    elif case == "wrong_blob":
        row["sources"][0]["blob"] = "0" * 40
    elif case == "missing_object":
        row["sources"][0]["path"] = "missing-evidence"
    elif case == "wrong_tree":
        assessment["subject"]["tree"] = "0" * 40
    elif case == "missing_property":
        assessment["properties"].pop()
    elif case == "property_invalidated":
        assessment["properties"][0]["state"] = "INVALIDATED"
    elif case == "invalidators":
        assessment["properties"][0]["reconciled_invalidators"] = []
    elif case == "historical_promotion":
        assessment["historical_review"]["independent_acceptance"] = True
    elif case == "blocker":
        assessment["material_blockers"] = ["unresolved"]
    elif case == "authority":
        verdict["trusted_authority"] = "candidate-selected"
    elif case == "self_reference":
        assessment["publication_commit"] = "future"
    elif case == "wrong_amendment":
        verdict["amendment"] = "unapproved"
    verdict["assessment_sha256"] = consumer.canonical_digest(assessment)
    with pytest.raises(consumer.AcceptanceError):
        evaluate(tmp_path, assessment, verdict)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"[]",
        b'{"a":NaN}',
        b'{"a":Infinity}',
    ],
)
def test_strict_json(raw):
    with pytest.raises(consumer.AcceptanceError):
        consumer.parse_record(raw)


def test_digest_and_absent_records(records, tmp_path):
    _, verdict = records
    path = tmp_path / "record.json"
    with pytest.raises(consumer.AcceptanceError):
        consumer.load_record(path, "verdict", "0" * 64)
    path.write_text(json.dumps(verdict), encoding="utf-8")
    with pytest.raises(consumer.AcceptanceError):
        consumer.load_record(path, "verdict", "0" * 64)


@pytest.fixture
def synthetic():
    body = {
        "subject_commit": "a" * 40,
        "subject_tree": "b" * 40,
        "assessment_sha256": "c" * 64,
        "verdict_sha256": "d" * 64,
        "amendment": "ATIS-S3-GOV-AMD-KF04-001",
        "independent_review": "DEFERRED_BY_OWNER",
        "fixture_only": True,
    }
    event = {
        "id": 1,
        "actor": "KiloAlpha21",
        "actor_id": 327435165,
        "body": body,
        "edited": False,
        "deleted": False,
    }
    binding = consumer.SyntheticBinding(
        1, consumer.canonical_digest(event), "a" * 40, "b" * 40, "c" * 64, "d" * 64
    )
    return event, binding


def test_synthetic_binding_is_explicitly_not_owner_approval(synthetic):
    assert (
        consumer.verify_synthetic_event(*synthetic)
        == "SYNTHETIC_BINDING_VERIFIED_NOT_OWNER_APPROVAL"
    )


@pytest.mark.parametrize(
    "case",
    [
        "actor",
        "event_id",
        "edited",
        "deleted",
        "missing_event",
        "missing_binding",
        "revoked",
        "superseded",
        "conflicting",
        "assessment",
        "verdict",
        "commit",
        "tree",
        "candidate_authority",
    ],
)
def test_synthetic_authentication_rejections(synthetic, case):
    event, binding = synthetic
    count = 1
    if case == "actor":
        event["actor"] = "impostor"
    elif case == "event_id":
        event["id"] = 2
    elif case in {"edited", "deleted"}:
        event[case] = True
    elif case == "missing_event":
        event = None
    elif case == "missing_binding":
        binding = None
    elif case in {"revoked", "superseded"}:
        binding = replace(binding, state=case.upper())
    elif case == "conflicting":
        count = 2
    elif case == "candidate_authority":
        event["authority"] = "self"
    else:
        key = {
            "assessment": "assessment_sha256",
            "verdict": "verdict_sha256",
            "commit": "subject_commit",
            "tree": "subject_tree",
        }[case]
        event["body"][key] = "0" * len(event["body"][key])
    with pytest.raises(consumer.AcceptanceError):
        consumer.verify_synthetic_event(event, binding, count)


def test_closed_schemas_are_valid():
    for kind in ("owner-assessment", "owner-verdict", "gate-execution"):
        Draft202012Validator.check_schema(
            json.loads((consumer.SCHEMAS / f"stage3-{kind}.schema.json").read_text())
        )


class FakeGitHub(consumer.GitHubReader):
    """Transport substitution only; these responses never reach real GitHub."""

    def __init__(self, commit, events):
        self.commit = commit
        self.events = events
        self.fail = False

    def get(self, route):
        if self.fail:
            raise consumer.AcceptanceError("Synthetic unavailable source")
        if route == "branches/master":
            return {"protected": True, "commit": {"sha": self.commit}}
        if route.startswith("issues/comments/"):
            return deepcopy(
                next(
                    (e for e in self.events if str(e["id"]) == route.split("/")[-1]),
                    None,
                )
            )
        return deepcopy(self.events)


@pytest.fixture
def production_case(records, tmp_path):
    import ast
    from hashlib import sha256

    assessment, verdict = deepcopy(records)
    subject = assessment["subject"]

    def ref(path):
        return {"path": path, "blob": git("rev-parse", subject["commit"] + ":" + path)}

    source = ref("src/automated_trading_bot/datasets/materialization.py")
    test = ref("tests/test_dataset_materialization.py")
    raw = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", subject["commit"] + ":" + test["path"]]
    )
    names = [
        n.name
        for n in ast.walk(ast.parse(raw))
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    ][:2]
    direct = {**test, "nodeid": test["path"] + "::" + names[0]}
    adverse = {**test, "nodeid": test["path"] + "::" + names[1]}
    historical = json.loads(
        subprocess.check_output(
            ["git", "-C", str(ROOT), "show", subject["commit"] + ":" + consumer.REVIEW]
        )
    )
    limit = consumer.canonical_digest(historical["provenance_limitations"])
    assessment["historical_review"]["provenance_sha256"] = limit
    for row in assessment["requirements"]:
        row["sources"] = [source]
        row["direct_tests"] = [direct]
        row["adversarial_tests"] = [adverse]
    receipt = {
        "producer": "SYNTHETIC_EXECUTION_PRODUCER",
        "subject": subject,
        "requirements": list(consumer.REQUIREMENTS),
        "dependencies": [source, test, ref(consumer.HISTORY), ref(consumer.REVIEW)],
        "tests": [
            {**identity, "requirement": req, "kind": kind, "outcome": "PASS"}
            for req in consumer.REQUIREMENTS
            for identity, kind in ((direct, "DIRECT"), (adverse, "ADVERSARIAL"))
        ],
        "invalidators": [
            {
                "property": p["id"],
                "condition": c,
                "state": "CURRENT",
                "requirements": p["requirements"],
            }
            for p in assessment["properties"]
            for c in p["invalidation_conditions"]
        ],
        "gate_domains": {domain: "PASS" for domain in consumer.DOMAINS},
        "result": "PASS",
        "limitations": [
            "Synthetic fixture, not evidence of actual test execution or approval"
        ],
        "historical_review_blob": ref(consumer.REVIEW)["blob"],
        "historical_limitations_sha256": limit,
    }
    receipt_path = tmp_path / "execution.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    verdict.update(
        state="PASS",
        record_id="SYNTHETIC-OWNER-VERDICT",
        assessment_sha256=consumer.canonical_digest(assessment),
    )
    a, v = tmp_path / "a.json", tmp_path / "v.json"
    a.write_text(json.dumps(assessment), encoding="utf-8")
    v.write_text(json.dumps(verdict), encoding="utf-8")
    ad, vd = consumer.canonical_digest(assessment), consumer.canonical_digest(verdict)
    decision = {
        "kind": "OWNER_VERDICT",
        "record_id": verdict["record_id"],
        "subject": subject,
        "assessment_sha256": ad,
        "verdict_sha256": vd,
        "state": "PASS",
        "amendment": consumer.AMENDMENT,
        "independent_review": "DEFERRED_BY_OWNER",
    }

    def event(identity, content):
        return {
            "id": identity,
            "node_id": f"SYNTHETIC-{identity}",
            "body": consumer.DECISION_PREFIX + json.dumps(content),
            "user": {"login": "KiloAlpha21", "id": 327435165},
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
            "issue_url": f"https://api.github.com/repos/{consumer.REPOSITORY}/issues/1",
        }

    events = [
        event(1, decision),
        event(2, {**decision, "kind": "GATE_EXECUTION", "state": "AUTHORIZED"}),
    ]
    digest = consumer.canonical_digest({"events": events})

    def pin(e):
        return consumer.OwnerEventPin(
            1,
            e["id"],
            e["node_id"],
            sha256(e["body"].encode()).hexdigest(),
            e["created_at"],
            digest,
        )

    authority = consumer.RuntimeAuthority(
        subject["commit"],
        subject["tree"],
        ref("docs/programme/programme-control.json")["blob"],
        ad,
        vd,
        pin(events[0]),
        (
            consumer.EvidencePin(
                receipt_path,
                sha256(receipt_path.read_bytes()).hexdigest(),
                receipt["producer"],
            ),
        ),
        gate_event=pin(events[1]),
    )
    return a, v, authority, FakeGitHub(subject["commit"], events), receipt


def run_production(case):
    a, v, authority, github, _ = case
    return consumer.evaluate_authenticated_gate(ROOT, a, v, authority, github)


def test_production_consumer_synthetic_end_to_end_not_actual_gate(production_case):
    result = run_production(production_case)
    assert result["gate_result"] == "READY_FOR_AUTHORIZED_EXECUTION"
    assert result["s3req036"] == "CURRENT_EVIDENCE_COMPLETE"
    assert result["reasons"] == ["GATE_NOT_EXECUTED"]
    schema = json.loads(
        (consumer.SCHEMAS / "stage3-gate-execution.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(result)


def test_owner_pass_alone_never_grants_gate(production_case):
    a, v, authority, github, receipt = production_case
    result = run_production(
        (a, v, replace(authority, gate_event=None), github, receipt)
    )
    assert result["gate_result"] == "NOT_ESTABLISHED"


def test_initial_production_state_has_no_authority(tmp_path):
    result = consumer.evaluate_authenticated_gate(
        ROOT, tmp_path / "missing", tmp_path / "missing", None, FakeGitHub("", [])
    )
    assert result["owner_acceptance"] == "NO_APPROVED_OWNER_VERDICT"


@pytest.mark.parametrize(
    "case",
    [
        "actor",
        "actor_id",
        "event_id",
        "node_id",
        "edited",
        "deleted",
        "body",
        "conversation",
        "revocation",
        "supersession",
        "conflict",
        "runtime_revoked",
        "runtime_superseded",
        "unavailable",
        "master_moved",
        "wrong_governance",
        "wrong_tree",
        "assessment_digest",
        "verdict_digest",
        "candidate_authority",
        "missing_artifact",
        "artifact_substitution",
        "missing_receipt",
        "producer",
        "no_effective_amendment",
    ],
)
def test_production_authentication_fails_closed(production_case, case):
    a, v, authority, github, receipt = production_case
    e = github.events[0]
    if case == "actor":
        e["user"]["login"] = "impostor"
    elif case == "actor_id":
        e["user"]["id"] = 1
    elif case == "event_id":
        e["id"] = 999
    elif case == "node_id":
        e["node_id"] = "forged"
    elif case == "edited":
        e["updated_at"] = "2026-10-10T00:00:00Z"
    elif case == "deleted":
        github.events.pop(0)
    elif case == "body":
        e["body"] += " "
    elif case == "conversation":
        e["issue_url"] += "2"
    elif case in {"revocation", "supersession", "conflict"}:
        other = deepcopy(e)
        other["id"] = 99
        other["body"] = other["body"].replace('"PASS"', json.dumps(case.upper()))
        github.events.append(other)
    elif case.startswith("runtime_"):
        authority = replace(authority, state=case[8:].upper())
    elif case == "unavailable":
        github.fail = True
    elif case == "master_moved":
        github.commit = "0" * 40
    elif case == "wrong_governance":
        authority = replace(authority, governance_blob="0" * 40)
    elif case == "wrong_tree":
        authority = replace(authority, protected_tree="0" * 40)
    elif case == "assessment_digest":
        authority = replace(authority, assessment_sha256="0" * 64)
    elif case == "verdict_digest":
        authority = replace(authority, verdict_sha256="0" * 64)
    elif case == "candidate_authority":
        value = json.loads(v.read_text())
        value["authority"] = {"state": "ACTIVE"}
        v.write_text(json.dumps(value))
        authority = replace(authority, verdict_sha256=consumer.canonical_digest(value))
    elif case == "missing_artifact":
        authority.evidence[0].path.unlink()
    elif case == "artifact_substitution":
        authority.evidence[0].path.write_text("{}")
    elif case == "missing_receipt":
        authority = replace(authority, evidence=())
    elif case == "producer":
        authority = replace(
            authority, evidence=(replace(authority.evidence[0], producer="impostor"),)
        )
    elif case == "no_effective_amendment":
        github.commit = "562e0d633b5b14749d5eb5476d9d8886adc703cf"
        authority = replace(
            authority,
            protected_commit=github.commit,
            protected_tree="d7178f5d84cffc6368b69878f0ff2998fe5a4c5d",
            governance_blob=git(
                "rev-parse", github.commit + ":docs/programme/programme-control.json"
            ),
        )
    with pytest.raises(consumer.AcceptanceError):
        run_production((a, v, authority, github, receipt))


@pytest.mark.parametrize(
    "case",
    [
        "stale_blob",
        "missing_direct",
        "missing_adversarial",
        "failed_test",
        "skipped_test",
        "nonexistent_test",
        "duplicate_test",
        "missing_requirement",
        "duplicate_requirement",
        "invalidated_property",
        "missing_invalidator",
        "wrong_invalidator",
        "historical_limitations",
        "historical_blob",
        "failed_domain",
        "missing_domain",
        "receipt_failed",
        "self_reference",
        "unknown_authority",
        "missing_dependency",
    ],
)
def test_semantic_currentness_negative_dominance(production_case, case):
    from hashlib import sha256

    a, _, authority, _, receipt = production_case
    if case == "stale_blob":
        receipt["dependencies"][0]["blob"] = "0" * 40
    elif case == "missing_direct":
        receipt["tests"] = [t for t in receipt["tests"] if t["kind"] != "DIRECT"]
    elif case == "missing_adversarial":
        receipt["tests"] = [t for t in receipt["tests"] if t["kind"] != "ADVERSARIAL"]
    elif case == "failed_test":
        receipt["tests"][0]["outcome"] = "FAIL"
    elif case == "skipped_test":
        receipt["tests"][0]["outcome"] = "SKIPPED"
    elif case == "nonexistent_test":
        receipt["tests"][0]["nodeid"] += "_DOES_NOT_EXIST"
    elif case == "duplicate_test":
        receipt["tests"].append(deepcopy(receipt["tests"][0]))
    elif case == "missing_requirement":
        receipt["requirements"].pop()
    elif case == "duplicate_requirement":
        receipt["requirements"].append(receipt["requirements"][0])
    elif case == "invalidated_property":
        receipt["invalidators"][0]["state"] = "INVALIDATED"
    elif case == "missing_invalidator":
        receipt["invalidators"].pop()
    elif case == "wrong_invalidator":
        receipt["invalidators"][0]["condition"] = "fabricated"
    elif case == "historical_limitations":
        receipt["historical_limitations_sha256"] = "0" * 64
    elif case == "historical_blob":
        receipt["historical_review_blob"] = "0" * 40
    elif case == "failed_domain":
        receipt["gate_domains"][consumer.DOMAINS[0]] = "FAIL"
    elif case == "missing_domain":
        del receipt["gate_domains"][consumer.DOMAINS[0]]
    elif case == "receipt_failed":
        receipt["result"] = "FAIL"
    elif case == "self_reference":
        receipt["future_commit"] = "future"
    elif case == "unknown_authority":
        receipt["independent_review"] = "PASS"
    elif case == "missing_dependency":
        receipt["dependencies"].pop(1)
    path = authority.evidence[0].path
    path.write_text(json.dumps(receipt), encoding="utf-8")
    pin = replace(authority.evidence[0], sha256=sha256(path.read_bytes()).hexdigest())
    with pytest.raises(consumer.AcceptanceError):
        consumer.verify_semantic_currentness(ROOT, json.loads(a.read_text()), (pin,))


def test_transport_rejects_missing_credentials_and_candidate_routes():
    with pytest.raises(consumer.AcceptanceError):
        consumer.GitHubReader("")
    with pytest.raises(consumer.AcceptanceError):
        consumer.GitHubReader("SYNTHETIC").get("https://evil.invalid")

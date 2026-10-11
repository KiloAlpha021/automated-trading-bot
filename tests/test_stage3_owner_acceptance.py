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


def schema_context():
    return consumer.SchemaContext(
        ROOT, git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}"),
        tuple((p, git("rev-parse", "HEAD:" + p)) for p in consumer.SCHEMA_PATHS),
    )


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
        schema_context=schema_context(),
    )


@pytest.mark.parametrize("state", ["PENDING", "PASS", "FAIL", "INSUFFICIENT"])
def test_no_candidate_verdict_can_enable_acceptance(records, tmp_path, state):
    assessment, verdict = deepcopy(records)
    verdict["state"] = state
    result = evaluate(tmp_path, assessment, verdict)
    assert result["gate_result"] == "NOT_ESTABLISHED"
    assert result["owner_acceptance"] == "NO_APPROVED_OWNER_VERDICT"
    schema = consumer.load_schema(schema_context(), "gate-execution")
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
        consumer.load_record(path, "verdict", "0" * 64, schema_context())
    path.write_text(json.dumps(verdict), encoding="utf-8")
    with pytest.raises(consumer.AcceptanceError):
        consumer.load_record(path, "verdict", "0" * 64, schema_context())


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
    for kind in ("assessment", "verdict", "gate-execution"):
        Draft202012Validator.check_schema(
            consumer.load_schema(schema_context(), kind)
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
        schema_context=schema_context(),
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
    schema = consumer.load_schema(schema_context(), "gate-execution")
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
        consumer.verify_semantic_currentness(
            ROOT, json.loads(a.read_text()), (pin,), schema_context=schema_context()
        )


def test_transport_rejects_missing_credentials_and_candidate_routes():
    with pytest.raises(consumer.AcceptanceError):
        consumer.GitHubReader("")
    with pytest.raises(consumer.AcceptanceError):
        consumer.GitHubReader("SYNTHETIC").get("https://evil.invalid")


@pytest.mark.parametrize("kind", ["assessment", "verdict", "gate-execution"])
def test_schema_context_exact_git_identity(kind, monkeypatch, tmp_path):
    context = schema_context()
    expected = consumer.load_schema(context, kind)
    monkeypatch.chdir(tmp_path)
    assert consumer.load_schema(context, kind) == expected


@pytest.mark.parametrize("case", [
    "missing_context", "candidate_dict", "missing_root", "wrong_root",
    "relative_root", "wrong_commit", "wrong_tree", "missing_blob",
    "substituted_blob", "missing_pin", "duplicate_pin", "path_substitution",
])
def test_schema_context_rejects_invalid_authority(case, tmp_path):
    context = schema_context()
    if case == "missing_context":
        context = None
    elif case == "candidate_dict":
        context = {"repository": str(ROOT), "blobs": context.blobs}
    elif case == "missing_root":
        context = replace(context, repository=tmp_path / "missing")
    elif case == "wrong_root":
        context = replace(context, repository=tmp_path)
    elif case == "relative_root":
        context = replace(context, repository=Path("."))
    elif case == "wrong_commit":
        context = replace(context, commit="0" * 40)
    elif case == "wrong_tree":
        context = replace(context, tree="0" * 40)
    elif case in {"missing_blob", "substituted_blob"}:
        blob = "0" * 40 if case == "missing_blob" else context.blobs[1][1]
        context = replace(context, blobs=((context.blobs[0][0], blob), *context.blobs[1:]))
    elif case == "missing_pin":
        context = replace(context, blobs=context.blobs[:-1])
    elif case == "duplicate_pin":
        context = replace(context, blobs=(context.blobs[0],) * 3)
    elif case == "path_substitution":
        context = replace(context, blobs=(("elsewhere.json", context.blobs[0][1]), *context.blobs[1:]))
    with pytest.raises(consumer.AcceptanceError):
        consumer.load_schema(context, "assessment")


@pytest.mark.parametrize("raw", [b"not-json", b'{"type":17}', b'{"type":"object","type":"array"}'])
def test_schema_context_rejects_malformed_pinned_schema(raw, monkeypatch):
    # Synthetic object-reader fault, never an authority for real evaluation.
    monkeypatch.setattr(consumer, "resolve_evidence", lambda *args: raw)
    with pytest.raises(consumer.AcceptanceError):
        consumer.load_schema(schema_context(), "assessment")


@pytest.mark.parametrize("case", ["absent", "wrong_root", "wrong_commit", "wrong_tree"])
def test_authenticated_schema_context_must_match_authority(production_case, tmp_path, case):
    a, v, authority, github, receipt = production_case
    context = authority.schema_context
    if case == "absent":
        context = None
    elif case == "wrong_root":
        context = replace(context, repository=tmp_path)
    elif case == "wrong_commit":
        context = replace(context, commit="0" * 40)
    else:
        context = replace(context, tree="0" * 40)
    with pytest.raises(consumer.AcceptanceError):
        run_production((a, v, replace(authority, schema_context=context), github, receipt))


def test_candidate_cannot_select_schema_context(records, tmp_path):
    assessment, verdict = deepcopy(records)
    assessment["schema_context"] = {"repository": str(tmp_path)}
    with pytest.raises(consumer.AcceptanceError):
        evaluate(tmp_path, assessment, verdict)


def test_installed_wheel_schema_context_without_source_pythonpath(production_case, tmp_path):
    import os
    import sys

    a, v, authority, _, _ = production_case
    context = authority.schema_context
    payload = {
        "repository": str(context.repository), "commit": context.commit,
        "tree": context.tree, "blobs": context.blobs,
        "assessment": str(a), "verdict": str(v),
        "assessment_digest": authority.assessment_sha256,
        "verdict_digest": authority.verdict_sha256,
        "receipt": str(authority.evidence[0].path),
        "receipt_digest": authority.evidence[0].sha256,
        "producer": authority.evidence[0].producer,
    }
    payload_path = tmp_path / "synthetic-installed-inputs.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    script = r"""
import json, sys
from pathlib import Path
from automated_trading_bot.governance import stage3_acceptance as c
assert "site-packages" in Path(c.__file__).parts, c.__file__
p = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ctx = c.SchemaContext(Path(p["repository"]), p["commit"], p["tree"], tuple(tuple(x) for x in p["blobs"]))
for kind in ("assessment", "verdict"):
    c.load_record(Path(p[kind]), kind, p[kind + "_digest"], ctx)
result = c.evaluate_gate(Path(p["repository"]), Path(p["assessment"]), Path(p["verdict"]), p["assessment_digest"], p["verdict_digest"], schema_context=ctx)
assert result["gate_result"] == "NOT_ESTABLISHED"
output = Path("synthetic-gate-output.json")
output.write_text(json.dumps(result), encoding="utf-8")
c.load_record(output, "gate-execution", c.canonical_digest(result), ctx)
c._receipt(c.EvidencePin(Path(p["receipt"]), p["receipt_digest"], p["producer"]), ctx)
print(json.dumps({"module": c.__file__, "schemas": 3, "receipt": "PASS", "actual_approval": False}))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(payload_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["schemas"] == 3


# Everything below uses a temporary synthetic repository and API transport.
# No fixture is actual execution evidence, owner approval or gate authority.
@pytest.fixture(scope="module")
def receipt_repository(tmp_path_factory):
    import shutil

    target = tmp_path_factory.mktemp("synthetic-git") / "repository"
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--shared",
                    "--no-checkout", str(ROOT), str(target)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target), "-c", "core.autocrlf=false",
                    "checkout", "--detach", git("rev-parse", "HEAD")], check=True, capture_output=True)
    paths = [consumer.PRODUCER_PATH, consumer.WORKFLOW_PATH,
             "tests/test_engineering_foundation.py", consumer.EXECUTOR_PATH,
             "docs/programme/stage3-gate-execution.schema.json",
             "tests/test_stage3_owner_acceptance.py", "tests/test_stage3_gate_s03_01.py"]
    for path in paths:
        shutil.copyfile(ROOT / path, target / path)
    subprocess.run(["git", "-C", str(target), "-c", "core.autocrlf=false", "add", "--", *paths],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target), "-c", "user.name=Synthetic fixture",
                    "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "--allow-empty", "-m", "Synthetic test subject; no protected authority"],
                   check=True, capture_output=True)
    return target


class ArtifactGitHub(FakeGitHub):
    def __init__(self, original, responses):
        super().__init__(original.commit, original.events)
        self.responses = responses

    def get(self, route):
        if route in self.responses:
            return deepcopy(self.responses[route])
        return super().get(route)


def pin_capture(tmp_path, capture, pin=None):
    from hashlib import sha256
    import zipfile

    archive = tmp_path / "synthetic-execution.zip"
    raw = json.dumps(capture, sort_keys=True, separators=(",", ":")).encode()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("execution-evidence/capture.json", raw)
    kwargs = dict(archive=archive, sha256=sha256(archive.read_bytes()).hexdigest(),
                  capture_sha256=sha256(raw).hexdigest(), canonical_sha256=consumer.canonical_digest(capture))
    if pin is not None:
        return replace(pin, **kwargs)
    return consumer.ExecutionArtifactPin(
        **kwargs, run_id=10, job_id=20, attempt=1, artifact_id=30,
        subject_commit=capture["subject"]["commit"], subject_tree=capture["subject"]["tree"],
        producer_blob=capture["producer"]["blob"], workflow_blob=git("rev-parse", "HEAD:" + consumer.WORKFLOW_PATH))


def pin_mapping(tmp_path, plan):
    from hashlib import sha256

    path = tmp_path / "synthetic-mapping.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return consumer.RequirementMappingPin(path, sha256(path.read_bytes()).hexdigest(),
                                          consumer.canonical_digest(plan))


@pytest.fixture
def admitted_case(receipt_repository, monkeypatch, tmp_path):
    # Independent fixture bootstrap, never selected by a submitted record.
    monkeypatch.setattr(__import__(__name__), "ROOT", receipt_repository)
    base = production_case.__wrapped__(records.__wrapped__(), tmp_path)
    a, v, authority, original, receipt = base
    subject = receipt["subject"]
    nodes = [receipt["tests"][i]["nodeid"] for i in (0, 1)]
    locked = {}
    for line in (ROOT / "requirements-dev.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, version = line.split()[0].split("==", 1)
            locked[name] = version
    capture = {
        "format": "ATIS_PYTEST_EXECUTION_V1", "subject": subject,
        "producer": {"path": consumer.PRODUCER_PATH, "blob": git("rev-parse", "HEAD:" + consumer.PRODUCER_PATH)},
        "dependencies": consumer._tree_dependencies(ROOT, subject["commit"]),
        "environment": {"module_origin": "SYNTHETIC/site-packages/automated_trading_bot",
                        "installed_wheel": True, "module_sources_match": True,
                        "python": "3.12.10", "distributions": locked},
        "provenance": {"repository": consumer.REPOSITORY, "event": "push", "ref": "refs/heads/master",
                       "sha": subject["commit"], "run_id": "10", "attempt": "1", "job": "m1-engineering-foundation"},
        "collected": nodes,
        "reports": [{"nodeid": n, "when": phase, "outcome": "passed", "xfail": False}
                    for n in nodes for phase in ("setup", "call", "teardown")],
        "collection_errors": [], "session_finished": True, "exit_code": 0,
        "workspace_stable": True, "committed_subject": True,
    }
    execution = pin_capture(tmp_path, capture)
    definitions = consumer.requirement_definitions(ROOT, subject["commit"])
    refs = json.loads(a.read_bytes())["requirements"][0]
    mapped = [{"nodeid": n, "code_sha256": consumer.test_function_digest(
                ROOT, subject["commit"], n, receipt["tests"][i]["blob"]),
               "rationale": "SYNTHETIC classification for contract testing only"}
              for i, n in enumerate(nodes)]
    plan = {
        "format": "ATIS_STAGE3_REQUIREMENT_MAPPING_V1", "subject": subject,
        "requirements": [{"id": req, "definition_sha256": definitions[req],
                          "sources": refs["sources"], "direct": [mapped[0]], "adversarial": [mapped[1]]}
                         for req in consumer.REQUIREMENTS],
        "invalidators": [{**item, "nodes": nodes, "rationale": "SYNTHETIC reviewed invalidator"}
                         for item in receipt["invalidators"]],
        "gate_domains": {d: {"state": "PASS", "nodes": nodes, "rationale": "SYNTHETIC reviewed domain"}
                         for d in consumer.DOMAINS},
        "limitations": receipt["limitations"], "historical_review_blob": receipt["historical_review_blob"],
        "historical_limitations_sha256": receipt["historical_limitations_sha256"],
    }
    mapping = pin_mapping(tmp_path, plan)
    responses = {
        "actions/runs/10/attempts/1": {"id": 10, "run_attempt": 1, "repository": {"full_name": consumer.REPOSITORY},
            "event": "push", "head_branch": "master", "head_sha": subject["commit"], "path": consumer.WORKFLOW_PATH,
            "status": "completed", "conclusion": "success"},
        "actions/jobs/20": {"id": 20, "run_id": 10, "run_attempt": 1, "name": "m1-engineering-foundation",
            "head_sha": subject["commit"], "status": "completed", "conclusion": "success"},
        "actions/artifacts/30": {"id": 30, "expired": False, "name": "m1-engineering-foundation-evidence",
            "workflow_run": {"id": 10, "head_sha": subject["commit"]}, "digest": "sha256:" + execution.sha256},
    }
    return dict(a=a, v=v, authority=authority, github=ArtifactGitHub(original, responses),
                execution=execution, mapping=mapping, capture=capture, plan=plan, directory=tmp_path)


def admit_case(case):
    return consumer.admit_execution_receipt(
        ROOT, case["execution"], case["mapping"], case["github"], case["authority"].schema_context,
        case["directory"] / "admitted.json")


def execute_case(case, admitted):
    authority = replace(case["authority"], evidence=(admitted.evidence,))
    context = consumer.GateExecutionContext(case["directory"], git("rev-parse", "HEAD:" + consumer.EXECUTOR_PATH),
                                            (admitted,))
    return consumer.execute_authorized_gate(ROOT, case["a"], case["v"], authority, case["github"], context)


def test_independent_admission_and_explicit_synthetic_terminal_execution(admitted_case):
    case = admitted_case
    admitted = admit_case(case)
    receipt = json.loads(admitted.evidence.path.read_bytes())
    assert receipt["requirements"] == list(consumer.REQUIREMENTS)
    assert len(receipt["tests"]) == 80
    assert receipt["limitations"][0].startswith("Synthetic fixture")
    authority = replace(case["authority"], evidence=(admitted.evidence,))
    ready = consumer.evaluate_authenticated_gate(ROOT, case["a"], case["v"], authority, case["github"])
    assert ready["gate_result"] == "READY_FOR_AUTHORIZED_EXECUTION"
    result = execute_case(case, admitted)
    assert result["gate_result"] == "PASS"  # SYNTHETIC ONLY: no real owner/gate action.
    assert result["authority_denials"] == list(consumer.TERMINAL_DENIALS)
    assert result["independent_review"] == "DEFERRED_BY_OWNER"
    with pytest.raises(consumer.AcceptanceError, match="Replayed"):
        execute_case(case, admitted)


@pytest.mark.parametrize("case_name", [
    "missing_archive", "altered_archive", "wrong_producer", "wrong_subject", "missing_node",
    "duplicate_node", "contradictory_outcome", "missing_call", "skipped", "xfail", "wrong_node",
    "missing_dependency", "changed_blob", "changed_environment", "uncommitted", "workspace_changed",
    "missing_direct", "missing_adversarial", "wrong_code", "missing_requirement", "duplicate_requirement",
    "stale_invalidator", "missing_invalidator", "wrong_review", "missing_domain", "unsupported_domain",
    "candidate_authority", "revoked_execution", "revoked_mapping", "wrong_artifact", "wrong_run",
    "wrong_job", "expired", "canonical_digest", "mapping_digest", "wrong_requirement_definition",
])
def test_execution_admission_rejects_unsupported_evidence(admitted_case, case_name):
    case = admitted_case
    capture, plan = case["capture"], case["plan"]
    if case_name == "wrong_producer":
        capture["producer"]["blob"] = "0" * 40
    elif case_name == "wrong_subject":
        capture["subject"] = {"commit": "0" * 40, "tree": "0" * 40}
    elif case_name == "missing_node":
        capture["reports"] = capture["reports"][:3]
    elif case_name == "duplicate_node":
        capture["collected"].append(capture["collected"][0])
    elif case_name == "contradictory_outcome":
        capture["reports"].append({**capture["reports"][0], "outcome": "failed"})
    elif case_name == "missing_call":
        capture["reports"].pop(1)
    elif case_name == "skipped":
        capture["reports"][1]["outcome"] = "skipped"
    elif case_name == "xfail":
        capture["reports"][1]["xfail"] = True
    elif case_name == "wrong_node":
        capture["reports"][1]["nodeid"] = "tests/forged.py::test_forged"
    elif case_name == "missing_dependency":
        capture["dependencies"].pop()
    elif case_name == "changed_blob":
        capture["dependencies"][0]["blob"] = "0" * 40
    elif case_name == "changed_environment":
        capture["environment"]["distributions"]["pytest"] = "0"
    elif case_name == "uncommitted":
        capture["committed_subject"] = False
    elif case_name == "workspace_changed":
        capture["workspace_stable"] = False
    elif case_name in {"missing_direct", "missing_adversarial"}:
        plan["requirements"][0][case_name.removeprefix("missing_")] = []
    elif case_name == "wrong_code":
        plan["requirements"][0]["direct"][0]["code_sha256"] = "0" * 64
    elif case_name == "missing_requirement":
        plan["requirements"].pop()
    elif case_name == "duplicate_requirement":
        plan["requirements"].append(plan["requirements"][0])
    elif case_name == "stale_invalidator":
        plan["invalidators"][0]["state"] = "STALE"
    elif case_name == "missing_invalidator":
        plan["invalidators"].pop()
    elif case_name == "wrong_review":
        plan["historical_review_blob"] = "0" * 40
    elif case_name == "missing_domain":
        plan["gate_domains"].pop(consumer.DOMAINS[0])
    elif case_name == "unsupported_domain":
        plan["gate_domains"][consumer.DOMAINS[0]]["nodes"] = ["unobserved"]
    elif case_name == "wrong_requirement_definition":
        plan["requirements"][0]["definition_sha256"] = "0" * 64
    case["execution"] = pin_capture(case["directory"], capture, case["execution"])
    case["mapping"] = pin_mapping(case["directory"], plan)
    github = case["github"]
    github.responses["actions/artifacts/30"]["digest"] = "sha256:" + case["execution"].sha256
    if case_name == "missing_archive":
        case["execution"] = replace(case["execution"], archive=case["directory"] / "absent")
    elif case_name == "altered_archive":
        case["execution"] = replace(case["execution"], sha256="0" * 64)
    elif case_name == "candidate_authority":
        case["mapping"] = {"path": str(case["mapping"].path)}
    elif case_name == "revoked_execution":
        case["execution"] = replace(case["execution"], state="REVOKED")
    elif case_name == "revoked_mapping":
        case["mapping"] = replace(case["mapping"], state="REVOKED")
    elif case_name == "wrong_artifact":
        github.responses["actions/artifacts/30"]["workflow_run"]["id"] = 999
    elif case_name == "wrong_run":
        github.responses["actions/runs/10/attempts/1"]["event"] = "pull_request"
    elif case_name == "wrong_job":
        github.responses["actions/jobs/20"]["conclusion"] = "failure"
    elif case_name == "expired":
        github.responses["actions/artifacts/30"]["expired"] = True
    elif case_name == "canonical_digest":
        case["execution"] = replace(case["execution"], canonical_sha256="0" * 64)
    elif case_name == "mapping_digest":
        case["mapping"] = replace(case["mapping"], canonical_sha256="0" * 64)
    with pytest.raises(consumer.AcceptanceError):
        admit_case(case)
    assert not (case["directory"] / "admitted.json").exists()


@pytest.mark.parametrize("mutation", ["owner_missing", "owner_revoked", "conflict", "gate_missing", "gate_wrong", "admission_missing"])
def test_terminal_requires_separate_current_authorities(admitted_case, mutation):
    case = admitted_case
    admitted = admit_case(case)
    authority = replace(case["authority"], evidence=(admitted.evidence,))
    context = consumer.GateExecutionContext(case["directory"], git("rev-parse", "HEAD:" + consumer.EXECUTOR_PATH),
                                            (admitted,))
    if mutation == "owner_missing":
        case["github"].events.pop(0)
    elif mutation == "owner_revoked":
        authority = replace(authority, state="REVOKED")
    elif mutation == "conflict":
        case["github"].events.append({**case["github"].events[0], "id": 3})
    elif mutation == "gate_missing":
        authority = replace(authority, gate_event=None)
    elif mutation == "gate_wrong":
        authority = replace(authority, gate_event=authority.owner_event)
    elif mutation == "admission_missing":
        context = replace(context, receipts=())
    if mutation == "admission_missing":
        result = consumer.execute_authorized_gate(ROOT, case["a"], case["v"], authority, case["github"], context)
        assert result["gate_result"] == "INSUFFICIENT"
    else:
        with pytest.raises(consumer.AcceptanceError):
            consumer.execute_authorized_gate(ROOT, case["a"], case["v"], authority, case["github"], context)
        assert not list(case["directory"].glob("gate-s03-01-*.json"))


def test_terminal_reauthenticates_admitted_execution(admitted_case):
    case = admitted_case
    admitted = admit_case(case)
    case["github"].responses["actions/artifacts/30"]["expired"] = True
    assert execute_case(case, admitted)["gate_result"] == "INSUFFICIENT"



def test_authenticated_material_blocker_produces_terminal_failure(admitted_case):
    from hashlib import sha256

    case = admitted_case
    admitted = admit_case(case)
    assessment = json.loads(case["a"].read_bytes())
    assessment["material_blockers"] = ["SYNTHETIC unresolved material blocker"]
    verdict = json.loads(case["v"].read_bytes())
    ad = consumer.canonical_digest(assessment)
    verdict["assessment_sha256"] = ad
    vd = consumer.canonical_digest(verdict)
    case["a"].write_text(json.dumps(assessment), encoding="utf-8")
    case["v"].write_text(json.dumps(verdict), encoding="utf-8")
    for event in case["github"].events:
        body = json.loads(event["body"].removeprefix(consumer.DECISION_PREFIX))
        body.update(assessment_sha256=ad, verdict_sha256=vd)
        event["body"] = consumer.DECISION_PREFIX + json.dumps(body)
    digest = consumer.canonical_digest({"events": case["github"].events})
    owner, gate = case["github"].events
    authority = case["authority"]
    case["authority"] = replace(authority, assessment_sha256=ad, verdict_sha256=vd,
        owner_event=replace(authority.owner_event, body_sha256=sha256(owner["body"].encode()).hexdigest(),
                            conversation_sha256=digest),
        gate_event=replace(authority.gate_event, body_sha256=sha256(gate["body"].encode()).hexdigest(),
                           conversation_sha256=digest))
    result = execute_case(case, admitted)
    assert result["gate_result"] == "FAIL"
    assert result["s3req036"] == "NOT_ESTABLISHED"


def test_repinning_receipt_does_not_override_execution_facts(admitted_case):
    from hashlib import sha256

    case = admitted_case
    admitted = admit_case(case)
    value = json.loads(admitted.evidence.path.read_bytes())
    value["tests"][0]["kind"] = "ADVERSARIAL"
    admitted.evidence.path.write_text(json.dumps(value), encoding="utf-8")
    admitted = replace(admitted,
        evidence=replace(admitted.evidence, sha256=sha256(admitted.evidence.path.read_bytes()).hexdigest()),
        canonical_sha256=consumer.canonical_digest(value))
    with pytest.raises(consumer.AcceptanceError, match="differs from authenticated"):
        consumer.revalidate_admitted_receipt(ROOT, admitted, case["github"], case["authority"].schema_context)

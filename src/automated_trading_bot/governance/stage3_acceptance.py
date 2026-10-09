"""Restrictive Stage 3 record consumer.

Git identities are not owner approval. Production evaluation requires independently
installed runtime authority, fresh authenticated GitHub reads and pinned execution
facts. No authority or actual approval is installed by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

REQUIREMENTS = tuple(f"S3-REQ-{n:03}" for n in range(1, 42) if n != 36)
PROPERTIES = tuple(f"P{n:02}" for n in range(1, 17))
SCHEMA_PATHS = tuple(
    "docs/programme/stage3-" + name + ".schema.json"
    for name in ("owner-assessment", "owner-verdict", "gate-execution")
)


@dataclass(frozen=True)
class SchemaContext:
    """Independent bootstrap pins, never populated from a candidate record.

    Schemas are immutable Git objects, not installation-relative files. The
    bootstrap must establish the repository and identities independently.
    """

    repository: Path
    commit: str
    tree: str
    blobs: tuple[tuple[str, str], ...]


def load_schema(context: SchemaContext | None, kind: str) -> dict[str, Any]:
    if type(context) is not SchemaContext:
        raise AcceptanceError("Missing trusted schema context")
    if kind not in {"assessment", "verdict", "gate-execution"}:
        raise AcceptanceError("Unknown schema kind")
    if not context.repository.is_absolute() or not context.repository.is_dir():
        raise AcceptanceError("Invalid trusted schema repository")
    if (
        len(context.blobs) != 3
        or len(dict(context.blobs)) != 3
        or set(dict(context.blobs)) != set(SCHEMA_PATHS)
    ):
        raise AcceptanceError("Incomplete trusted schema identities")
    verify_subject(context.repository, {"commit": context.commit, "tree": context.tree})
    schemas: dict[str, dict[str, Any]] = {}
    for path, blob in context.blobs:
        raw = resolve_evidence(
            context.repository, context.commit, {"path": path, "blob": blob}
        )
        schema = parse_record(raw)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise AcceptanceError("Malformed trusted schema") from error
        schemas[path] = schema
    name = "owner-" + kind if kind != "gate-execution" else kind
    return schemas["docs/programme/stage3-" + name + ".schema.json"]
HISTORY = "docs/stage3/s3-gd-024-property-disposition.json"
REVIEW = "docs/stage3/40of40-independent-review-recovery.json"


class AcceptanceError(ValueError):
    """Unusable or contradictory governance evidence."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise AcceptanceError(f"Non-finite JSON constant: {value}")


def parse_record(raw: bytes) -> dict[str, Any]:
    if len(raw) > 8_000_000 or raw.startswith(b"\xef\xbb\xbf"):
        raise AcceptanceError("Oversized or BOM-prefixed record")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise AcceptanceError("Invalid strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AcceptanceError("Record must be an object")
    return value


def canonical_digest(record: dict[str, Any]) -> str:
    try:
        raw = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, UnicodeError, TypeError, RecursionError) as error:
        raise AcceptanceError("Noncanonical record") from error
    return sha256(raw).hexdigest()


def load_record(
    path: Path, kind: str, expected_digest: str,
    schema_context: SchemaContext | None = None,
) -> dict[str, Any]:
    if kind not in {"assessment", "verdict", "gate-execution"}:
        raise AcceptanceError("Unknown record kind")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise AcceptanceError("Invalid expected digest")
    try:
        record = parse_record(path.read_bytes())
    except OSError as error:
        raise AcceptanceError("Missing record") from error
    schema = load_schema(schema_context, kind)
    if not Draft202012Validator(schema).is_valid(record):
        raise AcceptanceError("Closed schema rejection")
    if canonical_digest(record) != expected_digest:
        raise AcceptanceError("Canonical digest mismatch")
    return record


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise AcceptanceError("Missing or incompatible Git object")
    return result.stdout


def verify_subject(repo: Path, subject: dict[str, Any]) -> None:
    if set(subject) != {"commit", "tree"} or any(
        not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v)
        for v in subject.values()
    ):
        raise AcceptanceError("Invalid subject identity")
    if _git(repo, "cat-file", "-t", subject["commit"]).strip() != b"commit":
        raise AcceptanceError("Subject is not a commit")
    if (
        _git(repo, "rev-parse", subject["commit"] + "^{tree}").decode().strip()
        != subject["tree"]
    ):
        raise AcceptanceError("Subject tree mismatch")


def resolve_evidence(repo: Path, commit: str, reference: dict[str, str]) -> bytes:
    path = reference["path"]
    relative = PurePosixPath(path)
    if (
        relative.is_absolute()
        or relative.as_posix() != path
        or any(p in {"..", ".git"} for p in relative.parts)
        or not path
        or "\\" in path
        or ":" in path
    ):
        raise AcceptanceError("Unsafe evidence path")
    actual = _git(repo, "rev-parse", commit + ":" + path).decode().strip()
    if actual != reference["blob"]:
        raise AcceptanceError("Substituted evidence blob")
    if _git(repo, "cat-file", "-t", actual).strip() != b"blob":
        raise AcceptanceError("Evidence is not a blob")
    return _git(repo, "cat-file", "blob", actual)


def verify_evidence_structure(repo: Path, assessment: dict[str, Any]) -> None:
    """Verify exact object attribution; not semantic acceptance of test claims."""
    verify_subject(repo, assessment["subject"])
    commit = assessment["subject"]["commit"]
    cache: dict[tuple[str, str], bytes] = {}

    def resolved(reference: dict[str, str]) -> bytes:
        key = (reference["path"], reference["blob"])
        if key not in cache:
            cache[key] = resolve_evidence(repo, commit, reference)
        return cache[key]

    rows = assessment["requirements"]
    if tuple(row["id"] for row in rows) != REQUIREMENTS:
        raise AcceptanceError("Missing, duplicate or misordered requirements")
    if assessment["material_blockers"]:
        raise AcceptanceError("Material blockers")
    for row in rows:
        if row["state"] != "CURRENT":
            raise AcceptanceError("Noncurrent requirement")
        for group in (
            "sources",
            "direct_tests",
            "adversarial_tests",
            "execution_evidence",
        ):
            for reference in row[group]:
                resolved(reference)
    properties = assessment["properties"]
    if tuple(row["id"] for row in properties) != PROPERTIES:
        raise AcceptanceError("Missing or duplicate property applicability")
    for row in properties:
        if row["state"] != "CURRENT" or row["historical_record"]["path"] != HISTORY:
            raise AcceptanceError("Property applicability not established")
        record = parse_record(resolved(row["historical_record"]))
        originals = {item["id"]: item for item in record["properties"]}
        original = originals[row["id"]]
        if (
            row["invalidation_conditions"] != original["invalidation_conditions"]
            or sorted(row["reconciled_invalidators"])
            != sorted(original["invalidation_conditions"])
            or row["requirements"] != original["requirements"]
        ):
            raise AcceptanceError("Unreconciled or substituted property contract")
    review = assessment["historical_review"]
    if review["state"] != "CURRENT" or review["record"]["path"] != REVIEW:
        raise AcceptanceError("Historical review applicability missing")
    historical = parse_record(resolved(review["record"]))
    if not historical.get("provenance_limitations") or review["independent_acceptance"]:
        raise AcceptanceError("Historical authority promotion")
    # Semantic invalidation and execution-authenticity claims remain unaccepted
    # until a protected owner authority adapter is separately established.


@dataclass(frozen=True)
class SyntheticBinding:
    """Test-only input. Never accepted by evaluate_gate as production authority."""

    event_id: int
    event_digest: str
    subject_commit: str
    subject_tree: str
    assessment_digest: str
    verdict_digest: str
    state: str = "ACTIVE"


def verify_synthetic_event(
    event: dict[str, Any] | None,
    binding: SyntheticBinding | None,
    active_count: int = 1,
) -> str:
    if binding is None or binding.state != "ACTIVE" or active_count != 1:
        raise AcceptanceError("Missing, revoked, superseded or conflicting binding")
    if event is None or set(event) != {
        "id",
        "actor",
        "actor_id",
        "body",
        "edited",
        "deleted",
    }:
        raise AcceptanceError("Missing or forged event")
    if (
        event["actor"] != "KiloAlpha21"
        or event["actor_id"] != 327435165
        or event["id"] != binding.event_id
        or event["edited"]
        or event["deleted"]
        or canonical_digest(event) != binding.event_digest
    ):
        raise AcceptanceError("Event authentication mismatch")
    expected = {
        "subject_commit": binding.subject_commit,
        "subject_tree": binding.subject_tree,
        "assessment_sha256": binding.assessment_digest,
        "verdict_sha256": binding.verdict_digest,
        "amendment": "ATIS-S3-GOV-AMD-KF04-001",
        "independent_review": "DEFERRED_BY_OWNER",
        "fixture_only": True,
    }
    if event["body"] != expected:
        raise AcceptanceError("Event binding mismatch")
    return "SYNTHETIC_BINDING_VERIFIED_NOT_OWNER_APPROVAL"


def evaluate_gate(
    repo: Path,
    assessment_path: Path,
    verdict_path: Path,
    assessment_digest: str,
    verdict_digest: str,
    *, schema_context: SchemaContext | None = None,
) -> dict[str, Any]:
    """Fail closed: no caller-supplied authority or gate-PASS capability."""
    assessment = load_record(assessment_path, "assessment", assessment_digest, schema_context)
    verdict = load_record(verdict_path, "verdict", verdict_digest, schema_context)
    if (
        verdict["subject"] != assessment["subject"]
        or verdict["assessment_sha256"] != assessment_digest
    ):
        raise AcceptanceError("Verdict subject/assessment substitution")
    verify_evidence_structure(repo, assessment)
    return {
        "gate_id": "GATE-S03-01",
        "s3req036": "EVIDENCE_STRUCTURE_VERIFIED",
        "owner_acceptance": "NO_APPROVED_OWNER_VERDICT",
        "gate_result": "NOT_ESTABLISHED",
        "reasons": [
            "NO_PROTECTED_OWNER_AUTHORITY_ADAPTER",
            "SEMANTIC_CURRENTNESS_AND_EXECUTION_AUTHENTICITY_NOT_ACCEPTED",
            "NO_EXPLICIT_GATE_EXECUTION_AUTHORITY",
        ],
    }


# The configuration below belongs to the trusted invoking process. There is no
# JSON loader for it and it must never be populated from assessment/verdict data.
# Pins are explicit: no environment discovery, candidate-selected URL or fallback.
REPOSITORY = "KiloAlpha021/automated-trading-bot"
AMENDMENT = "ATIS-S3-GOV-AMD-KF04-001"
DECISION_PREFIX = "ATIS-CP1-OWNER-VERDICT-AUTH-V1\n"
DOMAINS = (
    "INSTRUMENT_PIT",
    "CALENDAR_PIT",
    "MARKET_DATA_INTEGRITY",
    "CORPORATE_ACTION_PIT",
    "FRESHNESS",
    "QUARANTINE",
)


@dataclass(frozen=True)
class OwnerEventPin:
    issue: int
    event_id: int
    node_id: str
    body_sha256: str
    created_at: str
    conversation_sha256: str


@dataclass(frozen=True)
class EvidencePin:
    """Independently selected immutable receipt; not candidate-supplied provenance."""

    path: Path
    sha256: str
    producer: str


@dataclass(frozen=True)
class RuntimeAuthority:
    """Trusted bootstrap input, never an assertion made by a candidate record.

    The caller must independently establish these pins and current revocation
    state before invocation. No pins are installed by this module. Replacing a
    revoked/superseded pin requires a new explicit owner decision, not replay.
    """

    protected_commit: str
    protected_tree: str
    governance_blob: str
    assessment_sha256: str
    verdict_sha256: str
    owner_event: OwnerEventPin
    evidence: tuple[EvidencePin, ...]
    state: str = "ACTIVE"
    gate_event: OwnerEventPin | None = None
    schema_context: SchemaContext | None = None


class GitHubReader:
    """Authenticated, read-only, fixed-origin GitHub REST transport."""

    def __init__(self, token: str) -> None:
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise AcceptanceError("Missing or invalid GitHub credential")
        self._token = token

    def get(self, route: str) -> Any:
        from urllib.error import HTTPError, URLError
        from urllib.request import HTTPRedirectHandler, Request, build_opener

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(
                self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
            ) -> None:
                raise AcceptanceError("GitHub redirect rejected")

        if not re.fullmatch(r"[A-Za-z0-9_/?=&.\-]+", route) or ".." in route:
            raise AcceptanceError("Unsupported GitHub route")
        url = "https://api.github.com/repos/" + REPOSITORY + "/" + route
        request = Request(
            url,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ATIS-CP1-read-only",
            },
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                if response.status != 200 or response.geturl() != url:
                    raise AcceptanceError("Unverifiable GitHub response")
                raw = response.read(8_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise AcceptanceError("GitHub authority unavailable") from error
        if len(raw) > 8_000_000:
            raise AcceptanceError("Oversized GitHub response")
        # Lists and objects both occur in GitHub REST; retain strict parsing.
        return parse_record(b'{"response":' + raw + b"}")["response"]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AcceptanceError(reason)


def _exact_object(value: Any, keys: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == keys, "Closed evidence record")
    result: dict[str, Any] = dict(value)
    return result


def _verify_protected_authority(
    repo: Path, authority: RuntimeAuthority, github: GitHubReader
) -> None:
    _require(
        type(authority) is RuntimeAuthority and authority.state == "ACTIVE",
        "Missing, revoked or superseded runtime authority",
    )
    live = github.get("branches/master")
    _require(
        isinstance(live, dict) and live.get("protected") is True,
        "Protected master unavailable",
    )
    _require(
        live.get("commit", {}).get("sha") == authority.protected_commit,
        "Protected authority moved",
    )
    verify_subject(
        repo, {"commit": authority.protected_commit, "tree": authority.protected_tree}
    )
    raw = resolve_evidence(
        repo,
        authority.protected_commit,
        {
            "path": "docs/programme/programme-control.json",
            "blob": authority.governance_blob,
        },
    )
    control = parse_record(raw)
    schema = parse_record(
        _git(
            repo,
            "show",
            authority.protected_commit
            + ":docs/programme/programme-control.schema.json",
        )
    )
    _require(
        Draft202012Validator(schema).is_valid(control), "Invalid protected governance"
    )
    amendment = control.get("stage3_acceptance_amendment", {})
    required = {
        "amendment_id": AMENDMENT,
        "scope": "STAGE3_ONLY",
        "acceptance_authority": "ATIS_OWNER",
        "acceptance_mode": "OWNER_LED_ACCEPTANCE",
        "independent_review": "DEFERRED_BY_OWNER",
        "independent_review_pass": False,
        "precedence": "PROSPECTIVE_ACCEPTANCE_MECHANISM_ONLY",
        "consumer": "GATE-S03-01",
        "preserve_substantive_obligations": True,
    }
    _require(
        all(amendment.get(k) == v for k, v in required.items()),
        "Owner-led amendment is not effective on protected master",
    )
    _require(
        amendment.get("authority_granted")
        == ["PROSPECTIVE_STAGE3_TECHNICAL_ACCEPTANCE"],
        "Unauthorized authority expansion",
    )


def _comments(github: GitHubReader, issue: int) -> list[dict[str, Any]]:
    _require(type(issue) is int and issue > 0, "Invalid protected conversation")
    result: list[dict[str, Any]] = []
    for page in range(1, 101):
        rows = github.get(f"issues/{issue}/comments?per_page=100&page={page}")
        _require(
            isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            "Unverifiable decision inventory",
        )
        result.extend(rows)
        if len(rows) < 100:
            ids = [row.get("id") for row in result]
            _require(
                all(type(i) is int for i in ids) and len(ids) == len(set(ids)),
                "Duplicate or invalid GitHub event identity",
            )
            return result
    raise AcceptanceError("Decision inventory exceeds verification bound")


def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(event, dict), "Missing GitHub event")
    keys = ("id", "node_id", "body", "user", "created_at", "updated_at", "issue_url")
    _require(all(k in event for k in keys), "Incomplete GitHub event")
    user = event["user"]
    _require(isinstance(user, dict), "Invalid GitHub actor")
    return {
        **{k: event[k] for k in keys if k != "user"},
        "user": {"login": user.get("login"), "id": user.get("id")},
    }


def authenticate_owner_event(
    github: GitHubReader, pin: OwnerEventPin, expected: dict[str, Any]
) -> None:
    """Re-read exact event and the complete protected conversation each time.

    Any inventory change invalidates the pin, including deletion of a pinned
    event. A caller must retain revocation/supersession monotonically in its
    trusted runtime authority; a mutable conversation is not that durable store.
    """
    _require(
        type(pin) is OwnerEventPin and type(pin.event_id) is int and pin.event_id > 0,
        "Missing independently selected owner event",
    )
    event = github.get(f"issues/comments/{pin.event_id}")
    _require(isinstance(event, dict), "Missing owner event")
    projected = _event_projection(event)
    _require(
        projected["id"] == pin.event_id and projected["node_id"] == pin.node_id,
        "Owner event identity mismatch",
    )
    _require(
        projected["user"] == {"login": "KiloAlpha21", "id": 327435165},
        "Wrong owner actor",
    )
    _require(
        projected["created_at"] == pin.created_at == projected["updated_at"],
        "Edited owner event",
    )
    _require(
        projected["issue_url"]
        == f"https://api.github.com/repos/{REPOSITORY}/issues/{pin.issue}",
        "Wrong owner conversation",
    )
    body = projected["body"]
    _require(
        isinstance(body, str)
        and sha256(body.encode("utf-8")).hexdigest() == pin.body_sha256,
        "Altered owner event contents",
    )
    _require(body.startswith(DECISION_PREFIX), "Unrecognized owner decision")
    _require(
        parse_record(body[len(DECISION_PREFIX) :].encode("utf-8")) == expected,
        "Owner subject, state or digest binding mismatch",
    )
    inventory = [_event_projection(row) for row in _comments(github, pin.issue)]
    _require(
        canonical_digest({"events": inventory}) == pin.conversation_sha256,
        "Decision deleted, revoked, superseded or conversation changed",
    )
    _require(sum(row == projected for row in inventory) == 1, "Unlisted owner event")
    for row in inventory:
        if row["id"] == pin.event_id or row["user"].get("id") != 327435165:
            continue
        text = row["body"]
        if isinstance(text, str) and text.startswith(DECISION_PREFIX):
            decision = parse_record(text[len(DECISION_PREFIX) :].encode("utf-8"))
            # A separate gate authorization is allowed; another owner decision
            # for this subject, including revocation/supersession, is restrictive.
            if (
                decision.get("subject") == expected["subject"]
                and decision.get("kind") == expected["kind"]
            ):
                raise AcceptanceError(
                    "Conflicting, revoked or superseded owner decision"
                )
    _require(
        _event_projection(github.get(f"issues/comments/{pin.event_id}")) == projected,
        "Owner event changed during evaluation",
    )


def _receipt(pin: EvidencePin, schema_context: SchemaContext | None) -> dict[str, Any]:
    _require(
        type(pin) is EvidencePin and bool(pin.producer), "Missing evidence producer"
    )
    try:
        raw = pin.path.read_bytes()
    except OSError as error:
        raise AcceptanceError("Unavailable execution artifact") from error
    _require(sha256(raw).hexdigest() == pin.sha256, "Execution artifact substitution")
    value = parse_record(raw)
    schema = load_schema(schema_context, "assessment")
    _require(
        Draft202012Validator(schema["$defs"]["executionReceipt"]).is_valid(value),
        "Malformed execution receipt",
    )
    receipt = _exact_object(
        value,
        {
            "producer",
            "subject",
            "requirements",
            "dependencies",
            "tests",
            "invalidators",
            "gate_domains",
            "result",
            "limitations",
            "historical_review_blob",
            "historical_limitations_sha256",
        },
    )
    _require(
        receipt["producer"] == pin.producer and receipt["result"] == "PASS",
        "Unattributed or failed execution artifact",
    )
    return receipt


def verify_semantic_currentness(
    repo: Path, assessment: dict[str, Any], pins: tuple[EvidencePin, ...],
    *, schema_context: SchemaContext | None = None,
) -> None:
    """Evaluate independently attributed execution facts against current Git.

    Historical executions carry forward only for the exact complete dependency
    closure recorded by the independently trusted evidence producer. Changed
    dependencies require a fresh receipt. Git identity alone is never a PASS.
    """
    _require(
        bool(pins) and len({p.sha256 for p in pins}) == len(pins),
        "Missing or competing execution evidence",
    )
    current = assessment["subject"]["commit"]
    historical = parse_record(_git(repo, "show", current + ":" + HISTORY))
    review = parse_record(_git(repo, "show", current + ":" + REVIEW))
    limitations = canonical_digest(review["provenance_limitations"])
    _require(
        assessment["historical_review"].get("provenance_sha256") == limitations,
        "Historical limitations not preserved",
    )
    review_blob = _git(repo, "rev-parse", current + ":" + REVIEW).decode().strip()
    receipts = [_receipt(pin, schema_context) for pin in pins]
    cache: dict[tuple[str, str, str], bytes] = {}

    def resolved(commit: str, ref: dict[str, str]) -> bytes:
        key = (commit, ref["path"], ref["blob"])
        if key not in cache:
            cache[key] = resolve_evidence(repo, commit, ref)
        return cache[key]

    for receipt in receipts:
        verify_subject(repo, receipt["subject"])
        _require(
            isinstance(receipt["dependencies"], list) and bool(receipt["dependencies"]),
            "Missing complete execution dependency closure",
        )
        dependencies: dict[str, str] = {}
        for ref in receipt["dependencies"]:
            _exact_object(ref, {"path", "blob"})
            _require(ref["path"] not in dependencies, "Competing dependency identities")
            resolved(receipt["subject"]["commit"], ref)
            resolved(current, ref)
            dependencies[ref["path"]] = ref["blob"]
        _require(
            receipt["historical_review_blob"] == review_blob
            and receipt["historical_limitations_sha256"] == limitations,
            "Stale historical applicability",
        )
        _require(
            isinstance(receipt["requirements"], list)
            and bool(receipt["requirements"])
            and len(set(receipt["requirements"])) == len(receipt["requirements"])
            and set(receipt["requirements"]) <= set(REQUIREMENTS),
            "Invalid requirement evidence scope",
        )
        _require(
            isinstance(receipt["tests"], list) and bool(receipt["tests"]),
            "Missing test outcomes",
        )
        ids: set[tuple[str, str]] = set()
        for test in receipt["tests"]:
            _exact_object(
                test, {"requirement", "nodeid", "kind", "outcome", "path", "blob"}
            )
            key = (test["requirement"], test["nodeid"])
            _require(key not in ids, "Duplicate or contradictory test result")
            ids.add(key)
            _require(
                test["requirement"] in receipt["requirements"]
                and test["kind"] in {"DIRECT", "ADVERSARIAL"}
                and test["outcome"] == "PASS",
                "Failed or unattributed mandatory test",
            )
            _require(
                dependencies.get(test["path"]) == test["blob"]
                and test["path"].startswith("tests/")
                and test["nodeid"].startswith(test["path"] + "::test_"),
                "Missing exact test identity",
            )
        import ast

        for test in receipt["tests"]:
            content = resolved(current, {"path": test["path"], "blob": test["blob"]})
            try:
                names = {
                    n.name
                    for n in ast.walk(ast.parse(content))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            except (SyntaxError, UnicodeError) as error:
                raise AcceptanceError("Unparseable test identity") from error
            _require(
                test["nodeid"].split("::")[-1].split("[")[0] in names,
                "Nonexistent executed test",
            )
        expected_conditions = {
            (p["id"], c): p["requirements"]
            for p in historical["properties"]
            for c in p["invalidation_conditions"]
        }
        for invalidator in receipt["invalidators"]:
            key_condition = (invalidator["property"], invalidator["condition"])
            _require(
                key_condition in expected_conditions
                and invalidator["state"] == "CURRENT"
                and invalidator["requirements"] == expected_conditions[key_condition]
                and set(invalidator["requirements"]) <= set(receipt["requirements"]),
                "Unsupported or invalidated property assessment",
            )
    for row in assessment["requirements"]:
        applicable = [r for r in receipts if row["id"] in r["requirements"]]
        _require(bool(applicable), "Requirement execution not established")
        all_tests = [
            t for r in applicable for t in r["tests"] if t["requirement"] == row["id"]
        ]
        for group, kind in (
            ("direct_tests", "DIRECT"),
            ("adversarial_tests", "ADVERSARIAL"),
        ):
            _require(bool(row[group]), "Missing mandatory tests")
            for ref in row[group]:
                _require(
                    any(
                        t["kind"] == kind
                        and t["path"] == ref["path"]
                        and t["blob"] == ref["blob"]
                        and t["nodeid"] == ref.get("nodeid")
                        for t in all_tests
                    ),
                    "Unsupported test PASS claim",
                )
        for ref in row["sources"]:
            _require(
                any(ref in r["dependencies"] for r in applicable),
                "Unexecuted source identity",
            )
    for prop in historical["properties"]:
        for condition in prop["invalidation_conditions"]:
            witnesses = [
                item
                for r in receipts
                for item in r["invalidators"]
                if item.get("property") == prop["id"]
                and item.get("condition") == condition
            ]
            _require(bool(witnesses), "Property invalidation not assessed")
            for witness in witnesses:
                _exact_object(
                    witness, {"property", "condition", "state", "requirements"}
                )
                _require(
                    witness["state"] == "CURRENT"
                    and witness["requirements"] == prop["requirements"],
                    "Invalidated or contradictory property evidence",
                )
    for domain in DOMAINS:
        witnesses = [
            r["gate_domains"].get(domain)
            for r in receipts
            if domain in r["gate_domains"]
        ]
        _require(
            bool(witnesses) and all(w == "PASS" for w in witnesses),
            "Mandatory gate predicate failed",
        )


def evaluate_authenticated_gate(
    repo: Path,
    assessment_path: Path,
    verdict_path: Path,
    authority: RuntimeAuthority | None,
    github: GitHubReader,
) -> dict[str, Any]:
    """Prospective existing-gate evaluation; never executes or publishes a gate.

    Passing configuration from an untrusted candidate is a caller trust-boundary
    violation. The deployment bootstrap, adapter and pins must be independently
    controlled. No actual approval or authority is shipped with this consumer.
    """
    if authority is None:
        return {
            "gate_id": "GATE-S03-01",
            "s3req036": "NOT_ESTABLISHED",
            "owner_acceptance": "NO_APPROVED_OWNER_VERDICT",
            "gate_result": "NOT_ESTABLISHED",
            "reasons": ["NO_INDEPENDENTLY_CONFIGURED_AUTHORITY"],
        }
    _verify_protected_authority(repo, authority, github)
    context = authority.schema_context
    _require(
        type(context) is SchemaContext
        and context.repository == repo.resolve()
        and context.commit == authority.protected_commit
        and context.tree == authority.protected_tree,
        "Schema context differs from independently protected authority",
    )
    assessment = load_record(assessment_path, "assessment", authority.assessment_sha256, context)
    verdict = load_record(verdict_path, "verdict", authority.verdict_sha256, context)
    subject = {"commit": authority.protected_commit, "tree": authority.protected_tree}
    _require(
        assessment["subject"] == verdict["subject"] == subject
        and verdict["assessment_sha256"] == authority.assessment_sha256,
        "Protected subject or assessment substitution",
    )
    _require(bool(verdict.get("record_id")), "Missing attributable verdict identity")
    _require(
        verdict["claimed_event_id"] in (None, authority.owner_event.event_id),
        "Candidate event reference differs from independently selected authority",
    )
    expected = {
        "kind": "OWNER_VERDICT",
        "record_id": verdict["record_id"],
        "subject": subject,
        "assessment_sha256": authority.assessment_sha256,
        "verdict_sha256": authority.verdict_sha256,
        "state": verdict["state"],
        "amendment": AMENDMENT,
        "independent_review": "DEFERRED_BY_OWNER",
    }
    authenticate_owner_event(github, authority.owner_event, expected)
    _require(verdict["state"] == "PASS", "Owner verdict is not PASS")
    verify_evidence_structure(repo, assessment)
    verify_semantic_currentness(repo, assessment, authority.evidence, schema_context=context)
    result = {
        "gate_id": "GATE-S03-01",
        "s3req036": "CURRENT_EVIDENCE_COMPLETE",
        "owner_acceptance": "AUTHENTICATED_OWNER_PASS",
        "gate_result": "NOT_ESTABLISHED",
        "reasons": ["NO_EXPLICIT_GATE_EXECUTION_AUTHORITY"],
    }
    if authority.gate_event is not None:
        authenticate_owner_event(
            github,
            authority.gate_event,
            {**expected, "kind": "GATE_EXECUTION", "state": "AUTHORIZED"},
        )
        result["gate_result"] = "READY_FOR_AUTHORIZED_EXECUTION"
        result["reasons"] = ["GATE_NOT_EXECUTED"]
    _verify_protected_authority(repo, authority, github)
    context = authority.schema_context
    _require(
        type(context) is SchemaContext
        and context.repository == repo.resolve()
        and context.commit == authority.protected_commit
        and context.tree == authority.protected_tree,
        "Schema context differs from independently protected authority",
    )
    authenticate_owner_event(github, authority.owner_event, expected)
    return result

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


# Phase 6: capture is evidence, never authority. These pins are constructed only
# by the independently controlled runtime, not deserialized from a submission.
@dataclass(frozen=True)
class ExecutionArtifactPin:
    archive: Path
    sha256: str
    capture_sha256: str
    canonical_sha256: str
    run_id: int
    job_id: int
    attempt: int
    artifact_id: int
    subject_commit: str
    subject_tree: str
    producer_blob: str
    workflow_blob: str
    state: str = "ACTIVE"


@dataclass(frozen=True)
class RequirementMappingPin:
    path: Path
    sha256: str
    canonical_sha256: str
    state: str = "ACTIVE"


@dataclass(frozen=True)
class AdmittedReceipt:
    evidence: EvidencePin
    canonical_sha256: str
    execution: ExecutionArtifactPin
    mapping: RequirementMappingPin


@dataclass(frozen=True)
class GateExecutionContext:
    """Independent execution bootstrap; result directory is operator controlled.

    The immutable result itself consumes a gate-event authorization. Deleting
    or relocating that result is outside this API and must not be allowed by
    the operator. No authority state or signing credential is created here.
    """

    output_directory: Path
    executor_blob: str
    receipts: tuple[AdmittedReceipt, ...]


PRODUCER_PATH = "scripts/m1_engineering_foundation.py"
WORKFLOW_PATH = ".github/workflows/m1-engineering-foundation.yml"
EXECUTOR_PATH = "src/automated_trading_bot/governance/stage3_acceptance.py"
SPECIFICATION = "docs/stage3/stage3-specification.json"
TERMINAL_DENIALS = (
    "NO_AUTOMATIC_STAGE3_CLOSURE", "NO_STAGE4_AUTHORITY", "NO_TRADING_AUTHORITY",
    "NO_INDEPENDENT_REVIEW_PASS",
)


def _pinned_bytes(path: Path, digest: str) -> bytes:
    _require(bool(re.fullmatch(r"[0-9a-f]{64}", digest)), "Invalid independent pin")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise AcceptanceError("Missing pinned artifact") from error
    _require(sha256(raw).hexdigest() == digest, "Altered pinned artifact")
    return raw


def _tree_dependencies(repo: Path, commit: str) -> list[dict[str, str]]:
    rows = []
    for entry in _git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        _require(kind == "blob" and mode in {"100644", "100755"},
                 "Unsupported dependency object")
        rows.append({"path": name.decode("utf-8"), "blob": blob})
    return rows


def _authenticate_execution(
    repo: Path, pin: ExecutionArtifactPin, github: GitHubReader,
) -> dict[str, Any]:
    import io
    import zipfile

    _require(type(pin) is ExecutionArtifactPin and pin.state == "ACTIVE",
             "Missing, stale or revoked execution authority")
    _require(all(type(n) is int and n > 0 for n in
                 (pin.run_id, pin.job_id, pin.attempt, pin.artifact_id)),
             "Invalid execution identity")
    subject = {"commit": pin.subject_commit, "tree": pin.subject_tree}
    verify_subject(repo, subject)
    for path, blob in ((PRODUCER_PATH, pin.producer_blob),
                       (WORKFLOW_PATH, pin.workflow_blob)):
        resolve_evidence(repo, pin.subject_commit, {"path": path, "blob": blob})
    run = github.get(f"actions/runs/{pin.run_id}/attempts/{pin.attempt}")
    job = github.get(f"actions/jobs/{pin.job_id}")
    artifact = github.get(f"actions/artifacts/{pin.artifact_id}")
    _require(all(isinstance(v, dict) for v in (run, job, artifact)),
             "Execution authority unavailable")
    _require(
        run.get("id") == pin.run_id and run.get("run_attempt") == pin.attempt
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("event") == "push" and run.get("head_branch") == "master"
        and run.get("head_sha") == pin.subject_commit
        and run.get("path") == WORKFLOW_PATH and run.get("status") == "completed"
        and run.get("conclusion") == "success",
        "Unverified protected producer execution",
    )
    _require(
        job.get("id") == pin.job_id and job.get("run_id") == pin.run_id
        and job.get("run_attempt") == pin.attempt
        and job.get("name") == "m1-engineering-foundation"
        and job.get("head_sha") == pin.subject_commit
        and job.get("status") == "completed" and job.get("conclusion") == "success",
        "Wrong or failed execution job",
    )
    _require(
        artifact.get("id") == pin.artifact_id and artifact.get("expired") is False
        and artifact.get("name") == "m1-engineering-foundation-evidence"
        and artifact.get("workflow_run", {}).get("id") == pin.run_id
        and artifact.get("workflow_run", {}).get("head_sha") == pin.subject_commit
        and artifact.get("digest") == "sha256:" + pin.sha256,
        "Unverified execution artifact origin",
    )
    raw = _pinned_bytes(pin.archive, pin.sha256)
    _require(len(raw) <= 32_000_000, "Oversized execution archive")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)), "Duplicate archive member")
            info = archive.getinfo("execution-evidence/capture.json")
            _require(info.file_size <= 8_000_000, "Oversized execution capture")
            capture = archive.read(info)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        raise AcceptanceError("Missing or malformed execution capture") from error
    _require(sha256(capture).hexdigest() == pin.capture_sha256,
             "Substituted execution capture")
    value = parse_record(capture)
    _require(canonical_digest(value) == pin.canonical_sha256,
             "Execution canonical digest mismatch")
    _exact_object(value, {"format", "subject", "producer", "dependencies", "environment",
                          "provenance", "collected", "reports", "collection_errors",
                          "session_finished", "exit_code", "workspace_stable",
                          "committed_subject"})
    _require(value["format"] == "ATIS_PYTEST_EXECUTION_V1" and value["subject"] == subject,
             "Execution subject substitution")
    _require(value["producer"] == {"path": PRODUCER_PATH, "blob": pin.producer_blob},
             "Wrong evidence producer")
    _require(value["dependencies"] == _tree_dependencies(repo, pin.subject_commit),
             "Incomplete execution dependency closure")
    _require(value["provenance"] == {
        "repository": REPOSITORY, "event": "push", "ref": "refs/heads/master",
        "sha": pin.subject_commit, "run_id": str(pin.run_id),
        "attempt": str(pin.attempt), "job": "m1-engineering-foundation",
    }, "Execution provenance mismatch")
    environment = _exact_object(value["environment"], {
        "module_origin", "installed_wheel", "module_sources_match", "python", "distributions",
    })
    _require(environment["python"] == "3.12.10"
             and environment["installed_wheel"] is True
             and environment["module_sources_match"] is True,
             "Unverified installed source identity")
    expected = {}
    for line in _git(repo, "show", pin.subject_commit + ":requirements-dev.lock").decode().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split()[0].split("==", 1)
            expected[name] = version
    _require(environment["distributions"] == expected, "Execution dependency environment changed")
    _require(value["session_finished"] is True and value["workspace_stable"] is True
             and value["committed_subject"] is True and type(value["exit_code"]) is int
             and value["exit_code"] == 0 and value["collection_errors"] == [],
             "Incomplete, failed or uncommitted execution")
    return value


def _observed_nodes(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = capture["collected"]
    _require(isinstance(nodes, list) and bool(nodes)
             and all(isinstance(n, str) and n for n in nodes)
             and len(nodes) == len(set(nodes)), "Missing or duplicate collected nodes")
    observed: dict[str, dict[str, Any]] = {n: {} for n in nodes}
    _require(isinstance(capture["reports"], list), "Missing node reports")
    for raw in capture["reports"]:
        report = _exact_object(raw, {"nodeid", "when", "outcome", "xfail"})
        node, phase = report["nodeid"], report["when"]
        _require(isinstance(node, str) and node in observed
                 and phase in {"setup", "call", "teardown"}
                 and report["outcome"] in {"passed", "failed", "skipped"}
                 and type(report["xfail"]) is bool, "Unattributed test report")
        _require(phase not in observed[node], "Duplicate or contradictory test outcome")
        _require(report["outcome"] != "failed", "Failed execution hidden by exit code")
        observed[node][phase] = report
    # All collected nodes must have an actual lifecycle. Skips remain visible;
    # they may not witness a required positive/adversarial outcome.
    for phases in observed.values():
        _require("setup" in phases and "teardown" in phases,
                 "Not-run or interrupted collected test")
        if phases["setup"]["outcome"] == "passed":
            _require("call" in phases, "Missing test call outcome")
    return observed


def _passed_node(nodes: dict[str, dict[str, Any]], node: str) -> None:
    _require(node in nodes and set(nodes[node]) == {"setup", "call", "teardown"}
             and all(r["outcome"] == "passed" and not r["xfail"]
                     for r in nodes[node].values()), "Missing, skipped or failed mandatory node")


def requirement_definitions(repo: Path, commit: str) -> dict[str, str]:
    """Bind mapping rationales to exact canonical requirement text, not filenames."""
    value = parse_record(_git(repo, "show", commit + ":" + SPECIFICATION))
    definitions: dict[str, str] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            identity = item.get("id")
            if isinstance(identity, str) and identity in REQUIREMENTS and "exact_final_section" in item:
                digest = canonical_digest({"id": identity, "text": item["exact_final_section"]})
                _require(identity not in definitions, "Competing canonical requirements")
                definitions[identity] = digest
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    _require(set(definitions) == set(REQUIREMENTS), "Missing canonical requirement definition")
    return definitions


def test_function_digest(repo: Path, commit: str, node: str, blob: str) -> str:
    import ast

    path, separator, function = node.partition("::")
    _require(bool(separator) and path.startswith("tests/") and function.startswith("test_")
             and "::" not in function, "Unsupported exact test node")
    raw = resolve_evidence(repo, commit, {"path": path, "blob": blob})
    text = raw.decode("utf-8")
    name = function.split("[", 1)[0]
    found = [n for n in ast.parse(text).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    _require(len(found) == 1, "Unknown or competing test definition")
    segment = ast.get_source_segment(text, found[0])
    _require(segment is not None, "Missing test code")
    return sha256(str(segment).encode("utf-8")).hexdigest()


def _assemble_receipt(
    repo: Path, execution: ExecutionArtifactPin, mapping: RequirementMappingPin,
    github: GitHubReader, context: SchemaContext,
) -> dict[str, Any]:
    _require(type(mapping) is RequirementMappingPin and mapping.state == "ACTIVE",
             "Missing, stale or candidate-selected requirement authority")
    plan = parse_record(_pinned_bytes(mapping.path, mapping.sha256))
    _require(canonical_digest(plan) == mapping.canonical_sha256, "Mapping canonical digest mismatch")
    _exact_object(plan, {"format", "subject", "requirements", "invalidators", "gate_domains",
                         "limitations", "historical_review_blob", "historical_limitations_sha256"})
    _require(plan["format"] == "ATIS_STAGE3_REQUIREMENT_MAPPING_V1"
             and plan["subject"] == {"commit": context.commit, "tree": context.tree},
             "Wrong mapping subject")
    load_schema(context, "assessment")
    capture = _authenticate_execution(repo, execution, github)
    _require(capture["dependencies"] == _tree_dependencies(repo, context.commit),
             "Stale execution dependencies")
    nodes = _observed_nodes(capture)
    definitions = requirement_definitions(repo, context.commit)
    dependencies = {r["path"]: r["blob"] for r in capture["dependencies"]}
    _require(isinstance(plan["requirements"], list)
             and [r.get("id") for r in plan["requirements"] if isinstance(r, dict)]
             == list(REQUIREMENTS), "Incomplete or duplicate requirement allocation")
    tests = []
    allocated: set[str] = set()
    code_digests: dict[str, str] = {}
    for raw in plan["requirements"]:
        row = _exact_object(raw, {"id", "definition_sha256", "sources", "direct", "adversarial"})
        _require(row["definition_sha256"] == definitions[row["id"]], "Changed requirement definition")
        _require(isinstance(row["sources"], list) and bool(row["sources"]), "Missing source mapping")
        for ref in row["sources"]:
            _exact_object(ref, {"path", "blob"})
            _require(dependencies.get(ref["path"]) == ref["blob"], "Changed source identity")
        seen: set[str] = set()
        for group, kind in (("direct", "DIRECT"), ("adversarial", "ADVERSARIAL")):
            _require(isinstance(row[group], list) and bool(row[group]), "Missing " + kind + " mapping")
            for raw_test in row[group]:
                item = _exact_object(raw_test, {"nodeid", "code_sha256", "rationale"})
                node = item["nodeid"]
                _require(isinstance(node, str) and node not in seen
                         and isinstance(item["rationale"], str) and bool(item["rationale"].strip()),
                         "Duplicate or unsupported semantic allocation")
                _passed_node(nodes, node)
                path = node.split("::", 1)[0]
                _require(path in dependencies, "Missing test dependency")
                if node not in code_digests:
                    code_digests[node] = test_function_digest(repo, context.commit, node, dependencies[path])
                _require(code_digests[node] == item["code_sha256"], "Test code substitution")
                seen.add(node)
                allocated.add(node)
                tests.append({"requirement": row["id"], "nodeid": node, "kind": kind,
                              "outcome": "PASS", "path": path, "blob": dependencies[path]})
    historical = parse_record(_git(repo, "show", context.commit + ":" + HISTORY))
    conditions = {(p["id"], condition): p["requirements"] for p in historical["properties"]
                  for condition in p["invalidation_conditions"]}
    invalidators = []
    seen_conditions: set[tuple[str, str]] = set()
    _require(isinstance(plan["invalidators"], list), "Missing property assessment")
    for raw in plan["invalidators"]:
        item = _exact_object(raw, {"property", "condition", "state", "requirements", "nodes", "rationale"})
        key = (item["property"], item["condition"])
        _require(key in conditions and key not in seen_conditions
                 and item["requirements"] == conditions[key] and item["state"] == "CURRENT",
                 "Stale or unsupported invalidator")
        _mapped_witness(item, allocated, nodes)
        seen_conditions.add(key)
        invalidators.append({k: item[k] for k in ("property", "condition", "state", "requirements")})
    _require(seen_conditions == set(conditions), "Missing invalidator witness")
    domains = _exact_object(plan["gate_domains"], set(DOMAINS))
    for raw in domains.values():
        item = _exact_object(raw, {"state", "nodes", "rationale"})
        _require(item["state"] == "PASS", "Gate domain not established")
        _mapped_witness(item, allocated, nodes)
    review = parse_record(_git(repo, "show", context.commit + ":" + REVIEW))
    _require(plan["historical_review_blob"] == dependencies[REVIEW]
             and plan["historical_limitations_sha256"] == canonical_digest(review["provenance_limitations"]),
             "Wrong historical review attribution")
    _require(isinstance(plan["limitations"], list) and bool(plan["limitations"])
             and all(isinstance(x, str) and x.strip() for x in plan["limitations"]),
             "Missing evidence limitations")
    receipt = {
        "producer": f"{REPOSITORY}/actions/runs/{execution.run_id}/attempts/{execution.attempt}/jobs/{execution.job_id}",
        "subject": capture["subject"], "requirements": list(REQUIREMENTS),
        "dependencies": capture["dependencies"], "tests": tests, "invalidators": invalidators,
        "gate_domains": {domain: "PASS" for domain in DOMAINS}, "result": "PASS",
        "limitations": plan["limitations"], "historical_review_blob": plan["historical_review_blob"],
        "historical_limitations_sha256": plan["historical_limitations_sha256"],
    }
    schema = load_schema(context, "assessment")["$defs"]["executionReceipt"]
    _require(Draft202012Validator(schema).is_valid(receipt), "Receipt schema rejection")
    return receipt


def _mapped_witness(
    item: dict[str, Any], allocated: set[str], nodes: dict[str, dict[str, Any]],
) -> None:
    _require(isinstance(item["rationale"], str) and bool(item["rationale"].strip())
             and isinstance(item["nodes"], list) and bool(item["nodes"])
             and all(isinstance(n, str) for n in item["nodes"])
             and len(item["nodes"]) == len(set(item["nodes"])) , "Unattributed semantic witness")
    for node in item["nodes"]:
        _require(node in allocated, "Unmapped semantic witness")
        _passed_node(nodes, node)


def admit_execution_receipt(
    repo: Path, execution: ExecutionArtifactPin, mapping: RequirementMappingPin,
    github: GitHubReader, context: SchemaContext, output: Path,
) -> AdmittedReceipt:
    """Assemble only independently authenticated observations; never owner PASS.

    The mapping's classifications/rationales require independent semantic review.
    This function binds that review to exact definitions and executed test code;
    it cannot manufacture semantic review from function names or test counts.
    """
    _require(output.is_absolute() and not output.resolve().is_relative_to(repo.resolve()),
             "Receipt output must be external")
    try:
        receipt = _assemble_receipt(repo, execution, mapping, github, context)
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
        with output.open("xb") as stream:
            stream.write(raw)
    except (OSError, KeyError, TypeError, AttributeError, UnicodeError, SyntaxError) as error:
        raise AcceptanceError("Unusable receipt inputs or output") from error
    return AdmittedReceipt(EvidencePin(output, sha256(raw).hexdigest(), receipt["producer"]),
                           canonical_digest(receipt), execution, mapping)


def revalidate_admitted_receipt(
    repo: Path, receipt: AdmittedReceipt, github: GitHubReader, context: SchemaContext,
) -> None:
    _require(type(receipt) is AdmittedReceipt, "Missing independent receipt admission")
    try:
        expected = _assemble_receipt(repo, receipt.execution, receipt.mapping, github, context)
        actual = _receipt(receipt.evidence, context)
    except (KeyError, TypeError, AttributeError, UnicodeError, SyntaxError) as error:
        raise AcceptanceError("Malformed admitted evidence") from error
    _require(actual == expected and canonical_digest(actual) == receipt.canonical_sha256,
             "Receipt differs from authenticated execution")


def _execution_authority(
    repo: Path, assessment_path: Path, verdict_path: Path,
    authority: RuntimeAuthority, github: GitHubReader,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _verify_protected_authority(repo, authority, github)
    context = authority.schema_context
    _require(type(context) is SchemaContext and context.repository == repo.resolve()
             and context.commit == authority.protected_commit and context.tree == authority.protected_tree,
             "Wrong execution schema authority")
    assessment = load_record(assessment_path, "assessment", authority.assessment_sha256, context)
    verdict = load_record(verdict_path, "verdict", authority.verdict_sha256, context)
    subject = {"commit": authority.protected_commit, "tree": authority.protected_tree}
    _require(assessment["subject"] == verdict["subject"] == subject
             and verdict["assessment_sha256"] == authority.assessment_sha256,
             "Wrong execution subject or assessment")
    _require(bool(verdict.get("record_id")) and verdict["state"] == "PASS"
             and verdict["claimed_event_id"] in (None, authority.owner_event.event_id),
             "Owner verdict is not attributable PASS")
    expected = {"kind": "OWNER_VERDICT", "record_id": verdict["record_id"], "subject": subject,
                "assessment_sha256": authority.assessment_sha256, "verdict_sha256": authority.verdict_sha256,
                "state": "PASS", "amendment": AMENDMENT, "independent_review": "DEFERRED_BY_OWNER"}
    authenticate_owner_event(github, authority.owner_event, expected)
    _require(type(authority.gate_event) is OwnerEventPin, "Missing separate gate authorization")
    assert authority.gate_event is not None
    authenticate_owner_event(github, authority.gate_event,
                             {**expected, "kind": "GATE_EXECUTION", "state": "AUTHORIZED"})
    return assessment, expected


def execute_authorized_gate(
    repo: Path, assessment_path: Path, verdict_path: Path,
    authority: RuntimeAuthority | None, github: GitHubReader,
    execution: GateExecutionContext | None,
) -> dict[str, Any]:
    """Execute the existing gate only under separate owner authorization.

    This function is never called by capture, receipt preparation or readiness.
    Its result grants no closure, Stage4 or trading authority. A single immutable
    output per authenticated gate event prevents replay in the trusted runtime.
    """
    import os

    if authority is None or execution is None:
        return {"gate_id": "GATE-S03-01", "s3req036": "NOT_ESTABLISHED",
                "owner_acceptance": "NO_APPROVED_OWNER_VERDICT", "gate_result": "NOT_ESTABLISHED",
                "reasons": ["NO_INDEPENDENT_EXECUTION_AUTHORITY"]}
    _require(type(execution) is GateExecutionContext, "Candidate-selected gate context")
    assessment, _ = _execution_authority(repo, assessment_path, verdict_path, authority, github)
    context = authority.schema_context
    assert context is not None and authority.gate_event is not None
    code = resolve_evidence(repo, authority.protected_commit,
                            {"path": EXECUTOR_PATH, "blob": execution.executor_blob})
    try:
        _require(Path(__file__).read_bytes() == code, "Unpinned executing implementation")
    except OSError as error:
        raise AcceptanceError("Unavailable executor identity") from error
    directory = execution.output_directory
    _require(directory.is_absolute() and directory.is_dir()
             and not directory.resolve().is_relative_to(repo.resolve()), "Untrusted gate output context")
    identity = canonical_digest({"gate": "GATE-S03-01", "event": authority.gate_event.event_id,
                                 "repository": REPOSITORY})
    output = directory / ("gate-s03-01-" + identity + ".json")
    schema = load_schema(context, "gate-execution")
    # Reserve once before technical evaluation; interruptions remain nonpositive.
    try:
        stream = output.open("xb+")
    except OSError as error:
        raise AcceptanceError("Replayed or unavailable gate execution") from error
    result = {
        "record_kind": "GATE_S03_01_TERMINAL_V1", "gate_id": "GATE-S03-01",
        "execution_id": identity, "subject": assessment["subject"],
        "executor_blob": execution.executor_blob, "assessment_sha256": authority.assessment_sha256,
        "verdict_sha256": authority.verdict_sha256,
        "receipt_sha256s": [p.sha256 for p in authority.evidence],
        "owner_event_id": authority.owner_event.event_id, "gate_event_id": authority.gate_event.event_id,
        "s3req036": "NOT_ESTABLISHED", "owner_acceptance": "AUTHENTICATED_OWNER_PASS",
        "gate_result": "INSUFFICIENT", "reasons": ["EXECUTION_INTERRUPTED_OR_INCOMPLETE"],
        "independent_review": "DEFERRED_BY_OWNER", "authority_denials": list(TERMINAL_DENIALS),
    }
    with stream:
        stream.write(json.dumps(result, sort_keys=True).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        try:
            _require(bool(execution.receipts) and tuple(r.evidence for r in execution.receipts)
                     == authority.evidence, "Receipt admission does not match runtime pins")
            for receipt in execution.receipts:
                revalidate_admitted_receipt(repo, receipt, github, context)
            if assessment["material_blockers"] or any(
                r["state"] in {"INVALIDATED", "CONTRADICTORY"} for r in assessment["requirements"]
            ):
                _execution_authority(repo, assessment_path, verdict_path, authority, github)
                result["gate_result"] = "FAIL"
                result["reasons"] = ["MATERIAL_BLOCKER_OR_NEGATIVE_REQUIREMENT"]
            else:
                ready = evaluate_authenticated_gate(repo, assessment_path, verdict_path, authority, github)
                _require(ready["gate_result"] == "READY_FOR_AUTHORIZED_EXECUTION",
                         "Gate prerequisites not satisfied")
                # Reauthenticate at completion, including the distinct gate event.
                _execution_authority(repo, assessment_path, verdict_path, authority, github)
                for receipt in execution.receipts:
                    revalidate_admitted_receipt(repo, receipt, github, context)
                result["s3req036"] = "CURRENT_EVIDENCE_COMPLETE"
                result["gate_result"] = "PASS"
                result["reasons"] = ["ALL_EXISTING_GATE_PREDICATES_VERIFIED"]
        except AcceptanceError as error:
            result["gate_result"] = "INSUFFICIENT"
            result["reasons"] = [str(error)]
        _require(Draft202012Validator(schema).is_valid(result), "Terminal result schema rejection")
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return result

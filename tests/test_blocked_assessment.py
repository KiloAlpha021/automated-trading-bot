"""Verify the M1 assessment state using fresh, non-authoritative evidence."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELECTION = [
    "tests/test_money.py",
    "tests/test_quantity.py",
    "tests/test_alerts.py",
    "tests/test_health.py",
    "tests/test_metrics.py",
    "tests/test_diagnostics.py",
    "tests/test_secret_provider_contract.py",
    "tests/test_initial_migration_specification.py",
    "tests/test_event_storage_design.py",
    "tests/test_decision.py::test_expiry_across_utc_date_boundary",
    "tests/test_clock.py",
    "tests/test_timestamp.py",
    "tests/test_closure_security.py",
    "tests/test_architecture.py",
    "tests/test_specification_provenance.py",
    "tests/test_closure_traceability.py::test_required_parents_and_explicit_dispositions",
    "tests/test_closure_traceability.py::test_identity_precedence_rejects_changed_evidence",
]

INPUT_HASH_MODEL = "sha256:utf8:newlines-lf:v1"
M1_SOURCE_PATHS_SHA256 = "4da0cfdf9cd6dafbd55864cb91f1faabee4bff56e40669ae8a814d497c920e2c"
M1_HISTORICAL_REVISION = "841d903cb5670777f8ce23fe8433a0c5dd7dac49"


def _checkout_independent_text_sha256(path: Path) -> str:
    return _canonical_text_sha256(path.read_bytes())


def _canonical_text_sha256(raw: bytes) -> str:
    if b"\x00" in raw:
        raise ValueError("Evidence input is not text")
    text = raw.decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _historical_m1_source_hashes(root: Path, recorded: dict[str, str]) -> dict[str, str]:
    """Recheck the frozen M1 source paths without enrolling later-stage files."""
    paths = sorted(path for path in recorded if path.startswith("src/"))
    inventory = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
    assert inventory == M1_SOURCE_PATHS_SHA256, "Frozen M1 source inventory changed"
    result = {}
    for relative in paths:
        path = root / relative
        assert path.is_file() and not path.is_symlink(), f"Missing or replaced M1 input: {relative}"
        result[relative] = _checkout_independent_text_sha256(path)
    return result


def _historical_git_input_hashes(recorded: dict[str, str]) -> dict[str, str]:
    paths = sorted(recorded)
    source_paths = [path for path in paths if path.startswith("src/")]
    inventory = hashlib.sha256("\n".join(source_paths).encode("utf-8")).hexdigest()
    assert inventory == M1_SOURCE_PATHS_SHA256, "Frozen M1 source inventory changed"
    resolved = subprocess.run(
        ["git", "rev-parse", f"{M1_HISTORICAL_REVISION}^{{tree}}"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert resolved, "Historical M1 tree is unavailable"
    result = {}
    for relative in paths:
        raw = subprocess.run(
            ["git", "show", f"{M1_HISTORICAL_REVISION}:{relative}"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout
        if b"\x00" in raw:
            raise ValueError(f"Evidence input is not text: {relative}")
        normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        result[relative] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return result


# Finite display-ID migration for the unchanged credential rejection test.
# Canonical UTF-8 text binds parameter expressions, values, assertions and scanner.
_CREDENTIAL_TEST_SHA256 = "fdef0e7e0c2393ad69b04b97706215ead9c8ae3d40ec0f8ee10cbe7c63c4dd88"
_CURRENT_TO_HISTORICAL_CASE = {
    "9243ed4383dfb1138d5814d95d4c0c78b48e9bb4ddf2d5625559ea0253d90c1c": "439438c522e38137d1500cd7bd57636e04ca1c87415a8e472885872b68ba95e3",
    "e647cc81036420127f9b0f49cc0e61dbd7d27abeffeb0567811da448a1b41854": "143ff55b3a0d9fe37f57655ea8eb822252bd0c4a012a0865aaf574589d22a4ca",
    "1a5fd8368487b9d6aa1d17f3428436980c8d1508d45bf96b6897f8cd692bc5e8": "cb0c26c7892d08d29d705bd0535af7efaf6267e67cc1301c8ad0d3c676f51986",
    "c955757437f289057b02314c0f5f30d73489db55af78169cadfa4fbc99f177d3": "98dd517bae95f72602662cacf4bffe8712842e938cf22ae224081ecdada7882b",
    "e0e628afc57ae9d9a7bd0f2a7b917f27f2d08e9d65a6e6c7817c6870d5bdeed8": "2106bc30487df3343f66f9bc703a7e0cfebf17c94d062ddf247a59d6701510c4",
    "f154fcc0c0753ae1e48d48fb7b707ec4671192d91c0f5590f6d1a3516060de96": "2f47fb83f4eaa2ae1411c572e165ae04506d6f9bdfc847185fd950922c559cf8",
    "cf4c7119762433b578320ce10f29984c8bf2db305282a03ab933e25b54109b64": "5196274c106b8dc182b58d6e9fc36e4d2746d28ae0a8a2866a488bc07c793c47",
    "440ab74c203ac4b009b8831bb3dcdebd15792ed1175ed5338f9448bf53020605": "0b2a9451883494e6b215129fee33b90470a00b3ae2d5012d3d9626b8bada20b3",
}


def _historical_case_compatibility(cases, recorded, source):
    assert _canonical_text_sha256(source) == _CREDENTIAL_TEST_SHA256, "Credential test semantics changed"
    mapping = _CURRENT_TO_HISTORICAL_CASE
    assert len(mapping) == len(set(mapping.values())) == 8, "Invalid finite mapping"
    historical = {identity for identity, outcome in recorded}
    assert len(historical) == len(recorded) == 329, "Historical inventory changed"
    assert all(outcome == "passed" for _, outcome in recorded)
    assert set(mapping.values()) <= historical
    expected = (historical - set(mapping.values())) | set(mapping)
    identities = [identity for identity, _ in cases]
    assert len(identities) == len(set(identities)) == 329, "Missing or duplicate case"
    assert set(identities) == expected, "Unknown or missing current identity"
    assert all(outcome == "passed" for _, outcome in cases), "Nonpassing current outcome"
    # Outcomes are copied unchanged; only the eight named identities translate.
    translated = sorted([[mapping.get(identity, identity), outcome] for identity, outcome in cases])
    assert translated == sorted(recorded), "Historical correspondence failed"
    return translated


def _identity_compatibility_output() -> Path:
    configured = os.environ.get("ATIS_EXECUTION_OUTPUT")
    assert configured, "Controlled external evidence output is required"
    output = Path(configured)
    assert output.is_absolute(), "Evidence output must be absolute"
    resolved = output.resolve()
    repository = ROOT.resolve()
    assert resolved != repository and repository not in resolved.parents, (
        "Evidence output must be outside the repository"
    )
    output.mkdir(parents=True, exist_ok=True)
    return output


def _write_identity_compatibility_record(record: dict) -> tuple[Path, str]:
    output = _identity_compatibility_output()
    raw = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".json", prefix="m1-identity-compatibility-",
        dir=output, delete=False,
    ) as attribution:
        attribution.write(raw)
        path = Path(attribution.name)
    digest = hashlib.sha256(raw).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        digest + "\n", encoding="ascii", newline="\n",
    )
    return path, digest


def _run_evidence() -> dict:
    with tempfile.TemporaryDirectory(prefix="m1-evidence-") as directory:
        report = Path(directory) / "execution.xml"
        run = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", *SELECTION,
                f"--junitxml={report}", f"--basetemp={Path(directory) / 'pytest'}",
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        assert report.is_file(), "Evidence execution produced no report"
        cases = []
        for case in ET.parse(report).iter("testcase"):
            outcome = "passed"
            for kind in ("failure", "error", "skipped"):
                if case.find(kind) is not None:
                    outcome = kind
                    break
            # Parameter labels can contain synthetic secrets; retain identity by hash.
            identity = case.get("classname", "") + "::" + case.get("name", "")
            cases.append([hashlib.sha256(identity.encode()).hexdigest(), outcome])
        assert run.returncode == 0, "Underlying evidence tests failed"
        assert cases and all(outcome == "passed" for _, outcome in cases)
    recorded = json.loads((ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text())
    raw_cases = sorted(cases)
    source = (ROOT / "tests/test_closure_security.py").read_bytes()
    cases = _historical_case_compatibility(
        raw_cases, recorded["current_evidence"]["test_cases"],
        source,
    )
    # Retain raw identities/outcomes and the explicit mapping, without secret labels.
    _write_identity_compatibility_record({
        "raw_current_cases": raw_cases,
        "historical_cases": cases,
        "mapping": _CURRENT_TO_HISTORICAL_CASE,
        "source_sha256": _canonical_text_sha256(source),
        "source_raw_sha256": hashlib.sha256(source).hexdigest(),
        "source_hash_model": INPUT_HASH_MODEL,
        "classification": "LOCAL_OBSERVED_IDENTITY_COMPATIBILITY",
    })
    source_hashes = _historical_git_input_hashes(
        recorded["current_evidence"]["input_sha256"]
    )
    return {
        "selection": SELECTION,
        "test_cases": sorted(cases),
        "passed": len(cases), "failed": 0,
        "input_hash_model": INPUT_HASH_MODEL,
        "input_sha256": source_hashes,
    }


def _verify(checkpoint: dict, traceability: dict, actual: dict) -> None:
    assert checkpoint["complete"] is True
    assert checkpoint["assessment_status"] == "COMPLETED"
    assert checkpoint["authorization"] == "NONE"
    assert checkpoint["current_evidence"] == actual, "Recorded evidence differs from fresh execution"
    assert checkpoint["historical_assessments"], "Historical observations lost"
    rows = {row["id"]: row for row in traceability["rows"]}
    assessed = {row["id"]: row for row in checkpoint["records"]}
    # Atomic mappings are complete; the separate owner-decision gap still blocks closure.
    unresolved = sorted(
        {key for key, row in rows.items()
         if row["disposition"] == "UNRESOLVED"
         or (row["disposition"] == "M1" and row["residual_gap"])}
        | {key for key, row in assessed.items()
           if row["status"] in {"INSUFFICIENT_EVIDENCE", "NOT_IMPLEMENTED"}}
    )
    assert checkpoint["unresolved_traceability_ids"] == unresolved, "Unresolved blocker omitted"
    assert unresolved == []
    expected_unassessed = sorted(rows.keys() - assessed.keys())
    assert checkpoint["unassessed_requirement_ids"] == expected_unassessed
    assert checkpoint["parent_set_is_assessed"] is (not expected_unassessed)
    assert assessed["IMP-001-M1-01"]["status"] == "VERIFIED"
    assert assessed["IMP-001-M1-14"]["status"] == "VERIFIED"
    assert all(
        record["status"] == "VERIFIED"
        for key, record in assessed.items()
        if key not in unresolved
    )
    assert checkpoint["stop_reason"] == ""
    assert (ROOT / "docs/m1-closure/closure-manifest.json").is_file()
    assert checkpoint["reclosure"]["status"] == "COMPLETED"
    assert checkpoint["reclosure"]["stage2_authorized"] is False
    assert checkpoint["post_closure_audit"]["classification"] == "REOPEN_M1"
    assert checkpoint["post_closure_audit"]["resolved_findings_provenance"]["status"] == "REPAIRED"
    assert checkpoint["post_closure_audit"]["active_blockers"] == []


@pytest.fixture(scope="module")
def evidence():
    return _run_evidence()


def _inputs():
    return (
        json.loads((ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text()),
        json.loads((ROOT / "docs/m1-closure/traceability.json").read_text()),
    )


def test_blocked_checkpoint_matches_current_evidence(evidence):
    _verify(*_inputs(), evidence)


def test_identity_compatibility_evidence_is_external_and_digest_bound(evidence):
    output = _identity_compatibility_output()
    records = sorted(output.glob("m1-identity-compatibility-*.json"))
    assert len(records) == 1
    record = records[0]
    raw = record.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert record.with_suffix(record.suffix + ".sha256").read_text(
        encoding="ascii"
    ) == digest + "\n"
    decoded = json.loads(raw)
    assert decoded["classification"] == "LOCAL_OBSERVED_IDENTITY_COMPATIBILITY"
    assert decoded["historical_cases"] == evidence["test_cases"]
    assert not (ROOT / "execution-evidence").exists()


def test_evidence_input_hash_is_checkout_independent_and_content_sensitive(tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    changed = tmp_path / "changed.txt"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")
    changed.write_bytes(b"first\nchanged\n")

    assert _checkout_independent_text_sha256(lf) == _checkout_independent_text_sha256(crlf)
    assert _checkout_independent_text_sha256(lf) != _checkout_independent_text_sha256(changed)


@pytest.mark.parametrize("content,error", [(b"bad\x00text", ValueError), (b"\xff", UnicodeDecodeError)])
def test_evidence_input_hash_rejects_non_text(content, error, tmp_path):
    evidence_input = tmp_path / "input"
    evidence_input.write_bytes(content)
    with pytest.raises(error):
        _checkout_independent_text_sha256(evidence_input)


def test_frozen_m1_source_inventory_survives_stage2_without_losing_binding(tmp_path):
    recorded = _inputs()[0]["current_evidence"]["input_sha256"]
    expected = {key: value for key, value in recorded.items() if key.startswith("src/")}
    assert {
        key: value for key, value in _historical_git_input_hashes(recorded).items()
        if key.startswith("src/")
    } == expected
    for relative in expected:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(subprocess.run(
            ["git", "show", f"{M1_HISTORICAL_REVISION}:{relative}"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout)

    assert _historical_m1_source_hashes(tmp_path, recorded) == expected

    later = tmp_path / "src/automated_trading_bot/domain/versioning.py"
    later.write_text("# Stage 2, not an M1 input\n", encoding="utf-8")
    assert _historical_m1_source_hashes(tmp_path, recorded) == expected

    changed = tmp_path / next(iter(expected))
    original = changed.read_bytes()
    changed.write_bytes(original + b"\n# changed M1 input\n")
    assert _historical_m1_source_hashes(tmp_path, recorded) != expected

    changed.unlink()
    with pytest.raises(AssertionError, match="Missing or replaced M1 input"):
        _historical_m1_source_hashes(tmp_path, recorded)

    renamed = changed.with_name(changed.name + ".renamed")
    changed.write_bytes(original)
    changed.rename(renamed)
    with pytest.raises(AssertionError, match="Missing or replaced M1 input"):
        _historical_m1_source_hashes(tmp_path, recorded)

    changed.write_bytes(b"# Stage 2 replacement at an M1 path\n")
    assert _historical_m1_source_hashes(tmp_path, recorded) != expected

    expanded = dict(recorded)
    expanded["src/automated_trading_bot/domain/versioning.py"] = _checkout_independent_text_sha256(later)
    with pytest.raises(AssertionError, match="Frozen M1 source inventory changed"):
        _historical_m1_source_hashes(tmp_path, expanded)


@pytest.mark.parametrize("mutation", ["completion", "blocker", "test_count", "test_outcome", "unassessed", "authority", "unverified"])
def test_blocked_checkpoint_rejects_false_observation(evidence, mutation):
    checkpoint, traceability = _inputs()
    checkpoint = copy.deepcopy(checkpoint)
    if mutation == "completion":
        checkpoint["complete"] = False
    elif mutation == "blocker":
        checkpoint["post_closure_audit"]["active_blockers"] = [{"id": "false"}]
    elif mutation == "test_count":
        checkpoint["current_evidence"]["passed"] += 1
    elif mutation == "test_outcome":
        checkpoint["current_evidence"]["test_cases"][0][1] = "skipped"
    elif mutation == "unassessed":
        checkpoint["unassessed_requirement_ids"] = ["IMP-033-M1-03"] if not checkpoint["unassessed_requirement_ids"] else []
    elif mutation == "authority":
        checkpoint["authorization"] = "APPROVED"
    else:
        target = next(row for row in checkpoint["records"] if row["id"] == "IMP-001-M1-14")
        target["status"] = "INSUFFICIENT_EVIDENCE"
    with pytest.raises(AssertionError):
        _verify(checkpoint, traceability, evidence)


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate", "unknown", "failure",
                                      "error", "skipped", "parameter", "assertion"])
def test_finite_historical_identity_compatibility(mutation):
    checkpoint_path = ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json"
    before = checkpoint_path.read_bytes()
    recorded = json.loads(before)["current_evidence"]["test_cases"]
    inverse = {old: current for current, old in _CURRENT_TO_HISTORICAL_CASE.items()}
    raw = [[inverse.get(identity, identity), outcome] for identity, outcome in recorded]
    source = (ROOT / "tests/test_closure_security.py").read_bytes()
    if mutation == "missing":
        raw.pop()
    elif mutation == "duplicate":
        raw[-1] = raw[0][:]
    elif mutation == "unknown":
        raw[0][0] = "0" * 64
    elif mutation in {"failure", "error", "skipped"}:
        raw[0][1] = mutation
    elif mutation == "parameter":
        source = source.replace(b"synthetic-value-only", b"changed-value-only")
    elif mutation == "assertion":
        source = source.replace(b"pytest.raises", b"changed.raises")
    original = copy.deepcopy(raw)
    if mutation == "none":
        assert _historical_case_compatibility(raw, recorded, source) == sorted(recorded)
        assert sum(identity in _CURRENT_TO_HISTORICAL_CASE for identity, _ in raw) == 8
    else:
        with pytest.raises(AssertionError):
            _historical_case_compatibility(raw, recorded, source)
    assert raw == original
    assert checkpoint_path.read_bytes() == before


@pytest.mark.parametrize("representation", ["lf", "crlf", "mutation", "encoding", "nul"])
def test_credential_source_canonical_identity(representation):
    recorded = json.loads(
        (ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text()
    )["current_evidence"]["test_cases"]
    inverse = {old: current for current, old in _CURRENT_TO_HISTORICAL_CASE.items()}
    cases = [[inverse.get(identity, identity), outcome] for identity, outcome in recorded]
    source = (ROOT / "tests/test_closure_security.py").read_bytes().replace(b"\r\n", b"\n")
    if representation == "crlf":
        source = source.replace(b"\n", b"\r\n")
    elif representation == "mutation":
        source += b"# changed content\n"
    elif representation == "encoding":
        source += b"\xff"
    elif representation == "nul":
        source += b"\x00"
    original = copy.deepcopy(cases)
    if representation in {"lf", "crlf"}:
        assert _historical_case_compatibility(cases, recorded, source) == sorted(recorded)
        assert _canonical_text_sha256(source) == _CREDENTIAL_TEST_SHA256
        expected_raw = (_CREDENTIAL_TEST_SHA256 if representation == "lf" else
                        "3dae4846f64fb4d2df4b6c14966cd7401007ccd04527f3fd15935ed4a10434fa")
        assert hashlib.sha256(source).hexdigest() == expected_raw
    else:
        with pytest.raises((AssertionError, ValueError)):
            _historical_case_compatibility(cases, recorded, source)
    assert cases == original


def test_finite_mapping_mutation_rejected(monkeypatch):
    recorded = json.loads(
        (ROOT / "docs/m1-closure/atomic-assessment-checkpoint.json").read_text()
    )["current_evidence"]["test_cases"]
    inverse = {old: current for current, old in _CURRENT_TO_HISTORICAL_CASE.items()}
    cases = [[inverse.get(identity, identity), outcome] for identity, outcome in recorded]
    source = (ROOT / "tests/test_closure_security.py").read_bytes()
    monkeypatch.setitem(_CURRENT_TO_HISTORICAL_CASE, next(iter(_CURRENT_TO_HISTORICAL_CASE)), "0" * 64)
    with pytest.raises(AssertionError):
        _historical_case_compatibility(cases, recorded, source)

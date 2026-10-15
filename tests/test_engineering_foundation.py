from __future__ import annotations

import builtins
import importlib.metadata
import re
from pathlib import Path
import subprocess
import hashlib
import json
import os
import sys

import pytest
from jsonschema import ValidationError

from scripts import m1_engineering_foundation as foundation


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2"
SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0"
UPLOAD_ARTIFACT = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"


def _configuration() -> tuple[str, str, str, str]:
    return (
        (ROOT / ".python-version").read_text().strip(),
        (ROOT / "pyproject.toml").read_text(),
        (ROOT / "scripts/bootstrap.ps1").read_text(),
        (ROOT / ".github/workflows/m1-engineering-foundation.yml").read_text(),
    )


def _validate(version: str, project: str, bootstrap: str, workflow: str) -> None:
    assert version == "3.12.10"
    assert 'requires-python = "==3.12.10"' in project
    assert 'select = ["E4", "E7", "E9", "F"]' in project
    assert "strict = true" in project
    assert 'files = ["src/automated_trading_bot"]' in project
    for required in (
        "sys.version_info[:3] == (3, 12, 10)", '"-m", "venv"', "--require-hashes",
        "--no-cache-dir", "https://pypi.org/simple", "--no-build-isolation",
        "--no-deps", "m1_engineering_foundation.py",
    ):
        assert required in bootstrap
    assert ".venv\\Scripts" not in bootstrap
    assert not re.search(r"[A-Z]:\\Users\\", bootstrap)
    assert "name: m1-engineering-foundation" in workflow
    assert re.search(r"(?m)^permissions:\r?\n  contents: read\r?$", workflow)
    assert not re.search(r"(?m)^\s+[a-z-]+: write\s*(?:#.*)?$", workflow)
    assert re.search(
        rf"(?m)^      - uses: {re.escape(CHECKOUT)}\r?\n"
        r"        with:\r?\n"
        r"          fetch-depth: 0\r?$",
        workflow,
    )
    assert f"uses: {SETUP_PYTHON}" in workflow
    assert f"uses: {UPLOAD_ARTIFACT}" in workflow
    assert "python-version: 3.12.10" in workflow
    assert "./scripts/bootstrap.ps1" in workflow
    assert "continue-on-error" not in workflow


def test_engineering_foundation_contract() -> None:
    _validate(*_configuration())


@pytest.mark.parametrize(
    ("part", "old", "new"),
    [
        (0, "3.12.10", "3.12.9"),
        (1, 'requires-python = "==3.12.10"', 'requires-python = ">=3.12"'),
        (2, "--require-hashes", "--no-warn-script-location"),
        (2, '"-m", "venv"', '"-m", "pip"'),
        (2, "--no-build-isolation", "--use-pep517"),
        (3, "python-version: 3.12.10", "python-version: 3.13"),
        (3, "fetch-depth: 0", "fetch-depth: 1"),
        (3, "          fetch-depth: 0\n", ""),
        (3, "permissions:\n  contents: read\n", ""),
        (3, "contents: read", "contents: write"),
        (3, CHECKOUT, "actions/checkout@v4"),
        (3, SETUP_PYTHON, "actions/setup-python@v5"),
        (3, UPLOAD_ARTIFACT, "actions/upload-artifact@v4"),
        (3, "name: m1-engineering-foundation", "name: checks"),
        (3, "run: ./scripts/bootstrap.ps1", "continue-on-error: true\n        run: ./scripts/bootstrap.ps1"),
    ],
)
def test_engineering_foundation_rejects_weakened_contract(part: int, old: str, new: str) -> None:
    values = list(_configuration())
    values[part] = values[part].replace(old, new)
    with pytest.raises(AssertionError):
        _validate(*values)


def test_lock_rejects_unhashed_or_machine_local_dependencies() -> None:
    lock = (ROOT / "requirements-dev.lock").read_text()
    lines = [line for line in lock.splitlines() if line and not line.startswith("#")]
    assert all(" --hash=sha256:" in line for line in lines)
    assert not any(token in lock for token in ("file:", "-e ", "C:\\Users\\"))


def _validate_runner(runner: str) -> None:
    assert "check=True" in runner
    for invocation in (
        '"ruff"', '"mypy"', '"coverage", "run"', '"pytest"', '"pip_audit"'
    ):
        assert invocation in runner
    assert '"coverage", "xml"' in runner
    assert "tests/test_closure_security.py" in runner


def test_required_control_invocations_are_fatal() -> None:
    runner = (ROOT / "scripts/m1_engineering_foundation.py").read_text()
    _validate_runner(runner)
    assert '"--basetemp", RUN_ENV["TEMP"]' in runner


@pytest.mark.parametrize(
    ("required", "replacement"),
    [
        ('"ruff"', '"python"'),
        ('"mypy"', '"python"'),
        ('"coverage", "run"', '"coverage", "erase"'),
        ('"pip_audit"', '"pip"'),
        ('"tests/test_closure_security.py"', '"tests/test_smoke.py"'),
    ],
)
def test_engineering_runner_rejects_removed_required_control(
    required: str, replacement: str
) -> None:
    runner = (ROOT / "scripts/m1_engineering_foundation.py").read_text()
    weakened = runner.replace(required, replacement)
    assert weakened != runner
    with pytest.raises(AssertionError):
        _validate_runner(weakened)


def test_bootstrap_checks_every_native_command() -> None:
    bootstrap = (ROOT / "scripts/bootstrap.ps1").read_text()
    assert '. (Join-Path $PSScriptRoot "checked-native.ps1")' in bootstrap
    for step in (
        "Python version verification",
        "virtual-environment creation",
        "hash-locked dependency installation",
        "project installation",
        "M1 engineering-foundation checks",
    ):
        assert f'Invoke-CheckedNative "{step}"' in bootstrap
    assert bootstrap.count("Invoke-CheckedNative ") == 5


def test_checked_native_preserves_exit_code_and_stops(tmp_path: Path) -> None:
    marker = tmp_path / "incorrectly-continued"
    harness = tmp_path / "harness.ps1"
    helper = ROOT / "scripts/checked-native.ps1"
    harness.write_text(
        f'. "{helper}"\n'
        'Invoke-CheckedNative "controlled failure" "cmd.exe" @("/c", "exit 23")\n'
        f'Set-Content -LiteralPath "{marker}" -Value "continued"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(harness),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23
    assert "controlled failure" in result.stderr
    assert "exit code 23" in result.stderr
    assert not marker.exists()


def _installed_versions() -> dict[str, str]:
    return {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower():
        distribution.version
        for distribution in importlib.metadata.distributions()
        if re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        != "automated-trading-bot"
    }


def test_exact_dependency_identity_failure_stops_verification(monkeypatch) -> None:
    monkeypatch.setattr(foundation, "locked_versions", lambda: {})
    with pytest.raises(RuntimeError, match="installed dependency identity mismatch"):
        foundation.verify_environment()


def test_pip_check_failure_stops_verification(monkeypatch) -> None:
    monkeypatch.setattr(foundation, "locked_versions", _installed_versions)

    def fail(_command):
        raise subprocess.CalledProcessError(9, "pip check")

    monkeypatch.setattr(foundation, "run", fail)
    with pytest.raises(subprocess.CalledProcessError) as error:
        foundation.verify_environment()
    assert error.value.returncode == 9


def test_import_failure_stops_verification(monkeypatch) -> None:
    monkeypatch.setattr(foundation, "locked_versions", _installed_versions)
    monkeypatch.setattr(foundation, "run", lambda _command: None)
    original_import = builtins.__import__

    def fail_import(name, *args, **kwargs):
        if name == "automated_trading_bot":
            raise ImportError("controlled import failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    with pytest.raises(ImportError, match="controlled import failure"):
        foundation.verify_environment()


@pytest.mark.parametrize(
    "failure_marker",
    ["ruff", "mypy", "coverage run", "coverage report", "coverage xml", "pip_audit", "test_closure_security.py"],
)
def test_engineering_check_stops_at_failed_required_control(
    tmp_path: Path, monkeypatch, failure_marker: str
) -> None:
    _runner_fixture(tmp_path, monkeypatch)
    calls: list[str] = []

    def controlled_run(command: list[str]) -> None:
        rendered = " ".join(command)
        calls.append(rendered)
        if failure_marker.replace("_", "-") in rendered.replace("_", "-"):
            raise subprocess.CalledProcessError(17, command)

    monkeypatch.setattr(foundation, "run", controlled_run)
    with pytest.raises(subprocess.CalledProcessError) as error:
        foundation.check()
    assert error.value.returncode == 17
    assert failure_marker.replace("_", "-") in calls[-1].replace("_", "-")


@pytest.mark.parametrize(
    "failure",
    [
        "ruff violation", "mypy violation", "pytest failure",
        "coverage report failure", "pip check failure", "known vulnerability",
        "advisory retrieval failure",
    ],
)
def test_required_control_failure_propagates(monkeypatch, failure: str) -> None:
    def fail(*args, **kwargs):
        raise foundation.subprocess.CalledProcessError(1, failure)

    monkeypatch.setattr(foundation.subprocess, "run", fail)
    with pytest.raises(foundation.subprocess.CalledProcessError):
        foundation.run([failure])


# Reporting tests exercise actual pytest reports, never synthetic owner approval.
def test_execution_report_preserves_failed_and_skipped_nodes(tmp_path) -> None:
    import json
    import os
    import sys

    source = tmp_path / "test_capture_fixture.py"
    source.write_text(
        "import pytest\ndef test_pass(): assert True\n"
        "def test_fail(): assert False, 'controlled negative fixture'\n"
        "@pytest.mark.skip(reason='controlled skip')\ndef test_skip(): pass\n",
        encoding="utf-8",
    )
    output = tmp_path / "node-output"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["ATIS_EXECUTION_OUTPUT"] = str(output)
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", str(source), "--rootdir", str(ROOT), "-q", "-p",
         "scripts.m1_engineering_foundation", "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    capture = json.loads((output / "capture.json").read_text(encoding="utf-8"))
    assert capture["exit_code"] == 1 and capture["session_finished"] is True
    assert len(capture["collected"]) == 3
    assert {r["outcome"] for r in capture["reports"]} == {"passed", "failed", "skipped"}
    assert any(r["nodeid"].endswith("::test_fail") and r["outcome"] == "failed"
               for r in capture["reports"])
    assert (output / "events.jsonl").is_file()
    assert capture["producer"]["path"] == "scripts/m1_engineering_foundation.py"
    # Subprocesses retain native pytest failure; a report never changes exit1 to0.


def test_execution_report_retains_collection_failure(tmp_path) -> None:
    import json
    import os
    import sys

    source = tmp_path / "test_broken_fixture.py"
    source.write_text("raise RuntimeError('controlled collection error')\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    output = tmp_path / "collection-output"
    env["ATIS_EXECUTION_OUTPUT"] = str(output)
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", str(source), "--rootdir", str(ROOT), "-q", "-p",
         "scripts.m1_engineering_foundation", "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    capture = json.loads((output / "capture.json").read_text(encoding="utf-8"))
    assert capture["collection_errors"] and capture["exit_code"] == 2
    assert capture["session_finished"] is True


def test_capture_detects_working_byte_changes_and_refuses_overwrite(tmp_path, monkeypatch) -> None:
    dependency = {"path": "src/example.py", "blob": "a" * 40}
    monkeypatch.setattr(foundation, "working_snapshot", lambda: ([dependency], False))
    monkeypatch.setattr(foundation, "installed_source_identity", lambda _: {})
    reporter = foundation.NodeCapture(tmp_path / "captured")
    assert reporter.record["committed_subject"] is False
    with pytest.raises(FileExistsError):
        foundation.NodeCapture(tmp_path / "captured")
    monkeypatch.setattr(foundation, "working_snapshot",
                        lambda: ([{**dependency, "blob": "b" * 40}], False))
    reporter.pytest_sessionfinish(None, 0)
    assert reporter.record["workspace_stable"] is False
    assert reporter.record["exit_code"] == 0  # Never invent a pytest exit code.


def test_workflow_preserves_execution_artifacts_on_failure() -> None:
    workflow = _configuration()[3]
    assert "if: always()" in workflow
    assert "path: ${{ runner.temp }}/atis-engineering-evidence/" in workflow
    assert "include-hidden-files: true" in workflow
    assert "            coverage.xml" not in workflow
    assert 'GIT_CONFIG_KEY_0: core.autocrlf' in workflow
    assert workflow.count("        env:") == 2
    assert "continue-on-error" not in workflow
    runner = (ROOT / "scripts/m1_engineering_foundation.py").read_text()
    assert '"-p", "scripts.m1_engineering_foundation"' in runner
    assert "pytest.main(" not in runner  # Reporting plugin, same mandatory runner.


AUTHORIZED = [
    ".github/workflows/m1-engineering-foundation.yml",
    "docs/m1-closure/M1-engineering-foundation-scope-clarification.md",
    "docs/m1-closure/engineering-foundation-execution-contract.schema.json",
    "scripts/bootstrap.ps1",
    "scripts/m1_engineering_foundation.py",
    "tests/test_blocked_assessment.py",
    "tests/test_engineering_foundation.py",
]


def _contract(tmp_path: Path) -> dict:
    lock = ROOT / "requirements-dev.lock"
    return {
        "contract_version": "ATIS_ENGINEERING_FOUNDATION_EXECUTION_V1",
        "task_id": "TEST-TASK",
        "owner_authorization_ref": "TEST-AUTHORIZATION",
        "objective": "Test the engineering contract",
        "repository": {
            "owner": "KiloAlpha021", "name": "automated-trading-bot",
            "protected_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "protected_tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
        },
        "authorized_changed_paths": AUTHORIZED,
        "dependency_inventory": [{
            "producer": "candidate", "consumer": "runner", "interface": "files",
            "applicability": "test", "validation": "test", "evidence": "capture",
        }],
        "candidate": {"mode": "PRE_SEAL_WORKING_TREE", "expected_parent": None},
        "environment": {
            "python_version": [3, 12, 10], "dependency_lock_path": "requirements-dev.lock",
            "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "pytest_state_directory": str(tmp_path / "pytest-state"),
        },
        "validation_profile": {"profile_id": "AUTO", "controls": list(foundation.MANDATORY_CONTROLS)},
        "evidence": {"output_directory": str(tmp_path / "evidence"), "no_overwrite": True,
                     "canonical_serialization": "JSON_SORTED_COMPACT_UTF8_SHA256"},
        "pre_seal_requirements": ["EXACT_BYTES"],
        "post_seal_requirements": ["COMMITTED_SUBJECT"],
        "downstream_certification": {"obligations": ["TRUSTED_EVALUATION", "OWNER_ACCEPTANCE"]},
        "authority_exclusions": ["NO_TRADING_AUTHORITY"],
    }


def test_execution_contract_schema_accepts_complete_contract(tmp_path: Path) -> None:
    schema = json.loads(foundation.SCHEMA.read_text(encoding="utf-8"))
    foundation.Draft202012Validator(schema).validate(_contract(tmp_path))


@pytest.mark.parametrize("field", [
    "task_id", "owner_authorization_ref", "objective", "repository",
    "authorized_changed_paths", "dependency_inventory", "candidate", "environment",
    "validation_profile", "evidence", "pre_seal_requirements", "post_seal_requirements",
    "downstream_certification", "authority_exclusions",
])
def test_contract_rejects_missing_mandatory_fields(tmp_path: Path, field: str) -> None:
    value = _contract(tmp_path)
    value.pop(field)
    schema = json.loads(foundation.SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        foundation.Draft202012Validator(schema).validate(value)


def test_contract_rejects_unknown_and_unsafe_paths(tmp_path: Path) -> None:
    schema = json.loads(foundation.SCHEMA.read_text(encoding="utf-8"))
    validator = foundation.Draft202012Validator(schema)
    unknown = _contract(tmp_path)
    unknown["unknown"] = True
    assert list(validator.iter_errors(unknown))
    unsafe = _contract(tmp_path)
    unsafe["authorized_changed_paths"] = ["../escape"]
    assert list(validator.iter_errors(unsafe))


@pytest.mark.parametrize("mutation", [
    "wrong_commit", "wrong_tree", "wrong_python", "wrong_lock", "incomplete_profile",
    "existing_evidence", "internal_evidence", "relative_pytest", "unauthorized_path",
])
def test_preflight_rejects_invalid_contract_or_environment(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    value = _contract(tmp_path)
    if mutation == "wrong_commit":
        value["repository"]["protected_commit"] = "0" * 40
    elif mutation == "wrong_tree":
        value["repository"]["protected_tree"] = "0" * 40
    elif mutation == "wrong_python":
        value["environment"]["python_version"] = [3, 12, 9]
    elif mutation == "wrong_lock":
        value["environment"]["dependency_lock_sha256"] = "0" * 64
    elif mutation == "incomplete_profile":
        value["validation_profile"]["controls"].pop()
    elif mutation == "existing_evidence":
        Path(value["evidence"]["output_directory"]).mkdir()
    elif mutation == "internal_evidence":
        value["evidence"]["output_directory"] = str(ROOT / "evidence")
    elif mutation == "relative_pytest":
        value["environment"]["pytest_state_directory"] = "relative"
    elif mutation == "unauthorized_path":
        monkeypatch.setattr(foundation, "changed_paths", lambda: ["outside.txt"])
    with pytest.raises(RuntimeError):
        foundation.preflight(value)


def test_preflight_accepts_exact_preseal_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    value = _contract(tmp_path)
    result = foundation.preflight(value)
    assert result["claim"] == "PREFLIGHT_PASS"
    assert result["subject"]["commit"] == value["repository"]["protected_commit"]
    assert result["subject"]["tree"] == value["repository"]["protected_tree"]
    assert result["attribution"]["mode"] == "PRE_SEAL_WORKING_TREE"
    assert {item["path"] for item in result["candidate_snapshot"]} == set(AUTHORIZED)


def test_profile_identity_changes_with_runner_or_lock(monkeypatch) -> None:
    first = foundation.profile_identity("a" * 64)
    second = foundation.profile_identity("b" * 64)
    assert first != second


def test_source_tree_attribution_ignores_hostile_pythonpath(tmp_path: Path, monkeypatch) -> None:
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    contract = _contract(tmp_path)
    environment = foundation.build_run_environment(contract)
    result = foundation._source_attribution(contract, environment)
    assert Path(result["module_origin"]).is_relative_to(ROOT / "src")
    assert environment["PYTHONPATH"] == str((ROOT / "src").resolve())
    foundation.RUN_ENV = None
    monkeypatch.chdir(ROOT)
    foundation.preflight(contract)
    assert foundation.RUN_ENV == environment


def test_failed_preflight_does_not_activate_invalid_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    contract = _contract(tmp_path)
    previous = {"ATIS_EXECUTION_OUTPUT": "preserved"}
    foundation.RUN_ENV = previous
    monkeypatch.setattr(foundation, "_source_attribution",
                        lambda *_args: (_ for _ in ()).throw(RuntimeError("controlled failure")))
    with pytest.raises(RuntimeError, match="controlled failure"):
        foundation.preflight(contract)
    assert foundation.RUN_ENV is previous


def test_candidate_snapshot_detects_present_and_deleted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(foundation, "ROOT", tmp_path)
    present = tmp_path / "engineering-foundation-test-present.tmp"
    try:
        present.write_bytes(b"candidate")
        result = foundation.candidate_snapshot([present.name, "missing.tmp"])
        by_path = {item["path"]: item for item in result}
        assert by_path[present.name]["blob"] and by_path[present.name]["sha256"]
        assert by_path["missing.tmp"]["state"] == "DELETED"
    finally:
        present.unlink(missing_ok=True)


def test_preseal_capture_never_claims_committed_subject(tmp_path: Path, monkeypatch) -> None:
    value = _contract(tmp_path)
    monkeypatch.setattr(foundation, "ACTIVE_CONTRACT", value)
    monkeypatch.setattr(foundation, "PREFLIGHT_RECORD", {
        "attribution": {"mode": "PRE_SEAL_WORKING_TREE"},
        "candidate_snapshot": foundation.candidate_snapshot(AUTHORIZED),
    })
    reporter = foundation.NodeCapture(tmp_path / "capture")
    assert reporter.record["committed_subject"] is False
    assert reporter.record["phase"] == "PRE_SEAL_WORKING_TREE"


def test_postseal_rejects_dirty_or_source_tree_candidate(tmp_path: Path, monkeypatch) -> None:
    value = _contract(tmp_path)
    value["candidate"] = {"mode": "POST_SEAL_IMMUTABLE_COMMIT", "expected_parent": "0" * 40}
    monkeypatch.setattr(foundation, "changed_paths", lambda: ["dirty.py"])
    with pytest.raises(RuntimeError, match="clean committed subject"):
        foundation._source_attribution(value, foundation.build_run_environment(value))


def test_claim_vocabulary_separates_preseal_and_postseal() -> None:
    assert foundation.CLAIMS["PRE_SEAL_WORKING_TREE"] == "PRE_SEAL_VALIDATION_PASS"
    assert foundation.CLAIMS["POST_SEAL_IMMUTABLE_COMMIT"] == "POST_SEAL_CERTIFICATION_PASS"
    assert foundation.CLAIMS["PRE_SEAL_WORKING_TREE"] != foundation.CLAIMS["POST_SEAL_IMMUTABLE_COMMIT"]


def test_bootstrap_and_workflow_carry_postseal_contract() -> None:
    bootstrap = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/m1-engineering-foundation.yml").read_text(encoding="utf-8")
    worktree_blob = subprocess.check_output(
        ["git", "hash-object", "--path=scripts/bootstrap.ps1", "scripts/bootstrap.ps1"],
        cwd=ROOT, text=True,
    ).strip()
    protected_blob = subprocess.check_output(
        ["git", "rev-parse", "HEAD:scripts/bootstrap.ps1"], cwd=ROOT, text=True,
    ).strip()
    assert worktree_blob == protected_blob == "78c82833f387ce94f786f58f93947917076fd61a"
    changed = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--", "scripts/bootstrap.ps1"],
        cwd=ROOT, text=True,
    )
    assert changed == ""
    assert "POST_SEAL_IMMUTABLE_COMMIT" not in bootstrap
    assert "ATIS_HOSTED_POST_SEAL" in workflow
    assert "-Hosted" not in workflow
    assert "atis-engineering-evidence" in workflow


def test_controlled_child_environment_is_complete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile"))
    value = _contract(tmp_path)
    environment = foundation.build_run_environment(value)
    assert environment["ATIS_EXECUTION_OUTPUT"] == str(Path(value["evidence"]["output_directory"]) / "execution-evidence")
    assert environment["TEMP"] == value["environment"]["pytest_state_directory"]
    assert environment["TMP"] == value["environment"]["pytest_state_directory"]
    assert environment["PYTHONPATH"] == str((ROOT / "src").resolve())


def test_controlled_environment_reaches_nested_python(tmp_path: Path) -> None:
    value = _contract(tmp_path)
    environment = foundation.build_run_environment(value)
    completed = subprocess.run(
        [sys.executable, "-c", "import os,json;print(json.dumps({k:os.environ[k] for k in ('ATIS_EXECUTION_OUTPUT','TEMP','TMP','PYTHONPATH')}))"],
        cwd=ROOT, env=environment, check=True, capture_output=True, text=True,
    )
    observed = json.loads(completed.stdout)
    assert observed == {name: environment[name] for name in observed}


def test_full_pytest_command_binds_validated_basetemp(
    tmp_path: Path, monkeypatch
) -> None:
    contract = _runner_fixture(tmp_path, monkeypatch)
    commands: list[list[str]] = []
    def record(command):
        commands.append(command)
        if "coverage" in command and "run" in command:
            _passing_capture(contract)
    monkeypatch.setattr(foundation, "run", record)
    foundation.check()
    pytest_command = next(command for command in commands if "coverage" in command and "run" in command)
    position = pytest_command.index("--basetemp")
    assert pytest_command[position + 1] == contract["environment"]["pytest_state_directory"]
    assert "pytest-of-" not in pytest_command[position + 1]


def test_real_short_root_deep_git_probe(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    foundation._probe_deep_git(root, os.environ.copy())
    assert not (root / "pytest-of-ATIS" / "pytest-999" / "synthetic-git0").exists()


def test_deep_git_probe_fails_closed_when_git_is_missing(tmp_path: Path, monkeypatch) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("controlled missing Git")
    monkeypatch.setattr(foundation.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="deep-path Git capability"):
        foundation._probe_deep_git(tmp_path, os.environ.copy())
    probe_root = tmp_path / "pytest-of-ATIS" / "pytest-999" / "synthetic-git0"
    assert not probe_root.exists()
    assert not any(tmp_path.rglob(".git"))
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_deep_git_probe_preserves_native_failure_details(
    tmp_path: Path, monkeypatch
) -> None:
    error = subprocess.CalledProcessError(
        128, ["git", "add", "--", "payload"], stderr=b"Filename too long\n"
    )
    monkeypatch.setattr(
        foundation.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(RuntimeError) as raised:
        foundation._probe_deep_git(tmp_path, os.environ.copy())
    message = str(raised.value)
    assert "exit=128" in message
    assert "Filename too long" in message
    assert "argv=" in message
    assert "cwd=" in message


def test_disposable_cleanup_rejects_boundary_escape(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="outside its boundary"):
        foundation._remove_disposable_tree(tmp_path, tmp_path)


def test_disposable_cleanup_propagates_unresolved_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "payload").write_text("preserve on failure", encoding="utf-8")
    monkeypatch.setattr(foundation.shutil, "rmtree",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("controlled")))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        foundation._remove_disposable_tree(root, tmp_path)
    assert (root / "payload").is_file()


def test_deep_git_probe_rejects_overlong_root(tmp_path: Path) -> None:
    overlong = tmp_path / ("x" * 240)
    with pytest.raises(RuntimeError, match="deep-path Git capability"):
        foundation._probe_deep_git(overlong, os.environ.copy())


def test_hosted_contract_derivation_rejects_incomplete_context(monkeypatch) -> None:
    monkeypatch.delenv("ATIS_HOSTED_POST_SEAL", raising=False)
    with pytest.raises(RuntimeError, match="hosted execution context is incomplete"):
        foundation.hosted_contract()


def test_hosted_contract_derives_git_identity(tmp_path: Path, monkeypatch) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    values = {
        "ATIS_HOSTED_POST_SEAL": "1",
        "ATIS_EXECUTION_OUTPUT": str(tmp_path / "evidence"),
        "ATIS_PYTEST_STATE": str(tmp_path / "pytest"),
        "GITHUB_REPOSITORY": "KiloAlpha021/automated-trading-bot",
        "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": head,
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "m1-engineering-foundation", "GITHUB_ACTOR": "owner",
        "GITHUB_ACTOR_ID": "327435165",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    contract = foundation.hosted_contract()
    assert contract["candidate"]["mode"] == "POST_SEAL_IMMUTABLE_COMMIT"
    assert contract["repository"]["protected_commit"] == head
    assert contract["evidence"]["output_directory"] == values["ATIS_EXECUTION_OUTPUT"]


def test_hosted_contract_rejects_forged_sha(tmp_path: Path, monkeypatch) -> None:
    values = {
        "ATIS_HOSTED_POST_SEAL": "1", "ATIS_EXECUTION_OUTPUT": str(tmp_path / "e"),
        "ATIS_PYTEST_STATE": str(tmp_path / "p"),
        "GITHUB_REPOSITORY": "KiloAlpha021/automated-trading-bot",
        "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": "0" * 40,
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "job",
        "GITHUB_ACTOR": "owner", "GITHUB_ACTOR_ID": "327435165",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="hosted SHA"):
        foundation.hosted_contract()


def _runner_fixture(tmp_path, monkeypatch):
    contract = _contract(tmp_path)
    monkeypatch.setattr(foundation, "ACTIVE_CONTRACT", contract)
    monkeypatch.setattr(foundation, "PREFLIGHT_RECORD", {
        "contract_sha256": hashlib.sha256(foundation.canonical(contract)).hexdigest(),
        "candidate_snapshot": foundation.candidate_snapshot(AUTHORIZED),
        "attribution": {"mode": "PRE_SEAL_WORKING_TREE"},
    })
    monkeypatch.setattr(foundation, "RUN_ENV", foundation.build_run_environment(contract))
    monkeypatch.setattr(foundation, "RUN_REPORT", None)
    monkeypatch.setattr(foundation, "_verify_python", lambda: None)
    monkeypatch.setattr(foundation, "_verify_dependencies", lambda: None)
    monkeypatch.setattr(foundation, "_source_attribution", lambda *_args: {})
    return contract


def _passing_capture(contract):
    output = Path(contract["evidence"]["output_directory"]) / "execution-evidence"
    output.mkdir()
    raw = foundation.canonical({"format": "ATIS_PYTEST_EXECUTION_V1",
        "session_finished": True, "exit_code": 0, "workspace_stable": True,
        "collection_errors": []})
    (output / "capture.json").write_bytes(raw)
    (output / "capture.sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")


@pytest.mark.parametrize("outcome,code,message", [
    ("clean", 0, "No known vulnerabilities found"),
    ("vulnerable", 1, "package 1.0 CVE-fixture"),
    ("network", 7, "advisory retrieval failed: network unavailable"),
])
def test_runner_audit_terminal_evidence(tmp_path, monkeypatch, outcome, code, message):
    contract = _runner_fixture(tmp_path, monkeypatch)
    native = foundation.subprocess.Popen
    commands = []
    def controlled(command, **kwargs):
        if command[0] == "git":
            return native(command, **kwargs)
        commands.append(command)
        is_audit = "pip_audit" in command
        if "coverage" in command and "run" in command:
            _passing_capture(contract)
        # Real native processes and exit codes; no package/network execution.
        return native([sys.executable, "-B", "-c",
            f"import sys;print({message if is_audit else 'fixture pass'!r});sys.exit({code if is_audit else 0})"], **kwargs)
    monkeypatch.setattr(foundation.subprocess, "Popen", controlled)
    if code:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            foundation.check()
        assert raised.value.returncode == code
    else:
        foundation.check()
    root = Path(contract["evidence"]["output_directory"])
    raw = (root / "runner.json").read_bytes()
    record = json.loads(raw)
    assert raw == foundation.canonical(record)
    assert (root / "runner.sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    assert record["terminal"] == ("FAIL" if code else "PASS")
    assert record["initial_candidate"] == record["final_candidate"]
    assert record["initial_dependencies"] == record["final_dependencies"]
    assert [entry["name"] for entry in record["controls"]] == list(foundation.MANDATORY_CONTROLS)
    controls = {entry["name"]: entry for entry in record["controls"]}
    assert controls["FULL_PYTEST"]["status"] == "PASS"
    audit = controls["VULNERABILITY_AUDIT"]
    assert audit["native"][0]["exit_code"] == code
    assert audit["native"][0]["started"] is True
    assert message in (root / audit["native"][0]["stdout"]).read_text()
    argv = audit["native"][0]["argv"]
    cache = Path(argv[argv.index("--cache-dir") + 1])
    assert cache == root / "cache" / "pip-audit" and cache.is_dir()
    assert "--ignore-vuln" not in argv and "--disable-pip" not in argv
    assert controls["CLOSURE_SECURITY"]["status"] == ("NOT_STARTED" if code else "PASS")
    assert record["native_exit_code"] == code
    for name, digest in record["evidence_digests"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    xml = next(cmd for cmd in commands if "coverage" in cmd and "xml" in cmd)
    assert Path(xml[-1]) == root / "coverage.xml"
    assert record["remaining_obligations"] == ["TRUSTED_EVALUATION", "OWNER_ACCEPTANCE"]
    before = raw
    with pytest.raises(FileExistsError):
        foundation.check()
    assert (root / "runner.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["relative", "internal", "existing", "denied", "wrong_version"])
def test_audit_cache_preflight_rejects_before_native(tmp_path, monkeypatch, kind):
    cache = tmp_path / "cache"
    calls = []
    monkeypatch.setattr(foundation, "run", calls.append)
    if kind == "relative":
        cache = Path("relative")
    elif kind == "internal":
        cache = ROOT / "not-created-audit-cache"
    elif kind == "existing":
        cache.mkdir()
        (cache / "historical").write_bytes(b"preserve")
    elif kind == "denied":
        def denied(_path):
            raise PermissionError("controlled inaccessible cache")
        monkeypatch.setattr(foundation, "_probe_directory", denied)
    else:
        monkeypatch.setattr(foundation.importlib.metadata, "version", lambda _: "2.9.0")
    with pytest.raises((RuntimeError, PermissionError)):
        foundation.audit(cache)
    assert calls == []
    if kind == "existing":
        assert (cache / "historical").read_bytes() == b"preserve"


def test_controlled_outputs_are_external_and_override_ambient(tmp_path, monkeypatch):
    for key in ("COVERAGE_FILE", "RUFF_CACHE_DIR", "MYPY_CACHE_DIR", "PYTHONPYCACHEPREFIX"):
        monkeypatch.setenv(key, str(ROOT / "bad-cache"))
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cache-clear")
    contract = _contract(tmp_path)
    env = foundation.build_run_environment(contract)
    for key in ("COVERAGE_FILE", "RUFF_CACHE_DIR", "MYPY_CACHE_DIR", "PYTHONPYCACHEPREFIX",
                "ATIS_EXECUTION_OUTPUT", "TEMP", "TMP"):
        path = Path(env[key])
        assert path.is_absolute() and not path.is_relative_to(ROOT)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


def test_runner_preserves_spawn_failure_without_invented_exit(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    native = foundation.subprocess.Popen
    def unavailable(command, **kwargs):
        if command[0] == "git":
            return native(command, **kwargs)
        raise FileNotFoundError("controlled missing interpreter")
    monkeypatch.setattr(foundation.subprocess, "Popen", unavailable)
    with pytest.raises(FileNotFoundError):
        foundation.check()
    record = json.loads((Path(contract["evidence"]["output_directory"]) / "runner.json").read_bytes())
    control = next(item for item in record["controls"] if item["name"] == "PIP_CONSISTENCY")
    assert control["status"] == "FAIL"
    assert control["native"][0]["started"] is False
    assert control["native"][0]["exit_code"] is None
    assert record["terminal"] == "FAIL"


def test_runner_capture_digest_tampering_fails(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    def fake(command):
        if "coverage" in command and "run" in command:
            _passing_capture(contract)
            path = Path(contract["evidence"]["output_directory"]) / "execution-evidence" / "capture.sha256"
            path.write_text("0" * 64)
    monkeypatch.setattr(foundation, "run", fake)
    with pytest.raises(RuntimeError, match="capture"):
        foundation.check()
    record = json.loads((Path(contract["evidence"]["output_directory"]) / "runner.json").read_bytes())
    assert record["terminal"] == "FAIL"


def test_runner_rejects_candidate_drift(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    def fake(command):
        if "coverage" in command and "run" in command:
            _passing_capture(contract)
        if "pip_audit" in command:
            monkeypatch.setattr(foundation, "candidate_snapshot", lambda _: [{"path": "changed"}])
    monkeypatch.setattr(foundation, "run", fake)
    with pytest.raises(RuntimeError, match="candidate changed"):
        foundation.check()
    record = json.loads((Path(contract["evidence"]["output_directory"]) / "runner.json").read_bytes())
    assert record["terminal"] == "FAIL"
    assert record["initial_candidate"] != record["final_candidate"]


def test_legacy_capture_has_exact_historical_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(foundation, "ACTIVE_CONTRACT", None)
    monkeypatch.setattr(foundation, "PREFLIGHT_RECORD", None)
    reporter = foundation.NodeCapture(tmp_path / "execution-evidence")
    reporter.pytest_sessionfinish(None, 0)
    value = json.loads((tmp_path / "execution-evidence" / "capture.json").read_bytes())
    assert set(value) == {"format", "subject", "producer", "dependencies", "environment",
        "provenance", "collected", "reports", "collection_errors", "session_finished",
        "exit_code", "workspace_stable", "committed_subject"}
    assert value["format"] == "ATIS_PYTEST_EXECUTION_V1"


def test_runner_controlled_native_nonzero_is_not_lost(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    report = foundation.RunnerEvidence(contract, foundation.PREFLIGHT_RECORD)
    monkeypatch.setattr(foundation, "RUN_REPORT", report)
    with pytest.raises(subprocess.CalledProcessError) as error:
        report.control("VULNERABILITY_AUDIT", lambda: foundation.run(
            [sys.executable, "-B", "-c", "raise SystemExit(23)"]))
    assert error.value.returncode == 23
    event = report.entry("VULNERABILITY_AUDIT")["native"][0]
    assert event["exit_code"] == 23


def test_preflight_rejects_overlapping_evidence_and_pytest_state(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    contract = _contract(tmp_path)
    contract["evidence"]["output_directory"] = str(Path(contract["environment"]["pytest_state_directory"]) / "evidence")
    with pytest.raises(RuntimeError, match="disjoint"):
        foundation.preflight(contract)


def test_runner_rejects_preflight_candidate_substitution(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    foundation.PREFLIGHT_RECORD["candidate_snapshot"] = []
    monkeypatch.setattr(foundation, "run", lambda _: None)
    with pytest.raises(RuntimeError, match="attributed preflight"):
        foundation.check()
    record = json.loads((Path(contract["evidence"]["output_directory"]) / "runner.json").read_bytes())
    assert record["terminal"] == "FAIL"
    assert next(e for e in record["controls"] if e["name"] == "RUFF")["status"] == "NOT_STARTED"


@pytest.mark.parametrize("kind,code", [("success", 0), ("child", 23),
    ("runtime", 1), ("spawn", 1), ("interrupt", 130)])
def test_terminal_cli_real_process_mapping(tmp_path, kind, code):
    action = {"success": "return None", "child": "raise subprocess.CalledProcessError(23, ['fixture'])",
              "runtime": "raise RuntimeError('fixture')", "spawn": "raise FileNotFoundError('fixture')",
              "interrupt": "raise KeyboardInterrupt('fixture')"}[kind]
    program = ("import sys,subprocess;sys.path.insert(0," + repr(str(ROOT)) + ");"
               "from scripts import m1_engineering_foundation as f\n"
               "def replacement():\n    " + action + "\nf.main=replacement\nraise SystemExit(f.cli())")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode == code


@pytest.mark.parametrize("kind,code,terminal", [("success", 0, "PASS"),
    ("child", 23, "FAIL"), ("runtime", 1, "FAIL"),
    ("spawn", 1, "FAIL"), ("interrupt", 130, "INTERRUPTED")])
def test_terminal_summary_mapping(tmp_path, monkeypatch, kind, code, terminal):
    contract = _runner_fixture(tmp_path, monkeypatch)
    report = foundation.RunnerEvidence(contract, foundation.PREFLIGHT_RECORD)
    errors = {"success": None, "child": subprocess.CalledProcessError(23, ["fixture"]),
              "runtime": RuntimeError("fixture"), "spawn": FileNotFoundError("fixture"),
              "interrupt": KeyboardInterrupt("fixture")}
    error = errors[kind]
    if error is None:
        report.finish(None)
    else:
        with pytest.raises(type(error)) as caught:
            report.finish(error)
        assert caught.value is error
    raw = (report.root / "runner.json").read_bytes()
    record = json.loads(raw)
    assert record["terminal"] == terminal
    assert type(record["native_exit_code"]) is int
    assert record["native_exit_code"] == code
    assert all(x["status"] == "NOT_STARTED" for x in record["controls"])
    assert raw == foundation.canonical(record)
    assert hashlib.sha256(raw).hexdigest() == (report.root / "runner.sha256").read_text().strip()


@pytest.mark.parametrize("phase", ["digest", "sidecar", "replace"])
@pytest.mark.parametrize("primary", [False, True])
def test_terminal_persistence_failure_never_accepts_success(tmp_path, monkeypatch, phase, primary):
    contract = _runner_fixture(tmp_path, monkeypatch)
    report = foundation.RunnerEvidence(contract, foundation.PREFLIGHT_RECORD)
    error = subprocess.CalledProcessError(23, ["fixture"]) if primary else None
    def denied(*args, **kwargs):
        raise PermissionError("terminal persistence fixture")
    if phase == "digest":
        monkeypatch.setattr(foundation, "file_sha256", denied)
    elif phase == "sidecar":
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path.name == "runner.sha256":
                return denied()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", write)
    else:
        monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(subprocess.CalledProcessError if primary else PermissionError) as caught:
        report.finish(error)
    if primary:
        assert caught.value is error
        assert isinstance(caught.value.__cause__, PermissionError)
    assert json.loads((report.root / "runner.json").read_bytes())["terminal"] == "RUNNING"


def test_terminal_primary_survives_secondary_reconciliation(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    report = foundation.RunnerEvidence(contract, foundation.PREFLIGHT_RECORD)
    def drift():
        raise RuntimeError("secondary drift")
    monkeypatch.setattr(report, "reconcile", drift)
    primary = subprocess.CalledProcessError(23, ["fixture"])
    with pytest.raises(subprocess.CalledProcessError) as caught:
        report.finish(primary)
    assert caught.value is primary
    record = json.loads((report.root / "runner.json").read_bytes())
    assert record["native_exit_code"] == 23
    assert record["identity_error"] == "secondary drift"


def test_terminal_forced_process_termination_leaves_incomplete(tmp_path):
    root = tmp_path / "forced-evidence"
    program = ("import sys,os;sys.path.insert(0," + repr(str(ROOT)) + ");"
        "from scripts import m1_engineering_foundation as f;"
        "f.candidate_snapshot=lambda _: [];f.working_snapshot=lambda: ([],False);"
        "r=f.RunnerEvidence({'evidence':{'output_directory':" + repr(str(root)) +
        "},'authorized_changed_paths':[],'downstream_certification':{'obligations':[]}},{});"
        "os._exit(9)")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode == 9, result.stderr
    record = json.loads((root / "runner.json").read_bytes())
    assert record["terminal"] == "RUNNING"
    assert record["native_exit_code"] is None


def test_terminal_interrupt_during_wait_preserves_child_result(tmp_path, monkeypatch):
    contract = _runner_fixture(tmp_path, monkeypatch)
    report = foundation.RunnerEvidence(contract, foundation.PREFLIGHT_RECORD)
    class InterruptedChild:
        calls = 0
        def wait(self):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt("controlled interrupt")
            return 17
        def terminate(self):
            pass
    monkeypatch.setattr(foundation.subprocess, "Popen", lambda *a, **k: InterruptedChild())
    with pytest.raises(KeyboardInterrupt) as caught:
        report.control("PIP_CONSISTENCY", lambda: report.native(["fixture"]))
    with pytest.raises(KeyboardInterrupt):
        report.finish(caught.value)
    record = json.loads((report.root / "runner.json").read_bytes())
    assert record["terminal"] == "INTERRUPTED"
    assert record["native_exit_code"] == 130
    entry = next(x for x in record["controls"] if x["name"] == "PIP_CONSISTENCY")
    assert entry["native"][0]["exit_code"] == 17
    assert next(x for x in record["controls"] if x["name"] == "RUFF")["status"] == "NOT_STARTED"

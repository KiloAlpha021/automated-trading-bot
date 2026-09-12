from __future__ import annotations

import builtins
import importlib.metadata
import re
from pathlib import Path
import subprocess

import pytest

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
    _validate_runner((ROOT / "scripts/m1_engineering_foundation.py").read_text())


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
def test_engineering_check_stops_at_failed_required_control(monkeypatch, failure_marker: str) -> None:
    monkeypatch.setattr(foundation, "verify_environment", lambda: None)
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

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import m1_engineering_foundation as foundation


ROOT = Path(__file__).resolve().parents[1]


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
        "sys.version_info[:3] == (3, 12, 10)", "-m venv", "--require-hashes",
        "--no-cache-dir", "https://pypi.org/simple", "--no-build-isolation",
        "--no-deps", "m1_engineering_foundation.py",
    ):
        assert required in bootstrap
    assert ".venv\\Scripts" not in bootstrap
    assert not re.search(r"[A-Z]:\\Users\\", bootstrap)
    assert "name: m1-engineering-foundation" in workflow
    assert re.search(
        r"(?m)^      - uses: actions/checkout@v4\r?\n"
        r"        with:\r?\n"
        r"          fetch-depth: 0\r?$",
        workflow,
    )
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
        (2, "-m venv", "# no isolation"),
        (2, "--no-build-isolation", "--use-pep517"),
        (3, "python-version: 3.12.10", "python-version: 3.13"),
        (3, "fetch-depth: 0", "fetch-depth: 1"),
        (3, "          fetch-depth: 0\n", ""),
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


def test_required_control_invocations_are_fatal() -> None:
    runner = (ROOT / "scripts/m1_engineering_foundation.py").read_text()
    assert "check=True" in runner
    for invocation in ('"ruff"', '"mypy"', '"coverage"', '"pytest"', '"pip_audit"'):
        assert invocation in runner
    assert '"coverage", "xml"' in runner
    assert "tests/test_closure_security.py" in runner


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

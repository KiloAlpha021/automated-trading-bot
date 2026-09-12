"""Run the repository-contained M1 engineering foundation checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 12, 10)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def locked_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / "requirements-dev.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, version = line.split()[0].split("==", 1)
            result[name] = version
            if "--hash=sha256:" not in line:
                raise RuntimeError(f"unhashed lock entry: {name}")
    return result


def verify_environment() -> None:
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise RuntimeError("M1 requires CPython 3.12.10 exactly")
    expected = locked_versions()
    actual = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
        if re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        != "automated-trading-bot"
    }
    if actual != expected:
        raise RuntimeError(f"installed dependency identity mismatch: {actual} != {expected}")
    run([sys.executable, "-m", "pip", "check"])
    __import__("automated_trading_bot")


def check() -> None:
    verify_environment()
    run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"])
    run([sys.executable, "-m", "mypy", "src/automated_trading_bot"])
    run([
        sys.executable, "-m", "coverage", "run", "--source=automated_trading_bot",
        "-m", "pytest", "-q",
    ])
    run([sys.executable, "-m", "coverage", "report", "--show-missing"])
    run([sys.executable, "-m", "coverage", "xml", "-o", "coverage.xml"])
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    audit_version = importlib.metadata.version("pip-audit")
    print(json.dumps({"pip_audit_version": audit_version, "observed_at": observed}))
    run([sys.executable, "-m", "pip_audit", "--local"])
    run([sys.executable, "-m", "pytest", "-q", "tests/test_closure_security.py"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "check"), default="check", nargs="?")
    args = parser.parse_args()
    verify_environment() if args.command == "verify" else check()

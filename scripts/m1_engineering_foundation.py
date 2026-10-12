"""Run the repository-contained M1 engineering foundation checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha1, sha256
import importlib.metadata
import os
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


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
        "-m", "pytest", "-q", "-p", "scripts.m1_engineering_foundation",
    ])
    run([sys.executable, "-m", "coverage", "report", "--show-missing"])
    run([sys.executable, "-m", "coverage", "xml", "-o", "coverage.xml"])
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    audit_version = importlib.metadata.version("pip-audit")
    print(json.dumps({"pip_audit_version": audit_version, "observed_at": observed}))
    run([sys.executable, "-m", "pip_audit", "--local"])
    run([sys.executable, "-m", "pytest", "-q", "tests/test_closure_security.py"])


# This is a pytest reporting plugin, not another test executor. The mandatory
# coverage/pytest invocation above remains the sole execution of the full suite.
def _git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(ROOT), *args])


def _blob(raw: bytes) -> str:
    return sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def working_snapshot() -> tuple[list[dict[str, str]], bool]:
    dependencies = []
    committed = True
    for entry in _git_bytes("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode().split()
        path = name.decode("utf-8")
        target = ROOT / path
        if kind != "blob" or mode not in {"100644", "100755"} or target.is_symlink():
            raise RuntimeError("Unsupported execution dependency")
        blob = _blob(target.read_bytes())
        dependencies.append({"path": path, "blob": blob})
        committed = committed and blob == expected
    # A new source/config file may affect imports despite not being in HEAD.
    extra = _git_bytes("ls-files", "--others", "--exclude-standard", "--",
                       "src", "tests", "scripts", "docs", ".github")
    return dependencies, committed and not bool(extra.strip())


def installed_source_identity(dependencies: list[dict[str, str]]) -> dict:
    import automated_trading_bot

    location = Path(automated_trading_bot.__file__).resolve().parent
    expected = {r["path"]: r["blob"] for r in dependencies
                if r["path"].startswith("src/automated_trading_bot/")
                and r["path"].endswith(".py")}
    actual = {"src/automated_trading_bot/" + p.relative_to(location).as_posix():
              _blob(p.read_bytes()) for p in location.rglob("*.py")}
    return {
        "module_origin": str(location),
        "installed_wheel": "site-packages" in location.parts,
        "module_sources_match": actual == expected,
        "python": ".".join(str(n) for n in sys.version_info[:3]),
        "distributions": {
            re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version
            for d in importlib.metadata.distributions()
            if re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower()
            != "automated-trading-bot"
        },
    }


class NodeCapture:
    """Retain observed facts even on native failure; confer no approval.

    Local uncommitted candidates retain their working-file identities but are
    explicitly ineligible as evidence of execution of a protected commit.
    """

    def __init__(self, output: Path):
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        dependencies, committed = working_snapshot()
        self.record = {
            "format": "ATIS_PYTEST_EXECUTION_V1",
            "subject": {
                "commit": _git_bytes("rev-parse", "HEAD").decode().strip(),
                "tree": _git_bytes("rev-parse", "HEAD^{tree}").decode().strip(),
            },
            "producer": {"path": "scripts/m1_engineering_foundation.py",
                         "blob": _blob(Path(__file__).read_bytes())},
            "dependencies": dependencies,
            "environment": installed_source_identity(dependencies),
            "provenance": {key: os.environ.get(variable, "") for key, variable in (
                ("repository", "GITHUB_REPOSITORY"), ("event", "GITHUB_EVENT_NAME"),
                ("ref", "GITHUB_REF"), ("sha", "GITHUB_SHA"),
                ("run_id", "GITHUB_RUN_ID"), ("attempt", "GITHUB_RUN_ATTEMPT"),
                ("job", "GITHUB_JOB"))},
            "collected": [], "reports": [], "collection_errors": [],
            "session_finished": False, "exit_code": None,
            "workspace_stable": False, "committed_subject": committed,
        }
        self._write()

    def _write(self) -> None:
        raw = json.dumps(self.record, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
        temporary = self.output / "capture.incomplete"
        temporary.write_bytes(raw)
        temporary.replace(self.output / "capture.json")

    def pytest_collection_finish(self, session) -> None:
        self.record["collected"] = [item.nodeid for item in session.items]
        self._write()

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.record["collection_errors"].append({"nodeid": report.nodeid,
                                                       "outcome": report.outcome})
            self._write()

    def pytest_runtest_logreport(self, report) -> None:
        event = {"nodeid": report.nodeid, "when": report.when,
                 "outcome": report.outcome, "xfail": hasattr(report, "wasxfail")}
        self.record["reports"].append(event)
        # Incremental facts survive interruption even without sessionfinish.
        with (self.output / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus) -> None:
        current, committed = working_snapshot()
        self.record["workspace_stable"] = current == self.record["dependencies"]
        self.record["committed_subject"] = self.record["committed_subject"] and committed
        self.record["session_finished"] = True
        self.record["exit_code"] = int(exitstatus)
        self._write()
        raw = (self.output / "capture.json").read_bytes()
        (self.output / "capture.sha256").write_text(sha256(raw).hexdigest() + "\n",
                                                  encoding="ascii")


def pytest_configure(config) -> None:
    output = Path(os.environ.get("ATIS_EXECUTION_OUTPUT", str(ROOT / "execution-evidence")))
    config.pluginmanager.register(NodeCapture(output), "atis-node-evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "check"), default="check", nargs="?")
    args = parser.parse_args()
    verify_environment() if args.command == "verify" else check()

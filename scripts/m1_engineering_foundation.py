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
import shutil
import stat
import subprocess
import sys
import traceback
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 12, 10)
SCHEMA = ROOT / "docs/m1-closure/engineering-foundation-execution-contract.schema.json"
MANDATORY_CONTROLS = (
    "PYTHON_3_12_10", "HASH_LOCKED_DEPENDENCIES", "PIP_CONSISTENCY",
    "CANDIDATE_ATTRIBUTION", "RUFF", "STRICT_MYPY", "FULL_PYTEST",
    "COVERAGE", "VULNERABILITY_AUDIT", "CLOSURE_SECURITY",
    "NATIVE_FAILURE_PROPAGATION", "EXECUTION_CAPTURE",
    "CANDIDATE_SNAPSHOT_RECONCILIATION",
)
CLAIMS = {
    "PRE_SEAL_WORKING_TREE": "PRE_SEAL_VALIDATION_PASS",
    "POST_SEAL_IMMUTABLE_COMMIT": "POST_SEAL_CERTIFICATION_PASS",
}
ACTIVE_CONTRACT: dict[str, Any] | None = None
PREFLIGHT_RECORD: dict[str, Any] | None = None
RUN_ENV: dict[str, str] | None = None
RUN_REPORT: RunnerEvidence | None = None


def run(command: list[str]) -> None:
    if RUN_REPORT is None:
        subprocess.run(command, cwd=ROOT, env=RUN_ENV, check=True)
        return
    RUN_REPORT.native(command)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError("contract must be an existing absolute file")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    value = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
    return cast(dict[str, Any], value)


def _decode_z(raw: bytes) -> list[str]:
    try:
        return [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as error:
        raise RuntimeError("non-UTF-8 Git path") from error


def changed_paths() -> list[str]:
    raw = _git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = _decode_z(raw)
    paths: list[str] = []
    index = 0
    while index < len(entries):
        record = entries[index]
        if len(record) < 4 or record[2] != " ":
            raise RuntimeError("malformed Git porcelain record")
        status, path = record[:2], record[3:]
        paths.append(path)
        if "R" in status or "C" in status:
            index += 1
            if index >= len(entries):
                raise RuntimeError("truncated Git rename/copy record")
            paths.append(entries[index])
        index += 1
    return sorted(set(paths))


def candidate_snapshot(paths: list[str]) -> list[dict[str, str]]:
    result = []
    for relative in sorted(paths):
        target = ROOT / relative
        if target.is_file() and not target.is_symlink():
            raw = target.read_bytes()
            result.append({"path": relative, "state": "PRESENT", "sha256": sha256(raw).hexdigest(),
                           "blob": _blob(raw)})
        elif not target.exists():
            result.append({"path": relative, "state": "DELETED", "sha256": "", "blob": ""})
        else:
            raise RuntimeError(f"unsupported candidate path: {relative}")
    return result


def _probe_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f"atis-probe-{os.getpid()}"
    moved = probe.with_suffix(".moved")
    probe.write_bytes(b"probe")
    list(path.iterdir())
    probe.replace(moved)
    if moved.read_bytes() != b"probe":
        raise RuntimeError(f"directory probe failed: {path}")
    moved.unlink()


def profile_identity(lock_sha256: str) -> str:
    value = {"controls": MANDATORY_CONTROLS, "runner_blob": _blob(Path(__file__).read_bytes()),
             "pyproject_blob": _blob((ROOT / "pyproject.toml").read_bytes()),
             "lock_sha256": lock_sha256,
             "closure_security_blob": _blob((ROOT / "tests/test_closure_security.py").read_bytes())}
    return sha256(canonical(value)).hexdigest()


def build_run_environment(contract: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    output = Path(contract["evidence"]["output_directory"])
    env["ATIS_EXECUTION_OUTPUT"] = str(output / "execution-evidence")
    env["COVERAGE_FILE"] = str(output / "coverage" / ".coverage")
    env["RUFF_CACHE_DIR"] = str(output / "cache" / "ruff")
    env["MYPY_CACHE_DIR"] = str(output / "cache" / "mypy")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(output / "cache" / "python")
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    env["ATIS_RUN_CONTRACT"] = str(output / "contract.json")
    env["ATIS_RUN_PREFLIGHT"] = str(output / "preflight.json")
    state = contract["environment"]["pytest_state_directory"]
    env["TEMP"] = state
    env["TMP"] = state
    if contract["candidate"]["mode"] == "PRE_SEAL_WORKING_TREE":
        env["PYTHONPATH"] = str((ROOT / "src").resolve())
    required = ("ATIS_EXECUTION_OUTPUT", "TEMP", "TMP")
    if any(not env.get(name) for name in required):
        raise RuntimeError("controlled child environment is incomplete")
    return env


def _source_attribution(contract: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    mode = contract["candidate"]["mode"]
    if mode == "PRE_SEAL_WORKING_TREE":
        source = (ROOT / "src").resolve()
        if env.get("PYTHONPATH") != str(source):
            raise RuntimeError("SOURCE_TREE environment identity mismatch")
        # -I ignores ambient PYTHONPATH; insert only the bound source root.
        probe = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             f"import sys,json;sys.path.insert(0,{str(source)!r});import automated_trading_bot as m;print(json.dumps({{'origin':m.__file__}}))"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )
        origin = Path(json.loads(probe.stdout)["origin"]).resolve()
        if source not in origin.parents:
            raise RuntimeError("SOURCE_TREE module origin mismatch")
        return {"mode": mode, "source_root": str(source), "module_origin": str(origin)}
    dependencies, committed = working_snapshot()
    identity = installed_source_identity(dependencies)
    if changed_paths() or not committed:
        raise RuntimeError("POST_SEAL requires a clean committed subject")
    if not identity["installed_wheel"] or not identity["module_sources_match"]:
        raise RuntimeError("installed candidate does not match committed source")
    return {"mode": mode, **identity}


def _probe_deep_git(path: Path, env: dict[str, str]) -> None:
    probe_root = path / "pytest-of-ATIS" / "pytest-999" / "synthetic-git0"
    repository = probe_root / "repository"
    payload = b"atis-deep-path-probe\n"
    relative = Path("docs/stage3/s3-gd-024-property-disposition.json")
    try:
        repository.mkdir(parents=True)
        commands = (
            ["git", "init", "-q"],
            ["git", "config", "user.name", "ATIS Disposable Probe"],
            ["git", "config", "user.email", "atis-probe.invalid@example.invalid"],
        )
        for command in commands:
            subprocess.run(command, cwd=repository, env=env, check=True,
                           capture_output=True)
        target = repository / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        subprocess.run(["git", "add", "--", relative.as_posix()], cwd=repository,
                       env=env, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "disposable deep-path probe"],
                       cwd=repository, env=env, check=True, capture_output=True)
        observed = subprocess.check_output(
            ["git", "show", f"HEAD:{relative.as_posix()}"], cwd=repository, env=env
        )
        if observed != payload:
            raise RuntimeError("deep-path Git probe returned different bytes")
    except (OSError, subprocess.SubprocessError) as error:
        detail = ""
        if isinstance(error, subprocess.CalledProcessError):
            stderr = error.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            detail = (
                f": argv={error.cmd!r}; cwd={repository}; "
                f"exit={error.returncode}; stderr={stderr!r}"
            )
        raise RuntimeError(
            "deep-path Git capability is not established" + detail
        ) from error
    finally:
        if probe_root.exists():
            _remove_disposable_tree(probe_root, path)
        if probe_root.exists():
            raise RuntimeError("deep-path Git probe cleanup failed")


def _remove_disposable_tree(root: Path, boundary: Path) -> None:
    """Remove an owned disposable tree without crossing its declared boundary."""
    resolved_root = root.resolve()
    resolved_boundary = boundary.resolve()
    if resolved_root == resolved_boundary or resolved_boundary not in resolved_root.parents:
        raise RuntimeError("disposable cleanup target is outside its boundary")

    def make_writable_and_retry(function: Any, path: str, _error: BaseException) -> None:
        resolved_path = Path(path).resolve()
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            raise RuntimeError("disposable cleanup escaped its owned root")
        os.chmod(resolved_path, os.stat(resolved_path).st_mode | stat.S_IWRITE)
        function(path)

    try:
        shutil.rmtree(resolved_root, onexc=make_writable_and_retry)
    except (OSError, RuntimeError) as error:
        raise RuntimeError("deep-path Git probe cleanup failed") from error


def hosted_contract() -> dict[str, Any]:
    required = {
        "ATIS_HOSTED_POST_SEAL", "ATIS_EXECUTION_OUTPUT", "ATIS_PYTEST_STATE",
        "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_ACTOR", "GITHUB_ACTOR_ID",
    }
    missing = sorted(name for name in required if not os.environ.get(name))
    if missing or os.environ.get("ATIS_HOSTED_POST_SEAL") != "1":
        raise RuntimeError(f"hosted execution context is incomplete: {missing}")
    if os.environ["GITHUB_REPOSITORY"] != "KiloAlpha021/automated-trading-bot":
        raise RuntimeError("hosted repository identity mismatch")
    head = _git_bytes("rev-parse", "HEAD").decode().strip()
    if os.environ["GITHUB_SHA"] != head:
        raise RuntimeError("hosted SHA does not equal checked-out HEAD")
    tree = _git_bytes("rev-parse", "HEAD^{tree}").decode().strip()
    parent = _git_bytes("rev-parse", "HEAD^").decode().strip()
    paths = _decode_z(_git_bytes("diff", "--name-only", "-z", parent, head))
    lock_digest = file_sha256(ROOT / "requirements-dev.lock")
    contract: dict[str, Any] = {
        "contract_version": "ATIS_ENGINEERING_FOUNDATION_EXECUTION_V1",
        "task_id": f"GITHUB-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}",
        "owner_authorization_ref": (
            f"PROTECTED_GITHUB_EVENT-{os.environ['GITHUB_EVENT_NAME']}-"
            f"{os.environ['GITHUB_ACTOR_ID']}-{head}"
        ),
        "objective": "Post-seal immutable engineering certification",
        "repository": {"owner": "KiloAlpha021", "name": "automated-trading-bot",
                       "protected_commit": head, "protected_tree": tree},
        "authorized_changed_paths": sorted(set(paths)),
        "dependency_inventory": [{"producer": "sealed Git commit",
            "consumer": "m1-engineering-foundation", "interface": "repository tree",
            "applicability": "all candidate changes", "validation": "protected runner",
            "evidence": "execution capture"}],
        "candidate": {"mode": "POST_SEAL_IMMUTABLE_COMMIT", "expected_parent": parent},
        "environment": {"python_version": [3, 12, 10],
            "dependency_lock_path": "requirements-dev.lock",
            "dependency_lock_sha256": lock_digest,
            "pytest_state_directory": os.environ["ATIS_PYTEST_STATE"]},
        "validation_profile": {"profile_id": "AUTO", "controls": list(MANDATORY_CONTROLS)},
        "evidence": {"output_directory": os.environ["ATIS_EXECUTION_OUTPUT"],
            "no_overwrite": True, "canonical_serialization": "JSON_SORTED_COMPACT_UTF8_SHA256"},
        "pre_seal_requirements": ["SEPARATELY_ESTABLISHED"],
        "post_seal_requirements": ["COMMITTED_SUBJECT", "INSTALLED_WHEEL", "MODULE_SOURCES_MATCH"],
        "downstream_certification": {"obligations": ["TRUSTED_EVALUATION", "OWNER_ACCEPTANCE"]},
        "authority_exclusions": ["NO_TRADING_AUTHORITY", "NO_FINANCIAL_AUTHORITY",
                                 "NO_OWNER_ACCEPTANCE"],
    }
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(contract)
    return contract


def preflight(contract: dict[str, Any]) -> dict[str, Any]:
    global RUN_ENV
    if Path.cwd().resolve() != ROOT.resolve():
        raise RuntimeError("working directory does not equal repository root")
    head = _git_bytes("rev-parse", "HEAD").decode().strip()
    tree = _git_bytes("rev-parse", "HEAD^{tree}").decode().strip()
    repository = contract["repository"]
    if head != repository["protected_commit"] or tree != repository["protected_tree"]:
        raise RuntimeError("protected Git identity mismatch")
    if contract["candidate"]["mode"] == "POST_SEAL_IMMUTABLE_COMMIT":
        parent = _git_bytes("rev-parse", "HEAD^").decode().strip()
        if parent != contract["candidate"]["expected_parent"]:
            raise RuntimeError("protected parent identity mismatch")
    lock_digest = file_sha256(ROOT / contract["environment"]["dependency_lock_path"])
    if lock_digest != contract["environment"]["dependency_lock_sha256"]:
        raise RuntimeError("dependency-lock identity mismatch")
    if sys.version_info[:3] != tuple(contract["environment"]["python_version"]):
        raise RuntimeError("interpreter identity mismatch")
    allowed = sorted(contract["authorized_changed_paths"])
    observed = changed_paths()
    if any(path not in allowed for path in observed):
        raise RuntimeError(f"unauthorized changed path: {observed}")
    controls = tuple(contract["validation_profile"]["controls"])
    if controls != MANDATORY_CONTROLS:
        raise RuntimeError("validation profile is not equivalent to protected runner")
    expected_profile = profile_identity(lock_digest)
    supplied_profile = contract["validation_profile"]["profile_id"]
    if supplied_profile not in {"AUTO", expected_profile}:
        raise RuntimeError("protected validation profile identity mismatch")
    evidence = Path(contract["evidence"]["output_directory"])
    if not evidence.is_absolute() or ROOT.resolve() in evidence.resolve().parents or evidence.exists():
        raise RuntimeError("evidence output must be new, absolute and external")
    pytest_root = Path(contract["environment"]["pytest_state_directory"])
    if (not pytest_root.is_absolute() or pytest_root.resolve() == ROOT.resolve()
            or ROOT.resolve() in pytest_root.resolve().parents):
        raise RuntimeError("pytest state must be absolute and external")
    if (evidence.resolve() == pytest_root.resolve()
            or evidence.resolve() in pytest_root.resolve().parents
            or pytest_root.resolve() in evidence.resolve().parents):
        raise RuntimeError("pytest state and evidence must be disjoint")
    _probe_directory(pytest_root)
    child_environment = build_run_environment(contract)
    _probe_deep_git(pytest_root, child_environment)
    attribution = _source_attribution(contract, child_environment)
    RUN_ENV = child_environment
    snapshot = candidate_snapshot(allowed)
    return {"format": "ATIS_ENGINEERING_PREFLIGHT_V1", "contract_sha256": sha256(canonical(contract)).hexdigest(),
            "repository_root": str(ROOT.resolve()), "cwd": str(Path.cwd().resolve()),
            "subject": {"commit": head, "tree": tree}, "runner": {"path": str(Path(__file__).resolve()),
            "blob": _blob(Path(__file__).read_bytes()), "sha256": file_sha256(Path(__file__))},
            "interpreter": {"path": sys.executable, "version": list(sys.version_info[:3])},
            "dependency_lock_sha256": lock_digest, "profile_id": expected_profile,
            "changed_paths": observed, "candidate_snapshot": snapshot, "attribution": attribution,
            "claim": "PREFLIGHT_PASS"}


def locked_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / "requirements-dev.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, version = line.split()[0].split("==", 1)
            result[name] = version
            if "--hash=sha256:" not in line:
                raise RuntimeError(f"unhashed lock entry: {name}")
    return result


def _verify_python() -> None:
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise RuntimeError("M1 requires CPython 3.12.10 exactly")


def _verify_dependencies() -> None:
    expected = locked_versions()
    actual = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
        if re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        != "automated-trading-bot"
    }
    if actual != expected:
        raise RuntimeError(f"installed dependency identity mismatch: {actual} != {expected}")


def verify_environment() -> None:
    _verify_python()
    _verify_dependencies()
    run([sys.executable, "-m", "pip", "check"])
    __import__("automated_trading_bot")


def terminal_exit_code(error: BaseException | None) -> int:
    """The enclosing runner exit; never fabricate an unstarted child exit."""
    if error is None:
        return 0
    if isinstance(error, subprocess.CalledProcessError):
        return error.returncode
    if isinstance(error, KeyboardInterrupt):
        return 130
    return 1


class RunnerEvidence:
    """Separate runner facts; never convert a pytest result to runner approval."""

    def __init__(self, contract: dict[str, Any], preflight_record: dict[str, Any]):
        self.root = Path(contract["evidence"]["output_directory"])
        if (not self.root.is_absolute() or self.root.resolve() == ROOT.resolve()
                or ROOT.resolve() in self.root.resolve().parents):
            raise RuntimeError("runner evidence must be absolute and external")
        self.root.mkdir(parents=True, exist_ok=False)
        self.current: str | None = None
        self.contract = contract
        self.record: dict[str, Any] = {
            "format": "ATIS_RUNNER_EXECUTION_V1", "contract": contract,
            "contract_sha256": sha256(canonical(contract)).hexdigest(),
            "preflight": preflight_record,
            "interpreter": {"path": sys.executable, "version": list(sys.version_info[:3])},
            "environment": {key: (RUN_ENV or {}).get(key) for key in (
                "ATIS_EXECUTION_OUTPUT", "COVERAGE_FILE", "RUFF_CACHE_DIR",
                "MYPY_CACHE_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX",
                "TEMP", "TMP", "PYTHONPATH", "PYTEST_ADDOPTS")},
            "controls": [{"name": name, "status": "NOT_STARTED", "native": []}
                         for name in MANDATORY_CONTROLS],
            "initial_candidate": candidate_snapshot(contract["authorized_changed_paths"]),
            "initial_dependencies": working_snapshot()[0],
            "final_candidate": None, "final_dependencies": None,
            "terminal": "RUNNING", "native_exit_code": None,
            "remaining_obligations": contract["downstream_certification"]["obligations"],
            "evidence_digests": {},
        }
        (self.root / "contract.json").write_bytes(canonical(contract))
        (self.root / "preflight.json").write_bytes(canonical(preflight_record))
        self.write()

    def write(self) -> None:
        raw = canonical(self.record)
        temporary = self.root / "runner.incomplete"
        temporary.write_bytes(raw)
        # Publish the summary last: a failed write leaves incomplete or mismatched
        # evidence, never an old digest authenticating a new terminal success.
        (self.root / "runner.sha256").write_text(sha256(raw).hexdigest() + "\n", encoding="ascii")
        temporary.replace(self.root / "runner.json")

    def write_preserving_error(self) -> None:
        primary = sys.exc_info()[1]
        try:
            self.write()
        except BaseException as persistence_error:
            if primary is not None:
                raise primary from persistence_error
            raise

    def entry(self, name: str) -> dict[str, Any]:
        return next(item for item in self.record["controls"] if item["name"] == name)

    def control(self, name: str, action: Any) -> None:
        entry = self.entry(name)
        entry["status"] = "RUNNING"
        self.current = name
        self.write()
        try:
            action()
        except BaseException as error:
            entry.update(status="FAIL", error={"type": type(error).__name__, "message": str(error)})
            raise
        else:
            entry["status"] = "PASS"
        finally:
            self.current = None
            self.write_preserving_error()

    def native(self, command: list[str]) -> None:
        if self.current is None:
            raise RuntimeError("native command has no mandatory control")
        entry = self.entry(self.current)
        number = len(entry["native"])
        log = self.root / "native"
        log.mkdir(exist_ok=True)
        stdout = log / f"{self.current}-{number}.stdout"
        stderr = log / f"{self.current}-{number}.stderr"
        event = {"argv": command, "exit_code": None, "started": False,
                 "stdout": str(stdout.relative_to(self.root)),
                 "stderr": str(stderr.relative_to(self.root))}
        entry["native"].append(event)
        self.write()
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                process = subprocess.Popen(command, cwd=ROOT, env=RUN_ENV, stdout=out, stderr=err)
                event["started"] = True
                self.write()
                try:
                    code = process.wait()
                except BaseException as primary:
                    try:
                        process.terminate()
                        event["exit_code"] = process.wait()
                    except BaseException as cleanup_error:
                        event["cleanup_error"] = str(cleanup_error)
                        raise primary from cleanup_error
                    raise
                event["exit_code"] = code
            if code:
                raise subprocess.CalledProcessError(code, command)
        except BaseException as error:
            event["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            self.write_preserving_error()

    def reconcile(self) -> None:
        self.record["final_candidate"] = candidate_snapshot(self.contract["authorized_changed_paths"])
        self.record["final_dependencies"] = working_snapshot()[0]
        if (self.record["final_candidate"] != self.record["initial_candidate"]
                or self.record["final_dependencies"] != self.record["initial_dependencies"]
                or any(p not in self.contract["authorized_changed_paths"] for p in changed_paths())):
            raise RuntimeError("candidate changed during runner execution")

    def finish(self, error: BaseException | None) -> None:
        try:
            self.reconcile()
        except BaseException as identity_error:
            self.record["identity_error"] = str(identity_error)
            if error is None:
                error = identity_error
        try:
            digests = {
                path.relative_to(self.root).as_posix(): file_sha256(path)
                for path in sorted(self.root.rglob("*")) if path.is_file()
                and path.name not in {"runner.json", "runner.sha256", "runner.incomplete"}
            }
            self.record["terminal"] = (
                "INTERRUPTED" if isinstance(error, KeyboardInterrupt)
                else "FAIL" if error else "PASS")
            self.record["claim"] = (
                "RUNNER_FAIL" if error else CLAIMS[self.contract["candidate"]["mode"]])
            if error:
                self.record["error"] = {"type": type(error).__name__, "message": str(error)}
            self.record["native_exit_code"] = terminal_exit_code(error)
            self.record["evidence_digests"] = digests
            self.write()
        except BaseException as persistence_error:
            if error is not None:
                raise error from persistence_error
            raise
        if error is not None:
            raise error



def _control(name: str, action: Any) -> None:
    if RUN_REPORT is None:
        action()
    else:
        RUN_REPORT.control(name, action)


def audit(cache: Path) -> None:
    if (not cache.is_absolute() or cache.resolve() == ROOT.resolve()
            or ROOT.resolve() in cache.resolve().parents or cache.exists()):
        raise RuntimeError("audit cache must be fresh, absolute and external")
    if importlib.metadata.version("pip-audit") != "2.10.1":
        raise RuntimeError("pip-audit 2.10.1 is required")
    cache.mkdir(parents=True, exist_ok=False)
    _probe_directory(cache)
    if RUN_REPORT is not None:
        RUN_REPORT.entry("VULNERABILITY_AUDIT")["audit"] = {
            "version": "2.10.1", "cache": str(cache), "fresh_cache": True,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "advisory_requirement": "LIVE_RETRIEVAL", "result": "RUNNING"}
    try:
        run([sys.executable, "-m", "pip_audit", "--local", "--cache-dir", str(cache)])
    except BaseException:
        if RUN_REPORT is not None:
            RUN_REPORT.entry("VULNERABILITY_AUDIT")["audit"]["result"] = "FAIL_SEE_NATIVE_EVIDENCE"
        raise
    else:
        if RUN_REPORT is not None:
            RUN_REPORT.entry("VULNERABILITY_AUDIT")["audit"]["result"] = "PASS"


def check() -> None:
    global RUN_REPORT
    if RUN_ENV is None or not RUN_ENV.get("TEMP") or not ACTIVE_CONTRACT or not PREFLIGHT_RECORD:
        raise RuntimeError("controlled contract and preflight are unavailable")
    expected_environment = build_run_environment(ACTIVE_CONTRACT)
    bound = ("ATIS_EXECUTION_OUTPUT", "COVERAGE_FILE", "RUFF_CACHE_DIR", "MYPY_CACHE_DIR",
             "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTEST_ADDOPTS", "TEMP", "TMP")
    if any(RUN_ENV.get(key) != expected_environment[key] for key in bound):
        raise RuntimeError("controlled output environment changed after preflight")
    report = RunnerEvidence(ACTIVE_CONTRACT, PREFLIGHT_RECORD)
    RUN_REPORT = report
    error: BaseException | None = None
    try:
        (report.root / "coverage").mkdir()
        _control("PYTHON_3_12_10", _verify_python)
        _control("HASH_LOCKED_DEPENDENCIES", _verify_dependencies)
        _control("PIP_CONSISTENCY", lambda: run([sys.executable, "-m", "pip", "check"]))
        def attribution() -> None:
            if (PREFLIGHT_RECORD.get("contract_sha256") != report.record["contract_sha256"]
                    or PREFLIGHT_RECORD.get("candidate_snapshot") != report.record["initial_candidate"]):
                raise RuntimeError("candidate no longer matches attributed preflight")
            if not PREFLIGHT_RECORD.get("attribution"):
                raise RuntimeError("candidate attribution is missing")
        _control("CANDIDATE_ATTRIBUTION", attribution)
        _control("RUFF", lambda: run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]))
        _control("STRICT_MYPY", lambda: run([sys.executable, "-m", "mypy", "src/automated_trading_bot"]))
        _control("FULL_PYTEST", lambda: run([
            sys.executable, "-m", "coverage", "run", "--source=automated_trading_bot",
            "-m", "pytest", "-q", "--basetemp", RUN_ENV["TEMP"],
            "-p", "scripts.m1_engineering_foundation", "-p", "no:cacheprovider",
        ]))
        def coverage_outputs() -> None:
            run([sys.executable, "-m", "coverage", "report", "--show-missing"])
            run([sys.executable, "-m", "coverage", "xml", "-o", str(report.root / "coverage.xml")])
        _control("COVERAGE", coverage_outputs)
        _control("VULNERABILITY_AUDIT", lambda: audit(report.root / "cache" / "pip-audit"))
        _control("CLOSURE_SECURITY", lambda: run([
            sys.executable, "-m", "pytest", "-q", "tests/test_closure_security.py",
            "-p", "no:cacheprovider", "--basetemp", str(report.root / "closure-pytest"),
        ]))
        def native_results() -> None:
            for entry in report.record["controls"]:
                for event in entry["native"]:
                    if not event["started"] or event["exit_code"] != 0:
                        raise RuntimeError("native failure or incomplete execution")
        _control("NATIVE_FAILURE_PROPAGATION", native_results)
        def capture_result() -> None:
            path = report.root / "execution-evidence" / "capture.json"
            raw = path.read_bytes()
            digest = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
            value = json.loads(raw)
            if (sha256(raw).hexdigest() != digest or canonical(value) != raw
                    or value["format"] != "ATIS_PYTEST_EXECUTION_V1"
                    or not value["session_finished"] or value["exit_code"] != 0
                    or not value["workspace_stable"] or value["collection_errors"]):
                raise RuntimeError("pytest capture is incomplete, changed or failed")
        _control("EXECUTION_CAPTURE", capture_result)
        _control("CANDIDATE_SNAPSHOT_RECONCILIATION", report.reconcile)
    except BaseException as caught:
        error = caught
    finally:
        RUN_REPORT = None
        report.finish(error)


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


def installed_source_identity(dependencies: list[dict[str, str]]) -> dict[str, Any]:
    import automated_trading_bot  # type: ignore[import-untyped]

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
            "environment": (
                PREFLIGHT_RECORD["attribution"] if PREFLIGHT_RECORD
                and ACTIVE_CONTRACT
                and ACTIVE_CONTRACT["candidate"]["mode"] == "PRE_SEAL_WORKING_TREE"
                else installed_source_identity(dependencies)
            ),
            "provenance": {key: os.environ.get(variable, "") for key, variable in (
                ("repository", "GITHUB_REPOSITORY"), ("event", "GITHUB_EVENT_NAME"),
                ("ref", "GITHUB_REF"), ("sha", "GITHUB_SHA"),
                ("run_id", "GITHUB_RUN_ID"), ("attempt", "GITHUB_RUN_ATTEMPT"),
                ("job", "GITHUB_JOB"))},
            "collected": [], "reports": [], "collection_errors": [],
            "session_finished": False, "exit_code": None,
            "workspace_stable": False,
            "committed_subject": committed and (
                not ACTIVE_CONTRACT
                or ACTIVE_CONTRACT["candidate"]["mode"] == "POST_SEAL_IMMUTABLE_COMMIT"
            ),
            "contract": ACTIVE_CONTRACT,
            "preflight": PREFLIGHT_RECORD,
            "phase": ACTIVE_CONTRACT["candidate"]["mode"] if ACTIVE_CONTRACT else "LEGACY",
            "claim": "VALIDATION_INCOMPLETE",
            "remaining_obligations": (
                ACTIVE_CONTRACT["downstream_certification"]["obligations"]
                if ACTIVE_CONTRACT else []
            ),
        }
        self._write()

    def _write(self) -> None:
        record = dict(self.record)
        # Historical Stage 3 readers require exactly these original V1 fields.
        # Pre-seal extension semantics remain available for local consumers.
        if record["phase"] != "PRE_SEAL_WORKING_TREE":
            for key in ("contract", "preflight", "phase", "claim", "remaining_obligations",
                        "candidate_snapshot_final"):
                record.pop(key, None)
        raw = canonical(record)
        temporary = self.output / "capture.incomplete"
        temporary.write_bytes(raw)
        temporary.replace(self.output / "capture.json")

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.record["collected"] = [item.nodeid for item in session.items]
        self._write()

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.record["collection_errors"].append({"nodeid": report.nodeid,
                                                       "outcome": report.outcome})
            self._write()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        event = {"nodeid": report.nodeid, "when": report.when,
                 "outcome": report.outcome, "xfail": hasattr(report, "wasxfail")}
        self.record["reports"].append(event)
        # Incremental facts survive interruption even without sessionfinish.
        with (self.output / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        current, committed = working_snapshot()
        self.record["workspace_stable"] = current == self.record["dependencies"]
        self.record["committed_subject"] = self.record["committed_subject"] and committed
        self.record["session_finished"] = True
        self.record["exit_code"] = int(exitstatus)
        if ACTIVE_CONTRACT:
            expected = PREFLIGHT_RECORD["candidate_snapshot"] if PREFLIGHT_RECORD else []
            current_candidate = candidate_snapshot(ACTIVE_CONTRACT["authorized_changed_paths"])
            self.record["candidate_snapshot_final"] = current_candidate
            self.record["workspace_stable"] = self.record["workspace_stable"] and current_candidate == expected
            if int(exitstatus) == 0 and self.record["workspace_stable"]:
                self.record["claim"] = CLAIMS[ACTIVE_CONTRACT["candidate"]["mode"]]
            else:
                self.record["claim"] = (
                    "PRE_SEAL_VALIDATION_FAIL" if ACTIVE_CONTRACT["candidate"]["mode"] == "PRE_SEAL_WORKING_TREE"
                    else "POST_SEAL_CERTIFICATION_FAIL"
                )
        self._write()
        raw = (self.output / "capture.json").read_bytes()
        (self.output / "capture.sha256").write_text(sha256(raw).hexdigest() + "\n",
                                                  encoding="ascii")


def pytest_configure(config: pytest.Config) -> None:
    global ACTIVE_CONTRACT, PREFLIGHT_RECORD
    contract_path = os.environ.get("ATIS_RUN_CONTRACT")
    if contract_path and Path(contract_path).is_file():
        ACTIVE_CONTRACT = load_contract(Path(contract_path))
        PREFLIGHT_RECORD = json.loads(Path(os.environ["ATIS_RUN_PREFLIGHT"]).read_bytes())
        if PREFLIGHT_RECORD["contract_sha256"] != sha256(canonical(ACTIVE_CONTRACT)).hexdigest():
            raise RuntimeError("pytest contract digest mismatch")
    configured = os.environ.get("ATIS_EXECUTION_OUTPUT")
    if not configured:
        raise RuntimeError("external pytest evidence is required")
    output = Path(configured)
    if not output.is_absolute() or output.resolve() == ROOT.resolve() or ROOT.resolve() in output.resolve().parents:
        raise RuntimeError("pytest evidence must be absolute and external")
    config.pluginmanager.register(NodeCapture(output), "atis-node-evidence")


def main() -> None:
    global ACTIVE_CONTRACT, PREFLIGHT_RECORD
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "preflight", "check"), default="check", nargs="?")
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args()
    if args.command in {"preflight", "check"}:
        ACTIVE_CONTRACT = (
            load_contract(args.contract.resolve()) if args.contract is not None
            else hosted_contract()
        )
        PREFLIGHT_RECORD = preflight(ACTIVE_CONTRACT)
        if args.command == "preflight":
            print(canonical(PREFLIGHT_RECORD).decode("utf-8"))
        else:
            check()
    else:
        verify_environment()


def cli() -> int:
    try:
        main()
    except (KeyboardInterrupt, Exception) as error:
        traceback.print_exc()
        return terminal_exit_code(error)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

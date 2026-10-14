from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Sequence


SCHEMA_VERSION = "ATIS-ENGINEERING-EXECUTION-GUARD-V1"
SUMMARY_VERSION = "ATIS-ENGINEERING-EXECUTION-SUMMARY-V1"
RETRY_AUTHORIZATION_VERSION = "ATIS-ENGINEERING-RETRY-AUTHORIZATION-V1"
ZERO_HASH = "0" * 64
TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FAILURE_DISPOSITIONS = {"FAILED", "INTERRUPTED"}


class GuardError(RuntimeError):
    """Fail-closed guard error."""


class ProcessStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"
    NOT_FOUND = "NOT_FOUND"
    ACTIVE_OR_UNKNOWN = "ACTIVE_OR_UNKNOWN"


@dataclass(frozen=True)
class ProcessQuery:
    status: ProcessStatus
    error_code: int
    native_result: int | None

    @property
    def death_confirmed(self) -> bool:
        return self.status in {ProcessStatus.TERMINATED, ProcessStatus.NOT_FOUND}

    def evidence(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "error_code": self.error_code,
            "native_result": self.native_result,
        }


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardError(f"JSON object required: {path}")
    return value


def require_keys(value: dict[str, Any], required: set[str], where: str) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required)
    if missing or extra:
        raise GuardError(f"{where} keys invalid; missing={missing}, extra={extra}")


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={repo.resolve()}", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}")
    return completed.stdout.strip()


def current_git_identity(repo: Path) -> tuple[str, str]:
    return git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")


def changed_paths(repo: Path) -> set[str]:
    output = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not output:
        return set()
    raw = output.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(raw) and raw[index]:
        entry = raw[index]
        status = entry[:2]
        path = entry[3:]
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 1
            if index >= len(raw) or not raw[index]:
                raise GuardError("malformed git porcelain rename record")
            path = raw[index]
        paths.add(PurePosixPath(path).as_posix())
        index += 1
    return paths


def path_allowed(path: str, scopes: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(path)
    for raw_scope in scopes:
        scope = PurePosixPath(raw_scope)
        if candidate == scope or scope in candidate.parents:
            return True
    return False


@dataclass(frozen=True)
class Descriptor:
    raw: dict[str, Any]
    digest: str
    task_id: str
    defect_id: str
    authorization_ref: str
    repository_identity: str
    repository_path: Path
    base_commit: str
    base_tree: str
    authorized_stage: str
    allowed_paths: tuple[str, ...]
    lock_path: str
    lock_sha256: str
    validation_id: str
    validation_commands: tuple[tuple[str, ...], ...]
    max_failed_attempts: int
    evidence_dir: Path


def validate_descriptor(path: Path) -> Descriptor:
    raw = load_json(path)
    required = {
        "schema_version", "task_id", "defect_id", "owner_authorization_ref",
        "repository", "protected_baseline", "authorized_stage", "allowed_paths",
        "dependency_lock", "validation_profile", "maximum_failed_attempts",
        "evidence_output_directory",
    }
    require_keys(raw, required, "descriptor")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise GuardError("unsupported descriptor schema_version")
    task_id = raw["task_id"]
    defect_id = raw["defect_id"]
    auth = raw["owner_authorization_ref"]
    stage = raw["authorized_stage"]
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise GuardError("invalid task_id")
    for name, value in (("defect_id", defect_id), ("owner_authorization_ref", auth), ("authorized_stage", stage)):
        if not isinstance(value, str) or not value.strip():
            raise GuardError(f"missing {name}")

    repository = raw["repository"]
    baseline = raw["protected_baseline"]
    dependency = raw["dependency_lock"]
    validation = raw["validation_profile"]
    for name, value, keys in (
        ("repository", repository, {"identity", "path"}),
        ("protected_baseline", baseline, {"commit", "tree"}),
        ("dependency_lock", dependency, {"path", "sha256"}),
        ("validation_profile", validation, {"id", "commands"}),
    ):
        if not isinstance(value, dict):
            raise GuardError(f"{name} must be an object")
        require_keys(value, keys, name)

    repo_identity = repository["identity"]
    repo_path_raw = repository["path"]
    if repo_identity != "KiloAlpha021/automated-trading-bot":
        raise GuardError("unauthorized repository identity")
    if not isinstance(repo_path_raw, str) or not repo_path_raw:
        raise GuardError("repository path missing")
    repo_path = Path(repo_path_raw).resolve()
    if not (repo_path / ".git").exists():
        raise GuardError("repository path is not a Git working tree")
    commit = baseline["commit"]
    tree = baseline["tree"]
    if not isinstance(commit, str) or not GIT_SHA_RE.fullmatch(commit):
        raise GuardError("invalid protected base commit")
    if not isinstance(tree, str) or not GIT_SHA_RE.fullmatch(tree):
        raise GuardError("invalid protected base tree")

    scopes_raw = raw["allowed_paths"]
    if not isinstance(scopes_raw, list) or not scopes_raw:
        raise GuardError("allowed_paths must be a non-empty list")
    scopes: list[str] = []
    for item in scopes_raw:
        if not isinstance(item, str) or not item or "\\" in item:
            raise GuardError("allowed_paths must be normalized repository-relative POSIX paths")
        pure = PurePosixPath(item)
        if pure.is_absolute() or ".." in pure.parts or str(pure) != item.rstrip("/"):
            raise GuardError(f"unsafe allowed path: {item}")
        scopes.append(str(pure))
    if len(scopes) != len(set(scopes)):
        raise GuardError("duplicate allowed path")

    lock_rel = dependency["path"]
    lock_digest = dependency["sha256"]
    if not isinstance(lock_rel, str) or not lock_rel or PurePosixPath(lock_rel).is_absolute() or ".." in PurePosixPath(lock_rel).parts:
        raise GuardError("invalid dependency lock path")
    if not isinstance(lock_digest, str) or not SHA256_RE.fullmatch(lock_digest):
        raise GuardError("invalid dependency lock digest")
    commands_raw = validation["commands"]
    if not isinstance(validation["id"], str) or not validation["id"]:
        raise GuardError("validation profile id missing")
    if not isinstance(commands_raw, list) or not commands_raw:
        raise GuardError("validation commands missing")
    commands: list[tuple[str, ...]] = []
    for command in commands_raw:
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
            raise GuardError("each validation command must be a non-empty argv array")
        commands.append(tuple(command))

    maximum = raw["maximum_failed_attempts"]
    if maximum != 2:
        raise GuardError("maximum_failed_attempts must equal 2")
    evidence_raw = raw["evidence_output_directory"]
    if not isinstance(evidence_raw, str) or not evidence_raw:
        raise GuardError("evidence output directory missing")
    evidence = Path(evidence_raw).resolve()
    try:
        evidence.relative_to(repo_path)
    except ValueError:
        pass
    else:
        raise GuardError("evidence output directory must be outside the repository")

    digest = sha256_bytes(canonical_bytes(raw))
    return Descriptor(
        raw=raw, digest=digest, task_id=task_id, defect_id=defect_id,
        authorization_ref=auth, repository_identity=repo_identity,
        repository_path=repo_path, base_commit=commit, base_tree=tree,
        authorized_stage=stage, allowed_paths=tuple(scopes), lock_path=lock_rel,
        lock_sha256=lock_digest, validation_id=validation["id"],
        validation_commands=tuple(commands), max_failed_attempts=maximum,
        evidence_dir=evidence,
    )


def state_root_for(descriptor: Descriptor, override: Path | None) -> Path:
    if override is not None:
        if os.environ.get("ATIS_GUARD_TEST_MODE") != "1":
            raise GuardError("state-root override is restricted to explicit test mode")
        root = override.resolve()
    else:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise GuardError("LOCALAPPDATA unavailable; --state-root required")
        root = Path(local) / "ATIS" / "engineering-execution-guard" / "v1"
    try:
        root.relative_to(descriptor.repository_path)
    except ValueError:
        return root
    raise GuardError("guard state root must be outside repository")


def repo_key(descriptor: Descriptor) -> str:
    return sha256_bytes(descriptor.repository_identity.encode("utf-8"))


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class Journal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "journal.jsonl"
        self.anchor_path = directory / "journal.anchor.json"
        directory.mkdir(parents=True, exist_ok=True)

    def verify(self, *, allow_anchor_lag: bool = False) -> tuple[int, str, list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        previous = ZERO_HASH
        expected_sequence = 1
        if self.path.exists():
            try:
                lines = self.path.read_bytes().splitlines()
            except OSError as exc:
                raise GuardError(f"journal unreadable: {exc}") from exc
            for line in lines:
                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise GuardError(f"journal corruption at sequence {expected_sequence}: {exc}") from exc
                if not isinstance(record, dict):
                    raise GuardError("journal record must be an object")
                record_hash = record.get("record_hash")
                body = {key: value for key, value in record.items() if key != "record_hash"}
                if body.get("sequence") != expected_sequence or body.get("previous_hash") != previous:
                    raise GuardError("journal sequence or chain conflict")
                actual = sha256_bytes(canonical_bytes(body))
                if record_hash != actual:
                    raise GuardError("journal hash mismatch")
                records.append(record)
                previous = actual
                expected_sequence += 1
        if self.anchor_path.exists():
            anchor = load_json(self.anchor_path)
            require_keys(anchor, {"sequence", "record_hash"}, "journal anchor")
            expected_anchor = {"sequence": expected_sequence - 1, "record_hash": previous}
            if anchor != expected_anchor:
                lag_is_exactly_one = (
                    allow_anchor_lag
                    and len(records) >= 1
                    and anchor
                    == {
                        "sequence": expected_sequence - 2,
                        "record_hash": ZERO_HASH if len(records) == 1 else records[-2]["record_hash"],
                    }
                )
                if not lag_is_exactly_one:
                    raise GuardError("journal truncation or anchor conflict detected")
        elif records:
            raise GuardError("journal anchor missing")
        return expected_sequence - 1, previous, records

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        sequence, previous, _ = self.verify()
        body = {"sequence": sequence + 1, "previous_hash": previous, **event}
        record = {**body, "record_hash": sha256_bytes(canonical_bytes(body))}
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab", buffering=0) as handle:
            handle.write(canonical_bytes(record))
            os.fsync(handle.fileno())
        atomic_write(self.anchor_path, canonical_bytes({"sequence": sequence + 1, "record_hash": record["record_hash"]}))
        return record


def _windows_process_query(pid: int) -> ProcessQuery:
    error_invalid_parameter = 87
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ctypes.set_last_error(0)
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return ProcessQuery(ProcessStatus.NOT_FOUND, error, None)
        return ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, error, None)
    try:
        ctypes.set_last_error(0)
        result = int(kernel32.WaitForSingleObject(handle, 0))
        error = ctypes.get_last_error()
        if result == wait_object_0:
            return ProcessQuery(ProcessStatus.TERMINATED, error, result)
        if result == wait_timeout:
            return ProcessQuery(ProcessStatus.ACTIVE, error, result)
        return ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, error, result)
    finally:
        kernel32.CloseHandle(handle)


def query_process(pid: int) -> ProcessQuery:
    if pid <= 0:
        return ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, 87, None)
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return ProcessQuery(ProcessStatus.NOT_FOUND, 0, None)
        except PermissionError:
            return ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, 13, None)
        return ProcessQuery(ProcessStatus.ACTIVE, 0, None)
    return _windows_process_query(pid)


def process_alive(pid: int) -> bool:
    return query_process(pid).status in {ProcessStatus.ACTIVE, ProcessStatus.ACTIVE_OR_UNKNOWN}


class RepositoryLock:
    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as exc:
            raise GuardError(f"repository execution already locked: {self.path}") from exc
        try:
            os.write(descriptor, canonical_bytes(self.payload))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            try:
                current = load_json(self.path)
                if current != self.payload:
                    raise GuardError("lock ownership changed while held")
                self.path.unlink()
            finally:
                self.acquired = False

    def update(self, **changes: Any) -> None:
        if not self.acquired:
            raise GuardError("cannot update a lock that is not held")
        current = load_json(self.path)
        if current != self.payload:
            raise GuardError("lock ownership changed before update")
        self.payload = {**self.payload, **changes}
        atomic_write(self.path, canonical_bytes(self.payload))

    def __enter__(self) -> RepositoryLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


def registry_paths(root: Path, descriptor: Descriptor) -> tuple[Path, Path, Path]:
    repository = root / "repositories" / repo_key(descriptor)
    return repository, repository / "repository.lock.json", repository / "tasks"


def verify_baseline_and_scope(descriptor: Descriptor) -> set[str]:
    commit, tree = current_git_identity(descriptor.repository_path)
    if commit != descriptor.base_commit or tree != descriptor.base_tree:
        raise GuardError(f"protected baseline mismatch: got {commit}/{tree}")
    lock_file = descriptor.repository_path / descriptor.lock_path
    if not lock_file.is_file() or sha256_file(lock_file) != descriptor.lock_sha256:
        raise GuardError("dependency lock identity mismatch")
    changed = changed_paths(descriptor.repository_path)
    unauthorized = sorted(path for path in changed if not path_allowed(path, descriptor.allowed_paths))
    if unauthorized:
        raise GuardError(f"unauthorized changed paths: {unauthorized}")
    return changed


def register_descriptor(task_directory: Path, descriptor: Descriptor) -> None:
    registered = task_directory / "descriptor.json"
    if registered.exists():
        existing = registered.read_bytes()
        if existing != canonical_bytes(descriptor.raw):
            raise GuardError("conflicting descriptor for existing task_id")
    else:
        atomic_write(registered, canonical_bytes(descriptor.raw))


def all_task_records(tasks_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not tasks_dir.exists():
        return records
    for child in tasks_dir.iterdir():
        if not child.is_dir():
            continue
        descriptor_path = child / "descriptor.json"
        if descriptor_path.exists():
            records.append(load_json(descriptor_path))
    return records


def attempt_state(records: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    starts: list[dict[str, Any]] = []
    terminals: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    previous_number = 0
    for record in records:
        event = record.get("event")
        attempt_id = record.get("attempt_id")
        if event == "ATTEMPT_STARTED":
            number = record.get("attempt_number")
            if not isinstance(attempt_id, str) or not isinstance(number, int) or number <= previous_number:
                raise GuardError("ambiguous or non-monotonic attempt start")
            if attempt_id in seen:
                raise GuardError("duplicate attempt start")
            previous_number = number
            seen.add(attempt_id)
            starts.append(record)
        elif event == "ATTEMPT_FINISHED":
            if not isinstance(attempt_id, str) or attempt_id not in seen or attempt_id in terminals:
                raise GuardError("orphan or conflicting attempt terminal event")
            if record.get("disposition") not in {"SUCCEEDED", *FAILURE_DISPOSITIONS}:
                raise GuardError("invalid attempt terminal disposition")
            terminals[attempt_id] = record
    return starts, terminals


def open_attempts(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    starts, terminals = attempt_state(records)
    return [record for record in starts if record["attempt_id"] not in terminals]


def failure_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    attempt_state(records)
    return [
        record
        for record in records
        if record.get("event") == "ATTEMPT_FINISHED" and record.get("disposition") in FAILURE_DISPOSITIONS
    ]


def failed_attempts(journal: Journal) -> int:
    _, _, records = journal.verify()
    return len(failure_records(records))


def successful_attempt_exists(journal: Journal) -> bool:
    _, _, records = journal.verify()
    attempt_state(records)
    return any(item.get("event") == "ATTEMPT_FINISHED" and item.get("disposition") == "SUCCEEDED" for item in records)


def active_authorization(records: Sequence[dict[str, Any]], descriptor: Descriptor) -> tuple[str, int]:
    reference = descriptor.authorization_ref
    epoch_start_sequence = 0
    seen_ids: set[str] = set()
    seen_references = {reference}
    for record in records:
        if record.get("event") != "RETRY_AUTHORIZED":
            continue
        authorization_id = record.get("authorization_id")
        if not isinstance(authorization_id, str) or authorization_id in seen_ids:
            raise GuardError("conflicting retry authorization history")
        if record.get("previous_authorization_ref") != reference:
            raise GuardError("retry authorization predecessor conflict")
        new_reference = record.get("new_authorization_ref")
        if not isinstance(new_reference, str) or not new_reference or new_reference in seen_references:
            raise GuardError("invalid retry authorization reference")
        seen_ids.add(authorization_id)
        seen_references.add(new_reference)
        reference = new_reference
        epoch_start_sequence = int(record["sequence"])
    return reference, epoch_start_sequence


def epoch_failures(records: Sequence[dict[str, Any]], epoch_start_sequence: int) -> list[dict[str, Any]]:
    return [record for record in failure_records(records) if int(record["sequence"]) > epoch_start_sequence]


def create_event(
    descriptor: Descriptor,
    attempt_id: str,
    event: str,
    *,
    authorization_ref: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event": event,
        "task_id": descriptor.task_id,
        "defect_id": descriptor.defect_id,
        "descriptor_sha256": descriptor.digest,
        "authorization_ref": authorization_ref or descriptor.authorization_ref,
        "attempt_id": attempt_id,
        "process_id": os.getpid(),
        "thread_id": os.environ.get("CODEX_THREAD_ID", "NOT_ESTABLISHED"),
        "recorded_at_ns": time.time_ns(),
        **extra,
    }


def run_argv(argv: Sequence[str], cwd: Path, log_path: Path) -> int:
    completed = subprocess.run(list(argv), cwd=cwd, check=False, capture_output=True)
    log = {
        "argv": list(argv),
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout),
        "stderr_sha256": sha256_bytes(completed.stderr),
        "stdout_size": len(completed.stdout),
        "stderr_size": len(completed.stderr),
    }
    atomic_write(log_path, canonical_bytes(log))
    log_path.with_suffix(log_path.suffix + ".stdout").write_bytes(completed.stdout)
    log_path.with_suffix(log_path.suffix + ".stderr").write_bytes(completed.stderr)
    return completed.returncode


def execution_summary(
    descriptor: Descriptor,
    attempt_id: str,
    disposition: str,
    changed: set[str],
    results: list[dict[str, Any]],
    authorization_ref: str,
) -> dict[str, Any]:
    commit, tree = current_git_identity(descriptor.repository_path)
    return {
        "schema_version": SUMMARY_VERSION,
        "task_id": descriptor.task_id,
        "defect_id": descriptor.defect_id,
        "descriptor_sha256": descriptor.digest,
        "authorization_ref": authorization_ref,
        "repository_identity": descriptor.repository_identity,
        "protected_base_commit": descriptor.base_commit,
        "protected_base_tree": descriptor.base_tree,
        "final_head": commit,
        "final_tree": tree,
        "dependency_lock_sha256": descriptor.lock_sha256,
        "validation_profile": descriptor.validation_id,
        "attempt_id": attempt_id,
        "disposition": disposition,
        "changed_paths": sorted(changed),
        "command_results": results,
    }


def fail(message: str) -> NoReturn:
    raise GuardError(message)


def implementation_command(arguments: Sequence[str]) -> tuple[str, ...]:
    command = tuple(arguments)
    if command and command[0] == "--":
        command = command[1:]
        if not command:
            raise GuardError("implementation command missing after -- separator")
    return command


def command_run(args: argparse.Namespace) -> int:
    descriptor = validate_descriptor(Path(args.descriptor))
    implementation = implementation_command(args.command)
    root = state_root_for(descriptor, Path(args.state_root) if args.state_root else None)
    repository_dir, lock_path, tasks_dir = registry_paths(root, descriptor)
    task_dir = tasks_dir / descriptor.task_id
    journal = Journal(task_dir)
    lock_payload = {
        "schema_version": SCHEMA_VERSION,
        "repository_identity": descriptor.repository_identity,
        "task_id": descriptor.task_id,
        "defect_id": descriptor.defect_id,
        "descriptor_sha256": descriptor.digest,
        "process_id": os.getpid(),
        "lock_id": str(uuid.uuid4()),
        "created_at_ns": time.time_ns(),
        "attempt_id": None,
    }
    with RepositoryLock(lock_path, lock_payload) as repository_lock:
        for existing in all_task_records(tasks_dir):
            if existing.get("defect_id") == descriptor.defect_id and existing.get("task_id") != descriptor.task_id:
                other = tasks_dir / str(existing.get("task_id"))
                other_journal = Journal(other)
                if failed_attempts(other_journal) >= int(existing.get("maximum_failed_attempts", 2)):
                    raise GuardError("cross-task retry circuit breaker open for defect_id")
                raise GuardError("duplicate underlying defect registered under a different task_id")
        register_descriptor(task_dir, descriptor)
        _, _, records = journal.verify()
        if open_attempts(records):
            raise GuardError("interrupted attempt requires explicit authorized lock recovery")
        if successful_attempt_exists(journal):
            raise GuardError("duplicate execution rejected: task already succeeded")
        active_auth, epoch_start = active_authorization(records, descriptor)
        failures = epoch_failures(records, epoch_start)
        if len(failures) >= descriptor.max_failed_attempts:
            journal.append(
                create_event(
                    descriptor,
                    "NONE",
                    "CIRCUIT_BREAKER_REJECTED",
                    authorization_ref=active_auth,
                    failed_attempts=len(failures),
                )
            )
            raise GuardError("retry circuit breaker open; new explicit owner authorization required")
        verify_baseline_and_scope(descriptor)
        starts, _ = attempt_state(records)
        attempt_number = 1 + max((int(record["attempt_number"]) for record in starts), default=0)
        attempt_id = f"{descriptor.task_id}-A{attempt_number}"
        evidence = descriptor.evidence_dir / descriptor.task_id / attempt_id
        journal.append(
            create_event(
                descriptor,
                attempt_id,
                "ATTEMPT_STARTED",
                authorization_ref=active_auth,
                attempt_number=attempt_number,
                evidence_directory=str(evidence),
            )
        )
        repository_lock.update(attempt_id=attempt_id)
        results: list[dict[str, Any]] = []
        disposition = "FAILED"
        evidence_created = False
        try:
            evidence.mkdir(parents=True, exist_ok=False)
            evidence_created = True
            commands = []
            if implementation:
                commands.append(("implementation", implementation))
            commands.extend((f"validation-{index}", command) for index, command in enumerate(descriptor.validation_commands, 1))
            for index, (kind, argv) in enumerate(commands, 1):
                verify_baseline_and_scope(descriptor)
                journal.append(
                    create_event(
                        descriptor,
                        attempt_id,
                        "CHECKPOINT_PASSED",
                        authorization_ref=active_auth,
                        checkpoint=f"before-{kind}",
                    )
                )
                log_path = evidence / f"{index:02d}-{kind}.json"
                exit_code = run_argv(argv, descriptor.repository_path, log_path)
                result = {"kind": kind, "argv": list(argv), "exit_code": exit_code, "log": str(log_path)}
                results.append(result)
                changed = verify_baseline_and_scope(descriptor)
                if exit_code != 0:
                    raise GuardError(f"{kind} command failed with exit code {exit_code}")
                journal.append(
                    create_event(
                        descriptor,
                        attempt_id,
                        "VALIDATION_PASSED",
                        authorization_ref=active_auth,
                        validation=kind,
                        exit_code=exit_code,
                    )
                )
            changed = verify_baseline_and_scope(descriptor)
            disposition = "SUCCEEDED"
            summary = execution_summary(descriptor, attempt_id, disposition, changed, results, active_auth)
            summary_path = evidence / "execution-summary.json"
            atomic_write(summary_path, canonical_bytes(summary))
            atomic_write(evidence / "execution-summary.sha256", (sha256_file(summary_path) + "\n").encode("ascii"))
            journal.append(
                create_event(
                    descriptor,
                    attempt_id,
                    "ATTEMPT_FINISHED",
                    authorization_ref=active_auth,
                    disposition=disposition,
                    summary_sha256=sha256_file(summary_path),
                )
            )
            print(str(summary_path))
            return 0
        except Exception as exc:
            changed = changed_paths(descriptor.repository_path)
            summary_digest: str | None = None
            if evidence_created:
                summary = execution_summary(descriptor, attempt_id, disposition, changed, results, active_auth)
                summary["failure"] = str(exc)
                summary_path = evidence / "execution-summary.json"
                atomic_write(summary_path, canonical_bytes(summary))
                atomic_write(evidence / "execution-summary.sha256", (sha256_file(summary_path) + "\n").encode("ascii"))
                summary_digest = sha256_file(summary_path)
            journal.append(
                create_event(
                    descriptor,
                    attempt_id,
                    "ATTEMPT_FINISHED",
                    authorization_ref=active_auth,
                    disposition=disposition,
                    failure=str(exc),
                    summary_sha256=summary_digest,
                )
            )
            if isinstance(exc, GuardError):
                raise
            raise GuardError(f"attempt failed before completion: {exc}") from exc


def validate_retry_authorization(path: Path, descriptor: Descriptor) -> tuple[dict[str, Any], str]:
    try:
        path.resolve().relative_to(descriptor.repository_path)
    except ValueError:
        pass
    else:
        raise GuardError("retry authorization must be outside the repository")
    raw = load_json(path)
    require_keys(
        raw,
        {
            "schema_version",
            "authorization_id",
            "original_descriptor_sha256",
            "task_id",
            "defect_id",
            "previous_authorization_ref",
            "new_authorization_ref",
            "failed_attempt_ids",
        },
        "retry authorization",
    )
    if raw["schema_version"] != RETRY_AUTHORIZATION_VERSION:
        raise GuardError("unsupported retry authorization schema_version")
    if raw["original_descriptor_sha256"] != descriptor.digest:
        raise GuardError("retry authorization descriptor substitution")
    if raw["task_id"] != descriptor.task_id or raw["defect_id"] != descriptor.defect_id:
        raise GuardError("retry authorization task or defect substitution")
    for key in ("authorization_id", "previous_authorization_ref", "new_authorization_ref"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise GuardError(f"invalid retry authorization {key}")
    if raw["new_authorization_ref"] == raw["previous_authorization_ref"]:
        raise GuardError("retry authorization must name a new authorization reference")
    failed_ids = raw["failed_attempt_ids"]
    if (
        not isinstance(failed_ids, list)
        or not failed_ids
        or not all(isinstance(item, str) and item for item in failed_ids)
        or len(failed_ids) != len(set(failed_ids))
    ):
        raise GuardError("retry authorization failed_attempt_ids invalid")
    return raw, sha256_bytes(canonical_bytes(raw))


def command_authorize_retry(args: argparse.Namespace) -> int:
    descriptor = validate_descriptor(Path(args.descriptor))
    authorization, authorization_digest = validate_retry_authorization(Path(args.authorization), descriptor)
    root = state_root_for(descriptor, Path(args.state_root) if args.state_root else None)
    _, lock_path, tasks_dir = registry_paths(root, descriptor)
    task_dir = tasks_dir / descriptor.task_id
    journal = Journal(task_dir)
    lock_payload = {
        "schema_version": SCHEMA_VERSION,
        "repository_identity": descriptor.repository_identity,
        "task_id": descriptor.task_id,
        "defect_id": descriptor.defect_id,
        "descriptor_sha256": descriptor.digest,
        "process_id": os.getpid(),
        "lock_id": str(uuid.uuid4()),
        "created_at_ns": time.time_ns(),
        "attempt_id": None,
    }
    with RepositoryLock(lock_path, lock_payload):
        registered = task_dir / "descriptor.json"
        if not registered.exists() or registered.read_bytes() != canonical_bytes(descriptor.raw):
            raise GuardError("retry authorization requires the exact registered descriptor")
        _, _, records = journal.verify()
        used_ids = {
            record.get("authorization_id")
            for record in records
            if record.get("event") == "RETRY_AUTHORIZED"
        }
        used_digests = {
            record.get("retry_authorization_sha256")
            for record in records
            if record.get("event") == "RETRY_AUTHORIZED"
        }
        if authorization["authorization_id"] in used_ids or authorization_digest in used_digests:
            raise GuardError("retry authorization replay rejected")
        if open_attempts(records):
            raise GuardError("retry authorization rejected while an attempt is unresolved")
        if successful_attempt_exists(journal):
            raise GuardError("retry authorization rejected after successful completion")
        current_ref, epoch_start = active_authorization(records, descriptor)
        used_references = {descriptor.authorization_ref} | {
            str(record["new_authorization_ref"])
            for record in records
            if record.get("event") == "RETRY_AUTHORIZED"
        }
        if authorization["new_authorization_ref"] in used_references:
            raise GuardError("retry authorization reference replay rejected")
        failures = epoch_failures(records, epoch_start)
        if len(failures) < descriptor.max_failed_attempts:
            raise GuardError("retry authorization rejected before circuit breaker threshold")
        expected_ids = [str(record["attempt_id"]) for record in failures]
        if authorization["failed_attempt_ids"] != expected_ids:
            raise GuardError("retry authorization failed-attempt binding mismatch")
        if authorization["previous_authorization_ref"] != current_ref:
            raise GuardError("retry authorization predecessor reference mismatch")
        journal.append(
            create_event(
                descriptor,
                "NONE",
                "RETRY_AUTHORIZED",
                authorization_ref=str(authorization["new_authorization_ref"]),
                authorization_id=str(authorization["authorization_id"]),
                previous_authorization_ref=current_ref,
                new_authorization_ref=str(authorization["new_authorization_ref"]),
                failed_attempt_ids=expected_ids,
                retry_authorization_sha256=authorization_digest,
            )
        )
    print(authorization_digest)
    return 0


def command_verify(args: argparse.Namespace) -> int:
    descriptor = validate_descriptor(Path(args.descriptor))
    root = state_root_for(descriptor, Path(args.state_root) if args.state_root else None)
    _, _, tasks_dir = registry_paths(root, descriptor)
    task_dir = tasks_dir / descriptor.task_id
    registered = task_dir / "descriptor.json"
    if not registered.exists() or registered.read_bytes() != canonical_bytes(descriptor.raw):
        raise GuardError("descriptor is not registered or conflicts with registry")
    Journal(task_dir).verify()
    verify_baseline_and_scope(descriptor)
    print("PASS")
    return 0


def command_recover(args: argparse.Namespace) -> int:
    descriptor = validate_descriptor(Path(args.descriptor))
    if not args.recovery_authorization_ref.strip():
        raise GuardError("explicit stale-lock recovery authorization required")
    root = state_root_for(descriptor, Path(args.state_root) if args.state_root else None)
    _, lock_path, tasks_dir = registry_paths(root, descriptor)
    if not lock_path.exists():
        raise GuardError("no repository lock exists")
    lock = load_json(lock_path)
    require_keys(
        lock,
        {
            "schema_version", "repository_identity", "task_id", "defect_id",
            "descriptor_sha256", "process_id", "lock_id", "created_at_ns", "attempt_id",
        },
        "repository lock",
    )
    if lock["repository_identity"] != descriptor.repository_identity:
        raise GuardError("lock repository identity conflict")
    if (
        lock["task_id"] != descriptor.task_id
        or lock["defect_id"] != descriptor.defect_id
        or lock["descriptor_sha256"] != descriptor.digest
    ):
        raise GuardError("recovery descriptor does not own the stale lock")
    process_query = query_process(int(lock["process_id"]))
    if not process_query.death_confirmed:
        raise GuardError(
            "unsafe stale-lock recovery rejected: process status "
            f"{process_query.status.value}, error_code={process_query.error_code}"
        )
    task_id = str(lock["task_id"])
    journal = Journal(tasks_dir / task_id)
    sequence, record_hash, records = journal.verify(allow_anchor_lag=True)
    atomic_write(journal.anchor_path, canonical_bytes({"sequence": sequence, "record_hash": record_hash}))
    used_recovery_refs = {
        record.get("recovery_authorization_ref")
        for record in records
        if record.get("event") == "STALE_LOCK_RECOVERED"
    }
    if args.recovery_authorization_ref in used_recovery_refs:
        raise GuardError("stale-lock recovery authorization replay rejected")
    unresolved = open_attempts(records)
    if len(unresolved) > 1:
        raise GuardError("ambiguous crash state: multiple unresolved attempts")
    locked_attempt = lock.get("attempt_id")
    active_ref, _ = active_authorization(records, descriptor)
    if unresolved:
        unresolved_id = str(unresolved[0]["attempt_id"])
        if locked_attempt is not None and locked_attempt != unresolved_id:
            raise GuardError("ambiguous crash state: lock and journal attempt identities differ")
        journal.append(
            create_event(
                descriptor,
                unresolved_id,
                "ATTEMPT_FINISHED",
                authorization_ref=active_ref,
                disposition="INTERRUPTED",
                failure="owner-authorized recovery after confirmed process death",
                summary_sha256=None,
                recovery_authorization_ref=args.recovery_authorization_ref,
                process_query=process_query.evidence(),
            )
        )
    elif locked_attempt is not None:
        starts, terminals = attempt_state(records)
        known = {str(record["attempt_id"]) for record in starts}
        if locked_attempt not in known or locked_attempt not in terminals:
            raise GuardError("ambiguous crash state: lock attempt has no complete journal lifecycle")
    journal.append(
        create_event(
            descriptor,
            "RECOVERY",
            "STALE_LOCK_RECOVERED",
            authorization_ref=active_ref,
            stale_lock=lock,
            recovery_authorization_ref=args.recovery_authorization_ref,
            process_query=process_query.evidence(),
            interrupted_attempt_id=str(unresolved[0]["attempt_id"]) if unresolved else None,
        )
    )
    lock_path.unlink()
    print("RECOVERED")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m engineering_execution_guard")
    result.add_argument("--state-root", help="test-only override; requires ATIS_GUARD_TEST_MODE=1")
    sub = result.add_subparsers(dest="subcommand", required=True)
    run = sub.add_parser("run", help="run one bounded attempt and its mandatory validation profile")
    run.add_argument("--descriptor", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=command_run)
    verify = sub.add_parser("verify", help="verify registered descriptor, journal, baseline, lock and scope")
    verify.add_argument("--descriptor", required=True)
    verify.set_defaults(handler=command_verify)
    recover = sub.add_parser("recover-lock", help="recover a lock only after owner-process death is established")
    recover.add_argument("--descriptor", required=True)
    recover.add_argument("--recovery-authorization-ref", required=True)
    recover.set_defaults(handler=command_recover)
    authorize = sub.add_parser("authorize-retry", help="append one owner-supplied retry authorization successor")
    authorize.add_argument("--descriptor", required=True)
    authorize.add_argument("--authorization", required=True)
    authorize.set_defaults(handler=command_authorize_retry)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return int(args.handler(args))
    except GuardError as exc:
        print(f"GUARD_REJECTED: {exc}", file=sys.stderr)
        return 2

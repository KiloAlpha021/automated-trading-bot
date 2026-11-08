"""Bounded tamper-evident security audit storage.

The HMAC key must come from an external protected-key provider.  This module
deliberately has no plaintext-key loader and makes no claim against a privileged
administrator.  Native DPAPI qualification is a separate Windows gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import BinaryIO, Callable, Final, Protocol


AUDIT_SCHEMA: Final = "ATIS-OC-SECURITY-AUDIT-V1"
GENESIS_DIGEST: Final = "0" * 64
MAX_EVENT_BYTES: Final = 4096
MAX_EVENTS: Final = 100_000
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}", re.ASCII)
_SUBJECT = re.compile(r"[A-Za-z0-9._:@/-]{1,128}", re.ASCII)


class AuditIntegrityError(RuntimeError):
    pass


class AuditStorageError(RuntimeError):
    pass


class AuditContentionError(AuditStorageError):
    pass


class AuditCommitUnconfirmed(AuditStorageError):
    pass


class ProtectedAuditKey(Protocol):
    """Returns key bytes already released by a qualified protected store."""

    def load_key(self) -> bytes: ...


FaultInjector = Callable[[str], None]
PathIdentity = tuple[int, int]


def _path_is_reparse(path: Path) -> bool:
    """Return true for every link/reparse form detectable by this runtime."""

    try:
        details = path.lstat()
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return (
            path.is_symlink()
            or path.is_junction()
            or bool(attributes & reparse_attribute)
        )
    except OSError as error:
        raise AuditStorageError("audit path safety cannot be established") from error


def _assert_safe_existing_path(path: Path, *, require_directory: bool) -> PathIdentity:
    """Reject links/reparse points in the path and return its stable identity."""

    if not path.is_absolute():
        raise AuditStorageError("audit path must be absolute")
    components = [*reversed(path.parents), path]
    try:
        for component in components:
            if not component.exists() or _path_is_reparse(component):
                raise AuditStorageError("audit path is missing or reparse-backed")
        details = path.stat(follow_symlinks=False)
    except AuditStorageError:
        raise
    except OSError as error:
        raise AuditStorageError("audit path safety cannot be established") from error
    if require_directory and not stat.S_ISDIR(details.st_mode):
        raise AuditStorageError("audit path is not a directory")
    return details.st_dev, details.st_ino


def _assert_safe_optional_path(path: Path) -> None:
    """Reject an existing link/reparse-backed audit file without creating it."""

    try:
        if path.exists() or path.is_symlink():
            if _path_is_reparse(path):
                raise AuditStorageError("audit file is reparse-backed")
    except AuditStorageError:
        raise
    except OSError as error:
        raise AuditStorageError("audit file safety cannot be established") from error


class _AuditWriterLock:
    """Non-blocking OS lock; the file may remain but ownership never does."""

    _thread_lock = threading.Lock()

    def __init__(self, path: Path, validate_storage: Callable[[], None]) -> None:
        self.path = path
        self._validate_storage = validate_storage
        self.handle: BinaryIO | None = None
        self.acquired = False

    def __enter__(self) -> "_AuditWriterLock":
        if not self._thread_lock.acquire(blocking=False):
            return self
        try:
            self._validate_storage()
            _assert_safe_optional_path(self.path)
            handle = self.path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    # The Linux type-checking environment exposes the POSIX
                    # ``msvcrt`` stub, which intentionally omits Windows-only
                    # locking members.  Runtime access is guarded by os.name.
                    msvcrt.locking(  # type: ignore[attr-defined]
                        handle.fileno(), msvcrt.LK_NBLCK, 1  # type: ignore[attr-defined]
                    )
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                handle.close()
                self._thread_lock.release()
                return self
            self.handle = handle
            self.acquired = True
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.acquired or self.handle is None:
            return
        handle = self.handle
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(  # type: ignore[attr-defined]
                    handle.fileno(), msvcrt.LK_UNLCK, 1  # type: ignore[attr-defined]
                )
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._thread_lock.release()
            self.acquired = False


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_id: str
    occurred_at_utc: str
    event_type: str
    outcome: str
    reason: str
    subject_ref: str | None
    previous_digest: str
    digest: str
    mac: str

    def mapping(self, *, include_integrity: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at_utc": self.occurred_at_utc,
            "outcome": self.outcome,
            "previous_digest": self.previous_digest,
            "reason": self.reason,
            "schema": AUDIT_SCHEMA,
            "sequence": self.sequence,
            "subject_ref": self.subject_ref,
        }
        if include_integrity:
            value.update({"digest": self.digest, "mac": self.mac})
        return value

    def canonical_bytes(self) -> bytes:
        return _canonical(self.mapping())


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _utc_text(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("audit timestamp must be UTC")
    return now.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validated_code(value: str, name: str) -> str:
    if type(value) is not str or _CODE.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


class SecurityAuditLog:
    """One immutable canonical file per event plus an atomic head pointer."""

    def __init__(
        self,
        root: Path,
        key_provider: ProtectedAuditKey,
        *,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("audit root must be absolute")
        key = key_provider.load_key()
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("protected audit key is unavailable")
        self._key = key
        self.root = root
        self.events = root / "events"
        self.head = root / "head.json"
        self.writer_lock = root / "writer.lock"
        self._fault_injector = fault_injector
        self._root_identity = _assert_safe_existing_path(
            root, require_directory=True
        )
        self._events_identity = _assert_safe_existing_path(
            self.events, require_directory=True
        )
        _assert_safe_optional_path(self.head)
        _assert_safe_optional_path(self.writer_lock)

    def _assert_storage_identity(self) -> None:
        """Fail closed if validated storage was replaced or became unsafe."""

        if _assert_safe_existing_path(
            self.root, require_directory=True
        ) != self._root_identity:
            raise AuditStorageError("audit root identity changed")
        if _assert_safe_existing_path(
            self.events, require_directory=True
        ) != self._events_identity:
            raise AuditStorageError("audit events identity changed")
        _assert_safe_optional_path(self.head)
        _assert_safe_optional_path(self.writer_lock)

    def _event_path(self, sequence: int) -> Path:
        return self.events / f"{sequence:012d}.json"

    def _fault(self, phase: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(phase)

    def _load_head(self) -> tuple[int, str]:
        self._assert_storage_identity()
        if not self.head.exists():
            return 0, GENESIS_DIGEST
        try:
            raw = self.head.read_bytes()
            item = json.loads(raw.decode("ascii"))
            if (
                type(item) is not dict
                or frozenset(item) != frozenset({"digest", "sequence"})
                or type(item["sequence"]) is not int
                or type(item["digest"]) is not str
                or _canonical(item) != raw
            ):
                raise AuditIntegrityError("audit head is malformed")
            return item["sequence"], item["digest"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditIntegrityError("audit head is unreadable") from error

    def append(
        self,
        event_type: str,
        outcome: str,
        reason: str,
        *,
        subject_ref: str | None = None,
        now: datetime | None = None,
    ) -> AuditEvent:
        self._assert_storage_identity()
        with _AuditWriterLock(self.writer_lock, self._assert_storage_identity) as lock:
            if not lock.acquired:
                raise AuditContentionError("security audit writer is busy")
            return self._append_exclusive(
                event_type, outcome, reason, subject_ref=subject_ref, now=now
            )

    def _append_exclusive(
        self,
        event_type: str,
        outcome: str,
        reason: str,
        *,
        subject_ref: str | None,
        now: datetime | None,
    ) -> AuditEvent:
        event_type = _validated_code(event_type, "event_type")
        outcome = _validated_code(outcome, "outcome")
        reason = _validated_code(reason, "reason")
        if subject_ref is not None and (
            type(subject_ref) is not str or _SUBJECT.fullmatch(subject_ref) is None
        ):
            raise ValueError("subject_ref is invalid")
        sequence, previous = self.verify()
        if sequence >= MAX_EVENTS:
            raise AuditStorageError("audit retention boundary reached")
        sequence += 1
        occurred = _utc_text(datetime.now(UTC) if now is None else now)
        event_id = sha256(
            f"{sequence}:{occurred}:{event_type}:{outcome}:{reason}".encode("ascii")
        ).hexdigest()
        unsigned = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at_utc": occurred,
            "outcome": outcome,
            "previous_digest": previous,
            "reason": reason,
            "schema": AUDIT_SCHEMA,
            "sequence": sequence,
            "subject_ref": subject_ref,
        }
        digest = sha256(_canonical(unsigned)).hexdigest()
        mac = hmac.new(self._key, digest.encode("ascii"), sha256).hexdigest()
        event = AuditEvent(
            sequence, event_id, occurred, event_type, outcome, reason,
            subject_ref, previous, digest, mac
        )
        raw = event.canonical_bytes()
        if len(raw) > MAX_EVENT_BYTES:
            raise AuditStorageError("audit event exceeds limit")
        path = self._event_path(sequence)
        candidate = self.events / f".{sequence:012d}.{os.urandom(8).hex()}.tmp"
        head_candidate = self.root / f".head.{os.urandom(8).hex()}.tmp"
        head_replaced = False
        try:
            self._fault("before_event_write")
            self._assert_storage_identity()
            with candidate.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self._fault("before_event_publish")
            self._assert_storage_identity()
            os.link(candidate, path)
            candidate.unlink()
            self._fault("after_event_publish")
            self._assert_storage_identity()
            head_raw = _canonical({"digest": digest, "sequence": sequence})
            with head_candidate.open("xb") as handle:
                handle.write(head_raw)
                handle.flush()
                os.fsync(handle.fileno())
            self._fault("before_head_replace")
            self._assert_storage_identity()
            os.replace(head_candidate, self.head)
            head_replaced = True
            self._fault("after_head_replace")
            self.verify()
            return event
        except (OSError, AuditIntegrityError) as error:
            if head_replaced:
                raise AuditCommitUnconfirmed(
                    "security audit append committed but verification is unconfirmed"
                ) from error
            raise AuditStorageError("security audit append failed") from error
        finally:
            candidate.unlink(missing_ok=True)
            head_candidate.unlink(missing_ok=True)

    def recover_interrupted_append(self) -> int:
        """Delete only uncommitted tail/candidate files while holding ownership."""

        self._assert_storage_identity()
        with _AuditWriterLock(self.writer_lock, self._assert_storage_identity) as lock:
            if not lock.acquired:
                raise AuditContentionError("security audit writer is busy")
            head_sequence, _ = self._verify_committed()
            removed = 0
            for item in self.events.iterdir():
                if item.name.startswith(".") and item.suffix == ".tmp":
                    item.unlink()
                    removed += 1
                elif item.suffix == ".json" and item.stem.isdigit():
                    if int(item.stem) > head_sequence:
                        item.unlink()
                        removed += 1
            for item in self.root.glob(".head.*.tmp"):
                item.unlink()
                removed += 1
            self.verify()
            return removed

    def _verify_committed(self) -> tuple[int, str]:
        self._assert_storage_identity()
        head_sequence, head_digest = self._load_head()
        previous = GENESIS_DIGEST
        for sequence in range(1, head_sequence + 1):
            path = self._event_path(sequence)
            try:
                _assert_safe_optional_path(path)
                raw = path.read_bytes()
                if len(raw) > MAX_EVENT_BYTES:
                    raise AuditIntegrityError("audit event exceeds limit")
                item = json.loads(raw.decode("ascii"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AuditIntegrityError("audit event is unreadable") from error
            if type(item) is not dict or _canonical(item) != raw:
                raise AuditIntegrityError("audit event is not canonical")
            required = frozenset(
                {"schema", "sequence", "event_id", "occurred_at_utc", "event_type",
                 "outcome", "reason", "subject_ref", "previous_digest", "digest", "mac"}
            )
            if frozenset(item) != required or item["schema"] != AUDIT_SCHEMA:
                raise AuditIntegrityError("audit event schema is invalid")
            if item["sequence"] != sequence or item["previous_digest"] != previous:
                raise AuditIntegrityError("audit chain is discontinuous")
            unsigned = {key: item[key] for key in item if key not in {"digest", "mac"}}
            digest = sha256(_canonical(unsigned)).hexdigest()
            expected_mac = hmac.new(self._key, digest.encode("ascii"), sha256).hexdigest()
            if not hmac.compare_digest(str(item["digest"]), digest) or not hmac.compare_digest(
                str(item["mac"]), expected_mac
            ):
                raise AuditIntegrityError("audit integrity verification failed")
            previous = digest
        if previous != head_digest:
            raise AuditIntegrityError("audit head does not match chain")
        return head_sequence, head_digest

    def verify(self) -> tuple[int, str]:
        head_sequence, head_digest = self._verify_committed()
        extras = [p for p in self.events.glob("*.json") if p.name != f"{head_sequence:012d}.json"]
        if head_sequence == 0 and extras:
            raise AuditIntegrityError("uncommitted audit events exist")
        if any(int(p.stem) > head_sequence for p in extras if p.stem.isdigit()):
            raise AuditIntegrityError("uncommitted audit events exist")
        return head_sequence, head_digest

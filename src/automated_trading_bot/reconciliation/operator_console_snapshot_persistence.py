"""Atomic local persistence for reconciliation snapshots.

The store is an in-process, read-only integration foundation.  It creates no
listener, scheduler, broker connection, or trading-authority path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import BinaryIO, Final, cast

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    FreshnessState,
    NO_TRADING_AUTHORITY,
    PerformanceSnapshotV1,
    ReconciliationSnapshotAdapter,
    ReconciliationSnapshotV1,
    SCHEMA_VERSION,
    SnapshotReadResult,
    SnapshotValidationError,
    validate_snapshot,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.reconciliation_performance import (
    DiscrepancyCode,
    ReconciliationState,
)


POINTER_VERSION: Final = "ATIS-OC-RECONCILIATION-POINTER-V1"
PUBLICATION_EVIDENCE_VERSION: Final = "ATIS-OC-RECONCILIATION-PUBLICATION-V1"
MAX_SNAPSHOT_BYTES: Final = 1_048_576
MAX_POINTER_BYTES: Final = 4096
MAX_EVIDENCE_BYTES: Final = 16_384
MAX_ROOT_PATH_CHARS: Final = 180
MAX_CANDIDATES_REPORTED: Final = 1024
_VERSION_FILE = re.compile(r"observation-[0-9a-f]{64}\.json", re.ASCII)
_CANDIDATE_TOKEN = re.compile(r"[0-9a-f]{32}", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_ALLOWED_ROOT_ENTRIES = frozenset(
    {"versions", "candidates", "evidence", "current.json", "writer.lock"}
)


class PersistenceConfigurationError(ValueError):
    pass


class PersistenceIntegrityError(ValueError):
    pass


class PersistenceIoError(OSError):
    def __init__(self, phase: str, *, pointer_committed: bool) -> None:
        super().__init__(f"snapshot persistence failed during {phase}")
        self.phase = phase
        self.pointer_committed = pointer_committed


class PublicationState(StrEnum):
    PUBLISHED = "PUBLISHED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    BUSY = "BUSY"
    COMMITTED_UNCONFIRMED = "COMMITTED_UNCONFIRMED"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    state: PublicationState
    observation_id: str
    storage_digest: str
    version_file: str
    pointer_committed: bool
    evidence_file: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    current: SnapshotReadResult
    ignored_candidates: int
    candidate_count_bounded: bool
    promoted: bool = False


@dataclass(frozen=True, slots=True)
class _Pointer:
    pointer_version: str
    schema_version: str
    observation_id: str
    storage_digest: str
    version_file: str
    published_at_utc: str

    def canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "observation_id": self.observation_id,
                "pointer_version": self.pointer_version,
                "published_at_utc": self.published_at_utc,
                "schema_version": self.schema_version,
                "storage_digest": self.storage_digest,
                "version_file": self.version_file,
            }
        )


FaultInjector = Callable[[str], None]
TokenFactory = Callable[[], str]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _sha256(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _utc_text(value: Timestamp) -> str:
    return value.value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _default_token() -> str:
    return os.urandom(16).hex()


def _is_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _reject_reparse(path: Path, name: str) -> None:
    if _is_reparse(path):
        raise PersistenceConfigurationError(f"{name} must not be a reparse point")


def _read_bounded(path: Path, limit: int) -> bytes:
    _reject_reparse(path, path.name)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise
    if size > limit:
        raise PersistenceIntegrityError(f"{path.name} exceeds its byte limit")
    with path.open("rb") as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise PersistenceIntegrityError(f"{path.name} exceeds its byte limit")
    return value


def _load_json(value: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise PersistenceIntegrityError("duplicate JSON member")
            result[key] = item
        return result

    try:
        return json.loads(value.decode("ascii"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PersistenceIntegrityError("stored JSON is malformed") from error


def _exact_keys(value: object, keys: frozenset[str], name: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise PersistenceIntegrityError(f"{name} has unsupported fields")
    return value


def _string(item: dict[str, object], name: str) -> str:
    value = item[name]
    if type(value) is not str:
        raise PersistenceIntegrityError(f"{name} must be a string")
    return value


def _boolean(item: dict[str, object], name: str) -> bool:
    value = item[name]
    if type(value) is not bool:
        raise PersistenceIntegrityError(f"{name} must be a boolean")
    return value


def _parse_timestamp(value: object, name: str) -> Timestamp:
    if type(value) is not str or not value.endswith("Z"):
        raise PersistenceIntegrityError(f"{name} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        timestamp = Timestamp(parsed)
    except (TypeError, ValueError) as error:
        raise PersistenceIntegrityError(f"{name} is invalid") from error
    if _utc_text(timestamp) != value:
        raise PersistenceIntegrityError(f"{name} is not canonical UTC")
    return timestamp


def _parse_evidence_ref(value: object) -> EvidenceRef:
    item = _exact_keys(
        value,
        frozenset({"source_id", "dataset_id", "evidence_id", "content_digest"}),
        "evidence reference",
    )
    try:
        return EvidenceRef(
            SourceId(_string(item, "source_id")),
            DatasetId(_string(item, "dataset_id")),
            EvidenceId(_string(item, "evidence_id")),
            EvidenceContentDigest(_string(item, "content_digest")),
        )
    except (TypeError, ValueError) as error:
        raise PersistenceIntegrityError("evidence reference is invalid") from error


def _parse_performance(value: object) -> PerformanceSnapshotV1 | None:
    if value is None:
        return None
    names = frozenset(
        {
            "gross_pnl", "commission_fees", "bid_ask_spread", "slippage",
            "market_impact", "additional_friction", "net_pnl", "realised_pnl",
            "unrealised_pnl", "portfolio_value", "turnover", "participation", "capacity",
        }
    )
    item = _exact_keys(value, names, "performance")
    try:
        return PerformanceSnapshotV1(
            gross_pnl=Decimal(_string(item, "gross_pnl")),
            commission_fees=Decimal(_string(item, "commission_fees")),
            bid_ask_spread=Decimal(_string(item, "bid_ask_spread")),
            slippage=Decimal(_string(item, "slippage")),
            market_impact=Decimal(_string(item, "market_impact")),
            additional_friction=Decimal(_string(item, "additional_friction")),
            net_pnl=Decimal(_string(item, "net_pnl")),
            realised_pnl=Decimal(_string(item, "realised_pnl")),
            unrealised_pnl=Decimal(_string(item, "unrealised_pnl")),
            portfolio_value=Decimal(_string(item, "portfolio_value")),
            turnover=Decimal(_string(item, "turnover")),
            participation=_string(item, "participation"),
            capacity=_string(item, "capacity"),
        )
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PersistenceIntegrityError("performance is invalid") from error


def _snapshot_from_bytes(value: bytes, *, evaluated_at_utc: Timestamp) -> ReconciliationSnapshotV1:
    keys = frozenset(
        {
            "schema_version", "observation_id", "source_authority", "source_run_id",
            "evidence_refs", "evidence_digest", "reconciliation_state",
            "discrepancy_codes", "observed_at_utc", "produced_at_utc",
            "freshness_state", "simulation_only", "trading_authority", "performance",
        }
    )
    item = _exact_keys(_load_json(value), keys, "snapshot")
    if type(item["evidence_refs"]) is not list:
        raise PersistenceIntegrityError("evidence_refs must be an array")
    if type(item["discrepancy_codes"]) is not list:
        raise PersistenceIntegrityError("discrepancy_codes must be an array")
    evidence_refs = cast(list[object], item["evidence_refs"])
    discrepancy_codes = cast(list[object], item["discrepancy_codes"])
    try:
        snapshot = ReconciliationSnapshotV1(
            schema_version=_string(item, "schema_version"),
            observation_id=_string(item, "observation_id"),
            source_authority=_string(item, "source_authority"),
            source_run_id=ResearchRunId(_string(item, "source_run_id")),
            evidence_refs=tuple(_parse_evidence_ref(ref) for ref in evidence_refs),
            evidence_digest=_string(item, "evidence_digest"),
            reconciliation_state=ReconciliationState(
                _string(item, "reconciliation_state")
            ),
            discrepancy_codes=tuple(
                DiscrepancyCode(code)
                for value in discrepancy_codes
                for code in (_string({"code": value}, "code"),)
            ),
            observed_at_utc=_parse_timestamp(item["observed_at_utc"], "observed_at_utc"),
            produced_at_utc=_parse_timestamp(item["produced_at_utc"], "produced_at_utc"),
            freshness_state=FreshnessState(_string(item, "freshness_state")),
            simulation_only=_boolean(item, "simulation_only"),
            trading_authority=_string(item, "trading_authority"),
            performance=_parse_performance(item["performance"]),
        )
        validate_snapshot(snapshot, evaluated_at_utc=evaluated_at_utc)
    except (SnapshotValidationError, TypeError, ValueError) as error:
        raise PersistenceIntegrityError("snapshot validation failed") from error
    if snapshot.canonical_bytes() != value:
        raise PersistenceIntegrityError("snapshot bytes are not canonical")
    return snapshot


class _WriterLock:
    def __init__(self, path: Path, thread_lock: threading.Lock) -> None:
        self._path = path
        self._thread_lock = thread_lock
        self._handle: BinaryIO | None = None
        self.acquired = False

    def __enter__(self) -> "_WriterLock":
        if not self._thread_lock.acquire(blocking=False):
            return self
        try:
            _reject_reparse(self._path, "writer.lock")
            handle = self._path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            try:
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                self._thread_lock.release()
                return self
            self._handle = handle
            self.acquired = True
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.acquired or self._handle is None:
            return
        handle = self._handle
        try:
            handle.seek(0)
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
            self._thread_lock.release()
            self.acquired = False


class AtomicSnapshotStore:
    """A bounded, component-controlled local snapshot store."""

    def __init__(
        self,
        root: Path,
        *,
        token_factory: TokenFactory = _default_token,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise PersistenceConfigurationError("root must be an absolute Path")
        if os.name != "nt":
            raise PersistenceConfigurationError("this persistence contract is Windows-only")
        if len(str(root)) > MAX_ROOT_PATH_CHARS:
            raise PersistenceConfigurationError("root path exceeds the supported limit")
        if root.name in {"", ".", ".."}:
            raise PersistenceConfigurationError("root is invalid")
        self.root = root
        self.versions = root / "versions"
        self.candidates = root / "candidates"
        self.evidence = root / "evidence"
        self.current = root / "current.json"
        self.writer_lock = root / "writer.lock"
        self._token_factory = token_factory
        self._fault_injector = fault_injector
        self._thread_lock = threading.Lock()
        self._prepare_layout()

    def _prepare_layout(self) -> None:
        if self.root.exists():
            _reject_reparse(self.root, "root")
            if not self.root.is_dir():
                raise PersistenceConfigurationError("root must be a directory")
        else:
            if not self.root.parent.is_dir():
                raise PersistenceConfigurationError("root parent must already exist")
            self.root.mkdir()
        for directory in (self.versions, self.candidates, self.evidence):
            if directory.exists():
                _reject_reparse(directory, directory.name)
                if not directory.is_dir():
                    raise PersistenceConfigurationError(f"{directory.name} must be a directory")
            else:
                directory.mkdir()
        unexpected = {item.name for item in self.root.iterdir()} - _ALLOWED_ROOT_ENTRIES
        if unexpected:
            raise PersistenceConfigurationError("root contains unsupported entries")
        devices = {self.root.stat().st_dev, self.versions.stat().st_dev,
                   self.candidates.stat().st_dev, self.evidence.stat().st_dev}
        if len(devices) != 1:
            raise PersistenceConfigurationError("store paths must share one filesystem")

    def _fault(self, phase: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(phase)

    def _candidate_token(self) -> str:
        token = self._token_factory()
        if type(token) is not str or _CANDIDATE_TOKEN.fullmatch(token) is None:
            raise PersistenceConfigurationError("candidate token is invalid")
        return token

    def _version_name(self, observation_id: str) -> str:
        return f"observation-{sha256(observation_id.encode('ascii')).hexdigest()}.json"

    def _write_synced(self, path: Path, value: bytes, limit: int) -> None:
        if len(value) > limit:
            raise PersistenceIntegrityError("candidate exceeds its byte limit")
        self._fault("before_write")
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        self._fault("after_sync")
        if _read_bounded(path, limit) != value:
            raise PersistenceIntegrityError("candidate verification failed")

    def _read_pointer(self) -> _Pointer:
        raw = _read_bounded(self.current, MAX_POINTER_BYTES)
        keys = frozenset(
            {"pointer_version", "schema_version", "observation_id", "storage_digest",
             "version_file", "published_at_utc"}
        )
        item = _exact_keys(_load_json(raw), keys, "current pointer")
        pointer = _Pointer(
            pointer_version=_string(item, "pointer_version"),
            schema_version=_string(item, "schema_version"),
            observation_id=_string(item, "observation_id"),
            storage_digest=_string(item, "storage_digest"),
            version_file=_string(item, "version_file"),
            published_at_utc=_string(item, "published_at_utc"),
        )
        if raw != pointer.canonical_bytes():
            raise PersistenceIntegrityError("current pointer is not canonical")
        if pointer.pointer_version != POINTER_VERSION or pointer.schema_version != SCHEMA_VERSION:
            raise PersistenceIntegrityError("current pointer version is unsupported")
        if _VERSION_FILE.fullmatch(pointer.version_file) is None:
            raise PersistenceIntegrityError("current pointer version path is invalid")
        if _DIGEST.fullmatch(pointer.storage_digest) is None:
            raise PersistenceIntegrityError("current pointer digest is invalid")
        _parse_timestamp(pointer.published_at_utc, "published_at_utc")
        return pointer

    def publish(
        self,
        snapshot: ReconciliationSnapshotV1,
        *,
        evaluated_at_utc: Timestamp,
        published_at_utc: Timestamp,
    ) -> PublicationResult:
        validate_snapshot(snapshot, evaluated_at_utc=evaluated_at_utc)
        published_text = _utc_text(published_at_utc)
        if published_at_utc.value > evaluated_at_utc.value:
            raise SnapshotValidationError("publication timestamp is in the future")
        raw = snapshot.canonical_bytes()
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise PersistenceIntegrityError("snapshot exceeds its byte limit")
        storage_digest = _sha256(raw)
        version_file = self._version_name(snapshot.observation_id)
        version_path = self.versions / version_file
        with _WriterLock(self.writer_lock, self._thread_lock) as lock:
            if not lock.acquired:
                return PublicationResult(
                    PublicationState.BUSY, snapshot.observation_id, storage_digest,
                    version_file, False, None, "writer lock is busy"
                )
            version_already_exists = version_path.exists()
            if version_already_exists:
                try:
                    _reject_reparse(version_path, "version")
                    existing = _read_bounded(version_path, MAX_SNAPSHOT_BYTES)
                except (OSError, PersistenceIntegrityError):
                    return PublicationResult(
                        PublicationState.CONFLICT, snapshot.observation_id, storage_digest,
                        version_file, False, None, "existing immutable version is untrusted"
                    )
                if existing != raw:
                    return PublicationResult(
                        PublicationState.CONFLICT, snapshot.observation_id, storage_digest,
                        version_file, False, None, "observation identity already has different bytes"
                    )
                # A valid immutable version is not proof that the current pointer
                # was committed. A new explicit publish may complete that step.
                if self.current.exists():
                    try:
                        _reject_reparse(self.current, "current pointer")
                        current_pointer = self._read_pointer()
                        current_read = self.read_current(evaluated_at_utc=evaluated_at_utc)
                    except (OSError, PersistenceIntegrityError):
                        raise PersistenceIntegrityError("existing current pointer is untrusted")
                    if current_read.snapshot is None:
                        raise PersistenceIntegrityError("existing current observation is untrusted")
                    if (current_pointer.observation_id == snapshot.observation_id
                            and current_pointer.storage_digest == storage_digest
                            and current_pointer.version_file == version_file):
                        return PublicationResult(
                            PublicationState.DUPLICATE, snapshot.observation_id,
                            storage_digest, version_file, False, None,
                            "current pointer already references identical verified version"
                        )
            token = self._candidate_token()
            snapshot_candidate = self.candidates / f"{version_file}.{token}.candidate"
            pointer_candidate = self.candidates / f"current.{token}.candidate"
            pointer_committed = False
            try:
                if not version_already_exists:
                    self._fault("before_snapshot_write")
                    self._write_synced(snapshot_candidate, raw, MAX_SNAPSHOT_BYTES)
                    self._fault("before_version_publish")
                    os.link(snapshot_candidate, version_path)
                    snapshot_candidate.unlink()
                if _read_bounded(version_path, MAX_SNAPSHOT_BYTES) != raw:
                    raise PersistenceIntegrityError("published version verification failed")
                pointer = _Pointer(
                    POINTER_VERSION, SCHEMA_VERSION, snapshot.observation_id,
                    storage_digest, version_file, published_text
                )
                pointer_raw = pointer.canonical_bytes()
                self._fault("before_pointer_write")
                self._write_synced(pointer_candidate, pointer_raw, MAX_POINTER_BYTES)
                self._fault("before_pointer_replace")
                os.replace(pointer_candidate, self.current)
                pointer_committed = True
                self._fault("after_pointer_replace")
                verified_pointer = self._read_pointer()
                if verified_pointer != pointer:
                    raise PersistenceIntegrityError("published pointer verification failed")
                if _sha256(_read_bounded(version_path, MAX_SNAPSHOT_BYTES)) != storage_digest:
                    raise PersistenceIntegrityError("published version digest mismatch")
                evidence_body = {
                    "evidence_version": PUBLICATION_EVIDENCE_VERSION,
                    "observation_id": snapshot.observation_id,
                    "pointer_digest": _sha256(pointer_raw),
                    "published_at_utc": published_text,
                    "schema_version": SCHEMA_VERSION,
                    "storage_digest": storage_digest,
                    "trading_authority": NO_TRADING_AUTHORITY,
                    "version_file": version_file,
                }
                evidence_raw = _canonical_json(evidence_body)
                evidence_name = f"publication-{sha256(evidence_raw).hexdigest()}.json"
                evidence_path = self.evidence / evidence_name
                try:
                    self._fault("before_evidence_write")
                    self._write_synced(evidence_path, evidence_raw, MAX_EVIDENCE_BYTES)
                except (OSError, PersistenceIntegrityError):
                    return PublicationResult(
                        PublicationState.COMMITTED_UNCONFIRMED, snapshot.observation_id,
                        storage_digest, version_file, True, None,
                        "pointer committed but publication evidence is unconfirmed"
                    )
                return PublicationResult(
                    PublicationState.PUBLISHED, snapshot.observation_id, storage_digest,
                    version_file, True, evidence_name, "publication verified"
                )
            except (OSError, PersistenceIntegrityError) as error:
                raise PersistenceIoError(
                    "after_pointer_replace" if pointer_committed else "before_pointer_replace",
                    pointer_committed=pointer_committed,
                ) from error
            finally:
                for candidate in (snapshot_candidate, pointer_candidate):
                    try:
                        candidate.unlink(missing_ok=True)
                    except OSError:
                        pass

    def read_current(self, *, evaluated_at_utc: Timestamp) -> SnapshotReadResult:
        if not self.current.exists():
            return SnapshotReadResult(
                AdapterReadState.UNAVAILABLE, None, ("CURRENT_POINTER_MISSING",)
            )
        try:
            pointer = self._read_pointer()
            version_path = self.versions / pointer.version_file
            _reject_reparse(version_path, "version")
            raw = _read_bounded(version_path, MAX_SNAPSHOT_BYTES)
            if _sha256(raw) != pointer.storage_digest:
                raise PersistenceIntegrityError("version digest mismatch")
            snapshot = _snapshot_from_bytes(raw, evaluated_at_utc=evaluated_at_utc)
            if snapshot.observation_id != pointer.observation_id:
                raise PersistenceIntegrityError("pointer observation mismatch")
            return ReconciliationSnapshotAdapter(enabled=True).read(
                snapshot, evaluated_at_utc=evaluated_at_utc
            )
        except (OSError, PersistenceIntegrityError, SnapshotValidationError, TypeError, ValueError):
            return SnapshotReadResult(
                AdapterReadState.UNKNOWN, None, ("PERSISTED_SNAPSHOT_UNTRUSTED",)
            )

    def recover(self, *, evaluated_at_utc: Timestamp) -> RecoveryResult:
        count = 0
        bounded = True
        try:
            for item in self.candidates.iterdir():
                _reject_reparse(item, "candidate")
                count += 1
                if count >= MAX_CANDIDATES_REPORTED:
                    bounded = False
                    break
        except OSError:
            return RecoveryResult(
                SnapshotReadResult(AdapterReadState.UNKNOWN, None, ("RECOVERY_SCAN_FAILED",)),
                count,
                bounded,
            )
        return RecoveryResult(
            self.read_current(evaluated_at_utc=evaluated_at_utc), count, bounded
        )

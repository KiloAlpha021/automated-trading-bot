from datetime import UTC, datetime, timedelta
from decimal import Decimal
import inspect
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)
from automated_trading_bot.reconciliation import operator_console_snapshot_persistence as module
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    FreshnessState,
    NO_TRADING_AUTHORITY,
    evidence_digest_for,
    produce_reconciliation_snapshot,
)
from automated_trading_bot.reconciliation.operator_console_snapshot_persistence import (
    AtomicSnapshotStore,
    PersistenceConfigurationError,
    PersistenceIoError,
    PersistenceIntegrityError,
    PublicationState,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.reconciliation_performance import (
    CostDrag,
    MetricAvailability,
    PerformanceTruth,
    ReconciliationResult,
    ReconciliationState,
)


NOW = Timestamp(datetime(2026, 11, 6, 12, tzinfo=UTC))
OBSERVED = Timestamp(NOW.value - timedelta(minutes=2))
PRODUCED = Timestamp(NOW.value - timedelta(minutes=1))
PUBLISHED = Timestamp(NOW.value - timedelta(seconds=30))
RUN_ID = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:atis/stage4-reconciliation"),
        DatasetId("dataset:atis/research-simulation"),
        EvidenceId("evidence:atis/reconciliation-persistence"),
        EvidenceContentDigest.from_bytes(b"qualified-reconciliation-evidence"),
    )


def _snapshot(*, net_pnl: str = "85"):
    costs = CostDrag(*(Decimal(value) for value in ("1", "2", "3", "4", "5")))
    performance = PerformanceTruth(
        Decimal("100"), costs, Decimal(net_pnl), Decimal("80"), Decimal("5"),
        Decimal("25085"), Decimal("0.4"), MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )
    refs = (_evidence(),)
    return produce_reconciliation_snapshot(
        ReconciliationResult(ReconciliationState.RECONCILED, (), performance),
        source_run_id=RUN_ID,
        evidence_refs=refs,
        expected_evidence_digest=evidence_digest_for(refs),
        observed_at_utc=OBSERVED,
        produced_at_utc=PRODUCED,
        evaluated_at_utc=NOW,
    )


def _store(tmp_path: Path, **kwargs: object) -> AtomicSnapshotStore:
    return AtomicSnapshotStore(
        tmp_path / "snapshot-store",
        token_factory=lambda: "1" * 32,
        **kwargs,
    )


def _publish(store: AtomicSnapshotStore, snapshot: object | None = None):
    return store.publish(
        _snapshot() if snapshot is None else snapshot,
        evaluated_at_utc=NOW,
        published_at_utc=PUBLISHED,
    )


def test_valid_publication_and_retrieval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot()
    result = _publish(store, snapshot)
    assert result.state is PublicationState.PUBLISHED
    assert result.pointer_committed is True
    assert result.evidence_file is not None
    assert (store.versions / result.version_file).read_bytes() == snapshot.canonical_bytes()
    read = store.read_current(evaluated_at_utc=NOW)
    assert read.state is AdapterReadState.UNKNOWN
    assert read.snapshot == snapshot
    assert read.reasons == ("FRESHNESS_UNVERIFIED",)


def test_duplicate_publication_is_explicit_and_does_not_republish(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _publish(store)
    pointer_before = store.current.read_bytes()
    second = _publish(store)
    assert first.state is PublicationState.PUBLISHED
    assert second.state is PublicationState.DUPLICATE
    assert second.pointer_committed is False
    assert store.current.read_bytes() == pointer_before


def test_retry_after_version_publish_before_pointer_replace(tmp_path: Path) -> None:
    snapshot = _snapshot()

    def interrupt(phase: str) -> None:
        if phase == "before_pointer_replace":
            raise OSError("simulated interruption")

    interrupted = _store(tmp_path, fault_injector=interrupt)
    with pytest.raises(PersistenceIoError):
        _publish(interrupted, snapshot)
    version = interrupted.versions / interrupted._version_name(snapshot.observation_id)
    assert version.read_bytes() == snapshot.canonical_bytes()
    assert not interrupted.current.exists()

    resumed = _store(tmp_path)
    result = _publish(resumed, snapshot)
    assert result.state is PublicationState.PUBLISHED
    assert result.pointer_committed is True
    assert resumed.read_current(evaluated_at_utc=NOW).snapshot == snapshot


def test_retry_orphaned_version_preserves_previous_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    previous = _snapshot()
    _publish(store, previous)
    old_pointer = store.current.read_bytes()

    def interrupt(phase: str) -> None:
        if phase == "before_pointer_replace":
            raise OSError("simulated interruption")

    next_snapshot = _snapshot(net_pnl="84")
    with pytest.raises(PersistenceIoError):
        _publish(_store(tmp_path, fault_injector=interrupt), next_snapshot)
    assert store.current.read_bytes() == old_pointer
    result = _publish(store, next_snapshot)
    assert result.state is PublicationState.PUBLISHED
    assert store.read_current(evaluated_at_utc=NOW).snapshot == next_snapshot


def test_retry_orphaned_version_rejects_invalid_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot()
    version = store.versions / store._version_name(snapshot.observation_id)
    version.write_bytes(snapshot.canonical_bytes())
    store.current.write_bytes(b"corrupt")
    with pytest.raises(PersistenceIntegrityError):
        _publish(store, snapshot)
    assert store.current.read_bytes() == b"corrupt"


def test_conflicting_immutable_version_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot()
    version = store.versions / store._version_name(snapshot.observation_id)
    version.write_bytes(b"different")
    result = _publish(store, snapshot)
    assert result.state is PublicationState.CONFLICT
    assert not store.current.exists()


def test_missing_snapshot_is_unavailable(tmp_path: Path) -> None:
    read = _store(tmp_path).read_current(evaluated_at_utc=NOW)
    assert read.state is AdapterReadState.UNAVAILABLE
    assert read.snapshot is None


def test_corrupted_version_is_unknown_not_trusted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _publish(store)
    (store.versions / result.version_file).write_bytes(b"corrupt")
    assert store.read_current(evaluated_at_utc=NOW).state is AdapterReadState.UNKNOWN


def test_corrupted_pointer_is_unknown_not_trusted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish(store)
    store.current.write_bytes(b"{")
    assert store.read_current(evaluated_at_utc=NOW).state is AdapterReadState.UNKNOWN


def test_interrupted_candidate_write_preserves_previous_pointer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish(store)
    pointer_before = store.current.read_bytes()

    def fail(phase: str) -> None:
        if phase == "before_snapshot_write":
            raise OSError("injected interruption")

    failing = AtomicSnapshotStore(
        store.root, token_factory=lambda: "2" * 32, fault_injector=fail
    )
    with pytest.raises(PersistenceIoError) as caught:
        _publish(failing, _snapshot(net_pnl="84"))
    assert caught.value.pointer_committed is False
    assert store.current.read_bytes() == pointer_before


def test_interrupted_pointer_replace_preserves_previous_pointer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish(store)
    pointer_before = store.current.read_bytes()

    def fail(phase: str) -> None:
        if phase == "before_pointer_replace":
            raise OSError("injected interruption")

    failing = AtomicSnapshotStore(
        store.root, token_factory=lambda: "2" * 32, fault_injector=fail
    )
    with pytest.raises(PersistenceIoError) as caught:
        _publish(failing, _snapshot(net_pnl="84"))
    assert caught.value.pointer_committed is False
    assert store.current.read_bytes() == pointer_before


def test_post_replace_failure_reports_committed_state(tmp_path: Path) -> None:
    def fail(phase: str) -> None:
        if phase == "after_pointer_replace":
            raise OSError("injected interruption")

    store = _store(tmp_path, fault_injector=fail)
    with pytest.raises(PersistenceIoError) as caught:
        _publish(store)
    assert caught.value.pointer_committed is True
    assert store.current.exists()


def test_evidence_failure_reports_committed_unconfirmed(tmp_path: Path) -> None:
    def fail(phase: str) -> None:
        if phase == "before_evidence_write":
            raise OSError("audit unavailable")

    result = _publish(_store(tmp_path, fault_injector=fail))
    assert result.state is PublicationState.COMMITTED_UNCONFIRMED
    assert result.pointer_committed is True
    assert result.evidence_file is None


def test_concurrent_reader_observes_previous_valid_pointer(tmp_path: Path) -> None:
    base = _store(tmp_path)
    first = _snapshot()
    _publish(base, first)
    entered = threading.Event()
    release = threading.Event()

    def block(phase: str) -> None:
        if phase == "before_pointer_replace":
            entered.set()
            assert release.wait(timeout=5)

    writer = AtomicSnapshotStore(
        base.root, token_factory=lambda: "2" * 32, fault_injector=block
    )
    result: list[object] = []
    thread = threading.Thread(
        target=lambda: result.append(_publish(writer, _snapshot(net_pnl="84")))
    )
    thread.start()
    assert entered.wait(timeout=5)
    during = base.read_current(evaluated_at_utc=NOW)
    assert during.snapshot == first
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result[0].state is PublicationState.PUBLISHED


def test_competing_store_instance_receives_busy(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def block(phase: str) -> None:
        if phase == "before_version_publish":
            entered.set()
            assert release.wait(timeout=5)

    store = _store(tmp_path, fault_injector=block)
    competing_store = _store(tmp_path)
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(_publish(store)))
    thread.start()
    assert entered.wait(timeout=5)
    competing = _publish(competing_store, _snapshot(net_pnl="84"))
    assert competing.state is PublicationState.BUSY
    release.set()
    thread.join(timeout=5)
    assert result[0].state is PublicationState.PUBLISHED


def test_publication_requests_file_sync_and_same_directory_pointer_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    fsync_calls: list[int] = []
    replace_calls: list[tuple[Path, Path]] = []
    real_fsync = module.os.fsync
    real_replace = module.os.replace

    def recording_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replace_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "fsync", recording_fsync)
    monkeypatch.setattr(module.os, "replace", recording_replace)

    result = _publish(store)

    assert result.state is PublicationState.PUBLISHED
    assert len(fsync_calls) >= 4
    assert len(replace_calls) == 1
    source, destination = replace_calls[0]
    assert source.parent == store.candidates
    assert destination == store.root / "current.json"
    assert source.parent.stat().st_dev == destination.parent.stat().st_dev


def test_oversized_pointer_is_unknown_not_trusted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish(store)
    (store.root / "current.json").write_bytes(b"{" + b" " * 5000 + b"}")

    retrieval = store.read_current(evaluated_at_utc=NOW)

    assert retrieval.state is AdapterReadState.UNKNOWN
    assert retrieval.snapshot is None


def test_unsupported_snapshot_version_is_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _publish(store)
    path = store.versions / result.version_file
    document = json.loads(path.read_text(encoding="ascii"))
    document["schema_version"] = "UNSUPPORTED"
    path.write_bytes(module._canonical_json(document))
    assert store.read_current(evaluated_at_utc=NOW).state is AdapterReadState.UNKNOWN


def test_invalid_provenance_is_rejected_before_write(tmp_path: Path) -> None:
    snapshot = _snapshot()
    object.__setattr__(snapshot, "source_authority", "UNAUTHORISED")
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        _publish(store, snapshot)
    assert not store.current.exists()


def test_invalid_observation_identity_is_rejected_before_write(tmp_path: Path) -> None:
    snapshot = _snapshot()
    object.__setattr__(snapshot, "observation_id", "invalid")
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        _publish(store, snapshot)
    assert not store.current.exists()


def test_pointer_path_traversal_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pointer = {
        "observation_id": _snapshot().observation_id,
        "pointer_version": module.POINTER_VERSION,
        "published_at_utc": module._utc_text(PUBLISHED),
        "schema_version": module.SCHEMA_VERSION,
        "storage_digest": "sha256:" + "0" * 64,
        "version_file": "../escape.json",
    }
    store.current.write_bytes(module._canonical_json(pointer))
    assert store.read_current(evaluated_at_utc=NOW).state is AdapterReadState.UNKNOWN


def test_reparse_point_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / "versions", target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root / "versions"), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert created.returncode == 0, created.stderr
    with pytest.raises(PersistenceConfigurationError, match="reparse"):
        AtomicSnapshotStore(root)


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError(28, "disk full")])
def test_permission_or_capacity_failure_preserves_no_pointer(
    tmp_path: Path, error: OSError
) -> None:
    def fail(phase: str) -> None:
        if phase == "before_snapshot_write":
            raise error

    store = _store(tmp_path, fault_injector=fail)
    with pytest.raises(PersistenceIoError) as caught:
        _publish(store)
    assert caught.value.pointer_committed is False
    assert not store.current.exists()


def test_restart_recovery_ignores_incomplete_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot()
    _publish(store, snapshot)
    (store.candidates / "incomplete.candidate").write_bytes(b"partial")
    restarted = AtomicSnapshotStore(store.root)
    recovery = restarted.recover(evaluated_at_utc=NOW)
    assert recovery.current.snapshot == snapshot
    assert recovery.ignored_candidates == 1
    assert recovery.promoted is False


def test_recovery_never_guesses_latest_without_pointer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unreferenced = store.versions / ("observation-" + "0" * 64 + ".json")
    unreferenced.write_bytes(_snapshot().canonical_bytes())
    recovery = store.recover(evaluated_at_utc=NOW)
    assert recovery.current.state is AdapterReadState.UNAVAILABLE
    assert recovery.promoted is False


def test_freshness_and_authority_are_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish(store)
    read = store.read_current(evaluated_at_utc=NOW)
    assert read.snapshot is not None
    assert read.snapshot.freshness_state is FreshnessState.UNVERIFIED
    assert read.snapshot.simulation_only is True
    assert read.snapshot.trading_authority == NO_TRADING_AUTHORITY
    assert read.state is AdapterReadState.UNKNOWN


def test_long_root_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / ("x" * 181)
    with pytest.raises(PersistenceConfigurationError, match="path exceeds"):
        AtomicSnapshotStore(root)


def test_no_network_broker_or_trading_capability() -> None:
    source = inspect.getsource(module).lower()
    assert "import socket" not in source
    assert "requests" not in source
    assert "submit_order" not in source
    assert "automated_trading_bot.broker" not in source
    assert not hasattr(AtomicSnapshotStore, "broker")
    assert not hasattr(AtomicSnapshotStore, "start")
    assert not hasattr(AtomicSnapshotStore, "listen")

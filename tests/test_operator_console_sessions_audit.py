from datetime import UTC, datetime, timedelta
import subprocess
import sys
from threading import Event, Thread

import pytest

from automated_trading_bot.operator_console import security_audit as audit_module
from automated_trading_bot.operator_console.security_audit import (
    AuditCommitUnconfirmed,
    AuditContentionError,
    AuditIntegrityError,
    AuditStorageError,
    SecurityAuditLog,
)
from automated_trading_bot.operator_console.sessions import SessionPolicy, SessionStore


NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


class Key:
    def load_key(self) -> bytes:
        return b"qualified-test-key-material-only" * 2


class MissingKey:
    def load_key(self) -> bytes:
        raise RuntimeError("DPAPI unavailable")


def _audit(tmp_path):
    root = (tmp_path / "audit").resolve()
    (root / "events").mkdir(parents=True)
    return SecurityAuditLog(root, Key())


def test_safe_ordinary_audit_directories_are_accepted(tmp_path) -> None:
    audit = _audit(tmp_path)
    assert audit.verify()[0] == 0


def test_symlink_backed_audit_root_is_rejected_before_write(tmp_path) -> None:
    actual = (tmp_path / "actual").resolve()
    (actual / "events").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(AuditStorageError, match="reparse"):
        SecurityAuditLog(linked, Key())
    assert not (actual / "writer.lock").exists()
    assert not (actual / "head.json").exists()


def test_symlink_backed_events_directory_is_rejected_before_write(tmp_path) -> None:
    root = (tmp_path / "audit").resolve()
    outside = (tmp_path / "outside").resolve()
    root.mkdir()
    outside.mkdir()
    (root / "events").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AuditStorageError, match="reparse"):
        SecurityAuditLog(root, Key())
    assert not (root / "writer.lock").exists()
    assert not (root / "head.json").exists()


@pytest.mark.parametrize("unsafe_part", ["root", "events", "parent"])
def test_detectable_reparse_component_is_rejected_portably(
    tmp_path, monkeypatch, unsafe_part
) -> None:
    parent = (tmp_path / "parent").resolve()
    root = parent / "audit"
    events = root / "events"
    events.mkdir(parents=True)
    target = {"root": root, "events": events, "parent": parent}[unsafe_part]
    original = audit_module._path_is_reparse

    def simulated_reparse(path):
        return path == target or original(path)

    monkeypatch.setattr(audit_module, "_path_is_reparse", simulated_reparse)
    with pytest.raises(AuditStorageError, match="reparse"):
        SecurityAuditLog(root, Key())
    assert not (root / "writer.lock").exists()
    assert not (root / "head.json").exists()


def test_events_path_replacement_during_append_fails_before_event_write(tmp_path) -> None:
    root = (tmp_path / "audit").resolve()
    events = root / "events"
    events.mkdir(parents=True)
    displaced = root / "events-displaced"

    def replace_events(phase: str) -> None:
        if phase == "before_event_write":
            events.rename(displaced)
            events.mkdir()

    audit = SecurityAuditLog(root, Key(), fault_injector=replace_events)
    with pytest.raises(AuditStorageError, match="identity"):
        audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    assert list(events.iterdir()) == []
    assert not (root / "head.json").exists()


def test_root_path_replacement_after_validation_fails_closed(tmp_path) -> None:
    root = (tmp_path / "audit").resolve()
    (root / "events").mkdir(parents=True)
    audit = SecurityAuditLog(root, Key())
    displaced = (tmp_path / "audit-displaced").resolve()
    root.rename(displaced)
    (root / "events").mkdir(parents=True)
    with pytest.raises(AuditStorageError, match="identity"):
        audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    assert list((root / "events").iterdir()) == []
    assert not (root / "writer.lock").exists()
    assert not (root / "head.json").exists()


def test_session_rotation_expiry_logout_revoke_all_and_restart(tmp_path) -> None:
    store = SessionStore(
        (tmp_path / "sessions.sqlite").resolve(),
        SessionPolicy(timedelta(minutes=5), timedelta(hours=1)),
    )
    first, first_session = store.create("atis-owner", now=NOW)
    assert store.validate(first, now=NOW + timedelta(minutes=1)) is not None
    second, second_session = store.create("atis-owner", now=NOW, rotate_token=first)
    assert first_session.session_ref != second_session.session_ref
    assert store.validate(first, now=NOW) is None
    assert store.validate(second, now=NOW + timedelta(minutes=6)) is None
    third, third_session = store.create("atis-owner", now=NOW)
    assert store.verify_csrf(third, third_session.csrf_token)
    store.revoke(third, now=NOW)
    assert store.validate(third, now=NOW) is None
    fourth, _ = store.create("atis-owner", now=NOW)
    restarted = SessionStore(store.database, store.policy)
    assert restarted.validate(fourth, now=NOW) is not None
    assert restarted.revoke_all(now=NOW) == 1
    assert restarted.validate(fourth, now=NOW) is None


def test_audit_chain_detects_tampering_and_persists(tmp_path) -> None:
    audit = _audit(tmp_path)
    first = audit.append("AUTH_CHALLENGE", "SUCCESS", "ASSERTION_CREATED", now=NOW)
    second = audit.append("AUTH_RESULT", "DENIED", "ASSERTION_REJECTED", now=NOW)
    assert second.previous_digest == first.digest
    assert audit.verify() == (2, second.digest)
    restarted = SecurityAuditLog(audit.root, Key())
    assert restarted.verify() == (2, second.digest)
    path = audit.events / "000000000001.json"
    path.write_bytes(path.read_bytes().replace(b"SUCCESS", b"DENIED "))
    with pytest.raises(AuditIntegrityError):
        restarted.verify()


def test_audit_storage_and_protected_key_failure_are_fail_closed(tmp_path) -> None:
    root = (tmp_path / "audit").resolve()
    root.mkdir()
    with pytest.raises(AuditStorageError):
        SecurityAuditLog(root, Key())
    (root / "events").mkdir()
    with pytest.raises(RuntimeError, match="DPAPI unavailable"):
        SecurityAuditLog(root, MissingKey())
    audit = SecurityAuditLog(root, Key())
    audit.events.rmdir()
    with pytest.raises(AuditStorageError):
        audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)


def test_two_simultaneous_writers_have_explicit_contention_and_no_lost_event(tmp_path) -> None:
    entered = Event()
    release = Event()

    def hold(phase: str) -> None:
        if phase == "before_event_write":
            entered.set()
            assert release.wait(2)

    first = _audit(tmp_path)
    first._fault_injector = hold
    second = SecurityAuditLog(first.root, Key())
    outcome = []

    def append_first() -> None:
        outcome.append(first.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW).sequence)

    worker = Thread(target=append_first)
    worker.start()
    assert entered.wait(2)
    with pytest.raises(AuditContentionError):
        second.append("AUTH_RESULT", "DENIED", "ASSERTION_REJECTED", now=NOW)
    release.set()
    worker.join(2)
    assert outcome == [1]
    assert second.verify()[0] == 1
    assert second.append("AUTH_RESULT", "DENIED", "ASSERTION_REJECTED", now=NOW).sequence == 2


def test_independent_process_writer_contention_is_explicit(tmp_path) -> None:
    audit = _audit(tmp_path)
    marker = (tmp_path / "writer-entered").resolve()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
from pathlib import Path
import time
from automated_trading_bot.operator_console.security_audit import SecurityAuditLog

class Key:
    def load_key(self):
        return b'qualified-test-key-material-only' * 2

root = Path(__import__('sys').argv[1])
marker = Path(__import__('sys').argv[2])
def hold(phase):
    if phase == 'before_event_write':
        marker.write_text('entered', encoding='ascii')
        time.sleep(1.0)
SecurityAuditLog(root, Key(), fault_injector=hold).append(
    'AUTH_RESULT', 'SUCCESS', 'OWNER_AUTHENTICATED'
)
""",
            str(audit.root),
            str(marker),
        ]
    )
    for _ in range(100):
        if marker.exists():
            break
        __import__("time").sleep(0.02)
    assert marker.exists()
    with pytest.raises(AuditContentionError):
        audit.append("AUTH_RESULT", "DENIED", "ASSERTION_REJECTED", now=NOW)
    assert child.wait(timeout=3) == 0
    assert audit.verify()[0] == 1


def test_interrupted_event_publish_recovers_only_uncommitted_tail(tmp_path) -> None:
    def interrupt(phase: str) -> None:
        if phase == "after_event_publish":
            raise OSError("simulated interruption")

    audit = _audit(tmp_path)
    audit._fault_injector = interrupt
    with pytest.raises(AuditStorageError):
        audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    assert not audit.head.exists()
    assert (audit.events / "000000000001.json").exists()
    with pytest.raises(AuditIntegrityError):
        audit.verify()
    recovered = SecurityAuditLog(audit.root, Key())
    assert recovered.recover_interrupted_append() == 1
    assert recovered.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW).sequence == 1


def test_interrupted_head_confirmation_reports_committed_unconfirmed(tmp_path) -> None:
    def interrupt(phase: str) -> None:
        if phase == "after_head_replace":
            raise OSError("simulated interruption")

    audit = _audit(tmp_path)
    audit._fault_injector = interrupt
    with pytest.raises(AuditCommitUnconfirmed):
        audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    recovered = SecurityAuditLog(audit.root, Key())
    assert recovered.verify()[0] == 1
    assert recovered.recover_interrupted_append() == 0
    assert recovered.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW).sequence == 2


def test_stale_lock_corrupt_head_broken_digest_and_hmac_fail_closed(tmp_path) -> None:
    audit = _audit(tmp_path)
    audit.writer_lock.write_bytes(b"stale")
    audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    audit.head.write_bytes(b"not-json")
    with pytest.raises(AuditIntegrityError):
        audit.verify()

    other_root = (tmp_path / "other-audit").resolve()
    (other_root / "events").mkdir(parents=True)
    other = SecurityAuditLog(other_root, Key())
    other.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", now=NOW)
    event = other.events / "000000000001.json"
    item = __import__("json").loads(event.read_text("ascii"))
    item["mac"] = "0" * 64
    event.write_text(__import__("json").dumps(item, sort_keys=True, separators=(",", ":")), "ascii")
    with pytest.raises(AuditIntegrityError):
        other.verify()

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from engineering_execution_guard.guard import (
    Descriptor,
    GuardError,
    Journal,
    ProcessQuery,
    ProcessStatus,
    _windows_process_query,
    RepositoryLock,
    canonical_bytes,
    changed_paths,
    main,
    process_alive,
    repo_key,
    run_argv,
    validate_descriptor,
)


def run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


class GuardFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_test_mode = os.environ.get("ATIS_GUARD_TEST_MODE")
        os.environ["ATIS_GUARD_TEST_MODE"] = "1"
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run(["git", "init", "-q"], self.repo)
        run(["git", "config", "user.email", "guard@example.invalid"], self.repo)
        run(["git", "config", "user.name", "Guard Test"], self.repo)
        (self.repo / "requirements-dev.lock").write_text("locked==1\n", encoding="utf-8", newline="\n")
        (self.repo / "work").mkdir()
        (self.repo / "work" / "seed.txt").write_text("seed\n", encoding="utf-8", newline="\n")
        run(["git", "add", "."], self.repo)
        run(["git", "commit", "-qm", "fixture"], self.repo)
        self.commit = run(["git", "rev-parse", "HEAD"], self.repo)
        self.tree = run(["git", "rev-parse", "HEAD^{tree}"], self.repo)
        lock_digest = hashlib.sha256((self.repo / "requirements-dev.lock").read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.evidence = self.root / "evidence"
        self.descriptor_path = self.root / "descriptor.json"
        self.descriptor: dict[str, Any] = {
            "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
            "task_id": "ATIS-TEST-001",
            "defect_id": "DEFECT-ONE",
            "owner_authorization_ref": "OWNER-AUTH-001",
            "repository": {"identity": "KiloAlpha021/automated-trading-bot", "path": str(self.repo)},
            "protected_baseline": {"commit": self.commit, "tree": self.tree},
            "authorized_stage": "TEST",
            "allowed_paths": ["work"],
            "dependency_lock": {"path": "requirements-dev.lock", "sha256": lock_digest},
            "validation_profile": {"id": "focused-v1", "commands": [[sys.executable, "-c", "raise SystemExit(0)"]]},
            "maximum_failed_attempts": 2,
            "evidence_output_directory": str(self.evidence),
        }
        self.write_descriptor()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        if self.previous_test_mode is None:
            os.environ.pop("ATIS_GUARD_TEST_MODE", None)
        else:
            os.environ["ATIS_GUARD_TEST_MODE"] = self.previous_test_mode

    def write_descriptor(self, value: dict[str, Any] | None = None, path: Path | None = None) -> Path:
        target = path or self.descriptor_path
        target.write_bytes(canonical_bytes(value or self.descriptor))
        return target

    def invoke(self, *arguments: str) -> int:
        return main(["--state-root", str(self.state), *arguments])

    def task_journal(self, descriptor: Descriptor | None = None) -> Journal:
        item = descriptor or validate_descriptor(self.descriptor_path)
        directory = self.state / "repositories" / repo_key(item) / "tasks" / item.task_id
        return Journal(directory)

    def write_retry_authorization(
        self,
        failed_attempt_ids: list[str],
        *,
        authorization_id: str = "RETRY-AUTH-001",
        previous: str = "OWNER-AUTH-001",
        new: str = "OWNER-AUTH-RETRY-001",
        descriptor: Descriptor | None = None,
    ) -> Path:
        item = descriptor or validate_descriptor(self.descriptor_path)
        value = {
            "schema_version": "ATIS-ENGINEERING-RETRY-AUTHORIZATION-V1",
            "authorization_id": authorization_id,
            "original_descriptor_sha256": item.digest,
            "task_id": item.task_id,
            "defect_id": item.defect_id,
            "previous_authorization_ref": previous,
            "new_authorization_ref": new,
            "failed_attempt_ids": failed_attempt_ids,
        }
        path = self.root / f"{authorization_id}.json"
        path.write_bytes(canonical_bytes(value))
        return path

    def seed_interrupted_attempt(
        self,
        *,
        attempt_number: int = 1,
        checkpoint: str | None = None,
        validation_passed: bool = False,
        create_evidence: bool = True,
    ) -> tuple[Descriptor, Journal, Path]:
        descriptor = validate_descriptor(self.descriptor_path)
        repository_dir = self.state / "repositories" / repo_key(descriptor)
        task_dir = repository_dir / "tasks" / descriptor.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "descriptor.json").write_bytes(canonical_bytes(descriptor.raw))
        journal = Journal(task_dir)
        attempt_id = f"{descriptor.task_id}-A{attempt_number}"
        evidence = descriptor.evidence_dir / descriptor.task_id / attempt_id
        journal.append(
            {
                "event": "ATTEMPT_STARTED",
                "task_id": descriptor.task_id,
                "defect_id": descriptor.defect_id,
                "descriptor_sha256": descriptor.digest,
                "authorization_ref": descriptor.authorization_ref,
                "attempt_id": attempt_id,
                "process_id": 2_000_000_000,
                "thread_id": "interrupted-thread",
                "recorded_at_ns": 1,
                "attempt_number": attempt_number,
                "evidence_directory": str(evidence),
            }
        )
        if create_evidence:
            evidence.mkdir(parents=True)
            (evidence / "preserved.txt").write_text("preserve me")
        if checkpoint:
            journal.append(
                {
                    "event": "CHECKPOINT_PASSED",
                    "task_id": descriptor.task_id,
                    "defect_id": descriptor.defect_id,
                    "descriptor_sha256": descriptor.digest,
                    "authorization_ref": descriptor.authorization_ref,
                    "attempt_id": attempt_id,
                    "process_id": 2_000_000_000,
                    "thread_id": "interrupted-thread",
                    "recorded_at_ns": 2,
                    "checkpoint": checkpoint,
                }
            )
        if validation_passed:
            journal.append(
                {
                    "event": "VALIDATION_PASSED",
                    "task_id": descriptor.task_id,
                    "defect_id": descriptor.defect_id,
                    "descriptor_sha256": descriptor.digest,
                    "authorization_ref": descriptor.authorization_ref,
                    "attempt_id": attempt_id,
                    "process_id": 2_000_000_000,
                    "thread_id": "interrupted-thread",
                    "recorded_at_ns": 3,
                    "validation": "validation-1",
                    "exit_code": 0,
                }
            )
        lock_payload = {
            "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
            "repository_identity": descriptor.repository_identity,
            "task_id": descriptor.task_id,
            "defect_id": descriptor.defect_id,
            "descriptor_sha256": descriptor.digest,
            "process_id": 2_000_000_000,
            "lock_id": "stale-interrupted",
            "created_at_ns": 1,
            "attempt_id": attempt_id,
        }
        (repository_dir / "repository.lock.json").write_bytes(canonical_bytes(lock_payload))
        return descriptor, journal, evidence

    def test_01_descriptor_requires_stable_task_id(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["task_id"] = "bad task"
        with self.assertRaises(GuardError):
            validate_descriptor(self.write_descriptor(value))

    def test_02_missing_authorization_rejected(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["owner_authorization_ref"] = ""
        with self.assertRaises(GuardError):
            validate_descriptor(self.write_descriptor(value))

    def test_03_duplicate_task_after_success_rejected(self) -> None:
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_04_conflicting_descriptor_rejected(self) -> None:
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)
        value = copy.deepcopy(self.descriptor)
        value["authorized_stage"] = "OTHER"
        self.write_descriptor(value)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_05_atomic_repository_lock_excludes_second_holder(self) -> None:
        lock_path = self.root / "atomic.lock"
        first = RepositoryLock(lock_path, {"owner": 1})
        first.acquire()
        try:
            with self.assertRaises(GuardError):
                RepositoryLock(lock_path, {"owner": 2}).acquire()
        finally:
            first.release()

    def test_06_windows_concurrency_race_has_one_winner(self) -> None:
        lock_path = self.root / "race.lock"
        barrier = threading.Barrier(3)
        results: list[str] = []

        def contender(number: int) -> None:
            barrier.wait()
            lock = RepositoryLock(lock_path, {"owner": number})
            try:
                lock.acquire()
                results.append("won")
                time.sleep(0.08)
            except GuardError:
                results.append("rejected")
            finally:
                if lock.acquired:
                    lock.release()

        threads = [threading.Thread(target=contender, args=(item,)) for item in (1, 2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["rejected", "won"])

    def test_07_overlapping_task_different_id_is_rejected(self) -> None:
        descriptor = validate_descriptor(self.descriptor_path)
        repository_dir = self.state / "repositories" / repo_key(descriptor)
        repository_dir.mkdir(parents=True)
        lock = RepositoryLock(repository_dir / "repository.lock.json", {"owner": "first"})
        lock.acquire()
        try:
            value = copy.deepcopy(self.descriptor)
            value["task_id"] = "ATIS-TEST-002"
            value["defect_id"] = "DEFECT-TWO"
            path = self.write_descriptor(value, self.root / "descriptor-2.json")
            self.assertEqual(self.invoke("run", "--descriptor", str(path)), 2)
        finally:
            lock.release()

    def test_08_baseline_commit_drift_rejected(self) -> None:
        (self.repo / "other.txt").write_text("other\n")
        run(["git", "add", "other.txt"], self.repo)
        run(["git", "commit", "-qm", "drift"], self.repo)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_09_baseline_tree_drift_rejected(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["protected_baseline"]["tree"] = "1" * 40
        self.write_descriptor(value)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_10_unauthorized_path_rejected_before_execution(self) -> None:
        (self.repo / "outside.txt").write_text("forbidden\n")
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_11_authorized_scope_is_allowed(self) -> None:
        (self.repo / "work" / "allowed.txt").write_text("allowed\n")
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)

    def test_12_dependency_lock_substitution_rejected(self) -> None:
        (self.repo / "requirements-dev.lock").write_text("substituted==2\n")
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_13_two_failures_open_circuit(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(9)"]
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path), *failing), 2)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path), *failing), 2)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)
        self.assertEqual(len([r for r in self.task_journal().verify()[2] if r.get("event") == "ATTEMPT_STARTED"]), 2)

    def test_14_cross_thread_retry_bypass_rejected(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(7)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        value = copy.deepcopy(self.descriptor)
        value["task_id"] = "ATIS-TEST-NEWTHREAD"
        value["owner_authorization_ref"] = "SAME-OLD-AUTH"
        path = self.write_descriptor(value, self.root / "new-thread.json")
        self.assertEqual(self.invoke("run", "--descriptor", str(path)), 2)

    def test_15_failed_validation_is_never_success(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["validation_profile"]["commands"] = [[sys.executable, "-c", "raise SystemExit(3)"]]
        self.write_descriptor(value)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)
        records = self.task_journal(validate_descriptor(self.descriptor_path)).verify()[2]
        self.assertEqual(records[-1]["disposition"], "FAILED")

    def test_16_journal_hash_tampering_detected(self) -> None:
        self.invoke("run", "--descriptor", str(self.descriptor_path))
        journal = self.task_journal()
        content = journal.path.read_text(encoding="utf-8").replace("SUCCEEDED", "CORRUPTED")
        journal.path.write_text(content, encoding="utf-8", newline="\n")
        with self.assertRaises(GuardError):
            journal.verify()

    def test_17_journal_truncation_detected(self) -> None:
        self.invoke("run", "--descriptor", str(self.descriptor_path))
        journal = self.task_journal()
        lines = journal.path.read_bytes().splitlines(keepends=True)
        journal.path.write_bytes(b"".join(lines[:-1]))
        with self.assertRaises(GuardError):
            journal.verify()

    def test_18_journal_sequence_conflict_detected(self) -> None:
        self.invoke("run", "--descriptor", str(self.descriptor_path))
        journal = self.task_journal()
        first = json.loads(journal.path.read_bytes().splitlines()[0])
        first["sequence"] = 8
        journal.path.write_bytes(canonical_bytes(first))
        with self.assertRaises(GuardError):
            journal.verify()

    def test_19_active_lock_cannot_be_recovered(self) -> None:
        descriptor = validate_descriptor(self.descriptor_path)
        repository_dir = self.state / "repositories" / repo_key(descriptor)
        repository_dir.mkdir(parents=True)
        lock_payload = {
            "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
            "repository_identity": descriptor.repository_identity,
            "task_id": descriptor.task_id,
            "defect_id": descriptor.defect_id,
            "descriptor_sha256": descriptor.digest,
            "process_id": os.getpid(), "lock_id": "active", "created_at_ns": 1,
            "attempt_id": None,
        }
        (repository_dir / "repository.lock.json").write_bytes(canonical_bytes(lock_payload))
        self.assertTrue(process_alive(os.getpid()))
        self.assertEqual(self.invoke("recover-lock", "--descriptor", str(self.descriptor_path), "--recovery-authorization-ref", "RECOVERY-1"), 2)

    def test_20_stale_lock_requires_explicit_recovery_authorization(self) -> None:
        parser_result = main(["--state-root", str(self.state), "recover-lock", "--descriptor", str(self.descriptor_path), "--recovery-authorization-ref", ""])
        self.assertEqual(parser_result, 2)

    def test_21_stale_lock_recovery_is_journalled(self) -> None:
        descriptor = validate_descriptor(self.descriptor_path)
        repository_dir = self.state / "repositories" / repo_key(descriptor)
        tasks = repository_dir / "tasks" / descriptor.task_id
        tasks.mkdir(parents=True)
        (tasks / "descriptor.json").write_bytes(canonical_bytes(descriptor.raw))
        lock_payload = {
            "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
            "repository_identity": descriptor.repository_identity,
            "task_id": descriptor.task_id,
            "defect_id": descriptor.defect_id,
            "descriptor_sha256": descriptor.digest,
            "process_id": 2_000_000_000, "lock_id": "stale", "created_at_ns": 1,
            "attempt_id": None,
        }
        (repository_dir / "repository.lock.json").write_bytes(canonical_bytes(lock_payload))
        self.assertEqual(self.invoke("recover-lock", "--descriptor", str(self.descriptor_path), "--recovery-authorization-ref", "OWNER-RECOVERY-1"), 0)
        self.assertEqual(Journal(tasks).verify()[2][-1]["event"], "STALE_LOCK_RECOVERED")

    def test_22_command_argv_is_not_shell_interpreted(self) -> None:
        marker = self.root / "shell-marker"
        command = [sys.executable, "-c", "import sys; raise SystemExit(0)", ";", "echo", str(marker)]
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path), *command), 0)
        self.assertFalse(marker.exists())

    def test_23_direct_write_outside_guard_is_detected_but_not_prevented(self) -> None:
        forbidden = self.repo / "direct-bypass.txt"
        forbidden.write_text("ordinary shell access can bypass prevention\n")
        self.assertTrue(forbidden.exists())
        self.assertIn("direct-bypass.txt", changed_paths(self.repo))
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_24_summary_is_canonical_and_digest_bound(self) -> None:
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)
        summaries = list(self.evidence.rglob("execution-summary.json"))
        self.assertEqual(len(summaries), 1)
        parsed = json.loads(summaries[0].read_text(encoding="utf-8"))
        self.assertEqual(summaries[0].read_bytes(), canonical_bytes(parsed))
        expected = hashlib.sha256(summaries[0].read_bytes()).hexdigest()
        self.assertEqual(summaries[0].with_name("execution-summary.sha256").read_text().strip(), expected)

    def test_25_evidence_and_state_must_be_outside_repository(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["evidence_output_directory"] = str(self.repo / "evidence")
        with self.assertRaises(GuardError):
            validate_descriptor(self.write_descriptor(value))

    def test_26_unknown_descriptor_fields_rejected(self) -> None:
        value = copy.deepcopy(self.descriptor)
        value["owner_pass"] = True
        with self.assertRaises(GuardError):
            validate_descriptor(self.write_descriptor(value))

    def test_27_guard_has_no_owner_or_github_actions(self) -> None:
        source = (Path(__file__).parents[1] / "guard.py").read_text(encoding="utf-8")
        for forbidden in ("gh pr", 'git(repo, "push"', "OWNER_VERDICT", "GATE-S03-01", "execute_trade", "broker_order"):
            self.assertNotIn(forbidden, source)

    def test_28_repository_lock_key_is_shared_across_worktree_paths(self) -> None:
        first = validate_descriptor(self.descriptor_path)
        second_raw = copy.deepcopy(self.descriptor)
        second_raw["repository"]["path"] = str(self.root / "another-worktree")
        (self.root / "another-worktree" / ".git").mkdir(parents=True)
        second = validate_descriptor(self.write_descriptor(second_raw, self.root / "second-worktree.json"))
        self.assertEqual(repo_key(first), repo_key(second))

    def test_29_authorized_recovery_repairs_only_one_record_anchor_lag(self) -> None:
        descriptor = validate_descriptor(self.descriptor_path)
        repository_dir = self.state / "repositories" / repo_key(descriptor)
        tasks = repository_dir / "tasks" / descriptor.task_id
        tasks.mkdir(parents=True)
        (tasks / "descriptor.json").write_bytes(canonical_bytes(descriptor.raw))
        journal = Journal(tasks)
        journal.append({"event": "PREVIOUS", "task_id": descriptor.task_id})
        stale_anchor = journal.anchor_path.read_bytes()
        journal.append({"event": "DURABLE_BEFORE_CRASH", "task_id": descriptor.task_id})
        journal.anchor_path.write_bytes(stale_anchor)
        with self.assertRaises(GuardError):
            journal.verify()
        lock_payload = {
            "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
            "repository_identity": descriptor.repository_identity,
            "task_id": descriptor.task_id,
            "defect_id": descriptor.defect_id,
            "descriptor_sha256": descriptor.digest,
            "process_id": 2_000_000_000,
            "lock_id": "stale-after-journal-append",
            "created_at_ns": 1,
            "attempt_id": None,
        }
        (repository_dir / "repository.lock.json").write_bytes(canonical_bytes(lock_payload))
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-ANCHOR-1",
            ),
            0,
        )
        records = journal.verify()[2]
        self.assertEqual(records[-1]["event"], "STALE_LOCK_RECOVERED")

    def test_30_state_root_override_rejected_outside_test_mode(self) -> None:
        os.environ.pop("ATIS_GUARD_TEST_MODE", None)
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)

    def test_31_crash_after_evidence_directory_is_terminalized(self) -> None:
        _, journal, evidence = self.seed_interrupted_attempt()
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-DIR",
            ),
            0,
        )
        terminals = [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_FINISHED"]
        self.assertEqual(terminals[-1]["disposition"], "INTERRUPTED")
        self.assertEqual((evidence / "preserved.txt").read_text(), "preserve me")

    def test_32_crash_after_attempt_started_without_directory_is_terminalized(self) -> None:
        _, journal, evidence = self.seed_interrupted_attempt(create_evidence=False)
        self.assertFalse(evidence.exists())
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-START",
            ),
            0,
        )
        self.assertEqual(
            [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_FINISHED"][-1]["disposition"],
            "INTERRUPTED",
        )

    def test_33_crash_during_command_is_terminalized(self) -> None:
        _, journal, _ = self.seed_interrupted_attempt(checkpoint="before-implementation")
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-COMMAND",
            ),
            0,
        )
        self.assertEqual(
            [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_FINISHED"][-1]["disposition"],
            "INTERRUPTED",
        )

    def test_34_crash_after_validation_before_terminal_is_terminalized(self) -> None:
        _, journal, _ = self.seed_interrupted_attempt(
            checkpoint="before-validation-1",
            validation_passed=True,
        )
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-VALIDATION",
            ),
            0,
        )
        self.assertEqual(
            [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_FINISHED"][-1]["disposition"],
            "INTERRUPTED",
        )

    def test_35_attempt_number_is_monotonic_after_interruption(self) -> None:
        _, journal, _ = self.seed_interrupted_attempt()
        self.invoke(
            "recover-lock", "--descriptor", str(self.descriptor_path),
            "--recovery-authorization-ref", "OWNER-RECOVERY-MONOTONIC",
        )
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)
        starts = [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_STARTED"]
        self.assertEqual([(item["attempt_id"], item["attempt_number"]) for item in starts], [("ATIS-TEST-001-A1", 1), ("ATIS-TEST-001-A2", 2)])

    def test_36_two_interrupted_attempts_open_circuit(self) -> None:
        _, journal, _ = self.seed_interrupted_attempt(attempt_number=1)
        self.invoke(
            "recover-lock", "--descriptor", str(self.descriptor_path),
            "--recovery-authorization-ref", "OWNER-RECOVERY-I1",
        )
        self.seed_interrupted_attempt(attempt_number=2)
        self.invoke(
            "recover-lock", "--descriptor", str(self.descriptor_path),
            "--recovery-authorization-ref", "OWNER-RECOVERY-I2",
        )
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)
        failures = [record for record in journal.verify()[2] if record.get("disposition") in {"FAILED", "INTERRUPTED"}]
        self.assertEqual(len(failures), 2)

    def test_37_valid_retry_authorization_successor_opens_new_epoch(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(6)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        authorization = self.write_retry_authorization(["ATIS-TEST-001-A1", "ATIS-TEST-001-A2"])
        self.assertEqual(
            self.invoke(
                "authorize-retry", "--descriptor", str(self.descriptor_path),
                "--authorization", str(authorization),
            ),
            0,
        )
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 0)
        records = self.task_journal().verify()[2]
        self.assertEqual([r for r in records if r.get("event") == "ATTEMPT_STARTED"][-1]["attempt_id"], "ATIS-TEST-001-A3")
        self.assertEqual([r for r in records if r.get("event") == "ATTEMPT_STARTED"][-1]["authorization_ref"], "OWNER-AUTH-RETRY-001")

    def test_38_retry_authorization_replay_is_rejected(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(6)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        authorization = self.write_retry_authorization(["ATIS-TEST-001-A1", "ATIS-TEST-001-A2"])
        arguments = (
            "authorize-retry", "--descriptor", str(self.descriptor_path),
            "--authorization", str(authorization),
        )
        self.assertEqual(self.invoke(*arguments), 0)
        self.assertEqual(self.invoke(*arguments), 2)

    def test_39_retry_authorization_substitution_is_rejected(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(6)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        authorization = self.write_retry_authorization(["ATIS-TEST-001-A1", "ATIS-TEST-001-A9"])
        self.assertEqual(
            self.invoke(
                "authorize-retry", "--descriptor", str(self.descriptor_path),
                "--authorization", str(authorization),
            ),
            2,
        )

    def test_40_retry_authorization_missing_or_malformed_is_rejected(self) -> None:
        missing = self.root / "missing.json"
        self.assertEqual(
            self.invoke(
                "authorize-retry", "--descriptor", str(self.descriptor_path),
                "--authorization", str(missing),
            ),
            2,
        )
        malformed = self.root / "malformed.json"
        malformed.write_text("{}")
        self.assertEqual(
            self.invoke(
                "authorize-retry", "--descriptor", str(self.descriptor_path),
                "--authorization", str(malformed),
            ),
            2,
        )

    def test_41_retry_authorization_cross_defect_is_rejected(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(6)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        authorization = self.write_retry_authorization(["ATIS-TEST-001-A1", "ATIS-TEST-001-A2"])
        value = json.loads(authorization.read_text())
        value["defect_id"] = "OTHER-DEFECT"
        authorization.write_bytes(canonical_bytes(value))
        self.assertEqual(
            self.invoke(
                "authorize-retry", "--descriptor", str(self.descriptor_path),
                "--authorization", str(authorization),
            ),
            2,
        )

    def test_42_access_denied_process_query_is_active_or_unknown(self) -> None:
        class Kernel:
            def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
                return 0

        with (
            patch("engineering_execution_guard.guard.ctypes.WinDLL", return_value=Kernel()),
            patch("engineering_execution_guard.guard.ctypes.set_last_error"),
            patch("engineering_execution_guard.guard.ctypes.get_last_error", return_value=5),
        ):
            result = _windows_process_query(123)
        self.assertEqual(result, ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, 5, None))

    def test_43_indeterminate_wait_result_is_active_or_unknown(self) -> None:
        class Kernel:
            def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
                return 123

            def WaitForSingleObject(self, handle: int, timeout: int) -> int:
                return 0xFFFFFFFF

            def CloseHandle(self, handle: int) -> None:
                return None

        with (
            patch("engineering_execution_guard.guard.ctypes.WinDLL", return_value=Kernel()),
            patch("engineering_execution_guard.guard.ctypes.set_last_error"),
            patch("engineering_execution_guard.guard.ctypes.get_last_error", return_value=6),
        ):
            result = _windows_process_query(123)
        self.assertEqual(result, ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, 6, 0xFFFFFFFF))

    def test_44_process_not_found_is_confirmed_dead(self) -> None:
        class Kernel:
            def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
                return 0

        with (
            patch("engineering_execution_guard.guard.ctypes.WinDLL", return_value=Kernel()),
            patch("engineering_execution_guard.guard.ctypes.set_last_error"),
            patch("engineering_execution_guard.guard.ctypes.get_last_error", return_value=87),
        ):
            result = _windows_process_query(123)
        self.assertEqual(result.status, ProcessStatus.NOT_FOUND)
        self.assertTrue(result.death_confirmed)

    def test_45_unknown_process_state_cannot_remove_lock(self) -> None:
        self.seed_interrupted_attempt()
        lock_path = self.state / "repositories" / repo_key(validate_descriptor(self.descriptor_path)) / "repository.lock.json"
        with patch(
            "engineering_execution_guard.guard.query_process",
            return_value=ProcessQuery(ProcessStatus.ACTIVE_OR_UNKNOWN, 5, None),
        ):
            result = self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-UNKNOWN",
            )
        self.assertEqual(result, 2)
        self.assertTrue(lock_path.exists())

    def test_46_interrupted_recovery_is_idempotent(self) -> None:
        _, journal, _ = self.seed_interrupted_attempt()
        arguments = (
            "recover-lock", "--descriptor", str(self.descriptor_path),
            "--recovery-authorization-ref", "OWNER-RECOVERY-ONCE",
        )
        self.assertEqual(self.invoke(*arguments), 0)
        self.assertEqual(self.invoke(*arguments), 2)
        terminals = [record for record in journal.verify()[2] if record.get("event") == "ATTEMPT_FINISHED"]
        self.assertEqual(len(terminals), 1)

    def test_47_conflicting_open_attempts_block_recovery(self) -> None:
        descriptor, journal, _ = self.seed_interrupted_attempt(attempt_number=1)
        journal.append(
            {
                "event": "ATTEMPT_STARTED",
                "task_id": descriptor.task_id,
                "defect_id": descriptor.defect_id,
                "descriptor_sha256": descriptor.digest,
                "authorization_ref": descriptor.authorization_ref,
                "attempt_id": "ATIS-TEST-001-A2",
                "process_id": 2_000_000_000,
                "thread_id": "conflict",
                "recorded_at_ns": 2,
                "attempt_number": 2,
                "evidence_directory": str(self.evidence / descriptor.task_id / "ATIS-TEST-001-A2"),
            }
        )
        self.assertEqual(
            self.invoke(
                "recover-lock", "--descriptor", str(self.descriptor_path),
                "--recovery-authorization-ref", "OWNER-RECOVERY-CONFLICT",
            ),
            2,
        )

    @unittest.skipUnless(os.name == "nt", "Windows detached-process behavior")
    def test_48_detached_descendant_can_outlive_guarded_parent(self) -> None:
        marker = self.root / "detached-marker"
        child = (
            "import pathlib,time; time.sleep(0.4); "
            f"pathlib.Path({str(marker)!r}).write_text('detached')"
        )
        parent = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}], creationflags=0x208)"
        )
        self.assertEqual(
            run_argv([sys.executable, "-c", parent], self.repo, self.root / "detached-log.json"),
            0,
        )
        deadline = time.time() + 3
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists())

    def test_49_existing_evidence_directory_is_preserved_and_failed(self) -> None:
        evidence = self.evidence / "ATIS-TEST-001" / "ATIS-TEST-001-A1"
        evidence.mkdir(parents=True)
        marker = evidence / "historical.txt"
        marker.write_text("do not overwrite")
        self.assertEqual(self.invoke("run", "--descriptor", str(self.descriptor_path)), 2)
        self.assertEqual(marker.read_text(), "do not overwrite")
        terminal = [r for r in self.task_journal().verify()[2] if r.get("event") == "ATTEMPT_FINISHED"][-1]
        self.assertEqual(terminal["disposition"], "FAILED")

    def test_50_retry_authorization_reference_cannot_be_reused(self) -> None:
        failing = [sys.executable, "-c", "raise SystemExit(6)"]
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        first = self.write_retry_authorization(["ATIS-TEST-001-A1", "ATIS-TEST-001-A2"])
        self.assertEqual(
            self.invoke("authorize-retry", "--descriptor", str(self.descriptor_path), "--authorization", str(first)),
            0,
        )
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        self.invoke("run", "--descriptor", str(self.descriptor_path), *failing)
        replay = self.write_retry_authorization(
            ["ATIS-TEST-001-A3", "ATIS-TEST-001-A4"],
            authorization_id="RETRY-AUTH-002",
            previous="OWNER-AUTH-RETRY-001",
            new="OWNER-AUTH-001",
        )
        self.assertEqual(
            self.invoke("authorize-retry", "--descriptor", str(self.descriptor_path), "--authorization", str(replay)),
            2,
        )

    def test_51_recovery_authorization_reference_cannot_be_reused(self) -> None:
        descriptor, journal, _ = self.seed_interrupted_attempt()
        lock_path = self.state / "repositories" / repo_key(descriptor) / "repository.lock.json"
        arguments = (
            "recover-lock", "--descriptor", str(self.descriptor_path),
            "--recovery-authorization-ref", "OWNER-RECOVERY-ONE",
        )
        self.assertEqual(self.invoke(*arguments), 0)
        attempt_id = f"{descriptor.task_id}-A2"
        journal.append(
            {
                "event": "ATTEMPT_STARTED",
                "task_id": descriptor.task_id,
                "defect_id": descriptor.defect_id,
                "descriptor_sha256": descriptor.digest,
                "authorization_ref": descriptor.authorization_ref,
                "attempt_id": attempt_id,
                "attempt_number": 2,
                "process_id": 2_000_000_000,
                "thread_id": "CRASH-THREAD",
                "recorded_at_ns": time.time_ns(),
                "evidence_directory": str(descriptor.evidence_dir / descriptor.task_id / attempt_id),
            }
        )
        lock_path.write_bytes(
            canonical_bytes(
                {
                    "schema_version": "ATIS-ENGINEERING-EXECUTION-GUARD-V1",
                    "repository_identity": descriptor.repository_identity,
                    "task_id": descriptor.task_id,
                    "defect_id": descriptor.defect_id,
                    "descriptor_sha256": descriptor.digest,
                    "process_id": 2_000_000_000,
                    "lock_id": "reused-recovery-reference",
                    "created_at_ns": time.time_ns(),
                    "attempt_id": attempt_id,
                }
            )
        )
        self.assertEqual(self.invoke(*arguments), 2)
        self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()

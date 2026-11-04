"""Synthetic recovery authority kept outside the runtime/journal boundary."""

from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

from automated_trading_bot.stage5.commit_boundary import (
    RecoveryAuthorityVerifier,
    RecoveryJournalFactory,
    RecoveryReceipt,
    RecoveryReceiptType,
    RecoveryTerminalOutcome,
    RecoveryTrustPolicy,
    recovery_authority_store_path,
)
from automated_trading_bot.stage5.commit_boundary.recovery_journal import RecoveryJournal


TEST_RSA_N = 93884530107454372883108803192878152024664499083738295990826905442471281878915457671941547853804761340451109251410829269365541819791951839238269282586763695130633782938167136286539700605503101990689294499442991428608982097989506920248131872557296740747013497133849596094225959566662493824975450120590112809027
TEST_RSA_D = 38986640933737747501622077282972655253539898722308584851158188986631306534243010521413693407407976866203471905597536026455028771924835883621000328449559385307233499080256703292124608392100011365357657420219711919706125792431164402320175756511589314755606572961287743558695702064782933233176632893926131059393
_DER = bytes.fromhex("3031300d060960864801650304020105000420")


def test_policy() -> RecoveryTrustPolicy:
    return RecoveryTrustPolicy(
        installation_id="SYNTHETIC-INSTALLATION-1",
        issuer_identity="SYNTHETIC-RECOVERY-AUTHORITY",
        deployment_identity="Q05-ISOLATED",
        recipient="SOE-01-RECOVERY-JOURNAL",
        policy_version=1,
        issuer_epoch=1,
        rsa_modulus=TEST_RSA_N,
    )


def sign_receipt(receipt: RecoveryReceipt) -> RecoveryReceipt:
    size = (TEST_RSA_N.bit_length() + 7) // 8
    digest = _DER + hashlib.sha256(receipt.payload()).digest()
    encoded = b"\x00\x01" + (b"\xff" * (size - len(digest) - 3)) + b"\x00" + digest
    signature = pow(int.from_bytes(encoded, "big"), TEST_RSA_D, TEST_RSA_N)
    return replace(receipt, signature=signature.to_bytes(size, "big").hex())


def authority_for(path: Path) -> RecoveryAuthorityVerifier:
    authority_path = recovery_authority_store_path(path)
    policy = test_policy()
    if not authority_path.exists():
        with sqlite3.connect(authority_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE authority_identity (singleton INTEGER PRIMARY KEY CHECK "
                "(singleton=1), installation_id TEXT NOT NULL, issuer_identity TEXT NOT NULL, "
                "deployment_identity TEXT NOT NULL, recipient TEXT NOT NULL, policy_version "
                "INTEGER NOT NULL, issuer_epoch INTEGER NOT NULL, rsa_modulus TEXT NOT NULL, "
                "rsa_exponent INTEGER NOT NULL, max_future_skew_ns INTEGER NOT NULL, "
                "policy_fingerprint TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE consumed_receipt (nonce TEXT PRIMARY KEY, receipt_hash TEXT "
                "NOT NULL, receipt_type TEXT NOT NULL, operation_id TEXT, "
                "consumed_ns INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO authority_identity VALUES (1,?,?,?,?,?,?,?,?,?,?)",
                (
                    policy.installation_id,
                    policy.issuer_identity,
                    policy.deployment_identity,
                    policy.recipient,
                    policy.policy_version,
                    policy.issuer_epoch,
                    str(policy.rsa_modulus),
                    policy.rsa_exponent,
                    policy.max_future_skew_ns,
                    policy.fingerprint(),
                ),
            )
    return RecoveryAuthorityVerifier(authority_path)


def open_journal(path: Path, *, authorize_new: bool = True) -> RecoveryJournal:
    new_store = not path.exists()
    authority_for(path)
    journal = RecoveryJournalFactory().open(path)
    if new_store and authorize_new:
        journal.authorize_bootstrap(bootstrap_receipt(journal))
    return journal


def bootstrap_receipt(journal: RecoveryJournal, **changes: object) -> RecoveryReceipt:
    now = time.time_ns()
    policy = test_policy()
    values: dict[str, object] = {
        "receipt_type": RecoveryReceiptType.BOOTSTRAP,
        "installation_id": policy.installation_id,
        "store_challenge": journal.bootstrap_challenge,
        "operation_id": None,
        "request_digest": None,
        "issuer_identity": policy.issuer_identity,
        "deployment_identity": policy.deployment_identity,
        "recipient": policy.recipient,
        "policy_version": policy.policy_version,
        "issuer_epoch": policy.issuer_epoch,
        "terminal_outcome": None,
        "valid_from_ns": now - 1_000_000,
        "valid_until_ns": now + 10_000_000_000,
        "nonce": f"BOOTSTRAP-{uuid.uuid4()}",
    }
    values.update(changes)
    return sign_receipt(RecoveryReceipt(**values))  # type: ignore[arg-type]


def terminal_receipt(
    journal: RecoveryJournal,
    operation_id: str,
    request_digest: str,
    outcome: RecoveryTerminalOutcome,
    attempt_id: str | None = None,
    g00_sequence: str = "10",
    **changes: object,
) -> RecoveryReceipt:
    now = time.time_ns()
    policy = test_policy()
    values: dict[str, object] = {
        "receipt_type": RecoveryReceiptType.TERMINAL,
        "installation_id": policy.installation_id,
        "store_challenge": journal.bootstrap_challenge,
        "operation_id": operation_id,
        "request_digest": request_digest,
        "attempt_id": attempt_id or f"ATTEMPT-{operation_id}",
        "g00_sequence": g00_sequence,
        "issuer_identity": policy.issuer_identity,
        "deployment_identity": policy.deployment_identity,
        "recipient": policy.recipient,
        "policy_version": policy.policy_version,
        "issuer_epoch": policy.issuer_epoch,
        "terminal_outcome": outcome,
        "valid_from_ns": now - 1_000_000,
        "valid_until_ns": now + 10_000_000_000,
        "nonce": f"TERMINAL-{uuid.uuid4()}",
    }
    values.update(changes)
    return sign_receipt(RecoveryReceipt(**values))  # type: ignore[arg-type]

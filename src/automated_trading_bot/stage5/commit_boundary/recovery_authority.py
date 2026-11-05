"""Independent cryptographic authority for SOE recovery transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import time_ns
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recovery_journal import RecoveryJournal


class RecoveryAuthorityError(RuntimeError):
    """A recovery receipt or authority-store invariant was rejected."""


class RecoveryReceiptType(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    TERMINAL = "TERMINAL"


class RecoveryTerminalOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    receipt_type: RecoveryReceiptType
    installation_id: str
    store_challenge: str
    operation_id: str | None
    request_digest: str | None
    issuer_identity: str
    deployment_identity: str
    recipient: str
    policy_version: int
    issuer_epoch: int
    terminal_outcome: RecoveryTerminalOutcome | None
    valid_from_ns: int
    valid_until_ns: int
    nonce: str
    signature: str = ""
    attempt_id: str | None = None
    g00_sequence: str | None = None

    def payload(self) -> bytes:
        value = {
            "deployment_identity": self.deployment_identity,
            "g00_sequence": self.g00_sequence,
            "installation_id": self.installation_id,
            "issuer_epoch": self.issuer_epoch,
            "issuer_identity": self.issuer_identity,
            "attempt_id": self.attempt_id,
            "nonce": self.nonce,
            "operation_id": self.operation_id,
            "policy_version": self.policy_version,
            "receipt_type": self.receipt_type.value,
            "recipient": self.recipient,
            "request_digest": self.request_digest,
            "store_challenge": self.store_challenge,
            "terminal_outcome": (
                None if self.terminal_outcome is None else self.terminal_outcome.value
            ),
            "valid_from_ns": self.valid_from_ns,
            "valid_until_ns": self.valid_until_ns,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def with_signature(self, signature: str) -> RecoveryReceipt:
        return replace(self, signature=signature)


@dataclass(frozen=True, slots=True)
class RecoveryTrustPolicy:
    installation_id: str
    issuer_identity: str
    deployment_identity: str
    recipient: str
    policy_version: int
    issuer_epoch: int
    rsa_modulus: int
    rsa_exponent: int = 65537
    max_future_skew_ns: int = 1_000_000_000

    def fingerprint(self) -> str:
        data = {
            "deployment_identity": self.deployment_identity,
            "installation_id": self.installation_id,
            "issuer_epoch": self.issuer_epoch,
            "issuer_identity": self.issuer_identity,
            "policy_version": self.policy_version,
            "recipient": self.recipient,
            "rsa_exponent": self.rsa_exponent,
            "rsa_modulus": str(self.rsa_modulus),
        }
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _verify_rsa_signature(payload: bytes, signature: str, policy: RecoveryTrustPolicy) -> bool:
    try:
        raw = bytes.fromhex(signature)
    except ValueError:
        return False
    size = (policy.rsa_modulus.bit_length() + 7) // 8
    if len(raw) != size or size < len(_SHA256_DER_PREFIX) + 32 + 11:
        return False
    recovered = pow(int.from_bytes(raw, "big"), policy.rsa_exponent, policy.rsa_modulus)
    encoded = recovered.to_bytes(size, "big")
    digest_info = _SHA256_DER_PREFIX + hashlib.sha256(payload).digest()
    padding_length = size - len(digest_info) - 3
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    return encoded == expected


def recovery_authority_store_path(journal_path: str | Path) -> Path:
    """Return the deployment-owned authority domain fixed for a journal directory."""
    return Path(journal_path).parent / ".soe01-recovery-authority.sqlite"


class RecoveryAuthorityVerifier:
    """Public-key verifier with a separate durable single-use receipt domain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        if not self.path.is_file():
            raise RecoveryAuthorityError("provisioned recovery authority store is required")
        with closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            row = connection.execute(
                "SELECT installation_id,issuer_identity,deployment_identity,recipient,"
                "policy_version,issuer_epoch,rsa_modulus,rsa_exponent,max_future_skew_ns,"
                "policy_fingerprint FROM authority_identity "
                "WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise RecoveryAuthorityError("recovery authority identity is missing")
        policy = RecoveryTrustPolicy(
            installation_id=str(row[0]),
            issuer_identity=str(row[1]),
            deployment_identity=str(row[2]),
            recipient=str(row[3]),
            policy_version=int(row[4]),
            issuer_epoch=int(row[5]),
            rsa_modulus=int(row[6]),
            rsa_exponent=int(row[7]),
            max_future_skew_ns=int(row[8]),
        )
        if row[9] != policy.fingerprint():
            raise RecoveryAuthorityError("recovery authority policy binding mismatch")
        self.policy = policy

    @property
    def installation_id(self) -> str:
        return self.policy.installation_id

    @property
    def policy_fingerprint(self) -> str:
        return self.policy.fingerprint()

    def consume_bootstrap(self, receipt: RecoveryReceipt, store_challenge: str) -> str:
        self._validate_common(receipt, RecoveryReceiptType.BOOTSTRAP, store_challenge)
        if any(
            (
                receipt.operation_id,
                receipt.request_digest,
                receipt.attempt_id,
                receipt.g00_sequence,
                receipt.terminal_outcome,
            )
        ):
            raise RecoveryAuthorityError("bootstrap receipt contains terminal context")
        self._consume(receipt)
        return receipt.nonce

    def consume_terminal(
        self,
        receipt: RecoveryReceipt,
        *,
        store_challenge: str,
        operation_id: str,
        request_digest: str,
        attempt_id: str,
        g00_sequence: str,
        expected_outcome: RecoveryTerminalOutcome,
    ) -> str:
        if type(expected_outcome) is not RecoveryTerminalOutcome:
            raise RecoveryAuthorityError("terminal recovery outcome is not terminal")
        self._validate_common(receipt, RecoveryReceiptType.TERMINAL, store_challenge)
        if type(receipt.terminal_outcome) is not RecoveryTerminalOutcome:
            raise RecoveryAuthorityError("terminal recovery outcome is not terminal")
        if (
            receipt.operation_id != operation_id
            or receipt.request_digest != request_digest
            or receipt.attempt_id != attempt_id
            or receipt.g00_sequence != g00_sequence
            or receipt.terminal_outcome is not expected_outcome
        ):
            raise RecoveryAuthorityError("terminal receipt context mismatch")
        self._consume(receipt)
        return receipt.nonce

    def _validate_common(
        self,
        receipt: RecoveryReceipt,
        expected_type: RecoveryReceiptType,
        store_challenge: str,
    ) -> None:
        if type(receipt) is not RecoveryReceipt:
            raise RecoveryAuthorityError("recovery requires an exact receipt capability")
        policy = self.policy
        if receipt.receipt_type is not expected_type:
            raise RecoveryAuthorityError("recovery receipt type mismatch")
        if (
            receipt.installation_id != policy.installation_id
            or receipt.store_challenge != store_challenge
            or receipt.issuer_identity != policy.issuer_identity
            or receipt.deployment_identity != policy.deployment_identity
            or receipt.recipient != policy.recipient
            or receipt.policy_version != policy.policy_version
            or receipt.issuer_epoch != policy.issuer_epoch
        ):
            raise RecoveryAuthorityError("recovery receipt trust context mismatch")
        now = time_ns()
        if (
            receipt.valid_from_ns > now + policy.max_future_skew_ns
            or receipt.valid_until_ns < now
            or receipt.valid_until_ns <= receipt.valid_from_ns
        ):
            raise RecoveryAuthorityError("recovery receipt validity rejected")
        if not receipt.nonce or not _verify_rsa_signature(receipt.payload(), receipt.signature, policy):
            raise RecoveryAuthorityError("recovery receipt signature rejected")

    def _consume(self, receipt: RecoveryReceipt) -> None:
        digest = hashlib.sha256(receipt.payload() + receipt.signature.encode("ascii")).hexdigest()
        with self._lock, closing(sqlite3.connect(self.path, timeout=2.0)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO consumed_receipt VALUES (?,?,?,?,?)",
                    (
                        receipt.nonce,
                        digest,
                        receipt.receipt_type.value,
                        receipt.operation_id,
                        time_ns(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise RecoveryAuthorityError("recovery receipt replay rejected") from error


class RecoveryJournalFactory:
    """Trusted composition boundary; callers cannot supply verification callbacks."""

    def open(self, path: str | Path) -> RecoveryJournal:
        from .recovery_journal import RecoveryJournal

        authority = RecoveryAuthorityVerifier(recovery_authority_store_path(path))
        return RecoveryJournal._open_with_authority(path, authority)

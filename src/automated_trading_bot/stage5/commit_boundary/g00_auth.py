"""Synthetic authenticated G00 receipts for the isolated SOE boundary.

This module deliberately owns no production trust root.  Tests must provide an
explicit synthetic key ring; deployment identities and keys remain an external
qualification dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, replace
from threading import Lock
from time import monotonic_ns
from typing import Final, Mapping, Protocol


_DOMAIN: Final = b"ATIS-S5-G00-ADMISSION-RECEIPT-V1\x00"


class G00ReceiptRejected(ValueError):
    """An admission receipt failed authentication or context validation."""


@dataclass(frozen=True, slots=True)
class G00AdmissionReceipt:
    issuer_identity: str
    deployment_identity: str
    recipient: str
    operation_id: str
    request_digest: str
    scope: str
    generation: int
    issuer_epoch: int
    ticket_sequence: int
    observed_cutover_sequence: int | None
    issued_ns: int
    valid_until_ns: int
    nonce: str
    key_id: str
    signature: str = ""

    def payload(self) -> bytes:
        data = asdict(self)
        data.pop("signature")
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _DOMAIN + encoded


@dataclass(frozen=True, slots=True)
class VerifiedG00Attestation:
    """Immutable result of verification by an injected trust provider."""

    issuer_identity: str
    deployment_identity: str
    recipient: str
    operation_id: str
    request_digest: str
    scope: str
    generation: int
    issuer_epoch: int
    ticket_sequence: int
    observed_cutover_sequence: int | None
    replay_identity: str
    key_id: str
    verification_digest: str


class G00Verifier(Protocol):
    """Deployment-supplied verifier; this component owns no trust root."""

    def verify_and_consume(
        self,
        receipt: G00AdmissionReceipt,
        *,
        expected_issuer: str,
        expected_deployment: str,
        expected_recipient: str,
        operation_id: str,
        request_digest: str,
        scope: str,
        generation: int,
        issuer_epoch: int,
        admitted_ns: int,
    ) -> VerifiedG00Attestation: ...


class SyntheticG00TrustRoot:
    """Explicit synthetic HMAC trust owner with replay and revocation state."""

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        if not keys or any(not key_id or len(secret) < 32 for key_id, secret in keys.items()):
            raise ValueError("synthetic G00 keys require identifiers and at least 256 bits")
        self._keys = dict(keys)
        self._revoked: set[str] = set()
        self._consumed_nonces: set[str] = set()
        self._lock = Lock()

    def sign(self, receipt: G00AdmissionReceipt) -> G00AdmissionReceipt:
        secret = self._keys.get(receipt.key_id)
        if secret is None or receipt.key_id in self._revoked:
            raise G00ReceiptRejected("G00 signing key is unavailable or revoked")
        signature = hmac.new(secret, receipt.payload(), hashlib.sha256).hexdigest()
        return replace(receipt, signature=signature)

    def revoke(self, key_id: str) -> None:
        with self._lock:
            self._revoked.add(key_id)

    def verify_and_consume(
        self,
        receipt: G00AdmissionReceipt,
        *,
        expected_issuer: str,
        expected_deployment: str,
        expected_recipient: str,
        operation_id: str,
        request_digest: str,
        scope: str,
        generation: int,
        issuer_epoch: int,
        admitted_ns: int,
    ) -> VerifiedG00Attestation:
        if not all(
            (
                receipt.issuer_identity,
                receipt.deployment_identity,
                receipt.recipient,
                receipt.operation_id,
                receipt.request_digest,
                receipt.scope,
                receipt.nonce,
                receipt.key_id,
                receipt.signature,
            )
        ):
            raise G00ReceiptRejected("G00 receipt contains an empty identity field")
        if receipt.generation < 0 or receipt.issuer_epoch < 0 or receipt.ticket_sequence < 0:
            raise G00ReceiptRejected("G00 receipt contains a negative sequence or epoch")
        now_ns = monotonic_ns()
        if receipt.issued_ns > now_ns or now_ns > receipt.valid_until_ns:
            raise G00ReceiptRejected("G00 receipt is not fresh for this admission")
        expected = (
            expected_issuer,
            expected_deployment,
            expected_recipient,
            operation_id,
            request_digest,
            scope,
            generation,
            issuer_epoch,
        )
        actual = (
            receipt.issuer_identity,
            receipt.deployment_identity,
            receipt.recipient,
            receipt.operation_id,
            receipt.request_digest,
            receipt.scope,
            receipt.generation,
            receipt.issuer_epoch,
        )
        if actual != expected:
            raise G00ReceiptRejected("G00 receipt has a foreign admission context")
        with self._lock:
            if receipt.key_id in self._revoked:
                raise G00ReceiptRejected("G00 receipt key is revoked")
            secret = self._keys.get(receipt.key_id)
            if secret is None:
                raise G00ReceiptRejected("G00 receipt key is not trusted")
            expected_signature = hmac.new(
                secret, receipt.payload(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(receipt.signature, expected_signature):
                raise G00ReceiptRejected("G00 receipt signature is invalid")
            if receipt.nonce in self._consumed_nonces:
                raise G00ReceiptRejected("G00 receipt nonce was replayed")
            self._consumed_nonces.add(receipt.nonce)
        verification_digest = hashlib.sha256(
            b"ATIS-S5-G00-VERIFIED-V1\x00" + receipt.payload()
            + receipt.signature.encode("ascii")
        ).hexdigest()
        return VerifiedG00Attestation(
            issuer_identity=receipt.issuer_identity,
            deployment_identity=receipt.deployment_identity,
            recipient=receipt.recipient,
            operation_id=receipt.operation_id,
            request_digest=receipt.request_digest,
            scope=receipt.scope,
            generation=receipt.generation,
            issuer_epoch=receipt.issuer_epoch,
            ticket_sequence=receipt.ticket_sequence,
            observed_cutover_sequence=receipt.observed_cutover_sequence,
            replay_identity=receipt.nonce,
            key_id=receipt.key_id,
            verification_digest=verification_digest,
        )

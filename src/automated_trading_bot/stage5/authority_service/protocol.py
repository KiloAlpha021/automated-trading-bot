"""Canonical wire values for the local Authority Service."""

from __future__ import annotations

from dataclasses import dataclass
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
import json
from typing import Final

from automated_trading_bot.stage5.identity_scope import IdentityCapabilityV1


ADMISSION_DOMAIN: Final = b"ATIS_STAGE5_OPERATIONAL_ADMISSION_V1\0"
PROTOCOL: Final = "ATIS_STAGE5_AUTHORITY_SERVICE_V1"


class AuthorityServiceError(RuntimeError):
    """The service boundary cannot safely establish authority."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def digest(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise AuthorityServiceError(f"{name} must be non-empty text")
    return value


def _utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AuthorityServiceError(f"{name} is not a timestamp") from error
    if parsed.tzinfo is not timezone.utc:
        raise AuthorityServiceError(f"{name} must be canonical UTC")
    return parsed


@dataclass(frozen=True, slots=True)
class AdmissionEnvelope:
    service_identity: str
    deployment_identity: str
    authority_domain_identity: str
    signing_key_id: str
    signing_key_version: int
    signing_key_status: str
    store_epoch: str
    authority_id: str
    authority_head: str
    authority_generation: int
    authority_status: str
    authority_valid_from: str
    authority_expires_at: str
    provider_descriptor_digest: str
    provider_implementation_identity: str
    snapshot_id: str
    snapshot_digest: str
    snapshot_version: int
    snapshot_expires_at: str
    currency: str
    portfolio_id: str
    run_scope: str
    operation_purpose: str
    policy_id: str
    policy_version: int
    admission_id: str
    request_nonce: str
    admitted_at: str
    valid_until: str
    protocol: str = PROTOCOL

    def __post_init__(self) -> None:
        for name in (
            "service_identity", "deployment_identity", "authority_domain_identity",
            "signing_key_id", "signing_key_status", "store_epoch", "authority_id",
            "authority_head", "authority_status", "authority_valid_from",
            "authority_expires_at", "provider_descriptor_digest",
            "provider_implementation_identity", "snapshot_id", "snapshot_digest",
            "currency", "portfolio_id",
            "run_scope", "operation_purpose", "policy_id", "admission_id",
            "request_nonce",
        ):
            _text(getattr(self, name), name)
        if self.protocol != PROTOCOL:
            raise AuthorityServiceError("unsupported admission protocol")
        if self.signing_key_version < 1 or self.authority_generation < 1 or self.policy_version < 1:
            raise AuthorityServiceError("invalid key or authority generation")
        if self.signing_key_status != "SIGN_CURRENT":
            raise AuthorityServiceError("admission signing key is not current")
        if self.authority_status != "ACTIVE":
            raise AuthorityServiceError("admission authority is not active")
        if self.snapshot_version < 0:
            raise AuthorityServiceError("invalid snapshot version")
        admitted = _utc(self.admitted_at, "admitted_at")
        valid_until = _utc(self.valid_until, "valid_until")
        snapshot_expires_at = _utc(self.snapshot_expires_at, "snapshot_expires_at")
        authority_valid_from = _utc(
            self.authority_valid_from, "authority_valid_from"
        )
        authority_expires_at = _utc(
            self.authority_expires_at, "authority_expires_at"
        )
        if not authority_valid_from <= admitted < authority_expires_at:
            raise AuthorityServiceError("admission authority validity is inconsistent")
        if valid_until <= admitted:
            raise AuthorityServiceError("admission validity is empty")
        if snapshot_expires_at <= admitted or valid_until > snapshot_expires_at:
            raise AuthorityServiceError("admission exceeds snapshot currentness")

    def body(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }

    def signing_bytes(self) -> bytes:
        return ADMISSION_DOMAIN + canonical_bytes(self.body())


@dataclass(frozen=True, slots=True)
class SignedAdmission:
    envelope: AdmissionEnvelope
    signature: bytes

    def __post_init__(self) -> None:
        if type(self.envelope) is not AdmissionEnvelope:
            raise AuthorityServiceError("canonical admission envelope required")
        if type(self.signature) is not bytes or not self.signature:
            raise AuthorityServiceError("admission signature is missing")

    def wire(self) -> dict[str, object]:
        return {
            "envelope": self.envelope.body(),
            "signature": b64encode(self.signature).decode("ascii"),
        }

    @classmethod
    def from_wire(cls, value: object) -> SignedAdmission:
        if type(value) is not dict:
            raise AuthorityServiceError("signed admission response must be an object")
        envelope_value = value.get("envelope")
        signature_value = value.get("signature")
        if type(envelope_value) is not dict or type(signature_value) is not str:
            raise AuthorityServiceError("signed admission response is malformed")
        try:
            envelope = AdmissionEnvelope(**envelope_value)
            signature = b64decode(signature_value, validate=True)
        except (TypeError, ValueError) as error:
            raise AuthorityServiceError("signed admission response is malformed") from error
        return cls(envelope, signature)


@dataclass(frozen=True, slots=True)
class SignedIdentityCapability:
    """A capability-specific identity assertion; never a generic signing API."""

    capability: IdentityCapabilityV1
    key_id: str
    key_version: int
    nonce: str
    issued_at: str
    valid_until: str
    signature: bytes

    def __post_init__(self) -> None:
        if type(self.capability) is not IdentityCapabilityV1:
            raise AuthorityServiceError("canonical identity capability required")
        _text(self.key_id, "key_id")
        _text(self.nonce, "nonce")
        if self.key_version < 1 or not self.signature:
            raise AuthorityServiceError("identity signature metadata is invalid")
        issued = _utc(self.issued_at, "issued_at")
        valid = _utc(self.valid_until, "valid_until")
        if valid <= issued:
            raise AuthorityServiceError("identity capability validity is empty")

    def body(self) -> dict[str, object]:
        return {
            "capability": self.capability.body(),
            "issued_at": self.issued_at,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "nonce": self.nonce,
            "valid_until": self.valid_until,
        }

    def signing_bytes(self) -> bytes:
        domain = (
            b"ATIS-S5-ACCOUNT-IDENTITY-SIGNATURE-V1\0"
            if self.capability.capability == "ACCOUNT_IDENTITY_V1"
            else b"ATIS-S5-PORTFOLIO-IDENTITY-SIGNATURE-V1\0"
        )
        return domain + canonical_bytes(self.body())


def same_digest(left: str, right: str) -> bool:
    return compare_digest(left.encode("ascii"), right.encode("ascii"))

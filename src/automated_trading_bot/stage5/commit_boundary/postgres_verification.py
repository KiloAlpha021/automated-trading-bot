"""Verification-only U3 foundation, executed independently inside PostgreSQL.

The SQL wrapper supplies protected public policies and database time. A successful
signature is not lifecycle, catalogue, admission, or financial authorization.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..authority_service.cng import CngIdentityVerifier
from ..identity_scope import (
    AuthenticatedScopeEnvelopeV1,
    IdentityRecordV1,
    IdentityScopeError,
    OperationCoreV1,
    assertion_signing_bytes,
    parse_canonical,
    parse_container,
    parse_timestamp,
)
from ..scope_registry import RegistryTransitionV1


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    capability: str
    producer: str
    deployment: str
    key_id: str
    key_version: int
    policy_version: int
    public_blob: bytes
    valid_from: datetime
    valid_until: datetime
    revoked: bool


def verify_signature(
    capability: str,
    context: bytes,
    evidence: bytes,
    policies: Iterable[VerificationPolicy],
    now: datetime,
) -> str:
    """Reconstruct signed bytes; never accept a caller-supplied key or digest.

    Malformed/policy/signature failures raise IdentityScopeError. Native failures
    remain distinct so the SQL boundary can report unavailable, never valid.
    """
    payload, signature = parse_container(evidence)
    if capability in ("ACCOUNT", "PORTFOLIO"):
        identity = IdentityRecordV1.parse(parse_canonical(context))
        signing = assertion_signing_bytes(payload)
        if (
            identity.dimension != capability
            or payload["schema"] != capability.lower() + "-identity-assertion/v1"
            or payload["identity_digest"] != identity.digest
            or payload["state"] != "ACTIVE"
        ):
            raise IdentityScopeError("U3_CONTEXT_REJECTED")
        producer, deployment = identity.producer_id, identity.deployment_id
        key_id, key_version = (
            payload["producer_key_id"],
            payload["producer_key_version"],
        )
        start, end = payload["valid_from"], payload["valid_until"]
    elif capability == "SCOPE":
        core = OperationCoreV1(context)
        body = parse_canonical(core.raw)
        envelope = AuthenticatedScopeEnvelopeV1(evidence)
        if (
            any(
                payload[name] != body[name]
                for name in ("operation_id", "deployment_id", "operation_class")
            )
            or payload["core_digest"] != core.digest
        ):
            raise IdentityScopeError("U3_CONTEXT_REJECTED")
        signing = envelope.signing_bytes
        producer, deployment = payload["signer_id"], payload["deployment_id"]
        key_id, key_version = payload["signer_key_id"], payload["signer_key_version"]
        start, end = payload["issued_at"], payload["expires_at"]
    elif capability == "REGISTRY_TRANSITION":
        transition = RegistryTransitionV1(evidence)
        from ..identity_scope import exact_fields

        body = parse_canonical(context)
        exact_fields(body, ("actor", "deployment_id"))
        if any(body[name] != payload[name] for name in body):
            raise IdentityScopeError("U3_CONTEXT_REJECTED")
        signing = transition.signing_bytes
        producer, deployment = payload["actor"], payload["deployment_id"]
        key_id, key_version = payload["key_id"], payload["key_version"]
        start, end = payload["issued_at"], payload["expires_at"]
    else:
        raise IdentityScopeError("U3_CAPABILITY_REJECTED")
    if not parse_timestamp(start) <= now < parse_timestamp(end):
        raise IdentityScopeError("U3_VALIDITY_REJECTED")
    available = tuple(policies)
    selected = [
        p
        for p in available
        if (
            p.capability,
            p.producer,
            p.deployment,
            p.key_id,
            p.key_version,
            p.policy_version,
        )
        == (
            capability,
            producer,
            deployment,
            key_id,
            key_version,
            payload["policy_version"],
        )
    ]
    if len(selected) != 1:
        raise IdentityScopeError("U3_POLICY_REJECTED")
    policy = selected[0]
    if policy.revoked or not policy.valid_from <= now < policy.valid_until:
        raise IdentityScopeError("U3_POLICY_REJECTED")
    # A key cannot be provisioned for more than one cryptographic capability,
    # including revoked policies. Rotation cannot repurpose old signing keys.
    if any(
        p.capability != capability and p.public_blob == policy.public_blob
        for p in available
    ):
        raise IdentityScopeError("U3_CAPABILITY_REJECTED")
    if not CngIdentityVerifier(policy.public_blob).verify_bytes(signing, signature):
        raise IdentityScopeError("U3_SIGNATURE_REJECTED")
    return "VALID_SIGNATURE_ONLY"


def policies_from_rows(
    rows: Iterable[dict[str, Any]],
) -> tuple[VerificationPolicy, ...]:
    """Private SQL bridge; rows originate solely from the fixed protected table."""
    return tuple(
        VerificationPolicy(
            capability=row["capability"],
            producer=row["producer"],
            deployment=row["deployment"],
            key_id=row["key_id"],
            key_version=row["key_version"],
            policy_version=row["policy_version"],
            public_blob=bytes(row["public_blob"]),
            valid_from=datetime.fromisoformat(str(row["valid_from"])),
            valid_until=datetime.fromisoformat(str(row["valid_until"])),
            revoked=row["revoked"],
        )
        for row in rows
    )

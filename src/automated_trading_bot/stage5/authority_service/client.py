"""Fixed-service I01 client and verified operational admission wrapper."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from automated_trading_bot.stage5.authority_service.protocol import (
    AdmissionEnvelope,
    AuthorityServiceError,
    SignedAdmission,
)
from automated_trading_bot.stage5.authority_service.service import (
    AdmissionVerifier,
)
from automated_trading_bot.stage5.contracts import CapitalSnapshot, ProviderAuthority


class AuthorityServiceTransport(Protocol):
    """Deployment supplies one fixed authenticated transport implementation."""

    def request_admission(self, snapshot: CapitalSnapshot,
                          operation_purpose: str) -> SignedAdmission: ...

    def fenced_consumption(
        self, admission: SignedAdmission, operation_purpose: str
    ) -> AbstractContextManager[None]: ...

@dataclass(frozen=True, slots=True, init=False)
class VerifiedOperationalAdmission:
    """Evidence wrapper whose use still requires fixed-path revalidation."""

    snapshot: CapitalSnapshot
    signed_admission: SignedAdmission
    _receipt: bytes

    def __init__(self, *_: object, **__: object) -> None:
        raise AuthorityServiceError(
            "verified operational admission requires the fixed verification path"
        )

    @classmethod
    def _verified(
        cls, snapshot: CapitalSnapshot, signed: SignedAdmission
    ) -> VerifiedOperationalAdmission:
        value = object.__new__(cls)
        object.__setattr__(value, "snapshot", snapshot)
        object.__setattr__(value, "signed_admission", signed)
        object.__setattr__(
            value, "_receipt",
            sha256(signed.envelope.signing_bytes() + signed.signature).digest(),
        )
        return value


class I01AuthorityServiceClient:
    """No local authority fallback and no caller-selected service configuration."""

    def __init__(self, transport: AuthorityServiceTransport,
                 verifier: AdmissionVerifier) -> None:
        self._transport = transport
        self._verifier = verifier
        self._operational = False

    @classmethod
    def from_protected_windows_deployment(cls) -> I01AuthorityServiceClient:
        """Build the sole operational composition from protected local state."""
        from automated_trading_bot.stage5.authority_service.ipc import (
            WindowsRuntimePipeTransport,
        )
        transport, verifier = (
            WindowsRuntimePipeTransport.from_protected_windows_deployment()
        )
        value = object.__new__(cls)
        value._transport = transport
        value._verifier = verifier
        value._operational = True
        return value

    def admit(self, snapshot: CapitalSnapshot, *, operation_purpose: str) -> VerifiedOperationalAdmission:
        if not self._operational:
            raise AuthorityServiceError(
                "injectable Authority Service clients are ENGINEERING_ONLY"
            )
        if type(snapshot) is not CapitalSnapshot:
            raise AuthorityServiceError("canonical CapitalSnapshot required")
        if snapshot.provider_authority is not ProviderAuthority.AUTHORITATIVE_FINANCIAL:
            raise AuthorityServiceError("authoritative financial snapshot required")
        try:
            signed = self._transport.request_admission(snapshot, operation_purpose)
        except Exception as error:
            raise AuthorityServiceError("Authority Service is unavailable") from error
        self._verify(snapshot, signed, operation_purpose)
        return VerifiedOperationalAdmission._verified(snapshot, signed)

    def _verify(
        self,
        snapshot: CapitalSnapshot,
        signed: SignedAdmission,
        operation_purpose: str,
    ) -> AdmissionEnvelope:
        envelope = self._verifier.verify(
            signed,
            snapshot_digest=snapshot.content_digest,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.state_version,
            snapshot_expires_at=snapshot.expires_at.value.isoformat(),
            currency=snapshot.currency.code,
            portfolio_id=snapshot.portfolio_id,
            run_scope=snapshot.run_scope,
            operation_purpose=operation_purpose,
        )
        now = datetime.now(timezone.utc)
        if (
            datetime.fromisoformat(envelope.admitted_at) > now
            or datetime.fromisoformat(envelope.valid_until) <= now
            or snapshot.cutoff.value > now
            or snapshot.expires_at.value <= now
        ):
            raise AuthorityServiceError("service admission is expired")
        return envelope

    def require_current(
        self, value: VerifiedOperationalAdmission, *, operation_purpose: str
    ) -> VerifiedOperationalAdmission:
        if not self._operational or type(value) is not VerifiedOperationalAdmission:
            raise AuthorityServiceError("fixed operational verification is required")
        expected = sha256(
            value.signed_admission.envelope.signing_bytes()
            + value.signed_admission.signature
        ).digest()
        if value._receipt != expected:
            raise AuthorityServiceError("verified admission provenance is invalid")
        self._verify(value.snapshot, value.signed_admission, operation_purpose)
        fresh = self._transport.request_admission(value.snapshot, operation_purpose)
        self._verify(value.snapshot, fresh, operation_purpose)
        old = value.signed_admission.envelope
        new = fresh.envelope
        if (
            old.authority_domain_identity != new.authority_domain_identity
            or old.service_identity != new.service_identity
            or old.deployment_identity != new.deployment_identity
            or old.store_epoch != new.store_epoch
            or old.authority_id != new.authority_id
            or old.authority_head != new.authority_head
            or old.authority_generation != new.authority_generation
            or old.authority_status != new.authority_status
            or old.authority_valid_from != new.authority_valid_from
            or old.authority_expires_at != new.authority_expires_at
            or old.provider_descriptor_digest != new.provider_descriptor_digest
            or old.provider_implementation_identity
            != new.provider_implementation_identity
            or old.policy_id != new.policy_id
            or old.policy_version != new.policy_version
            or old.signing_key_id != new.signing_key_id
            or old.signing_key_version != new.signing_key_version
            or old.signing_key_status != new.signing_key_status
            or old.snapshot_id != new.snapshot_id
            or old.snapshot_digest != new.snapshot_digest
            or old.snapshot_version != new.snapshot_version
            or old.snapshot_expires_at != new.snapshot_expires_at
            or old.currency != new.currency
            or old.portfolio_id != new.portfolio_id
            or old.run_scope != new.run_scope
            or old.operation_purpose != new.operation_purpose
        ):
            raise AuthorityServiceError("service admission is no longer current")
        return VerifiedOperationalAdmission._verified(value.snapshot, fresh)

    @contextmanager
    def fenced_consumption(
        self, value: VerifiedOperationalAdmission, *, operation_purpose: str
    ) -> Iterator[VerifiedOperationalAdmission]:
        """Hold deployment authority currentness through a consumer section."""
        if not self._operational or type(value) is not VerifiedOperationalAdmission:
            raise AuthorityServiceError("fixed operational verification is required")
        expected = sha256(
            value.signed_admission.envelope.signing_bytes()
            + value.signed_admission.signature
        ).digest()
        if value._receipt != expected:
            raise AuthorityServiceError("verified admission provenance is invalid")
        self._verify(value.snapshot, value.signed_admission, operation_purpose)
        try:
            with self._transport.fenced_consumption(
                value.signed_admission, operation_purpose
            ):
                yield value
        except AuthorityServiceError:
            raise
        except Exception as error:
            raise AuthorityServiceError("operational authority fence failed") from error


def require_current_operational_admission(
    value: VerifiedOperationalAdmission, *, operation_purpose: str
) -> VerifiedOperationalAdmission:
    """Consumer boundary: rebuild trust from protected deployment state."""
    return I01AuthorityServiceClient.from_protected_windows_deployment().require_current(
        value, operation_purpose=operation_purpose
    )


@contextmanager
def operational_authority_fence(
    value: VerifiedOperationalAdmission, *, operation_purpose: str
) -> Iterator[VerifiedOperationalAdmission]:
    """Future I02 boundary: AUTHORITY is held before any CAPITAL lock."""
    client = I01AuthorityServiceClient.from_protected_windows_deployment()
    with client.fenced_consumption(
        value, operation_purpose=operation_purpose
    ) as fenced:
        yield fenced

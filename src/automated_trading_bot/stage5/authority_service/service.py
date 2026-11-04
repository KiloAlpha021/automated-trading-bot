"""Service-side admission composition and verify-only I01 adapter."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Protocol
from uuid import uuid4

from automated_trading_bot.stage5.identity_scope import IdentityCapabilityV1

from automated_trading_bot.stage5.authority_service.persistence import AuthorityState
from automated_trading_bot.stage5.authority_service.keys import require_signing_key
from automated_trading_bot.stage5.authority_service.protocol import (
    AdmissionEnvelope,
    AuthorityServiceError,
    SignedAdmission,
    SignedIdentityCapability,
)


class AdmissionSigner(Protocol):
    @property
    def key_id(self) -> str: ...
    @property
    def key_version(self) -> int: ...
    def sign_admission(self, envelope: AdmissionEnvelope) -> bytes: ...


class AdmissionSignatureVerifier(Protocol):
    def verify_admission(self, admission: SignedAdmission) -> bool: ...


class IdentityCapabilitySigner(Protocol):
    @property
    def key_id(self) -> str: ...
    @property
    def key_version(self) -> int: ...
    @property
    def capability(self) -> str: ...
    def sign_identity(self, value: bytes) -> bytes: ...


class CurrentAuthority(Protocol):
    def admission_witness(
        self, observed_at: datetime
    ) -> AbstractContextManager[AuthorityState]: ...

    def consumption_witness(
        self, observed_at: datetime, *, timeout_seconds: float
    ) -> AbstractContextManager[AuthorityState]: ...


class TrustedClock(Protocol):
    def now(self) -> datetime: ...


class AuthoritativeSnapshotSource(Protocol):
    def resolve_snapshot(self, *, portfolio_id: str, run_scope: str,
                         operation_purpose: str) -> AuthoritativeSnapshotRecord: ...


@dataclass(frozen=True, slots=True)
class AuthoritativeSnapshotRecord:
    snapshot_id: str
    snapshot_digest: str
    snapshot_version: int
    expires_at: str
    portfolio_id: str
    run_scope: str
    operation_purpose: str
    currency: str
    provider_descriptor_digest: str
    provider_implementation_identity: str
    policy_id: str
    policy_version: int


@dataclass(frozen=True, slots=True)
class AuthorityLineage:
    domain_identity: str
    store_epoch: str
    authority_id: str
    authority_head: str
    authority_generation: int
    authority_status: str
    authority_valid_from: str
    authority_expires_at: str
    provider_descriptor_digest: str
    policy_id: str
    policy_version: int
    signing_key_id: str
    signing_key_version: int
    signing_key_current: bool

    @classmethod
    def from_state(cls, state: AuthorityState) -> AuthorityLineage:
        state.verify()
        require_signing_key(
            state, key_id=state.current_key_id, version=state.current_key_version
        )
        return cls(
            state.domain_identity, state.store_epoch, state.authority_id,
            state.authority_head, state.authority_generation,
            state.authority_status.value, state.authority_valid_from,
            state.authority_expires_at, state.provider_descriptor_digest,
            state.policy_id, state.policy_version, state.current_key_id,
            state.current_key_version, True,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAdmissionRequest:
    portfolio_id: str
    run_scope: str
    operation_purpose: str
    expected_domain_identity: str
    request_nonce: str


class AuthorityService:
    """No generic signing API: only validated canonical admissions are signed."""

    def __init__(self, *, service_identity: str, deployment_identity: str,
                 authority: CurrentAuthority, signer: AdmissionSigner,
                 snapshot_source: AuthoritativeSnapshotSource,
                 clock: TrustedClock,
                 admission_verifier: AdmissionSignatureVerifier | None = None,
                 identity_signers: tuple[IdentityCapabilitySigner, ...] = ()) -> None:
        self._service_identity = service_identity
        self._deployment_identity = deployment_identity
        self._authority = authority
        self._signer = signer
        self._snapshot_source = snapshot_source
        self._clock = clock
        self._admission_verifier = admission_verifier
        self._identity_signers = {
            value.capability: value for value in identity_signers
        }
        if len(self._identity_signers) != len(identity_signers):
            raise AuthorityServiceError("duplicate identity capability signer")

    def issue_identity(
        self, capability: IdentityCapabilityV1, *, request_nonce: str
    ) -> SignedIdentityCapability:
        """Issue one bounded identity assertion with a capability-specific key."""
        if type(capability) is not IdentityCapabilityV1:
            raise AuthorityServiceError("typed identity capability required")
        if capability.deployment_identity != self._deployment_identity:
            raise AuthorityServiceError("foreign identity deployment")
        signer = self._identity_signers.get(capability.capability)
        if signer is None:
            raise AuthorityServiceError("identity capability is unavailable")
        now = self._clock.now()
        if now.tzinfo is not timezone.utc:
            raise AuthorityServiceError("trusted clock must return canonical UTC")
        unsigned = SignedIdentityCapability(
            capability=capability,
            key_id=signer.key_id,
            key_version=signer.key_version,
            nonce=request_nonce,
            issued_at=now.isoformat(),
            valid_until=(now + timedelta(minutes=5)).isoformat(),
            signature=b"UNSIGNED",
        )
        return SignedIdentityCapability(
            capability=capability,
            key_id=signer.key_id,
            key_version=signer.key_version,
            nonce=request_nonce,
            issued_at=unsigned.issued_at,
            valid_until=unsigned.valid_until,
            signature=signer.sign_identity(unsigned.signing_bytes()),
        )

    def admit(self, request: RuntimeAdmissionRequest) -> SignedAdmission:
        if type(request) is not RuntimeAdmissionRequest:
            raise AuthorityServiceError("typed runtime admission request required")
        if type(request.request_nonce) is not str or not request.request_nonce:
            raise AuthorityServiceError("Runtime request nonce is missing")
        now = self._clock.now()
        if now.tzinfo is not timezone.utc:
            raise AuthorityServiceError("trusted clock must return canonical UTC")
        with self._authority.admission_witness(now) as state:
            state.verify()
            require_signing_key(
                state, key_id=self._signer.key_id, version=self._signer.key_version
            )
            if request.expected_domain_identity != state.domain_identity:
                raise AuthorityServiceError("foreign authority domain")
            snapshot = self._snapshot_source.resolve_snapshot(
                portfolio_id=request.portfolio_id, run_scope=request.run_scope,
                operation_purpose=request.operation_purpose,
            )
            if (
                snapshot.portfolio_id != request.portfolio_id
                or snapshot.run_scope != request.run_scope
                or snapshot.operation_purpose != request.operation_purpose
                or snapshot.currency != state.currency
                or snapshot.provider_descriptor_digest != state.provider_descriptor_digest
                or snapshot.provider_implementation_identity
                != state.provider_implementation_identity
                or snapshot.policy_id != state.policy_id
                or snapshot.policy_version != state.policy_version
            ):
                raise AuthorityServiceError("authoritative snapshot provenance mismatch")
            snapshot_expiry = datetime.fromisoformat(snapshot.expires_at)
            if snapshot_expiry.tzinfo is not timezone.utc or snapshot_expiry <= now:
                raise AuthorityServiceError("authoritative snapshot is stale")
            valid_until = min(now + timedelta(minutes=5), snapshot_expiry)
            envelope = AdmissionEnvelope(
                service_identity=self._service_identity,
                deployment_identity=self._deployment_identity,
                authority_domain_identity=state.domain_identity,
                signing_key_id=self._signer.key_id,
                signing_key_version=self._signer.key_version,
                signing_key_status="SIGN_CURRENT",
                store_epoch=state.store_epoch,
                authority_id=state.authority_id,
                authority_head=state.authority_head,
                authority_generation=state.authority_generation,
                authority_status=state.authority_status.value,
                authority_valid_from=state.authority_valid_from,
                authority_expires_at=state.authority_expires_at,
                provider_descriptor_digest=state.provider_descriptor_digest,
                provider_implementation_identity=(
                    state.provider_implementation_identity
                ),
                snapshot_id=snapshot.snapshot_id,
                snapshot_digest=snapshot.snapshot_digest,
                snapshot_version=snapshot.snapshot_version,
                snapshot_expires_at=snapshot.expires_at,
                currency=snapshot.currency,
                portfolio_id=request.portfolio_id,
                run_scope=request.run_scope,
                operation_purpose=request.operation_purpose,
                policy_id=state.policy_id,
                policy_version=state.policy_version,
                admission_id=str(uuid4()),
                request_nonce=request.request_nonce,
                admitted_at=now.isoformat(),
                valid_until=valid_until.isoformat(),
            )
            signature = self._signer.sign_admission(envelope)
            return SignedAdmission(envelope, signature)

    @contextmanager
    def consumption_fence(
        self,
        admission: SignedAdmission,
        *,
        operation_purpose: str,
        timeout_seconds: float,
    ) -> Iterator[AdmissionEnvelope]:
        """Revalidate and hold one deployment authority witness for a consumer."""
        if type(admission) is not SignedAdmission:
            raise AuthorityServiceError("signed admission required")
        if self._admission_verifier is None:
            raise AuthorityServiceError("operational admission verifier is unavailable")
        if not self._admission_verifier.verify_admission(admission):
            raise AuthorityServiceError("invalid service admission signature")
        now = self._clock.now()
        if now.tzinfo is not timezone.utc:
            raise AuthorityServiceError("trusted clock must return canonical UTC")
        envelope = admission.envelope
        if (
            envelope.service_identity != self._service_identity
            or envelope.deployment_identity != self._deployment_identity
            or envelope.operation_purpose != operation_purpose
            or datetime.fromisoformat(envelope.admitted_at) > now
            or datetime.fromisoformat(envelope.valid_until) <= now
        ):
            raise AuthorityServiceError("fenced admission is invalid or expired")
        with self._authority.consumption_witness(
            now, timeout_seconds=timeout_seconds
        ) as state:
            state.verify()
            require_signing_key(
                state,
                key_id=envelope.signing_key_id,
                version=envelope.signing_key_version,
            )
            if (
                envelope.authority_domain_identity != state.domain_identity
                or envelope.store_epoch != state.store_epoch
                or envelope.authority_id != state.authority_id
                or envelope.authority_head != state.authority_head
                or envelope.authority_generation != state.authority_generation
                or envelope.authority_status != state.authority_status.value
                or envelope.authority_valid_from != state.authority_valid_from
                or envelope.authority_expires_at != state.authority_expires_at
                or envelope.provider_descriptor_digest
                != state.provider_descriptor_digest
                or envelope.provider_implementation_identity
                != state.provider_implementation_identity
                or envelope.policy_id != state.policy_id
                or envelope.policy_version != state.policy_version
                or envelope.signing_key_id != state.current_key_id
                or envelope.signing_key_version != state.current_key_version
                or envelope.signing_key_status != "SIGN_CURRENT"
            ):
                raise AuthorityServiceError("fenced admission authority is not current")
            try:
                snapshot = self._snapshot_source.resolve_snapshot(
                    portfolio_id=envelope.portfolio_id,
                    run_scope=envelope.run_scope,
                    operation_purpose=envelope.operation_purpose,
                )
            except Exception as error:
                raise AuthorityServiceError(
                    "fenced admission snapshot resolution failed"
                ) from error
            if (
                snapshot.snapshot_id != envelope.snapshot_id
                or snapshot.snapshot_digest != envelope.snapshot_digest
                or snapshot.snapshot_version != envelope.snapshot_version
                or snapshot.expires_at != envelope.snapshot_expires_at
                or snapshot.currency != envelope.currency
                or snapshot.provider_descriptor_digest
                != envelope.provider_descriptor_digest
                or snapshot.provider_implementation_identity
                != envelope.provider_implementation_identity
                or snapshot.policy_id != envelope.policy_id
                or snapshot.policy_version != envelope.policy_version
                or snapshot.portfolio_id != envelope.portfolio_id
                or snapshot.run_scope != envelope.run_scope
                or snapshot.operation_purpose != envelope.operation_purpose
                or datetime.fromisoformat(snapshot.expires_at) <= now
            ):
                raise AuthorityServiceError("fenced admission snapshot is not current")
            yield envelope


class AdmissionVerifier:
    """Runtime has pinned verification identity and no signing capability."""

    def __init__(self, *, verifier: AdmissionSignatureVerifier,
                 service_identity: str, deployment_identity: str,
                 authority_domain_identity: str, signing_key_id: str,
                 signing_key_version: int) -> None:
        self._verifier = verifier
        self._service_identity = service_identity
        self._deployment_identity = deployment_identity
        self._domain_identity = authority_domain_identity
        self._signing_key_id = signing_key_id
        self._signing_key_version = signing_key_version

    def verify(self, admission: SignedAdmission, *, snapshot_digest: str,
               snapshot_id: str, snapshot_version: int,
               snapshot_expires_at: str, currency: str, portfolio_id: str, run_scope: str,
               operation_purpose: str) -> AdmissionEnvelope:
        if type(admission) is not SignedAdmission:
            raise AuthorityServiceError("signed admission required")
        envelope = admission.envelope
        if (
            envelope.service_identity != self._service_identity
            or envelope.deployment_identity != self._deployment_identity
            or envelope.authority_domain_identity != self._domain_identity
            or envelope.signing_key_id != self._signing_key_id
            or envelope.signing_key_version != self._signing_key_version
            or envelope.signing_key_status != "SIGN_CURRENT"
            or envelope.authority_status != "ACTIVE"
            or envelope.snapshot_id != snapshot_id
            or envelope.snapshot_digest != snapshot_digest
            or envelope.snapshot_version != snapshot_version
            or envelope.snapshot_expires_at != snapshot_expires_at
            or envelope.currency != currency
            or envelope.portfolio_id != portfolio_id
            or envelope.run_scope != run_scope
            or envelope.operation_purpose != operation_purpose
        ):
            raise AuthorityServiceError("admission binding mismatch")
        if not self._verifier.verify_admission(admission):
            raise AuthorityServiceError("invalid service admission signature")
        return envelope

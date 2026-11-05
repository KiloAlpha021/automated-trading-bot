"""Fixed-service I01 client and verified operational admission wrapper."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, Any, Callable, Iterable
from automated_trading_bot.stage5.identity_scope import (
    IdentityScopeError,
    IdentityRecordV1,
    parse_canonical,
    canonical_bytes as identity_bytes,
    parse_container,
    assertion_signing_bytes,
    parse_timestamp,
    AuthenticatedScopeEnvelopeV1,
    OperationCoreV1,
    digest_list,
    positive,
    identity_request_budget,
)

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

    def request_admission(
        self, snapshot: CapitalSnapshot, operation_purpose: str
    ) -> SignedAdmission: ...

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
            value,
            "_receipt",
            sha256(signed.envelope.signing_bytes() + signed.signature).digest(),
        )
        return value


class I01AuthorityServiceClient:
    """No local authority fallback and no caller-selected service configuration."""

    def __init__(
        self, transport: AuthorityServiceTransport, verifier: AdmissionVerifier
    ) -> None:
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

    def admit(
        self, snapshot: CapitalSnapshot, *, operation_purpose: str
    ) -> VerifiedOperationalAdmission:
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
    return (
        I01AuthorityServiceClient.from_protected_windows_deployment().require_current(
            value, operation_purpose=operation_purpose
        )
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


class ScopeRegistryVerifier(Protocol):
    def verify_scope(self, payload: dict[str, Any]) -> tuple[int, Iterable[str]]: ...


@dataclass(frozen=True, slots=True)
class IdentityKeyPolicy:
    dimension: str
    deployment: str
    key_id: str
    key_version: int
    policy_version: int
    public_blob: bytes
    valid_from: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        if (
            self.dimension not in ("ACCOUNT", "PORTFOLIO", "SCOPE")
            or not self.deployment
            or not self.key_id
            or not self.public_blob
        ):
            raise IdentityScopeError("incomplete protected identity policy")
        positive(self.key_version, "key version")
        positive(self.policy_version, "policy version")
        if (
            type(self.key_version) is not int
            or self.key_version < 1
            or type(self.policy_version) is not int
            or self.policy_version < 1
        ):
            raise IdentityScopeError("invalid policy version")
        if (
            self.valid_from.tzinfo is not timezone.utc
            or self.valid_until.tzinfo is not timezone.utc
            or self.valid_until <= self.valid_from
        ):
            raise IdentityScopeError("invalid protected policy validity")


class IdentityConsumer:
    """Verify-only protected composition; trust providers are never in requests."""

    def __init__(
        self,
        policies: Iterable[IdentityKeyPolicy],
        current_assertion: Callable[[IdentityRecordV1], tuple[bytes, bytes] | None],
        clock: Callable[[], datetime],
        registry: ScopeRegistryVerifier | None = None,
    ) -> None:
        self.policies = tuple(policies)
        self.current_assertion = current_assertion
        self.clock = clock
        self.registry = registry
        if (
            len({p.dimension for p in self.policies}) != len(self.policies)
            or len({p.key_id for p in self.policies}) != len(self.policies)
            or len({p.public_blob for p in self.policies}) != len(self.policies)
        ):
            raise IdentityScopeError("capability keys/policies overlap")

    def policy(
        self,
        dimension: str,
        deployment: str,
        key_id: str,
        key_version: int,
        policy_version: int,
    ) -> IdentityKeyPolicy:
        now = self.clock()
        if now.tzinfo is not timezone.utc:
            raise IdentityScopeError("untrusted clock format")
        found = [
            p
            for p in self.policies
            if (p.dimension, p.deployment, p.key_id, p.key_version, p.policy_version)
            == (dimension, deployment, key_id, key_version, policy_version)
            and p.valid_from <= now < p.valid_until
        ]
        if len(found) != 1:
            raise IdentityScopeError("missing/stale/cross-capability policy")
        return found[0]

    def verify_signature(self, record: IdentityRecordV1, raw: bytes) -> dict[str, Any]:
        from .cng import CngIdentityVerifier

        if not isinstance(record, IdentityRecordV1):
            raise IdentityScopeError("INPUT_INVALID: identity record required")
        payload, signature = parse_container(raw)
        signing = assertion_signing_bytes(payload)
        if (
            record.dimension not in ("ACCOUNT", "PORTFOLIO")
            or payload["schema"] != record.dimension.lower() + "-identity-assertion/v1"
            or payload["identity_digest"] != record.digest
        ):
            raise IdentityScopeError("identity context substitution")
        policy = self.policy(
            record.dimension,
            record.deployment_id,
            payload["producer_key_id"],
            payload["producer_key_version"],
            payload["policy_version"],
        )
        if not CngIdentityVerifier(policy.public_blob).verify_bytes(signing, signature):
            raise IdentityScopeError("invalid identity signature")
        if (
            not parse_timestamp(payload["valid_from"])
            <= self.clock()
            < parse_timestamp(payload["valid_until"])
        ):
            raise IdentityScopeError("identity outside validity")
        return payload

    def verify(self, record: IdentityRecordV1, raw: bytes) -> dict[str, Any]:
        with identity_request_budget():
            from .persistence import IdentityLifecycleStore

            if not isinstance(record, IdentityRecordV1):
                raise IdentityScopeError("INPUT_INVALID: identity record required")
            record = IdentityRecordV1.parse(record.body())
            owner = getattr(self.current_assertion, "__self__", None)
            if type(owner) is not IdentityLifecycleStore:
                raise IdentityScopeError(
                    "qualified atomic current-state provider unavailable"
                )
            current, parent = owner.verification_view()
            return self._verify_current(record, raw, current, parent)

    def _verify_current(
        self,
        record: IdentityRecordV1,
        raw: bytes,
        current_provider: Callable[[IdentityRecordV1], tuple[bytes, bytes] | None],
        parent_provider: Callable[[str], tuple[bytes, bytes] | None],
    ) -> dict[str, Any]:
        if type(record) not in (IdentityRecordV1,):
            record = IdentityRecordV1.parse(record.body())
        payload = self.verify_signature(record, raw)
        current = current_provider(record)
        if (
            current is None
            or current != (identity_bytes(record.body()), raw)
            or payload["state"] != "ACTIVE"
        ):
            raise IdentityScopeError("stale or inactive identity")
        if record.dimension == "PORTFOLIO":
            # Parent must be obtained from independently owned current state,
            # never merely from the signed portfolio's caller-supplied string.
            parent = parent_provider(payload["parent_account_identity_digest"])
            if parent is None:
                raise IdentityScopeError("authenticated parent unavailable")
            parent_record = IdentityRecordV1.parse(parse_canonical(parent[0]))
            if (
                parent_record.dimension != "ACCOUNT"
                or parent_record.deployment_id != record.deployment_id
            ):
                raise IdentityScopeError("foreign parent")
            self._verify_current(
                parent_record, parent[1], current_provider, parent_provider
            )
        return payload

    def verify_scope(
        self, core: OperationCoreV1, raw: bytes
    ) -> AuthenticatedScopeEnvelopeV1:
        with identity_request_budget():
            from .cng import CngIdentityVerifier

            if not isinstance(core, OperationCoreV1):
                raise IdentityScopeError("INPUT_INVALID: operation core required")
            envelope = AuthenticatedScopeEnvelopeV1(raw)
            p, signature = parse_container(raw)
            c = parse_canonical(core.raw)
            if (
                p["operation_id"],
                p["core_digest"],
                p["deployment_id"],
                p["operation_class"],
            ) != (
                c["operation_id"],
                core.digest,
                c["deployment_id"],
                c["operation_class"],
            ):
                raise IdentityScopeError("operation core substitution")
            policy = self.policy(
                "SCOPE",
                p["deployment_id"],
                p["signer_key_id"],
                p["signer_key_version"],
                p["policy_version"],
            )
            if not CngIdentityVerifier(policy.public_blob).verify_bytes(
                envelope.signing_bytes, signature
            ):
                raise IdentityScopeError("invalid scope signature")
            if (
                not parse_timestamp(p["issued_at"])
                <= self.clock()
                < parse_timestamp(p["expires_at"])
            ):
                raise IdentityScopeError("scope outside validity")
            if self.registry is None:
                raise IdentityScopeError("qualified registry unavailable")
            # U3 owns this protected interface and all non-account/portfolio
            # assertion authority. No snapshot or registry callback comes in wire.
            epoch, closure = self.registry.verify_scope(p)
            if (
                type(epoch) is not int
                or epoch != p["catalogue_epoch"]
                or digest_list(closure) != p["closure_digest"]
            ):
                raise IdentityScopeError("stale catalogue or forged closure")
            for record, assertion in zip(
                p["leaf_identities"], p["identity_assertions"]
            ):
                identity = IdentityRecordV1.parse(record)
                if identity.dimension in ("ACCOUNT", "PORTFOLIO"):
                    self.verify(identity, identity_bytes(assertion))
            return envelope

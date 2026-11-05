"""Service-side admission composition and verify-only I01 adapter."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Protocol, Callable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .persistence import IdentityLifecycleStore
    from .cng import CngIdentityCapabilitySigner
    from .client import IdentityConsumer
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
    def resolve_snapshot(
        self, *, portfolio_id: str, run_scope: str, operation_purpose: str
    ) -> AuthoritativeSnapshotRecord: ...


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
            state.domain_identity,
            state.store_epoch,
            state.authority_id,
            state.authority_head,
            state.authority_generation,
            state.authority_status.value,
            state.authority_valid_from,
            state.authority_expires_at,
            state.provider_descriptor_digest,
            state.policy_id,
            state.policy_version,
            state.current_key_id,
            state.current_key_version,
            True,
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

    def __init__(
        self,
        *,
        service_identity: str,
        deployment_identity: str,
        authority: CurrentAuthority,
        signer: AdmissionSigner,
        snapshot_source: AuthoritativeSnapshotSource,
        clock: TrustedClock,
        admission_verifier: AdmissionSignatureVerifier | None = None,
        identity_signers: tuple[IdentityCapabilitySigner, ...] = (),
    ) -> None:
        self._service_identity = service_identity
        self._deployment_identity = deployment_identity
        self._authority = authority
        self._signer = signer
        self._snapshot_source = snapshot_source
        self._clock = clock
        self._admission_verifier = admission_verifier
        self._identity_signers = {value.capability: value for value in identity_signers}
        if len(self._identity_signers) != len(identity_signers):
            raise AuthorityServiceError("duplicate identity capability signer")

    def issue_identity(
        self, capability: IdentityCapabilityV1, *, request_nonce: str
    ) -> SignedIdentityCapability:
        """Legacy unsigned-schema issuance cannot bypass durable capabilities."""
        raise AuthorityServiceError(
            "legacy identity issuance unavailable; use protected lifecycle endpoint"
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
                portfolio_id=request.portfolio_id,
                run_scope=request.run_scope,
                operation_purpose=request.operation_purpose,
            )
            if (
                snapshot.portfolio_id != request.portfolio_id
                or snapshot.run_scope != request.run_scope
                or snapshot.operation_purpose != request.operation_purpose
                or snapshot.currency != state.currency
                or snapshot.provider_descriptor_digest
                != state.provider_descriptor_digest
                or snapshot.provider_implementation_identity
                != state.provider_implementation_identity
                or snapshot.policy_id != state.policy_id
                or snapshot.policy_version != state.policy_version
            ):
                raise AuthorityServiceError(
                    "authoritative snapshot provenance mismatch"
                )
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

    def __init__(
        self,
        *,
        verifier: AdmissionSignatureVerifier,
        service_identity: str,
        deployment_identity: str,
        authority_domain_identity: str,
        signing_key_id: str,
        signing_key_version: int,
    ) -> None:
        self._verifier = verifier
        self._service_identity = service_identity
        self._deployment_identity = deployment_identity
        self._domain_identity = authority_domain_identity
        self._signing_key_id = signing_key_id
        self._signing_key_version = signing_key_version

    def verify(
        self,
        admission: SignedAdmission,
        *,
        snapshot_digest: str,
        snapshot_id: str,
        snapshot_version: int,
        snapshot_expires_at: str,
        currency: str,
        portfolio_id: str,
        run_scope: str,
        operation_purpose: str,
    ) -> AdmissionEnvelope:
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


class IdentityCapabilityService:
    """Two capability-specific issuers within the existing independent host.

    Only protected management handlers own this object. Runtime consumers own
    verify-only policy. All issuance state changes use one durable transaction.
    """

    def __init__(
        self,
        store: IdentityLifecycleStore,
        signers: Mapping[str, CngIdentityCapabilitySigner],
        consumer: IdentityConsumer,
        clock: Callable[[], datetime],
        deployment: str,
    ) -> None:
        from automated_trading_bot.stage5.identity_scope import IdentityScopeError

        self.store = store
        self._signers = dict(signers)
        self.consumer = consumer
        self.clock = clock
        self.deployment = deployment
        if set(self._signers) != {"ACCOUNT", "PORTFOLIO"}:
            raise IdentityScopeError("two independent identity capabilities required")
        for dimension, signer in self._signers.items():
            policies = [p for p in consumer.policies if p.dimension == dimension]
            if (
                len(policies) != 1
                or signer.capability != dimension + "_IDENTITY_V1"
                or signer.public_blob() != policies[0].public_blob
                or signer.key_id != policies[0].key_id
                or signer.key_version != policies[0].key_version
            ):
                raise IdentityScopeError("identity signer/policy mismatch")

    def issue(self, dimension: str, command: object) -> bytes:
        from automated_trading_bot.stage5.identity_scope import (
            IdentityScopeError,
            IdentityRecordV1,
            exact_fields,
            positive,
            hex_digest,
            parse_timestamp,
            canonical_bytes as wire,
            parse_canonical,
            assertion_signing_bytes,
            signed_container,
            assertion_digest,
            json_object,
            check_request_budget,
        )

        command = json_object(command)
        wire(command)
        exact_fields(
            command,
            (
                "command_id",
                "record",
                "lifecycle_version",
                "state",
                "valid_from",
                "valid_until",
                "policy_version",
                "nonce",
                "parent",
            ),
        )
        if dimension not in self._signers:
            raise IdentityScopeError("invalid issuing endpoint")
        record = IdentityRecordV1.parse(command["record"])
        if record.dimension != dimension or record.deployment_id != self.deployment:
            raise IdentityScopeError("foreign identity endpoint/deployment")
        if type(command["command_id"]) is not str or not command["command_id"]:
            raise IdentityScopeError("command ID required")
        version = positive(command["lifecycle_version"], "lifecycle")
        hex_digest(command["nonce"])
        positive(command["policy_version"], "policy version")
        signer = self._signers[dimension]
        self.consumer.policy(
            dimension,
            self.deployment,
            signer.key_id,
            signer.key_version,
            command["policy_version"],
        )
        if (
            not parse_timestamp(command["valid_from"])
            <= self.clock()
            < parse_timestamp(command["valid_until"])
        ):
            raise IdentityScopeError("issuance outside validity")
        assertions, current_table, commands = self.store.tables_for(dimension)
        with self.store.transaction() as c:
            if c.execute(
                "SELECT 1 FROM "
                + commands
                + " WHERE (dimension=? AND command_id=?) OR (key_id=? AND nonce=?)",
                (dimension, command["command_id"], signer.key_id, command["nonce"]),
            ).fetchone():
                raise IdentityScopeError("command/nonce replay")
            prior = self.store.current(record, c)
            predecessor = None
            if prior is not None:
                old = IdentityRecordV1.parse(parse_canonical(prior[0]))
                # Public digest/lineage consistency is not signing authority.
                # Authenticate the exact current predecessor under this same
                # write transaction before using its epoch or lifecycle state.
                oldp = self.consumer.verify_signature(old, prior[1])
                if record.producer_epoch < old.producer_epoch:
                    raise IdentityScopeError("producer epoch rollback")
                if record.producer_epoch == old.producer_epoch:
                    if (
                        oldp["state"] == "REVOKED"
                        or version != oldp["lifecycle_version"] + 1
                    ):
                        raise IdentityScopeError("revoked or nonmonotonic lifecycle")
                    predecessor = assertion_digest(prior[1])
                elif version != 1:
                    raise IdentityScopeError("new epoch must start lifecycle")
            elif version != 1:
                raise IdentityScopeError("missing lifecycle predecessor")
            if predecessor is None and command["state"] != "ACTIVE":
                raise IdentityScopeError("creation must be ACTIVE")
            payload = {
                "schema": dimension.lower() + "-identity-assertion/v1",
                "identity_digest": record.digest,
                "lifecycle_version": version,
                "state": command["state"],
                "valid_from": command["valid_from"],
                "valid_until": command["valid_until"],
                "policy_version": command["policy_version"],
                "producer_key_id": signer.key_id,
                "producer_key_version": signer.key_version,
                "predecessor_digest": predecessor,
                "nonce": command["nonce"],
            }
            if dimension == "PORTFOLIO":
                parent = command["parent"]
                exact_fields(parent, ("record", "assertion_hex"))
                parent_record = IdentityRecordV1.parse(parent["record"])
                if (
                    parent_record.dimension != "ACCOUNT"
                    or parent_record.deployment_id != self.deployment
                ):
                    raise IdentityScopeError("foreign parent")
                try:
                    parent_raw = bytes.fromhex(parent["assertion_hex"])
                except (ValueError, TypeError) as exc:
                    raise IdentityScopeError("invalid parent evidence") from exc
                parent_payload = self.consumer.verify_signature(
                    parent_record, parent_raw
                )
                if parent_payload["state"] != "ACTIVE" or self.store.current(
                    parent_record, c
                ) != (wire(parent_record.body()), parent_raw):
                    raise IdentityScopeError("stale/revoked parent")
                payload["parent_account_identity_digest"] = parent_record.digest
                if (
                    predecessor is not None
                    and oldp["parent_account_identity_digest"] != parent_record.digest
                ):
                    raise IdentityScopeError("in-place parent substitution")
            elif command["parent"] is not None:
                raise IdentityScopeError("account cannot have parent")
            check_request_budget()
            signing = assertion_signing_bytes(payload)
            raw = signed_container(payload, signer.sign_identity(signing))
            self.consumer.verify_signature(record, raw)
            check_request_budget()
            digest = assertion_digest(raw)
            c.execute(
                "INSERT INTO " + assertions + " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    digest,
                    record.digest,
                    wire(record.body()),
                    raw,
                    record.deployment_id,
                    dimension,
                    record.value,
                    record.producer_epoch,
                    version,
                    command["state"],
                ),
            )
            c.execute(
                "INSERT INTO " + commands + " VALUES (?,?,?,?,?)",
                (
                    dimension,
                    command["command_id"],
                    signer.key_id,
                    command["nonce"],
                    digest,
                ),
            )
            c.execute(
                "INSERT INTO "
                + current_table
                + " VALUES (?,?,?,?) ON CONFLICT(deployment,dimension,value) DO UPDATE SET digest=excluded.digest",
                (record.deployment_id, dimension, record.value, digest),
            )
        # Returned only after durable commit; ambiguous completion cannot reissue
        # the same command. A protected read resolves the exact stored assertion.
        return raw

    def read(self, dimension: str, record: object) -> bytes:
        from automated_trading_bot.stage5.identity_scope import (
            IdentityRecordV1,
            IdentityScopeError,
        )

        record = IdentityRecordV1.parse(record)
        if record.dimension != dimension or record.deployment_id != self.deployment:
            raise IdentityScopeError("foreign read endpoint")
        current = self.store.current(record)
        if current is None:
            raise IdentityScopeError("identity unavailable")
        self.consumer.verify(record, current[1])
        return current[1]

    def issue_account_identity(self, command: object) -> bytes:
        return self.issue("ACCOUNT", command)

    def issue_portfolio_identity(self, command: object) -> bytes:
        return self.issue("PORTFOLIO", command)

    def transition_account_identity(self, command: object) -> bytes:
        from automated_trading_bot.stage5.identity_scope import (
            exact_fields,
            positive,
            json_object,
        )

        exact_fields(
            command,
            (
                "command_id",
                "record",
                "lifecycle_version",
                "state",
                "valid_from",
                "valid_until",
                "policy_version",
                "nonce",
                "parent",
            ),
        )
        command = json_object(command)
        positive(command["lifecycle_version"], "transition successor version", 2)
        return self.issue("ACCOUNT", command)

    def transition_portfolio_identity(self, command: object) -> bytes:
        from automated_trading_bot.stage5.identity_scope import (
            exact_fields,
            positive,
            json_object,
        )

        exact_fields(
            command,
            (
                "command_id",
                "record",
                "lifecycle_version",
                "state",
                "valid_from",
                "valid_until",
                "policy_version",
                "nonce",
                "parent",
            ),
        )
        command = json_object(command)
        positive(command["lifecycle_version"], "transition successor version", 2)
        return self.issue("PORTFOLIO", command)

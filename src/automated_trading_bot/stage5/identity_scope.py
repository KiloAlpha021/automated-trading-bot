"""Canonical Stage 5 identity and scope contracts.

These values are signed capabilities and applicability evidence.  They do not
themselves grant trading, publication, recovery, or financial authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import unicodedata
from typing import Final, Iterable


SCOPE_KINDS: Final = (
    "GLOBAL", "ACCOUNT", "PORTFOLIO", "STRATEGY", "INSTRUMENT",
    "CURRENCY", "OPERATION",
)
IDENTITY_DOMAIN: Final = b"ATIS-S5-IDENTITY-CAPABILITY-V1\0"
SCOPE_DOMAIN: Final = b"ATIS-S5-CANONICAL-SCOPE-ENVELOPE-V1\0"


class IdentityScopeError(ValueError):
    """Canonical identity or scope evidence is malformed or incomplete."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise IdentityScopeError(f"{name} must be non-empty canonical text")
    if not unicodedata.is_normalized("NFC", value):
        raise IdentityScopeError(f"{name} must be NFC normalized")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise IdentityScopeError(f"{name} contains a prohibited code point")
    return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class IdentityCapabilityV1:
    capability: str
    identity: str
    namespace: str
    deployment_identity: str
    producer_identity: str
    producer_epoch: int
    lifecycle_state: str
    account_identity: str | None = None

    def __post_init__(self) -> None:
        if self.capability not in ("ACCOUNT_IDENTITY_V1", "PORTFOLIO_IDENTITY_V1"):
            raise IdentityScopeError("unsupported identity capability")
        for name in (
            "identity", "namespace", "deployment_identity", "producer_identity",
            "lifecycle_state",
        ):
            _text(getattr(self, name), name)
        if self.producer_epoch < 1 or self.lifecycle_state != "ACTIVE":
            raise IdentityScopeError("identity capability is stale or inactive")
        if self.capability == "PORTFOLIO_IDENTITY_V1":
            _text(self.account_identity, "account_identity")
        elif self.account_identity is not None:
            raise IdentityScopeError("account capability cannot name a parent account")

    def body(self) -> dict[str, object]:
        return {
            "account_identity": self.account_identity,
            "capability": self.capability,
            "deployment_identity": self.deployment_identity,
            "identity": self.identity,
            "lifecycle_state": self.lifecycle_state,
            "namespace": self.namespace,
            "producer_epoch": self.producer_epoch,
            "producer_identity": self.producer_identity,
        }

    def signing_bytes(self) -> bytes:
        return IDENTITY_DOMAIN + canonical_bytes(self.body())


@dataclass(frozen=True, slots=True, order=True)
class ScopeIdentityV1:
    kind: str
    namespace: str
    identity: str

    def __post_init__(self) -> None:
        if self.kind not in SCOPE_KINDS:
            raise IdentityScopeError("unsupported scope kind")
        _text(self.namespace, "namespace")
        _text(self.identity, "identity")

    def wire(self) -> dict[str, str]:
        return {"identity": self.identity, "kind": self.kind, "namespace": self.namespace}

    @property
    def canonical_key(self) -> str:
        return canonical_bytes(self.wire()).decode("ascii")


@dataclass(frozen=True, slots=True)
class CanonicalScopeEnvelopeV1:
    deployment_identity: str
    catalogue_epoch: int
    operation_class: str
    operation_id: str
    operation_core_digest: str
    scopes: tuple[ScopeIdentityV1, ...]

    def __post_init__(self) -> None:
        for name in (
            "deployment_identity", "operation_class", "operation_id",
            "operation_core_digest",
        ):
            _text(getattr(self, name), name)
        if self.catalogue_epoch < 1:
            raise IdentityScopeError("catalogue epoch must be positive")
        canonical = tuple(sorted(set(self.scopes), key=lambda scope: scope.canonical_key))
        if canonical != self.scopes:
            raise IdentityScopeError("scopes must be unique and canonically ordered")
        kinds = {scope.kind for scope in self.scopes}
        if not {"GLOBAL", "ACCOUNT", "PORTFOLIO", "OPERATION"} <= kinds:
            raise IdentityScopeError("mandatory scope dimensions are missing")

    @classmethod
    def create(
        cls, *, deployment_identity: str, catalogue_epoch: int,
        operation_class: str, operation_id: str, operation_core_digest: str,
        scopes: Iterable[ScopeIdentityV1],
    ) -> "CanonicalScopeEnvelopeV1":
        ordered = tuple(sorted(set(scopes), key=lambda scope: scope.canonical_key))
        return cls(
            deployment_identity, catalogue_epoch, operation_class, operation_id,
            operation_core_digest, ordered,
        )

    def body(self) -> dict[str, object]:
        return {
            "catalogue_epoch": self.catalogue_epoch,
            "deployment_identity": self.deployment_identity,
            "operation_class": self.operation_class,
            "operation_core_digest": self.operation_core_digest,
            "operation_id": self.operation_id,
            "scopes": [scope.wire() for scope in self.scopes],
        }

    @property
    def digest(self) -> str:
        return sha256(SCOPE_DOMAIN + canonical_bytes(self.body())).hexdigest()

"""Canonical Stage 5 identity and scope contracts.

These values are signed capabilities and applicability evidence.  They do not
themselves grant trading, publication, recovery, or financial authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import unicodedata
from typing import Any, Final, Iterable, Iterator, TYPE_CHECKING, cast
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
import base64
import re
import struct
from datetime import datetime, timezone

if TYPE_CHECKING:
    from .commit_boundary.operation_envelope import OperationRequest


SCOPE_KINDS: Final = (
    "GLOBAL",
    "ACCOUNT",
    "PORTFOLIO",
    "STRATEGY",
    "INSTRUMENT",
    "CURRENCY",
    "OPERATION",
)
IDENTITY_DOMAIN: Final = b"ATIS-S5-IDENTITY-CAPABILITY-V1\0"
SCOPE_DOMAIN: Final = b"ATIS-S5-CANONICAL-SCOPE-ENVELOPE-V1\0"


class IdentityScopeError(ValueError):
    """Canonical identity or scope evidence is malformed or incomplete."""

    @property
    def code(self) -> str:
        text = str(self)
        known = (
            "INPUT_INVALID",
            "INPUT_LIMIT",
            "AUTHENTICATION_REJECTED",
            "REPLAY_REJECTED",
            "LIFECYCLE_CONFLICT",
            "STATE_UNAVAILABLE",
            "DEADLINE_UNCERTAIN",
        )
        prefix = text.split(":", 1)[0]
        if prefix in known:
            return prefix
        if "replay" in text:
            return "REPLAY_REJECTED"
        if any(
            word in text for word in ("lifecycle", "revoked", "rollback", "predecessor")
        ):
            return "LIFECYCLE_CONFLICT"
        if any(
            word in text
            for word in ("signature", "policy", "foreign", "key", "stale", "parent")
        ):
            return "AUTHENTICATION_REJECTED"
        return "INPUT_INVALID"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise IdentityScopeError(f"{name} must be non-empty canonical text")
    if not unicodedata.is_normalized("NFC", value):
        raise IdentityScopeError(f"{name} must be NFC normalized")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise IdentityScopeError(f"{name} contains a prohibited code point")
    return value


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
            "identity",
            "namespace",
            "deployment_identity",
            "producer_identity",
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
        return {
            "identity": self.identity,
            "kind": self.kind,
            "namespace": self.namespace,
        }

    @property
    def canonical_key(self) -> str:
        return canonical_bytes(self.wire()).decode("utf-8")


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
            "deployment_identity",
            "operation_class",
            "operation_id",
            "operation_core_digest",
        ):
            _text(getattr(self, name), name)
        if self.catalogue_epoch < 1:
            raise IdentityScopeError("catalogue epoch must be positive")
        canonical = tuple(
            sorted(set(self.scopes), key=lambda scope: scope.canonical_key)
        )
        if canonical != self.scopes:
            raise IdentityScopeError("scopes must be unique and canonically ordered")
        kinds = {scope.kind for scope in self.scopes}
        if not {"GLOBAL", "ACCOUNT", "PORTFOLIO", "OPERATION"} <= kinds:
            raise IdentityScopeError("mandatory scope dimensions are missing")

    @classmethod
    def create(
        cls,
        *,
        deployment_identity: str,
        catalogue_epoch: int,
        operation_class: str,
        operation_id: str,
        operation_core_digest: str,
        scopes: Iterable[ScopeIdentityV1],
    ) -> "CanonicalScopeEnvelopeV1":
        ordered = tuple(sorted(set(scopes), key=lambda scope: scope.canonical_key))
        return cls(
            deployment_identity,
            catalogue_epoch,
            operation_class,
            operation_id,
            operation_core_digest,
            ordered,
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

    @classmethod
    def from_signed(cls, raw: bytes) -> AuthenticatedScopeEnvelopeV1:
        """Parse the strict C013 container; consumers must verify authority."""
        return AuthenticatedScopeEnvelopeV1(raw)

    @property
    def digest(self) -> str:
        return sha256(SCOPE_DOMAIN + canonical_bytes(self.body())).hexdigest()


# Contract013 codecs. Earlier three-field helpers remain compatibility values,
# never accepted by the authenticated issuing/consumer interfaces below.
MAX_CANONICAL_BYTES: Final = 1048576
MAX_DEPTH: Final = 16
MAX_COLLECTION: Final = 256
MAX_NODES: Final = 4096
MAX_STRING_BYTES: Final = 65536
REQUEST_SECONDS: Final = 5.0
_request_deadline: ContextVar[float | None] = ContextVar(
    "u2_request_deadline", default=None
)


def request_remaining() -> float:
    deadline = _request_deadline.get()
    return REQUEST_SECONDS if deadline is None else max(0.0, deadline - monotonic())


def check_request_budget() -> None:
    deadline = _request_deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise IdentityScopeError("DEADLINE_UNCERTAIN: identity request budget exceeded")


@contextmanager
def identity_request_budget() -> Iterator[None]:
    token = None
    if _request_deadline.get() is None:
        token = _request_deadline.set(monotonic() + REQUEST_SECONDS)
    try:
        check_request_budget()
        yield
        check_request_budget()
    finally:
        if token is not None:
            _request_deadline.reset(token)


def json_object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise IdentityScopeError("INPUT_INVALID: object required")
    return cast(dict[str, Any], value)


def _canonical_value(value: object, allow_negative: bool = False) -> None:
    # Iterative validation also bounds caller-created cyclic/deep Python values.
    stack: list[tuple[object, int, bool]] = [(value, 0, allow_negative)]
    nodes = 0
    while stack:
        check_request_budget()
        item, depth, negative = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise IdentityScopeError("INPUT_LIMIT: JSON nodes")
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if item < 0 and not negative:
                raise IdentityScopeError("negative canonical integer")
            continue
        if type(item) is str:
            if not unicodedata.is_normalized("NFC", item) or any(
                0xD800 <= ord(c) <= 0xDFFF for c in item
            ):
                raise IdentityScopeError("non-NFC or surrogate text")
            if len(item.encode("utf-8")) > MAX_STRING_BYTES:
                raise IdentityScopeError("INPUT_LIMIT: decoded string")
            continue
        if type(item) in (dict, list, tuple):
            if depth >= MAX_DEPTH:
                raise IdentityScopeError("INPUT_LIMIT: container depth")
            if isinstance(item, dict):
                if len(item) > MAX_COLLECTION:
                    raise IdentityScopeError("INPUT_LIMIT: collection")
                for key, child in item.items():
                    if type(key) is not str:
                        raise IdentityScopeError("non-string object key")
                    stack.append((key, depth + 1, False))
                    stack.append(
                        (
                            child,
                            depth + 1,
                            negative or key == "immutable_business_parameters",
                        )
                    )
            elif isinstance(item, (list, tuple)):
                if len(item) > MAX_COLLECTION:
                    raise IdentityScopeError("INPUT_LIMIT: collection")
                stack.extend((child, depth + 1, negative) for child in item)
            continue
        raise IdentityScopeError("unsupported canonical type; floating point forbidden")


def canonical_bytes(value: object) -> bytes:
    _canonical_value(value)
    chunks: list[bytes] = []
    size = 0
    try:
        for text in json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            check_request_budget()
            chunk = text.encode("utf-8")
            size += len(chunk)
            if size > MAX_CANONICAL_BYTES:
                raise IdentityScopeError("INPUT_LIMIT: canonical bytes")
            chunks.append(chunk)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise IdentityScopeError("INPUT_INVALID: canonical encoding") from exc
    return b"".join(chunks)


class _BoundedJSON:
    """Construct at most the accepted depth/nodes; never recursively load first."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0
        self.nodes = 0
        self.scalar_decoder = json.JSONDecoder()

    def whitespace(self) -> None:
        while self.position < len(self.text) and self.text[self.position] in " \t\r\n":
            self.position += 1

    def string(self) -> str:
        value, end = self.scalar_decoder.raw_decode(self.text, self.position)
        if type(value) is not str:
            raise IdentityScopeError("INPUT_INVALID: string required")
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise IdentityScopeError("INPUT_LIMIT: decoded string")
        self.position = end
        return value

    def node(self) -> None:
        check_request_budget()
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise IdentityScopeError("INPUT_LIMIT: JSON nodes")

    def value(self, depth: int = 0) -> Any:
        self.whitespace()
        self.node()
        if self.position >= len(self.text):
            raise IdentityScopeError("INPUT_INVALID: missing JSON value")
        char = self.text[self.position]
        if char == '"':
            return self.string()
        if char not in "{[":
            if char not in "-0123456789tfn":
                raise IdentityScopeError("INPUT_INVALID: JSON scalar")
            value, end = self.scalar_decoder.raw_decode(self.text, self.position)
            self.position = end
            return value
        if depth >= MAX_DEPTH:
            raise IdentityScopeError("INPUT_LIMIT: container depth")
        is_object = char == "{"
        end_char = "}" if is_object else "]"
        self.position += 1
        self.whitespace()
        result: dict[str, Any] = {}
        array: list[Any] = []
        if self.position < len(self.text) and self.text[self.position] == end_char:
            self.position += 1
            return result if is_object else array
        count = 0
        while True:
            count += 1
            if count > MAX_COLLECTION:
                raise IdentityScopeError("INPUT_LIMIT: collection")
            self.whitespace()
            if is_object:
                if self.position >= len(self.text) or self.text[self.position] != '"':
                    raise IdentityScopeError("INPUT_INVALID: object key")
                self.node()
                key = self.string()
                if key in result:
                    raise IdentityScopeError("duplicate JSON key")
                self.whitespace()
                if self.position >= len(self.text) or self.text[self.position] != ":":
                    raise IdentityScopeError("INPUT_INVALID: object separator")
                self.position += 1
                result[key] = self.value(depth + 1)
            else:
                array.append(self.value(depth + 1))
            self.whitespace()
            if self.position >= len(self.text):
                raise IdentityScopeError("INPUT_INVALID: unterminated container")
            separator = self.text[self.position]
            self.position += 1
            if separator == end_char:
                return result if is_object else array
            if separator != ",":
                raise IdentityScopeError("INPUT_INVALID: container separator")


def parse_canonical(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise IdentityScopeError("canonical bytes required")
    if len(raw) > MAX_CANONICAL_BYTES:
        raise IdentityScopeError("INPUT_LIMIT: canonical bytes")
    try:
        parser = _BoundedJSON(raw.decode("utf-8"))
        value = json_object(parser.value())
        parser.whitespace()
        if parser.position != len(parser.text) or canonical_bytes(value) != raw:
            raise IdentityScopeError("noncanonical JSON bytes")
        return value
    except IdentityScopeError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError, OverflowError) as exc:
        raise IdentityScopeError("INPUT_INVALID: canonical JSON") from exc


def exact_fields(value: object, names: Iterable[str]) -> None:
    if set(json_object(value)) != set(names):
        raise IdentityScopeError("missing or unknown fields")


def positive(value: object, name: str, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise IdentityScopeError("invalid signed-64-bit " + name)
    return value


def hex_digest(value: object) -> str:
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise IdentityScopeError("invalid digest or nonce")
    return value


def timestamp(now: datetime) -> str:
    if now.tzinfo is not timezone.utc:
        raise IdentityScopeError("UTC clock required")
    return now.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    if (
        type(value) is not str
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value
        )
        is None
    ):
        raise IdentityScopeError("noncanonical timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise IdentityScopeError("invalid timestamp") from exc


@dataclass(frozen=True, slots=True)
class IdentityRecordV1:
    schema: str
    dimension: str
    namespace: str
    deployment_id: str
    value: str
    producer_id: str
    producer_epoch: int

    def __post_init__(self) -> None:
        if self.schema != "scope-identity/v1" or self.dimension not in SCOPE_KINDS:
            raise IdentityScopeError("invalid identity schema/dimension")
        for name in ("namespace", "deployment_id", "value", "producer_id"):
            _text(getattr(self, name), name)
        positive(self.producer_epoch, "producer epoch")
        if self.dimension in ("ACCOUNT", "PORTFOLIO"):
            kind = self.dimension.lower()
            if (
                self.namespace != "atis." + kind + "/v1"
                or self.producer_id != "atis." + kind + "-state-authority"
            ):
                raise IdentityScopeError("foreign identity producer/namespace")

    def body(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in self.__dataclass_fields__}

    @property
    def digest(self) -> str:
        return sha256(canonical_bytes(self.body())).hexdigest()

    @classmethod
    def parse(cls, value: object) -> IdentityRecordV1:
        exact_fields(value, IdentityRecordV1.__dataclass_fields__)
        return IdentityRecordV1(**json_object(value))


class AccountIdentityV1(IdentityRecordV1):
    def __init__(self, value: str, deployment_id: str, producer_epoch: int) -> None:
        super().__init__(
            "scope-identity/v1",
            "ACCOUNT",
            "atis.account/v1",
            deployment_id,
            value,
            "atis.account-state-authority",
            producer_epoch,
        )


class PortfolioIdentityV1(IdentityRecordV1):
    def __init__(self, value: str, deployment_id: str, producer_epoch: int) -> None:
        super().__init__(
            "scope-identity/v1",
            "PORTFOLIO",
            "atis.portfolio/v1",
            deployment_id,
            value,
            "atis.portfolio-state-authority",
            producer_epoch,
        )


ASSERTION_FIELDS = (
    "schema",
    "identity_digest",
    "lifecycle_version",
    "state",
    "valid_from",
    "valid_until",
    "policy_version",
    "producer_key_id",
    "producer_key_version",
    "predecessor_digest",
    "nonce",
)


def assertion_signing_bytes(payload: object) -> bytes:
    payload = json_object(payload)
    schema = payload.get("schema")
    if schema not in (
        "account-identity-assertion/v1",
        "portfolio-identity-assertion/v1",
    ):
        raise IdentityScopeError("unsupported assertion schema")
    portfolio = schema.startswith("portfolio")
    exact_fields(
        payload,
        ASSERTION_FIELDS + (("parent_account_identity_digest",) if portfolio else ()),
    )
    hex_digest(payload["identity_digest"])
    hex_digest(payload["nonce"])
    positive(payload["lifecycle_version"], "lifecycle")
    positive(payload["policy_version"], "policy")
    positive(payload["producer_key_version"], "key version")
    _text(payload["producer_key_id"], "key id")
    if payload["state"] not in ("ACTIVE", "SUSPENDED", "REVOKED"):
        raise IdentityScopeError("invalid lifecycle state")
    if parse_timestamp(payload["valid_until"]) <= parse_timestamp(
        payload["valid_from"]
    ):
        raise IdentityScopeError("invalid validity interval")
    if payload["lifecycle_version"] == 1:
        if payload["predecessor_digest"] is not None:
            raise IdentityScopeError("first assertion has predecessor")
    else:
        hex_digest(payload["predecessor_digest"])
    if portfolio:
        hex_digest(payload["parent_account_identity_digest"])
    domain = (
        b"ATIS-S5-PORTFOLIO-IDENTITY-ASSERTION-V1\0"
        if portfolio
        else b"ATIS-S5-ACCOUNT-IDENTITY-ASSERTION-V1\0"
    )
    return domain + canonical_bytes(payload)


def signed_container(payload: dict[str, Any], signature: bytes) -> bytes:
    if type(signature) is not bytes or len(signature) != 64:
        raise IdentityScopeError("P256 signature required")
    return canonical_bytes(
        {
            "algorithm": "ECDSA_P256_SHA256",
            "payload": payload,
            "signature_b64": base64.b64encode(signature).decode("ascii"),
        }
    )


def parse_container(raw: bytes) -> tuple[dict[str, Any], bytes]:
    container = parse_canonical(raw)
    exact_fields(container, ("algorithm", "payload", "signature_b64"))
    if container["algorithm"] != "ECDSA_P256_SHA256":
        raise IdentityScopeError("wrong signature algorithm")
    try:
        signature = base64.b64decode(container["signature_b64"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode() != container["signature_b64"]
        ):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise IdentityScopeError("noncanonical signature") from exc
    return json_object(container["payload"]), signature


def assertion_digest(raw: bytes) -> str:
    payload, _ = parse_container(raw)
    assertion_signing_bytes(payload)
    return sha256(b"ATIS-S5-SIGNED-ASSERTION-V1\0" + raw).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationCoreV1:
    raw: bytes

    def __post_init__(self) -> None:
        value = parse_canonical(self.raw)
        exact_fields(
            value,
            (
                "schema",
                "operation_id",
                "request_type",
                "amount_minor",
                "currency",
                "expected_generation",
                "expected_epoch",
                "deployment_id",
                "operation_class",
                "immutable_business_parameters",
                "legacy_scope_hint",
            ),
        )
        if value["schema"] != "operation-core/v1":
            raise IdentityScopeError("invalid core schema")
        for n in (
            "operation_id",
            "request_type",
            "deployment_id",
            "operation_class",
            "legacy_scope_hint",
        ):
            _text(value[n], n)
        for n in ("amount_minor", "expected_generation", "expected_epoch"):
            positive(value[n], n, 0)
        if (
            type(value["currency"]) is not str
            or re.fullmatch("[A-Z]{3}", value["currency"]) is None
        ):
            raise IdentityScopeError("invalid currency")
        if (
            type(value["immutable_business_parameters"]) is not dict
            or "atis_scope_envelope_digest" in value["immutable_business_parameters"]
        ):
            raise IdentityScopeError("reserved envelope key")
        from .commit_boundary.operation_envelope import _canonical

        _canonical(
            value["immutable_business_parameters"], "immutable_business_parameters"
        )

    @classmethod
    def from_request(
        cls, request: OperationRequest, deployment_id: str, operation_class: str
    ) -> OperationCoreV1:
        from .commit_boundary.operation_envelope import _plain

        parameters = _plain(request.immutable_parameters)
        return cls(
            canonical_bytes(
                {
                    "schema": "operation-core/v1",
                    "operation_id": request.operation_id,
                    "request_type": request.request_type,
                    "amount_minor": request.amount_minor,
                    "currency": request.currency,
                    "expected_generation": request.expected_generation,
                    "expected_epoch": request.expected_epoch,
                    "deployment_id": deployment_id,
                    "operation_class": operation_class,
                    "immutable_business_parameters": parameters,
                    "legacy_scope_hint": request.scope,
                }
            )
        )

    @property
    def digest(self) -> str:
        return sha256(b"ATIS-S5-OPERATION-CORE-V1\0" + self.raw).hexdigest()


def digest_list(values: Iterable[str]) -> str:
    values = tuple(values)
    if len(set(values)) != len(values):
        raise IdentityScopeError("duplicate identity digest")
    framed = struct.pack(">I", len(values))
    for value in values:
        framed += struct.pack(">I", 32) + bytes.fromhex(hex_digest(value))
    return sha256(framed).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthenticatedScopeEnvelopeV1:
    """Strict signed wire representation; parsing is NOT authority validation."""

    raw: bytes

    def __post_init__(self) -> None:
        p, _ = parse_container(self.raw)
        exact_fields(
            p,
            (
                "schema",
                "operation_id",
                "core_digest",
                "deployment_id",
                "operation_class",
                "catalogue_epoch",
                "leaf_identities",
                "identity_assertions",
                "leaf_set_digest",
                "closure_digest",
                "issued_at",
                "expires_at",
                "nonce",
                "signer_id",
                "signer_key_id",
                "signer_key_version",
                "policy_version",
            ),
        )
        if (
            p["schema"] != "canonical-scope-envelope/v1"
            or p["signer_id"] != "atis.scope-envelope-verifier"
        ):
            raise IdentityScopeError("foreign scope producer")
        for n in ("operation_id", "deployment_id", "operation_class", "signer_key_id"):
            _text(p[n], n)
        for n in ("core_digest", "leaf_set_digest", "closure_digest", "nonce"):
            hex_digest(p[n])
        for n in ("catalogue_epoch", "signer_key_version", "policy_version"):
            positive(p[n], n)
        if parse_timestamp(p["expires_at"]) <= parse_timestamp(p["issued_at"]):
            raise IdentityScopeError("invalid envelope interval")
        if (
            type(p["leaf_identities"]) is not list
            or type(p["identity_assertions"]) is not list
            or len(p["leaf_identities"]) != len(p["identity_assertions"])
        ):
            raise IdentityScopeError("assertion/leaf mismatch")
        records = [IdentityRecordV1.parse(v) for v in p["leaf_identities"]]
        order = [(SCOPE_KINDS.index(v.dimension), v.digest) for v in records]
        if order != sorted(order) or len(set(order)) != len(order):
            raise IdentityScopeError("scope order/duplicate")
        if not {"GLOBAL", "ACCOUNT", "PORTFOLIO", "OPERATION"} <= set(
            v.dimension for v in records
        ):
            raise IdentityScopeError("mandatory scope missing")
        if (
            sum(v.dimension == "ACCOUNT" for v in records) != 1
            or sum(v.dimension == "PORTFOLIO" for v in records) != 1
        ):
            raise IdentityScopeError("ambiguous account/portfolio")
        if any(v.deployment_id != p["deployment_id"] for v in records):
            raise IdentityScopeError("foreign leaf deployment")
        if digest_list(v.digest for v in records) != p["leaf_set_digest"]:
            raise IdentityScopeError("leaf digest substitution")

    @property
    def digest(self) -> str:
        return sha256(b"ATIS-S5-SIGNED-SCOPE-ENVELOPE-V1\0" + self.raw).hexdigest()

    @property
    def signing_bytes(self) -> bytes:
        return b"ATIS-S5-CANONICAL-SCOPE-ENVELOPE-V1\0" + canonical_bytes(
            parse_container(self.raw)[0]
        )

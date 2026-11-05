"""Canonical identities for bounded Stage 5 commit-boundary operations.

These values identify requests; they do not issue authority, reserve capital,
or make risk, operations, security, broker, or trading decisions.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Mapping, TypeAlias
import unicodedata


CanonicalScalar: TypeAlias = str | int | bool | None
CanonicalValue: TypeAlias = CanonicalScalar | tuple["CanonicalValue", ...] | Mapping[str, "CanonicalValue"]

_DOMAIN = b"ATIS-STAGE5-SERIALIZABLE-OPERATION-V1\0"


class OperationContractError(ValueError):
    """Raised when operation identity or canonical content is invalid."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise OperationContractError(f"{name} must be non-empty canonical text")
    if not unicodedata.is_normalized("NFC", value):
        raise OperationContractError(f"{name} must use NFC-normalized Unicode")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise OperationContractError(f"{name} contains a prohibited Unicode code point")
    return value


def _canonical(value: object, path: str) -> CanonicalValue:
    if value is None:
        return None
    if type(value) is str:
        return _text(value, path)
    if type(value) is bool:
        return bool(value)
    if type(value) is int:
        return int(value)
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(item, f"{path}[]") for item in value)
    if isinstance(value, dict):
        items: list[tuple[str, object]] = []
        for key, item in value.items():
            canonical_key = _text(key, f"{path}.key")
            items.append((canonical_key, item))
        normalized: dict[str, CanonicalValue] = {}
        for key, item in sorted(items):
            normalized[key] = _canonical(item, f"{path}.{key}")
        return MappingProxyType(normalized)
    raise OperationContractError(f"{path} contains a noncanonical value")


def _plain(value: CanonicalValue) -> object:
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _plain(value[key]) for key in sorted(value)}
    return value


@dataclass(frozen=True, slots=True)
class OperationRequest:
    operation_id: str
    request_type: str
    scope: str
    amount_minor: int | None
    currency: str | None
    expected_generation: int
    expected_epoch: int
    immutable_parameters: Mapping[str, CanonicalValue]
    digest: str

    def __post_init__(self) -> None:
        _text(self.operation_id, "operation_id")
        _text(self.request_type, "request_type")
        _text(self.scope, "scope")
        if type(self.expected_generation) is not int or self.expected_generation < 0:
            raise OperationContractError("expected_generation must be a non-negative int")
        if type(self.expected_epoch) is not int or self.expected_epoch < 0:
            raise OperationContractError("expected_epoch must be a non-negative int")
        if self.amount_minor is not None and (type(self.amount_minor) is not int or self.amount_minor < 0):
            raise OperationContractError("amount_minor must be a non-negative int or None")
        if (self.amount_minor is None) != (self.currency is None):
            raise OperationContractError("amount and currency must be supplied together")
        if self.currency is not None:
            _text(self.currency, "currency")
            if self.currency != self.currency.upper() or len(self.currency) != 3:
                raise OperationContractError("currency must be a three-letter uppercase code")
        canonical = _canonical(dict(self.immutable_parameters), "immutable_parameters")
        if not isinstance(canonical, Mapping):
            raise AssertionError("mapping normalization failed")
        object.__setattr__(self, "immutable_parameters", canonical)
        expected = operation_digest(self._body())
        if self.digest != expected:
            raise OperationContractError("operation identity/content digest mismatch")

    def _body(self) -> dict[str, object]:
        return {
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "expected_epoch": self.expected_epoch,
            "expected_generation": self.expected_generation,
            "immutable_parameters": _plain(self.immutable_parameters),
            "operation_id": self.operation_id,
            "request_type": self.request_type,
            "scope": self.scope,
        }

    @classmethod
    def create(
        cls, *, operation_id: str, request_type: str, scope: str,
        expected_generation: int, expected_epoch: int,
        amount_minor: int | None = None, currency: str | None = None,
        immutable_parameters: Mapping[str, object] | None = None,
    ) -> "OperationRequest":
        canonical_parameters = _canonical(
            dict(immutable_parameters or {}), "immutable_parameters"
        )
        if not isinstance(canonical_parameters, Mapping):
            raise AssertionError("mapping normalization failed")
        body = {
            "amount_minor": amount_minor,
            "currency": currency,
            "expected_epoch": expected_epoch,
            "expected_generation": expected_generation,
            "immutable_parameters": _plain(canonical_parameters),
            "operation_id": operation_id,
            "request_type": request_type,
            "scope": scope,
        }
        return cls(
            operation_id=operation_id,
            request_type=request_type,
            scope=scope,
            amount_minor=amount_minor,
            currency=currency,
            expected_generation=expected_generation,
            expected_epoch=expected_epoch,
            immutable_parameters=canonical_parameters,
            digest=operation_digest(body),
        )

    def bind_scope(
        self, core: object, signed_envelope: bytes, verifier: object
    ) -> "OperationRequest":
        """C013 two-stage binding; this method never grants financial authority."""
        from typing import Any, cast

        from automated_trading_bot.stage5.identity_scope import (
            IdentityScopeError,
            OperationCoreV1,
            parse_canonical,
        )

        if verifier is None or "atis_scope_envelope_digest" in self.immutable_parameters:
            raise IdentityScopeError(
                "qualified scope verifier required; reserved key forbidden"
            )
        operation_core = cast(Any, core)
        body = parse_canonical(operation_core.raw)
        if (
            OperationCoreV1.from_request(
                self, body["deployment_id"], body["operation_class"]
            )
            != core
        ):
            raise IdentityScopeError("operation core mismatch")
        scope_verifier = cast(Any, verifier)
        envelope = scope_verifier.verify_scope(operation_core, signed_envelope)
        parameters = _plain(self.immutable_parameters)
        if not isinstance(parameters, dict):
            raise IdentityScopeError("immutable parameters mapping required")
        parameters["atis_scope_envelope_digest"] = envelope.digest
        return OperationRequest.create(
            operation_id=self.operation_id,
            request_type=self.request_type,
            scope=self.scope,
            amount_minor=self.amount_minor,
            currency=self.currency,
            expected_generation=self.expected_generation,
            expected_epoch=self.expected_epoch,
            immutable_parameters=parameters,
        )


def operation_digest(body: Mapping[str, object]) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return sha256(_DOMAIN + encoded).hexdigest()

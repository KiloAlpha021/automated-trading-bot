"""Inert versioned command contracts without execution authority."""

import base64 as _base64
import binascii as _binascii
from dataclasses import dataclass as _dataclass
import json as _json
from typing import Any as _Any
from uuid import UUID as _UUID

from automated_trading_bot.domain.identifiers import (
    CausationId as _CausationId,
    CorrelationId as _CorrelationId,
    IdempotencyKey as _IdempotencyKey,
)
from automated_trading_bot.domain.versioning import (
    Codec as _Codec,
    ContractVersion as _ContractVersion,
    MalformedRepresentationError as _MalformedRepresentationError,
    VersionDefinition as _VersionDefinition,
)

__all__ = (
    "CommandId",
    "VersionedCommandEnvelope",
    "COMMAND_ENVELOPE_V1",
    "COMMAND_ENVELOPE_V1_DEFINITION",
)

_ROOT_KEYS = frozenset(
    (
        "causation_id",
        "command_id",
        "contract_version",
        "correlation_id",
        "idempotency_key",
        "payload",
    )
)
_VERSION_KEYS = frozenset(("family", "version"))


@_dataclass(frozen=True, slots=True)
class CommandId:
    value: _UUID

    def __post_init__(self) -> None:
        if type(self.value) is not _UUID:
            raise TypeError("value must be a UUID")

    def to_string(self) -> str:
        return str(self.value)


@_dataclass(frozen=True, slots=True)
class VersionedCommandEnvelope:
    command_id: CommandId
    correlation_id: _CorrelationId
    causation_id: _CausationId
    idempotency_key: _IdempotencyKey
    payload: bytes
    contract_version: _ContractVersion

    def __post_init__(self) -> None:
        if type(self.command_id) is not CommandId:
            raise TypeError("command_id must be a CommandId")
        if not isinstance(self.correlation_id, _CorrelationId):
            raise TypeError("correlation_id must be a CorrelationId")
        if not isinstance(self.causation_id, _CausationId):
            raise TypeError("causation_id must be a CausationId")
        if not isinstance(self.idempotency_key, _IdempotencyKey):
            raise TypeError("idempotency_key must be an IdempotencyKey")
        if type(self.payload) is not bytes:
            raise TypeError("payload must be bytes")
        if type(self.contract_version) is not _ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")


COMMAND_ENVELOPE_V1 = _ContractVersion(family="command-envelope", version=1)


def _reject_constant(value: str) -> _Any:
    raise ValueError(f"nonstandard JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, _Any]]) -> dict[str, _Any]:
    result: dict[str, _Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_object(representation: bytes) -> dict[str, _Any]:
    if type(representation) is not bytes:
        raise _MalformedRepresentationError("representation must be bytes")
    try:
        value = _json.loads(
            representation.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, _json.JSONDecodeError, TypeError, ValueError) as error:
        raise _MalformedRepresentationError("invalid command JSON") from error
    if type(value) is not dict:
        raise _MalformedRepresentationError("command representation must be an object")
    return value


def _require_keys(value: object, expected: frozenset[str], name: str) -> dict[str, _Any]:
    if type(value) is not dict or frozenset(value) != expected:
        raise _MalformedRepresentationError(f"invalid {name} fields")
    return value


def _canonical_payload(token: object) -> bytes:
    if type(token) is not str:
        raise _MalformedRepresentationError("payload must be a Base64 string")
    try:
        payload = _base64.b64decode(token, validate=True)
    except (_binascii.Error, ValueError) as error:
        raise _MalformedRepresentationError("invalid Base64 payload") from error
    if _base64.b64encode(payload).decode("ascii") != token:
        raise _MalformedRepresentationError("noncanonical Base64 payload")
    return payload


def _encode_command_envelope_v1(value: object) -> bytes:
    if type(value) is not VersionedCommandEnvelope:
        raise TypeError("value must be a VersionedCommandEnvelope")
    if value.contract_version != COMMAND_ENVELOPE_V1:
        raise ValueError("command codec requires command-envelope version 1")
    representation = {
        "causation_id": str(value.causation_id.value),
        "command_id": value.command_id.to_string(),
        "contract_version": {
            "family": value.contract_version.family,
            "version": value.contract_version.version,
        },
        "correlation_id": str(value.correlation_id.value),
        "idempotency_key": value.idempotency_key.value,
        "payload": _base64.b64encode(value.payload).decode("ascii"),
    }
    return _json.dumps(
        representation,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _decode_command_envelope_v1(representation: bytes) -> object:
    root = _require_keys(_load_object(representation), _ROOT_KEYS, "command")
    version = _require_keys(root["contract_version"], _VERSION_KEYS, "contract version")

    if type(version["family"]) is not str or type(version["version"]) is not int:
        raise _MalformedRepresentationError("invalid command contract version types")
    for name in ("causation_id", "command_id", "correlation_id", "idempotency_key"):
        if type(root[name]) is not str:
            raise _MalformedRepresentationError(f"invalid command {name} type")
    try:
        identity = _ContractVersion(version["family"], version["version"])
    except (TypeError, ValueError) as error:
        raise _MalformedRepresentationError("invalid command contract version") from error
    if identity != COMMAND_ENVELOPE_V1:
        raise _MalformedRepresentationError("wrong command contract version")

    payload = _canonical_payload(root["payload"])
    try:
        value = VersionedCommandEnvelope(
            command_id=CommandId(_UUID(root["command_id"])),
            correlation_id=_CorrelationId(_UUID(root["correlation_id"])),
            causation_id=_CausationId(_UUID(root["causation_id"])),
            idempotency_key=_IdempotencyKey(root["idempotency_key"]),
            payload=payload,
            contract_version=identity,
        )
    except (TypeError, ValueError) as error:
        raise _MalformedRepresentationError("invalid command value") from error
    if _encode_command_envelope_v1(value) != representation:
        raise _MalformedRepresentationError("command representation is not canonical")
    return value


COMMAND_ENVELOPE_V1_DEFINITION = _VersionDefinition(
    identity=COMMAND_ENVELOPE_V1,
    codec=_Codec(
        encode=_encode_command_envelope_v1,
        decode=_decode_command_envelope_v1,
    ),
)

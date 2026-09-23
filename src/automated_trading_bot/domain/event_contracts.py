"""Versioned event successor contracts without persistence or authority."""

import base64 as _base64
import binascii as _binascii
from dataclasses import dataclass as _dataclass
import json as _json
from typing import Any as _Any

from automated_trading_bot.domain.event import EventEnvelope as _EventEnvelope
from automated_trading_bot.domain.versioning import (
    Codec as _Codec,
    ContractVersion as _ContractVersion,
    MalformedRepresentationError as _MalformedRepresentationError,
    VersionDefinition as _VersionDefinition,
)

__all__ = (
    "VersionedEventEnvelope",
    "EVENT_ENVELOPE_V1",
    "EVENT_ENVELOPE_V1_DEFINITION",
)

_ROOT_KEYS = frozenset(("contract_version", "envelope", "payload"))
_VERSION_KEYS = frozenset(("family", "version"))
_ENVELOPE_KEYS = frozenset(
    (
        "event_id",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "occurred_at",
        "schema_version",
    )
)


@_dataclass(frozen=True, slots=True)
class VersionedEventEnvelope:
    envelope: _EventEnvelope
    payload: bytes
    contract_version: _ContractVersion

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, _EventEnvelope):
            raise TypeError("envelope must be an EventEnvelope")
        if type(self.payload) is not bytes:
            raise TypeError("payload must be bytes")
        if type(self.contract_version) is not _ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")


EVENT_ENVELOPE_V1 = _ContractVersion(family="event-envelope", version=1)


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
        raise _MalformedRepresentationError("invalid event JSON") from error
    if type(value) is not dict:
        raise _MalformedRepresentationError("event representation must be an object")
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


def _event_mapping(value: VersionedEventEnvelope) -> dict[str, object]:
    envelope = value.envelope.to_dict()
    if frozenset(envelope) != _ENVELOPE_KEYS:
        raise TypeError("EventEnvelope serialization fields changed")
    for name in (
        "event_id",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "occurred_at",
    ):
        if type(envelope[name]) is not str:
            raise TypeError(f"EventEnvelope {name} must serialize as str")
    if type(envelope["schema_version"]) is not int:
        raise TypeError("EventEnvelope schema_version must serialize as int")
    return {
        "contract_version": {
            "family": value.contract_version.family,
            "version": value.contract_version.version,
        },
        "envelope": envelope,
        "payload": _base64.b64encode(value.payload).decode("ascii"),
    }


def _encode_event_envelope_v1(value: object) -> bytes:
    if type(value) is not VersionedEventEnvelope:
        raise TypeError("value must be a VersionedEventEnvelope")
    if value.contract_version != EVENT_ENVELOPE_V1:
        raise ValueError("event codec requires event-envelope version 1")
    return _json.dumps(
        _event_mapping(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _decode_event_envelope_v1(representation: bytes) -> object:
    root = _require_keys(_load_object(representation), _ROOT_KEYS, "event")
    version = _require_keys(root["contract_version"], _VERSION_KEYS, "contract version")
    envelope = _require_keys(root["envelope"], _ENVELOPE_KEYS, "EventEnvelope")

    if type(version["family"]) is not str or type(version["version"]) is not int:
        raise _MalformedRepresentationError("invalid event contract version types")
    try:
        identity = _ContractVersion(version["family"], version["version"])
    except (TypeError, ValueError) as error:
        raise _MalformedRepresentationError("invalid event contract version") from error
    if identity != EVENT_ENVELOPE_V1:
        raise _MalformedRepresentationError("wrong event contract version")

    for name in (
        "event_id",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "occurred_at",
    ):
        if type(envelope[name]) is not str:
            raise _MalformedRepresentationError(f"invalid EventEnvelope {name} type")
    if type(envelope["schema_version"]) is not int:
        raise _MalformedRepresentationError("invalid EventEnvelope schema_version type")

    payload = _canonical_payload(root["payload"])
    try:
        predecessor = _EventEnvelope.from_dict(envelope)
        value = VersionedEventEnvelope(predecessor, payload, identity)
    except (TypeError, ValueError, KeyError) as error:
        raise _MalformedRepresentationError("invalid EventEnvelope value") from error
    if _encode_event_envelope_v1(value) != representation:
        raise _MalformedRepresentationError("event representation is not canonical")
    return value


EVENT_ENVELOPE_V1_DEFINITION = _VersionDefinition(
    identity=EVENT_ENVELOPE_V1,
    codec=_Codec(
        encode=_encode_event_envelope_v1,
        decode=_decode_event_envelope_v1,
    ),
)

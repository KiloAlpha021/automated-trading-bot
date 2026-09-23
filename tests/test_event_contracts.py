import json
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from automated_trading_bot.domain.event import EventEnvelope
from automated_trading_bot.domain.event_contracts import (
    EVENT_ENVELOPE_V1,
    EVENT_ENVELOPE_V1_DEFINITION,
    VersionedEventEnvelope,
)
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.versioning import (
    ContractVersion,
    MalformedRepresentationError,
    UnknownContractError,
    UnsupportedVersionError,
    VersionRegistry,
)


def event() -> EventEnvelope:
    return EventEnvelope(
        EventId(uuid4()),
        CorrelationId(uuid4()),
        CausationId(uuid4()),
        IdempotencyKey("event:test:1"),
        Timestamp(datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
        1,
    )


def value(payload: bytes = b"payload") -> VersionedEventEnvelope:
    return VersionedEventEnvelope(event(), payload, EVENT_ENVELOPE_V1)


def codec():
    return EVENT_ENVELOPE_V1_DEFINITION.codec


def mutate(raw: bytes, path: tuple[str, ...], replacement: object) -> bytes:
    item = json.loads(raw)
    target = item
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def test_contract_shape_and_construction() -> None:
    predecessor = event()
    item = VersionedEventEnvelope(predecessor, b"\x00\xff", ContractVersion("other", 9))
    assert [field.name for field in fields(item)] == [
        "envelope",
        "payload",
        "contract_version",
    ]
    assert item.envelope is predecessor and item.payload == b"\x00\xff"
    assert item.contract_version == ContractVersion("other", 9)
    assert EVENT_ENVELOPE_V1 == ContractVersion("event-envelope", 1)


@pytest.mark.parametrize(
    "args",
    [
        (object(), b"", EVENT_ENVELOPE_V1),
        (event(), bytearray(), EVENT_ENVELOPE_V1),
        (event(), b"", "1"),
    ],
)
def test_constructor_rejects_wrong_categories(
    args: tuple[object, object, object],
) -> None:
    with pytest.raises(TypeError):
        VersionedEventEnvelope(*args)  # type: ignore[arg-type]


def test_immutable_slotted_and_has_no_authority_api() -> None:
    item = value()
    with pytest.raises(FrozenInstanceError):
        item.payload = b"changed"  # type: ignore[misc]
    assert not hasattr(item, "__dict__")
    for name in (
        "execute",
        "send",
        "place",
        "submit",
        "approve",
        "authorize",
        "persist",
    ):
        assert not hasattr(item, name)


@pytest.mark.parametrize("payload", [b"", b"\x00", bytes(range(256)), b"x" * 4096])
def test_codec_is_deterministic_and_round_trips_binary(payload: bytes) -> None:
    item = value(payload)
    encoded = codec().encode(item)
    assert encoded == codec().encode(item)
    assert codec().decode(encoded) == item
    assert codec().encode(codec().decode(encoded)) == encoded


def test_canonical_structure_and_m1_composition() -> None:
    item = value(b"a")
    encoded = codec().encode(item)
    parsed = json.loads(encoded)
    assert parsed == {
        "contract_version": {"family": "event-envelope", "version": 1},
        "envelope": item.envelope.to_dict(),
        "payload": "YQ==",
    }
    assert (
        encoded
        == json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    )


def test_registry_exact_selection_and_unknown_rejection() -> None:
    registry = VersionRegistry((EVENT_ENVELOPE_V1_DEFINITION,))
    item = value()
    assert (
        registry.interpret(
            EVENT_ENVELOPE_V1, codec().encode(item), target=EVENT_ENVELOPE_V1
        ).value
        == item
    )
    with pytest.raises(UnknownContractError):
        registry.codec_for(ContractVersion("other", 1))
    with pytest.raises(UnsupportedVersionError):
        registry.codec_for(ContractVersion("event-envelope", 2))


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"",
        b"[]",
        b"null",
        b"1",
        b"{} trailing",
        b'{"x":NaN}',
        b'{"x":Infinity}',
    ],
)
def test_malformed_json_rejected(raw: bytes) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(raw)


def test_duplicate_keys_at_each_object_level_rejected() -> None:
    good = codec().encode(value()).decode()
    duplicates = [
        good.replace('{"contract_version":', '{"payload":"","contract_version":', 1),
        good.replace('{"family":', '{"family":"event-envelope","family":', 1),
        good.replace('{"causation_id":', '{"event_id":"x","causation_id":', 1),
    ]
    for raw in duplicates:
        with pytest.raises(MalformedRepresentationError):
            codec().decode(raw.encode())


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("contract_version", "version"), True),
        (("contract_version", "version"), 1.0),
        (("contract_version", "family"), 1),
        (("envelope", "schema_version"), True),
        (("envelope", "schema_version"), 1.0),
        (("envelope", "event_id"), 1),
        (("payload",), 1),
    ],
)
def test_wrong_primitive_types_rejected(
    path: tuple[str, ...], replacement: object
) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(mutate(codec().encode(value()), path, replacement))


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("envelope", "event_id"), "bad"),
        (("envelope", "occurred_at"), "bad"),
        (("envelope", "schema_version"), 0),
        (("contract_version", "family"), "other"),
        (("contract_version", "version"), 2),
    ],
)
def test_malformed_typed_values_rejected(
    path: tuple[str, ...], replacement: object
) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(mutate(codec().encode(value()), path, replacement))


@pytest.mark.parametrize("token", ["YQ=", "YQ===", "Y Q==", "YR==", "_w==", "!!!!"])
def test_malformed_or_noncanonical_base64_rejected(token: str) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(mutate(codec().encode(value(b"a")), ("payload",), token))


def test_missing_unexpected_and_noncanonical_json_rejected() -> None:
    raw = codec().encode(value())
    parsed = json.loads(raw)
    missing = dict(parsed)
    missing.pop("payload")
    unexpected = {**parsed, "extra": 1}
    variants = [
        json.dumps(missing, sort_keys=True, separators=(",", ":")).encode(),
        json.dumps(unexpected, sort_keys=True, separators=(",", ":")).encode(),
        json.dumps(parsed).encode(),
        b'{"payload":"cGF5bG9hZA==","envelope":'
        + json.dumps(parsed["envelope"], separators=(",", ":")).encode()
        + b',"contract_version":{"version":1,"family":"event-envelope"}}',
    ]
    for candidate in variants:
        with pytest.raises(MalformedRepresentationError):
            codec().decode(candidate)


def test_alternate_unicode_escape_rejected_by_canonical_reencode() -> None:
    item = value()
    raw = codec().encode(item)
    altered = raw.replace(b"event:test:1", b"event:test:\\u0031")
    assert altered != raw
    with pytest.raises(MalformedRepresentationError):
        codec().decode(altered)


def test_public_surface_is_narrow() -> None:
    import automated_trading_bot.domain.event_contracts as module

    assert module.__all__ == (
        "VersionedEventEnvelope",
        "EVENT_ENVELOPE_V1",
        "EVENT_ENVELOPE_V1_DEFINITION",
    )

import ast
import inspect
import json
from dataclasses import FrozenInstanceError, fields
from decimal import getcontext
from pathlib import Path
from uuid import UUID, uuid1, uuid4

import pytest

from automated_trading_bot.domain.command_contracts import (
    COMMAND_ENVELOPE_V1,
    COMMAND_ENVELOPE_V1_DEFINITION,
    CommandId,
    VersionedCommandEnvelope,
)
from automated_trading_bot.domain.identity_contracts import (
    AggregateId,
    RoundingPolicyId,
)
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
    OrderId,
    TradeId,
)
from automated_trading_bot.domain.versioning import (
    ContractVersion,
    MalformedRepresentationError,
    UnknownContractError,
    UnsupportedVersionError,
    VersionRegistry,
)


def value(payload: bytes = b"payload") -> VersionedCommandEnvelope:
    return VersionedCommandEnvelope(
        CommandId(uuid4()),
        CorrelationId(uuid4()),
        CausationId(uuid4()),
        IdempotencyKey("command:test:1"),
        payload,
        COMMAND_ENVELOPE_V1,
    )


def codec():
    return COMMAND_ENVELOPE_V1_DEFINITION.codec


def mutate(raw: bytes, key: str, replacement: object) -> bytes:
    item = json.loads(raw)
    item[key] = replacement
    return json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


@pytest.mark.parametrize("identifier", [uuid1(), uuid4(), UUID(int=0)])
def test_command_id_preserves_every_valid_uuid_version(identifier: UUID) -> None:
    item = CommandId(identifier)
    assert item.value is identifier and item.to_string() == str(identifier)


@pytest.mark.parametrize("invalid", [str(uuid4()), 1, True, b"x", None, object()])
def test_command_id_rejects_non_uuid(invalid: object) -> None:
    with pytest.raises(TypeError):
        CommandId(invalid)  # type: ignore[arg-type]


def test_command_id_rejects_uuid_subclass_and_proxy() -> None:
    class SubUUID(UUID):
        pass

    class Proxy:
        def __str__(self) -> str:
            return str(uuid4())

    with pytest.raises(TypeError):
        CommandId(SubUUID(int=1))
    with pytest.raises(TypeError):
        CommandId(Proxy())  # type: ignore[arg-type]


def test_command_id_equality_hash_immutability_and_no_ordering() -> None:
    raw = uuid4()
    first = CommandId(raw)
    second = CommandId(raw)
    assert (
        first == second and hash(first) == hash(second) and first != CommandId(uuid4())
    )
    assert (
        first != raw
        and first != EventId(raw)
        and first != OrderId(raw)
        and first != TradeId(raw)
        and first != AggregateId(raw)
    )
    with pytest.raises(FrozenInstanceError):
        first.value = uuid4()  # type: ignore[misc]
    with pytest.raises(TypeError):
        _ = first < second  # type: ignore[operator]
    assert not hasattr(first, "__dict__") and "__str__" not in CommandId.__dict__


def test_envelope_shape_and_exact_field_categories() -> None:
    item = value()
    assert [field.name for field in fields(item)] == [
        "command_id",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "payload",
        "contract_version",
    ]
    assert COMMAND_ENVELOPE_V1 == ContractVersion("command-envelope", 1)
    assert not hasattr(item, "aggregate_id") and not hasattr(item, "rounding_policy_id")


@pytest.mark.parametrize(
    "index,replacement",
    [(0, uuid4()), (1, uuid4()), (2, uuid4()), (3, "key"), (4, bytearray()), (5, "1")],
)
def test_envelope_rejects_wrong_categories(index: int, replacement: object) -> None:
    args = [
        CommandId(uuid4()),
        CorrelationId(uuid4()),
        CausationId(uuid4()),
        IdempotencyKey("key"),
        b"",
        COMMAND_ENVELOPE_V1,
    ]
    args[index] = replacement
    with pytest.raises(TypeError):
        VersionedCommandEnvelope(*args)  # type: ignore[arg-type]


def test_envelope_is_frozen_slotted_and_inert() -> None:
    item = value()
    with pytest.raises(FrozenInstanceError):
        item.payload = b"x"  # type: ignore[misc]
    assert not hasattr(item, "__dict__")
    for name in (
        "execute",
        "send",
        "place",
        "submit",
        "approve",
        "authorize",
        "dispatch",
        "persist",
    ):
        assert not hasattr(item, name)


@pytest.mark.parametrize("payload", [b"", b"\x00", bytes(range(256)), b"z" * 4096])
def test_command_codec_deterministic_round_trip(payload: bytes) -> None:
    item = value(payload)
    raw = codec().encode(item)
    assert raw == codec().encode(item)
    assert codec().decode(raw) == item
    assert codec().encode(codec().decode(raw)) == raw


def test_command_canonical_structure() -> None:
    item = value(b"a")
    parsed = json.loads(codec().encode(item))
    assert parsed == {
        "causation_id": str(item.causation_id.value),
        "command_id": item.command_id.to_string(),
        "contract_version": {"family": "command-envelope", "version": 1},
        "correlation_id": str(item.correlation_id.value),
        "idempotency_key": item.idempotency_key.value,
        "payload": "YQ==",
    }


def test_registry_selection_and_unknown_rejection() -> None:
    registry = VersionRegistry((COMMAND_ENVELOPE_V1_DEFINITION,))
    item = value()
    assert (
        registry.interpret(
            COMMAND_ENVELOPE_V1, codec().encode(item), target=COMMAND_ENVELOPE_V1
        ).value
        == item
    )
    with pytest.raises(UnknownContractError):
        registry.codec_for(ContractVersion("other", 1))
    with pytest.raises(UnsupportedVersionError):
        registry.codec_for(ContractVersion("command-envelope", 2))


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"",
        b"[]",
        b"null",
        b"{}x",
        b'{"x":NaN}',
        b'{"x":-Infinity}',
    ],
)
def test_malformed_command_json_rejected(raw: bytes) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(raw)


def test_duplicate_keys_all_levels_rejected() -> None:
    good = codec().encode(value()).decode()
    for raw in (
        good.replace('{"causation_id":', '{"payload":"","causation_id":', 1),
        good.replace('{"family":', '{"family":"command-envelope","family":', 1),
    ):
        with pytest.raises(MalformedRepresentationError):
            codec().decode(raw.encode())


@pytest.mark.parametrize(
    "key,replacement",
    [
        ("command_id", 1),
        ("correlation_id", True),
        ("causation_id", 1.0),
        ("idempotency_key", None),
        ("payload", 1),
        ("command_id", "bad"),
        ("payload", "YQ="),
        ("payload", "YQ==="),
        ("payload", "YR=="),
        ("payload", "_w=="),
        ("payload", "Y Q=="),
    ],
)
def test_invalid_command_values_rejected(key: str, replacement: object) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(mutate(codec().encode(value(b"a")), key, replacement))


@pytest.mark.parametrize(
    "version",
    [
        {"family": "command-envelope", "version": True},
        {"family": "command-envelope", "version": 1.0},
        {"family": "other", "version": 1},
        {"family": "command-envelope", "version": 2},
    ],
)
def test_invalid_command_versions_rejected(version: object) -> None:
    with pytest.raises(MalformedRepresentationError):
        codec().decode(mutate(codec().encode(value()), "contract_version", version))


def test_missing_unexpected_whitespace_reordered_and_escape_variants_rejected() -> None:
    raw = codec().encode(value())
    parsed = json.loads(raw)
    missing = dict(parsed)
    missing.pop("payload")
    candidates = [
        json.dumps(missing, sort_keys=True, separators=(",", ":")).encode(),
        json.dumps(
            {**parsed, "extra": 1}, sort_keys=True, separators=(",", ":")
        ).encode(),
        json.dumps(parsed).encode(),
        raw.replace(b"command:test:1", b"command:test:\\u0031"),
        b'{"payload":"cGF5bG9hZA==","idempotency_key":"command:test:1","correlation_id":"'
        + parsed["correlation_id"].encode()
        + b'","contract_version":{"version":1,"family":"command-envelope"},"command_id":"'
        + parsed["command_id"].encode()
        + b'","causation_id":"'
        + parsed["causation_id"].encode()
        + b'"}',
    ]
    for candidate in candidates:
        with pytest.raises(MalformedRepresentationError):
            codec().decode(candidate)


def test_identity_categories_do_not_collapse() -> None:
    raw = uuid4()
    ids = [
        CommandId(raw),
        EventId(raw),
        AggregateId(raw),
        CorrelationId(raw),
        CausationId(raw),
    ]
    assert all(
        left != right for index, left in enumerate(ids) for right in ids[index + 1 :]
    )
    assert CommandId(raw) != IdempotencyKey(str(raw))
    assert CommandId(raw) != ContractVersion("command-envelope", 1)
    assert RoundingPolicyId("command-envelope:1") != ContractVersion(
        "command-envelope", 1
    )


def test_hostile_callable_payload_is_rejected_without_invocation() -> None:
    called = False

    class Hostile:
        def __call__(self) -> bytes:
            nonlocal called
            called = True
            return b"bad"

    with pytest.raises(TypeError):
        VersionedCommandEnvelope(
            CommandId(uuid4()),
            CorrelationId(uuid4()),
            CausationId(uuid4()),
            IdempotencyKey("key"),
            Hostile(),
            COMMAND_ENVELOPE_V1,
        )  # type: ignore[arg-type]
    assert called is False


def test_codec_independent_of_decimal_context() -> None:
    item = value()
    before = codec().encode(item)
    context = getcontext()
    original = context.prec
    try:
        context.prec = 3
        assert codec().encode(item) == before
    finally:
        context.prec = original


def test_module_ast_has_no_infrastructure_or_dynamic_execution() -> None:
    source = inspect.getsource(inspect.getmodule(CommandId))
    tree = ast.parse(source)
    forbidden_imports = {
        "subprocess",
        "socket",
        "pathlib",
        "sqlite3",
        "requests",
        "decimal",
        "importlib",
    }
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    } | {
        str(node.module).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imports & forbidden_imports
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"eval", "exec", "open", "__import__"}
        for node in ast.walk(tree)
    )
    assert Path(inspect.getfile(CommandId)).name == "command_contracts.py"


def test_public_surface_is_narrow() -> None:
    import automated_trading_bot.domain.command_contracts as module

    assert module.__all__ == (
        "CommandId",
        "VersionedCommandEnvelope",
        "COMMAND_ENVELOPE_V1",
        "COMMAND_ENVELOPE_V1_DEFINITION",
    )

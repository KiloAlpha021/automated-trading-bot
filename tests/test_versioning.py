"""S2.1 properties; example codecs/upcasters are test fixtures, not S2.3 contracts."""

import json
from dataclasses import FrozenInstanceError

import pytest

from automated_trading_bot.domain.versioning import (
    Codec,
    CodecFailureError,
    Compatibility,
    ContractVersion,
    InvalidRegistryError,
    InvalidVersionIdentityError,
    MalformedRepresentationError,
    MissingUpcastPathError,
    UnknownContractError,
    UnsupportedVersionError,
    UpcastDefinition,
    UpcastFailureError,
    VersionDefinition,
    VersionRegistry,
)

FIRST = ContractVersion("example", 1)
SECOND = ContractVersion("example", 2)
THIRD = ContractVersion("example", 3)


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(value: bytes) -> object:
    try:
        return json.loads(value.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise MalformedRepresentationError("invalid example representation") from error


CODEC = Codec(encode, decode)


def advance(value: object) -> object:
    assert isinstance(value, dict)
    return {**value, "upcast": True}


def registry(*, with_path: bool = True) -> VersionRegistry:
    return VersionRegistry(
        (VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC)),
        (UpcastDefinition(FIRST, SECOND, advance),) if with_path else (),
    )


def test_identity_is_typed_immutable_and_distinct_from_m1_ids() -> None:
    assert FIRST != SECOND
    assert FIRST == ContractVersion("example", 1)
    assert hash(FIRST) == hash(ContractVersion("example", 1))
    assert {FIRST: "registered"}[ContractVersion("example", 1)] == "registered"
    with pytest.raises(FrozenInstanceError):
        FIRST.version = 2  # type: ignore[misc]


@pytest.mark.parametrize("family,version", [("", 1), (" example", 1), ("example ", 1), ("example", 0), ("example", True), ("example", "1")])
def test_malformed_identity_rejected(family: str, version: int) -> None:
    with pytest.raises(InvalidVersionIdentityError):
        ContractVersion(family, version)


def test_unhashable_identity_components_rejected_at_construction() -> None:
    class UnhashableFamily(str):
        __hash__ = None  # type: ignore[assignment]

    class UnhashableVersion(int):
        __hash__ = None  # type: ignore[assignment]

    with pytest.raises(InvalidVersionIdentityError):
        ContractVersion(UnhashableFamily("example"), 1)
    with pytest.raises(InvalidVersionIdentityError):
        ContractVersion("example", UnhashableVersion(1))
    assert registry().codec_for(ContractVersion("example", 1)) is CODEC
    assert hash(ContractVersion("example", 1)) == hash(FIRST)


def test_contract_version_subclasses_rejected_before_hash_lookup() -> None:
    class UnhashableIdentity(ContractVersion):
        __hash__ = None  # type: ignore[assignment]

    class OrdinarySubclass(ContractVersion):
        pass

    for kind in (UnhashableIdentity, OrdinarySubclass):
        with pytest.raises(InvalidVersionIdentityError):
            kind("example", 1)

    # A subclass can bypass the inherited constructor check, so consumers
    # revalidate before dictionary membership or lookup.
    class BypassedIdentity(UnhashableIdentity):
        def __post_init__(self) -> None:
            pass

    invalid = BypassedIdentity("example", 1)
    valid = registry()
    for action in (
        lambda: valid.codec_for(invalid),
        lambda: valid.compatibility(invalid, SECOND),
        lambda: valid.interpret(invalid, b"{}", target=FIRST),
        lambda: VersionRegistry((VersionDefinition(invalid, CODEC),)),
        lambda: VersionRegistry((VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC)),
                                (UpcastDefinition(invalid, SECOND, advance),)),
    ):
        with pytest.raises(InvalidVersionIdentityError):
            action()


@pytest.mark.parametrize("definitions,upcasts", [
    (None, ()),
    ((VersionDefinition(FIRST, CODEC),), None),
    (42, ()),
    ((VersionDefinition(FIRST, CODEC),), 42),
])
def test_malformed_top_level_registry_inputs_rejected(
    definitions: object, upcasts: object,
) -> None:
    with pytest.raises(InvalidRegistryError):
        VersionRegistry(definitions, upcasts)  # type: ignore[arg-type]


def test_exact_codec_and_native_compatibility() -> None:
    first = registry()
    second = registry()
    assert first.codec_for(FIRST) is CODEC
    assert second.codec_for(FIRST) is CODEC
    assert first.compatibility(FIRST, FIRST) is Compatibility.SUPPORTED_NATIVE
    raw = encode({"a": 1})
    result = first.interpret(FIRST, raw, target=FIRST)
    assert (result.value, result.original_bytes, result.transformation_path) == ({"a": 1}, raw, ())
    assert first.interpret(FIRST, raw, target=FIRST) == result
    with pytest.raises(FrozenInstanceError):
        first._families = frozenset()  # type: ignore[misc]


def test_historical_forward_interpretation_preserves_bytes_and_path() -> None:
    original = bytearray(encode({"a": 1}))
    before = bytes(original)
    result = registry().interpret(FIRST, bytes(original), target=SECOND)
    assert result.value == {"a": 1, "upcast": True}
    assert (result.original, result.target, result.transformation_path) == (FIRST, SECOND, (SECOND,))
    assert result.original_bytes == before == bytes(original)
    assert registry().interpret(FIRST, before, target=SECOND) == result
    assert registry().compatibility(FIRST, SECOND) is Compatibility.SUPPORTED_VIA_APPROVED_UPCAST


def test_unique_multistep_path_records_each_approved_target() -> None:
    governed = VersionRegistry(
        (VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC), VersionDefinition(THIRD, CODEC)),
        (UpcastDefinition(FIRST, SECOND, advance),
         UpcastDefinition(SECOND, THIRD, lambda value: {**value, "third": True})),
    )
    result = governed.interpret(FIRST, b"{}", target=THIRD)
    assert result.transformation_path == (SECOND, THIRD)
    assert result.value == {"upcast": True, "third": True}
    assert result.original_bytes == b"{}"


@pytest.mark.parametrize("identity,error", [(ContractVersion("other", 1), UnknownContractError), (THIRD, UnsupportedVersionError)])
def test_unknown_family_and_future_version_fail_closed(identity: ContractVersion, error: type[Exception]) -> None:
    with pytest.raises(error):
        registry().codec_for(identity)
    with pytest.raises(error):
        registry().interpret(identity, encode({}), target=SECOND)


@pytest.mark.parametrize("raw,error", [("{}", MalformedRepresentationError), (b'{"b":2,"a":1}', MalformedRepresentationError), (b"{bad", MalformedRepresentationError)])
def test_malformed_or_noncanonical_representation_fails(raw: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        registry().interpret(FIRST, raw, target=FIRST)  # type: ignore[arg-type]


def test_version_order_does_not_imply_compatibility_or_downgrade() -> None:
    no_path = registry(with_path=False)
    assert no_path.compatibility(FIRST, SECOND) is Compatibility.UNSUPPORTED
    assert registry().compatibility(SECOND, FIRST) is Compatibility.UNSUPPORTED
    with pytest.raises(MissingUpcastPathError):
        no_path.interpret(FIRST, encode({}), target=SECOND)
    with pytest.raises(MissingUpcastPathError):
        registry().interpret(SECOND, encode({}), target=FIRST)


@pytest.mark.parametrize("definitions,upcasts", [
    ((VersionDefinition(FIRST, CODEC), VersionDefinition(FIRST, CODEC)), ()),
    ((VersionDefinition(FIRST, CODEC),), (UpcastDefinition(FIRST, SECOND, advance),)),
    ((VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC)), (UpcastDefinition(SECOND, FIRST, advance),)),
    ((VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC), VersionDefinition(THIRD, CODEC)),
     (UpcastDefinition(FIRST, SECOND, advance), UpcastDefinition(FIRST, THIRD, advance))),
])
def test_invalid_or_ambiguous_governed_definitions_rejected(
    definitions: tuple[VersionDefinition, ...], upcasts: tuple[UpcastDefinition, ...],
) -> None:
    with pytest.raises(InvalidRegistryError):
        VersionRegistry(definitions, upcasts)


def test_noncanonical_codec_output_is_rejected() -> None:
    bad = Codec(lambda value: b"wrong", decode)
    governed = VersionRegistry((VersionDefinition(FIRST, bad),))
    with pytest.raises(MalformedRepresentationError):
        governed.interpret(FIRST, b"{}", target=FIRST)


def test_codec_internal_failure_is_distinct_from_malformed_input() -> None:
    for failure in (ValueError("internal decoder defect"), RuntimeError("decoder defect")):
        def broken(_value: bytes) -> object:
            raise failure

        governed = VersionRegistry((VersionDefinition(FIRST, Codec(encode, broken)),))
        with pytest.raises(CodecFailureError) as caught:
            governed.interpret(FIRST, b"{}", target=FIRST)
        assert caught.value.__cause__ is failure


def test_explicit_decoder_rejection_preserves_malformed_category_and_cause() -> None:
    def invalid(_value: bytes) -> object:
        try:
            raise ValueError("invalid representation")
        except ValueError as error:
            raise MalformedRepresentationError("explicit decoder rejection") from error

    governed = VersionRegistry((VersionDefinition(FIRST, Codec(encode, invalid)),))
    with pytest.raises(MalformedRepresentationError) as caught:
        governed.interpret(FIRST, b"{bad", target=FIRST)
    assert isinstance(caught.value.__cause__, ValueError)


def test_upcast_failure_is_distinct_from_codec_and_path_failures() -> None:
    def broken(_value: object) -> object:
        raise RuntimeError("failure")

    governed = VersionRegistry(
        (VersionDefinition(FIRST, CODEC), VersionDefinition(SECOND, CODEC)),
        (UpcastDefinition(FIRST, SECOND, broken),),
    )
    with pytest.raises(UpcastFailureError):
        governed.interpret(FIRST, b"{}", target=SECOND)


def test_m1_event_envelope_keeps_positive_integer_semantics() -> None:
    from automated_trading_bot.domain.event import EventEnvelope, EventRegistry

    assert "schema_version" in EventEnvelope.__dataclass_fields__
    assert EventEnvelope.__module__ == "automated_trading_bot.domain.event"
    assert EventRegistry is not VersionRegistry

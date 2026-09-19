"""Closed Stage-2 contract-version interpretation, separate from M1 events.

Definitions are supplied by governed software configuration at construction time.
Codec functions must be deterministic and canonical; upcasters must be pure. This
module checks structural configuration and representation properties, not the
absence of external side effects inside caller-supplied functions.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Callable, Mapping


class VersioningError(ValueError):
    """A versioned contract could not be interpreted safely."""


class InvalidVersionIdentityError(VersioningError):
    pass


class UnknownContractError(VersioningError):
    pass


class UnsupportedVersionError(VersioningError):
    pass


class MalformedRepresentationError(VersioningError):
    """Explicit decoder rejection of invalid input, or noncanonical bytes."""

    pass


class CodecFailureError(VersioningError):
    pass


class MissingUpcastPathError(VersioningError):
    pass


class UpcastFailureError(VersioningError):
    pass


class InvalidRegistryError(VersioningError):
    pass


@dataclass(frozen=True, slots=True)
class ContractVersion:
    family: str
    version: int

    def __post_init__(self) -> None:
        if type(self) is not ContractVersion:
            raise InvalidVersionIdentityError("identity must be a ContractVersion")
        if type(self.family) is not str or not self.family or self.family != self.family.strip():
            raise InvalidVersionIdentityError("family must be a nonempty, unpadded string")
        if type(self.version) is not int or self.version < 1:
            raise InvalidVersionIdentityError("version must be a positive integer")


def _require_identity(identity: ContractVersion) -> ContractVersion:
    if type(identity) is not ContractVersion:
        raise InvalidVersionIdentityError("identity must be a ContractVersion")
    ContractVersion.__post_init__(identity)
    return identity


@dataclass(frozen=True, slots=True)
class Codec:
    encode: Callable[[object], bytes]
    decode: Callable[[bytes], object]

    def __post_init__(self) -> None:
        if not callable(self.encode) or not callable(self.decode):
            raise InvalidRegistryError("codec functions must be callable")


@dataclass(frozen=True, slots=True)
class VersionDefinition:
    identity: ContractVersion
    codec: Codec


@dataclass(frozen=True, slots=True)
class UpcastDefinition:
    source: ContractVersion
    target: ContractVersion
    transform: Callable[[object], object]


class Compatibility(StrEnum):
    SUPPORTED_NATIVE = "SUPPORTED_NATIVE"
    SUPPORTED_VIA_APPROVED_UPCAST = "SUPPORTED_VIA_APPROVED_UPCAST"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class Interpretation:
    original: ContractVersion
    target: ContractVersion
    original_bytes: bytes
    value: object
    transformation_path: tuple[ContractVersion, ...]


@dataclass(frozen=True, slots=True, init=False)
class VersionRegistry:
    """Immutable, explicitly defined codecs and forward-only upcast routes."""

    _codecs: Mapping[ContractVersion, Codec]
    _routes: Mapping[ContractVersion, UpcastDefinition]
    _families: frozenset[str]

    def __init__(
        self,
        definitions: tuple[VersionDefinition, ...],
        upcasts: tuple[UpcastDefinition, ...] = (),
    ) -> None:
        if type(definitions) is not tuple or type(upcasts) is not tuple:
            raise InvalidRegistryError("registry definitions and upcasts must be tuples")
        codecs: dict[ContractVersion, Codec] = {}
        for definition in definitions:
            if not isinstance(definition, VersionDefinition) or not isinstance(definition.identity, ContractVersion) or not isinstance(definition.codec, Codec):
                raise InvalidRegistryError("invalid version definition")
            _require_identity(definition.identity)
            if definition.identity in codecs:
                raise InvalidRegistryError("duplicate contract/version codec")
            codecs[definition.identity] = definition.codec
        if not codecs:
            raise InvalidRegistryError("registry must contain a version definition")

        routes: dict[ContractVersion, UpcastDefinition] = {}
        for route in upcasts:
            if not isinstance(route, UpcastDefinition) or not isinstance(route.source, ContractVersion) or not isinstance(route.target, ContractVersion) or not callable(route.transform):
                raise InvalidRegistryError("invalid upcast definition")
            _require_identity(route.source)
            _require_identity(route.target)
            if route.source not in codecs or route.target not in codecs:
                raise InvalidRegistryError("upcast endpoint is not registered")
            if route.source.family != route.target.family or route.source.version >= route.target.version:
                raise InvalidRegistryError("upcast must advance within one family")
            if route.source in routes:
                raise InvalidRegistryError("ambiguous upcast path")
            routes[route.source] = route
        object.__setattr__(self, "_codecs", MappingProxyType(codecs))
        object.__setattr__(self, "_routes", MappingProxyType(routes))
        object.__setattr__(self, "_families", frozenset(key.family for key in codecs))

    def codec_for(self, identity: ContractVersion) -> Codec:
        _require_identity(identity)
        if identity.family not in self._families:
            raise UnknownContractError(identity.family)
        try:
            return self._codecs[identity]
        except KeyError as error:
            raise UnsupportedVersionError(str(identity)) from error

    def _path(self, source: ContractVersion, target: ContractVersion) -> tuple[UpcastDefinition, ...] | None:
        path: list[UpcastDefinition] = []
        current = source
        while current != target:
            route = self._routes.get(current)
            if route is None or route.target.version > target.version:
                return None
            path.append(route)
            current = route.target
        return tuple(path)

    def compatibility(self, source: ContractVersion, target: ContractVersion) -> Compatibility:
        self.codec_for(source)
        self.codec_for(target)
        if source == target:
            return Compatibility.SUPPORTED_NATIVE
        if source.family != target.family or source.version > target.version:
            return Compatibility.UNSUPPORTED
        return (Compatibility.SUPPORTED_VIA_APPROVED_UPCAST
                if self._path(source, target) is not None else Compatibility.UNSUPPORTED)

    def interpret(self, original: ContractVersion, representation: bytes, *, target: ContractVersion) -> Interpretation:
        codec = self.codec_for(original)
        self.codec_for(target)
        if not isinstance(representation, bytes):
            raise MalformedRepresentationError("representation must be bytes")
        if original.family != target.family or original.version > target.version:
            raise MissingUpcastPathError("no approved forward interpretation path")
        path = self._path(original, target)
        if path is None:
            raise MissingUpcastPathError("no approved forward interpretation path")
        try:
            historical = codec.decode(representation)
        except MalformedRepresentationError:
            raise
        except Exception as error:
            raise CodecFailureError("exact-version decoder failed") from error
        try:
            canonical = codec.encode(historical)
        except Exception as error:
            raise CodecFailureError("exact-version encoder failed") from error
        if not isinstance(canonical, bytes):
            raise CodecFailureError("codec did not produce bytes")
        if canonical != representation:
            raise MalformedRepresentationError("representation is not canonical")
        value = historical
        for route in path:
            try:
                value = route.transform(value)
            except Exception as error:
                raise UpcastFailureError("approved upcast failed") from error
        return Interpretation(
            original=original,
            target=target,
            original_bytes=representation,
            value=value,
            transformation_path=tuple(route.target for route in path),
        )

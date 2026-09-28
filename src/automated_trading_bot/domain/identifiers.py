from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OrderId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")


@dataclass(frozen=True, slots=True)
class TradeId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")


@dataclass(frozen=True, slots=True)
class EventId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")


@dataclass(frozen=True, slots=True)
class CorrelationId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")


@dataclass(frozen=True, slots=True)
class CausationId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")


@dataclass(frozen=True, slots=True)
class StrategyId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("value must be a str")


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Opaque ATIS canonical instrument identity."""

    value: UUID

    _PREFIX = "atis:instrument:v1:"

    def __post_init__(self) -> None:
        _require_uuid4(self.value)

    @classmethod
    def parse(cls, value: str) -> "InstrumentId":
        return cls(_parse_uuid4(value, cls._PREFIX))

    def to_string(self) -> str:
        return f"{self._PREFIX}{self.value}"


def _require_uuid4(value: object) -> None:
    if type(value) is not UUID:
        raise TypeError("value must be a UUID")
    if value.int == 0 or value.version != 4:
        raise ValueError("value must be a non-nil RFC UUIDv4")


def _parse_uuid4(value: object, prefix: str) -> UUID:
    if type(value) is not str:
        raise TypeError("value must be a str")
    if not value.startswith(prefix):
        raise ValueError("value has the wrong or missing identity namespace")
    token = value[len(prefix):]
    try:
        parsed = UUID(token)
    except (ValueError, AttributeError) as error:
        raise ValueError("value contains an invalid UUID") from error
    _require_uuid4(parsed)
    if value != f"{prefix}{parsed}":
        raise ValueError("value is not in canonical form")
    return parsed


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("value must be a str")

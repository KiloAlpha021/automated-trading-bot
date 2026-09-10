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
class StrategyId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("value must be a str")


@dataclass(frozen=True, slots=True)
class InstrumentId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("value must be a str")

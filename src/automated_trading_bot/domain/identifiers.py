from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OrderId:
    value: UUID


@dataclass(frozen=True, slots=True)
class TradeId:
    value: UUID


@dataclass(frozen=True, slots=True)
class StrategyId:
    value: str


@dataclass(frozen=True, slots=True)
class InstrumentId:
    value: str

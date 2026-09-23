"""Typed persistence ports without infrastructure or execution authority."""

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from typing import Protocol as _Protocol

from automated_trading_bot.domain.state_transitions import (
    TransitionResult as _TransitionResult,
)
from automated_trading_bot.domain.versioning import (
    ContractVersion as _ContractVersion,
)

__all__ = (
    "EventPersistenceRequest",
    "TransitionPersistenceRequest",
    "PersistenceDisposition",
    "PersistenceOutcome",
    "EventPersistencePort",
    "TransitionPersistencePort",
)


@_dataclass(frozen=True, slots=True)
class EventPersistenceRequest:
    contract_version: _ContractVersion
    representation: bytes

    def __post_init__(self) -> None:
        if type(self.contract_version) is not _ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")
        if type(self.representation) is not bytes:
            raise TypeError("representation must be bytes")


@_dataclass(frozen=True, slots=True)
class TransitionPersistenceRequest:
    result: _TransitionResult

    def __post_init__(self) -> None:
        if type(self.result) is not _TransitionResult:
            raise TypeError("result must be a TransitionResult")


class PersistenceDisposition(_StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    CONFLICT = "CONFLICT"


@_dataclass(frozen=True, slots=True)
class PersistenceOutcome:
    disposition: PersistenceDisposition

    def __post_init__(self) -> None:
        if type(self.disposition) is not PersistenceDisposition:
            raise TypeError("disposition must be a PersistenceDisposition")


class EventPersistencePort(_Protocol):
    def persist_event(
        self,
        request: EventPersistenceRequest,
    ) -> PersistenceOutcome:
        ...


class TransitionPersistencePort(_Protocol):
    def persist_transition(
        self,
        request: TransitionPersistenceRequest,
    ) -> PersistenceOutcome:
        ...

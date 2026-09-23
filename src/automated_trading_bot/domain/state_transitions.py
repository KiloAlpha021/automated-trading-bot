"""Pure generic contract-level state transitions without external authority."""

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from uuid import UUID as _UUID

from automated_trading_bot.domain.versioning import (
    ContractVersion as _ContractVersion,
    UnknownContractError as _UnknownContractError,
    UnsupportedVersionError as _UnsupportedVersionError,
)

__all__ = (
    "StateId",
    "TransitionActionId",
    "TransitionState",
    "TransitionActionKind",
    "TransitionDisposition",
    "GenericState",
    "TransitionAction",
    "TransitionResult",
    "TRANSITION_CONTRACT_V1",
    "transition",
)


@_dataclass(frozen=True, slots=True)
class StateId:
    value: _UUID

    def __post_init__(self) -> None:
        if type(self.value) is not _UUID:
            raise TypeError("value must be a UUID")

    def to_string(self) -> str:
        return str(self.value)


@_dataclass(frozen=True, slots=True)
class TransitionActionId:
    value: _UUID

    def __post_init__(self) -> None:
        if type(self.value) is not _UUID:
            raise TypeError("value must be a UUID")

    def to_string(self) -> str:
        return str(self.value)


class TransitionState(_StrEnum):
    ACTIVE = "ACTIVE"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


class TransitionActionKind(_StrEnum):
    APPLY = "APPLY"
    DUPLICATE = "DUPLICATE"


class TransitionDisposition(_StrEnum):
    APPLIED = "APPLIED"
    ILLEGAL = "ILLEGAL"
    DUPLICATE = "DUPLICATE"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


TRANSITION_CONTRACT_V1 = _ContractVersion(family="state-transition", version=1)


@_dataclass(frozen=True, slots=True)
class GenericState:
    state_id: StateId
    status: TransitionState
    contract_version: _ContractVersion

    def __post_init__(self) -> None:
        if type(self.state_id) is not StateId:
            raise TypeError("state_id must be a StateId")
        if type(self.status) is not TransitionState:
            raise TypeError("status must be a TransitionState")
        if type(self.contract_version) is not _ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")


@_dataclass(frozen=True, slots=True)
class TransitionAction:
    action_id: TransitionActionId
    kind: TransitionActionKind
    target_state: TransitionState
    contract_version: _ContractVersion

    def __post_init__(self) -> None:
        if type(self.action_id) is not TransitionActionId:
            raise TypeError("action_id must be a TransitionActionId")
        if type(self.kind) is not TransitionActionKind:
            raise TypeError("kind must be a TransitionActionKind")
        if type(self.target_state) is not TransitionState:
            raise TypeError("target_state must be a TransitionState")
        if type(self.contract_version) is not _ContractVersion:
            raise TypeError("contract_version must be a ContractVersion")


@_dataclass(frozen=True, slots=True)
class TransitionResult:
    previous_state: GenericState
    next_state: GenericState
    disposition: TransitionDisposition

    def __post_init__(self) -> None:
        if type(self.previous_state) is not GenericState:
            raise TypeError("previous_state must be a GenericState")
        if type(self.next_state) is not GenericState:
            raise TypeError("next_state must be a GenericState")
        if type(self.disposition) is not TransitionDisposition:
            raise TypeError("disposition must be a TransitionDisposition")


def _require_transition_version(
    version: _ContractVersion,
    *,
    subject: str,
) -> None:
    if version.family != TRANSITION_CONTRACT_V1.family:
        raise _UnknownContractError(f"unknown {subject} contract family: {version.family}")
    if version.version != TRANSITION_CONTRACT_V1.version:
        raise _UnsupportedVersionError(
            f"unsupported {subject} state-transition version: {version.version}"
        )


def _unchanged_result(
    state: GenericState,
    disposition: TransitionDisposition,
) -> TransitionResult:
    return TransitionResult(
        previous_state=state,
        next_state=state,
        disposition=disposition,
    )


def transition(
    state: GenericState,
    action: TransitionAction,
) -> TransitionResult:
    if type(state) is not GenericState:
        raise TypeError("state must be a GenericState")
    if type(action) is not TransitionAction:
        raise TypeError("action must be a TransitionAction")

    _require_transition_version(state.contract_version, subject="state")
    _require_transition_version(action.contract_version, subject="action")
    if state.contract_version != action.contract_version:
        raise _UnsupportedVersionError("state and action contract versions must match")

    if state.status is TransitionState.UNKNOWN:
        return _unchanged_result(state, TransitionDisposition.UNKNOWN)
    if state.status is TransitionState.TERMINAL:
        return _unchanged_result(state, TransitionDisposition.TERMINAL)
    if action.kind is TransitionActionKind.DUPLICATE:
        return _unchanged_result(state, TransitionDisposition.DUPLICATE)
    if action.target_state is TransitionState.TERMINAL:
        next_state = GenericState(
            state_id=state.state_id,
            status=TransitionState.TERMINAL,
            contract_version=TRANSITION_CONTRACT_V1,
        )
        return TransitionResult(
            previous_state=state,
            next_state=next_state,
            disposition=TransitionDisposition.APPLIED,
        )
    return _unchanged_result(state, TransitionDisposition.ILLEGAL)

"""Optimistic-concurrency evidence contracts without persistence behavior."""

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum

from automated_trading_bot.domain.persistence import (
    PersistenceDisposition as _PersistenceDisposition,
    PersistenceOutcome as _PersistenceOutcome,
    TransitionPersistenceRequest as _TransitionPersistenceRequest,
)
from automated_trading_bot.domain.state_transitions import StateId as _StateId

__all__ = (
    "ConcurrencyToken",
    "INITIAL_CONCURRENCY_TOKEN",
    "CommitCertainty",
    "RetryDisposition",
    "ConcurrencyDisposition",
    "ConcurrentTransitionRequest",
    "ConcurrencyOutcome",
    "advance_concurrency_token",
)


@_dataclass(frozen=True, slots=True, order=True)
class ConcurrencyToken:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("value must be an integer")
        if self.value < 0:
            raise ValueError("value must be non-negative")


INITIAL_CONCURRENCY_TOKEN = ConcurrencyToken(0)


def advance_concurrency_token(token: ConcurrencyToken) -> ConcurrencyToken:
    if type(token) is not ConcurrencyToken:
        raise TypeError("token must be a ConcurrencyToken")
    return ConcurrencyToken(token.value + 1)


class CommitCertainty(_StrEnum):
    NOT_COMMITTED = "NOT_COMMITTED"
    COMMITTED = "COMMITTED"
    INDETERMINATE = "INDETERMINATE"


class RetryDisposition(_StrEnum):
    NO_RETRY = "NO_RETRY"
    SAME_IDENTITY = "SAME_IDENTITY"
    REEVALUATE = "REEVALUATE"
    RESOLVE_FIRST = "RESOLVE_FIRST"


class ConcurrencyDisposition(_StrEnum):
    COMMITTED = "COMMITTED"
    STALE = "STALE"
    FAILED = "FAILED"
    INDETERMINATE = "INDETERMINATE"


@_dataclass(frozen=True, slots=True)
class ConcurrentTransitionRequest:
    state_id: _StateId
    request: _TransitionPersistenceRequest
    expected_token: ConcurrencyToken

    def __post_init__(self) -> None:
        if type(self.state_id) is not _StateId:
            raise TypeError("state_id must be a StateId")
        if type(self.request) is not _TransitionPersistenceRequest:
            raise TypeError("request must be a TransitionPersistenceRequest")
        if type(self.expected_token) is not ConcurrencyToken:
            raise TypeError("expected_token must be a ConcurrencyToken")
        result = self.request.result
        if result.previous_state.state_id != self.state_id:
            raise ValueError("previous state must match state_id")
        if result.next_state.state_id != self.state_id:
            raise ValueError("next state must match state_id")


@_dataclass(frozen=True, slots=True)
class ConcurrencyOutcome:
    request: ConcurrentTransitionRequest
    persistence_outcome: _PersistenceOutcome
    disposition: ConcurrencyDisposition
    current_token: ConcurrencyToken | None
    commit_certainty: CommitCertainty
    retry_disposition: RetryDisposition

    def __post_init__(self) -> None:
        if type(self.request) is not ConcurrentTransitionRequest:
            raise TypeError("request must be a ConcurrentTransitionRequest")
        if type(self.persistence_outcome) is not _PersistenceOutcome:
            raise TypeError("persistence_outcome must be a PersistenceOutcome")
        if type(self.disposition) is not ConcurrencyDisposition:
            raise TypeError("disposition must be a ConcurrencyDisposition")
        if self.current_token is not None and type(self.current_token) is not ConcurrencyToken:
            raise TypeError("current_token must be a ConcurrencyToken or None")
        if type(self.commit_certainty) is not CommitCertainty:
            raise TypeError("commit_certainty must be a CommitCertainty")
        if type(self.retry_disposition) is not RetryDisposition:
            raise TypeError("retry_disposition must be a RetryDisposition")
        valid = (
            self._is_committed()
            or self._is_stale()
            or self._is_failed()
            or self._is_indeterminate()
        )
        if not valid:
            raise ValueError("invalid concurrency outcome combination")

    def _is_committed(self) -> bool:
        return (
            self.disposition is ConcurrencyDisposition.COMMITTED
            and self.persistence_outcome.disposition is _PersistenceDisposition.SUCCESS
            and self.commit_certainty is CommitCertainty.COMMITTED
            and self.current_token == advance_concurrency_token(self.request.expected_token)
            and self.retry_disposition is RetryDisposition.NO_RETRY
        )

    def _is_stale(self) -> bool:
        return (
            self.disposition is ConcurrencyDisposition.STALE
            and self.persistence_outcome.disposition is _PersistenceDisposition.CONFLICT
            and self.commit_certainty is CommitCertainty.NOT_COMMITTED
            and self.current_token is not None
            and self.current_token > self.request.expected_token
            and self.retry_disposition is RetryDisposition.REEVALUATE
        )

    def _is_failed(self) -> bool:
        return (
            self.disposition is ConcurrencyDisposition.FAILED
            and self.persistence_outcome.disposition is _PersistenceDisposition.FAILURE
            and self.commit_certainty is CommitCertainty.NOT_COMMITTED
            and self.current_token is None
            and self.retry_disposition
            in (RetryDisposition.NO_RETRY, RetryDisposition.SAME_IDENTITY)
        )

    def _is_indeterminate(self) -> bool:
        return (
            self.disposition is ConcurrencyDisposition.INDETERMINATE
            and self.persistence_outcome.disposition is _PersistenceDisposition.FAILURE
            and self.commit_certainty is CommitCertainty.INDETERMINATE
            and self.current_token is None
            and self.retry_disposition is RetryDisposition.RESOLVE_FIRST
        )

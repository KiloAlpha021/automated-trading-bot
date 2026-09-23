from dataclasses import FrozenInstanceError, fields
from itertools import product
from uuid import uuid4

import pytest

from automated_trading_bot.domain.concurrency import (
    INITIAL_CONCURRENCY_TOKEN,
    CommitCertainty,
    ConcurrencyDisposition,
    ConcurrencyOutcome,
    ConcurrencyToken,
    ConcurrentTransitionRequest,
    RetryDisposition,
    advance_concurrency_token,
)
from automated_trading_bot.domain.persistence import PersistenceDisposition, PersistenceOutcome, TransitionPersistenceRequest
from automated_trading_bot.domain.state_transitions import GenericState, StateId, TransitionDisposition, TransitionResult, TransitionState, TRANSITION_CONTRACT_V1


def request(expected: int = 3) -> ConcurrentTransitionRequest:
    state_id = StateId(uuid4())
    state = GenericState(state_id, TransitionState.ACTIVE, TRANSITION_CONTRACT_V1)
    result = TransitionResult(state, state, TransitionDisposition.ILLEGAL)
    return ConcurrentTransitionRequest(state_id, TransitionPersistenceRequest(result), ConcurrencyToken(expected))


VALID = [
    (PersistenceDisposition.SUCCESS, ConcurrencyDisposition.COMMITTED, 4, CommitCertainty.COMMITTED, RetryDisposition.NO_RETRY),
    (PersistenceDisposition.CONFLICT, ConcurrencyDisposition.STALE, 5, CommitCertainty.NOT_COMMITTED, RetryDisposition.REEVALUATE),
    (PersistenceDisposition.FAILURE, ConcurrencyDisposition.FAILED, None, CommitCertainty.NOT_COMMITTED, RetryDisposition.NO_RETRY),
    (PersistenceDisposition.FAILURE, ConcurrencyDisposition.FAILED, None, CommitCertainty.NOT_COMMITTED, RetryDisposition.SAME_IDENTITY),
    (PersistenceDisposition.FAILURE, ConcurrencyDisposition.INDETERMINATE, None, CommitCertainty.INDETERMINATE, RetryDisposition.RESOLVE_FIRST),
]


@pytest.mark.parametrize("value", [0, 1, 2**64, 10**100])
def test_concurrency_token_accepts_nonnegative_exact_integers(value: int) -> None:
    assert ConcurrencyToken(value).value == value


@pytest.mark.parametrize("value", [-1, True, 1.0, "1", None])
def test_concurrency_token_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ConcurrencyToken(value)  # type: ignore[arg-type]


def test_initial_and_advancement_are_exact_and_deterministic() -> None:
    assert INITIAL_CONCURRENCY_TOKEN == ConcurrencyToken(0)
    token = ConcurrencyToken(10)
    assert advance_concurrency_token(token) == ConcurrencyToken(11)
    assert token == ConcurrencyToken(10)


def test_request_has_exact_fields_and_preserves_wrapped_request() -> None:
    item = request()
    assert [field.name for field in fields(item)] == ["state_id", "request", "expected_token"]
    assert item.request.result.previous_state.state_id is item.state_id


def test_request_rejects_state_scope_mismatch() -> None:
    item = request()
    with pytest.raises(ValueError):
        ConcurrentTransitionRequest(StateId(uuid4()), item.request, item.expected_token)


@pytest.mark.parametrize("persistence,disposition,current,certainty,retry", VALID)
def test_complete_valid_outcome_matrix(persistence: PersistenceDisposition, disposition: ConcurrencyDisposition, current: int | None, certainty: CommitCertainty, retry: RetryDisposition) -> None:
    item = request()
    outcome = ConcurrencyOutcome(item, PersistenceOutcome(persistence), disposition, None if current is None else ConcurrencyToken(current), certainty, retry)
    assert outcome.request is item
    assert outcome.disposition is disposition


def test_all_other_cross_combinations_reject() -> None:
    item = request()
    currents = [None, 3, 4, 5]
    accepted = 0
    for p, d, c, certainty, retry in product(PersistenceDisposition, ConcurrencyDisposition, currents, CommitCertainty, RetryDisposition):
        args = (item, PersistenceOutcome(p), d, None if c is None else ConcurrencyToken(c), certainty, retry)
        is_valid = (
            (p is PersistenceDisposition.SUCCESS and d is ConcurrencyDisposition.COMMITTED and c == 4 and certainty is CommitCertainty.COMMITTED and retry is RetryDisposition.NO_RETRY)
            or (p is PersistenceDisposition.CONFLICT and d is ConcurrencyDisposition.STALE and c is not None and c > 3 and certainty is CommitCertainty.NOT_COMMITTED and retry is RetryDisposition.REEVALUATE)
            or (p is PersistenceDisposition.FAILURE and d is ConcurrencyDisposition.FAILED and c is None and certainty is CommitCertainty.NOT_COMMITTED and retry in (RetryDisposition.NO_RETRY, RetryDisposition.SAME_IDENTITY))
            or (p is PersistenceDisposition.FAILURE and d is ConcurrencyDisposition.INDETERMINATE and c is None and certainty is CommitCertainty.INDETERMINATE and retry is RetryDisposition.RESOLVE_FIRST)
        )
        if is_valid:
            ConcurrencyOutcome(*args)
            accepted += 1
        else:
            with pytest.raises(ValueError):
                ConcurrencyOutcome(*args)
    assert accepted == 6


def test_stale_requires_authoritative_newer_token() -> None:
    item = request()
    for current in (None, ConcurrencyToken(2), ConcurrencyToken(3)):
        with pytest.raises(ValueError):
            ConcurrencyOutcome(item, PersistenceOutcome(PersistenceDisposition.CONFLICT), ConcurrencyDisposition.STALE, current, CommitCertainty.NOT_COMMITTED, RetryDisposition.REEVALUATE)


def test_indeterminate_cannot_fabricate_token_or_blind_retry() -> None:
    item = request()
    for current, retry in ((ConcurrencyToken(4), RetryDisposition.RESOLVE_FIRST), (None, RetryDisposition.SAME_IDENTITY)):
        with pytest.raises(ValueError):
            ConcurrencyOutcome(item, PersistenceOutcome(PersistenceDisposition.FAILURE), ConcurrencyDisposition.INDETERMINATE, current, CommitCertainty.INDETERMINATE, retry)


def test_outcomes_are_frozen_and_authority_negative() -> None:
    item = request()
    outcome = ConcurrencyOutcome(item, PersistenceOutcome(PersistenceDisposition.SUCCESS), ConcurrencyDisposition.COMMITTED, ConcurrencyToken(4), CommitCertainty.COMMITTED, RetryDisposition.NO_RETRY)
    with pytest.raises(FrozenInstanceError):
        outcome.current_token = ConcurrencyToken(5)  # type: ignore[misc]
    assert not any(hasattr(outcome, name) for name in ("commit", "retry", "execute", "authorize", "trade"))

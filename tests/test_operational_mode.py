from dataclasses import FrozenInstanceError, replace

import pytest

from automated_trading_bot.domain.operational_mode import OperationalMode, OperationalState


def test_startup_defaults_to_no_new_orders() -> None:
    state = OperationalState()
    assert state.mode is OperationalMode.NO_NEW_ORDERS


@pytest.mark.parametrize("mode", [
    OperationalMode.NO_NEW_ORDERS, OperationalMode.READ_ONLY,
    OperationalMode.CANCEL_ONLY, OperationalMode.STRATEGY_DISABLED,
    OperationalMode.INSTRUMENT_DISABLED, OperationalMode.BROKER_DISABLED,
    OperationalMode.HALTED,
])
def test_restrictive_mode_can_be_recorded_without_granting_authority(mode: OperationalMode) -> None:
    assert OperationalState(mode).mode is mode


@pytest.mark.parametrize("mode", [OperationalMode.NORMAL, OperationalMode.DEGRADED])
def test_modes_requiring_authorization_are_rejected(mode: OperationalMode) -> None:
    with pytest.raises(ValueError, match="operational authorization gate is not implemented"):
        OperationalState(mode)
    state = OperationalState()
    with pytest.raises(ValueError):
        replace(state, mode=mode)
    assert state.mode is OperationalMode.NO_NEW_ORDERS


@pytest.mark.parametrize("invalid", [None, True, False, 0, 1, "NORMAL", "NO_NEW_ORDERS", "UNKNOWN", {}, []])
def test_invalid_or_unknown_mode_is_not_coerced(invalid: object) -> None:
    with pytest.raises(TypeError, match="mode must be an OperationalMode"):
        OperationalState(invalid)


def test_state_is_immutable() -> None:
    state = OperationalState()
    with pytest.raises(FrozenInstanceError):
        state.mode = OperationalMode.NORMAL
    assert state.mode is OperationalMode.NO_NEW_ORDERS


def test_fresh_start_does_not_restore_previous_state_implicitly() -> None:
    previous = OperationalState(OperationalMode.HALTED)
    fresh = OperationalState()
    assert previous.mode is OperationalMode.HALTED
    assert fresh.mode is OperationalMode.NO_NEW_ORDERS
    assert fresh is not previous


@pytest.mark.parametrize("evidence", [None, True, "APPROVED", {"status": "APPROVED"}])
def test_startup_has_no_authorization_shortcut(evidence: object) -> None:
    with pytest.raises(TypeError):
        OperationalState(authorization=evidence)
    assert OperationalState().mode is OperationalMode.NO_NEW_ORDERS

"""M1 replay denial acceptance; no real or simulated financial ports are added."""

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from automated_trading_bot.recovery.replay import ReplayContext


def assert_financial_attempt_denied(invoke: Callable[[Callable[[], object]], object]) -> None:
    calls: list[str] = []

    def attempted_effect() -> object:
        # Inert sentinel, not a broker adapter or a future port implementation.
        calls.append("called")
        return None

    try:
        invoke(attempted_effect)
    except PermissionError as error:
        assert str(error) == "financial side effects are forbidden during replay"
    else:
        raise AssertionError("replay allowed a financial-effect attempt")
    assert calls == [], "financial operation ran before denial"


def test_replay_financial_attempt_is_hard_denied() -> None:
    replay = ReplayContext()
    for _ in range(3):
        assert_financial_attempt_denied(replay.invoke_financial_effect)


def test_permissive_replay_mutation_fails_acceptance() -> None:
    def permissive(operation: Callable[[], object]) -> object:
        return operation()

    with pytest.raises(AssertionError, match="replay allowed"):
        assert_financial_attempt_denied(permissive)


def test_execute_then_deny_mutation_fails_acceptance() -> None:
    def too_late(operation: Callable[[], object]) -> object:
        operation()
        raise PermissionError("financial side effects are forbidden during replay")

    with pytest.raises(AssertionError, match="ran before denial"):
        assert_financial_attempt_denied(too_late)


@pytest.mark.parametrize("invalid", [None, True, "APPROVED", {}, 1])
def test_invalid_invocation_remains_denied(invalid: object) -> None:
    with pytest.raises(PermissionError, match="forbidden during replay"):
        ReplayContext().invoke_financial_effect(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("override", ["authorized", "mode", "ports"])
def test_replay_has_no_activation_override(override: str) -> None:
    with pytest.raises(TypeError):
        ReplayContext(**{override: True})


@pytest.mark.parametrize("package", ["broker", "execution", "oms"])
def test_current_financial_packages_expose_no_ports(package: str) -> None:
    # Pin the current M1 absence branch of the exit criterion. If implementations
    # appear later, this must fail until their replay denial is explicitly tested.
    root = Path(__file__).resolve().parents[1] / "src" / "automated_trading_bot" / package
    assert root.is_dir()
    paths = sorted(root.rglob("*.py"))
    assert paths
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        executable = [node for node in tree.body if not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )]
        assert not executable, f"{path}: financial package changed; replay port evidence required"

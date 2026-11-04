"""Picklable, lightweight worker handlers for process-isolation tests."""

from __future__ import annotations

import time

from automated_trading_bot.stage5.commit_boundary import (
    IsolatedCommand,
    IsolatedOutcome,
    IsolatedStatus,
)


def committed(command: IsolatedCommand) -> IsolatedOutcome:
    effect = int(command.payload.get("effect", 5000))
    return IsolatedOutcome(command.operation_id, command.request_digest, IsolatedStatus.COMMITTED, "RESERVED", effect, effect)


def success_handler(command: IsolatedCommand, cancel: object) -> IsolatedOutcome:
    del cancel
    return committed(command)


def denied_handler(command: IsolatedCommand, cancel: object) -> IsolatedOutcome:
    del cancel
    return IsolatedOutcome(command.operation_id, command.request_digest, IsolatedStatus.DENIED, "DENIED")


def hanging_handler(command: IsolatedCommand, cancel: object) -> None:
    del command, cancel
    time.sleep(30)


def cooperative_handler(command: IsolatedCommand, cancel: object) -> None:
    del command
    while not cancel.is_set():  # type: ignore[attr-defined]
        time.sleep(0.01)


def missing_readback(command: IsolatedCommand, cancel: object) -> None:
    del command, cancel


def committed_readback(command: IsolatedCommand, cancel: object) -> IsolatedOutcome:
    del cancel
    return committed(command)


def foreign_readback(command: IsolatedCommand, cancel: object) -> IsolatedOutcome:
    del cancel
    return IsolatedOutcome("FOREIGN", command.request_digest, IsolatedStatus.COMMITTED, "RESERVED", 5000, 5000)

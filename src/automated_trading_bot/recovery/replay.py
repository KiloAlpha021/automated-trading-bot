"""M1 replay non-execution boundary (IMPL-I009, IMPL-I018, IMP-027).

This is a denial control only, not a replay engine or a financial port. There is
no live mode, authorization override, port registry or dispatch implementation.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never


@dataclass(frozen=True, slots=True)
class ReplayContext:
    """A replay-only context that cannot invoke financial operations.

    Current M1 broker/execution/OMS packages expose no financial ports. Any
    attempted financial invocation through this boundary is denied before the
    callable is inspected or executed, including malformed inputs. Future replay
    orchestration must preserve this boundary and its port-absence evidence.
    """

    def invoke_financial_effect(self, operation: Callable[[], object]) -> Never:
        raise PermissionError("financial side effects are forbidden during replay")

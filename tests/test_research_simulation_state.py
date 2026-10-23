from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.dataset_qualification import ClaimCeiling
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import DecisionInputEligibility, EligibilityState
from automated_trading_bot.research.research_governance import AssuranceMode, JournalError
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
    create_order,
)
from automated_trading_bot.research.simulation_state import (
    SimulationStateError,
    SimulationStateJournal,
)


DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
USD = Currency("USD")
INSTRUMENT = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))


@dataclass(frozen=True, slots=True)
class Trust:
    checkpoint_digests: frozenset[str]
    identity: str = "test-trust:s4-11"
    assurance_mode: AssuranceMode = AssuranceMode.ENGINEERING_SIMULATION

    def require_approval(self, evidence, plan):
        raise AssertionError("approval is outside this fixture")

    def require_checkpoint(self, checkpoint):
        if checkpoint.checkpoint_digest not in self.checkpoint_digests:
            raise JournalError("checkpoint is not independently established")


def at(hour: int) -> Timestamp:
    return Timestamp(datetime(2026, 1, 2, hour, tzinfo=timezone.utc))


def make_order():
    eligibility = DecisionInputEligibility(
        EligibilityState.ELIGIBLE,
        (),
        "atis:research:feature-observation:v1:sha256:" + "3" * 64,
        at(10),
        ClaimCeiling.ENGINEERING_ONLY,
    )
    return create_order(
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        currency=USD,
        release_at=at(11),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=eligibility,
    )


def cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        "atis:simulation-cost-model:v1:ec0",
        CostScenario.EC0,
        tuple(
            CostRate(component, Decimal(0), CostProvenance.MODELLED, DIGEST)
            for component in CostComponent
        ),
        True,
    )


def execution() -> ExecutionSnapshot:
    return ExecutionSnapshot(
        at(13), Decimal("20"), at(12), ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE, Decimal("2"), USD, DIGEST,
    )


def journal(root: Path, trust: Trust, checkpoint=None) -> SimulationStateJournal:
    return SimulationStateJournal(
        root,
        journal_identity="simulation:s4-11:test",
        epoch="epoch:1",
        opening_cash=Decimal("100000"),
        currency=USD,
        research_run_id=RUN,
        opened_at=at(9),
        evidence_digest=DIGEST,
        checkpoint=checkpoint,
        trust=trust,
    )


def test_command_history_recovers_complete_reconciled_state(tmp_path: Path) -> None:
    value = journal(tmp_path / "journal", Trust(frozenset()))
    order = make_order()
    value.submit("command:submit", order)
    final = value.execute_and_post(
        "command:execute", order.order_id, Decimal("2"), execution(), cost_model()
    )
    recovered = value.recover()
    assert recovered == final
    assert len(recovered.oms.orders) == len(recovered.oms.fills) == 1
    assert len(recovered.ledger.events) == 2


def test_checkpoint_is_independently_required(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    initial = journal(root, Trust(frozenset()))
    initial.submit("command:submit", make_order())
    candidate = initial.checkpoint_candidate(establishment_identity="test-trust:s4-11")
    with pytest.raises(SimulationStateError, match="checkpoint"):
        initial.require_checkpointed()
    accepted = journal(root, Trust(frozenset({candidate.checkpoint_digest})), candidate)
    accepted.require_checkpointed()


def test_truncation_and_replacement_fail_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    initial = journal(root, Trust(frozenset()))
    order = make_order()
    initial.submit("command:submit", order)
    initial.execute_and_post("command:execute", order.order_id, Decimal("2"), execution(), cost_model())
    candidate = initial.checkpoint_candidate(establishment_identity="test-trust:s4-11")
    (root / "00000002.json").unlink()
    truncated = journal(root, Trust(frozenset({candidate.checkpoint_digest})), candidate)
    with pytest.raises(SimulationStateError, match="checkpoint mismatch"):
        truncated.require_checkpointed()


def test_incomplete_or_concurrent_publication_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    value = journal(root, Trust(frozenset()))
    (root / ".simulation-state.lock").write_text("busy", encoding="utf-8")
    with pytest.raises(SimulationStateError, match="already active"):
        value.submit("command:submit", make_order())
    (root / ".simulation-state.lock").unlink()
    (root / ".00000001.json.1.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(SimulationStateError, match="unexpected"):
        value.recover()

"""Durable, complete S4-04/S4-05 engineering-simulation state history."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
import os
from pathlib import Path
from typing import Iterator, Mapping
from uuid import UUID

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId, OrderId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import EligibilityState
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationState,
    reconcile,
)
from automated_trading_bot.research.research_governance import (
    AssuranceMode,
    JournalCheckpoint,
    TrustContext,
)
from automated_trading_bot.research.simulated_execution import (
    CostComponent,
    CostProvenance,
    CostRate,
    CostScenario,
    ExecutionCostModel,
    ExecutionInputState,
    ExecutionSnapshot,
    SimulatedOms,
    SimulatedOrder,
    post_fill_to_ledger,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerEventId,
    SimulationLedger,
)


class SimulationStateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SimulationStateSnapshot:
    sequence: int
    terminal_digest: str
    state_digest: str
    oms: SimulatedOms
    ledger: SimulationLedger


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise SimulationStateError(f"{name} must be nonempty text")
    return value


def _normal(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {item.name: _normal(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SimulationStateError("state contains non-finite Decimal")
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_normal(item) for item in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    raise SimulationStateError(f"unsupported canonical value: {type(value).__name__}")


def _digest(domain: bytes, body: object) -> str:
    return EvidenceContentDigest.from_bytes(domain + b"\0" + canonical_json(body)).value


def _timestamp(value: str) -> Timestamp:
    return Timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _order_body(value: SimulatedOrder) -> dict[str, object]:
    return {
        "order_id": str(value.order_id.value),
        "instrument_id": value.instrument_id.to_string(),
        "side": value.side.value,
        "quantity": str(value.quantity),
        "currency": value.currency.code,
        "release_at": value.release_at.value.isoformat(),
        "research_run_id": value.research_run_id.value,
        "evidence_digest": value.evidence_digest.value,
        "eligibility_state": value.eligibility_state.value,
        "eligibility_reasons": list(value.eligibility_reasons),
        "eligibility_observation_id": value.eligibility_observation_id,
        "decision_time": value.decision_time.value.isoformat(),
    }


def _order(body: Mapping[str, object]) -> SimulatedOrder:
    reasons = body["eligibility_reasons"]
    if type(reasons) is not list:
        raise SimulationStateError("eligibility reasons are invalid")
    return SimulatedOrder(
        OrderId(UUID(str(body["order_id"]))),
        InstrumentId.parse(str(body["instrument_id"])),
        OrderSide(str(body["side"])),
        Decimal(str(body["quantity"])),
        Currency(str(body["currency"])),
        _timestamp(str(body["release_at"])),
        ResearchRunId(str(body["research_run_id"])),
        EvidenceContentDigest(str(body["evidence_digest"])),
        EligibilityState(str(body["eligibility_state"])),
        tuple(str(item) for item in reasons),
        str(body["eligibility_observation_id"]),
        _timestamp(str(body["decision_time"])),
    )


def _snapshot_body(value: ExecutionSnapshot) -> dict[str, object]:
    return {
        "execution_at": value.execution_at.value.isoformat(),
        "price": None if value.price is None else str(value.price),
        "price_available_at": None if value.price_available_at is None else value.price_available_at.value.isoformat(),
        "security_state": value.security_state.value,
        "liquidity_state": value.liquidity_state.value,
        "available_quantity": None if value.available_quantity is None else str(value.available_quantity),
        "currency": None if value.currency is None else value.currency.code,
        "evidence_digest": None if value.evidence_digest is None else value.evidence_digest.value,
    }


def _snapshot(body: Mapping[str, object]) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        _timestamp(str(body["execution_at"])),
        None if body["price"] is None else Decimal(str(body["price"])),
        None if body["price_available_at"] is None else _timestamp(str(body["price_available_at"])),
        ExecutionInputState(str(body["security_state"])),
        ExecutionInputState(str(body["liquidity_state"])),
        None if body["available_quantity"] is None else Decimal(str(body["available_quantity"])),
        None if body["currency"] is None else Currency(str(body["currency"])),
        None if body["evidence_digest"] is None else EvidenceContentDigest(str(body["evidence_digest"])),
    )


def _model_body(value: ExecutionCostModel) -> dict[str, object]:
    return {
        "model_id": value.model_id,
        "scenario": value.scenario.value,
        "diagnostic_only": value.diagnostic_only,
        "rates": [
            {
                "component": rate.component.value,
                "basis_points": str(rate.basis_points),
                "provenance": rate.provenance.value,
                "evidence_digest": rate.evidence_digest.value,
            }
            for rate in value.rates
        ],
    }


def _model(body: Mapping[str, object]) -> ExecutionCostModel:
    rates = body["rates"]
    if type(rates) is not list:
        raise SimulationStateError("cost rates are invalid")
    return ExecutionCostModel(
        str(body["model_id"]),
        CostScenario(str(body["scenario"])),
        tuple(
            CostRate(
                CostComponent(str(item["component"])),
                Decimal(str(item["basis_points"])),
                CostProvenance(str(item["provenance"])),
                EvidenceContentDigest(str(item["evidence_digest"])),
            )
            for item in rates
        ),
        bool(body["diagnostic_only"]),
    )


class SimulationStateJournal:
    """Command-only durable owner of a complete simulated OMS/ledger pair."""

    def __init__(
        self,
        root: Path,
        *,
        journal_identity: str,
        epoch: str,
        opening_cash: Decimal,
        currency: Currency,
        research_run_id: ResearchRunId,
        opened_at: Timestamp,
        evidence_digest: EvidenceContentDigest,
        checkpoint: JournalCheckpoint | None = None,
        trust: TrustContext,
    ) -> None:
        if not isinstance(root, Path) or root.is_symlink():
            raise SimulationStateError("journal root must be a safe pathlib.Path")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise SimulationStateError("journal root is unsafe")
        self._root = root.resolve(strict=True)
        self._identity = _text(journal_identity, "journal_identity")
        self._epoch = _text(epoch, "epoch")
        self._checkpoint = checkpoint
        self._trust = trust
        self._genesis = {
            "opening_cash": str(opening_cash),
            "currency": currency.code,
            "research_run_id": research_run_id.value,
            "opened_at": opened_at.value.isoformat(),
            "evidence_digest": evidence_digest.value,
        }
        if trust.assurance_mode is not AssuranceMode.ENGINEERING_SIMULATION:
            raise SimulationStateError("simulation-state trust must be engineering-only")
        self.recover()

    @property
    def journal_identity(self) -> str:
        return self._identity

    @property
    def epoch(self) -> str:
        return self._epoch

    def _paths(self) -> list[Path]:
        unexpected = [
            p
            for p in self._root.iterdir()
            if p.suffix != ".json" and p.name != ".simulation-state.lock"
        ]
        if unexpected:
            raise SimulationStateError("journal contains incomplete or unexpected files")
        return sorted(self._root.glob("*.json"))

    def _genesis_state(self) -> tuple[SimulatedOms, SimulationLedger]:
        body = self._genesis
        ledger = SimulationLedger.empty(Currency(str(body["currency"])))
        opening = FinancialEvent(
            LedgerEventId("atis:simulation-ledger-event:v1:simulation-state-opening"),
            1,
            _timestamp(str(body["opened_at"])),
            FinancialEventKind.OPENING_CASH,
            ResearchRunId(str(body["research_run_id"])),
            EvidenceContentDigest(str(body["evidence_digest"])),
            Currency(str(body["currency"])),
            cash_delta=Decimal(str(body["opening_cash"])),
        )
        return SimulatedOms(), ledger.apply(opening)

    def _apply(self, oms: SimulatedOms, ledger: SimulationLedger, command: str, payload: Mapping[str, object]) -> tuple[SimulatedOms, SimulationLedger]:
        if command == "SUBMIT":
            return oms.submit(_order(payload["order"])), ledger  # type: ignore[arg-type]
        order_id = OrderId(UUID(str(payload["order_id"])))
        if command == "CANCEL":
            return oms.cancel(order_id), ledger
        if command == "MARK_UNFILLED":
            return oms.mark_unfilled(order_id, str(payload["reason"])), ledger
        if command == "EXECUTE_AND_POST":
            prior = len(oms.fills)
            next_oms = oms.execute(
                order_id,
                Decimal(str(payload["requested_quantity"])),
                _snapshot(payload["snapshot"]),  # type: ignore[arg-type]
                _model(payload["cost_model"]),  # type: ignore[arg-type]
            )
            next_ledger = ledger
            for fill in next_oms.fills[prior:]:
                next_ledger = post_fill_to_ledger(next_ledger, fill)
            if reconcile(next_oms, next_ledger).state is not ReconciliationState.RECONCILED:
                raise SimulationStateError("combined simulation state does not reconcile")
            return next_oms, next_ledger
        raise SimulationStateError("unknown simulation-state command")

    def recover(self, *, continuity_verified: bool = False) -> SimulationStateSnapshot:
        oms, ledger = self._genesis_state()
        previous: str | None = None
        terminal = _digest(b"ATIS-S4-11-SIMULATION-GENESIS-V1", self._genesis)
        seen: set[str] = set()
        for sequence, path in enumerate(self._paths(), 1):
            if path.name != f"{sequence:08d}.json" or path.is_symlink():
                raise SimulationStateError("journal sequence is missing or unsafe")
            try:
                raw = path.read_bytes()
                record = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SimulationStateError("journal record is unreadable") from error
            if type(record) is not dict or canonical_json(record) != raw:
                raise SimulationStateError("journal record is not canonical")
            required = {"journal_identity", "epoch", "sequence", "command_id", "command", "payload", "previous_digest", "state_digest", "content_digest"}
            if set(record) != required:
                raise SimulationStateError("journal record schema mismatch")
            supplied_digest = record["content_digest"]
            body = dict(record)
            del body["content_digest"]
            expected_digest = _digest(b"ATIS-S4-11-SIMULATION-STATE-RECORD-V1", body)
            if (
                record["journal_identity"] != self._identity
                or record["epoch"] != self._epoch
                or record["sequence"] != sequence
                or record["previous_digest"] != previous
                or supplied_digest != expected_digest
                or record["command_id"] in seen
            ):
                raise SimulationStateError("journal chain or identity is invalid")
            seen.add(str(record["command_id"]))
            payload = record["payload"]
            if type(payload) is not dict:
                raise SimulationStateError("command payload is invalid")
            oms, ledger = self._apply(oms, ledger, str(record["command"]), payload)
            state_digest = _digest(b"ATIS-S4-11-SIMULATION-COMBINED-STATE-V1", {"oms": _normal(oms), "ledger": _normal(ledger)})
            if record["state_digest"] != state_digest:
                raise SimulationStateError("resulting combined state digest mismatch")
            previous = str(supplied_digest)
            terminal = previous
        snapshot = SimulationStateSnapshot(len(self._paths()), terminal, _digest(b"ATIS-S4-11-SIMULATION-COMBINED-STATE-V1", {"oms": _normal(oms), "ledger": _normal(ledger)}), oms, ledger)
        if continuity_verified:
            self.require_checkpointed(snapshot)
        return snapshot

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        lock = self._root / ".simulation-state.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise SimulationStateError("simulation-state writer is already active") from error
        os.close(descriptor)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def _publish(self, command_id: str, command: str, payload: dict[str, object]) -> SimulationStateSnapshot:
        with self._exclusive():
            prior = self.recover()
            oms, ledger = self._apply(prior.oms, prior.ledger, command, payload)
            state_digest = _digest(b"ATIS-S4-11-SIMULATION-COMBINED-STATE-V1", {"oms": _normal(oms), "ledger": _normal(ledger)})
            body = {
                "journal_identity": self._identity,
                "epoch": self._epoch,
                "sequence": prior.sequence + 1,
                "command_id": _text(command_id, "command_id"),
                "command": command,
                "payload": payload,
                "previous_digest": None if prior.sequence == 0 else prior.terminal_digest,
                "state_digest": state_digest,
            }
            record = dict(body)
            record["content_digest"] = _digest(b"ATIS-S4-11-SIMULATION-STATE-RECORD-V1", body)
            target = self._root / f"{prior.sequence + 1:08d}.json"
            temporary = self._root / f".{target.name}.{os.getpid()}.tmp"
            data = canonical_json(record)
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if target.exists():
                    raise SimulationStateError("conflicting simulation-state successor")
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            result = self.recover()
            if result.sequence != prior.sequence + 1 or result.state_digest != state_digest:
                raise SimulationStateError("published state failed recovery")
            return result

    def submit(self, command_id: str, order: SimulatedOrder) -> SimulationStateSnapshot:
        if type(order) is not SimulatedOrder:
            raise TypeError("order must be SimulatedOrder")
        return self._publish(command_id, "SUBMIT", {"order": _order_body(order)})

    def cancel(self, command_id: str, order_id: OrderId) -> SimulationStateSnapshot:
        return self._publish(command_id, "CANCEL", {"order_id": str(order_id.value)})

    def mark_unfilled(self, command_id: str, order_id: OrderId, reason: str) -> SimulationStateSnapshot:
        return self._publish(command_id, "MARK_UNFILLED", {"order_id": str(order_id.value), "reason": reason})

    def execute_and_post(self, command_id: str, order_id: OrderId, requested_quantity: Decimal, snapshot: ExecutionSnapshot, cost_model: ExecutionCostModel) -> SimulationStateSnapshot:
        return self._publish(command_id, "EXECUTE_AND_POST", {"order_id": str(order_id.value), "requested_quantity": str(requested_quantity), "snapshot": _snapshot_body(snapshot), "cost_model": _model_body(cost_model)})

    def checkpoint_candidate(self, *, establishment_identity: str) -> JournalCheckpoint:
        state = self.recover()
        return JournalCheckpoint.create(journal_identity=self._identity, epoch=self._epoch, accepted_sequence=state.sequence, terminal_digest=state.terminal_digest if state.sequence else None, establishment_identity=establishment_identity)

    def require_checkpointed(self, snapshot: SimulationStateSnapshot | None = None) -> None:
        state = self.recover() if snapshot is None else snapshot
        checkpoint = self._checkpoint
        if checkpoint is None:
            raise SimulationStateError("trusted simulation-state checkpoint is NOT_ESTABLISHED")
        self._trust.require_checkpoint(checkpoint)
        if (
            checkpoint.journal_identity != self._identity
            or checkpoint.epoch != self._epoch
            or checkpoint.establishment_identity != self._trust.identity
            or checkpoint.accepted_sequence != state.sequence
            or checkpoint.terminal_digest != (state.terminal_digest if state.sequence else None)
        ):
            raise SimulationStateError("simulation-state checkpoint mismatch")

    @contextmanager
    def exclusive_snapshot(self, *, continuity_verified: bool = True) -> Iterator[SimulationStateSnapshot]:
        with self._exclusive():
            state = self.recover()
            if continuity_verified:
                self.require_checkpointed(state)
            yield state

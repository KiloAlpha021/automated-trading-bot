"""Durable, complete S4-04/S4-05 engineering-simulation state history."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
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


def _v2_json(body: object) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _v2_digest(domain: bytes, body: object) -> str:
    return EvidenceContentDigest.from_bytes(domain + b"\0" + _v2_json(body)).value


# Version 2 is deliberately separate from the S4-11 journal wire protocol.
# It is a closed engineering fixture, NOT a TrustContext or deployment authority.
ENGINEERING_SIMULATION_ONLY = "ENGINEERING_SIMULATION_ONLY"


@dataclass(frozen=True, slots=True)
class PortfolioControllerConfig:
    portfolio_id: str
    journal_identity: str
    epoch: str
    initial_book: object
    opening_cash: Decimal
    opening_run: ResearchRunId
    opened_at: Timestamp
    provenance: EvidenceContentDigest
    scheduling_mode: str = "GENERIC"
    calendar_id: str = ""
    calendar_version: str = ""
    authority: str = ENGINEERING_SIMULATION_ONLY

    def __post_init__(self) -> None:
        from automated_trading_bot.research import portfolio_construction as pc

        for name in ("portfolio_id", "journal_identity", "epoch"):
            _text(getattr(self, name), name)
        if self.authority != ENGINEERING_SIMULATION_ONLY:
            raise SimulationStateError("operational authority NOT_ESTABLISHED")
        book = self.initial_book
        if type(book) is not pc.CommitmentBook or book.records or book.rebalance_transitions or book.removed_strategies:
            raise SimulationStateError("fresh portfolio genesis required; imports forbidden")
        pc._check_book(book)
        if book.allocation.currency != Currency("USD") or self.opening_cash != Decimal(book.allocation.total_cents) / 100:
            raise SimulationStateError("USD genesis funding must equal allocation")
        if any(a.admission_class is not pc.AdmissionClass.HYPOTHETICAL_PARTICIPATION for a in book.admissions):
            raise SimulationStateError("engineering admissions only")
        if book.admissions and self.opening_run.value not in {a.strategy.research_run_id for a in book.admissions}:
            raise SimulationStateError("opening run is foreign")
        if self.scheduling_mode not in {"GENERIC", "MONTHLY"}:
            raise SimulationStateError("unsupported scheduling mode")
        if self.scheduling_mode == "MONTHLY" and (not self.calendar_id or not self.calendar_version):
            raise SimulationStateError("monthly calendar identity and version required")


@dataclass(frozen=True, slots=True)
class FinancialCommand:
    event: FinancialEvent
    knowledge_cutoff: Timestamp


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    order_id: OrderId
    quantity: Decimal
    snapshot: ExecutionSnapshot
    cost_model: ExecutionCostModel


@dataclass(frozen=True, slots=True)
class PortfolioCommand:
    kind: str
    decision_cutoff: Timestamp
    payload: object
    version: int = 2


@dataclass(frozen=True, slots=True)
class AcceptedPortfolioState:
    sequence: int
    digest: str
    previous_digest: str | None
    book: object
    oms: SimulatedOms
    ledger: SimulationLedger
    decision_cutoff: Timestamp
    infeasible_reservations: tuple[str, ...]
    authority: str = ENGINEERING_SIMULATION_ONLY


def _wire(value: object) -> object:
    """Closed tagged JSON, never pickle, eval or a client-selected import."""
    from datetime import date

    if isinstance(value, Enum):
        return {"enum": type(value).__module__ + "." + type(value).__name__, "value": value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {"type": type(value).__module__ + "." + type(value).__name__,
                "fields": {f.name: _wire(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, (Decimal, UUID, datetime, date)):
        return {"scalar": type(value).__name__, "value": str(value)}
    if type(value) is bytes:
        return {"bytes": value.hex()}
    if type(value) is tuple:
        return {"tuple": [_wire(v) for v in value]}
    if value is None or type(value) in {str, bool, int}:
        return value
    raise SimulationStateError("unsupported version-2 wire value")


@lru_cache(maxsize=1)
def _wire_registry() -> dict[str, object]:
    # Only reviewed value-model modules are eligible; names in data never import code.
    from importlib import import_module
    from typing import Any

    modules = (
        "research.simulation_state", "research.portfolio_construction",
        "research.robustness_assurance", "research.reconciliation_performance",
        "research.simulated_execution", "research.simulation_ledger", "research.identity",
        "research.pit_features", "research.dataset_qualification", "research.statistical_assurance",
        "domain.currency", "domain.identifiers", "domain.order_side", "domain.timestamp",
        "calendars.model", "calendars.resolution", "instruments.model", "corporate_actions.model",
    )
    registry: dict[str, Any] = {}
    for module in modules:
        for cls in vars(import_module("automated_trading_bot." + module)).values():
            if isinstance(cls, type) and (is_dataclass(cls) or issubclass(cls, Enum)):
                registry[cls.__module__ + "." + cls.__name__] = cls
    return registry


def _unwire(value: object) -> object:
    from datetime import date
    from typing import Any, cast
    from automated_trading_bot.research import portfolio_construction as pc

    registry = cast(dict[str, Any], _wire_registry())
    if value is None or type(value) in {str, bool, int}:
        return value
    if not isinstance(value, dict):
        raise SimulationStateError("invalid wire object")
    if set(value) == {"bytes"} and type(value["bytes"]) is str:
        return bytes.fromhex(value["bytes"])
    if set(value) == {"tuple"} and type(value["tuple"]) is list:
        return tuple(_unwire(v) for v in value["tuple"])
    if set(value) == {"scalar", "value"}:
        constructors: dict[str, Any] = {"Decimal": Decimal, "UUID": UUID,
                                       "datetime": datetime.fromisoformat, "date": date.fromisoformat}
        if value["scalar"] not in constructors:
            raise SimulationStateError("unknown wire scalar")
        return constructors[value["scalar"]](value["value"])
    if set(value) == {"enum", "value"} and value["enum"] in registry:
        return registry[value["enum"]](value["value"])
    if set(value) != {"type", "fields"} or value["type"] not in registry:
        raise SimulationStateError("unknown wire schema")
    cls = registry[value["type"]]
    body = value["fields"]
    if type(body) is not dict or set(body) != {f.name for f in fields(cls)}:
        raise SimulationStateError("wire fields mismatch")
    args: dict[str, Any] = {k: _unwire(v) for k, v in body.items()}
    if cls is pc.PortfolioAdmission:
        rebuilt = pc.PortfolioAdmission._from_assurance(args["source_context"], args["admission_class"],
                                                       args["source_robustness"], args["limitations"])
        if _wire(rebuilt) != value:
            raise SimulationStateError("admission reconstruction mismatch")
        return rebuilt
    rebuilt = cls(**{f.name: args[f.name] for f in fields(cls) if f.init})
    if _wire(rebuilt) != value:
        raise SimulationStateError("derived wire fields mismatch")
    return rebuilt


class EngineeringPortfolioController:
    """Fixture-owned v2 authority over KNOWN simulation commands only.

    The launch code supplies roots/configuration; propose() cannot replace them.
    Same-privilege filesystem/code tampering and power-loss durability are NOT
    established. A fresh process requires the retained bootstrap, never discovery
    from a candidate. Legacy journals and TrustContext are entirely unchanged.
    """

    def __init__(self, root: Path, accepted_root: Path, config: PortfolioControllerConfig,
                 *, establish: bool = False) -> None:
        if type(config) is not PortfolioControllerConfig:
            raise SimulationStateError("explicit engineering bootstrap required")
        config.__post_init__()
        if root.is_symlink() or accepted_root.is_symlink():
            raise SimulationStateError("unsafe controller root")
        self._root, self._accepted = root.resolve(), accepted_root.resolve()
        if self._root == self._accepted or self._root in self._accepted.parents or self._accepted in self._root.parents:
            raise SimulationStateError("separate non-overlapping accepted storage required")
        self._config = config
        self._config_digest = _v2_digest(b"S4-13-CONTROLLER-CONFIG-V2", {
            "config": _wire(config), "journal_root": str(self._root), "accepted_root": str(self._accepted)})
        self._seen_sequence = -1
        self._seen_digest = ""
        if establish:
            if root.exists() or accepted_root.exists():
                raise SimulationStateError("fresh epoch roots must not exist")
            root.mkdir(parents=True)
            accepted_root.mkdir(parents=True)
            self._write(self._accepted / "bootstrap.json", {"config": _wire(config), "digest": self._config_digest})
            self._write(self._accepted / "head.json", self._head(self._genesis()))
        self.read_current()

    @staticmethod
    def _write(path: Path, body: object) -> None:
        temporary = path.with_suffix(".prepared")
        if path.is_symlink() or temporary.is_symlink():
            raise SimulationStateError("unsafe controller file")
        # An interrupted temporary write is never accepted; it may be replaced.
        with temporary.open("wb") as stream:
            stream.write(_v2_json(body))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        try:
            if path.is_symlink():
                raise SimulationStateError("unsafe controller file")
            raw = path.read_bytes()
            body = json.loads(raw)
            if type(body) is not dict or _v2_json(body) != raw:
                raise SimulationStateError("noncanonical controller record")
            return body
        except (OSError, ValueError) as error:
            raise SimulationStateError("missing or corrupt controller record") from error

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self._accepted / ".controller.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise SimulationStateError("controller writer already active") from error
        os.close(fd)
        try:
            yield
        finally:
            path.unlink()

    def _genesis(self) -> AcceptedPortfolioState:
        c = self._config
        ledger = SimulationLedger.empty(Currency("USD")).apply(FinancialEvent(
            LedgerEventId("atis:simulation-ledger-event:v1:controller-opening"), 1, c.opened_at,
            FinancialEventKind.OPENING_CASH, c.opening_run, c.provenance, Currency("USD"), cash_delta=c.opening_cash))
        return AcceptedPortfolioState(0, self._config_digest, None, c.initial_book, SimulatedOms(), ledger, c.opened_at, ())

    def _head(self, state: AcceptedPortfolioState) -> dict[str, object]:
        return {"schema": 2, "configuration": self._config_digest, "sequence": state.sequence, "digest": state.digest}

    def _recover(self) -> AcceptedPortfolioState:
        if self._root.is_symlink() or self._accepted.is_symlink():
            raise SimulationStateError("unsafe controller root")
        if self._read(self._accepted / "bootstrap.json") != {"config": _wire(self._config), "digest": self._config_digest}:
            raise SimulationStateError("foreign or changed bootstrap")
        head = self._read(self._accepted / "head.json")
        if set(head) != {"schema", "configuration", "sequence", "digest"} or head["schema"] != 2 or head["configuration"] != self._config_digest:
            raise SimulationStateError("foreign head configuration")
        seq = head["sequence"]
        if type(seq) is not int or seq < 0:
            raise SimulationStateError("invalid accepted sequence")
        if seq < self._seen_sequence or (seq == self._seen_sequence and head["digest"] != self._seen_digest):
            raise SimulationStateError("accepted-head rollback")
        state = self._genesis()
        ids: set[str] = set()
        accepted_heads: dict[int, dict[str, object]] = {}
        for n in range(1, seq + 1):
            record = self._read(self._root / f"{n:08d}.json")
            if set(record) != {"configuration", "previous", "command_id", "command", "state"} or record["configuration"] != self._config_digest or record["previous"] != state.digest:
                raise SimulationStateError("history identity or continuity mismatch")
            command_id = _text(record["command_id"], "command_id")
            if command_id in ids:
                raise SimulationStateError("duplicate accepted command")
            ids.add(command_id)
            command = _unwire(record["command"])
            if type(command) is not PortfolioCommand:
                raise SimulationStateError("typed command required")
            state = self._transition(state, command_id, command)
            accepted_heads[n] = self._head(state)
            if record["state"] != _wire(state):
                raise SimulationStateError("semantic replay mismatch")
        if head != self._head(state):
            raise SimulationStateError("head does not bind replayed state")
        # Durable acknowledgement witnesses detect head-only rollback after restart.
        for path in self._accepted.glob("ack-*.json"):
            ack = self._read(path)
            ack_sequence = ack.get("sequence")
            if type(ack_sequence) is not int or ack_sequence > state.sequence or ack_sequence < 1:
                raise SimulationStateError("acknowledged head rollback")
            if ack != accepted_heads[ack_sequence]:
                raise SimulationStateError("acknowledgement mismatch")
        self._seen_sequence, self._seen_digest = state.sequence, state.digest
        return state

    def read_current(self) -> AcceptedPortfolioState:
        with self._lock():
            return self._recover()

    def _crash_point(self, phase: str) -> None:
        """Fault injection seam for deterministic engineering recovery tests."""

    def propose(self, command_id: str, command: PortfolioCommand, *, expected_sequence: int,
                expected_digest: str) -> AcceptedPortfolioState:
        _text(command_id, "command_id")
        if type(command) is not PortfolioCommand:
            raise SimulationStateError("typed command required")
        if type(expected_sequence) is not int or type(expected_digest) is not str:
            raise SimulationStateError("typed concurrency expectation required")
        with self._lock():
            prior = self._recover()
            for n in range(1, prior.sequence + 1):
                old = self._read(self._root / f"{n:08d}.json")
                if old["command_id"] == command_id:
                    if old["command"] != _wire(command):
                        raise SimulationStateError("conflicting duplicate command")
                    # Retry returns current state without reposting. A crash may
                    # have advanced the head before writing its acknowledgement.
                    witness = self._accepted / f"ack-{prior.sequence:08d}.json"
                    self._write(witness, self._head(prior))
                    if self._read(witness) != self._head(prior):
                        raise SimulationStateError("retry acknowledgement readback mismatch")
                    return prior
            if expected_sequence != prior.sequence or expected_digest != prior.digest:
                raise SimulationStateError("stale accepted predecessor")
            if _unwire(_wire(command)) != command:
                raise SimulationStateError("command cannot replay exactly")
            successor = self._transition(prior, command_id, command)
            record = {"configuration": self._config_digest, "previous": prior.digest,
                      "command_id": command_id, "command": _wire(command), "state": _wire(successor)}
            self._crash_point("prepared")
            path = self._root / f"{successor.sequence:08d}.json"
            if path.exists() and self._read(path) != record:
                raise SimulationStateError("conflicting unaccepted tail; explicit recovery required")
            self._write(path, record)
            self._crash_point("record_persisted")
            self._write(self._accepted / "head.json", self._head(successor))
            self._crash_point("head_advanced")
            recovered = self._recover()
            if recovered != successor:
                raise SimulationStateError("accepted-head readback mismatch")
            self._crash_point("readback")
            self._write(self._accepted / f"ack-{successor.sequence:08d}.json", self._head(successor))
            if self._read(self._accepted / f"ack-{successor.sequence:08d}.json") != self._head(successor):
                raise SimulationStateError("acknowledgement readback mismatch")
            self._crash_point("acknowledged")
            return recovered

    def _transition(self, prior: AcceptedPortfolioState, command_id: str,
                    command: PortfolioCommand) -> AcceptedPortfolioState:
        from automated_trading_bot.research import portfolio_construction as pc

        if command.version != 2 or type(command.decision_cutoff) is not Timestamp or command.decision_cutoff.value < prior.decision_cutoff.value:
            raise SimulationStateError("command version or backward decision cutoff")
        book = prior.book
        if type(book) is not pc.CommitmentBook:
            raise SimulationStateError("portfolio state required")
        oms, ledger = prior.oms, prior.ledger
        payload, kind = command.payload, command.kind
        if kind == "RESERVE":
            if prior.infeasible_reservations:
                raise SimulationStateError("INFEASIBLE_RESERVATION")
            if type(payload) is not tuple or any(type(r) is not pc.CommitmentRequest for r in payload):
                raise SimulationStateError("typed reservation batch required")
            for request in payload:
                if request.order.decision_time != command.decision_cutoff:
                    raise SimulationStateError("reservation decision cutoff mismatch")
                if self._config.scheduling_mode == "MONTHLY" and request.schedule is None:
                    raise SimulationStateError("mandatory monthly schedule missing")
                if request.schedule is not None:
                    self._schedule_identity(request.schedule)
                    if request.schedule.source_oms != oms or request.schedule.source_ledger != ledger:
                        raise SimulationStateError("stale schedule financial state")
                if request.order.side.value == "SELL" and (request.holdings_oms != oms or request.holdings_ledger != ledger):
                    raise SimulationStateError("stale sell decision state")
            book = pc.reserve_commitments(book, payload)
        elif kind == "SUBMIT":
            if type(payload) is not SimulatedOrder:
                raise SimulationStateError("typed original order required")
            latest = pc._latest_record(book.records, payload.order_id)
            if latest.original_order != payload or latest.state is pc.CommitmentState.RELEASED:
                raise SimulationStateError("order is not currently reserved")
            oms = oms.submit(payload)
        elif kind in {"CANCEL", "MARK_UNFILLED", "RESOLVE_OBLIGATION", "RECONCILE"}:
            if type(payload) is not OrderId:
                raise SimulationStateError("typed order identity required")
            if kind in {"CANCEL", "RESOLVE_OBLIGATION"}:
                oms = oms.cancel(payload)
            elif kind == "MARK_UNFILLED":
                oms = oms.mark_unfilled(payload, "controller:zero-fill-unfilled")
            book = pc.reconcile_commitment(book, order_id=payload, oms=oms, ledger=ledger)
        elif kind == "EXECUTE_AND_POST":
            if type(payload) is not ExecutionCommand:
                raise SimulationStateError("typed execution required")
            if payload.snapshot.execution_at.value != command.decision_cutoff.value:
                raise SimulationStateError("execution cutoff mismatch")
            latest = pc._latest_record(book.records, payload.order_id)
            if latest.cost_model != payload.cost_model:
                raise SimulationStateError("approved cost model mismatch")
            oms = oms.execute(payload.order_id, payload.quantity, payload.snapshot, payload.cost_model)
            for fill in oms.fills[len(prior.oms.fills):]:
                ledger = post_fill_to_ledger(ledger, fill)
            book = pc.reconcile_commitment(book, order_id=payload.order_id, oms=oms, ledger=ledger)
        elif kind in {"MARK", "CASH_DIVIDEND", "SPLIT", "CASH_ADJUSTMENT"}:
            if type(payload) is not FinancialCommand or payload.event.kind.value != kind:
                raise SimulationStateError("typed financial event kind mismatch")
            event = payload.event
            if (event.research_run_id.value not in {self._config.opening_run.value, *(a.strategy.research_run_id for a in book.admissions)}
                or event.occurred_at.value < prior.decision_cutoff.value
                or not event.occurred_at.value <= payload.knowledge_cutoff.value <= command.decision_cutoff.value
                or event.sequence != len(ledger.events) + 1
                or any(e.event_id == event.event_id for e in ledger.events)):
                raise SimulationStateError("financial event identity, sequence or chronology mismatch")
            if event.instrument_id is not None and ledger.state.position(event.instrument_id) is None:
                raise SimulationStateError("financial instrument is not established")
            if event.corporate_action_version_id is not None and any(
                e.corporate_action_version_id == event.corporate_action_version_id
                and e.instrument_id == event.instrument_id and e.kind == event.kind for e in ledger.events
            ):
                raise SimulationStateError("duplicate corporate action")
            ledger = ledger.apply(event)
        elif kind in {"REBALANCE", "REMOVE_STRATEGY"}:
            if type(payload) is not pc.RebalanceSchedule:
                raise SimulationStateError("qualified rebalance transition required")
            self._schedule_identity(payload)
            if payload.retained_book != book or payload.source_oms != oms or payload.source_ledger != ledger or payload.decision_time != command.decision_cutoff:
                raise SimulationStateError("stale rebalance predecessor")
            if kind == "REMOVE_STRATEGY" and not payload.removed_strategies:
                raise SimulationStateError("removal identities required")
            book = pc.carry_forward_rebalance(payload)
        else:
            raise SimulationStateError("unsupported controller command; raw replacement forbidden")
        if any(e.occurred_at.value > command.decision_cutoff.value for e in ledger.events):
            raise SimulationStateError("financial evidence beyond decision cutoff")
        require_result = reconcile(oms, ledger)
        if require_result.state is not ReconciliationState.RECONCILED:
            raise SimulationStateError("controller financial state does not reconcile")
        issues = pc.current_reservation_issues(book, oms, ledger, command.decision_cutoff)
        state = AcceptedPortfolioState(prior.sequence + 1, "", prior.digest, book, oms, ledger,
                                       command.decision_cutoff, issues)
        digest = _v2_digest(b"S4-13-ACCEPTED-PORTFOLIO-V2", {"config": self._config_digest, "id": command_id,
                         "command": _wire(command), "state": _wire(state)})
        from dataclasses import replace
        return replace(state, digest=digest)

    def _schedule_identity(self, schedule: object) -> None:
        from automated_trading_bot.research import portfolio_construction as pc

        if type(schedule) is not pc.RebalanceSchedule:
            raise SimulationStateError("qualified schedule required")
        if (str(schedule.final_session.calendar_id.value) != self._config.calendar_id
            or schedule.final_session.selected_calendar_version_id is None
            or str(schedule.final_session.selected_calendar_version_id.value) != self._config.calendar_version):
            raise SimulationStateError("configured calendar identity/version mismatch")

    def build_current_evidence(self) -> object:
        from automated_trading_bot.research import portfolio_construction as pc

        with self._lock():
            current = self._recover()
            if current.infeasible_reservations:
                raise SimulationStateError("INFEASIBLE_RESERVATION")
            book = current.book
            if type(book) is not pc.CommitmentBook:
                raise SimulationStateError("portfolio state required")
            result = reconcile(current.oms, current.ledger)
            attribution = pc.attribute_portfolio(admissions=book.admissions, oms=current.oms,
                                                ledger=current.ledger, reconciliation=result, effects=())
            return pc.build_portfolio_evidence(configuration_id=self._config_digest, allocation=book.allocation,
                commitments=book, oms=current.oms, ledger=current.ledger, reconciliation=result,
                attribution=attribution, stress_results=(),
                limitations=(ENGINEERING_SIMULATION_ONLY, "OPERATIONAL_AUTHORITY_NOT_ESTABLISHED",
                             "EXTERNAL_EVENT_COMPLETENESS_NOT_ESTABLISHED", "ACCEPTED_HEAD:" + current.digest))

"""Read-only golden-reference assurance for Stage-4 research simulation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import (
    Clamped,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Underflow,
    getcontext,
    localcontext,
)
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final
from uuid import UUID

from automated_trading_bot.corporate_actions.model import ActionVersionId
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.order_side import OrderSide
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest, ListingId
from automated_trading_bot.research.dataset_qualification import (
    AssessmentState,
    ClaimCeiling,
    DIMENSIONS,
    DimensionAssessment,
    EligibleCoverage,
    QualificationLevel,
    QualificationPolicy,
    qualify_dataset,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.pit_features import (
    AvailabilityPolicy,
    DecisionInputEligibility,
    EligibilityState,
    FeatureDefinition,
    InputState,
    MissingDataPolicy,
    PitInput,
    assess_decision_input,
    create_feature_observation,
)
from automated_trading_bot.research.reconciliation_performance import (
    ReconciliationState,
    reconcile,
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
    create_order,
    post_fill_to_ledger,
)
from automated_trading_bot.research.simulation_ledger import (
    FinancialEvent,
    FinancialEventKind,
    LedgerContractError,
    LedgerEventId,
    SimulationLedger,
)


ASSURANCE_AUTHORITY: Final = "READ_ONLY_RESEARCH_ASSURANCE_NO_TRADING_AUTHORITY"
CONTRACT_ID: Final = "ATIS-S4-07-GOLDEN-REFERENCE-ASSURANCE-V1"
CASE_CATALOGUE: Final = MappingProxyType(
    {
        "GR-01A": ("GR-01", "ROUND_TRIP"),
        "GR-01B": ("GR-01", "AVERAGE_COST_PARTIAL_SALE"),
        "GR-02": ("GR-02", "CASH_DIVIDEND"),
        "GR-03": ("GR-03", "TWO_FOR_ONE_SPLIT"),
        "GR-04": ("GR-04", "TWO_INSTRUMENT_REBALANCE"),
        "GR-05-EC0": ("GR-05", "EXECUTION_COST_EC0"),
        "GR-05-EC1": ("GR-05", "EXECUTION_COST_EC1"),
        "GR-05-EC2": ("GR-05", "EXECUTION_COST_EC2"),
        "GR-05-EC3": ("GR-05", "EXECUTION_COST_EC3"),
        "GR-06": ("GR-06", "INELIGIBLE_ORDER"),
        "GR-07-PRICE": ("GR-07", "PRICE_UNAVAILABLE"),
        "GR-07-SECURITY": ("GR-07", "SECURITY_UNAVAILABLE"),
        "GR-07-LIQUIDITY": ("GR-07", "LIQUIDITY_UNAVAILABLE"),
        "GR-07-EVIDENCE": ("GR-07", "EXECUTION_EVIDENCE_MISSING"),
        "GR-08": ("GR-08", "DELISTING"),
        "GR-09-REPLAY": ("GR-09", "DETERMINISTIC_REPLAY"),
        "GR-09-SEQUENCE": ("GR-09", "CORRUPTED_SEQUENCE"),
        "GR-09-IDENTITY": ("GR-09", "DUPLICATE_CONFLICTING_IDENTITY"),
        "GR-10-FUTURE": ("GR-10", "FUTURE_KNOWLEDGE"),
        "GR-10-UNAVAILABLE": ("GR-10", "UNAVAILABLE_FEATURE"),
        "GR-10-SAME-INSTANT": ("GR-10", "SAME_INSTANT_WITHOUT_CAUSALITY"),
    }
)
MANDATORY_CASES: Final = tuple(CASE_CATALOGUE)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
_CANONICAL_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z"
)
_MONEY_KEYS = frozenset(
    {
        "opening_cash",
        "quantity",
        "price",
        "unit_amount",
        "cash",
        "basis",
        "realised_pnl",
        "unrealised_pnl",
        "portfolio_value",
        "net_pnl",
        "gross_pnl",
        "turnover",
        "commission_fees",
        "bid_ask_spread",
        "slippage",
        "market_impact",
        "additional_friction",
        "total_cost",
        "b_quantity",
        "b_basis",
    }
)
_EXPECTED_KEYS: Final = MappingProxyType(
    {
        "GR-01A": frozenset(
            {
                "attempt_count",
                "basis",
                "cash",
                "event_count",
                "fill_count",
                "gross_pnl",
                "net_pnl",
                "order_count",
                "portfolio_value",
                "quantity",
                "realised_pnl",
                "turnover",
                "unrealised_pnl",
            }
        ),
        "GR-01B": frozenset(
            {
                "attempt_count",
                "basis",
                "cash",
                "event_count",
                "fill_count",
                "order_count",
                "quantity",
                "realised_pnl",
            }
        ),
        "GR-02": frozenset(
            {"basis", "cash", "event_count", "portfolio_value", "quantity", "realised_pnl"}
        ),
        "GR-03": frozenset(
            {"basis", "cash", "event_count", "portfolio_value", "quantity"}
        ),
        "GR-04": frozenset(
            {
                "attempt_count",
                "b_basis",
                "b_quantity",
                "basis",
                "cash",
                "event_count",
                "fill_count",
                "gross_pnl",
                "net_pnl",
                "order_count",
                "portfolio_value",
                "quantity",
                "realised_pnl",
                "turnover",
                "unrealised_pnl",
            }
        ),
        **{
            f"GR-05-EC{index}": frozenset(
                {
                    "additional_friction",
                    "basis",
                    "bid_ask_spread",
                    "cash",
                    "commission_fees",
                    "gross_pnl",
                    "market_impact",
                    "net_pnl",
                    "portfolio_value",
                    "slippage",
                    "total_cost",
                    "turnover",
                }
            )
            for index in range(4)
        },
        "GR-06": frozenset({"attempt_count", "fill_count", "status"}),
        **{
            case_id: frozenset({"attempt_count", "fill_count", "reasons", "status"})
            for case_id in (
                "GR-07-PRICE",
                "GR-07-SECURITY",
                "GR-07-LIQUIDITY",
                "GR-07-EVIDENCE",
            )
        },
        "GR-08": frozenset({"capability", "classification"}),
        "GR-09-REPLAY": frozenset({"deterministic"}),
        "GR-09-SEQUENCE": frozenset({"rejected"}),
        "GR-09-IDENTITY": frozenset(
            {"conflict_rejected", "exact_duplicate_idempotent"}
        ),
        **{
            case_id: frozenset({"reasons", "state"})
            for case_id in (
                "GR-10-FUTURE",
                "GR-10-UNAVAILABLE",
                "GR-10-SAME-INSTANT",
            )
        },
    }
)
_COST_VECTORS: Final = MappingProxyType(
    {
        "EC0": ("0", "0", "0", "0", "0"),
        "EC1": ("1", "2", "3", "4", "5"),
        "EC2": ("2", "4", "6", "8", "10"),
        "EC3": ("5", "10", "15", "20", "25"),
    }
)


class GoldenReferenceError(ValueError):
    """The golden reference or assurance request is invalid."""


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    NOT_EXECUTED = "NOT_EXECUTED"


class AggregateState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


@dataclass(frozen=True, slots=True)
class GoldenFixture:
    case_id: str
    family: str
    operation: str
    inputs: Mapping[str, object]
    expected: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GoldenReference:
    contract_version: int
    approval_reference: str
    approval_command_sha256: str
    reference_digest: str
    file_sha256: str
    fixtures: tuple[GoldenFixture, ...]


@dataclass(frozen=True, slots=True)
class OracleApprovalEvidence:
    source_url: str
    comment_id: int
    actor: str
    approved_at: str
    task_id: str
    approval_reference: str
    command_sha256: str
    reference_digest: str
    file_sha256: str


TRUSTED_OWNER_APPROVAL: Final = OracleApprovalEvidence(
    "https://github.com/KiloAlpha021/automated-trading-bot/issues/100#issuecomment-6302924690",
    6302924690,
    "KiloAlpha21",
    "2026-10-20T22:11:29Z",
    "ATIS-S4-07-GOLDEN-REFERENCE-ASSURANCE-001",
    "ATIS-S4-07 owner-approved implementation command and numerical oracles",
    "edc134874b460aafde0576854631ee2b329498b84fa1e3e4884cc0fa8a5efa5f",
    "sha256:f30b05861e95dd44b504904e5c22015be4589382f2d4192d223df57503f973b5",
    "52975ee609dd8bee9b39432e2146e0832c70e4022aed1d17e23c7941e03728d5",
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    verdict: Verdict
    evidence: tuple[str, ...]
    differences: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssuranceReport:
    contract_id: str
    reference_digest: str
    cases: tuple[CaseResult, ...]
    supported_scope_assurance: AggregateState
    full_stage4_scenario_coverage: AggregateState
    reference_integrity: AggregateState
    unsupported_count: int
    not_executed_count: int
    authority: str = ASSURANCE_AUTHORITY


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise GoldenReferenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _deep_freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType(
            {key: _deep_freeze(child) for key, child in value.items()}
        )
    if type(value) is list:
        return tuple(_deep_freeze(child) for child in value)
    return value


def _require_deeply_immutable(value: object, name: str) -> None:
    if type(value) is MappingProxyType:
        for key, child in value.items():
            if type(key) is not str:
                raise GoldenReferenceError(f"{name} keys must be strings")
            _require_deeply_immutable(child, f"{name}.{key}")
        return
    if type(value) is tuple:
        for index, child in enumerate(value):
            _require_deeply_immutable(child, f"{name}[{index}]")
        return
    if type(value) not in {str, int, bool, type(None)}:
        raise GoldenReferenceError(f"{name} is not deeply immutable")


def _owned_freeze(value: object) -> object:
    if type(value) is MappingProxyType:
        return MappingProxyType(
            {key: _owned_freeze(child) for key, child in value.items()}
        )
    if type(value) is tuple:
        return tuple(_owned_freeze(child) for child in value)
    return value


def _thaw(value: object) -> object:
    if type(value) is MappingProxyType:
        return {key: _thaw(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_thaw(child) for child in value]
    return value


def _reject_float(value: str) -> object:
    raise GoldenReferenceError(f"JSON float is prohibited: {value}")


def _reject_constant(value: str) -> object:
    raise GoldenReferenceError(f"nonfinite JSON value: {value}")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(document: Mapping[str, object]) -> str:
    body = dict(document)
    body.pop("reference_digest", None)
    return "sha256:" + sha256(_canonical(body)).hexdigest()


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise GoldenReferenceError(f"{name} must be a canonical Decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or (parsed.is_zero() and value.startswith("-")):
        raise GoldenReferenceError(f"{name} must be finite")
    return parsed


def _validate_decimal_values(value: object, path: str = "reference") -> None:
    if type(value) is dict:
        for key, child in value.items():
            if key in _MONEY_KEYS:
                _decimal(child, f"{path}.{key}")
            else:
                _validate_decimal_values(child, f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _validate_decimal_values(child, f"{path}[{index}]")


def _exact_keys(
    value: Mapping[str, object], required: set[str] | frozenset[str], name: str
) -> None:
    if set(value) != required:
        raise GoldenReferenceError(f"{name} keys mismatch")


def _validate_timestamp_text(value: object, name: str) -> None:
    if type(value) is not str or _CANONICAL_TIMESTAMP.fullmatch(value) is None:
        raise GoldenReferenceError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise GoldenReferenceError(f"{name} must be canonical UTC") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise GoldenReferenceError(f"{name} must be canonical UTC")


def _timestamp_datetime(value: object, name: str) -> datetime:
    _validate_timestamp_text(value, name)
    assert isinstance(value, str)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _validate_fixture_inputs(case_id: str, inputs: Mapping[str, object]) -> None:
    common = {"currency", "research_run_id", "evidence_digest"}
    financial = common | {"opening_cash", "transactions"}
    if case_id.startswith(("GR-01", "GR-02", "GR-03", "GR-04", "GR-05")):
        expected = set(financial)
        if case_id in {"GR-02", "GR-03"}:
            expected |= {"corporate_action", "mark"}
        elif case_id == "GR-04":
            expected.add("mark")
        elif case_id.startswith("GR-05"):
            expected |= {"cost_scenario", "cost_bps", "mark"}
        _exact_keys(inputs, expected, case_id)
        if inputs["currency"] != "USD":
            raise GoldenReferenceError("fixture currency must be USD")
        ResearchRunId(_input_string(inputs["research_run_id"], "research_run_id"))
        EvidenceContentDigest(
            _input_string(inputs["evidence_digest"], "evidence_digest")
        )
        if _decimal(inputs["opening_cash"], "opening_cash") < 0:
            raise GoldenReferenceError("opening_cash must be non-negative")
        transactions = _input_list(inputs["transactions"], "transactions")
        if not transactions:
            raise GoldenReferenceError("transactions must be non-empty")
        previous_minute = 0
        for item in transactions:
            transaction = _input_mapping(item, "transaction")
            _exact_keys(
                transaction,
                {"instrument", "side", "quantity", "price", "release_minute"},
                "transaction",
            )
            _instrument(transaction["instrument"])
            OrderSide(_input_string(transaction["side"], "side"))
            if (
                _decimal(transaction["quantity"], "quantity") <= 0
                or _decimal(transaction["price"], "price") <= 0
            ):
                raise GoldenReferenceError(
                    "transaction quantity and price must be positive"
                )
            minute = transaction["release_minute"]
            if (
                type(minute) is not int
                or not 1 <= minute <= 59
                or minute <= previous_minute
            ):
                raise GoldenReferenceError(
                    "release minutes must be in 1..59 and strictly increasing"
                )
            previous_minute = minute
        instruments = tuple(
            _input_string(_input_mapping(item, "transaction")["instrument"], "instrument")
            for item in transactions
        )
        sides = tuple(
            _input_string(_input_mapping(item, "transaction")["side"], "side")
            for item in transactions
        )
        if case_id == "GR-01A" and (
            len(transactions) != 2
            or len(set(instruments)) != 1
            or sides != ("BUY", "SELL")
        ):
            raise GoldenReferenceError("GR-01A transaction pattern mismatch")
        if case_id == "GR-01B" and (
            len(transactions) != 3
            or len(set(instruments)) != 1
            or sides != ("BUY", "BUY", "SELL")
        ):
            raise GoldenReferenceError("GR-01B transaction pattern mismatch")
        if "mark" in inputs:
            mark = _input_mapping(inputs["mark"], "mark")
            _exact_keys(mark, {"instrument", "price"}, "mark")
            _instrument(mark["instrument"])
            if _decimal(mark["price"], "mark.price") <= 0:
                raise GoldenReferenceError("mark price must be positive")
        if "cost_bps" in inputs:
            rates = _input_list(inputs["cost_bps"], "cost_bps")
            if len(rates) != 5 or any(_decimal(item, "cost_bps") < 0 for item in rates):
                raise GoldenReferenceError(
                    "cost_bps must contain five non-negative values"
                )
            if inputs["cost_scenario"] != case_id.removeprefix("GR-05-"):
                raise GoldenReferenceError("cost scenario mismatch")
            if tuple(rates) != _COST_VECTORS[inputs["cost_scenario"]]:
                raise GoldenReferenceError("cost vector mismatch")
        if "corporate_action" in inputs:
            action = _input_mapping(inputs["corporate_action"], "corporate_action")
            if case_id == "GR-02":
                _exact_keys(action, {"kind", "unit_amount"}, "corporate_action")
                if (
                    action["kind"] != "CASH_DIVIDEND"
                    or _decimal(action["unit_amount"], "unit_amount") <= 0
                ):
                    raise GoldenReferenceError("dividend fixture mismatch")
            else:
                _exact_keys(action, {"kind", "quantity"}, "corporate_action")
                if (
                    action["kind"] != "SPLIT"
                    or _decimal(action["quantity"], "quantity") <= 0
                ):
                    raise GoldenReferenceError("split fixture mismatch")
        if case_id in {"GR-02", "GR-03"}:
            mark = _input_mapping(inputs["mark"], "mark")
            if (
                len(transactions) != 1
                or sides != ("BUY",)
                or mark["instrument"] != instruments[0]
            ):
                raise GoldenReferenceError("corporate-action transaction mismatch")
        if case_id == "GR-04":
            mark = _input_mapping(inputs["mark"], "mark")
            if (
                len(transactions) != 3
                or sides != ("BUY", "BUY", "SELL")
                or instruments[0] != instruments[2]
                or instruments[0] == instruments[1]
                or mark["instrument"] != instruments[0]
            ):
                raise GoldenReferenceError("GR-04 transaction pattern mismatch")
        if case_id.startswith("GR-05"):
            mark = _input_mapping(inputs["mark"], "mark")
            if (
                len(transactions) != 1
                or sides != ("BUY",)
                or mark["instrument"] != instruments[0]
            ):
                raise GoldenReferenceError("GR-05 transaction pattern mismatch")
        return
    if case_id == "GR-06":
        _exact_keys(
            inputs,
            common | {"quantity", "eligibility_state", "eligibility_reasons"},
            case_id,
        )
        if (
            EligibilityState(
                _input_string(inputs["eligibility_state"], "eligibility_state")
            )
            is not EligibilityState.INELIGIBLE
        ):
            raise GoldenReferenceError("GR-06 must be ineligible")
        reasons = _input_list(inputs["eligibility_reasons"], "eligibility_reasons")
        if not reasons or any(
            re.fullmatch(r"[A-Z][A-Z0-9_]*", _input_string(reason, "eligibility_reason"))
            is None
            for reason in reasons
        ) or len(set(reasons)) != len(reasons):
            raise GoldenReferenceError("eligibility reasons must be nonblank")
    elif case_id.startswith("GR-07"):
        _exact_keys(inputs, common | {"quantity", "price", "failure"}, case_id)
        if inputs["failure"] != case_id.removeprefix("GR-07-"):
            raise GoldenReferenceError("execution failure mismatch")
    elif case_id == "GR-08":
        _exact_keys(inputs, {"capability"}, case_id)
        if inputs["capability"] != "TERMINAL_SECURITY_DELISTING":
            raise GoldenReferenceError("delisting capability mismatch")
        return
    elif case_id.startswith("GR-09"):
        _exact_keys(inputs, {"mode", "opening_cash"}, case_id)
        if inputs["mode"] != CASE_CATALOGUE[case_id][1]:
            raise GoldenReferenceError("fixture mode mismatch")
        if _decimal(inputs["opening_cash"], "opening_cash") < 0:
            raise GoldenReferenceError("opening_cash must be non-negative")
        return
    else:
        _exact_keys(
            inputs,
            common
            | {"mode", "decision_time", "event_time", "knowledge_time", "input_state"},
            case_id,
        )
        if inputs["mode"] != CASE_CATALOGUE[case_id][1]:
            raise GoldenReferenceError("fixture mode mismatch")
        decision = _timestamp_datetime(inputs["decision_time"], "decision_time")
        event = _timestamp_datetime(inputs["event_time"], "event_time")
        knowledge = _timestamp_datetime(inputs["knowledge_time"], "knowledge_time")
        state = InputState(_input_string(inputs["input_state"], "input_state"))
        if case_id == "GR-10-FUTURE" and not (
            state is InputState.VALID and event <= decision < knowledge
        ):
            raise GoldenReferenceError("future-knowledge chronology mismatch")
        if case_id == "GR-10-UNAVAILABLE" and not (
            state is InputState.MISSING and event <= knowledge <= decision
        ):
            raise GoldenReferenceError("unavailable-input chronology mismatch")
        if case_id == "GR-10-SAME-INSTANT" and not (
            state is InputState.VALID and event == knowledge == decision
        ):
            raise GoldenReferenceError("same-instant chronology mismatch")
    if inputs.get("currency") != "USD":
        raise GoldenReferenceError("fixture currency must be USD")
    ResearchRunId(_input_string(inputs.get("research_run_id"), "research_run_id"))
    EvidenceContentDigest(
        _input_string(inputs.get("evidence_digest"), "evidence_digest")
    )
    if "quantity" in inputs and _decimal(inputs["quantity"], "quantity") <= 0:
        raise GoldenReferenceError("quantity must be positive")
    if "price" in inputs and _decimal(inputs["price"], "price") <= 0:
        raise GoldenReferenceError("price must be positive")


def _validate_expected(case_id: str, expected: Mapping[str, object]) -> None:
    _exact_keys(expected, _EXPECTED_KEYS[case_id], f"{case_id}.expected")
    boolean_keys = {
        "conflict_rejected",
        "deterministic",
        "exact_duplicate_idempotent",
        "rejected",
    }
    count_keys = {"attempt_count", "event_count", "fill_count", "order_count"}
    string_keys = {"capability", "classification", "state", "status"}
    for key, value in expected.items():
        if key in _MONEY_KEYS:
            _decimal(value, f"{case_id}.expected.{key}")
        elif key in boolean_keys:
            if type(value) is not bool:
                raise GoldenReferenceError(f"{case_id}.expected.{key} must be boolean")
        elif key in count_keys:
            if type(value) is not int or value < 0:
                raise GoldenReferenceError(
                    f"{case_id}.expected.{key} must be a non-negative integer"
                )
        elif key == "reasons":
            reasons = _input_list(value, f"{case_id}.expected.reasons")
            if not reasons or any(
                re.fullmatch(r"[A-Z][A-Z0-9_]*", _input_string(item, "reason"))
                is None
                for item in reasons
            ):
                raise GoldenReferenceError(
                    f"{case_id}.expected.reasons must be canonical"
                )
        elif key in string_keys:
            _input_string(value, f"{case_id}.expected.{key}")
        else:
            raise GoldenReferenceError(f"unsupported expected field: {key}")


def _validate_reference_structure(reference: GoldenReference) -> None:
    if type(reference) is not GoldenReference:
        raise GoldenReferenceError("reference must be a GoldenReference")
    if reference.contract_version != 1:
        raise GoldenReferenceError("contract version mismatch")
    if not reference.approval_reference:
        raise GoldenReferenceError("approval reference is required")
    if re.fullmatch(r"[0-9a-f]{64}", reference.approval_command_sha256) is None:
        raise GoldenReferenceError("approval command digest is invalid")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", reference.reference_digest) is None:
        raise GoldenReferenceError("reference digest is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", reference.file_sha256) is None:
        raise GoldenReferenceError("reference file digest is invalid")
    if type(reference.fixtures) is not tuple:
        raise GoldenReferenceError("fixtures must be an immutable tuple")
    seen: set[str] = set()
    for fixture in reference.fixtures:
        if type(fixture) is not GoldenFixture:
            raise GoldenReferenceError("fixture type is invalid")
        if fixture.case_id not in CASE_CATALOGUE or fixture.case_id in seen:
            raise GoldenReferenceError("fixture identity is invalid or duplicated")
        if (fixture.family, fixture.operation) != CASE_CATALOGUE[fixture.case_id]:
            raise GoldenReferenceError("fixture metadata does not match case identity")
        if type(fixture.inputs) is not MappingProxyType or type(
            fixture.expected
        ) is not MappingProxyType:
            raise GoldenReferenceError("fixture mappings must be immutable")
        _require_deeply_immutable(fixture.inputs, f"{fixture.case_id}.inputs")
        _require_deeply_immutable(fixture.expected, f"{fixture.case_id}.expected")
        _validate_fixture_inputs(fixture.case_id, fixture.inputs)
        _validate_expected(fixture.case_id, fixture.expected)
        seen.add(fixture.case_id)
    if seen != set(MANDATORY_CASES):
        raise GoldenReferenceError("mandatory fixture set mismatch")


def _reference_document(reference: GoldenReference) -> dict[str, object]:
    return {
        "approval_verification": {
            "fixture_metadata_authenticates_approval": False,
            "required_external_binding": [
                "approval_reference",
                "command_sha256",
                "reference_digest",
            ],
            "unverified_semantics": "NOT_ESTABLISHED",
        },
        "authority": ASSURANCE_AUTHORITY,
        "canonicalization": {
            "decimal_strings": (
                "plain notation; zero is 0; redundant trailing fractional zeros "
                "prohibited"
            ),
            "digest_excludes": ["reference_digest"],
            "encoding": "UTF-8",
            "json": "sorted keys and compact separators",
            "timestamps": "YYYY-MM-DDTHH:MM:SS.ffffffZ",
        },
        "contract_id": CONTRACT_ID,
        "contract_version": reference.contract_version,
        "decimal_context": {
            "capitals": 1,
            "clamp": 0,
            "emax": 999999,
            "emin": -999999,
            "precision": 28,
            "rounding": "ROUND_HALF_EVEN",
            "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
        },
        "fixtures": [
            {
                "case_id": fixture.case_id,
                "expected": _thaw(fixture.expected),
                "family": fixture.family,
                "inputs": _thaw(fixture.inputs),
                "operation": fixture.operation,
            }
            for fixture in reference.fixtures
        ],
        "independent_approval": {
            "approved_by": "ATIS_OWNER",
            "command_sha256": reference.approval_command_sha256,
            "reference": reference.approval_reference,
            "status": "APPROVED",
            "version": 1,
        },
    }


def _validate_reference(reference: GoldenReference) -> GoldenReference:
    _validate_reference_structure(reference)
    fixtures: list[GoldenFixture] = []
    for fixture in reference.fixtures:
        owned_inputs = _owned_freeze(fixture.inputs)
        owned_expected = _owned_freeze(fixture.expected)
        if not isinstance(owned_inputs, Mapping) or not isinstance(
            owned_expected, Mapping
        ):
            raise GoldenReferenceError("fixture snapshot failed")
        fixtures.append(
            GoldenFixture(
                fixture.case_id,
                fixture.family,
                fixture.operation,
                owned_inputs,
                owned_expected,
            )
        )
    snapshot = GoldenReference(
        reference.contract_version,
        reference.approval_reference,
        reference.approval_command_sha256,
        reference.reference_digest,
        reference.file_sha256,
        tuple(fixtures),
    )
    _validate_reference_structure(snapshot)
    document = _reference_document(snapshot)
    digest = "sha256:" + sha256(_canonical(document)).hexdigest()
    document["reference_digest"] = digest
    file_sha256 = sha256(_canonical(document) + b"\n").hexdigest()
    if digest != snapshot.reference_digest:
        raise GoldenReferenceError("reconstructed reference digest mismatch")
    if file_sha256 != snapshot.file_sha256:
        raise GoldenReferenceError("reconstructed reference file digest mismatch")
    return snapshot


def _expected_context() -> Context:
    context = Context(
        prec=28,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )
    context.traps[InvalidOperation] = True
    context.traps[DivisionByZero] = True
    context.traps[Overflow] = True
    context.traps[Underflow] = False
    context.traps[Clamped] = False
    return context


def decimal_context_compatible() -> bool:
    current = getcontext()
    expected = _expected_context()
    return (
        current.prec == expected.prec
        and current.rounding == expected.rounding
        and current.Emin == expected.Emin
        and current.Emax == expected.Emax
        and current.capitals == expected.capitals
        and current.clamp == expected.clamp
        and dict(current.traps) == dict(expected.traps)
    )


def load_reference_bytes(raw: bytes) -> GoldenReference:
    if type(raw) is not bytes:
        raise TypeError("raw must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GoldenReferenceError("reference must be UTF-8") from error
    try:
        document = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise GoldenReferenceError("reference is not valid JSON") from error
    if type(document) is not dict:
        raise GoldenReferenceError("reference root must be an object")
    canonical_file = _canonical(document) + b"\n"
    if raw != canonical_file:
        raise GoldenReferenceError("reference file is not canonical JSON")
    if document.get("contract_id") != CONTRACT_ID:
        raise GoldenReferenceError("contract identity mismatch")
    version = document.get("contract_version")
    if type(version) is not int or version < 1:
        raise GoldenReferenceError("contract_version must be a positive integer")
    approval = document.get("independent_approval")
    if (
        type(approval) is not dict
        or type(approval.get("reference")) is not str
        or not approval["reference"]
    ):
        raise GoldenReferenceError("independent oracle approval is required")
    if approval.get("status") != "APPROVED" or approval.get("version") != 1:
        raise GoldenReferenceError("independent oracle approval is invalid")
    command_sha256 = approval.get("command_sha256")
    if (
        type(command_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", command_sha256) is None
    ):
        raise GoldenReferenceError("approval command digest is invalid")
    context = document.get("decimal_context")
    required_context = {
        "capitals": 1,
        "clamp": 0,
        "emax": -(-999999),
        "emin": -999999,
        "precision": 28,
        "rounding": "ROUND_HALF_EVEN",
        "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
    }
    if context != required_context:
        raise GoldenReferenceError("Decimal context declaration mismatch")
    digest = document.get("reference_digest")
    if type(digest) is not str or digest != _digest(document):
        raise GoldenReferenceError("reference digest mismatch")
    fixtures_value = document.get("fixtures")
    if type(fixtures_value) is not list:
        raise GoldenReferenceError("fixtures must be an array")
    fixtures: list[GoldenFixture] = []
    seen: set[str] = set()
    for value in fixtures_value:
        if type(value) is not dict:
            raise GoldenReferenceError("fixture must be an object")
        case_id = value.get("case_id")
        family = value.get("family")
        operation = value.get("operation")
        inputs = value.get("inputs")
        expected = value.get("expected")
        if type(case_id) is not str or case_id not in CASE_CATALOGUE:
            raise GoldenReferenceError("fixture case_id is not canonical")
        if case_id in seen:
            raise GoldenReferenceError(f"duplicate fixture ID: {case_id}")
        if type(family) is not str or type(operation) is not str:
            raise GoldenReferenceError("fixture family and operation are required")
        if (family, operation) != CASE_CATALOGUE[case_id]:
            raise GoldenReferenceError("fixture metadata does not match case identity")
        if type(inputs) is not dict or type(expected) is not dict:
            raise GoldenReferenceError(
                "fixture inputs and expected values are required"
            )
        if not inputs:
            raise GoldenReferenceError("fixture inputs must be reproducible")
        _validate_fixture_inputs(case_id, inputs)
        frozen_inputs = _deep_freeze(inputs)
        frozen_expected = _deep_freeze(expected)
        if not isinstance(frozen_inputs, Mapping) or not isinstance(
            frozen_expected, Mapping
        ):
            raise GoldenReferenceError("fixture freeze failed")
        seen.add(case_id)
        fixtures.append(
            GoldenFixture(
                case_id,
                family,
                operation,
                frozen_inputs,
                frozen_expected,
            )
        )
    if tuple(sorted(seen)) != tuple(sorted(MANDATORY_CASES)):
        missing = sorted(set(MANDATORY_CASES) - seen)
        extra = sorted(seen - set(MANDATORY_CASES))
        raise GoldenReferenceError(
            f"mandatory fixture mismatch: missing={missing}, extra={extra}"
        )
    _validate_decimal_values(document)
    reference = GoldenReference(
        version,
        approval["reference"],
        command_sha256,
        digest,
        sha256(raw).hexdigest(),
        tuple(fixtures),
    )
    return _validate_reference(reference)


def load_reference(path: Path) -> GoldenReference:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path")
    return load_reference_bytes(path.read_bytes())


USD = Currency("USD")
RUN = ResearchRunId("atis:research:run:v1:sha256:" + "2" * 64)
DIGEST = EvidenceContentDigest("sha256:" + "1" * 64)
INSTRUMENT_A = InstrumentId(UUID("12345678-1234-4234-8234-123456789abc"))
INSTRUMENT_B = InstrumentId(UUID("22345678-1234-4234-8234-123456789abc"))


def _time(hour: int, minute: int = 0) -> Timestamp:
    return Timestamp(datetime(2026, 1, 5, hour, minute, tzinfo=timezone.utc))


def _eligibility(
    state: EligibilityState = EligibilityState.ELIGIBLE,
    reasons: tuple[str, ...] = (),
    minute: int = 0,
) -> DecisionInputEligibility:
    return DecisionInputEligibility(
        state,
        reasons,
        f"atis:research:feature-observation:v1:sha256:{minute:064x}",
        _time(10, minute),
        ClaimCeiling.ECONOMIC_VALIDATION,
    )


def _model(scenario: CostScenario, rates: tuple[str, ...]) -> ExecutionCostModel:
    values = tuple(
        CostRate(component, Decimal(rate), provenance, DIGEST)
        for component, rate, provenance in zip(
            CostComponent,
            rates,
            (
                CostProvenance.MODELLED,
                CostProvenance.MODELLED,
                CostProvenance.MODELLED,
                CostProvenance.MODELLED,
                CostProvenance.PROXY,
            ),
            strict=True,
        )
    )
    return ExecutionCostModel(
        f"atis:simulation-cost-model:v1:{scenario.value.lower()}",
        scenario,
        values,
        scenario is CostScenario.EC0,
    )


def _opening(cash: Decimal = Decimal("1000")) -> SimulationLedger:
    event = FinancialEvent(
        LedgerEventId("atis:simulation-ledger-event:v1:opening"),
        1,
        _time(9),
        FinancialEventKind.OPENING_CASH,
        RUN,
        DIGEST,
        currency=USD,
        cash_delta=cash,
    )
    return SimulationLedger.empty(USD).apply(event)


def _trade(
    oms: SimulatedOms,
    ledger: SimulationLedger,
    *,
    instrument: InstrumentId,
    side: OrderSide,
    quantity: str,
    price: str,
    minute: int,
    model: ExecutionCostModel,
) -> tuple[SimulatedOms, SimulationLedger]:
    eligibility = _eligibility(minute=minute)
    order = create_order(
        instrument_id=instrument,
        side=side,
        quantity=Decimal(quantity),
        currency=USD,
        release_at=_time(11, minute),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=eligibility,
    )
    oms = oms.submit(order)
    snapshot = ExecutionSnapshot(
        _time(13, minute),
        Decimal(price),
        _time(12, minute),
        ExecutionInputState.AVAILABLE,
        ExecutionInputState.AVAILABLE,
        Decimal(quantity),
        USD,
        DIGEST,
    )
    oms = oms.execute(order.order_id, Decimal(quantity), snapshot, model)
    return oms, post_fill_to_ledger(ledger, oms.fills[-1])


def _mark(
    ledger: SimulationLedger, instrument: InstrumentId, price: str
) -> SimulationLedger:
    return ledger.apply(
        FinancialEvent(
            LedgerEventId(
                f"atis:simulation-ledger-event:v1:mark-{len(ledger.events) + 1}"
            ),
            len(ledger.events) + 1,
            _time(14),
            FinancialEventKind.MARK,
            RUN,
            DIGEST,
            currency=USD,
            instrument_id=instrument,
            unit_amount=Decimal(price),
        )
    )


def _input_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GoldenReferenceError(f"{name} must be an object")
    return value


def _input_string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise GoldenReferenceError(f"{name} must be a non-empty string")
    return value


def _input_list(value: object, name: str) -> tuple[object, ...]:
    if isinstance(value, list):
        if type(value) is not list:
            raise GoldenReferenceError(f"{name} must be an array")
        return tuple(value)
    if isinstance(value, tuple):
        if type(value) is not tuple:
            raise GoldenReferenceError(f"{name} must be an array")
        return value
    raise GoldenReferenceError(f"{name} must be an array")


def _instrument(value: object) -> InstrumentId:
    name = _input_string(value, "instrument")
    if name == "A":
        return INSTRUMENT_A
    if name == "B":
        return INSTRUMENT_B
    raise GoldenReferenceError("instrument must be A or B")


def _financial_actual(fixture: GoldenFixture) -> dict[str, object]:
    inputs = fixture.inputs
    if inputs.get("currency") != "USD" or inputs.get("research_run_id") != RUN.value:
        raise GoldenReferenceError("financial fixture attribution mismatch")
    if inputs.get("evidence_digest") != DIGEST.value:
        raise GoldenReferenceError("financial fixture evidence mismatch")
    ledger = _opening(_decimal(inputs.get("opening_cash"), "opening_cash"))
    oms = SimulatedOms()
    scenario_name = inputs.get("cost_scenario", "EC0")
    if type(scenario_name) is not str:
        raise GoldenReferenceError("cost_scenario must be a string")
    scenario = CostScenario(scenario_name)
    raw_rates = inputs.get("cost_bps", ["0", "0", "0", "0", "0"])
    rates = tuple(
        _input_string(item, "cost_bps") for item in _input_list(raw_rates, "cost_bps")
    )
    if len(rates) != 5:
        raise GoldenReferenceError("cost_bps must contain five components")
    for index, rate in enumerate(rates):
        _decimal(rate, f"cost_bps[{index}]")
    model = _model(scenario, rates)
    transactions = _input_list(inputs.get("transactions"), "transactions")
    for raw_transaction in transactions:
        transaction = _input_mapping(raw_transaction, "transaction")
        minute = transaction.get("release_minute")
        if type(minute) is not int or minute < 1:
            raise GoldenReferenceError("release_minute must be a positive integer")
        oms, ledger = _trade(
            oms,
            ledger,
            instrument=_instrument(transaction.get("instrument")),
            side=OrderSide(_input_string(transaction.get("side"), "side")),
            quantity=_input_string(transaction.get("quantity"), "quantity"),
            price=_input_string(transaction.get("price"), "price"),
            minute=minute,
            model=model,
        )
    raw_action = inputs.get("corporate_action")
    if raw_action is not None:
        action = _input_mapping(raw_action, "corporate_action")
        kind = _input_string(action.get("kind"), "corporate_action.kind")
        if kind == "CASH_DIVIDEND":
            ledger = ledger.apply(
                FinancialEvent(
                    LedgerEventId("atis:simulation-ledger-event:v1:dividend"),
                    len(ledger.events) + 1,
                    _time(14),
                    FinancialEventKind.CASH_DIVIDEND,
                    RUN,
                    DIGEST,
                    currency=USD,
                    instrument_id=INSTRUMENT_A,
                    unit_amount=_decimal(action.get("unit_amount"), "unit_amount"),
                    corporate_action_version_id=ActionVersionId.from_digest(DIGEST),
                )
            )
        elif kind == "SPLIT":
            ledger = ledger.apply(
                FinancialEvent(
                    LedgerEventId("atis:simulation-ledger-event:v1:split"),
                    len(ledger.events) + 1,
                    _time(14),
                    FinancialEventKind.SPLIT,
                    RUN,
                    DIGEST,
                    instrument_id=INSTRUMENT_A,
                    quantity=_decimal(action.get("quantity"), "quantity"),
                    corporate_action_version_id=ActionVersionId.from_digest(DIGEST),
                )
            )
        else:
            raise GoldenReferenceError("unsupported corporate action fixture")
    raw_mark = inputs.get("mark")
    if raw_mark is not None:
        mark = _input_mapping(raw_mark, "mark")
        ledger = _mark(
            ledger,
            _instrument(mark.get("instrument")),
            _input_string(mark.get("price"), "mark.price"),
        )
    result = reconcile(oms, ledger)
    if result.state is not ReconciliationState.RECONCILED or result.performance is None:
        raise GoldenReferenceError(f"{fixture.case_id} did not reconcile")
    position_a = ledger.state.position(INSTRUMENT_A)
    position_b = ledger.state.position(INSTRUMENT_B)
    costs = result.performance.execution_cost_drag
    actual: dict[str, object] = {
        "cash": ledger.state.cash,
        "realised_pnl": ledger.state.realised_pnl,
        "unrealised_pnl": ledger.state.unrealised_pnl,
        "portfolio_value": ledger.state.portfolio_value,
        "net_pnl": result.performance.net_pnl,
        "gross_pnl": result.performance.gross_pnl,
        "turnover": result.performance.turnover,
        "event_count": len(ledger.events),
        "order_count": len(oms.orders),
        "attempt_count": len(oms.attempts),
        "fill_count": len(oms.fills),
        "commission_fees": costs.commission_fees,
        "bid_ask_spread": costs.bid_ask_spread,
        "slippage": costs.slippage,
        "market_impact": costs.market_impact,
        "additional_friction": costs.additional_friction,
        "total_cost": costs.total,
    }
    if position_a is not None:
        actual.update({"quantity": position_a.quantity, "basis": position_a.cost_basis})
    if position_b is not None:
        actual.update(
            {"b_quantity": position_b.quantity, "b_basis": position_b.cost_basis}
        )
    return actual


def _execution_failure(fixture: GoldenFixture) -> dict[str, object]:
    inputs = fixture.inputs
    quantity = _decimal(inputs.get("quantity"), "quantity")
    if inputs.get("currency") != "USD" or inputs.get("research_run_id") != RUN.value:
        raise GoldenReferenceError("execution fixture attribution mismatch")
    if inputs.get("evidence_digest") != DIGEST.value:
        raise GoldenReferenceError("execution fixture evidence mismatch")
    if fixture.case_id == "GR-06":
        raw_reasons = _input_list(
            inputs.get("eligibility_reasons"), "eligibility_reasons"
        )
        reasons = tuple(
            _input_string(item, "eligibility_reason") for item in raw_reasons
        )
        state = EligibilityState(
            _input_string(inputs.get("eligibility_state"), "eligibility_state")
        )
        rejected = create_order(
            instrument_id=INSTRUMENT_A,
            side=OrderSide.BUY,
            quantity=quantity,
            currency=USD,
            release_at=_time(11),
            research_run_id=RUN,
            evidence_digest=DIGEST,
            eligibility=_eligibility(state, reasons),
        )
        oms = SimulatedOms().submit(rejected)
        record = next(
            item for item in oms.orders if item.order.order_id == rejected.order_id
        )
        return {
            "status": record.status.value,
            "attempt_count": len(oms.attempts),
            "fill_count": len(oms.fills),
        }
    order = create_order(
        instrument_id=INSTRUMENT_A,
        side=OrderSide.BUY,
        quantity=quantity,
        currency=USD,
        release_at=_time(11),
        research_run_id=RUN,
        evidence_digest=DIGEST,
        eligibility=_eligibility(),
    )
    oms = SimulatedOms().submit(order)
    failure = _input_string(inputs.get("failure"), "failure")
    price: Decimal | None = _decimal(inputs.get("price"), "price")
    price_available_at: Timestamp | None = _time(12)
    security_state = ExecutionInputState.AVAILABLE
    liquidity_state = ExecutionInputState.AVAILABLE
    available_quantity = quantity
    evidence_digest: EvidenceContentDigest | None = DIGEST
    if failure == "PRICE":
        price = None
        price_available_at = None
    elif failure == "SECURITY":
        security_state = ExecutionInputState.UNAVAILABLE
    elif failure == "LIQUIDITY":
        liquidity_state = ExecutionInputState.UNAVAILABLE
        available_quantity = Decimal("0")
    elif failure == "EVIDENCE":
        evidence_digest = None
    else:
        raise GoldenReferenceError("unknown execution failure fixture")
    oms = oms.execute(
        order.order_id,
        quantity,
        ExecutionSnapshot(
            _time(13),
            price,
            price_available_at,
            security_state,
            liquidity_state,
            available_quantity,
            USD,
            evidence_digest,
        ),
        _model(CostScenario.EC0, ("0",) * 5),
    )
    return {
        "status": oms.orders[0].status.value,
        "attempt_count": len(oms.attempts),
        "fill_count": len(oms.fills),
        "reasons": list(oms.attempts[0].reasons),
    }


def _fixture_time(value: object, name: str) -> Timestamp:
    text = _input_string(value, name)
    _validate_timestamp_text(text, name)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise GoldenReferenceError(f"{name} must be canonical UTC") from error
    return Timestamp(parsed)


def _pit_actual(fixture: GoldenFixture) -> dict[str, object]:
    inputs = fixture.inputs
    policy = QualificationPolicy("policy:atis/golden", "v1", "evidence:atis/golden")
    assessments = tuple(
        DimensionAssessment(name, AssessmentState.PASS, ("evidence:atis/golden",))
        for name in DIMENSIONS
    )
    g1 = qualify_dataset(
        dataset_id="dataset:atis/golden",
        dataset_version="v1",
        manifest_digest=DIGEST.value,
        provenance_digest=DIGEST.value,
        policy=policy,
        requested_level=QualificationLevel.G1,
        assessments=assessments,
        coverage=EligibleCoverage(date(2026, 1, 1), date(2026, 1, 31)),
    )
    qualification = qualify_dataset(
        dataset_id="dataset:atis/golden",
        dataset_version="v1",
        manifest_digest=DIGEST.value,
        provenance_digest=DIGEST.value,
        policy=policy,
        requested_level=QualificationLevel.G2,
        assessments=assessments,
        coverage=g1.coverage,
        predecessor=g1,
    )
    definition = FeatureDefinition.create(
        version="v1",
        algorithm_id="algorithm:atis/golden",
        configuration_digest=DIGEST.value,
        required_inputs=("close",),
        lookback=1,
        minimum_history=1,
        missing_data_policy=MissingDataPolicy.REJECT,
        availability_policy=AvailabilityPolicy.AT_KNOWLEDGE,
        output_semantics="decimal:return",
    )
    decision = _fixture_time(inputs.get("decision_time"), "decision_time")
    event = _fixture_time(inputs.get("event_time"), "event_time")
    knowledge = _fixture_time(inputs.get("knowledge_time"), "knowledge_time")
    state = InputState(_input_string(inputs.get("input_state"), "input_state"))
    pit_input = PitInput(
        "input:atis/golden",
        "close",
        qualification.dataset_id,
        "source:atis/golden",
        DIGEST.value,
        event,
        knowledge,
        state,
    )
    observation = create_feature_observation(
        definition=definition,
        instrument_id=INSTRUMENT_A,
        listing_id=ListingId(UUID("32345678-1234-4234-8234-123456789abc")),
        qualification=qualification,
        inputs=(pit_input,),
        available_at=decision,
        value_digest=DIGEST.value,
    )
    eligibility = assess_decision_input(
        definition=definition,
        observation=observation,
        qualification=qualification,
        inputs=(pit_input,),
        decision_time=decision,
        requested_claim=ClaimCeiling.EXPLORATORY_RESEARCH,
    )
    return {"state": eligibility.state.value, "reasons": list(eligibility.reasons)}


def _special_actual(fixture: GoldenFixture) -> dict[str, object]:
    inputs = fixture.inputs
    if fixture.case_id == "GR-08":
        return {
            "classification": Verdict.UNSUPPORTED_CAPABILITY.value,
            "capability": _input_string(inputs.get("capability"), "capability"),
        }
    mode = _input_string(inputs.get("mode"), "mode")
    if mode != fixture.operation:
        raise GoldenReferenceError("special fixture operation mismatch")
    opening_cash = _decimal(inputs.get("opening_cash"), "opening_cash")
    if fixture.case_id == "GR-09-REPLAY":
        ledger = _opening(opening_cash)
        return {"deterministic": SimulationLedger.replay(USD, ledger.events) == ledger}
    if fixture.case_id == "GR-09-SEQUENCE":
        bad = FinancialEvent(
            LedgerEventId("atis:simulation-ledger-event:v1:gap"),
            2,
            _time(9),
            FinancialEventKind.OPENING_CASH,
            RUN,
            DIGEST,
            currency=USD,
            cash_delta=opening_cash,
        )
        try:
            SimulationLedger.empty(USD).apply(bad)
        except LedgerContractError:
            return {"rejected": True}
        return {"rejected": False}
    ledger = _opening(opening_cash)
    conflict = FinancialEvent(
        ledger.events[0].event_id,
        1,
        _time(9),
        FinancialEventKind.OPENING_CASH,
        RUN,
        DIGEST,
        currency=USD,
        cash_delta=opening_cash - Decimal("1"),
    )
    try:
        ledger.apply(conflict)
    except LedgerContractError:
        return {
            "exact_duplicate_idempotent": ledger.apply(ledger.events[0]) == ledger,
            "conflict_rejected": True,
        }
    return {"exact_duplicate_idempotent": True, "conflict_rejected": False}


def _actual(fixture: GoldenFixture) -> dict[str, object]:
    case_id = fixture.case_id
    if (
        case_id.startswith("GR-01")
        or case_id in {"GR-02", "GR-03", "GR-04"}
        or case_id.startswith("GR-05")
    ):
        return _financial_actual(fixture)
    if case_id == "GR-06" or case_id.startswith("GR-07"):
        return _execution_failure(fixture)
    if case_id.startswith("GR-10"):
        return _pit_actual(fixture)
    return _special_actual(fixture)


def _compare(
    expected: Mapping[str, object], actual: Mapping[str, object]
) -> tuple[str, ...]:
    differences: list[str] = []
    for key, wanted in expected.items():
        found = actual.get(key)
        if key in _MONEY_KEYS:
            wanted = _decimal(wanted, key)
        elif key == "reasons":
            if isinstance(wanted, (list, tuple)) and all(
                type(reason) is str for reason in wanted
            ):
                wanted = tuple(wanted)
            if isinstance(found, (list, tuple)) and all(
                type(reason) is str for reason in found
            ):
                found = tuple(found)
        if found != wanted:
            differences.append(f"{key}: expected={wanted!r}, actual={found!r}")
    return tuple(differences)


def _approval_verified(
    reference: GoldenReference, approval: OracleApprovalEvidence | None
) -> bool:
    return (
        type(approval) is OracleApprovalEvidence
        and approval == TRUSTED_OWNER_APPROVAL
        and approval.source_url.endswith("issues/100#issuecomment-6302924690")
        and approval.comment_id == 6302924690
        and approval.actor == "KiloAlpha21"
        and approval.approved_at == "2026-10-20T22:11:29Z"
        and approval.task_id == "ATIS-S4-07-GOLDEN-REFERENCE-ASSURANCE-001"
        and approval.approval_reference == reference.approval_reference
        and approval.command_sha256 == reference.approval_command_sha256
        and approval.reference_digest == reference.reference_digest
        and approval.file_sha256 == reference.file_sha256
    )


def _invalid_reference_report(error: Exception) -> AssuranceReport:
    return AssuranceReport(
        CONTRACT_ID,
        "UNVERIFIED",
        (
            CaseResult(
                "REFERENCE",
                Verdict.INVALID_REFERENCE,
                (type(error).__name__,),
                (str(error)[:240],),
            ),
        ),
        AggregateState.NOT_ESTABLISHED,
        AggregateState.NOT_ESTABLISHED,
        AggregateState.FAIL,
        0,
        0,
    )


def evaluate(
    reference: GoldenReference,
    approval: OracleApprovalEvidence | None = None,
) -> AssuranceReport:
    try:
        reference = _validate_reference(reference)
    except Exception as error:
        return _invalid_reference_report(error)
    if not _approval_verified(reference, approval):
        cases = tuple(
            CaseResult(
                item.case_id,
                Verdict.NOT_EXECUTED,
                ("INDEPENDENT_APPROVAL_NOT_VERIFIED",),
            )
            for item in reference.fixtures
        )
        return AssuranceReport(
            CONTRACT_ID,
            reference.reference_digest,
            cases,
            AggregateState.NOT_ESTABLISHED,
            AggregateState.NOT_ESTABLISHED,
            AggregateState.PASS,
            0,
            len(cases),
        )
    if not decimal_context_compatible():
        cases = tuple(
            CaseResult(
                item.case_id, Verdict.NOT_EXECUTED, ("DECIMAL_CONTEXT_MISMATCH",)
            )
            for item in reference.fixtures
        )
        return AssuranceReport(
            CONTRACT_ID,
            reference.reference_digest,
            cases,
            AggregateState.NOT_ESTABLISHED,
            AggregateState.NOT_ESTABLISHED,
            AggregateState.PASS,
            0,
            len(cases),
        )
    results: list[CaseResult] = []
    with localcontext(_expected_context()):
        for fixture in reference.fixtures:
            try:
                actual = _actual(fixture)
                differences = _compare(fixture.expected, actual)
                if fixture.case_id == "GR-08" and not differences:
                    verdict = Verdict.UNSUPPORTED_CAPABILITY
                else:
                    verdict = Verdict.FAIL if differences else Verdict.PASS
                results.append(
                    CaseResult(
                        fixture.case_id, verdict, (fixture.operation,), differences
                    )
                )
            except Exception as error:
                results.append(
                    CaseResult(
                        fixture.case_id,
                        Verdict.FAIL,
                        (type(error).__name__,),
                        (str(error)[:240],),
                    )
                )
    cases = tuple(results)
    supported = tuple(
        item for item in cases if item.verdict is not Verdict.UNSUPPORTED_CAPABILITY
    )
    supported_state = (
        AggregateState.PASS
        if supported and all(item.verdict is Verdict.PASS for item in supported)
        else AggregateState.FAIL
    )
    full_state = (
        AggregateState.PASS
        if all(item.verdict is Verdict.PASS for item in cases)
        else AggregateState.NOT_ESTABLISHED
    )
    return AssuranceReport(
        CONTRACT_ID,
        reference.reference_digest,
        cases,
        supported_state,
        full_state,
        AggregateState.PASS,
        sum(item.verdict is Verdict.UNSUPPORTED_CAPABILITY for item in cases),
        sum(item.verdict is Verdict.NOT_EXECUTED for item in cases),
    )


def assure_bytes(
    raw: bytes,
    approval: OracleApprovalEvidence | None = None,
) -> AssuranceReport:
    try:
        reference = load_reference_bytes(raw)
    except Exception as error:
        return _invalid_reference_report(error)
    return evaluate(reference, approval)


def assure_path(
    path: Path,
    approval: OracleApprovalEvidence | None = None,
) -> AssuranceReport:
    try:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        raw = path.read_bytes()
    except Exception as error:
        return _invalid_reference_report(error)
    return assure_bytes(raw, approval)

"""Fail-closed, read-only reconciliation snapshots for Operator Console integration.

This module translates the existing Stage 4 research-simulation reconciliation
truth.  It does not reconcile, schedule work, open sockets, or confer authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Final

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceRef,
    canonicalize_evidence_refs,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.reconciliation_performance import (
    RECONCILIATION_AUTHORITY,
    DiscrepancyCode,
    PerformanceTruth,
    ReconciliationResult,
    ReconciliationState,
)


SCHEMA_VERSION: Final = "ATIS-OC-RECONCILIATION-SNAPSHOT-V1"
NO_TRADING_AUTHORITY: Final = "NO_TRADING_AUTHORITY"
_OBSERVATION_ID = re.compile(
    r"atis:operator-console:reconciliation-observation:v1:sha256:[0-9a-f]{64}",
    re.ASCII,
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)


class SnapshotValidationError(ValueError):
    """Snapshot trust could not be established."""


class FreshnessState(StrEnum):
    UNVERIFIED = "UNVERIFIED"


class AdapterReadState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _utc_text(value: Timestamp) -> str:
    return value.value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validated_timestamp(value: object, name: str) -> Timestamp:
    if type(value) is not Timestamp:
        raise SnapshotValidationError(f"{name} must be a Timestamp")
    instant = value.value
    if type(instant) is not datetime or instant.tzinfo is None:
        raise SnapshotValidationError(f"{name} must be a valid UTC timestamp")
    try:
        offset = instant.utcoffset()
    except (OverflowError, ValueError) as error:
        raise SnapshotValidationError(f"{name} must be a valid UTC timestamp") from error
    if offset != timedelta(0):
        raise SnapshotValidationError(f"{name} must be UTC")
    return value


def _decimal_text(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise SnapshotValidationError("performance values must be finite Decimals")
    return str(value)


@dataclass(frozen=True, slots=True)
class PerformanceSnapshotV1:
    gross_pnl: Decimal
    commission_fees: Decimal
    bid_ask_spread: Decimal
    slippage: Decimal
    market_impact: Decimal
    additional_friction: Decimal
    net_pnl: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    portfolio_value: Decimal
    turnover: Decimal
    participation: str
    capacity: str

    @classmethod
    def from_truth(cls, truth: PerformanceTruth) -> "PerformanceSnapshotV1":
        if type(truth) is not PerformanceTruth:
            raise SnapshotValidationError("performance must be PerformanceTruth")
        costs = truth.execution_cost_drag
        values = (
            truth.gross_pnl,
            costs.commission_fees,
            costs.bid_ask_spread,
            costs.slippage,
            costs.market_impact,
            costs.additional_friction,
            truth.net_pnl,
            truth.realised_pnl,
            truth.unrealised_pnl,
            truth.portfolio_value,
            truth.turnover,
        )
        for value in values:
            _decimal_text(value)
        return cls(*values, truth.participation.value, truth.capacity.value)

    def as_mapping(self) -> dict[str, str]:
        return {
            "additional_friction": _decimal_text(self.additional_friction),
            "bid_ask_spread": _decimal_text(self.bid_ask_spread),
            "capacity": self.capacity,
            "commission_fees": _decimal_text(self.commission_fees),
            "gross_pnl": _decimal_text(self.gross_pnl),
            "market_impact": _decimal_text(self.market_impact),
            "net_pnl": _decimal_text(self.net_pnl),
            "participation": self.participation,
            "portfolio_value": _decimal_text(self.portfolio_value),
            "realised_pnl": _decimal_text(self.realised_pnl),
            "slippage": _decimal_text(self.slippage),
            "turnover": _decimal_text(self.turnover),
            "unrealised_pnl": _decimal_text(self.unrealised_pnl),
        }


def _evidence_mapping(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise SnapshotValidationError("evidence_refs must contain EvidenceRef values")
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def evidence_digest_for(evidence_refs: tuple[EvidenceRef, ...]) -> str:
    try:
        canonical = canonicalize_evidence_refs(evidence_refs)
    except (TypeError, ValueError) as error:
        raise SnapshotValidationError("evidence_refs are invalid") from error
    body = [_evidence_mapping(value) for value in canonical]
    digest = sha256(
        b"ATIS-OC-RECONCILIATION-EVIDENCE-V1\0" + _canonical_json(body)
    ).hexdigest()
    return f"sha256:{digest}"


def _observation_id_for(
    *,
    source_run_id: ResearchRunId,
    evidence_digest: str,
    reconciliation_state: ReconciliationState,
    discrepancy_codes: tuple[DiscrepancyCode, ...],
    observed_at_utc: Timestamp,
    produced_at_utc: Timestamp,
    performance: PerformanceSnapshotV1 | None,
) -> str:
    identity_body = {
        "discrepancy_codes": [value.value for value in discrepancy_codes],
        "evidence_digest": evidence_digest,
        "observed_at_utc": _utc_text(observed_at_utc),
        "performance": None if performance is None else performance.as_mapping(),
        "produced_at_utc": _utc_text(produced_at_utc),
        "reconciliation_state": reconciliation_state.value,
        "schema_version": SCHEMA_VERSION,
        "simulation_only": True,
        "source_authority": RECONCILIATION_AUTHORITY,
        "source_run_id": source_run_id.value,
        "trading_authority": NO_TRADING_AUTHORITY,
    }
    return (
        "atis:operator-console:reconciliation-observation:v1:sha256:"
        + sha256(_canonical_json(identity_body)).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshotV1:
    schema_version: str
    observation_id: str
    source_authority: str
    source_run_id: ResearchRunId
    evidence_refs: tuple[EvidenceRef, ...]
    evidence_digest: str
    reconciliation_state: ReconciliationState
    discrepancy_codes: tuple[DiscrepancyCode, ...]
    observed_at_utc: Timestamp
    produced_at_utc: Timestamp
    freshness_state: FreshnessState
    simulation_only: bool
    trading_authority: str
    performance: PerformanceSnapshotV1 | None

    def as_mapping(self) -> dict[str, object]:
        return {
            "discrepancy_codes": [value.value for value in self.discrepancy_codes],
            "evidence_digest": self.evidence_digest,
            "evidence_refs": [_evidence_mapping(value) for value in self.evidence_refs],
            "freshness_state": self.freshness_state.value,
            "observation_id": self.observation_id,
            "observed_at_utc": _utc_text(self.observed_at_utc),
            "performance": None if self.performance is None else self.performance.as_mapping(),
            "produced_at_utc": _utc_text(self.produced_at_utc),
            "reconciliation_state": self.reconciliation_state.value,
            "schema_version": self.schema_version,
            "simulation_only": self.simulation_only,
            "source_authority": self.source_authority,
            "source_run_id": self.source_run_id.value,
            "trading_authority": self.trading_authority,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.as_mapping())


def _result_parts(
    result: ReconciliationResult,
) -> tuple[tuple[DiscrepancyCode, ...], PerformanceSnapshotV1 | None]:
    if type(result) is not ReconciliationResult:
        raise SnapshotValidationError("result must be a ReconciliationResult")
    if result.authority != RECONCILIATION_AUTHORITY:
        raise SnapshotValidationError("reconciliation authority is not canonical")
    if type(result.state) is not ReconciliationState or type(result.discrepancies) is not tuple:
        raise SnapshotValidationError("reconciliation result is malformed")
    if any(type(item.code) is not DiscrepancyCode for item in result.discrepancies):
        raise SnapshotValidationError("reconciliation discrepancy is malformed")
    # The source result may contain one code for several distinct subjects.  The
    # snapshot contract exposes a deterministic code set, not subject-level data.
    codes = tuple(dict.fromkeys(item.code for item in result.discrepancies))
    if result.state is ReconciliationState.RECONCILED:
        if result.discrepancies or result.performance is None:
            raise SnapshotValidationError("RECONCILED requires performance and no discrepancies")
        return (), PerformanceSnapshotV1.from_truth(result.performance)
    if result.performance is not None:
        raise SnapshotValidationError("unreconciled results must not expose performance")
    if not result.discrepancies:
        raise SnapshotValidationError("unreconciled results require discrepancy evidence")
    return codes, None


def produce_reconciliation_snapshot(
    result: ReconciliationResult,
    *,
    source_run_id: ResearchRunId,
    evidence_refs: tuple[EvidenceRef, ...],
    expected_evidence_digest: str,
    observed_at_utc: Timestamp,
    produced_at_utc: Timestamp,
    evaluated_at_utc: Timestamp,
) -> ReconciliationSnapshotV1:
    """Translate authoritative Stage 4 truth into an immutable snapshot.

    All time and provenance inputs are explicit.  Freshness remains UNVERIFIED
    until a separately qualified policy exists.
    """

    if type(source_run_id) is not ResearchRunId:
        raise SnapshotValidationError("source_run_id must be a ResearchRunId")
    observed = _validated_timestamp(observed_at_utc, "observed_at_utc")
    produced = _validated_timestamp(produced_at_utc, "produced_at_utc")
    evaluated = _validated_timestamp(evaluated_at_utc, "evaluated_at_utc")
    if observed.value > produced.value:
        raise SnapshotValidationError("observation cannot follow production")
    if observed.value > evaluated.value or produced.value > evaluated.value:
        raise SnapshotValidationError("future timestamps are not trusted")
    try:
        canonical_refs = canonicalize_evidence_refs(evidence_refs)
    except (TypeError, ValueError) as error:
        raise SnapshotValidationError("evidence_refs are invalid") from error
    actual_digest = evidence_digest_for(canonical_refs)
    if type(expected_evidence_digest) is not str or _DIGEST.fullmatch(expected_evidence_digest) is None:
        raise SnapshotValidationError("expected_evidence_digest is invalid")
    if expected_evidence_digest != actual_digest:
        raise SnapshotValidationError("evidence digest mismatch")
    codes, performance = _result_parts(result)
    observation_id = _observation_id_for(
        source_run_id=source_run_id,
        evidence_digest=actual_digest,
        reconciliation_state=result.state,
        discrepancy_codes=codes,
        observed_at_utc=observed,
        produced_at_utc=produced,
        performance=performance,
    )
    snapshot = ReconciliationSnapshotV1(
        SCHEMA_VERSION,
        observation_id,
        RECONCILIATION_AUTHORITY,
        source_run_id,
        canonical_refs,
        actual_digest,
        result.state,
        codes,
        observed,
        produced,
        FreshnessState.UNVERIFIED,
        True,
        NO_TRADING_AUTHORITY,
        performance,
    )
    validate_snapshot(snapshot, evaluated_at_utc=evaluated)
    return snapshot


def validate_snapshot(
    snapshot: object, *, evaluated_at_utc: Timestamp
) -> ReconciliationSnapshotV1:
    if type(snapshot) is not ReconciliationSnapshotV1:
        raise SnapshotValidationError("unsupported snapshot type")
    if snapshot.schema_version != SCHEMA_VERSION:
        raise SnapshotValidationError("unsupported schema version")
    if _OBSERVATION_ID.fullmatch(snapshot.observation_id) is None:
        raise SnapshotValidationError("observation_id is invalid")
    if snapshot.source_authority != RECONCILIATION_AUTHORITY:
        raise SnapshotValidationError("source authority is not authorised")
    if type(snapshot.source_run_id) is not ResearchRunId:
        raise SnapshotValidationError("source_run_id is invalid")
    evaluated = _validated_timestamp(evaluated_at_utc, "evaluated_at_utc")
    observed = _validated_timestamp(snapshot.observed_at_utc, "observed_at_utc")
    produced = _validated_timestamp(snapshot.produced_at_utc, "produced_at_utc")
    if observed.value > produced.value or produced.value > evaluated.value:
        raise SnapshotValidationError("snapshot timestamps are not trusted")
    if snapshot.freshness_state is not FreshnessState.UNVERIFIED:
        raise SnapshotValidationError("freshness policy is not qualified")
    if snapshot.simulation_only is not True or snapshot.trading_authority != NO_TRADING_AUTHORITY:
        raise SnapshotValidationError("snapshot authority boundary is invalid")
    if evidence_digest_for(snapshot.evidence_refs) != snapshot.evidence_digest:
        raise SnapshotValidationError("evidence digest mismatch")
    if type(snapshot.reconciliation_state) is not ReconciliationState:
        raise SnapshotValidationError("snapshot reconciliation state is invalid")
    if type(snapshot.discrepancy_codes) is not tuple or any(
        type(code) is not DiscrepancyCode for code in snapshot.discrepancy_codes
    ):
        raise SnapshotValidationError("snapshot discrepancy codes are invalid")
    if len(set(snapshot.discrepancy_codes)) != len(snapshot.discrepancy_codes):
        raise SnapshotValidationError("duplicate discrepancy codes are ambiguous")
    if snapshot.reconciliation_state is ReconciliationState.RECONCILED:
        if snapshot.discrepancy_codes or type(snapshot.performance) is not PerformanceSnapshotV1:
            raise SnapshotValidationError("RECONCILED snapshot semantics are invalid")
    elif snapshot.performance is not None or not snapshot.discrepancy_codes:
        raise SnapshotValidationError("unreconciled snapshot semantics are invalid")
    expected_observation_id = _observation_id_for(
        source_run_id=snapshot.source_run_id,
        evidence_digest=snapshot.evidence_digest,
        reconciliation_state=snapshot.reconciliation_state,
        discrepancy_codes=snapshot.discrepancy_codes,
        observed_at_utc=observed,
        produced_at_utc=produced,
        performance=snapshot.performance,
    )
    if snapshot.observation_id != expected_observation_id:
        raise SnapshotValidationError("observation identity mismatch")
    return snapshot


@dataclass(frozen=True, slots=True)
class SnapshotReadResult:
    state: AdapterReadState
    snapshot: ReconciliationSnapshotV1 | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshotAdapter:
    enabled: bool = False
    allowed_source_authorities: frozenset[str] = frozenset({RECONCILIATION_AUTHORITY})

    def read(
        self, snapshot: object | None, *, evaluated_at_utc: Timestamp
    ) -> SnapshotReadResult:
        if self.enabled is not True:
            return SnapshotReadResult(AdapterReadState.UNAVAILABLE, None, ("ADAPTER_DISABLED",))
        if snapshot is None:
            return SnapshotReadResult(AdapterReadState.UNAVAILABLE, None, ("PRODUCER_UNAVAILABLE",))
        try:
            validated = validate_snapshot(snapshot, evaluated_at_utc=evaluated_at_utc)
        except (AttributeError, TypeError, ValueError):
            return SnapshotReadResult(AdapterReadState.UNKNOWN, None, ("TRUST_NOT_ESTABLISHED",))
        if validated.source_authority not in self.allowed_source_authorities:
            return SnapshotReadResult(AdapterReadState.UNKNOWN, None, ("SOURCE_NOT_AUTHORISED",))
        return SnapshotReadResult(
            AdapterReadState.UNKNOWN,
            validated,
            ("FRESHNESS_UNVERIFIED",),
        )

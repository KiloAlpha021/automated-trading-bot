from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import inspect

import pytest

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)
from automated_trading_bot.reconciliation import operator_console_snapshot as module
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    FreshnessState,
    NO_TRADING_AUTHORITY,
    ReconciliationSnapshotAdapter,
    SnapshotValidationError,
    evidence_digest_for,
    produce_reconciliation_snapshot,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.reconciliation_performance import (
    CostDrag,
    Discrepancy,
    DiscrepancyCode,
    MetricAvailability,
    PerformanceTruth,
    ReconciliationResult,
    ReconciliationState,
)


NOW = Timestamp(datetime(2026, 11, 5, 12, tzinfo=UTC))
OBSERVED = Timestamp(NOW.value - timedelta(minutes=2))
PRODUCED = Timestamp(NOW.value - timedelta(minutes=1))
RUN_ID = ResearchRunId("atis:research:run:v1:sha256:" + "1" * 64)


def _evidence(content: bytes = b"stage4-reconciliation-evidence") -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:atis/stage4-reconciliation"),
        DatasetId("dataset:atis/research-simulation"),
        EvidenceId("evidence:atis/reconciliation-result"),
        EvidenceContentDigest.from_bytes(content),
    )


def _performance() -> PerformanceTruth:
    costs = CostDrag(*(Decimal(value) for value in ("1", "2", "3", "4", "5")))
    return PerformanceTruth(
        Decimal("100"),
        costs,
        Decimal("85"),
        Decimal("80"),
        Decimal("5"),
        Decimal("25085"),
        Decimal("0.4"),
        MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )


def _result(
    state: ReconciliationState = ReconciliationState.RECONCILED,
) -> ReconciliationResult:
    if state is ReconciliationState.RECONCILED:
        return ReconciliationResult(state, (), _performance())
    code = (
        DiscrepancyCode.MISSING_FILL
        if state is ReconciliationState.DISCREPANCY
        else DiscrepancyCode.INVALID_INPUT
    )
    return ReconciliationResult(state, (Discrepancy(code, "fixture"),), None)


def _snapshot(
    result: ReconciliationResult | None = None,
    *,
    observed: object = OBSERVED,
    produced: object = PRODUCED,
    evaluated: object = NOW,
    digest: str | None = None,
):
    refs = (_evidence(),)
    return produce_reconciliation_snapshot(
        _result() if result is None else result,
        source_run_id=RUN_ID,
        evidence_refs=refs,
        expected_evidence_digest=evidence_digest_for(refs) if digest is None else digest,
        observed_at_utc=observed,
        produced_at_utc=produced,
        evaluated_at_utc=evaluated,
    )


def test_valid_reconciliation_result_preserves_truth_and_performance() -> None:
    snapshot = _snapshot()
    assert snapshot.reconciliation_state is ReconciliationState.RECONCILED
    assert snapshot.discrepancy_codes == ()
    assert snapshot.performance is not None
    assert snapshot.performance.net_pnl == Decimal("85")
    assert snapshot.source_run_id is RUN_ID


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (ReconciliationState.DISCREPANCY, DiscrepancyCode.MISSING_FILL),
        (ReconciliationState.INVALID_OR_UNKNOWN, DiscrepancyCode.INVALID_INPUT),
    ],
)
def test_non_reconciled_states_preserve_discrepancy_and_hide_performance(
    state: ReconciliationState, code: DiscrepancyCode
) -> None:
    snapshot = _snapshot(_result(state))
    assert snapshot.reconciliation_state is state
    assert snapshot.discrepancy_codes == (code,)
    assert snapshot.performance is None


def test_repeated_source_discrepancy_code_is_canonicalized_without_redefining_state() -> None:
    result = ReconciliationResult(
        ReconciliationState.DISCREPANCY,
        (
            Discrepancy(DiscrepancyCode.MISSING_FILL, "fill-a"),
            Discrepancy(DiscrepancyCode.MISSING_FILL, "fill-b"),
        ),
        None,
    )
    snapshot = _snapshot(result)
    assert snapshot.reconciliation_state is ReconciliationState.DISCREPANCY
    assert snapshot.discrepancy_codes == (DiscrepancyCode.MISSING_FILL,)


def test_missing_research_run_identity_fails_closed() -> None:
    refs = (_evidence(),)
    with pytest.raises(SnapshotValidationError, match="source_run_id"):
        produce_reconciliation_snapshot(
            _result(),
            source_run_id=None,
            evidence_refs=refs,
            expected_evidence_digest=evidence_digest_for(refs),
            observed_at_utc=OBSERVED,
            produced_at_utc=PRODUCED,
            evaluated_at_utc=NOW,
        )


def test_invalid_evidence_reference_fails_closed() -> None:
    with pytest.raises(SnapshotValidationError, match="evidence_refs"):
        evidence_digest_for((object(),))


def test_evidence_digest_mismatch_fails_closed() -> None:
    with pytest.raises(SnapshotValidationError, match="digest mismatch"):
        _snapshot(digest="sha256:" + "0" * 64)


def test_observation_identity_detects_semantic_tampering() -> None:
    snapshot = _snapshot()
    assert snapshot.performance is not None
    object.__setattr__(snapshot.performance, "net_pnl", Decimal("999"))
    read = ReconciliationSnapshotAdapter(enabled=True).read(
        snapshot, evaluated_at_utc=NOW
    )
    assert read.state is AdapterReadState.UNKNOWN
    assert read.snapshot is None


@pytest.mark.parametrize("value", [None, object()])
def test_missing_or_invalid_observation_timestamp_fails_closed(value: object) -> None:
    with pytest.raises(SnapshotValidationError, match="observed_at_utc"):
        _snapshot(observed=value)


def test_future_timestamp_fails_closed() -> None:
    future = Timestamp(NOW.value + timedelta(seconds=1))
    with pytest.raises(SnapshotValidationError, match="future"):
        _snapshot(observed=future, produced=future)


def test_freshness_is_unverified_and_never_presented_as_available() -> None:
    snapshot = _snapshot()
    assert snapshot.freshness_state is FreshnessState.UNVERIFIED
    read = ReconciliationSnapshotAdapter(enabled=True).read(
        snapshot, evaluated_at_utc=NOW
    )
    assert read.state is AdapterReadState.UNKNOWN
    assert read.reasons == ("FRESHNESS_UNVERIFIED",)


def test_unsupported_schema_is_rejected_by_adapter() -> None:
    snapshot = _snapshot()
    object.__setattr__(snapshot, "schema_version", "UNSUPPORTED")
    read = ReconciliationSnapshotAdapter(enabled=True).read(
        snapshot, evaluated_at_utc=NOW
    )
    assert read.state is AdapterReadState.UNKNOWN
    assert read.snapshot is None


def test_adapter_is_disabled_by_default() -> None:
    read = ReconciliationSnapshotAdapter().read(_snapshot(), evaluated_at_utc=NOW)
    assert read.state is AdapterReadState.UNAVAILABLE
    assert read.snapshot is None
    assert read.reasons == ("ADAPTER_DISABLED",)


def test_snapshot_and_adapter_reject_mutation() -> None:
    snapshot = _snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.trading_authority = "LIVE"
    adapter = ReconciliationSnapshotAdapter()
    with pytest.raises(FrozenInstanceError):
        adapter.enabled = True


def test_snapshot_exposes_no_trading_authority_or_broker_capability() -> None:
    snapshot = _snapshot()
    assert snapshot.simulation_only is True
    assert snapshot.trading_authority == NO_TRADING_AUTHORITY
    assert not hasattr(ReconciliationSnapshotAdapter, "submit_order")
    source = inspect.getsource(module)
    assert "import socket" not in source
    assert "broker" not in source.lower()


def test_producer_has_no_operational_side_effects_or_input_mutation() -> None:
    result = _result()
    refs = (_evidence(),)
    before = (result, refs, result.performance)
    first = produce_reconciliation_snapshot(
        result,
        source_run_id=RUN_ID,
        evidence_refs=refs,
        expected_evidence_digest=evidence_digest_for(refs),
        observed_at_utc=OBSERVED,
        produced_at_utc=PRODUCED,
        evaluated_at_utc=NOW,
    )
    assert (result, refs, result.performance) == before
    assert first.source_authority.endswith("NO_TRADING_AUTHORITY")


def test_serialization_is_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()
    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.observation_id == second.observation_id


def test_inconsistent_reconciliation_semantics_fail_closed() -> None:
    malformed = ReconciliationResult(ReconciliationState.RECONCILED, (), None)
    with pytest.raises(SnapshotValidationError, match="RECONCILED"):
        _snapshot(malformed)


def test_unavailable_producer_returns_unavailable() -> None:
    read = ReconciliationSnapshotAdapter(enabled=True).read(None, evaluated_at_utc=NOW)
    assert read.state is AdapterReadState.UNAVAILABLE
    assert read.reasons == ("PRODUCER_UNAVAILABLE",)

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

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
    validate_snapshot,
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
SCHEMA_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "stage4"
    / "s4-operator-console-reconciliation-snapshot-v1.schema.json"
)


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


def _attacker_recomputed_identity(snapshot: object) -> str:
    performance = snapshot.performance
    performance_mapping = None
    if performance is not None:
        performance_mapping = {
            name: str(getattr(performance, name))
            for name in module._PERFORMANCE_DECIMAL_FIELDS
        }
        performance_mapping.update(
            {
                "participation": performance.participation,
                "capacity": performance.capacity,
            }
        )
    body = {
        "discrepancy_codes": [value.value for value in snapshot.discrepancy_codes],
        "evidence_digest": snapshot.evidence_digest,
        "observed_at_utc": snapshot.observed_at_utc.value.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
        "performance": performance_mapping,
        "produced_at_utc": snapshot.produced_at_utc.value.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
        "reconciliation_state": snapshot.reconciliation_state.value,
        "schema_version": snapshot.schema_version,
        "simulation_only": snapshot.simulation_only,
        "source_authority": snapshot.source_authority,
        "source_run_id": snapshot.source_run_id.value,
        "trading_authority": snapshot.trading_authority,
    }
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return (
        "atis:operator-console:reconciliation-observation:v1:sha256:"
        + sha256(encoded).hexdigest()
    )


def test_valid_reconciliation_result_preserves_truth_and_performance() -> None:
    snapshot = _snapshot()
    assert snapshot.reconciliation_state is ReconciliationState.RECONCILED
    assert snapshot.discrepancy_codes == ()
    assert snapshot.performance is not None
    assert snapshot.performance.net_pnl == Decimal("85")
    assert snapshot.source_run_id is RUN_ID


def test_performance_validation_accepts_valid_finite_values() -> None:
    snapshot = _snapshot()
    assert validate_snapshot(snapshot, evaluated_at_utc=NOW) is snapshot


def test_performance_validation_preserves_legitimate_negative_pnl() -> None:
    truth = PerformanceTruth(
        Decimal("-100.25"),
        CostDrag(*(Decimal(value) for value in ("1", "2", "3", "4", "5"))),
        Decimal("-115.25"),
        Decimal("-80"),
        Decimal("-35.25"),
        Decimal("24884.75"),
        Decimal("0.4"),
        MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )
    snapshot = _snapshot(
        ReconciliationResult(ReconciliationState.RECONCILED, (), truth)
    )
    assert snapshot.performance is not None
    assert snapshot.performance.gross_pnl == Decimal("-100.25")
    assert snapshot.performance.net_pnl == Decimal("-115.25")


@pytest.mark.parametrize("field", module._PERFORMANCE_DECIMAL_FIELDS)
@pytest.mark.parametrize("invalid", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_performance_validation_rejects_non_finite_decimal_fields(
    field: str, invalid: Decimal
) -> None:
    snapshot = _snapshot()
    assert snapshot.performance is not None
    object.__setattr__(snapshot.performance, field, invalid)
    object.__setattr__(snapshot, "observation_id", _attacker_recomputed_identity(snapshot))
    with pytest.raises(SnapshotValidationError, match="finite Decimal"):
        validate_snapshot(snapshot, evaluated_at_utc=NOW)


@pytest.mark.parametrize("field", module._PERFORMANCE_DECIMAL_FIELDS)
@pytest.mark.parametrize("invalid", ["1.0", 1, 1.0])
def test_performance_validation_rejects_incorrect_decimal_types(
    field: str, invalid: object
) -> None:
    snapshot = _snapshot()
    assert snapshot.performance is not None
    object.__setattr__(snapshot.performance, field, invalid)
    object.__setattr__(snapshot, "observation_id", _attacker_recomputed_identity(snapshot))
    with pytest.raises(SnapshotValidationError, match="finite Decimal"):
        validate_snapshot(snapshot, evaluated_at_utc=NOW)


@pytest.mark.parametrize("field", ["participation", "capacity"])
def test_performance_validation_rejects_unsupported_metric_states(field: str) -> None:
    snapshot = _snapshot()
    assert snapshot.performance is not None
    object.__setattr__(snapshot.performance, field, "UNSUPPORTED")
    object.__setattr__(snapshot, "observation_id", _attacker_recomputed_identity(snapshot))
    with pytest.raises(SnapshotValidationError, match="supported metric state"):
        validate_snapshot(snapshot, evaluated_at_utc=NOW)


@pytest.mark.parametrize(
    "malformed",
    ["NaN", "Infinity", "-Infinity", "+1", "01", "1.", ".5", "1e3", " 1"],
)
def test_schema_rejects_malformed_or_noncanonical_numeric_strings(
    malformed: str,
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    document = _snapshot().as_mapping()
    performance = document["performance"]
    assert isinstance(performance, dict)
    performance["net_pnl"] = malformed
    assert list(Draft202012Validator(schema).iter_errors(document))


def test_schema_accepts_canonical_negative_decimal_strings() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = _snapshot().as_mapping()
    performance = document["performance"]
    assert isinstance(performance, dict)
    performance["gross_pnl"] = "-100.25"
    performance["net_pnl"] = "-115.25"
    assert list(Draft202012Validator(schema).iter_errors(document)) == []


def test_recomputed_identity_does_not_bypass_independent_validation() -> None:
    snapshot = _snapshot()
    assert snapshot.performance is not None
    object.__setattr__(snapshot.performance, "participation", "UNSUPPORTED")
    object.__setattr__(snapshot, "observation_id", _attacker_recomputed_identity(snapshot))
    read = ReconciliationSnapshotAdapter(enabled=True).read(
        snapshot, evaluated_at_utc=NOW
    )
    assert read.state is AdapterReadState.UNKNOWN
    assert read.snapshot is None


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

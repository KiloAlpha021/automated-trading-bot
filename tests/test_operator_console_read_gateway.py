from datetime import UTC, datetime, timedelta
from decimal import Decimal
import inspect

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)
from automated_trading_bot.reconciliation import operator_console_read_gateway as module
from automated_trading_bot.reconciliation.operator_console_read_gateway import (
    AuthenticatedPrincipal,
    GatewayState,
    OperatorConsoleReadGateway,
    REQUIRED_PERMISSION,
)
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    NO_TRADING_AUTHORITY,
    SnapshotReadResult,
    evidence_digest_for,
    produce_reconciliation_snapshot,
)
from automated_trading_bot.research.identity import ResearchRunId
from automated_trading_bot.research.reconciliation_performance import (
    CostDrag,
    MetricAvailability,
    PerformanceTruth,
    ReconciliationResult,
    ReconciliationState,
)


NOW = Timestamp(datetime(2026, 11, 6, 14, tzinfo=UTC))
CREDENTIAL = object()


def _principal(*, permissions: frozenset[str] | None = None, expired: bool = False):
    return AuthenticatedPrincipal(
        "atis-operator",
        frozenset({REQUIRED_PERMISSION}) if permissions is None else permissions,
        Timestamp(NOW.value - timedelta(minutes=1)),
        Timestamp(NOW.value + timedelta(minutes=-1 if expired else 5)),
    )


def _snapshot():
    ref = EvidenceRef(
        SourceId("source:atis/stage4-reconciliation"),
        DatasetId("dataset:atis/research-simulation"),
        EvidenceId("evidence:atis/gateway-test"),
        EvidenceContentDigest.from_bytes(b"gateway-test"),
    )
    refs = (ref,)
    costs = CostDrag(*(Decimal(value) for value in ("1", "2", "3", "4", "5")))
    truth = PerformanceTruth(
        Decimal("100"), costs, Decimal("85"), Decimal("80"), Decimal("5"),
        Decimal("25085"), Decimal("0.4"), MetricAvailability.NOT_ESTABLISHED,
        MetricAvailability.NOT_ESTABLISHED,
    )
    return produce_reconciliation_snapshot(
        ReconciliationResult(ReconciliationState.RECONCILED, (), truth),
        source_run_id=ResearchRunId("atis:research:run:v1:sha256:" + "3" * 64),
        evidence_refs=refs,
        expected_evidence_digest=evidence_digest_for(refs),
        observed_at_utc=Timestamp(NOW.value - timedelta(minutes=2)),
        produced_at_utc=Timestamp(NOW.value - timedelta(minutes=1)),
        evaluated_at_utc=NOW,
    )


class Authentication:
    def __init__(self, result: object = None, *, error: Exception | None = None) -> None:
        self.result = _principal() if result is None else result
        self.error = error

    def authenticate(self, credential: object, *, evaluated_at_utc: Timestamp):
        if self.error is not None:
            raise self.error
        if credential is not CREDENTIAL:
            return None
        return self.result


class Reader:
    def __init__(self, result: object = None, *, error: Exception | None = None) -> None:
        self.result = (
            SnapshotReadResult(
                AdapterReadState.UNKNOWN, _snapshot(), ("FRESHNESS_UNVERIFIED",)
            )
            if result is None
            else result
        )
        self.error = error
        self.calls = 0

    def read_current(self, *, evaluated_at_utc: Timestamp):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _gateway(authentication: Authentication | None = None, reader: Reader | None = None):
    return OperatorConsoleReadGateway(
        authentication=Authentication() if authentication is None else authentication,
        reader=Reader() if reader is None else reader,
    )


def test_authorised_reader_receives_allowlisted_unverified_observation() -> None:
    response = _gateway().read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.reasons == ("FRESHNESS_UNVERIFIED",)
    assert response.observation is not None
    body = response.as_mapping()["observation"]
    assert isinstance(body, dict)
    assert frozenset(body) == frozenset(
        {
            "schema_version", "observation_id", "reconciliation_state",
            "discrepancy_codes", "observed_at_utc", "produced_at_utc",
            "freshness_state", "simulation_only", "trading_authority", "performance",
        }
    )
    assert body["trading_authority"] == NO_TRADING_AUTHORITY
    assert "source_run_id" not in body and "evidence_refs" not in body


def test_missing_or_invalid_identity_is_denied_before_storage_read() -> None:
    reader = Reader()
    gateway = _gateway(reader=reader)
    for credential in (None, object(), "atis-operator"):
        response = gateway.read(credential, evaluated_at_utc=NOW)
        assert response.state is GatewayState.ACCESS_DENIED
        assert response.reasons == ("AUTHENTICATION_REQUIRED",)
    assert reader.calls == 0


def test_expired_identity_is_denied() -> None:
    response = _gateway(Authentication(_principal(expired=True))).read(
        CREDENTIAL, evaluated_at_utc=NOW
    )
    assert response.reasons == ("AUTHENTICATION_REQUIRED",)


def test_missing_permission_is_denied_before_read() -> None:
    reader = Reader()
    response = _gateway(
        Authentication(_principal(permissions=frozenset())), reader
    ).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.reasons == ("PERMISSION_DENIED",)
    assert reader.calls == 0


def test_forged_caller_claims_are_not_identity_proof() -> None:
    forged = {
        "authenticated": True,
        "principal_id": "atis-operator",
        "permissions": [REQUIRED_PERMISSION],
    }
    response = _gateway().read(forged, evaluated_at_utc=NOW)
    assert response.state is GatewayState.ACCESS_DENIED


def test_missing_snapshot_is_unavailable() -> None:
    result = SnapshotReadResult(
        AdapterReadState.UNAVAILABLE, None, ("CURRENT_POINTER_MISSING",)
    )
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNAVAILABLE
    assert response.observation is None


def test_corrupted_or_unsupported_snapshot_is_unknown() -> None:
    result = SnapshotReadResult(
        AdapterReadState.UNKNOWN, None, ("PERSISTED_SNAPSHOT_UNTRUSTED",)
    )
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.observation is None


def test_malformed_snapshot_object_is_revalidated_and_rejected() -> None:
    result = SnapshotReadResult(
        AdapterReadState.UNKNOWN, object(), ("FRESHNESS_UNVERIFIED",)  # type: ignore[arg-type]
    )
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.reasons == ("SOURCE_RESULT_UNTRUSTED",)
    assert response.observation is None


def test_observation_requires_exact_unverified_freshness_reason() -> None:
    result = SnapshotReadResult(AdapterReadState.UNKNOWN, _snapshot(), ())
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.reasons == ("SOURCE_RESULT_UNTRUSTED",)
    assert response.observation is None


def test_unverified_freshness_is_never_promoted() -> None:
    response = _gateway().read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.observation is not None
    assert response.observation.freshness_state == "UNVERIFIED"


def test_storage_exception_is_sanitised() -> None:
    response = _gateway(reader=Reader(error=OSError("/secret/store/current.json"))).read(
        CREDENTIAL, evaluated_at_utc=NOW
    )
    assert response.as_mapping() == {
        "gateway_schema_version": module.GATEWAY_SCHEMA_VERSION,
        "observation": None,
        "reasons": ["STORAGE_READ_FAILED"],
        "state": "UNKNOWN",
    }
    assert b"secret" not in response.canonical_bytes()


def test_authentication_exception_is_sanitised() -> None:
    response = _gateway(Authentication(error=RuntimeError("identity database secret"))).read(
        CREDENTIAL, evaluated_at_utc=NOW
    )
    assert response.reasons == ("AUTHENTICATION_UNAVAILABLE",)
    assert b"secret" not in response.canonical_bytes()


def test_unrecognised_source_reason_is_not_disclosed() -> None:
    result = SnapshotReadResult(AdapterReadState.UNKNOWN, None, ("/private/path",))
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.reasons == ("SOURCE_RESULT_UNTRUSTED",)


def test_oversized_response_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 32)
    response = _gateway().read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.observation is None
    assert response.reasons == ("RESPONSE_LIMIT_EXCEEDED",)


def test_read_capability_has_no_publication_or_recovery_authority() -> None:
    gateway = _gateway()
    assert not hasattr(gateway, "publish")
    assert not hasattr(gateway, "recover")
    assert not hasattr(gateway, "root")
    assert not hasattr(gateway, "current")


def test_no_broker_trading_or_network_capability() -> None:
    source = inspect.getsource(module).lower()
    for forbidden in (
        "import socket", "import requests", "http.server", "submit_order",
        "automated_trading_bot.broker", "asyncio.start_server",
    ):
        assert forbidden not in source
    gateway = _gateway()
    assert not hasattr(gateway, "broker")
    assert not hasattr(gateway, "listen")
    assert not hasattr(gateway, "start")


def test_invalid_authenticator_result_and_time_fail_closed() -> None:
    assert _gateway(Authentication({"principal_id": "forged"})).read(
        CREDENTIAL, evaluated_at_utc=NOW
    ).state is GatewayState.ACCESS_DENIED
    assert _gateway().read(CREDENTIAL, evaluated_at_utc=None).state is GatewayState.ACCESS_DENIED


def test_available_source_state_is_rejected_as_unqualified_promotion() -> None:
    result = SnapshotReadResult(AdapterReadState.AVAILABLE, _snapshot(), ())
    response = _gateway(reader=Reader(result)).read(CREDENTIAL, evaluated_at_utc=NOW)
    assert response.state is GatewayState.UNKNOWN
    assert response.reasons == ("SOURCE_RESULT_UNTRUSTED",)


def test_canonical_response_is_deterministic_and_bounded() -> None:
    first = _gateway().read(CREDENTIAL, evaluated_at_utc=NOW).canonical_bytes()
    second = _gateway().read(CREDENTIAL, evaluated_at_utc=NOW).canonical_bytes()
    assert first == second
    assert len(first) <= module.MAX_RESPONSE_BYTES

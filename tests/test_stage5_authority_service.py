from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import hmac
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
from uuid import uuid4

import pytest
import win32api  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32file  # type: ignore[import-untyped]
import win32pipe  # type: ignore[import-untyped]
import win32security  # type: ignore[import-untyped]

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.timestamp import Timestamp

from automated_trading_bot.stage5.authority_service import (
    ApdDisposition,
    AuthorityDatabase,
    AuthorityServiceError,
    AuthorityState,
    SignedAdmission,
    classify_recovery,
)
from automated_trading_bot.stage5.authority_service.persistence import (
    RetainedAuthorityStatus,
)
from automated_trading_bot.stage5.authority_service.service import (
    AdmissionVerifier,
    AuthorityService,
    AuthoritativeSnapshotRecord,
    AuthorityLineage,
    RuntimeAdmissionRequest,
)
from automated_trading_bot.stage5.authority_service.client import (
    I01AuthorityServiceClient,
    VerifiedOperationalAdmission,
)
from automated_trading_bot.stage5.contracts import (
    CapitalSnapshot,
    FinancialComponent,
    ProviderAuthority,
    _digest,
)
from automated_trading_bot.stage5.authority_service.cng import (
    CngAdmissionSigner,
    CngAdmissionVerifier,
)
from automated_trading_bot.stage5.authority_service.composition import (
    ProtectedAuthorityManager,
)
from automated_trading_bot.stage5.authority_service.keys import (
    AnchoredKeyLifecycle,
    SigningKeyStatus,
    activate_key,
    key_record,
    provision_verify_only,
    require_signing_key,
    retire_key,
)


def _state(sequence: int = 0) -> AuthorityState:
    now = datetime.now(timezone.utc)
    return AuthorityState.create(
        domain_identity="domain-1", store_epoch="epoch-1", sequence=sequence,
        authority_id="authority-1",
        authority_head=f"head-{sequence}", authority_generation=1,
        authority_status=RetainedAuthorityStatus.ACTIVE,
        authority_valid_from=(now - timedelta(minutes=1)).isoformat(),
        authority_expires_at=(now + timedelta(hours=1)).isoformat(),
        currency="USD", provider_descriptor_digest="descriptor-1",
        provider_implementation_identity="provider-implementation-1",
        policy_id="policy-1", policy_version=1,
        trusted_time_high_water=(now - timedelta(seconds=1)).isoformat(),
        current_key_id="key-1", current_key_version=1,
        key_records=(key_record("key-1", 1, SigningKeyStatus.SIGN_CURRENT),),
    )


class Witness:
    def __init__(self, anchor: str) -> None:
        self.anchor = anchor
        self.pending: str | None = None

    def read_anchor(self) -> str:
        return self.anchor

    def read_pending(self) -> str | None:
        return self.pending

    def write_pending(self, value: str) -> None:
        if self.pending is not None:
            raise AuthorityServiceError("pending already exists")
        self.pending = value

    def write_anchor(self, value: str) -> None:
        self.anchor = value

    def clear_pending(self, expected: str) -> None:
        if self.pending != expected:
            raise AuthorityServiceError("pending changed")
        self.pending = None


@pytest.mark.parametrize(
    ("anchor", "pending", "database", "expected"),
    [
        ("old", None, "old", ApdDisposition.NORMAL_SUCCESSOR),
        ("old", "new", "old", ApdDisposition.FAIL_CLOSED_OWNER_RESOLUTION),
        ("old", "new", "new", ApdDisposition.COMPLETE_ANCHOR_ADVANCE),
        ("new", "new", "new", ApdDisposition.COMPLETE_PENDING_RESOLUTION),
        ("new", None, "new", ApdDisposition.NORMAL_SUCCESSOR),
        ("old", None, "new", ApdDisposition.FAIL_CLOSED_UNPROVEN_COMMIT),
        ("new", "other", "old", ApdDisposition.FAIL_CLOSED_CONTRADICTION),
        ("old", "x", "y", ApdDisposition.FAIL_CLOSED_CONTRADICTION),
    ],
)
def test_r01_r08_recovery_table(
    anchor: str, pending: str | None, database: str, expected: ApdDisposition
) -> None:
    assert classify_recovery(
        anchor=anchor, pending=pending, database=database
    ).disposition is expected


def test_r10_publish_and_lost_acknowledgement_are_idempotent(tmp_path: Path) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    successor = AuthorityState.create(**{
        **initial.body(), "sequence": 1, "authority_head": "head-1",
    })
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    result = (f'{{"state_digest":"{successor.state_digest}"}}').encode("ascii")
    assert database.publish(
        witness=witness, command_id="command-1", payload_digest="a" * 64,
        successor=successor, result=result,
    ) == result
    assert database.publish(
        witness=witness, command_id="command-1", payload_digest="a" * 64,
        successor=successor, result=result,
    ) == result
    with pytest.raises(AuthorityServiceError, match="conflicting payload"):
        database.publish(
            witness=witness, command_id="command-1", payload_digest="b" * 64,
            successor=successor, result=result,
        )


def test_n03_n06_cached_result_requires_consistent_apd_and_result_digest(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    successor = AuthorityState.create(**{
        **initial.body(), "sequence": 1, "authority_head": "head-1",
    })
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    result = (f'{{"state_digest":"{successor.state_digest}"}}').encode("ascii")
    database.publish(
        witness=witness, command_id="command-1", payload_digest="a" * 64,
        successor=successor, result=result,
    )
    witness.anchor = initial.state_digest
    with pytest.raises(AuthorityServiceError, match="not operational"):
        database.publish(
            witness=witness, command_id="command-1", payload_digest="a" * 64,
            successor=successor, result=result,
        )
    witness.anchor = successor.state_digest
    with sqlite3.connect(tmp_path / "authority.sqlite3") as connection:
        connection.execute(
            "UPDATE commands SET result_digest=? WHERE command_id=?",
            ("0" * 64, "command-1"),
        )
    with pytest.raises(AuthorityServiceError, match="result digest mismatch"):
        database.publish(
            witness=witness, command_id="command-1", payload_digest="a" * 64,
            successor=successor, result=result,
        )


def test_n03_old_committed_result_cannot_claim_a_later_authority_witness(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    first = AuthorityState.create(**{
        **initial.body(), "sequence": 1, "authority_head": "head-1",
    })
    second = AuthorityState.create(**{
        **first.body(), "sequence": 2, "authority_head": "head-2",
    })
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    first_result = (f'{{"state_digest":"{first.state_digest}"}}').encode("ascii")
    second_result = (f'{{"state_digest":"{second.state_digest}"}}').encode("ascii")
    database.publish(
        witness=witness, command_id="command-1", payload_digest="a" * 64,
        successor=first, result=first_result,
    )
    database.publish(
        witness=witness, command_id="command-2", payload_digest="b" * 64,
        successor=second, result=second_result,
    )
    with pytest.raises(AuthorityServiceError, match="witness is not current"):
        database.retry_result(
            witness=witness, command_id="command-1", payload_digest="a" * 64,
        )
def test_n01_trusted_time_highwater_persists_and_moves_only_forward(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    observed = datetime.now(timezone.utc)
    with database.admission_witness(
        witness=witness, observed_at=observed.isoformat()
    ) as accepted:
        assert accepted.trusted_time_high_water == observed.isoformat()
    recovered = database.read()
    assert recovered.trusted_time_high_water == observed.isoformat()
    with pytest.raises(AuthorityServiceError, match="moved backwards"):
        with database.admission_witness(
            witness=witness,
            observed_at=(observed - timedelta(seconds=1)).isoformat(),
        ):
            pass


def test_f02_q17_expiry_is_retained_before_admission_rejects(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    now = datetime.now(timezone.utc)
    expired = AuthorityState.create(**{
        **_state().body(),
        "authority_valid_from": (now - timedelta(hours=2)).isoformat(),
        "authority_expires_at": (now - timedelta(hours=1)).isoformat(),
        "trusted_time_high_water": (now - timedelta(hours=2)).isoformat(),
    })
    database.initialise(expired)
    witness = Witness(expired.state_digest)
    with pytest.raises(AuthorityServiceError, match="authority is expired"):
        with database.admission_witness(
            witness=witness, observed_at=now.isoformat()
        ):
            pass
    retained = database.read()
    assert retained.authority_status is RetainedAuthorityStatus.EXPIRED
    assert witness.anchor == retained.state_digest


def test_f03_recovery_completes_only_provable_pending_transition(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    current = _state()
    database.initialise(current)
    witness = Witness("old")
    witness.pending = current.state_digest
    assert database.recover(witness) == current
    assert witness.anchor == current.state_digest
    assert witness.pending is None
    witness.anchor = "older"
    with pytest.raises(AuthorityServiceError, match="owner resolution"):
        database.recover(witness)


def test_n06_q13_q14_management_lost_ack_retry_binds_payload_and_witness(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    manager = ProtectedAuthorityManager(database, witness)
    request: dict[str, object] = {
        "operation": "REVOKE",
        "command_id": "revoke-1",
        "expected_authority_head": initial.authority_head,
    }
    first = manager(request)
    assert manager(request) == first
    with pytest.raises(AuthorityServiceError, match="conflicting payload"):
        manager({**request, "unexpected": "different"})
    witness.anchor = initial.state_digest
    with pytest.raises(AuthorityServiceError, match="not operational"):
        manager(request)


class EngineeringSigner:
    key_id = "key-1"
    key_version = 1

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def sign_admission(self, envelope: object) -> bytes:
        from automated_trading_bot.stage5.authority_service.protocol import AdmissionEnvelope

        assert type(envelope) is AdmissionEnvelope
        return hmac.new(self._secret, envelope.signing_bytes(), sha256).digest()


class EngineeringVerifier:
    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def verify_admission(self, admission: SignedAdmission) -> bool:
        expected = hmac.new(
            self._secret, admission.envelope.signing_bytes(), sha256
        ).digest()
        return hmac.compare_digest(expected, admission.signature)


class Current:
    def __init__(self, state: AuthorityState) -> None:
        self.value = state

    @contextmanager
    def admission_witness(self, observed_at: datetime):
        assert observed_at.tzinfo is timezone.utc
        yield self.value

    @contextmanager
    def consumption_witness(
        self, observed_at: datetime, *, timeout_seconds: float
    ):
        assert observed_at.tzinfo is timezone.utc
        assert timeout_seconds > 0
        yield self.value


class FixedClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SnapshotSource:
    def resolve_snapshot(self, *, portfolio_id: str, run_scope: str,
                         operation_purpose: str) -> AuthoritativeSnapshotRecord:
        assert (portfolio_id, run_scope, operation_purpose) == (
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT"
        )
        return AuthoritativeSnapshotRecord(
            "snapshot-id-1", "snapshot-1", 4,
            (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "USD",
            "descriptor-1", "provider-implementation-1", "policy-1", 1,
        )


def _lineage(state: AuthorityState) -> AuthorityLineage:
    return AuthorityLineage.from_state(state)


def test_t01_s01_s04_runtime_verifies_bound_admission_without_sign_api() -> None:
    secret = b"ENGINEERING_SIMULATION_ONLY"
    current = Current(_state())
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=current, signer=EngineeringSigner(secret),
        snapshot_source=SnapshotSource(), clock=FixedClock(),
    )
    request = RuntimeAdmissionRequest(
        "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
    )
    signed = service.admit(request)
    assert not hasattr(service, "sign")
    verifier = AdmissionVerifier(
        verifier=EngineeringVerifier(secret), service_identity="service-1",
        deployment_identity="deployment-1", authority_domain_identity="domain-1",
        signing_key_id="key-1", signing_key_version=1,
    )
    assert verifier.verify(
        signed, snapshot_digest="snapshot-1", snapshot_id="snapshot-id-1",
        snapshot_version=4, snapshot_expires_at=signed.envelope.snapshot_expires_at,
        currency="USD",
        portfolio_id="portfolio-1",
        run_scope="run-1", operation_purpose="CAPITAL_SNAPSHOT",
    ) == signed.envelope
    with pytest.raises(AuthorityServiceError, match="binding mismatch"):
        verifier.verify(
            signed, snapshot_digest="forged", snapshot_id="snapshot-id-1",
            snapshot_version=4, snapshot_expires_at=signed.envelope.snapshot_expires_at,
            currency="USD",
            portfolio_id="portfolio-1",
            run_scope="run-1", operation_purpose="CAPITAL_SNAPSHOT",
        )


def test_s03_mutated_envelope_and_wrong_signature_reject() -> None:
    secret = b"ENGINEERING_SIMULATION_ONLY"
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(_state()), signer=EngineeringSigner(secret),
        snapshot_source=SnapshotSource(), clock=FixedClock(),
    )
    signed = service.admit(RuntimeAdmissionRequest(
        "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
    ))
    mutated = SignedAdmission(
        replace(signed.envelope, portfolio_id="foreign"), signed.signature
    )
    verifier = AdmissionVerifier(
        verifier=EngineeringVerifier(secret), service_identity="service-1",
        deployment_identity="deployment-1", authority_domain_identity="domain-1",
        signing_key_id="key-1", signing_key_version=1,
    )
    with pytest.raises(AuthorityServiceError, match="binding mismatch"):
        verifier.verify(
            mutated, snapshot_digest="snapshot-1", snapshot_id="snapshot-id-1",
            snapshot_version=4, snapshot_expires_at=signed.envelope.snapshot_expires_at,
            currency="USD",
            portfolio_id="portfolio-1",
            run_scope="run-1", operation_purpose="CAPITAL_SNAPSHOT",
        )


def test_t02_foreign_domain_cannot_select_authority() -> None:
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(_state()), signer=EngineeringSigner(b"engineering"),
        snapshot_source=SnapshotSource(), clock=FixedClock(),
    )
    with pytest.raises(AuthorityServiceError, match="foreign authority domain"):
        service.admit(RuntimeAdmissionRequest(
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "foreign", "nonce-1"
        ))


@pytest.mark.parametrize(
    "status",
    [
        RetainedAuthorityStatus.REVOKED,
        RetainedAuthorityStatus.SUPERSEDED,
        RetainedAuthorityStatus.EXPIRED,
    ],
)
def test_f02_q03_q15_q17_nonactive_lifecycle_rejects(
    status: RetainedAuthorityStatus,
) -> None:
    state = AuthorityState.create(**{
        **_state().body(),
        "authority_status": status,
    })
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(state), signer=EngineeringSigner(b"engineering"),
        snapshot_source=SnapshotSource(), clock=FixedClock(),
    )
    with pytest.raises(AuthorityServiceError, match="authority is not active"):
        service.admit(RuntimeAdmissionRequest(
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
        ))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("portfolio_id", "foreign-portfolio"),
        ("run_scope", "foreign-run"),
        ("operation_purpose", "FOREIGN"),
        ("currency", "GBP"),
        ("provider_descriptor_digest", "foreign-provider"),
        ("provider_implementation_identity", "foreign-implementation"),
        ("policy_id", "foreign-policy"),
        ("policy_version", 2),
    ],
)
def test_n02_q05_q06_q08_q09_provider_source_provenance_rejects(
    field: str, replacement: object,
) -> None:
    base = SnapshotSource().resolve_snapshot(
        portfolio_id="portfolio-1", run_scope="run-1",
        operation_purpose="CAPITAL_SNAPSHOT",
    )

    class AlteredSource:
        def resolve_snapshot(self, **_: object) -> AuthoritativeSnapshotRecord:
            return replace(base, **{field: replacement})

    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(_state()), signer=EngineeringSigner(b"engineering"),
        snapshot_source=AlteredSource(), clock=FixedClock(),
    )
    with pytest.raises(AuthorityServiceError, match="provenance mismatch"):
        service.admit(RuntimeAdmissionRequest(
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
        ))


def test_c1_ephemeral_cng_signs_only_admission_and_verifies() -> None:
    with CngAdmissionSigner.engineering_ephemeral(key_id="key-1") as signer:
        service = AuthorityService(
            service_identity="service-1", deployment_identity="deployment-1",
            authority=Current(_state()), signer=signer,
            snapshot_source=SnapshotSource(), clock=FixedClock(),
        )
        signed = service.admit(RuntimeAdmissionRequest(
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
        ))
        assert CngAdmissionVerifier(signer.public_blob()).verify_admission(signed)
        assert not hasattr(signer, "sign")
        with pytest.raises(AuthorityServiceError, match="canonical admissions"):
            signer.sign_admission(object())  # type: ignore[arg-type]


def test_n07_q23_q24_cng_pin_mismatch_and_closed_handle_fail_closed() -> None:
    snapshot, signed, _, _ = _signed_fixture()
    del snapshot
    with CngAdmissionSigner.engineering_ephemeral(key_id="wrong-key") as other:
        assert not CngAdmissionVerifier(other.public_blob()).verify_admission(signed)
    with pytest.raises(AuthorityServiceError, match="closed"):
        other.sign_admission(signed.envelope)


def test_n01_q04_q07_authority_fence_blocks_transition_until_admission_finishes(
    tmp_path: Path,
) -> None:
    database = AuthorityDatabase(tmp_path / "authority.sqlite3")
    initial = _state()
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    successor = _state(1)
    completed: list[bytes] = []

    def transition() -> None:
        completed.append(database.publish(
            witness=witness, command_id="transition", payload_digest="a" * 64,
            successor=successor,
            result=(f'{{"state_digest":"{successor.state_digest}"}}').encode(
                "ascii"
            ),
        ))

    observed = datetime.now(timezone.utc)
    with database.admission_witness(
        witness=witness, observed_at=observed.isoformat()
    ) as fenced:
        successor = AuthorityState.create(**{
            **fenced.body(),
            "sequence": fenced.sequence + 1,
            "authority_head": "transition-head",
        })
        thread = Thread(target=transition)
        thread.start()
        time.sleep(0.05)
        assert thread.is_alive()
        assert database.read().authority_head == fenced.authority_head
    thread.join(5)
    assert not thread.is_alive()
    assert completed == [
        (f'{{"state_digest":"{successor.state_digest}"}}').encode("ascii")
    ]


def test_k01_k06_key_lifecycle_is_retained_and_monotonic() -> None:
    original = _state()
    provisioned = provision_verify_only(original, key_id="key-2", version=2)
    with pytest.raises(AuthorityServiceError, match="not retained SIGN_CURRENT"):
        require_signing_key(provisioned, key_id="key-2", version=2)
    activated = activate_key(provisioned, key_id="key-2", version=2)
    require_signing_key(activated, key_id="key-2", version=2)
    assert key_record("key-1", 1, SigningKeyStatus.VERIFY_ONLY) in activated.key_records
    retired = retire_key(activated, key_id="key-1", version=1)
    assert key_record("key-1", 1, SigningKeyStatus.RETIRED) in retired.key_records
    with pytest.raises(AuthorityServiceError, match="current signing key cannot retire"):
        retire_key(retired, key_id="key-2", version=2)
    assert original.current_key_id == "key-1"


def test_k02_k05_lifecycle_transitions_publish_through_apd(tmp_path: Path) -> None:
    database = AuthorityDatabase(tmp_path / "keys.sqlite3")
    initial = _state()
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    lifecycle = AnchoredKeyLifecycle(database, witness)
    provisioned = lifecycle.provision(
        command_id="key:provision:2", key_id="key-2", version=2
    )
    assert provisioned.current_key_id == "key-1"
    activated = lifecycle.activate(
        command_id="key:activate:2", key_id="key-2", version=2
    )
    assert activated.current_key_id == "key-2"
    retired = lifecycle.retire(
        command_id="key:retire:1", key_id="key-1", version=1
    )
    assert key_record("key-1", 1, SigningKeyStatus.RETIRED) in retired.key_records
    assert witness.pending is None
    assert witness.anchor == retired.state_digest


def _capital_snapshot() -> CapitalSnapshot:
    now = Timestamp(datetime.now(timezone.utc) - timedelta(seconds=1))
    expiry = Timestamp(datetime.now(timezone.utc) + timedelta(minutes=10))
    currency = Currency("USD")
    amounts = {
        "POSITIONS": "0", "CASH": "10000", "ACTIVE_RESERVATIONS": "2000",
        "PENDING_EFFECTS": "0", "UNRESOLVED_LIABILITIES": "3000",
        "CORPORATE_ACTIONS": "0",
    }
    components = tuple(
        FinancialComponent(
            kind, "portfolio-1", "run-1", 4, currency, Decimal(amount),
            f"source:{kind}", True,
        )
        for kind, amount in amounts.items()
    )
    body = {
        "snapshot_id": "snapshot-id-1", "portfolio_id": "portfolio-1",
        "run_scope": "run-1", "state_version": 4, "currency": "USD",
        "components": [
            {"kind": item.kind, "amount": str(item.amount),
             "source": item.source_identity, "complete": item.complete}
            for item in components
        ],
        "cutoff": now.value.isoformat(), "expires_at": expiry.value.isoformat(),
        "provenance_identity": "provider:source",
        "provider_authority": ProviderAuthority.AUTHORITATIVE_FINANCIAL.value,
    }
    return CapitalSnapshot(
        "snapshot-id-1", "portfolio-1", "run-1", 4, currency, components,
        now, expiry, "provider:source", ProviderAuthority.AUTHORITATIVE_FINANCIAL,
        _digest("ATIS-STAGE5-CAPITAL-SNAPSHOT-V1", body),
    )


class BoundSnapshotSource:
    def __init__(self, snapshot: CapitalSnapshot) -> None:
        self.snapshot = snapshot

    def resolve_snapshot(
        self, *, portfolio_id: str, run_scope: str, operation_purpose: str
    ) -> AuthoritativeSnapshotRecord:
        assert (portfolio_id, run_scope, operation_purpose) == (
            "portfolio-1", "run-1", "CAPITAL_SNAPSHOT"
        )
        return AuthoritativeSnapshotRecord(
            self.snapshot.snapshot_id, self.snapshot.content_digest,
            self.snapshot.state_version, self.snapshot.expires_at.value.isoformat(),
            self.snapshot.portfolio_id, self.snapshot.run_scope,
            "CAPITAL_SNAPSHOT", self.snapshot.currency.code,
            "descriptor-1", "provider-implementation-1", "policy-1", 1,
        )


class BoundTransport:
    def __init__(self, signed: SignedAdmission, lineage: AuthorityLineage) -> None:
        self.signed = signed
        self.lineage = lineage

    def request_admission(
        self, snapshot: CapitalSnapshot, operation_purpose: str
    ) -> SignedAdmission:
        del snapshot, operation_purpose
        return self.signed

    def current_authority(self) -> AuthorityLineage:
        return self.lineage

    @contextmanager
    def fenced_consumption(
        self, admission: SignedAdmission, operation_purpose: str
    ):
        assert admission == self.signed
        assert operation_purpose == "CAPITAL_SNAPSHOT"
        yield


class SequenceTransport(BoundTransport):
    def __init__(self, values: list[SignedAdmission], lineage: AuthorityLineage) -> None:
        super().__init__(values[0], lineage)
        self.values = values

    def request_admission(
        self, snapshot: CapitalSnapshot, operation_purpose: str
    ) -> SignedAdmission:
        del snapshot, operation_purpose
        return self.values.pop(0)


def _signed_fixture() -> tuple[
    CapitalSnapshot, SignedAdmission, AdmissionVerifier, AuthorityLineage
]:
    snapshot = _capital_snapshot()
    state = _state()
    secret = b"ENGINEERING_SIMULATION_ONLY"
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(state), signer=EngineeringSigner(secret),
        snapshot_source=BoundSnapshotSource(snapshot), clock=FixedClock(),
    )
    signed = service.admit(RuntimeAdmissionRequest(
        "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-1"
    ))
    verifier = AdmissionVerifier(
        verifier=EngineeringVerifier(secret), service_identity="service-1",
        deployment_identity="deployment-1", authority_domain_identity="domain-1",
        signing_key_id="key-1", signing_key_version=1,
    )
    return snapshot, signed, verifier, _lineage(state)


def test_t01_t02_t07_t16_injected_or_unavailable_client_fails_closed() -> None:
    snapshot, signed, verifier, lineage = _signed_fixture()
    injected = I01AuthorityServiceClient(BoundTransport(signed, lineage), verifier)
    with pytest.raises(AuthorityServiceError, match="ENGINEERING_ONLY"):
        injected.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")

    class Unavailable(BoundTransport):
        def request_admission(
            self, snapshot: CapitalSnapshot, operation_purpose: str
        ) -> SignedAdmission:
            raise OSError("offline")

    unavailable = object.__new__(I01AuthorityServiceClient)
    unavailable._transport = Unavailable(signed, lineage)
    unavailable._verifier = verifier
    unavailable._operational = True
    with pytest.raises(AuthorityServiceError, match="unavailable"):
        unavailable.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")


def test_t03_t04_t05_t06_t18_operational_factory_has_no_trust_inputs() -> None:
    import inspect
    import automated_trading_bot.stage5.authority_service as public_api

    assert tuple(inspect.signature(
        I01AuthorityServiceClient.from_protected_windows_deployment
    ).parameters) == ()
    assert not hasattr(public_api, "AdmissionVerifier")
    assert not hasattr(public_api, "CngAdmissionSigner")
    assert not hasattr(public_api, "AuthorityService")


def test_f03_b03_protected_host_composes_without_caller_trust_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect
    import automated_trading_bot.stage5.authority_service.composition as composition
    from automated_trading_bot.stage5.authority_service.windows_security import (
        WindowsDeploymentConfiguration,
    )

    state = _state()
    store = tmp_path / "authority.sqlite3"
    AuthorityDatabase(store).initialise(state)
    witness = Witness(state.state_digest)
    configuration = WindowsDeploymentConfiguration(
        "service-1", "deployment-1", "domain-1", str(store),
        r"\\.\pipe\runtime", r"\\.\pipe\management", "runtime-sid",
        "management-sid", "service-sid", "key-name", "key-1", 1,
        b"public", "protected.module:Factory",
    )

    class Ownership:
        def __init__(self, **_: object) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    signer = EngineeringSigner(b"engineering")
    monkeypatch.setattr(
        composition.WindowsDeploymentConfiguration, "load_protected",
        classmethod(lambda cls: configuration),
    )
    monkeypatch.setattr(composition, "WindowsRegistryWitness", lambda: witness)
    monkeypatch.setattr(composition, "WindowsDomainMutex", Ownership)
    monkeypatch.setattr(
        composition.CngAdmissionSigner, "open_provisioned",
        classmethod(lambda cls, **kwargs: signer),
    )
    monkeypatch.setattr(
        composition, "_load_snapshot_source", lambda path: SnapshotSource()
    )
    host = composition.ProtectedAuthorityServiceHost.from_protected_windows_deployment()
    assert host.endpoints.runtime is not host.endpoints.management
    assert tuple(inspect.signature(
        composition.ProtectedAuthorityServiceHost.from_protected_windows_deployment
    ).parameters) == ()


def test_t17_direct_verified_result_construction_rejects() -> None:
    snapshot, signed, _, _ = _signed_fixture()
    with pytest.raises(AuthorityServiceError, match="fixed verification path"):
        VerifiedOperationalAdmission(snapshot, signed)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        pytest.param("authority_domain_identity", "foreign", id="T09-X02-foreign-domain"),
        pytest.param("signing_key_id", "foreign-key", id="T10-X01-foreign-key"),
        pytest.param("signing_key_version", 2, id="T10-old-key-version"),
        pytest.param("store_epoch", "foreign-epoch", id="X10-store-epoch"),
        pytest.param("authority_id", "foreign-authority", id="Q20-authority-id"),
        pytest.param("authority_head", "stale-head", id="T11-X03-X11-stale-head"),
        pytest.param("authority_generation", 99, id="T11-stale-generation"),
        pytest.param("provider_descriptor_digest", "stale-provider", id="T12-X04-provider"),
        pytest.param(
            "provider_implementation_identity", "stale-implementation",
            id="Q08-provider-implementation",
        ),
        pytest.param("policy_id", "stale-policy", id="T13-X05-policy"),
        pytest.param("policy_version", 2, id="Q19-policy-version"),
    ],
)
def test_t09_t13_x01_x05_current_lineage_substitution_rejects(
    field: str, replacement: object
) -> None:
    snapshot, signed, verifier, lineage = _signed_fixture()
    altered = SignedAdmission(
        replace(signed.envelope, **{field: replacement}), signed.signature
    )
    client = object.__new__(I01AuthorityServiceClient)
    client._transport = BoundTransport(altered, lineage)
    client._verifier = verifier
    client._operational = True
    with pytest.raises(AuthorityServiceError, match="mismatch|invalid service admission"):
        client.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        pytest.param("service_identity", "foreign-service", id="T09-X01-service"),
        pytest.param("deployment_identity", "foreign-deployment", id="T09-deployment"),
        pytest.param("snapshot_id", "foreign-snapshot", id="T14-X06-snapshot-id"),
        pytest.param("snapshot_version", 99, id="T14-X06-snapshot-version"),
        pytest.param("currency", "GBP", id="Q09-currency"),
        pytest.param("portfolio_id", "foreign-portfolio", id="T15-X07-portfolio"),
        pytest.param("run_scope", "foreign-run", id="T15-X07-run"),
        pytest.param("operation_purpose", "FOREIGN", id="T15-X07-purpose"),
    ],
)
def test_t09_t15_x01_x07_envelope_substitution_rejects(
    field: str, replacement: object
) -> None:
    snapshot, signed, verifier, lineage = _signed_fixture()
    mutated = SignedAdmission(replace(signed.envelope, **{field: replacement}), signed.signature)
    client = object.__new__(I01AuthorityServiceClient)
    client._transport = BoundTransport(mutated, lineage)
    client._verifier = verifier
    client._operational = True
    with pytest.raises(AuthorityServiceError, match="mismatch"):
        client.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")


def test_t19_x12_exact_fixed_lineage_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        WindowsRuntimePipeTransport,
    )
    snapshot, signed, verifier, lineage = _signed_fixture()
    transport = BoundTransport(signed, lineage)
    monkeypatch.setattr(
        WindowsRuntimePipeTransport, "from_protected_windows_deployment",
        classmethod(lambda cls: (transport, verifier)),
    )
    client = I01AuthorityServiceClient.from_protected_windows_deployment()
    admitted = client.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")
    refreshed = client.require_current(
        admitted, operation_purpose="CAPITAL_SNAPSHOT"
    )
    assert refreshed.snapshot is admitted.snapshot
    assert refreshed.signed_admission == signed


def test_t11_revalidation_rejects_freshly_signed_superseding_head() -> None:
    snapshot, original, verifier, lineage = _signed_fixture()
    secret = b"ENGINEERING_SIMULATION_ONLY"
    successor = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(_state(1)), signer=EngineeringSigner(secret),
        snapshot_source=BoundSnapshotSource(snapshot), clock=FixedClock(),
    ).admit(RuntimeAdmissionRequest(
        "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "nonce-2"
    ))
    client = object.__new__(I01AuthorityServiceClient)
    client._transport = SequenceTransport([original, successor], lineage)
    client._verifier = verifier
    client._operational = True
    admitted = client.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")
    with pytest.raises(AuthorityServiceError, match="no longer current"):
        client.require_current(admitted, operation_purpose="CAPITAL_SNAPSHOT")


def test_t11_runtime_transport_rejects_replayed_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        WindowsRuntimePipeTransport,
    )
    from automated_trading_bot.stage5.authority_service.windows_security import (
        WindowsDeploymentConfiguration,
    )

    snapshot, signed, _, _ = _signed_fixture()
    transport = object.__new__(WindowsRuntimePipeTransport)
    transport._configuration = WindowsDeploymentConfiguration(
        "service-1", "deployment-1", "domain-1", "store",
        r"\\.\pipe\runtime", r"\\.\pipe\management", "runtime-sid",
        "management-sid", "service-sid", "key-name", "key-1", 1, b"public",
    )
    monkeypatch.setattr(
        transport, "_request", lambda request: {"signed_admission": signed.wire()}
    )
    with pytest.raises(AuthorityServiceError, match="replay detected"):
        transport.request_admission(snapshot, "CAPITAL_SNAPSHOT")


def test_t17_t18_forged_wrapper_still_requires_fixed_consumer_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automated_trading_bot.stage5.authority_service import (
        require_current_operational_admission,
    )
    from automated_trading_bot.stage5.authority_service.ipc import (
        WindowsRuntimePipeTransport,
    )

    snapshot, signed, verifier, lineage = _signed_fixture()
    forged = SignedAdmission(signed.envelope, b"forged")
    apparent = VerifiedOperationalAdmission._verified(snapshot, forged)
    transport = SequenceTransport([signed], lineage)
    monkeypatch.setattr(
        WindowsRuntimePipeTransport, "from_protected_windows_deployment",
        classmethod(lambda cls: (transport, verifier)),
    )
    with pytest.raises(AuthorityServiceError, match="invalid service admission"):
        require_current_operational_admission(
            apparent, operation_purpose="CAPITAL_SNAPSHOT"
        )


def test_runtime_and_management_protocols_are_disjoint_and_domain_bound() -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        AuthorityManagementHandler,
        AuthorityRuntimeHandler,
    )
    from automated_trading_bot.stage5.authority_service.protocol import PROTOCOL

    snapshot, _, _, _ = _signed_fixture()
    service = AuthorityService(
        service_identity="service-1", deployment_identity="deployment-1",
        authority=Current(_state()), signer=EngineeringSigner(b"engineering"),
        snapshot_source=BoundSnapshotSource(snapshot), clock=FixedClock(),
    )
    runtime = AuthorityRuntimeHandler(service, domain_identity="domain-1")
    management = AuthorityManagementHandler(
        lambda request: {"accepted": request["operation"]},
        domain_identity="domain-1",
    )
    common = {"protocol": PROTOCOL, "expected_domain_identity": "domain-1"}
    with pytest.raises(AuthorityServiceError, match="Runtime operation"):
        runtime({**common, "operation": "CURRENT_AUTHORITY"})
    with pytest.raises(AuthorityServiceError, match="Runtime operation"):
        runtime({**common, "operation": "REVOKE"})
    with pytest.raises(AuthorityServiceError, match="Management operation"):
        management({**common, "operation": "ADMIT"})
    assert management({**common, "operation": "REVOKE"}) == {
        "accepted": "REVOKE"
    }
    with pytest.raises(AuthorityServiceError, match="foreign authority domain"):
        runtime({**common, "expected_domain_identity": "foreign",
                 "operation": "ADMIT"})


def test_native_windows_pipe_dacl_peer_and_framing_round_trip() -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        NamedPipeEndpoint,
        decode_frame,
        encode_frame,
    )

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        sid = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        )
    finally:
        token.Close()
    pipe_name = rf"\\.\pipe\atis-i01-{uuid4()}"
    failures: list[BaseException] = []
    endpoint = NamedPipeEndpoint(
        pipe_name=pipe_name, service_sid=sid, client_sid=sid,
        handler=lambda request: {"echo": request["value"]},
    )

    def serve() -> None:
        try:
            endpoint.serve_once()
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=serve)
    thread.start()
    deadline = time.monotonic() + 5
    while True:
        try:
            win32pipe.WaitNamedPipe(pipe_name, 5000)
            break
        except Exception as error:
            error_code = getattr(error, "winerror", None)
            if error_code is None and error.args:
                error_code = error.args[0]
            if failures:
                raise failures[0]
            if error_code != 2 or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    handle = win32file.CreateFile(
        pipe_name, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        0, None, win32con.OPEN_EXISTING, 0, None,
    )
    try:
        win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
        )
        win32file.WriteFile(handle, encode_frame({"value": "native"}))
        time.sleep(0.05)
        if failures:
            raise failures[0]
        _, response = win32file.ReadFile(handle, 1024)
        assert decode_frame(bytes(response)) == {"echo": "native"}
    finally:
        win32file.CloseHandle(handle)
    thread.join(5)
    assert not thread.is_alive()
    assert failures == []


def _operational_fence_fixture(
    tmp_path: Path,
) -> tuple[
    AuthorityDatabase,
    Witness,
    ProtectedAuthorityManager,
    AuthorityService,
    SignedAdmission,
    CapitalSnapshot,
]:
    from automated_trading_bot.stage5.authority_service.composition import (
        DatabaseCurrentAuthority,
    )

    database = AuthorityDatabase(tmp_path / "fenced-authority.sqlite3")
    initial = _state()
    database.initialise(initial)
    witness = Witness(initial.state_digest)
    snapshot = _capital_snapshot()
    secret = b"ENGINEERING_SIMULATION_ONLY"
    verifier = EngineeringVerifier(secret)
    service = AuthorityService(
        service_identity="service-1",
        deployment_identity="deployment-1",
        authority=DatabaseCurrentAuthority(database, witness),
        signer=EngineeringSigner(secret),
        snapshot_source=BoundSnapshotSource(snapshot),
        clock=FixedClock(),
        admission_verifier=verifier,
    )
    signed = service.admit(RuntimeAdmissionRequest(
        "portfolio-1", "run-1", "CAPITAL_SNAPSHOT", "domain-1", "fence-admit"
    ))
    return (
        database,
        witness,
        ProtectedAuthorityManager(database, witness),
        service,
        signed,
        snapshot,
    )


def _wait_for_test_pipe(pipe_name: str, failures: list[BaseException]) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            win32pipe.WaitNamedPipe(pipe_name, 5000)
            return
        except Exception as error:
            error_code = getattr(error, "winerror", None)
            if error_code is None and error.args:
                error_code = error.args[0]
            if failures:
                raise failures[0]
            if error_code != 2 or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def test_fence_01_14_exact_current_path_holds_until_consumer_exit(
    tmp_path: Path,
) -> None:
    database, _, _, service, signed, _ = _operational_fence_fixture(tmp_path)
    entered = False
    with service.consumption_fence(
        signed, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=1.0
    ) as envelope:
        entered = True
        assert envelope.authority_head == database.read().authority_head
    assert entered


@pytest.mark.parametrize(
    "status",
    [RetainedAuthorityStatus.REVOKED, RetainedAuthorityStatus.SUPERSEDED],
)
def test_fence_02_03_13_noncurrent_authority_rejects_before_entry(
    tmp_path: Path, status: RetainedAuthorityStatus,
) -> None:
    database, witness, _, service, signed, _ = _operational_fence_fixture(tmp_path)
    current = database.read()
    body = current.body()
    body.update(sequence=current.sequence + 1, authority_status=status)
    body["authority_head"] = f"{status.value.lower()}-head"
    successor = AuthorityState.create(**body)
    database.publish(
        witness=witness,
        command_id=f"set-{status.value}",
        payload_digest=status.value.lower().ljust(64, "0")[:64],
        successor=successor,
        result=(
            f'{{"state_digest":"{successor.state_digest}"}}'.encode("ascii")
        ),
    )
    with pytest.raises(AuthorityServiceError, match="not active|not current"):
        with service.consumption_fence(
            signed, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=0.5
        ):
            pytest.fail("stale authority entered fenced section")


def test_fence_04_05_06_writer_waits_and_new_consumer_cannot_overtake(
    tmp_path: Path,
) -> None:
    database, _, manager, service, signed, _ = _operational_fence_fixture(tmp_path)
    events: list[str] = []
    writer_started = Event()

    def revoke() -> None:
        writer_started.set()
        current = database.read()
        manager({
            "operation": "REVOKE",
            "command_id": "fenced-revoke",
            "expected_authority_head": current.authority_head,
        })
        events.append("writer")

    def late_consumer() -> None:
        try:
            with service.consumption_fence(
                signed, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=1.0
            ):
                events.append("late-consumer-entered")
        except AuthorityServiceError:
            events.append("late-consumer-rejected")

    with service.consumption_fence(
        signed, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=1.0
    ):
        writer = Thread(target=revoke)
        writer.start()
        assert writer_started.wait(1)
        time.sleep(0.05)
        assert writer.is_alive()
        consumer = Thread(target=late_consumer)
        consumer.start()
        time.sleep(0.05)
        assert consumer.is_alive()
        assert events == []
    writer.join(2)
    consumer.join(2)
    assert events == ["writer", "late-consumer-rejected"]


def test_fence_07_08_exception_releases_and_acquisition_timeout_fails_closed(
    tmp_path: Path,
) -> None:
    database, _, manager, service, signed, _ = _operational_fence_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="simulated consumer crash"):
        with service.consumption_fence(
            signed, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=1.0
        ):
            with pytest.raises(AuthorityServiceError, match="timed out"):
                with database._exclusive(timeout=0.05):
                    pytest.fail("writer acquired active consumer fence")
            raise RuntimeError("simulated consumer crash")
    current = database.read()
    result = manager({
        "operation": "REVOKE",
        "command_id": "post-crash-revoke",
        "expected_authority_head": current.authority_head,
    })
    assert "post-crash-revoke" in result["result"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority_domain_identity", "foreign-domain"),
        ("store_epoch", "foreign-epoch"),
        ("authority_head", "foreign-head"),
        ("authority_generation", 999),
        ("authority_id", "foreign-authority"),
        ("policy_version", 999),
        ("snapshot_version", 999),
        ("portfolio_id", "foreign-portfolio"),
    ],
)
def test_fence_09_exact_signed_lineage_substitution_rejects(
    tmp_path: Path, field: str, value: object,
) -> None:
    _, _, _, service, signed, _ = _operational_fence_fixture(tmp_path)
    signer = EngineeringSigner(b"ENGINEERING_SIMULATION_ONLY")
    envelope = replace(signed.envelope, **{field: value})
    altered = SignedAdmission(envelope, signer.sign_admission(envelope))
    with pytest.raises(AuthorityServiceError, match="not current|snapshot"):
        with service.consumption_fence(
            altered, operation_purpose="CAPITAL_SNAPSHOT", timeout_seconds=0.5
        ):
            pytest.fail("foreign lineage entered fenced section")


def test_fence_10_11_12_session_is_one_shot_and_engineering_fence_cannot_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automated_trading_bot.stage5.authority_service import (
        operational_authority_fence,
    )
    from automated_trading_bot.stage5.authority_service.ipc import (
        WindowsRuntimePipeTransport,
    )
    from automated_trading_bot.stage5.provider_authority import AuthorityFence

    snapshot, signed, verifier, lineage = _signed_fixture()
    transport = BoundTransport(signed, lineage)
    monkeypatch.setattr(
        WindowsRuntimePipeTransport,
        "from_protected_windows_deployment",
        classmethod(lambda cls: (transport, verifier)),
    )
    client = I01AuthorityServiceClient.from_protected_windows_deployment()
    admitted = client.admit(snapshot, operation_purpose="CAPITAL_SNAPSHOT")
    context = operational_authority_fence(
        admitted, operation_purpose="CAPITAL_SNAPSHOT"
    )
    with context as fenced:
        assert fenced is admitted
    with pytest.raises((AttributeError, RuntimeError)):
        with context:
            pytest.fail("released fence was replayed")
    with pytest.raises(AuthorityServiceError, match="fixed operational verification"):
        with client.fenced_consumption(
            object.__new__(AuthorityFence),  # type: ignore[arg-type]
            operation_purpose="CAPITAL_SNAPSHOT",
        ):
            pytest.fail("engineering fence substituted operational admission")
    assert not hasattr(client, "capital_cas")
    assert not hasattr(client, "reserve_capital")


def test_fence_01_runtime_ipc_holds_and_releases_on_same_authenticated_connection(
    tmp_path: Path,
) -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        AuthorityRuntimeHandler,
        NamedPipeEndpoint,
        WindowsRuntimePipeTransport,
    )
    from automated_trading_bot.stage5.authority_service.windows_security import (
        WindowsDeploymentConfiguration,
    )

    _, _, _, service, signed, _ = _operational_fence_fixture(tmp_path)
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        sid = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        )
    finally:
        token.Close()
    pipe_name = rf"\\.\pipe\atis-i01-fence-{uuid4()}"
    endpoint = NamedPipeEndpoint(
        pipe_name=pipe_name,
        service_sid=sid,
        client_sid=sid,
        handler=AuthorityRuntimeHandler(service, domain_identity="domain-1"),
    )
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            endpoint.serve_once()
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=serve)
    thread.start()
    _wait_for_test_pipe(pipe_name, failures)
    transport = object.__new__(WindowsRuntimePipeTransport)
    transport._configuration = WindowsDeploymentConfiguration(
        "service-1", "deployment-1", "domain-1", "store",
        pipe_name, rf"\\.\pipe\unused-{uuid4()}", sid, sid, sid,
        "key-name", "key-1", 1, b"public",
    )
    with transport.fenced_consumption(signed, "CAPITAL_SNAPSHOT"):
        assert thread.is_alive()
    thread.join(5)
    assert not thread.is_alive()
    assert failures == []


def test_fence_07_runtime_disconnect_releases_without_manufacturing_result(
    tmp_path: Path,
) -> None:
    from automated_trading_bot.stage5.authority_service.ipc import (
        AuthorityRuntimeHandler,
        NamedPipeEndpoint,
        decode_frame,
        encode_frame,
    )
    from automated_trading_bot.stage5.authority_service.protocol import PROTOCOL

    database, _, manager, service, signed, _ = _operational_fence_fixture(tmp_path)
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        sid = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        )
    finally:
        token.Close()
    pipe_name = rf"\\.\pipe\atis-i01-fence-crash-{uuid4()}"
    endpoint = NamedPipeEndpoint(
        pipe_name=pipe_name,
        service_sid=sid,
        client_sid=sid,
        handler=AuthorityRuntimeHandler(service, domain_identity="domain-1"),
    )
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            endpoint.serve_once()
        except AuthorityServiceError as error:
            failures.append(error)

    thread = Thread(target=serve)
    thread.start()
    _wait_for_test_pipe(pipe_name, failures)
    handle = win32file.CreateFile(
        pipe_name, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        0, None, win32con.OPEN_EXISTING, 0, None,
    )
    win32pipe.SetNamedPipeHandleState(
        handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
    )
    win32file.WriteFile(handle, encode_frame({
        "protocol": PROTOCOL,
        "operation": "BEGIN_FENCED_CONSUMPTION",
        "expected_domain_identity": "domain-1",
        "operation_purpose": "CAPITAL_SNAPSHOT",
        "fence_nonce": "crash-fence",
        "signed_admission": signed.wire(),
    }))
    _, raw = win32file.ReadFile(handle, 1024 * 1024 + 4)
    assert decode_frame(bytes(raw))["operation"] == "FENCED_CONSUMPTION_ACQUIRED"
    win32file.CloseHandle(handle)
    thread.join(5)
    assert not thread.is_alive()
    assert len(failures) == 1
    assert "disconnected" in str(failures[0])
    current = database.read()
    result = manager({
        "operation": "REVOKE",
        "command_id": "post-disconnect-revoke",
        "expected_authority_head": current.authority_head,
    })
    assert "post-disconnect-revoke" in result["result"]

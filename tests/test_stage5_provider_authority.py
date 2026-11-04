from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import pytest

from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.stage5.contracts import (
    CapitalSnapshot,
    CapitalSnapshotError,
    CapitalSnapshotRequest,
    FinancialComponent,
    ProviderAuthority,
    _digest,
)
from automated_trading_bot.stage5.provider_authority import (
    AdmittedCapitalSnapshot,
    AuthorityScope,
    ProviderAuthorityBootstrap,
    ProviderAuthorityError,
    ProviderAuthorityManager,
    ProviderAuthorityRuntime,
    ProviderImplementationDescriptor,
    _establish_owner_composition,
)


NOW = Timestamp(datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
LATER = Timestamp(NOW.value + timedelta(hours=1))
USD = Currency("USD")


@dataclass
class Clock:
    value: Timestamp

    def now(self) -> Timestamp:
        return self.value


def descriptor(*, code: str = "code:qualified:v1") -> ProviderImplementationDescriptor:
    return ProviderImplementationDescriptor.create(
        port_contract_identity="stage5:capital-snapshot-port:v1",
        code_package_identity=code,
        adapter_identity="adapter:qualified:v1",
        configuration_identity="config:accepted:v1",
        build_dependency_identity="lock:sha256:abc",
        deployment_instance_identity="deployment:owner:1",
    )


def capital_snapshot() -> CapitalSnapshot:
    amounts = {
        "POSITIONS": "0",
        "CASH": "10000",
        "ACTIVE_RESERVATIONS": "2000",
        "PENDING_EFFECTS": "0",
        "UNRESOLVED_LIABILITIES": "3000",
        "CORPORATE_ACTIONS": "0",
    }
    components = tuple(
        FinancialComponent(
            kind, "P1", "R1", 7, USD, Decimal(amount), f"source:{kind}", True
        )
        for kind, amount in amounts.items()
    )
    body = {
        "snapshot_id": "S1",
        "portfolio_id": "P1",
        "run_scope": "R1",
        "state_version": 7,
        "currency": "USD",
        "components": [
            {
                "kind": item.kind,
                "amount": str(item.amount),
                "source": item.source_identity,
                "complete": item.complete,
            }
            for item in components
        ],
        "cutoff": NOW.value.isoformat(),
        "expires_at": LATER.value.isoformat(),
        "provenance_identity": "provider:stage6",
        "provider_authority": "AUTHORITATIVE_FINANCIAL",
    }
    return CapitalSnapshot(
        "S1", "P1", "R1", 7, USD, components, NOW, LATER,
        "provider:stage6", ProviderAuthority.AUTHORITATIVE_FINANCIAL,
        _digest("ATIS-STAGE5-CAPITAL-SNAPSHOT-V1", body),
    )


class Provider:
    authority = ProviderAuthority.AUTHORITATIVE_FINANCIAL
    provider_identity = "provider:stage6"
    authority_policy_id = "policy:financial:v1"

    def __init__(
        self,
        value: CapitalSnapshot,
        implementation_descriptor: ProviderImplementationDescriptor,
    ) -> None:
        self.value = value
        self.implementation_descriptor = implementation_descriptor

    def snapshot(self, request: CapitalSnapshotRequest) -> CapitalSnapshot:
        self.value.require_current(
            portfolio_id=request.portfolio_id,
            run_scope=request.run_scope,
            at=request.at,
            operational=request.operational,
        )
        return self.value


class Resolver:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def resolve(self, provider_identity: str) -> Provider:
        if provider_identity != self.provider.provider_identity:
            raise ProviderAuthorityError("provider is not owner-enrolled")
        return self.provider


def configured(tmp_path: Path, *, timeout: float = 1.0) -> tuple[
    ProviderAuthorityManager, ProviderAuthorityRuntime, Clock
]:
    clock = Clock(NOW)
    provider_descriptor = descriptor()
    bootstrap = ProviderAuthorityBootstrap(tmp_path).establish(
        command_id="bootstrap:1",
        store_epoch="epoch:1",
        policy_id="policy:financial:v1",
        roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    management = ProviderAuthorityManager(tmp_path, clock=clock, timeout_seconds=timeout)
    management.activate(
        command_id="activate:1",
        expected_head=bootstrap.empty_head_digest,
        provider_identity="provider:stage6",
        descriptor=provider_descriptor,
        role="CAPITAL_SNAPSHOT",
        scope=AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),
        policy_id="policy:financial:v1",
        valid_from=NOW,
        expires_at=LATER,
    )
    runtime = ProviderAuthorityRuntime(
        tmp_path,
        resolver=Resolver(Provider(capital_snapshot(), provider_descriptor)),
        clock=clock,
        timeout_seconds=timeout,
    )
    return management, runtime, clock


def test_bootstrap_is_empty_idempotent_and_conflicts_fail(tmp_path: Path) -> None:
    bootstrap = ProviderAuthorityBootstrap(tmp_path)
    first = bootstrap.establish(
        command_id="bootstrap:1", store_epoch="epoch:1",
        policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    second = bootstrap.establish(
        command_id="bootstrap:1", store_epoch="epoch:1",
        policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    assert first == second
    runtime = ProviderAuthorityRuntime(
        tmp_path,
        resolver=Resolver(Provider(capital_snapshot(), descriptor())),
        clock=Clock(NOW),
    )
    with pytest.raises(ProviderAuthorityError, match="fixed Authority Service"):
        runtime.admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    with pytest.raises(ProviderAuthorityError, match="conflicting second bootstrap"):
        bootstrap.establish(
            command_id="bootstrap:2", store_epoch="epoch:2",
            policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
            scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
            trusted_at=NOW,
        )


def test_current_authority_admits_distinct_snapshot(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)
    admitted = runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    assert type(admitted) is AdmittedCapitalSnapshot
    assert admitted.snapshot.maximum_new_reservation() == Decimal("5000")
    assert admitted.authority_generation == 1
    assert admitted.valid_until == LATER
    assert not hasattr(admitted, "reserve")
    assert not hasattr(admitted, "approve")


def test_caller_cannot_supply_trust_store_or_resolver() -> None:
    fields = CapitalSnapshotRequest.__dataclass_fields__
    assert set(fields) == {"portfolio_id", "run_scope", "currency", "at", "operational"}
    from automated_trading_bot.stage5 import contracts
    import automated_trading_bot.stage5 as stage5

    assert not hasattr(contracts, "FinancialProviderTrust")
    assert not hasattr(contracts, "admit_operational_snapshot")
    assert not hasattr(stage5, "ProviderAuthorityBootstrap")
    assert not hasattr(stage5, "ProviderAuthorityManager")
    assert not hasattr(stage5, "ProviderAuthorityRuntime")


def test_p01_p10_legacy_owner_composition_is_permanently_disabled(tmp_path: Path) -> None:
    with pytest.raises(ProviderAuthorityError, match="permanently disabled"):
        _establish_owner_composition(
            tmp_path, command_id="forged", store_epoch="forged",
            policy_id="forged", roles=("CAPITAL_SNAPSHOT",),
            scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
            trusted_at=NOW,
            resolver=Resolver(Provider(capital_snapshot(), descriptor())),
            resolver_identity="caller", clock=Clock(NOW),
            clock_policy_identity="caller", deployment_identity="caller",
        )


def test_c1_c2_caller_domain_and_direct_runtime_are_non_operational(
    tmp_path: Path,
) -> None:
    clock = Clock(NOW)
    desc = descriptor()
    bootstrap = ProviderAuthorityBootstrap(tmp_path)
    evidence = bootstrap.establish(
        command_id="attacker:bootstrap", store_epoch="attacker:epoch",
        policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    manager = ProviderAuthorityManager(tmp_path, clock=clock)
    manager.activate(
        command_id="attacker:activate", expected_head=evidence.empty_head_digest,
        provider_identity="provider:stage6", descriptor=desc,
        role="CAPITAL_SNAPSHOT",
        scope=AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),
        policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
    )
    runtime = ProviderAuthorityRuntime(
        tmp_path, resolver=Resolver(Provider(capital_snapshot(), desc)), clock=clock
    )
    with pytest.raises(ProviderAuthorityError, match="fixed Authority Service"):
        runtime.admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))


def test_c3_c11_foreign_domain_admission_rejects(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _, runtime_a, _ = configured(root_a)
    _, runtime_b, _ = configured(root_b)
    admitted_a = runtime_a._engineering_admit(
        CapitalSnapshotRequest("P1", "R1", USD, NOW, True)
    )
    assert admitted_a.authority_domain_identity == runtime_a.current_head().authority_domain_identity
    with pytest.raises(ProviderAuthorityError, match="STALE_AUTHORITY"):
        with runtime_b.authority_fence(admitted_a):
            pytest.fail("foreign admission entered Domain B fence")


def test_c5_runtime_cannot_escalate_to_management(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)
    for name in ("establish", "activate", "rotate", "revoke", "expire"):
        assert not hasattr(runtime, name)


def test_rotation_invalidates_old_admission_and_requires_new_descriptor(
    tmp_path: Path,
) -> None:
    manager, runtime, _ = configured(tmp_path)
    admitted = runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    prior = runtime.current_head()
    changed = descriptor(code="code:qualified:v2")
    result = manager.rotate(
        command_id="rotate:1", expected_head=prior.terminal_digest,
        provider_identity="provider:stage6:v2", descriptor=changed,
        role="CAPITAL_SNAPSHOT",
        scope=AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),
        policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
    )
    assert result.active is not None and result.active.generation == 2
    with pytest.raises(ProviderAuthorityError, match="STALE_AUTHORITY"):
        with runtime.authority_fence(admitted):
            pytest.fail("stale admission entered authority fence")


def test_revoked_but_unexpired_authority_rejects(tmp_path: Path) -> None:
    manager, runtime, _ = configured(tmp_path)
    admitted = runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    head = runtime.current_head()
    manager.revoke(command_id="revoke:1", expected_head=head.terminal_digest)
    with pytest.raises(ProviderAuthorityError, match="NOT_ESTABLISHED"):
        runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    with pytest.raises(ProviderAuthorityError, match="STALE_AUTHORITY"):
        with runtime.authority_fence(admitted):
            pytest.fail("revoked admission entered authority fence")


def test_reauthorisation_creates_new_generation_without_resurrection(
    tmp_path: Path,
) -> None:
    manager, runtime, _ = configured(tmp_path)
    active = runtime.current_head()
    revoked = manager.revoke(
        command_id="revoke:1", expected_head=active.terminal_digest
    )
    restored = manager.activate(
        command_id="activate:2", expected_head=revoked.terminal_digest,
        provider_identity="provider:stage6", descriptor=descriptor(),
        role="CAPITAL_SNAPSHOT",
        scope=AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),
        policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
    )
    assert restored.active is not None
    assert restored.active.generation == 2
    assert restored.active.authority_id != active.last_authority_id
    assert restored.active.predecessor_authority_id == active.last_authority_id


def test_stale_head_and_conflicting_duplicate_reject(tmp_path: Path) -> None:
    manager, runtime, _ = configured(tmp_path)
    head = runtime.current_head()
    manager.revoke(command_id="revoke:1", expected_head=head.terminal_digest)
    with pytest.raises(ProviderAuthorityError, match="stale authority head"):
        manager.activate(
            command_id="activate:2", expected_head=head.terminal_digest,
            provider_identity="provider:stage6", descriptor=descriptor(),
            role="CAPITAL_SNAPSHOT",
            scope=AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),
            policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
        )


def test_identical_command_retry_is_idempotent_and_changed_payload_rejects(
    tmp_path: Path,
) -> None:
    clock = Clock(NOW)
    evidence = ProviderAuthorityBootstrap(tmp_path).establish(
        command_id="bootstrap:1", store_epoch="epoch:1",
        policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    manager = ProviderAuthorityManager(tmp_path, clock=clock)
    scope = AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT")
    original_descriptor = descriptor()
    first = manager.activate(
        command_id="activate:1", expected_head=evidence.empty_head_digest,
        provider_identity="provider:stage6", descriptor=original_descriptor,
        role="CAPITAL_SNAPSHOT", scope=scope,
        policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
    )
    assert manager.activate(
        command_id="activate:1", expected_head=evidence.empty_head_digest,
        provider_identity="provider:stage6", descriptor=original_descriptor,
        role="CAPITAL_SNAPSHOT", scope=scope,
        policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
    ) == first
    with pytest.raises(ProviderAuthorityError, match="conflicting duplicate"):
        manager.activate(
            command_id="activate:1", expected_head=evidence.empty_head_digest,
            provider_identity="provider:stage6",
            descriptor=descriptor(code="code:qualified:changed"),
            role="CAPITAL_SNAPSHOT", scope=scope,
            policy_id="policy:financial:v1", valid_from=NOW, expires_at=LATER,
        )


def test_invalid_validity_boundaries_reject(tmp_path: Path) -> None:
    bootstrap = ProviderAuthorityBootstrap(tmp_path)
    evidence = bootstrap.establish(
        command_id="bootstrap:1", store_epoch="epoch:1",
        policy_id="policy:financial:v1", roles=("CAPITAL_SNAPSHOT",),
        scopes=(AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT"),),
        trusted_at=NOW,
    )
    manager = ProviderAuthorityManager(tmp_path, clock=Clock(NOW))
    provider_descriptor = descriptor()
    scope = AuthorityScope("P1", "R1", USD, "CAPITAL_SNAPSHOT")
    with pytest.raises(ProviderAuthorityError, match="exceeds 24 hours"):
        manager.activate(
            command_id="long", expected_head=evidence.empty_head_digest,
            provider_identity="provider:stage6", descriptor=provider_descriptor,
            role="CAPITAL_SNAPSHOT", scope=scope,
            policy_id="policy:financial:v1", valid_from=NOW,
            expires_at=Timestamp(NOW.value + timedelta(hours=25)),
        )
    with pytest.raises(ProviderAuthorityError, match="not yet valid"):
        manager.activate(
            command_id="future", expected_head=evidence.empty_head_digest,
            provider_identity="provider:stage6", descriptor=provider_descriptor,
            role="CAPITAL_SNAPSHOT", scope=scope,
            policy_id="policy:financial:v1",
            valid_from=Timestamp(NOW.value + timedelta(minutes=1)),
            expires_at=LATER,
        )


def test_backwards_clock_and_recovery_fail_closed(tmp_path: Path) -> None:
    _, runtime, clock = configured(tmp_path)
    clock.value = Timestamp(NOW.value + timedelta(minutes=10))
    runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, clock.value, True))
    restarted_clock = Clock(NOW)
    restarted = ProviderAuthorityRuntime(
        tmp_path,
        resolver=Resolver(Provider(capital_snapshot(), descriptor())),
        clock=restarted_clock,
    )
    with pytest.raises(ProviderAuthorityError, match="moved backwards"):
        restarted._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))


def test_unavailable_clock_fails_closed(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)

    class UnavailableClock:
        def now(self) -> Timestamp:
            raise OSError("clock unavailable")

    failed = ProviderAuthorityRuntime(
        tmp_path,
        resolver=Resolver(Provider(capital_snapshot(), descriptor())),
        clock=UnavailableClock(),
    )
    with pytest.raises(ProviderAuthorityError, match="clock is unavailable"):
        failed._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))


def test_provider_self_report_cannot_establish_descriptor(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)
    wrong = Provider(capital_snapshot(), descriptor(code="self-reported"))
    head = runtime.current_head()
    with pytest.raises(ProviderAuthorityError, match="permanently ENGINEERING_ONLY"):
        ProviderAuthorityRuntime._for_owner_composition(
            tmp_path, resolver=Resolver(wrong), clock=Clock(NOW),
            authority_domain_identity=head.authority_domain_identity,
            timeout_seconds=1.0,
        )
    assert runtime.current_head().active is not None


def test_foreign_scope_and_engineering_provider_reject(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)
    with pytest.raises(ProviderAuthorityError, match="foreign authority scope"):
        runtime._engineering_admit(CapitalSnapshotRequest("P2", "R1", USD, NOW, True))


def test_corrupt_head_and_truncated_history_reject(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path)
    head_path = tmp_path / "head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head["terminal_digest"] = "0" * 64
    head_path.write_text(
        json.dumps(head, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(ProviderAuthorityError, match="rollback or corruption"):
        runtime.current_head()


def test_authority_fence_blocks_management_and_times_out(tmp_path: Path) -> None:
    manager, runtime, _ = configured(tmp_path, timeout=0.05)
    admitted = runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    with pytest.raises(ProviderAuthorityError, match="STALE_AUTHORITY"):
        with runtime.authority_fence(admitted):
            pytest.fail("engineering admission entered operational fence")
    manager.revoke(command_id="revoke:1", expected_head=admitted.authority_head_digest)
    assert runtime.current_head().active is None


def test_authority_fence_is_cross_process_and_crash_released(tmp_path: Path) -> None:
    manager, runtime, _ = configured(tmp_path, timeout=0.05)
    head = runtime.current_head()
    code = (
        "from pathlib import Path; import sys,time; "
        "from automated_trading_bot.stage5.provider_authority import _ProcessFileLock; "
        "lock=_ProcessFileLock(Path(sys.argv[1]),2); lock.__enter__(); "
        "print('LOCKED',flush=True); time.sleep(10)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "authority.fence")],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdout.readline().strip() == "LOCKED"
    with pytest.raises(ProviderAuthorityError, match="timed out"):
        manager.revoke(command_id="revoke:blocked", expected_head=head.terminal_digest)
    process.kill()
    process.wait(timeout=5)
    result = manager.revoke(
        command_id="revoke:after-crash", expected_head=head.terminal_digest
    )
    assert result.active is None


def test_c7_waiting_writer_blocks_new_readers_and_progresses(tmp_path: Path) -> None:
    manager, runtime, _ = configured(tmp_path, timeout=2.0)
    head = runtime.current_head()
    code = (
        "from pathlib import Path; import sys,time; "
        "from automated_trading_bot.stage5.provider_authority import _AuthorityStore; "
        "store=_AuthorityStore(Path(sys.argv[1]),timeout_seconds=2); "
        "print('READY',flush=True); "
        "exec('for _ in range(100):\\n with store.reader_fence():\\n  time.sleep(.02)')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdout.readline().strip() == "READY"
    started = datetime.now(timezone.utc)
    result = manager.revoke(
        command_id="revoke:writer-progress", expected_head=head.terminal_digest
    )
    elapsed = datetime.now(timezone.utc) - started
    process.kill()
    process.wait(timeout=5)
    assert result.active is None
    assert elapsed < timedelta(seconds=2)


def test_c9_writer_crash_before_commit_leaves_head_unchanged(tmp_path: Path) -> None:
    _, runtime, _ = configured(tmp_path, timeout=1.0)
    before = runtime.current_head()
    code = (
        "from pathlib import Path; import sys,time; "
        "from automated_trading_bot.stage5.provider_authority import _AuthorityStore; "
        "store=_AuthorityStore(Path(sys.argv[1]),timeout_seconds=2); "
        "ctx=store.writer_fence(); ctx.__enter__(); print('WRITER_READY',flush=True); "
        "time.sleep(10)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "WRITER_READY"
    process.kill()
    process.wait(timeout=5)
    runtime._engineering_admit(CapitalSnapshotRequest("P1", "R1", USD, NOW, True))
    after = runtime.current_head()
    assert after.sequence == before.sequence
    assert after.terminal_digest == before.terminal_digest


def test_c10_committed_writer_retry_does_not_duplicate_generation(
    tmp_path: Path,
) -> None:
    manager, runtime, _ = configured(tmp_path)
    before = runtime.current_head()
    committed = manager.revoke(
        command_id="revoke:ack-lost", expected_head=before.terminal_digest
    )
    retried = manager.revoke(
        command_id="revoke:ack-lost", expected_head=before.terminal_digest
    )
    assert retried == committed
    assert retried.sequence == before.sequence + 1
    assert retried.last_generation == before.last_generation


def test_raw_snapshot_remains_non_operational() -> None:
    raw = capital_snapshot()
    with pytest.raises(CapitalSnapshotError, match="cannot self-certify"):
        raw.require_current(portfolio_id="P1", run_scope="R1", at=NOW, operational=True)
    assert replace(raw, state_version=raw.state_version).content_digest == raw.content_digest


"""Retained Stage 5 provider authority and operational admission.

The filesystem root is fixed by the owner composition boundary.  Bootstrap,
management, and runtime are deliberately distinct facades so deployments can
give them different OS principals and ACLs.  Python object possession is not
treated as an authority boundary; filesystem capability is.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from types import TracebackType
from typing import BinaryIO, Protocol

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.currency import Currency
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.stage5.contracts import (
    CapitalSnapshot,
    CapitalSnapshotError,
    CapitalSnapshotPort,
    CapitalSnapshotRequest,
)


class ProviderAuthorityError(CapitalSnapshotError):
    """Provider authority cannot be established or safely consumed."""


class AuthorityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AuthorityDomainClassification(StrEnum):
    ENGINEERING_ONLY = "ENGINEERING_ONLY"
    OPERATIONAL = "OPERATIONAL"


class AuthorityCommand(StrEnum):
    ACTIVATE = "ACTIVATE"
    ROTATE = "ROTATE"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"


class ControlledUtcClock(Protocol):
    def now(self) -> Timestamp: ...


class ProviderResolver(Protocol):
    def resolve(self, provider_identity: str) -> CapitalSnapshotPort: ...


@dataclass(frozen=True, slots=True)
class SystemUtcClock:
    def now(self) -> Timestamp:
        return Timestamp(datetime.now(timezone.utc))


def _controlled_now(clock: ControlledUtcClock) -> Timestamp:
    try:
        value = clock.now()
    except Exception as error:
        raise ProviderAuthorityError(
            "controlled UTC clock is unavailable or invalid"
        ) from error
    if type(value) is not Timestamp:
        raise ProviderAuthorityError("controlled UTC clock is unavailable or invalid")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ProviderAuthorityError(f"{name} must be non-empty text")
    return value


def _identity(domain: str, body: object) -> str:
    return sha256(domain.encode() + b"\0" + canonical_json(body)).hexdigest()


def _timestamp(value: object, name: str) -> Timestamp:
    if type(value) is not str:
        raise ProviderAuthorityError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ProviderAuthorityError(f"{name} is invalid") from error
    try:
        return Timestamp(parsed)
    except (TypeError, ValueError) as error:
        raise ProviderAuthorityError(f"{name} must be UTC") from error


def _safe_root(root: Path) -> Path:
    if not isinstance(root, Path) or root.is_symlink():
        raise ProviderAuthorityError("authority root must be a safe pathlib.Path")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ProviderAuthorityError("authority root is unsafe")
    return root.resolve(strict=True)


def _read_json(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise ProviderAuthorityError("authority artifact is unsafe")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderAuthorityError("authority artifact is unreadable") from error
    if type(value) is not dict or canonical_json(value) != raw:
        raise ProviderAuthorityError("authority artifact is not canonical")
    return value


def _durable_replace(path: Path, body: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = canonical_json(dict(body))
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _durable_create(path: Path, body: Mapping[str, object]) -> None:
    data = canonical_json(dict(body))
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ProviderAuthorityError("conflicting immutable authority artifact") from error


class _ProcessFileLock:
    """A bounded, OS-released cross-process exclusive byte-range lock."""

    def __init__(self, path: Path, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ProviderAuthorityError("authority lock timeout must be positive")
        self._path = path
        self._timeout = timeout_seconds
        self._stream: BinaryIO | None = None

    def __enter__(self) -> None:
        stream = self._path.open("a+b")
        stream.seek(0)
        if self._path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(  # type: ignore[attr-defined]
                        stream.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                    )
                self._stream = stream
                return None
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    stream.close()
                    raise ProviderAuthorityError("authority fence acquisition timed out")
                time.sleep(0.01)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        stream = self._stream
        if stream is None:
            return
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                stream.fileno(), fcntl.LOCK_UN  # type: ignore[attr-defined]
            )
        stream.close()
        self._stream = None


@dataclass(frozen=True, slots=True)
class ProviderImplementationDescriptor:
    port_contract_identity: str
    code_package_identity: str
    adapter_identity: str
    configuration_identity: str
    build_dependency_identity: str
    deployment_instance_identity: str
    descriptor_digest: str

    def __post_init__(self) -> None:
        for name in (
            "port_contract_identity",
            "code_package_identity",
            "adapter_identity",
            "configuration_identity",
            "build_dependency_identity",
            "deployment_instance_identity",
        ):
            _text(getattr(self, name), name)
        if self.descriptor_digest != _identity(
            "ATIS-STAGE5-PROVIDER-DESCRIPTOR-V1", self._body()
        ):
            raise ProviderAuthorityError("provider descriptor identity mismatch")

    def _body(self) -> dict[str, str]:
        return {
            "adapter_identity": self.adapter_identity,
            "build_dependency_identity": self.build_dependency_identity,
            "code_package_identity": self.code_package_identity,
            "configuration_identity": self.configuration_identity,
            "deployment_instance_identity": self.deployment_instance_identity,
            "port_contract_identity": self.port_contract_identity,
        }

    @classmethod
    def create(
        cls,
        *,
        port_contract_identity: str,
        code_package_identity: str,
        adapter_identity: str,
        configuration_identity: str,
        build_dependency_identity: str,
        deployment_instance_identity: str,
    ) -> ProviderImplementationDescriptor:
        body = {
            "adapter_identity": adapter_identity,
            "build_dependency_identity": build_dependency_identity,
            "code_package_identity": code_package_identity,
            "configuration_identity": configuration_identity,
            "deployment_instance_identity": deployment_instance_identity,
            "port_contract_identity": port_contract_identity,
        }
        return cls(
            port_contract_identity,
            code_package_identity,
            adapter_identity,
            configuration_identity,
            build_dependency_identity,
            deployment_instance_identity,
            _identity("ATIS-STAGE5-PROVIDER-DESCRIPTOR-V1", body),
        )


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    portfolio_id: str
    run_scope: str
    currency: Currency
    operation: str

    def __post_init__(self) -> None:
        _text(self.portfolio_id, "portfolio_id")
        _text(self.run_scope, "run_scope")
        _text(self.operation, "operation")
        if type(self.currency) is not Currency:
            raise TypeError("currency must be Currency")

    @property
    def identity(self) -> str:
        return _identity("ATIS-STAGE5-AUTHORITY-SCOPE-V1", self._body())

    def _body(self) -> dict[str, str]:
        return {
            "currency": self.currency.code,
            "operation": self.operation,
            "portfolio_id": self.portfolio_id,
            "run_scope": self.run_scope,
        }


@dataclass(frozen=True, slots=True)
class BootstrapEvidence:
    store_epoch: str
    policy_id: str
    bootstrap_identity: str
    empty_head_digest: str
    authority_domain_identity: str
    authority_domain_classification: AuthorityDomainClassification


@dataclass(frozen=True, slots=True)
class AuthorityGeneration:
    authority_id: str
    generation: int
    provider_identity: str
    descriptor: ProviderImplementationDescriptor
    role: str
    scope: AuthorityScope
    policy_id: str
    valid_from: Timestamp
    expires_at: Timestamp
    status: AuthorityStatus
    predecessor_authority_id: str | None
    command_id: str
    record_identity: str

    def require_current(self, *, at: Timestamp) -> None:
        if self.status is not AuthorityStatus.ACTIVE:
            raise ProviderAuthorityError("provider authority is not ACTIVE")
        if at.value < self.valid_from.value:
            raise ProviderAuthorityError("provider authority is not yet valid")
        if at.value >= self.expires_at.value:
            raise ProviderAuthorityError("provider authority is expired")


@dataclass(frozen=True, slots=True)
class AuthorityHead:
    store_epoch: str
    authority_domain_identity: str
    authority_domain_classification: AuthorityDomainClassification
    sequence: int
    terminal_digest: str
    time_high_water: Timestamp
    active: AuthorityGeneration | None
    last_generation: int
    last_authority_id: str | None


@dataclass(frozen=True, slots=True)
class AdmittedCapitalSnapshot:
    snapshot: CapitalSnapshot
    provider_descriptor: ProviderImplementationDescriptor
    store_epoch: str
    authority_domain_identity: str
    authority_id: str
    authority_generation: int
    authority_head_digest: str
    policy_id: str
    scope: AuthorityScope
    admitted_at: Timestamp
    valid_until: Timestamp
    admission_identity: str

    def __post_init__(self) -> None:
        expected = _identity("ATIS-STAGE5-ADMITTED-CAPITAL-SNAPSHOT-V1", self._body())
        if self.admission_identity != expected:
            raise ProviderAuthorityError("admitted snapshot identity mismatch")

    def _body(self) -> dict[str, object]:
        return {
            "admitted_at": self.admitted_at.value.isoformat(),
            "authority_generation": self.authority_generation,
            "authority_head_digest": self.authority_head_digest,
            "authority_id": self.authority_id,
            "authority_domain_identity": self.authority_domain_identity,
            "descriptor_digest": self.provider_descriptor.descriptor_digest,
            "policy_id": self.policy_id,
            "scope": self.scope._body(),
            "snapshot_digest": self.snapshot.content_digest,
            "snapshot_id": self.snapshot.snapshot_id,
            "state_version": self.snapshot.state_version,
            "store_epoch": self.store_epoch,
            "valid_until": self.valid_until.value.isoformat(),
        }


def _descriptor_body(value: ProviderImplementationDescriptor) -> dict[str, str]:
    body = value._body()
    body["descriptor_digest"] = value.descriptor_digest
    return body


def _descriptor(body: object) -> ProviderImplementationDescriptor:
    if type(body) is not dict:
        raise ProviderAuthorityError("provider descriptor is invalid")
    return ProviderImplementationDescriptor(
        str(body["port_contract_identity"]),
        str(body["code_package_identity"]),
        str(body["adapter_identity"]),
        str(body["configuration_identity"]),
        str(body["build_dependency_identity"]),
        str(body["deployment_instance_identity"]),
        str(body["descriptor_digest"]),
    )


def _scope(body: object) -> AuthorityScope:
    if type(body) is not dict:
        raise ProviderAuthorityError("authority scope is invalid")
    return AuthorityScope(
        str(body["portfolio_id"]),
        str(body["run_scope"]),
        Currency(str(body["currency"])),
        str(body["operation"]),
    )


def _generation_body(value: AuthorityGeneration, *, include_identity: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "authority_id": value.authority_id,
        "command_id": value.command_id,
        "descriptor": _descriptor_body(value.descriptor),
        "expires_at": value.expires_at.value.isoformat(),
        "generation": value.generation,
        "policy_id": value.policy_id,
        "predecessor_authority_id": value.predecessor_authority_id,
        "provider_identity": value.provider_identity,
        "role": value.role,
        "scope": value.scope._body(),
        "status": value.status.value,
        "valid_from": value.valid_from.value.isoformat(),
    }
    if include_identity:
        body["record_identity"] = value.record_identity
    return body


def _generation(body: object) -> AuthorityGeneration:
    if type(body) is not dict:
        raise ProviderAuthorityError("authority generation is invalid")
    value = AuthorityGeneration(
        str(body["authority_id"]),
        int(str(body["generation"])),
        str(body["provider_identity"]),
        _descriptor(body["descriptor"]),
        str(body["role"]),
        _scope(body["scope"]),
        str(body["policy_id"]),
        _timestamp(body["valid_from"], "valid_from"),
        _timestamp(body["expires_at"], "expires_at"),
        AuthorityStatus(str(body["status"])),
        None if body["predecessor_authority_id"] is None else str(body["predecessor_authority_id"]),
        str(body["command_id"]),
        str(body["record_identity"]),
    )
    expected = _identity(
        "ATIS-STAGE5-PROVIDER-AUTHORITY-GENERATION-V1",
        _generation_body(value, include_identity=False),
    )
    if value.record_identity != expected:
        raise ProviderAuthorityError("authority generation identity mismatch")
    return value


def _new_generation(
    *,
    generation: int,
    provider_identity: str,
    descriptor: ProviderImplementationDescriptor,
    role: str,
    scope: AuthorityScope,
    policy_id: str,
    valid_from: Timestamp,
    expires_at: Timestamp,
    status: AuthorityStatus,
    predecessor: str | None,
    command_id: str,
) -> AuthorityGeneration:
    provisional = AuthorityGeneration(
        "pending",
        generation,
        provider_identity,
        descriptor,
        role,
        scope,
        policy_id,
        valid_from,
        expires_at,
        status,
        predecessor,
        command_id,
        "pending",
    )
    body = _generation_body(provisional, include_identity=False)
    body["authority_id"] = _identity("ATIS-STAGE5-PROVIDER-AUTHORITY-ID-V1", body)
    provisional = AuthorityGeneration(
        str(body["authority_id"]), generation, provider_identity, descriptor, role,
        scope, policy_id, valid_from, expires_at, status, predecessor, command_id,
        "pending",
    )
    return AuthorityGeneration(
        provisional.authority_id, generation, provider_identity, descriptor, role,
        scope, policy_id, valid_from, expires_at, status, predecessor, command_id,
        _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-GENERATION-V1",
            _generation_body(provisional, include_identity=False),
        ),
    )


class _AuthorityStore:
    def __init__(self, root: Path, *, timeout_seconds: float = 5.0) -> None:
        self.root = _safe_root(root)
        self.records = self.root / "records"
        self.lock_path = self.root / "authority.fence"
        self.writer_intent_path = self.root / "authority.writer-intent"
        self.writer_owner_path = self.root / "authority.writer-owner"
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def reader_fence(self) -> Iterator[None]:
        deadline = time.monotonic() + self.timeout_seconds
        while self.writer_intent_path.exists():
            try:
                with _ProcessFileLock(self.writer_owner_path, 0.01):
                    if self.writer_intent_path.exists():
                        self.writer_intent_path.unlink()
            except ProviderAuthorityError:
                if time.monotonic() >= deadline:
                    raise ProviderAuthorityError(
                        "authority fence acquisition timed out behind waiting writer"
                    )
                time.sleep(0.01)
        with _ProcessFileLock(self.lock_path, self.timeout_seconds):
            yield

    @contextmanager
    def writer_fence(self) -> Iterator[None]:
        with _ProcessFileLock(self.writer_owner_path, self.timeout_seconds):
            try:
                _durable_replace(
                    self.writer_intent_path,
                    {"coordination_only": True, "pid": os.getpid()},
                )
                with _ProcessFileLock(self.lock_path, self.timeout_seconds):
                    yield
            finally:
                if self.writer_intent_path.exists():
                    self.writer_intent_path.unlink()

    def _bootstrap(self) -> dict[str, object]:
        return _read_json(self.root / "bootstrap.json")

    def recover(self) -> AuthorityHead:
        bootstrap = self._bootstrap()
        required = {
            "bootstrap_identity", "command_id", "empty_head_digest", "policy_id",
            "roles", "schema", "store_epoch", "initial_time_high_water",
            "scopes", "authority_domain_identity",
            "authority_domain_classification",
        }
        if set(bootstrap) != required or bootstrap["schema"] != 1:
            raise ProviderAuthorityError("bootstrap schema is invalid")
        bootstrap_body = dict(bootstrap)
        supplied_bootstrap_identity = str(bootstrap_body.pop("bootstrap_identity"))
        if supplied_bootstrap_identity != _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-BOOTSTRAP-V1", bootstrap_body
        ):
            raise ProviderAuthorityError("bootstrap identity mismatch")
        empty = _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-EMPTY-HEAD-V1",
            {
                "policy_id": bootstrap["policy_id"],
                "store_epoch": bootstrap["store_epoch"],
            },
        )
        if bootstrap["empty_head_digest"] != empty:
            raise ProviderAuthorityError("EMPTY authority head identity mismatch")
        previous = empty
        active: AuthorityGeneration | None = None
        last_generation = 0
        last_authority_id: str | None = None
        seen: dict[str, str] = {}
        paths = sorted(self.records.glob("*.json")) if self.records.exists() else []
        for sequence, path in enumerate(paths, 1):
            if path.name != f"{sequence:08d}.json":
                raise ProviderAuthorityError("authority history is truncated or reordered")
            record = _read_json(path)
            required_record = {
                "command", "command_digest", "command_id", "content_digest",
                "generation", "previous_digest", "sequence", "store_epoch",
            }
            if set(record) != required_record:
                raise ProviderAuthorityError("authority transition schema mismatch")
            body = dict(record)
            supplied = str(body.pop("content_digest"))
            expected = _identity("ATIS-STAGE5-PROVIDER-AUTHORITY-TRANSITION-V1", body)
            command_id = str(record["command_id"])
            command_digest = str(record["command_digest"])
            if (
                supplied != expected
                or record["store_epoch"] != bootstrap["store_epoch"]
                or record["sequence"] != sequence
                or record["previous_digest"] != previous
            ):
                raise ProviderAuthorityError("authority transition chain is invalid")
            if command_id in seen and seen[command_id] != command_digest:
                raise ProviderAuthorityError("conflicting duplicate authority command")
            if command_id in seen:
                raise ProviderAuthorityError("duplicate authority command in history")
            seen[command_id] = command_digest
            next_generation = _generation(record["generation"])
            command = AuthorityCommand(str(record["command"]))
            if command is AuthorityCommand.ACTIVATE:
                if (
                    active is not None
                    or next_generation.generation != last_generation + 1
                    or next_generation.predecessor_authority_id != last_authority_id
                ):
                    raise ProviderAuthorityError("invalid initial activation")
            elif command is AuthorityCommand.ROTATE:
                if active is None or next_generation.generation != active.generation + 1:
                    raise ProviderAuthorityError("invalid authority rotation")
                if next_generation.predecessor_authority_id != active.authority_id:
                    raise ProviderAuthorityError("authority predecessor mismatch")
            elif command in (AuthorityCommand.REVOKE, AuthorityCommand.EXPIRE):
                if active is None or next_generation.authority_id != active.authority_id:
                    raise ProviderAuthorityError("invalid terminal authority transition")
                if next_generation.generation != active.generation:
                    raise ProviderAuthorityError("terminal generation mismatch")
            active = (
                next_generation
                if next_generation.status is AuthorityStatus.ACTIVE
                else None
            )
            last_generation = next_generation.generation
            last_authority_id = next_generation.authority_id
            previous = supplied
        head = _read_json(self.root / "head.json")
        if set(head) != {"sequence", "store_epoch", "terminal_digest"}:
            raise ProviderAuthorityError("authority head schema mismatch")
        if (
            head["store_epoch"] != bootstrap["store_epoch"]
            or head["sequence"] != len(paths)
            or head["terminal_digest"] != previous
        ):
            raise ProviderAuthorityError("authority head rollback or corruption detected")
        high = _read_json(self.root / "time-high-water.json")
        if set(high) != {"store_epoch", "time_high_water"}:
            raise ProviderAuthorityError("authority time high-water schema mismatch")
        if high["store_epoch"] != bootstrap["store_epoch"]:
            raise ProviderAuthorityError("authority time high-water epoch mismatch")
        return AuthorityHead(
            str(bootstrap["store_epoch"]),
            str(bootstrap["authority_domain_identity"]),
            AuthorityDomainClassification(
                str(bootstrap["authority_domain_classification"])
            ),
            len(paths), previous,
            _timestamp(high["time_high_water"], "time_high_water"), active,
            last_generation, last_authority_id,
        )

    def advance_time(self, observed: Timestamp) -> Timestamp:
        high_path = self.root / "time-high-water.json"
        high = _read_json(high_path)
        retained = _timestamp(high["time_high_water"], "time_high_water")
        if observed.value < retained.value:
            raise ProviderAuthorityError("trusted UTC moved backwards")
        if observed.value > retained.value:
            _durable_replace(
                high_path,
                {"store_epoch": high["store_epoch"], "time_high_water": observed.value.isoformat()},
            )
        return observed

    def require_permitted(
        self, *, role: str, scope: AuthorityScope, policy_id: str
    ) -> None:
        bootstrap = self._bootstrap()
        roles = bootstrap.get("roles")
        scopes = bootstrap.get("scopes")
        if type(roles) is not list or role not in roles:
            raise ProviderAuthorityError("provider role is not permitted by bootstrap")
        if type(scopes) is not list or scope._body() not in scopes:
            raise ProviderAuthorityError("provider scope is not permitted by bootstrap")
        if bootstrap.get("policy_id") != policy_id:
            raise ProviderAuthorityError("provider policy is not permitted by bootstrap")

    def command_was_accepted(
        self, *, command_id: str, command_payload: Mapping[str, object]
    ) -> bool:
        command_digest = _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-COMMAND-V1", dict(command_payload)
        )
        if not self.records.exists():
            return False
        for path in sorted(self.records.glob("*.json")):
            old = _read_json(path)
            if old["command_id"] == command_id:
                if old["command_digest"] != command_digest:
                    raise ProviderAuthorityError("conflicting duplicate authority command")
                return True
        return False

    def terminal_command_was_accepted(
        self, *, command_id: str, command: AuthorityCommand
    ) -> bool:
        if not self.records.exists():
            return False
        for path in sorted(self.records.glob("*.json")):
            old = _read_json(path)
            if old["command_id"] == command_id:
                if old["command"] != command.value:
                    raise ProviderAuthorityError("conflicting duplicate authority command")
                return True
        return False

    def publish(
        self,
        *,
        expected_head: str,
        command: AuthorityCommand,
        command_id: str,
        generation: AuthorityGeneration,
        command_payload: Mapping[str, object],
    ) -> AuthorityHead:
        prior = self.recover()
        command_digest = _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-COMMAND-V1", dict(command_payload)
        )
        if self.records.exists():
            for path in sorted(self.records.glob("*.json")):
                old = _read_json(path)
                if old["command_id"] == command_id:
                    if old["command_digest"] != command_digest:
                        raise ProviderAuthorityError("conflicting duplicate authority command")
                    return prior
        if expected_head != prior.terminal_digest:
            raise ProviderAuthorityError("stale authority head")
        body: dict[str, object] = {
            "command": command.value,
            "command_digest": command_digest,
            "command_id": _text(command_id, "command_id"),
            "generation": _generation_body(generation, include_identity=True),
            "previous_digest": prior.terminal_digest,
            "sequence": prior.sequence + 1,
            "store_epoch": prior.store_epoch,
        }
        record = dict(body)
        record["content_digest"] = _identity(
            "ATIS-STAGE5-PROVIDER-AUTHORITY-TRANSITION-V1", body
        )
        self.records.mkdir(exist_ok=True)
        _durable_create(self.records / f"{prior.sequence + 1:08d}.json", record)
        _durable_replace(
            self.root / "head.json",
            {
                "sequence": prior.sequence + 1,
                "store_epoch": prior.store_epoch,
                "terminal_digest": record["content_digest"],
            },
        )
        result = self.recover()
        if result.sequence != prior.sequence + 1:
            raise ProviderAuthorityError("authority successor failed durable readback")
        return result


class ProviderAuthorityBootstrap:
    """Low-level engineering bootstrap; it can never create operational trust."""

    def __init__(self, root: Path, *, timeout_seconds: float = 5.0) -> None:
        self._store = _AuthorityStore(root, timeout_seconds=timeout_seconds)

    def establish(
        self,
        *,
        command_id: str,
        store_epoch: str,
        policy_id: str,
        roles: tuple[str, ...],
        scopes: tuple[AuthorityScope, ...],
        trusted_at: Timestamp,
    ) -> BootstrapEvidence:
        domain_identity = _identity(
            "ATIS-STAGE5-ENGINEERING-AUTHORITY-DOMAIN-V1",
            {
                "policy_id": policy_id,
                "store_epoch": store_epoch,
                "bootstrap_command_id": command_id,
            },
        )
        return self._establish(
            command_id=command_id,
            store_epoch=store_epoch,
            policy_id=policy_id,
            roles=roles,
            scopes=scopes,
            trusted_at=trusted_at,
            authority_domain_identity=domain_identity,
            authority_domain_classification=AuthorityDomainClassification.ENGINEERING_ONLY,
        )

    def _establish(
        self,
        *,
        command_id: str,
        store_epoch: str,
        policy_id: str,
        roles: tuple[str, ...],
        scopes: tuple[AuthorityScope, ...],
        trusted_at: Timestamp,
        authority_domain_identity: str,
        authority_domain_classification: AuthorityDomainClassification,
    ) -> BootstrapEvidence:
        if authority_domain_classification is not AuthorityDomainClassification.ENGINEERING_ONLY:
            raise ProviderAuthorityError(
                "local bootstrap cannot establish OPERATIONAL authority"
            )
        if type(roles) is not tuple or not roles or len(set(roles)) != len(roles):
            raise ProviderAuthorityError("bootstrap roles must be unique and non-empty")
        for role in roles:
            _text(role, "role")
        if type(scopes) is not tuple or not scopes or any(
            type(scope) is not AuthorityScope for scope in scopes
        ):
            raise ProviderAuthorityError("bootstrap scopes must be non-empty AuthorityScope values")
        if len({scope.identity for scope in scopes}) != len(scopes):
            raise ProviderAuthorityError("bootstrap scopes must be unique")
        with self._store.writer_fence():
            empty = _identity(
                "ATIS-STAGE5-PROVIDER-AUTHORITY-EMPTY-HEAD-V1",
                {"policy_id": policy_id, "store_epoch": store_epoch},
            )
            base: dict[str, object] = {
                "command_id": _text(command_id, "command_id"),
                "authority_domain_identity": _text(
                    authority_domain_identity, "authority_domain_identity"
                ),
                "authority_domain_classification": authority_domain_classification.value,
                "empty_head_digest": empty,
                "policy_id": _text(policy_id, "policy_id"),
                "roles": list(roles),
                "scopes": [scope._body() for scope in scopes],
                "schema": 1,
                "store_epoch": _text(store_epoch, "store_epoch"),
                "initial_time_high_water": trusted_at.value.isoformat(),
            }
            body = dict(base)
            body["bootstrap_identity"] = _identity(
                "ATIS-STAGE5-PROVIDER-AUTHORITY-BOOTSTRAP-V1", base
            )
            bootstrap_path = self._store.root / "bootstrap.json"
            if bootstrap_path.exists():
                if _read_json(bootstrap_path) != body:
                    raise ProviderAuthorityError("conflicting second bootstrap")
                head = self._store.recover()
                return BootstrapEvidence(
                    head.store_epoch, policy_id, str(body["bootstrap_identity"]), empty,
                    head.authority_domain_identity,
                    head.authority_domain_classification,
                )
            _durable_create(bootstrap_path, body)
            self._store.records.mkdir(exist_ok=False)
            _durable_create(
                self._store.root / "head.json",
                {"sequence": 0, "store_epoch": store_epoch, "terminal_digest": empty},
            )
            _durable_create(
                self._store.root / "time-high-water.json",
                {"store_epoch": store_epoch, "time_high_water": trusted_at.value.isoformat()},
            )
            head = self._store.recover()
            if head.active is not None:
                raise ProviderAuthorityError("BOOTSTRAPPED_EMPTY unexpectedly grants authority")
            return BootstrapEvidence(
                head.store_epoch, policy_id, str(body["bootstrap_identity"]), empty,
                head.authority_domain_identity,
                head.authority_domain_classification,
            )


class ProviderAuthorityManager:
    """Management capability; deployment ACL must grant this facade write access."""

    def __init__(
        self, root: Path, *, clock: ControlledUtcClock, timeout_seconds: float = 5.0
    ) -> None:
        self._store = _AuthorityStore(root, timeout_seconds=timeout_seconds)
        self._clock = clock

    def _now(self) -> Timestamp:
        return self._store.advance_time(_controlled_now(self._clock))

    def activate(
        self,
        *,
        command_id: str,
        expected_head: str,
        provider_identity: str,
        descriptor: ProviderImplementationDescriptor,
        role: str,
        scope: AuthorityScope,
        policy_id: str,
        valid_from: Timestamp,
        expires_at: Timestamp,
    ) -> AuthorityHead:
        return self._activate_or_rotate(
            command=AuthorityCommand.ACTIVATE,
            command_id=command_id,
            expected_head=expected_head,
            provider_identity=provider_identity,
            descriptor=descriptor,
            role=role,
            scope=scope,
            policy_id=policy_id,
            valid_from=valid_from,
            expires_at=expires_at,
        )

    def rotate(
        self,
        *,
        command_id: str,
        expected_head: str,
        provider_identity: str,
        descriptor: ProviderImplementationDescriptor,
        role: str,
        scope: AuthorityScope,
        policy_id: str,
        valid_from: Timestamp,
        expires_at: Timestamp,
    ) -> AuthorityHead:
        return self._activate_or_rotate(
            command=AuthorityCommand.ROTATE,
            command_id=command_id,
            expected_head=expected_head,
            provider_identity=provider_identity,
            descriptor=descriptor,
            role=role,
            scope=scope,
            policy_id=policy_id,
            valid_from=valid_from,
            expires_at=expires_at,
        )

    def _activate_or_rotate(
        self,
        *,
        command: AuthorityCommand,
        command_id: str,
        expected_head: str,
        provider_identity: str,
        descriptor: ProviderImplementationDescriptor,
        role: str,
        scope: AuthorityScope,
        policy_id: str,
        valid_from: Timestamp,
        expires_at: Timestamp,
    ) -> AuthorityHead:
        if expires_at.value <= valid_from.value:
            raise ProviderAuthorityError("authority expiry must follow valid_from")
        if expires_at.value - valid_from.value > timedelta(hours=24):
            raise ProviderAuthorityError("provider authority validity exceeds 24 hours")
        payload = {
            "command": command.value,
            "descriptor_digest": descriptor.descriptor_digest,
            "expires_at": expires_at.value.isoformat(),
            "policy_id": policy_id,
            "provider_identity": provider_identity,
            "role": role,
            "scope": scope._body(),
            "valid_from": valid_from.value.isoformat(),
        }
        with self._store.writer_fence():
            now = self._now()
            if valid_from.value > now.value:
                raise ProviderAuthorityError("provider authority is not yet valid")
            prior = self._store.recover()
            self._store.require_permitted(
                role=role, scope=scope, policy_id=policy_id
            )
            if self._store.command_was_accepted(
                command_id=command_id, command_payload=payload
            ):
                return prior
            if command is AuthorityCommand.ACTIVATE and prior.active is not None:
                raise ProviderAuthorityError("an ACTIVE provider already exists")
            if command is AuthorityCommand.ROTATE and prior.active is None:
                raise ProviderAuthorityError("rotation requires current ACTIVE authority")
            generation_number = prior.last_generation + 1
            predecessor = prior.last_authority_id
            value = _new_generation(
                generation=generation_number,
                provider_identity=_text(provider_identity, "provider_identity"),
                descriptor=descriptor,
                role=_text(role, "role"),
                scope=scope,
                policy_id=_text(policy_id, "policy_id"),
                valid_from=valid_from,
                expires_at=expires_at,
                status=AuthorityStatus.ACTIVE,
                predecessor=predecessor,
                command_id=command_id,
            )
            return self._store.publish(
                expected_head=expected_head,
                command=command,
                command_id=command_id,
                generation=value,
                command_payload=payload,
            )

    def revoke(self, *, command_id: str, expected_head: str) -> AuthorityHead:
        return self._terminal(
            command=AuthorityCommand.REVOKE,
            command_id=command_id,
            expected_head=expected_head,
        )

    def expire(self, *, command_id: str, expected_head: str) -> AuthorityHead:
        return self._terminal(
            command=AuthorityCommand.EXPIRE,
            command_id=command_id,
            expected_head=expected_head,
        )

    def _terminal(
        self, *, command: AuthorityCommand, command_id: str, expected_head: str
    ) -> AuthorityHead:
        payload: dict[str, object]
        with self._store.writer_fence():
            self._now()
            prior = self._store.recover()
            active = prior.active
            if active is None:
                if self._store.terminal_command_was_accepted(
                    command_id=command_id, command=command
                ):
                    return prior
                raise ProviderAuthorityError("no ACTIVE provider authority")
            payload = {"authority_id": active.authority_id, "command": command.value}
            if self._store.command_was_accepted(
                command_id=command_id, command_payload=payload
            ):
                return prior
            status = (
                AuthorityStatus.REVOKED
                if command is AuthorityCommand.REVOKE
                else AuthorityStatus.EXPIRED
            )
            value = AuthorityGeneration(
                active.authority_id,
                active.generation,
                active.provider_identity,
                active.descriptor,
                active.role,
                active.scope,
                active.policy_id,
                active.valid_from,
                active.expires_at,
                status,
                active.predecessor_authority_id,
                command_id,
                "pending",
            )
            value = AuthorityGeneration(
                value.authority_id, value.generation, value.provider_identity,
                value.descriptor, value.role, value.scope, value.policy_id,
                value.valid_from, value.expires_at, value.status,
                value.predecessor_authority_id, value.command_id,
                _identity(
                    "ATIS-STAGE5-PROVIDER-AUTHORITY-GENERATION-V1",
                    _generation_body(value, include_identity=False),
                ),
            )
            return self._store.publish(
                expected_head=expected_head,
                command=command,
                command_id=command_id,
                generation=value,
                command_payload=payload,
            )


class AuthorityFence:
    """I01 side of the future AUTHORITY -> CAPITAL ordering contract."""

    def __init__(
        self,
        store: _AuthorityStore,
        admitted: AdmittedCapitalSnapshot,
        clock: ControlledUtcClock,
        trusted_domain_identity: str | None,
    ) -> None:
        self._store = store
        self._admitted = admitted
        self._clock = clock
        self._trusted_domain_identity = trusted_domain_identity
        self._lock: AbstractContextManager[None] | None = None

    def __enter__(self) -> AdmittedCapitalSnapshot:
        context = self._store.reader_fence()
        context.__enter__()
        self._lock = context
        try:
            now = self._store.advance_time(_controlled_now(self._clock))
            head = self._store.recover()
            active = head.active
            if (
                active is None
                or self._trusted_domain_identity is None
                or head.authority_domain_classification
                is not AuthorityDomainClassification.OPERATIONAL
                or head.authority_domain_identity != self._trusted_domain_identity
                or self._admitted.authority_domain_identity
                != self._trusted_domain_identity
                or head.store_epoch != self._admitted.store_epoch
                or head.terminal_digest != self._admitted.authority_head_digest
                or active.authority_id != self._admitted.authority_id
                or active.generation != self._admitted.authority_generation
            ):
                raise ProviderAuthorityError("STALE_AUTHORITY")
            active.require_current(at=now)
            return self._admitted
        except Exception:
            context.__exit__(None, None, None)
            self._lock = None
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._lock is not None:
            self._lock.__exit__(exc_type, exc, traceback)
            self._lock = None


class ProviderAuthorityRuntime:
    """Low-level runtime; direct construction is engineering-only."""

    def __init__(
        self,
        root: Path,
        *,
        resolver: ProviderResolver,
        clock: ControlledUtcClock,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._store = _AuthorityStore(root, timeout_seconds=timeout_seconds)
        self._resolver = resolver
        self._clock = clock
        self._trusted_domain_identity: str | None = None

    @classmethod
    def _for_owner_composition(
        cls,
        root: Path,
        *,
        resolver: ProviderResolver,
        clock: ControlledUtcClock,
        authority_domain_identity: str,
        timeout_seconds: float,
    ) -> ProviderAuthorityRuntime:
        del root, resolver, clock, authority_domain_identity, timeout_seconds
        raise ProviderAuthorityError(
            "local owner composition is permanently ENGINEERING_ONLY; "
            "operational admission requires the fixed Authority Service"
        )

    def current_head(self) -> AuthorityHead:
        return self._store.recover()

    def admit(self, request: CapitalSnapshotRequest) -> AdmittedCapitalSnapshot:
        del request
        raise ProviderAuthorityError(
            "local runtime cannot mint operational admission; "
            "use the fixed Authority Service client"
        )

    def _engineering_admit(
        self, request: CapitalSnapshotRequest
    ) -> AdmittedCapitalSnapshot:
        """Exercise retained lifecycle mechanics without operational authority."""
        if type(request) is not CapitalSnapshotRequest:
            raise ProviderAuthorityError("typed snapshot request required")
        with self._store.reader_fence():
            now = self._store.advance_time(_controlled_now(self._clock))
            head = self._store.recover()
            if head.authority_domain_classification is not AuthorityDomainClassification.ENGINEERING_ONLY:
                raise ProviderAuthorityError("foreign authority domain")
            active = head.active
            if active is None:
                raise ProviderAuthorityError("engineering provider is NOT_ESTABLISHED")
            active.require_current(at=now)
            expected_scope = AuthorityScope(
                request.portfolio_id, request.run_scope, request.currency,
                "CAPITAL_SNAPSHOT",
            )
            if active.scope != expected_scope:
                raise ProviderAuthorityError("foreign authority scope")
            provider = self._resolver.resolve(active.provider_identity)
            if provider.provider_identity != active.provider_identity:
                raise ProviderAuthorityError("resolved provider identity mismatch")
            if provider.authority_policy_id != active.policy_id:
                raise ProviderAuthorityError("resolved provider policy mismatch")
            if getattr(provider, "implementation_descriptor", None) != active.descriptor:
                raise ProviderAuthorityError("resolved provider implementation mismatch")
            value = provider.snapshot(CapitalSnapshotRequest(
                request.portfolio_id, request.run_scope, request.currency, now,
                operational=False,
            ))
            valid_until = Timestamp(min(value.expires_at.value, active.expires_at.value))
            body = {
                "admitted_at": now.value.isoformat(),
                "authority_generation": active.generation,
                "authority_head_digest": head.terminal_digest,
                "authority_id": active.authority_id,
                "authority_domain_identity": head.authority_domain_identity,
                "descriptor_digest": active.descriptor.descriptor_digest,
                "policy_id": active.policy_id,
                "scope": active.scope._body(),
                "snapshot_digest": value.content_digest,
                "snapshot_id": value.snapshot_id,
                "state_version": value.state_version,
                "store_epoch": head.store_epoch,
                "valid_until": valid_until.value.isoformat(),
            }
            return AdmittedCapitalSnapshot(
                value, active.descriptor, head.store_epoch,
                head.authority_domain_identity, active.authority_id,
                active.generation, head.terminal_digest, active.policy_id,
                active.scope, now, valid_until,
                _identity("ATIS-STAGE5-ADMITTED-CAPITAL-SNAPSHOT-V1", body),
            )

    def authority_fence(self, admitted: AdmittedCapitalSnapshot) -> AuthorityFence:
        if type(admitted) is not AdmittedCapitalSnapshot:
            raise TypeError("admitted must be AdmittedCapitalSnapshot")
        return AuthorityFence(
            self._store, admitted, self._clock, self._trusted_domain_identity
        )


@dataclass(frozen=True, slots=True)
class _TrustedProviderAuthorityComposition:
    authority_domain_identity: str
    bootstrap: BootstrapEvidence
    management: ProviderAuthorityManager
    runtime: ProviderAuthorityRuntime


def _establish_owner_composition(
    root: Path,
    *,
    command_id: str,
    store_epoch: str,
    policy_id: str,
    roles: tuple[str, ...],
    scopes: tuple[AuthorityScope, ...],
    trusted_at: Timestamp,
    resolver: ProviderResolver,
    resolver_identity: str,
    clock: ControlledUtcClock,
    clock_policy_identity: str,
    deployment_identity: str,
    timeout_seconds: float = 5.0,
) -> _TrustedProviderAuthorityComposition:
    """Removed local trust root; operational composition lives in the service."""
    del (
        root, command_id, store_epoch, policy_id, roles, scopes, trusted_at,
        resolver, resolver_identity, clock, clock_policy_identity,
        deployment_identity, timeout_seconds,
    )
    raise ProviderAuthorityError(
        "local owner composition is permanently disabled; operational admission "
        "requires authentic signed Authority Service evidence"
    )


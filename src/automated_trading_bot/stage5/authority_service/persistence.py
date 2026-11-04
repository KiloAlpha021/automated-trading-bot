"""Transactional authority truth with external anchor/pending witnesses."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
import time
from threading import Condition
from typing import Iterator, Protocol

import msvcrt

from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
    canonical_bytes,
    digest,
    same_digest,
)


class ApdDisposition(StrEnum):
    NORMAL_PREDECESSOR = "NORMAL_PREDECESSOR"
    FAIL_CLOSED_OWNER_RESOLUTION = "FAIL_CLOSED_OWNER_RESOLUTION"
    COMPLETE_ANCHOR_ADVANCE = "COMPLETE_ANCHOR_ADVANCE"
    COMPLETE_PENDING_RESOLUTION = "COMPLETE_PENDING_RESOLUTION"
    NORMAL_SUCCESSOR = "NORMAL_SUCCESSOR"
    FAIL_CLOSED_UNPROVEN_COMMIT = "FAIL_CLOSED_UNPROVEN_COMMIT"
    FAIL_CLOSED_ROLLBACK_OR_CONTRADICTION = "FAIL_CLOSED_ROLLBACK_OR_CONTRADICTION"
    FAIL_CLOSED_CONTRADICTION = "FAIL_CLOSED_CONTRADICTION"


class RetainedAuthorityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    disposition: ApdDisposition
    operational: bool


def classify_recovery(*, anchor: str, pending: str | None,
                      database: str) -> RecoveryDecision:
    if pending is not None and pending != database and database != anchor:
        return RecoveryDecision(ApdDisposition.FAIL_CLOSED_CONTRADICTION, False)
    if anchor == database and pending is None:
        return RecoveryDecision(ApdDisposition.NORMAL_SUCCESSOR, True)
    if pending == anchor and anchor == database:
        return RecoveryDecision(ApdDisposition.COMPLETE_PENDING_RESOLUTION, False)
    if pending is not None and anchor == database:
        return RecoveryDecision(ApdDisposition.FAIL_CLOSED_OWNER_RESOLUTION, False)
    if pending == database and anchor != database:
        return RecoveryDecision(ApdDisposition.COMPLETE_ANCHOR_ADVANCE, False)
    if pending is None and anchor != database:
        return RecoveryDecision(ApdDisposition.FAIL_CLOSED_UNPROVEN_COMMIT, False)
    if pending is not None and anchor != database:
        return RecoveryDecision(
            ApdDisposition.FAIL_CLOSED_ROLLBACK_OR_CONTRADICTION, False
        )
    return RecoveryDecision(ApdDisposition.FAIL_CLOSED_CONTRADICTION, False)


@dataclass(frozen=True, slots=True)
class AuthorityState:
    domain_identity: str
    store_epoch: str
    sequence: int
    authority_id: str
    authority_head: str
    authority_generation: int
    authority_status: RetainedAuthorityStatus
    authority_valid_from: str
    authority_expires_at: str
    currency: str
    provider_descriptor_digest: str
    provider_implementation_identity: str
    policy_id: str
    policy_version: int
    trusted_time_high_water: str
    current_key_id: str
    current_key_version: int
    key_records: tuple[str, ...]
    state_digest: str

    @classmethod
    def create(cls, **values: object) -> AuthorityState:
        values.setdefault("key_records", ())
        status = values.get("authority_status")
        if type(status) is str:
            values["authority_status"] = RetainedAuthorityStatus(status)
        body = dict(values)
        return cls(**values, state_digest=digest(body))  # type: ignore[arg-type]

    def body(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "state_digest"
        }

    def verify(self) -> None:
        if self.state_digest != digest(self.body()):
            raise AuthorityServiceError("authority state digest mismatch")
        if (
            not self.domain_identity or not self.store_epoch or not self.authority_id
            or not self.currency
            or not self.authority_head or self.sequence < 0
            or self.authority_generation < 1 or self.policy_version < 1
            or type(self.authority_status) is not RetainedAuthorityStatus
        ):
            raise AuthorityServiceError("authority state is semantically invalid")
        try:
            valid_from = datetime.fromisoformat(self.authority_valid_from)
            expires_at = datetime.fromisoformat(self.authority_expires_at)
            high_water = datetime.fromisoformat(self.trusted_time_high_water)
        except ValueError as error:
            raise AuthorityServiceError("authority time is invalid") from error
        if (
            valid_from.tzinfo is not timezone.utc
            or expires_at.tzinfo is not timezone.utc
            or high_water.tzinfo is not timezone.utc
            or valid_from >= expires_at
            or expires_at - valid_from > timedelta(hours=24)
        ):
            raise AuthorityServiceError("authority validity is invalid")


class ExternalWitness(Protocol):
    def read_anchor(self) -> str: ...
    def read_pending(self) -> str | None: ...
    def write_pending(self, value: str) -> None: ...
    def write_anchor(self, value: str) -> None: ...
    def clear_pending(self, expected: str) -> None: ...


class AuthorityDatabase:
    """One SQLite transaction owns authority state and command idempotency."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._gate = Condition()
        self._active_readers = 0
        self._waiting_writers = 0
        self._writer_active = False

    @contextmanager
    def _reader_turn(self, timeout: float = 5.0) -> Iterator[None]:
        """Prevent new operational consumers from overtaking a waiting writer."""
        deadline = time.monotonic() + timeout
        with self._gate:
            while self._writer_active or self._waiting_writers:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._gate.wait(remaining):
                    raise AuthorityServiceError("authority witness lock timed out")
            self._active_readers += 1
        try:
            yield
        finally:
            with self._gate:
                self._active_readers -= 1
                self._gate.notify_all()

    @contextmanager
    def _writer_turn(self, timeout: float = 5.0) -> Iterator[None]:
        deadline = time.monotonic() + timeout
        with self._gate:
            self._waiting_writers += 1
            try:
                while self._writer_active or self._active_readers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._gate.wait(remaining):
                        raise AuthorityServiceError("authority witness lock timed out")
                self._writer_active = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._gate:
                self._writer_active = False
                self._gate.notify_all()

    @contextmanager
    def _exclusive(self, timeout: float = 5.0) -> Iterator[None]:
        with self._writer_turn(timeout):
            with self._file_lock(timeout):
                yield

    @contextmanager
    def _consumer_exclusive(self, timeout: float = 5.0) -> Iterator[None]:
        with self._reader_turn(timeout):
            with self._file_lock(timeout):
                yield

    @contextmanager
    def _file_lock(self, timeout: float) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+b")
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AuthorityServiceError("authority witness lock timed out")
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()

    def initialise(self, state: AuthorityState) -> None:
        state.verify()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS metadata (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema_version INTEGER NOT NULL,
                    state_json BLOB NOT NULL,
                    state_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    payload_digest TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    result_json BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identity_nonces (
                    capability TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    consumed_at TEXT NOT NULL,
                    PRIMARY KEY (capability, nonce),
                    CHECK (capability IN (
                        'ACCOUNT_IDENTITY_V1','PORTFOLIO_IDENTITY_V1'
                    ))
                );
                """
            )
            row = connection.execute(
                "SELECT state_digest FROM metadata WHERE singleton=1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO metadata VALUES (1, 1, ?, ?)",
                    (canonical_bytes(state.body()), state.state_digest),
                )
            elif row[0] != state.state_digest:
                raise AuthorityServiceError("foreign or conflicting authority store")

    def consume_identity_nonce(
        self, *, capability: str, nonce: str, payload_digest: str,
        consumed_at: datetime,
    ) -> None:
        """Atomically consume a capability nonce; replay never becomes authority."""
        if capability not in ("ACCOUNT_IDENTITY_V1", "PORTFOLIO_IDENTITY_V1"):
            raise AuthorityServiceError("unsupported identity capability")
        if not nonce or not payload_digest or consumed_at.tzinfo is not timezone.utc:
            raise AuthorityServiceError("invalid identity replay evidence")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO identity_nonces VALUES (?,?,?,?)",
                    (capability, nonce, payload_digest, consumed_at.isoformat()),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise AuthorityServiceError("identity capability replay") from error
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def read(self) -> AuthorityState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT schema_version,state_json,state_digest FROM metadata WHERE singleton=1"
            ).fetchone()
        if row is None or row[0] != 1:
            raise AuthorityServiceError("authority store is absent or unknown")
        body = json.loads(bytes(row[1]))
        body["key_records"] = tuple(body.get("key_records", ()))
        body["authority_status"] = RetainedAuthorityStatus(
            body["authority_status"]
        )
        state = AuthorityState(**body, state_digest=row[2])
        state.verify()
        return state

    def command_result(
        self, command_id: str, payload_digest: str, *, state_digest: str,
    ) -> bytes | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_digest,result_digest,result_json FROM commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        if not same_digest(row[0], payload_digest):
            raise AuthorityServiceError("command ID reused with conflicting payload")
        result = bytes(row[2])
        if not same_digest(row[1], digest(result.hex())):
            raise AuthorityServiceError("committed command result digest mismatch")
        try:
            result_body = json.loads(result)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthorityServiceError("committed command result is invalid") from error
        result_state_digest = (
            result_body.get("state_digest") if type(result_body) is dict else None
        )
        if (
            type(result_state_digest) is not str
            or not same_digest(result_state_digest, state_digest)
        ):
            raise AuthorityServiceError("committed command witness is not current")
        return result

    @staticmethod
    def _require_successor(
        current: AuthorityState, successor: AuthorityState
    ) -> None:
        if (
            successor.domain_identity != current.domain_identity
            or successor.store_epoch != current.store_epoch
            or successor.currency != current.currency
            or successor.sequence != current.sequence + 1
            or successor.authority_generation
            not in (current.authority_generation, current.authority_generation + 1)
            or successor.trusted_time_high_water < current.trusted_time_high_water
        ):
            raise AuthorityServiceError("invalid authority successor")
        if successor.authority_generation == current.authority_generation:
            if (
                successor.authority_id != current.authority_id
                or successor.provider_descriptor_digest
                != current.provider_descriptor_digest
                or successor.provider_implementation_identity
                != current.provider_implementation_identity
                or successor.policy_id != current.policy_id
                or successor.policy_version != current.policy_version
                or successor.authority_valid_from != current.authority_valid_from
                or successor.authority_expires_at != current.authority_expires_at
                or (
                    current.authority_status
                    is not RetainedAuthorityStatus.ACTIVE
                    and successor.authority_status
                    is RetainedAuthorityStatus.ACTIVE
                )
            ):
                raise AuthorityServiceError("invalid same-generation authority successor")
        elif (
            successor.authority_status is not RetainedAuthorityStatus.ACTIVE
            or successor.authority_id == current.authority_id
        ):
            raise AuthorityServiceError("invalid authority-generation successor")

    def commit_successor(self, *, expected_digest: str, command_id: str,
                         payload_digest: str, successor: AuthorityState,
                         result: bytes) -> None:
        successor.verify()
        try:
            result_body = json.loads(result)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthorityServiceError("command result is invalid") from error
        result_state_digest = (
            result_body.get("state_digest") if type(result_body) is dict else None
        )
        if (
            type(result_state_digest) is not str
            or not same_digest(result_state_digest, successor.state_digest)
        ):
            raise AuthorityServiceError("command result witness mismatch")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state_json,state_digest FROM metadata WHERE singleton=1"
                ).fetchone()
                if row is None or not same_digest(row[1], expected_digest):
                    raise AuthorityServiceError("stale authority predecessor")
                body = json.loads(bytes(row[0]))
                body["key_records"] = tuple(body.get("key_records", ()))
                body["authority_status"] = RetainedAuthorityStatus(
                    body["authority_status"]
                )
                current = AuthorityState(
                    **body, state_digest=row[1]
                )
                current.verify()
                self._require_successor(current, successor)
                existing = connection.execute(
                    "SELECT payload_digest,result_digest FROM commands WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                if existing is not None:
                    if not same_digest(existing[0], payload_digest):
                        raise AuthorityServiceError(
                            "command ID reused with conflicting payload"
                        )
                    raise AuthorityServiceError("command already committed")
                connection.execute(
                    "UPDATE metadata SET state_json=?,state_digest=? WHERE singleton=1",
                    (canonical_bytes(successor.body()), successor.state_digest),
                )
                connection.execute(
                    "INSERT INTO commands VALUES (?,?,?,?)",
                    (command_id, payload_digest, digest(result.hex()), result),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def publish(self, *, witness: ExternalWitness, command_id: str,
                payload_digest: str, successor: AuthorityState,
                result: bytes) -> bytes:
        with self._exclusive():
            current = self.read()
            anchor = witness.read_anchor()
            pending = witness.read_pending()
            decision = classify_recovery(
                anchor=anchor, pending=pending, database=current.state_digest
            )
            if not decision.operational:
                raise AuthorityServiceError(
                    f"authority witness is not operational: {decision.disposition}"
                )
            prior_result = self.command_result(
                command_id, payload_digest, state_digest=current.state_digest
            )
            if prior_result is not None:
                return prior_result
            witness.write_pending(successor.state_digest)
            if witness.read_pending() != successor.state_digest:
                raise AuthorityServiceError("pending witness readback failed")
            self.commit_successor(
                expected_digest=current.state_digest, command_id=command_id,
                payload_digest=payload_digest, successor=successor, result=result,
            )
            if self.read().state_digest != successor.state_digest:
                raise AuthorityServiceError("committed authority readback failed")
            witness.write_anchor(successor.state_digest)
            if witness.read_anchor() != successor.state_digest:
                raise AuthorityServiceError("anchor readback failed")
            witness.clear_pending(successor.state_digest)
            if witness.read_pending() is not None:
                raise AuthorityServiceError("pending witness resolution failed")
            return result

    def retry_result(
        self, *, witness: ExternalWitness, command_id: str,
        payload_digest: str,
    ) -> bytes:
        """Return a committed result only under the current coherent A/P/D witness."""
        with self._exclusive():
            state = self.read()
            decision = classify_recovery(
                anchor=witness.read_anchor(), pending=witness.read_pending(),
                database=state.state_digest,
            )
            if not decision.operational:
                raise AuthorityServiceError(
                    f"authority witness is not operational: {decision.disposition}"
                )
            result = self.command_result(
                command_id, payload_digest, state_digest=state.state_digest
            )
            if result is None:
                raise AuthorityServiceError("stale Management authority head")
            return result

    def recover(self, witness: ExternalWitness) -> AuthorityState:
        """Complete only a uniquely provable A/P/D repair; otherwise fail closed."""
        with self._exclusive():
            state = self.read()
            pending = witness.read_pending()
            decision = classify_recovery(
                anchor=witness.read_anchor(), pending=pending,
                database=state.state_digest,
            )
            if decision.disposition is ApdDisposition.COMPLETE_ANCHOR_ADVANCE:
                witness.write_anchor(state.state_digest)
                if witness.read_anchor() != state.state_digest:
                    raise AuthorityServiceError("recovery anchor readback failed")
                if pending is None:
                    raise AuthorityServiceError("recovery pending witness is missing")
                witness.clear_pending(pending)
            elif decision.disposition is ApdDisposition.COMPLETE_PENDING_RESOLUTION:
                if pending is None:
                    raise AuthorityServiceError("recovery pending witness is missing")
                witness.clear_pending(pending)
            elif not decision.operational:
                raise AuthorityServiceError(
                    f"authority recovery requires owner resolution: {decision.disposition}"
                )
            final = classify_recovery(
                anchor=witness.read_anchor(), pending=witness.read_pending(),
                database=state.state_digest,
            )
            if not final.operational:
                raise AuthorityServiceError("authority recovery readback failed")
            return state

    @contextmanager
    def admission_witness(
        self, *, witness: ExternalWitness, observed_at: str
    ) -> Iterator[AuthorityState]:
        """Hold one cross-process witness while validating and signing admission."""
        with self._exclusive():
            state = self.read()
            decision = classify_recovery(
                anchor=witness.read_anchor(), pending=witness.read_pending(),
                database=state.state_digest,
            )
            if not decision.operational:
                raise AuthorityServiceError(
                    f"authority witness is not operational: {decision.disposition}"
                )
            try:
                observed = datetime.fromisoformat(observed_at)
                high_water = datetime.fromisoformat(state.trusted_time_high_water)
                valid_from = datetime.fromisoformat(state.authority_valid_from)
                expires_at = datetime.fromisoformat(state.authority_expires_at)
            except ValueError as error:
                raise AuthorityServiceError("trusted authority time is invalid") from error
            if observed.tzinfo is not timezone.utc:
                raise AuthorityServiceError("trusted time must be canonical UTC")
            if observed < high_water:
                raise AuthorityServiceError("trusted time moved backwards")
            if state.authority_status is not RetainedAuthorityStatus.ACTIVE:
                raise AuthorityServiceError("authority is not active")
            if observed < valid_from:
                raise AuthorityServiceError("authority is not yet valid")
            if observed >= expires_at:
                body = state.body()
                body.update(
                    sequence=state.sequence + 1,
                    trusted_time_high_water=observed_at,
                    authority_status=RetainedAuthorityStatus.EXPIRED,
                )
                body["authority_head"] = digest(body)
                successor = AuthorityState.create(**body)
                command_id = f"EXPIRE:{state.authority_id}:{state.authority_expires_at}"
                payload_digest = digest({
                    "authority_id": state.authority_id,
                    "expires_at": state.authority_expires_at,
                })
                result = canonical_bytes({
                    "command_id": command_id,
                    "state_digest": successor.state_digest,
                })
                witness.write_pending(successor.state_digest)
                if witness.read_pending() != successor.state_digest:
                    raise AuthorityServiceError("expiry pending readback failed")
                self.commit_successor(
                    expected_digest=state.state_digest, command_id=command_id,
                    payload_digest=payload_digest, successor=successor, result=result,
                )
                witness.write_anchor(successor.state_digest)
                if witness.read_anchor() != successor.state_digest:
                    raise AuthorityServiceError("expiry anchor readback failed")
                witness.clear_pending(successor.state_digest)
                if witness.read_pending() is not None:
                    raise AuthorityServiceError("expiry pending resolution failed")
                raise AuthorityServiceError("authority is expired")
            if observed_at > state.trusted_time_high_water:
                body = state.body()
                body.update(
                    sequence=state.sequence + 1,
                    trusted_time_high_water=observed_at,
                )
                successor = AuthorityState.create(**body)
                command_id = f"TRUSTED_TIME:{observed_at}"
                payload_digest = digest({"observed_at": observed_at})
                result = canonical_bytes({
                    "command_id": command_id,
                    "state_digest": successor.state_digest,
                })
                witness.write_pending(successor.state_digest)
                if witness.read_pending() != successor.state_digest:
                    raise AuthorityServiceError("trusted-time pending readback failed")
                self.commit_successor(
                    expected_digest=state.state_digest,
                    command_id=command_id,
                    payload_digest=payload_digest,
                    successor=successor,
                    result=result,
                )
                witness.write_anchor(successor.state_digest)
                if witness.read_anchor() != successor.state_digest:
                    raise AuthorityServiceError("trusted-time anchor readback failed")
                witness.clear_pending(successor.state_digest)
                if witness.read_pending() is not None:
                    raise AuthorityServiceError("trusted-time pending resolution failed")
                state = successor
            yield state

    @contextmanager
    def consumption_witness(
        self,
        *,
        witness: ExternalWitness,
        observed_at: str,
        timeout: float = 5.0,
    ) -> Iterator[AuthorityState]:
        """Hold the exact current authority state through consumer completion.

        Unlike admission issuance this read-only boundary does not advance the
        trusted-time high-water (which would itself change the authority head).
        It still rejects a backwards clock and validates current validity while
        the writer-prioritized authority lock remains held.
        """
        with self._consumer_exclusive(timeout):
            state = self.read()
            decision = classify_recovery(
                anchor=witness.read_anchor(), pending=witness.read_pending(),
                database=state.state_digest,
            )
            if not decision.operational:
                raise AuthorityServiceError(
                    f"authority witness is not operational: {decision.disposition}"
                )
            try:
                observed = datetime.fromisoformat(observed_at)
                high_water = datetime.fromisoformat(state.trusted_time_high_water)
                valid_from = datetime.fromisoformat(state.authority_valid_from)
                expires_at = datetime.fromisoformat(state.authority_expires_at)
            except ValueError as error:
                raise AuthorityServiceError("trusted authority time is invalid") from error
            if observed.tzinfo is not timezone.utc:
                raise AuthorityServiceError("trusted time must be canonical UTC")
            if observed < high_water:
                raise AuthorityServiceError("trusted time moved backwards")
            if state.authority_status is not RetainedAuthorityStatus.ACTIVE:
                raise AuthorityServiceError("authority is not active")
            if observed < valid_from or observed >= expires_at:
                raise AuthorityServiceError("authority is not currently valid")
            yield state

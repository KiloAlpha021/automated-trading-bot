"""Durable server-side owner sessions with persisted revocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import Final


MAX_ACTIVE_SESSIONS: Final = 8
SESSION_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    idle_timeout: timedelta = timedelta(minutes=15)
    absolute_timeout: timedelta = timedelta(hours=8)

    def __post_init__(self) -> None:
        if not timedelta(minutes=1) <= self.idle_timeout <= timedelta(hours=1):
            raise ValueError("idle timeout is outside policy")
        if not self.idle_timeout < self.absolute_timeout <= timedelta(hours=24):
            raise ValueError("absolute timeout is outside policy")


@dataclass(frozen=True, slots=True)
class ValidSession:
    session_ref: str
    principal_id: str
    csrf_token: str
    expires_at_utc: datetime


class SessionStore:
    def __init__(self, database: Path, policy: SessionPolicy = SessionPolicy()) -> None:
        if not database.is_absolute() or not database.parent.is_dir():
            raise ValueError("session database parent must be pre-provisioned")
        self.database = database
        self.policy = policy
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=2, isolation_level="IMMEDIATE")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _prepare(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token_digest BLOB PRIMARY KEY,
                    session_ref TEXT NOT NULL UNIQUE,
                    principal_id TEXT NOT NULL,
                    csrf_digest BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    absolute_expires REAL NOT NULL,
                    revoked_at REAL
                )"""
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS revocations (epoch INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO revocations(epoch) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM revocations)"
            )

    @staticmethod
    def _digest(token: str) -> bytes:
        return sha256(token.encode("ascii")).digest()

    def create(
        self, principal_id: str, *, now: datetime | None = None, rotate_token: str | None = None
    ) -> tuple[str, ValidSession]:
        instant = datetime.now(UTC) if now is None else now
        if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
            raise ValueError("session time must be UTC")
        token = secrets.token_urlsafe(SESSION_BYTES)
        csrf = secrets.token_urlsafe(SESSION_BYTES)
        reference = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE revoked_at IS NULL "
                "AND (absolute_expires<=? OR last_seen<?)",
                (instant.timestamp(), instant.timestamp(),
                 (instant - self.policy.idle_timeout).timestamp()),
            )
            if rotate_token is not None:
                connection.execute(
                    "UPDATE sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL",
                    (instant.timestamp(), self._digest(rotate_token)),
                )
            active = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL AND absolute_expires>?",
                (instant.timestamp(),),
            ).fetchone()[0]
            if active >= MAX_ACTIVE_SESSIONS:
                raise RuntimeError("active session limit reached")
            absolute = instant + self.policy.absolute_timeout
            connection.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,NULL)",
                (self._digest(token), reference, principal_id, self._digest(csrf),
                 instant.timestamp(), instant.timestamp(), absolute.timestamp()),
            )
        return token, ValidSession(reference, principal_id, csrf, absolute)

    def validate(self, token: str, *, now: datetime | None = None) -> ValidSession | None:
        if type(token) is not str or len(token) > 128:
            return None
        instant = datetime.now(UTC) if now is None else now
        digest = self._digest(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_digest,session_ref,principal_id,csrf_digest,last_seen,absolute_expires,revoked_at "
                "FROM sessions WHERE token_digest=?", (digest,)
            ).fetchone()
            if row is None or not hmac.compare_digest(row[0], digest) or row[6] is not None:
                return None
            if instant.timestamp() >= row[5] or instant.timestamp() - row[4] > self.policy.idle_timeout.total_seconds():
                connection.execute(
                    "UPDATE sessions SET revoked_at=? WHERE token_digest=?",
                    (instant.timestamp(), digest),
                )
                return None
            connection.execute(
                "UPDATE sessions SET last_seen=? WHERE token_digest=?",
                (instant.timestamp(), digest),
            )
            return ValidSession(
                row[1], row[2], "", datetime.fromtimestamp(row[5], UTC)
            )

    def verify_csrf(self, token: str, csrf_token: str) -> bool:
        if type(csrf_token) is not str or len(csrf_token) > 128:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT csrf_digest FROM sessions WHERE token_digest=? AND revoked_at IS NULL",
                (self._digest(token),),
            ).fetchone()
        return row is not None and hmac.compare_digest(row[0], self._digest(csrf_token))

    def revoke(self, token: str, *, now: datetime | None = None) -> None:
        instant = datetime.now(UTC) if now is None else now
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL",
                (instant.timestamp(), self._digest(token)),
            )

    def revoke_all(self, *, now: datetime | None = None) -> int:
        instant = datetime.now(UTC) if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE revoked_at IS NULL",
                (instant.timestamp(),),
            )
            connection.execute("UPDATE revocations SET epoch=epoch+1")
            return cursor.rowcount

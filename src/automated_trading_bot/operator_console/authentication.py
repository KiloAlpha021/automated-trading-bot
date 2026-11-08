"""Owner-only WebAuthn ceremonies for the local Operator Console.

Initial and recovery enrolment require an injected trusted authority.  There is
no password, default credential, recovery token, or unauthenticated reset path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Final, Protocol

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.reconciliation.operator_console_read_gateway import (
    AuthenticatedPrincipal,
    REQUIRED_PERMISSION,
)


OWNER_ID: Final = "atis-owner"
CHALLENGE_BYTES: Final = 32
CHALLENGE_TTL: Final = timedelta(minutes=3)
AUTHENTICATION_TTL: Final = timedelta(minutes=5)
MAX_OWNER_CREDENTIALS: Final = 2
MAX_PENDING_CHALLENGES: Final = 64
RECOVERY_TTL: Final = timedelta(minutes=5)


class AuthenticationError(RuntimeError):
    pass


class EnrollmentAuthority(Protocol):
    """Separately qualified offline bootstrap or authenticated owner boundary."""

    def authorize_enrollment(self, owner_id: str, existing_credentials: int) -> bool: ...

    def authorize_revocation(self, owner_id: str, credential_ref: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Ceremony:
    ceremony_id: str
    public_options_json: str
    expires_at_utc: datetime


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    credential_ref: str
    credential_id: bytes
    public_key: bytes
    sign_count: int
    revoked: bool


@dataclass(frozen=True, slots=True)
class RecoveryGrant:
    token: str
    csrf_token: str
    expires_at_utc: datetime


class OwnerWebAuthn:
    def __init__(self, database: Path, *, rp_id: str, expected_origin: str) -> None:
        if rp_id != "localhost":
            raise ValueError("the qualified local HTTP RP ID is localhost")
        if expected_origin != "http://localhost:8765":
            raise ValueError("the qualified origin must be exact")
        if not database.is_absolute() or not database.parent.is_dir():
            raise ValueError("credential database parent must be pre-provisioned")
        self.database = database
        self.rp_id = rp_id
        self.expected_origin = expected_origin
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=2, isolation_level="IMMEDIATE")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _prepare(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS credentials (
                    credential_id BLOB PRIMARY KEY,
                    credential_ref TEXT NOT NULL UNIQUE,
                    public_key BLOB NOT NULL,
                    sign_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS recovery_grants (
                    token_digest BLOB PRIMARY KEY,
                    csrf_digest BLOB NOT NULL,
                    survivor_credential_id BLOB NOT NULL,
                    lost_credential_id BLOB NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS challenges (
                    ceremony_id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    challenge BLOB NOT NULL,
                    credential_id BLOB,
                    expires_at REAL NOT NULL
                )"""
            )

    def _active_credentials(self) -> list[CredentialRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT credential_ref,credential_id,public_key,sign_count,revoked_at "
                "FROM credentials ORDER BY created_at"
            ).fetchall()
        return [CredentialRecord(row[0], row[1], row[2], row[3], row[4] is not None) for row in rows]

    def _store_challenge(
        self,
        ceremony_id: str,
        purpose: str,
        challenge: bytes,
        expires: datetime,
        *,
        now: datetime,
        context: bytes | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM challenges WHERE expires_at<=?", (now.timestamp(),))
            count = connection.execute("SELECT COUNT(*) FROM challenges").fetchone()[0]
            if count >= MAX_PENDING_CHALLENGES:
                raise AuthenticationError("pending ceremony limit reached")
            connection.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?)",
                (ceremony_id, purpose, challenge, context, expires.timestamp()),
            )

    def active_credential_count(self) -> int:
        return sum(not item.revoked for item in self._active_credentials())

    def recovery_ready(self) -> bool:
        return self.active_credential_count() == MAX_OWNER_CREDENTIALS

    def begin_registration(
        self,
        authority: EnrollmentAuthority,
        *,
        now: datetime | None = None,
    ) -> Ceremony:
        instant = datetime.now(UTC) if now is None else now
        count = self.active_credential_count()
        if count >= MAX_OWNER_CREDENTIALS or not authority.authorize_enrollment(OWNER_ID, count):
            raise AuthenticationError("credential enrolment is not authorised")
        challenge = secrets.token_bytes(CHALLENGE_BYTES)
        ceremony_id = secrets.token_hex(16)
        expires = instant + CHALLENGE_TTL
        existing = [
            PublicKeyCredentialDescriptor(id=item.credential_id)
            for item in self._active_credentials() if not item.revoked
        ]
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name="ATIS Local Operator Console",
            user_id=OWNER_ID.encode("ascii"),
            user_name=OWNER_ID,
            user_display_name="ATIS Owner",
            challenge=challenge,
            timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
            exclude_credentials=existing,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        self._store_challenge(
            ceremony_id, "REGISTER", challenge, expires, now=instant
        )
        return Ceremony(ceremony_id, options_to_json(options), expires)

    def _consume_challenge(
        self, ceremony_id: str, purpose: str, *, now: datetime
    ) -> tuple[bytes, bytes | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT purpose,challenge,credential_id,expires_at FROM challenges WHERE ceremony_id=?",
                (ceremony_id,),
            ).fetchone()
            connection.execute("DELETE FROM challenges WHERE ceremony_id=?", (ceremony_id,))
        if row is None or row[0] != purpose or now.timestamp() >= row[3]:
            raise AuthenticationError("authentication ceremony is invalid or expired")
        return row[1], row[2]

    def complete_registration(
        self, ceremony_id: str, credential: dict[str, object], *, now: datetime | None = None
    ) -> CredentialRecord:
        instant = datetime.now(UTC) if now is None else now
        challenge, _ = self._consume_challenge(ceremony_id, "REGISTER", now=instant)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.expected_origin,
                require_user_verification=True,
            )
        except (InvalidRegistrationResponse, ValueError, TypeError) as error:
            raise AuthenticationError("credential registration verification failed") from error
        reference = secrets.token_hex(16)
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                active = connection.execute(
                    "SELECT COUNT(*) FROM credentials WHERE revoked_at IS NULL"
                ).fetchone()[0]
                if active >= MAX_OWNER_CREDENTIALS:
                    raise AuthenticationError("owner credential limit reached")
                connection.execute(
                    "INSERT INTO credentials VALUES(?,?,?,?,?,NULL)",
                    (verified.credential_id, reference, verified.credential_public_key,
                     verified.sign_count, instant.timestamp()),
                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.IntegrityError as error:
            raise AuthenticationError("credential is already registered") from error
        return CredentialRecord(
            reference, verified.credential_id, verified.credential_public_key,
            verified.sign_count, False
        )

    def begin_authentication(self, *, now: datetime | None = None) -> Ceremony:
        instant = datetime.now(UTC) if now is None else now
        active = [item for item in self._active_credentials() if not item.revoked]
        if len(active) != MAX_OWNER_CREDENTIALS:
            raise AuthenticationError("two owner passkeys are required")
        challenge = secrets.token_bytes(CHALLENGE_BYTES)
        ceremony_id = secrets.token_hex(16)
        expires = instant + CHALLENGE_TTL
        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
            allow_credentials=[PublicKeyCredentialDescriptor(id=item.credential_id) for item in active],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        self._store_challenge(
            ceremony_id, "AUTHENTICATE", challenge, expires, now=instant
        )
        return Ceremony(ceremony_id, options_to_json(options), expires)

    def complete_authentication(
        self, ceremony_id: str, credential: dict[str, object], *, now: datetime | None = None
    ) -> AuthenticatedPrincipal:
        instant = datetime.now(UTC) if now is None else now
        challenge, _ = self._consume_challenge(ceremony_id, "AUTHENTICATE", now=instant)
        record, new_sign_count = self._verify_assertion(credential, challenge)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE credentials SET sign_count=? WHERE credential_id=? AND revoked_at IS NULL",
                (new_sign_count, record.credential_id),
            )
            if cursor.rowcount != 1:
                raise AuthenticationError("credential state changed during authentication")
        return AuthenticatedPrincipal(
            OWNER_ID,
            frozenset({REQUIRED_PERMISSION}),
            Timestamp(instant),
            Timestamp(instant + AUTHENTICATION_TTL),
        )

    def _verify_assertion(
        self, credential: dict[str, object], challenge: bytes
    ) -> tuple[CredentialRecord, int]:
        try:
            credential_id = base64url_to_bytes(str(credential["id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise AuthenticationError("authentication response is invalid") from error
        record = next(
            (item for item in self._active_credentials() if item.credential_id == credential_id),
            None,
        )
        if record is None or record.revoked:
            raise AuthenticationError("credential is unavailable")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.expected_origin,
                credential_public_key=record.public_key,
                credential_current_sign_count=record.sign_count,
                require_user_verification=True,
            )
        except (InvalidAuthenticationResponse, ValueError, TypeError) as error:
            raise AuthenticationError("authentication verification failed") from error
        return record, verified.new_sign_count

    @staticmethod
    def _secret_digest(value: str) -> bytes:
        return sha256(value.encode("ascii")).digest()

    def begin_recovery(
        self, lost_credential_ref: str, *, now: datetime | None = None
    ) -> Ceremony:
        """Start a scope-limited ceremony with the credential that survived."""

        instant = datetime.now(UTC) if now is None else now
        records = self._active_credentials()
        lost = next(
            (item for item in records if item.credential_ref == lost_credential_ref),
            None,
        )
        if lost is None:
            raise AuthenticationError("recovery request is invalid")
        survivors = [
            item for item in records
            if not item.revoked and item.credential_id != lost.credential_id
        ]
        active_count = sum(not item.revoked for item in records)
        if len(survivors) != 1 or active_count not in {1, 2}:
            raise AuthenticationError("recovery is not available")
        challenge = secrets.token_bytes(CHALLENGE_BYTES)
        ceremony_id = secrets.token_hex(16)
        expires = instant + CHALLENGE_TTL
        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=survivors[0].credential_id)
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        self._store_challenge(
            ceremony_id,
            "RECOVER",
            challenge,
            expires,
            now=instant,
            context=lost.credential_id,
        )
        return Ceremony(ceremony_id, options_to_json(options), expires)

    def complete_recovery(
        self,
        ceremony_id: str,
        credential: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> RecoveryGrant:
        instant = datetime.now(UTC) if now is None else now
        challenge, lost_credential_id = self._consume_challenge(
            ceremony_id, "RECOVER", now=instant
        )
        if lost_credential_id is None:
            raise AuthenticationError("recovery context is missing")
        survivor, new_sign_count = self._verify_assertion(credential, challenge)
        if survivor.credential_id == lost_credential_id:
            raise AuthenticationError("lost credential cannot authorize recovery")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        token_digest = self._secret_digest(token)
        expires = instant + RECOVERY_TTL
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revoked_at FROM credentials WHERE credential_id=?",
                (lost_credential_id,),
            ).fetchone()
            if current is None:
                raise AuthenticationError("lost credential is unavailable")
            survivor_update = connection.execute(
                "UPDATE credentials SET sign_count=? WHERE credential_id=? AND revoked_at IS NULL",
                (new_sign_count, survivor.credential_id),
            )
            if survivor_update.rowcount != 1:
                raise AuthenticationError("surviving credential state changed during recovery")
            connection.execute(
                "UPDATE credentials SET revoked_at=COALESCE(revoked_at,?) WHERE credential_id=?",
                (instant.timestamp(), lost_credential_id),
            )
            connection.execute(
                "DELETE FROM recovery_grants WHERE expires_at<=? OR consumed_at IS NOT NULL",
                (instant.timestamp(),),
            )
            connection.execute(
                "INSERT INTO recovery_grants VALUES(?,?,?,?,?,NULL)",
                (token_digest, self._secret_digest(csrf), survivor.credential_id,
                 lost_credential_id, expires.timestamp()),
            )
            connection.commit()
        finally:
            connection.close()
        return RecoveryGrant(token, csrf, expires)

    def _validated_recovery_grant(
        self, token: str, csrf_token: str, *, now: datetime
    ) -> tuple[bytes, bytes, bytes]:
        token_digest = self._secret_digest(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_digest,csrf_digest,survivor_credential_id,lost_credential_id,expires_at,consumed_at "
                "FROM recovery_grants WHERE token_digest=?",
                (token_digest,),
            ).fetchone()
        if (
            row is None
            or not hmac.compare_digest(row[0], token_digest)
            or not hmac.compare_digest(row[1], self._secret_digest(csrf_token))
            or row[5] is not None
            or now.timestamp() >= row[4]
        ):
            raise AuthenticationError("recovery authority is invalid or expired")
        return token_digest, row[2], row[3]

    def begin_replacement(
        self,
        token: str,
        csrf_token: str,
        *,
        now: datetime | None = None,
    ) -> Ceremony:
        instant = datetime.now(UTC) if now is None else now
        token_digest, survivor_id, _ = self._validated_recovery_grant(
            token, csrf_token, now=instant
        )
        active = [item for item in self._active_credentials() if not item.revoked]
        if len(active) != 1 or active[0].credential_id != survivor_id:
            raise AuthenticationError("recovery registry state is invalid")
        challenge = secrets.token_bytes(CHALLENGE_BYTES)
        ceremony_id = secrets.token_hex(16)
        expires = instant + CHALLENGE_TTL
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name="ATIS Local Operator Console",
            user_id=OWNER_ID.encode("ascii"),
            user_name=OWNER_ID,
            user_display_name="ATIS Owner",
            challenge=challenge,
            timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=survivor_id)],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        self._store_challenge(
            ceremony_id,
            "REPLACE",
            challenge,
            expires,
            now=instant,
            context=token_digest,
        )
        return Ceremony(ceremony_id, options_to_json(options), expires)

    def complete_replacement(
        self,
        ceremony_id: str,
        credential: dict[str, object],
        token: str,
        csrf_token: str,
        *,
        now: datetime | None = None,
    ) -> CredentialRecord:
        instant = datetime.now(UTC) if now is None else now
        challenge, challenge_grant = self._consume_challenge(
            ceremony_id, "REPLACE", now=instant
        )
        token_digest, survivor_id, _ = self._validated_recovery_grant(
            token, csrf_token, now=instant
        )
        if challenge_grant is None or not hmac.compare_digest(
            challenge_grant, token_digest
        ):
            raise AuthenticationError("replacement ceremony is not bound to recovery")
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.expected_origin,
                require_user_verification=True,
            )
        except (InvalidRegistrationResponse, ValueError, TypeError) as error:
            raise AuthenticationError("replacement verification failed") from error
        reference = secrets.token_hex(16)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            grant = connection.execute(
                "SELECT survivor_credential_id,expires_at,consumed_at FROM recovery_grants WHERE token_digest=?",
                (token_digest,),
            ).fetchone()
            active = connection.execute(
                "SELECT credential_id FROM credentials WHERE revoked_at IS NULL"
            ).fetchall()
            if (
                grant is None
                or grant[2] is not None
                or instant.timestamp() >= grant[1]
                or grant[0] != survivor_id
                or active != [(survivor_id,)]
            ):
                raise AuthenticationError("recovery replacement lost exclusive authority")
            connection.execute(
                "INSERT INTO credentials VALUES(?,?,?,?,?,NULL)",
                (verified.credential_id, reference, verified.credential_public_key,
                 verified.sign_count, instant.timestamp()),
            )
            cursor = connection.execute(
                "UPDATE recovery_grants SET consumed_at=? WHERE token_digest=? AND consumed_at IS NULL",
                (instant.timestamp(), token_digest),
            )
            if cursor.rowcount != 1:
                raise AuthenticationError("recovery replacement was already consumed")
            connection.commit()
        except sqlite3.IntegrityError as error:
            raise AuthenticationError("replacement credential is already registered") from error
        finally:
            connection.close()
        return CredentialRecord(
            reference, verified.credential_id, verified.credential_public_key,
            verified.sign_count, False
        )

    def revoke_credential(
        self, credential_ref: str, authority: EnrollmentAuthority, *, now: datetime | None = None
    ) -> None:
        if not authority.authorize_revocation(OWNER_ID, credential_ref):
            raise AuthenticationError("credential revocation is not authorised")
        instant = datetime.now(UTC) if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE credentials SET revoked_at=? WHERE credential_ref=? AND revoked_at IS NULL",
                (instant.timestamp(), credential_ref),
            )
            if cursor.rowcount != 1:
                raise AuthenticationError("credential is unavailable")

    def export_public_policy(self) -> str:
        return json.dumps(
            {
                "complete_credential_loss": "OFFLINE_OWNER_AUTHORITY_REQUIRED",
                "emergency_password": False,
                "maximum_owner_credentials": MAX_OWNER_CREDENTIALS,
                "minimum_owner_credentials_for_login": MAX_OWNER_CREDENTIALS,
                "recovery_factor_is_equal_strength": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

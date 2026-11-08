"""Non-activated ASGI application for the local Operator Console boundary.

This module defines an application object only.  It never opens a socket.  A
future Windows launcher must bind the qualified server to 127.0.0.1 and prove
the native browser/process/firewall boundary before operation.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable, Final, cast

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.reconciliation.operator_console_read_gateway import (
    AuthenticatedPrincipal,
    OperatorConsoleReadGateway,
    REQUIRED_PERMISSION,
)

from .authentication import (
    AuthenticationError,
    OwnerWebAuthn,
)
from .security_audit import AuditIntegrityError, AuditStorageError, SecurityAuditLog
from .sessions import SessionStore, ValidSession


SESSION_COOKIE: Final = "ATIS_OC_SESSION"
RECOVERY_COOKIE: Final = "ATIS_OC_RECOVERY"
MAX_REQUEST_BODY: Final = 65_536
MAX_RESPONSE_BODY: Final = 65_536
MAX_STATIC_BODY: Final = 5_242_880
MAX_CONNECTIONS: Final = 16
REQUEST_TIMEOUT_SECONDS: Final = 2.0
_SECURITY_HEADERS: Final = (
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
)
_STATIC_TYPES: Final = {
    ".html": b"text/html; charset=utf-8",
    ".css": b"text/css; charset=utf-8",
    ".js": b"text/javascript; charset=utf-8",
    ".json": b"application/json",
    ".svg": b"image/svg+xml",
    ".png": b"image/png",
    ".woff2": b"font/woff2",
}


class _ResponseClass:
    API = "API"
    STATIC = "STATIC"


@dataclass(frozen=True, slots=True)
class LocalBffConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8765
    public_host: str = "localhost:8765"
    origin: str = "http://localhost:8765"
    rp_id: str = "localhost"
    https: bool = False

    def __post_init__(self) -> None:
        if (
            self.bind_host != "127.0.0.1"
            or self.port != 8765
            or self.public_host != "localhost:8765"
            or self.origin != "http://localhost:8765"
            or self.rp_id != "localhost"
            or self.https is not False
        ):
            raise ValueError("unqualified local BFF configuration")


class SessionAuthenticationBoundary:
    """Adapts a durable session token to the existing gateway protocol."""

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def authenticate(
        self, credential: object, *, evaluated_at_utc: Timestamp
    ) -> AuthenticatedPrincipal | None:
        if type(credential) is not str:
            return None
        session = self.sessions.validate(credential, now=evaluated_at_utc.value)
        if session is None:
            return None
        return AuthenticatedPrincipal(
            session.principal_id,
            frozenset({REQUIRED_PERMISSION}),
            evaluated_at_utc,
            Timestamp(session.expires_at_utc),
        )


class _RateLimiter:
    def __init__(self, limit: int, window: timedelta) -> None:
        self.limit = limit
        self.window = window
        self.events: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, now: datetime) -> bool:
        threshold = now - self.window
        queue = self.events[key]
        while queue and queue[0] <= threshold:
            queue.popleft()
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        return True


class LocalOperatorConsoleBff:
    def __init__(
        self,
        *,
        config: LocalBffConfig,
        webauthn: OwnerWebAuthn,
        sessions: SessionStore,
        audit: SecurityAuditLog,
        gateway: OperatorConsoleReadGateway,
        static_root: Path,
    ) -> None:
        if not static_root.is_absolute() or not static_root.is_dir() or static_root.is_symlink():
            raise ValueError("static root must be a pre-provisioned directory")
        self.config = config
        self.webauthn = webauthn
        self.sessions = sessions
        self.audit = audit
        self.gateway = gateway
        self.static_root = static_root.resolve(strict=True)
        self._connections = asyncio.Semaphore(MAX_CONNECTIONS)
        self._auth_rate = _RateLimiter(10, timedelta(minutes=5))
        self._read_rate = _RateLimiter(120, timedelta(minutes=1))

    async def __call__(self, scope: dict[str, object], receive: Callable[[], Awaitable[dict[str, object]]], send: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope.get("type") != "http":
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        async with self._connections:
            try:
                await asyncio.wait_for(self._dispatch(scope, receive, send), REQUEST_TIMEOUT_SECONDS)
            except TimeoutError:
                await self._send(send, 503, {"error": "REQUEST_TIMEOUT"})
            except (AuditIntegrityError, AuditStorageError):
                await self._send(send, 503, {"error": "SECURITY_AUDIT_UNAVAILABLE"})
            except Exception:
                await self._send(send, 500, {"error": "INTERNAL_FAILURE"})

    async def _lifespan(self, receive: Callable[[], Awaitable[dict[str, object]]], send: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self.audit.verify()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                self.audit.verify()
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    def _headers(scope: dict[str, object]) -> dict[str, str]:
        values: dict[str, str] = {}
        raw_headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        for raw_name, raw_value in raw_headers:
            name = bytes(raw_name).decode("ascii").lower()
            if name in values:
                values[name] = values[name] + "," + bytes(raw_value).decode("latin1")
            else:
                values[name] = bytes(raw_value).decode("latin1")
        return values

    def _validate_request_boundary(self, scope: dict[str, object], headers: dict[str, str]) -> bool:
        client = scope.get("client")
        return (
            isinstance(client, tuple)
            and len(client) == 2
            and client[0] in {"127.0.0.1", "::1"}
            and headers.get("host") == self.config.public_host
        )

    async def _read_body(self, receive: Callable[[], Awaitable[dict[str, object]]]) -> bytes:
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise ValueError("invalid request stream")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise ValueError("invalid request body")
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BODY:
                raise OverflowError("request body exceeds limit")
            if not message.get("more_body", False):
                return bytes(body)

    @staticmethod
    def _cookies(headers: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in headers.get("cookie", "").split(";"):
            if "=" in item:
                name, value = item.strip().split("=", 1)
                result[name] = value
        return result

    def _session(self, headers: dict[str, str], now: datetime) -> tuple[str, ValidSession] | None:
        token = self._cookies(headers).get(SESSION_COOKIE)
        if token is None:
            return None
        session = self.sessions.validate(token, now=now)
        return None if session is None else (token, session)

    def _state_change_allowed(self, headers: dict[str, str], token: str) -> bool:
        return (
            headers.get("origin") == self.config.origin
            and headers.get("sec-fetch-site") in {"same-origin", "none"}
            and self.sessions.verify_csrf(token, headers.get("x-atis-csrf", ""))
        )

    async def _dispatch(self, scope: dict[str, object], receive: Callable[[], Awaitable[dict[str, object]]], send: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        headers = self._headers(scope)
        if not self._validate_request_boundary(scope, headers):
            await self._send(send, 400, {"error": "REQUEST_BOUNDARY_REJECTED"})
            return
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        now = datetime.now(UTC)
        if method == "POST" and headers.get("origin") != self.config.origin:
            await self._send(send, 403, {"error": "ORIGIN_REJECTED"})
            return
        if path.startswith("/api/") or path.startswith("/auth/"):
            await self._api(method, path, headers, receive, send, now)
            return
        if method != "GET":
            await self._send(send, 405, {"error": "METHOD_NOT_ALLOWED"})
            return
        await self._static(path, send)

    async def _api(self, method: str, path: str, headers: dict[str, str], receive: Callable[[], Awaitable[dict[str, object]]], send: Callable[[dict[str, object]], Awaitable[None]], now: datetime) -> None:
        known_paths = frozenset(
            {
                "/auth/assertion/options",
                "/auth/assertion/verify",
                "/auth/recovery/options",
                "/auth/recovery/verify",
                "/auth/recovery/registration/options",
                "/auth/recovery/registration/verify",
                "/auth/session",
                "/auth/logout",
                "/auth/revoke-all",
                "/api/v1/reconciliation/current",
            }
        )
        if path not in known_paths:
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        if path == "/auth/assertion/options" and method == "POST":
            if not self._auth_rate.allow("loopback", now):
                await self._send(send, 429, {"error": "RATE_LIMITED"})
                return
            ceremony = self.webauthn.begin_authentication(now=now)
            self.audit.append("AUTH_CHALLENGE", "SUCCESS", "ASSERTION_CREATED", now=now)
            await self._send(send, 200, {"ceremony_id": ceremony.ceremony_id, "options": json.loads(ceremony.public_options_json)})
            return
        if path == "/auth/assertion/verify" and method == "POST":
            if not self._auth_rate.allow("loopback", now):
                await self._send(send, 429, {"error": "RATE_LIMITED"})
                return
            body = await self._json_body(receive)
            try:
                credential = body["credential"]
                if type(credential) is not dict:
                    raise TypeError("credential must be an object")
                principal = self.webauthn.complete_authentication(
                    str(body["ceremony_id"]), cast(dict[str, object], credential), now=now
                )
            except (AuthenticationError, KeyError, TypeError):
                self.audit.append("AUTH_RESULT", "DENIED", "ASSERTION_REJECTED", now=now)
                await self._send(send, 401, {"error": "AUTHENTICATION_FAILED"})
                return
            token, session = self.sessions.create(principal.principal_id, now=now)
            self.audit.append("AUTH_RESULT", "SUCCESS", "OWNER_AUTHENTICATED", subject_ref=session.session_ref, now=now)
            cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"
            await self._send(send, 200, {"csrf_token": session.csrf_token, "recovery_ready": self.webauthn.recovery_ready()}, extra=((b"set-cookie", cookie.encode("ascii")),))
            return
        if path == "/auth/recovery/options" and method == "POST":
            if not self._auth_rate.allow("recovery", now):
                await self._send(send, 429, {"error": "RATE_LIMITED"})
                return
            body = await self._json_body(receive)
            try:
                ceremony = self.webauthn.begin_recovery(
                    str(body["lost_credential_ref"]), now=now
                )
            except (AuthenticationError, KeyError, TypeError):
                self.audit.append("RECOVERY_DECISION", "DENIED", "RECOVERY_UNAVAILABLE", now=now)
                await self._send(send, 403, {"error": "RECOVERY_UNAVAILABLE"})
                return
            self.audit.append("RECOVERY_DECISION", "SUCCESS", "RECOVERY_CHALLENGE", now=now)
            await self._send(send, 200, {"ceremony_id": ceremony.ceremony_id, "options": json.loads(ceremony.public_options_json)})
            return
        if path == "/auth/recovery/verify" and method == "POST":
            if not self._auth_rate.allow("recovery", now):
                await self._send(send, 429, {"error": "RATE_LIMITED"})
                return
            body = await self._json_body(receive)
            try:
                credential = body["credential"]
                if type(credential) is not dict:
                    raise TypeError("credential must be an object")
                grant = self.webauthn.complete_recovery(
                    str(body["ceremony_id"]),
                    cast(dict[str, object], credential),
                    now=now,
                )
            except (AuthenticationError, KeyError, TypeError):
                self.audit.append("RECOVERY_DECISION", "DENIED", "RECOVERY_ASSERTION_REJECTED", now=now)
                await self._send(send, 403, {"error": "RECOVERY_FAILED"})
                return
            self.audit.append("RECOVERY_DECISION", "SUCCESS", "RECOVERY_SCOPED", now=now)
            cookie = f"{RECOVERY_COOKIE}={grant.token}; Path=/auth/recovery/; HttpOnly; SameSite=Strict"
            await self._send(send, 200, {"csrf_token": grant.csrf_token, "scope": "PASSKEY_REPLACEMENT_ONLY"}, extra=((b"set-cookie", cookie.encode("ascii")),))
            return
        if path in {
            "/auth/recovery/registration/options",
            "/auth/recovery/registration/verify",
        } and method == "POST":
            recovery_token = self._cookies(headers).get(RECOVERY_COOKIE, "")
            recovery_csrf = headers.get("x-atis-csrf", "")
            if (
                headers.get("origin") != self.config.origin
                or headers.get("sec-fetch-site") not in {"same-origin", "none"}
                or not recovery_token
                or not recovery_csrf
            ):
                await self._send(send, 403, {"error": "RECOVERY_SCOPE_REJECTED"})
                return
            if path.endswith("/options"):
                try:
                    ceremony = self.webauthn.begin_replacement(
                        recovery_token, recovery_csrf, now=now
                    )
                except AuthenticationError:
                    await self._send(send, 403, {"error": "RECOVERY_SCOPE_REJECTED"})
                    return
                self.audit.append("CREDENTIAL_ENROLMENT", "SUCCESS", "REPLACEMENT_CHALLENGE", now=now)
                await self._send(send, 200, {"ceremony_id": ceremony.ceremony_id, "options": json.loads(ceremony.public_options_json)})
                return
            body = await self._json_body(receive)
            try:
                credential = body["credential"]
                if type(credential) is not dict:
                    raise TypeError("credential must be an object")
                record = self.webauthn.complete_replacement(
                    str(body["ceremony_id"]),
                    cast(dict[str, object], credential),
                    recovery_token,
                    recovery_csrf,
                    now=now,
                )
            except (AuthenticationError, KeyError, TypeError):
                self.audit.append("CREDENTIAL_ENROLMENT", "DENIED", "REPLACEMENT_REJECTED", now=now)
                await self._send(send, 400, {"error": "REPLACEMENT_FAILED"})
                return
            self.sessions.revoke_all(now=now)
            self.audit.append("SESSION_REVOCATION", "SUCCESS", "RECOVERY_REVOKE_ALL", now=now)
            await self._send(
                send,
                200,
                {"credential_ref": record.credential_ref, "reauthentication_required": True},
                extra=((b"set-cookie", f"{RECOVERY_COOKIE}=; Path=/auth/recovery/; HttpOnly; SameSite=Strict; Max-Age=0".encode("ascii")),),
            )
            return
        session_pair = self._session(headers, now)
        if session_pair is None:
            self.audit.append("AUTHORIZATION", "DENIED", "SESSION_REQUIRED", now=now)
            await self._send(send, 401, {"error": "AUTHENTICATION_REQUIRED"})
            return
        token, session = session_pair
        if path == "/auth/session" and method == "GET":
            await self._send(send, 200, {"authenticated": True, "recovery_ready": self.webauthn.recovery_ready()})
            return
        if path in {"/auth/logout", "/auth/revoke-all"} and method == "POST":
            if not self._state_change_allowed(headers, token):
                await self._send(send, 403, {"error": "CSRF_REJECTED"})
                return
            if path == "/auth/logout":
                self.sessions.revoke(token, now=now)
                reason = "LOGOUT"
            elif path == "/auth/revoke-all":
                self.sessions.revoke_all(now=now)
                reason = "OWNER_REVOKE_ALL"
            self.audit.append("SESSION_REVOCATION", "SUCCESS", reason, subject_ref=session.session_ref, now=now)
            await self._send(send, 200, {"revoked": True}, extra=((b"set-cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0".encode("ascii")),))
            return
        if path == "/api/v1/reconciliation/current" and method == "GET":
            if not self._read_rate.allow(session.session_ref, now):
                await self._send(send, 429, {"error": "RATE_LIMITED"})
                return
            response = self.gateway.read(token, evaluated_at_utc=Timestamp(now))
            self.audit.append("GATEWAY_READ", "SUCCESS", response.state.value, subject_ref=session.session_ref, now=now)
            await self._send_raw(send, 200, response.canonical_bytes())
            return
        await self._send(send, 404, {"error": "NOT_FOUND"})

    async def _json_body(self, receive: Callable[[], Awaitable[dict[str, object]]]) -> dict[str, object]:
        raw = await self._read_body(receive)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("request JSON is invalid") from error
        if type(value) is not dict:
            raise ValueError("request JSON must be an object")
        return value

    async def _static(self, request_path: str, send: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        relative = PurePosixPath(request_path.lstrip("/") or "index.html")
        if relative.is_absolute() or ".." in relative.parts:
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        target = (self.static_root / Path(*relative.parts)).resolve(strict=False)
        if self.static_root not in target.parents and target != self.static_root:
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        if not target.is_file() or target.is_symlink():
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        content_type = _STATIC_TYPES.get(target.suffix.lower())
        if content_type is None:
            await self._send(send, 404, {"error": "NOT_FOUND"})
            return
        raw = target.read_bytes()
        if len(raw) > MAX_STATIC_BODY:
            await self._send(send, 413, {"error": "RESPONSE_LIMIT_EXCEEDED"})
            return
        await self._send_raw(
            send,
            200,
            raw,
            content_type=content_type,
            response_class=_ResponseClass.STATIC,
        )

    async def _send(self, send: Callable[[dict[str, object]], Awaitable[None]], status: int, value: dict[str, object], *, extra: tuple[tuple[bytes, bytes], ...] = ()) -> None:
        await self._send_raw(send, status, json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"), extra=extra)

    async def _send_raw(self, send: Callable[[dict[str, object]], Awaitable[None]], status: int, body: bytes, *, content_type: bytes = b"application/json", extra: tuple[tuple[bytes, bytes], ...] = (), response_class: str = _ResponseClass.API) -> None:
        limit = (
            MAX_STATIC_BODY
            if response_class == _ResponseClass.STATIC
            else MAX_RESPONSE_BODY
        )
        if len(body) > limit:
            status, body = 500, b'{"error":"RESPONSE_LIMIT_EXCEEDED"}'
        headers = ((b"content-type", content_type), (b"content-length", str(len(body)).encode("ascii"))) + _SECURITY_HEADERS + extra
        await send({"type": "http.response.start", "status": status, "headers": list(headers)})
        await send({"type": "http.response.body", "body": body})

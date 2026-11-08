import asyncio
from datetime import UTC, datetime
import json

from automated_trading_bot.operator_console.authentication import OwnerWebAuthn
from automated_trading_bot.operator_console.local_bff import (
    MAX_RESPONSE_BODY,
    MAX_STATIC_BODY,
    LocalBffConfig,
    LocalOperatorConsoleBff,
    SessionAuthenticationBoundary,
)
from automated_trading_bot.operator_console.security_audit import SecurityAuditLog
from automated_trading_bot.operator_console.sessions import SessionStore
from automated_trading_bot.reconciliation.operator_console_read_gateway import (
    OperatorConsoleReadGateway,
)
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    SnapshotReadResult,
)


class Key:
    def load_key(self) -> bytes:
        return b"test-only-protected-key-material" * 2


class Reader:
    def __init__(self, state=AdapterReadState.UNAVAILABLE, reasons=("CURRENT_POINTER_MISSING",)):
        self.result = SnapshotReadResult(state, None, reasons)
        self.calls = 0

    def read_current(self, *, evaluated_at_utc):
        self.calls += 1
        return self.result


def _app(tmp_path, reader=None):
    audit_root = (tmp_path / "audit").resolve()
    (audit_root / "events").mkdir(parents=True)
    static = (tmp_path / "static").resolve()
    static.mkdir()
    (static / "index.html").write_text("<h1>ATIS</h1>", encoding="utf-8")
    sessions = SessionStore((tmp_path / "sessions.sqlite").resolve())
    auth = OwnerWebAuthn(
        (tmp_path / "credentials.sqlite").resolve(),
        rp_id="localhost",
        expected_origin="http://localhost:8765",
    )
    actual_reader = Reader() if reader is None else reader
    gateway = OperatorConsoleReadGateway(
        authentication=SessionAuthenticationBoundary(sessions),
        reader=actual_reader,
    )
    return (
        LocalOperatorConsoleBff(
            config=LocalBffConfig(),
            webauthn=auth,
            sessions=sessions,
            audit=SecurityAuditLog(audit_root, Key()),
            gateway=gateway,
            static_root=static,
        ),
        sessions,
        actual_reader,
    )


def _request(app, path, *, method="GET", host="localhost:8765", origin=None, client="127.0.0.1", cookie=None, raw_cookie=None, csrf=None, body=None):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers += [(b"origin", origin.encode()), (b"sec-fetch-site", b"same-origin")]
    if cookie is not None:
        headers.append((b"cookie", f"ATIS_OC_SESSION={cookie}".encode()))
    if raw_cookie is not None:
        headers.append((b"cookie", raw_cookie.encode()))
    if csrf is not None:
        headers.append((b"x-atis-csrf", csrf.encode()))
    raw = b"" if body is None else json.dumps(body).encode()
    requests = iter([{"type": "http.request", "body": raw, "more_body": False}])
    messages = []

    async def receive():
        return next(requests)

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "method": method, "path": path,
        "headers": headers, "client": (client, 44444),
    }
    asyncio.run(app(scope, receive, send))
    start = next(item for item in messages if item["type"] == "http.response.start")
    response = next(item for item in messages if item["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), response["body"]


def _raw_response(app, body: bytes, *, static: bool = False):
    messages = []

    async def send(message):
        messages.append(message)

    kwargs = {"response_class": "STATIC"} if static else {}
    asyncio.run(app._send_raw(send, 200, body, **kwargs))
    start = next(item for item in messages if item["type"] == "http.response.start")
    response = next(item for item in messages if item["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), response["body"]


def test_loopback_host_origin_and_prohibited_routes_are_default_deny(tmp_path) -> None:
    app, _, _ = _app(tmp_path)
    assert _request(app, "/", host="evil.example")[0] == 400
    assert _request(app, "/", client="192.168.1.20")[0] == 400
    assert _request(app, "/auth/logout", method="POST", origin="http://127.0.0.1:8765")[0] == 403
    for route in ("/api/v1/publish", "/api/v1/recover", "/api/v1/orders", "/debug", "/shell"):
        assert _request(app, route)[0] == 404


def test_session_bound_gateway_preserves_unavailable_and_has_security_headers(tmp_path) -> None:
    reader = Reader(AdapterReadState.UNAVAILABLE, ("CURRENT_POINTER_MISSING",))
    app, sessions, _ = _app(tmp_path, reader)
    token, session = sessions.create("atis-owner", now=datetime.now(UTC))
    status, headers, body = _request(
        app, "/api/v1/reconciliation/current", cookie=token
    )
    assert status == 200
    value = json.loads(body)
    assert value["state"] == "UNAVAILABLE"
    assert value["observation"] is None
    assert reader.calls == 1
    assert headers[b"cache-control"] == b"no-store"
    assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]
    assert session.principal_id == "atis-owner"


def test_missing_session_permission_boundary_csrf_logout_and_replay(tmp_path) -> None:
    app, sessions, reader = _app(tmp_path)
    assert _request(app, "/api/v1/reconciliation/current")[0] == 401
    assert reader.calls == 0
    token, session = sessions.create("atis-owner", now=datetime.now(UTC))
    assert _request(
        app, "/auth/logout", method="POST", origin="http://localhost:8765",
        cookie=token, csrf="wrong",
    )[0] == 403
    assert _request(
        app, "/auth/logout", method="POST", origin="http://localhost:8765",
        cookie=token, csrf=session.csrf_token,
    )[0] == 200
    assert _request(app, "/api/v1/reconciliation/current", cookie=token)[0] == 401


def test_static_serving_is_contained_and_response_size_is_bounded(tmp_path) -> None:
    app, _, _ = _app(tmp_path)
    status, headers, _ = _request(app, "/")
    assert status == 200
    assert headers[b"content-type"] == b"text/html; charset=utf-8"
    (app.static_root / "app.js").write_text("export {};", encoding="utf-8")
    assert _request(app, "/app.js")[1][b"content-type"] == b"text/javascript; charset=utf-8"
    assert _request(app, "/../credentials.sqlite")[0] == 404
    (app.static_root / "large.js").write_bytes(b"x" * 5_242_881)
    assert _request(app, "/large.js")[0] == 413


def test_api_and_static_response_limits_are_distinct_and_exact(tmp_path) -> None:
    app, _, _ = _app(tmp_path)
    status, headers, body = _raw_response(app, b"a" * MAX_RESPONSE_BODY)
    assert status == 200
    assert len(body) == MAX_RESPONSE_BODY
    assert headers[b"content-length"] == str(MAX_RESPONSE_BODY).encode()
    status, _, body = _raw_response(app, b"a" * (MAX_RESPONSE_BODY + 1))
    assert status == 500
    assert body == b'{"error":"RESPONSE_LIMIT_EXCEEDED"}'

    medium = app.static_root / "medium.js"
    medium.write_bytes(b"x" * (MAX_RESPONSE_BODY + 1))
    status, headers, body = _request(app, "/medium.js")
    assert status == 200
    assert len(body) == MAX_RESPONSE_BODY + 1
    assert headers[b"content-length"] == str(MAX_RESPONSE_BODY + 1).encode()

    maximum = app.static_root / "maximum.css"
    maximum.write_bytes(b"x" * MAX_STATIC_BODY)
    status, headers, body = _request(app, "/maximum.css")
    assert status == 200
    assert len(body) == MAX_STATIC_BODY
    assert headers[b"content-length"] == str(MAX_STATIC_BODY).encode()
    assert headers[b"x-content-type-options"] == b"nosniff"

    too_large = app.static_root / "too-large.js"
    too_large.write_bytes(b"x" * (MAX_STATIC_BODY + 1))
    assert _request(app, "/too-large.js")[0] == 413
    (app.static_root / "unsupported.exe").write_bytes(b"MZ")
    assert _request(app, "/unsupported.exe")[0] == 404
    assert _request(app, "/missing.js")[0] == 404


def test_recovery_cookie_has_no_operational_gateway_scope(tmp_path) -> None:
    app, _, reader = _app(tmp_path)
    status, _, _ = _request(
        app,
        "/api/v1/reconciliation/current",
        raw_cookie="ATIS_OC_RECOVERY=recovery-only-token",
    )
    assert status == 401
    assert reader.calls == 0


def test_successful_replacement_revokes_all_sessions_and_requires_fresh_login(tmp_path) -> None:
    app, sessions, _ = _app(tmp_path)
    session_token, _ = sessions.create("atis-owner", now=datetime.now(UTC))

    class ReplacementOnly:
        def complete_replacement(self, ceremony_id, credential, token, csrf, *, now):
            assert ceremony_id == "ceremony"
            assert token == "recovery-token"
            assert csrf == "recovery-csrf"
            return type("Record", (), {"credential_ref": "replacement"})()

    app.webauthn = ReplacementOnly()
    status, headers, body = _request(
        app,
        "/auth/recovery/registration/verify",
        method="POST",
        origin="http://localhost:8765",
        raw_cookie="ATIS_OC_RECOVERY=recovery-token",
        csrf="recovery-csrf",
        body={"ceremony_id": "ceremony", "credential": {}},
    )
    assert status == 200
    assert json.loads(body)["reauthentication_required"] is True
    assert b"Max-Age=0" in headers[b"set-cookie"]
    assert sessions.validate(session_token, now=datetime.now(UTC)) is None


def test_configuration_rejects_non_loopback_and_unqualified_origins() -> None:
    for values in (
        {"bind_host": "0.0.0.0"},
        {"origin": "http://127.0.0.1:8765"},
        {"rp_id": "127.0.0.1"},
        {"port": 8000},
    ):
        try:
            LocalBffConfig(**values)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe configuration accepted")


def test_rate_limit_is_enforced_without_listener(tmp_path) -> None:
    app, _, _ = _app(tmp_path)
    now = datetime.now(UTC)
    assert all(app._auth_rate.allow("test", now) for _ in range(10))
    assert not app._auth_rate.allow("test", now)

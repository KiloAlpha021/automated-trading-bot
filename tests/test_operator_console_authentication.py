from base64 import urlsafe_b64encode
import json
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from automated_trading_bot.operator_console.authentication import (
    AuthenticationError,
    OwnerWebAuthn,
)


NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
ORIGIN = "http://localhost:8765"
RP_ID = "localhost"


def _b64(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Authority:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def authorize_enrollment(self, owner_id: str, existing_credentials: int) -> bool:
        return self.allowed and owner_id == "atis-owner" and existing_credentials in {0, 1}

    def authorize_revocation(self, owner_id: str, credential_ref: str) -> bool:
        return self.allowed and owner_id == "atis-owner" and bool(credential_ref)


def _registration(challenge: bytes, *, origin: str = ORIGIN, uv: bool = True):
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    credential_id = sha256(private.private_numbers().private_value.to_bytes(32, "big")).digest()
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
    flags = 0x41 | (0x04 if uv else 0)
    auth_data = sha256(RP_ID.encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big") + bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose
    client_data = json.dumps(
        {"type": "webauthn.create", "challenge": _b64(challenge), "origin": origin, "crossOrigin": False},
        separators=(",", ":"),
    ).encode()
    response = {
        "id": _b64(credential_id),
        "rawId": _b64(credential_id),
        "type": "public-key",
        "response": {
            "attestationObject": _b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})),
            "clientDataJSON": _b64(client_data),
            "transports": ["internal"],
        },
        "clientExtensionResults": {},
        "authenticatorAttachment": "platform",
    }
    return private, credential_id, response


def _assertion(private, credential_id: bytes, challenge: bytes, *, origin: str = ORIGIN, rp_id: str = RP_ID, uv: bool = True, sign_count: int = 1):
    flags = 0x01 | (0x04 if uv else 0)
    auth_data = sha256(rp_id.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")
    client_data = json.dumps(
        {"type": "webauthn.get", "challenge": _b64(challenge), "origin": origin, "crossOrigin": False},
        separators=(",", ":"),
    ).encode()
    signature = private.sign(auth_data + sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    return {
        "id": _b64(credential_id),
        "rawId": _b64(credential_id),
        "type": "public-key",
        "response": {
            "authenticatorData": _b64(auth_data),
            "clientDataJSON": _b64(client_data),
            "signature": _b64(signature),
            "userHandle": _b64(b"atis-owner"),
        },
        "clientExtensionResults": {},
        "authenticatorAttachment": "platform",
    }


def _challenge_from_options(ceremony) -> bytes:
    return __import__("base64").urlsafe_b64decode(json.loads(ceremony.public_options_json)["challenge"] + "==")


def _auth(tmp_path):
    return OwnerWebAuthn((tmp_path / "credentials.sqlite").resolve(), rp_id=RP_ID, expected_origin=ORIGIN)


def _enrol_two(auth):
    keys = []
    for _ in range(2):
        ceremony = auth.begin_registration(Authority(), now=NOW)
        private, credential_id, response = _registration(_challenge_from_options(ceremony))
        record = auth.complete_registration(ceremony.ceremony_id, response, now=NOW)
        keys.append((private, credential_id, record))
    return keys


def test_valid_two_passkey_registration_and_assertion(tmp_path) -> None:
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    assert auth.recovery_ready()
    ceremony = auth.begin_authentication(now=NOW)
    response = _assertion(keys[0][0], keys[0][1], _challenge_from_options(ceremony))
    principal = auth.complete_authentication(ceremony.ceremony_id, response, now=NOW)
    assert principal.principal_id == "atis-owner"
    assert principal.permissions == frozenset({"reconciliation.observation.read"})


def test_wrong_origin_invalid_signature_missing_uv_and_replay_are_rejected(tmp_path) -> None:
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    for response_factory in (
        lambda c: _assertion(keys[0][0], keys[0][1], c, origin="http://127.0.0.1:8765"),
        lambda c: _assertion(keys[0][0], keys[0][1], c, rp_id="127.0.0.1"),
        lambda c: _assertion(ec.generate_private_key(ec.SECP256R1()), keys[0][1], c),
        lambda c: _assertion(keys[0][0], keys[0][1], c, uv=False),
    ):
        ceremony = auth.begin_authentication(now=NOW)
        with pytest.raises(AuthenticationError):
            auth.complete_authentication(ceremony.ceremony_id, response_factory(_challenge_from_options(ceremony)), now=NOW)
    ceremony = auth.begin_authentication(now=NOW)
    response = _assertion(keys[0][0], keys[0][1], _challenge_from_options(ceremony))
    auth.complete_authentication(ceremony.ceremony_id, response, now=NOW)
    with pytest.raises(AuthenticationError):
        auth.complete_authentication(ceremony.ceremony_id, response, now=NOW)


def test_expired_challenge_wrong_rp_and_revoked_credential_fail_closed(tmp_path) -> None:
    with pytest.raises(ValueError):
        OwnerWebAuthn((tmp_path / "bad.sqlite").resolve(), rp_id="127.0.0.1", expected_origin=ORIGIN)
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    ceremony = auth.begin_authentication(now=NOW)
    with pytest.raises(AuthenticationError):
        auth.complete_authentication(
            ceremony.ceremony_id,
            _assertion(keys[0][0], keys[0][1], _challenge_from_options(ceremony)),
            now=NOW + timedelta(minutes=4),
        )
    auth.revoke_credential(keys[0][2].credential_ref, Authority(), now=NOW)
    assert not auth.recovery_ready()
    with pytest.raises(AuthenticationError, match="two owner passkeys"):
        auth.begin_authentication(now=NOW)
    recovery = auth.begin_recovery(keys[0][2].credential_ref, now=NOW)
    grant = auth.complete_recovery(
        recovery.ceremony_id,
        _assertion(keys[1][0], keys[1][1], _challenge_from_options(recovery)),
        now=NOW,
    )
    replacement = auth.begin_replacement(
        grant.token, grant.csrf_token, now=NOW
    )
    new_private, new_id, response = _registration(_challenge_from_options(replacement))
    auth.complete_replacement(
        replacement.ceremony_id,
        response,
        grant.token,
        grant.csrf_token,
        now=NOW,
    )
    assert auth.recovery_ready()
    login = auth.begin_authentication(now=NOW)
    principal = auth.complete_authentication(
        login.ceremony_id,
        _assertion(new_private, new_id, _challenge_from_options(login)),
        now=NOW,
    )
    assert principal.principal_id == "atis-owner"


def test_physical_loss_uses_scoped_recovery_and_requires_fresh_login(tmp_path) -> None:
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    recovery = auth.begin_recovery(keys[0][2].credential_ref, now=NOW)
    allowed = json.loads(recovery.public_options_json)["allowCredentials"]
    assert [item["id"] for item in allowed] == [_b64(keys[1][1])]
    grant = auth.complete_recovery(
        recovery.ceremony_id,
        _assertion(keys[1][0], keys[1][1], _challenge_from_options(recovery)),
        now=NOW,
    )
    with pytest.raises(AuthenticationError, match="two owner passkeys"):
        auth.begin_authentication(now=NOW)
    replacement = auth.begin_replacement(grant.token, grant.csrf_token, now=NOW)
    new_private, new_id, response = _registration(_challenge_from_options(replacement))
    auth.complete_replacement(
        replacement.ceremony_id, response, grant.token, grant.csrf_token, now=NOW
    )
    assert auth.recovery_ready()
    with pytest.raises(AuthenticationError):
        auth.begin_replacement(grant.token, grant.csrf_token, now=NOW)
    login = auth.begin_authentication(now=NOW)
    assert auth.complete_authentication(
        login.ceremony_id,
        _assertion(new_private, new_id, _challenge_from_options(login)),
        now=NOW,
    ).principal_id == "atis-owner"


def test_recovery_rejects_forgery_replay_and_complete_loss(tmp_path) -> None:
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    with pytest.raises(AuthenticationError):
        auth.begin_recovery("0" * 32, now=NOW)
    recovery = auth.begin_recovery(keys[0][2].credential_ref, now=NOW)
    response = _assertion(keys[1][0], keys[1][1], _challenge_from_options(recovery))
    auth.complete_recovery(recovery.ceremony_id, response, now=NOW)
    with pytest.raises(AuthenticationError):
        auth.complete_recovery(recovery.ceremony_id, response, now=NOW)
    auth.revoke_credential(keys[1][2].credential_ref, Authority(), now=NOW)
    with pytest.raises(AuthenticationError):
        auth.begin_recovery(keys[0][2].credential_ref, now=NOW)


def test_concurrent_replacement_allows_exactly_one_commit(tmp_path) -> None:
    auth = _auth(tmp_path)
    keys = _enrol_two(auth)
    recovery = auth.begin_recovery(keys[0][2].credential_ref, now=NOW)
    grant = auth.complete_recovery(
        recovery.ceremony_id,
        _assertion(keys[1][0], keys[1][1], _challenge_from_options(recovery)),
        now=NOW,
    )
    ceremonies = [
        auth.begin_replacement(grant.token, grant.csrf_token, now=NOW)
        for _ in range(2)
    ]
    attempts = []
    for ceremony in ceremonies:
        _, _, response = _registration(_challenge_from_options(ceremony))
        attempts.append((ceremony, response))

    def complete(item):
        ceremony, response = item
        try:
            auth.complete_replacement(
                ceremony.ceremony_id,
                response,
                grant.token,
                grant.csrf_token,
                now=NOW,
            )
            return "COMMITTED"
        except AuthenticationError:
            return "REJECTED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, attempts))
    assert sorted(results) == ["COMMITTED", "REJECTED"]
    assert auth.recovery_ready()


def test_initial_enrolment_requires_trusted_authority_and_policy_has_no_fallback(tmp_path) -> None:
    auth = _auth(tmp_path)
    with pytest.raises(AuthenticationError):
        auth.begin_registration(Authority(False), now=NOW)
    policy = json.loads(auth.export_public_policy())
    assert policy["emergency_password"] is False
    assert policy["minimum_owner_credentials_for_login"] == 2
    assert policy["complete_credential_loss"] == "OFFLINE_OWNER_AUTHORITY_REQUIRED"

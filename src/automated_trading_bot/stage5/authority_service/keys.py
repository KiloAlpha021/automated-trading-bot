"""Anchored signing-key lifecycle transitions."""

from enum import StrEnum

from automated_trading_bot.stage5.authority_service.persistence import (
    AuthorityDatabase,
    AuthorityState,
    ExternalWitness,
)
from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
    canonical_bytes,
    digest,
)


class SigningKeyStatus(StrEnum):
    SIGN_CURRENT = "SIGN_CURRENT"
    VERIFY_ONLY = "VERIFY_ONLY"
    RETIRED = "RETIRED"


def key_record(key_id: str, version: int, status: SigningKeyStatus) -> str:
    if not key_id or version < 1:
        raise AuthorityServiceError("invalid signing-key record")
    return f"{key_id}:{version}:{status.value}"


def _parts(record: str) -> tuple[str, int, SigningKeyStatus]:
    try:
        key_id, version, status = record.rsplit(":", 2)
        return key_id, int(version), SigningKeyStatus(status)
    except (ValueError, TypeError) as error:
        raise AuthorityServiceError("invalid retained signing-key record") from error


def _successor(state: AuthorityState, *, records: tuple[str, ...],
               current_id: str, current_version: int) -> AuthorityState:
    body = state.body()
    body.update(
        sequence=state.sequence + 1,
        current_key_id=current_id,
        current_key_version=current_version,
        key_records=records,
    )
    body["authority_head"] = digest(body)
    return AuthorityState.create(**body)


def provision_verify_only(state: AuthorityState, *, key_id: str,
                          version: int) -> AuthorityState:
    if any(_parts(item)[:2] == (key_id, version) for item in state.key_records):
        raise AuthorityServiceError("signing key already retained")
    return _successor(
        state,
        records=state.key_records + (key_record(key_id, version, SigningKeyStatus.VERIFY_ONLY),),
        current_id=state.current_key_id,
        current_version=state.current_key_version,
    )


def activate_key(state: AuthorityState, *, key_id: str,
                 version: int) -> AuthorityState:
    found = False
    records: list[str] = []
    for item in state.key_records:
        old_id, old_version, old_status = _parts(item)
        if (old_id, old_version) == (key_id, version):
            if old_status is not SigningKeyStatus.VERIFY_ONLY:
                raise AuthorityServiceError("only VERIFY_ONLY key may activate")
            found = True
            old_status = SigningKeyStatus.SIGN_CURRENT
        elif old_status is SigningKeyStatus.SIGN_CURRENT:
            old_status = SigningKeyStatus.VERIFY_ONLY
        records.append(key_record(old_id, old_version, old_status))
    if not found:
        raise AuthorityServiceError("activation key is not retained")
    if sum(_parts(item)[2] is SigningKeyStatus.SIGN_CURRENT for item in records) != 1:
        raise AuthorityServiceError("exactly one SIGN_CURRENT key required")
    return _successor(
        state, records=tuple(records), current_id=key_id, current_version=version
    )


def retire_key(state: AuthorityState, *, key_id: str, version: int) -> AuthorityState:
    if (key_id, version) == (state.current_key_id, state.current_key_version):
        raise AuthorityServiceError("current signing key cannot retire")
    changed = False
    records: list[str] = []
    for item in state.key_records:
        old_id, old_version, old_status = _parts(item)
        if (old_id, old_version) == (key_id, version):
            if old_status is not SigningKeyStatus.VERIFY_ONLY:
                raise AuthorityServiceError("only VERIFY_ONLY key may retire")
            old_status = SigningKeyStatus.RETIRED
            changed = True
        records.append(key_record(old_id, old_version, old_status))
    if not changed:
        raise AuthorityServiceError("retirement key is not retained")
    return _successor(
        state, records=tuple(records), current_id=state.current_key_id,
        current_version=state.current_key_version,
    )


def require_signing_key(state: AuthorityState, *, key_id: str,
                        version: int) -> None:
    expected = key_record(key_id, version, SigningKeyStatus.SIGN_CURRENT)
    if expected not in state.key_records or (
        key_id, version
    ) != (state.current_key_id, state.current_key_version):
        raise AuthorityServiceError("key is not retained SIGN_CURRENT authority")


class AnchoredKeyLifecycle:
    """Publish every key transition through the shared external A/P/D protocol."""

    def __init__(self, database: AuthorityDatabase, witness: ExternalWitness) -> None:
        self._database = database
        self._witness = witness

    def _publish(self, *, command_id: str, payload: dict[str, object],
                 successor: AuthorityState) -> AuthorityState:
        result = canonical_bytes({
            "command_id": command_id,
            "state_digest": successor.state_digest,
        })
        self._database.publish(
            witness=self._witness, command_id=command_id,
            payload_digest=digest(payload), successor=successor, result=result,
        )
        return self._database.read()

    def provision(self, *, command_id: str, key_id: str,
                  version: int) -> AuthorityState:
        current = self._database.read()
        return self._publish(
            command_id=command_id,
            payload={"action": "PROVISION", "key_id": key_id, "version": version},
            successor=provision_verify_only(current, key_id=key_id, version=version),
        )

    def activate(self, *, command_id: str, key_id: str,
                 version: int) -> AuthorityState:
        current = self._database.read()
        return self._publish(
            command_id=command_id,
            payload={"action": "ACTIVATE", "key_id": key_id, "version": version},
            successor=activate_key(current, key_id=key_id, version=version),
        )

    def retire(self, *, command_id: str, key_id: str,
               version: int) -> AuthorityState:
        current = self._database.read()
        return self._publish(
            command_id=command_id,
            payload={"action": "RETIRE", "key_id": key_id, "version": version},
            successor=retire_key(current, key_id=key_id, version=version),
        )

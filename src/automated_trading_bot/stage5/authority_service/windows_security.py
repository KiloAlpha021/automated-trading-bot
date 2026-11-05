"""Windows deployment bindings for the Authority Service.

All operational values come from a fixed HKLM record.  Request payloads never
select registry roots, pipe names, principals, stores, clocks, or keys.
"""

from __future__ import annotations

from dataclasses import dataclass

import pywintypes  # type: ignore[import-untyped]
import win32api  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32event  # type: ignore[import-untyped]
import win32pipe  # type: ignore[import-untyped]
import win32security  # type: ignore[import-untyped]

from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
)


_CONFIG_KEY = r"SOFTWARE\ATIS\Stage5\AuthorityService"


@dataclass(frozen=True, slots=True)
class WindowsDeploymentConfiguration:
    service_identity: str
    deployment_identity: str
    authority_domain_identity: str
    store_path: str
    runtime_pipe: str
    management_pipe: str
    runtime_sid: str
    management_sid: str
    service_sid: str
    signing_key_name: str
    signing_key_id: str
    signing_key_version: int
    signing_public_blob: bytes
    snapshot_source_factory: str = ""

    @classmethod
    def load_protected(cls) -> WindowsDeploymentConfiguration:
        try:
            handle = win32api.RegOpenKeyEx(
                win32con.HKEY_LOCAL_MACHINE, _CONFIG_KEY, 0, win32con.KEY_READ
            )
        except pywintypes.error as error:
            raise AuthorityServiceError(
                "protected Authority Service configuration is unavailable"
            ) from error
        text_names = (
            "ServiceIdentity",
            "DeploymentIdentity",
            "AuthorityDomainIdentity",
            "StorePath",
            "RuntimePipe",
            "ManagementPipe",
            "RuntimeSid",
            "ManagementSid",
            "ServiceSid",
            "SigningKeyName",
            "SigningKeyId",
            "SnapshotSourceFactory",
        )
        values: list[str] = []
        try:
            for name in text_names:
                value, kind = win32api.RegQueryValueEx(handle, name)
                if kind != win32con.REG_SZ or type(value) is not str or not value:
                    raise AuthorityServiceError(
                        f"protected configuration value {name} is invalid"
                    )
                values.append(value)
            version, version_kind = win32api.RegQueryValueEx(
                handle, "SigningKeyVersion"
            )
            public_blob, blob_kind = win32api.RegQueryValueEx(
                handle, "SigningPublicBlob"
            )
            if (
                version_kind != win32con.REG_DWORD
                or type(version) is not int
                or version < 1
            ):
                raise AuthorityServiceError("protected signing-key version is invalid")
            if (
                blob_kind != win32con.REG_BINARY
                or type(public_blob) is not bytes
                or not public_blob
            ):
                raise AuthorityServiceError("protected signing public key is invalid")
        except pywintypes.error as error:
            raise AuthorityServiceError(
                "protected Authority Service configuration is incomplete"
            ) from error
        finally:
            win32api.RegCloseKey(handle)
        value = cls(
            service_identity=values[0],
            deployment_identity=values[1],
            authority_domain_identity=values[2],
            store_path=values[3],
            runtime_pipe=values[4],
            management_pipe=values[5],
            runtime_sid=values[6],
            management_sid=values[7],
            service_sid=values[8],
            signing_key_name=values[9],
            signing_key_id=values[10],
            signing_key_version=version,
            signing_public_blob=public_blob,
            snapshot_source_factory=values[11],
        )
        if value.runtime_pipe == value.management_pipe:
            raise AuthorityServiceError("Runtime and Management endpoints overlap")
        if not value.runtime_pipe.startswith("\\\\.\\pipe\\"):
            raise AuthorityServiceError("Runtime endpoint is not a local named pipe")
        if not value.management_pipe.startswith("\\\\.\\pipe\\"):
            raise AuthorityServiceError("Management endpoint is not a local named pipe")
        return value


class WindowsRegistryWitness:
    """Protected A/P/D witness retained beside the deployment configuration."""

    def _read(self, name: str) -> str | None:
        handle = win32api.RegOpenKeyEx(
            win32con.HKEY_LOCAL_MACHINE, _CONFIG_KEY, 0, win32con.KEY_READ
        )
        try:
            try:
                value, kind = win32api.RegQueryValueEx(handle, name)
            except pywintypes.error as error:
                if getattr(error, "winerror", None) == 2:
                    return None
                raise
            if kind != win32con.REG_SZ or type(value) is not str or not value:
                raise AuthorityServiceError(f"protected witness {name} is invalid")
            return value
        finally:
            win32api.RegCloseKey(handle)

    def _write(self, name: str, value: str) -> None:
        handle = win32api.RegOpenKeyEx(
            win32con.HKEY_LOCAL_MACHINE,
            _CONFIG_KEY,
            0,
            win32con.KEY_READ | win32con.KEY_SET_VALUE,
        )
        try:
            win32api.RegSetValueEx(handle, name, 0, win32con.REG_SZ, value)
            win32api.RegFlushKey(handle)
        finally:
            win32api.RegCloseKey(handle)

    def read_anchor(self) -> str:
        value = self._read("AuthorityAnchor")
        if value is None:
            raise AuthorityServiceError("protected authority anchor is absent")
        return value

    def read_pending(self) -> str | None:
        return self._read("AuthorityPending")

    def write_pending(self, value: str) -> None:
        if self.read_pending() is not None:
            raise AuthorityServiceError("protected pending witness already exists")
        self._write("AuthorityPending", value)

    def write_anchor(self, value: str) -> None:
        self._write("AuthorityAnchor", value)

    def clear_pending(self, expected: str) -> None:
        if self.read_pending() != expected:
            raise AuthorityServiceError("protected pending witness changed")
        handle = win32api.RegOpenKeyEx(
            win32con.HKEY_LOCAL_MACHINE,
            _CONFIG_KEY,
            0,
            win32con.KEY_READ | win32con.KEY_SET_VALUE,
        )
        try:
            win32api.RegDeleteValue(handle, "AuthorityPending")
            win32api.RegFlushKey(handle)
        finally:
            win32api.RegCloseKey(handle)


class WindowsDomainMutex:
    """Single active service owner for one protected authority domain."""

    def __init__(self, *, domain_identity: str, service_sid: str) -> None:
        safe = "".join(c if c.isalnum() else "_" for c in domain_identity)
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"O:{service_sid}D:P(A;;GA;;;{service_sid})",
                win32security.SDDL_REVISION_1,
            )
        )
        self._handle = win32event.CreateMutex(
            attributes, False, rf"Global\ATIS_STAGE5_AUTHORITY_{safe}"
        )
        if win32api.GetLastError() == win32con.ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(self._handle)
            self._handle = None
            raise AuthorityServiceError("Authority Service already owns this domain")

    def close(self) -> None:
        if self._handle is not None:
            win32api.CloseHandle(self._handle)
            self._handle = None


class ImpersonatedPipeClient:
    """Authenticate a connected peer SID and always revert impersonation."""

    def __init__(self, pipe_handle: object, allowed_sid: str) -> None:
        self._pipe_handle = pipe_handle
        self._allowed_sid = win32security.ConvertStringSidToSid(allowed_sid)
        self._entered = False

    def __enter__(self) -> None:
        try:
            win32security.ImpersonateNamedPipeClient(self._pipe_handle)
            self._entered = True
            token = win32security.OpenThreadToken(
                win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True
            )
            try:
                token_user = win32security.GetTokenInformation(
                    token, win32security.TokenUser
                )[0]
            finally:
                token.Close()
            if win32security.ConvertSidToStringSid(
                token_user
            ) != win32security.ConvertSidToStringSid(self._allowed_sid):
                raise AuthorityServiceError("named-pipe principal is unauthorised")
        except Exception:
            if self._entered:
                win32security.RevertToSelf()
                self._entered = False
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if self._entered:
            win32security.RevertToSelf()
            self._entered = False


def pipe_reject_remote_clients_flag() -> int:
    """Return the OS flag required for every operational pipe creation."""
    value = getattr(win32pipe, "PIPE_REJECT_REMOTE_CLIENTS", None)
    if type(value) is not int:
        raise AuthorityServiceError("remote-client rejection is unavailable")
    return value


@dataclass(frozen=True, slots=True)
class WindowsIdentityBinding:
    dimension: str
    key_name: str
    key_id: str
    key_version: int
    policy_version: int
    public_blob: bytes
    valid_from: str
    valid_until: str
    management_pipe: str
    read_pipe: str
    issuer_sid: str
    reader_sid: str


def load_identity_bindings(
    configuration: WindowsDeploymentConfiguration,
) -> tuple[WindowsIdentityBinding, ...]:
    """Optional protected config schema; no default keys, peers or endpoints.

    Missing values leave identity issuance unavailable while preserving I01.
    No provisioning, key creation or registry writes occur here.
    """
    import base64
    from automated_trading_bot.stage5.identity_scope import (
        parse_canonical,
        exact_fields,
        positive,
        parse_timestamp,
    )

    try:
        handle = win32api.RegOpenKeyEx(
            win32con.HKEY_LOCAL_MACHINE, _CONFIG_KEY, 0, win32con.KEY_READ
        )
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) == 2:
            return ()
        raise
    try:
        try:
            raw, kind = win32api.RegQueryValueEx(handle, "IdentityCapabilitiesV1")
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) == 2:
                return ()
            raise
    finally:
        win32api.RegCloseKey(handle)
    if kind != win32con.REG_BINARY:
        raise AuthorityServiceError(
            "identity configuration must be canonical protected bytes"
        )
    value = parse_canonical(raw)
    exact_fields(value, ("ACCOUNT", "PORTFOLIO"))
    result = []
    names = set(WindowsIdentityBinding.__dataclass_fields__) - {
        "dimension",
        "public_blob",
    } | {"public_blob_b64"}
    for dimension in ("ACCOUNT", "PORTFOLIO"):
        fields = value[dimension]
        exact_fields(fields, names)
        public = base64.b64decode(fields["public_blob_b64"], validate=True)
        if base64.b64encode(public).decode() != fields["public_blob_b64"]:
            raise AuthorityServiceError("noncanonical public key")
        for n in ("key_version", "policy_version"):
            positive(fields[n], n)
        if parse_timestamp(fields["valid_until"]) <= parse_timestamp(
            fields["valid_from"]
        ):
            raise AuthorityServiceError("invalid identity policy interval")
        for n in names - {"public_blob_b64", "key_version", "policy_version"}:
            if type(fields[n]) is not str or not fields[n]:
                raise AuthorityServiceError("missing identity binding")
        if fields["issuer_sid"] == configuration.runtime_sid:
            raise AuthorityServiceError("runtime cannot be identity issuer")
        for n in ("management_pipe", "read_pipe"):
            if not fields[n].startswith("\\\\.\\pipe\\"):
                raise AuthorityServiceError("identity endpoint must be local")
        result.append(
            WindowsIdentityBinding(
                dimension=dimension,
                public_blob=public,
                **{n: v for n, v in fields.items() if n != "public_blob_b64"},
            )
        )
    pipes = [configuration.runtime_pipe, configuration.management_pipe] + [
        p for b in result for p in (b.management_pipe, b.read_pipe)
    ]
    if (
        len(set(pipes)) != len(pipes)
        or len({configuration.signing_key_name, *[b.key_name for b in result]}) != 3
        or len({configuration.signing_key_id, *[b.key_id for b in result]}) != 3
        or len({configuration.signing_public_blob, *[b.public_blob for b in result]})
        != 3
    ):
        raise AuthorityServiceError("identity/admission capability bindings overlap")
    return tuple(result)

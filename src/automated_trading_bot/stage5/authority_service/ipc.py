"""Bounded authenticated Windows named-pipe host."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
import json
import struct
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

import pywintypes  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32file  # type: ignore[import-untyped]
import win32pipe  # type: ignore[import-untyped]
import win32security  # type: ignore[import-untyped]

from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
    AdmissionEnvelope,
    PROTOCOL,
    SignedAdmission,
    canonical_bytes,
)
from automated_trading_bot.stage5.authority_service.service import (
    AdmissionVerifier,
    AuthorityService,
    RuntimeAdmissionRequest,
)
from automated_trading_bot.stage5.contracts import CapitalSnapshot

if TYPE_CHECKING:
    from automated_trading_bot.stage5.authority_service.windows_security import (
        WindowsDeploymentConfiguration,
    )
from automated_trading_bot.stage5.authority_service.windows_security import (
    ImpersonatedPipeClient,
    pipe_reject_remote_clients_flag,
)


MAX_FRAME = 1024 * 1024
FENCE_ACQUIRE_TIMEOUT_SECONDS = 5.0


def encode_frame(value: object) -> bytes:
    body = canonical_bytes(value)
    if len(body) > MAX_FRAME:
        raise AuthorityServiceError("IPC response exceeds maximum frame")
    return struct.pack("!I", len(body)) + body


def decode_frame(value: bytes) -> dict[str, object]:
    if len(value) < 4:
        raise AuthorityServiceError("malformed IPC frame")
    size = struct.unpack("!I", value[:4])[0]
    if size > MAX_FRAME or size != len(value) - 4:
        raise AuthorityServiceError("malformed or oversized IPC frame")
    try:
        body = json.loads(value[4:])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityServiceError("malformed IPC JSON") from error
    if type(body) is not dict:
        raise AuthorityServiceError("IPC request must be an object")
    return body


def _security_attributes(service_sid: str, client_sid: str) -> pywintypes.SECURITY_ATTRIBUTES:
    sddl = f"O:{service_sid}D:P(A;;GA;;;{service_sid})(A;;GRGW;;;{client_sid})"
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes


@dataclass(slots=True)
class FencedRuntimeSession:
    """One non-transferable fence bound to its authenticated pipe connection."""

    context: AbstractContextManager[AdmissionEnvelope]
    fence_nonce: str

    def serve(self, handle: object) -> None:
        guard = Lock()
        entered = False
        released = False

        def release() -> None:
            nonlocal released
            with guard:
                if released or not entered:
                    return
                released = True
                self.context.__exit__(None, None, None)

        envelope = self.context.__enter__()
        entered = True
        try:
            win32file.WriteFile(handle, encode_frame({
                "protocol": PROTOCOL,
                "operation": "FENCED_CONSUMPTION_ACQUIRED",
                "fence_nonce": self.fence_nonce,
                "admission_id": envelope.admission_id,
                "authority_head": envelope.authority_head,
            }))
            win32file.FlushFileBuffers(handle)
            _, raw = win32file.ReadFile(handle, MAX_FRAME + 4)
            request = decode_frame(bytes(raw))
            if released:
                raise AuthorityServiceError("operational authority fence expired")
            if (
                request.get("protocol") != PROTOCOL
                or request.get("operation") != "RELEASE_FENCED_CONSUMPTION"
                or request.get("fence_nonce") != self.fence_nonce
                or request.get("admission_id") != envelope.admission_id
            ):
                raise AuthorityServiceError("operational authority fence release rejected")
            release()
            win32file.WriteFile(handle, encode_frame({
                "protocol": PROTOCOL,
                "operation": "FENCED_CONSUMPTION_RELEASED",
                "fence_nonce": self.fence_nonce,
            }))
            win32file.FlushFileBuffers(handle)
        except pywintypes.error as error:
            raise AuthorityServiceError(
                "fenced consumer disconnected before release"
            ) from error
        finally:
            release()


class NamedPipeEndpoint:
    """One endpoint with a fixed peer SID and no payload-derived privilege."""

    def __init__(self, *, pipe_name: str, service_sid: str, client_sid: str,
                 handler: Callable[
                     [dict[str, object]],
                     dict[str, object] | FencedRuntimeSession,
                 ]) -> None:
        self._pipe_name = pipe_name
        self._service_sid = service_sid
        self._client_sid = client_sid
        self._handler = handler

    def serve_once(self) -> None:
        flags = (
            win32pipe.PIPE_ACCESS_DUPLEX
            | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE
        )
        mode = (
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT
            | pipe_reject_remote_clients_flag()
        )
        try:
            handle = win32pipe.CreateNamedPipe(
                self._pipe_name, flags, mode, 1, MAX_FRAME + 4,
                MAX_FRAME + 4, 5000,
                _security_attributes(self._service_sid, self._client_sid),
            )
        except pywintypes.error as error:
            raise AuthorityServiceError(
                "named-pipe object could not be exclusively established"
            ) from error
        try:
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                _, request = win32file.ReadFile(handle, MAX_FRAME + 4)
                with ImpersonatedPipeClient(handle, self._client_sid):
                    response = self._handler(decode_frame(bytes(request)))
                if type(response) is FencedRuntimeSession:
                    response.serve(handle)
                else:
                    win32file.WriteFile(handle, encode_frame(response))
                    win32file.FlushFileBuffers(handle)
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(handle)
                except pywintypes.error:
                    pass
        finally:
            win32file.CloseHandle(handle)


class WindowsRuntimePipeTransport:
    """Runtime-only client fixed to the protected deployment pipe and key."""

    _configuration: WindowsDeploymentConfiguration

    def __init__(self, *_: object, **__: object) -> None:
        raise AuthorityServiceError(
            "operational Runtime transport requires protected composition"
        )

    @classmethod
    def from_protected_windows_deployment(
        cls,
    ) -> tuple[WindowsRuntimePipeTransport, AdmissionVerifier]:
        from automated_trading_bot.stage5.authority_service.cng import (
            CngAdmissionVerifier,
        )
        from automated_trading_bot.stage5.authority_service.windows_security import (
            WindowsDeploymentConfiguration,
        )

        configuration = WindowsDeploymentConfiguration.load_protected()
        value = object.__new__(cls)
        value._configuration = configuration
        verifier = AdmissionVerifier(
            verifier=CngAdmissionVerifier(configuration.signing_public_blob),
            service_identity=configuration.service_identity,
            deployment_identity=configuration.deployment_identity,
            authority_domain_identity=configuration.authority_domain_identity,
            signing_key_id=configuration.signing_key_id,
            signing_key_version=configuration.signing_key_version,
        )
        return value, verifier

    def _request(self, request: dict[str, object]) -> dict[str, object]:
        configuration = self._configuration
        try:
            handle = win32file.CreateFile(
                configuration.runtime_pipe,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0, None, win32con.OPEN_EXISTING, 0, None,
            )
        except pywintypes.error as error:
            raise AuthorityServiceError("Authority Service Runtime endpoint unavailable") from error
        try:
            win32pipe.SetNamedPipeHandleState(
                handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
            )
            win32file.WriteFile(handle, encode_frame(request))
            _, response = win32file.ReadFile(handle, MAX_FRAME + 4)
            return decode_frame(bytes(response))
        except pywintypes.error as error:
            raise AuthorityServiceError("Authority Service Runtime request failed") from error
        finally:
            win32file.CloseHandle(handle)

    def request_admission(
        self, snapshot: CapitalSnapshot, operation_purpose: str
    ) -> SignedAdmission:
        if type(snapshot) is not CapitalSnapshot:
            raise AuthorityServiceError("canonical CapitalSnapshot required")
        request_nonce = str(uuid4())
        response = self._request({
            "protocol": "ATIS_STAGE5_AUTHORITY_SERVICE_V1",
            "operation": "ADMIT_CAPITAL_SNAPSHOT",
            "portfolio_id": snapshot.portfolio_id,
            "run_scope": snapshot.run_scope,
            "operation_purpose": operation_purpose,
            "expected_domain_identity": self._configuration.authority_domain_identity,
            "request_nonce": request_nonce,
        })
        signed = SignedAdmission.from_wire(response.get("signed_admission"))
        if signed.envelope.request_nonce != request_nonce:
            raise AuthorityServiceError("Runtime admission replay detected")
        return signed

    @contextmanager
    def fenced_consumption(
        self, admission: SignedAdmission, operation_purpose: str
    ) -> Iterator[None]:
        configuration = self._configuration
        try:
            handle = win32file.CreateFile(
                configuration.runtime_pipe,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0, None, win32con.OPEN_EXISTING, 0, None,
            )
        except pywintypes.error as error:
            raise AuthorityServiceError(
                "Authority Service Runtime endpoint unavailable"
            ) from error
        fence_nonce = str(uuid4())
        try:
            win32pipe.SetNamedPipeHandleState(
                handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
            )
            win32file.WriteFile(handle, encode_frame({
                "protocol": PROTOCOL,
                "operation": "BEGIN_FENCED_CONSUMPTION",
                "expected_domain_identity": configuration.authority_domain_identity,
                "operation_purpose": operation_purpose,
                "fence_nonce": fence_nonce,
                "signed_admission": admission.wire(),
            }))
            _, raw = win32file.ReadFile(handle, MAX_FRAME + 4)
            acquired = decode_frame(bytes(raw))
            if (
                acquired.get("protocol") != PROTOCOL
                or acquired.get("operation") != "FENCED_CONSUMPTION_ACQUIRED"
                or acquired.get("fence_nonce") != fence_nonce
                or acquired.get("admission_id") != admission.envelope.admission_id
                or acquired.get("authority_head") != admission.envelope.authority_head
            ):
                raise AuthorityServiceError("operational authority fence acquisition failed")
            yield
            win32file.WriteFile(handle, encode_frame({
                "protocol": PROTOCOL,
                "operation": "RELEASE_FENCED_CONSUMPTION",
                "fence_nonce": fence_nonce,
                "admission_id": admission.envelope.admission_id,
            }))
            _, raw = win32file.ReadFile(handle, MAX_FRAME + 4)
            released = decode_frame(bytes(raw))
            if (
                released.get("protocol") != PROTOCOL
                or released.get("operation") != "FENCED_CONSUMPTION_RELEASED"
                or released.get("fence_nonce") != fence_nonce
            ):
                raise AuthorityServiceError("operational authority fence release failed")
        except pywintypes.error as error:
            raise AuthorityServiceError("operational authority fence failed") from error
        finally:
            win32file.CloseHandle(handle)

class AuthorityRuntimeHandler:
    """Strict Runtime protocol; it exposes no management transition operation."""

    def __init__(self, service: AuthorityService, *, domain_identity: str) -> None:
        if type(service) is not AuthorityService:
            raise AuthorityServiceError("canonical Authority Service required")
        self._service = service
        self._domain_identity = domain_identity

    def __call__(
        self, request: dict[str, object]
    ) -> dict[str, object] | FencedRuntimeSession:
        if request.get("protocol") != PROTOCOL:
            raise AuthorityServiceError("unsupported Runtime protocol")
        if request.get("expected_domain_identity") != self._domain_identity:
            raise AuthorityServiceError("foreign authority domain")
        operation = request.get("operation")
        if operation == "ADMIT_CAPITAL_SNAPSHOT":
            portfolio_id = request.get("portfolio_id")
            run_scope = request.get("run_scope")
            operation_purpose = request.get("operation_purpose")
            request_nonce = request.get("request_nonce")
            if any(
                type(value) is not str or not value
                for value in (
                    portfolio_id, run_scope, operation_purpose, request_nonce,
                )
            ):
                raise AuthorityServiceError("malformed Runtime admission request")
            assert isinstance(portfolio_id, str)
            assert isinstance(run_scope, str)
            assert isinstance(operation_purpose, str)
            assert isinstance(request_nonce, str)
            signed = self._service.admit(RuntimeAdmissionRequest(
                portfolio_id, run_scope, operation_purpose, self._domain_identity,
                request_nonce,
            ))
            return {"signed_admission": signed.wire()}
        if operation == "BEGIN_FENCED_CONSUMPTION":
            operation_purpose = request.get("operation_purpose")
            fence_nonce = request.get("fence_nonce")
            if (
                type(operation_purpose) is not str
                or not operation_purpose
                or type(fence_nonce) is not str
                or not fence_nonce
            ):
                raise AuthorityServiceError("malformed fenced-consumption request")
            admission = SignedAdmission.from_wire(request.get("signed_admission"))
            return FencedRuntimeSession(
                self._service.consumption_fence(
                    admission,
                    operation_purpose=operation_purpose,
                    timeout_seconds=FENCE_ACQUIRE_TIMEOUT_SECONDS,
                ),
                fence_nonce,
            )
        raise AuthorityServiceError("Runtime operation is not authorised")


class AuthorityManagementHandler:
    """Bounded Management protocol behind the Management SID endpoint."""

    _OPERATIONS = frozenset({
        "BOOTSTRAP", "ENROL_ACTIVATE", "ROTATE_SUPERSEDE", "REVOKE",
    })

    def __init__(
        self,
        handler: Callable[[dict[str, object]], dict[str, object]],
        *,
        domain_identity: str,
    ) -> None:
        self._handler = handler
        self._domain_identity = domain_identity

    def __call__(self, request: dict[str, object]) -> dict[str, object]:
        if request.get("protocol") != PROTOCOL:
            raise AuthorityServiceError("unsupported Management protocol")
        if request.get("expected_domain_identity") != self._domain_identity:
            raise AuthorityServiceError("foreign authority domain")
        if request.get("operation") not in self._OPERATIONS:
            raise AuthorityServiceError("Management operation is not authorised")
        return self._handler(request)


@dataclass(frozen=True, slots=True)
class ProtectedAuthorityServiceEndpoints:
    """Distinct protected Runtime and Management pipe compositions."""

    runtime: NamedPipeEndpoint
    management: NamedPipeEndpoint

    @classmethod
    def compose(
        cls,
        *,
        runtime_handler: AuthorityRuntimeHandler,
        management_handler: AuthorityManagementHandler,
    ) -> ProtectedAuthorityServiceEndpoints:
        from automated_trading_bot.stage5.authority_service.windows_security import (
            WindowsDeploymentConfiguration,
        )

        configuration = WindowsDeploymentConfiguration.load_protected()
        if type(runtime_handler) is not AuthorityRuntimeHandler:
            raise AuthorityServiceError("fixed Runtime handler required")
        if type(management_handler) is not AuthorityManagementHandler:
            raise AuthorityServiceError("fixed Management handler required")
        return cls(
            NamedPipeEndpoint(
                pipe_name=configuration.runtime_pipe,
                service_sid=configuration.service_sid,
                client_sid=configuration.runtime_sid,
                handler=runtime_handler,
            ),
            NamedPipeEndpoint(
                pipe_name=configuration.management_pipe,
                service_sid=configuration.service_sid,
                client_sid=configuration.management_sid,
                handler=management_handler,
            ),
        )


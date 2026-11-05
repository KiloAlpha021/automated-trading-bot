"""Deployment-fixed Authority Service composition and lifecycle boundary."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from threading import Event, Thread
from typing import cast

from automated_trading_bot.stage5.authority_service.cng import (
    CngAdmissionSigner,
    CngAdmissionVerifier,
)
from automated_trading_bot.stage5.authority_service.ipc import (
    AuthorityManagementHandler,
    AuthorityRuntimeHandler,
    ProtectedAuthorityServiceEndpoints,
)
from automated_trading_bot.stage5.authority_service.persistence import (
    AuthorityDatabase,
    AuthorityState,
    ExternalWitness,
    RetainedAuthorityStatus,
)
from automated_trading_bot.stage5.authority_service.protocol import (
    AuthorityServiceError,
    canonical_bytes,
    digest,
)
from automated_trading_bot.stage5.authority_service.service import (
    AuthoritativeSnapshotSource,
    AuthorityService,
)
from automated_trading_bot.stage5.authority_service.windows_security import (
    WindowsDeploymentConfiguration,
    WindowsDomainMutex,
    WindowsRegistryWitness,
)


class SystemTrustedClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DatabaseCurrentAuthority:
    def __init__(self, database: AuthorityDatabase, witness: ExternalWitness) -> None:
        self._database = database
        self._witness = witness

    def admission_witness(
        self, observed_at: datetime
    ) -> AbstractContextManager[AuthorityState]:
        return self._database.admission_witness(
            witness=self._witness, observed_at=observed_at.isoformat()
        )

    def consumption_witness(
        self, observed_at: datetime, *, timeout_seconds: float
    ) -> AbstractContextManager[AuthorityState]:
        return self._database.consumption_witness(
            witness=self._witness,
            observed_at=observed_at.isoformat(),
            timeout=timeout_seconds,
        )


class ProtectedAuthorityManager:
    """Typed lifecycle operations behind the authenticated Management pipe."""

    def __init__(self, database: AuthorityDatabase, witness: ExternalWitness) -> None:
        self._database = database
        self._witness = witness

    def __call__(self, request: dict[str, object]) -> dict[str, object]:
        command_id = request.get("command_id")
        expected_head = request.get("expected_authority_head")
        operation = request.get("operation")
        if type(command_id) is not str or not command_id:
            raise AuthorityServiceError("Management command ID is required")
        payload_digest = digest(request)
        current = self._database.read()
        if expected_head != current.authority_head:
            committed = self._database.retry_result(
                witness=self._witness, command_id=command_id,
                payload_digest=payload_digest,
            )
            return {"result": committed.decode("ascii")}
        body = current.body()
        body["sequence"] = current.sequence + 1
        if operation == "REVOKE":
            body["authority_status"] = RetainedAuthorityStatus.REVOKED
        elif operation == "ROTATE_SUPERSEDE":
            body["authority_status"] = RetainedAuthorityStatus.SUPERSEDED
        elif operation == "ENROL_ACTIVATE":
            required = {
                "authority_id": str,
                "authority_valid_from": str,
                "authority_expires_at": str,
                "provider_descriptor_digest": str,
                "provider_implementation_identity": str,
                "policy_id": str,
                "policy_version": int,
            }
            for name, expected_type in required.items():
                value = request.get(name)
                if type(value) is not expected_type or value in ("", 0):
                    raise AuthorityServiceError(f"Management {name} is invalid")
                body[name] = value
            body["authority_generation"] = current.authority_generation + 1
            body["authority_status"] = RetainedAuthorityStatus.ACTIVE
        else:
            raise AuthorityServiceError("bootstrap is an owner provisioning operation")
        body["authority_head"] = digest(body)
        successor = AuthorityState.create(**body)
        result = canonical_bytes({
            "command_id": command_id,
            "state_digest": successor.state_digest,
            "authority_head": successor.authority_head,
        })
        committed = self._database.publish(
            witness=self._witness,
            command_id=command_id,
            payload_digest=payload_digest,
            successor=successor,
            result=result,
        )
        return {"result": committed.decode("ascii")}


def _load_snapshot_source(path: str) -> AuthoritativeSnapshotSource:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise AuthorityServiceError("protected snapshot-source factory is invalid")
    factory = getattr(import_module(module_name), attribute, None)
    constructor = getattr(factory, "from_protected_windows_deployment", None)
    if not callable(constructor):
        raise AuthorityServiceError("protected snapshot-source factory is unavailable")
    return cast(AuthoritativeSnapshotSource, constructor())


class ProtectedAuthorityServiceHost:
    """The sole operational service graph; every trust input comes from HKLM."""

    def __init__(
        self,
        *,
        endpoints: ProtectedAuthorityServiceEndpoints,
        signer: CngAdmissionSigner,
        ownership: WindowsDomainMutex,
    ) -> None:
        self.endpoints = endpoints
        self._signer = signer
        self._ownership = ownership

    @classmethod
    def from_protected_windows_deployment(cls) -> ProtectedAuthorityServiceHost:
        configuration = WindowsDeploymentConfiguration.load_protected()
        ownership = WindowsDomainMutex(
            domain_identity=configuration.authority_domain_identity,
            service_sid=configuration.service_sid,
        )
        try:
            witness = WindowsRegistryWitness()
            database = AuthorityDatabase(Path(configuration.store_path))
            state = database.recover(witness)
            if state.domain_identity != configuration.authority_domain_identity:
                raise AuthorityServiceError("protected authority state is inconsistent")
            signer = CngAdmissionSigner.open_provisioned(
                key_name=configuration.signing_key_name,
                key_id=configuration.signing_key_id,
                key_version=configuration.signing_key_version,
                expected_public_blob=configuration.signing_public_blob,
            )
            source = _load_snapshot_source(configuration.snapshot_source_factory)
            service = AuthorityService(
                service_identity=configuration.service_identity,
                deployment_identity=configuration.deployment_identity,
                authority=DatabaseCurrentAuthority(database, witness),
                signer=signer,
                snapshot_source=source,
                clock=SystemTrustedClock(),
                admission_verifier=CngAdmissionVerifier(
                    configuration.signing_public_blob
                ),
            )
            endpoints = ProtectedAuthorityServiceEndpoints.compose(
                runtime_handler=AuthorityRuntimeHandler(
                    service, domain_identity=configuration.authority_domain_identity
                ),
                management_handler=AuthorityManagementHandler(
                    ProtectedAuthorityManager(database, witness),
                    domain_identity=configuration.authority_domain_identity,
                ),
            )
            return cls(endpoints=endpoints, signer=signer, ownership=ownership)
        except Exception:
            ownership.close()
            raise

    def serve_forever(self, stop: Event) -> None:
        def serve(endpoint: object) -> None:
            while not stop.is_set():
                try:
                    endpoint.serve_once()  # type: ignore[attr-defined]
                except AuthorityServiceError:
                    if stop.is_set():
                        return

        threads = (
            Thread(target=serve, args=(self.endpoints.runtime,), daemon=True),
            Thread(target=serve, args=(self.endpoints.management,), daemon=True),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def close(self) -> None:
        self._signer.close()
        self._ownership.close()


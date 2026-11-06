"""Authenticated, bounded, read-only reconciliation gateway application service.

This module has no transport, listener, filesystem path, publication, recovery,
broker, or trading capability.  Authentication is delegated to an injected
trusted boundary; this module does not claim that an in-process object is a
production identity boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import json
import re
from typing import Final, Protocol

from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.reconciliation.operator_console_snapshot import (
    AdapterReadState,
    NO_TRADING_AUTHORITY,
    PerformanceSnapshotV1,
    ReconciliationSnapshotV1,
    SnapshotReadResult,
    SnapshotValidationError,
    validate_snapshot,
)


GATEWAY_SCHEMA_VERSION: Final = "ATIS-OC-RECONCILIATION-READ-GATEWAY-V1"
REQUIRED_PERMISSION: Final = "reconciliation.observation.read"
MAX_RESPONSE_BYTES: Final = 65_536
MAX_PRINCIPAL_ID_CHARS: Final = 128
MAX_PERMISSIONS: Final = 32
MAX_PERMISSION_CHARS: Final = 96
MAX_REASONS: Final = 8
_PRINCIPAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}", re.ASCII)
_PERMISSION = re.compile(r"[a-z][a-z0-9._:-]{0,95}", re.ASCII)
_SAFE_SOURCE_REASONS: Final = frozenset(
    {
        "CURRENT_POINTER_MISSING",
        "PERSISTED_SNAPSHOT_UNTRUSTED",
        "FRESHNESS_UNVERIFIED",
        "PRODUCER_UNAVAILABLE",
        "TRUST_NOT_ESTABLISHED",
        "SOURCE_NOT_AUTHORISED",
        "ADAPTER_DISABLED",
    }
)


class GatewayState(StrEnum):
    ACCESS_DENIED = "ACCESS_DENIED"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Result asserted by the configured trusted authentication boundary."""

    principal_id: str
    permissions: frozenset[str]
    authenticated_at_utc: Timestamp
    expires_at_utc: Timestamp


class TrustedAuthenticationBoundary(Protocol):
    def authenticate(
        self, credential: object, *, evaluated_at_utc: Timestamp
    ) -> AuthenticatedPrincipal | None: ...


class SnapshotReader(Protocol):
    def read_current(self, *, evaluated_at_utc: Timestamp) -> SnapshotReadResult: ...


@dataclass(frozen=True, slots=True)
class GatewayObservation:
    observation_schema_version: str
    observation_id: str
    reconciliation_state: str
    discrepancy_codes: tuple[str, ...]
    observed_at_utc: str
    produced_at_utc: str
    freshness_state: str
    simulation_only: bool
    trading_authority: str
    performance: PerformanceSnapshotV1 | None

    def as_mapping(self) -> dict[str, object]:
        return {
            "discrepancy_codes": list(self.discrepancy_codes),
            "freshness_state": self.freshness_state,
            "observation_id": self.observation_id,
            "observed_at_utc": self.observed_at_utc,
            "performance": (
                None if self.performance is None else self.performance.as_mapping()
            ),
            "produced_at_utc": self.produced_at_utc,
            "reconciliation_state": self.reconciliation_state,
            "schema_version": self.observation_schema_version,
            "simulation_only": self.simulation_only,
            "trading_authority": self.trading_authority,
        }


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    state: GatewayState
    reasons: tuple[str, ...]
    observation: GatewayObservation | None

    def as_mapping(self) -> dict[str, object]:
        return {
            "gateway_schema_version": GATEWAY_SCHEMA_VERSION,
            "observation": (
                None if self.observation is None else self.observation.as_mapping()
            ),
            "reasons": list(self.reasons),
            "state": self.state.value,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.as_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")


def _validated_timestamp(value: object, name: str) -> Timestamp:
    if type(value) is not Timestamp:
        raise ValueError(f"{name} is invalid")
    instant = value.value
    if type(instant) is not datetime or instant.tzinfo is None:
        raise ValueError(f"{name} is invalid")
    try:
        offset = instant.utcoffset()
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} is invalid") from error
    if offset != timedelta(0):
        raise ValueError(f"{name} is invalid")
    return value


def _validated_principal(
    value: object, *, evaluated_at_utc: Timestamp
) -> AuthenticatedPrincipal:
    if type(value) is not AuthenticatedPrincipal:
        raise ValueError("principal is invalid")
    if type(value.principal_id) is not str or _PRINCIPAL_ID.fullmatch(value.principal_id) is None:
        raise ValueError("principal is invalid")
    if type(value.permissions) is not frozenset or len(value.permissions) > MAX_PERMISSIONS:
        raise ValueError("principal permissions are invalid")
    if any(
        type(permission) is not str
        or len(permission) > MAX_PERMISSION_CHARS
        or _PERMISSION.fullmatch(permission) is None
        for permission in value.permissions
    ):
        raise ValueError("principal permissions are invalid")
    authenticated = _validated_timestamp(
        value.authenticated_at_utc, "authenticated_at_utc"
    )
    expires = _validated_timestamp(value.expires_at_utc, "expires_at_utc")
    evaluated = _validated_timestamp(evaluated_at_utc, "evaluated_at_utc")
    if authenticated.value > evaluated.value or expires.value <= evaluated.value:
        raise ValueError("principal is not current")
    return value


def _utc_text(value: Timestamp) -> str:
    return value.value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _observation(snapshot: ReconciliationSnapshotV1) -> GatewayObservation:
    return GatewayObservation(
        observation_schema_version=snapshot.schema_version,
        observation_id=snapshot.observation_id,
        reconciliation_state=snapshot.reconciliation_state.value,
        discrepancy_codes=tuple(value.value for value in snapshot.discrepancy_codes),
        observed_at_utc=_utc_text(snapshot.observed_at_utc),
        produced_at_utc=_utc_text(snapshot.produced_at_utc),
        freshness_state=snapshot.freshness_state.value,
        simulation_only=snapshot.simulation_only,
        trading_authority=snapshot.trading_authority,
        performance=snapshot.performance,
    )


def _safe_reasons(result: SnapshotReadResult) -> tuple[str, ...]:
    if type(result.reasons) is not tuple or len(result.reasons) > MAX_REASONS:
        return ("SOURCE_RESULT_UNTRUSTED",)
    if any(type(reason) is not str for reason in result.reasons):
        return ("SOURCE_RESULT_UNTRUSTED",)
    safe = tuple(reason for reason in result.reasons if reason in _SAFE_SOURCE_REASONS)
    return safe if len(safe) == len(result.reasons) else ("SOURCE_RESULT_UNTRUSTED",)


class OperatorConsoleReadGateway:
    """Application service exposing only an authenticated read operation."""

    __slots__ = ("__authentication", "__reader")

    def __init__(
        self,
        *,
        authentication: TrustedAuthenticationBoundary,
        reader: SnapshotReader,
    ) -> None:
        self.__authentication = authentication
        self.__reader = reader

    @staticmethod
    def _response(
        state: GatewayState,
        reasons: tuple[str, ...],
        observation: GatewayObservation | None = None,
    ) -> GatewayResponse:
        response = GatewayResponse(state, reasons, observation)
        if len(response.canonical_bytes()) > MAX_RESPONSE_BYTES:
            return GatewayResponse(
                GatewayState.UNKNOWN, ("RESPONSE_LIMIT_EXCEEDED",), None
            )
        return response

    def read(
        self, credential: object, *, evaluated_at_utc: Timestamp
    ) -> GatewayResponse:
        try:
            evaluated = _validated_timestamp(evaluated_at_utc, "evaluated_at_utc")
            asserted = self.__authentication.authenticate(
                credential, evaluated_at_utc=evaluated
            )
            principal = _validated_principal(asserted, evaluated_at_utc=evaluated)
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._response(
                GatewayState.ACCESS_DENIED, ("AUTHENTICATION_REQUIRED",)
            )
        except Exception:
            return self._response(
                GatewayState.ACCESS_DENIED, ("AUTHENTICATION_UNAVAILABLE",)
            )
        if REQUIRED_PERMISSION not in principal.permissions:
            return self._response(
                GatewayState.ACCESS_DENIED, ("PERMISSION_DENIED",)
            )
        try:
            result = self.__reader.read_current(evaluated_at_utc=evaluated)
        except Exception:
            return self._response(GatewayState.UNKNOWN, ("STORAGE_READ_FAILED",))
        if type(result) is not SnapshotReadResult:
            return self._response(GatewayState.UNKNOWN, ("SOURCE_RESULT_UNTRUSTED",))
        reasons = _safe_reasons(result)
        if result.state is AdapterReadState.UNAVAILABLE:
            return self._response(GatewayState.UNAVAILABLE, reasons)
        if result.state is not AdapterReadState.UNKNOWN:
            return self._response(GatewayState.UNKNOWN, ("SOURCE_RESULT_UNTRUSTED",))
        if result.snapshot is None:
            return self._response(GatewayState.UNKNOWN, reasons)
        try:
            snapshot = validate_snapshot(result.snapshot, evaluated_at_utc=evaluated)
        except (AttributeError, SnapshotValidationError, TypeError, ValueError):
            return self._response(GatewayState.UNKNOWN, ("SOURCE_RESULT_UNTRUSTED",))
        if reasons != ("FRESHNESS_UNVERIFIED",):
            return self._response(GatewayState.UNKNOWN, ("SOURCE_RESULT_UNTRUSTED",))
        if (
            snapshot.simulation_only is not True
            or snapshot.trading_authority != NO_TRADING_AUTHORITY
            or snapshot.freshness_state.value != "UNVERIFIED"
        ):
            return self._response(GatewayState.UNKNOWN, ("SOURCE_RESULT_UNTRUSTED",))
        return self._response(GatewayState.UNKNOWN, reasons, _observation(snapshot))

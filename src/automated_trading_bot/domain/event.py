from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.timestamp import Timestamp


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Canonical metadata carried by every durable domain event."""

    event_id: EventId
    correlation_id: CorrelationId
    causation_id: CausationId
    idempotency_key: IdempotencyKey
    occurred_at: Timestamp
    schema_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("event_id must be an EventId")

        if not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be a CorrelationId")

        if not isinstance(self.causation_id, CausationId):
            raise TypeError("causation_id must be a CausationId")

        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise TypeError("idempotency_key must be an IdempotencyKey")

        if not isinstance(self.occurred_at, Timestamp):
            raise TypeError("occurred_at must be a Timestamp")

        if not isinstance(self.schema_version, int) or isinstance(
            self.schema_version,
            bool,
        ):
            raise TypeError("schema_version must be an int")

        if self.schema_version < 1:
            raise ValueError("schema_version must be at least 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id.value),
            "correlation_id": str(self.correlation_id.value),
            "causation_id": str(self.causation_id.value),
            "idempotency_key": self.idempotency_key.value,
            "occurred_at": self.occurred_at.value.isoformat(),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        return cls(
            event_id=EventId(UUID(data["event_id"])),
            correlation_id=CorrelationId(UUID(data["correlation_id"])),
            causation_id=CausationId(UUID(data["causation_id"])),
            idempotency_key=IdempotencyKey(data["idempotency_key"]),
            occurred_at=Timestamp(
                value=datetime.fromisoformat(data["occurred_at"])
            ),
            schema_version=data["schema_version"],
        )


class DuplicateEventError(ValueError):
    """An EventId has already been registered."""


class EventRegistry:
    """Process-local duplicate history owned by this registry instance.

    Retain one instance for the processing lifetime; a new instance starts empty.
    Storage is memory-only and does not survive process restarts. EventId remains
    the canonical durable identity for future persistence, replay and recovery.
    Registration records observation, not successful processing. Callers must
    serialize access to this registry.
    """

    def __init__(self) -> None:
        self._event_ids: set[EventId] = set()

    def register(self, event_id: EventId) -> None:
        """Accept an unseen identity or raise without changing duplicate history."""
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        if event_id in self._event_ids:
            raise DuplicateEventError(f"duplicate EventId: {event_id.value}")
        self._event_ids.add(event_id)

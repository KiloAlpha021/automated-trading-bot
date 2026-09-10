from dataclasses import dataclass

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
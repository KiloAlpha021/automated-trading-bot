# Initial PostgreSQL event history, outbox and inbox design

Design ID: M1-EVENT-STORAGE-01. Requirement: IMP-026-M1-03.
Provenance: INFERRED engineering specification derived from the hash-matched
Implementation Specification v1.0 sections 6–8.1 and 17.1 and owner decision
M1-SCOPE-2026-09-11-01. The field names/types below are design decisions, not
verbatim source wording. No original source or domain API is modified.

## Scope and evidence boundary

M1 delivers this design and its static control validation only. No SQL is
executed and no database, migration runner, transport, financial port or runtime
component is introduced. Existing EventEnvelope remains unchanged. Added storage
metadata is supplied by the future authorized producer/persistence boundary;
missing metadata must be rejected or quarantined, never fabricated.

Runtime outbox/inbox, restart/failover durability, durable handler consumption,
reconstruction and resumption remain deferred until those capabilities exist.
This design specifies obligations for that implementation; it does not claim
they are currently enforced. Runtime acceptance needs PostgreSQL constraints,
transaction/concurrency tests and crash/failover evidence, not this static test.

## Record definitions

The following machine-readable block is the normative initial record layout.
Columns are NOT NULL unless listed under nullable. Text identifiers must be
nonblank. PostgreSQL UUID carries the existing UUID identity; no UUID timestamp
or sequence is treated as business order. One database is isolated per environment;
the explicit environment attribute also travels with each event.

```json
{
  "design_id": "M1-EVENT-STORAGE-01",
  "status": "SPECIFICATION_ONLY",
  "source_sha256": "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7",
  "tables": {
    "event_history": {
      "owner": "owning domain via persistence boundary",
      "append_only": true,
      "columns": {
        "event_id": "uuid",
        "environment": "text",
        "stream_kind": "text",
        "stream_id": "uuid",
        "entity_ref": "text",
        "stream_version": "bigint",
        "event_type": "text",
        "schema_version": "integer",
        "correlation_id": "uuid",
        "causation_id": "uuid",
        "idempotency_key": "text",
        "operation_scope": "text",
        "authority_epoch": "bigint",
        "occurred_at": "timestamptz",
        "recorded_at": "timestamptz",
        "source_id": "text",
        "actor_id": "text",
        "release_id": "text",
        "config_id": "text",
        "payload_bytes": "bytea",
        "payload_encoding": "text",
        "integrity_scheme": "text",
        "content_sha256": "bytea",
        "previous_event_id": "uuid",
        "previous_sha256": "bytea",
        "corrects_event_id": "uuid"
      },
      "nullable": [
        "previous_event_id",
        "previous_sha256",
        "corrects_event_id"
      ],
      "primary_key": [
        "event_id"
      ],
      "unique": [
        [
          "environment",
          "stream_kind",
          "stream_id",
          "stream_version"
        ]
      ],
      "foreign_keys": [
        [
          "previous_event_id",
          "event_history",
          "event_id"
        ],
        [
          "corrects_event_id",
          "event_history",
          "event_id"
        ]
      ]
    },
    "outbox": {
      "owner": "owning domain creates; transport updates delivery metadata",
      "append_only": false,
      "columns": {
        "event_id": "uuid",
        "destination": "text",
        "status": "text",
        "attempt_count": "integer",
        "next_attempt_at": "timestamptz",
        "last_attempt_at": "timestamptz",
        "last_error_code": "text",
        "delivered_at": "timestamptz"
      },
      "nullable": [
        "next_attempt_at",
        "last_attempt_at",
        "last_error_code",
        "delivered_at"
      ],
      "primary_key": [
        "event_id",
        "destination"
      ],
      "unique": [],
      "foreign_keys": [
        [
          "event_id",
          "event_history",
          "event_id"
        ]
      ]
    },
    "inbox": {
      "owner": "named consuming handler within its authorized domain",
      "append_only": false,
      "columns": {
        "event_id": "uuid",
        "handler_id": "text",
        "handler_version": "text",
        "status": "text",
        "attempt_count": "integer",
        "received_at": "timestamptz",
        "next_attempt_at": "timestamptz",
        "last_attempt_at": "timestamptz",
        "last_error_code": "text",
        "applied_at": "timestamptz"
      },
      "nullable": [
        "next_attempt_at",
        "last_attempt_at",
        "last_error_code",
        "applied_at"
      ],
      "primary_key": [
        "event_id",
        "handler_id"
      ],
      "unique": [],
      "foreign_keys": [
        [
          "event_id",
          "event_history",
          "event_id"
        ]
      ]
    }
  },
  "statuses": {
    "outbox": [
      "PENDING",
      "RETRY",
      "DELIVERED",
      "QUARANTINED"
    ],
    "inbox": [
      "PENDING",
      "RETRY",
      "APPLIED",
      "QUARANTINED"
    ]
  },
  "controls": {
    "event_identity": "EventId",
    "handler_upgrade_reapplies": false,
    "global_order": false,
    "delivery": "at-least-once",
    "history_mutable": false,
    "replay_financial_authority": false,
    "atomic_state_and_outbox": true,
    "atomic_inbox_and_effect": true
  },
  "source_map": {
    "identity": "6.1",
    "ownership": "6.2",
    "requirement": "7 IMP-026",
    "records": "8",
    "transactions": "8.1",
    "schema_and_migration_specification": "17.1"
  }
}
```

## Identity, uniqueness and ordering — §§6.1, 7, 8

EventId is the sole canonical event duplicate identity. The global event_id
primary key cannot be replaced by payload equality or IdempotencyKey. Repeated
EventId with identical immutable content is the same observation; conflicting
content is quarantined and cannot overwrite the original or produce an effect.

IdempotencyKey is an opaque operation identity scoped by operation_scope and
authority_epoch, not a unique event constraint: one operation can emit multiple
distinct events. Producers must enforce operation-level idempotency in their
own state transaction. Event uniqueness alone does not prove that invariant.

stream_kind and stream_id identify an owning aggregate; entity_ref is its
attributable domain reference (not a replacement event identity). Versions start
at 1, are positive and contiguous within environment/stream_kind/stream_id.
Append requires an optimistic compare against the last committed version plus
one and the previous event/hash. The unique stream/version tuple prevents two
committed occupants but cannot by itself prevent gaps: the future transaction
must enforce contiguity. Version 1 has null predecessor fields; later versions
require both predecessor fields, matching the same stream and prior version.

There is no global order. Timestamps, UUID ordering and arrival order cannot
establish stream order. A consumer requiring ordered state must serialize its
handler/stream processing, verify the predecessor was applied, and keep gaps or
conflicts pending/quarantined. A dispatcher must not treat later delivery as
proof of earlier delivery. Neither design nor retries may silently skip a gap.

correlation_id and causation_id retain existing typed UUID values. Causation may
identify a command or an externally scoped cause, so it is not blindly foreign-
keyed to event_history. Command and event semantics remain distinct; an event
never becomes an instruction to submit a financial action.

## Integrity, provenance and immutability — §§6, 8

schema_version > 0; stream_version > 0; authority_epoch >= 0. event_type and
payload_encoding select a versioned event schema. occurrence time is the original
validated UTC instant, recorded_at the persistence observation time; timestamp
precision is microseconds. UTC-aware input and UTC output/session settings are
required (timestamptz alone does not enforce input timezone provenance). Clock
skew may make recorded_at earlier than occurred_at; do not rewrite either time.
Unknown source time/quality blocks any authority-dependent use.

source_id, actor_id, release_id and config_id identify attributable provenance.
They contain references, never secret values. Financial/account scope must be
encoded by the owning stream and operation_scope without merging ownership.

integrity_scheme is initially SHA256-LP-UTF8-v1. Hash all immutable columns in
the JSON column order except content_sha256. For each value append a one-byte
null flag (0 null, 1 present); present values follow with an unsigned 64-bit
big-endian byte length and the bytes. UUIDs are lowercase hyphenated text,
integers base-10 ASCII, timestamps UTC YYYY-MM-DDTHH:MM:SS.ffffffZ, text exact
UTF-8 without normalization, bytea raw bytes. Hash bytes once with SHA-256;
content_sha256 and nonnull previous_sha256 must have length 32. Payload bytes
are retained exactly; decoding/validation uses event_type/schema_version and
payload_encoding. No lossy JSON reserialization may replace evidence bytes.

A matching hash detects content changes, not authenticity or complete history.
Trusted source identity, access control, stream continuity and reconciliation
remain independently required. Event history is append-only: no UPDATE/DELETE
for ordinary writers, immutable EventId/content binding, restrictive foreign
keys with no cascade deletion. Corrections append a new EventId with
corrects_event_id pointing to retained evidence; no silent rewrite.

## Outbox and inbox state — §§7, 8

Outbox identity is (event_id, destination). Event payload/provenance stays in
immutable event_history, not duplicated mutable transport columns. Destination
is a stable logical route, never a credential or an execution capability.
Inbox identity is (event_id, handler_id); handler_id is stable across releases.
handler_version is recorded on first claim and never changed. It is deliberately
NOT part of the deduplication key: upgrading code cannot reapply an old financial
effect. Version mismatch requires explicit quarantine/review, not a new receipt.
A different handler ID must not be used to bypass an existing handler's effects.

attempt_count starts at 0, remains nonnegative and increments only on an actual
attempt. PENDING/RETRY may reach DELIVERED (outbox), APPLIED (inbox), or
QUARANTINED. Terminal success never transitions to pending. Quarantine is not
success; release requires attributable authorized review, never automatic retry.
Retries keep the same keys and immutable handler attribution. last_error_code
is a sanitized reason code, not raw credential-bearing exception text.
RETRY requires next_attempt_at; attempted rows require last_attempt_at.
DELIVERED requires delivered_at; APPLIED requires applied_at; success timestamps
are null otherwise. Retry/backoff timing is a bounded runtime policy, not a new
M1 scheduler. Delivery acknowledgment is not broker acceptance or execution.

Only delivery/attempt state is mutable; key/route/handler identity is immutable.
The runtime must retain attributable state-transition/attempt audit evidence;
these current-state fields alone are not a full attempt history. Deletion or
retention may never erase dedup identity or evidence needed by unresolved work.

## Ownership and transaction boundaries — §§6.2, 8.1

The domain owns its state and event meaning. Persistence enforces storage rules;
transport owns delivery bookkeeping only. OMS retains order/submission authority;
broker observations remain external reality. No row, status, handler, dispatcher
or schema grants trading authority or bypasses risk/compliance/reconciliation.

Producer transaction: compare/update owning state, append event_history, and
insert all required outbox destinations in ONE database transaction. Any failure
rolls back all three. No committed state-only or event-without-required-outbox
transition is permitted. Broker/network calls occur outside that transaction;
this pattern cannot make them atomic with the database.

Consumer transaction: validate immutable event identity/schema/integrity and
scope; insert/lock inbox identity; verify handler version and stream predecessor;
apply the authorized domain consequence and its outgoing events/outbox records;
mark inbox APPLIED in the SAME transaction. Conflict on APPLIED returns without
reapplying. Failed transactions roll back the consequence and APPLIED transition.
A receipt/retry record may be committed separately, but it cannot assert APPLIED.
Concurrent attempts require row/stream serialization and uniqueness enforcement.

For a local event the inbox foreign key references existing event_history. An
external event is first admitted as validated immutable event evidence; identical
redelivery may reuse it, conflicting identity cannot. External events are not
republished automatically. Operational command admission must use its own command
contract; these event-consumption rows do not create command submission authority.

Transport is at-least-once: a crash after delivery but before acknowledgment may
redeliver. Effectively-once financial consequence requires the atomic inbox/effect
rule AND domain idempotency/fencing/reconciliation. No exactly-once network claim
is made. Replay may rebuild inert projections/evidence only; it cannot reset an
APPLIED receipt, enqueue historical financial work, invoke financial ports or
mint current authority. Historical validity does not authorize resumption.

## Migration and versioning expectations — §17.1

The initial migration specification must assign a stable migration identity,
content checksum, predecessor/version and reviewed apply/recovery procedure.
Create event_history before its referencing inbox/outbox records; validate keys,
checks, restricted mutation rights and existing-data integrity before activation.
Never use a destructive downgrade to discard authoritative history or dedup keys.
Prefer a reviewed forward correction; abort activation on validation failure.

Database migration version and event schema_version are distinct. Preserve
original event bytes and old readers' compatibility; incompatible changes require
an explicit version, migration/backfill plan and attributable verification.
A later executable migration must prove apply/reapply detection, rollback or
roll-forward safety, crash interruption, uniqueness/concurrency and no data loss.
This section specifies expectations; the separately required concrete initial
migration plan and its verification are not delivered by this schema artifact.

## Source/control review

§6.1 maps to identity, UTC, causal and operation scope fields. §6.2 maps to
ownership and immutability. §7 IMP-026 maps to duplicate identity and workflow
records. §8 maps to event_history, inbox/outbox, integrity and retry state.
§8.1 maps to producer atomicity and consumption-before-effect commitment.
§17.1 and owner M1-SCOPE-2026-09-11-01 make this design M1 specification-only.

Static tests check required fields, keys, references and safety-control choices,
including deliberate removal/weakening. They do not emulate PostgreSQL or prove
future runtime correctness. Human design review must also inspect the transaction,
ordering, replay and migration text; keyword presence alone is not such review.

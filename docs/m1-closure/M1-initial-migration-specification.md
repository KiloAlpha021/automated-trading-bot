# Initial event storage migration specification

Requirement: IMP-026-M1-04. Migration identity: M1-EVENT-STORAGE-0001.
Provenance: INFERRED engineering specification, authorized by the project owner.
Governing sources: Implementation Specification v1.0 sections 6.1 (identities),
6.2 (ownership), 7 IMP-026, 8 (records), 8.1 (transaction boundaries), 17.1
(initial schema and migrations), and owner decision M1-SCOPE-2026-09-11-01.
The source and approved design hashes below bind the exact inputs.

M1 delivers this specification and static verification only. This is not an
executable migration or proof of database enforcement. No database is contacted,
no SQL is run, and no runtime persistence, financial authority, or new schema
semantics are introduced. The approved design remains unchanged.

## Concrete version 1 definition

The JSON below defines every column in approved design order, its PostgreSQL
type and nullability, all keys, foreign-key actions, initial defaults and CHECK
predicates. CHECK values are PostgreSQL predicate text for later translation,
not executed SQL. All constraints must be validated and immediate on activation;
none may be left NOT VALID, disabled, or replaced with application-only checks.
Primary/unique keys require their PostgreSQL constraint-backed unique indexes.
No additional index is required by the approved design; performance indexes
require later workload evidence and must never replace correctness constraints.

```json
{
  "migration_id": "M1-EVENT-STORAGE-0001",
  "schema_version": 1,
  "predecessor": null,
  "status": "SPECIFICATION_ONLY",
  "design_id": "M1-EVENT-STORAGE-01",
  "design_sha256": "e7e5563c061d7a7b3afffba98b492d93acc4c8c2d2b5b6c1dbcfbd3bb937eebb",
  "source_sha256": "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7",
  "creation_order": [
    "event_history",
    "outbox",
    "inbox"
  ],
  "tables": {
    "event_history": {
      "columns": [
        {
          "name": "event_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "environment",
          "type": "text",
          "not_null": true
        },
        {
          "name": "stream_kind",
          "type": "text",
          "not_null": true
        },
        {
          "name": "stream_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "entity_ref",
          "type": "text",
          "not_null": true
        },
        {
          "name": "stream_version",
          "type": "bigint",
          "not_null": true
        },
        {
          "name": "event_type",
          "type": "text",
          "not_null": true
        },
        {
          "name": "schema_version",
          "type": "integer",
          "not_null": true
        },
        {
          "name": "correlation_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "causation_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "idempotency_key",
          "type": "text",
          "not_null": true
        },
        {
          "name": "operation_scope",
          "type": "text",
          "not_null": true
        },
        {
          "name": "authority_epoch",
          "type": "bigint",
          "not_null": true
        },
        {
          "name": "occurred_at",
          "type": "timestamptz",
          "not_null": true
        },
        {
          "name": "recorded_at",
          "type": "timestamptz",
          "not_null": true
        },
        {
          "name": "source_id",
          "type": "text",
          "not_null": true
        },
        {
          "name": "actor_id",
          "type": "text",
          "not_null": true
        },
        {
          "name": "release_id",
          "type": "text",
          "not_null": true
        },
        {
          "name": "config_id",
          "type": "text",
          "not_null": true
        },
        {
          "name": "payload_bytes",
          "type": "bytea",
          "not_null": true
        },
        {
          "name": "payload_encoding",
          "type": "text",
          "not_null": true
        },
        {
          "name": "integrity_scheme",
          "type": "text",
          "not_null": true
        },
        {
          "name": "content_sha256",
          "type": "bytea",
          "not_null": true
        },
        {
          "name": "previous_event_id",
          "type": "uuid",
          "not_null": false
        },
        {
          "name": "previous_sha256",
          "type": "bytea",
          "not_null": false
        },
        {
          "name": "corrects_event_id",
          "type": "uuid",
          "not_null": false
        }
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
        {
          "column": "previous_event_id",
          "table": "event_history",
          "target": "event_id",
          "on_delete": "RESTRICT",
          "on_update": "RESTRICT"
        },
        {
          "column": "corrects_event_id",
          "table": "event_history",
          "target": "event_id",
          "on_delete": "RESTRICT",
          "on_update": "RESTRICT"
        }
      ],
      "defaults": {}
    },
    "outbox": {
      "columns": [
        {
          "name": "event_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "destination",
          "type": "text",
          "not_null": true
        },
        {
          "name": "status",
          "type": "text",
          "not_null": true
        },
        {
          "name": "attempt_count",
          "type": "integer",
          "not_null": true
        },
        {
          "name": "next_attempt_at",
          "type": "timestamptz",
          "not_null": false
        },
        {
          "name": "last_attempt_at",
          "type": "timestamptz",
          "not_null": false
        },
        {
          "name": "last_error_code",
          "type": "text",
          "not_null": false
        },
        {
          "name": "delivered_at",
          "type": "timestamptz",
          "not_null": false
        }
      ],
      "primary_key": [
        "event_id",
        "destination"
      ],
      "unique": [],
      "foreign_keys": [
        {
          "column": "event_id",
          "table": "event_history",
          "target": "event_id",
          "on_delete": "RESTRICT",
          "on_update": "RESTRICT"
        }
      ],
      "defaults": {
        "attempt_count": 0,
        "status": "PENDING"
      }
    },
    "inbox": {
      "columns": [
        {
          "name": "event_id",
          "type": "uuid",
          "not_null": true
        },
        {
          "name": "handler_id",
          "type": "text",
          "not_null": true
        },
        {
          "name": "handler_version",
          "type": "text",
          "not_null": true
        },
        {
          "name": "status",
          "type": "text",
          "not_null": true
        },
        {
          "name": "attempt_count",
          "type": "integer",
          "not_null": true
        },
        {
          "name": "received_at",
          "type": "timestamptz",
          "not_null": true
        },
        {
          "name": "next_attempt_at",
          "type": "timestamptz",
          "not_null": false
        },
        {
          "name": "last_attempt_at",
          "type": "timestamptz",
          "not_null": false
        },
        {
          "name": "last_error_code",
          "type": "text",
          "not_null": false
        },
        {
          "name": "applied_at",
          "type": "timestamptz",
          "not_null": false
        }
      ],
      "primary_key": [
        "event_id",
        "handler_id"
      ],
      "unique": [],
      "foreign_keys": [
        {
          "column": "event_id",
          "table": "event_history",
          "target": "event_id",
          "on_delete": "RESTRICT",
          "on_update": "RESTRICT"
        }
      ],
      "defaults": {
        "attempt_count": 0,
        "status": "PENDING"
      }
    }
  },
  "checks": {
    "event_history": {
      "positive_stream": "stream_version > 0",
      "positive_schema": "schema_version > 0",
      "nonnegative_epoch": "authority_epoch >= 0",
      "hash_size": "octet_length(content_sha256) = 32",
      "previous_hash_size": "previous_sha256 IS NULL OR octet_length(previous_sha256) = 32",
      "integrity_scheme": "integrity_scheme = 'SHA256-LP-UTF8-v1'",
      "predecessor_shape": "(stream_version = 1 AND previous_event_id IS NULL AND previous_sha256 IS NULL) OR (stream_version > 1 AND previous_event_id IS NOT NULL AND previous_sha256 IS NOT NULL)",
      "nonblank_environment": "length(btrim(environment)) > 0",
      "nonblank_stream_kind": "length(btrim(stream_kind)) > 0",
      "nonblank_entity_ref": "length(btrim(entity_ref)) > 0",
      "nonblank_event_type": "length(btrim(event_type)) > 0",
      "nonblank_idempotency_key": "length(btrim(idempotency_key)) > 0",
      "nonblank_operation_scope": "length(btrim(operation_scope)) > 0",
      "nonblank_source_id": "length(btrim(source_id)) > 0",
      "nonblank_actor_id": "length(btrim(actor_id)) > 0",
      "nonblank_release_id": "length(btrim(release_id)) > 0",
      "nonblank_config_id": "length(btrim(config_id)) > 0",
      "nonblank_payload_encoding": "length(btrim(payload_encoding)) > 0",
      "nonblank_integrity_scheme": "length(btrim(integrity_scheme)) > 0"
    },
    "outbox": {
      "nonnegative_attempts": "attempt_count >= 0",
      "allowed_status": "status IN ('PENDING', 'RETRY', 'DELIVERED', 'QUARANTINED')",
      "retry_time": "status <> 'RETRY' OR next_attempt_at IS NOT NULL",
      "attempt_time": "attempt_count = 0 OR last_attempt_at IS NOT NULL",
      "success_time": "(status = 'DELIVERED') = (delivered_at IS NOT NULL)",
      "nonblank_destination": "length(btrim(destination)) > 0",
      "nonblank_status": "length(btrim(status)) > 0",
      "nonblank_last_error_code": "last_error_code IS NULL OR length(btrim(last_error_code)) > 0"
    },
    "inbox": {
      "nonnegative_attempts": "attempt_count >= 0",
      "allowed_status": "status IN ('PENDING', 'RETRY', 'APPLIED', 'QUARANTINED')",
      "retry_time": "status <> 'RETRY' OR next_attempt_at IS NOT NULL",
      "attempt_time": "attempt_count = 0 OR last_attempt_at IS NOT NULL",
      "success_time": "(status = 'APPLIED') = (applied_at IS NOT NULL)",
      "nonblank_handler_id": "length(btrim(handler_id)) > 0",
      "nonblank_handler_version": "length(btrim(handler_version)) > 0",
      "nonblank_status": "length(btrim(status)) > 0",
      "nonblank_last_error_code": "last_error_code IS NULL OR length(btrim(last_error_code)) > 0"
    }
  },
  "extra_indexes": [],
  "apply": {
    "single_transaction": true,
    "initial_only": true,
    "existing_objects": "FAIL_CLOSED",
    "same_identity_different_checksum": "REJECT",
    "record_version_after_validation": true
  },
  "rollback": {
    "destructive_down_allowed": false,
    "before_commit": "ROLLBACK_TRANSACTION",
    "after_commit": "REVIEWED_FORWARD_CORRECTION"
  },
  "future_enforcement": [
    "append_only_history",
    "immutable_workflow_identity",
    "terminal_state_nonregression",
    "attributable_attempt_history",
    "same_stream_contiguous_predecessor",
    "producer_state_event_outbox_atomicity",
    "consumer_inbox_effect_atomicity",
    "no_replay_financial_authority"
  ],
  "ownership": {
    "event_history": "owning domain via persistence boundary",
    "outbox": "owning domain creates; transport updates delivery metadata",
    "inbox": "named consuming handler within its authorized domain"
  }
}
```

## Dependency order and forward intent

1. Before starting, verify approved source/design identities and this migration's
   SHA-256 checksum. The checksum is SHA-256 of this entire UTF-8 artifact's bytes,
   recorded externally with the migration ID; do not embed a self-referential
   checksum. Any edit under the same already-applied ID is rejected.
2. Require an isolated environment database, no pre-existing event_history,
   outbox or inbox objects, and no predecessor migration. This is an initial
   empty-schema plan, not an implicit adoption/backfill of unknown objects.
   Existing objects or conflicting migration history stop the migration.
3. In one future database transaction, create event_history columns in the
   specified order, then its primary/unique keys and CHECK constraints. Add its
   self-referential foreign keys only after its event_id primary key exists.
4. Create outbox, then inbox, with their columns, primary keys, CHECK constraints
   and event_id foreign keys after event_history exists. No FK may cascade
   deletion or update. SQL naming must deterministically use table plus column/
   control name; changing generated names must not change defined semantics.
5. Before exposure, inspect PostgreSQL catalog types, column order/nullability,
   default expressions, keys, unique indexes, CHECK definitions and FK actions
   against this specification. Install and verify the restricted access/update
   enforcement required below before any runtime writer is enabled. Constraint
   validity alone is not permission to activate financial/runtime processing.
6. Record migration ID, schema version, artifact checksum and successful
   validation in the future migration mechanism's metadata, then commit together
   with schema creation. No separate migration-history subsystem is added in M1.
   A repeat of the same recorded ID/checksum verifies catalog equivalence and
   performs no DDL; differing checksum or catalog drift fails closed.

The initial existence check distinguishes a previously validated same-ID
installation from unexplained existing objects. Retry never uses IF NOT EXISTS
as a substitute for migration identity and catalog verification.

## Keys, deduplication and constraint limits

The event_history primary key is EventId, never payload or IdempotencyKey.
IdempotencyKey is not unique across events: one operation can emit multiple
EventIds. Stream uniqueness is precisely the approved environment/stream-kind/
stream-ID/version tuple. Outbox is unique by event/destination; inbox by
(event_id, handler_id). handler_version remains NOT NULL attribution, not part
of the key; upgrades cannot manufacture a second application entitlement.

All causal/provenance columns retain approved UUID/text types and NOT NULL
constraints. causation_id is deliberately not a foreign key to event_history:
its cause may be a command or external identity. Payload bytes remain exact.
Hash length and predecessor shape are CHECK constraints; hash recomputation,
source authenticity and predecessor same-stream/contiguous-version validation
are separate runtime obligations. No CHECK purports to query earlier rows.

Timestamptz preserves instants, not original input timezone provenance. UTC input
validation/output settings and microsecond precision remain inherited runtime
requirements. No recorded_at >= occurred_at constraint is introduced: clock
skew does not justify rewriting occurrence evidence.

## Ownership, immutability and future activation barriers

Ownership is copied verbatim from the approved design. Ordinary writers must
not UPDATE, DELETE or TRUNCATE event_history; corrections append successor
EventIds. Transport can update delivery/attempt metadata only. Inbox processing
cannot alter event_id, handler_id or handler_version. Outbox cannot alter event_id
or destination. Event identity and handler attribution never change on retry.

Row CHECK constraints do not prevent terminal-state regression, deletion of
dedup keys, loss of attempt history or privileged bypass. Future restricted
roles/update paths must enforce those inherited rules, retaining audit evidence
and prohibiting silent deletion. Their implementation is deferred, not waived;
unverified enforcement keeps runtime writers disabled. M1 adds no roles,
triggers, repositories, workers or transaction managers.

The owning domain must commit state + event_history + required outbox records
atomically. The handler must commit inbox APPLIED + authorized domain effect
atomically, including outgoing events. Network/broker calls cannot share that
transaction. OMS retains submission authority, broker observations retain
external truth, and storage/transport rows grant neither. Replay cannot reset
receipts or invoke financial side effects. Migration success grants no runtime
or financial authorization and makes no restart/failover correctness claim.

## Failure, rollback and versioning policy

Before commit, an error rolls back the entire schema transaction and its
migration metadata. Do not publish an applied version after failed validation.
After commit, destructive down migration is prohibited even if a later operator
believes the tables are empty: retain authoritative event/dedup history. Remedy
through a separately reviewed forward migration with a new identity, explicit
predecessor and preservation validation. Never drop/truncate tables or unique
keys to make a retry pass. Unknown schema state remains restricted.

Database schema version 1 is separate from per-event schema_version. Later
incompatible changes require an explicit forward plan and compatibility review;
retain old event bytes and identifiable provenance. No backfill, destructive
rollback or silent schema drift is authorized by this document.

## Verification plan and boundary

M1 static validation compares the complete column/key/FK definition with the
approved design, validates checks and constraint-backed index expectations, and
checks dependency order and rollback/version policy. Deliberate missing keys,
nullability changes, handler-version key expansion, missing checks, unsafe FK
actions, wrong order and destructive rollback must fail validation.

Future executable acceptance must test on disposable PostgreSQL: initial apply,
same-checksum repeat, checksum/catalog mismatch rejection, invalid row/duplicate
rejection, FK restrictions, interruption/rollback of uncommitted DDL, protected
writes, forward correction preserving data, and transaction/concurrency safety.
These are future acceptance obligations, not tests claimed to have run in M1.
Static verification does not prove PostgreSQL syntax execution or runtime safety.

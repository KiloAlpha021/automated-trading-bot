# ATIS background operations architecture contract

Status: design only. No daemon, Windows service, scheduled task, listener, or continuous execution is authorised or implemented by Task050.

## Non-negotiable boundary

ATIS processes must operate independently of the Operator Console. The Console is a supervisory client. Closing, reloading, or losing the Console must not stop research work, cancel an independently authorised job, alter evidence, or change trading authority. Research scheduling and trading authority remain separate. Automatic live trading is excluded.

The proposed process-supervision states are `STOPPED`, `STARTING`, `RUNNING`, `DEGRADED`, `STOPPING`, `FAILED`, and `RECOVERING`. They describe process lifecycle only and must never be mapped to existing `OperationalMode` or to trading permission.

## Proposed trust and lifecycle boundaries

| Boundary | Design obligation | Fail-closed outcome |
| --- | --- | --- |
| Authorised launcher or supervisor | Starts only authenticated, configured ATIS components under a least-privilege service identity | `FAILED`; no fallback identity |
| Research scheduler | Runs approved research/backtest definitions, with bounded concurrency and explicit run identity | Job rejected; evidence retained |
| Data ingestion | Uses licensed, qualified sources and records source identity, observation time, and integrity | `DEGRADED` or `FAILED`; data not promoted |
| Snapshot publisher | Publishes immutable, versioned, digest-bound read models atomically | Previous qualified snapshot remains; new one unavailable |
| Health/heartbeat | Reports component lifecycle separately from business and authority state | Missing/stale heartbeat is visible, never healthy |
| Evidence persistence | Appends attributable, tamper-evident records with retention and recovery controls | State transition blocked when mandatory audit fails |
| Console connection | Authenticated, authorised, read-only BFF boundary with provenance/freshness validation | `UNKNOWN`, `UNAVAILABLE`, or `RESTRICTED` |
| Recovery/reconciliation | Restores known-clean state, replays evidence, then requires reconciliation before reauthorisation | Remains `RECOVERING` or `FAILED` |

## Component contracts

1. **Independent process lifecycle.** A future supervisor owns start, stop, heartbeat, restart, and safe shutdown. The browser owns none of these operations.
2. **Research scheduler.** Schedules approved research and backtests with immutable configuration identity, deterministic inputs, explicit cancellation semantics, and durable outcomes. It cannot grant trading authority.
3. **Data-ingestion lifecycle.** Acquisition, validation, quarantine, freshness, provenance, and licensing gates precede use. No live provider is selected by this design.
4. **Health and heartbeat.** Each process reports identity, lifecycle state, observation time, build/configuration identity, and bounded diagnostics. Missing evidence fails closed.
5. **Snapshot publication.** A producer validates source evidence, writes a complete immutable candidate, verifies its digest, and atomically publishes a version pointer. Readers never observe partial files.
6. **Supervision and restart.** Restart policy must be bounded, observable, and state-aware; crash loops enter `FAILED`. There is no unbounded automatic restart.
7. **Failure containment.** Scheduler, ingestion, research, snapshot, UI boundary, and future execution processes use separate permissions and failure domains.
8. **Evidence persistence.** Lifecycle, job, ingestion, snapshot, authentication, and recovery events require attributable append-only evidence with independent integrity checks.
9. **Safe shutdown/restart.** Stop accepting work, checkpoint or cancel according to the job contract, flush mandatory evidence, then stop. Startup validates the known-clean checkpoint before work resumes.
10. **Recovery gate.** Restore, validate, replay, reconcile, and obtain the separately defined authority decision. Recovery alone never reauthorises operation.
11. **Operator notification.** Stale, unavailable, degraded, failed, or recovering services are explicit. Missing observations never appear healthy.
12. **Console continuity.** Console closure has no effect on independent services; reconnection reconstructs state from authenticated snapshots.

## Authentication and authorisation

A future private BFF terminates operator authentication, validates exact origin/host/session, applies server-side read permissions, sanitises responses, and records audit events. The browser is never an authority source. Process-control and trading permissions are distinct from read access and are not part of this foundation.

## Windows laptop limitations

Sleep, battery loss, user logoff, patch restarts, single-disk failure, local-account compromise, limited process isolation, and intermittent connectivity prevent the laptop from being treated as a highly available host. A future shortcut or approved launcher may start the supervisory application, but it must not bypass authentication, evidence gates, or safe recovery. This task does not create that launcher.

## Future dedicated-host option

A private dedicated host may later provide stronger service isolation, encrypted secrets, durable storage, monitored backups, controlled network egress, authenticated remote viewing, and higher availability. Remote access requires a separate threat model and security qualification; the local Vite server must never be exposed.

## Implementation roadmap

1. Qualify this snapshot foundation independently.
2. Approve numerical freshness policies and durable evidence semantics.
3. Design and qualify atomic snapshot persistence without adding network exposure.
4. Implement authenticated read-only BFF access with explicit unavailable/stale states.
5. Implement scheduler and heartbeat contracts in a non-operational test harness.
6. Qualify supervision, bounded restart, safe shutdown, backup, and known-clean recovery.
7. Pilot local background operation in PAPER ONLY research simulation after separate owner approval.
8. Evaluate an authorised launcher and, separately, private remote viewing.

Every step requires tests, independent qualification, rollback evidence, and owner acceptance. No step implies automatic live trading.

# M1 telemetry-degradation owner scope disposition

Decision ID: M1-SCOPE-2026-09-12-01. Received 2026-09-12.
Requirement: IMP-030-M1-07.
Lineage: hash-matched Implementation Specification v1.0; owner dispositions M1-SCOPE-2026-09-11-01, M1-SCOPE-2026-09-11-04 and M1-SCOPE-2026-09-11-05.
Specification SHA-256: 4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This records an explicit owner decision and is not verbatim canonical specification text.
Source SHA-256: 23d984873408c41617938684c82a33ec884a42f4182929be502ff7df565a1457
Historical origin (non-normative): explicit owner instruction received through Codex attachment ID `ac76cf4f-a7f7-480d-9ef0-365f34a7cca0`. The original machine-local path is not required for verification. Historical attachment SHA-256: `23d984873408c41617938684c82a33ec884a42f4182929be502ff7df565a1457`.

## Owner instruction (verbatim)

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
Continue M1 residual-findings closure.

Repository:
C:\Users\SK\Desktop\Automated Trading Bot

OWNER DECISION

The owner has explicitly APPROVED the proposed M1 telemetry-degradation scope for `IMP-030-M1-07`.

Treat the approved decision as authoritative from this point forward.

APPROVED M1 CONTRACT

For M1, the complete degradation-monitored telemetry source set is:

1. Canonical Clock condition.
2. Foundation diagnostic/log-delivery condition.

No other M1 telemetry/evidence capability requires independent degradation monitoring under IMP-030-M1-07.

CLOCK

Use the existing canonical ClockObservation states:

- AVAILABLE = positive.
- INVALID = explicit negative.
- UNAVAILABLE = explicit negative.

TELEMETRY DELIVERY

Use the existing foundation logging EmissionResult states:

- EMITTED = positive.
- CLOCK_FAILED = explicit negative.
- SINK_FAILED = explicit negative.

HEALTH EVALUATION

Each required observation must have:

- its canonical identity;
- typed status;
- canonical UTC observation timestamp.

The existing inclusive freshness rule applies unchanged and ONLY to these two health inputs:

0 <= evaluation_time - observed_at <= 60 seconds

When health evaluation is requested:

- missing required evidence -> UNKNOWN;
- stale evidence -> UNKNOWN;
- future-dated evidence -> UNKNOWN;
- malformed/unsupported evidence -> UNKNOWN or explicit rejection according to the existing contract, never HEALTHY;
- duplicate/conflicting/unattributable required evidence -> UNKNOWN;
- both observations fresh, valid and attributable, with either reporting an explicit negative condition -> DEGRADED;
- both observations fresh, valid, attributable and positive -> HEALTHY.

UNKNOWN is detectable uncertainty and must never be converted to HEALTHY merely because an explicit failure is absent.

M1 establishes no universal heartbeat or periodic observation schedule.

Silence outside an invoked health evaluation does not independently create a telemetry state.

RECOVERY

Recovery from UNKNOWN or DEGRADED to HEALTHY requires a newly attributable, complete, fresh, valid and positive observation set for BOTH monitored sources:

- Clock AVAILABLE;
- telemetry delivery EMITTED.

Absence, stale evidence or unresolved evidence cannot establish recovery.

No M1 history, hysteresis, debounce or multi-sample recovery requirement is introduced.

ALERT RELATIONSHIP

Existing alert evaluation may consume canonical Clock, telemetry-delivery and health outputs.

Explicit negative conditions may create active alerts.

Missing evidence and HealthState.UNKNOWN remain unresolved and must not fabricate either an alert or a clearance.

Alert absence is not evidence of health.

NON-INDEPENDENTLY-MONITORED M1 CAPABILITIES

The following remain valid M1 telemetry/evidence capabilities but do NOT require independent degradation monitoring under IMP-030-M1-07:

- metrics registry and counters;
- health evaluation output;
- alert evaluation output;
- event-causality evidence;
- machine-validatable milestone evidence.

Their silence is not independently classified as degradation because M1 defines no separate heartbeat, observation schedule, transport or freshness contract for them.

Health and alert outputs are derived results and must not create recursive monitoring requirements.

EXPLICIT DEFERRALS

The following remain outside IMP-030-M1-07 and are deferred to their appropriate later stages:

- telemetry-pipeline availability monitoring;
- monitoring of monitoring components;
- background telemetry collection;
- universal heartbeats;
- persistence;
- exporters;
- aggregation;
- dashboards;
- external telemetry transport;
- paging/incident integrations;
- production SLOs;
- trace-completeness monitoring;
- broker telemetry;
- OMS telemetry;
- execution telemetry;
- executable financial-workflow telemetry;
- Stage 9 integrated observability.

AUTHORITY BOUNDARY

All telemetry, health and alert outputs remain observational and non-authoritative.

They cannot:

- authorize trading;
- override a denial;
- alter OperationalState;
- mutate financial state;
- substitute for canonical events;
- substitute for audit evidence;
- substitute for ledger truth;
- substitute for reconciliation.

TASK

1. Record this owner decision in the appropriate M1 closure documentation with a new explicit owner-disposition identifier consistent with the existing repository convention.

2. Update traceability and assessment evidence for IMP-030-M1-07 to use this approved decision as attributable authority.

3. Inspect the existing implementation against every approved condition.

4. Do NOT change production code unless a specific approved acceptance condition is genuinely not satisfied.

5. Establish direct acceptance evidence for:

- positive/HEALTHY behavior;
- explicit Clock failure;
- explicit telemetry-delivery failure;
- missing evidence;
- stale evidence;
- future-dated evidence;
- malformed/unsupported evidence;
- duplicate/conflicting/unattributable evidence where applicable;
- inclusive 60-second freshness boundary;
- recovery;
- alert handling/resolution;
- deterministic evaluation;
- authority isolation.

6. Reuse existing tests wherever they directly prove the approved requirement.

Add only the smallest focused tests necessary for uncovered approved conditions.

7. If implementation satisfies the complete approved contract with sufficient evidence, move IMP-030-M1-07 from INSUFFICIENT_EVIDENCE to VERIFIED.

If an approved condition exposes a genuine implementation defect, do NOT force VERIFIED. Make only the smallest justified correction and test it.

8. Preserve all later-stage deferrals and existing architecture.

DO NOT

- broaden the approved contract;
- introduce additional monitored sources;
- build Stage 9 observability;
- introduce heartbeats;
- create recursive monitoring;
- resolve IMP-001-M1-02 during this task;
- start Stage 2;
- create the final M1 completion manifest;
- create the M1 completion commit.

TESTING

Run:

- directly mapped telemetry-degradation acceptance tests;
- monitoring/health/alert regressions;
- operational/authority isolation tests;
- closure traceability/integrity tests;
- full regression suite before claiming VERIFIED.

Run Git status/diff/whitespace checks.

STOP after IMP-030-M1-07 is fully assessed against the newly approved contract.

REPORT

1. Owner-disposition identifier created.
2. Files changed.
3. Approved contract mapping.
4. Existing implementation evidence for each acceptance condition.
5. Any implementation defect discovered.
6. Production changes, if any.
7. Tests added/changed.
8. Exact targeted test results.
9. Exact full-suite result.
10. Final IMP-030-M1-07 status.
11. Updated M1 counts.
12. Remaining unresolved findings.
13. Repository/diff status.
14. Confirmation that later-stage telemetry remains deferred.
15. Confirmation that IMP-001-M1-02 was not resolved, Stage 2 was not started, no final completion manifest was created, and no completion commit was made.

The owner decision is approved. Implement and evidence exactly this contract without expanding it.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

## Implementation assessment

The approved monitored-source set matches the existing HealthInput.CLOCK and HealthInput.TELEMETRY_DELIVERY model. Existing typed ClockObservation and EmissionResult states, the on-demand health evaluator, and alert evaluation implement the approved positive, explicit-negative, uncertainty, freshness, recovery, resolution, deterministic and authority boundaries. No production change is required. Metrics, derived health and alert outputs, event causality, and milestone evidence remain non-independently-monitored under this atom. All later-stage deferrals in the owner decision remain intact.
Provenance limitation: the individual text of R41-CI049, R41-CI050, and R41-CI051 remains unrecovered. That unrecoverable text is not substantive acceptance authority and is not used by this decision.

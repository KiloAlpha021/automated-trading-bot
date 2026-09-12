# M1 alert owner scope disposition

Decision ID: M1-SCOPE-2026-09-11-05. Received 2026-09-11.
Requirement: IMP-030-M1-06.
Lineage: Implementation Specification v1.0 sections 7, 10 and 11;
M1-SCOPE-2026-09-11-01, M1-SCOPE-2026-09-11-03 and M1-SCOPE-2026-09-11-04.
Specification SHA-256:
4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This owner contract is distinct from verbatim canonical specification text.
The previous interrupted request was read only; no alert files were created.
Source SHA-256: 067fa7f0832dcde4034c7762cba62b21a0c4f7796ddb849da02352143ed04a01
Historical origin (non-normative): explicit owner instruction received through Codex attachment ID `223b948f-9a8c-41a9-856e-cb172180e609`. The original machine-local path is not required for verification. Historical attachment SHA-256: `067fa7f0832dcde4034c7762cba62b21a0c4f7796ddb849da02352143ed04a01`.

## Owner instruction (verbatim)

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
Continue the M1 gate/closure verification from the current repository state.

CURRENT GAP:
`IMP-030-M1-06`

OWNER SCOPE DISPOSITION FOR M1 ALERT CONDITIONS

M1 requires deterministic executable alert-condition evaluation plus local immutable alert records.

M1 does NOT require external notification delivery.

AUTHORIZED M1 ALERT CONDITIONS

1. `CLOCK_CONDITION_ALERT`
   Trigger when the canonical M1 Clock condition is explicitly:
   - INVALID
   - UNAVAILABLE

   Do NOT trigger solely because a Clock observation is absent or stale. Missing/stale evidence belongs to health/telemetry-degradation semantics unless separately required.

2. `TELEMETRY_DELIVERY_ALERT`
   Trigger when the existing foundation telemetry/logging delivery outcome explicitly reports failure.

3. `HEALTH_STATE_ALERT`
   Trigger when the existing canonical M1 health evaluator returns:
   - DEGRADED

   Do NOT trigger this alert merely because health is UNKNOWN.
   UNKNOWN remains uncertainty and must not be converted into a false positive alert condition without separate telemetry-degradation authority.

ALERT SEVERITY

Use a dedicated alert severity model:

- `WARNING`
- `CRITICAL`

Mapping:

- CLOCK_CONDITION_ALERT:
  - INVALID -> CRITICAL
  - UNAVAILABLE -> CRITICAL

- TELEMETRY_DELIVERY_ALERT:
  - explicit delivery failure -> WARNING

- HEALTH_STATE_ALERT:
  - DEGRADED -> WARNING

Do not reuse logging severity as the alert model.

ALERT RECORD

Each triggered condition must produce an immutable local alert record containing only:

- fixed alert identity;
- fixed severity;
- canonical UTC evaluation timestamp;
- fixed condition/status identity;
- active state.

No:
- arbitrary text;
- payloads;
- raw exceptions;
- secrets;
- credentials;
- broker identifiers;
- account identifiers;
- uncontrolled dimensions.

M1 requires local record creation only.

No network delivery, persistence, paging, email, SMS, Slack, webhook, dashboard, or incident integration.

FRESHNESS / INPUT AUTHORITY

Alert evaluation must consume existing canonical M1 condition outputs.

Do not invent independent Clock, telemetry, or health semantics inside the alert layer.

Do not automatically reuse the health 60-second freshness boundary for all alerts.

For this atom:
- Clock alerts respond to explicit canonical Clock INVALID/UNAVAILABLE observations.
- Telemetry alerts respond to explicit delivery failure outcomes.
- Health alerts respond to the canonical health state returned by `evaluate_health(...)`.

If the authoritative input itself cannot be evaluated, the alert evaluator must not fabricate a positive or cleared state.

CLEARING / RECOVERY

M1 alerts are evaluated on demand.

An alert is active only when its canonical trigger condition currently exists.

Clear/recovery semantics:

- CLOCK_CONDITION_ALERT clears only after a new attributable canonical Clock condition is explicitly valid/available.
- TELEMETRY_DELIVERY_ALERT clears only after a new attributable successful delivery outcome.
- HEALTH_STATE_ALERT clears only after a new attributable health evaluation returns HEALTHY.

UNKNOWN is NOT sufficient to clear an existing HEALTH_STATE_ALERT.

Absence of evidence is NOT sufficient to clear any alert.

REPEAT / DEDUPLICATION

M1 does not require persistent alert history.

Within one evaluation result:
- emit at most one active record per alert identity;
- duplicate input observations must not multiply alert records.

Across separate evaluations:
- the evaluator may return the same active alert again if the condition still exists;
- this is deterministic reevaluation, not a new financial or control effect.

Do not implement notification-rate limiting, paging suppression, incident deduplication, debounce windows, persistence, or alert-storm infrastructure in M1.

AUTHORITY BOUNDARY

Alerts are NON-AUTHORITATIVE.

Alerts must NEVER:

- authorize trading;
- approve or deny an order;
- override Risk;
- override Compliance;
- alter OMS behavior;
- change OperationalState;
- mutate financial state;
- become ledger/accounting truth;
- become canonical event history;
- substitute for reconciliation;
- substitute for audit records.

An active CRITICAL alert does NOT independently halt trading in this atom.

Any future coupling between alerts and emergency controls requires separate canonical authority.

SECURITY

Alert records and validation errors must not expose:

- secrets;
- credentials;
- private keys;
- access tokens;
- arbitrary configuration values;
- raw exceptions;
- arbitrary payloads;
- uncontrolled identifiers.

Use fixed typed values and fixed error messages.

EXPLICITLY OUT OF SCOPE

Do NOT implement:

- email
- SMS
- Slack
- PagerDuty
- push notifications
- webhooks
- dashboards
- cloud monitoring
- incident management
- alert persistence
- alert transport
- paging escalation
- acknowledgement workflows
- operator clearing
- telemetry-degradation detection
- trading halt integration
- Stage 9 alert/incident infrastructure

TASK

1. Record this owner disposition verbatim in an attributable M1 alert-scope clarification artifact with specification lineage and source hash.
2. Inspect the current repository against this contract.
3. Implement only the smallest local deterministic alert-condition evaluator and immutable alert-record model required.
4. Reuse existing canonical monitoring outputs rather than duplicating their logic.
5. Do not broadly instrument unrelated components.
6. Add focused positive and adversarial tests.
7. Run targeted tests, appropriate regressions, and the full suite.
8. If verified, update only required traceability/checkpoint evidence.
9. Resume the atomic M1 assessment.
10. STOP immediately at the next genuine gap.

REQUIRED ACCEPTANCE TESTS

At minimum prove:

1. INVALID Clock -> active CRITICAL CLOCK_CONDITION_ALERT.
2. UNAVAILABLE Clock -> active CRITICAL CLOCK_CONDITION_ALERT.
3. valid/available Clock -> no active Clock alert.
4. telemetry delivery failure -> active WARNING TELEMETRY_DELIVERY_ALERT.
5. successful telemetry delivery -> no active telemetry alert.
6. DEGRADED health -> active WARNING HEALTH_STATE_ALERT.
7. HEALTHY health -> no active health alert.
8. UNKNOWN health does not create HEALTH_STATE_ALERT.
9. UNKNOWN health does not clear a previously active health condition without new attributable HEALTHY evidence where stateful comparison is tested.
10. duplicate inputs do not multiply alert records in one evaluation.
11. missing evidence does not fabricate a cleared state.
12. severity cannot be caller-overridden.
13. arbitrary alert identities are rejected.
14. arbitrary fields/payloads are rejected.
15. protected values cannot enter records.
16. raw exception text cannot enter records.
17. repeated evaluation has no financial side effects.
18. alerts do not mutate OperationalState.
19. alerts do not change expiry/decision outcomes.
20. alerts do not weaken replay denial.
21. CRITICAL alert does not itself grant or remove trading authority.
22. output ordering is deterministic if multiple alerts are active.

STRICT RULES

- DO NOT implement telemetry-degradation detection in this task.
- DO NOT wire alerts to kill switches or trading halts.
- DO NOT add transport or external notification infrastructure.
- DO NOT modify logging, metrics or health semantics unless a separately proven defect exists.
- DO NOT duplicate canonical evaluation logic inside alert code.
- DO NOT introduce third-party dependencies.
- DO NOT refactor unrelated production code.
- DO NOT start Stage 2 or later work.
- DO NOT commit.

CAPITAL-SAFETY STANDARD

Alerts must improve operator visibility without acquiring authority.

No alert—WARNING or CRITICAL—may independently cause capital movement, authorize trading, or alter canonical financial state.

REPORT

1. Owner disposition artifact.
2. Alert API/model implemented.
3. Alert identities.
4. Severity mapping.
5. Trigger semantics.
6. Clear/recovery semantics.
7. Repeat/deduplication behavior.
8. Security/leakage protections.
9. Authority-isolation evidence.
10. Positive/adversarial tests.
11. Files changed.
12. Exact targeted/regression/full-suite results.
13. Whether `IMP-030-M1-06` is VERIFIED.
14. Updated atomic assessment counts.
15. Next genuine gap.
16. M1 closure status.
17. Git status.

Do not perform work beyond this scope.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

## Implementation interpretation and limits

`evaluate_alerts(conditions, clock=clock)` takes a tuple of current canonical
ClockObservation, EmissionResult and HealthState outputs. Their exact enum types
identify the input, so arbitrary alert names or raw diagnostic text are never
accepted as evidence. Producers/callers must supply newly attributable current
outputs, not relabel retained historical results as new observations. The alert
layer does not poll, recreate health logic or authenticate arbitrary callers.
No independent age threshold or copy of the health 60-second policy is applied.

The immutable result separates `active` records, `cleared` identities (explicit
positive evidence permitting clearance), and `unresolved` identities. An empty
active tuple is NOT an all-clear signal. Missing inputs and HealthState.UNKNOWN
are unresolved, never cleared. Previously returned active records remain
immutable; no evaluator-owned active registry, history, automatic clearing or
stateful recovery comparison is implemented. A consumer retaining prior alerts
must not remove them for unresolved identities. This component has no such
consumer or control coupling. Each later call requires current evidence; the
caller is responsible for attribution across calls.

AVAILABLE / EMITTED / HEALTHY are the respective explicit clearing observations.
INVALID or UNAVAILABLE Clock conditions trigger CRITICAL. SINK_FAILED and
CLOCK_FAILED are explicit failed logging-delivery outcomes and trigger WARNING;
CLOCK_FAILED does not independently fabricate a Clock condition. DEGRADED health
triggers WARNING. UNKNOWN health produces no new active record and no clearance.

Exact repeated inputs collapse to one observation. Conflicting statuses for the
same identity reject the entire evaluation with a fixed error before returning
any records or clearances; no latest-write-wins policy is invented. Outputs are
ordered CLOCK_CONDITION_ALERT, TELEMETRY_DELIVERY_ALERT, HEALTH_STATE_ALERT.
Each active record contains only identity, severity, evaluated_at, condition,
and active=True. Severity and active state are derived, never caller-overridden.
UTC timestamps come from one injected Clock read and canonical Timestamp
validation. An invalid/unavailable evaluation clock raises a fixed error and
returns no synthetic evaluation/clearance. Ordinary exceptions are not exposed.

Alerts have no transport, persistence, acknowledgement or financial authority.
No existing monitoring or business component is changed. Behavioral evidence:
tests/test_alerts.py. This specification and its tests do not claim full M1 closure.

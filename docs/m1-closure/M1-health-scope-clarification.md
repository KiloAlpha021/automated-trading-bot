# M1 health owner scope disposition

Decision ID: M1-SCOPE-2026-09-11-04. Received 2026-09-11.
Requirement: IMP-030-M1-05.
Lineage: Implementation Specification v1.0 sections 7, 10 and 11;
M1-SCOPE-2026-09-11-01 and M1-SCOPE-2026-09-11-03.
Specification SHA-256:
4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This records an owner-defined M1 contract, not verbatim specification text.
Source SHA-256: e8d9047aa6fc63dbc5c488e6925981b68c1212cd3e20d2094555135807db15d6
Historical origin (non-normative): explicit owner instruction received through Codex attachment ID `3a005556-1c35-4bfa-86d5-4876279d0170`. The original machine-local path is not required for verification. Historical attachment SHA-256: `e8d9047aa6fc63dbc5c488e6925981b68c1212cd3e20d2094555135807db15d6`.

## Owner instruction (verbatim)

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
Continue the M1 gate/closure verification from the current repository state.

CURRENT GAP:
`IMP-030-M1-05`

OWNER SCOPE DISPOSITION FOR M1 HEALTH:

M1 requires a minimal executable health-condition evaluator for the M1 engineering foundation.

This is NOT a trading-readiness system, financial authority system, alerting system, or production health platform.

AUTHORIZED M1 HEALTH CONTRACT

1. HEALTH STATE VOCABULARY

Create a health-specific state model distinct from OperationalState:

- `UNKNOWN`
- `HEALTHY`
- `DEGRADED`

Do not reuse or reinterpret existing OperationalState values.

Health state is observational only.

2. REQUIRED M1 HEALTH INPUTS

The minimum required M1 inputs are:

A. Canonical Clock condition
- Derived from the existing canonical Clock/clock-observation semantics.
- Must distinguish valid/available from invalid/unavailable behavior.
- Must not introduce external time sources, NTP, exchange calendars, drift services, or Stage 3 functionality.

B. Foundation telemetry-delivery condition
- Derived only from the existing M1 logging/telemetry delivery outcome semantics.
- Successful delivery is a positive observation.
- Explicit sink/delivery failure is a degraded observation.
- No raw exception data may enter health evaluation.

Do not add additional dependencies simply to make the health model appear richer.

3. INPUT ATTRIBUTION

Each health input used for evaluation must have:
- a fixed input identity;
- a canonical UTC observation timestamp;
- a typed fixed status;
- no arbitrary payload;
- no free text;
- no secrets or protected values.

4. FRESHNESS

For M1 only, a required health input is fresh when:

`0 <= evaluation_time - observed_at <= 60 seconds`

using the canonical Clock/Timestamp rules.

The 60-second value is an M1 deterministic acceptance boundary only.

It is NOT a production SLO and must not be propagated to later stages as production policy without separate authority.

Future-dated observations are invalid for health determination.

5. EVALUATION SEMANTICS

Return `UNKNOWN` when ANY required input is:
- absent;
- stale;
- future-dated;
- malformed;
- unsupported;
- contradictory/conflicting;
- otherwise not attributable.

Missing telemetry must NEVER establish health.

Return `DEGRADED` when:
- every required input is present and fresh;
- but at least one required condition reports an explicit failure, invalidity, or unavailability.

Return `HEALTHY` only when:
- every required input is present;
- every required input is fresh;
- every required input is valid;
- every required condition is explicitly positive.

No optimistic default is permitted.

6. RECOVERY SEMANTICS

Recovery from `UNKNOWN` or `DEGRADED` to `HEALTHY` requires fresh, valid, positive observations for ALL required inputs.

No historical failure may be silently overwritten without a new attributable positive observation.

M1 does not require hysteresis, debounce windows, persistence, or multi-sample recovery thresholds.

7. COMPUTATION MODEL

M1 health is computed on demand from the supplied current observations.

No persistent health-state store is required.

No background worker is required.

No timer thread is required.

No network endpoint is required.

No orchestration integration is required.

8. AUTHORITY BOUNDARY

Health is NON-AUTHORITATIVE.

Health must NEVER:
- authorize trading;
- approve an order;
- override a denial;
- change a risk decision;
- change a compliance decision;
- change OMS behavior;
- mutate OperationalState;
- mutate ledger/accounting truth;
- become canonical event history;
- substitute for reconciliation;
- substitute for audit records.

`HEALTHY` means only that the defined M1 health inputs are currently positive and fresh.

It does NOT mean the trading system is safe or authorized to trade.

9. SECURITY

Health inputs and outputs must not contain:
- secrets;
- credentials;
- tokens;
- private keys;
- arbitrary configuration values;
- broker payloads;
- free-text exception messages;
- uncontrolled identifiers.

Use fixed typed states and fixed errors.

10. EXPLICITLY OUT OF SCOPE

Do NOT implement in this atom:
- alerts;
- telemetry degradation detection;
- trading readiness;
- market-session health;
- broker health;
- database health;
- external dependency health;
- dashboards;
- Prometheus;
- OpenTelemetry;
- exporters;
- cloud monitoring;
- SLOs;
- persistent health history;
- orchestration/liveness/readiness endpoints;
- Stage 3 market/data health;
- Stage 9 trace-completeness monitoring.

TASK

1. Record this owner disposition verbatim in an attributable M1 health-scope clarification artifact with governing-source lineage.
2. Inspect the current repository against this contract.
3. Implement only the smallest local health evaluator required.
4. Reuse existing Clock/Timestamp, logging-delivery and typed monitoring contracts where appropriate.
5. Do not duplicate canonical validation logic.
6. Do not broadly integrate health into unrelated components.
7. Add focused positive and adversarial tests.
8. Run targeted tests, appropriate regressions and the full suite.
9. If verified, update only required traceability/checkpoint evidence.
10. Resume the atomic M1 assessment.
11. STOP immediately at the next genuine gap.

REQUIRED ACCEPTANCE TESTS

At minimum prove:

1. no observations -> UNKNOWN;
2. one required observation missing -> UNKNOWN;
3. stale required observation -> UNKNOWN;
4. future-dated required observation -> UNKNOWN;
5. malformed/unsupported observation -> UNKNOWN or explicit rejection without optimistic health;
6. fresh valid Clock + successful telemetry delivery -> HEALTHY;
7. fresh unavailable/invalid Clock -> DEGRADED;
8. fresh telemetry delivery failure -> DEGRADED;
9. recovery requires new fresh positive observations for all required inputs;
10. evaluation uses canonical UTC Clock/Timestamp rules;
11. freshness boundary at exactly 60 seconds is deterministic;
12. observation older than 60 seconds cannot establish HEALTHY;
13. health evaluation does not alter OperationalState;
14. health evaluation does not change decision/order/risk behavior;
15. protected values cannot enter health inputs;
16. raw exception text cannot enter health inputs/output;
17. repeated evaluation has no financial side effects;
18. HEALTHY cannot be interpreted by the health component as authorization to trade.

STRICT RULES

- DO NOT add alerts.
- DO NOT add telemetry-degradation detection.
- DO NOT modify metrics semantics.
- DO NOT redesign foundation logging.
- DO NOT add external monitoring infrastructure.
- DO NOT create trading readiness logic.
- DO NOT connect health to order authority.
- DO NOT modify OperationalState.
- DO NOT introduce dependencies unless proven necessary.
- DO NOT refactor unrelated code.
- DO NOT start Stage 2 or later work.
- DO NOT commit.

CAPITAL-SAFETY STANDARD

This system may eventually control real capital.

The health component must therefore fail conservatively:
uncertainty must never be converted into confidence, and HEALTHY must never be converted into permission to trade.

REPORT

1. Owner disposition artifact.
2. Health API/state model implemented.
3. Required health inputs.
4. Freshness semantics.
5. Evaluation rules.
6. Recovery rules.
7. Authority isolation proof.
8. Security/leakage protections.
9. Positive and adversarial tests.
10. Files changed.
11. Exact targeted/regression/full-suite results.
12. Whether `IMP-030-M1-05` is VERIFIED.
13. Updated atomic assessment counts.
14. Next genuine gap.
15. M1 closure status.
16. Git status.

Do not perform work beyond this scope.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

## Implementation and limits

`evaluate_health(observations, clock=clock)` accepts a tuple containing exactly
one immutable HealthObservation for CLOCK and one for TELEMETRY_DELIVERY.
All duplicate identities, even identical duplicates, are rejected as UNKNOWN;
there is no last-write-wins selection or aggregation of conflicting evidence.
Input timestamps are revalidated through the canonical Timestamp constructor.
Evaluation obtains one UTC Timestamp from the injected Clock. Clock exceptions
or invalid evaluation time yield UNKNOWN without formatting exceptions.

CLOCK uses the existing ClockObservation enum: AVAILABLE is positive; INVALID
and UNAVAILABLE are explicit negative conditions. TELEMETRY_DELIVERY uses the
existing EmissionResult enum: EMITTED is positive; SINK_FAILED and CLOCK_FAILED
are negative delivery outcomes. CLOCK_FAILED means no diagnostic was delivered,
not an independently trustworthy clock-health observation. Both required input
identities remain necessary. A clock-condition failure with no attributable UTC
observation time is insufficient evidence (UNKNOWN), not a timestamp invented
by this component.

The caller supplies timestamped current observations from existing monitoring
outcomes. No implicit polling, health registry, history, background collection
or authenticity mechanism is added. These typed observations attest structure,
not trustworthiness of an arbitrary caller. A trusted producer must timestamp
an actual observation; an old snapshot must not be relabeled with a new time.

HealthState is separate from OperationalState and returns only UNKNOWN, HEALTHY
or DEGRADED. Uncertainty takes precedence over explicit negative conditions.
The 60-second inclusive window is solely the owner-defined M1 boundary. Recovery
requires a supplied complete fresh positive observation set; inputs are immutable
and prior negative records are never modified or implicitly removed. With no
history store, no cross-evaluation ordering or multi-sample recovery is claimed.

No health output carries trading permission. There are no consumers in business
or authorization code. Logging, metrics, operational modes, decision semantics,
replay denial and canonical financial truth remain unchanged. This component
does not implement alerts or required-telemetry degradation detection. Behavioral
evidence is in tests/test_health.py; full M1 closure remains a separate gate.

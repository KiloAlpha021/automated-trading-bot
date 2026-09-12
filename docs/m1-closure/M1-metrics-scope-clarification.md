# M1 metrics owner scope disposition

Decision ID: M1-SCOPE-2026-09-11-03. Owner instruction received 2026-09-11.
Requirement: IMP-030-M1-04.

Lineage: Implementation Specification v1.0 sections 7, 10 and 11;
M1-SCOPE-2026-09-11-01. Specification SHA-256:
4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

SHA-256: 7a0d3329eedbfb36a0897d1f59895626a96fd21c769a7e4de49d459d9f7a80e3
Historical origin (non-normative): explicit owner instruction received through Codex attachment ID `2aa59a46-92ba-4474-8329-45733be12503`. The original machine-local path is not required for verification. Historical attachment SHA-256: `7a0d3329eedbfb36a0897d1f59895626a96fd21c769a7e4de49d459d9f7a80e3`.

The following is owner wording, not verbatim canonical specification text.

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
OWNER SCOPE DISPOSITION FOR M1 METRICS:

M1 requires a minimal executable observational metrics foundation.

The authorized M1 metric set is limited to:

1. `clock_health_observation`
   - Purpose: record deterministic observations of canonical Clock availability/validity where M1 components explicitly perform such observation.
   - This metric does NOT establish overall system health.
   - Health-state determination remains a separate M1 obligation.

2. `expired_decision_count`
   - Purpose: count decisions rejected specifically because canonical expiry semantics determine they are expired.
   - Increment only on the attributable expiry-rejection observation.
   - Do not infer expiry from timestamps independently of the canonical decision logic.

3. `no_trade_decision_count`
   - Purpose: count explicit NO-TRADE decisions only where an M1-scoped canonical decision contract already emits or represents NO-TRADE.
   - Do not invent a new NO-TRADE decision path merely to populate this metric.
   - If no existing M1 canonical NO-TRADE observation exists, record that exact limitation rather than adding unrelated strategy functionality.

GENERAL METRIC SEMANTICS:

- Metrics are observational and NON-AUTHORITATIVE.
- Metrics are process-local and in-memory for M1.
- Metrics are not persistent.
- Metrics are not reconstructive financial truth.
- Metrics do not grant, deny or modify trading authority.
- Metrics cannot alter risk, compliance, OMS, execution or accounting outcomes.
- Metrics cannot substitute for canonical events, audit records, ledger state or reconciliation.
- Metric failure must not convert a denied action into an allowed action.
- Metric observation must not create financial side effects.

LIFECYCLE / AGGREGATION:

- Counter metrics are monotonic within the lifetime of a metric registry instance.
- Counters begin at zero.
- No reset operation is required or authorized within an active registry lifetime.
- A newly constructed registry starts a new process-local metric lifecycle.
- No cross-process aggregation is required.
- No persistence across restart is required.

CLOCK METRIC SEMANTICS:

- Clock observations must use the existing canonical Clock abstraction.
- UTC validity must follow existing canonical timestamp rules.
- Do not derive broader health status from this metric.
- Do not add drift monitoring, NTP integration, external time sources or alerting in this atom.

DIMENSIONS / LABELS:

Use only explicitly defined, low-cardinality, allowlisted dimensions.

No arbitrary strings, free-text labels, payload-derived dimensions, secrets, account identifiers, broker payloads or uncontrolled identifiers.

Do not create unbounded-cardinality metrics.

INVALID OBSERVATIONS:

- Invalid metric identity, invalid dimension, invalid value or unsupported observation must fail explicitly.
- Invalid observations must not mutate metric state.
- Do not silently coerce invalid values.
- Do not silently create unknown metrics.

ACCEPTANCE EVIDENCE:

M1 verification must prove at minimum:

1. registry starts deterministically;
2. known metrics can be observed;
3. counters increment exactly once per attributable observation;
4. expired-decision counting is tied to canonical expiry rejection;
5. NO-TRADE counting occurs only for an existing canonical NO-TRADE observation, if applicable;
6. clock observations use the canonical Clock;
7. unknown metrics are rejected;
8. unsupported dimensions are rejected;
9. invalid observations do not mutate state;
10. counters cannot decrement;
11. arbitrary labels/payloads are rejected;
12. protected values cannot enter metric dimensions;
13. metrics do not alter business/authority outcomes;
14. metric failure cannot create duplicate financial effects;
15. construction of a new registry provides a clean process-local lifecycle.

EXPLICITLY OUT OF SCOPE FOR THIS ATOM:

- health-state implementation
- alert conditions
- required-telemetry degradation detection
- Prometheus
- OpenTelemetry
- dashboards
- exporters
- cloud monitoring
- tracing
- persistent metrics
- cross-process aggregation
- SLO calculation
- Stage 9 trace completeness
- production telemetry transport
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

## Implementation interpretation

All three metrics apply. NoTrade is already an explicit canonical record.
No new decision path is introduced. Clock observations are AVAILABLE, INVALID,
or UNAVAILABLE; these are observations, not system-health states. Before the
first observation the snapshot contains None, never implicit health.
No dimensions are accepted. Counts represent observation calls, not globally
unique decisions; no identifiers, payloads or secret values are retained.
The registry is local to a serial caller; no concurrent or cross-process
aggregation is claimed. Snapshots are immutable; there is no reset API.

MetricsRegistry.observe takes a fixed Metric enum and the exact required
canonical inputs. It does not accept increments, labels or arbitrary payloads.
Clock observations validate through Timestamp. Expiry observation delegates
to Approval.matches_current_proposal with an optional expiry-only callback.
The callback occurs once only when all non-expiry match predicates hold and
expiry causes rejection; ordinary callback failures are contained by the
canonical check, preserving False. Other rejection reasons are not counted.
An expiry observation returns the existing boolean match result, which remains
non-authoritative. NoTrade observation counts an existing NoTrade record only.
No business record or callback is retained. Bad inputs raise fixed TypeError
messages before mutation; no input value is formatted. This API is not a
sandbox for malicious injected Clock implementations or reflective mutation.

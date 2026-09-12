# M1 foundation logging

Engineering implementation record for IMP-030-M1-03, not a completion manifest.
Lineage: Implementation Specification v1.0 section 9 Stage 1 (logging), section
10 (structured logs), section 8 secrets restriction; owner disposition
M1-SCOPE-2026-09-11-01 IMP-030. Specification SHA-256:
4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.
The repository-contained implementation scope recorded here is a local primitive
without broad call-site integration, governed by M1-SCOPE-2026-09-11-01 and the
specification lineage above. Historical origin (non-normative): owner instruction
received through Codex attachment ID `f64f700f-c6c8-4b7d-91dc-b1722f13ba9b`.
The original machine-local attachment is not required for verification. This
document is an engineering implementation record, not verbatim specification.

## API and data boundary

`DiagnosticLogger(clock, sink).emit(code, context)` emits one deterministic JSON
string to an injected diagnostic-only `Callable[[str], None]`. It has no global
configuration, transport, persistence, retries or business-object input.
`DiagnosticContext` is immutable: component is exactly Component.DOMAIN, REPLAY,
or EVIDENCE; optional correlation_id, causation_id and event_id are exactly the
canonical CorrelationId, CausationId and EventId wrappers containing UUID values.
These identify existing context; emission neither creates nor records an event.
No arbitrary text, payload dictionary, exception, configuration, idempotency string,
credential or secret handle is accepted. Unknown field names and unsupported
values raise fixed TypeError messages without representing supplied data.
Representations of the logger and context omit supplied values and dependencies.
Callers must use genuine public identifiers, never encode protected data in IDs.

| Code | Severity | Meaning |
| --- | --- | --- |
| COMPONENT_READY | INFO | Local initialization completed; no assertion of health or authority |
| INPUT_REJECTED | WARNING | Existing validation rejected input; logging does not perform the decision |
| OPERATION_FAILED | ERROR | Local operation failed; no raw exception details |

Severity cannot be caller-overridden. Each record has exactly kind,
authoritative, code, severity, timestamp and context. `kind=operational_diagnostic`
and `authoritative=false` distinguish it from canonical event/audit/ledger data.
Timestamps come solely from the injected canonical Clock, validated as UTC and
normalized to +00:00. JSON keys are sorted with fixed separators; absent optional
identifiers are omitted. No message/payload/stack-trace extension field exists.

## Failure and authority

EMITTED means only that the sink returned normally, not durable delivery or health.
CLOCK_FAILED means the clock raised an ordinary exception or returned invalid
UTC data; the sink is not called. SINK_FAILED means an ordinary sink exception
occurred; delivery may have happened before failure. Neither case retries,
rethrows exception text, writes elsewhere nor invokes a business operation.
Process-control BaseException subclasses are not intercepted. Injected clocks and
sinks are trusted application dependencies; this is not a sandbox for malicious
callbacks. Sinks must be diagnostic-only and cannot be financial adapters.

The caller receives an explicit result independent of its existing control outcome.
No log or return status grants permission, changes a decision, substitutes for
reconciliation or becomes canonical audit/event/accounting truth. Required
call-site propagation/containment/escalation policy remains unresolved and is
not invented here. No integration is claimed. Failure cannot count as health.
Metrics, health/alert conditions and telemetry degradation detection are separate
owner-required atoms, not provided by this primitive. Full financial-workflow
telemetry, persistent reconstruction lineage and Stage 9 trace-completeness SLO
remain deferred exactly as recorded by the owner.

Behavioral evidence: tests/test_diagnostics.py. No external infrastructure used.

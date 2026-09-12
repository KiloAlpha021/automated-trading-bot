# M1 core-contract serialization and property-test owner disposition

Decision ID: M1-SCOPE-2026-09-12-03. Received 2026-09-12.
Lineage: hash-matched Implementation Specification v1.0 section 11 M1.3.
Specification SHA-256: 4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This records an explicit owner decision and is not verbatim specification text.

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
M1-SCOPE-2026-09-12-03 approves the M1.3 serialization and property-test contract.

Types already crossing an existing M1 event or evidence boundary must preserve
their typed value through that boundary's canonical serialization round trip.
Types with an existing explicit serialization surface require direct,
deterministic round-trip and malformed-input tests. Pure value objects without
an existing canonical serialization boundary do not gain new public
serialization APIs solely for M1. Money and Quantity therefore require no new
standalone serializers during M1.

Money and Quantity require deterministic invariant evidence for exact Decimal
or value preservation, strict type rejection without coercion, precision
preservation, stable equality, immutability and, for Money, Currency association.
Deterministic parameterized or generated invariant tests satisfy the property
test requirement; Hypothesis is not required.

Typed UUID identifiers must preserve UUID values, remain distinct by wrapper
type, reject non-UUID values and remain immutable. EventId, CorrelationId and
CausationId must round trip where used by EventEnvelope. Timestamp accepts only
timezone-aware zero-offset datetime values, rejects naive, undefined-offset,
non-UTC and non-datetime values, remains immutable and preserves its instant
through the EventEnvelope round trip. Currency accepts exactly three ASCII
uppercase letters, rejects malformed and non-string values, remains immutable,
has stable equality and round trips through its existing string representation.

M1 does not add accounting arithmetic, FX conversion, authoritative ISO registry
integration, minor-unit enforcement, business rounding policy, instrument-master
lookup, lot-size or tick-size enforcement, portfolio or order-context sign rules,
standalone universal serializers or primitive schema versioning. Existing
acceptance of zero and negative Decimal primitive values remains unchanged and
does not authorize a financial operation. Context-dependent Money rounding, ISO
membership, Quantity increment and precision, and contextual validity remain for
their later owning contracts.

This decision authorizes only M1.3 atomic traceability remediation and the
smallest missing acceptance tests. It does not authorize M1.7 remediation,
Stage 0B closure, hosted branch-protection changes, M1 completion, Stage 2,
trading authority, a replacement completion manifest, a tag or a push.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

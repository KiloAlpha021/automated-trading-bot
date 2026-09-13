# M1 financial-primitive finiteness owner disposition

Decision ID: M1-SCOPE-2026-09-13-01. Received 2026-09-13.
Lineage: hash-matched Implementation Specification v1.0 section 11 M1.3 and
M1-SCOPE-2026-09-12-03.
Specification SHA-256: 4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This records an explicit owner decision and is not verbatim specification text.

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
M1-SCOPE-2026-09-13-01 clarifies the structural Decimal validity required by
the M1.3 contract established by M1-SCOPE-2026-09-12-03.

Money.amount and Quantity.value must each be a Decimal and must be finite.
Decimal NaN, signaling NaN, positive infinity and negative infinity are invalid
and must be rejected deterministically with ValueError. Finite positive values,
finite negative values, positive zero, signed negative zero and arbitrary finite
precision remain accepted. Accepted Decimal objects must not be coerced or
altered.

This clarification preserves strict type rejection, exact Decimal preservation,
immutability, stable equality and Money/Currency association. It defines no
positivity, non-negativity, maximum magnitude, decimal-place, instrument
increment, lot-size, tick-size, rounding, currency minor-unit, FX, order or
position-context rule.

This decision authorizes only the smallest Money/Quantity validation, acceptance
tests and attributable M1.3 traceability updates needed to enforce this
clarification. It grants no trading authority, does not close M1, does not
authorize Stage 2 and does not change authorization from NONE. M1 remains
REOPENED / BLOCKED.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

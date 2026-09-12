# M1 calendar/expiry scope clarification

Decision ID: M1-SCOPE-2026-09-11-02
Authority: explicit project-owner message in the current project conversation,
2026-09-11, headed OWNER SCOPE DISPOSITION FOR `IMP-004-M1-02`.
Affected requirement: IMP-004-M1-02.

This is an attributable owner applicability decision, not verbatim wording from
the Implementation Specification. It resolves the previously open M1 calendar
scope question without changing UTC/expiry semantics or financial authority.

## Canonical lineage

Implementation Specification v1.0 sections 7 (IMP-004), 11 (M1.4), 9
(Stage 3), 13 (time verification) and 14 (GATE-S03-01).
Specification SHA-256:
`4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7`.
The existing IMP-026/029/030 decision M1-SCOPE-2026-09-11-01 is unchanged.
Original source documents and prior assessment history are preserved.

## Owner disposition (verbatim scope text)

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
For M1, the required calendar/expiry acceptance scope is limited to:

1. UTC-only deterministic clock behaviour.
2. Deterministic expiry behaviour.
3. Deterministic calendar-boundary behaviour sufficient to prove expiry semantics across date boundaries.

M1 does NOT require implementation of the broader exchange-calendar service.

The following remain deferred to Stage 3:

- exchange holiday calendars
- exchange session/open-close semantics
- early-close handling
- DST-aware exchange-session behaviour
- leap-date exchange-calendar policy beyond generic deterministic date handling
- calendar point-in-time integrity
- calendar freshness/quarantine
- market-session dependency health
- complete exchange calendar/session/holiday/early-close service

This disposition does NOT waive the explicit M1 requirement for deterministic expiry/calendar tests. It defines the minimum M1 subset and preserves the broader canonical Stage 3 responsibility.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

## Assessment at this decision

Scope is resolved. Verification remains INSUFFICIENT_EVIDENCE.
The previous checkpoint's scope-ambiguity description is historical and is
superseded by this decision; it is not grounds to require Stage 3 behaviour.
No previous VERIFIED status is newly claimed by this document.

Inspected `domain/clock.py`, `domain/timestamp.py`, `domain/decision.py` and
`tests/test_clock.py`, `tests/test_timestamp.py`, `tests/test_decision.py`.
`test_test_clock_can_advance_across_day_boundary` checks UTC date arithmetic,
but does not invoke decision expiry. Independent proposal/approval expiry tests
use timestamps on the same date. Neither proves expiry across a date boundary.
`Approval.matches_current_proposal` compares full UTC instants using half-open
validity windows; no implementation defect was established.

Targeted command:
`.venv/Scripts/python.exe -m pytest -q tests/test_clock.py tests/test_timestamp.py tests/test_decision.py::test_proposal_expiry_independently_limits_approval tests/test_decision.py::test_approval_expiry_independently_limits_proposal`

Observed result on 2026-09-11: 31 passed, 0 failed. These passing tests do not
close the missing combined calendar-boundary/expiry verification.

Smallest missing evidence: exercise the existing Clock/TestClock and decision
contracts across a UTC date boundary, proving validity before expiry and
rejection at/after expiry, including independent proposal and approval expiry.
No exchange-calendar infrastructure is required. No missing test was added:
the owner's instruction requires stopping when this specific gap is found.

M1 remains BLOCKED. Assessed status counts remain 3 VERIFIED and
2 INSUFFICIENT_EVIDENCE (5 records); IMP-004-M1-02 is not VERIFIED.

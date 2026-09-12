# M1 engineering-foundation owner disposition

Decision ID: M1-SCOPE-2026-09-12-02. Received 2026-09-12.
Lineage: hash-matched Implementation Specification v1.0 M1.1 and M1.2.
Specification SHA-256: 4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7.

This records an explicit owner decision and is not verbatim specification text.

<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
M1-SCOPE-2026-09-12-02 approves the M1.1/M1.2 engineering-foundation contract.

M1 requires exact CPython 3.12.10 metadata and enforcement; the established
source, test and package layout; an exact hash-locked development dependency set;
and one documented clean bootstrap command independent of an existing environment.

M1 requires fatal repository controls for Ruff E4/E7/E9/F over src, tests and
scripts; strict mypy over src/automated_trading_bot; pytest; coverage measurement
with terminal missing-line and XML reports but no numeric threshold; pinned
pip-audit with advisory retrieval/evaluation failure treated as failure; and the
existing pattern-bounded secret scanner with its stated non-universal limitation.

A clean-clone CI workflow must expose the stable status
`m1-engineering-foundation` and run the canonical bootstrap/check path without
ignored failures. Hosted branch-protection enforcement requires attributable
external evidence and remains unresolved when that evidence is unavailable.

These obligations are decomposed into separate M1.1/M1.2 traceability atoms.
This decision does not authorize M1.3, M1.7, Stage 0B completion, Stage 2,
trading authority, a replacement completion manifest, a push or a tag.
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

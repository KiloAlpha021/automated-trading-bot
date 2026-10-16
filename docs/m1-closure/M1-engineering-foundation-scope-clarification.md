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

## Additive engineering lifecycle clarification

This clarification does not alter the owner decision above. Engineering validation
uses the repository-owned Draft 2020-12 execution contract and distinguishes two
subjects. `PRE_SEAL_WORKING_TREE` evidence identifies exact working bytes and may
establish readiness for a separately authorized local seal. It cannot establish a
committed subject, protected certification, trusted certification, publication,
merge, acceptance, Stage authority, or trading authority.

`POST_SEAL_IMMUTABLE_COMMIT` evidence identifies an exact commit, tree and parent,
uses a clean isolated installed distribution, and requires installed module bytes
to equal committed source bytes. Hosted and trusted certification and owner
acceptance remain separate obligations. Negative, interrupted, incomplete and
superseded evidence remains attributable and must not be overwritten or promoted
to a broader claim.

The protected runner rejects an unestablished repository, workspace, interpreter,
dependency lock, candidate attribution, temporary state, evidence destination,
changed-path scope, or validation profile before validation begins. The owner
authorization reference is attributable metadata and is not cryptographic proof
of owner identity. The engineering hold and all existing authority exclusions
remain effective unless the owner explicitly changes them.

## Consolidated runner evidence and external output clarification

The runner writes a separate canonical `ATIS_RUNNER_EXECUTION_V1` record in
`runner.json` with a SHA-256 sidecar. It binds the execution contract, interpreter,
controlled environment, initial/final candidate and dependency identities, ordered
mandatory controls, native command arguments and exit codes, captured output,
unreached controls (`NOT_STARTED`), terminal disposition, evidence digests and
outstanding certification obligations. In-process checks have no invented native
exit code. A pytest pass followed by an audit failure is an overall runner failure.
An interrupted process without terminal evidence remains incomplete, never PASS.

`execution-evidence/capture.json` remains pytest-scoped. Its historical
`ATIS_PYTEST_EXECUTION_V1` archive location and exact legacy/post-seal field set
remain compatible with existing Stage 3 consumers. Pre-seal capture extensions
retain their existing meaning. Runner certification must use the separate runner
summary; historical pytest evidence alone cannot establish all mandatory controls.

The live vulnerability control requires pip-audit 2.10.1 and an explicit fresh,
absolute, external cache with create/write/list/rename/read/delete capability.
No prior advisory cache is reused. Vulnerabilities and advisory/network failures
propagate their native nonzero results and retain stdout/stderr. Neither an
offline assumption nor separate historical audit success substitutes for this run.

Coverage data/XML, audit/Ruff/mypy/Python caches, pytest state and pytest evidence
are external to the source checkout. Bytecode writing and pytest's checkout cache
are disabled for controlled children; isolated source probes also disable bytecode.
The hosted artifact uploads the complete external evidence root, including hidden
coverage data, preserving the nested historical capture member. Each runner uses
a new evidence root and refuses an existing root rather than replacing history.

These records distinguish pre-seal working bytes from post-seal committed source.
They do not grant publication, protected/trusted acceptance, Stage progression,
trading authority or owner acceptance. Historical decisions above are unchanged.


## Enclosing runner terminal exits

The enclosing runner records and returns 0 for PASS, the exact child nonzero
code for a propagated child failure, 1 for an in-process or spawn failure, and
130 with terminal INTERRUPTED for a caught KeyboardInterrupt. An unstarted
child still has no native exit code. Original exceptions and secondary
reconciliation or persistence failures remain attributable; a passing pytest
child cannot mask a later failure.

RUNNING evidence, a remaining runner.incomplete file, missing or mismatched
summary digests, and failed final persistence are INCOMPLETE evidence, never
accepted terminal success. Evidence digests are collected before terminal
success is constructed. The summary is published after its sidecar; persistence
failure fails the invocation. A forced termination cannot be certified from an
unfinished record. The summary records the selected enclosing exit; independent
host observation of process termination remains separate evidence.

This clarification does not rewrite historical ATIS_RUNNER_EXECUTION_V1 or
ATIS_PYTEST_EXECUTION_V1 records. Historical Gate B results remain attributable
to their original bytes. Focused correction acceptance is not full-profile
certification of changed bytes, post-seal certification, or owner acceptance.
All existing authority exclusions and historical decisions remain unchanged.

# ATIS Smoothflow V5.5 Engineering Governance

## Document status and purpose

This document is the proposed canonical engineering-governance standard for ATIS under Smoothflow V5.5. It consolidates the seventeen controls from Unified Engineering Standard Governance Integration 035 with the approved efficiency and evidence-based anti-loop operating rules.

This candidate is `DOCUMENTED_ONLY`. It is not authoritative, accepted, or technically enforced merely because it exists. It becomes effective only after source authentication, separately attributable independent review, protected publication with all required checks, and explicit owner acceptance.

Source document: Unified Engineering Standard Governance Integration 035, dated 31 October 2026. Authenticated source SHA256: `7550e5c09fc11ef605b6b674be78bd9d2e9796ba3b150f7fe0db342deff00102`.

Preparation task: `ATIS-SMOOTHFLOW-GOVERNANCE-019`. This task grants local governance preparation only. It grants no publication, merge, acceptance, deployment, broker, financial, trading, or operational authority.

## 1 Governing identity and scope

Smoothflow V5.5 remains the governing ATIS engineering framework. This document does not create a new framework, execution guard, task-lock service, retry controller, evidence database, authority service, or autonomous acceptance mechanism.

The requirements in this document apply to bounded ATIS engineering work only after the document completes the effectiveness conditions stated above. Existing protected technical controls, accepted contracts and amendments, and explicit bounded task authorities remain unchanged.

Stage 5 is excluded from preparation under `ATIS-SMOOTHFLOW-GOVERNANCE-019`. This candidate neither inspects nor changes Stage 5 source, evidence, worktrees, task state, authority, checkpoints, or acceptance status.

## 2 Authority precedence and supersession

When requirements conflict, apply this precedence order:

1. The current explicit owner directive for the bounded action.
2. Protected technical controls and repository protection rules that apply to the action.
3. Accepted contracts and amendments within their stated scope.
4. The current bounded task authority and its exclusions.
5. Current accepted governance that is consistent with items 1 through 4.
6. Historical or superseded instructions, retained only as evidence.

Later material does not supersede earlier authority merely because it is newer. Supersession must be explicit, attributable to the owner or another accepted authority, scoped, and evidence-backed. Historical evidence must not be rewritten or deleted to simplify the current record.

No current-state record, programme register, recovery packet, evidence successor, test result, reviewer recommendation, or automated check grants authority beyond its explicit accepted contract.

## 3 Governance status vocabulary

Every governance finding or control status must use one of the following classifications:

- `DOCUMENTED_ONLY`: the requirement is written but no current executable evidence establishes enforcement.
- `TECHNICALLY_ENFORCED`: current executable evidence establishes the control on the actual applicable path and candidate identity.
- `BLOCKED`: verification or operation cannot proceed because a named evidence, authority, environment, security, or scope prerequisite is unresolved.
- `NOT_IMPLEMENTED`: an accepted requirement exists, but its required technical implementation is absent.
- `SUPERSEDED`: a current explicit authority replaces the instruction for active use while the original remains preserved.
- `HISTORICAL`: the artifact is retained for provenance, audit, or reconstruction and is not current operating authority.

Documentation, design intent, source presence, local tests, self-verification, independent assurance, owner acceptance, protected publication, and operational authority are distinct states. No lower state may be promoted without its required evidence.

## 4 Seventeen engineering controls

The following controls preserve the requirements of Governance Integration 035. Unless current executable evidence proves otherwise for a particular protected path, their status in this candidate is `DOCUMENTED_ONLY`.

### C01 Engineering readiness qualification

Verify contracts, the actual worktree, exact scope, authority, environment, dependencies, and evidence before editing. Missing or contradictory prerequisites must be classified rather than assumed.

### C02 Complete subsystem testing

Test actual issuer, consumer, and financial paths, including malformed input, concurrency, persistence, and recovery. A fixture or design assertion does not qualify an untested actual path.

### C03 Executable contract verification

Trace each accepted requirement to source and to positive and negative executable oracles. Record unexecutable requirements as gaps rather than passes.

### C04 Continuous quality checks

Run scoped lint, typing, security, and regression checks during authorised work. Distinguish inherited findings from newly introduced findings and preserve mandatory final qualification.

### C05 Consolidated root-cause management

Track causal defects and corrections across tasks. Address shared causes rather than repeatedly treating isolated symptoms. Preserve stable defect and root-cause identities.

### C06 Automated evidence authentication

Hash exact delivered bytes and verify file accessibility, manifests, and formats. Never cross-use hashes for different representations, including PDF, DOCX, and Markdown derivatives.

### C07 Capability-based progress

Record progress using `PREPARED`, `OWNER_AUTHORISED`, `IMPLEMENTING`, `SELF_VERIFIED`, `INDEPENDENTLY_ASSURED`, and `OWNER_ACCEPTED`, with blockers and authority boundaries. Do not infer a higher state.

### C08 Dependency and integration mapping

Maintain upstream and downstream interfaces, trust boundaries, status, prerequisites, and actual-path dependencies. Unverified dependencies must remain explicit.

### C09 Change-impact analysis

Identify affected interfaces, accepted units, invariants, consumers, and required regressions before mutation. Preserve unrelated accepted work.

### C10 Deterministic test environments

Record Python, dependencies, database, Windows capabilities, fixtures, and reproducible commands. Environment limitations must not be reported as source passes or failures without evidence.

### C11 Security and financial invariant register

Track fail-closed authorisation, replay resistance, capital and liability conservation, recovery holds, key separation, trusted identities, and evidence. Safety invariants must not be weakened to obtain a passing result.

### C12 Controlled checkpoints and recovery

Preserve the accepted candidate, tests, authority, blockers, and next action. Resume from authenticated checkpoints without repeating verified work or discarding unresolved failures.

### C13 Contract change control

Identify accepted versions, stop on contradictions, preserve superseded history, and require explicit authority for material contract amendments.

### C14 Risk-based assurance

Require separately attributable independent review for financial authority, cryptographic trust, security boundaries, and durable recovery. Use proportionate review elsewhere. An implementation agent must not self-certify independent assurance.

### C15 Automated regression selection

Use verified source-to-test mapping to select affected regressions. Retain mandatory safety suites and final subsystem qualification; focused diagnosis must not erase final qualification obligations.

### C16 Operational readiness levels

Keep component, integration, and deployed operational qualification separate. Never infer a higher qualification, deployment readiness, or trading authority from a lower level.

### C17 Efficiency and cost control

Use one bounded active mutation unit, reuse authenticated evidence, avoid redundant packages and investigations, and define evidence-based stop conditions. Efficiency must not weaken security, financial safety, protected checks, or independent assurance.

## 5 Efficiency operating rules

### R01 Single-agent default

Use one primary agent per bounded engineering task. Subagents require explicit owner authorisation. Separately attributable independent assurance remains a separate role and must not be represented as the implementation agent's assurance.

### R02 One active mutation task per workstream

Permit one active source-mutation task per workstream. Record its task identity, candidate identity, and mutation boundary. Separately authorised read-only review may proceed only when it cannot conflict with or obscure the active candidate.

### R03 Evidence-first preflight

Before implementation, authenticate the protected baseline, current candidate, owner authority, accepted contracts, required evidence, environment, dependencies, and allowed paths. Reuse verified evidence when its bound inputs are unchanged.

### R04 Engineering readiness

Verify upstream capability producers, interfaces, trust boundaries, qualified environments, and executable positive and negative oracles before coding. Classify missing prerequisites instead of manufacturing readiness.

### R05 Investigation reuse

Assign a stable fingerprint to each completed investigation. Do not repeat it without changed evidence, source, environment, authority, or a precise unresolved question that the prior investigation did not answer.

### R06 Focused failure diagnosis

Preserve the original failure and diagnose the exact failing positive control or negative oracle before broad test reruns. Focused diagnosis does not remove any mandatory final qualification suite.

### R07 Autonomous in-scope correction

Permit ordinary recoverable corrections inside explicitly authorised source, test, and environment boundaries. Escalate material security, architecture, authority, safety, or scope blockers. Do not expand authority by interpreting silence or missing evidence.

### R08 Evidence and progress preservation

Preserve accepted checkpoints, passing results, original failures, immutable source identities, correction history, and historical audit records. Keep self-verification, independent assurance, owner acceptance, protected publication, and operational authority distinct.

### R09 Compact persistent handover

Use schema-permitted programme-control records to record the current task, candidate, last accepted and independently verified checkpoints, blockers, latest authenticated evidence, next single action, stale-source exclusions, and authority not granted. Link to immutable evidence rather than duplicating complete packages. The master-recovery packet remains a fixed recovery index, with its existing `current_state.details_source` relationship pointing to `docs/programme/programme-control.json`; it is not an extensible live-task register.

### R10 Owner-controlled progression

No task, record, test, agent, or automated mechanism grants implicit publication, merge, acceptance, deployment, broker, financial, or trading authority. Each progression requiring owner authority must be explicit and bounded.

## 6 Evidence-based anti-loop rules

The anti-loop rules are operating requirements and measurements, not an execution engine, automatic stop, acceptance gate, or retry ceiling.

### AL01 Repeated investigation

Detect a proposed investigation whose stable fingerprint matches a completed investigation and whose bound source, evidence, environment, authority, and question have not changed. Reuse the prior result or identify the precise new question. Measure duplicate investigations avoided and justified repeats.

### AL02 Unchanged-root-cause failures

Use stable defect and root-cause identifiers. Before another correction attempt, record the new causal hypothesis or new evidence. Escalate genuine blockers based on evidence, safety, authority, or scope rather than an attempt count. Measure attempts that added no new causal evidence.

### AL03 Unnecessary full-suite reruns

Do not rerun a broad suite solely because a prior run failed. Diagnose the focused oracle first, then run affected regressions and any mandatory final qualification. Measure broad reruns without relevant source, dependency, environment, or oracle change.

### AL04 Repeated preparation without progress

Record whether each cycle retired a blocker, produced verified evidence, completed an authorised correction, or identified one precise owner decision. If none occurred, investigate missing readiness or authority rather than preparing another equivalent task. Measure preparation-only cycles with unchanged blockers.

### AL05 Conversation handover loss

Maintain a compact current-state handover in schema-permitted programme-control records, bound to immutable source and evidence identities. Each handover must identify the current candidate, accepted checkpoint, open blocker, authority exclusions, and next single action using permitted record content, without assuming dedicated JSON fields exist. Preserve the master-recovery packet's existing `current_state.details_source` relationship to `docs/programme/programme-control.json`. Measure handovers that omit or contradict this required content.

### AL06 Unnecessary escalation

Correct ordinary in-scope defects autonomously where authorised. Escalate only genuine evidence, safety, architecture, environment, authority, or scope blockers. Fixed retry ceilings are prohibited. Metrics must inform review and must not themselves stop, approve, or accept work.

## 7 Existing register reuse

No new register, database, service, task-lock system, or ECG replacement is authorised. Existing structures remain the only proposed persistence points, subject to their accepted schemas and separate authority for record mutation.

### programme-control.json

Use the existing programme-control ledger, milestone gate register, decision register, blocker and deferral register, evidence invalidation register, provenance gap register, and reality assumption register for:

- active workstream and task references;
- owner authority and exclusions;
- readiness prerequisites and decisions;
- stable blocker and root-cause identifiers;
- investigation fingerprints in permitted evidence or narrative fields, never as replacements for constrained record IDs; and
- governance supersession references.

### master-recovery-packet.json

`master-recovery-packet.json` is a fixed recovery index, not an extensible live-task register. Its existing `current_state` schema is closed and must not receive unapproved task, blocker, candidate, or next-action fields. Preserve the existing `current_state.details_source` relationship pointing to `docs/programme/programme-control.json`.

New handover details and immutable evidence references must use schema-permitted programme-control records for:

- current candidate identity;
- last owner-accepted checkpoint;
- last independently verified checkpoint;
- open blocker references;
- latest authenticated evidence;
- next single authorised action; and
- stale-source and authority exclusions.

### successor-evidence.json

Use successor evidence only for immutable predecessor and successor evidence relationships and their authority statements. It must not become a mutable current-state register or an authority source.

The current schemas do not expose a dedicated investigation-fingerprint field. Investigation fingerprints must use permitted evidence or narrative fields and must not replace constrained record IDs. Governance status labels must not be substituted into existing JSON lifecycle enums. Any direct recovery-packet field or list extension, dedicated typed field, or other schema extension requires a separate justified proposal, explicit owner authorisation for the schema change, protected validation, and protected publication. Existing closed property sets, fixed list cardinalities, and enumerated values remain binding. This candidate makes no schema change.

## 8 Security financial safety and fail-closed preservation

This governance candidate does not change protected technical controls. All accepted security, financial-safety, provenance, branch-protection, trusted-evaluator, independent-assurance, and fail-closed requirements remain in force within their accepted scopes.

Missing, stale, conflicting, unauthenticated, or ambiguous evidence must not grant permission, clear a restriction, release a liability, manufacture a terminal outcome, or advance an acceptance state. Existing owner acceptance authority remains exclusive.

## 9 Permanent ECG retirement

The Engineering Execution Guard is unfinished, retired, and prohibited. It must not be activated, executed, invoked, repaired, completed, relied on for decisions, used for failure ceilings, or treated as an approval or acceptance authority. Historical ECG records may be retained solely as evidence.

ECG source removal, historical evidence preservation, or later technical completion does not authorise ECG use. Only a new explicit owner decision may reverse this prohibition. This document does not recreate ECG or an equivalent component.

## 10 Permanent exclusion of fixed retry ceilings

Historical fixed two-failure, two-retry, or other attempt-count ceilings are `SUPERSEDED` and prohibited. No fixed retry ceiling may be reintroduced through governance, metrics, tooling, or task instructions.

Repeated work must instead be governed by evidence: diagnose the actual cause, reuse unchanged evidence, focus verification, correct within authority, and escalate material safety, evidence, architecture, environment, authority, or scope blockers.

## 11 Independent assurance and owner acceptance

Independent assurance must be separately attributable and must inspect the exact candidate and evidence without inheriting the implementation agent's pass labels. It must not modify the candidate while acting as independent reviewer.

The owner remains the sole internal acceptance authority. Independent assurance may recommend acceptance but cannot confer it. Protected publication and successful required checks do not themselves constitute owner acceptance, deployment authority, or operational authority.

## 12 Publication and effectiveness conditions

This candidate may be proposed for authority only after all of the following are evidenced:

1. Exact source authentication and source-to-clause traceability.
2. Separately attributable independent read-only review of the exact candidate bytes.
3. Protected publication under the applicable repository controls.
4. Successful required checks on the published candidate.
5. Explicit owner acceptance of the published identity.

Until those conditions are complete, this file remains `DOCUMENTED_ONLY`. It must not be cited as current accepted authority.

## 13 Source-to-clause traceability

| Source requirement | Candidate clause | Preservation result |
| --- | --- | --- |
| Governance identity, owner-authorised integration, not repository-enforced | Document status and purpose; Sections 1 and 12 | Preserved with stricter effectiveness conditions |
| C01 Engineering readiness qualification | Section 4 C01; R03; R04 | Preserved |
| C02 Complete subsystem testing | Section 4 C02 | Preserved |
| C03 Executable contract verification | Section 4 C03 | Preserved |
| C04 Continuous quality checks | Section 4 C04 | Preserved |
| C05 Consolidated root-cause management | Section 4 C05; AL02 | Preserved and operationally clarified |
| C06 Automated evidence authentication | Section 4 C06; R03; R08 | Preserved |
| C07 Capability-based progress | Section 4 C07; Section 3 | Preserved |
| C08 Dependency and integration mapping | Section 4 C08; R04 | Preserved |
| C09 Change-impact analysis | Section 4 C09 | Preserved |
| C10 Deterministic test environments | Section 4 C10 | Preserved |
| C11 Security and financial invariant register | Section 4 C11; Section 8 | Preserved |
| C12 Controlled checkpoints and recovery | Section 4 C12; R08; R09 | Preserved |
| C13 Contract change control | Section 4 C13; Section 2 | Preserved |
| C14 Risk-based assurance | Section 4 C14; Section 11 | Preserved |
| C15 Automated regression selection | Section 4 C15; R06; AL03 | Preserved |
| C16 Operational readiness levels | Section 4 C16; Section 3 | Preserved |
| C17 Efficiency and cost control | Section 4 C17; Sections 5 and 6 | Preserved and expanded without weakening |
| Permanent ECG prohibition | Section 9 | Preserved and made explicit |
| One-agent and governance-only boundaries | R01; document status; Sections 1 and 12 | Preserved |
| Status evidence and no false enforcement claim | Section 3; Section 12 | Preserved and clarified |
| Protected change control and no automatic integration | Sections 2, 11, and 12 | Preserved |
| Completion requires verified controls and separate owner acceptance | Sections 11 and 12 | Preserved |

## 14 Preparation exclusions

Preparation task `ATIS-SMOOTHFLOW-GOVERNANCE-019` authorises only the isolated local candidate at this path. It does not authorise changes to programme-control, master-recovery, successor-evidence, schemas, workflows, rulesets, protected branches, historical records, Stage 5, application source, tests, dependencies, security settings, financial systems, broker systems, or production environments.

No GitHub publication, pull request, merge, deployment, broker connection, trading action, or automatic acceptance is authorised by this candidate.

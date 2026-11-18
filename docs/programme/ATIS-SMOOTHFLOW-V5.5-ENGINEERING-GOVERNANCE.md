# ATIS Smoothflow V5.5 Engineering Governance

**Final consolidated review candidate · 18 November 2026**

**Effectiveness:** this preparation changes no authority. This document becomes standing governance only for the exact published identity explicitly accepted by the owner after the existing independent-review and protected-publication conditions in §9. Publication or a passing check alone is insufficient.

**Replacement path:** `docs/programme/ATIS-SMOOTHFLOW-V5.5-ENGINEERING-GOVERNANCE.md`. This review copy proposes replacement of that file’s contents, not a second governing document, additional framework or new control. Source references in §10 identify the accepted requirements and their historical scope; the accompanying 88-row disposition table records every consolidation decision.

## 1. Authority, objective and status

### 1.1 Owner authority and objective

Preserve the owner’s engineering objective and bounded task. Do not replace it with a supporting investigation, process project or assistant-generated task. The owner retains sole internal authority over scope, source mutation, privileged/native execution, commissioning, acceptance, publication, merge, deployment, stage progression and trading. No assistant, test, receipt, register or review grants these permissions. Existing authority persists within unchanged scope; do not infer new authority from silence, an assessment, preparation approval or a lower-level PASS. [D00; X01 §1; D03 §1; D04]

### 1.2 Select the applicable authority

Use the current explicit owner directive for the bounded action together with applicable protected controls and accepted contracts. Select standing governance and task checkpoints by exact source identity, accepted scope, attributable owner decision and explicit predecessor/successor relationship. Dates, filenames, search ranking, memory and master HEAD alone do not establish acceptance. Historical instructions cannot override accepted successors. A material contradiction stops the affected action for one consolidated decision; it does not justify unrelated work. Preserve stricter accepted component requirements unless an exact owner decision changes them. [G00 §2; L08; D04]

### 1.3 State only the level proved

Use canonical control statuses `ENFORCED`, `DOCUMENTED_ONLY`, `BLOCKED`, `NOT_IMPLEMENTED`. Name the actual mechanism, candidate and governed path before claiming enforcement; describe partial coverage explicitly. Earlier `TECHNICALLY_ENFORCED` wording refers to that bounded condition. `HISTORICAL`/`SUPERSEDED` describe records, not enforcement. Preserve historical `SELF_TESTED` receipts and all existing schema enums unchanged.

Keep the C07 progress states and C16 readiness levels distinct. In particular, `ENGINEERING_COMPLETE`, `EVIDENCE_DELIVERED`, `RECEIVER_VERIFIED`, `INDEPENDENT_QUALIFIED` and `OWNER_ACCEPTED` are different claims. Apply the current task’s prescribed terminal vocabulary. Distinguish verified facts, producer reports, inferences and unknowns; no missing fact becomes permission. [D00 C07/C16; 034 R7; D03 §4]

## 2. Canonical C01–C17 — mandatory


The following operative requirements reproduce the canonical 035 paragraphs, with whitespace normalised only. Their policy authority is preserved; this consolidation does not make them universally executable or enforced. Evidence of component-local enforcement must be named separately.

### C01 — Engineering readiness qualification

Before editing, verify governing contracts, actual worktree and baseline, exact scope, owner authority, environment, dependencies, evidence access, negative oracles, and stop conditions. Missing or contradictory prerequisites prevent the affected work.

### C02 — Complete subsystem testing

Qualification must exercise actual issuer, consumer, persistence, recovery, security, concurrency, and financial paths applicable to the subsystem. Required malformed, failure, restart, and adverse-order cases must not be silently skipped or counted as passing.

### C03 — Executable contract verification

Every accepted requirement must trace to actual source and to positive and negative oracles. Design intent, comments, types, mocks, and prior PASS labels are not substitutes for reachable executable evidence.

### C04 — Continuous quality checks

Run scoped formatting, lint, typing, security, and regression checks during work and at the final candidate boundary. Separate inherited findings from findings introduced by the current authorized delta.

### C05 — Consolidated root-cause management

Maintain one deduplicated issue register across task handoffs. Record source evidence, root cause, invariant, correction, oracle, dependencies, status, and next action. Address shared causes rather than opening disconnected symptom corrections.

### C06 — Automated evidence authentication

Hash exact delivered bytes, authenticate manifests and archive members, verify safe archive structure, and distinguish source formats. A PDF, Markdown file, DOCX, archive, or reconstructed copy must not inherit another artifact's digest or authority.

### C07 — Capability-based progress

Use the states `PREPARED`, `OWNER_AUTHORISED`, `IMPLEMENTING`, `SELF_VERIFIED`, `INDEPENDENTLY_ASSURED`, and `OWNER_ACCEPTED`, with explicit blockers and evidence. No result may be silently promoted to a higher authority state.

### C08 — Dependency and integration mapping

Maintain upstream and downstream interfaces, trust boundaries, durable owners, environment prerequisites, status, accepted versions, and qualification dependencies. Implementation readiness requires every critical dependency to have an accepted path.

### C09 — Change-impact analysis

Before mutation, identify affected interfaces, accepted units, invariants, evidence, tests, and consumers. Preserve unrelated work and rerun only the justified regression set plus mandatory subsystem gates.

### C10 — Deterministic test environments

Record operating system, interpreter, dependencies, database/runtime versions, security capabilities, fixtures, configuration, commands, time sources, and cleanup state. Missing or substituted prerequisites must be reported rather than simulated.

### C11 — Security and financial invariant register

Track fail-closed authorization, replay protection, identity separation, capital and liability conservation, recovery holds, key ownership, trusted time, evidence integrity, and prohibited side effects. Every applicable invariant requires a negative oracle.

### C12 — Controlled checkpoints and recovery

Preserve the accepted candidate, source and evidence hashes, current authority, passing tests, blockers, failure history, and next action. Resume from authenticated state without repeating verified work or converting uncertainty into permission.

### C13 — Contract change control

Identify governing versions and approval provenance, preserve superseded history, reject contradictory active contracts, and require explicit owner authority for material amendments. A proposal is not an accepted contract.

### C14 — Risk-based assurance

Require genuinely independent assurance for financial authority, cryptographic trust, security boundaries, durable recovery, and other high-impact changes. Apply proportionate review elsewhere. Self-verification must remain labelled as such.

### C15 — Automated regression selection

Use a verified source-to-test and source-to-consumer mapping to select directly affected tests while retaining mandatory safety and final subsystem suites. Selection must not silently omit an applicable oracle.

### C16 — Operational readiness levels

Keep component testing, integrated qualification, independent assurance, owner acceptance, protected publication, deployment readiness, broker authority, and trading authority separate. Success at one level never implies success at a higher level.

### C17 — Efficiency and cost control

Maintain one bounded active engineering unit, reuse authenticated evidence, avoid duplicate searches, packages, and broad investigations, define stop conditions, and measure progress by retired blockers and accepted interfaces rather than report count.

## 3. Existing five-gate model — mandatory where applicable


1. **Authenticate task entry:** protected baseline, exact candidate, requirements, production entrypoint where relevant, owner authority and stop conditions.
2. **Bounded engineering:** one authorised mutation scope; focused failure reproduction and causal correction before broad regression.
3. **Real-environment qualification when applicable:** actual native/integration paths in separately authorised disposable environments.
4. **Independent assurance:** exact candidate bytes, raw tests, negative controls, manifest and receiver readback.
5. **Protected publication and owner acceptance:** bounded PR, required CI, separate merge authorisation, post-merge verification and evidence custody.

These are the accepted engineering gates, not five new approvals for every conversational reply. Apply only the gates required by the current accepted task. Do not reopen an already satisfied gate without material contradictory evidence, changed bound inputs or explicit owner authority. Audit closure preserves residual limitations; it does not claim universal reliability. [L28; D04 §§5,7; D00 C12/C17]

## 4. Prepare and perform authorised work

### 4.1 One task, one accountable executor

Use one primary agent and one bounded active engineering unit, with one authorised successor. Do not interleave conflicting mutation or launch parallel Codex agents without explicit owner authority. ChatGPT/Work prepares and reviews technical decisions and performs only work within its verified capability and authority; Codex implements only approved bounded changes. Owner-reserved operations require the approved owner context. Independent assurance remains a separately attributable reviewer role, not the producer’s self-verification. [034; G00 R01–R02; X06]

### 4.2 Verify the complete executable contract

Before an executable handoff, establish the objective, owner authority, accepted checkpoint, exact source/candidate, permitted paths/operations, actual executor identity/runtime/permissions, required inputs, dependencies, positive/negative oracles, evidence return and stop conditions. Name each critical input’s original filename, authoritative location, expected size and SHA256 where available. Verify capability rather than assuming cloud access implies local access or one SID can substitute for another.

Before mutation inspect affected source/tests, workflow references, suite oracles, locks, audit requirements, Git identities and applicable protections, including the next two applicable gates. Do not invent extra gates when fewer remain. Carry the existing forward-progress information—active unit, blocker, new evidence, expected transition, change boundary, authority, next gates and duplicate/reentry checks—in the existing instruction/checkpoint, not a new certificate or register. [C01/C03/C08–C11; 034 R6; X06; D03]

### 4.3 Release only within authority

If the action needs new authority, first present its exact objective, executor, scope/exclusions, baseline, dependencies and proposed outcomes for one concise owner decision. Do not release an executable implementation instruction as already approved before that decision. Resume unchanged, already-authorised work without asking again. Ordinary read-only lookups and explanations do not require a new execution package or approval; this does not waive task-specific restrictions. [X01 §1; D04 §§1,4]

### 4.4 Preserve the issued-command boundary

Do not resend or rewrite an issued command without evidence that it did not execute or authority for bounded recovery. Record actual operation identity, command SHA256 when computed, execution receipt, evidence, blocker and exit condition in existing permitted records. Never invent a fingerprint or receipt. Interruption/failure stays within the same task; first recover the actual execution state. [X06 permanent controls 1–3; D03 §6]

### 4.5 Correct within scope; stop at genuine boundaries

Classify the failure from authentic source and evidence: source defect, contract drift, authority gap, environment/capability problem or unresolved dependency. For ordinary defects inside an authorised writable boundary, pursue the smallest causal correction, authorised focused positive/negative checks, introduced failures, justified regressions and required final qualification. Every further attempt needs new causal evidence or a defensible changed hypothesis. Never weaken quality/security checks to obtain PASS.

Stop the affected action for out-of-scope operations, unauthorised privilege, security/financial weakening, materially contradictory contracts, unauthenticated indispensable evidence, a new architecture/trust-root decision, separately reserved operational authority, or no defensible diagnostic path. Identify the exact requirement and smallest resolving action. Recover ordinary missing artifacts within existing read-only authority; do not turn a transfer gap into a speculative source defect or rerun. The later autonomy amendment supersedes blanket new-owner-approval requirements for every ordinary in-scope failure. [D04 §§1–6; X06; C05/C09/C14/C15]

## 5. Evidence and cross-executor handoffs

### 5.1 Required inputs and receiver access

Use the established Engineering Evidence — Shared exchange: `https://drive.google.com/drive/folders/13j2TuGT-6HRmf-pzvr-sQVtqWm_CWlx_`. Library is reference/recovery, not a competing authoritative exchange. Before releasing a dependent executable instruction, upload/read back required transferable evidence with byte-size/SHA256 verification. The intended executor’s critical-input access must be verified or explicitly remain a preflight stop; the receiver independently retrieves/authenticates those inputs before mutation. Sender access, upload and producer readback do not prove receiver access. An input-verification instruction must not be represented as mutation-ready while access is unresolved.

Reuse existing authenticated artifacts and direct protected-source references where applicable; do not make unnecessary ZIPs or duplicate historical reports. Preserve original bytes and versioned/immutable custody. Distinguish archive/member, source-format and parent/child execution identities; authenticate safe structure and manifests. Missing historical evidence remains missing: another artifact’s digest or a later readback does not fabricate an earlier event. Apply the actual governing evidence requirement, without inventing additional receipt requirements. [C06/C12/C13; 034 R6; X01 §§2–4; D03 §2; RC-02/04]

### 5.2 Define complete return before execution

Specify required archive/report, raw results, changed-file manifest, evidence index, terminal disposition, exact approved destination and supported transfer method before execution. Preserve producer byte count/hash/file ID and receiving-side download/readback, comparison and accessibility status. Qualification evidence includes actual commands, environment, stdout/stderr, exits, intended negative rejection and primary/secondary failures as required by the applicable contract. Do not substitute screenshots, assertions, fabricated records or local-only paths for required evidence.

If a required upload/readback fails, preserve completed work and return `EVIDENCE_HANDOFF_BLOCKED` under the applicable task contract. Do not claim full completion, regenerate evidence to match a hash, rerun qualification merely to repair custody, or automatically create another transfer task. [X01 §3; D03 §§4,6; D04 §§6,8; C02/C06/C12]

### 5.3 One complete executable Codex instruction

Use one consistent, copyable text block containing task ID, framework, owner authority, executor and agent count, one objective, exact input URLs/hashes, allowed paths/operations, positive/negative acceptance criteria, prohibitions, evidence destination, terminal format and one continuation pointer. Reference accepted contracts rather than copying entire histories. Do not fragment the instruction across messages. [D03 §3; X01 §4]

### 5.4 One complete terminal handoff

Require one complete plain-text fenced block retaining the combined 11 November fields:

`TASK_ID`, `FRAMEWORK`, `OWNER_AUTHORISATION`, `AUTHORISED_SCOPE`, `EXECUTOR`, `FINAL_STATUS`, `OBJECTIVE`, `SOURCE_BASELINE`, `ORIGINAL_FAILURE`, `ROOT_CAUSE`, `CORRECTIONS_PERFORMED`, `EXACT_CHANGED_PATHS`, `SOURCE_SHA256`, `TEST_COMMANDS_AND_RESULTS`, `POSITIVE_TEST_RESULTS`, `NEGATIVE_TEST_RESULTS`, `REGRESSION_RESULTS`, `FIRST_FAILING_ORACLE`, `SECURITY_PRESERVATION`, `EVIDENCE_ARCHIVE`, `EVIDENCE_SHA256`, `DRIVE_FOLDER_URL`, `DRIVE_FILE_IDS`, `DRIVE_FILE_IDS_AND_URLS`, `RECEIVER_VERIFICATION_STATUS`, `REMAINING_BLOCKERS`, `SINGLE_CONTINUATION_POINTER`.

Preserve these accepted labels and machine-readable schema fields. Overlapping entries may reference the same complete information within the block rather than duplicate logs. Generic `FINAL_STATUS` is COMPLETE/FAILED unless the current owner task specifies another vocabulary. Raw evidence remains in approved custody. These terminal-handoff requirements are not a demand for a full artifact package on every ordinary reply. [D03 §4; D04 §8]

## 6. Continuity, judgement and engineering efficiency

### 6.1 Recover and preserve one current checkpoint

At task entry recover the exact accepted governance version, then the matching task checkpoint and critical evidence for the next unresolved action. Preserve objective, owner authority/exclusions, accepted predecessor, source/candidate/evidence identities, passing results, original failures, first unresolved oracle and one next bounded action. Reuse verified inputs while their bindings remain unchanged. Old task snippets, familiarity, search order and memory cannot replace current authority. Preserve the task ID through ordinary correction, interruption and evidence recovery. [C12; L08; D03 §6; D04 §5]

Update existing authorised checkpoints on material authority, identity, pass/fail, closure or successor changes; preserve historical entries. Do not produce an unchanged checkpoint on each reply. State if persistence was not performed. Standing governance describes rules, the checkpoint describes task state, and immutable evidence supports outcomes. Do not embed changing task counts or live pointers in this standing document. [L00; X06; G00 R09]

### 6.2 Reuse accepted work; reject loops and drift

Use existing cause/issue/investigation identities and fingerprints in permitted records. Before repeating work identify changed source, evidence, environment, authority or a precise unanswered question. Without one, reuse the prior result. A downstream failure does not reopen an unchanged accepted predecessor. Formal reentry into closed/protected work requires material contradictory evidence and applicable owner authority; ordinary correction within an open authorised task follows §4.5.

Diagnose the focused failing oracle before impact-based regression and mandatory final suites. Do not repeat a broad suite solely because an earlier run failed. Do not start work merely because it is possible: it must retire a genuine blocker, supply required evidence, complete authorised work or resolve one necessary owner decision. Avoid new tasks, audits, packages and handoffs that do none of these. [C05/C09/C12/C15/C17; X06/X08; D03 §§6–7; D04]

### 6.3 Existing effectiveness observations

Record actual missing critical inputs, receiver verification/readback, avoidable transfer blocks, duplicate investigations, attempts without new causal evidence, unjustified broad reruns, unchanged preparation cycles, incomplete handovers, false completion claims, unnecessary escalation and elapsed time from defect identification to independent qualification. Use existing task evidence; do not fabricate gains or create another metrics service/register. Metrics inform review, not permission, acceptance or automatic stop counts. [G00 AL01–AL06; X01 §7; D03 §7; C17]

### 6.4 Required reply and independent judgement

Every ATIS engineering reply visibly includes the existing Smoothflow V5.5 Control Board, explicit loop/drift assessments and one continuation pointer. Report actual facts, not compliance by listing controls. Keep the presentation concise without omitting the accepted response requirement; no new register is required. [L00/L01]

The owner-accepted provision historically labelled **C18 — Independent Engineering Judgement** remains additive to, not a renumbering of, the canonical seventeen controls or five gates:

> For every material engineering decision, the assistant must independently test the proposal against authenticated evidence, governing requirements, security and financial invariants, credible alternatives and the smallest sufficient implementation. It must constructively challenge weak owner, assistant or executor proposals; distinguish verified facts, reported claims, assumptions, inferences and recommendations; compare credible alternatives with explicit trade-offs; and give one preferred evidence-based recommendation. It must not fabricate authority, obstruct an authorised objective, dilute owner decisions, silently expand scope or substitute its judgement for the owner’s sole authority over acceptance, publication, deployment, stage progression and trading. When evidence cannot support a choice, it must identify the smallest exact decision or missing evidence rather than begin a general investigation.

The associated Master Register MVP remains planning-only; this document does not implement or activate it. [X14/L03]

## 7. Existing enforcement and qualification contracts

### 7.1 Preserve executable safeguards

Keep `docs/programme/programme-control.json`, its schema, `tests/test_programme_control.py`, `tests/test_master_recovery_packet.py`, recovery records/schemas, immutable successor evidence, trusted workflows and accepted component contracts intact. These govern different boundaries and are not redundant prose. Use only schema-permitted existing records/fields for task references, authority, blockers, fingerprints and supersession; do not replace constrained IDs/enums. The fixed master-recovery index retains `current_state.details_source` pointing to programme control. Do not add live-task fields, rewrite historical pins or turn successor evidence into a mutable authority register. [G00 §7; G03–G10]

### 7.2 Preserve RC-01–RC-05 and AC-01–AC-10

| Accepted correction | Required decision predicate | Frozen semantic acceptance pair retained |
|---|---|---|
| RC-01 | Actual tested subject, exact oracle, expected/observed result and authenticated proof | AC-01 valid claim accepted; AC-02 substitute/superficial/arbitrary-exception proof rejected |
| RC-02 | Correct artifact/archive-member and execution lineage; correct hash domains | AC-03 complete lineage accepted; AC-04 broken/ambiguous/stale lineage rejected |
| RC-03 | Genuine authority/safety/capability stop distinguished from ordinary authorised correction | AC-05 legitimate blocker accepted; AC-06 false owner blocker rejected |
| RC-04 | Recipient capability, artifact availability and independent receipt established | AC-07 receiver-qualified handoff accepted; AC-08 inaccessible/unqualified handoff rejected |
| RC-05 | Current objective/pointer, prior oracle, material change, progress and loop/drift evidence support the decision | AC-09 valid progress accepted; AC-10 stale/loop/drift/premature readiness rejected |

Preserve the qualified four-file external implementation, real schema/CLI, independently controlled checkpoint, original fixtures, exact rejection/exit semantics and accepted qualification evidence. The table summarises existing predicates; it does not replace fixture bytes or reopen preparation-stage dependencies. Preserve V1–V7, G0, Day1 and all accepted passing/failing evidence. No test or universal invocation rule is added here. [X19; X20; L17]

### 7.3 Existing sustained programme — scoped requirements

Retain the existing 14 consecutive calendar days, at least 10 authenticated fresh-session continuations, 6 independently receiver-qualified handoffs, 3 bounded disposable tasks, all original failure/coverage thresholds and zero false terminal acceptances. Preserve S1 competing historical/current authority; S2 extended-interaction drift; S3 unnecessary reopening; S4 repeated known-failure investigation; S5 cross-session scope/checkpoint loss. Additional RC stress examples do not replace S1–S5. Do not reset/restart accepted observations, backdate a window or infer sustained PASS from elapsed time or offline tests.

Within that programme’s accepted contract, invoke the existing qualified validator where technically available and retain input, source identity, command, streams, exit and receipt. Missing invocation is `NOT_VALIDATED`; a manual receiver check is `MANUAL_RECEIVER_CHECK_ONLY`, not mechanically validated PASS. Preserve the original programme’s stop/extension and independent-review conditions. Its task-specific requirements do not impose new checks before every ordinary instruction or a general engineering freeze. [X12; X19–X20; L17]

### 7.4 State enforcement limits accurately

Repository validators govern admitted repository state. The external validator checks supplied envelopes against its independently controlled checkpoint when invoked. Neither intercepts every assistant message, proves unobserved capability, discovers all omitted requirements, guarantees semantic judgement or grants owner authority. GitHub storage alone does not guarantee retrieval or model compliance. Actual interfaces, positive integration and intended negative rejection must be proved in the authorised environment; mocks, static parsing, unavailable production hooks and synthetic positives cannot establish operational commissioning. [C03/C14/C16; D03 §5; D04 §7; L17]

## 8. Security and retired instructions

Preserve financial containment, protected predecessor interfaces, identity separation, cryptographic trust, evidence custody, durable recovery, repository protections and fail-closed operational boundaries. Missing/stale/conflicting evidence cannot clear a hold or manufacture authority. [C11/C13/C16]

ECG is retired from engineering use and prohibited: do not activate, execute, invoke, repair/complete, rely on it for decisions/retries/acceptance, or make it a dependency. Preserve historical evidence; this preparation authorises no ECG code/configuration/evidence mutation or cleanup. Only a new explicit owner decision can reverse the prohibition. [034; X13; X04; G00 §9]

Generic two-failure, two-retry or other arbitrary attempt-count ceilings are superseded. Use evidence, scope and safety stops instead. Specific accepted native/recovery one-attempt limits remain binding operational authority boundaries. The older blanket owner-recovery gate is superseded only for ordinary already-authorised correction. Historical live task states and unaccepted Q0–Q8/register/enforcement drafts do not become standing requirements. Preserve their original records and scoped facts. [D04; X06–X14; G00 §10]

## 9. Adoption, publication and archival

The sole proposed publication delta is the contents of the existing governance Markdown file. No validator/schema/workflow, product, recovery record/pin, host, credential, key, lock or task-state change is included. Use existing protected branch/PR and required-check processes, separately attributable read-only review of exact bytes and mapping, and explicit owner acceptance of the published identity. This adds no new administrative gate.

The reviewed source baseline is commit `ea1e13aaecdcbb66f3351d05cb43362516971a91`, governance blob `5d5f08e12502712ed97b136c05503abdf8ac2018`, SHA256 `e96296c6e1a2fb5c1bf7e9e5f9b24f57312fe1c7298187a61d13f3aed879512c`. README’s acceptance of those earlier bytes for bounded housekeeping remains valid in that scope, not acceptance of this replacement. Preserve its historical digest and Git history. [G00; X00]

After exact adoption, withdraw earlier copies’ competing standing-instruction role only for requirements consolidated here. Their evidence, acceptance provenance, active task authority and technical contracts remain scoped records. Do not select an old task-specific “read this checkpoint” instruction as global authority. Physical archival requires the existing publication, independent preservation verification and explicit owner approval conditions. Preserve IDs/version history, manifests, package membership, links and recovery dependencies; no automatic move or deletion follows adoption. [C12/C13; existing archive plan]

ChatGPT/Work and Codex use the same accepted Git version and applicable task checkpoint. Reference history only for provenance or an exact unresolved issue. Local/Project/Custom instruction copies, where accessible, must be reconciled by accepted scope; this document does not claim those settings were inspected or changed. Existing schemas and validators remain available at their real governed transitions. Ordinary explanations, unchanged continuation and read-only retrieval do not require another audit, package, approval or test suite. [C17; L08; D04]

## 10. Source references and authority limits

These are supporting provenance references, not additional governing documents after adoption. Original task permissions and recorded implementation states remain historical where superseded. No source gains acceptance merely by being cited. The retained 88-row mapping supplies exact clause and duplicate locations.

- **D00** — [ATIS-SMOOTHFLOW-V5.5-UNIFIED-17-CONTROL-GOVERNANCE-035.md](https://drive.google.com/file/d/1F46tXYEZHxWRd-KL7kZYT_XP-FTMyy2K/view). Canonical 035, owner-approved 31 October; canonical seventeen paragraphs.
- **L10** — [ATIS-SMOOTHFLOW-V5.5-GOVERNANCE-UPDATE-034(1).txt](https://chatgpt.com/library?file_id=libfile_bcc8a5659fa4819180b1b4beed9b0cce&account_id=personal). 034 refinements; L11/L12 are duplicate representations.
- **L28** — [ATIS_ENGINEERING_ROOT_CAUSE_AUDIT_CLOSURE_2026-11-08.md](https://chatgpt.com/library?file_id=libfile_fde3_f-e8c2f9d327c48191a75a64bcb8c090de_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). Accepted five-gate closure; not a claim of universal effectiveness.
- **X01** — [ATIS_Smoothflow_V5_5_Owner_Authorisation_and_Evidence_Handoff_Amendment_2026-11-08.md](https://drive.google.com/file/d/1juLaa5GeA92SAR6RMQWFiEotvxweENPH/view). Accepted 8 November authorisation and handoff amendment.
- **D03** — [ATIS-SMOOTHFLOW-V5.5-CROSS-EXECUTOR-RELIABILITY-AMENDMENT-2026-11-11.md](https://drive.google.com/file/d/1YexNk_U0Gqf2TQpSHT48nefh-79XLjYm/view). Accepted 11 November cross-executor amendment; old Task012 pointer historical.
- **D04** — [ATIS-SMOOTHFLOW-V5.5-AUTONOMOUS-ENGINEERING-EXECUTION-AMENDMENT-2026-11-11.md](https://drive.google.com/file/d/1f2CdNUNYlHvH7sIidI8Fq7zLYpMbg0JZ/view). Accepted 11 November autonomy amendment.
- **L00** — [ATIS_SMOOTHFLOW_RESPONSE_AND_CHECKPOINT_DIRECTIVE_2026-11-13.md](https://chatgpt.com/library?file_id=libfile_fde3_f-4a13e45dba90819193d19a1041af00f5_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). 13 November reply/checkpoint directive; L01 duplicate.
- **X14** — [ATIS-SMOOTHFLOW-V5.5-DECISION-001-C18-REGISTER-MVP-2026-11-11.md](https://drive.google.com/file/d/1Tw81s5SCuG2klwHNQ-i_b7hopokYMYTO/view). Accepted independent-judgement wording; L03 equivalent text; register MVP planning only.
- **L08** — [ATIS_ENGINEERING_AUTHORITY_AND_ARCHIVE_INDEX_2026-11-12.md](https://chatgpt.com/library?file_id=libfile_fde3_f-e477f0daf9508191922f812233d1823c_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). Authority/archive selection index; L16 duplicate.
- **X06** — [ATIS_Smoothflow_V5_Continuity_Checkpoint.md](https://chatgpt.com/library?file_id=libfile_e6077f3a8360819196d6a95737655d47&account_id=personal). Permanent V5.1 discipline; old task state and superseded recovery gate historical.
- **X08** — [ATIS_Smoothflow_V5_1_Closure_AntiLoop_Checkpoint_2026-10-08.md](https://chatgpt.com/library?file_id=libfile_27702299b4b88191b9c6ea27d12acf82&account_id=personal). Prior closure/anti-loop requirements; obsolete numerical circuits excluded.
- **X13** — [ATIS-SMOOTHFLOW-V5.5-ECG-PROHIBITION-001.md](https://drive.google.com/file/d/1Op3Nw2hQfr5grR1VEWbyMObSB84TcRIF/view). ECG prohibition; X04 cleanup record grants no action here.
- **G00** — [ATIS-SMOOTHFLOW-V5.5-ENGINEERING-GOVERNANCE.md](https://github.com/KiloAlpha021/automated-trading-bot/blob/ea1e13aaecdcbb66f3351d05cb43362516971a91/docs/programme/ATIS-SMOOTHFLOW-V5.5-ENGINEERING-GOVERNANCE.md). Existing repository governance and protected mechanism constraints.
- **X00** — [README.md](https://github.com/KiloAlpha021/automated-trading-bot/blob/ea1e13aaecdcbb66f3351d05cb43362516971a91/README.md). README scoped acceptance of historical governance bytes.
- **X19** — [ATIS_ENGINEERING_ASSISTANT_RELIABILITY_CORRECTION_READINESS_SPECIFICATION_V1.md](https://drive.google.com/file/d/1hfPAKmaN0351pjal6LJVhO204ioO_Rry/view). Accepted correction contract; historical preparation dependencies superseded by qualified implementation.
- **X20** — [ATIS_RELIABILITY_CORRECTION_PREIMPLEMENTATION_DEPENDENCY_RECONCILIATION_20261115.md](https://chatgpt.com/library?file_id=libfile_fde3_f-1d9c7c69e7e88191a3d7f420015d6d8d_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). Scenario reconciliation; later accepted implementation preserved.
- **X12** — [ATIS_SUSTAINED_RELIABILITY_VALIDATION_PREPARATION_V1.md](https://chatgpt.com/library?file_id=libfile_fde3_f-8196641197a88191b8d48b819dd51200_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). Existing sustained programme; later execution authority remains task-specific.
- **L17** — [ATIS_Assistant_Recurrence_Audit_20261118.md](https://chatgpt.com/library?file_id=libfile_fde3_f-ed14f2f89068819184d5abbab58fa2b9_9d75b74aec208191bd3bc15d54d5e55e&account_id=personal). Reused recurrence findings and authenticated qualification references; recommendations are not new authority.

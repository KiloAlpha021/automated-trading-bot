# ATIS engineering instructions

Read `docs/governance/ATIS-GOV-OWNER-001.md` and reconcile it with the latest explicit owner instructions before preparing or executing ATIS work.

- Smoothflow V5.5 only. ECG is incomplete and deactivated: do not invoke or activate it.
- The two-attempt ceiling is retired. No fixed retry limit. Continue evidence-based corrections within owner-authorized scope while useful progress is possible.
- One Codex agent; no subagents, delegation or concurrent equivalent workers.
- Reuse verified evidence; check actual downstream consumers and task scope before edits.
- Carry forward existing owner authorization within its scope; do not request it again solely for a continuation. Require new authority for expanded scope or protected publication/merge.
- Preserve historical evidence, security gates, protected branches and owner acceptance boundaries.
- Keep normal engineering sandboxed. Request narrowly scoped permissions for authenticated GitHub operations under the authorized Windows identity; never expose tokens.
- Distinguish local validation from protected acceptance; report precise blockers and the smallest next step.
- These instructions are guidance, not an automatic execution or trading authorization. Never use this file as justification to modify S4-07 or other work outside the current approved task.

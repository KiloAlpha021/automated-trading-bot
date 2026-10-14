# ATIS engineering execution guard

This tool is the restricted local entry point for bounded ATIS engineering tasks under Smoothflow V5.5. It is outside `src/automated_trading_bot` and has no production-runtime import path.

Run a task with a canonical descriptor:

```powershell
python -m engineering_execution_guard run --descriptor C:\external\task.json -- python path\to\bounded_implementation.py
```

The first `--` is the CLI boundary separator. The guard removes exactly that
separator before launching the executable. Any later `--` values belong to the
launched command and are preserved. A separator without an executable is
rejected before task state or evidence is created. Omitting the separator keeps
the existing validation-only mode when no implementation command is supplied.

The guard validates the exact Git commit/tree, dependency-lock digest, owner authorization reference, allowed path cone, repository-wide exclusion lock, persistent defect identity, two-failure circuit breaker, and mandatory validation commands. State, journals, locks, and evidence are outside the repository. Commands are argv arrays and never pass through a shell.

Attempt identities are allocated monotonically from every `ATTEMPT_STARTED` record. An interrupted attempt remains open until `recover-lock` positively establishes process death and receives a separate recovery authorization reference; recovery then appends an `INTERRUPTED` terminal event and counts it toward the circuit breaker. Existing attempt evidence is never reused or overwritten.

After two `FAILED` or `INTERRUPTED` attempts, continuation requires an external retry-authorization successor:

```powershell
python -m engineering_execution_guard authorize-retry `
  --descriptor C:\external\task.json `
  --authorization C:\external\retry-authorization.json
```

The successor binds the immutable original descriptor digest, task, defect, current authorization predecessor, and exact failed-attempt identities. It opens a new authorization epoch without deleting or resetting prior history. Replay, substitution, conflicting predecessor, premature authorization, and cross-defect authorization are rejected. Its owner reference remains an attributable assertion rather than a cryptographic owner signature.

## Enforcement boundary

The guard is mandatory for tasks launched through this entry point. It atomically excludes another guarded task for the same repository across worktrees and refuses cross-thread/task retry resets. It checks scope before and after every command and runs the descriptor's full validation profile.

It cannot prevent a user or agent with ordinary filesystem/shell write access from editing the repository directly. Windows ACL changes, Codex host policy changes, shell restriction, and GitHub workflow/ruleset changes were not authorized. The implementation is therefore **advisory outside the guarded entry point**. A caller must not claim host-wide mandatory enforcement.

The descriptor's authorization reference is an attributable owner-supplied identifier; this local tool does not create or cryptographically authenticate owner authority. Protected publication remains subject to GitHub protections and a separate explicit owner decision.

The state root is fixed beneath the current Windows user's `LOCALAPPDATA`. The CLI override is rejected unless explicit test mode is enabled; test-mode evidence is never authoritative task evidence.

The journal uses canonical JSON, monotonic sequences, SHA-256 chaining, and an atomic last-record anchor to detect accidental or one-sided corruption/truncation. It is not tamper-proof storage against an actor who can rewrite both journal and anchor.

Stale locks are never recovered automatically. Recovery requires the exact registered descriptor, a separate explicit recovery-authorization reference, and positive proof that the recorded operating-system process is terminated or does not exist. Windows access-denied and indeterminate process-query results are `ACTIVE_OR_UNKNOWN` and cannot release a lock. Successful recovery records the native process-query outcome and error code. Recovery may repair only the narrow interruption where one valid chained record was durably appended but the atomic anchor update did not complete.

The guard waits for the launched process but does not place descendants in a Windows Job Object. A deliberately detached descendant can outlive its parent and is outside this advisory enforcement boundary.

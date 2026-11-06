# ATIS Operator Console atomic reconciliation snapshot persistence

Status: Task052 local candidate. Research simulation only. No live trading authority.

## Boundary

This component persists the existing `ReconciliationSnapshotV1` canonical bytes. It does not redefine reconciliation truth, observation identity, evidence identity, freshness, or authority. It creates no API, listener, scheduler, Windows service, scheduled task, broker connection, or order path. The Operator Console remains disconnected.

## Authorised root and layout

The caller supplies one absolute, explicitly authorised local root. The component never derives it from the process working directory. The root is length-bounded and must be a real directory, not a symbolic link, junction, mount-style reparse point, or file.

```text
ROOT/
  versions/
    observation-<sha256(observation_id)>.json
  candidates/
    <bounded temporary candidates>
  evidence/
    publication-<sha256(canonical evidence)>.json
  current.json
  writer.lock
```

Unexpected root entries are rejected. Component-generated filenames contain only fixed prefixes and lowercase hexadecimal identities. `current.json` cannot name an absolute path, traversal path, or arbitrary file. Versions, candidates, evidence, and the pointer must remain on one filesystem.

Python's standard Windows path APIs detect existing reparse points before use. This blocks normal symbolic-link and junction escapes. Complete defence against a privileged local attacker replacing a checked path between validation and open is not established without handle-relative Windows APIs and an independently protected directory ACL.

## Canonical persistence identity

The immutable payload is exactly `snapshot.canonical_bytes()`. The storage digest is `sha256:<lowercase hex>` over those bytes. The version filename is derived from the SHA-256 of `observation_id`; it does not replace or redefine that identity.

An existing version with identical bytes produces `DUPLICATE` only when a verified current pointer already references that exact version. If the immutable version exists but is not current following an interrupted publication, a new explicit authorised `publish()` call may finish the pointer transition after verifying the existing version and any current pointer. This is a new publication attempt, not automatic recovery promotion. An invalid existing current pointer fails closed and is not overwritten. An existing version for the same observation identity with different or untrusted bytes produces `CONFLICT`. The store never overwrites an immutable version.

## Publication protocol

Under an exclusive, non-blocking writer lock:

1. Validate the existing snapshot and explicit timestamps.
2. Obtain and size-check canonical bytes.
3. Calculate the storage digest and bounded version name.
4. Exclusively create a candidate beneath `candidates/`.
5. Write, flush, and call `os.fsync` on the candidate handle.
6. Reopen and compare the candidate bytes.
7. Publish the immutable version with same-filesystem hard-link creation, then remove the candidate name.
8. Reopen and verify the immutable bytes.
9. Construct a canonical, bounded pointer containing only version, identity, digest, filename, and explicit publication time.
10. Exclusively write, flush, synchronise, reopen, and compare the pointer candidate.
11. Atomically replace `current.json` using same-filesystem `os.replace`.
12. Reopen the pointer and referenced version and verify both.
13. Exclusively create, synchronise, and verify attributable publication evidence.

Failure before pointer replacement cannot replace the previous valid pointer. Failure after replacement is reported with `pointer_committed=True`. If evidence persistence fails after replacement, the result is `COMMITTED_UNCONFIRMED`; it is never reported as an uncommitted failure.

## Reader contract

Readers open only `current.json` and its bounded version filename. They validate:

- Canonical pointer bytes and supported pointer/schema versions
- Pointer path and digest
- Immutable version size and storage digest
- Canonical snapshot JSON without duplicate members
- Exact snapshot serialization
- Source authority and research-run identity
- Evidence references and aggregate evidence digest
- Observation identity and reconciliation semantics
- Explicit timestamps
- `UNVERIFIED` freshness
- Simulation-only and `NO_TRADING_AUTHORITY`

Missing `current.json` returns `UNAVAILABLE`. Any malformed, missing, inconsistent, unsupported, or corrupted referenced state returns `UNKNOWN`. A valid snapshot remains `UNKNOWN` while freshness is `UNVERIFIED`; persistence never promotes it to current health.

## Concurrency and locks

Readers take no writer lock and therefore observe either the previous complete pointer or the replacement complete pointer. They never treat partial pointer bytes as valid.

Writers use a process-local non-blocking mutex plus a one-byte non-blocking OS file lock. A competing writer receives `BUSY`; last-writer-wins is prohibited. Lock ownership ends when the handle closes. A crashed process releases the OS lock. A stale lock file is not ownership and may remain safely. Lock-file reparse points are rejected.

## Recovery

Recovery validates `current.json` and only its referenced immutable version. It counts but ignores incomplete candidates. It never orders versions by timestamp, guesses a latest version, promotes an unreferenced candidate/version, changes freshness, or grants authority. Explicit recovery promotion requires a separate contract and owner authorisation.

## Resource limits

- Snapshot: 1 MiB
- Pointer: 4 KiB
- Publication evidence: 16 KiB
- Root path: 180 characters
- Recovery candidate reporting: bounded at 1024 entries
- Evidence references and semantic collections: bounded by the existing V1 snapshot validator

JSON duplicate members, non-ASCII/non-canonical encodings, unsupported fields, unsafe filenames, and oversized resources are rejected.

## Windows qualification and durability limitation

On Windows, `os.replace` provides same-filesystem name-replacement visibility atomicity, and `os.fsync` requests `FlushFileBuffers`-equivalent file synchronisation through CPython. These properties support complete-file visibility to concurrent readers.

They do not establish survival across every power loss, storage-controller cache failure, filesystem corruption, antivirus/filter-driver interference, or hardware fault. Python does not provide a portable Windows directory-handle fsync in this implementation. Directory-entry power-loss durability is therefore `NOT_ESTABLISHED`. Backups, protected directory ACLs, storage-health monitoring, and recovery qualification remain future controls.

No claim is made for non-Windows durability. No claim is made that local persistence is tamper-proof against an administrator or compromised operating system.

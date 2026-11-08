# ATIS Operator Console local authentication, transport and audit V1

Status: Task059 portable candidate. No listener activation, Windows configuration, deployment or trading authority.

## Exact local origin

The only qualified configuration accepted by the portable implementation is:

- browser origin: `http://localhost:8765`
- WebAuthn RP ID: `localhost`
- future socket bind: `127.0.0.1:8765`
- exact HTTP Host: `localhost:8765`

WebAuthn Level 3 permits HTTP only when the origin host is `localhost`; the RP ID is a domain string and must equal (or be a valid suffix of) the origin effective domain. Although Secure Contexts treats `127.0.0.0/8` as potentially trustworthy, that does not make `127.0.0.1` a valid HTTP WebAuthn RP origin under the WebAuthn rule. Therefore `localhost` and `127.0.0.1` are intentionally not interchangeable. Native Windows/target-browser evidence remains required. If the exact HTTP ceremony fails, the only allowed fallback is separately qualified loopback HTTPS; no security downgrade is permitted.

## Owner passkeys and recovery

Exactly two active owner credentials are required before authentication options are issued. Both use the same exact RP/origin, user-verification and cryptographic verification policy. The first credential requires a separately trusted offline bootstrap authority. The second can be enrolled only through an authenticated owner session. Registration completion also requires that session at the BFF boundary.

Either active passkey may authenticate after both are enrolled. Replacing a lost primary or secondary uses a separate recovery ceremony that allows only the surviving registered credential. Successful verification revokes the named missing credential and issues a five-minute, server-side, digest-stored recovery grant scoped only to one replacement registration. It does not create an operational session and cannot read the gateway. The replacement must pass the full RP, origin, user-verification and cryptographic checks. The grant is atomically consumed with registration, every previous operational session is revoked, and fresh ordinary authentication is required. New ordinary login ceremonies remain fail-closed while fewer than two credentials exist. Concurrent replacement attempts yield exactly one commit. Complete credential loss requires separate owner-authorised offline recovery and re-enrolment. There is no password, emergency token, default credential, filesystem-only reset or unauthenticated registration.

## Sessions

Sessions are opaque 256-bit random tokens. Only SHA-256 token digests and durable revocation state are stored in SQLite with full synchronous writes. Sessions have idle and absolute expiry, bounded active count, rotation support, explicit logout and owner revoke-all. CSRF tokens are separately random and stored as digests. Operational cookies are host-only, HttpOnly and SameSite=Strict. The current HTTP profile cannot set `Secure`; HTTPS qualification must add it. Tokens never appear in URLs or JavaScript-readable storage.

## BFF

`LocalOperatorConsoleBff` is an ASGI application object and opens no socket. It validates loopback clients and exact Host/Origin, defaults CORS to absent, applies bounded request/response sizes, a request deadline, connection semaphore, authentication/read limits, no-store and restrictive headers, path-contained static delivery and sanitized errors. API responses are explicitly classified and capped at 65,536 bytes. Allowlisted static assets are separately classified and capped at 5,242,880 bytes; the API cap is never enlarged. Only authentication/session routes, the replacement-only recovery ceremony, and `GET /api/v1/reconciliation/current` exist. Snapshot publication/recovery, broker, order, scheduler, shell and debug capabilities are absent.

The existing `OperatorConsoleReadGateway` is injected with a session authentication adapter and a read-only `SnapshotReader`. UNKNOWN, UNAVAILABLE, FRESHNESS_UNVERIFIED and NO_TRADING_AUTHORITY semantics remain owned by the existing gateway.

## Security audit

Security events use canonical JSON, monotonic sequence numbers, previous-digest linkage, SHA-256 event digests and HMAC-SHA-256. A non-blocking OS file lock provides exclusive append ownership across independent writers; a process-local guard also makes same-process contention explicit. Contention returns `AuditContentionError`, never last-writer-wins. Immutable event files are written and synced before an atomic head update, then verified. Interruption before the head transition leaves only an uncommitted tail that `recover_interrupted_append()` removes while exclusively locked; committed records are never deleted or rewritten. Interruption after head replacement returns `AuditCommitUnconfirmed`, and verification determines the committed state. The lock file may remain after a crash but carries no ownership when the OS handle closes. The store is bounded and fails closed at its retention boundary. Rotation is an offline procedure: verify and archive the complete closed directory, provision a new empty directory and protected key, and record the transition under separate owner authority. No committed record is silently deleted.

Audit storage is accepted only when every existing root and events-directory path component is an ordinary directory. Symbolic links, Windows junctions and paths carrying a detectable reparse-point attribute are rejected. The validated root and events identities are retained and rechecked before reads, locking, publication, head replacement and recovery; an unexpected substitution fails closed before the next protected operation. Existing head, writer-lock and committed-event paths are also rejected if link/reparse-backed. These checks reduce unprivileged substitution exposure but do not claim resistance to a privileged attacker who can win the residual check-to-use interval; native Windows verification remains mandatory.

The HMAC key comes only from `ProtectedAuditKey`; no plaintext loader exists. Tests use an explicit test provider. Production requires a separately qualified DPAPI provider and NTFS ACLs. The chain is tamper-evident and does not protect against a privileged administrator.

## Native qualification gate

Before operation on Windows: verify the exact browser ceremony; Python 3.12.10; DPAPI provider; dedicated non-admin reader identity; separate publisher identity; effective NTFS ACLs; native junction/reparse rejection for the corrected audit store; privileged path-swap limits; socket ownership and loopback-only binding; no firewall exception; port conflict; startup/shutdown/restart; audit recovery; and browser-closure independence. Portable Task065 tests do not establish those native facts.

PAPER ONLY. RESEARCH SIMULATION ONLY. NO_TRADING_AUTHORITY.

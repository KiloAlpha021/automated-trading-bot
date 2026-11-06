# ATIS Operator Console read gateway V1

Status: Task055A local candidate. Backend application service only. No deployment.

## Boundary

The gateway exposes one in-process `read()` operation over the qualified reconciliation snapshot reader. It has no HTTP server, listener, socket, scheduler, filesystem path, publication, recovery, broker, order-submission, React, or trading-authority capability.

It accepts an opaque credential and delegates authentication to a configured `TrustedAuthenticationBoundary`. Caller-supplied identity fields, roles, permissions, or booleans are never accepted directly as proof. The boundary must return an exact `AuthenticatedPrincipal` result; the gateway validates the principal identifier, permission set, authentication time, expiry, and UTC semantics. Access is denied by default and requires `reconciliation.observation.read`.

This contract does not claim production authentication. The injected authenticator and the process/object boundary that protects it require separate qualification before deployment.

## Read contract

The configured reader offers only `read_current(evaluated_at_utc=...)`. The gateway does not receive an authorised storage root or expose the persistence store's `publish()` or `recover()` operations.

Successful authorisation never changes reconciliation meaning:

- `UNAVAILABLE` remains `UNAVAILABLE`.
- Corruption, unsupported schemas, malformed results, or storage failures return `UNKNOWN`.
- A valid observation with `UNVERIFIED` freshness remains `UNKNOWN` with `FRESHNESS_UNVERIFIED`.
- `NO_TRADING_AUTHORITY` and simulation-only status are mandatory.
- An upstream `AVAILABLE` result is rejected because no freshness/current-health policy is qualified.

## Response allowlist

The response contains only:

- gateway schema version, state, and bounded reason codes;
- observation schema and observation identity;
- reconciliation state and discrepancy codes;
- observed and produced timestamps;
- freshness, simulation-only, and trading-authority markers;
- the existing allowlisted performance snapshot when reconciliation permits it.

Source-run identity, evidence references, filesystem paths, exception text, credentials, principal identity, roles, and internal authentication details are not serialized. JSON is deterministic ASCII with sorted keys and compact separators. Responses are limited to 65,536 bytes; oversize output fails closed without an observation.

## Failure semantics

Authentication absence, invalidity, expiry, malformed assertions, or missing permission prevents any storage read. Authentication-boundary exceptions become `AUTHENTICATION_UNAVAILABLE`. Storage exceptions become `STORAGE_READ_FAILED`. Unknown source reason text is replaced by `SOURCE_RESULT_UNTRUSTED`. Exception messages and paths are never returned.

## Trust and deployment limitations

- The Python protocol and injected test doubles prove application-service enforcement, not OS/process identity isolation.
- No native Windows qualification is performed by Task055A.
- No network transport, session binding, credential format, audit sink, rate limiter, process sandbox, service account, or deployment configuration is implemented.
- The existing snapshot-persistence durability and privileged path-swap limitations remain unchanged.
- PAPER ONLY, RESEARCH SIMULATION ONLY, and NO LIVE TRADING AUTHORITY remain mandatory.

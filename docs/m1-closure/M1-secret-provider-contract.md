# M1 secret-provider interface contract

Contract ID: M1-SECRET-PROVIDER-01. Atomic requirement: IMP-029-M1-03.
Provenance: INFERRED engineering contract derived from Implementation
Specification v1.0 §5.1 (injected provider, never config/source), §7 IMP-029,
§8 (references only), §11 (no live credentials or production paths), §18.4,
and R41-CI042–CI044. Source identity is in the normative block below.
Owner M1-SCOPE-2026-09-11-01 explicitly requires the interface CONTRACT only.

This artifact defines a typed boundary; it adds no runtime module, provider,
configuration loader, OS/environment lookup, local credential store, network
access or real credential. The sources do not explicitly require executable
provider code for this M1 atom. Existing secret scanning, replay denial and
operational-mode controls remain unchanged and are not substitutes for this
contract. Static verification is specification evidence, not proof that any
future provider enforces the contract or protects memory.

## Normative interface

The following interface description is specification data, not callable code.

```json
{
  "contract_id": "M1-SECRET-PROVIDER-01",
  "status": "SPECIFICATION_ONLY",
  "source_sha256": "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7",
  "owner_decision": "M1-SCOPE-2026-09-11-01",
  "lookup": {
    "operation": "resolve",
    "parameters": {
      "reference": "SecretReference",
      "now": "Timestamp"
    },
    "returns": "SecretHandle",
    "errors": "SecretLookupError(reason_code)",
    "enumeration": false,
    "fallback": false,
    "environment_source": "immutable_provider_binding",
    "privilege_source": "independently_authorized_binding",
    "reference_fields": [
      "environment",
      "namespace",
      "name",
      "required_version"
    ],
    "matching": "exact_case_sensitive",
    "name_pattern": "[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
  },
  "handle": {
    "metadata": [
      "reference",
      "version",
      "expires_at"
    ],
    "value_access": "explicit_scoped_use",
    "repr": "REDACTED",
    "serialization": false,
    "implicit_string_conversion": false,
    "cache_after_use": false,
    "revocation_check": "before_each_use",
    "value_type": "nonempty_bytes"
  },
  "failure_outcomes": {
    "missing": "DENY_NO_VALUE",
    "invalid_reference": "DENY_NO_VALUE",
    "malformed_value": "DENY_NO_VALUE",
    "access_denied": "DENY_NO_VALUE",
    "environment_mismatch": "DENY_NO_VALUE",
    "production_scope": "DENY_NO_VALUE",
    "provider_unavailable": "DENY_NO_VALUE",
    "timeout": "DENY_NO_VALUE",
    "integrity_unknown": "DENY_NO_VALUE",
    "version_mismatch": "DENY_NO_VALUE",
    "expired": "DENY_NO_VALUE",
    "revoked": "DENY_NO_VALUE",
    "revocation_unknown": "DENY_NO_VALUE"
  },
  "security": {
    "log_values": false,
    "exception_values": false,
    "exception_chaining": false,
    "persist_values": false,
    "configuration_literal_values": false,
    "configuration_grants_privilege": false,
    "retrieval_grants_financial_authority": false,
    "production_lookup_in_m1": false,
    "allowlist_required": true,
    "validate_before_backend_access": true,
    "unknown_denies": true
  },
  "error_surface": {
    "fields": [
      "reason_code"
    ],
    "message_source": "fixed_local_catalogue",
    "backend_messages": false
  },
  "m1_test_material": "opaque_noncredential_sentinels_only"
}
```

Signature: `resolve(reference: SecretReference, now: Timestamp) -> SecretHandle`.
Failure raises only `SecretLookupError(reason_code)` and returns no handle/value.
There is no list/search/enumerate operation. The provider is injected with an
immutable environment and an independently authorized principal, namespace/name/
version/purpose allowlist, and least-privilege read policy. The caller cannot
supply credentials or change those bindings through the lookup argument.

SecretReference is immutable nonsecret metadata: environment, namespace, name
and required_version are nonempty, case-sensitive strings matching name_pattern.
No path, URL, wildcard, whitespace normalization, implicit environment expansion,
embedded value, or production alias is accepted. Names identify a protected
resource; they are never interpreted as literal secret values or file locations.
required_version is exact: no implicit latest, previous-version or other-name
fallback. The reference identifies an authorized purpose through its allowlist
entry; possession of a reference does not confer access.

Validate shape, bound environment, privilege and M1 production exclusion before
any backend access. Absent/unknown authorization denies lookup without revealing
whether the name exists. Check source integrity and policy currency independently
of financial correctness. Administrative/configuration privilege is not provider
or trading privilege. Actual service identities are not created by M1.

## Return values, validation and failures

A successful handle binds the exact reference/version and a validated UTC
expires_at. Its value is nonempty bytes of the format/length allowed by the
reference's versioned provider policy. There is no universal credential format:
unknown format policy or invalid bytes denies access; no trimming, decoding,
substitution, empty default, or plaintext fallback is permitted.

SecretHandle is conceptually an opaque, nonserializable object. Explicit scoped
use is the only value-access operation; it yields bytes only to the authorized
consumer for the permitted operation. repr/str of handles and errors contain no
value; implicit string conversion is forbidden. Metadata may be inspected only
within the same authorized boundary. Do not include raw references in errors or
logs: arbitrary caller-controlled names could contain sensitive material.

Missing, malformed, unavailable, timeout, unauthorized, wrong-environment,
wrong-version, expired, revoked or unverifiable responses all deny. The public
error consists only of a fixed reason code from a local catalogue matching the
failure_outcomes keys. Do not propagate backend error text, repr, response bodies,
traceback-local values or exception chaining. Provider failures cannot become
an empty success, stale value, alternate environment, config read or cached fallback.

If an access-denied caller must not discover existence, access_denied is returned
before a backend query. Only an authorized exact lookup may distinguish missing
from provider failure. All failures remain non-authorizing. A failure reason is
operational evidence, never permission to use another credential.

## Secrecy and authority boundary

No values enter logs, metrics labels, traces, exceptions, source, configuration,
fixtures, databases, caches, artifacts or diagnostic captures. Configuration may
hold SecretReference metadata only. Configuration cannot instantiate a value,
claim successful retrieval, authorize a principal, or select a fallback store.
Environment/config files cannot be a hidden production provider.

Consumers must keep value use narrowly scoped and avoid copying/retaining bytes;
no value survives use as an application cache. Clearing references is not a claim
of guaranteed memory zeroization in Python. Future implementations must assess
memory/core-dump/debug handling separately and must not promise absolute erasure.
A wrapper cannot prevent a malicious consumer from copying bytes: independent
consumer privilege, sink controls and security review are required.

Retrieval yields no OMS, risk, compliance, capital, execution or resumption
authority. Even valid retrieval cannot bypass those gates. M1 forbids usable
production credentials and production lookup paths in every development,
research and replay context. Replay remains non-executable independently of any
provider outcome. This contract supplies neither broker authentication nor a
route capable of submitting an order.

## Rotation/revocation compatibility

Reference version pinning prevents silent rotation to a different credential.
A later independently authorized reference update may select a new version;
existing handles retain their original identity. A revoked/expired version is
never revived by retry, restart or configuration change.

Before each explicit use, verify the bound version is still authorized, not
revoked and not expired using validated UTC time; equality with expires_at denies.
Unknown/unavailable revocation evidence denies use. New resolves cannot return a
stale cached version. An already-in-flight external action is not retroactively
undone by revocation; external effects require the existing authoritative
reconciliation process. This is compatibility semantics only: production
rotation, monitoring and credential lifecycle implementation remain outside M1.

## Verification and deferred implementation

M1 checks compare the typed operation, value/error surface, denial cases and
security policy with this contract, and deliberately weaken them to ensure
validation rejects insecure specification changes. Tests use contract metadata
and booleans only: no real or simulated credential values are introduced.

Future executable conformance must use opaque noncredential sentinels and a
counting fake backend to prove unauthorized requests never touch the backend,
all failure paths expose no value, errors/logs remain sanitized, versions and
environments cannot fall back, and expiry/revocation deny use. These are future
provider tests, not claims of executed runtime protection in this task.

Local/dev implementation is not required by this contract decision. External
secret-manager integration, actual service identities, credential operational
lifecycle and backend policy enforcement remain deferred. A future adapter must
pass behavioral conformance before use; this document grants no lifecycle or
financial authorization.

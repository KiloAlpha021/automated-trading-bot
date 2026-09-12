# M1 scope clarification — owner decision

Decision ID: M1-SCOPE-2026-09-11-01

Authority: explicit project-owner governance instruction received 2026-09-11.
Affected requirements: IMP-026, IMP-029, IMP-030.
This decision resolves M1 applicability ambiguity; it does not change the
meaning of the parent requirements or authorize later-stage implementation.
The dispositions below reproduce the owner instruction without reinterpretation.

## Source lineage

- Implementation Specification v1.0, sections 7, 11 and 18.9.
- Specification SHA-256: `4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7`.
- Supporting canonical lineage: Technical Design v1.3, Master Audit Closure v1.4 Corrected, Master Invariant Register v1.4.
- Owner instruction SHA-256: `298ce22148c2f7b917c75fac2b5f6773e409a9ae089a2614c5a3dc2919b969d4`.
Historical origin (non-normative): explicit owner instruction received through Codex attachment ID `f114a63c-972e-4313-9412-991f5bb40e68`. The original machine-local path is not required for verification. Historical attachment SHA-256: `298ce22148c2f7b917c75fac2b5f6773e409a9ae089a2614c5a3dc2919b969d4`.
- Original source documents remain unchanged. This decision governs the affected M1 applicability questions over historical interpretations.
- Applicability is not proof of implementation or verification.

## Owner clarification (verbatim)


<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->
### IMP-026

M1_REQUIRED:
- Event duplicate rejection
- EventId as duplicate identity
- Initial event/outbox/inbox schema specification
- Initial migration specification
- Replay financial non-execution

DEFERRED UNTIL PERSISTENT/RUNTIME CAPABILITY EXISTS:
- Deduplication durability across process restart/failover
- Runtime transactional outbox/inbox
- Durable consumed-event + handler/version recording
- Financial-state reconstruction integrity controls
- Exposure/resumption controls dependent on authoritative reconstruction

Clarification:
M1 must define the persistence-related contracts/specification where required, but it does not implement persistence-dependent runtime guarantees while the M1 kernel remains financially inert and lacks that runtime capability.

### IMP-029

M1_REQUIRED:
- No live credentials
- No production secret paths
- Secret scanning
- No secret values in source/configuration/logs/databases/artifacts
- Secret-provider interface CONTRACT
- Application/authority-level least privilege
- Development/research/replay cannot possess production trading authority
- Administrative privilege cannot itself grant trading authority
- Unverified supply-chain integrity cannot create production financial authority

NOT_APPLICABLE_TO_M1:
- Production credential rotation
- Production credential monitoring
- Production credential operational lifecycle

Deferred:
- Production/service-identity least-privilege implementation details until those identities actually exist.

The M1 secret-provider requirement is a contract/interface requirement only. It does not authorize or require real credentials.

### IMP-030

M1_REQUIRED:
- Foundation logging
- Event causality
- Machine-validatable milestone evidence
- Structured logging for M1 components
- Minimum applicable metrics for M1 components
- Minimum applicable health conditions for M1 components
- Minimum applicable alert conditions for M1 components
- Missing telemetry cannot count as proof of health
- Telemetry cannot grant financial authority
- Detection of degradation of telemetry that M1 itself requires

DEFERRED:
- Full causal telemetry for executable financial workflows, because M1 contains no executable financial workflow
- Complete persistent reconstruction lineage until runtime persistence/reconstruction exists
- Trace-completeness SLO evidence to Stage 9 integrated hardening
<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->

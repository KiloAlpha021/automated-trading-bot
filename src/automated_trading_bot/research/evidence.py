"""Research-only evidence manifests composed from protected ATIS primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ExperimentId, HypothesisId, ResearchProjectId, ResearchRunId
from automated_trading_bot.research.reproducibility import ReproducibilityRecord


class ResearchEvidenceError(ValueError): pass
class EvidenceDisposition(StrEnum): VALID="VALID"; INVALID="INVALID"; UNKNOWN="UNKNOWN"; FAILED="FAILED"; REJECTED="REJECTED"
RESEARCH_ONLY_AUTHORITY = "RESEARCH_ONLY_NO_TRADING_AUTHORITY"


@dataclass(frozen=True, slots=True)
class ResearchEvidenceManifest:
    project_id: ResearchProjectId; hypothesis_id: HypothesisId; experiment_id: ExperimentId; run_id: ResearchRunId
    reproducibility: ReproducibilityRecord; input_identities: tuple[str, ...]; output_identities: tuple[str, ...]
    replay_of: ResearchRunId | None; disposition: EvidenceDisposition; reasons: tuple[str, ...]; authority: str; manifest_digest: str

    @classmethod
    def create(cls, *, project_id: ResearchProjectId, hypothesis_id: HypothesisId, experiment_id: ExperimentId, run_id: ResearchRunId, reproducibility: ReproducibilityRecord, input_identities: tuple[str, ...] = (), output_identities: tuple[str, ...] = (), replay_of: ResearchRunId | None = None, disposition: EvidenceDisposition, reasons: tuple[str, ...] = (), authority: str = RESEARCH_ONLY_AUTHORITY) -> "ResearchEvidenceManifest":
        expected = (ResearchProjectId, HypothesisId, ExperimentId, ResearchRunId)
        for value, kind in zip((project_id, hypothesis_id, experiment_id, run_id), expected, strict=True):
            if type(value) is not kind: raise TypeError(f"expected {kind.__name__}")
        if type(reproducibility) is not ReproducibilityRecord or type(disposition) is not EvidenceDisposition: raise TypeError("invalid evidence component")
        if authority != RESEARCH_ONLY_AUTHORITY: raise ResearchEvidenceError("trading authority is prohibited")
        for values, name in ((input_identities, "inputs"), (output_identities, "outputs"), (reasons, "reasons")):
            if type(values) is not tuple or len(set(values)) != len(values): raise ResearchEvidenceError(f"{name} must be a unique tuple")
            if any(type(v) is not str or not v or v != v.strip() for v in values): raise ResearchEvidenceError(f"{name} contains an invalid value")
        if disposition is EvidenceDisposition.VALID:
            if not reproducibility.authoritative: raise ResearchEvidenceError("VALID evidence requires a clean source")
            if reasons: raise ResearchEvidenceError("VALID evidence cannot carry failure reasons")
        elif not reasons: raise ResearchEvidenceError("non-VALID evidence requires reasons")
        body = {"authority": authority, "disposition": disposition.value, "experiment_id": experiment_id.value, "hypothesis_id": hypothesis_id.value, "inputs": list(input_identities), "outputs": list(output_identities), "project_id": project_id.value, "replay_of": replay_of.value if replay_of else None, "reproducibility_digest": reproducibility.digest, "run_id": run_id.value, "reasons": list(reasons)}
        digest = EvidenceContentDigest.from_bytes(b"ATIS-S4-01-RESEARCH-EVIDENCE-V1\0" + canonical_json(body)).value
        return cls(project_id, hypothesis_id, experiment_id, run_id, reproducibility, input_identities, output_identities, replay_of, disposition, reasons, authority, digest)

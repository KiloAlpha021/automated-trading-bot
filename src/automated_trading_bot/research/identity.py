"""Immutable, content-addressed identities for Stage-4 research."""

from __future__ import annotations

from dataclasses import dataclass
import re

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest


_ID = re.compile(
    r"atis:research:(project|hypothesis|experiment|run):v1:sha256:[0-9a-f]{64}"
)


class ResearchIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ResearchId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("identity must be a string")
        if _ID.fullmatch(self.value) is None:
            raise ResearchIdentityError("identity is not canonical")


class ResearchProjectId(_ResearchId):
    pass


class HypothesisId(_ResearchId):
    pass


class ExperimentId(_ResearchId):
    pass


class ResearchRunId(_ResearchId):
    pass


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ResearchIdentityError(f"{name} must be nonempty and unpadded")
    return value


def _derive(kind: str, body: object) -> str:
    digest = EvidenceContentDigest.from_bytes(
        f"ATIS-S4-01-{kind.upper()}-V1".encode("ascii") + b"\0" + canonical_json(body)
    ).value
    return f"atis:research:{kind}:v1:{digest}"


@dataclass(frozen=True, slots=True)
class ResearchProject:
    project_id: ResearchProjectId
    purpose: str

    @classmethod
    def create(cls, purpose: str) -> "ResearchProject":
        purpose = _text(purpose, "purpose")
        return cls(ResearchProjectId(_derive("project", {"purpose": purpose})), purpose)


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: HypothesisId
    project_id: ResearchProjectId
    proposition: str

    @classmethod
    def create(cls, project_id: ResearchProjectId, proposition: str) -> "Hypothesis":
        if type(project_id) is not ResearchProjectId:
            raise TypeError("project_id must be a ResearchProjectId")
        proposition = _text(proposition, "proposition")
        body = {"project_id": project_id.value, "proposition": proposition}
        return cls(HypothesisId(_derive("hypothesis", body)), project_id, proposition)


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: ExperimentId
    hypothesis_id: HypothesisId
    configuration_digest: str
    version: str
    purpose: str

    @classmethod
    def create(
        cls,
        hypothesis_id: HypothesisId,
        configuration_digest: str,
        version: str,
        purpose: str,
    ) -> "Experiment":
        if type(hypothesis_id) is not HypothesisId:
            raise TypeError("hypothesis_id must be a HypothesisId")
        configuration_digest = _text(configuration_digest, "configuration_digest")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", configuration_digest) is None:
            raise ResearchIdentityError("configuration_digest must be SHA-256")
        version, purpose = _text(version, "version"), _text(purpose, "purpose")
        body = {
            "configuration_digest": configuration_digest,
            "hypothesis_id": hypothesis_id.value,
            "purpose": purpose,
            "version": version,
        }
        return cls(
            ExperimentId(_derive("experiment", body)),
            hypothesis_id,
            configuration_digest,
            version,
            purpose,
        )


@dataclass(frozen=True, slots=True)
class ResearchRun:
    run_id: ResearchRunId
    experiment_id: ExperimentId
    reproducibility_digest: str
    replay_of: ResearchRunId | None = None

    @classmethod
    def create(
        cls,
        experiment_id: ExperimentId,
        reproducibility_digest: str,
        replay_of: ResearchRunId | None = None,
    ) -> "ResearchRun":
        if type(experiment_id) is not ExperimentId:
            raise TypeError("experiment_id must be an ExperimentId")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", reproducibility_digest) is None:
            raise ResearchIdentityError("reproducibility_digest must be SHA-256")
        if replay_of is not None and type(replay_of) is not ResearchRunId:
            raise TypeError("replay_of must be a ResearchRunId")
        body = {
            "experiment_id": experiment_id.value,
            "replay_of": replay_of.value if replay_of else None,
            "reproducibility_digest": reproducibility_digest,
        }
        identity = ResearchRunId(_derive("run", body))
        if replay_of == identity:
            raise ResearchIdentityError("a run cannot replay itself")
        return cls(identity, experiment_id, reproducibility_digest, replay_of)

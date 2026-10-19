"""Canonical S4-01 source, environment, configuration and replay bindings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest
from automated_trading_bot.research.identity import ExperimentId, ResearchRunId


class ReproducibilityError(ValueError): pass
class WorkingTreeState(StrEnum): CLEAN="CLEAN"; DIRTY="DIRTY"; UNKNOWN="UNKNOWN"
class PrngUsage(StrEnum): NONE="NONE"; DETERMINISTIC="DETERMINISTIC"; INVALID="INVALID"


def _text(value: object, name: str) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    if not value or value != value.strip(): raise ReproducibilityError(f"{name} must be nonempty and unpadded")
    return value


def _sha(value: object, name: str) -> str:
    value = _text(value, name)
    if re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value) is None: raise ReproducibilityError(f"{name} must be SHA-256")
    return value if value.startswith("sha256:") else f"sha256:{value}"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    commit: str; tree: str; working_tree: WorkingTreeState
    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", self.commit) is None or re.fullmatch(r"[0-9a-f]{40}", self.tree) is None: raise ReproducibilityError("commit and tree must be lowercase Git identities")
        if type(self.working_tree) is not WorkingTreeState: raise TypeError("working_tree must be WorkingTreeState")
    @property
    def authoritative(self) -> bool: return self.working_tree is WorkingTreeState.CLEAN


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    python_version: str; executable: str; platform: str; architecture: str
    def __post_init__(self) -> None:
        for name in ("python_version", "executable", "platform", "architecture"): _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class PrngIdentity:
    usage: PrngUsage; algorithm: str | None = None; seed: int | None = None; state_or_policy: str | None = None
    def __post_init__(self) -> None:
        if type(self.usage) is not PrngUsage: raise TypeError("usage must be PrngUsage")
        if self.usage is PrngUsage.NONE:
            if any(v is not None for v in (self.algorithm, self.seed, self.state_or_policy)): raise ReproducibilityError("PRNG NONE cannot carry random state")
        elif self.usage is PrngUsage.DETERMINISTIC:
            _text(self.algorithm, "algorithm"); _text(self.state_or_policy, "state_or_policy")
            if type(self.seed) is not int or type(self.seed) is bool: raise ReproducibilityError("deterministic PRNG requires an integer seed")
        else: raise ReproducibilityError("invalid or ambient randomness is prohibited")


@dataclass(frozen=True, slots=True)
class ReproducibilityRecord:
    source: SourceIdentity; runtime: RuntimeIdentity; dependency_lock_digest: str; configuration: object; configuration_digest: str; prng: PrngIdentity; clock_identity: str | None; dataset_identities: tuple[str, ...]; digest: str

    @classmethod
    def create(cls, *, source: SourceIdentity, runtime: RuntimeIdentity, dependency_lock_digest: str, configuration: object, prng: PrngIdentity, clock_identity: str | None = None, dataset_identities: tuple[str, ...] = ()) -> "ReproducibilityRecord":
        if type(source) is not SourceIdentity or type(runtime) is not RuntimeIdentity or type(prng) is not PrngIdentity: raise TypeError("invalid reproducibility component")
        lock = _sha(dependency_lock_digest, "dependency_lock_digest")
        config_digest = EvidenceContentDigest.from_bytes(canonical_json(configuration)).value
        if type(dataset_identities) is not tuple or len(set(dataset_identities)) != len(dataset_identities): raise ReproducibilityError("dataset identities must be a unique tuple")
        datasets = tuple(sorted(_text(v, "dataset_identity") for v in dataset_identities))
        if clock_identity is not None: _text(clock_identity, "clock_identity")
        body = {"clock_identity": clock_identity, "configuration_digest": config_digest, "dataset_identities": list(datasets), "dependency_lock_digest": lock, "prng": {"algorithm": prng.algorithm, "seed": prng.seed, "state_or_policy": prng.state_or_policy, "usage": prng.usage.value}, "runtime": {"architecture": runtime.architecture, "executable": runtime.executable, "platform": runtime.platform, "python_version": runtime.python_version}, "source": {"commit": source.commit, "tree": source.tree, "working_tree": source.working_tree.value}}
        digest = EvidenceContentDigest.from_bytes(b"ATIS-S4-01-REPRODUCIBILITY-V1\0" + canonical_json(body)).value
        return cls(source, runtime, lock, configuration, config_digest, prng, clock_identity, datasets, digest)

    @property
    def authoritative(self) -> bool: return self.source.authoritative


@dataclass(frozen=True, slots=True)
class ReplayIdentity:
    replay_of: ResearchRunId; experiment_id: ExperimentId; reproducibility_digest: str; dataset_identities: tuple[str, ...]
    def __post_init__(self) -> None:
        if type(self.replay_of) is not ResearchRunId or type(self.experiment_id) is not ExperimentId: raise TypeError("invalid replay identity")
        object.__setattr__(self, "reproducibility_digest", _sha(self.reproducibility_digest, "reproducibility_digest"))
        if type(self.dataset_identities) is not tuple or len(set(self.dataset_identities)) != len(self.dataset_identities): raise ReproducibilityError("replay datasets must be unique")

    def matches(self, experiment_id: ExperimentId, reproducibility_digest: str, dataset_identities: tuple[str, ...]) -> bool:
        return self.experiment_id == experiment_id and self.reproducibility_digest == _sha(reproducibility_digest, "reproducibility_digest") and self.dataset_identities == dataset_identities

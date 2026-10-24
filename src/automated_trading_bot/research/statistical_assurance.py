"""Deterministic statistical assurance for Stage 4 research evidence."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class StatisticalAssuranceError(ValueError):
    """Raised when statistical evidence violates the frozen contract."""


class AssuranceOutcome(StrEnum):
    TEST_PASS = "TEST_PASS"
    TEST_FAIL = "TEST_FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID = "INVALID"


class IntervalMethod(StrEnum):
    PERCENTILE_MOVING_BLOCK = "PERCENTILE_MOVING_BLOCK"


@dataclass(frozen=True, slots=True)
class SampleAdequacy:
    established: bool
    effective_sample_size: Decimal
    rule_identity: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.established) is not bool:
            raise TypeError("established must be bool")
        _finite(self.effective_sample_size, "effective_sample_size")
        if self.effective_sample_size < 0:
            raise StatisticalAssuranceError("effective sample size cannot be negative")
        _text(self.rule_identity, "rule_identity")
        _text_tuple(self.reasons, "reasons")
        if self.established == bool(self.reasons):
            raise StatisticalAssuranceError("adequacy reasons contradict status")


@dataclass(frozen=True, slots=True)
class StatisticalSample:
    sample_id: str
    research_run_id: str
    dataset_id: str
    dataset_version: str
    partition_id: str
    observations: tuple[Decimal, ...]
    observation_ids: tuple[str, ...]
    overlapping: bool
    adequacy: SampleAdequacy

    def __post_init__(self) -> None:
        for name in (
            "sample_id",
            "research_run_id",
            "dataset_id",
            "dataset_version",
            "partition_id",
        ):
            _text(getattr(self, name), name)
        if type(self.observations) is not tuple or not self.observations:
            raise StatisticalAssuranceError("nonempty observation tuple required")
        for value in self.observations:
            _finite(value, "observation")
        if (
            type(self.observation_ids) is not tuple
            or len(self.observation_ids) != len(self.observations)
            or len(set(self.observation_ids)) != len(self.observation_ids)
        ):
            raise StatisticalAssuranceError("observation identities are incomplete")
        _text_tuple(self.observation_ids, "observation_ids")
        if type(self.overlapping) is not bool or type(self.adequacy) is not SampleAdequacy:
            raise TypeError("invalid sample metadata")


@dataclass(frozen=True, slots=True)
class HypothesisTest:
    hypothesis_id: str
    p_value: Decimal

    def __post_init__(self) -> None:
        _text(self.hypothesis_id, "hypothesis_id")
        _finite(self.p_value, "p_value")
        if not Decimal("0") <= self.p_value <= Decimal("1"):
            raise StatisticalAssuranceError("p-value outside [0, 1]")


@dataclass(frozen=True, slots=True)
class HolmDecision:
    hypothesis_id: str
    raw_p_value: Decimal
    adjusted_p_value: Decimal
    rejected: bool


@dataclass(frozen=True, slots=True)
class HolmResult:
    family_id: str
    alpha: Decimal
    decisions: tuple[HolmDecision, ...]
    registered_trial_ids: tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class ResamplingConfig:
    method: IntervalMethod
    seed: int
    block_length: int
    resamples: int
    confidence_level: Decimal = Decimal("0.95")

    def __post_init__(self) -> None:
        if type(self.method) is not IntervalMethod:
            raise TypeError("method must be IntervalMethod")
        if type(self.seed) is not int or self.seed < 0:
            raise StatisticalAssuranceError("seed must be a nonnegative integer")
        if type(self.block_length) is not int or self.block_length < 1:
            raise StatisticalAssuranceError("block length must be positive")
        if type(self.resamples) is not int or self.resamples < 1:
            raise StatisticalAssuranceError("resample count must be positive")
        _finite(self.confidence_level, "confidence_level")
        if not Decimal("0") < self.confidence_level < Decimal("1"):
            raise StatisticalAssuranceError("confidence level must be in (0, 1)")


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    outcome: AssuranceOutcome
    sample_id: str
    mean: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    config: ResamplingConfig
    reasons: tuple[str, ...]
    evidence_digest: str


def holm_adjust(
    *,
    family_id: str,
    tests: tuple[HypothesisTest, ...],
    registered_trial_ids: tuple[str, ...],
    alpha: Decimal = Decimal("0.05"),
) -> HolmResult:
    """Apply Holm's step-down familywise correction to a complete family."""
    _text(family_id, "family_id")
    _finite(alpha, "alpha")
    if not Decimal("0") < alpha < Decimal("1"):
        raise StatisticalAssuranceError("alpha must be in (0, 1)")
    if type(tests) is not tuple or not tests or any(type(v) is not HypothesisTest for v in tests):
        raise StatisticalAssuranceError("nonempty hypothesis family required")
    if len({item.hypothesis_id for item in tests}) != len(tests):
        raise StatisticalAssuranceError("duplicate hypothesis identity")
    _text_tuple(registered_trial_ids, "registered_trial_ids")
    if len(set(registered_trial_ids)) != len(registered_trial_ids):
        raise StatisticalAssuranceError("duplicate trial identity")
    if set(registered_trial_ids) != {item.hypothesis_id for item in tests}:
        raise StatisticalAssuranceError("complete registered trial family required")

    ordered = sorted(tests, key=lambda item: (item.p_value, item.hypothesis_id))
    rejected: set[str] = set()
    active = True
    running_adjusted = Decimal("0")
    adjusted: dict[str, Decimal] = {}
    total = len(ordered)
    for index, item in enumerate(ordered):
        multiplier = Decimal(total - index)
        running_adjusted = max(running_adjusted, min(Decimal("1"), item.p_value * multiplier))
        adjusted[item.hypothesis_id] = running_adjusted
        threshold = alpha / multiplier
        if active and item.p_value <= threshold:
            rejected.add(item.hypothesis_id)
        else:
            active = False
    decisions = tuple(
        HolmDecision(item.hypothesis_id, item.p_value, adjusted[item.hypothesis_id], item.hypothesis_id in rejected)
        for item in tests
    )
    body = {
        "family_id": family_id,
        "alpha": _decimal_text(alpha),
        "decisions": [
            [d.hypothesis_id, _decimal_text(d.raw_p_value), _decimal_text(d.adjusted_p_value), d.rejected]
            for d in decisions
        ],
        "registered_trial_ids": list(registered_trial_ids),
    }
    return HolmResult(family_id, alpha, decisions, registered_trial_ids, _digest("HOLM-V1", body))


def moving_block_bootstrap_mean(
    sample: StatisticalSample, config: ResamplingConfig
) -> BootstrapResult:
    """Return a deterministic percentile moving-block interval for the sample mean."""
    if type(sample) is not StatisticalSample or type(config) is not ResamplingConfig:
        raise TypeError("validated sample and resampling configuration required")
    reasons: list[str] = []
    if not sample.adequacy.established:
        reasons.extend(sample.adequacy.reasons)
    if config.block_length > len(sample.observations):
        reasons.append("block length exceeds sample")
    if sample.overlapping and config.block_length == 1:
        reasons.append("overlapping returns require dependence-aware block length")
    if len(set(sample.observations)) == 1:
        reasons.append("zero variance cannot support significance")
    if reasons:
        body = _bootstrap_body(sample, config, None, None, None, reasons)
        return BootstrapResult(
            AssuranceOutcome.INSUFFICIENT_EVIDENCE,
            sample.sample_id,
            None,
            None,
            None,
            config,
            tuple(reasons),
            _digest("BOOTSTRAP-V1", body),
        )

    rng = random.Random(config.seed)
    values = sample.observations
    block_count = math.ceil(len(values) / config.block_length)
    means: list[Decimal] = []
    for _ in range(config.resamples):
        draw: list[Decimal] = []
        for _ in range(block_count):
            start = rng.randrange(len(values))
            draw.extend(values[(start + offset) % len(values)] for offset in range(config.block_length))
        draw = draw[: len(values)]
        means.append(sum(draw, Decimal("0")) / Decimal(len(draw)))
    means.sort()
    tail = (Decimal("1") - config.confidence_level) / Decimal("2")
    lower_index = _quantile_index(tail, len(means))
    upper_index = _quantile_index(Decimal("1") - tail, len(means))
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    lower = means[lower_index]
    upper = means[upper_index]
    body = _bootstrap_body(sample, config, mean, lower, upper, [])
    return BootstrapResult(
        AssuranceOutcome.TEST_PASS,
        sample.sample_id,
        mean,
        lower,
        upper,
        config,
        (),
        _digest("BOOTSTRAP-V1", body),
    )


def _bootstrap_body(
    sample: StatisticalSample,
    config: ResamplingConfig,
    mean: Decimal | None,
    lower: Decimal | None,
    upper: Decimal | None,
    reasons: list[str],
) -> dict[str, object]:
    return {
        "sample": _sample_body(sample),
        "config": {
            "method": config.method.value,
            "seed": config.seed,
            "block_length": config.block_length,
            "resamples": config.resamples,
            "confidence_level": _decimal_text(config.confidence_level),
        },
        "mean": _optional_decimal(mean),
        "lower": _optional_decimal(lower),
        "upper": _optional_decimal(upper),
        "reasons": reasons,
    }


def _sample_body(sample: StatisticalSample) -> dict[str, object]:
    return {
        "sample_id": sample.sample_id,
        "research_run_id": sample.research_run_id,
        "dataset_id": sample.dataset_id,
        "dataset_version": sample.dataset_version,
        "partition_id": sample.partition_id,
        "observations": [_decimal_text(v) for v in sample.observations],
        "observation_ids": list(sample.observation_ids),
        "overlapping": sample.overlapping,
        "adequacy": {
            "established": sample.adequacy.established,
            "effective_sample_size": _decimal_text(sample.adequacy.effective_sample_size),
            "rule_identity": sample.adequacy.rule_identity,
            "reasons": list(sample.adequacy.reasons),
        },
    }


def _quantile_index(probability: Decimal, count: int) -> int:
    return max(0, min(count - 1, int((probability * Decimal(count - 1)).to_integral_value())))


def _digest(domain: str, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(domain.encode() + b"\x00" + raw).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _finite(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise StatisticalAssuranceError(f"{name} must be finite")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StatisticalAssuranceError(f"{name} must be nonempty text")
    return value


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be tuple")
    for item in value:
        _text(item, name)
    return value

"""Point-in-time feature identity, lineage, and decision eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.domain.identifiers import InstrumentId
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import EvidenceContentDigest, ListingId
from automated_trading_bot.research.dataset_qualification import (
    ClaimCeiling,
    DatasetQualification,
    QualificationLifecycle,
    permits_claim,
)


class FeatureContractError(ValueError):
    pass


class MissingDataPolicy(StrEnum):
    REJECT = "REJECT"
    EXPLICIT_MISSING = "EXPLICIT_MISSING"


class AvailabilityPolicy(StrEnum):
    AT_KNOWLEDGE = "AT_KNOWLEDGE"
    AFTER_EVENT = "AFTER_EVENT"


class InputState(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"


class EligibilityState(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _identity(value: object, name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise FeatureContractError(f"{name} must be a canonical identity")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise FeatureContractError(f"{name} must be a SHA-256 digest")
    return value


def _time(value: Timestamp) -> str:
    if type(value) is not Timestamp:
        raise TypeError("expected Timestamp")
    return value.value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _derive(domain: bytes, body: object) -> str:
    return EvidenceContentDigest.from_bytes(domain + b"\0" + canonical_json(body)).value


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    feature_id: str
    version: str
    algorithm_id: str
    configuration_digest: str
    required_inputs: tuple[str, ...]
    lookback: int
    minimum_history: int
    missing_data_policy: MissingDataPolicy
    availability_policy: AvailabilityPolicy
    output_semantics: str

    @classmethod
    def create(
        cls,
        *,
        version: str,
        algorithm_id: str,
        configuration_digest: str,
        required_inputs: tuple[str, ...],
        lookback: int,
        minimum_history: int,
        missing_data_policy: MissingDataPolicy,
        availability_policy: AvailabilityPolicy,
        output_semantics: str,
    ) -> "FeatureDefinition":
        version = _identity(version, "version")
        algorithm_id = _identity(algorithm_id, "algorithm_id")
        configuration_digest = _digest(configuration_digest, "configuration_digest")
        output_semantics = _identity(output_semantics, "output_semantics")
        if (
            type(required_inputs) is not tuple
            or not required_inputs
            or len(set(required_inputs)) != len(required_inputs)
        ):
            raise FeatureContractError(
                "required_inputs must be a nonempty unique tuple"
            )
        required_inputs = tuple(_identity(v, "required_input") for v in required_inputs)
        if (
            type(lookback) is not int
            or type(minimum_history) is not int
            or lookback < 1
            or minimum_history < 1
            or minimum_history > lookback
        ):
            raise FeatureContractError("invalid lookback or minimum history")
        if (
            type(missing_data_policy) is not MissingDataPolicy
            or type(availability_policy) is not AvailabilityPolicy
        ):
            raise TypeError("invalid feature policy")
        body = {
            "algorithm_id": algorithm_id,
            "availability_policy": availability_policy.value,
            "configuration_digest": configuration_digest,
            "lookback": lookback,
            "minimum_history": minimum_history,
            "missing_data_policy": missing_data_policy.value,
            "output_semantics": output_semantics,
            "required_inputs": list(required_inputs),
            "version": version,
        }
        feature_id = "atis:research:feature:v1:" + _derive(
            b"ATIS-S4-03-FEATURE-DEFINITION-V1", body
        )
        return cls(
            feature_id,
            version,
            algorithm_id,
            configuration_digest,
            required_inputs,
            lookback,
            minimum_history,
            missing_data_policy,
            availability_policy,
            output_semantics,
        )


@dataclass(frozen=True, slots=True)
class PitInput:
    input_id: str
    input_kind: str
    dataset_id: str
    source_identity: str
    evidence_digest: str
    event_time: Timestamp
    knowledge_time: Timestamp | None
    state: InputState
    retrospective_correction: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.input_id, "input_id"),
            (self.input_kind, "input_kind"),
            (self.dataset_id, "dataset_id"),
            (self.source_identity, "source_identity"),
        ):
            _identity(value, name)
        _digest(self.evidence_digest, "evidence_digest")
        if type(self.event_time) is not Timestamp or (
            self.knowledge_time is not None
            and type(self.knowledge_time) is not Timestamp
        ):
            raise TypeError("invalid input time")
        if (
            type(self.state) is not InputState
            or type(self.retrospective_correction) is not bool
        ):
            raise TypeError("invalid input state")


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observation_id: str
    feature_id: str
    instrument_id: InstrumentId
    listing_id: ListingId
    dataset_id: str
    qualification_id: str
    input_ids: tuple[str, ...]
    input_window_start: Timestamp
    input_window_end: Timestamp
    available_at: Timestamp
    value_digest: str
    evidence_digest: str


def create_feature_observation(
    *,
    definition: FeatureDefinition,
    instrument_id: InstrumentId,
    listing_id: ListingId,
    qualification: DatasetQualification,
    inputs: tuple[PitInput, ...],
    available_at: Timestamp,
    value_digest: str,
) -> FeatureObservation:
    if (
        type(definition) is not FeatureDefinition
        or type(instrument_id) is not InstrumentId
        or type(listing_id) is not ListingId
        or type(qualification) is not DatasetQualification
    ):
        raise TypeError("invalid observation component")
    if (
        type(inputs) is not tuple
        or not inputs
        or any(type(v) is not PitInput for v in inputs)
    ):
        raise FeatureContractError("inputs must be a nonempty tuple")
    if len({v.input_id for v in inputs}) != len(inputs):
        raise FeatureContractError("duplicate input identity")
    if any(v.dataset_id != qualification.dataset_id for v in inputs):
        raise FeatureContractError("dataset substitution")
    if type(available_at) is not Timestamp:
        raise TypeError("available_at must be Timestamp")
    value_digest = _digest(value_digest, "value_digest")
    ordered = tuple(sorted(inputs, key=lambda v: (_time(v.event_time), v.input_id)))
    body = {
        "available_at": _time(available_at),
        "dataset_id": qualification.dataset_id,
        "definition": definition.feature_id,
        "inputs": [
            {
                "evidence_digest": v.evidence_digest,
                "event_time": _time(v.event_time),
                "id": v.input_id,
                "knowledge_time": _time(v.knowledge_time) if v.knowledge_time else None,
                "source": v.source_identity,
                "state": v.state.value,
            }
            for v in ordered
        ],
        "instrument_id": instrument_id.to_string(),
        "listing_id": listing_id.to_string(),
        "qualification_id": qualification.qualification_id,
        "value_digest": value_digest,
    }
    evidence = _derive(b"ATIS-S4-03-FEATURE-OBSERVATION-EVIDENCE-V1", body)
    observation_id = "atis:research:feature-observation:v1:" + _derive(
        b"ATIS-S4-03-FEATURE-OBSERVATION-V1", body
    )
    return FeatureObservation(
        observation_id,
        definition.feature_id,
        instrument_id,
        listing_id,
        qualification.dataset_id,
        qualification.qualification_id,
        tuple(v.input_id for v in ordered),
        ordered[0].event_time,
        ordered[-1].event_time,
        available_at,
        value_digest,
        evidence,
    )


@dataclass(frozen=True, slots=True)
class DecisionInputEligibility:
    state: EligibilityState
    reasons: tuple[str, ...]
    observation_id: str
    decision_time: Timestamp
    requested_claim: ClaimCeiling
    authority: str = "RESEARCH_INPUT_ONLY_NO_TRADING_AUTHORITY"


def assess_decision_input(
    *,
    definition: FeatureDefinition,
    observation: FeatureObservation,
    qualification: DatasetQualification,
    inputs: tuple[PitInput, ...],
    decision_time: Timestamp,
    requested_claim: ClaimCeiling,
    causal_same_instant_established: bool = False,
) -> DecisionInputEligibility:
    if (
        type(definition) is not FeatureDefinition
        or type(observation) is not FeatureObservation
        or type(qualification) is not DatasetQualification
    ):
        raise TypeError("invalid eligibility component")
    if (
        type(decision_time) is not Timestamp
        or type(requested_claim) is not ClaimCeiling
        or type(causal_same_instant_established) is not bool
    ):
        raise TypeError("invalid decision request")
    reasons = []
    if observation.feature_id != definition.feature_id:
        reasons.append("FEATURE_DEFINITION_SUBSTITUTION")
    if observation.dataset_id != qualification.dataset_id:
        reasons.append("DATASET_SUBSTITUTION")
    if observation.qualification_id != qualification.qualification_id:
        reasons.append("QUALIFICATION_SUBSTITUTION")
    if qualification.lifecycle is not QualificationLifecycle.CURRENT:
        reasons.append("QUALIFICATION_NOT_CURRENT")
    if requested_claim is ClaimCeiling.CONTRACT_ONLY or not permits_claim(
        qualification, requested_claim
    ):
        reasons.append("CLAIM_NOT_PERMITTED")
    if not (
        qualification.coverage.start <= observation.input_window_start.value.date()
        and observation.input_window_end.value.date() <= qualification.coverage.end
    ):
        reasons.append("OUTSIDE_QUALIFICATION_COVERAGE")
    if observation.available_at.value > decision_time.value:
        reasons.append("FEATURE_AFTER_DECISION")
    if len(inputs) < definition.minimum_history:
        reasons.append("INSUFFICIENT_HISTORY")
    if tuple(sorted(v.input_id for v in inputs)) != tuple(
        sorted(observation.input_ids)
    ):
        reasons.append("INPUT_LINEAGE_SUBSTITUTION")
    for value in inputs:
        if value.knowledge_time is None:
            reasons.append("UNKNOWN_KNOWLEDGE_TIME")
        elif value.knowledge_time.value > decision_time.value:
            reasons.append("FUTURE_KNOWLEDGE")
        if value.state is not InputState.VALID:
            reasons.append(f"INPUT_{value.state.value}")
        if value.retrospective_correction and (
            value.knowledge_time is None
            or value.knowledge_time.value > decision_time.value
        ):
            reasons.append("RETROSPECTIVE_CORRECTION_NOT_KNOWN")
        if (
            value.event_time.value == decision_time.value
            and not causal_same_instant_established
        ):
            reasons.append("UNSUPPORTED_SAME_INSTANT_USE")
    canonical = tuple(sorted(set(reasons)))
    return DecisionInputEligibility(
        EligibilityState.INELIGIBLE if canonical else EligibilityState.ELIGIBLE,
        canonical,
        observation.observation_id,
        decision_time,
        requested_claim,
    )


NO_TRADING_AUTHORITY = "FEATURE_ELIGIBILITY_ONLY_NO_STRATEGY_ORDER_OR_CAPITAL_AUTHORITY"

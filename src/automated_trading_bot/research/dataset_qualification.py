"""S4-02 immutable research-dataset qualification and claim ceilings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum, StrEnum
import re
import unicodedata

from automated_trading_bot.datasets.provenance import canonical_json
from automated_trading_bot.instruments.model import EvidenceContentDigest


class QualificationError(ValueError):
    pass


class AssessmentState(StrEnum):
    PASS = "PASS"
    RESTRICTED = "RESTRICTED"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class QualificationLevel(IntEnum):
    G0 = 0
    G1 = 1
    G2 = 2
    G3 = 3


class ClaimCeiling(StrEnum):
    CONTRACT_ONLY = "CONTRACT_ONLY"
    ENGINEERING_ONLY = "ENGINEERING_ONLY"
    EXPLORATORY_RESEARCH = "EXPLORATORY_RESEARCH"
    ECONOMIC_VALIDATION = "ECONOMIC_VALIDATION"


class QualificationLifecycle(StrEnum):
    CURRENT = "CURRENT"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


DIMENSIONS = (
    "Q01_IDENTITY",
    "Q02_PROVENANCE",
    "Q03_COVERAGE",
    "Q04_QUALITY_QUARANTINE",
    "Q05_CURRENTNESS",
    "Q06_INSTRUMENT_CALENDAR",
    "Q07_PIT_KNOWLEDGE_TIME",
    "Q08_SURVIVORSHIP_HISTORICAL_UNIVERSE",
    "Q09_CORPORATE_ACTIONS",
    "Q10_DELISTING_TERMINAL_TREATMENT",
)
CEILINGS = {
    QualificationLevel.G0: ClaimCeiling.CONTRACT_ONLY,
    QualificationLevel.G1: ClaimCeiling.ENGINEERING_ONLY,
    QualificationLevel.G2: ClaimCeiling.EXPLORATORY_RESEARCH,
    QualificationLevel.G3: ClaimCeiling.ECONOMIC_VALIDATION,
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}")


def _text(value: object, name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise QualificationError(f"{name} must be a canonical attributable identity")
    return value


def _descriptive_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if (
        not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
    ):
        raise QualificationError(f"{name} must be nonempty canonical text")
    return value


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    policy_id: str
    version: str
    evidence_ref: str

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id")
        _text(self.version, "version")
        _text(self.evidence_ref, "evidence_ref")


@dataclass(frozen=True, slots=True)
class DimensionAssessment:
    dimension: str
    state: AssessmentState
    evidence_refs: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise QualificationError("unknown qualification dimension")
        if type(self.state) is not AssessmentState:
            raise TypeError("state must be AssessmentState")
        if (
            type(self.evidence_refs) is not tuple
            or not self.evidence_refs
            or len(set(self.evidence_refs)) != len(self.evidence_refs)
        ):
            raise QualificationError(
                "assessment evidence must be a nonempty unique tuple"
            )
        for value in self.evidence_refs:
            _text(value, "evidence_ref")
        if self.state is not AssessmentState.PASS and not self.reasons:
            raise QualificationError("non-PASS assessment requires reasons")


@dataclass(frozen=True, slots=True)
class EligibleCoverage:
    start: date
    end: date

    def __post_init__(self) -> None:
        if type(self.start) is not date or type(self.end) is not date:
            raise TypeError("coverage boundaries must be dates")
        if self.start > self.end:
            raise QualificationError("coverage start exceeds end")


@dataclass(frozen=True, slots=True)
class DatasetQualification:
    qualification_id: str
    dataset_id: str
    dataset_version: str
    manifest_digest: str
    provenance_digest: str
    policy: QualificationPolicy
    level: QualificationLevel
    claim_ceiling: ClaimCeiling
    assessments: tuple[DimensionAssessment, ...]
    coverage: EligibleCoverage
    limitations: tuple[str, ...]
    predecessor_qualification_id: str | None
    lifecycle: QualificationLifecycle
    invalidation_reasons: tuple[str, ...]
    superseded_by: str | None


def _digest(value: str, name: str) -> str:
    value = _text(value, name)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise QualificationError(f"{name} must be SHA-256")
    return value


def _assessment_body(value: DimensionAssessment) -> dict[str, object]:
    return {
        "dimension": value.dimension,
        "evidence_refs": list(value.evidence_refs),
        "reasons": list(value.reasons),
        "state": value.state.value,
    }


def qualify_dataset(
    *,
    dataset_id: str,
    dataset_version: str,
    manifest_digest: str,
    provenance_digest: str,
    policy: QualificationPolicy,
    requested_level: QualificationLevel,
    assessments: tuple[DimensionAssessment, ...],
    coverage: EligibleCoverage,
    limitations: tuple[str, ...] = (),
    predecessor: DatasetQualification | None = None,
) -> DatasetQualification:
    dataset_id = _text(dataset_id, "dataset_id")
    dataset_version = _text(dataset_version, "dataset_version")
    manifest_digest = _digest(manifest_digest, "manifest_digest")
    provenance_digest = _digest(provenance_digest, "provenance_digest")
    if (
        type(policy) is not QualificationPolicy
        or type(requested_level) is not QualificationLevel
        or type(coverage) is not EligibleCoverage
    ):
        raise TypeError("invalid qualification component")
    if requested_level is QualificationLevel.G0:
        raise QualificationError(
            "G0 is the contract state, not a dataset qualification"
        )
    expected_previous = QualificationLevel(requested_level - 1)
    if predecessor is None:
        if requested_level is not QualificationLevel.G1:
            raise QualificationError("qualification level skipping is prohibited")
        predecessor_id = None
    else:
        if (
            type(predecessor) is not DatasetQualification
            or predecessor.lifecycle is not QualificationLifecycle.CURRENT
        ):
            raise QualificationError("current predecessor required")
        if (
            predecessor.dataset_id != dataset_id
            or predecessor.dataset_version != dataset_version
        ):
            raise QualificationError("predecessor dataset substitution")
        if predecessor.level is not expected_previous:
            raise QualificationError("qualification level skipping is prohibited")
        predecessor_id = predecessor.qualification_id
    if (
        type(assessments) is not tuple
        or {a.dimension for a in assessments} != set(DIMENSIONS)
        or len(assessments) != len(DIMENSIONS)
    ):
        raise QualificationError(
            "exactly ten unique dimension assessments are required"
        )
    ordered = tuple(sorted(assessments, key=lambda a: DIMENSIONS.index(a.dimension)))
    material_count = 6 if requested_level is QualificationLevel.G1 else 10
    for item in ordered[:material_count]:
        if item.state in (AssessmentState.FAIL, AssessmentState.UNKNOWN):
            raise QualificationError(
                f"material {item.state.value} prohibits qualification"
            )
        if item.state is AssessmentState.NOT_APPLICABLE:
            raise QualificationError("material dimension cannot be NOT_APPLICABLE")
    if requested_level is QualificationLevel.G3 and any(
        a.state is not AssessmentState.PASS for a in ordered
    ):
        raise QualificationError("G3 requires all dimensions PASS")
    if (
        any(a.state is AssessmentState.RESTRICTED for a in ordered[:material_count])
        and not limitations
    ):
        raise QualificationError("restricted qualification requires limitations")
    if type(limitations) is not tuple or len(set(limitations)) != len(limitations):
        raise QualificationError("limitations must be a unique tuple")
    for value in limitations:
        _descriptive_text(value, "limitation")
    body = {
        "assessments": [_assessment_body(a) for a in ordered],
        "coverage": {
            "end": coverage.end.isoformat(),
            "start": coverage.start.isoformat(),
        },
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "level": requested_level.name,
        "limitations": list(limitations),
        "manifest_digest": manifest_digest,
        "policy": {"id": policy.policy_id, "version": policy.version},
        "predecessor": predecessor_id,
        "provenance_digest": provenance_digest,
    }
    qid = (
        "atis:research:dataset-qualification:v1:"
        + EvidenceContentDigest.from_bytes(
            b"ATIS-S4-02-QUALIFICATION-V1\0" + canonical_json(body)
        ).value
    )
    return DatasetQualification(
        qid,
        dataset_id,
        dataset_version,
        manifest_digest,
        provenance_digest,
        policy,
        requested_level,
        CEILINGS[requested_level],
        ordered,
        coverage,
        limitations,
        predecessor_id,
        QualificationLifecycle.CURRENT,
        (),
        None,
    )


def invalidate_qualification(
    value: DatasetQualification, reasons: tuple[str, ...]
) -> DatasetQualification:
    if (
        type(value) is not DatasetQualification
        or value.lifecycle is not QualificationLifecycle.CURRENT
    ):
        raise QualificationError("only a current qualification can be invalidated")
    if type(reasons) is not tuple or not reasons:
        raise QualificationError("invalidation reasons required")
    return DatasetQualification(
        value.qualification_id,
        value.dataset_id,
        value.dataset_version,
        value.manifest_digest,
        value.provenance_digest,
        value.policy,
        value.level,
        value.claim_ceiling,
        value.assessments,
        value.coverage,
        value.limitations,
        value.predecessor_qualification_id,
        QualificationLifecycle.INVALIDATED,
        reasons,
        None,
    )


def supersede_qualification(
    value: DatasetQualification, successor: DatasetQualification
) -> DatasetQualification:
    if (
        type(value) is not DatasetQualification
        or type(successor) is not DatasetQualification
    ):
        raise TypeError("qualifications required")
    if (
        value.lifecycle is not QualificationLifecycle.CURRENT
        or successor.predecessor_qualification_id != value.qualification_id
    ):
        raise QualificationError("invalid supersession")
    return DatasetQualification(
        value.qualification_id,
        value.dataset_id,
        value.dataset_version,
        value.manifest_digest,
        value.provenance_digest,
        value.policy,
        value.level,
        value.claim_ceiling,
        value.assessments,
        value.coverage,
        value.limitations,
        value.predecessor_qualification_id,
        QualificationLifecycle.SUPERSEDED,
        (),
        successor.qualification_id,
    )


def permits_claim(value: DatasetQualification, requested: ClaimCeiling) -> bool:
    if type(value) is not DatasetQualification or type(requested) is not ClaimCeiling:
        raise TypeError("qualification and claim required")
    if value.lifecycle is not QualificationLifecycle.CURRENT:
        return False
    order = (
        ClaimCeiling.CONTRACT_ONLY,
        ClaimCeiling.ENGINEERING_ONLY,
        ClaimCeiling.EXPLORATORY_RESEARCH,
        ClaimCeiling.ECONOMIC_VALIDATION,
    )
    return order.index(requested) <= order.index(value.claim_ceiling)


NO_ECONOMIC_CONCLUSION_AUTHORITY = (
    "DATA_QUALIFICATION_ONLY_NO_EDGE_TRADING_OR_CAPITAL_AUTHORITY"
)

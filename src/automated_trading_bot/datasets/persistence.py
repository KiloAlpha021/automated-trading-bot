"""C11-S1 storage-neutral persistence and publication semantics.

This module derives and verifies semantic identities only.  It performs no
physical I/O, selects no provider or storage implementation, and grants no
currentness, eligibility, promotion, trading, or financial authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
import unicodedata

from automated_trading_bot.datasets.materialization import (
    SHARED_CONTRACT_VERSION,
    DatasetVersionId,
    LogicalContentId,
)
from automated_trading_bot.datasets.manifest import DatasetManifest
from automated_trading_bot.datasets.provenance import (
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    ManifestId,
    ResourcePolicyError,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    EvidenceContentDigest,
    EvidenceRef,
    canonicalize_evidence_refs,
)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)
_STORED_VERSION_DOMAIN = "ATIS:C11:STORED_VERSION:1"
_PERSISTENCE_RECEIPT_DOMAIN = "ATIS:C11:PERSISTENCE_RECEIPT:1"
_PUBLICATION_RECEIPT_DOMAIN = "ATIS:C11:PUBLICATION_RECEIPT:1"
_PERSISTENCE_RECEIPT_CONTENT_DOMAIN = "ATIS:C11:PERSISTENCE_RECEIPT_CONTENT:1"
_PUBLICATION_RECEIPT_CONTENT_DOMAIN = "ATIS:C11:PUBLICATION_RECEIPT_CONTENT:1"


class DatasetPersistenceReason(StrEnum):
    IDENTITY_CONTENT_CONFLICT = "IDENTITY_CONTENT_CONFLICT"
    CONTENT_DIGEST_MISMATCH = "CONTENT_DIGEST_MISMATCH"
    EXACT_VERSION_NOT_ESTABLISHED = "EXACT_VERSION_NOT_ESTABLISHED"
    EXACT_VERSION_AMBIGUOUS = "EXACT_VERSION_AMBIGUOUS"
    VERIFICATION_EVIDENCE_NOT_ESTABLISHED = "VERIFICATION_EVIDENCE_NOT_ESTABLISHED"
    PARTIAL_PUBLICATION_PROHIBITED = "PARTIAL_PUBLICATION_PROHIBITED"
    PREDECESSOR_PUBLICATION_MISMATCH = "PREDECESSOR_PUBLICATION_MISMATCH"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    UNSUPPORTED_VISIBILITY_STATE = "UNSUPPORTED_VISIBILITY_STATE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"


class DatasetPersistenceError(ValueError):
    """A C11-owned semantic condition prevents a positive result."""

    reason: DatasetPersistenceReason

    def __init__(self, reason: DatasetPersistenceReason) -> None:
        if type(reason) is not DatasetPersistenceReason:
            raise TypeError("reason must be DatasetPersistenceReason")
        self.reason = reason
        super().__init__(reason.value)


class PersistenceOperation(StrEnum):
    PERSIST_EXACT_VERSION = "PERSIST_EXACT_VERSION"


class PublicationOperation(StrEnum):
    PUBLISH_EXACT_VERSION = "PUBLISH_EXACT_VERSION"


class ConsumerVisibilityState(StrEnum):
    COMPLETE = "COMPLETE"


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty and unpadded")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC-normalized")
    return value


def _identity(value: object, name: str) -> str:
    checked = _text(value, name)
    if _ID.fullmatch(checked) is None:
        raise ValueError(f"{name} must be a canonical attributable identity")
    return checked


@dataclass(frozen=True, slots=True)
class _OpaqueId:
    value: str

    def __post_init__(self) -> None:
        _identity(self.value, "identity")


@dataclass(frozen=True, slots=True)
class StoredVersionId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class PersistenceReceiptId(_OpaqueId):
    pass


@dataclass(frozen=True, slots=True)
class PublicationReceiptId(_OpaqueId):
    pass


def _evidence_ref_body(value: EvidenceRef) -> dict[str, str]:
    if type(value) is not EvidenceRef:
        raise TypeError("expected EvidenceRef")
    return {
        "source_id": value.source_id.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "content_digest": value.content_digest.value,
    }


def _canonical_verification_evidence(
    values: tuple[EvidenceRef, ...],
) -> tuple[EvidenceRef, ...]:
    if type(values) is not tuple:
        raise TypeError("verification_evidence_refs must be a tuple")
    if not values:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.VERIFICATION_EVIDENCE_NOT_ESTABLISHED
        )
    return canonicalize_evidence_refs(values)


def _timestamp_value(value: Timestamp) -> str:
    if type(value) is not Timestamp:
        raise TypeError("completed_at must be Timestamp")
    return value.value.isoformat()


@dataclass(frozen=True, slots=True)
class PersistenceReceipt:
    contract_version: str
    dataset_version_id: DatasetVersionId
    manifest_id: ManifestId
    logical_content_id: LogicalContentId
    stored_version_id: StoredVersionId
    persistence_operation: PersistenceOperation
    storage_adapter_contract_ref: EvidenceRef
    written_content_digest: EvidenceContentDigest
    verification_evidence_refs: tuple[EvidenceRef, ...]
    completed_at: Timestamp
    resource_policy_id: DatasetLifecycleResourcePolicyId
    content_digest: EvidenceContentDigest

    def content_projection(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "dataset_version_id": self.dataset_version_id.value,
            "manifest_id": self.manifest_id.value,
            "logical_content_id": self.logical_content_id.value,
            "stored_version_id": self.stored_version_id.value,
            "persistence_operation": self.persistence_operation.value,
            "storage_adapter_contract_ref": _evidence_ref_body(
                self.storage_adapter_contract_ref
            ),
            "written_content_digest": self.written_content_digest.value,
            "verification_evidence_refs": [
                _evidence_ref_body(item) for item in self.verification_evidence_refs
            ],
            "completed_at": _timestamp_value(self.completed_at),
            "resource_policy_id": self.resource_policy_id.value,
        }

    def receipt_id_body(self) -> dict[str, object]:
        return {**self.content_projection(), "content_digest": self.content_digest.value}

    def receipt_id(self) -> PersistenceReceiptId:
        return derive_persistence_receipt_id(self.receipt_id_body())

    def __post_init__(self) -> None:
        _validate_common_receipt_fields(
            self.contract_version,
            self.dataset_version_id,
            self.manifest_id,
            self.logical_content_id,
            self.stored_version_id,
            self.completed_at,
            self.content_digest,
        )
        if type(self.persistence_operation) is not PersistenceOperation:
            raise DatasetPersistenceError(DatasetPersistenceReason.UNSUPPORTED_OPERATION)
        if type(self.storage_adapter_contract_ref) is not EvidenceRef:
            raise TypeError("storage_adapter_contract_ref must be EvidenceRef")
        if type(self.written_content_digest) is not EvidenceContentDigest:
            raise TypeError("written_content_digest must be EvidenceContentDigest")
        if type(self.resource_policy_id) is not DatasetLifecycleResourcePolicyId:
            raise TypeError("resource_policy_id must be DatasetLifecycleResourcePolicyId")
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _canonical_verification_evidence(self.verification_evidence_refs),
        )
        expected = _digest(_PERSISTENCE_RECEIPT_CONTENT_DOMAIN, self.content_projection())
        if self.content_digest != expected:
            raise DatasetPersistenceError(DatasetPersistenceReason.CONTENT_DIGEST_MISMATCH)


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    contract_version: str
    dataset_version_id: DatasetVersionId
    manifest_id: ManifestId
    logical_content_id: LogicalContentId
    stored_version_id: StoredVersionId
    publication_operation: PublicationOperation
    predecessor_publication_ref: PublicationReceiptId | None
    verification_evidence_refs: tuple[EvidenceRef, ...]
    consumer_visibility_state: ConsumerVisibilityState
    completed_at: Timestamp
    content_digest: EvidenceContentDigest

    def content_projection(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "dataset_version_id": self.dataset_version_id.value,
            "manifest_id": self.manifest_id.value,
            "logical_content_id": self.logical_content_id.value,
            "stored_version_id": self.stored_version_id.value,
            "publication_operation": self.publication_operation.value,
            "predecessor_publication_ref": (
                None
                if self.predecessor_publication_ref is None
                else self.predecessor_publication_ref.value
            ),
            "verification_evidence_refs": [
                _evidence_ref_body(item) for item in self.verification_evidence_refs
            ],
            "consumer_visibility_state": self.consumer_visibility_state.value,
            "completed_at": _timestamp_value(self.completed_at),
        }

    def receipt_id_body(self) -> dict[str, object]:
        return {**self.content_projection(), "content_digest": self.content_digest.value}

    def receipt_id(self) -> PublicationReceiptId:
        return derive_publication_receipt_id(self.receipt_id_body())

    def __post_init__(self) -> None:
        _validate_common_receipt_fields(
            self.contract_version,
            self.dataset_version_id,
            self.manifest_id,
            self.logical_content_id,
            self.stored_version_id,
            self.completed_at,
            self.content_digest,
        )
        if type(self.publication_operation) is not PublicationOperation:
            raise DatasetPersistenceError(DatasetPersistenceReason.UNSUPPORTED_OPERATION)
        if (
            self.predecessor_publication_ref is not None
            and type(self.predecessor_publication_ref) is not PublicationReceiptId
        ):
            raise TypeError("predecessor_publication_ref must be PublicationReceiptId or None")
        if type(self.consumer_visibility_state) is not ConsumerVisibilityState:
            raise DatasetPersistenceError(
                DatasetPersistenceReason.UNSUPPORTED_VISIBILITY_STATE
            )
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _canonical_verification_evidence(self.verification_evidence_refs),
        )
        expected = _digest(_PUBLICATION_RECEIPT_CONTENT_DOMAIN, self.content_projection())
        if self.content_digest != expected:
            raise DatasetPersistenceError(DatasetPersistenceReason.CONTENT_DIGEST_MISMATCH)


def _validate_common_receipt_fields(
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    manifest_id: ManifestId,
    logical_content_id: LogicalContentId,
    stored_version_id: StoredVersionId,
    completed_at: Timestamp,
    content_digest: EvidenceContentDigest,
) -> None:
    if contract_version != SHARED_CONTRACT_VERSION:
        raise DatasetPersistenceError(DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    expected = (
        (dataset_version_id, DatasetVersionId, "dataset_version_id"),
        (manifest_id, ManifestId, "manifest_id"),
        (logical_content_id, LogicalContentId, "logical_content_id"),
        (stored_version_id, StoredVersionId, "stored_version_id"),
        (completed_at, Timestamp, "completed_at"),
        (content_digest, EvidenceContentDigest, "content_digest"),
    )
    for value, kind, name in expected:
        if type(value) is not kind:
            raise TypeError(f"{name} must be {kind.__name__}")


def _validate_manifest_binding(
    *,
    dataset_version_id: DatasetVersionId,
    manifest_id: ManifestId,
    logical_content_id: LogicalContentId,
    manifest: DatasetManifest,
) -> None:
    if type(manifest) is not DatasetManifest:
        raise TypeError("manifest must be DatasetManifest")
    if (
        manifest.manifest_id != manifest_id
        or manifest.dataset_version_id != dataset_version_id
        or manifest.logical_content_id != logical_content_id
    ):
        raise DatasetPersistenceError(DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)


def create_persistence_receipt(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    manifest_id: ManifestId,
    logical_content_id: LogicalContentId,
    stored_version_id: StoredVersionId,
    persistence_operation: PersistenceOperation,
    storage_adapter_contract_ref: EvidenceRef,
    written_content_digest: EvidenceContentDigest,
    verification_evidence_refs: tuple[EvidenceRef, ...],
    completed_at: Timestamp,
    resource_policy_id: DatasetLifecycleResourcePolicyId,
    manifest: DatasetManifest,
    resource_policy: DatasetLifecycleResourcePolicy,
) -> PersistenceReceipt:
    """Construct a typed semantic receipt without performing persistence."""
    _validate_manifest_binding(
        dataset_version_id=dataset_version_id,
        manifest_id=manifest_id,
        logical_content_id=logical_content_id,
        manifest=manifest,
    )
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise ResourcePolicyError("RESOURCE_POLICY_NOT_ESTABLISHED")
    if resource_policy.policy_id != resource_policy_id:
        raise DatasetPersistenceError(DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    expected_stored = derive_stored_version_id(
        contract_version=contract_version,
        dataset_version_id=dataset_version_id,
        manifest_id=manifest_id,
        logical_content_id=logical_content_id,
        written_content_digest=written_content_digest,
    )
    if stored_version_id != expected_stored:
        raise DatasetPersistenceError(DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    canonical_evidence = _canonical_verification_evidence(verification_evidence_refs)
    projection = {
        "contract_version": contract_version,
        "dataset_version_id": dataset_version_id.value,
        "manifest_id": manifest_id.value,
        "logical_content_id": logical_content_id.value,
        "stored_version_id": stored_version_id.value,
        "persistence_operation": persistence_operation.value,
        "storage_adapter_contract_ref": _evidence_ref_body(storage_adapter_contract_ref),
        "written_content_digest": written_content_digest.value,
        "verification_evidence_refs": [_evidence_ref_body(item) for item in canonical_evidence],
        "completed_at": _timestamp_value(completed_at),
        "resource_policy_id": resource_policy_id.value,
    }
    return PersistenceReceipt(
        contract_version,
        dataset_version_id,
        manifest_id,
        logical_content_id,
        stored_version_id,
        persistence_operation,
        storage_adapter_contract_ref,
        written_content_digest,
        canonical_evidence,
        completed_at,
        resource_policy_id,
        _digest(_PERSISTENCE_RECEIPT_CONTENT_DOMAIN, projection),
    )


def create_publication_receipt(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    manifest_id: ManifestId,
    logical_content_id: LogicalContentId,
    stored_version_id: StoredVersionId,
    expected_written_content_digest: EvidenceContentDigest,
    publication_operation: PublicationOperation,
    predecessor_publication_ref: PublicationReceiptId | None,
    expected_predecessor_publication_ref: PublicationReceiptId | None,
    verification_evidence_refs: tuple[EvidenceRef, ...],
    consumer_visibility_state: ConsumerVisibilityState,
    completed_at: Timestamp,
    manifest: DatasetManifest,
) -> PublicationReceipt:
    """Construct a typed semantic receipt without publishing or promoting."""
    _validate_manifest_binding(
        dataset_version_id=dataset_version_id,
        manifest_id=manifest_id,
        logical_content_id=logical_content_id,
        manifest=manifest,
    )
    expected_stored = derive_stored_version_id(
        contract_version=contract_version,
        dataset_version_id=dataset_version_id,
        manifest_id=manifest_id,
        logical_content_id=logical_content_id,
        written_content_digest=expected_written_content_digest,
    )
    if stored_version_id != expected_stored:
        raise DatasetPersistenceError(DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    validate_publication_semantics(
        operation=publication_operation,
        visibility_state=consumer_visibility_state,
        predecessor_publication_ref=predecessor_publication_ref,
        expected_predecessor_publication_ref=expected_predecessor_publication_ref,
    )
    canonical_evidence = _canonical_verification_evidence(verification_evidence_refs)
    projection = {
        "contract_version": contract_version,
        "dataset_version_id": dataset_version_id.value,
        "manifest_id": manifest_id.value,
        "logical_content_id": logical_content_id.value,
        "stored_version_id": stored_version_id.value,
        "publication_operation": publication_operation.value,
        "predecessor_publication_ref": (
            None if predecessor_publication_ref is None else predecessor_publication_ref.value
        ),
        "verification_evidence_refs": [_evidence_ref_body(item) for item in canonical_evidence],
        "consumer_visibility_state": consumer_visibility_state.value,
        "completed_at": _timestamp_value(completed_at),
    }
    return PublicationReceipt(
        contract_version,
        dataset_version_id,
        manifest_id,
        logical_content_id,
        stored_version_id,
        publication_operation,
        predecessor_publication_ref,
        canonical_evidence,
        consumer_visibility_state,
        completed_at,
        _digest(_PUBLICATION_RECEIPT_CONTENT_DOMAIN, projection),
    )


@dataclass(frozen=True, slots=True)
class ExactVersionRetrievalRequest:
    stored_version_id: StoredVersionId
    expected_written_content_digest: EvidenceContentDigest

    def __post_init__(self) -> None:
        if type(self.stored_version_id) is not StoredVersionId:
            raise TypeError("stored_version_id must be StoredVersionId")
        if type(self.expected_written_content_digest) is not EvidenceContentDigest:
            raise TypeError("expected_written_content_digest must be EvidenceContentDigest")


@dataclass(frozen=True, slots=True)
class ExactVersionRetrievalResult:
    stored_version_id: StoredVersionId
    written_content_digest: EvidenceContentDigest
    verification_evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.stored_version_id) is not StoredVersionId:
            raise TypeError("stored_version_id must be StoredVersionId")
        if type(self.written_content_digest) is not EvidenceContentDigest:
            raise TypeError("written_content_digest must be EvidenceContentDigest")
        if type(self.verification_evidence_refs) is not tuple:
            raise TypeError("verification_evidence_refs must be a tuple")
        if self.verification_evidence_refs:
            object.__setattr__(
                self,
                "verification_evidence_refs",
                canonicalize_evidence_refs(self.verification_evidence_refs),
            )


def _digest(domain: str, body: object) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(
        domain.encode("ascii") + b"\0" + canonical_json(body)
    )


def _derived(prefix: str, digest: EvidenceContentDigest) -> str:
    return f"{prefix}:{digest.value.removeprefix('sha256:')}"


def derive_stored_version_id(
    *,
    contract_version: str,
    dataset_version_id: DatasetVersionId,
    manifest_id: ManifestId,
    logical_content_id: LogicalContentId,
    written_content_digest: EvidenceContentDigest,
) -> StoredVersionId:
    """Derive the storage-neutral identity of one exact written version."""
    _text(contract_version, "contract_version")
    if type(dataset_version_id) is not DatasetVersionId:
        raise TypeError("dataset_version_id must be DatasetVersionId")
    if type(manifest_id) is not ManifestId:
        raise TypeError("manifest_id must be ManifestId")
    if type(logical_content_id) is not LogicalContentId:
        raise TypeError("logical_content_id must be LogicalContentId")
    if type(written_content_digest) is not EvidenceContentDigest:
        raise TypeError("written_content_digest must be EvidenceContentDigest")
    body = {
        "contract_version": contract_version,
        "dataset_version_id": dataset_version_id.value,
        "manifest_id": manifest_id.value,
        "logical_content_id": logical_content_id.value,
        "written_content_digest": written_content_digest.value,
    }
    return StoredVersionId(
        _derived("c11-stored-version", _digest(_STORED_VERSION_DOMAIN, body))
    )


def _complete_semantic_body(
    value: Mapping[str, object], *, claimed_identity_field: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("complete_semantic_body must be a mapping")
    body = dict(value)
    if not body:
        raise ValueError("complete_semantic_body must not be empty")
    if claimed_identity_field in body:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT
        )
    canonical_json(body)
    return body


def _derive_receipt_id(
    *, domain: str, prefix: str, complete_semantic_body: Mapping[str, object],
    claimed_identity_field: str,
) -> str:
    body = _complete_semantic_body(
        complete_semantic_body, claimed_identity_field=claimed_identity_field
    )
    return _derived(prefix, _digest(domain, body))


def derive_persistence_receipt_id(
    complete_semantic_body: Mapping[str, object],
) -> PersistenceReceiptId:
    """Derive an ID without asserting that physical persistence occurred."""
    return PersistenceReceiptId(
        _derive_receipt_id(
            domain=_PERSISTENCE_RECEIPT_DOMAIN,
            prefix="c11-persistence-receipt",
            complete_semantic_body=complete_semantic_body,
            claimed_identity_field="persistence_receipt_id",
        )
    )


def derive_publication_receipt_id(
    complete_semantic_body: Mapping[str, object],
) -> PublicationReceiptId:
    """Derive an ID without asserting that physical publication occurred."""
    return PublicationReceiptId(
        _derive_receipt_id(
            domain=_PUBLICATION_RECEIPT_DOMAIN,
            prefix="c11-publication-receipt",
            complete_semantic_body=complete_semantic_body,
            claimed_identity_field="publication_receipt_id",
        )
    )


def verify_persistence_receipt_id(
    claimed_id: PersistenceReceiptId,
    complete_semantic_body: Mapping[str, object],
) -> None:
    if type(claimed_id) is not PersistenceReceiptId:
        raise TypeError("claimed_id must be PersistenceReceiptId")
    if claimed_id != derive_persistence_receipt_id(complete_semantic_body):
        raise DatasetPersistenceError(
            DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT
        )


def verify_publication_receipt_id(
    claimed_id: PublicationReceiptId,
    complete_semantic_body: Mapping[str, object],
) -> None:
    if type(claimed_id) is not PublicationReceiptId:
        raise TypeError("claimed_id must be PublicationReceiptId")
    if claimed_id != derive_publication_receipt_id(complete_semantic_body):
        raise DatasetPersistenceError(
            DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT
        )


def verify_exact_version_retrieval(
    request: ExactVersionRetrievalRequest,
    candidates: tuple[ExactVersionRetrievalResult, ...],
) -> ExactVersionRetrievalResult:
    """Return one exact verified candidate without fallback or substitution."""
    if type(request) is not ExactVersionRetrievalRequest:
        raise TypeError("request must be ExactVersionRetrievalRequest")
    if type(candidates) is not tuple or any(
        type(candidate) is not ExactVersionRetrievalResult for candidate in candidates
    ):
        raise TypeError("candidates must contain ExactVersionRetrievalResult values")
    if not candidates:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.EXACT_VERSION_NOT_ESTABLISHED
        )
    if len(candidates) != 1:
        raise DatasetPersistenceError(DatasetPersistenceReason.EXACT_VERSION_AMBIGUOUS)
    result = candidates[0]
    if result.stored_version_id != request.stored_version_id:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.EXACT_VERSION_NOT_ESTABLISHED
        )
    if result.written_content_digest != request.expected_written_content_digest:
        raise DatasetPersistenceError(DatasetPersistenceReason.CONTENT_DIGEST_MISMATCH)
    if not result.verification_evidence_refs:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.VERIFICATION_EVIDENCE_NOT_ESTABLISHED
        )
    return result


def validate_publication_semantics(
    *,
    operation: object,
    visibility_state: object,
    predecessor_publication_ref: PublicationReceiptId | None,
    expected_predecessor_publication_ref: PublicationReceiptId | None,
) -> None:
    """Validate complete-only publication semantics without publishing."""
    if type(operation) is not PublicationOperation:
        raise DatasetPersistenceError(DatasetPersistenceReason.UNSUPPORTED_OPERATION)
    if visibility_state == "PARTIAL":
        raise DatasetPersistenceError(
            DatasetPersistenceReason.PARTIAL_PUBLICATION_PROHIBITED
        )
    if type(visibility_state) is not ConsumerVisibilityState:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.UNSUPPORTED_VISIBILITY_STATE
        )
    for value in (predecessor_publication_ref, expected_predecessor_publication_ref):
        if value is not None and type(value) is not PublicationReceiptId:
            raise TypeError("predecessor publication references must be PublicationReceiptId or None")
    if predecessor_publication_ref != expected_predecessor_publication_ref:
        raise DatasetPersistenceError(
            DatasetPersistenceReason.PREDECESSOR_PUBLICATION_MISMATCH
        )


def derive_publication_receipt_ids(
    complete_semantic_bodies: tuple[Mapping[str, object], ...],
    resource_policy: DatasetLifecycleResourcePolicy,
) -> tuple[PublicationReceiptId, ...]:
    """Derive one bounded batch atomically from externally selected limits."""
    if type(complete_semantic_bodies) is not tuple:
        raise TypeError("complete_semantic_bodies must be a tuple")
    if type(resource_policy) is not DatasetLifecycleResourcePolicy:
        raise ResourcePolicyError("RESOURCE_POLICY_NOT_ESTABLISHED")
    limit = resource_policy.value("MAX_PUBLICATION_RECORDS_PER_BATCH")
    if len(complete_semantic_bodies) > limit:
        raise ResourcePolicyError(DatasetPersistenceReason.RESOURCE_LIMIT_EXCEEDED.value)
    validated = tuple(
        _complete_semantic_body(
            body, claimed_identity_field="publication_receipt_id"
        )
        for body in complete_semantic_bodies
    )
    return tuple(derive_publication_receipt_id(body) for body in validated)


__all__ = (
    "SHARED_CONTRACT_VERSION",
    "ConsumerVisibilityState",
    "DatasetPersistenceError",
    "DatasetPersistenceReason",
    "ExactVersionRetrievalRequest",
    "ExactVersionRetrievalResult",
    "PersistenceOperation",
    "PersistenceReceipt",
    "PersistenceReceiptId",
    "PublicationOperation",
    "PublicationReceipt",
    "PublicationReceiptId",
    "StoredVersionId",
    "create_persistence_receipt",
    "create_publication_receipt",
    "derive_persistence_receipt_id",
    "derive_publication_receipt_id",
    "derive_publication_receipt_ids",
    "derive_stored_version_id",
    "validate_publication_semantics",
    "verify_exact_version_retrieval",
    "verify_persistence_receipt_id",
    "verify_publication_receipt_id",
)

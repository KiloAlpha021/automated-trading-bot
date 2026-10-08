"""Bounded provider-neutral physical persistence for Stage-3 analytical data.

The store is deliberately local and content addressed.  It does not select a
production provider, fetch external evidence, decide eligibility, release
quarantine, promote datasets, or grant any downstream authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Final
from uuid import uuid4

from automated_trading_bot.datasets.manifest import DatasetManifest
from automated_trading_bot.datasets.materialization import (
    SHARED_CONTRACT_VERSION,
    DatasetVersionId,
    LogicalContentId,
)
from automated_trading_bot.datasets.persistence import (
    ConsumerVisibilityState,
    ExactVersionRetrievalResult,
    PersistenceOperation,
    PersistenceReceipt,
    PublicationOperation,
    PublicationReceipt,
    PublicationReceiptId,
    StoredVersionId,
    create_persistence_receipt,
    create_publication_receipt,
    derive_stored_version_id,
)
from automated_trading_bot.datasets.provenance import (
    REQUIRED_PHYSICAL_RESOURCE_LIMITS,
    DatasetLifecycleResourcePolicy,
    ManifestId,
    ResourcePolicyError,
    canonical_json,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceRef,
    SourceId,
)


_HEX: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", re.ASCII)
_MUTABLE_REFERENCE_TOKENS: Final = ("latest", "current", "?", "#")
_REPARSE_POINT: Final = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PhysicalPersistenceReason(StrEnum):
    STORE_ROOT_UNSAFE = "STORE_ROOT_UNSAFE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    OBJECT_MISSING = "OBJECT_MISSING"
    OBJECT_CORRUPT = "OBJECT_CORRUPT"
    OBJECT_COLLISION = "OBJECT_COLLISION"
    VERSION_NOT_ESTABLISHED = "VERSION_NOT_ESTABLISHED"
    VERSION_IDENTITY_MISMATCH = "VERSION_IDENTITY_MISMATCH"
    VERSION_BUNDLE_INVALID = "VERSION_BUNDLE_INVALID"
    PUBLICATION_PREDECESSOR_MISMATCH = "PUBLICATION_PREDECESSOR_MISMATCH"
    PUBLICATION_TARGET_INVALID = "PUBLICATION_TARGET_INVALID"
    PUBLICATION_MARKER_INVALID = "PUBLICATION_MARKER_INVALID"
    EXTERNAL_REFERENCE_INSUFFICIENT = "EXTERNAL_REFERENCE_INSUFFICIENT"
    ENTITLEMENT_NOT_ESTABLISHED = "ENTITLEMENT_NOT_ESTABLISHED"
    RETENTION_EVIDENCE_NOT_ESTABLISHED = "RETENTION_EVIDENCE_NOT_ESTABLISHED"
    WRITE_FAILED = "WRITE_FAILED"
    INJECTED_INTERRUPTION = "INJECTED_INTERRUPTION"


class PhysicalPersistenceError(RuntimeError):
    reason: PhysicalPersistenceReason

    def __init__(self, reason: PhysicalPersistenceReason) -> None:
        if type(reason) is not PhysicalPersistenceReason:
            raise TypeError("reason must be PhysicalPersistenceReason")
        self.reason = reason
        super().__init__(reason.value)


class RetainedArtifactKind(StrEnum):
    CANONICAL_ANALYTICAL_BYTES = "CANONICAL_ANALYTICAL_BYTES"
    DATASET_MANIFEST = "DATASET_MANIFEST"
    PROVENANCE_GRAPH = "PROVENANCE_GRAPH"
    TRANSFORMATION_LINEAGE = "TRANSFORMATION_LINEAGE"
    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"
    CURRENTNESS_EVIDENCE = "CURRENTNESS_EVIDENCE"
    QUARANTINE_ELIGIBILITY_EVIDENCE = "QUARANTINE_ELIGIBILITY_EVIDENCE"
    CORRECTION_CANCELLATION_EVIDENCE = "CORRECTION_CANCELLATION_EVIDENCE"
    INVALIDATION_AFFECTED_SET_EVIDENCE = "INVALIDATION_AFFECTED_SET_EVIDENCE"
    RESOURCE_POLICY = "RESOURCE_POLICY"
    RECOVERY_METADATA = "RECOVERY_METADATA"
    SUPERSESSION_LINEAGE = "SUPERSESSION_LINEAGE"


class FailurePoint(StrEnum):
    AFTER_STAGING = "AFTER_STAGING"
    AFTER_OBJECT_COMMIT = "AFTER_OBJECT_COMMIT"
    BEFORE_PUBLICATION_COMMIT = "BEFORE_PUBLICATION_COMMIT"
    AFTER_PUBLICATION_COMMIT = "AFTER_PUBLICATION_COMMIT"


class RecoveryState(StrEnum):
    COMMITTED_VALID = "COMMITTED_VALID"
    STAGED_ONLY = "STAGED_ONLY"
    COMPLETED_BUT_UNCOMMITTED = "COMPLETED_BUT_UNCOMMITTED"
    CORRUPT = "CORRUPT"
    MISSING = "MISSING"
    MARKER_TO_INVALID_VERSION = "MARKER_TO_INVALID_VERSION"


@dataclass(frozen=True, slots=True)
class ImmutableExternalReference:
    reference_id: str
    source_identity: str
    version_identity: str
    content_digest: EvidenceContentDigest
    acquired_at: Timestamp
    knowledge_at: Timestamp
    entitlement_ref: EvidenceRef
    exactly_retrievable: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.reference_id, "reference_id"),
            (self.source_identity, "source_identity"),
            (self.version_identity, "version_identity"),
        ):
            if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
                )
            if any(token in value.casefold() for token in _MUTABLE_REFERENCE_TOKENS):
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
                )
        if type(self.content_digest) is not EvidenceContentDigest:
            raise TypeError("content_digest must be EvidenceContentDigest")
        if type(self.acquired_at) is not Timestamp or type(self.knowledge_at) is not Timestamp:
            raise TypeError("acquired_at and knowledge_at must be Timestamp values")
        if type(self.entitlement_ref) is not EvidenceRef:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.ENTITLEMENT_NOT_ESTABLISHED
            )
        if type(self.exactly_retrievable) is not bool or not self.exactly_retrievable:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
            )

    def body(self) -> dict[str, object]:
        return {
            "acquired_at": self.acquired_at.value.isoformat(),
            "content_digest": self.content_digest.value,
            "entitlement_ref": _evidence_ref_body(self.entitlement_ref),
            "exactly_retrievable": self.exactly_retrievable,
            "knowledge_at": self.knowledge_at.value.isoformat(),
            "reference_id": self.reference_id,
            "source_identity": self.source_identity,
            "version_identity": self.version_identity,
        }


@dataclass(frozen=True, slots=True)
class RetainedArtifact:
    kind: RetainedArtifactKind
    identity: str
    content: bytes | None = None
    external_reference: ImmutableExternalReference | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not RetainedArtifactKind:
            raise TypeError("kind must be RetainedArtifactKind")
        if type(self.identity) is not str or _SAFE_ID.fullmatch(self.identity) is None:
            raise ValueError("identity must be canonical")
        if (self.content is None) == (self.external_reference is None):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RETENTION_EVIDENCE_NOT_ESTABLISHED
            )
        if self.content is not None and type(self.content) is not bytes:
            raise TypeError("content must be bytes")
        if self.external_reference is not None and self.kind is not RetainedArtifactKind.SOURCE_EVIDENCE:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
            )


@dataclass(frozen=True, slots=True)
class PhysicalPersistenceResult:
    receipt: PersistenceReceipt
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    identity: str
    state: RecoveryState


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    publication_state: RecoveryState
    published_stored_version_id: StoredVersionId | None
    staging_entries: tuple[RecoveryEntry, ...]


def _evidence_ref_body(value: EvidenceRef) -> dict[str, str]:
    return {
        "content_digest": value.content_digest.value,
        "dataset_id": value.dataset_id.value,
        "evidence_id": value.evidence_id.value,
        "source_id": value.source_id.value,
    }


def _evidence_ref_from_body(value: object) -> EvidenceRef:
    if type(value) is not dict:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
    try:
        return EvidenceRef(
            SourceId(value["source_id"]),
            DatasetId(value["dataset_id"]),
            EvidenceId(value["evidence_id"]),
            EvidenceContentDigest(value["content_digest"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PhysicalPersistenceError(
            PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
        ) from error


def _external_reference_from_body(value: object) -> ImmutableExternalReference:
    if type(value) is not dict or set(value) != {
        "acquired_at",
        "content_digest",
        "entitlement_ref",
        "exactly_retrievable",
        "knowledge_at",
        "reference_id",
        "source_identity",
        "version_identity",
    }:
        raise PhysicalPersistenceError(
            PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
        )
    try:
        return ImmutableExternalReference(
            _required_str(value["reference_id"]),
            _required_str(value["source_identity"]),
            _required_str(value["version_identity"]),
            EvidenceContentDigest(_required_str(value["content_digest"])),
            Timestamp(datetime.fromisoformat(_required_str(value["acquired_at"]))),
            Timestamp(datetime.fromisoformat(_required_str(value["knowledge_at"]))),
            _evidence_ref_from_body(value["entitlement_ref"]),
            value["exactly_retrievable"],
        )
    except (TypeError, ValueError) as error:
        raise PhysicalPersistenceError(
            PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
        ) from error


def _digest_hex(value: EvidenceContentDigest) -> str:
    prefix = "sha256:"
    if not value.value.startswith(prefix):
        raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
    result = value.value.removeprefix(prefix)
    if _HEX.fullmatch(result) is None:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
    return result


def _identity_hex(value: str) -> str:
    return _digest_hex(EvidenceContentDigest.from_bytes(value.encode("utf-8")))


def _required_str(value: object) -> str:
    if type(value) is not str:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
    return value


def _required_list(value: object) -> list[object]:
    if type(value) is not list:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
    return value


def _is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(value, "st_file_attributes", 0) & _REPARSE_POINT)


def _assert_safe_existing_chain(path: Path) -> None:
    current = path
    existing: list[Path] = []
    while True:
        if current.exists() or current.is_symlink():
            existing.append(current)
        if current.parent == current:
            break
        current = current.parent
    if any(_is_reparse(item) for item in existing):
        raise PhysicalPersistenceError(PhysicalPersistenceReason.STORE_ROOT_UNSAFE)


def _assert_trusted_store_path(root: Path, path: Path) -> None:
    """Reject a trusted path whose existing chain was redirected after setup."""
    try:
        if _is_reparse(root) or root.resolve(strict=True) != root:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.STORE_ROOT_UNSAFE)
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if not current.exists() and not current.is_symlink():
                continue
            if _is_reparse(current):
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.STORE_ROOT_UNSAFE
                )
            current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise PhysicalPersistenceError(
            PhysicalPersistenceReason.STORE_ROOT_UNSAFE
        ) from error


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file_durable(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except OSError as error:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error


def _commit_write_once(
    temporary: Path, target: Path, collision_reason: PhysicalPersistenceReason
) -> bool:
    """Atomically link a fully flushed file without replacing an existing name."""
    try:
        os.link(temporary, target)
    except FileExistsError:
        return False
    except OSError as error:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error
    temporary.unlink()
    _flush_directory(target.parent)
    if not target.exists():
        raise PhysicalPersistenceError(collision_reason)
    return True


def _read_bytes(path: Path, expected: EvidenceContentDigest) -> bytes:
    try:
        content = path.read_bytes()
    except FileNotFoundError as error:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_MISSING) from error
    except OSError as error:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT) from error
    if EvidenceContentDigest.from_bytes(content) != expected:
        raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
    return content


class LocalPhysicalDatasetStore:
    """A bounded local store whose paths are derived only from protected identities."""

    def __init__(self, root: Path, resource_policy: DatasetLifecycleResourcePolicy) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be pathlib.Path")
        if type(resource_policy) is not DatasetLifecycleResourcePolicy:
            raise ResourcePolicyError("RESOURCE_POLICY_NOT_ESTABLISHED")
        _assert_safe_existing_chain(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.STORE_ROOT_UNSAFE) from error
        _assert_safe_existing_chain(root)
        self._root = root.resolve(strict=True)
        self._policy = resource_policy
        if not REQUIRED_PHYSICAL_RESOURCE_LIMITS.issubset(dict(resource_policy.limits)):
            raise ResourcePolicyError("PHYSICAL_RESOURCE_POLICY_NOT_ESTABLISHED")
        self._objects = self._root / "objects"
        self._versions = self._root / "versions"
        self._staging = self._root / "staging"
        self._publication = self._root / "publication"
        for path in (self._objects, self._versions, self._staging, self._publication):
            path.mkdir(exist_ok=True)
            if _is_reparse(path) or path.resolve(strict=True).parent != self._root:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.STORE_ROOT_UNSAFE
                )

    @property
    def root(self) -> Path:
        return self._root

    def _assert_trusted(self, path: Path) -> None:
        _assert_trusted_store_path(self._root, path)

    def _object_path(self, digest: EvidenceContentDigest) -> Path:
        value = _digest_hex(digest)
        parent = self._objects / value[:2]
        self._assert_trusted(parent)
        parent.mkdir(exist_ok=True)
        target = parent / f"{value[2:]}.bin"
        self._assert_trusted(target)
        return target

    def _version_path(self, stored_version_id: StoredVersionId) -> Path:
        if type(stored_version_id) is not StoredVersionId:
            raise TypeError("stored_version_id must be StoredVersionId")
        target = self._versions / f"{_identity_hex(stored_version_id.value)}.json"
        self._assert_trusted(target)
        return target

    def _check_size(self, content: bytes, name: str) -> None:
        limit_name = (
            "MAX_METADATA_BYTES" if name == "metadata" else "MAX_PERSISTED_OBJECT_BYTES"
        )
        if len(content) > self._policy.value(limit_name):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED
            )

    def _write_object(self, digest: EvidenceContentDigest, content: bytes) -> None:
        self._check_size(content, "object")
        if EvidenceContentDigest.from_bytes(content) != digest:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
        target = self._object_path(digest)
        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as error:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.OBJECT_CORRUPT
                ) from error
            if existing != content:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.OBJECT_COLLISION
                )
            return
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        _write_file_durable(temporary, content)
        try:
            if not _commit_write_once(
                temporary, target, PhysicalPersistenceReason.OBJECT_COLLISION
            ):
                existing = target.read_bytes()
                if existing != content:
                    raise PhysicalPersistenceError(
                        PhysicalPersistenceReason.OBJECT_COLLISION
                    )
                temporary.unlink(missing_ok=True)
                return
        except PhysicalPersistenceError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error
        _read_bytes(target, digest)

    def _write_once_metadata(self, target: Path, content: bytes) -> None:
        self._check_size(content, "metadata")
        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as error:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
                ) from error
            if existing != content:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.OBJECT_COLLISION
                )
            return
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        _write_file_durable(temporary, content)
        try:
            if not _commit_write_once(
                temporary, target, PhysicalPersistenceReason.OBJECT_COLLISION
            ):
                if target.read_bytes() != content:
                    raise PhysicalPersistenceError(
                        PhysicalPersistenceReason.OBJECT_COLLISION
                    )
                temporary.unlink(missing_ok=True)
                return
        except PhysicalPersistenceError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error

    def persist_exact_version(
        self,
        *,
        canonical_bytes: bytes,
        manifest: DatasetManifest,
        manifest_bytes: bytes,
        provenance_bytes: bytes,
        lineage_bytes: bytes,
        retained_artifacts: tuple[RetainedArtifact, ...],
        storage_adapter_contract_ref: EvidenceRef,
        verification_evidence_refs: tuple[EvidenceRef, ...],
        completed_at: Timestamp,
        failure_point: FailurePoint | None = None,
    ) -> PhysicalPersistenceResult:
        self._assert_trusted(self._staging)
        self._assert_trusted(self._objects)
        self._assert_trusted(self._versions)
        if type(canonical_bytes) is not bytes:
            raise TypeError("canonical_bytes must be bytes")
        if type(manifest) is not DatasetManifest:
            raise TypeError("manifest must be DatasetManifest")
        if any(type(value) is not bytes for value in (manifest_bytes, provenance_bytes, lineage_bytes)):
            raise TypeError("structured records must be bytes")
        if type(retained_artifacts) is not tuple or any(
            type(item) is not RetainedArtifact for item in retained_artifacts
        ):
            raise TypeError("retained_artifacts must be RetainedArtifact values")
        if type(failure_point) not in (FailurePoint, type(None)):
            raise TypeError("failure_point must be FailurePoint or None")

        required = {
            RetainedArtifactKind.SOURCE_EVIDENCE,
            RetainedArtifactKind.CURRENTNESS_EVIDENCE,
            RetainedArtifactKind.QUARANTINE_ELIGIBILITY_EVIDENCE,
            RetainedArtifactKind.CORRECTION_CANCELLATION_EVIDENCE,
            RetainedArtifactKind.INVALIDATION_AFFECTED_SET_EVIDENCE,
            RetainedArtifactKind.RESOURCE_POLICY,
            RetainedArtifactKind.RECOVERY_METADATA,
            RetainedArtifactKind.SUPERSESSION_LINEAGE,
        }
        kinds = {item.kind for item in retained_artifacts}
        if not required.issubset(kinds):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RETENTION_EVIDENCE_NOT_ESTABLISHED
            )
        if len({item.identity for item in retained_artifacts}) != len(retained_artifacts):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RETENTION_EVIDENCE_NOT_ESTABLISHED
            )

        base_objects = (
            (RetainedArtifactKind.CANONICAL_ANALYTICAL_BYTES, "canonical", canonical_bytes),
            (RetainedArtifactKind.DATASET_MANIFEST, "manifest", manifest_bytes),
            (RetainedArtifactKind.PROVENANCE_GRAPH, "provenance", provenance_bytes),
            (RetainedArtifactKind.TRANSFORMATION_LINEAGE, "lineage", lineage_bytes),
        )
        local_objects = list(base_objects)
        external: list[dict[str, object]] = []
        for item in retained_artifacts:
            if item.content is not None:
                local_objects.append((item.kind, item.identity, item.content))
            else:
                assert item.external_reference is not None
                external.append(
                    {
                        "identity": item.identity,
                        "kind": item.kind.value,
                        "reference": item.external_reference.body(),
                    }
                )
        if len(local_objects) + len(external) > self._policy.value("MAX_OBJECTS_PER_VERSION"):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED
            )
        if any(len(content) > self._policy.value("MAX_PERSISTED_OBJECT_BYTES") for _, _, content in local_objects):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED
            )

        content_digest = EvidenceContentDigest.from_bytes(canonical_bytes)
        stored_version_id = derive_stored_version_id(
            contract_version=SHARED_CONTRACT_VERSION,
            dataset_version_id=manifest.dataset_version_id,
            manifest_id=manifest.manifest_id,
            logical_content_id=manifest.logical_content_id,
            written_content_digest=content_digest,
        )
        object_records = [
            {
                "content_digest": EvidenceContentDigest.from_bytes(content).value,
                "identity": identity,
                "kind": kind.value,
                "size": len(content),
            }
            for kind, identity, content in local_objects
        ]
        metadata = {
            "contract_version": SHARED_CONTRACT_VERSION,
            "dataset_version_id": manifest.dataset_version_id.value,
            "external_references": sorted(external, key=lambda value: str(value["identity"])),
            "logical_content_id": manifest.logical_content_id.value,
            "manifest_id": manifest.manifest_id.value,
            "objects": sorted(object_records, key=lambda value: (str(value["kind"]), str(value["identity"]))),
            "resource_policy_id": self._policy.policy_id.value,
            "stored_version_id": stored_version_id.value,
            "verification_evidence_refs": [
                _evidence_ref_body(item) for item in verification_evidence_refs
            ],
            "written_content_digest": content_digest.value,
        }
        metadata_bytes = canonical_json(metadata)
        self._check_size(metadata_bytes, "metadata")
        if sum(len(content) for _, _, content in local_objects) + len(metadata_bytes) > self._policy.value("MAX_BUNDLE_BYTES"):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED
            )

        staging_entries = tuple(
            item for item in self._staging.iterdir() if item.name != "quarantine"
        )
        if len(staging_entries) >= self._policy.value("MAX_RECOVERY_STAGING_ENTRIES"):
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED
            )
        stage = self._staging / uuid4().hex
        stage.mkdir()
        try:
            for index, (_, _, content) in enumerate(local_objects):
                _write_file_durable(stage / f"object-{index}.bin", content)
            if failure_point is FailurePoint.AFTER_STAGING:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.INJECTED_INTERRUPTION
                )
            _write_file_durable(stage / "version.json", metadata_bytes)
            for record, (_, _, content) in zip(object_records, local_objects, strict=True):
                self._write_object(
                    EvidenceContentDigest(_required_str(record["content_digest"])), content
                )
            if failure_point is FailurePoint.AFTER_OBJECT_COMMIT:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.INJECTED_INTERRUPTION
                )
            self._write_once_metadata(self._version_path(stored_version_id), metadata_bytes)
            _, verified_bytes = self.read_exact_version(stored_version_id)
            if verified_bytes != canonical_bytes:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
                )
            receipt = create_persistence_receipt(
                contract_version=SHARED_CONTRACT_VERSION,
                dataset_version_id=manifest.dataset_version_id,
                manifest_id=manifest.manifest_id,
                logical_content_id=manifest.logical_content_id,
                stored_version_id=stored_version_id,
                persistence_operation=PersistenceOperation.PERSIST_EXACT_VERSION,
                storage_adapter_contract_ref=storage_adapter_contract_ref,
                written_content_digest=content_digest,
                verification_evidence_refs=verification_evidence_refs,
                completed_at=completed_at,
                resource_policy_id=self._policy.policy_id,
                manifest=manifest,
                resource_policy=self._policy,
            )
            shutil.rmtree(stage)
            return PhysicalPersistenceResult(receipt, canonical_bytes)
        except PhysicalPersistenceError:
            raise
        except OSError as error:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error

    def _load_version(self, stored_version_id: StoredVersionId) -> dict[str, object]:
        path = self._version_path(stored_version_id)
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_NOT_ESTABLISHED
            ) from error
        self._check_size(content, "metadata")
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
            ) from error
        if type(value) is not dict or canonical_json(value) != content:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
            )
        if value.get("stored_version_id") != stored_version_id.value:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_IDENTITY_MISMATCH
            )
        try:
            derived = derive_stored_version_id(
                contract_version=value["contract_version"],
                dataset_version_id=DatasetVersionId(value["dataset_version_id"]),
                manifest_id=ManifestId(value["manifest_id"]),
                logical_content_id=LogicalContentId(value["logical_content_id"]),
                written_content_digest=EvidenceContentDigest(
                    value["written_content_digest"]
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_BUNDLE_INVALID
            ) from error
        if derived != stored_version_id:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.VERSION_IDENTITY_MISMATCH
            )
        return value

    def retrieve_exact_version(
        self, stored_version_id: StoredVersionId
    ) -> tuple[ExactVersionRetrievalResult, bytes]:
        """Read and reverify one exact committed version without fallback."""
        return self._retrieval(stored_version_id)

    def _retrieval(self, stored_version_id: StoredVersionId) -> tuple[ExactVersionRetrievalResult, bytes]:
        metadata = self._load_version(stored_version_id)
        records = metadata.get("objects")
        if type(records) is not list:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
        canonical: bytes | None = None
        for record in records:
            if type(record) is not dict:
                raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
            digest = EvidenceContentDigest(_required_str(record["content_digest"]))
            content = _read_bytes(self._object_path(digest), digest)
            if len(content) != record.get("size"):
                raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
            if record.get("kind") == RetainedArtifactKind.CANONICAL_ANALYTICAL_BYTES.value:
                canonical = content
        if canonical is None:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.VERSION_BUNDLE_INVALID)
        expected = EvidenceContentDigest(
            _required_str(metadata["written_content_digest"])
        )
        if EvidenceContentDigest.from_bytes(canonical) != expected:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.OBJECT_CORRUPT)
        refs = tuple(
            _evidence_ref_from_body(item)
            for item in _required_list(metadata.get("verification_evidence_refs"))
        )
        for item in _required_list(metadata.get("external_references")):
            if type(item) is not dict or set(item) != {"identity", "kind", "reference"}:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
                )
            if item["kind"] != RetainedArtifactKind.SOURCE_EVIDENCE.value:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT
                )
            _required_str(item["identity"])
            _external_reference_from_body(item["reference"])
        return ExactVersionRetrievalResult(stored_version_id, expected, refs), canonical

    def read_exact_version(
        self, stored_version_id: StoredVersionId
    ) -> tuple[ExactVersionRetrievalResult, bytes]:
        """Read and reverify one exact committed version without fallback."""
        return self._retrieval(stored_version_id)

    def _read_publication_marker(self) -> dict[str, object] | None:
        path = self._publication / "current.json"
        self._assert_trusted(path)
        if not path.exists():
            return None
        try:
            content = path.read_bytes()
            value = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID
            ) from error
        if type(value) is not dict or canonical_json(value) != content:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID
            )
        return value

    def published_version(self) -> StoredVersionId | None:
        marker = self._read_publication_marker()
        if marker is None:
            return None
        try:
            identity = StoredVersionId(_required_str(marker["stored_version_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID
            ) from error
        self._retrieval(identity)
        return identity

    def publish_exact_version(
        self,
        *,
        stored_version_id: StoredVersionId,
        manifest: DatasetManifest,
        expected_predecessor_publication_ref: PublicationReceiptId | None,
        verification_evidence_refs: tuple[EvidenceRef, ...],
        completed_at: Timestamp,
        failure_point: FailurePoint | None = None,
    ) -> PublicationReceipt:
        self._assert_trusted(self._staging)
        self._assert_trusted(self._publication)
        retrieval, _ = self._retrieval(stored_version_id)
        marker = self._read_publication_marker()
        predecessor: PublicationReceiptId | None = None
        if marker is not None:
            try:
                predecessor = PublicationReceiptId(
                    _required_str(marker["publication_receipt_id"])
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PhysicalPersistenceError(
                    PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID
                ) from error
            if marker.get("stored_version_id") == stored_version_id.value:
                predecessor = expected_predecessor_publication_ref
        if predecessor != expected_predecessor_publication_ref:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.PUBLICATION_PREDECESSOR_MISMATCH
            )
        receipt = create_publication_receipt(
            contract_version=SHARED_CONTRACT_VERSION,
            dataset_version_id=manifest.dataset_version_id,
            manifest_id=manifest.manifest_id,
            logical_content_id=manifest.logical_content_id,
            stored_version_id=stored_version_id,
            expected_written_content_digest=retrieval.written_content_digest,
            publication_operation=PublicationOperation.PUBLISH_EXACT_VERSION,
            predecessor_publication_ref=expected_predecessor_publication_ref,
            expected_predecessor_publication_ref=expected_predecessor_publication_ref,
            verification_evidence_refs=verification_evidence_refs,
            consumer_visibility_state=ConsumerVisibilityState.COMPLETE,
            completed_at=completed_at,
            manifest=manifest,
        )
        marker_body = {
            "consumer_visibility_state": ConsumerVisibilityState.COMPLETE.value,
            "publication_receipt_id": receipt.receipt_id().value,
            "stored_version_id": stored_version_id.value,
            "written_content_digest": retrieval.written_content_digest.value,
        }
        content = canonical_json(marker_body)
        self._check_size(content, "metadata")
        stage = self._staging / f"publication-{uuid4().hex}.json"
        _write_file_durable(stage, content)
        if failure_point is FailurePoint.BEFORE_PUBLICATION_COMMIT:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.INJECTED_INTERRUPTION
            )
        try:
            os.replace(stage, self._publication / "current.json")
            _flush_directory(self._publication)
        except OSError as error:
            raise PhysicalPersistenceError(PhysicalPersistenceReason.WRITE_FAILED) from error
        if failure_point is FailurePoint.AFTER_PUBLICATION_COMMIT:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.INJECTED_INTERRUPTION
            )
        committed = self._read_publication_marker()
        if committed != marker_body or self.published_version() != stored_version_id:
            raise PhysicalPersistenceError(
                PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID
            )
        return receipt

    def recover(self) -> RecoveryReport:
        self._assert_trusted(self._staging)
        self._assert_trusted(self._publication)
        self._assert_trusted(self._versions)
        self._assert_trusted(self._objects)
        entries: list[RecoveryEntry] = []
        for path in sorted(self._staging.iterdir(), key=lambda value: value.name):
            if path.name == "quarantine":
                continue
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    state = (
                        RecoveryState.COMPLETED_BUT_UNCOMMITTED
                        if type(value) is dict and "stored_version_id" in value
                        else RecoveryState.STAGED_ONLY
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    state = RecoveryState.CORRUPT
                entries.append(RecoveryEntry(path.name, state))
                continue
            if _is_reparse(path):
                entries.append(RecoveryEntry(path.name, RecoveryState.CORRUPT))
                continue
            metadata = path / "version.json"
            if not metadata.exists():
                entries.append(RecoveryEntry(path.name, RecoveryState.STAGED_ONLY))
                continue
            try:
                value = json.loads(metadata.read_text(encoding="utf-8"))
                if type(value) is not dict or canonical_json(value) != metadata.read_bytes():
                    raise ValueError
                identity = StoredVersionId(value["stored_version_id"])
                state = (
                    RecoveryState.COMMITTED_VALID
                    if self._version_path(identity).exists()
                    else RecoveryState.COMPLETED_BUT_UNCOMMITTED
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                state = RecoveryState.CORRUPT
            entries.append(RecoveryEntry(path.name, state))

        marker = self._read_publication_marker()
        if marker is None:
            publication_state = RecoveryState.MISSING
            published = None
        else:
            try:
                published = StoredVersionId(
                    _required_str(marker["stored_version_id"])
                )
                self._retrieval(published)
                publication_state = RecoveryState.COMMITTED_VALID
            except PhysicalPersistenceError:
                published = None
                publication_state = RecoveryState.MARKER_TO_INVALID_VERSION
            except (KeyError, TypeError, ValueError):
                published = None
                publication_state = RecoveryState.CORRUPT
        return RecoveryReport(publication_state, published, tuple(entries))


__all__ = (
    "FailurePoint",
    "ImmutableExternalReference",
    "LocalPhysicalDatasetStore",
    "PhysicalPersistenceError",
    "PhysicalPersistenceReason",
    "PhysicalPersistenceResult",
    "RecoveryEntry",
    "RecoveryReport",
    "RecoveryState",
    "RetainedArtifact",
    "RetainedArtifactKind",
)

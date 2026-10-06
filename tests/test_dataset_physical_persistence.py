from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from automated_trading_bot.datasets.manifest import DatasetManifest
from automated_trading_bot.datasets.materialization import DatasetVersionId, LogicalContentId
from automated_trading_bot.datasets.persistence import PublicationReceiptId
from automated_trading_bot.datasets.physical_persistence import (
    FailurePoint,
    ImmutableExternalReference,
    LocalPhysicalDatasetStore,
    PhysicalPersistenceError,
    PhysicalPersistenceReason,
    RecoveryState,
    RetainedArtifact,
    RetainedArtifactKind,
)
from automated_trading_bot.datasets.provenance import (
    REQUIRED_RESOURCE_LIMITS,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    ManifestId,
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


def digest(value: str) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(value.encode())


def ref(name: str) -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:test/f1"),
        DatasetId("dataset:test/f1"),
        EvidenceId(f"evidence:test/{name}"),
        digest(name),
    )


def policy(**overrides: int) -> DatasetLifecycleResourcePolicy:
    limits = {name: 1024 for name in REQUIRED_RESOURCE_LIMITS}
    limits.update(
        MAX_PERSISTED_OBJECT_BYTES=4096,
        MAX_BUNDLE_BYTES=65536,
        MAX_OBJECTS_PER_VERSION=32,
        MAX_METADATA_BYTES=32768,
        MAX_RECOVERY_STAGING_ENTRIES=16,
    )
    limits.update(overrides)
    return DatasetLifecycleResourcePolicy(
        DatasetLifecycleResourcePolicyId("resource-policy:test/f1"),
        ref("resource-policy"),
        tuple(limits.items()),
    )


def manifest_stub() -> DatasetManifest:
    value = object.__new__(DatasetManifest)
    object.__setattr__(value, "dataset_version_id", DatasetVersionId("c08-dataset-version:f1"))
    object.__setattr__(value, "manifest_id", ManifestId("c09-dataset-manifest:f1"))
    object.__setattr__(value, "logical_content_id", LogicalContentId("c08-logical-content:f1"))
    return value


def retained() -> tuple[RetainedArtifact, ...]:
    required = (
        RetainedArtifactKind.SOURCE_EVIDENCE,
        RetainedArtifactKind.CURRENTNESS_EVIDENCE,
        RetainedArtifactKind.QUARANTINE_ELIGIBILITY_EVIDENCE,
        RetainedArtifactKind.CORRECTION_CANCELLATION_EVIDENCE,
        RetainedArtifactKind.INVALIDATION_AFFECTED_SET_EVIDENCE,
        RetainedArtifactKind.RESOURCE_POLICY,
        RetainedArtifactKind.RECOVERY_METADATA,
        RetainedArtifactKind.SUPERSESSION_LINEAGE,
    )
    return tuple(RetainedArtifact(kind, kind.value.lower(), kind.value.encode()) for kind in required)


def persist(store: LocalPhysicalDatasetStore, content: bytes = b"canonical", **kwargs: object):
    artifacts = kwargs.pop("retained_artifacts", retained())
    return store.persist_exact_version(
        canonical_bytes=content,
        manifest=manifest_stub(),
        manifest_bytes=canonical_json({"manifest": "f1"}),
        provenance_bytes=canonical_json({"provenance": "f1"}),
        lineage_bytes=canonical_json({"lineage": "f1"}),
        retained_artifacts=artifacts,  # type: ignore[arg-type]
        storage_adapter_contract_ref=ref("adapter"),
        verification_evidence_refs=(ref("verification"),),
        completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
        **kwargs,
    )


def reason(error: pytest.ExceptionInfo[PhysicalPersistenceError], expected: PhysicalPersistenceReason) -> None:
    assert error.value.reason is expected


def test_f1_a_write_read_replay_and_historical_immutability(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    first = persist(store)
    replay = persist(store)
    assert replay.receipt == first.receipt
    retrieval, content = store.read_exact_version(first.receipt.stored_version_id)
    assert content == b"canonical"
    assert retrieval.written_content_digest == EvidenceContentDigest.from_bytes(content)
    second = persist(store, b"new-version")
    assert store.read_exact_version(first.receipt.stored_version_id)[1] == b"canonical"
    assert store.read_exact_version(second.receipt.stored_version_id)[1] == b"new-version"


def test_f1_a_missing_corrupt_truncated_and_manifest_mismatch_fail_closed(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    result = persist(store)
    metadata_path = next((store.root / "versions").iterdir())
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    canonical = next(item for item in metadata["objects"] if item["kind"] == "CANONICAL_ANALYTICAL_BYTES")
    digest_hex = canonical["content_digest"].removeprefix("sha256:")
    object_path = store.root / "objects" / digest_hex[:2] / f"{digest_hex[2:]}.bin"
    original = object_path.read_bytes()
    object_path.unlink()
    with pytest.raises(PhysicalPersistenceError) as missing:
        store.read_exact_version(result.receipt.stored_version_id)
    reason(missing, PhysicalPersistenceReason.OBJECT_MISSING)
    object_path.write_bytes(original[:-1])
    with pytest.raises(PhysicalPersistenceError) as truncated:
        store.read_exact_version(result.receipt.stored_version_id)
    reason(truncated, PhysicalPersistenceReason.OBJECT_CORRUPT)
    object_path.write_bytes(original)
    metadata["manifest_id"] = "c09-dataset-manifest:forged"
    metadata_path.write_bytes(canonical_json(metadata))
    with pytest.raises(PhysicalPersistenceError) as mismatch:
        store.read_exact_version(result.receipt.stored_version_id)
    reason(mismatch, PhysicalPersistenceReason.VERSION_IDENTITY_MISMATCH)


def test_f1_a_write_once_collision_and_overwrite_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    result = persist(store)
    version_path = next((store.root / "versions").iterdir())
    version_path.write_bytes(b"{}")
    with pytest.raises(PhysicalPersistenceError) as collision:
        persist(store)
    reason(collision, PhysicalPersistenceReason.OBJECT_COLLISION)
    monkeypatch.setattr(os, "link", lambda *_: (_ for _ in ()).throw(OSError("write failure")))
    with pytest.raises(PhysicalPersistenceError) as write_failure:
        persist(LocalPhysicalDatasetStore(tmp_path / "failed", policy()), b"other")
    reason(write_failure, PhysicalPersistenceReason.WRITE_FAILED)
    assert result.receipt.stored_version_id.value


def test_f1_b_atomic_publication_preserves_previous_on_interruption(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    first = persist(store)
    first_receipt = store.publish_exact_version(
        stored_version_id=first.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publish-first"),),
        completed_at=Timestamp(datetime(2026, 10, 6, 1, tzinfo=timezone.utc)),
    )
    second = persist(store, b"second")
    with pytest.raises(PhysicalPersistenceError) as interrupted:
        store.publish_exact_version(
            stored_version_id=second.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=first_receipt.receipt_id(),
            verification_evidence_refs=(ref("publish-second"),),
            completed_at=Timestamp(datetime(2026, 10, 6, 2, tzinfo=timezone.utc)),
            failure_point=FailurePoint.BEFORE_PUBLICATION_COMMIT,
        )
    reason(interrupted, PhysicalPersistenceReason.INJECTED_INTERRUPTION)
    assert store.published_version() == first.receipt.stored_version_id
    report = store.recover()
    assert report.publication_state is RecoveryState.COMMITTED_VALID
    assert RecoveryState.COMPLETED_BUT_UNCOMMITTED in {item.state for item in report.staging_entries}


def test_f1_b_rejects_invalid_predecessor_and_missing_or_corrupt_target(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    result = persist(store)
    with pytest.raises(PhysicalPersistenceError) as predecessor:
        store.publish_exact_version(
            stored_version_id=result.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=PublicationReceiptId("c11-publication-receipt:forged"),
            verification_evidence_refs=(ref("publication"),),
            completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
        )
    reason(predecessor, PhysicalPersistenceReason.PUBLICATION_PREDECESSOR_MISMATCH)
    next((store.root / "versions").iterdir()).unlink()
    with pytest.raises(PhysicalPersistenceError) as missing:
        store.publish_exact_version(
            stored_version_id=result.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=None,
            verification_evidence_refs=(ref("publication"),),
            completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
        )
    reason(missing, PhysicalPersistenceReason.VERSION_NOT_ESTABLISHED)


def test_f1_c_cold_restart_and_invalid_marker_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = LocalPhysicalDatasetStore(root, policy())
    result = persist(store)
    store.publish_exact_version(
        stored_version_id=result.receipt.stored_version_id,
        manifest=manifest_stub(), expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publication"),),
        completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
    )
    restarted = LocalPhysicalDatasetStore(root, policy())
    report = restarted.recover()
    assert report.publication_state is RecoveryState.COMMITTED_VALID
    assert report.published_stored_version_id == result.receipt.stored_version_id
    (root / "publication" / "current.json").write_bytes(canonical_json({"stored_version_id": "c11-stored-version:missing"}))
    assert restarted.recover().publication_state is RecoveryState.MARKER_TO_INVALID_VERSION


def test_f1_c_staged_only_and_corrupt_staging_remain_non_authoritative(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    staged = store.root / "staging" / "partial"
    staged.mkdir()
    (staged / "object-0.bin").write_bytes(b"partial")
    (store.root / "staging" / "corrupt.json").write_bytes(b"not-json")
    report = store.recover()
    states = {item.identity: item.state for item in report.staging_entries}
    assert states == {"corrupt.json": RecoveryState.CORRUPT, "partial": RecoveryState.STAGED_ONLY}
    assert store.published_version() is None


def test_f1_d_external_reference_requires_exact_immutable_attributable_entitled_source(
    tmp_path: Path,
) -> None:
    external = ImmutableExternalReference(
        "source-object:v1", "provider:test", "version:immutable-1", digest("source"),
        Timestamp(datetime(2026, 10, 5, tzinfo=timezone.utc)),
        Timestamp(datetime(2026, 10, 4, tzinfo=timezone.utc)), ref("entitlement"), True,
    )
    artifact = RetainedArtifact(RetainedArtifactKind.SOURCE_EVIDENCE, "source-1", external_reference=external)
    assert artifact.external_reference is external
    artifacts = tuple(
        item for item in retained() if item.kind is not RetainedArtifactKind.SOURCE_EVIDENCE
    ) + (artifact,)
    store = LocalPhysicalDatasetStore(tmp_path / "external", policy())
    result = persist(store, retained_artifacts=artifacts)
    assert store.read_exact_version(result.receipt.stored_version_id)[1] == b"canonical"
    with pytest.raises(PhysicalPersistenceError) as mutable:
        ImmutableExternalReference(
            "latest", "provider:test", "version:1", digest("source"),
            Timestamp(datetime(2026, 10, 5, tzinfo=timezone.utc)),
            Timestamp(datetime(2026, 10, 4, tzinfo=timezone.utc)), ref("entitlement"), True,
        )
    reason(mutable, PhysicalPersistenceReason.EXTERNAL_REFERENCE_INSUFFICIENT)
    with pytest.raises(PhysicalPersistenceError) as digest_only:
        RetainedArtifact(RetainedArtifactKind.SOURCE_EVIDENCE, "source-2")
    reason(digest_only, PhysicalPersistenceReason.RETENTION_EVIDENCE_NOT_ESTABLISHED)


@pytest.mark.parametrize(
    ("limit", "value"),
    [("MAX_PERSISTED_OBJECT_BYTES", 8), ("MAX_BUNDLE_BYTES", 100), ("MAX_METADATA_BYTES", 100), ("MAX_OBJECTS_PER_VERSION", 4)],
)
def test_physical_resource_limits_fail_closed(tmp_path: Path, limit: str, value: int) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / limit, policy(**{limit: value}))
    with pytest.raises(PhysicalPersistenceError) as error:
        persist(store, b"canonical-data")
    reason(error, PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED)


def test_physical_resource_limits_accept_exact_boundaries(tmp_path: Path) -> None:
    measuring = LocalPhysicalDatasetStore(tmp_path / "measuring", policy())
    persist(measuring)
    metadata_path = next((measuring.root / "versions").iterdir())
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata_size = len(metadata_path.read_bytes())
    object_sizes = [item["size"] for item in metadata["objects"]]
    exact = policy(
        MAX_PERSISTED_OBJECT_BYTES=max(object_sizes),
        MAX_BUNDLE_BYTES=sum(object_sizes) + metadata_size,
        MAX_OBJECTS_PER_VERSION=len(object_sizes),
        MAX_METADATA_BYTES=metadata_size,
        MAX_RECOVERY_STAGING_ENTRIES=1,
    )
    result = persist(LocalPhysicalDatasetStore(tmp_path / "exact", exact))
    assert result.canonical_bytes == b"canonical"


def test_recovery_staging_entry_limit_fails_closed(tmp_path: Path) -> None:
    store = LocalPhysicalDatasetStore(
        tmp_path / "store", policy(MAX_RECOVERY_STAGING_ENTRIES=1)
    )
    with pytest.raises(PhysicalPersistenceError) as interrupted:
        persist(store, failure_point=FailurePoint.AFTER_STAGING)
    reason(interrupted, PhysicalPersistenceReason.INJECTED_INTERRUPTION)
    with pytest.raises(PhysicalPersistenceError) as exhausted:
        persist(store, b"second")
    reason(exhausted, PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED)


def test_path_and_reparse_safety_rejects_external_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import automated_trading_bot.datasets.physical_persistence as module

    link = tmp_path / "link"
    link.mkdir()
    monkeypatch.setattr(module, "_is_reparse", lambda path: path == link)
    with pytest.raises(PhysicalPersistenceError) as unsafe:
        LocalPhysicalDatasetStore(link, policy())
    reason(unsafe, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)

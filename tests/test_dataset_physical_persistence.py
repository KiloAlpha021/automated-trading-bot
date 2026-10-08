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
    marker_path = root / "publication" / "current.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["stored_version_id"] = "c11-stored-version:missing"
    marker_path.write_bytes(canonical_json(marker))
    assert restarted.recover().publication_state is RecoveryState.MARKER_TO_INVALID_VERSION


@pytest.mark.parametrize(
    "case",
    (
        "missing_visibility", "missing_receipt", "missing_digest", "missing_version",
        "extra_field", "wrong_visibility",
        "wrong_visibility_type", "wrong_receipt_type", "wrong_version_type", "wrong_digest_type",
        "malformed_receipt", "malformed_version", "malformed_digest", "mismatched_digest",
        "padded_receipt", "padded_version", "padded_digest", "empty_receipt", "non_nfc_receipt",
        "noncanonical_json", "malformed_json",
    ),
)
def test_publication_marker_contract_rejects_invalid_marker_for_all_consumers(
    tmp_path: Path, case: str
) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    persisted = persist(store)
    receipt = store.publish_exact_version(
        stored_version_id=persisted.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publication"),),
        completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
    )
    marker_path = store.root / "publication" / "current.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    missing = {
        "missing_visibility": "consumer_visibility_state",
        "missing_receipt": "publication_receipt_id",
        "missing_digest": "written_content_digest",
        "missing_version": "stored_version_id",
    }
    if case in missing:
        del marker[missing[case]]
    elif case == "extra_field":
        marker["unexpected"] = "value"
    elif case == "wrong_visibility":
        marker["consumer_visibility_state"] = "STAGED"
    elif case.endswith("_type"):
        field = {
            "wrong_visibility_type": "consumer_visibility_state",
            "wrong_receipt_type": "publication_receipt_id",
            "wrong_version_type": "stored_version_id",
            "wrong_digest_type": "written_content_digest",
        }[case]
        marker[field] = None
    elif case == "malformed_receipt":
        marker["publication_receipt_id"] = "bad receipt"
    elif case == "malformed_version":
        marker["stored_version_id"] = "bad version"
    elif case == "malformed_digest":
        marker["written_content_digest"] = "sha256:invalid"
    elif case == "mismatched_digest":
        marker["written_content_digest"] = digest("different content").value
    elif case == "padded_receipt":
        marker["publication_receipt_id"] = " " + marker["publication_receipt_id"]
    elif case == "padded_version":
        marker["stored_version_id"] += " "
    elif case == "padded_digest":
        marker["written_content_digest"] += " "
    elif case == "empty_receipt":
        marker["publication_receipt_id"] = ""
    elif case == "non_nfc_receipt":
        marker["publication_receipt_id"] = "c11-publication-receipt:cafe\u0301"
    elif case == "noncanonical_json":
        marker_path.write_bytes(json.dumps(marker, indent=2).encode("utf-8"))
    elif case == "malformed_json":
        marker_path.write_bytes(b"{")
    else:
        raise AssertionError(case)
    if case not in {"noncanonical_json", "malformed_json"}:
        marker_path.write_bytes(
            json.dumps(marker, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
    invalid_bytes = marker_path.read_bytes()

    with pytest.raises(PhysicalPersistenceError) as published:
        store.published_version()
    reason(published, PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID)
    with pytest.raises(PhysicalPersistenceError) as predecessor:
        store.publish_exact_version(
            stored_version_id=persisted.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=receipt.receipt_id(),
            verification_evidence_refs=(ref("publication-again"),),
            completed_at=Timestamp(datetime(2026, 10, 6, 1, tzinfo=timezone.utc)),
        )
    reason(predecessor, PhysicalPersistenceReason.PUBLICATION_MARKER_INVALID)
    report = LocalPhysicalDatasetStore(store.root, policy()).recover()
    assert report.publication_state is RecoveryState.CORRUPT
    assert report.published_stored_version_id is None
    assert marker_path.read_bytes() == invalid_bytes
    assert store.read_exact_version(persisted.receipt.stored_version_id)[1] == b"canonical"


def test_unrelated_marker_consumer_failure_is_not_reclassified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    persisted = persist(store)
    store.publish_exact_version(
        stored_version_id=persisted.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publication"),),
        completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
    )

    def unrelated_failure(_: object) -> bytes:
        raise RuntimeError("unrelated programming failure")

    monkeypatch.setattr(
        "automated_trading_bot.datasets.physical_persistence.canonical_json",
        unrelated_failure,
    )
    with pytest.raises(RuntimeError, match="unrelated programming failure"):
        store.published_version()
    with pytest.raises(RuntimeError, match="unrelated programming failure"):
        store.publish_exact_version(
            stored_version_id=persisted.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=None,
            verification_evidence_refs=(ref("publication-again"),),
            completed_at=Timestamp(datetime(2026, 10, 6, 1, tzinfo=timezone.utc)),
        )
    with pytest.raises(RuntimeError, match="unrelated programming failure"):
        store.recover()


def test_complete_publication_marker_survives_restart_and_preserves_predecessor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    store = LocalPhysicalDatasetStore(root, policy())
    first = persist(store)
    first_receipt = store.publish_exact_version(
        stored_version_id=first.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publish-first"),),
        completed_at=Timestamp(datetime(2026, 10, 6, 1, tzinfo=timezone.utc)),
    )
    assert store.publish_exact_version(
        stored_version_id=first.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("publish-first"),),
        completed_at=Timestamp(datetime(2026, 10, 6, 1, tzinfo=timezone.utc)),
    ) == first_receipt
    second = persist(store, b"second")
    second_receipt = store.publish_exact_version(
        stored_version_id=second.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=first_receipt.receipt_id(),
        verification_evidence_refs=(ref("publish-second"),),
        completed_at=Timestamp(datetime(2026, 10, 6, 2, tzinfo=timezone.utc)),
    )
    marker = json.loads((root / "publication" / "current.json").read_text(encoding="utf-8"))
    assert marker == {
        "consumer_visibility_state": "COMPLETE",
        "publication_receipt_id": second_receipt.receipt_id().value,
        "stored_version_id": second.receipt.stored_version_id.value,
        "written_content_digest": second.receipt.written_content_digest.value,
    }
    restarted = LocalPhysicalDatasetStore(root, policy())
    assert restarted.published_version() == second.receipt.stored_version_id
    assert restarted.recover().publication_state is RecoveryState.COMMITTED_VALID
    assert restarted.read_exact_version(first.receipt.stored_version_id)[1] == b"canonical"


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


def test_post_initialization_reparse_substitution_fails_closed_at_every_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import automated_trading_bot.datasets.physical_persistence as module

    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    persisted = persist(store)
    original = module._is_reparse

    def attack(path: Path) -> None:
        monkeypatch.setattr(
            module,
            "_is_reparse",
            lambda candidate: candidate == path or original(candidate),
        )

    attack(store.root / "versions")
    with pytest.raises(PhysicalPersistenceError) as redirected_read:
        store.read_exact_version(persisted.receipt.stored_version_id)
    reason(redirected_read, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)

    monkeypatch.setattr(module, "_is_reparse", original)
    attack(store.root / "staging")
    with pytest.raises(PhysicalPersistenceError) as redirected_write:
        persist(store, b"must-not-be-written")
    reason(redirected_write, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)

    monkeypatch.setattr(module, "_is_reparse", original)
    attack(store.root / "publication")
    with pytest.raises(PhysicalPersistenceError) as redirected_publication:
        store.publish_exact_version(
            stored_version_id=persisted.receipt.stored_version_id,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=None,
            verification_evidence_refs=(ref("publish-reparse"),),
            completed_at=Timestamp(datetime(2026, 10, 6, tzinfo=timezone.utc)),
        )
    reason(redirected_publication, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)

    monkeypatch.setattr(module, "_is_reparse", original)
    object_digest = EvidenceContentDigest.from_bytes(b"canonical").value.removeprefix("sha256:")
    attack(store.root / "objects" / object_digest[:2])
    with pytest.raises(PhysicalPersistenceError) as redirected_object:
        store.read_exact_version(persisted.receipt.stored_version_id)
    reason(redirected_object, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)

    monkeypatch.setattr(module, "_is_reparse", original)
    attack(store.root)
    with pytest.raises(PhysicalPersistenceError) as redirected_recovery:
        store.recover()
    reason(redirected_recovery, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)


def test_post_initialization_real_symlink_substitution_fails_closed_when_supported(
    tmp_path: Path,
) -> None:
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    external = tmp_path / "external"
    external.mkdir()
    publication = store.root / "publication"
    publication.rmdir()
    try:
        publication.symlink_to(external, target_is_directory=True)
    except OSError as error:
        publication.mkdir()
        pytest.skip(f"directory symlink/reparse creation unavailable: {error}")

    with pytest.raises(PhysicalPersistenceError) as redirected:
        store.published_version()
    reason(redirected, PhysicalPersistenceReason.STORE_ROOT_UNSAFE)
    assert list(external.iterdir()) == []


def _da01_published_store(tmp_path: Path):
    store = LocalPhysicalDatasetStore(tmp_path / "store", policy())
    persisted = persist(store)
    store.publish_exact_version(
        stored_version_id=persisted.receipt.stored_version_id,
        manifest=manifest_stub(),
        expected_predecessor_publication_ref=None,
        verification_evidence_refs=(ref("da01-publication"),),
        completed_at=Timestamp(datetime(2026, 10, 8, tzinfo=timezone.utc)),
    )
    return store, persisted, next((store.root / "versions").iterdir())


def _da01_assert_invalid_consumers(store, persisted) -> None:
    identity = persisted.receipt.stored_version_id
    with pytest.raises(PhysicalPersistenceError) as reading:
        store.read_exact_version(identity)
    assert reading.value.reason in {
        PhysicalPersistenceReason.VERSION_BUNDLE_INVALID,
        PhysicalPersistenceReason.OBJECT_CORRUPT,
    }
    with pytest.raises(PhysicalPersistenceError):
        store.published_version()
    with pytest.raises(PhysicalPersistenceError):
        store.publish_exact_version(
            stored_version_id=identity,
            manifest=manifest_stub(),
            expected_predecessor_publication_ref=None,
            verification_evidence_refs=(ref("da01-republish"),),
            completed_at=Timestamp(datetime(2026, 10, 8, tzinfo=timezone.utc)),
        )
    restarted = LocalPhysicalDatasetStore(store.root, policy())
    report = restarted.recover()
    assert report.publication_state is RecoveryState.MARKER_TO_INVALID_VERSION
    assert report.published_stored_version_id is None


@pytest.mark.parametrize("kind", tuple(RetainedArtifactKind))
def test_da01_required_inventory_omission_rejects_all_consumers(
    tmp_path: Path, kind: RetainedArtifactKind
) -> None:
    store, persisted, path = _da01_published_store(tmp_path)
    metadata = json.loads(path.read_bytes())
    metadata["objects"] = [
        item for item in metadata["objects"] if item["kind"] != kind.value
    ]
    path.write_bytes(canonical_json(metadata))
    _da01_assert_invalid_consumers(store, persisted)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_digest", "missing_identity", "entry_type", "digest",
        "padded_digest", "negative_size", "boolean_size", "string_size",
        "extra_field", "duplicate_retained", "duplicate_base", "whitespace",
    ),
)
def test_da01_malformed_inventory_rejects_all_consumers(
    tmp_path: Path, mutation: str
) -> None:
    store, persisted, path = _da01_published_store(tmp_path)
    metadata = json.loads(path.read_bytes())
    records = metadata["objects"]
    target = next(item for item in records if item["kind"] == "SOURCE_EVIDENCE")
    if mutation == "missing_digest":
        del target["content_digest"]
    elif mutation == "missing_identity":
        del target["identity"]
    elif mutation == "entry_type":
        records[records.index(target)] = 7
    elif mutation == "digest":
        target["content_digest"] = "sha256:not-a-digest"
    elif mutation == "padded_digest":
        target["content_digest"] = " " + target["content_digest"]
    elif mutation == "negative_size":
        target["size"] = -1
    elif mutation == "boolean_size":
        target["size"] = True
    elif mutation == "string_size":
        target["size"] = str(target["size"])
    elif mutation == "extra_field":
        target["extra"] = "unexpected"
    elif mutation == "duplicate_retained":
        records.append(dict(target))
    elif mutation == "duplicate_base":
        base = dict(next(item for item in records if item["kind"] == "DATASET_MANIFEST"))
        base["identity"] = "another-manifest"
        records.append(base)
    if mutation == "padded_digest":
        content = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    else:
        content = canonical_json(metadata)
    if mutation == "whitespace":
        content = b" " + content
    path.write_bytes(content)
    _da01_assert_invalid_consumers(store, persisted)


def test_da01_preserves_protected_retained_identity_namespace(tmp_path: Path) -> None:
    # The protected writer reserves no generated base-object names in the
    # separately uniqueness-checked caller-supplied retained identity set.
    artifacts = tuple(
        RetainedArtifact(
            item.kind,
            "manifest" if item.kind is RetainedArtifactKind.SOURCE_EVIDENCE else item.identity,
            item.content,
            item.external_reference,
        )
        for item in retained()
    )
    store = LocalPhysicalDatasetStore(tmp_path / "historical-contract", policy())
    persisted = persist(store, retained_artifacts=artifacts)
    assert store.read_exact_version(persisted.receipt.stored_version_id)[1] == b"canonical"


def test_da01_retained_inventory_resource_limit(tmp_path: Path) -> None:
    store, persisted, path = _da01_published_store(tmp_path)
    metadata = json.loads(path.read_bytes())
    records = metadata["objects"]
    source = next(item for item in records if item["kind"] == "SOURCE_EVIDENCE")
    limit = policy().value("MAX_OBJECTS_PER_VERSION")
    for index in range(limit - len(records) + 1):
        record = dict(source)
        record["identity"] = f"retained-source-{index}"
        records.append(record)
    content = canonical_json(metadata)
    assert len(records) == limit + 1
    assert len(content) <= policy().value("MAX_METADATA_BYTES")
    path.write_bytes(content)
    with pytest.raises(PhysicalPersistenceError) as failure:
        store.read_exact_version(persisted.receipt.stored_version_id)
    assert failure.value.reason is PhysicalPersistenceReason.RESOURCE_LIMIT_EXCEEDED

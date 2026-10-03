from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import inspect

import pytest

from automated_trading_bot.datasets.materialization import (
    SHARED_CONTRACT_VERSION,
    DatasetVersionId,
    LogicalContentId,
)
from automated_trading_bot.datasets.persistence import (
    ConsumerVisibilityState,
    DatasetPersistenceError,
    DatasetPersistenceReason,
    ExactVersionRetrievalRequest,
    ExactVersionRetrievalResult,
    PersistenceOperation,
    PersistenceReceiptId,
    PublicationOperation,
    PublicationReceiptId,
    StoredVersionId,
    derive_persistence_receipt_id,
    derive_publication_receipt_id,
    derive_publication_receipt_ids,
    derive_stored_version_id,
    validate_publication_semantics,
    verify_exact_version_retrieval,
    verify_persistence_receipt_id,
    verify_publication_receipt_id,
)
from automated_trading_bot.datasets.provenance import (
    REQUIRED_RESOURCE_LIMITS,
    DatasetLifecycleResourcePolicy,
    DatasetLifecycleResourcePolicyId,
    ManifestId,
    ResourcePolicyError,
    canonical_json,
)
from automated_trading_bot.instruments.model import (
    DatasetId,
    EvidenceContentDigest,
    EvidenceId,
    EvidenceIdentityConflict,
    EvidenceRef,
    SourceId,
    canonicalize_evidence_refs,
)


def digest(value: str) -> EvidenceContentDigest:
    return EvidenceContentDigest.from_bytes(value.encode())


def ref(name: str, *, content: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        SourceId("source:test/c11"),
        DatasetId("dataset:test/c11"),
        EvidenceId(f"evidence:test/{name}"),
        digest(name if content is None else content),
    )


def policy(limit: int = 2) -> DatasetLifecycleResourcePolicy:
    limits = {name: 32 for name in REQUIRED_RESOURCE_LIMITS}
    limits["MAX_PUBLICATION_RECORDS_PER_BATCH"] = limit
    return DatasetLifecycleResourcePolicy(
        DatasetLifecycleResourcePolicyId(f"resource-policy:test/c11-{limit}"),
        ref(f"resource-policy-{limit}"),
        tuple(limits.items()),
    )


def stored_version(*, content: str = "written") -> StoredVersionId:
    return derive_stored_version_id(
        contract_version=SHARED_CONTRACT_VERSION,
        dataset_version_id=DatasetVersionId("c08-dataset-version:test"),
        manifest_id=ManifestId("c09-dataset-manifest:test"),
        logical_content_id=LogicalContentId("c08-logical-content:test"),
        written_content_digest=digest(content),
    )


def publication_body(index: int = 1) -> dict[str, object]:
    return {
        "contract_version": SHARED_CONTRACT_VERSION,
        "publication_operation": PublicationOperation.PUBLISH_EXACT_VERSION.value,
        "sequence": index,
    }


def assert_reason(error: pytest.ExceptionInfo[DatasetPersistenceError], reason: DatasetPersistenceReason) -> None:
    assert error.value.reason is reason
    assert str(error.value) == reason.value


def test_stored_version_identity_uses_exact_protected_body() -> None:
    dataset = DatasetVersionId("c08-dataset-version:test")
    manifest = ManifestId("c09-dataset-manifest:test")
    logical = LogicalContentId("c08-logical-content:test")
    written = digest("written")
    value = derive_stored_version_id(
        contract_version=SHARED_CONTRACT_VERSION,
        dataset_version_id=dataset,
        manifest_id=manifest,
        logical_content_id=logical,
        written_content_digest=written,
    )
    body = {
        "contract_version": SHARED_CONTRACT_VERSION,
        "dataset_version_id": dataset.value,
        "manifest_id": manifest.value,
        "logical_content_id": logical.value,
        "written_content_digest": written.value,
    }
    expected = EvidenceContentDigest.from_bytes(
        b"ATIS:C11:STORED_VERSION:1\0" + canonical_json(body)
    )
    assert value.value == f"c11-stored-version:{expected.value.removeprefix('sha256:')}"
    assert type(value) is StoredVersionId
    assert type(value) is not DatasetVersionId
    assert value == stored_version()


@pytest.mark.parametrize(
    ("override", "replacement"),
    [
        ("contract_version", "different-contract"),
        ("dataset_version_id", DatasetVersionId("c08-dataset-version:other")),
        ("manifest_id", ManifestId("c09-dataset-manifest:other")),
        ("logical_content_id", LogicalContentId("c08-logical-content:other")),
        ("written_content_digest", digest("other")),
    ],
)
def test_each_stored_version_semantic_input_changes_identity(
    override: str, replacement: object
) -> None:
    inputs: dict[str, object] = {
        "contract_version": SHARED_CONTRACT_VERSION,
        "dataset_version_id": DatasetVersionId("c08-dataset-version:test"),
        "manifest_id": ManifestId("c09-dataset-manifest:test"),
        "logical_content_id": LogicalContentId("c08-logical-content:test"),
        "written_content_digest": digest("written"),
    }
    original = derive_stored_version_id(**inputs)  # type: ignore[arg-type]
    inputs[override] = replacement
    assert derive_stored_version_id(**inputs) != original  # type: ignore[arg-type]


def test_receipt_identities_are_deterministic_domain_separated_and_key_order_independent() -> None:
    body = {"z": 1, "a": "semantic"}
    reordered = {"a": "semantic", "z": 1}
    persistence = derive_persistence_receipt_id(body)
    publication = derive_publication_receipt_id(body)
    assert persistence == derive_persistence_receipt_id(reordered)
    assert publication == derive_publication_receipt_id(reordered)
    assert persistence.value.startswith("c11-persistence-receipt:")
    assert publication.value.startswith("c11-publication-receipt:")
    assert persistence.value != publication.value


def test_receipt_identity_rejects_claimed_identity_in_semantic_body() -> None:
    with pytest.raises(DatasetPersistenceError) as error:
        derive_persistence_receipt_id({"persistence_receipt_id": "forged"})
    assert_reason(error, DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    with pytest.raises(DatasetPersistenceError) as publication_error:
        derive_publication_receipt_id({"publication_receipt_id": "forged"})
    assert_reason(publication_error, DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)


def test_claimed_receipt_identity_conflicts_fail_closed() -> None:
    body = {"semantic": "body"}
    with pytest.raises(DatasetPersistenceError) as persistence_error:
        verify_persistence_receipt_id(
            PersistenceReceiptId("c11-persistence-receipt:forged"), body
        )
    assert_reason(persistence_error, DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)
    with pytest.raises(DatasetPersistenceError) as publication_error:
        verify_publication_receipt_id(
            PublicationReceiptId("c11-publication-receipt:forged"), body
        )
    assert_reason(publication_error, DatasetPersistenceReason.IDENTITY_CONTENT_CONFLICT)


def test_exact_bounded_vocabularies() -> None:
    assert list(PersistenceOperation) == [PersistenceOperation.PERSIST_EXACT_VERSION]
    assert list(PublicationOperation) == [PublicationOperation.PUBLISH_EXACT_VERSION]
    assert list(ConsumerVisibilityState) == [ConsumerVisibilityState.COMPLETE]
    assert {item.value for item in DatasetPersistenceReason} == {
        "IDENTITY_CONTENT_CONFLICT",
        "CONTENT_DIGEST_MISMATCH",
        "EXACT_VERSION_NOT_ESTABLISHED",
        "EXACT_VERSION_AMBIGUOUS",
        "VERIFICATION_EVIDENCE_NOT_ESTABLISHED",
        "PARTIAL_PUBLICATION_PROHIBITED",
        "PREDECESSOR_PUBLICATION_MISMATCH",
        "UNSUPPORTED_OPERATION",
        "UNSUPPORTED_VISIBILITY_STATE",
        "RESOURCE_LIMIT_EXCEEDED",
    }


def test_exact_version_retrieval_returns_only_the_exact_verified_result() -> None:
    version = stored_version()
    written = digest("written")
    request = ExactVersionRetrievalRequest(version, written)
    result = ExactVersionRetrievalResult(version, written, (ref("b"), ref("a")))
    assert verify_exact_version_retrieval(request, (result,)) is result
    expected = canonicalize_evidence_refs((ref("b"), ref("a")))
    assert result.verification_evidence_refs == expected


def test_exact_version_retrieval_rejects_missing_ambiguous_and_substituted_versions() -> None:
    request = ExactVersionRetrievalRequest(stored_version(), digest("written"))
    result = ExactVersionRetrievalResult(stored_version(), digest("written"), (ref("proof"),))
    with pytest.raises(DatasetPersistenceError) as missing:
        verify_exact_version_retrieval(request, ())
    assert_reason(missing, DatasetPersistenceReason.EXACT_VERSION_NOT_ESTABLISHED)
    with pytest.raises(DatasetPersistenceError) as ambiguous:
        verify_exact_version_retrieval(request, (result, result))
    assert_reason(ambiguous, DatasetPersistenceReason.EXACT_VERSION_AMBIGUOUS)
    wrong = ExactVersionRetrievalResult(stored_version(content="other"), digest("written"), (ref("proof"),))
    with pytest.raises(DatasetPersistenceError) as substituted:
        verify_exact_version_retrieval(request, (wrong,))
    assert_reason(substituted, DatasetPersistenceReason.EXACT_VERSION_NOT_ESTABLISHED)


def test_exact_version_retrieval_rejects_digest_mismatch_and_missing_evidence() -> None:
    version = stored_version()
    request = ExactVersionRetrievalRequest(version, digest("written"))
    mismatch = ExactVersionRetrievalResult(version, digest("other"), (ref("proof"),))
    with pytest.raises(DatasetPersistenceError) as digest_error:
        verify_exact_version_retrieval(request, (mismatch,))
    assert_reason(digest_error, DatasetPersistenceReason.CONTENT_DIGEST_MISMATCH)
    empty = ExactVersionRetrievalResult(version, digest("written"), ())
    with pytest.raises(DatasetPersistenceError) as evidence_error:
        verify_exact_version_retrieval(request, (empty,))
    assert_reason(
        evidence_error, DatasetPersistenceReason.VERIFICATION_EVIDENCE_NOT_ESTABLISHED
    )


def test_evidence_identity_conflict_propagates_unchanged() -> None:
    with pytest.raises(EvidenceIdentityConflict):
        ExactVersionRetrievalResult(
            stored_version(),
            digest("written"),
            (ref("same", content="one"), ref("same", content="two")),
        )


def test_publication_semantics_are_complete_only_and_predecessor_exact() -> None:
    predecessor = derive_publication_receipt_id({"predecessor": 1})
    validate_publication_semantics(
        operation=PublicationOperation.PUBLISH_EXACT_VERSION,
        visibility_state=ConsumerVisibilityState.COMPLETE,
        predecessor_publication_ref=predecessor,
        expected_predecessor_publication_ref=predecessor,
    )
    validate_publication_semantics(
        operation=PublicationOperation.PUBLISH_EXACT_VERSION,
        visibility_state=ConsumerVisibilityState.COMPLETE,
        predecessor_publication_ref=None,
        expected_predecessor_publication_ref=None,
    )
    with pytest.raises(DatasetPersistenceError) as partial:
        validate_publication_semantics(
            operation=PublicationOperation.PUBLISH_EXACT_VERSION,
            visibility_state="PARTIAL",
            predecessor_publication_ref=None,
            expected_predecessor_publication_ref=None,
        )
    assert_reason(partial, DatasetPersistenceReason.PARTIAL_PUBLICATION_PROHIBITED)
    with pytest.raises(DatasetPersistenceError) as predecessor_error:
        validate_publication_semantics(
            operation=PublicationOperation.PUBLISH_EXACT_VERSION,
            visibility_state=ConsumerVisibilityState.COMPLETE,
            predecessor_publication_ref=predecessor,
            expected_predecessor_publication_ref=None,
        )
    assert_reason(
        predecessor_error, DatasetPersistenceReason.PREDECESSOR_PUBLICATION_MISMATCH
    )


def test_unsupported_operation_and_visibility_fail_closed() -> None:
    with pytest.raises(DatasetPersistenceError) as operation_error:
        validate_publication_semantics(
            operation="PUBLISH_LATEST",
            visibility_state=ConsumerVisibilityState.COMPLETE,
            predecessor_publication_ref=None,
            expected_predecessor_publication_ref=None,
        )
    assert_reason(operation_error, DatasetPersistenceReason.UNSUPPORTED_OPERATION)
    with pytest.raises(DatasetPersistenceError) as visibility_error:
        validate_publication_semantics(
            operation=PublicationOperation.PUBLISH_EXACT_VERSION,
            visibility_state="UNKNOWN",
            predecessor_publication_ref=None,
            expected_predecessor_publication_ref=None,
        )
    assert_reason(
        visibility_error, DatasetPersistenceReason.UNSUPPORTED_VISIBILITY_STATE
    )


def test_publication_batch_enforces_external_limit_without_partial_output() -> None:
    bodies = (publication_body(1), publication_body(2))
    assert derive_publication_receipt_ids(bodies, policy(2)) == tuple(
        derive_publication_receipt_id(body) for body in bodies
    )
    with pytest.raises(ResourcePolicyError, match="RESOURCE_LIMIT_EXCEEDED"):
        derive_publication_receipt_ids((*bodies, publication_body(3)), policy(2))
    with pytest.raises(ResourcePolicyError, match="RESOURCE_POLICY_NOT_ESTABLISHED"):
        derive_publication_receipt_ids(bodies, None)  # type: ignore[arg-type]


def test_resource_policy_failure_is_not_wrapped() -> None:
    with pytest.raises(ResourcePolicyError):
        policy(0)


def test_records_are_frozen_slotted_and_have_exact_fields() -> None:
    request = ExactVersionRetrievalRequest(stored_version(), digest("written"))
    result = ExactVersionRetrievalResult(stored_version(), digest("written"), (ref("proof"),))
    assert [field.name for field in fields(request)] == [
        "stored_version_id",
        "expected_written_content_digest",
    ]
    assert [field.name for field in fields(result)] == [
        "stored_version_id",
        "written_content_digest",
        "verification_evidence_refs",
    ]
    assert not hasattr(request, "__dict__")
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.stored_version_id = stored_version(content="other")  # type: ignore[misc]


def test_module_has_no_physical_io_or_downstream_authority_surface() -> None:
    import automated_trading_bot.datasets.persistence as module

    source = inspect.getsource(module)
    forbidden_imports = (
        "pathlib",
        "sqlite3",
        "socket",
        "requests",
        "boto",
        "pyarrow",
        "pandas",
    )
    assert all(f"import {name}" not in source for name in forbidden_imports)
    assert all(f"from {name}" not in source for name in forbidden_imports)
    forbidden_public = (
        "currentness",
        "eligibility",
        "promotion",
        "provider",
        "storage_adapter",
        "trading",
        "financial_effect",
        "ai_trading_authority",
    )
    assert all(not hasattr(module, name) for name in forbidden_public)
    assert not hasattr(module, "PersistenceReceipt")
    assert not hasattr(module, "PublicationReceipt")

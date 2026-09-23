"""S2.7 cross-slice evidence for ATIS-R-022 and ATIS-R-023."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from hashlib import sha1
from itertools import combinations, product
import json
from pathlib import Path
from uuid import UUID

import pytest

from automated_trading_bot.domain.command_contracts import (
    COMMAND_ENVELOPE_V1,
    CommandId,
    VersionedCommandEnvelope,
)
from automated_trading_bot.domain.concurrency import (
    CommitCertainty,
    ConcurrencyDisposition,
    ConcurrencyOutcome,
    ConcurrencyToken,
    ConcurrentTransitionRequest,
    RetryDisposition,
)
from automated_trading_bot.domain.delivery import (
    DestinationId,
    HandlerId,
    HandlerVersion,
    InboxIdentity,
    InboxRecord,
    InboxState,
    OutboxIdentity,
    OutboxRecord,
    OutboxState,
    transition_inbox,
    transition_outbox,
)
from automated_trading_bot.domain.event import EventEnvelope
from automated_trading_bot.domain.event_contracts import (
    EVENT_ENVELOPE_V1,
    EVENT_ENVELOPE_V1_DEFINITION,
    VersionedEventEnvelope,
)
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.idempotency import (
    CommandAdmission,
    EventAdmission,
    EventAdmissionDisposition,
    IdempotencyDisposition,
    IdempotencyRecord,
    OperationScope,
    adjudicate_event,
    adjudicate_idempotency,
    fingerprint_content,
)
from automated_trading_bot.domain.identity_contracts import AggregateId
from automated_trading_bot.domain.persistence import (
    EventPersistenceRequest,
    PersistenceDisposition,
    PersistenceOutcome,
    TransitionPersistenceRequest,
)
from automated_trading_bot.domain.state_transitions import (
    TRANSITION_CONTRACT_V1,
    GenericState,
    StateId,
    TransitionAction,
    TransitionActionId,
    TransitionActionKind,
    TransitionState,
    transition,
)
from automated_trading_bot.domain.timestamp import Timestamp
from automated_trading_bot.domain.transaction_composition import (
    ConsumerTransactionIntent,
    ProducerTransactionIntent,
)
from automated_trading_bot.domain.versioning import (
    ContractVersion,
    UnknownContractError,
    UnsupportedVersionError,
    VersionRegistry,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "docs/stage2/s27-evidence.json"
UUID_VALUE = UUID("00000000-0000-4000-8000-000000000027")
OTHER_UUID = UUID("00000000-0000-4000-8000-000000000028")
EVIDENCE_BYTES = {
    "empty": b"",
    "nul": b"\x00",
    "arbitrary-binary": b"\x00\xff\x10evidence",
}
VERSION_CASES = (
    ("registered-event-envelope-v1", EVENT_ENVELOPE_V1, None),
    ("wrong-family-v1", ContractVersion("other-envelope", 1), UnknownContractError),
    ("event-envelope-v2", ContractVersion("event-envelope", 2), UnsupportedVersionError),
    (
        "event-envelope-max-supported-int-boundary",
        ContractVersion("event-envelope", 2**31 - 1),
        UnsupportedVersionError,
    ),
)
CERTAINTY_RETRY_CASES = tuple(product(CommitCertainty, RetryDisposition))


def _envelope(
    *,
    event_id: EventId | None = None,
    key: IdempotencyKey | None = None,
    payload: bytes = b"payload",
) -> VersionedEventEnvelope:
    envelope = EventEnvelope(
        event_id or EventId(UUID_VALUE),
        CorrelationId(OTHER_UUID),
        CausationId(UUID("00000000-0000-4000-8000-000000000029")),
        key or IdempotencyKey("s27-key"),
        Timestamp(datetime(2026, 1, 1, tzinfo=UTC)),
        1,
    )
    return VersionedEventEnvelope(envelope, payload, EVENT_ENVELOPE_V1)


def _admission(
    *,
    event_id: EventId | None = None,
    key: IdempotencyKey | None = None,
    representation: bytes | None = None,
    scope: str = "s27.event",
) -> EventAdmission:
    event = _envelope(event_id=event_id, key=key)
    canonical = representation or EVENT_ENVELOPE_V1_DEFINITION.codec.encode(event)
    chosen_key = event.envelope.idempotency_key
    record = IdempotencyRecord(
        OperationScope(scope), chosen_key, canonical, fingerprint_content(canonical)
    )
    return EventAdmission(
        event,
        EventPersistenceRequest(EVENT_ENVELOPE_V1, canonical),
        record,
    )


def _transition_case(
    status: TransitionState,
    kind: TransitionActionKind,
    target: TransitionState,
) -> tuple[StateId, object, TransitionPersistenceRequest]:
    state_id = StateId(UUID_VALUE)
    state = GenericState(state_id, status, TRANSITION_CONTRACT_V1)
    action = TransitionAction(
        TransitionActionId(OTHER_UUID), kind, target, TRANSITION_CONTRACT_V1
    )
    result = transition(state, action)
    return state_id, result, TransitionPersistenceRequest(result)


def _concurrent_request(expected: int = 3) -> ConcurrentTransitionRequest:
    state_id, _, request = _transition_case(
        TransitionState.ACTIVE,
        TransitionActionKind.APPLY,
        TransitionState.TERMINAL,
    )
    return ConcurrentTransitionRequest(state_id, request, ConcurrencyToken(expected))


def _pending_outbox(admission: EventAdmission, suffix: str = "primary") -> OutboxRecord:
    return OutboxRecord(
        OutboxIdentity(
            admission.event.envelope.event_id,
            DestinationId(f"destination.{suffix}"),
        ),
        OutboxState.PENDING,
    )


def _inbox(admission: EventAdmission, state: InboxState = InboxState.PENDING) -> InboxRecord:
    return InboxRecord(
        InboxIdentity(admission.event.envelope.event_id, HandlerId("handler.s27")),
        HandlerVersion("v1"),
        state,
    )


def _git_blob(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _manifest() -> dict[str, object]:
    return json.loads(
        EVIDENCE_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=lambda pairs: _unique_object(pairs),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@pytest.mark.parametrize(
    ("version_name", "version", "error", "evidence_name"),
    [(*version_case, evidence_name) for version_case in VERSION_CASES for evidence_name in (*EVIDENCE_BYTES, "canonical-event-envelope")],
    ids=[
        f"{version_name}--{evidence_name}"
        for version_name, _, _ in VERSION_CASES
        for evidence_name in (*EVIDENCE_BYTES, "canonical-event-envelope")
    ],
)
def test_version_and_evidence_property_cases(
    version_name: str,
    version: ContractVersion,
    error: type[Exception] | None,
    evidence_name: str,
) -> None:
    del version_name
    canonical = EVENT_ENVELOPE_V1_DEFINITION.codec.encode(_envelope())
    representation = canonical if evidence_name == "canonical-event-envelope" else EVIDENCE_BYTES[evidence_name]
    request = EventPersistenceRequest(version, representation)
    assert request.representation is representation
    assert fingerprint_content(representation).value
    registry = VersionRegistry((EVENT_ENVELOPE_V1_DEFINITION,))
    if error is None:
        assert registry.codec_for(version) is EVENT_ENVELOPE_V1_DEFINITION.codec
    else:
        with pytest.raises(error):
            registry.codec_for(version)


def test_registered_versions_compose_without_fallback() -> None:
    event = _envelope()
    canonical = EVENT_ENVELOPE_V1_DEFINITION.codec.encode(event)
    registry = VersionRegistry((EVENT_ENVELOPE_V1_DEFINITION,))
    interpretation = registry.interpret(
        EVENT_ENVELOPE_V1, canonical, target=EVENT_ENVELOPE_V1
    )
    request = EventPersistenceRequest(interpretation.original, interpretation.original_bytes)
    assert interpretation.value == event
    assert interpretation.original_bytes is canonical
    assert request.representation is canonical


def test_unknown_and_unsupported_versions_fail_before_persistence() -> None:
    registry = VersionRegistry((EVENT_ENVELOPE_V1_DEFINITION,))
    with pytest.raises(UnknownContractError):
        registry.codec_for(ContractVersion("unknown-envelope", 1))
    with pytest.raises(UnsupportedVersionError):
        registry.codec_for(ContractVersion("event-envelope", 2))


def test_original_bytes_remain_authoritative_under_equal_fingerprint() -> None:
    established = IdempotencyRecord(
        OperationScope("s27.collision"),
        IdempotencyKey("collision-key"),
        b"first",
        fingerprint_content(b"first"),
    )
    candidate = object.__new__(IdempotencyRecord)
    object.__setattr__(candidate, "operation_scope", established.operation_scope)
    object.__setattr__(candidate, "idempotency_key", established.idempotency_key)
    object.__setattr__(candidate, "canonical_bytes", b"second")
    object.__setattr__(candidate, "content_fingerprint", established.content_fingerprint)
    assert candidate is not established
    assert candidate.canonical_bytes != established.canonical_bytes
    assert candidate.content_fingerprint == established.content_fingerprint
    assert (
        adjudicate_idempotency(candidate, established).disposition
        is IdempotencyDisposition.CONFLICTING_REUSE
    )


def test_event_admission_duplicate_remains_separate_from_operation_idempotency() -> None:
    established = _admission()
    duplicate = EventAdmission(
        established.event,
        established.persistence_request,
        established.idempotency_record,
    )
    assert adjudicate_event(duplicate, established).disposition is EventAdmissionDisposition.EXACT_DUPLICATE
    assert adjudicate_idempotency(duplicate.idempotency_record, established.idempotency_record).disposition is IdempotencyDisposition.EXACT_DUPLICATE


def test_event_id_conflicting_evidence_is_not_operation_duplicate() -> None:
    established = _admission()
    conflicting = _admission(
        event_id=established.event.envelope.event_id,
        key=established.event.envelope.idempotency_key,
        representation=b"different-canonical-evidence",
    )
    assert adjudicate_event(conflicting, established).disposition is EventAdmissionDisposition.CONFLICTING_EVENT
    assert adjudicate_idempotency(conflicting.idempotency_record, established.idempotency_record).disposition is IdempotencyDisposition.CONFLICTING_REUSE


def test_same_scoped_key_and_same_evidence_is_exact_duplicate() -> None:
    established = _admission().idempotency_record
    duplicate = IdempotencyRecord(
        established.operation_scope,
        established.idempotency_key,
        established.canonical_bytes,
        established.content_fingerprint,
    )
    assert adjudicate_idempotency(duplicate, established).disposition is IdempotencyDisposition.EXACT_DUPLICATE


def test_same_scoped_key_and_conflicting_evidence_is_conflicting_reuse() -> None:
    established = _admission().idempotency_record
    conflicting = IdempotencyRecord(
        established.operation_scope,
        established.idempotency_key,
        b"different",
        fingerprint_content(b"different"),
    )
    assert adjudicate_idempotency(conflicting, established).disposition is IdempotencyDisposition.CONFLICTING_REUSE


IDENTITY_VALUES = (
    ("AggregateId", AggregateId(UUID_VALUE)),
    ("CausationId", CausationId(UUID_VALUE)),
    ("CommandId", CommandId(UUID_VALUE)),
    ("ConcurrencyToken", ConcurrencyToken(27)),
    ("ContractVersion", ContractVersion("identity", 27)),
    ("CorrelationId", CorrelationId(UUID_VALUE)),
    ("DestinationId", DestinationId("identity.value")),
    ("EventId", EventId(UUID_VALUE)),
    ("HandlerId", HandlerId("identity.value")),
    ("HandlerVersion", HandlerVersion("identity.value")),
    ("IdempotencyKey", IdempotencyKey("identity.value")),
    ("OperationScope", OperationScope("identity.value")),
    ("StateId", StateId(UUID_VALUE)),
    ("TransitionActionId", TransitionActionId(UUID_VALUE)),
)
IDENTITY_PAIRS = tuple(combinations(IDENTITY_VALUES, 2))


@pytest.mark.parametrize(
    ("left", "right"),
    [(left, right) for (_, left), (_, right) in IDENTITY_PAIRS],
    ids=[f"{left_name}--{right_name}" for (left_name, _), (right_name, _) in IDENTITY_PAIRS],
)
def test_identity_separation_property_cases(left: object, right: object) -> None:
    assert type(left) is not type(right)
    assert left != right


def test_cross_category_substitutions_reject() -> None:
    admission = _admission()
    with pytest.raises(TypeError):
        InboxIdentity(CommandId(UUID_VALUE), HandlerId("handler"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ConcurrentTransitionRequest(
            AggregateId(UUID_VALUE),  # type: ignore[arg-type]
            _concurrent_request().request,
            ConcurrencyToken(0),
        )
    command = VersionedCommandEnvelope(
        CommandId(UUID_VALUE),
        CorrelationId(OTHER_UUID),
        CausationId(OTHER_UUID),
        admission.event.envelope.idempotency_key,
        b"payload",
        COMMAND_ENVELOPE_V1,
    )
    inert = CommandAdmission(command, admission.idempotency_record)
    assert not any(hasattr(inert, name) for name in ("execute", "dispatch", "send"))


@pytest.mark.parametrize(
    ("status", "kind", "target"),
    tuple(product(TransitionState, TransitionActionKind, TransitionState)),
    ids=[
        f"{status.value}--{kind.value}--{target.value}"
        for status, kind, target in product(
            TransitionState, TransitionActionKind, TransitionState
        )
    ],
)
def test_transition_persistence_property_cases(
    status: TransitionState,
    kind: TransitionActionKind,
    target: TransitionState,
) -> None:
    state_id, result, request = _transition_case(status, kind, target)
    assert request.result is result
    assert result.previous_state.state_id is state_id
    assert result.next_state.state_id is state_id
    concurrent = ConcurrentTransitionRequest(state_id, request, ConcurrencyToken(0))
    assert concurrent.request is request


def test_concurrent_transition_scope_and_advancement() -> None:
    request = _concurrent_request()
    outcome = ConcurrencyOutcome(
        request,
        PersistenceOutcome(PersistenceDisposition.SUCCESS),
        ConcurrencyDisposition.COMMITTED,
        ConcurrencyToken(4),
        CommitCertainty.COMMITTED,
        RetryDisposition.NO_RETRY,
    )
    assert outcome.current_token == ConcurrencyToken(request.expected_token.value + 1)
    with pytest.raises(ValueError):
        ConcurrentTransitionRequest(StateId(OTHER_UUID), request.request, request.expected_token)


def test_competing_transitions_require_distinct_concurrency_evidence() -> None:
    first = _concurrent_request()
    committed = ConcurrencyOutcome(
        first,
        PersistenceOutcome(PersistenceDisposition.SUCCESS),
        ConcurrencyDisposition.COMMITTED,
        ConcurrencyToken(4),
        CommitCertainty.COMMITTED,
        RetryDisposition.NO_RETRY,
    )
    competing = ConcurrentTransitionRequest(first.state_id, first.request, first.expected_token)
    stale = ConcurrencyOutcome(
        competing,
        PersistenceOutcome(PersistenceDisposition.CONFLICT),
        ConcurrencyDisposition.STALE,
        committed.current_token,
        CommitCertainty.NOT_COMMITTED,
        RetryDisposition.REEVALUATE,
    )
    assert stale.current_token == committed.current_token
    assert stale.retry_disposition is RetryDisposition.REEVALUATE


def test_stale_writer_requires_newer_current_token() -> None:
    request = _concurrent_request()
    for current in (None, ConcurrencyToken(2), ConcurrencyToken(3)):
        with pytest.raises(ValueError):
            ConcurrencyOutcome(
                request,
                PersistenceOutcome(PersistenceDisposition.CONFLICT),
                ConcurrencyDisposition.STALE,
                current,
                CommitCertainty.NOT_COMMITTED,
                RetryDisposition.REEVALUATE,
            )


def test_generic_persistence_conflict_does_not_establish_stale() -> None:
    request = _concurrent_request()
    marker = PersistenceOutcome(PersistenceDisposition.CONFLICT)
    with pytest.raises(ValueError):
        ConcurrencyOutcome(
            request,
            marker,
            ConcurrencyDisposition.STALE,
            None,
            CommitCertainty.NOT_COMMITTED,
            RetryDisposition.REEVALUATE,
        )


def test_stale_result_requires_reevaluation() -> None:
    request = _concurrent_request()
    with pytest.raises(ValueError):
        ConcurrencyOutcome(
            request,
            PersistenceOutcome(PersistenceDisposition.CONFLICT),
            ConcurrencyDisposition.STALE,
            ConcurrencyToken(4),
            CommitCertainty.NOT_COMMITTED,
            RetryDisposition.SAME_IDENTITY,
        )


@pytest.mark.parametrize(
    ("certainty", "retry"),
    CERTAINTY_RETRY_CASES,
    ids=[f"{certainty.value}--{retry.value}" for certainty, retry in CERTAINTY_RETRY_CASES],
)
def test_certainty_retry_property_cases(
    certainty: CommitCertainty,
    retry: RetryDisposition,
) -> None:
    request = _concurrent_request()
    if certainty is CommitCertainty.COMMITTED:
        args = (
            request,
            PersistenceOutcome(PersistenceDisposition.SUCCESS),
            ConcurrencyDisposition.COMMITTED,
            ConcurrencyToken(request.expected_token.value + 1),
            certainty,
            retry,
        )
        constructs = retry is RetryDisposition.NO_RETRY
    elif certainty is CommitCertainty.INDETERMINATE:
        args = (
            request,
            PersistenceOutcome(PersistenceDisposition.FAILURE),
            ConcurrencyDisposition.INDETERMINATE,
            None,
            certainty,
            retry,
        )
        constructs = retry is RetryDisposition.RESOLVE_FIRST
    elif retry is RetryDisposition.REEVALUATE:
        args = (
            request,
            PersistenceOutcome(PersistenceDisposition.CONFLICT),
            ConcurrencyDisposition.STALE,
            ConcurrencyToken(request.expected_token.value + 1),
            certainty,
            retry,
        )
        constructs = True
    else:
        args = (
            request,
            PersistenceOutcome(PersistenceDisposition.FAILURE),
            ConcurrencyDisposition.FAILED,
            None,
            certainty,
            retry,
        )
        constructs = retry in (
            RetryDisposition.NO_RETRY,
            RetryDisposition.SAME_IDENTITY,
        )

    if constructs:
        outcome = ConcurrencyOutcome(*args)
        assert outcome.commit_certainty is certainty
        assert outcome.retry_disposition is retry
    else:
        with pytest.raises(ValueError):
            ConcurrencyOutcome(*args)


def test_producer_transaction_membership_is_descriptive_and_complete() -> None:
    event = _admission()
    outbox = _pending_outbox(event)
    intent = ProducerTransactionIntent(_concurrent_request(), (event,), (outbox,))
    assert intent.events == (event,) and intent.outbox == (outbox,)
    assert not any(hasattr(intent, name) for name in ("commit", "rollback", "execute"))
    with pytest.raises(ValueError):
        ProducerTransactionIntent(_concurrent_request(), (), ())
    with pytest.raises(ValueError):
        ProducerTransactionIntent(_concurrent_request(), (event,), (outbox, outbox))


def test_consumer_transaction_membership_is_descriptive_and_complete() -> None:
    incoming = _admission()
    outgoing = _admission(event_id=EventId(OTHER_UUID), key=IdempotencyKey("outgoing"))
    applied = transition_inbox(_inbox(incoming), InboxState.APPLIED)
    outbox = _pending_outbox(outgoing)
    intent = ConsumerTransactionIntent(
        applied, _concurrent_request(), (outgoing,), (outbox,)
    )
    assert intent.inbox_transition is applied
    with pytest.raises(ValueError):
        ConsumerTransactionIntent(
            transition_inbox(_inbox(incoming), InboxState.RETRY),
            _concurrent_request(),
            (),
            (),
        )


def test_commit_before_acknowledgement_remains_indeterminate() -> None:
    request = _concurrent_request()
    outcome = ConcurrencyOutcome(
        request,
        PersistenceOutcome(PersistenceDisposition.FAILURE),
        ConcurrencyDisposition.INDETERMINATE,
        None,
        CommitCertainty.INDETERMINATE,
        RetryDisposition.RESOLVE_FIRST,
    )
    assert outcome.current_token is None
    assert outcome.retry_disposition is RetryDisposition.RESOLVE_FIRST


def test_inbox_receipt_crash_does_not_become_applied() -> None:
    received = _inbox(_admission())
    assert received.state is InboxState.PENDING
    assert received.state is not InboxState.APPLIED


def test_outbox_acknowledgement_loss_does_not_claim_exactly_once() -> None:
    pending = _pending_outbox(_admission())
    retry = transition_outbox(pending, OutboxState.RETRY)
    assert retry.next_record.state is OutboxState.RETRY
    assert retry.next_record.state is not OutboxState.DELIVERED


def test_indeterminate_recovery_fails_closed() -> None:
    request = _concurrent_request()
    with pytest.raises(ValueError):
        ConcurrencyOutcome(
            request,
            PersistenceOutcome(PersistenceDisposition.FAILURE),
            ConcurrencyDisposition.INDETERMINATE,
            ConcurrencyToken(4),
            CommitCertainty.INDETERMINATE,
            RetryDisposition.RESOLVE_FIRST,
        )


def test_replay_cannot_regress_or_reauthorize_terminal_evidence() -> None:
    admission = _admission()
    applied_record = transition_inbox(_inbox(admission), InboxState.APPLIED).next_record
    delivered_record = transition_outbox(
        _pending_outbox(admission), OutboxState.DELIVERED
    ).next_record
    assert transition_inbox(applied_record, InboxState.RETRY).next_record is applied_record
    assert transition_outbox(delivered_record, OutboxState.RETRY).next_record is delivered_record
    assert not any(hasattr(item, name) for item in (applied_record, delivered_record) for name in ("execute", "authorize", "trade"))


def test_composed_technical_outcomes_grant_no_execution_authority() -> None:
    values: tuple[object, ...] = (
        PersistenceOutcome(PersistenceDisposition.SUCCESS),
        adjudicate_idempotency(_admission().idempotency_record, None),
        transition_inbox(_inbox(_admission()), InboxState.APPLIED),
        transition_outbox(_pending_outbox(_admission()), OutboxState.DELIVERED),
    )
    forbidden = ("execute", "dispatch", "publish", "approve", "authorize", "trade", "place_order")
    assert all(not hasattr(value, name) for value in values for name in forbidden)


def test_manifest_protected_component_blobs_match_entry() -> None:
    manifest = _manifest()
    for component in manifest["protected_components"]:  # type: ignore[index]
        path = ROOT / component["path"]
        assert path.is_file()
        assert _git_blob(path) == component["blob"]


def test_manifest_classifies_versioning_evidence_by_change_impact() -> None:
    manifest = _manifest()
    rows = manifest["inherited_evidence"]  # type: ignore[index]
    assert any(row["slice"] == "S2.1" and row["assumption"] for row in rows)
    regenerated = manifest["regenerated_evidence"]  # type: ignore[index]
    assert any("version" in row["interaction"] for row in regenerated)


def test_manifest_inherits_transition_matrix_and_regenerates_composition() -> None:
    manifest = _manifest()
    assert any(row["slice"] == "S2.4" for row in manifest["inherited_evidence"])
    assert any("transition" in row["interaction"] for row in manifest["regenerated_evidence"])


def test_manifest_inherits_persistence_contracts_and_regenerates_composition() -> None:
    manifest = _manifest()
    assert any(row["slice"] == "S2.5" for row in manifest["inherited_evidence"])
    assert any("persistence" in row["interaction"] for row in manifest["regenerated_evidence"])


def test_manifest_inherits_s26_matrices_and_regenerates_cross_slice_evidence() -> None:
    manifest = _manifest()
    assert any(row["slice"] == "S2.6" for row in manifest["inherited_evidence"])
    assert len(manifest["regenerated_evidence"]) >= 4


def test_manifest_maps_all_adversarial_scenarios() -> None:
    scenarios = _manifest()["adversarial_scenarios"]
    assert [row["id"] for row in scenarios] == [f"ADV-S27-{index:02d}" for index in range(1, 20)]
    assert all(row["acceptance_ids"] and row["test_reference"] for row in scenarios)


def test_property_domains_are_finite_deterministic_and_identifiable() -> None:
    assert len(VERSION_CASES) * 4 == 16
    assert len(IDENTITY_PAIRS) == 91
    assert len(tuple(product(TransitionState, TransitionActionKind, TransitionState))) == 18
    assert len(CERTAINTY_RETRY_CASES) == 12


def test_invalidated_evidence_is_explicit_and_fail_closed() -> None:
    manifest = _manifest()
    assert manifest["invalidated_evidence"] == []
    assert not any(
        "INHERITED" in row["classifications"] and row.get("invalidated")
        for row in manifest["acceptance"]
    )


def test_s27_candidate_surface_is_evidence_only() -> None:
    manifest = _manifest()
    assert manifest["hard_non_scope"]
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(name.startswith(("sqlite3", "subprocess", "socket")) for name in imports)
    assert all("src/" not in reference for row in manifest["s27_owned_evidence"] for reference in row["test_references"])


EXPECTED_TOP_LEVEL_KEYS = [
    "schema_version", "record_id", "entry", "requirements",
    "protected_components", "evidence_classifications", "acceptance",
    "adversarial_scenarios", "inherited_evidence", "regenerated_evidence",
    "invalidated_evidence", "s27_owned_evidence", "gate_owned_evidence",
    "hard_non_scope", "authority_statement", "gate_id", "gate_status",
    "promotion_authorization", "required_gate_inputs",
]


def test_s27_evidence_schema_is_exact() -> None:
    manifest = _manifest()
    assert list(manifest) == EXPECTED_TOP_LEVEL_KEYS
    assert manifest["schema_version"] == 1
    assert manifest["record_id"] == "S2.7-EVIDENCE"
    assert manifest["entry"] == {
        "master": "3c3e42b02c82b917ff1c864ce21d71cec04a35de",
        "tree": "2387814b2b17cd540c8ad114916575a268f4b325",
    }
    assert EVIDENCE_PATH.read_bytes().endswith(b"\n")
    assert not EVIDENCE_PATH.read_bytes().startswith(b"\xef\xbb\xbf")


def test_s27_evidence_ids_and_references_are_complete() -> None:
    manifest = _manifest()
    requirements = [row["id"] for row in manifest["requirements"]]
    acceptance = [row["id"] for row in manifest["acceptance"]]
    assert requirements == ["ATIS-R-022", "ATIS-R-023"]
    assert acceptance == [
        *[f"AC-022-{index:02d}" for index in range(1, 11)],
        *[f"AC-023-{index:02d}" for index in range(1, 13)],
    ]
    assert len(acceptance) == len(set(acceptance))
    functions = {
        node.name
        for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    references = [
        reference
        for row in manifest["acceptance"]
        for reference in row["test_references"]
    ] + [row["test_reference"] for row in manifest["adversarial_scenarios"]]
    for reference in references:
        path, function = reference.split("::", 1)
        assert path == "tests/test_stage2_integration.py"
        assert function.split("[", 1)[0] in functions


def test_s27_cannot_evaluate_or_promote_its_gate() -> None:
    manifest = _manifest()
    assert manifest["gate_id"] == "GATE-S02-01"
    assert manifest["gate_status"] == "NOT_EVALUATED"
    assert manifest["promotion_authorization"] == "NONE"
    assert set(manifest["required_gate_inputs"].values()) == {
        "REQUIRED_AFTER_S2.7_PROTECTION"
    }
    assert "Stage 2 closed" not in EVIDENCE_PATH.read_text(encoding="utf-8")

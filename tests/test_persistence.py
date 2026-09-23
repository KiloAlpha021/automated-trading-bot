import ast
from dataclasses import FrozenInstanceError, fields
from enum import StrEnum
import inspect
from typing import get_type_hints
from uuid import uuid4

import pytest

from automated_trading_bot.domain.persistence import (
    EventPersistencePort,
    EventPersistenceRequest,
    PersistenceDisposition,
    PersistenceOutcome,
    TransitionPersistencePort,
    TransitionPersistenceRequest,
)
from automated_trading_bot.domain.state_transitions import (
    GenericState,
    StateId,
    TransitionDisposition,
    TransitionResult,
    TransitionState,
    TRANSITION_CONTRACT_V1,
)
from automated_trading_bot.domain.versioning import ContractVersion


def transition_result(disposition: TransitionDisposition) -> TransitionResult:
    state = GenericState(
        state_id=StateId(uuid4()),
        status=TransitionState.ACTIVE,
        contract_version=TRANSITION_CONTRACT_V1,
    )
    return TransitionResult(
        previous_state=state,
        next_state=state,
        disposition=disposition,
    )


def test_event_request_preserves_exact_fields_and_objects() -> None:
    version = ContractVersion("event-envelope", 1)
    representation = b"canonical-event"
    request = EventPersistenceRequest(version, representation)

    assert [field.name for field in fields(request)] == [
        "contract_version",
        "representation",
    ]
    assert request.contract_version is version
    assert request.representation is representation


@pytest.mark.parametrize(
    "version",
    [ContractVersion("event-envelope", 1), ContractVersion("other", 99)],
)
def test_event_request_accepts_any_valid_contract_version(
    version: ContractVersion,
) -> None:
    assert EventPersistenceRequest(version, b"value").contract_version is version


@pytest.mark.parametrize(
    "representation",
    [b"", b"\x00", b"\x00\xffATIS\x00", bytes(range(256)), b"x" * 4096],
)
def test_event_request_preserves_arbitrary_exact_bytes(representation: bytes) -> None:
    request = EventPersistenceRequest(
        ContractVersion("event-envelope", 1), representation
    )
    assert request.representation is representation
    assert request.representation == representation


@pytest.mark.parametrize("invalid", ["v1", 1, True, None, ("event", 1), object()])
def test_event_request_rejects_wrong_contract_version_category(
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="contract_version must be a ContractVersion"):
        EventPersistenceRequest(invalid, b"value")  # type: ignore[arg-type]


class BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "invalid",
    ["value", bytearray(b"value"), memoryview(b"value"), 1, True, None, BytesSubclass(b"value")],
)
def test_event_request_rejects_non_exact_bytes(invalid: object) -> None:
    with pytest.raises(TypeError, match="representation must be bytes"):
        EventPersistenceRequest(ContractVersion("event-envelope", 1), invalid)  # type: ignore[arg-type]


def test_event_request_rejects_hostile_proxy_without_invocation() -> None:
    called = False

    class Hostile:
        def __bytes__(self) -> bytes:
            nonlocal called
            called = True
            return b"value"

        def __call__(self) -> bytes:
            nonlocal called
            called = True
            return b"value"

    with pytest.raises(TypeError, match="representation must be bytes"):
        EventPersistenceRequest(ContractVersion("event-envelope", 1), Hostile())  # type: ignore[arg-type]
    assert called is False


def test_event_request_has_deterministic_value_semantics() -> None:
    version = ContractVersion("event-envelope", 1)
    first = EventPersistenceRequest(version, b"value")
    second = EventPersistenceRequest(version, b"value")
    different = EventPersistenceRequest(version, b"other")
    assert first == second
    assert hash(first) == hash(second)
    assert first != different


def test_event_request_is_frozen_and_slotted() -> None:
    request = EventPersistenceRequest(ContractVersion("event-envelope", 1), b"x")
    with pytest.raises(FrozenInstanceError):
        request.representation = b"y"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        request.extra = object()  # type: ignore[attr-defined]
    assert not hasattr(request, "__dict__")


@pytest.mark.parametrize("disposition", list(TransitionDisposition))
def test_transition_request_accepts_every_protected_disposition(
    disposition: TransitionDisposition,
) -> None:
    result = transition_result(disposition)
    request = TransitionPersistenceRequest(result)
    assert [field.name for field in fields(request)] == ["result"]
    assert request.result is result
    assert request.result.disposition is disposition


@pytest.mark.parametrize(
    "invalid",
    [None, object(), {}, (), "APPLIED"],
)
def test_transition_request_rejects_wrong_result_category(invalid: object) -> None:
    with pytest.raises(TypeError, match="result must be a TransitionResult"):
        TransitionPersistenceRequest(invalid)  # type: ignore[arg-type]


def test_transition_request_rejects_hostile_proxy_without_invocation() -> None:
    called = False

    class Hostile:
        def __call__(self) -> TransitionResult:
            nonlocal called
            called = True
            return transition_result(TransitionDisposition.APPLIED)

    with pytest.raises(TypeError, match="result must be a TransitionResult"):
        TransitionPersistenceRequest(Hostile())  # type: ignore[arg-type]
    assert called is False


def test_transition_request_has_deterministic_value_semantics() -> None:
    result = transition_result(TransitionDisposition.UNKNOWN)
    first = TransitionPersistenceRequest(result)
    second = TransitionPersistenceRequest(result)
    different = TransitionPersistenceRequest(
        transition_result(TransitionDisposition.UNKNOWN)
    )
    assert first == second
    assert hash(first) == hash(second)
    assert first != different


def test_transition_request_is_frozen_and_slotted() -> None:
    request = TransitionPersistenceRequest(
        transition_result(TransitionDisposition.ILLEGAL)
    )
    with pytest.raises(FrozenInstanceError):
        request.result = transition_result(TransitionDisposition.APPLIED)  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        request.extra = object()  # type: ignore[attr-defined]
    assert not hasattr(request, "__dict__")


def test_persistence_disposition_is_exact() -> None:
    assert list(PersistenceDisposition) == [
        PersistenceDisposition.SUCCESS,
        PersistenceDisposition.FAILURE,
        PersistenceDisposition.CONFLICT,
    ]
    assert [item.name for item in PersistenceDisposition] == [
        "SUCCESS",
        "FAILURE",
        "CONFLICT",
    ]
    assert [item.value for item in PersistenceDisposition] == [
        "SUCCESS",
        "FAILURE",
        "CONFLICT",
    ]
    assert len(PersistenceDisposition.__members__) == 3


@pytest.mark.parametrize("disposition", list(PersistenceDisposition))
def test_persistence_outcome_accepts_each_typed_disposition(
    disposition: PersistenceDisposition,
) -> None:
    outcome = PersistenceOutcome(disposition)
    assert [field.name for field in fields(outcome)] == ["disposition"]
    assert outcome.disposition is disposition


class OtherDisposition(StrEnum):
    SUCCESS = "SUCCESS"


@pytest.mark.parametrize(
    "invalid",
    ["SUCCESS", 1, True, None, OtherDisposition.SUCCESS, object()],
)
def test_persistence_outcome_rejects_wrong_disposition_category(
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="disposition must be a PersistenceDisposition"):
        PersistenceOutcome(invalid)  # type: ignore[arg-type]


def test_persistence_outcomes_are_distinct_hashable_values() -> None:
    outcomes = [PersistenceOutcome(item) for item in PersistenceDisposition]
    assert len(set(outcomes)) == 3
    assert PersistenceOutcome(PersistenceDisposition.SUCCESS) == PersistenceOutcome(
        PersistenceDisposition.SUCCESS
    )
    assert hash(PersistenceOutcome(PersistenceDisposition.FAILURE)) == hash(
        PersistenceOutcome(PersistenceDisposition.FAILURE)
    )


def test_persistence_outcome_is_frozen_slotted_and_payload_free() -> None:
    outcome = PersistenceOutcome(PersistenceDisposition.SUCCESS)
    with pytest.raises(FrozenInstanceError):
        outcome.disposition = PersistenceDisposition.FAILURE  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        outcome.payload = object()  # type: ignore[attr-defined]
    assert not hasattr(outcome, "__dict__")
    assert [field.name for field in fields(outcome)] == ["disposition"]


class EventPortFixture:
    def __init__(self, outcome: PersistenceOutcome) -> None:
        self.outcome = outcome
        self.received: EventPersistenceRequest | None = None

    def persist_event(
        self,
        request: EventPersistenceRequest,
    ) -> PersistenceOutcome:
        self.received = request
        return self.outcome


class TransitionPortFixture:
    def __init__(self, outcome: PersistenceOutcome) -> None:
        self.outcome = outcome
        self.received: TransitionPersistenceRequest | None = None

    def persist_transition(
        self,
        request: TransitionPersistenceRequest,
    ) -> PersistenceOutcome:
        self.received = request
        return self.outcome


def use_event_port(
    port: EventPersistencePort,
    request: EventPersistenceRequest,
) -> PersistenceOutcome:
    return port.persist_event(request)


def use_transition_port(
    port: TransitionPersistencePort,
    request: TransitionPersistenceRequest,
) -> PersistenceOutcome:
    return port.persist_transition(request)


def test_event_port_structural_fixture_preserves_request_and_outcome() -> None:
    request = EventPersistenceRequest(ContractVersion("event-envelope", 1), b"x")
    outcome = PersistenceOutcome(PersistenceDisposition.SUCCESS)
    fixture = EventPortFixture(outcome)
    assert use_event_port(fixture, request) is outcome
    assert fixture.received is request


def test_transition_port_structural_fixture_preserves_request_and_outcome() -> None:
    request = TransitionPersistenceRequest(
        transition_result(TransitionDisposition.TERMINAL)
    )
    outcome = PersistenceOutcome(PersistenceDisposition.CONFLICT)
    fixture = TransitionPortFixture(outcome)
    assert use_transition_port(fixture, request) is outcome
    assert fixture.received is request


@pytest.mark.parametrize(
    "protocol,fixture",
    [
        (
            EventPersistencePort,
            EventPortFixture(PersistenceOutcome(PersistenceDisposition.SUCCESS)),
        ),
        (
            TransitionPersistencePort,
            TransitionPortFixture(PersistenceOutcome(PersistenceDisposition.SUCCESS)),
        ),
    ],
)
def test_ports_are_not_runtime_checkable(protocol: type[object], fixture: object) -> None:
    with pytest.raises(TypeError, match="runtime_checkable"):
        isinstance(fixture, protocol)


def test_port_method_signatures_and_hints_are_exact() -> None:
    event_method = EventPersistencePort.__dict__["persist_event"]
    transition_method = TransitionPersistencePort.__dict__["persist_transition"]
    assert list(inspect.signature(event_method).parameters) == ["self", "request"]
    assert list(inspect.signature(transition_method).parameters) == ["self", "request"]
    assert get_type_hints(event_method) == {
        "request": EventPersistenceRequest,
        "return": PersistenceOutcome,
    }
    assert get_type_hints(transition_method) == {
        "request": TransitionPersistenceRequest,
        "return": PersistenceOutcome,
    }


def test_protocol_methods_have_ellipsis_only_bodies() -> None:
    import automated_trading_bot.domain.persistence as module

    tree = ast.parse(inspect.getsource(module))
    expected = {
        "EventPersistencePort": "persist_event",
        "TransitionPersistencePort": "persist_transition",
    }
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    for class_name, method_name in expected.items():
        methods = [
            node
            for node in classes[class_name].body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        ]
        assert len(methods) == 1
        assert len(methods[0].body) == 1
        expression = methods[0].body[0]
        assert isinstance(expression, ast.Expr)
        assert isinstance(expression.value, ast.Constant)
        assert expression.value.value is Ellipsis


def test_module_public_surface_and_contract_fields_are_exact() -> None:
    import automated_trading_bot.domain.persistence as module

    assert module.__all__ == (
        "EventPersistenceRequest",
        "TransitionPersistenceRequest",
        "PersistenceDisposition",
        "PersistenceOutcome",
        "EventPersistencePort",
        "TransitionPersistencePort",
    )
    assert {name for name in vars(module) if not name.startswith("_")} == set(
        module.__all__
    )
    assert [field.name for field in fields(EventPersistenceRequest)] == [
        "contract_version",
        "representation",
    ]
    assert [field.name for field in fields(TransitionPersistenceRequest)] == [
        "result"
    ]
    assert [field.name for field in fields(PersistenceOutcome)] == ["disposition"]


def test_no_deferred_or_unauthorized_public_contract_exists() -> None:
    import automated_trading_bot.domain.persistence as module

    forbidden = {
        "EventId",
        "AggregateId",
        "VersionId",
        "VersionedCommandEnvelope",
        "CommandId",
        "VersionRegistry",
        "VersionDefinition",
        "Codec",
        "Interpretation",
        "UnitOfWork",
        "Repository",
        "Adapter",
        "NotFound",
        "NOT_FOUND",
        "load",
        "read",
        "query",
        "retry",
        "AuthorityEpoch",
        "FenceToken",
    }
    assert not forbidden & set(vars(module))


def test_success_is_storage_information_without_authority() -> None:
    outcome = PersistenceOutcome(PersistenceDisposition.SUCCESS)
    assert outcome.disposition is PersistenceDisposition.SUCCESS
    assert outcome.disposition != TransitionDisposition.APPLIED
    forbidden = {
        "execute",
        "submit",
        "send",
        "dispatch",
        "publish",
        "approve",
        "authorize",
        "trade",
        "place",
        "retry",
        "load",
        "save",
    }
    for contract in (
        EventPersistenceRequest,
        TransitionPersistenceRequest,
        PersistenceOutcome,
        EventPersistencePort,
        TransitionPersistencePort,
    ):
        assert not forbidden & set(contract.__dict__)


def test_transition_dispositions_are_not_reinterpreted_as_persistence_outcomes() -> None:
    for disposition in TransitionDisposition:
        request = TransitionPersistenceRequest(transition_result(disposition))
        assert request.result.disposition is disposition
        assert request.result.disposition not in set(PersistenceDisposition)


def test_module_ast_has_exact_imports_and_no_infrastructure_behavior() -> None:
    import automated_trading_bot.domain.persistence as module

    tree = ast.parse(inspect.getsource(module))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imports == {
        "dataclasses",
        "enum",
        "typing",
        "automated_trading_bot.domain.state_transitions",
        "automated_trading_bot.domain.versioning",
    }
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    forbidden_calls = {"open", "eval", "exec", "__import__"}
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_calls
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, (ast.List, ast.Dict, ast.Set))
        for node in tree.body
    )

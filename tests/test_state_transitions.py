import ast
from dataclasses import FrozenInstanceError, fields
import inspect
from uuid import UUID, uuid1, uuid4

import pytest

from automated_trading_bot.domain.command_contracts import CommandId
from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyKey,
)
from automated_trading_bot.domain.identity_contracts import AggregateId
from automated_trading_bot.domain.state_transitions import (
    TRANSITION_CONTRACT_V1,
    GenericState,
    StateId,
    TransitionAction,
    TransitionActionId,
    TransitionActionKind,
    TransitionDisposition,
    TransitionResult,
    TransitionState,
    transition,
)
from automated_trading_bot.domain.versioning import (
    ContractVersion,
    UnknownContractError,
    UnsupportedVersionError,
)


def make_state(
    status: TransitionState = TransitionState.ACTIVE,
    version: ContractVersion = TRANSITION_CONTRACT_V1,
) -> GenericState:
    return GenericState(StateId(uuid4()), status, version)


def make_action(
    kind: TransitionActionKind = TransitionActionKind.APPLY,
    target: TransitionState = TransitionState.TERMINAL,
    version: ContractVersion = TRANSITION_CONTRACT_V1,
) -> TransitionAction:
    return TransitionAction(TransitionActionId(uuid4()), kind, target, version)


@pytest.mark.parametrize("identity_type", [StateId, TransitionActionId])
@pytest.mark.parametrize("value", [uuid1(), uuid4(), UUID(int=0)])
def test_identities_preserve_uuid_and_to_string(identity_type: type, value: UUID) -> None:
    identity = identity_type(value)
    assert identity.value is value
    assert identity.to_string() == str(value)


@pytest.mark.parametrize("identity_type", [StateId, TransitionActionId])
@pytest.mark.parametrize("invalid", [str(uuid4()), 1, True, b"uuid", None, object()])
def test_identities_reject_non_uuid(identity_type: type, invalid: object) -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        identity_type(invalid)


@pytest.mark.parametrize("identity_type", [StateId, TransitionActionId])
def test_identities_reject_uuid_subclass_and_proxy(identity_type: type) -> None:
    class SubUUID(UUID):
        pass

    called = False

    class Proxy:
        def __str__(self) -> str:
            nonlocal called
            called = True
            return str(uuid4())

    with pytest.raises(TypeError):
        identity_type(SubUUID(int=1))
    with pytest.raises(TypeError):
        identity_type(Proxy())
    assert called is False


@pytest.mark.parametrize("identity_type", [StateId, TransitionActionId])
def test_identities_have_typed_equality_hash_and_immutability(identity_type: type) -> None:
    raw = uuid4()
    first = identity_type(raw)
    second = identity_type(raw)
    assert first == second
    assert hash(first) == hash(second)
    assert first != identity_type(uuid4())
    assert first != raw
    assert {first: "value"}[second] == "value"
    with pytest.raises(FrozenInstanceError):
        first.value = uuid4()
    assert not hasattr(first, "__dict__")


@pytest.mark.parametrize("identity_type", [StateId, TransitionActionId])
def test_identities_have_no_ordering_parser_or_str_override(identity_type: type) -> None:
    first = identity_type(uuid4())
    second = identity_type(uuid4())
    with pytest.raises(TypeError):
        _ = first < second
    assert "__str__" not in identity_type.__dict__
    assert "from_string" not in identity_type.__dict__


def test_enums_have_exact_members_and_values() -> None:
    assert [(item.name, item.value) for item in TransitionState] == [
        ("ACTIVE", "ACTIVE"),
        ("TERMINAL", "TERMINAL"),
        ("UNKNOWN", "UNKNOWN"),
    ]
    assert [(item.name, item.value) for item in TransitionActionKind] == [
        ("APPLY", "APPLY"),
        ("DUPLICATE", "DUPLICATE"),
    ]
    assert [(item.name, item.value) for item in TransitionDisposition] == [
        ("APPLIED", "APPLIED"),
        ("ILLEGAL", "ILLEGAL"),
        ("DUPLICATE", "DUPLICATE"),
        ("TERMINAL", "TERMINAL"),
        ("UNKNOWN", "UNKNOWN"),
    ]


@pytest.mark.parametrize("status", list(TransitionState))
def test_generic_state_constructs_for_every_status(status: TransitionState) -> None:
    identity = StateId(uuid4())
    version = ContractVersion("other", 9)
    state = GenericState(identity, status, version)
    assert [field.name for field in fields(state)] == [
        "state_id",
        "status",
        "contract_version",
    ]
    assert state.state_id is identity
    assert state.status is status
    assert state.contract_version is version
    assert not hasattr(state, "__dict__")
    with pytest.raises(FrozenInstanceError):
        state.status = TransitionState.UNKNOWN


@pytest.mark.parametrize(
    "index,invalid,message",
    [
        (0, uuid4(), "state_id must be a StateId"),
        (0, AggregateId(uuid4()), "state_id must be a StateId"),
        (1, "ACTIVE", "status must be a TransitionState"),
        (2, "v1", "contract_version must be a ContractVersion"),
    ],
)
def test_generic_state_rejects_wrong_categories(
    index: int, invalid: object, message: str
) -> None:
    values = [StateId(uuid4()), TransitionState.ACTIVE, TRANSITION_CONTRACT_V1]
    values[index] = invalid
    with pytest.raises(TypeError, match=message):
        GenericState(*values)


@pytest.mark.parametrize("kind", list(TransitionActionKind))
@pytest.mark.parametrize("target", list(TransitionState))
def test_transition_action_constructs_for_every_kind_target(
    kind: TransitionActionKind, target: TransitionState
) -> None:
    identity = TransitionActionId(uuid4())
    version = ContractVersion("other", 7)
    action = TransitionAction(identity, kind, target, version)
    assert [field.name for field in fields(action)] == [
        "action_id",
        "kind",
        "target_state",
        "contract_version",
    ]
    assert action.action_id is identity
    assert action.kind is kind
    assert action.target_state is target
    assert action.contract_version is version
    assert not hasattr(action, "__dict__")
    with pytest.raises(FrozenInstanceError):
        action.kind = TransitionActionKind.APPLY


@pytest.mark.parametrize(
    "index,invalid,message",
    [
        (0, uuid4(), "action_id must be a TransitionActionId"),
        (0, StateId(uuid4()), "action_id must be a TransitionActionId"),
        (1, "APPLY", "kind must be a TransitionActionKind"),
        (2, "TERMINAL", "target_state must be a TransitionState"),
        (3, "v1", "contract_version must be a ContractVersion"),
    ],
)
def test_transition_action_rejects_wrong_categories(
    index: int, invalid: object, message: str
) -> None:
    values = [
        TransitionActionId(uuid4()),
        TransitionActionKind.APPLY,
        TransitionState.TERMINAL,
        TRANSITION_CONTRACT_V1,
    ]
    values[index] = invalid
    with pytest.raises(TypeError, match=message):
        TransitionAction(*values)


def test_transition_result_shape_values_and_immutability() -> None:
    previous = make_state()
    next_state = make_state(TransitionState.TERMINAL)
    result = TransitionResult(previous, next_state, TransitionDisposition.APPLIED)
    assert [field.name for field in fields(result)] == [
        "previous_state",
        "next_state",
        "disposition",
    ]
    assert result.previous_state is previous
    assert result.next_state is next_state
    assert result.disposition is TransitionDisposition.APPLIED
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.disposition = TransitionDisposition.ILLEGAL


@pytest.mark.parametrize(
    "index,invalid,message",
    [
        (0, object(), "previous_state must be a GenericState"),
        (1, object(), "next_state must be a GenericState"),
        (2, "APPLIED", "disposition must be a TransitionDisposition"),
    ],
)
def test_transition_result_rejects_wrong_categories(
    index: int, invalid: object, message: str
) -> None:
    state = make_state()
    values = [state, state, TransitionDisposition.ILLEGAL]
    values[index] = invalid
    with pytest.raises(TypeError, match=message):
        TransitionResult(*values)


def test_transition_contract_v1_has_exact_identity() -> None:
    assert TRANSITION_CONTRACT_V1 == ContractVersion("state-transition", 1)


@pytest.mark.parametrize("subject", ["state", "action"])
def test_transition_rejects_unknown_contract_family(subject: str) -> None:
    state = make_state()
    action = make_action()
    other = ContractVersion("other", 1)
    if subject == "state":
        state = make_state(version=other)
    else:
        action = make_action(version=other)
    with pytest.raises(UnknownContractError, match=f"unknown {subject} contract family"):
        transition(state, action)


@pytest.mark.parametrize("subject", ["state", "action"])
def test_transition_rejects_unsupported_version(subject: str) -> None:
    state = make_state()
    action = make_action()
    second = ContractVersion("state-transition", 2)
    if subject == "state":
        state = make_state(version=second)
    else:
        action = make_action(version=second)
    with pytest.raises(
        UnsupportedVersionError,
        match=f"unsupported {subject} state-transition version: 2",
    ):
        transition(state, action)


@pytest.mark.parametrize("state,action", [(object(), make_action()), (make_state(), object())])
def test_transition_rejects_wrong_argument_categories(
    state: object, action: object
) -> None:
    with pytest.raises(TypeError):
        transition(state, action)


MATRIX = [
    (TransitionState.ACTIVE, TransitionActionKind.APPLY, TransitionState.ACTIVE, TransitionDisposition.ILLEGAL, False),
    (TransitionState.ACTIVE, TransitionActionKind.APPLY, TransitionState.TERMINAL, TransitionDisposition.APPLIED, True),
    (TransitionState.ACTIVE, TransitionActionKind.APPLY, TransitionState.UNKNOWN, TransitionDisposition.ILLEGAL, False),
    (TransitionState.ACTIVE, TransitionActionKind.DUPLICATE, TransitionState.ACTIVE, TransitionDisposition.DUPLICATE, False),
    (TransitionState.ACTIVE, TransitionActionKind.DUPLICATE, TransitionState.TERMINAL, TransitionDisposition.DUPLICATE, False),
    (TransitionState.ACTIVE, TransitionActionKind.DUPLICATE, TransitionState.UNKNOWN, TransitionDisposition.DUPLICATE, False),
    (TransitionState.TERMINAL, TransitionActionKind.APPLY, TransitionState.ACTIVE, TransitionDisposition.TERMINAL, False),
    (TransitionState.TERMINAL, TransitionActionKind.APPLY, TransitionState.TERMINAL, TransitionDisposition.TERMINAL, False),
    (TransitionState.TERMINAL, TransitionActionKind.APPLY, TransitionState.UNKNOWN, TransitionDisposition.TERMINAL, False),
    (TransitionState.TERMINAL, TransitionActionKind.DUPLICATE, TransitionState.ACTIVE, TransitionDisposition.TERMINAL, False),
    (TransitionState.TERMINAL, TransitionActionKind.DUPLICATE, TransitionState.TERMINAL, TransitionDisposition.TERMINAL, False),
    (TransitionState.TERMINAL, TransitionActionKind.DUPLICATE, TransitionState.UNKNOWN, TransitionDisposition.TERMINAL, False),
    (TransitionState.UNKNOWN, TransitionActionKind.APPLY, TransitionState.ACTIVE, TransitionDisposition.UNKNOWN, False),
    (TransitionState.UNKNOWN, TransitionActionKind.APPLY, TransitionState.TERMINAL, TransitionDisposition.UNKNOWN, False),
    (TransitionState.UNKNOWN, TransitionActionKind.APPLY, TransitionState.UNKNOWN, TransitionDisposition.UNKNOWN, False),
    (TransitionState.UNKNOWN, TransitionActionKind.DUPLICATE, TransitionState.ACTIVE, TransitionDisposition.UNKNOWN, False),
    (TransitionState.UNKNOWN, TransitionActionKind.DUPLICATE, TransitionState.TERMINAL, TransitionDisposition.UNKNOWN, False),
    (TransitionState.UNKNOWN, TransitionActionKind.DUPLICATE, TransitionState.UNKNOWN, TransitionDisposition.UNKNOWN, False),
]


@pytest.mark.parametrize("status,kind,target,disposition,applied", MATRIX)
def test_complete_v1_transition_matrix(
    status: TransitionState,
    kind: TransitionActionKind,
    target: TransitionState,
    disposition: TransitionDisposition,
    applied: bool,
) -> None:
    state = make_state(status)
    action = make_action(kind, target)
    result = transition(state, action)
    assert result.previous_state is state
    assert result.disposition is disposition
    if applied:
        assert result.next_state is not state
        assert result.next_state.state_id is state.state_id
        assert result.next_state.status is TransitionState.TERMINAL
        assert result.next_state.contract_version is TRANSITION_CONTRACT_V1
    else:
        assert result.next_state is state


def test_transition_is_deterministic_and_does_not_mutate_inputs() -> None:
    state = make_state()
    action = make_action()
    state_hash = hash(state)
    action_hash = hash(action)
    first = transition(state, action)
    second = transition(state, action)
    equal_state = GenericState(state.state_id, state.status, state.contract_version)
    equal_action = TransitionAction(
        action.action_id, action.kind, action.target_state, action.contract_version
    )
    assert first == second == transition(equal_state, equal_action)
    assert hash(state) == state_hash
    assert hash(action) == action_hash


def test_shared_uuid_identity_categories_remain_distinct() -> None:
    raw = uuid4()
    identities = [
        StateId(raw),
        TransitionActionId(raw),
        AggregateId(raw),
        EventId(raw),
        CommandId(raw),
        CorrelationId(raw),
        CausationId(raw),
    ]
    assert all(
        left != right
        for index, left in enumerate(identities)
        for right in identities[index + 1 :]
    )
    assert StateId(raw) != ContractVersion("state-transition", 1)
    assert TransitionActionId(raw) != ContractVersion("state-transition", 1)
    assert StateId(raw) != IdempotencyKey(str(raw))
    assert TransitionActionId(raw) != IdempotencyKey(str(raw))


def test_hostile_proxy_rejects_without_invocation() -> None:
    called = False

    class Hostile:
        def __call__(self) -> object:
            nonlocal called
            called = True
            return object()

    with pytest.raises(TypeError):
        transition(Hostile(), make_action())
    with pytest.raises(TypeError):
        transition(make_state(), Hostile())
    assert called is False


def test_module_ast_has_no_ambient_infrastructure_or_s23_dependencies() -> None:
    module = inspect.getmodule(StateId)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    imports = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    } | {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden_roots = {
        "datetime",
        "time",
        "random",
        "secrets",
        "os",
        "threading",
        "multiprocessing",
        "socket",
        "subprocess",
        "pathlib",
        "sqlite3",
    }
    assert not {name.split(".")[0] for name in imports} & forbidden_roots
    assert not any("event_contracts" in name or "command_contracts" in name for name in imports)
    forbidden_calls = {"open", "eval", "exec", "__import__", "uuid1", "uuid4"}
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_calls
        for node in ast.walk(tree)
    )
    mutable_literals = (ast.List, ast.Dict, ast.Set)
    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, mutable_literals)
        for node in tree.body
    )


def test_public_api_and_authority_surface_are_exact() -> None:
    import automated_trading_bot.domain.state_transitions as module

    assert module.__all__ == (
        "StateId",
        "TransitionActionId",
        "TransitionState",
        "TransitionActionKind",
        "TransitionDisposition",
        "GenericState",
        "TransitionAction",
        "TransitionResult",
        "TRANSITION_CONTRACT_V1",
        "transition",
    )
    forbidden = {
        "execute",
        "submit",
        "send",
        "dispatch",
        "publish",
        "persist",
        "save",
        "approve",
        "authorize",
        "trade",
        "place",
    }
    for contract in (StateId, TransitionActionId, GenericState, TransitionAction, TransitionResult):
        assert not forbidden & set(contract.__dict__)
    assert [field.name for field in fields(TransitionResult)] == [
        "previous_state",
        "next_state",
        "disposition",
    ]


def test_no_registry_codec_upcast_or_v2_public_contract() -> None:
    import automated_trading_bot.domain.state_transitions as module

    assert not hasattr(module, "TRANSITION_CONTRACT_V2")
    assert not hasattr(module, "VersionRegistry")
    assert not hasattr(module, "VersionDefinition")
    assert not hasattr(module, "Codec")
    assert not hasattr(module, "UpcastDefinition")

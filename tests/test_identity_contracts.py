import ast
from dataclasses import FrozenInstanceError, fields
from decimal import ROUND_DOWN, ROUND_UP, getcontext
import inspect
from pathlib import Path
from uuid import UUID, uuid1, uuid4

import pytest

from automated_trading_bot.domain.identifiers import (
    CausationId,
    CorrelationId,
    EventId,
    OrderId,
    TradeId,
)
from automated_trading_bot.domain.identity_contracts import (
    AggregateId,
    RoundingPolicyId,
)
from automated_trading_bot.domain.versioning import ContractVersion


MODULE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "automated_trading_bot"
    / "domain"
    / "identity_contracts.py"
)


class UUIDSubclass(UUID):
    pass


class StringSubclass(str):
    pass


class UUIDProxy:
    def __init__(self, value: UUID) -> None:
        self.value = value


def _uuid7(value: int) -> UUID:
    raw = (value & ((1 << 76) - 1)) | (7 << 76) | (2 << 62)
    return UUID(int=raw)


def _public_methods(contract: type[object]) -> set[str]:
    return {
        name
        for name, value in contract.__dict__.items()
        if not name.startswith("_") and callable(value)
    }


def test_aggregate_id_accepts_uuid_and_preserves_exact_value() -> None:
    value = uuid4()
    identity = AggregateId(value)
    assert identity.value is value


@pytest.mark.parametrize("value", [uuid1(), uuid4(), _uuid7(1), UUID(int=0)])
def test_aggregate_id_accepts_multiple_uuid_versions(value: UUID) -> None:
    assert AggregateId(value).value is value


def test_aggregate_id_to_string_is_canonical_and_deterministic() -> None:
    value = UUID("A6A4A4A8-4872-4E68-985A-00306BF14A76")
    identity = AggregateId(value)
    assert identity.to_string() == "a6a4a4a8-4872-4e68-985a-00306bf14a76"
    assert identity.to_string() == str(value)


def test_aggregate_id_rejects_uuid_string() -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        AggregateId(str(uuid4()))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, True, 1, 1.0, b"uuid", object()])
def test_aggregate_id_rejects_non_uuid_values(value: object) -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        AggregateId(value)  # type: ignore[arg-type]


def test_aggregate_id_rejects_uuid_proxy() -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        AggregateId(UUIDProxy(uuid4()))  # type: ignore[arg-type]


def test_aggregate_id_rejects_uuid_subclass() -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        AggregateId(UUIDSubclass(int=1))


def test_aggregate_id_equality_and_hash_for_same_uuid() -> None:
    value = uuid4()
    left = AggregateId(value)
    right = AggregateId(value)
    assert left == right
    assert hash(left) == hash(right)


def test_aggregate_id_inequality_for_different_uuid() -> None:
    assert AggregateId(uuid4()) != AggregateId(uuid4())


def test_aggregate_id_is_distinct_from_existing_identifier_types() -> None:
    value = uuid4()
    aggregate = AggregateId(value)
    existing = (
        EventId(value),
        OrderId(value),
        TradeId(value),
        CorrelationId(value),
        CausationId(value),
    )
    assert all(aggregate != identity for identity in existing)


def test_aggregate_id_is_distinct_from_contract_version() -> None:
    aggregate = AggregateId(uuid4())
    assert aggregate != ContractVersion("aggregate", 1)
    with pytest.raises(TypeError, match="value must be a UUID"):
        AggregateId(ContractVersion("aggregate", 1))  # type: ignore[arg-type]


def test_aggregate_id_is_immutable() -> None:
    identity = AggregateId(uuid4())
    with pytest.raises(FrozenInstanceError):
        identity.value = uuid4()  # type: ignore[misc]


def test_aggregate_id_has_no_ordering_semantics() -> None:
    earlier = AggregateId(_uuid7(1))
    later = AggregateId(_uuid7(2))
    with pytest.raises(TypeError):
        _ = earlier < later  # type: ignore[operator]


def test_aggregate_id_exposes_only_value_and_to_string_contract() -> None:
    assert [field.name for field in fields(AggregateId)] == ["value"]
    assert _public_methods(AggregateId) == {"to_string"}
    assert "__str__" not in AggregateId.__dict__


def test_rounding_policy_id_accepts_minimum_token() -> None:
    assert RoundingPolicyId("A").value == "A"


@pytest.mark.parametrize(
    "token",
    ["policy", "POLICY", "vendor:policy-v1", "namespace.policy_2"],
)
def test_rounding_policy_id_accepts_representative_opaque_tokens(token: str) -> None:
    assert RoundingPolicyId(token).value == token


def test_rounding_policy_id_accepts_maximum_length_token() -> None:
    token = "A" + "a" * 63
    assert RoundingPolicyId(token).value == token


def test_rounding_policy_id_accepts_unknown_structurally_valid_token() -> None:
    token = "unknown.vendor:future-policy_987"
    assert RoundingPolicyId(token).to_string() == token


def test_rounding_policy_id_preserves_exact_token_and_case() -> None:
    upper = RoundingPolicyId("Vendor:Policy")
    lower = RoundingPolicyId("vendor:policy")
    assert upper.value == "Vendor:Policy"
    assert lower.value == "vendor:policy"
    assert upper != lower


def test_rounding_policy_id_to_string_is_exact_and_deterministic() -> None:
    identity = RoundingPolicyId("opaque.policy-v1")
    assert identity.to_string() == "opaque.policy-v1"
    assert identity.to_string() == identity.value


@pytest.mark.parametrize("value", [None, True, 1, 1.0, b"policy", object()])
def test_rounding_policy_id_rejects_non_string_values(value: object) -> None:
    with pytest.raises(TypeError, match="value must be a str"):
        RoundingPolicyId(value)  # type: ignore[arg-type]


def test_rounding_policy_id_rejects_string_subclass() -> None:
    with pytest.raises(TypeError, match="value must be a str"):
        RoundingPolicyId(StringSubclass("policy"))


def test_rounding_policy_id_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId("")


@pytest.mark.parametrize(
    "token",
    [" policy", "policy ", "pol icy", "policy\tname", "policy\nname", "policy\rname"],
)
def test_rounding_policy_id_rejects_whitespace(token: str) -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId(token)


@pytest.mark.parametrize("token", ["polícy", "pоlicy", "policy\u200b", "policy\u00a0name"])
def test_rounding_policy_id_rejects_non_ascii_and_homoglyphs(token: str) -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId(token)


@pytest.mark.parametrize("token", ["policy/name", "policy\\name", "policy@name", "policy+name", "policy=name", "policy,name", "policy#name"])
def test_rounding_policy_id_rejects_invalid_punctuation(token: str) -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId(token)


@pytest.mark.parametrize("token", [".policy", "_policy", ":policy", "-policy"])
def test_rounding_policy_id_rejects_leading_punctuation(token: str) -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId(token)


def test_rounding_policy_id_rejects_overlength_token() -> None:
    with pytest.raises(ValueError, match="canonical rounding-policy token"):
        RoundingPolicyId("A" + "a" * 64)


def test_rounding_policy_id_equality_and_hash() -> None:
    left = RoundingPolicyId("opaque-policy")
    right = RoundingPolicyId("opaque-policy")
    assert left == right
    assert hash(left) == hash(right)


def test_rounding_policy_id_inequality() -> None:
    assert RoundingPolicyId("policy-a") != RoundingPolicyId("policy-b")


def test_rounding_policy_id_is_immutable() -> None:
    identity = RoundingPolicyId("policy")
    with pytest.raises(FrozenInstanceError):
        identity.value = "other"  # type: ignore[misc]


def test_rounding_policy_id_is_distinct_from_aggregate_and_existing_ids() -> None:
    identity = RoundingPolicyId("policy")
    assert identity != AggregateId(uuid4())
    assert identity != EventId(uuid4())
    assert identity != "policy"


def test_rounding_policy_id_is_distinct_from_contract_version() -> None:
    identity = RoundingPolicyId("policy")
    assert identity != ContractVersion("policy", 1)
    with pytest.raises(TypeError, match="value must be a str"):
        RoundingPolicyId(ContractVersion("policy", 1))  # type: ignore[arg-type]


def test_rounding_policy_id_exposes_no_registry_or_resolution_api() -> None:
    names = {name.lower() for name in RoundingPolicyId.__dict__}
    assert not any("registry" in name or "resolve" in name for name in names)


def test_rounding_policy_id_exposes_no_rounding_or_default_api() -> None:
    assert _public_methods(RoundingPolicyId) == {"to_string"}
    names = {name.lower() for name in RoundingPolicyId.__dict__}
    assert not any("default" in name or "quantize" in name or name == "round" for name in names)


def test_rounding_policy_id_is_independent_of_decimal_context() -> None:
    context = getcontext()
    original_precision = context.prec
    original_rounding = context.rounding
    try:
        context.prec = 3
        context.rounding = ROUND_DOWN
        first = RoundingPolicyId("opaque-policy")
        context.prec = 50
        context.rounding = ROUND_UP
        second = RoundingPolicyId("opaque-policy")
    finally:
        context.prec = original_precision
        context.rounding = original_rounding
    assert first == second
    assert hash(first) == hash(second)
    assert first.to_string() == second.to_string()


def test_rounding_policy_id_exposes_only_value_and_to_string_contract() -> None:
    assert [field.name for field in fields(RoundingPolicyId)] == ["value"]
    assert _public_methods(RoundingPolicyId) == {"to_string"}
    assert "__str__" not in RoundingPolicyId.__dict__


def test_identity_contract_module_has_exact_public_contract_classes() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert classes == ["AggregateId", "RoundingPolicyId"]


def test_identity_contract_module_has_no_decimal_or_rounding_dependency() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "decimal" not in imports
    assert "quantize" not in source
    assert "ROUND_" not in source
    assert "getcontext" not in source


def test_identity_contract_module_has_no_execution_or_infrastructure_dependency() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = (
        "broker",
        "oms",
        "execution",
        "submission",
        "persistence",
        "adapter",
        "infrastructure",
    )
    assert not any(
        term in imported
        for imported in imports
        for term in forbidden
    )


def test_identity_contract_module_has_no_deferred_identity_or_ordering_api() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "VersionId",
        "AuthorityEpoch",
        "FenceToken",
        "BrokerOrderRef",
        "BrokerFillRef",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
    )
    assert not any(term in source for term in forbidden)


def test_identity_contract_module_does_not_define_registry_resolver_or_default() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    assert not any("registry" in name or "resolve" in name or "default" in name for name in names)


def test_contract_classes_are_declared_in_the_authorized_module() -> None:
    assert inspect.getmodule(AggregateId).__name__ == "automated_trading_bot.domain.identity_contracts"
    assert inspect.getmodule(RoundingPolicyId).__name__ == "automated_trading_bot.domain.identity_contracts"

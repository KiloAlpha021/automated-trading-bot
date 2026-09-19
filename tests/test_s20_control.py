"""Structural S2.0 controls only; no runtime or semantic authority is proved here."""

import copy
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "docs/stage2/s20-control.json"
SLICES = [f"S2.{number}" for number in range(8)]
FAMILIES = {
    "versioning_compatibility", "identifiers_values", "event_successor",
    "inert_command", "pure_transitions", "persistence_ports",
    "typed_outcomes", "inbox_outbox", "idempotency",
    "optimistic_concurrency",
}
ROOT_KEYS = {
    "schema_version", "control_id", "authorization", "purpose", "sources",
    "slices", "requirements", "contracts", "horizontal_constraints",
    "decisions", "version_policy", "scope", "dependencies", "evidence_policy",
    "change_control", "semantic_review", "handoff",
}


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_control() -> dict[str, object]:
    return json.loads(CONTROL.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)


def _object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"invalid {label} fields")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {label}")
    return value


def _list(value: object, label: str, *, nonempty: bool = True) -> list[object]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"invalid {label}")
    return value


def _unique(values: list[str], label: str) -> set[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")
    return set(values)


def validate_control(value: object) -> None:
    """Reject malformed links/ownership, never certify semantic correctness."""
    data = _object(value, ROOT_KEYS, "control")
    if data["schema_version"] != 1 or data["control_id"] != "ATIS-STAGE2-S20":
        raise ValueError("wrong S2.0 control identity")
    if data["authorization"] != "NONE":
        raise ValueError("control data cannot grant authorization")
    _text(data["purpose"], "purpose")

    sources = [_object(row, {"id", "authority", "reference"}, "source")
               for row in _list(data["sources"], "sources")]
    source_ids = _unique([_text(row["id"], "source id") for row in sources], "source id")
    decisions = {f"S2-D{i:02}" for i in range(1, 13)}
    if not decisions <= source_ids:
        raise ValueError("approved owner decision source missing")
    for row in sources:
        _text(row["authority"], "source authority")
        _text(row["reference"], "source reference")
        reference = row["reference"]
        if isinstance(reference, str) and reference.startswith("docs/"):
            path = (ROOT / reference).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError("missing or escaping repository source")

    slices = [_object(row, {"id", "objective"}, "slice")
              for row in _list(data["slices"], "slices")]
    if [row["id"] for row in slices] != SLICES:
        raise ValueError("invalid Stage-2 slice order")
    for row in slices:
        _text(row["objective"], "slice objective")
    order = {slice_id: index for index, slice_id in enumerate(SLICES)}

    rows = [_object(row, {"id", "sources", "owner", "statement", "property",
                          "family", "consumers", "depends_on", "evidence"}, "requirement")
            for row in _list(data["requirements"], "requirements")]
    ids = [_text(row["id"], "requirement id") for row in rows]
    requirement_ids = _unique(ids, "requirement id")
    referenced_sources = {item for row in rows for item in row["sources"]}
    if not decisions <= referenced_sources:
        raise ValueError("approved owner decision has no requirement")
    if any(re.fullmatch(r"ATIS-R-[0-9]{3}", item) is None for item in ids):
        raise ValueError("requirement ID must be stable and slice-independent")
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        owner = row["owner"]
        if owner not in order:
            raise ValueError("invalid owning slice")
        refs = [_text(item, "requirement source")
                for item in _list(row["sources"], "requirement sources")]
        _unique(refs, "requirement source")
        if not set(refs) <= source_ids:
            raise ValueError("unregistered authoritative source")
        _text(row["statement"], "requirement statement")
        _text(row["property"], "requirement property")
        _text(row["evidence"], "evidence obligation")
        family = row["family"]
        if family is not None and family not in FAMILIES:
            raise ValueError("invalid contract family")
        consumers = [_text(item, "consumer")
                     for item in _list(row["consumers"], "consumers", nonempty=False)]
        _unique(consumers, "consumer")
        if any(item not in order or order[item] <= order[owner] for item in consumers):
            raise ValueError("invalid transitive consumer")
        dependencies = [_text(item, "dependency")
                        for item in _list(row["depends_on"], "dependencies", nonempty=False)]
        _unique(dependencies, "requirement dependency")
        if any(item not in requirement_ids or item == row["id"] for item in dependencies):
            raise ValueError("unresolved requirement dependency")
        if any(order[by_id[item]["owner"]] > order[owner] for item in dependencies):
            raise ValueError("requirement depends on a later owning slice")

    visited: set[str] = set()
    active: set[str] = set()

    def visit(requirement_id: str) -> None:
        if requirement_id in active:
            raise ValueError("cyclic requirement dependency")
        if requirement_id in visited:
            return
        active.add(requirement_id)
        for dependency in by_id[requirement_id]["depends_on"]:
            visit(dependency)
        active.remove(requirement_id)
        visited.add(requirement_id)

    for requirement_id in ids:
        visit(requirement_id)

    contracts = [_object(row, {"id", "owner", "requirements"}, "contract")
                 for row in _list(data["contracts"], "contracts")]
    if _unique([_text(row["id"], "contract id") for row in contracts],
               "contract id") != FAMILIES:
        raise ValueError("authorized contract-family catalogue changed")
    for contract in contracts:
        if contract["owner"] not in order:
            raise ValueError("invalid contract owner")
        refs = [_text(item, "contract requirement")
                for item in _list(contract["requirements"], "contract requirements")]
        _unique(refs, "contract requirement")
        if any(item not in requirement_ids or by_id[item]["family"] != contract["id"]
               or by_id[item]["owner"] != contract["owner"] for item in refs):
            raise ValueError("orphan contract or ownership conflict")
    linked = {item for row in contracts for item in row["requirements"]}
    if linked != {row["id"] for row in rows if row["family"] is not None}:
        raise ValueError("unlinked contract requirement")

    for field, expected in (("horizontal_constraints", {f"H{i}" for i in range(1, 8)}),
                            ("decisions", {f"DP{i:02}" for i in range(1, 9)})):
        entries = [_object(row, {"id", "rule"}, field)
                   for row in _list(data[field], field)]
        if _unique([_text(row["id"], field) for row in entries], field) != expected:
            raise ValueError(f"missing or unexpected {field}")
        for row in entries:
            _text(row["rule"], f"{field} rule")

    policy = _object(data["version_policy"], {"applies_to", "supported_versions",
        "unsupported_versions", "codec_selection", "upcasting", "identity_separation"},
        "version policy")
    for key, item in policy.items():
        _text(item, f"version policy {key}")
    scope = _object(data["scope"], {"authorized", "excluded", "authority"}, "scope")
    for key in ("authorized", "excluded"):
        entries = [_text(item, key) for item in _list(scope[key], key)]
        _unique(entries, key)
    _text(scope["authority"], "scope authority boundary")

    edges = [_object(row, {"from", "to", "reason"}, "cross-slice dependency")
             for row in _list(data["dependencies"], "cross-slice dependencies")]
    pairs = [(row["from"], row["to"]) for row in edges]
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate cross-slice dependency")
    for row in edges:
        if row["from"] not in order or row["to"] not in order or \
                order[row["from"]] >= order[row["to"]]:
            raise ValueError("invalid cross-slice dependency")
        _text(row["reason"], "cross-slice reason")
    consumer_pairs = {
        (row["owner"], consumer)
        for row in rows if row["owner"] != "S2.0"
        for consumer in row["consumers"]
    }
    implied = {
        (by_id[dependency]["owner"], row["owner"])
        for row in rows for dependency in row["depends_on"]
        if by_id[dependency]["owner"] != row["owner"]
        and by_id[dependency]["owner"] != "S2.0"
    }
    if not implied <= set(pairs):
        raise ValueError("requirement-level cross-slice dependency missing from map")
    if any(
        row["owner"] not in by_id[dependency]["consumers"]
        for row in rows for dependency in row["depends_on"]
        if by_id[dependency]["owner"] != row["owner"]
        and by_id[dependency]["owner"] != "S2.0"
    ):
        raise ValueError("requirement-level consumer missing from producer metadata")
    if set(pairs) != consumer_pairs:
        raise ValueError("cross-slice map and producer consumers disagree")

    evidence = _object(data["evidence_policy"], {"primary_by_slice", "inheritance"},
                       "evidence policy")
    primary = evidence["primary_by_slice"]
    if not isinstance(primary, dict) or set(primary) != set(SLICES):
        raise ValueError("missing primary evidence owner")
    for item in primary.values():
        _text(item, "primary evidence obligation")
    _text(evidence["inheritance"], "evidence inheritance")
    change = _object(data["change_control"], {"clarification", "material_change",
                                                      "preserved_boundary"}, "change control")
    for item in change.values():
        _text(item, "change-control rule")
    review = [_object(row, {"criterion", "review"}, "semantic review")
              for row in _list(data["semantic_review"], "semantic review")]
    if _unique([_text(row["criterion"], "semantic criterion") for row in review],
               "semantic criterion") != {"AC-09", "AC-10", "AC-12"}:
        raise ValueError("semantic review boundary missing")
    for row in review:
        _text(row["review"], "semantic review obligation")
    handoff = _object(data["handoff"], {"to", "rely_on", "boundary"}, "handoff")
    if handoff["to"] != "S2.1":
        raise ValueError("wrong controlled handoff")
    relied_on = [_text(item, "handoff reference")
                 for item in _list(handoff["rely_on"], "handoff dependencies")]
    _unique(relied_on, "handoff reference")
    dependency_refs = [item for item in relied_on if item.startswith("dependencies:")]
    if len(dependency_refs) != 1:
        raise ValueError("handoff dependency reference missing or duplicate")
    handoff_consumers = dependency_refs[0].removeprefix("dependencies:").split(",")
    if not handoff_consumers or len(handoff_consumers) != len(set(handoff_consumers)):
        raise ValueError("duplicate or missing handoff consumer")
    expected_handoff = {target for source, target in consumer_pairs if source == handoff["to"]}
    if set(handoff_consumers) != expected_handoff:
        raise ValueError("handoff consumers disagree with requirements")
    _text(handoff["boundary"], "handoff authority boundary")


def test_current_s20_control_structure() -> None:
    validate_control(load_control())


@pytest.mark.parametrize("change", [
    lambda data: data["requirements"].append(copy.deepcopy(data["requirements"][0])),
    lambda data: data["requirements"][0].pop("id"),
    lambda data: data["requirements"][0].update(sources=[]),
    lambda data: data["requirements"][0].update(owner="S2.8"),
    lambda data: data["requirements"][0].update(evidence=""),
    lambda data: data["requirements"][0].update(depends_on=["ATIS-R-999"]),
    lambda data: data["contracts"][0].update(requirements=["ATIS-R-999"]),
    lambda data: data["contracts"][0].update(owner="S2.2"),
    lambda data: data["contracts"].append(copy.deepcopy(data["contracts"][0])),
    lambda data: data["dependencies"].append(copy.deepcopy(data["dependencies"][0])),
    lambda data: data["dependencies"].pop(),
    lambda data: data["horizontal_constraints"].pop(),
    lambda data: data.update(authorization="TRADING_APPROVED"),
    lambda data: data["requirements"][8].update(sources=["UNKNOWN-SOURCE"]),
    lambda data: data["requirements"][8].update(id="S2.1-R-009"),
    lambda data: data["requirements"][11]["sources"].remove("S2-D01"),
])
def test_s20_structural_mutations_fail_closed(change) -> None:
    data = copy.deepcopy(load_control())
    change(data)
    with pytest.raises(ValueError):
        validate_control(data)


def test_duplicate_json_key_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads('{"id":"one","id":"two"}', object_pairs_hook=_unique_pairs)


def test_registered_version_dependency_requires_s21_to_s24_map_edge() -> None:
    data = copy.deepcopy(load_control())
    data["dependencies"] = [
        edge for edge in data["dependencies"]
        if (edge["from"], edge["to"]) != ("S2.1", "S2.4")
    ]
    with pytest.raises(ValueError, match="requirement-level cross-slice dependency"):
        validate_control(data)


def test_registered_version_dependency_requires_s24_consumer() -> None:
    data = copy.deepcopy(load_control())
    producer = next(row for row in data["requirements"] if row["id"] == "ATIS-R-009")
    producer["consumers"].remove("S2.4")
    with pytest.raises(ValueError, match="requirement-level consumer missing"):
        validate_control(data)


@pytest.mark.parametrize("case", [
    "missing_handoff", "unsupported_handoff", "invalid_handoff_slice",
    "duplicate_handoff_consumer", "unsupported_map_edge", "reversed_map_edge",
    "unsupported_consumer", "stale_dependency", "stale_owner", "duplicate_map_edge",
])
def test_cross_slice_representations_reject_stale_mutations(case: str) -> None:
    data = copy.deepcopy(load_control())
    handoff = data["handoff"]["rely_on"]
    handoff_index = next(i for i, item in enumerate(handoff)
                         if item.startswith("dependencies:"))
    edge = next(item for item in data["dependencies"]
                if (item["from"], item["to"]) == ("S2.1", "S2.4"))
    producer = next(item for item in data["requirements"]
                    if item["id"] == "ATIS-R-009")
    consumer = next(item for item in data["requirements"]
                    if item["id"] == "ATIS-R-016")
    if case == "missing_handoff":
        handoff[handoff_index] = handoff[handoff_index].replace(",S2.7", "")
    elif case == "unsupported_handoff":
        handoff[handoff_index] += ",S2.2"
    elif case == "invalid_handoff_slice":
        handoff[handoff_index] += ",S2.8"
    elif case == "duplicate_handoff_consumer":
        handoff[handoff_index] += ",S2.4"
    elif case == "unsupported_map_edge":
        data["dependencies"].append({"from": "S2.1", "to": "S2.2", "reason": "stale"})
    elif case == "reversed_map_edge":
        edge["from"], edge["to"] = edge["to"], edge["from"]
    elif case == "unsupported_consumer":
        producer["consumers"].append("S2.2")
    elif case == "stale_dependency":
        consumer["depends_on"].append("ATIS-R-013")
    elif case == "stale_owner":
        consumer["owner"] = "S2.5"
    elif case == "duplicate_map_edge":
        data["dependencies"].append(copy.deepcopy(edge))
    with pytest.raises(ValueError):
        validate_control(data)


def test_same_slice_and_s20_baseline_dependencies_need_no_map_edge() -> None:
    data = copy.deepcopy(load_control())
    by_id = {row["id"]: row for row in data["requirements"]}
    assert by_id["ATIS-R-015"]["owner"] == by_id["ATIS-R-014"]["owner"]
    assert "ATIS-R-014" in by_id["ATIS-R-015"]["depends_on"]
    by_id["ATIS-R-010"]["depends_on"].append("ATIS-R-001")
    assert by_id["ATIS-R-001"]["owner"] == "S2.0"
    validate_control(data)


def test_s20_validator_does_not_claim_semantic_or_runtime_proof() -> None:
    data = load_control()
    assert data["authorization"] == "NONE"
    assert {entry["criterion"] for entry in data["semantic_review"]} == {
        "AC-09", "AC-10", "AC-12",
    }
    assert all("review" in entry for entry in data["semantic_review"])

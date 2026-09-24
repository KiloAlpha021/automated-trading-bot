from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import pytest

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "docs/stage3/stage3-specification.json"
S = ROOT / "docs/stage3/stage3-specification.schema.json"
RANGES = {
    "requirements": ("S3-REQ", 41, 3), "components": ("S3-CMP", 11, 3),
    "failures": ("S3-F", 66, 3), "compound_failures": ("S3-CF", 15, 3),
    "controls": ("S3-CTL", 25, 3), "direct_test_families": ("S3-DT", 41, 3),
    "adversarial_families": ("S3-AT", 16, 3),
    "adversarial_scenarios": ("S3-ADV", 107, 3),
    "observability": ("OBS", 20, 2), "security_integrity": ("SEC", 12, 2),
    "gate_dimensions": ("S3-GD", 25, 3), "gate_threats": ("S3-GT", 26, 3),
}
TURNS = [
    "01a0d38a-87f4-7da1-b6ec-a9d47206a8a3", "01a0d3ca-dd7a-7892-89ca-e429c39c1da9",
    "01a0d3f6-9c3b-7612-abbe-f97b4a39dbad", "01a0d3fe-1435-7061-b8da-212bb0b8618e",
    "01a0d406-f2d8-7943-87fd-c0ebcef3d235", "01a0d40c-a044-7d83-bbde-1754d745b860",
    "01a0d411-60c6-7ae1-a3e2-839bc3789431", "01a0d422-5787-79c0-adc4-07eaac8f9ca6",
    "01a0d428-5ff4-7da0-acc6-82325e81d981", "01a0d42c-8fad-74e2-a269-218ebd12b0f8",
    "01a0d435-9a63-73d1-b5d6-d32e366c5b72", "01a0d456-4ae9-70f0-8335-c2ff74da4228",
    "01a0d45c-7223-7e01-abb2-1fe915cf9e3d", "01a0d464-f4f7-70a3-9401-d0282281ee38",
    "01a0d468-e1dc-7401-beb5-39456ba4cedd",
]

class DuplicateKeyError(ValueError):
    pass

def hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def load(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook)

def corpus():
    return load(C)

def catalogues(data=None):
    return (data or corpus())["controlled_catalogues"]

def ids(prefix, count, width):
    return [f"{prefix}-{number:0{width}d}" for number in range(1, count + 1)]

def by_id(name):
    return {record["id"]: record for record in catalogues()[name]}

def validator():
    schema = load(S)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)

def reject_schema(mutator):
    damaged = deepcopy(corpus())
    mutator(damaged)
    with pytest.raises(ValidationError):
        validator().validate(damaged)

def validate_cross_records(data):
    manifest = data["source_manifest"]
    source_ids = [record["turn_id"] for record in manifest]
    if source_ids != TURNS or [r["sequence"] for r in manifest] != list(range(1, 16)):
        raise ValueError("source manifest identities, order, or sequence invalid")
    source_set = set(source_ids)
    for source in manifest:
        if sha256(source["content"].encode()).hexdigest() != source["content_sha256"]:
            raise ValueError("source hash mismatch")
    all_ids = []
    cats = catalogues(data)
    for name, (prefix, count, width) in RANGES.items():
        actual = [record["id"] for record in cats[name]]
        if actual != ids(prefix, count, width):
            raise ValueError(f"controlled range mismatch: {name}")
        all_ids.extend(actual)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("duplicate controlled identity")
    controlled = set(all_ids)
    for records in cats.values():
        for record in records:
            refs = record["source_turn_ids"]
            if not refs or not set(refs) <= source_set:
                raise ValueError("invalid source reference")
            if "final_source_turn_id" in record and record["final_source_turn_id"] not in refs:
                raise ValueError("final source is not an attributable source")
    for references in data["semantic_layer_source_turns"].values():
        if not set(references) <= source_set:
            raise ValueError("invalid semantic-layer source")
    if not set(data["unresolved_decisions"]["source_turn_ids"]) <= source_set:
        raise ValueError("invalid unresolved-decision source")
    if not set(data["gate_closure_contract"]["source_turn_ids"]) <= source_set:
        raise ValueError("invalid gate source")
    edges = {turn: set() for turn in source_ids}
    for rule in data["supersession_rules"]:
        earlier, later = rule["earlier_turn_id"], rule["later_turn_id"]
        if earlier not in source_set or later not in source_set or earlier == later:
            raise ValueError("invalid supersession endpoint")
        edges[earlier].add(later)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("supersession cycle")
        if node in visited:
            return
        visiting.add(node)
        for successor in edges[node]:
            visit(successor)
        visiting.remove(node)
        visited.add(node)
    for turn in source_ids:
        visit(turn)
    for number, scenario in enumerate(cats["adversarial_scenarios"], 1):
        expected = (f"S3-F-{number:03d}" if number <= 66 else
                    f"S3-CF-{number - 66:03d}" if number <= 81 else
                    f"S3-GT-{number - 81:03d}")
        if scenario["realizes_id"] != expected or scenario["realizes_id"] not in controlled:
            raise ValueError("invalid ADV realization")

def test_json_and_schema_parse_with_duplicate_key_rejection():
    assert corpus()["schema_version"] == 1
    assert load(S)["$schema"].endswith("2020-12/schema")
    with pytest.raises(DuplicateKeyError):
        json.loads('{"x":1,"x":2}', object_pairs_hook=hook)

def test_draft_2020_12_schema_is_valid_and_validates_corpus():
    validator().validate(corpus())

@pytest.mark.parametrize("path", [C, S])
def test_deterministic_utf8_lf_final_newline(path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert raw == (json.dumps(load(path), ensure_ascii=False, indent=2) + "\n").encode()

def test_exact_ranges_and_cross_record_integrity():
    validate_cross_records(corpus())

def test_source_integrity_and_history():
    data = corpus()
    assert len(data["source_manifest"]) == 15 and len(data["supersession_rules"]) == 8
    assert all(s["substantive_correctness"] == "NOT_SELF_CERTIFIED" for s in data["source_manifest"])

def test_classification_and_bounded_requirement_corrections():
    requirements = by_id("requirements")
    assert {i for i, r in requirements.items() if r["classification"] == "APPROVED_DERIVATION"} == {
        "S3-REQ-014", "S3-REQ-021", "S3-REQ-031", "S3-REQ-037"
    }
    correction = "01a0d3f6-9c3b-7612-abbe-f97b4a39dbad"
    assert {i for i, r in requirements.items() if r["final_source_turn_id"] == correction} == {
        "S3-REQ-013", "S3-REQ-025", "S3-REQ-029"
    }

def test_corrected_semantics():
    requirements = by_id("requirements")
    temporal = requirements["S3-REQ-013"]["exact_final_section"].lower()
    action = requirements["S3-REQ-025"]["exact_final_section"].lower()
    survival = requirements["S3-REQ-029"]["exact_final_section"]
    assert "acquisition" in temporal and "knowledge" in temporal and "must not" in temporal
    assert "unsupported" in action and "silently" in action
    assert "prevent survivorship leakage" in survival and "RISK-006" in survival

def test_final_component_failure_control_ranges():
    cats = catalogues()
    assert len(cats["components"]) == 11
    assert cats["failures"][-1]["id"] == "S3-F-066"
    assert cats["compound_failures"][-1]["id"] == "S3-CF-015"
    assert cats["controls"][-1]["id"] == "S3-CTL-025"

def test_dt_semantic_clauses():
    assert all(r["mandatory_semantic_clauses_exact"].strip() and r["mapping_provenance"] == "INFERRED"
               for r in catalogues()["direct_test_families"])

def test_adv_indexed_realizations():
    validate_cross_records(corpus())
    assert all(r["representation"] == "INDEXED_SCENARIO_REALIZATION" and
               r["execution_result"] == "NOT_ESTABLISHED"
               for r in catalogues()["adversarial_scenarios"])

def test_mapping_provenance_not_historical_wording():
    for records in catalogues().values():
        for record in records:
            if "mapping_provenance" in record:
                assert record["mapping_provenance"] in {"CONFIRMED_PROGRAMME_RECORD", "INFERRED"}

def test_unresolved_and_not_established_not_prepassed():
    data = corpus()
    assert data["unresolved_decisions"]["state"] == "UNRESOLVED"
    assert not data["unresolved_decisions"]["invented_resolution"]
    assert set(data["assurance_state"].values()) <= {"NOT_ESTABLISHED", "NOT_SELF_CERTIFIED"}

def test_gate_closure_protection_separate():
    contract = corpus()["gate_closure_contract"]
    assert not contract["gate_pass_is_merge"] and not contract["gate_pass_is_closure"]
    assert not contract["gate_pass_is_protection"]

def test_no_implementation_financial_trading_authority():
    authority = corpus()["authority"]
    assert not authority["stage3_implementation_authorized"]
    assert not authority["stage4_implementation_authorized"]
    assert authority["financial_or_trading_authority"] == [] and authority["provider_selected"] is None
    assert {"FINANCIAL_EFFECTS", "BROKER_EXECUTION", "LIVE_TRADING", "AI_TRADING_AUTHORITY"} <= set(authority["denied"])

def test_no_self_certification():
    data = corpus()
    assert data["corpus_identity"] == {
        "algorithm": "SHA256", "digest": None, "state": "NOT_ESTABLISHED"
    }
    assert data["assurance_state"]["independent_review"] == "NOT_ESTABLISHED"
    assert data["assurance_state"]["substantive_correctness"] == "NOT_SELF_CERTIFIED"

def test_no_machine_local_paths_outside_preserved_sources():
    data = corpus()
    data["source_manifest"] = []
    text = json.dumps(data)
    windows_user_path = r"[A-Za-z]:\\" + "Users" + r"\\"
    unix_user_path = "/" + "Users/"
    assert not re.search(windows_user_path + "|" + unix_user_path + r"|/home/", text)

SCHEMA_MUTATIONS = [
    ("unknown-nested", lambda d: d["baseline"].update({"unexpected": True})),
    ("missing-required", lambda d: d["baseline"].pop("tree")),
    ("malformed-id", lambda d: d["controlled_catalogues"]["requirements"][0].update({"id": "S3-REQ-01"})),
    ("invalid-state", lambda d: d["assurance_state"].update({"implementation": "CURRENT"})),
    ("invalid-authority", lambda d: d["authority"].update({"maximum_gate_effect": "LIVE_TRADING"})),
    ("bad-sha", lambda d: d["source_manifest"][0].update({"content_sha256": "bad"})),
    ("bad-source-ref", lambda d: d["source_manifest"][0].update({"turn_id": "bad"})),
    ("bad-adv", lambda d: d["controlled_catalogues"]["adversarial_scenarios"][0].pop("realizes_id")),
    ("bad-classification", lambda d: d["controlled_catalogues"]["requirements"][0].update({"classification": "INVENTED"})),
    ("bad-supersession", lambda d: d["supersession_rules"][0].pop("scope")),
    ("bad-decision", lambda d: d["unresolved_decisions"].pop("state")),
    ("unauthorized-gate", lambda d: d["gate_closure_contract"].update({"gate_pass_is_merge": True})),
    ("unexpected-object", lambda d: d["authority"].update({"unexpected": {"nested": True}})),
]

@pytest.mark.parametrize("_name,mutator", SCHEMA_MUTATIONS, ids=[x[0] for x in SCHEMA_MUTATIONS])
def test_schema_rejects_representative_corruption(_name, mutator):
    reject_schema(mutator)

CROSS_MUTATIONS = [
    ("duplicate-id", lambda d: d["controlled_catalogues"]["requirements"][1].update({"id": "S3-REQ-001"})),
    ("missing-source", lambda d: d["controlled_catalogues"]["requirements"][0].update({"source_turn_ids": ["01a0d38a-87f4-7da1-b6ec-000000000000"]})),
    ("hash-mismatch", lambda d: d["source_manifest"][0].update({"content": "tampered"})),
    ("missing-adv-target", lambda d: d["controlled_catalogues"]["adversarial_scenarios"][0].update({"realizes_id": "S3-F-999"})),
    ("off-by-one-adv", lambda d: d["controlled_catalogues"]["adversarial_scenarios"][1].update({"realizes_id": "S3-F-001"})),
    ("missing-supersession", lambda d: d["supersession_rules"][0].update({"earlier_turn_id": "01a0d38a-87f4-7da1-b6ec-000000000000"})),
    ("range-gap", lambda d: d["controlled_catalogues"]["requirements"].pop(1)),
    ("final-source", lambda d: d["controlled_catalogues"]["requirements"][0].update({"final_source_turn_id": TURNS[3]})),
    ("orphan", lambda d: d["controlled_catalogues"]["requirements"][0].update({"source_turn_ids": []})),
]

def add_cycle(data):
    first = data["supersession_rules"][0]
    data["supersession_rules"].append({
        "later_turn_id": first["earlier_turn_id"], "earlier_turn_id": first["later_turn_id"],
        "scope": first["scope"], "effect": "cycle test", "preserve_history": True,
    })

CROSS_MUTATIONS.append(("supersession-cycle", add_cycle))

@pytest.mark.parametrize("_name,mutator", CROSS_MUTATIONS, ids=[x[0] for x in CROSS_MUTATIONS])
def test_cross_record_validation_rejects_corruption(_name, mutator):
    damaged = deepcopy(corpus())
    mutator(damaged)
    with pytest.raises(ValueError):
        validate_cross_records(damaged)

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


RB1_SLICE1_IMPLEMENTATION_CONTRACT_V1 = {'contract_id': 'ATIS_RB1_SLICE1_IMPLEMENTATION_CONTRACT_V1', 'state': 'RATIFIED', 'parent_design': 'ATIS_RB1_IDENTITY_AND_REFERENCE_MODEL_V1', 'scope': 'ATIS-S3-RB1-S1_IMPLEMENTATION_CONTRACT_ONLY', 'canonical_identity': {'reference_version_id': 'DISTINCT_IMMUTABLE_ATIS_CONTROLLED_RFC_UUIDV4_NON_NIL_STRONGLY_TYPED', 'canonical_forms': {'instrument_id': 'atis:instrument:v1:<lowercase-hyphenated-uuidv4>', 'listing_id': 'atis:listing:v1:<lowercase-hyphenated-uuidv4>', 'reference_version_id': 'atis:reference-version:v1:<lowercase-hyphenated-uuidv4>'}, 'parsing': 'EXACT_CASE_SENSITIVE_NO_NORMALIZATION', 'reject': ['BARE_UUID', 'UPPERCASE_OR_NONCANONICAL', 'WHITESPACE', 'ALTERNATIVE_UUID_ENCODING', 'NIL_UUID', 'NON_V4_UUID', 'WRONG_IDENTITY_KIND', 'ARBITRARY_LEGACY_STRING']}, 'content_identity': {'algorithm': 'SHA-256', 'external_form': 'sha256:<64-lowercase-hex-digits>', 'canonicalization': {'encoding': 'UTF-8_WITHOUT_BOM', 'representation': 'CANONICAL_JSON', 'object_keys': 'FIXED_ASCII_LEXICOGRAPHIC_SORT', 'whitespace': 'NONE_INSIGNIFICANT', 'floats': 'PROHIBITED', 'semantic_strings': 'NFC_REQUIRED_NO_SILENT_NORMALIZATION', 'identities': 'EXACT_TYPED_CANONICAL_SERIALIZATION', 'enums': 'EXACT_UPPERCASE_TOKEN', 'timestamps': 'YYYY-MM-DDTHH:MM:SS.ffffffZ', 'absent_model_fields': 'EXPLICIT_NULL', 'unknown_fields': 'REJECTED'}, 'included_fields': 'ALL_SEMANTIC_REFERENCE_VERSION_FIELDS', 'included_field_classes': ['RECORD_KIND', 'SUBJECT_IDENTITIES', 'EFFECTIVE_AND_KNOWLEDGE_TIMES', 'FACTUAL_ATTRIBUTES', 'LISTING_STATE_AND_REASON', 'EVIDENCE_REFS', 'VALIDATION_POLICY_AND_STATE', 'SUPERSEDES_VERSION_ID', 'CORRECTION_REASON', 'DEFINED_CONDITIONAL_MODEL_FIELDS'], 'excluded_fields': ['REFERENCE_VERSION_ID', 'CONTENT_DIGEST', 'RUNTIME_CACHE_STORAGE_METADATA', 'PUBLICATION_OR_GIT_IDENTITIES', 'FIELDS_OUTSIDE_PROTECTED_MODEL'], 'digest_self_exclusion': True}, 'evidence_ordering': {'semantics': 'UNORDERED', 'canonical_order': 'SORT_DISTINCT_EVIDENCE_REFS_BY_CANONICAL_BYTES', 'same_key_same_digest': 'IDEMPOTENT_REPLAY_SINGLE_CANONICAL_MEMBER', 'same_key_different_digest': 'EVIDENCE_IDENTITY_CONFLICT', 'caller_order_has_authority': False}, 'validation_contract': {'states': ['VALID', 'INVALID', 'NOT_VALIDATED', 'INCOMPATIBLE', 'NOT_ESTABLISHED'], 'authoritative_state': 'VALID_ONLY', 'implicit_default': False, 'restrictive_states': ['INVALID', 'NOT_VALIDATED', 'INCOMPATIBLE', 'NOT_ESTABLISHED'], 'policy_or_state_change': 'NEW_IMMUTABLE_REFERENCE_VERSION'}, 'evidence_ref_contract': {'fields': ['SOURCE_ID', 'DATASET_ID', 'EVIDENCE_ID', 'EVIDENCE_CONTENT_DIGEST'], 'validation_policy_id': 'SEPARATE_REQUIRED_REFERENCE_VERSION_FIELD', 'canonical_forms': {'source_id': 'source:<authority>/<local-id>', 'dataset_id': 'dataset:<authority>/<local-id>', 'evidence_id': 'evidence:<authority>/<local-id>', 'validation_policy_id': 'validation-policy:<authority>/<local-id>'}, 'authority_component': 'LOWERCASE_ASCII_1_TO_63_ALPHANUMERIC_DOT_HYPHEN_NO_INVALID_BOUNDARY_OR_ADJACENT_SEPARATOR', 'local_id': 'CASE_SENSITIVE_ASCII_1_TO_191_BOUNDED_PERMITTED_SET_NO_WHITESPACE_CONTROL_UNICODE_OR_NORMALIZATION', 'identifier_maximum_ascii_characters': 255, 'evidence_key': ['SOURCE_ID', 'DATASET_ID', 'EVIDENCE_ID'], 'same_key_same_digest': 'IDEMPOTENT_REPLAY', 'same_key_different_digest': 'EVIDENCE_IDENTITY_CONFLICT', 'different_key_same_digest': 'PERMITTED_ATTRIBUTABLE_CORROBORATION', 'maximum_evidence_refs_per_version': 64}, 'lineage_contract': {'validation_boundary': 'ONE_COMPLETE_FINITE_CANDIDATE_LINEAGE_SET_FOR_ONE_CANONICAL_SUBJECT', 'instrument_subject': ['INSTRUMENT_ID'], 'listing_subject': ['INSTRUMENT_ID', 'LISTING_ID'], 'requirements': ['ALL_REFERENCED_PREDECESSORS_PRESENT', 'NO_SELF_EDGE', 'NO_CYCLE', 'NO_CROSS_SUBJECT_EDGE', 'NO_CROSS_KIND_EDGE', 'UNIQUE_REFERENCE_VERSION_ID', 'INPUT_ORDER_INDEPENDENT'], 'branching_successors': 'BRANCHING_SUCCESSOR_CONFLICT', 'missing_predecessor': 'MISSING_PREDECESSOR', 'cycle': 'LINEAGE_CYCLE', 'resource_bound_failure': 'LINEAGE_RESOURCE_LIMIT_EXCEEDED', 'successor_knowledge_order': 'STRICTLY_GREATER_THAN_PREDECESSOR', 'effective_time_correction_of_earlier_interval': True, 'maximum_lineage_set_versions': 4096, 'maximum_predecessor_depth_edges': 256, 'maximum_evidence_refs_per_version': 64, 'limits_are_versioned_contract_values': True}, 'instrument_id_migration': {'approach': 'IMMEDIATE_STRICT_ATOMIC_MIGRATION', 'existing_import_location_preserved': True, 'authoritative_instrument_id_type_count': 1, 'all_production_calls_tests_and_fixtures_migrate_atomically': True, 'arbitrary_string_compatibility': False, 'automatic_uuid_derivation_from_legacy_string': False, 'dual_format_fallback': False, 'second_instrument_id_implementation': False, 'fixtures': 'DETERMINISTIC_UUIDV4_REPLACE_TEST_OTHER_XAUUSD_STYLE_VALUES', 'future_persisted_legacy_values': 'SEPARATELY_AUTHORIZED_EXPLICIT_MIGRATION_MAPPING'}, 'authority': {'RB1_IMPLEMENTATION_AUTHORIZED': False, 'STAGE3_IMPLEMENTATION_AUTHORIZED': False, 'IMPLEMENTATION_AUTHORIZED': False, 'AI_TRADING_AUTHORITY': 'NONE'}}

# Stage-3 owner-ratified recording candidate checks.
RECORDING_EXPECTED_IDS = (
    [f"OBS-{i:02d}" for i in range(1, 21)]
    + [f"SEC-{i:02d}" for i in range(1, 13)]
    + [f"S3-GD-{i:03d}" for i in range(1, 26)]
    + [f"S3-GT-{i:03d}" for i in range(1, 27)]
)
RECORDING_SET_KEYS = {
    "requirement_ids", "failure_ids", "compound_failure_ids", "control_ids",
    "direct_test_ids", "adversarial_family_ids", "adversarial_scenario_ids",
    "constituent_components", "constituent_requirements",
    "required_adversarial_families", "verified_scenario_coverage",
}
RECORDING_DIGEST_EXCLUSIONS = {
    "candidate_digest", "independent_expected_digest", "independent_recomputed_digest",
}

def _recording_norm(value, key=None):
    import unicodedata
    if isinstance(value, float):
        raise ValueError("floating point is prohibited")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        normalized = [_recording_norm(item) for item in value]
        if key in RECORDING_SET_KEYS:
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in normalized]
            if len(encoded) != len(set(encoded)):
                raise ValueError("duplicate semantic-set member")
            normalized = [item for _, item in sorted(zip(encoded, normalized))]
        return normalized
    if isinstance(value, dict):
        return {
            key_name: _recording_norm(item, key_name)
            for key_name, item in sorted(value.items())
            if key_name not in RECORDING_DIGEST_EXCLUSIONS
        }
    return value

def _recording_digest(manifest):
    canonical = json.dumps(
        _recording_norm(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical).hexdigest()

def test_stage3_recording_manifest_inventory_and_layers():
    manifest = corpus()["stage3_recording_manifest"]
    assert [row["id"] for row in manifest["historical_catalogue"]] == RECORDING_EXPECTED_IDS
    assert [row["id"] for row in manifest["prospective_catalogue"]] == RECORDING_EXPECTED_IDS
    assert [row["id"] for row in manifest["rb1_traceability"]] == RECORDING_EXPECTED_IDS
    assert len({row["id"] for row in manifest["historical_catalogue"]}) == 83
    assert all(row["historical_rb1_applicability_state"] == "NOT_HISTORICALLY_ESTABLISHED" for row in manifest["historical_catalogue"])
    assert sum(row["rb1_applicability"] == "APPLIES_RB1" for row in manifest["prospective_catalogue"]) == 50
    assert sum(row["traceability_state"] == "FINAL_LITERAL_TRACEABILITY_ESTABLISHED" for row in manifest["rb1_traceability"]) == 50
    assert all(row["adversarial_scenario_ids"] == [] for row in manifest["rb1_traceability"])

def test_stage3_recording_manifest_repurposed_ids_and_history():
    manifest = corpus()["stage3_recording_manifest"]
    historical = {row["id"]: row for row in manifest["historical_catalogue"]}
    prospective = {row["id"]: row for row in manifest["prospective_catalogue"]}
    expected = {
        "OBS-16": "Manifest/lineage integrity failure",
        "SEC-10": "Freshness/invalidation policy integrity",
        "S3-GD-020": "External-dependency attribution",
        "S3-GT-022": "Gate PASS interpreted as automatic Stage-4 implementation authority",
    }
    for identifier, meaning in expected.items():
        assert historical[identifier]["historical_meaning"] == meaning
        assert historical[identifier]["prospective_relationship"] == "REPURPOSED_ID"
    assert prospective["SEC-10"]["normative_name"] == "Logical exact-version resolution integrity"
    assert historical["SEC-10"]["historical_component_requirement_metadata"] == "C10; 010,026,034-035,039,041"
    assert manifest["gate_threat_evolution"]["initial_records_unchanged"] is True

def test_stage3_recording_manifest_traceability_firewalls():
    manifest = corpus()["stage3_recording_manifest"]
    records = manifest["rb1_traceability"]
    excluded = [r for r in records if r["traceability_state"] == "DOES_NOT_APPLY_RB1"]
    assert len(excluded) == 33
    arrays = ("requirement_ids","failure_ids","compound_failure_ids","control_ids","direct_test_ids","adversarial_family_ids","adversarial_scenario_ids")
    assert all(all(r[k] == [] for k in arrays) for r in excluded)
    controls = {x for r in records for x in r["control_ids"]}
    assert controls == {"S3-CTL-001","S3-CTL-005","S3-CTL-006","S3-CTL-012","S3-CTL-014","S3-CTL-015","S3-CTL-017","S3-CTL-022"}
    assert all("S3-CF-002" not in r["compound_failure_ids"] and "S3-CF-005" not in r["compound_failure_ids"] for r in records)

def test_stage3_recording_manifest_cf002_adv_and_authority():
    manifest = corpus()["stage3_recording_manifest"]
    cf = manifest["compound_failure_obligation"]
    assert cf["compound_failure_id"] == "S3-CF-002"
    assert cf["global_stage3_cf002_state"] == "OPEN_WITH_TRIGGER"
    assert cf["activation_trigger"]["all_of"] == [
        "candidate_consumes_rb1_symbol_or_listing_history",
        "candidate_consumes_late_or_corrected_corporate_action_evidence",
    ]
    assert len(manifest["historical_scenario_bindings"]) == 26
    assert manifest["verified_scenario_coverage"] == []
    assert all(not b["coverage_credit"] for b in manifest["historical_scenario_bindings"])
    assert manifest["authority_ceiling"]["IMPLEMENTATION_AUTHORIZED"] is False
    assert manifest["authority_ceiling"]["PROTECTED_RECORDING_AUTHORIZED"] is False

def test_rb1_identity_and_reference_model_v1_is_ratified_without_implementation():
    data = corpus()
    manifest = data["stage3_recording_manifest"]
    decision = manifest["rb1_design_ratification"]
    assert decision["contract_id"] == "ATIS_RB1_IDENTITY_AND_REFERENCE_MODEL_V1"
    assert decision["state"] == "RATIFIED"
    assert decision["identity_contract"] == {
        "instrument_id": "OPAQUE_IMMUTABLE_ATIS_CONTROLLED_UUIDV4_STRONGLY_TYPED",
        "listing_id": "OPAQUE_IMMUTABLE_ATIS_CONTROLLED_UUIDV4_STRONGLY_TYPED",
        "namespace_separation": True,
        "external_attributes_are_identity": False,
        "prohibited_identity_derivations": [
            "TICKER_SYMBOL", "MIC", "EXCHANGE_NAME", "ISIN", "FIGI", "CUSIP",
            "SEDOL", "PROVIDER_ID", "BROKER_ALIAS", "ISSUER_NAME",
            "OTHER_MUTABLE_OR_EXTERNAL_ATTRIBUTE",
        ],
        "continuity_adjudication": "ATTRIBUTABLE_RESTRICTIVE_NO_SILENT_REUSE",
        "listing_instrument_binding": "PERMANENT_ONE_INSTRUMENT",
    }
    assert decision["temporal_contract"] == {
        "effective_interval": "HALF_OPEN_START_INCLUSIVE_END_EXCLUSIVE",
        "open_effective_end": "NULL",
        "timezone": "UTC",
        "knowledge_visibility": "KNOWLEDGE_FROM_LESS_THAN_OR_EQUAL_TO_CUTOFF",
        "exact_resolution": True,
        "current_or_latest_substitution": False,
    }
    assert decision["correction_lineage_contract"]["lineage_edge"] == "SUPERSEDES_VERSION_ID_ONLY"
    assert decision["correction_lineage_contract"]["overwrite_prior_evidence"] is False
    assert decision["provenance_contract"]["evidence_refs"] == "NONEMPTY_IMMUTABLE_COLLECTION"
    assert decision["factual_listing_states"] == [
        "ACTIVE", "HALTED", "SUSPENDED", "INACTIVE", "DELISTED",
    ]
    assert decision["resolution_dispositions"] == [
        "ESTABLISHED", "ABSENT", "AMBIGUOUS", "CONFLICTING", "NOT_ESTABLISHED",
    ]
    assert decision["restrictive_evidence_conditions"] == ["STALE", "INVALID"]
    assert decision["absent_semantics"] == "REQUIRES_AUTHORITATIVE_NEGATIVE_COVERAGE"
    assert decision["trading_authority_effect"] == "NONE"
    assert decision["implementation_authorized"] is False
    requirements = {r["id"]: r["exact_final_section"] for r in catalogues(data)["requirements"]}
    for requirement_id in ("S3-REQ-001", "S3-REQ-002", "S3-REQ-003"):
        assert "ATIS_RB1_IDENTITY_AND_REFERENCE_MODEL_V1" in requirements[requirement_id]
        assert "**Unresolved:**" not in requirements[requirement_id]


def test_rb1_slice1_implementation_contract_v1_is_ratified_without_implementation():
    manifest = corpus()["stage3_recording_manifest"]
    contract = manifest["rb1_slice1_implementation_contract"]
    assert contract == RB1_SLICE1_IMPLEMENTATION_CONTRACT_V1
    assert contract["parent_design"] == manifest["rb1_design_ratification"]["contract_id"]
    assert contract["validation_contract"]["authoritative_state"] == "VALID_ONLY"
    assert contract["lineage_contract"]["maximum_lineage_set_versions"] == 4096
    assert contract["lineage_contract"]["maximum_predecessor_depth_edges"] == 256
    assert contract["evidence_ref_contract"]["maximum_evidence_refs_per_version"] == 64
    assert contract["authority"] == {
        "RB1_IMPLEMENTATION_AUTHORIZED": False,
        "STAGE3_IMPLEMENTATION_AUTHORIZED": False,
        "IMPLEMENTATION_AUTHORIZED": False,
        "AI_TRADING_AUTHORITY": "NONE",
    }


def test_successor_evidence_recovery_policy_is_ratified_without_authority():
    policy = corpus()["stage3_recording_manifest"]["successor_evidence_recovery_policy"]
    assert policy["contract_id"] == "ATIS_STAGE3_SUCCESSOR_EVIDENCE_AND_RECOVERY_POLICY_V1"
    assert policy["state"] == "RATIFIED"
    assert policy["historical_evidence"] == {
        "immutability": "CREATION_TIME_CONTENT_MEMBERSHIP_CONCLUSIONS_LIMITATIONS_AND_GIT_IDENTITIES_PRESERVED",
        "later_source_content_rewrites_history": False,
        "protected_or_frozen_prohibits_authorized_successor": False,
        "verification_source": "ATTRIBUTABLE_HISTORICAL_GIT_COMMIT_AND_TREE",
        "current_worktree_substitution": "PROHIBITED",
        "missing_or_mismatched_historical_object": "FAIL_CLOSED",
    }
    assert policy["successor_evidence"]["record_types"] == [
        "CANDIDATE_SUCCESSOR", "PROTECTED_PUBLICATION",
    ]
    assert policy["successor_evidence"]["candidate_path_cardinality"] == "EVERY_CHANGED_PATH_EXACTLY_ONCE"
    assert policy["successor_evidence"]["candidate_future_publication_identities"] == "PROHIBITED"
    assert policy["recovery"] == {
        "packet_role": "RECOVERY_INDEX_ONLY",
        "complete_repository_recovery_object": "PROTECTED_GIT_COMMIT_AND_TREE",
        "ordinary_implementation_and_test_membership": "NOT_AUTOMATIC",
        "successor_register_classification": "SUPPLEMENTARY_SOURCE",
        "future_transition_membership": "UPDATE_SINGLE_REGISTER_NOT_PER_IMPLEMENTATION_FILE",
        "required_sources": 15,
        "supplementary_sources": 5,
        "historical_sources": 3,
    }
    assert policy["authority"]["successor_evidence_grants_authority"] is False
    assert policy["authority"]["RB1_IMPLEMENTATION_RESUMPTION"] == "NOT_AUTHORIZED"
    assert policy["authority"]["SLICE1_CANDIDATE_PUBLICATION"] == "NOT_AUTHORIZED"
    assert policy["authority"]["IMPLEMENTATION_AUTHORIZED"] is False
    assert policy["authority"]["AI_TRADING_AUTHORITY"] == "NONE"


def test_stage3_recording_manifest_canonicalization():
    manifest = corpus()["stage3_recording_manifest"]
    assert manifest["candidate_digest"] == _recording_digest(manifest)
    reordered = deepcopy(manifest)
    reordered["rb1_traceability"][0]["failure_ids"].reverse()
    assert _recording_digest(reordered) == _recording_digest(manifest)
    ordered = deepcopy(manifest)
    ordered["historical_catalogue"][0], ordered["historical_catalogue"][1] = ordered["historical_catalogue"][1], ordered["historical_catalogue"][0]
    assert _recording_digest(ordered) != _recording_digest(manifest)
    changed = deepcopy(manifest)
    changed["historical_catalogue"][0]["historical_meaning"] += " changed"
    assert _recording_digest(changed) != _recording_digest(manifest)

def test_stage3_recording_manifest_schema_is_closed():
    data = corpus()
    validator().validate(data)
    damaged = deepcopy(data)
    damaged["stage3_recording_manifest"]["unexpected"] = True
    with pytest.raises(ValidationError):
        validator().validate(damaged)



def test_calendar_session_authority_model_v1_is_ratified_without_implementation():
    manifest = corpus()["stage3_recording_manifest"]
    model = manifest["calendar_session_authority_model_v1"]
    assert model["contract_id"] == "ATIS_STAGE3_CALENDAR_SESSION_AUTHORITY_MODEL_V1"
    assert model["state"] == "RATIFIED"
    assert model["component_id"] == "S3-CMP-002"
    assert model["protected_rb_ordinal"] == "NOT_ESTABLISHED"
    assert model["owned_requirements"] == [
        "S3-REQ-005", "S3-REQ-006", "S3-REQ-007", "S3-REQ-008",
        "S3-REQ-009", "S3-REQ-010", "S3-REQ-041",
    ]
    assert model["currentness_separation"] == {
        "vocabulary": ["CURRENT", "STALE", "UNKNOWN"],
        "evaluated_assessment": True,
        "prohibited_semantic_fields": [
            "CalendarVersion", "HistoricalCoverage", "TradingDayDefinition",
            "SessionDefinition",
        ],
        "passage_of_time_mutates_fact_or_digest": False,
        "calendar_s2_behavior": True,
    }
    assert model["timezone_rule_ref"]["exact_corpus_digest_verification"] is True
    assert model["record_model"]["calendar_version"]["unbounded_day_or_session_aggregate"] is False
    assert model["coverage_contract"]["candidate_self_certification"] == "PROHIBITED"
    assert model["descriptive_scope_codes"]["canonical_pattern"] == r"[A-Z0-9][A-Z0-9._-]{0,31}"
    assert model["initial_session_scope"]["supported"] == ["REGULAR"]
    assert model["resource_bounds"] == {
        "MAX_EVIDENCE_REFS": 64,
        "MAX_LINEAGE_VERSIONS": 4096,
        "MAX_LINEAGE_DEPTH": 256,
        "exhaustion": "EXPLICIT_RESTRICTIVE_FAILURE",
        "silent_truncation": False,
    }
    assert model["authority"]["CALENDAR_S1_IMPLEMENTATION_AUTHORIZED"] is False
    assert model["authority"]["AI_TRADING_AUTHORITY"] == "NONE"


def test_calendar_s1_contract_is_ratified_with_exact_adversarial_matrix():
    manifest = corpus()["stage3_recording_manifest"]
    model = manifest["calendar_session_authority_model_v1"]
    contract = manifest["calendar_s1_implementation_contract_v1"]
    assert contract["contract_id"] == "ATIS_STAGE3_CALENDAR_S1_IMPLEMENTATION_CONTRACT_V1"
    assert contract["parent_design"] == model["contract_id"]
    assert contract["implementation_surface"] == [
        "src/automated_trading_bot/calendars/__init__.py",
        "src/automated_trading_bot/calendars/model.py",
        "tests/test_calendar_session_model.py",
    ]
    assert [item["id"] for item in contract["adversarial_tests"]] == [
        f"A{i:02d}" for i in range(1, 41)
    ]
    attacks = {item["id"]: item for item in contract["adversarial_tests"]}
    assert attacks["A35"]["case"] == "TIMEZONE_RULE_CORPUS_DIGEST_MISMATCH"
    assert attacks["A36"]["case"] == "TIMEZONE_RULE_LABEL_REUSED_WITH_DIFFERENT_CONTENT"
    assert attacks["A37"]["case"] == "COVERAGE_SELF_CERTIFICATION"
    assert attacks["A38"]["case"] == "CURRENTNESS_CHANGE_MUTATES_FACTUAL_VERSION"
    assert attacks["A39"]["case"] == "DUPLICATE_FACT_IDENTITY_DIFFERENT_CONTENT"
    assert attacks["A40"]["case"] == "CHILD_FACT_BOUND_TO_WRONG_CALENDAR_VERSION"
    assert contract["publication_preflight"]["mandatory_not_guidance"] is True
    assert contract["publication_preflight"]["future_sha_prediction"] == "PROHIBITED"
    assert contract["authority"]["CALENDAR_S1_IMPLEMENTATION_AUTHORIZED"] is False


def test_calendar_design_resolutions_do_not_claim_implementation():
    requirements = {
        item["id"]: item for item in catalogues(corpus())["requirements"]
    }
    for requirement_id in ("S3-REQ-005", "S3-REQ-006", "S3-REQ-007", "S3-REQ-008", "S3-REQ-010"):
        section = requirements[requirement_id]["exact_final_section"]
        assert "**Ratified design resolution:**" in section
        assert "Implementation evidence:** NOT_ESTABLISHED" in section
        assert "**Unresolved:**" not in section
    assert requirements["S3-REQ-009"]["implementation_state"] == "NOT_ESTABLISHED"
    assert requirements["S3-REQ-041"]["implementation_state"] == "NOT_ESTABLISHED"


def test_calendar_s2_design_and_contract_are_jointly_ratified_without_implementation():
    manifest = corpus()["stage3_recording_manifest"]
    design = manifest["calendar_s2_resolution_and_correction_v1"]
    contract = manifest["calendar_s2_implementation_contract_v1"]
    assert design["contract_id"] == "ATIS_STAGE3_CALENDAR_S2_DETERMINISTIC_HISTORICAL_SESSION_RESOLUTION_AND_CORRECTION_V1"
    assert contract["contract_id"] == "ATIS_STAGE3_CALENDAR_S2_IMPLEMENTATION_CONTRACT_V1"
    assert contract["parent_design"] == design["contract_id"]
    assert design["query_contract"]["fields"] == ["calendar_id", "local_date", "session_kind", "effective_as_of", "knowledge_cutoff", "evaluation_at"]
    assert design["candidate_completeness_contract"]["knowledge_rule"] == "knowledge_from <= query.knowledge_cutoff"
    assert design["currentness_contract"]["evaluation_time_equality_required"] is False
    assert design["negative_coverage_contract"]["regular_day_plus_no_supported_session"] == "CONFLICTING_POSITIVE_NEGATIVE_SESSION_CONFLICT"
    assert design["global_disposition_ranking"] is False
    assert len(design["phase_specific_resolution"]) == 11
    assert design["timezone_corpus_contract"]["fields"] == ["timezone_rule_id", "corpus"]
    assert design["result_contract"]["evidence"] == "MINIMAL_CANONICAL_SUPPORTING_OR_CONFLICTING_SET"
    assert design["authority"]["CALENDAR_S2_IMPLEMENTATION_AUTHORIZED"] is False
    assert contract["implementation_surface"] == ["src/automated_trading_bot/calendars/resolution.py", "src/automated_trading_bot/calendars/__init__.py", "tests/test_calendar_session_resolution.py"]
    assert contract["prohibited_semantic_modification"] == "src/automated_trading_bot/calendars/model.py"


def test_calendar_s2_contract_records_exact_s2_a01_to_s2_a46_matrix():
    contract = corpus()["stage3_recording_manifest"]["calendar_s2_implementation_contract_v1"]
    assert [item["id"] for item in contract["adversarial_tests"]] == [f"S2-A{i:02d}" for i in range(1, 47)]
    attacks = {item["id"]: item["case"] for item in contract["adversarial_tests"]}
    assert attacks["S2-A41"] == "EFFECTIVE_BOUNDARY_AMBIGUITY_OR_INCONSISTENCY"
    assert attacks["S2-A42"] == "FUTURE_CANDIDATE_SET_MANIFEST"
    assert attacks["S2-A43"] == "FUTURE_CANDIDATE_IDENTITY_CONTAMINATION"
    assert attacks["S2-A44"] == "CURRENTNESS_FUTURE_OR_OUTSIDE_VALIDITY"
    assert attacks["S2-A45"] == "CORRECTION_AUTHORITY_SELF_CERTIFICATION"
    assert attacks["S2-A46"] == "DIAGNOSTIC_OR_EVIDENCE_AUTHORITY_LEAKAGE"
    assert all(item["required_assertions"] == ["VISIBLE_RESTRICTIVE_RESULT_OR_REJECTION", "NO_FALLBACK", "NO_MUTATION", "NO_SIDE_EFFECT", "NO_AUTHORITY_PROMOTION"] for item in contract["adversarial_tests"])


def test_parallel_wave1_designs_and_contracts_record_exact_reviewed_refinements():
    record = corpus()["stage3_recording_manifest"]["parallel_wave1_abc_design_and_implementation_contracts_v1"]
    assert record["record_id"] == "ATIS_STAGE3_PARALLEL_WAVE1_ABC_DESIGN_AND_IMPLEMENTATION_CONTRACTS_V1"
    assert record["review"]["id"] == "ATIS_STAGE3_PARALLEL_WAVE1_ABC_INDEPENDENT_ADVERSARIAL_REVIEW_V1"
    assert record["review"]["cross_stream_convergence"] == "PASS"
    assert list(record["streams"]["A"]["refinements"]) == [f"A-R{i}" for i in range(1, 6)]
    assert list(record["streams"]["B"]["refinements"]) == [f"B-R{i}" for i in range(1, 7)]
    assert list(record["streams"]["C"]["refinements"]) == [f"C-R{i}" for i in range(1, 6)]
    assert list(record["synchronization_gates"]) == ["SYNC-1", "SYNC-2", "SYNC-3"]
    assert record["s3_req_017_allocation"]["final_satisfaction_claimed"] is False
    assert record["requirement_disposition"] == "TARGET_ONLY_NO_IMPLEMENTATION_SATISFACTION"
    assert record["authority"]["C03_TO_C07_IMPLEMENTATION_AUTHORIZED"] is False
    assert record["authority"]["C08_TO_C11_IMPLEMENTATION_AUTHORIZED"] is False
    assert record["authority"]["AI_TRADING_AUTHORITY"] == "NONE"


# B1/C1 reconciliation is ratified, never implementation authority.
B1_C1_KEY = "wave1_b1_c1_targeted_contract_refinement_v1"
B1_C1_EXPECTED_SHA256 = "a0e540e637b2c7677a330ded0ff83eae422f03cfd16f156620401eb62c7bb8e1"
WAVE1_PROTECTED_SHA256 = "b06d4f0c02e5c228f3df06d29482a17ceb66f913449f78e0ff241c36320e83d8"


def _contract_digest(value):
    return sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def test_b1_c1_ratified_exact_identity_and_protected_parent():
    manifest = corpus()["stage3_recording_manifest"]
    record = manifest[B1_C1_KEY]
    assert _contract_digest(record) == B1_C1_EXPECTED_SHA256
    assert _contract_digest(
        manifest["parallel_wave1_abc_design_and_implementation_contracts_v1"]
    ) == WAVE1_PROTECTED_SHA256
    assert record["review"]["final_disposition"] == {
        "B1": "RATIFICATION_READY", "C1": "RATIFICATION_READY",
    }
    assert record["state"] == "RATIFIED"
    assert record["authority"]["ratification_approved"] is True
    assert record["readiness"]["implementation_authorized_now"] is False
    assert record["authority"]["AI_TRADING_AUTHORITY"] == "NONE"


def test_b1_sequence_coverage_and_order_are_independent_required_results():
    result = corpus()["stage3_recording_manifest"][B1_C1_KEY]["b1"]["result"]
    assert result["dimensions"]["sequence_coverage"] == [
        "COMPLETE", "GAP", "NOT_ESTABLISHED", "NOT_APPLICABLE",
    ]
    assert result["dimensions"]["sequence_order"] == [
        "CONSISTENT", "REORDERED", "AMBIGUOUS_ORDER",
        "NOT_ESTABLISHED", "NOT_APPLICABLE",
    ]
    assert "sequence_coverage" in result["required_output_fields"]
    assert "sequence_order" in result["required_output_fields"]
    assert "sequence" not in result["dimensions"]


@pytest.mark.parametrize("path,value", [
    (("b1", "result", "dimensions", "presence"), ["PRESENT", "MISSING"]),
    (("b1", "identity_rules", "duplicate"), "SAME_LOGICAL_ID_IS_DUPLICATE"),
    (("b1", "synchronization", "post_sync_2"), []),
    (("b1", "result", "dimensions", "sequence_coverage"), ["COMPLETE"]),
    (("c1", "canonical_identity", "form"), "PROVIDER_NATIVE_EVENT_ID"),
    (("c1", "version_identity", "composition"), "ARRIVAL_ORDER"),
    (("c1", "temporal_roles", "family_required", "DIVIDEND"), ["ex_at"]),
    (("c1", "lineage", "authority_binding"), "SELF_CERTIFIED"),
    (("c1", "lineage", "rules"), []),
    (("c1", "affected_dependency_declaration", "boundary"), "EXECUTE_INVALIDATION"),
    (("authority", "implementation_authorized"), True),
    (("authority", "ratification_approved"), False),
])
def test_b1_c1_contract_rejects_semantic_or_authority_drift(path, value):
    record = deepcopy(corpus()["stage3_recording_manifest"][B1_C1_KEY])
    target = record
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    schema = load(S)["properties"]["stage3_recording_manifest"]["properties"][B1_C1_KEY]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(record)


C04_C05_COMPAT_KEY = "c04_c05_compatibility_contract_v1"


def test_c04_c05_compatibility_contract_is_ratification_candidate_only():
    record = corpus()["stage3_recording_manifest"][C04_C05_COMPAT_KEY]
    assert record["record_id"] == "ATIS_STAGE3_C04_C05_COMPATIBILITY_CONTRACT_V1"
    assert record["state"] == "RATIFICATION_CANDIDATE"
    assert record["authority"]["ratification_approved"] is False
    assert record["authority"]["implementation_authorized"] is False
    assert record["authority"]["sync_2_consumable"] is False
    assert record["authority"]["c05_post_authorized"] is False
    assert record["authority"]["c10_authorized"] is False
    assert record["authority"]["dataset_promotion_authorized"] is False
    assert record["sync_2_lifecycle"]["current_sync_2"] == "NOT_CONSUMABLE"
    assert record["sync_2_lifecycle"]["current_c05_post"] == "ASLEEP_NOT_REASSESSED"
    assert record["review"]["result"] == "PASS_WITH_BOUNDED_REFINEMENT"
    assert record["review"]["final_disposition"] == "RATIFICATION_READY"
    assert record["readiness"]["current"] == "RATIFICATION_READY"
    assert record["authority"]["AI_TRADING_AUTHORITY"] == "NONE"


def test_c04_c05_projection_excludes_context_and_preserves_complete_digest():
    record = corpus()["stage3_recording_manifest"][C04_C05_COMPAT_KEY]
    projection = record["semantic_projection"]
    assert projection["complete_observation_digest"] == (
        "PRESERVED_UNCHANGED_AND_NOT_A_SEMANTIC_DIGEST_FALLBACK"
    )
    assert {
        "ACQUISITION_IDENTITY", "SOURCE_PAYLOAD_AND_PROVENANCE_REFS",
        "PUBLICATION_AND_KNOWLEDGE_CONTEXT", "SOURCE_ORDER_AND_SEQUENCE_CONTEXT",
        "COMPLETE_C04_CONTENT_DIGEST",
    } <= set(projection["excluded"])
    assert record["semantic_digest"]["independent_of_acquisition_and_provenance"] is True
    assert record["material_field_policy"]["owner"] == "C04"
    assert "material_field_policy_ref" not in projection["included"]
    assert "material_field_policy_content_digest" in projection["included"]
    assert len(projection["required_vectors"]) == 3


def test_c04_c05_logical_identity_and_calendar_authority_are_not_invented():
    record = corpus()["stage3_recording_manifest"][C04_C05_COMPAT_KEY]
    assert record["logical_identity"]["source"].startswith("Caller-supplied")
    assert {"SEMANTIC_DIGEST", "ACQUISITION_ORDER", "QUALITY_RESULT"} <= set(
        record["logical_identity"]["prohibited_derivations"]
    )
    sequence = record["sequence_calendar_evidence"]
    assert sequence["owner"] == "CALENDAR_SESSION_AUTHORITY"
    assert sequence["states"] == [
        "AVAILABLE", "UNAVAILABLE", "NOT_APPLICABLE", "INCOMPATIBLE",
        "AMBIGUOUS_CONFLICTING",
    ]
    assert "CURRENT_CALENDAR" in sequence["fabrication_prohibited"]
    assert sequence["consumer_boundary"]["INCOMPATIBLE"].startswith("Do not promote")
    assert sequence["consumer_boundary"]["AMBIGUOUS_CONFLICTING"].startswith("Retain competing")


def test_c04_c05_descriptor_maps_exactly_to_existing_c05_input_contract():
    record = corpus()["stage3_recording_manifest"][C04_C05_COMPAT_KEY]
    mapping = record["descriptor"]["consumer_mapping"]
    assert mapping == {
        "representation_contract_ref": "adapter_contract_ref",
        "logical_identity": "logical_identity preserved exactly",
        "semantic_content": "{contract_ref: semantic_projection_contract_ref, canonical_bytes_ref, digest: semantic_digest}",
        "expected_sequence_ref": "expected_sequence_evidence.ref only when state=AVAILABLE",
    }
    assert record["restrictive_failures"]["SEMANTIC_DIGEST_MISMATCH"] == "REJECT"
    assert record["restrictive_failures"]["RESOURCE_BOUND_EXHAUSTED"] == "REJECT_NO_TRUNCATION"
    matrix = record["descriptor"]["compatibility_matrix"]
    assert matrix["admitted_v1"]["consumer_descriptor"] == (
        "ContractVersion('ATIS_C05_QUALITY_INPUT', 1)"
    )
    assert "No newest-version" in matrix["rule"]
    assert "canonicalized" in record["descriptor"]["evidence_canonicalization"]


@pytest.mark.parametrize("path,value", [
    (("semantic_projection", "complete_observation_digest"), "USE_AS_SEMANTIC_DIGEST"),
    (("semantic_digest", "independent_of_acquisition_and_provenance"), False),
    (("material_field_policy", "owner"), "C05"),
    (("logical_identity", "source"), "DERIVE_FROM_DIGEST"),
    (("sequence_calendar_evidence", "owner"), "C05"),
    (("sync_2_lifecycle", "current_sync_2"), "CONSUMABLE"),
    (("authority", "implementation_authorized"), True),
    (("authority", "sync_2_consumable"), True),
])
def test_c04_c05_schema_rejects_semantic_or_authority_drift(path, value):
    record = deepcopy(corpus()["stage3_recording_manifest"][C04_C05_COMPAT_KEY])
    target = record
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    schema = load(S)["properties"]["stage3_recording_manifest"]["properties"][C04_C05_COMPAT_KEY]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(record)

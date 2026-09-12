"""Contract conformance only; does not execute a secret provider."""
import copy
import json
from pathlib import Path

import pytest


def _contract():
    path = Path(__file__).resolve().parents[1] / "docs/m1-closure/M1-secret-provider-contract.md"
    return json.loads(path.read_text(encoding="utf-8").split("```json\n", 1)[1].split("\n```", 1)[0])


def _validate(contract):
    assert contract["status"] == "SPECIFICATION_ONLY"
    assert contract["source_sha256"] == "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
    assert contract["owner_decision"] == "M1-SCOPE-2026-09-11-01"
    assert contract["lookup"] == {
        "operation": "resolve", "parameters": {"reference": "SecretReference", "now": "Timestamp"},
        "returns": "SecretHandle", "errors": "SecretLookupError(reason_code)",
        "enumeration": False, "fallback": False,
        "environment_source": "immutable_provider_binding",
        "privilege_source": "independently_authorized_binding",
        "reference_fields": ["environment", "namespace", "name", "required_version"],
        "matching": "exact_case_sensitive", "name_pattern": "[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
    }
    assert contract["handle"] == {
        "metadata": ["reference", "version", "expires_at"], "value_access": "explicit_scoped_use",
        "repr": "REDACTED", "serialization": False, "implicit_string_conversion": False,
        "cache_after_use": False, "revocation_check": "before_each_use", "value_type": "nonempty_bytes",
    }
    assert contract["failure_outcomes"] == dict.fromkeys([
        "missing", "invalid_reference", "malformed_value", "access_denied", "environment_mismatch",
        "production_scope", "provider_unavailable", "timeout", "integrity_unknown",
        "version_mismatch", "expired", "revoked", "revocation_unknown",
    ], "DENY_NO_VALUE")
    assert contract["security"] == {
        "log_values": False, "exception_values": False, "exception_chaining": False,
        "persist_values": False, "configuration_literal_values": False,
        "configuration_grants_privilege": False, "retrieval_grants_financial_authority": False,
        "production_lookup_in_m1": False, "allowlist_required": True,
        "validate_before_backend_access": True, "unknown_denies": True,
    }
    assert contract["error_surface"] == {
        "fields": ["reason_code"], "message_source": "fixed_local_catalogue", "backend_messages": False,
    }
    assert contract["m1_test_material"] == "opaque_noncredential_sentinels_only"


def test_secret_provider_contract_controls():
    _validate(_contract())


@pytest.mark.parametrize("section,key", [
    ("lookup", "fallback"), ("lookup", "enumeration"),
    ("security", "log_values"), ("security", "exception_values"),
    ("security", "exception_chaining"), ("security", "persist_values"),
    ("security", "configuration_literal_values"), ("security", "configuration_grants_privilege"),
    ("security", "retrieval_grants_financial_authority"), ("security", "production_lookup_in_m1"),
    ("handle", "serialization"), ("handle", "cache_after_use"),
    ("error_surface", "backend_messages"),
])
def test_contract_rejects_insecure_capability(section, key):
    contract = copy.deepcopy(_contract())
    contract[section][key] = True
    with pytest.raises(AssertionError):
        _validate(contract)


@pytest.mark.parametrize("failure", ["missing", "malformed_value", "provider_unavailable", "environment_mismatch", "version_mismatch", "revocation_unknown"])
def test_contract_rejects_non_denying_failure(failure):
    contract = copy.deepcopy(_contract())
    contract["failure_outcomes"][failure] = "ALLOW"
    with pytest.raises(AssertionError):
        _validate(contract)


def test_contract_rejects_caller_selected_environment():
    contract = copy.deepcopy(_contract())
    contract["lookup"]["environment_source"] = "caller_configuration"
    with pytest.raises(AssertionError):
        _validate(contract)

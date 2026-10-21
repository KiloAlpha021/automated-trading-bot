from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from decimal import Decimal, DivisionByZero, localcontext
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from automated_trading_bot.research import golden_reference
from automated_trading_bot.research.golden_reference import (
    ASSURANCE_AUTHORITY,
    AggregateState,
    MANDATORY_CASES,
    AssuranceReport,
    GoldenFixture,
    GoldenReference,
    OracleApprovalEvidence,
    TRUSTED_OWNER_APPROVAL,
    Verdict,
    assure_bytes,
    assure_path,
    evaluate,
    load_reference,
    load_reference_bytes,
)


CONTRACT = (
    Path(__file__).parents[1]
    / "docs"
    / "stage4"
    / "s4-07-golden-reference-assurance-contract.json"
)


def _document() -> dict[str, object]:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _encode(document: dict[str, object]) -> bytes:
    body = dict(document)
    body.pop("reference_digest", None)
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    document["reference_digest"] = "sha256:" + sha256(canonical).hexdigest()
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def _fixture(document: dict[str, object], case_id: str) -> dict[str, object]:
    fixtures = document["fixtures"]
    assert type(fixtures) is list
    return next(item for item in fixtures if item["case_id"] == case_id)


def _expected(document: dict[str, object], case_id: str) -> dict[str, object]:
    expected = _fixture(document, case_id)["expected"]
    assert type(expected) is dict
    return expected


def _evidence(reference: GoldenReference) -> OracleApprovalEvidence:
    assert reference.reference_digest == TRUSTED_OWNER_APPROVAL.reference_digest
    assert reference.file_sha256 == TRUSTED_OWNER_APPROVAL.file_sha256
    return TRUSTED_OWNER_APPROVAL


def _constructed_reference(
    reference: GoldenReference, fixture: GoldenFixture
) -> GoldenReference:
    fixtures = tuple(
        fixture if item.case_id == fixture.case_id else item
        for item in reference.fixtures
    )
    return GoldenReference(
        reference.contract_version,
        reference.approval_reference,
        reference.approval_command_sha256,
        reference.reference_digest,
        reference.file_sha256,
        fixtures,
    )


def _assure_contract() -> AssuranceReport:
    reference = load_reference(CONTRACT)
    return evaluate(reference, _evidence(reference))


def _assure_document(document: dict[str, object]) -> AssuranceReport:
    raw = _encode(document)
    reference = load_reference_bytes(raw)
    return evaluate(reference, _evidence(reference))


def test_published_contract_is_canonical_approved_and_complete() -> None:
    reference = load_reference(CONTRACT)
    assert reference.contract_version == 1
    assert reference.approval_reference
    assert tuple(item.case_id for item in reference.fixtures) == MANDATORY_CASES


def test_complete_assurance_report_is_fail_closed_and_read_only() -> None:
    before = CONTRACT.read_bytes()
    report = _assure_contract()
    assert CONTRACT.read_bytes() == before
    assert report.authority == ASSURANCE_AUTHORITY
    assert report.supported_scope_assurance is AggregateState.PASS
    assert report.full_stage4_scenario_coverage is AggregateState.NOT_ESTABLISHED
    assert report.reference_integrity is AggregateState.PASS
    assert report.unsupported_count == 1
    assert report.not_executed_count == 0


@pytest.mark.parametrize("case_id", MANDATORY_CASES)
def test_every_mandatory_case_has_the_required_verdict(case_id: str) -> None:
    result = next(item for item in _assure_contract().cases if item.case_id == case_id)
    expected = Verdict.UNSUPPORTED_CAPABILITY if case_id == "GR-08" else Verdict.PASS
    assert result.verdict is expected
    assert not result.differences


def test_repeated_assurance_is_deterministic() -> None:
    reference = load_reference(CONTRACT)
    approval = _evidence(reference)
    assert evaluate(reference, approval) == evaluate(reference, approval)


def test_report_and_reference_are_immutable() -> None:
    report = _assure_contract()
    with pytest.raises(FrozenInstanceError):
        setattr(report, "unsupported_count", 0)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.replace(b'"contract_version":1', b'"contract_version":2'),
        lambda raw: raw.replace(b'"cash":"1050"', b'"cash":"1051"'),
    ],
)
def test_tampered_reference_is_invalid(mutation: Callable[[bytes], bytes]) -> None:
    raw = mutation(CONTRACT.read_bytes())
    report = assure_bytes(raw)
    assert report.reference_integrity is AggregateState.FAIL
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_duplicate_json_key_is_invalid() -> None:
    report = assure_bytes(b'{"contract_id":"x","contract_id":"y"}\n')
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_noncanonical_json_layout_is_invalid() -> None:
    document = _document()
    raw = json.dumps(document, indent=2, sort_keys=True).encode() + b"\n"
    assert assure_bytes(raw).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "value", ["-0", "-0.0", "0.0", "1.0", "1.00", "1e2", "NaN", "Infinity"]
)
def test_noncanonical_or_nonfinite_decimal_is_invalid(value: str) -> None:
    document = _document()
    _expected(document, "GR-01A")["cash"] = value
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_json_number_is_rejected_in_decimal_field() -> None:
    document = _document()
    _expected(document, "GR-01A")["cash"] = 1050
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize("case_id", ["GR-01A", "GR-04", "GR-05-EC3"])
def test_expected_value_tampering_invalidates_external_approval(case_id: str) -> None:
    document = _document()
    _expected(document, case_id)["cash"] = "1"
    report = assure_bytes(_encode(document), TRUSTED_OWNER_APPROVAL)
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_approved_reference_executed_output_mismatch_is_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_actual = getattr(golden_reference, "_actual")

    def mismatched_actual(fixture: object) -> dict[str, object]:
        actual = original_actual(fixture)
        if fixture.case_id == "GR-01A":
            actual = dict(actual)
            actual["cash"] = Decimal("1")
        return actual

    monkeypatch.setattr(golden_reference, "_actual", mismatched_actual)
    result = next(item for item in _assure_contract().cases if item.case_id == "GR-01A")
    assert result.verdict is Verdict.FAIL
    assert result.differences


def test_missing_fixture_is_invalid() -> None:
    document = _document()
    fixtures = document["fixtures"]
    assert type(fixtures) is list
    fixtures.pop()
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_duplicate_fixture_identity_is_invalid() -> None:
    document = _document()
    fixtures = document["fixtures"]
    assert type(fixtures) is list
    fixtures[-1]["case_id"] = fixtures[0]["case_id"]
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_unknown_fixture_identity_is_invalid() -> None:
    document = _document()
    fixtures = document["fixtures"]
    assert type(fixtures) is list
    fixtures[-1]["case_id"] = "GR-99"
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_unapproved_reference_is_invalid() -> None:
    document = _document()
    approval = document["independent_approval"]
    assert type(approval) is dict
    approval["status"] = "PENDING"
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_fixture_metadata_alone_does_not_authenticate_approval() -> None:
    report = assure_path(CONTRACT)
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_mismatched_external_approval_fails_closed() -> None:
    reference = load_reference(CONTRACT)
    approval = OracleApprovalEvidence(
        TRUSTED_OWNER_APPROVAL.source_url,
        TRUSTED_OWNER_APPROVAL.comment_id,
        TRUSTED_OWNER_APPROVAL.actor,
        TRUSTED_OWNER_APPROVAL.approved_at,
        TRUSTED_OWNER_APPROVAL.task_id,
        reference.approval_reference,
        "0" * 64,
        reference.reference_digest,
        reference.file_sha256,
    )
    assert (
        evaluate(reference, approval).supported_scope_assurance
        is AggregateState.NOT_ESTABLISHED
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "untrusted-actor"),
        (
            "source_url",
            "https://github.com/KiloAlpha021/automated-trading-bot/issues/100",
        ),
        ("file_sha256", "0" * 64),
    ],
)
def test_external_approval_identity_and_candidate_binding_fail_closed(
    field: str, value: str
) -> None:
    reference = load_reference(CONTRACT)
    values = {
        "source_url": TRUSTED_OWNER_APPROVAL.source_url,
        "comment_id": TRUSTED_OWNER_APPROVAL.comment_id,
        "actor": TRUSTED_OWNER_APPROVAL.actor,
        "approved_at": TRUSTED_OWNER_APPROVAL.approved_at,
        "task_id": TRUSTED_OWNER_APPROVAL.task_id,
        "approval_reference": TRUSTED_OWNER_APPROVAL.approval_reference,
        "command_sha256": TRUSTED_OWNER_APPROVAL.command_sha256,
        "reference_digest": TRUSTED_OWNER_APPROVAL.reference_digest,
        "file_sha256": TRUSTED_OWNER_APPROVAL.file_sha256,
    }
    values[field] = value
    approval = OracleApprovalEvidence(**values)
    report = evaluate(reference, approval)
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_fixture_input_tampering_invalidates_external_approval() -> None:
    document = _document()
    inputs = _fixture(document, "GR-01A")["inputs"]
    assert type(inputs) is dict
    transactions = inputs["transactions"]
    assert type(transactions) is list
    transactions[0]["price"] = "21"
    report = assure_bytes(_encode(document), TRUSTED_OWNER_APPROVAL)
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_decimal_context_mismatch_prevents_all_execution() -> None:
    reference = load_reference(CONTRACT)
    with localcontext() as context:
        context.prec = 27
        report = evaluate(reference, _evidence(reference))
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_decimal_trap_mismatch_prevents_all_execution() -> None:
    reference = load_reference(CONTRACT)
    with localcontext() as context:
        context.traps[DivisionByZero] = False
        report = evaluate(reference, _evidence(reference))
    assert report.supported_scope_assurance is AggregateState.NOT_ESTABLISHED
    assert all(item.verdict is Verdict.NOT_EXECUTED for item in report.cases)


def test_gr08_cannot_establish_full_coverage() -> None:
    report = _assure_contract()
    result = next(item for item in report.cases if item.case_id == "GR-08")
    assert result.verdict is Verdict.UNSUPPORTED_CAPABILITY
    assert report.full_stage4_scenario_coverage is AggregateState.NOT_ESTABLISHED


def test_execution_failures_are_explicit_not_executed_outcomes() -> None:
    results = {item.case_id: item for item in _assure_contract().cases}
    for case_id in (
        "GR-07-PRICE",
        "GR-07-SECURITY",
        "GR-07-LIQUIDITY",
        "GR-07-EVIDENCE",
    ):
        assert results[case_id].verdict is Verdict.PASS


def test_reason_comparison_preserves_ordered_sequence_semantics() -> None:
    compare = getattr(golden_reference, "_compare")
    assert compare({"reasons": ("A", "B")}, {"reasons": ["A", "B"]}) == ()
    assert compare({"reasons": ("A", "A")}, {"reasons": ["A", "A"]}) == ()
    assert compare({"reasons": ("A", "B")}, {"reasons": ["B", "A"]})
    assert compare({"reasons": ("A", "A")}, {"reasons": ["A"]})
    assert compare({"reasons": ("A",)}, {"reasons": ["A", "B"]})
    assert compare({"reasons": ("A",)}, {"reasons": [1]})


def test_public_report_has_no_trading_authority() -> None:
    report: AssuranceReport = _assure_contract()
    assert report.authority == "READ_ONLY_RESEARCH_ASSURANCE_NO_TRADING_AUTHORITY"


def test_wrong_input_type_is_rejected() -> None:
    loader = getattr(
        __import__(
            "automated_trading_bot.research.golden_reference",
            fromlist=["load_reference_bytes"],
        ),
        "load_reference_bytes",
    )
    with pytest.raises(TypeError):
        loader("not bytes")


def test_concrete_platform_path_is_accepted() -> None:
    assert load_reference(CONTRACT).contract_version == 1


def test_invalid_path_type_is_rejected() -> None:
    loader = getattr(
        __import__(
            "automated_trading_bot.research.golden_reference",
            fromlist=["load_reference"],
        ),
        "load_reference",
    )
    with pytest.raises(TypeError):
        loader("not a Path")


@pytest.mark.parametrize("case_id", ["GR-01A", "GR-01B", "GR-10-SAME-INSTANT"])
def test_authoritative_complex_case_ids_are_accepted(case_id: str) -> None:
    assert case_id in {item.case_id for item in load_reference(CONTRACT).fixtures}


@pytest.mark.parametrize("case_id", ["GR-1", "GR-01-A", "GR-11", "gr-01a"])
def test_unauthorised_case_ids_are_rejected(case_id: str) -> None:
    document = _document()
    _fixture(document, "GR-01A")["case_id"] = case_id
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_loaded_reference_is_deeply_immutable() -> None:
    fixture = load_reference(CONTRACT).fixtures[0]
    transactions = fixture.inputs["transactions"]
    assert type(transactions) is tuple
    with pytest.raises(TypeError):
        transactions[0]["price"] = "999"
    with pytest.raises(TypeError):
        fixture.expected["cash"] = "0"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-01-05T10:00:00Z",
        "2026-01-05T10:00:00.0Z",
        "2026-01-05T10:00:00.00000Z",
        "2026-01-05T10:00:00.000000+00:00",
        "2026-13-05T10:00:00.000000Z",
    ],
)
def test_noncanonical_timestamps_are_invalid(timestamp: str) -> None:
    document = _document()
    inputs = _fixture(document, "GR-10-FUTURE")["inputs"]
    assert type(inputs) is dict
    inputs["decision_time"] = timestamp
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "field,value", [("family", "GR-02"), ("operation", "DELISTING")]
)
def test_fixture_metadata_mismatch_is_invalid(field: str, value: str) -> None:
    document = _document()
    _fixture(document, "GR-01A")[field] = value
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_all_fixture_inputs_validate_before_execution() -> None:
    document = _document()
    inputs = _fixture(document, "GR-01B")["inputs"]
    assert type(inputs) is dict
    transactions = inputs["transactions"]
    assert type(transactions) is list
    transactions[-1].pop("price")
    report = assure_bytes(_encode(document), TRUSTED_OWNER_APPROVAL)
    assert report.reference_integrity is AggregateState.FAIL
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "case_id,field,value",
    [
        ("GR-06", "eligibility_state", "ELIGIBLE"),
        ("GR-06", "eligibility_reasons", []),
        ("GR-06", "eligibility_reasons", ["   "]),
        ("GR-09-REPLAY", "opening_cash", "-1"),
    ],
)
def test_operation_specific_invalid_inputs_fail_before_execution(
    case_id: str, field: str, value: object
) -> None:
    document = _document()
    inputs = _fixture(document, case_id)["inputs"]
    assert type(inputs) is dict
    inputs[field] = value
    report = assure_bytes(_encode(document), TRUSTED_OWNER_APPROVAL)
    assert report.reference_integrity is AggregateState.FAIL
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_direct_constructor_missing_fixture_is_invalid_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = load_reference(CONTRACT)
    malformed = GoldenReference(
        reference.contract_version,
        reference.approval_reference,
        reference.approval_command_sha256,
        reference.reference_digest,
        reference.file_sha256,
        reference.fixtures[:-1],
    )
    called = False

    def should_not_execute(fixture: GoldenFixture) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(golden_reference, "_actual", should_not_execute)
    report = evaluate(malformed, TRUSTED_OWNER_APPROVAL)
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE
    assert called is False


def test_direct_constructor_mutable_fixture_is_invalid() -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    mutable = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        dict(original.inputs),
        dict(original.expected),
    )
    malformed = GoldenReference(
        reference.contract_version,
        reference.approval_reference,
        reference.approval_command_sha256,
        reference.reference_digest,
        reference.file_sha256,
        (mutable, *reference.fixtures[1:]),
    )
    assert evaluate(malformed, TRUSTED_OWNER_APPROVAL).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_direct_constructor_substituted_fixture_identity_is_invalid_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    changed = dict(original.inputs["transactions"][0])
    changed["price"] = "21"
    transactions = (
        MappingProxyType(changed),
        *original.inputs["transactions"][1:],
    )
    inputs = dict(original.inputs)
    inputs["transactions"] = transactions
    substituted = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        MappingProxyType(inputs),
        original.expected,
    )
    called = False

    def should_not_execute(fixture: GoldenFixture) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(golden_reference, "_actual", should_not_execute)
    report = evaluate(
        _constructed_reference(reference, substituted), TRUSTED_OWNER_APPROVAL
    )
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE
    assert called is False


def test_direct_constructor_substituted_expected_value_is_invalid() -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    expected = dict(original.expected)
    expected["cash"] = "999"
    substituted = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        original.inputs,
        MappingProxyType(expected),
    )
    report = evaluate(
        _constructed_reference(reference, substituted), TRUSTED_OWNER_APPROVAL
    )
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_caller_retained_mapping_backing_mutation_is_invalid() -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    backing = dict(original.inputs)
    fixture = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        MappingProxyType(backing),
        original.expected,
    )
    constructed = _constructed_reference(reference, fixture)
    backing["opening_cash"] = "999"
    assert evaluate(constructed, TRUSTED_OWNER_APPROVAL).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_owned_snapshot_is_not_affected_by_later_backing_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    backing = dict(original.inputs)
    fixture = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        MappingProxyType(backing),
        original.expected,
    )
    constructed = _constructed_reference(reference, fixture)
    approval_verified = getattr(golden_reference, "_approval_verified")

    def mutate_after_snapshot(
        snapshot: GoldenReference, approval: OracleApprovalEvidence | None
    ) -> bool:
        backing["opening_cash"] = "999"
        return approval_verified(snapshot, approval)

    monkeypatch.setattr(golden_reference, "_approval_verified", mutate_after_snapshot)
    report = evaluate(constructed, TRUSTED_OWNER_APPROVAL)
    assert report.cases[0].verdict is Verdict.PASS


def test_nested_mutable_constructor_value_is_invalid() -> None:
    reference = load_reference(CONTRACT)
    original = reference.fixtures[0]
    inputs = dict(original.inputs)
    inputs["transactions"] = [dict(item) for item in original.inputs["transactions"]]
    fixture = GoldenFixture(
        original.case_id,
        original.family,
        original.operation,
        MappingProxyType(inputs),
        original.expected,
    )
    assert (
        evaluate(_constructed_reference(reference, fixture), TRUSTED_OWNER_APPROVAL)
        .cases[0]
        .verdict
        is Verdict.INVALID_REFERENCE
    )


def test_legitimate_immutable_constructor_is_accepted() -> None:
    reference = load_reference(CONTRACT)
    fixture = reference.fixtures[0]
    assert type(fixture.inputs) is MappingProxyType
    assert type(fixture.expected) is MappingProxyType
    assert evaluate(reference, TRUSTED_OWNER_APPROVAL).cases[0].verdict is Verdict.PASS


def test_assure_path_missing_file_is_invalid(tmp_path: Path) -> None:
    report = assure_path(tmp_path / "missing.json", TRUSTED_OWNER_APPROVAL)
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_assure_path_directory_is_invalid(tmp_path: Path) -> None:
    report = assure_path(tmp_path, TRUSTED_OWNER_APPROVAL)
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_assure_path_wrong_type_is_invalid() -> None:
    assurer = getattr(golden_reference, "assure_path")
    report = assurer("not-a-path", TRUSTED_OWNER_APPROVAL)
    assert report.cases[0].verdict is Verdict.INVALID_REFERENCE


def test_unexpected_execution_exception_is_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_execution(fixture: GoldenFixture) -> dict[str, object]:
        raise RuntimeError(f"unexpected execution failure: {fixture.case_id}")

    monkeypatch.setattr(golden_reference, "_actual", fail_execution)
    report = _assure_contract()
    assert all(item.verdict is Verdict.FAIL for item in report.cases)
    assert all(item.evidence == ("RuntimeError",) for item in report.cases)


def test_malformed_actual_output_is_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def malformed_actual(fixture: GoldenFixture) -> object:
        return None

    monkeypatch.setattr(golden_reference, "_actual", malformed_actual)
    report = _assure_contract()
    assert all(item.verdict is Verdict.FAIL for item in report.cases)


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    def interrupt(fixture: GoldenFixture) -> dict[str, object]:
        raise error(fixture.case_id)

    monkeypatch.setattr(golden_reference, "_actual", interrupt)
    with pytest.raises(error):
        _assure_contract()


@pytest.mark.parametrize("minute", [True, 0, 60, -1])
def test_release_minute_must_fit_scenario_clock(minute: object) -> None:
    document = _document()
    inputs = _fixture(document, "GR-01A")["inputs"]
    assert type(inputs) is dict
    transactions = inputs["transactions"]
    assert type(transactions) is list
    transactions[0]["release_minute"] = minute
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_duplicate_release_minutes_are_invalid() -> None:
    document = _document()
    transactions = _fixture(document, "GR-01A")["inputs"]["transactions"]
    assert type(transactions) is list
    transactions[1]["release_minute"] = transactions[0]["release_minute"]
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "case_id,mutation",
    [
        ("GR-01A", "instrument"),
        ("GR-02", "mark"),
        ("GR-03", "corporate_action"),
        ("GR-04", "rebalance"),
        ("GR-05-EC1", "cost_vector"),
        ("GR-05-EC1", "cost_metadata"),
    ],
)
def test_financial_case_structure_is_validated(
    case_id: str, mutation: str
) -> None:
    document = _document()
    inputs = _fixture(document, case_id)["inputs"]
    assert type(inputs) is dict
    transactions = inputs["transactions"]
    assert type(transactions) is list
    if mutation == "instrument":
        transactions[-1]["instrument"] = "B"
    elif mutation == "mark":
        inputs["mark"]["instrument"] = "B"
    elif mutation == "corporate_action":
        inputs["corporate_action"]["kind"] = "CASH_DIVIDEND"
    elif mutation == "rebalance":
        transactions[1]["instrument"] = "A"
    elif mutation == "cost_vector":
        inputs["cost_bps"] = ["1", "2"]
    else:
        inputs["cost_scenario"] = "EC2"
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize("unit_amount", ["0", "-0.5"])
def test_dividend_must_be_strictly_positive(unit_amount: str) -> None:
    document = _document()
    inputs = _fixture(document, "GR-02")["inputs"]
    assert type(inputs) is dict
    inputs["corporate_action"]["unit_amount"] = unit_amount
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "case_id,rates",
    [
        ("GR-05-EC1", ["1", "2", "3", "5", "4"]),
        ("GR-05-EC1", ["2", "4", "6", "8", "10"]),
        ("GR-05-EC2", ["1", "2", "3", "4", "5"]),
        ("GR-05-EC3", ["5", "10", "15", "25", "20"]),
    ],
)
def test_cost_scenario_requires_exact_authoritative_vector(
    case_id: str, rates: list[str]
) -> None:
    document = _document()
    inputs = _fixture(document, case_id)["inputs"]
    assert type(inputs) is dict
    inputs["cost_bps"] = rates
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "case_id,field,value",
    [
        ("GR-06", "quantity", "0"),
        ("GR-07-PRICE", "failure", "SECURITY"),
        ("GR-07-PRICE", "quantity", "0"),
        ("GR-07-PRICE", "price", "0"),
        ("GR-07-PRICE", "currency", "EUR"),
        ("GR-09-REPLAY", "mode", "CORRUPTED_SEQUENCE"),
        ("GR-09-REPLAY", "opening_cash", "invalid"),
    ],
)
def test_nonfinancial_case_semantics_are_validated(
    case_id: str, field: str, value: object
) -> None:
    document = _document()
    inputs = _fixture(document, case_id)["inputs"]
    assert type(inputs) is dict
    inputs[field] = value
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize(
    "case_id,field,value",
    [
        ("GR-10-FUTURE", "input_state", "MISSING"),
        ("GR-10-FUTURE", "knowledge_time", "2026-01-05T09:30:00.000000Z"),
        ("GR-10-UNAVAILABLE", "input_state", "VALID"),
        ("GR-10-UNAVAILABLE", "knowledge_time", "2026-01-05T10:30:00.000000Z"),
        ("GR-10-SAME-INSTANT", "input_state", "MISSING"),
        ("GR-10-SAME-INSTANT", "knowledge_time", "2026-01-05T10:00:01.000000Z"),
    ],
)
def test_gr10_semantic_chronology_is_validated_before_execution(
    case_id: str, field: str, value: str
) -> None:
    document = _document()
    inputs = _fixture(document, case_id)["inputs"]
    assert type(inputs) is dict
    inputs[field] = value
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_expected_schema_must_be_complete_and_exact(mutation: str) -> None:
    document = _document()
    expected = _expected(document, "GR-01A")
    if mutation == "missing":
        expected.pop("cash")
    else:
        expected["unknown"] = "0"
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE


def test_expected_value_types_are_validated() -> None:
    document = _document()
    _expected(document, "GR-01A")["attempt_count"] = True
    assert assure_bytes(_encode(document)).cases[0].verdict is Verdict.INVALID_REFERENCE

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RELATIVE_PATH = "docs/stage3/40of40-independent-review-recovery.json"
RECORD_PATH = ROOT / RELATIVE_PATH
SOURCE_SHA256 = "14183309a0d5f5879795d52f4712ebe0ceaea0ac5fe99058cc0cfd95b31c44b2"
SUBSTANTIVE_IDS = tuple(
    f"S3-REQ-{number:03d}" for number in range(1, 42) if number != 36
)

# Independent recovery pins: retrieved source metadata, untruncated command
# evidence and authorized semantics. The original response is not duplicated:
# its exact bytes are authenticated by the separately recovered digest.
# Keep the expectation immutable; each validation parses a fresh value.
EXPECTED_RECORD_JSON = r'''{
  "schema_version": 1,
  "record_id": "ATIS-STAGE3-40OF40-INDEPENDENT-REVIEW-RECOVERY",
  "record_kind": "PRESENT_DAY_PRESERVATION_OF_RECOVERED_HISTORICAL_SOURCE",
  "canonical_reference": "ATIS-STAGE3-40-OF-40-SUBSTANTIVE-REQUIREMENTS-RECONCILIATION:STAGE3_SUBSTANTIVE_REQUIREMENTS_40_OF_40_VERIFIED",
  "historical_review": {
    "review_id": "ATIS-STAGE3-40-OF-40-SUBSTANTIVE-REQUIREMENTS-RECONCILIATION",
    "decision_id": "STAGE3_SUBSTANTIVE_REQUIREMENTS_40_OF_40_VERIFIED",
    "repository": "KiloAlpha021/automated-trading-bot",
    "evaluated_master": "a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325",
    "evaluated_tree": "e0ada9e8434b2122b547a323b83e1306553e9042",
    "containing_turn": {
      "started_at": "2026-10-05T23:30:13Z",
      "completed_at": "2026-10-05T23:44:02Z",
      "timestamp_semantics": "SOURCE_TURN_METADATA_NOT_REPOSITORY_CREATION_PUBLICATION_OR_COMMIT_TIME"
    }
  },
  "recovered_sources": {
    "session_id": "01a08d3f-e474-72b1-ac46-a12c865268fc",
    "turn_id": "01a10e67-4b9e-7380-8b73-a9abae1c7b74",
    "original_response": {
      "message_id": "msg_0d407a9e2a4f3561016ac435f518ac87d29c19aed6e14836c8",
      "message_type": "agentMessage",
      "phase": "final_answer",
      "text": "",
      "utf8_bytes": 19371,
      "recovered_content_sha256": "14183309a0d5f5879795d52f4712ebe0ceaea0ac5fe99058cc0cfd95b31c44b2",
      "digest_semantics": "PRESENT_RECOVERY_MEASUREMENT_OF_RETRIEVED_ORIGINAL_RESPONSE_UTF8_NOT_ORIGINAL_HISTORICAL_ARTIFACT_HASH"
    },
    "executions": [
      {
        "role": "STAGE3_COMPONENT_INTEGRATION",
        "source_execution": {
          "type": "commandExecution",
          "id": "exec-195ce1b0-93d5-46cd-b85a-cfdfef2fe661",
          "command": "\"C:\\\\Users\\\\SK\\\\.cache\\\\codex-runtimes\\\\codex-primary-runtime\\\\dependencies\\\\native\\\\powershell\\\\pwsh.exe\" -Command '$env:PYTHONDONTWRITEBYTECODE='\"'1'; \"'$env:PYTEST_ADDOPTS='\"'-p no:cacheprovider'; \"'$env:PYTHONPATH=(Join-Path (Get-Location) '\"'src'); \"'$py='\"'C:\\\\Users\\\\SK\\\\AppData\\\\Local\\\\Temp\\\\atis-sync3-final-bcf64415f6304e09aad046b8a441f86b\\\\Scripts\\\\python.exe'; & \"'$py -m pytest -q --basetemp '\"'C:\\\\Users\\\\SK\\\\AppData\\\\Local\\\\Temp\\\\atis-stage3-40of40' tests/test_instrument_reference_model.py tests/test_pit_reference_resolution.py tests/test_calendar_session_model.py tests/test_calendar_session_resolution.py tests/test_market_data_acquisition.py tests/test_market_data_normalization.py tests/test_market_data_compatibility.py tests/test_market_data_quality.py tests/test_market_data_eligibility.py tests/test_corporate_actions.py tests/test_dataset_provenance.py tests/test_dataset_materialization.py tests/test_dataset_manifest.py tests/test_dataset_currentness.py tests/test_dataset_lifecycle.py tests/test_stage3_sync3.py\"",
          "cwd": "C:\\Users\\SK\\Documents\\Codex\\2026-09-10\\files-pasted-by-the-user-automated\\stage3-publication-workspace-4706222e",
          "status": "completed",
          "exitCode": 0,
          "durationMs": 14087,
          "output": {
            "text": "........................................................................ [ 12%]\r\n........................................................................ [ 24%]\r\n........................................................................ [ 37%]\r\n........................................................................ [ 49%]\r\n........................................................................ [ 62%]\r\n........................................................................ [ 74%]\r\n........................................................................ [ 87%]\r\n........................................................................ [ 99%]\r\n.                                                                        [100%]\r\n577 passed in 11.14s\r\n",
            "truncated": false
          }
        },
        "passed": 577,
        "historical_context": {
          "attribution": "EXECUTION_IN_THE_ORIGINAL_RECONCILIATION_TURN",
          "invocation": "PRESERVED_SOURCE_COMMAND_NOT_A_CURRENT_VALIDATION_RECIPE",
          "component_scope": "ORIGINAL_16_FILE_COMPONENT_INTEGRATION_SET",
          "limitations": [
            "HISTORICAL_LOCAL_EXECUTION_NOT_HOSTED_ASSURANCE",
            "ORIGINAL_COMMAND_USED_TEMPORARY_INTERPRETER_AND_PYTHONPATH_SRC",
            "NOT_LATER_REGRESSION_OR_BOOTSTRAP_VALIDATION",
            "NO_CLAIM_OF_RECOVERED_REVIEWER_MODEL_OR_VERSION_METADATA"
          ]
        }
      },
      {
        "role": "STAGE3_GOVERNANCE_SPEC_RECOVERY",
        "source_execution": {
          "type": "commandExecution",
          "id": "exec-79ce9935-cb9a-43ba-a5e3-7d826a536e92",
          "command": "\"C:\\\\Users\\\\SK\\\\.cache\\\\codex-runtimes\\\\codex-primary-runtime\\\\dependencies\\\\native\\\\powershell\\\\pwsh.exe\" -Command '$env:PYTHONDONTWRITEBYTECODE='\"'1'; \"'$env:PYTEST_ADDOPTS='\"'-p no:cacheprovider'; \"'$env:PYTHONPATH=(Join-Path (Get-Location) '\"'src'); \"'$py='\"'C:\\\\Users\\\\SK\\\\AppData\\\\Local\\\\Temp\\\\atis-sync3-final-bcf64415f6304e09aad046b8a441f86b\\\\Scripts\\\\python.exe'; & \"'$py -m pytest -q --basetemp '\"'C:\\\\Users\\\\SK\\\\AppData\\\\Local\\\\Temp\\\\atis-stage3-governance-40of40' tests/test_stage3_specification.py tests/test_programme_control.py tests/test_successor_evidence.py tests/test_master_recovery_packet.py\"",
          "cwd": "C:\\Users\\SK\\Documents\\Codex\\2026-09-10\\files-pasted-by-the-user-automated\\stage3-publication-workspace-4706222e",
          "status": "completed",
          "exitCode": 0,
          "durationMs": 242538,
          "output": {
            "text": "........................................................................ [ 20%]\r\n........................................................................ [ 40%]\r\n........................................................................ [ 60%]\r\n........................................................................ [ 80%]\r\n....................................................................     [100%]\r\n356 passed in 239.58s (0:03:59)\r\n",
            "truncated": false
          }
        },
        "passed": 356,
        "historical_context": {
          "attribution": "EXECUTION_IN_THE_ORIGINAL_RECONCILIATION_TURN",
          "invocation": "PRESERVED_SOURCE_COMMAND_NOT_A_CURRENT_VALIDATION_RECIPE",
          "component_scope": "ORIGINAL_4_FILE_GOVERNANCE_SPEC_RECOVERY_SET",
          "limitations": [
            "HISTORICAL_LOCAL_EXECUTION_NOT_HOSTED_ASSURANCE",
            "ORIGINAL_COMMAND_USED_TEMPORARY_INTERPRETER_AND_PYTHONPATH_SRC",
            "NOT_LATER_REGRESSION_OR_BOOTSTRAP_VALIDATION",
            "NO_CLAIM_OF_RECOVERED_REVIEWER_MODEL_OR_VERSION_METADATA"
          ]
        }
      }
    ]
  },
  "recovered_results": {
    "requirements": [
      {
        "requirement_id": "S3-REQ-001",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-002",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-003",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-004",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-005",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-006",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-007",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-008",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-009",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-010",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-011",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-012",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-013",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-014",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-015",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-016",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-017",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-018",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-019",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-020",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-021",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-022",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-023",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-024",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-025",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-026",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-027",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-028",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-029",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-030",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-031",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-032",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-033",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-034",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-035",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-037",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-038",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-039",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-040",
        "disposition": "SATISFIED"
      },
      {
        "requirement_id": "S3-REQ-041",
        "disposition": "SATISFIED"
      }
    ],
    "substantive_count": 40,
    "satisfied": 40,
    "partial": 0,
    "blocked": 0,
    "excluded_requirement": "S3-REQ-036",
    "component_integration_passed": 577,
    "governance_spec_recovery_passed": 356,
    "combined_passed": 933,
    "result_semantics": "RECOVERED_HISTORICAL_RESULTS_NOT_A_PRESENT_RECONCILIATION_OR_GATE_RESULT"
  },
  "recording_event": {
    "recorded_at": "2026-10-06T12:20:31Z",
    "timestamp_semantics": "PRESENT_REPOSITORY_PRESERVATION_TIME_NOT_HISTORICAL_REVIEW_OR_PUBLICATION_TIME",
    "retrieval_method": "CODEX_READ_THREAD_SOURCE_SESSION_TURN_MESSAGE_AND_COMMAND_EXECUTION_ITEMS",
    "source_fidelity": "UTF8_BYTES_AND_SHA256_RECOMPUTED_BEFORE_PRESERVATION",
    "protected_preservation_base": "96f92b58fd72c8d999e558b22d20f5b52476ccaf",
    "protected_preservation_tree": "5967aee9708f786658efd6643e32380d0de7887d",
    "publication_identity": "NOT_YET_ESTABLISHED"
  },
  "provenance_limitations": {
    "original_file_identity": null,
    "original_publication_commit": null,
    "original_artifact_hash": null,
    "original_reviewer_identity": null,
    "original_model_version": null,
    "unrecovered_semantics": "NULL_MEANS_UNRECOVERED_NOT_ABSENT_OR_INFERRED",
    "not_original_historical_repository_artifact": true,
    "no_original_repository_timestamp_claim": true,
    "separated_events": [
      "HISTORICAL_REVIEW",
      "HISTORICAL_DECISION",
      "HISTORICAL_EVALUATED_BASELINE",
      "RECOVERED_ORIGINAL_RESPONSE",
      "RECOVERED_EXECUTION_OUTPUTS",
      "PRESENT_RECOVERY",
      "PRESENT_REPOSITORY_PRESERVATION",
      "FUTURE_PUBLICATION"
    ]
  },
  "consumer_applicability": {
    "intended_consumers": [
      "GATE-S03-01",
      "FINAL-STAGE3-INDEPENDENT-AUDIT"
    ],
    "acceptance": "INDEPENDENT_APPLICABILITY_ACCEPTANCE_REQUIRED",
    "gate_evidence_accepted": false,
    "self_certification": false,
    "depends_on_gate_result": false,
    "resolution": "EXACT_CANONICAL_REFERENCE_TO_THIS_RECORD_AT_AN_INDEPENDENTLY_ACCEPTED_IMMUTABLE_GIT_COMMIT_AND_BLOB",
    "duplicate_resolution": "REJECT_COMPETING_RECORDS_FOR_THE_SAME_CANONICAL_REFERENCE"
  },
  "authority": {
    "grants": [],
    "denials": [
      "GATE_S03_01_PASS",
      "STAGE3_CLOSURE",
      "STAGE4_ACTIVATION",
      "STAGE4_IMPLEMENTATION",
      "RESEARCH_BACKTEST_EXECUTION",
      "DATASET_PROMOTION",
      "SHADOW_TRADING",
      "PAPER_TRADING",
      "BROKER_AUTHORITY",
      "OMS_AUTHORITY",
      "RISK_APPROVAL",
      "FINANCIAL_EFFECTS",
      "LIVE_TRADING",
      "LIVE_CAPITAL",
      "AI_TRADING_AUTHORITY"
    ]
  }
}'''


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"duplicate JSON key: {key}"
        result[key] = value
    return result


def load_record():
    return json.loads(
        RECORD_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )


def exact(actual, expected):
    """Closed, type-sensitive recursive equality (bool is not an integer)."""
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            exact(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            exact(left, right)
    else:
        assert actual == expected


def validate(record):
    response = record["recovered_sources"]["original_response"]
    source = response["text"]
    assert type(source) is str
    encoded = source.encode("utf-8")
    assert len(encoded) == 19371 == response["utf8_bytes"]
    assert hashlib.sha256(encoded).hexdigest() == SOURCE_SHA256
    assert response["recovered_content_sha256"] == SOURCE_SHA256

    expected = json.loads(EXPECTED_RECORD_JSON)
    preserved = deepcopy(record)
    preserved["recovered_sources"]["original_response"]["text"] = ""
    exact(preserved, expected)

    historical = record["historical_review"]
    assert f'ACTIVE_UNIT = {historical["review_id"]}' in source
    assert f'DECISION = {historical["decision_id"]}' in source
    assert f'ATIS_PROTECTED_MASTER = {historical["evaluated_master"]}' in source
    assert f'ATIS_PROTECTED_TREE = {historical["evaluated_tree"]}' in source
    source_ids = tuple(
        f"S3-REQ-{number}"
        for number in re.findall(r"^(\d{3}) \| SATISFIED \|", source, re.MULTILINE)
    )
    assert source_ids == SUBSTANTIVE_IDS
    requirements = record["recovered_results"]["requirements"]
    assert tuple(row["requirement_id"] for row in requirements) == source_ids
    assert len(set(source_ids)) == len(requirements) == 40
    assert all(row["disposition"] == "SATISFIED" for row in requirements)
    for name, value in (
        ("SUBSTANTIVE_REQUIREMENTS", 40),
        ("SATISFIED_COUNT", 40),
        ("PARTIAL_COUNT", 0),
        ("BLOCKED_COUNT", 0),
    ):
        assert f"{name} = {value}" in source

    counts = []
    for execution in record["recovered_sources"]["executions"]:
        evidence = execution["source_execution"]
        assert evidence["exitCode"] == 0
        assert evidence["status"] == "completed"
        assert evidence["output"]["truncated"] is False
        matches = re.findall(
            r"^(\d+) passed in .+\r?$", evidence["output"]["text"], re.MULTILINE
        )
        assert len(matches) == 1
        count = int(matches[0])
        assert count == execution["passed"]
        counts.append(count)
    assert counts == [577, 356]
    assert sum(counts) == record["recovered_results"]["combined_passed"] == 933

    turn = historical["containing_turn"]
    started = datetime.fromisoformat(turn["started_at"])
    completed = datetime.fromisoformat(turn["completed_at"])
    recorded = datetime.fromisoformat(record["recording_event"]["recorded_at"])
    assert started.tzinfo == completed.tzinfo == recorded.tzinfo == timezone.utc
    assert started < completed < recorded
    assert all(value is None for key, value in record["provenance_limitations"].items()
               if key.startswith("original_"))
    assert record["authority"]["grants"] == []
    assert record["consumer_applicability"]["gate_evidence_accepted"] is False


def validate_package(documents):
    expected = json.loads(EXPECTED_RECORD_JSON)
    matches = [
        (path, record)
        for path, record in documents
        if isinstance(record, dict) and (
            record.get("record_id") == expected["record_id"]
            or record.get("record_kind") == expected["record_kind"]
            or record.get("canonical_reference") == expected["canonical_reference"]
        )
    ]
    assert len(matches) == 1, "competing or missing canonical recovery record"
    path, record = matches[0]
    assert path == RELATIVE_PATH
    validate(record)


def test_canonical_recovered_evidence():
    validate(load_record())


def test_repository_has_one_canonical_recovery_record():
    documents = [
        (path.relative_to(ROOT).as_posix(), json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((ROOT / "docs").rglob("*.json"))
    ]
    validate_package(documents)


def test_deterministic_utf8_bom_free_canonical_lf_serialization():
    raw = RECORD_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    record = load_record()
    canonical = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Validate the canonical Git representation while permitting Git's native
    # checkout line endings. No new checkout policy or normalization is applied.
    assert text.replace("\r\n", "\n").encode("utf-8") == canonical
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", RELATIVE_PATH],
        cwd=ROOT, capture_output=True, check=False,
    )
    if tracked.returncode == 0:
        stored = subprocess.run(
            ["git", "show", f":{RELATIVE_PATH}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
    else:
        assert tracked.returncode == 1
        stored = raw
    assert stored == canonical
    assert b"\r" not in stored
    assert stored.endswith(b"\n") and not stored.endswith(b"\n\n")


def replaced(path, value):
    record = load_record()
    node = record
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return record


@pytest.mark.parametrize("mutation", ["byte", "truncate", "append", "summary"])
def test_reject_source_text_mutation(mutation):
    source = load_record()["recovered_sources"]["original_response"]["text"]
    value = {
        "byte": "X" + source[1:],
        "truncate": source[:-1],
        "append": source + "\n",
        "summary": "40 requirements satisfied; 933 tests passed.",
    }[mutation]
    with pytest.raises(AssertionError):
        validate(replaced(("recovered_sources", "original_response", "text"), value))


BAD_VALUES = (
    (("recovered_sources", "original_response", "recovered_content_sha256"), "0" * 64),
    (("recovered_sources", "original_response", "utf8_bytes"), 19370),
    (("recovered_sources", "session_id"), "01a08d3f-e474-72b1-ac46-a12c865268fd"),
    (("recovered_sources", "turn_id"), "01a10e67-4b9e-7380-8b73-a9abae1c7b75"),
    (("recovered_sources", "original_response", "message_id"), "another-message"),
    (("historical_review", "evaluated_master"), "96f92b58fd72c8d999e558b22d20f5b52476ccaf"),
    (("historical_review", "evaluated_tree"), "5967aee9708f786658efd6643e32380d0de7887d"),
    (("historical_review", "evaluated_master"), "origin/master"),
    (("historical_review", "decision_id"), "GATE_S03_01_PASS"),
    (("recovered_results", "substantive_count"), 41),
    (("recovered_results", "satisfied"), 39),
    (("recovered_results", "partial"), 1),
    (("recovered_results", "blocked"), 1),
    (("recovered_results", "component_integration_passed"), 578),
    (("recovered_results", "governance_spec_recovery_passed"), 357),
    (("recovered_results", "combined_passed"), 934),
    (("recovered_sources", "executions", 0, "passed"), 576),
    (("recovered_sources", "executions", 1, "passed"), 355),
    (("recovered_sources", "executions", 0, "source_execution", "id"), "exec-invented"),
    (("recovered_sources", "executions", 0, "source_execution", "exitCode"), 1),
    (("recovered_sources", "executions", 1, "source_execution", "exitCode"), True),
    (("recovered_sources", "executions", 1, "source_execution", "output", "truncated"), True),
    (("recovered_sources", "executions", 0, "source_execution", "command"), "pytest"),
    (("recovered_sources", "executions", 0, "historical_context", "limitations"), []),
    (("provenance_limitations", "original_file_identity"), "original-review.json"),
    (("provenance_limitations", "original_publication_commit"), "a1a70752ec2a5fa50dd4c80c5613dabe5ec0d325"),
    (("provenance_limitations", "original_artifact_hash"), SOURCE_SHA256),
    (("provenance_limitations", "original_reviewer_identity"), "invented-reviewer"),
    (("provenance_limitations", "original_model_version"), "invented-model"),
    (("provenance_limitations", "not_original_historical_repository_artifact"), False),
    (("provenance_limitations", "no_original_repository_timestamp_claim"), False),
    (("recovered_sources", "original_response", "digest_semantics"), "ORIGINAL_HISTORICAL_ARTIFACT_HASH"),
    (("historical_review", "containing_turn", "completed_at"), "2026-10-06T12:00:00Z"),
    (("historical_review", "containing_turn", "timestamp_semantics"), "REPOSITORY_CREATION_TIME"),
    (("recording_event", "recorded_at"), "2026-10-05T23:44:02Z"),
    (("recording_event", "timestamp_semantics"), "HISTORICAL_REVIEW_TIME"),
    (("recording_event", "publication_identity"), "main"),
    (("record_kind",), "ORIGINAL_HISTORICAL_REPOSITORY_ARTIFACT"),
    (("consumer_applicability", "acceptance"), "ACCEPTED"),
    (("consumer_applicability", "self_certification"), True),
    (("consumer_applicability", "gate_evidence_accepted"), True),
    (("consumer_applicability", "depends_on_gate_result"), True),
    (("consumer_applicability", "resolution"), "origin/master"),
    (("canonical_reference",), "latest-review"),
    (("schema_version",), True),
    (("recovered_results", "requirements"), {}),
    (("authority", "denials"), []),
)


@pytest.mark.parametrize(("path", "value"), BAD_VALUES)
def test_reject_attribution_provenance_and_type_substitution(path, value):
    with pytest.raises((AssertionError, KeyError, TypeError, ValueError)):
        validate(replaced(path, value))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "insert036", "disposition"])
def test_reject_requirement_corruption(mutation):
    record = load_record()
    rows = record["recovered_results"]["requirements"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif mutation == "unknown":
        rows[-1]["requirement_id"] = "S3-REQ-999"
    elif mutation == "insert036":
        rows.append({"requirement_id": "S3-REQ-036", "disposition": "SATISFIED"})
    else:
        rows[0]["disposition"] = "PARTIAL"
    with pytest.raises(AssertionError):
        validate(record)


@pytest.mark.parametrize("mutation", ["swapped", "false_sum", "output", "relabel"])
def test_reject_execution_evidence_corruption(mutation):
    record = load_record()
    executions = record["recovered_sources"]["executions"]
    if mutation == "swapped":
        executions.reverse()
    elif mutation == "false_sum":
        executions[0]["passed"] = 578
        record["recovered_results"]["component_integration_passed"] = 578
        record["recovered_results"]["combined_passed"] = 933
    elif mutation == "output":
        output = executions[0]["source_execution"]["output"]
        output["text"] = output["text"].replace("577 passed", "578 passed")
    else:
        executions[0]["historical_context"]["attribution"] = "LATER_BOOTSTRAP"
    with pytest.raises(AssertionError):
        validate(record)


@pytest.mark.parametrize(
    "grant",
    [
        "GATE_S03_01_PASS", "41_OF_41_GATE_PASS", "STAGE3_CLOSURE",
        "STAGE4_ACTIVATION", "STAGE4_IMPLEMENTATION", "RESEARCH_BACKTEST_EXECUTION",
        "DATASET_PROMOTION", "SHADOW_TRADING", "PAPER_TRADING", "BROKER_AUTHORITY",
        "OMS_AUTHORITY", "RISK_APPROVAL", "FINANCIAL_EFFECTS", "LIVE_TRADING",
        "LIVE_CAPITAL", "AI_TRADING_AUTHORITY",
    ],
)
def test_reject_every_authority_grant(grant):
    with pytest.raises(AssertionError):
        validate(replaced(("authority", "grants"), [grant]))


@pytest.mark.parametrize("path", [
    (), ("historical_review",), ("recovered_sources",),
    ("recovered_sources", "original_response"), ("recovered_results",),
    ("recording_event",), ("provenance_limitations",),
    ("consumer_applicability",), ("authority",),
])
def test_reject_unexpected_keys(path):
    record = load_record()
    node = record
    for key in path:
        node = node[key]
    node["unexpected"] = "unassessed"
    with pytest.raises(AssertionError):
        validate(record)


def test_reject_competing_canonical_recovery_record():
    record = load_record()
    with pytest.raises(AssertionError):
        validate_package([
            (RELATIVE_PATH, record),
            ("docs/stage3/competing-recovery.json", deepcopy(record)),
        ])


def test_reject_mutable_resolution_alias():
    with pytest.raises(AssertionError):
        validate_package([("origin/master", load_record())])


def test_reject_duplicate_json_key():
    with pytest.raises(AssertionError):
        json.loads('{"schema_version": 1, "schema_version": 1}',
                   object_pairs_hook=reject_duplicate_keys)

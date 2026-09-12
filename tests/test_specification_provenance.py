from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.extract_specification_text import EXTRACTION_METHOD, extract_docx_paragraph_text


ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "docs/baseline/specification-provenance.json"
EXPECTED_SOURCE_SHA256 = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
EXPECTED_EXTRACTION_SHA256 = "13d18b573af4885c492d43704e2138de4f8dff3307d32200640ef4ee5344e80c"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load() -> dict:
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def _repository_path(value: str) -> Path:
    path = Path(value)
    assert not path.is_absolute()
    resolved = (ROOT / path).resolve()
    assert resolved.is_relative_to(ROOT)
    assert resolved.is_file()
    return resolved


def _verify(metadata: dict, source: bytes, extraction: bytes) -> None:
    assert metadata["canonical_artifact_sha256"] == EXPECTED_SOURCE_SHA256
    assert metadata["extraction_sha256"] == EXPECTED_EXTRACTION_SHA256
    assert _sha256(source) == metadata["canonical_artifact_sha256"]
    assert _sha256(extraction) == metadata["extraction_sha256"]


def test_canonical_specification_and_extraction_are_repository_bound() -> None:
    metadata = _load()
    source = _repository_path(metadata["canonical_artifact"])
    extraction = _repository_path(metadata["machine_readable_extraction"])
    implementation = _repository_path(metadata["extraction_implementation"])

    assert metadata["version"] == "1.0"
    assert metadata["extraction_method"] == EXTRACTION_METHOD
    assert implementation == ROOT / "scripts/extract_specification_text.py"
    assert "governing canonical artifact" in metadata["normative_statement"]
    assert "does not supersede" in metadata["normative_statement"]
    assert "machine-local absolute path" in metadata["path_policy"]
    _verify(metadata, source.read_bytes(), extraction.read_bytes())
    assert extract_docx_paragraph_text(source) == extraction.read_bytes()

    traceability = json.loads(
        (ROOT / "docs/m1-closure/traceability.json").read_text(encoding="utf-8")
    )
    assert traceability["source_sha256"] == EXPECTED_SOURCE_SHA256
    assert traceability["source_artifact"] == metadata["canonical_artifact"]
    assert traceability["source_extraction"] == metadata["machine_readable_extraction"]
    assert traceability["source_provenance"] == PROVENANCE.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("target", ["source", "extraction", "metadata"])
def test_specification_provenance_rejects_mutation(target: str) -> None:
    metadata = _load()
    source = _repository_path(metadata["canonical_artifact"]).read_bytes()
    extraction = _repository_path(metadata["machine_readable_extraction"]).read_bytes()
    if target == "source":
        source += b"tampered"
    elif target == "extraction":
        extraction += b"tampered"
    else:
        metadata = copy.deepcopy(metadata)
        metadata["canonical_artifact_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _verify(metadata, source, extraction)


def test_canonical_docx_has_no_executable_or_external_package_content() -> None:
    source = _repository_path(_load()["canonical_artifact"])
    with zipfile.ZipFile(source) as package:
        names = package.namelist()
        assert not any("vbaproject" in name.lower() for name in names)
        assert not any(name.startswith("word/embeddings/") for name in names)
        for name in names:
            if name.endswith(".rels"):
                assert b'TargetMode="External"' not in package.read(name)

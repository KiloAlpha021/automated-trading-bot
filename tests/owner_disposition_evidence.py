"""Repository-contained validation for M1 owner scope dispositions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


BEGIN = "<!-- REPOSITORY-CANONICAL-OWNER-TEXT:BEGIN -->"
END = "<!-- REPOSITORY-CANONICAL-OWNER-TEXT:END -->"
SPECIFICATION_SHA256 = "4e3f144b12dbdcd43a29b15ccc99aaf9f5611bf51bbda2d1a3b3891d76a7bbe7"
EXPECTED_IDS = {
    "M1-SCOPE-2026-09-11-01",
    "M1-SCOPE-2026-09-11-02",
    "M1-SCOPE-2026-09-11-03",
    "M1-SCOPE-2026-09-11-04",
    "M1-SCOPE-2026-09-11-05",
    "M1-SCOPE-2026-09-12-01",
    "M1-SCOPE-2026-09-12-02",
    "M1-SCOPE-2026-09-12-03",
    "M1-SCOPE-2026-09-12-04",
}


def validate_owner_dispositions(root: Path) -> dict[str, dict[str, object]]:
    manifest_path = root / "docs/m1-closure/owner-dispositions.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["normative_source"] == "the marked canonical text in each artifact"
    records = {item["decision_id"]: item for item in manifest["decisions"]}
    assert set(records) == EXPECTED_IDS
    assert len(records) == len(manifest["decisions"])

    for decision_id, record in records.items():
        relative = Path(record["artifact"])
        assert not relative.is_absolute()
        artifact = (root / relative).resolve()
        assert artifact.is_relative_to(root.resolve()) and artifact.is_file()
        text = artifact.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert text.count(BEGIN) == text.count(END) == 1
        canonical = text.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0].strip() + "\n"
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == record["canonical_text_sha256"]
        assert decision_id in text
        assert record["specification_sha256"] == SPECIFICATION_SHA256
        assert SPECIFICATION_SHA256 in text
        assert record["historical_attachment_is_normative"] is False
    return records


def assert_owner_disposition(root: Path, decision_id: str) -> None:
    assert decision_id in validate_owner_dispositions(root)

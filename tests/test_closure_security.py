"""M1 source credential checks; not credential validity or universal secret detection."""

import re
import subprocess
from pathlib import Path

import pytest


_PATTERNS = (
    rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----",
    rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    rb"gh[pousr]_[A-Za-z0-9]{36,}",
    rb"github_pat_[A-Za-z0-9_]{20,}",
    rb"xox[baprs]-[A-Za-z0-9-]{10,}",
    rb"(?i)bearer[ \t]+[A-Za-z0-9._~-]{16,}",
    rb"(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)[\"']?[ \t]*[:=][ \t]*[\"'][^\"'\r\n]{8,}[\"']",
)


def _scan_file(path: Path, content: bytes) -> None:
    name = path.name.lower()
    credential_file = (
        (name == ".env" or name.startswith(".env.")) and name != ".env.example"
    ) or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or name in {
        "credentials", "credentials.json", "id_rsa", "id_ed25519", ".netrc"
    }
    assert not credential_file, f"Credential artifact: {path}"
    for pattern in _PATTERNS:
        assert re.search(pattern, content) is None, f"Recognizable secret literal: {path}"


def test_project_has_no_credential_artifacts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "ls-files", "-z",
         "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=True,
    )
    names = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    assert names, "Empty scan cannot establish source safety"
    for name in names:
        path = root / name
        assert path.is_file(), f"Unreadable scan input: {name}"
        _scan_file(Path(name), path.read_bytes())


@pytest.mark.parametrize("content", [
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN RSA " + b"PRIVATE KEY-----",
    b"AK" + b"IA" + b"A" * 16,
    b"gh" + b"p_" + b"a" * 36,
    b"github_" + b"pat_" + b"a" * 24,
    b"xox" + b"b-" + b"1234567890123",
    b"Bearer " + b"a" * 24,
    b"api_key = " + b"'synthetic-value-only'",
])
def test_scanner_rejects_synthetic_secret(content):
    with pytest.raises(AssertionError, match="Recognizable secret literal"):
        _scan_file(Path("synthetic.txt"), content)


@pytest.mark.parametrize("name", [".env", ".env.production", "credential.pem", "id_rsa", "credentials.json"])
def test_scanner_rejects_credential_files(name):
    with pytest.raises(AssertionError, match="Credential artifact"):
        _scan_file(Path(name), b"")


def test_scanner_accepts_nonsecret_source():
    _scan_file(Path("settings.py"), b"import os\nvalue = os.environ['API_KEY']\n")

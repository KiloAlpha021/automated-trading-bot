from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LOCK_LINE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9-]*)==(?P<version>[^\s]+) "
    r"--hash=sha256:(?P<digest>[0-9a-f]{64})$"
)


def _locked_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in (ROOT / "requirements-dev.lock").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = LOCK_LINE.fullmatch(line)
        assert match, f"invalid locked requirement: {line}"
        name = match.group("name")
        assert name not in packages, f"duplicate locked package: {name}"
        packages[name] = match.group("version")
    return packages


def test_development_lock_contains_declared_exact_requirements() -> None:
    locked = _locked_packages()
    declared = {}
    for line in (ROOT / "requirements-dev.in").read_text(encoding="utf-8").splitlines():
        name, version = line.split("==", 1)
        declared[name] = version
    assert declared.items() <= locked.items()


def test_development_lock_has_no_urls_editables_or_unhashed_entries() -> None:
    text = (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
    assert "https://" not in text
    assert "http://" not in text
    assert "file:" not in text
    assert "-e " not in text
    assert len(_locked_packages()) == 41

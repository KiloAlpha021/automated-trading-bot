from pathlib import Path
import re
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOCK_LINE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9-]*)==(?P<version>[^\s]+) "
    r"--hash=sha256:(?P<digest>[0-9a-f]{64})$"
)
EXACT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s]+)$"
)
EXPECTED_LOCKED_PACKAGES = {
    "ast-serialize": "0.11.1",
    "attrs": "26.1.0",
    "boolean-py": "5.0",
    "cachecontrol": "0.14.4",
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "colorama": "0.4.6",
    "coverage": "7.16.0",
    "cyclonedx-python-lib": "11.12.0",
    "defusedxml": "0.7.1",
    "filelock": "3.32.6",
    "idna": "3.19",
    "iniconfig": "2.3.0",
    "jsonschema": "4.26.0",
    "jsonschema-specifications": "2025.9.1",
    "librt": "0.15.0",
    "license-expression": "30.4.4",
    "markdown-it-py": "4.2.0",
    "mdurl": "0.1.2",
    "msgpack": "1.2.2",
    "mypy": "2.3.1",
    "mypy-extensions": "1.1.0",
    "packageurl-python": "0.17.6",
    "packaging": "26.3",
    "pathspec": "1.1.1",
    "pip": "26.2.1",
    "pip-api": "0.0.35",
    "pip-audit": "2.10.1",
    "pip-requirements-parser": "32.0.1",
    "platformdirs": "4.11.8",
    "pluggy": "1.6.0",
    "py-serializable": "2.1.0",
    "pygments": "2.21.0",
    "pyparsing": "3.3.2",
    "pytest": "9.1.1",
    "referencing": "0.37.0",
    "requests": "2.34.2",
    "rich": "15.0.0",
    "rpds-py": "2026.6.3",
    "ruff": "0.16.7",
    "setuptools": "84.0.0",
    "sortedcontainers": "2.4.0",
    "tomli": "2.4.1",
    "tomli-w": "1.2.0",
    "typing-extensions": "4.16.0",
    "urllib3": "2.7.0",
}


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_packages(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = LOCK_LINE.fullmatch(line)
        assert match, f"invalid locked requirement: {line}"
        name = match.group("name")
        assert name not in packages, f"duplicate locked package: {name}"
        packages[name] = match.group("version")
    return packages


def _exact_requirements(lines: list[str]) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for line in lines:
        match = EXACT_REQUIREMENT.fullmatch(line)
        assert match, f"development requirement is not an exact pin: {line}"
        name = _normalized(match.group("name"))
        assert name not in requirements, f"duplicate development requirement: {name}"
        requirements[name] = match.group("version")
    return requirements


def _lock_text() -> str:
    return (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")


def test_development_lock_contains_declared_exact_requirements() -> None:
    assert _locked_packages(_lock_text()) == EXPECTED_LOCKED_PACKAGES
    assert len(EXPECTED_LOCKED_PACKAGES) == 46


def test_direct_development_declarations_reconcile_with_lock() -> None:
    requirements_input = _exact_requirements(
        (ROOT / "requirements-dev.in").read_text(encoding="utf-8").splitlines()
    )
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_dev = _exact_requirements(pyproject["project"]["optional-dependencies"]["dev"])
    locked = _locked_packages(_lock_text())

    assert project_dev.items() <= requirements_input.items()
    assert requirements_input.items() <= locked.items()


def test_development_lock_has_no_urls_editables_or_unhashed_entries() -> None:
    text = _lock_text()
    assert "https://" not in text
    assert "http://" not in text
    assert "file:" not in text
    assert "-e " not in text
    assert _locked_packages(text) == EXPECTED_LOCKED_PACKAGES


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda lines: lines[1:],
        lambda lines: [
            *lines,
            "unexpected-package==1.0 --hash=sha256:" + "0" * 64,
        ],
        lambda lines: [
            *lines[1:],
            "unexpected-package==1.0 --hash=sha256:" + "0" * 64,
        ],
        lambda lines: [
            line.replace("attrs==26.1.0 ", "attrs==26.1.1 ")
            for line in lines
        ],
    ],
    ids=["missing", "unexpected", "one-for-one-substitution", "wrong-version"],
)
def test_exact_package_contract_rejects_set_corruption(corrupt) -> None:
    lines = [
        line
        for line in _lock_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert _locked_packages("\n".join(corrupt(lines))) != EXPECTED_LOCKED_PACKAGES


def test_lock_parser_rejects_duplicate_package() -> None:
    lines = _lock_text().splitlines()
    duplicate = next(line for line in lines if line.startswith("attrs=="))
    with pytest.raises(AssertionError, match="duplicate locked package: attrs"):
        _locked_packages("\n".join([*lines, duplicate]))


@pytest.mark.parametrize(
    "entry",
    [
        "attrs==26.1.0",
        "attrs==26.1.0 --hash=sha256:not-a-digest",
        "attrs @ https://example.invalid/attrs.whl",
        "-e file:../attrs",
        "attrs=26.1.0 --hash=sha256:" + "0" * 64,
    ],
    ids=["missing-hash", "malformed-hash", "url", "editable", "malformed-pin"],
)
def test_lock_parser_rejects_malformed_or_unhashed_entry(entry: str) -> None:
    with pytest.raises(AssertionError, match="invalid locked requirement"):
        _locked_packages(entry)

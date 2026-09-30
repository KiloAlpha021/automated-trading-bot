"""Generate the Windows/Python 3.12.10 development lock from declared inputs."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import tempfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 12, 10)
PYPI_INDEX = "https://pypi.org/simple"
LOCK_LINE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9-]*)==(?P<version>[^\s]+) "
    r"--hash=sha256:(?P<digest>[0-9a-f]{64})$"
)
UPGRADE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s]+)$"
)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _wheel_identity(path: Path) -> tuple[str, str]:
    with ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(metadata_names) != 1:
            raise ValueError(f"wheel has {len(metadata_names)} METADATA files: {path.name}")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields.setdefault(key, value)
    return _normalized(fields["Name"]), fields["Version"]


def _parse_lock(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = LOCK_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"invalid locked requirement: {line}")
        name = match.group("name")
        if name in packages:
            raise ValueError(f"duplicate locked package: {name}")
        packages[name] = match.group("version")
    if not packages:
        raise ValueError("preserved lock contains no packages")
    return packages


def _parse_upgrades(specifications: list[str], prior: dict[str, str]) -> dict[str, str]:
    upgrades: dict[str, str] = {}
    for specification in specifications:
        match = UPGRADE.fullmatch(specification)
        if match is None:
            raise ValueError(f"invalid upgrade specification: {specification}")
        name = _normalized(match.group("name"))
        version = match.group("version")
        if name in upgrades:
            raise ValueError(f"duplicate upgrade authority: {name}")
        if name not in prior:
            raise ValueError(f"upgrade target is absent from preserved lock: {name}")
        if prior[name] == version:
            raise ValueError(f"upgrade version is unchanged for {name}: {version}")
        upgrades[name] = version
    if not upgrades:
        raise ValueError("minimum-delta mode requires at least one authorized upgrade")
    return upgrades


def _validate_minimum_delta(
    prior: dict[str, str],
    resolved: dict[str, str],
    upgrades: dict[str, str],
) -> None:
    prior_names = set(prior)
    resolved_names = set(resolved)
    if prior_names != resolved_names:
        added = sorted(resolved_names - prior_names)
        removed = sorted(prior_names - resolved_names)
        raise ValueError(
            f"dependency package set changed: added={added}, removed={removed}"
        )
    changed = {
        name: (prior[name], resolved[name])
        for name in sorted(prior)
        if prior[name] != resolved[name]
    }
    expected = {
        name: (prior[name], version)
        for name, version in sorted(upgrades.items())
    }
    if changed != expected:
        raise ValueError(
            f"unauthorized dependency movement: expected={expected}, actual={changed}"
        )


def _write_atomic(output: Path, text: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def generate(
    output: Path,
    *,
    preserve_lock: Path | None = None,
    upgrade_specifications: list[str] | None = None,
) -> int:
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise SystemExit("lock generation requires CPython 3.12.10")
    if sysconfig.get_platform() != "win-amd64":
        raise SystemExit("lock generation requires Windows x86-64")

    source = ROOT / "requirements-dev.in"
    specifications = upgrade_specifications or []
    prior: dict[str, str] | None = None
    upgrades: dict[str, str] = {}
    if preserve_lock is None:
        if specifications:
            raise ValueError("--upgrade requires --preserve-lock")
    else:
        prior = _parse_lock(preserve_lock)
        upgrades = _parse_upgrades(specifications, prior)
    with tempfile.TemporaryDirectory(prefix="automated-trading-bot-lock-") as tmp:
        wheelhouse = Path(tmp)
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--timeout",
            "15",
            "--retries",
            "1",
            "--index-url",
            PYPI_INDEX,
            "--only-binary=:all:",
            "--dest",
            os.fspath(wheelhouse),
            "-r",
            os.fspath(source),
        ]
        if prior is not None:
            constraints = wheelhouse / "minimum-delta-constraints.txt"
            constrained = {
                name: upgrades.get(name, version)
                for name, version in prior.items()
            }
            constraints.write_text(
                "".join(
                    f"{name}=={version}\n"
                    for name, version in sorted(constrained.items())
                ),
                encoding="utf-8",
                newline="\n",
            )
            command.extend(["-c", os.fspath(constraints)])
        subprocess.run(command, check=True)
        packages: dict[tuple[str, str], str] = {}
        for wheel in wheelhouse.glob("*.whl"):
            identity = _wheel_identity(wheel)
            if identity in packages:
                raise ValueError(f"duplicate resolved wheel: {identity}")
            packages[identity] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if not packages:
            raise ValueError("resolver produced no wheels")
        resolved = {name: version for name, version in packages}
        if prior is not None:
            _validate_minimum_delta(prior, resolved, upgrades)

    lines = [
        "# CPython 3.12.10 / Windows x86-64; public PyPI wheels only.",
        "# Generated from requirements-dev.in; exact versions and SHA-256 hashes.",
    ]
    lines.extend(
        f"{name}=={version} --hash=sha256:{digest}"
        for (name, version), digest in sorted(packages.items())
    )
    _write_atomic(output, "\n".join(lines) + "\n")
    return len(packages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "requirements-dev.lock")
    parser.add_argument("--preserve-lock", type=Path)
    parser.add_argument("--upgrade", action="append", default=[])
    args = parser.parse_args()
    print(
        "locked "
        f"{generate(args.output, preserve_lock=args.preserve_lock, upgrade_specifications=args.upgrade)} "
        "packages"
    )

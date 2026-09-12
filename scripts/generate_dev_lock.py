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


def generate(output: Path) -> int:
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise SystemExit("lock generation requires CPython 3.12.10")
    if sysconfig.get_platform() != "win-amd64":
        raise SystemExit("lock generation requires Windows x86-64")

    source = ROOT / "requirements-dev.in"
    with tempfile.TemporaryDirectory(prefix="automated-trading-bot-lock-") as tmp:
        wheelhouse = Path(tmp)
        subprocess.run(
            [
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
            ],
            check=True,
        )
        packages: dict[tuple[str, str], str] = {}
        for wheel in wheelhouse.glob("*.whl"):
            identity = _wheel_identity(wheel)
            if identity in packages:
                raise ValueError(f"duplicate resolved wheel: {identity}")
            packages[identity] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if not packages:
            raise ValueError("resolver produced no wheels")

    lines = [
        "# CPython 3.12.10 / Windows x86-64; public PyPI wheels only.",
        "# Generated from requirements-dev.in; exact versions and SHA-256 hashes.",
    ]
    lines.extend(
        f"{name}=={version} --hash=sha256:{digest}"
        for (name, version), digest in sorted(packages.items())
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(packages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "requirements-dev.lock")
    args = parser.parse_args()
    print(f"locked {generate(args.output)} packages")

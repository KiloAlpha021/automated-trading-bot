from pathlib import Path

import pytest

from scripts.generate_dev_lock import (
    _parse_lock,
    _parse_upgrades,
    _validate_minimum_delta,
    _write_atomic,
)


HASH = "0" * 64


def _lock(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "requirements.lock"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_valid_prior_lock_and_single_authorized_upgrade(tmp_path: Path) -> None:
    prior = _parse_lock(
        _lock(
            tmp_path,
            [
                "# protected",
                f"requests==2.34.2 --hash=sha256:{HASH}",
                f"urllib3==2.7.0 --hash=sha256:{HASH}",
            ],
        )
    )
    upgrades = _parse_upgrades(["urllib3==2.8.0"], prior)
    _validate_minimum_delta(
        prior,
        {"requests": "2.34.2", "urllib3": "2.8.0"},
        upgrades,
    )
    assert upgrades == {"urllib3": "2.8.0"}


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["urllib3==2.7.0"], "invalid locked requirement"),
        (
            [
                f"urllib3==2.7.0 --hash=sha256:{HASH}",
                f"urllib3==2.7.0 --hash=sha256:{HASH}",
            ],
            "duplicate locked package",
        ),
    ],
)
def test_prior_lock_rejects_malformed_or_duplicate_entries(
    tmp_path: Path,
    lines: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_lock(_lock(tmp_path, lines))


@pytest.mark.parametrize(
    ("specifications", "message"),
    [
        (["urllib3"], "invalid upgrade specification"),
        (["missing==1.0"], "absent from preserved lock"),
        (["urllib3==2.7.0"], "upgrade version is unchanged"),
        (
            ["urllib3==2.8.0", "urllib3==2.9.0"],
            "duplicate upgrade authority",
        ),
    ],
)
def test_upgrade_authority_rejects_invalid_requests(
    specifications: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_upgrades(specifications, {"urllib3": "2.7.0"})


@pytest.mark.parametrize(
    ("resolved", "message"),
    [
        (
            {"requests": "2.34.2", "urllib3": "2.8.0", "extra": "1.0"},
            "dependency package set changed",
        ),
        ({"urllib3": "2.8.0"}, "dependency package set changed"),
        (
            {"requests": "2.35.0", "urllib3": "2.8.0"},
            "unauthorized dependency movement",
        ),
    ],
)
def test_minimum_delta_rejects_set_or_unauthorized_movement(
    resolved: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_minimum_delta(
            {"requests": "2.34.2", "urllib3": "2.7.0"},
            resolved,
            {"urllib3": "2.8.0"},
        )


def test_atomic_write_preserves_existing_output_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "requirements.lock"
    output.write_text("protected\n", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("scripts.generate_dev_lock.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        _write_atomic(output, "candidate\n")
    assert output.read_text(encoding="utf-8") == "protected\n"

import hashlib

import pytest

from automated_trading_bot.research.identity import (
    Experiment,
    Hypothesis,
    ResearchProject,
    ResearchRun,
)
from automated_trading_bot.research.reproducibility import (
    PrngIdentity,
    PrngUsage,
    ReplayIdentity,
    ReproducibilityRecord,
    RuntimeIdentity,
    SourceIdentity,
    WorkingTreeState,
)


SHA = hashlib.sha256(b"lock").hexdigest()
SOURCE = SourceIdentity("1" * 40, "2" * 40, WorkingTreeState.CLEAN)
RUNTIME = RuntimeIdentity("3.12.10", "python.exe", "Windows", "AMD64")


def record(**changes):
    values = dict(
        source=SOURCE,
        runtime=RUNTIME,
        dependency_lock_digest=SHA,
        configuration={"b": [2], "a": 1},
        prng=PrngIdentity(PrngUsage.NONE),
    )
    values.update(changes)
    return ReproducibilityRecord.create(**values)


def experiment():
    p = ResearchProject.create("p")
    h = Hypothesis.create(p.project_id, "h")
    return Experiment.create(h.hypothesis_id, record().configuration_digest, "1", "e")


def test_clean_dirty_and_unknown_source_classification() -> None:
    assert record().authoritative
    assert not record(
        source=SourceIdentity("1" * 40, "2" * 40, WorkingTreeState.DIRTY)
    ).authoritative
    assert not record(
        source=SourceIdentity("1" * 40, "2" * 40, WorkingTreeState.UNKNOWN)
    ).authoritative


def test_source_runtime_and_lock_substitution_change_identity() -> None:
    assert (
        record().digest
        != record(
            source=SourceIdentity("3" * 40, "2" * 40, WorkingTreeState.CLEAN)
        ).digest
    )
    assert (
        record().digest
        != record(
            runtime=RuntimeIdentity("3.12.10", "other", "Windows", "AMD64")
        ).digest
    )
    assert record().digest != record(dependency_lock_digest="4" * 64).digest


def test_missing_or_malformed_source_and_lock_fail_closed() -> None:
    with pytest.raises((TypeError, ValueError)):
        SourceIdentity("bad", "2" * 40, WorkingTreeState.CLEAN)
    with pytest.raises((TypeError, ValueError)):
        record(dependency_lock_digest="bad")


def test_configuration_is_canonical_and_semantic_change_changes_identity() -> None:
    assert (
        record(configuration={"a": 1, "b": [2]}).digest
        == record(configuration={"b": [2], "a": 1}).digest
    )
    assert (
        record(configuration={"a": 2}).configuration_digest
        != record(configuration={"a": 1}).configuration_digest
    )


def test_prng_none_and_deterministic_contracts() -> None:
    assert PrngIdentity(PrngUsage.NONE).usage is PrngUsage.NONE
    assert (
        PrngIdentity(PrngUsage.DETERMINISTIC, "PCG64", 42, "full-state-v1").seed == 42
    )
    with pytest.raises(ValueError):
        PrngIdentity(PrngUsage.DETERMINISTIC, "PCG64", None, "state")
    with pytest.raises(ValueError):
        PrngIdentity(PrngUsage.INVALID)


def test_hidden_random_state_is_rejected() -> None:
    with pytest.raises(ValueError):
        PrngIdentity(PrngUsage.NONE, "ambient", None, None)


def test_replay_identity_matches_exact_inputs_and_rejects_divergence() -> None:
    exp = experiment()
    run = ResearchRun.create(exp.experiment_id, record().digest)
    replay = ReplayIdentity(
        run.run_id, exp.experiment_id, record().digest, ("dataset:v1",)
    )
    assert replay.matches(exp.experiment_id, record().digest, ("dataset:v1",))
    assert not replay.matches(exp.experiment_id, record().digest, ("dataset:v2",))

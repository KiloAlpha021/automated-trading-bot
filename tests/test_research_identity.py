from dataclasses import FrozenInstanceError
import hashlib

import pytest

from automated_trading_bot.research.identity import (
    Experiment,
    Hypothesis,
    ResearchProject,
    ResearchRun,
)


def digest(value: bytes = b"x") -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def hierarchy():
    project = ResearchProject.create("daily US equity research")
    hypothesis = Hypothesis.create(
        project.project_id, "momentum has positive risk-adjusted return"
    )
    experiment = Experiment.create(
        hypothesis.hypothesis_id, digest(), "1.0.0", "falsify momentum"
    )
    run = ResearchRun.create(experiment.experiment_id, digest(b"repro"))
    return project, hypothesis, experiment, run


def test_identity_hierarchy_is_typed_deterministic_and_content_addressed() -> None:
    assert hierarchy() == hierarchy()
    project, hypothesis, experiment, run = hierarchy()
    assert hypothesis.project_id == project.project_id
    assert experiment.hypothesis_id == hypothesis.hypothesis_id
    assert run.experiment_id == experiment.experiment_id


def test_missing_and_wrong_parent_types_are_rejected() -> None:
    with pytest.raises(TypeError):
        Hypothesis.create(None, "claim")  # type: ignore[arg-type]
    project = ResearchProject.create("p")
    with pytest.raises(TypeError):
        Experiment.create(project.project_id, digest(), "1", "x")  # type: ignore[arg-type]


def test_parent_and_configuration_substitution_changes_identity() -> None:
    p1, p2 = ResearchProject.create("one"), ResearchProject.create("two")
    assert (
        Hypothesis.create(p1.project_id, "claim").hypothesis_id
        != Hypothesis.create(p2.project_id, "claim").hypothesis_id
    )
    h = Hypothesis.create(p1.project_id, "claim")
    assert (
        Experiment.create(h.hypothesis_id, digest(b"a"), "1", "x").experiment_id
        != Experiment.create(h.hypothesis_id, digest(b"b"), "1", "x").experiment_id
    )


def test_version_is_part_of_frozen_experiment_semantics() -> None:
    h = hierarchy()[1]
    assert (
        Experiment.create(h.hypothesis_id, digest(), "1", "x").experiment_id
        != Experiment.create(h.hypothesis_id, digest(), "2", "x").experiment_id
    )


def test_records_are_immutable_and_invalid_identity_is_rejected() -> None:
    project = hierarchy()[0]
    with pytest.raises(FrozenInstanceError):
        project.purpose = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        type(project.project_id)("bad")


def test_replay_is_explicit_and_self_cycle_is_rejected() -> None:
    run = hierarchy()[3]
    replay = ResearchRun.create(run.experiment_id, digest(b"new"), run.run_id)
    assert replay.replay_of == run.run_id

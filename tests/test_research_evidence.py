import hashlib

import pytest

from automated_trading_bot.research.evidence import EvidenceDisposition, RESEARCH_ONLY_AUTHORITY, ResearchEvidenceManifest
from automated_trading_bot.research.identity import Experiment, Hypothesis, ResearchProject, ResearchRun
from automated_trading_bot.research.reproducibility import PrngIdentity, PrngUsage, ReproducibilityRecord, RuntimeIdentity, SourceIdentity, WorkingTreeState


def fixture(state=WorkingTreeState.CLEAN):
    repro=ReproducibilityRecord.create(source=SourceIdentity("1"*40,"2"*40,state),runtime=RuntimeIdentity("3.12.10","python.exe","Windows","AMD64"),dependency_lock_digest=hashlib.sha256(b"lock").hexdigest(),configuration={"window":20},prng=PrngIdentity(PrngUsage.NONE),dataset_identities=("dataset:v1",))
    p=ResearchProject.create("p"); h=Hypothesis.create(p.project_id,"h"); e=Experiment.create(h.hypothesis_id,repro.configuration_digest,"1","e"); r=ResearchRun.create(e.experiment_id,repro.digest)
    return p,h,e,r,repro


def manifest(disposition=EvidenceDisposition.VALID, reasons=(), state=WorkingTreeState.CLEAN):
    p,h,e,r,repro=fixture(state)
    return ResearchEvidenceManifest.create(project_id=p.project_id,hypothesis_id=h.hypothesis_id,experiment_id=e.experiment_id,run_id=r.run_id,reproducibility=repro,input_identities=("dataset:v1",),output_identities=("result:v1",),disposition=disposition,reasons=reasons)


def test_complete_manifest_is_content_addressed_and_immutable() -> None:
    assert manifest().manifest_digest == manifest().manifest_digest
    assert manifest().authority == RESEARCH_ONLY_AUTHORITY


def test_missing_or_conflicting_identity_is_rejected() -> None:
    p,h,e,r,repro=fixture()
    with pytest.raises(TypeError): ResearchEvidenceManifest.create(project_id=None,hypothesis_id=h.hypothesis_id,experiment_id=e.experiment_id,run_id=r.run_id,reproducibility=repro,disposition=EvidenceDisposition.VALID)  # type: ignore[arg-type]
    with pytest.raises(ValueError): ResearchEvidenceManifest.create(project_id=p.project_id,hypothesis_id=h.hypothesis_id,experiment_id=e.experiment_id,run_id=r.run_id,reproducibility=repro,input_identities=("same","same"),disposition=EvidenceDisposition.VALID)


@pytest.mark.parametrize("state", [EvidenceDisposition.INVALID,EvidenceDisposition.UNKNOWN,EvidenceDisposition.FAILED,EvidenceDisposition.REJECTED])
def test_fail_closed_dispositions_require_reasons(state) -> None:
    with pytest.raises(ValueError): manifest(state)
    assert manifest(state,("reason",)).disposition is state


def test_valid_manifest_rejects_dirty_or_unknown_source() -> None:
    with pytest.raises(ValueError): manifest(state=WorkingTreeState.DIRTY)
    with pytest.raises(ValueError): manifest(state=WorkingTreeState.UNKNOWN)


def test_trading_authority_is_rejected() -> None:
    p,h,e,r,repro=fixture()
    with pytest.raises(ValueError,match="trading authority"):
        ResearchEvidenceManifest.create(project_id=p.project_id,hypothesis_id=h.hypothesis_id,experiment_id=e.experiment_id,run_id=r.run_id,reproducibility=repro,disposition=EvidenceDisposition.VALID,authority="LIVE_TRADING")


def test_stage3_dataset_is_referenced_not_duplicated() -> None:
    value=manifest()
    assert value.input_identities == ("dataset:v1",)
    assert not hasattr(value,"dataset_manifest")

"""Replay the private V3.1 source failure when its immutable store is present."""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from aletheia_lab.evaluation import claim_validation_v3_qualification as qualification_module
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_support_v3_cohort import (
    build_cohort_plan,
    load_authorization,
    load_verified_qualification,
    rehearse_cohort,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_execution import (
    verify_source_cohort,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_failure import (
    audit_failed_source_cohort,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import read_document
from aletheia_lab.filesystem import windows_io_path

ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT.parent / "memory"
PREDECESSOR = MEMORY / "claim-support-validation-v2-extraction" / "closeout.json"
QUALIFICATION = MEMORY / "claim-support-validation-v3-qualification-v2"
RUN = MEMORY / "claim-support-validation-v3-source-cohort"


def _archived_inputs(commit: str, destination: Path) -> Path:
    assert len(commit) == 40 and all(character in "0123456789abcdef" for character in commit)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit, "src", "scripts", "configs"],
        cwd=ROOT, check=True, capture_output=True, timeout=30,
    )
    destination = windows_io_path(destination)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as contents:
        for member in contents:
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert member.isdir() or member.isfile()
            if member.isfile():
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = contents.extractfile(member)
                assert source is not None
                with source:
                    target.write_bytes(source.read())
    return destination


@pytest.mark.integration
def test_private_v3_source_failure_matches_tracked_closeout(tmp_path, monkeypatch) -> None:
    if not all(path.exists() for path in (PREDECESSOR, QUALIFICATION, RUN)):
        pytest.skip("private V3.1 terminal artifacts are not available")
    authorization = load_authorization(RUN / "authorization.json")
    authority = read_document(QUALIFICATION / "authorization.json", "authorization_sha256")
    qualification_root = _archived_inputs(authority["source_commit_ref"], tmp_path / "q")
    cohort_root = _archived_inputs(authorization["source_commit_ref"], tmp_path / "c")
    # Completed replay uses the archived commit, not a fictitious live checkout.
    # This does not bypass the execution gate: no execute/authorize path is called.
    monkeypatch.setattr(
        qualification_module, "inspect_repository_state",
        lambda _: RepositoryExecutionState(
            branch="main", head_commit=authority["source_commit_ref"],
            origin_main_commit=authority["source_commit_ref"], clean=True,
        ),
    )
    qualification = load_verified_qualification(qualification_root, PREDECESSOR, QUALIFICATION)
    plan = build_cohort_plan(
        cohort_root,
        qualification,
        source_commit_ref=authorization["source_commit_ref"],
    )
    rehearsal = rehearse_cohort(cohort_root, plan, qualification)
    receipt = verify_source_cohort(cohort_root, RUN, plan, rehearsal)

    closeout = audit_failed_source_cohort(ROOT, RUN, receipt)

    assert closeout["status"] == "v3_1_source_cohort_closed_semantic_failure"
    assert closeout["closeout_sha256"] == (
        "e62bd2c59c411a2df8dc29b45459c2c5d538674eac560584215c030d2da79d59"
    )
    assert closeout["rate_limited_failure_overlap_count"] == 0
    assert closeout["implementation_mapping_defect_detected"] is False
    assert closeout["serialization_defect_detected"] is False

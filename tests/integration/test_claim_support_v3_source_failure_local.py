"""Replay the private V3.1 source failure when its immutable store is present."""

from __future__ import annotations

from pathlib import Path

import pytest

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

ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT.parent / "memory"
PREDECESSOR = MEMORY / "claim-support-validation-v2-extraction" / "closeout.json"
QUALIFICATION = MEMORY / "claim-support-validation-v3-qualification-v2"
RUN = MEMORY / "claim-support-validation-v3-source-cohort"


@pytest.mark.integration
def test_private_v3_source_failure_matches_tracked_closeout() -> None:
    if not all(path.exists() for path in (PREDECESSOR, QUALIFICATION, RUN)):
        pytest.skip("private V3.1 terminal artifacts are not available")
    authorization = load_authorization(RUN / "authorization.json")
    qualification = load_verified_qualification(ROOT, PREDECESSOR, QUALIFICATION)
    plan = build_cohort_plan(
        ROOT,
        qualification,
        source_commit_ref=authorization["source_commit_ref"],
    )
    rehearsal = rehearse_cohort(ROOT, plan, qualification)
    receipt = verify_source_cohort(ROOT, RUN, plan, rehearsal)

    closeout = audit_failed_source_cohort(ROOT, RUN, receipt)

    assert closeout["status"] == "v3_1_source_cohort_closed_semantic_failure"
    assert closeout["closeout_sha256"] == (
        "e62bd2c59c411a2df8dc29b45459c2c5d538674eac560584215c030d2da79d59"
    )
    assert closeout["rate_limited_failure_overlap_count"] == 0
    assert closeout["implementation_mapping_defect_detected"] is False
    assert closeout["serialization_defect_detected"] is False

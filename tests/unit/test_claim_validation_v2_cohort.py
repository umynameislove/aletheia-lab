"""Contract tests for V2 cohort planning and one-use authorization."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    ClaimValidationV2CohortError,
    _request_projections,
    build_cohort_authorization,
    build_cohort_plan,
    build_cohort_preflight,
    checked_cohort_run_directory,
    publish_cohort_authorization,
    rehearse_cohort,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_contracts import (
    V2CohortExecutionPlan,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    PROVIDER_VARIANTS,
    V2QualificationOutcome,
    V2QualificationReceipt,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def _state(*, clean: bool = True, branch: str = "main") -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch=branch,
        head_commit=COMMIT,
        origin_main_commit=COMMIT,
        clean=clean,
    )


def _qualification() -> V2QualificationReceipt:
    outcomes = []
    for index, variant in enumerate(PROVIDER_VARIANTS, start=1):
        payload = {
            "variant": variant,
            "qualification_request_sha256": f"{index:064x}",
            "gateway_request_identity_sha256": f"{index + 10:064x}",
            "gateway_status": "parsed",
            "attempt_count": 1,
            "first_witness_accepted": True,
            "issue_sha256": None,
        }
        outcomes.append(
            V2QualificationOutcome.model_validate(
                {
                    **payload,
                    "outcome_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    payload = {
        "schema_version": "claim-support-validation-v2-qualification-receipt/v1",
        "status": "claim_support_validation_v2_qualification_passed",
        "authorization_sha256": "1" * 64,
        "plan_sha256": "2" * 64,
        "rehearsal_sha256": "3" * 64,
        "amendment_sha256": "4" * 64,
        "expressiveness_review_sha256": "5" * 64,
        "source_commit_ref": "6" * 40,
        "terminal_store_sha256": "7" * 64,
        "terminal_request_count": 7,
        "parsed_count": 7,
        "first_witness_accepted_count": 7,
        "technical_failure_count": 0,
        "semantic_validation_failure_count": 0,
        "provider_attempt_count": 7,
        "gateway_status_counts": {"parsed": 7},
        "outcomes": tuple(item.model_dump(mode="json") for item in outcomes),
        "synthetic_only": True,
        "admitted_to_corpus": False,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "full_cohort_authorization_unlocked": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2QualificationReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def test_plan_freezes_exact_balanced_census_and_conservative_cost() -> None:
    first = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=_qualification()
    )
    second = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=_qualification()
    )

    assert first == second
    assert first.diagnosis_request_count == 360
    assert first.model_request_count == 315
    assert first.deterministic_request_count == 45
    assert len(set(first.request_projection_sha256s)) == 360
    assert first.exact_message_input_token_count > 0
    assert first.exact_response_schema_token_count > 0
    assert first.provider_billed_input_tokens_known is False
    assert first.estimated_upper_cost_usd < 36.09
    assert first.provider_calls_executed is False
    assert first.relation_execution_authorized is False


def test_projection_routes_and_amended_provider_contracts_are_exact() -> None:
    projections = _request_projections(ROOT)

    assert tuple(item.sequence for item in projections) == tuple(range(1, 361))
    assert {item.schedule_round for item in projections} == set(range(1, 16))
    model = tuple(item for item in projections if item.execution_route == "model_gateway")
    local = tuple(item for item in projections if item.execution_route == "deterministic_local")
    assert len(model) == 315
    assert len(local) == 45
    assert all(item.maximum_output_tokens == 2048 for item in model)
    assert all(item.maximum_attempts == 2 for item in model)
    assert all(item.prompt_sha256 is not None for item in model)
    assert all(item.response_schema_sha256 is not None for item in model)
    assert all(item.maximum_output_tokens == 0 for item in local)
    assert all(item.prompt_sha256 is None for item in local)


def test_plan_and_projection_tampering_fail_validation() -> None:
    plan = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=_qualification()
    )
    changed = plan.model_dump(mode="python")
    changed["model_request_count"] = 314
    with pytest.raises(ValidationError):
        V2CohortExecutionPlan.model_validate(changed)

    changed = plan.model_dump(mode="python")
    changed["estimated_upper_cost_usd"] += 0.01
    with pytest.raises(ValidationError):
        V2CohortExecutionPlan.model_validate(changed)


def test_authorization_requires_clean_main_and_sufficient_ceiling(
    tmp_path: Path,
) -> None:
    qualification = _qualification()
    plan = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=qualification
    )
    rehearsal = rehearse_cohort(ROOT, plan, qualification)
    run_dir = checked_cohort_run_directory(ROOT, tmp_path / "cohort")

    with pytest.raises(ClaimValidationV2CohortError, match="clean synchronized"):
        build_cohort_authorization(
            plan,
            rehearsal,
            repository_state=_state(clean=False),
            run_dir=run_dir,
            authorized_at="2026-09-10T00:00:00Z",
            operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
        )
    with pytest.raises(ClaimValidationV2CohortError, match="clean synchronized"):
        build_cohort_authorization(
            plan,
            rehearsal,
            repository_state=_state(),
            run_dir=run_dir,
            authorized_at="2026-09-10T00:00:00Z",
            operator_cost_ceiling_usd=plan.estimated_upper_cost_usd - 0.01,
        )

    authorization = build_cohort_authorization(
        plan,
        rehearsal,
        repository_state=_state(),
        run_dir=run_dir,
        authorized_at="2026-09-10T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    run_dir.mkdir()
    assert publish_cohort_authorization(run_dir / "authorization.json", authorization) == (
        "created"
    )
    assert authorization.execution_phase == "v2_diagnosis_cohort"
    assert authorization.registered_attempts == 1
    assert authorization.credential_stored is False


def test_preflight_fails_closed_without_authorization_credential_or_clean_main(
    tmp_path: Path,
) -> None:
    qualification = _qualification()
    plan = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=qualification
    )
    rehearsal = rehearse_cohort(ROOT, plan, qualification)
    preflight = build_cohort_preflight(
        plan,
        rehearsal,
        repository_state=_state(clean=False, branch="feature"),
        credential_present=False,
        authorization=None,
        run_dir=checked_cohort_run_directory(ROOT, tmp_path / "cohort"),
    )

    assert preflight.status == "claim_support_validation_v2_cohort_live_blocked"
    assert preflight.live_blockers == (
        "authorization_pending",
        "credential_missing",
        "repository_not_clean_synchronized_main",
    )
    assert preflight.provider_calls_executed is False
    assert preflight.claims_materialized is False


def test_destination_must_be_private_and_create_only(tmp_path: Path) -> None:
    with pytest.raises(ClaimValidationV2CohortError, match="outside"):
        checked_cohort_run_directory(ROOT, ROOT / "private-cohort")

    run_dir = tmp_path / "cohort"
    run_dir.mkdir()
    (run_dir / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ClaimValidationV2CohortError, match="unknown"):
        checked_cohort_run_directory(ROOT, run_dir)

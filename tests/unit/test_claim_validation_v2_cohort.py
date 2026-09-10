"""Contract tests for V2 cohort planning and one-use authorization."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation import (
    claim_validation_v2_cohort_execution as cohort_execution,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import PreparedClaimCorpusRequest
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
    RECEIPT_SCHEMA_VERSION,
    V2CohortAuthorization,
    V2CohortExecutionPlan,
    V2CohortReceipt,
    V2CohortRehearsal,
    V2CohortRequestProjection,
    V2CohortTerminalOutcome,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_execution import (
    V2DeterministicB0Adapter,
    build_cohort_lease,
    build_v2_cohort_gateway_requests,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    PROVIDER_VARIANTS,
    V2QualificationOutcome,
    V2QualificationReceipt,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    build_v2_runtime_manifest,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import ProviderCall

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


@pytest.fixture(scope="module")
def qualification() -> V2QualificationReceipt:
    return _qualification()


@pytest.fixture(scope="module")
def plan(qualification: V2QualificationReceipt) -> V2CohortExecutionPlan:
    return build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=qualification
    )


@pytest.fixture(scope="module")
def rehearsal(
    plan: V2CohortExecutionPlan,
    qualification: V2QualificationReceipt,
) -> V2CohortRehearsal:
    return rehearse_cohort(ROOT, plan, qualification)


@pytest.fixture(scope="module")
def projections() -> tuple[V2CohortRequestProjection, ...]:
    return _request_projections(ROOT)


@pytest.fixture(scope="module")
def authorized_prepared(
    tmp_path_factory: pytest.TempPathFactory,
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
) -> tuple[V2CohortAuthorization, tuple[PreparedClaimCorpusRequest, ...]]:
    run_dir = checked_cohort_run_directory(
        ROOT, tmp_path_factory.mktemp("v2-cohort")
    )
    authorization = build_cohort_authorization(
        plan,
        rehearsal,
        repository_state=_state(),
        run_dir=run_dir,
        authorized_at="2026-09-10T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    return authorization, build_v2_cohort_gateway_requests(ROOT, plan, authorization)


def test_plan_freezes_exact_balanced_census_and_conservative_cost(
    plan: V2CohortExecutionPlan,
    qualification: V2QualificationReceipt,
) -> None:
    second = build_cohort_plan(
        ROOT, source_commit_ref=COMMIT, qualification_receipt=qualification
    )

    assert plan == second
    assert plan.diagnosis_request_count == 360
    assert plan.model_request_count == 315
    assert plan.deterministic_request_count == 45
    assert len(set(plan.request_projection_sha256s)) == 360
    assert plan.exact_message_input_token_count > 0
    assert plan.exact_response_schema_token_count > 0
    assert plan.provider_billed_input_tokens_known is False
    assert plan.estimated_upper_cost_usd < 36.09
    assert plan.provider_calls_executed is False
    assert plan.relation_execution_authorized is False


def test_projection_routes_and_amended_provider_contracts_are_exact(
    projections: tuple[V2CohortRequestProjection, ...],
) -> None:
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


def test_plan_and_projection_tampering_fail_validation(
    plan: V2CohortExecutionPlan,
) -> None:
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
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
) -> None:
    run_dir = checked_cohort_run_directory(ROOT, tmp_path / "cohort")

    assert rehearsal.balanced_round_count == 15
    assert rehearsal.exact_request_projections_rebuilt is True

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
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
) -> None:
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


def test_gateway_requests_exactly_bind_the_authorized_v2_census(
    authorized_prepared: tuple[
        V2CohortAuthorization, tuple[PreparedClaimCorpusRequest, ...]
    ],
) -> None:
    authorization, prepared = authorized_prepared
    manifest = build_v2_runtime_manifest(ROOT)

    assert tuple(item.request_sha256 for item in prepared) == tuple(
        item.v2_request_sha256 for item in manifest.diagnosis_schedule
    )
    assert sum(item.route == "model_gateway" for item in prepared) == 315
    assert sum(item.route == "deterministic_local" for item in prepared) == 45
    assert len(
        {item.request.initial_attempt.request_identity_sha256 for item in prepared}
    ) == 360
    assert all(item.request.runtime_policy.max_attempts == 2 for item in prepared if item.route == "model_gateway")
    assert all(item.request.runtime_policy.max_attempts == 1 for item in prepared if item.route == "deterministic_local")
    assert all(
        "mechanism" not in item.request.context.model_payload()
        and "evidence_condition" not in item.request.context.model_payload()
        and "family_id" not in item.request.context.model_payload()
        for item in prepared
    )
    assert build_cohort_lease(authorization).registered_attempts == 1


def test_deterministic_b0_emits_only_the_registered_measurement_witness(
    authorized_prepared: tuple[
        V2CohortAuthorization, tuple[PreparedClaimCorpusRequest, ...]
    ],
) -> None:
    _, prepared = authorized_prepared
    local = next(item for item in prepared if item.route == "deterministic_local")
    request = local.request
    attempt = request.initial_attempt
    call = ProviderCall(
        request_identity_sha256=attempt.request_identity_sha256,
        attempt_id=attempt.attempt_id,
        attempt_identity_sha256=attempt.attempt_identity_sha256,
        attempt_ordinal=1,
        context_sha256=attempt.context_sha256,
        prompt_sha256=attempt.prompt_sha256,
        response_schema_sha256=attempt.response_schema_sha256,
        context_json=canonical_execution_json(request.context.model_payload()),
        prompt_text=request.prompt_text,
        response_schema_json=request.response_schema_json,
        runtime_policy=request.runtime_policy,
    )
    envelope = V2DeterministicB0Adapter(local).invoke(call)
    payload = json.loads(envelope.raw_response.content.decode("utf-8"))

    assert payload["schema_version"] == "diagnosis-provider-output/2"
    assert payload["result"]["output_status"] == "completed"
    assert len(payload["result"]["atomic_claims"]) == 1
    assert envelope.usage.input_tokens == 0
    assert envelope.usage.output_tokens == 0


def test_receipt_builder_accepts_all_strict_terminal_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
    authorized_prepared: tuple[
        V2CohortAuthorization, tuple[PreparedClaimCorpusRequest, ...]
    ],
) -> None:
    authorization, prepared = authorized_prepared
    manifest = build_v2_runtime_manifest(ROOT)

    class _Reader:
        def terminal_inventory(self, _identity: str) -> SimpleNamespace:
            return SimpleNamespace(
                gateway_status="parsed",
                parsed_response_sha256="8" * 64,
                issue_sha256=None,
            )

        def terminal_attempt_records(
            self, _identity: str
        ) -> tuple[SimpleNamespace, ...]:
            return (SimpleNamespace(provider_failure_category=None, usage=None),)

    monkeypatch.setattr(
        cohort_execution,
        "verified_complete_claim_corpus_store_sha256",
        lambda *_args: "9" * 64,
    )
    monkeypatch.setattr(cohort_execution, "_reader", lambda *_args: _Reader())
    monkeypatch.setattr(
        cohort_execution,
        "_usage_census",
        lambda *_args: (False, None, None, None),
    )

    receipt = cohort_execution.build_cohort_receipt(
        manifest,
        plan,
        rehearsal,
        authorization,
        prepared,
        tmp_path / "store",
    )

    assert receipt.parsed_count == 360
    assert receipt.provider_attempt_count == 315
    assert receipt.technical_admission_passed is True


def test_terminal_receipt_is_fail_closed_and_content_addressed() -> None:
    manifest = build_v2_runtime_manifest(ROOT)
    outcomes = []
    for scheduled in manifest.diagnosis_schedule:
        payload: dict[str, object] = {
            "sequence": scheduled.sequence,
            "schedule_round": scheduled.schedule_round,
            "v2_request_sha256": scheduled.v2_request_sha256,
            "source_request_sha256": scheduled.source_request_sha256,
            "gateway_request_identity_sha256": f"{scheduled.sequence:064x}",
            "mechanism": scheduled.mechanism,
            "evidence_condition": scheduled.evidence_condition,
            "variant": scheduled.variant,
            "execution_route": scheduled.execution_route,
            "gateway_status": "parsed",
            "attempt_count": 1,
            "provider_failure_categories": (),
            "parsed_response_sha256": f"{scheduled.sequence + 360:064x}",
            "issue_sha256": None,
        }
        outcomes.append(
            V2CohortTerminalOutcome.model_validate(
                {
                    **payload,
                    "outcome_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    payload = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "claim_support_validation_v2_cohort_complete_technical_admission_passed",
        "authorization_sha256": "1" * 64,
        "plan_sha256": "2" * 64,
        "rehearsal_sha256": "3" * 64,
        "qualification_receipt_sha256": "4" * 64,
        "protocol_sha256": manifest.protocol_sha256,
        "runtime_manifest_sha256": manifest.manifest_sha256,
        "source_commit_ref": "5" * 40,
        "terminal_store_sha256": "6" * 64,
        "terminal_request_count": 360,
        "parsed_count": 360,
        "technical_failure_count": 0,
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "provider_attempt_count": 315,
        "technical_attempt_count": 360,
        "gateway_status_counts": {"parsed": 360},
        "provider_failure_category_counts": {},
        "provider_usage_complete": False,
        "observed_provider_input_token_count": None,
        "observed_provider_output_token_count": None,
        "observed_provider_total_token_count": None,
        "outcomes": tuple(item.model_dump(mode="python") for item in outcomes),
        "technical_admission_blockers": (),
        "technical_admission_passed": True,
        "missingness_exchangeability_established": False,
        "relation_execution_unlocked": True,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    receipt = V2CohortReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )
    changed = receipt.model_dump(mode="python")
    changed["relation_execution_unlocked"] = False

    assert receipt.technical_admission_passed is True
    assert receipt.missingness_exchangeability_established is False
    with pytest.raises(ValidationError):
        V2CohortReceipt.model_validate(changed)

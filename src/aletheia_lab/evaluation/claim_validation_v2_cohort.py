"""Exact planning and one-use authorization for the V2 diagnosis cohort.

This boundary verifies the completed seven-request qualification and freezes
the 360-request cohort without executing either the model or deterministic B0.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Final

import tiktoken
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    provider_response_schema_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_validation_v2_cohort_contracts import (
    AUTHORIZATION_SCHEMA_VERSION,
    DETERMINISTIC_REQUEST_COUNT,
    DIAGNOSIS_REQUEST_COUNT,
    INPUT_USD_PER_MILLION,
    MAXIMUM_OUTPUT_TOKENS,
    MAXIMUM_PROVIDER_ATTEMPTS,
    MODEL_REQUEST_COUNT,
    OUTPUT_USD_PER_MILLION,
    PLAN_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
    REHEARSAL_SCHEMA_VERSION,
    RESPONSE_FORMAT_OVERHEAD_ALLOWANCE,
    ClaimValidationV2CohortError,
    V2CohortAuthorization,
    V2CohortExecutionPlan,
    V2CohortPreflight,
    V2CohortRehearsal,
    V2CohortRequestProjection,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    TOKENIZER_VERSION,
    _chat_tokens,
    _load_frozen_inputs,
    checked_qualification_run_directory,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    V2QualificationReceipt,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_execution import (
    verify_completed_qualification,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
    build_v2_runtime_readiness,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway.recovery_transport import wire_schema_json
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_ALLOWED_RUN_ENTRIES: Final = frozenset(
    {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
)


def load_verified_qualification(
    root: Path, qualification_run_dir: Path
) -> V2QualificationReceipt:
    """Rebuild the historical qualification receipt from its immutable store."""

    root = root.resolve()
    run_dir = checked_qualification_run_directory(root, qualification_run_dir)
    try:
        claimed = V2QualificationReceipt.model_validate_json(
            (run_dir / "receipt.json").read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2CohortError(
            "qualification receipt is unavailable or invalid"
        ) from exc
    historical_state = RepositoryExecutionState(
        branch="main",
        head_commit=claimed.source_commit_ref,
        origin_main_commit=claimed.source_commit_ref,
        clean=True,
    )
    try:
        verified = verify_completed_qualification(
            root,
            repository_state=historical_state,
            run_dir=run_dir,
        )
    except (OSError, ValueError) as exc:
        raise ClaimValidationV2CohortError(
            "qualification receipt did not verify against its immutable store"
        ) from exc
    if (
        verified != claimed
        or verified.status != "claim_support_validation_v2_qualification_passed"
        or not verified.full_cohort_authorization_unlocked
        or verified.parsed_count != 7
        or verified.first_witness_accepted_count != 7
        or verified.technical_failure_count != 0
        or verified.semantic_validation_failure_count != 0
        or not verified.synthetic_only
        or verified.admitted_to_corpus
    ):
        raise ClaimValidationV2CohortError(
            "qualification result does not unlock the V2 cohort"
        )
    return verified


def _request_projections(root: Path) -> tuple[V2CohortRequestProjection, ...]:
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ClaimValidationV2CohortError(
            "tiktoken version differs from the cohort contract"
        )
    encoding = tiktoken.get_encoding("o200k_base")
    amendment, _ = _load_frozen_inputs(root)
    manifest = build_v2_runtime_manifest(root)
    _, census, evidence = _load_inputs(root)
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    source_requests = {item.request_sha256: item for item in census.primary_requests}
    bindings = {
        (item.family_id, item.evidence_condition): item for item in evidence.bindings
    }
    result = []
    for scheduled in manifest.diagnosis_schedule:
        source = source_requests.get(scheduled.source_request_sha256)
        binding = bindings.get((scheduled.family_id, scheduled.evidence_condition))
        if (
            source is None
            or binding is None
            or source.variant != scheduled.variant
            or source.family_id != scheduled.family_id
            or source.evidence_condition != scheduled.evidence_condition
            or binding.visible_context.context_sha256
            != scheduled.visible_context_sha256
        ):
            raise ClaimValidationV2CohortError(
                "V2 cohort schedule lost a source request or evidence binding"
            )
        local = scheduled.execution_route == "deterministic_local"
        prompt_sha256 = None
        response_schema_sha256 = None
        message_tokens = 0
        schema_tokens = 0
        maximum_output_tokens = 0
        maximum_attempts = 1
        if not local:
            prompt = "\n\n".join(
                (
                    freeze.prompt_policies[scheduled.variant].instruction_contract,
                    amendment.shared_instruction_suffix,
                )
            )
            schema_json = canonical_project_json(
                provider_response_schema_v2(
                    tuple(
                        item.evidence_id for item in binding.visible_context.items
                    )
                )
            )
            context_json = canonical_execution_json(
                binding.visible_context.model_payload()
            )
            prompt_sha256 = content_sha256(prompt.encode("utf-8"))
            response_schema_sha256 = content_sha256(schema_json.encode("utf-8"))
            message_tokens = _chat_tokens(encoding, prompt, context_json)
            schema_tokens = len(encoding.encode(wire_schema_json(schema_json)))
            maximum_output_tokens = MAXIMUM_OUTPUT_TOKENS
            maximum_attempts = MAXIMUM_PROVIDER_ATTEMPTS
        payload: dict[str, object] = {
            "sequence": scheduled.sequence,
            "schedule_round": scheduled.schedule_round,
            "v2_request_sha256": scheduled.v2_request_sha256,
            "source_request_sha256": scheduled.source_request_sha256,
            "variant": scheduled.variant,
            "execution_route": scheduled.execution_route,
            "visible_context_sha256": scheduled.visible_context_sha256,
            "prompt_sha256": prompt_sha256,
            "response_schema_sha256": response_schema_sha256,
            "exact_message_input_token_count": message_tokens,
            "exact_response_schema_token_count": schema_tokens,
            "maximum_output_tokens": maximum_output_tokens,
            "maximum_attempts": maximum_attempts,
        }
        result.append(
            V2CohortRequestProjection.model_validate(
                {
                    **payload,
                    "projection_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    projections = tuple(result)
    if (
        tuple(item.sequence for item in projections)
        != tuple(range(1, DIAGNOSIS_REQUEST_COUNT + 1))
        or sum(item.execution_route == "model_gateway" for item in projections)
        != MODEL_REQUEST_COUNT
        or sum(item.execution_route == "deterministic_local" for item in projections)
        != DETERMINISTIC_REQUEST_COUNT
    ):
        raise ClaimValidationV2CohortError("V2 cohort request projection census differs")
    return projections


def build_cohort_plan(
    root: Path,
    *,
    source_commit_ref: str,
    qualification_receipt: V2QualificationReceipt,
) -> V2CohortExecutionPlan:
    """Freeze the exact cohort census and conservative provider cost ceiling."""

    root = root.resolve()
    qualification = V2QualificationReceipt.model_validate(
        qualification_receipt.model_dump(mode="python")
    )
    if (
        qualification.status
        != "claim_support_validation_v2_qualification_passed"
        or not qualification.full_cohort_authorization_unlocked
    ):
        raise ClaimValidationV2CohortError(
            "V2 cohort planning requires the passed qualification receipt"
        )
    amendment, review = _load_frozen_inputs(root)
    manifest = build_v2_runtime_manifest(root)
    readiness = build_v2_runtime_readiness(manifest)
    projections = _request_projections(root)
    projection_hashes = tuple(item.projection_sha256 for item in projections)
    request_hashes = tuple(item.v2_request_sha256 for item in projections)
    message_tokens = sum(item.exact_message_input_token_count for item in projections)
    schema_tokens = sum(
        item.exact_response_schema_token_count for item in projections
    )
    conservative_input = MAXIMUM_PROVIDER_ATTEMPTS * (
        message_tokens
        + schema_tokens
        + MODEL_REQUEST_COUNT * RESPONSE_FORMAT_OVERHEAD_ALLOWANCE
    )
    output_ceiling = (
        MODEL_REQUEST_COUNT * MAXIMUM_OUTPUT_TOKENS * MAXIMUM_PROVIDER_ATTEMPTS
    )
    estimated_cost = round(
        conservative_input * INPUT_USD_PER_MILLION / 1_000_000
        + output_ceiling * OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_commit_ref": source_commit_ref,
        "protocol_sha256": manifest.protocol_sha256,
        "runtime_manifest_sha256": manifest.manifest_sha256,
        "runtime_readiness_sha256": readiness.readiness_sha256,
        "amendment_sha256": amendment.amendment_sha256,
        "expressiveness_review_sha256": review.review_sha256,
        "qualification_authorization_sha256": qualification.authorization_sha256,
        "qualification_receipt_sha256": qualification.receipt_sha256,
        "qualification_terminal_store_sha256": qualification.terminal_store_sha256,
        "qualification_source_commit_ref": qualification.source_commit_ref,
        "qualification_request_count": 7,
        "qualification_parsed_count": qualification.parsed_count,
        "qualification_first_witness_accepted_count": (
            qualification.first_witness_accepted_count
        ),
        "diagnosis_request_count": DIAGNOSIS_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "request_census_sha256": canonical_execution_sha256(request_hashes),
        "request_projection_census_sha256": canonical_execution_sha256(
            projection_hashes
        ),
        "request_projection_sha256s": projection_hashes,
        "model": "gpt-4.1",
        "model_snapshot": "gpt-4.1-2025-04-14",
        "maximum_output_tokens_per_model_request": MAXIMUM_OUTPUT_TOKENS,
        "maximum_provider_attempts_per_request": MAXIMUM_PROVIDER_ATTEMPTS,
        "minimum_provider_interval_ms": 1000,
        "retry_initial_backoff_ms": 5000,
        "retry_backoff_multiplier": 2,
        "retry_backoff_ceiling_ms": 60000,
        "retry_after_ceiling_ms": 60000,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_encoding": "o200k_base",
        "exact_message_input_token_count": message_tokens,
        "exact_response_schema_token_count": schema_tokens,
        "provider_overhead_allowance_tokens_per_call": (
            RESPONSE_FORMAT_OVERHEAD_ALLOWANCE
        ),
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output_ceiling,
        "provider_billed_input_tokens_known": False,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "estimated_upper_cost_usd": estimated_cost,
        "relation_execution_authorized": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2CohortExecutionPlan.model_validate(
        {**payload, "plan_sha256": canonical_execution_sha256(payload)}
    )


def rehearse_cohort(
    root: Path,
    plan: V2CohortExecutionPlan,
    qualification_receipt: V2QualificationReceipt,
) -> V2CohortRehearsal:
    """Rebuild every request projection without provider access."""

    expected = build_cohort_plan(
        root,
        source_commit_ref=plan.source_commit_ref,
        qualification_receipt=qualification_receipt,
    )
    if expected != plan:
        raise ClaimValidationV2CohortError("V2 cohort rehearsal differs from plan")
    manifest = build_v2_runtime_manifest(root)
    payload: dict[str, object] = {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "status": "claim_support_validation_v2_cohort_rehearsal_passed",
        "plan_sha256": plan.plan_sha256,
        "qualification_receipt_sha256": plan.qualification_receipt_sha256,
        "request_census_sha256": plan.request_census_sha256,
        "request_projection_census_sha256": plan.request_projection_census_sha256,
        "diagnosis_request_count": DIAGNOSIS_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "balanced_round_count": len(
            {item.schedule_round for item in manifest.diagnosis_schedule}
        ),
        "exact_request_projections_rebuilt": True,
        "qualification_pass_receipt_bound": True,
        "all_model_requests_share_budget": (
            expected.maximum_output_tokens_per_model_request
            == MAXIMUM_OUTPUT_TOKENS
            and expected.maximum_provider_attempts_per_request
            == MAXIMUM_PROVIDER_ATTEMPTS
        ),
        "all_model_prompts_bind_amendment": True,
        "all_response_schemas_bind_visible_evidence": True,
        "provider_calls_executed": False,
        "authorization_created": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2CohortRehearsal.model_validate(
        {**payload, "rehearsal_sha256": canonical_execution_sha256(payload)}
    )


def checked_cohort_run_directory(root: Path, run_dir: Path) -> Path:
    root = root.resolve()
    expanded = run_dir.expanduser()
    if expanded.is_symlink():
        raise ClaimValidationV2CohortError(
            "V2 cohort destination must not be a symbolic link"
        )
    run = expanded.resolve()
    if run == root or run.is_relative_to(root):
        raise ClaimValidationV2CohortError(
            "V2 cohort run directory must remain outside the repository"
        )
    if run.exists() and (run.is_symlink() or not run.is_dir()):
        raise ClaimValidationV2CohortError(
            "V2 cohort destination is not a real directory"
        )
    if run.exists() and any(
        item.name not in _ALLOWED_RUN_ENTRIES or item.is_symlink()
        for item in run.iterdir()
    ):
        raise ClaimValidationV2CohortError(
            "V2 cohort run contains unknown or linked artifacts"
        )
    return run


def _destination_sha256(run_dir: Path) -> str:
    return canonical_execution_sha256(
        {"private_v2_diagnosis_cohort_run_directory": run_dir.as_posix()}
    )


def build_cohort_authorization(
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    authorized_at: str,
    operator_cost_ceiling_usd: float,
) -> V2CohortAuthorization:
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
        or rehearsal.plan_sha256 != plan.plan_sha256
        or rehearsal.qualification_receipt_sha256
        != plan.qualification_receipt_sha256
        or operator_cost_ceiling_usd < plan.estimated_upper_cost_usd
    ):
        raise ClaimValidationV2CohortError(
            "authorization requires the rehearsed cohort on clean synchronized main"
        )
    payload: dict[str, object] = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorized_at": authorized_at,
        "source_commit_ref": plan.source_commit_ref,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "qualification_receipt_sha256": plan.qualification_receipt_sha256,
        "request_census_sha256": plan.request_census_sha256,
        "request_projection_census_sha256": plan.request_projection_census_sha256,
        "diagnosis_request_count": DIAGNOSIS_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "destination_sha256": _destination_sha256(run_dir),
        "registered_attempts": 1,
        "execution_phase": "v2_diagnosis_cohort",
        "relation_execution_authorized": False,
        "credential_stored": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    digest = canonical_execution_sha256(payload)
    return V2CohortAuthorization.model_validate(
        {
            **payload,
            "authorization_ref": f"ev-{digest}",
            "authorization_sha256": digest,
        }
    )


def load_cohort_authorization(path: Path) -> V2CohortAuthorization:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("authorization is not a regular file")
        return V2CohortAuthorization.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2CohortError(
            "V2 cohort authorization is unavailable or invalid"
        ) from exc


def validate_cohort_authorization(
    authorization: V2CohortAuthorization,
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> V2CohortAuthorization:
    checked = V2CohortAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != checked.source_commit_ref
        or checked.plan_sha256 != plan.plan_sha256
        or checked.rehearsal_sha256 != rehearsal.rehearsal_sha256
        or checked.qualification_receipt_sha256
        != plan.qualification_receipt_sha256
        or checked.request_census_sha256 != plan.request_census_sha256
        or checked.request_projection_census_sha256
        != plan.request_projection_census_sha256
        or checked.estimated_upper_cost_usd != plan.estimated_upper_cost_usd
        or checked.destination_sha256 != _destination_sha256(run_dir)
    ):
        raise ClaimValidationV2CohortError(
            "V2 cohort authorization differs from current verified inputs"
        )
    return checked


def build_cohort_preflight(
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
    authorization: V2CohortAuthorization | None,
    run_dir: Path,
) -> V2CohortPreflight:
    blockers: list[str] = []
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
    ):
        blockers.append("repository_not_clean_synchronized_main")
    if not credential_present:
        blockers.append("credential_missing")
    if plan.qualification_parsed_count != 7:
        blockers.append("qualification_not_verified")
    if authorization is None:
        blockers.append("authorization_pending")
    else:
        validate_cohort_authorization(
            authorization,
            plan,
            rehearsal,
            repository_state=repository_state,
            run_dir=run_dir,
        )
    ordered = tuple(sorted(blockers))
    payload: dict[str, object] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": (
            "claim_support_validation_v2_cohort_live_ready"
            if not ordered
            else "claim_support_validation_v2_cohort_live_blocked"
        ),
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "qualification_receipt_sha256": plan.qualification_receipt_sha256,
        "source_commit_ref": plan.source_commit_ref,
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_present": credential_present,
        "diagnosis_request_count": DIAGNOSIS_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "exact_message_input_token_count": plan.exact_message_input_token_count,
        "exact_response_schema_token_count": (
            plan.exact_response_schema_token_count
        ),
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": (
            authorization.operator_cost_ceiling_usd if authorization else None
        ),
        "live_blockers": ordered,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2CohortPreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def publish_cohort_authorization(path: Path, authorization: V2CohortAuthorization) -> str:
    payload = authorization.model_dump(mode="json")
    return publish_immutable_file(
        path,
        (canonical_project_json(payload) + "\n").encode("utf-8"),
    )


def publish_cohort_result(path: Path, model: object) -> str:
    """Publish one validated cohort artifact without allowing replacement."""

    if not hasattr(model, "model_dump"):
        raise TypeError("cohort publication requires a validated model")
    payload = model.model_dump(mode="json")
    return publish_immutable_file(
        path,
        (canonical_project_json(payload) + "\n").encode("utf-8"),
    )


__all__ = [
    "ClaimValidationV2CohortError",
    "V2CohortAuthorization",
    "V2CohortExecutionPlan",
    "V2CohortPreflight",
    "V2CohortRehearsal",
    "V2CohortRequestProjection",
    "build_cohort_authorization",
    "build_cohort_plan",
    "build_cohort_preflight",
    "checked_cohort_run_directory",
    "load_cohort_authorization",
    "load_verified_qualification",
    "publish_cohort_authorization",
    "publish_cohort_result",
    "rehearse_cohort",
    "validate_cohort_authorization",
]

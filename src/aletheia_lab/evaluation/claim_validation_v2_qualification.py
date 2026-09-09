"""Authorized seven-request transport and expressiveness qualification for V2.

The run is synthetic and excluded from every scientific corpus and estimand.
It gates the later 360-request authorization by requiring every provider-backed
variant to parse and satisfy the prospectively frozen first-witness contract.
"""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import tiktoken
from pydantic import ValidationError

from aletheia_lab.diagnosis.variant_registry import build_variant_registry
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequest
from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    inspect_repository_state,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    PreparedClaimCorpusRequest,
    _model_policy,
    _opaque,
    _request_authority,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    EXPRESSIVENESS_AMENDMENT_PATH,
    EXPRESSIVENESS_REVIEW_PATH,
    ClaimSupportValidationV2ExpressivenessAmendment,
    ClaimSupportValidationV2ExpressivenessReview,
    ExpressivenessQualificationProbe,
    build_measurement_witness_claim,
    build_v2_expressiveness_amendment,
    build_v2_expressiveness_review,
    select_amended_source_claims,
    verify_tracked_v2_expressiveness,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    AUTHORIZATION_SCHEMA_VERSION,
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    PLAN_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
    PROVIDER_VARIANTS,
    QUALIFICATION_REQUEST_COUNT,
    REHEARSAL_SCHEMA_VERSION,
    RESPONSE_FORMAT_TOKEN_ALLOWANCE,
    ClaimValidationV2QualificationError,
    V2QualificationAuthorization,
    V2QualificationExecutionPlan,
    V2QualificationPreflight,
    V2QualificationRehearsal,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    OpenAIGatewayPolicy,
    RuntimePolicyReference,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json

TOKENIZER_VERSION: Final = "0.14.0"
_ALLOWED_RUN_ENTRIES: Final = frozenset(
    {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
)


def _load_frozen_inputs(
    root: Path,
) -> tuple[
    ClaimSupportValidationV2ExpressivenessAmendment,
    ClaimSupportValidationV2ExpressivenessReview,
]:
    verify_tracked_v2_expressiveness(root)
    try:
        amendment = ClaimSupportValidationV2ExpressivenessAmendment.model_validate_json(
            (root / EXPRESSIVENESS_AMENDMENT_PATH).read_bytes()
        )
        review = ClaimSupportValidationV2ExpressivenessReview.model_validate_json(
            (root / EXPRESSIVENESS_REVIEW_PATH).read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2QualificationError(
            "tracked expressiveness inputs are unavailable or invalid"
        ) from exc
    if (
        amendment != build_v2_expressiveness_amendment(root)
        or review != build_v2_expressiveness_review(root, amendment)
        or review.amendment_sha256 != amendment.amendment_sha256
        or review.next_gate != "separately_authorized_seven_request_live_qualification"
    ):
        raise ClaimValidationV2QualificationError(
            "tracked expressiveness inputs do not open qualification"
        )
    return amendment, review


def _chat_tokens(encoding: tiktoken.Encoding, prompt: str, context_json: str) -> int:
    total = 3
    for role, content in (("system", prompt), ("user", context_json)):
        total += 3 + len(encoding.encode(role)) + len(encoding.encode(content))
    return total


def _openai_policy(root: Path) -> OpenAIGatewayPolicy:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    return OpenAIGatewayPolicy.from_fairness_policy(
        freeze.model_policies["main_llm_v1"]
    ).with_recovery_output_budget()


def _transport_sha256(root: Path) -> str:
    policy = _openai_policy(root)
    return canonical_execution_sha256(
        {
            "openai_gateway_policy": policy.model_dump(mode="json"),
            "minimum_provider_interval_ms": 1000,
            "retry_initial_backoff_ms": 5000,
            "retry_backoff_multiplier": 2,
            "retry_backoff_ceiling_ms": 60000,
            "retry_after_ceiling_ms": 60000,
            "retryable_categories": (
                "connection",
                "http_408",
                "http_409",
                "http_425",
                "rate_limited",
                "server_error",
                "timeout",
            ),
        }
    )


def build_qualification_plan(root: Path, *, source_commit_ref: str) -> V2QualificationExecutionPlan:
    """Build the exact seven-request plan without provider access."""

    root = root.resolve()
    amendment, review = _load_frozen_inputs(root)
    probes = amendment.qualification_probes
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ClaimValidationV2QualificationError(
            "tiktoken version differs from the qualification contract"
        )
    encoding = tiktoken.get_encoding("o200k_base")
    message_tokens = sum(
        _chat_tokens(
            encoding,
            probe.prompt_text,
            canonical_execution_json(probe.context.model_payload()),
        )
        for probe in probes
    )
    request_hashes = tuple(item.qualification_request_sha256 for item in probes)
    conservative_input = 2 * (
        message_tokens + QUALIFICATION_REQUEST_COUNT * RESPONSE_FORMAT_TOKEN_ALLOWANCE
    )
    output_ceiling = QUALIFICATION_REQUEST_COUNT * 2048 * 2
    estimated_cost = round(
        conservative_input * INPUT_USD_PER_MILLION / 1_000_000
        + output_ceiling * OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_commit_ref": source_commit_ref,
        "amendment_sha256": amendment.amendment_sha256,
        "expressiveness_review_sha256": review.review_sha256,
        "parent_protocol_sha256": amendment.parent_protocol_sha256,
        "parent_runtime_manifest_sha256": amendment.parent_runtime_manifest_sha256,
        "request_census_sha256": canonical_execution_sha256(request_hashes),
        "qualification_request_sha256s": request_hashes,
        "variants": PROVIDER_VARIANTS,
        "request_count": QUALIFICATION_REQUEST_COUNT,
        "model": "gpt-4.1",
        "model_snapshot": "gpt-4.1-2025-04-14",
        "maximum_output_tokens_per_request": 2048,
        "maximum_provider_attempts_per_request": 2,
        "minimum_provider_interval_ms": 1000,
        "retry_initial_backoff_ms": 5000,
        "retry_backoff_multiplier": 2,
        "retry_backoff_ceiling_ms": 60000,
        "retry_after_ceiling_ms": 60000,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_encoding": "o200k_base",
        "response_format_token_allowance_per_request": RESPONSE_FORMAT_TOKEN_ALLOWANCE,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "exact_message_input_token_count": message_tokens,
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output_ceiling,
        "estimated_upper_cost_usd": estimated_cost,
        "transport_sha256": _transport_sha256(root),
        "synthetic_only": True,
        "admitted_to_corpus": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2QualificationExecutionPlan.model_validate(
        {**payload, "plan_sha256": canonical_execution_sha256(payload)}
    )


def _source_requests(
    root: Path, amendment: ClaimSupportValidationV2ExpressivenessAmendment
) -> dict[str, ClaimCorpusRequest]:
    manifest = build_v2_runtime_manifest(root)
    _, census, _ = _load_inputs(root)
    schedule = {item.v2_request_sha256: item for item in manifest.diagnosis_schedule}
    requests = {item.request_sha256: item for item in census.primary_requests}
    result: dict[str, ClaimCorpusRequest] = {}
    for probe in amendment.qualification_probes:
        scheduled = schedule.get(probe.source_schedule_entry_sha256)
        if scheduled is None or scheduled.variant != probe.variant:
            raise ClaimValidationV2QualificationError(
                "qualification probe lost its source schedule binding"
            )
        source = requests.get(scheduled.source_request_sha256)
        if source is None or source.variant != probe.variant:
            raise ClaimValidationV2QualificationError(
                "qualification probe lost its source request binding"
            )
        result[probe.qualification_request_sha256] = source
    if len(result) != QUALIFICATION_REQUEST_COUNT:
        raise ClaimValidationV2QualificationError("qualification source-request census differs")
    return result


def _expected_provider_payload(
    probe: ExpressivenessQualificationProbe,
) -> dict[str, object]:
    claim = build_measurement_witness_claim(probe.context)
    return {
        "schema_version": "diagnosis-provider-output/2",
        "result": {
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_type": claim.claim_type,
                    "claim_text": claim.claim_text,
                    "material_parts": [{"text": item.text} for item in claim.material_parts],
                    "visible_evidence_ids": list(claim.visible_evidence_ids),
                }
            ],
        },
    }


def _first_witness_accepted(
    probe: ExpressivenessQualificationProbe,
    request: ClaimCorpusRequest,
    payload: Mapping[str, object],
    *,
    source_record_sha256: str,
) -> bool:
    try:
        output = normalize_provider_output_v2(
            request,
            payload,
            source_record_sha256=source_record_sha256,
            visible_evidence_ids=tuple(item.evidence_id for item in probe.context.items),
        )
        selected = select_amended_source_claims(output, probe.context)
    except (ValueError, ValidationError):
        return False
    return bool(selected and selected[0] == build_measurement_witness_claim(probe.context))


def rehearse_qualification(
    root: Path, plan: V2QualificationExecutionPlan
) -> V2QualificationRehearsal:
    """Exercise valid and invalid local acceptance paths without provider access."""

    amendment, _ = _load_frozen_inputs(root)
    if (
        plan.amendment_sha256 != amendment.amendment_sha256
        or plan.qualification_request_sha256s
        != tuple(item.qualification_request_sha256 for item in amendment.qualification_probes)
    ):
        raise ClaimValidationV2QualificationError(
            "qualification rehearsal inputs differ from the plan"
        )
    requests = _source_requests(root, amendment)
    exact_accepted = True
    changed_rejected = True
    abstention_rejected = True
    unknown_rejected = True
    for probe in amendment.qualification_probes:
        request = requests[probe.qualification_request_sha256]
        valid = _expected_provider_payload(probe)
        record_sha = canonical_execution_sha256(
            {"purpose": "qualification-rehearsal", "probe": probe.qualification_request_sha256}
        )
        exact_accepted &= _first_witness_accepted(
            probe, request, valid, source_record_sha256=record_sha
        )
        changed = json.loads(json.dumps(valid))
        changed["result"]["atomic_claims"][0]["claim_text"] = "payload.observed.probe_score = 0.8"
        changed_rejected &= not _first_witness_accepted(
            probe, request, changed, source_record_sha256=record_sha
        )
        abstained: dict[str, object] = {
            "schema_version": "diagnosis-provider-output/2",
            "result": {
                "output_status": "abstained",
                "abstention_reason": "Synthetic qualification abstention.",
            },
        }
        abstention_rejected &= not _first_witness_accepted(
            probe, request, abstained, source_record_sha256=record_sha
        )
        unknown = json.loads(json.dumps(valid))
        unknown["result"]["atomic_claims"][0]["visible_evidence_ids"] = ["ev-unknown"]
        unknown_rejected &= not _first_witness_accepted(
            probe, request, unknown, source_record_sha256=record_sha
        )
    if not all((exact_accepted, changed_rejected, abstention_rejected, unknown_rejected)):
        raise ClaimValidationV2QualificationError(
            "qualification acceptance rehearsal did not fail closed"
        )
    payload: dict[str, object] = {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "status": "claim_support_validation_v2_qualification_rehearsal_passed",
        "plan_sha256": plan.plan_sha256,
        "amendment_sha256": amendment.amendment_sha256,
        "request_census_sha256": plan.request_census_sha256,
        "request_count": QUALIFICATION_REQUEST_COUNT,
        "variants": PROVIDER_VARIANTS,
        "all_requests_synthetic_and_excluded": True,
        "all_response_schemas_validated": True,
        "exact_first_witness_accepted": True,
        "changed_witness_rejected": True,
        "abstention_rejected_for_qualification": True,
        "unknown_evidence_rejected": True,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2QualificationRehearsal.model_validate(
        {**payload, "rehearsal_sha256": canonical_execution_sha256(payload)}
    )


def checked_qualification_run_directory(root: Path, run_dir: Path) -> Path:
    root = root.resolve()
    expanded = run_dir.expanduser()
    if expanded.is_symlink():
        raise ClaimValidationV2QualificationError(
            "qualification destination must not be a symbolic link"
        )
    run = expanded.resolve()
    if run == root or run.is_relative_to(root):
        raise ClaimValidationV2QualificationError(
            "qualification run directory must remain outside the repository"
        )
    if run.exists() and (run.is_symlink() or not run.is_dir()):
        raise ClaimValidationV2QualificationError(
            "qualification destination is not a real directory"
        )
    if run.exists() and any(
        item.name not in _ALLOWED_RUN_ENTRIES or item.is_symlink() for item in run.iterdir()
    ):
        raise ClaimValidationV2QualificationError(
            "qualification run contains unknown or linked artifacts"
        )
    return run


def _destination_sha256(run_dir: Path) -> str:
    return canonical_execution_sha256(
        {"private_v2_qualification_run_directory": run_dir.as_posix()}
    )


def build_qualification_authorization(
    plan: V2QualificationExecutionPlan,
    rehearsal: V2QualificationRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    authorized_at: str,
    operator_cost_ceiling_usd: float,
) -> V2QualificationAuthorization:
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
        or rehearsal.plan_sha256 != plan.plan_sha256
        or rehearsal.amendment_sha256 != plan.amendment_sha256
    ):
        raise ClaimValidationV2QualificationError(
            "authorization requires the passed plan on clean synchronized main"
        )
    payload: dict[str, object] = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorized_at": authorized_at,
        "source_commit_ref": plan.source_commit_ref,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "amendment_sha256": plan.amendment_sha256,
        "expressiveness_review_sha256": plan.expressiveness_review_sha256,
        "request_census_sha256": plan.request_census_sha256,
        "request_count": QUALIFICATION_REQUEST_COUNT,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "destination_sha256": _destination_sha256(run_dir),
        "registered_attempts": 1,
        "synthetic_only": True,
        "admitted_to_corpus": False,
        "credential_stored": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    digest = canonical_execution_sha256(payload)
    return V2QualificationAuthorization.model_validate(
        {
            **payload,
            "authorization_ref": f"ev-{digest}",
            "authorization_sha256": digest,
        }
    )


def load_qualification_authorization(path: Path) -> V2QualificationAuthorization:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("authorization is not a regular file")
        return V2QualificationAuthorization.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2QualificationError(
            "qualification authorization is unavailable or invalid"
        ) from exc


def validate_qualification_authorization(
    authorization: V2QualificationAuthorization,
    plan: V2QualificationExecutionPlan,
    rehearsal: V2QualificationRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> V2QualificationAuthorization:
    checked = V2QualificationAuthorization.model_validate(authorization.model_dump(mode="python"))
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != checked.source_commit_ref
        or checked.plan_sha256 != plan.plan_sha256
        or checked.rehearsal_sha256 != rehearsal.rehearsal_sha256
        or checked.amendment_sha256 != plan.amendment_sha256
        or checked.expressiveness_review_sha256 != plan.expressiveness_review_sha256
        or checked.request_census_sha256 != plan.request_census_sha256
        or checked.estimated_upper_cost_usd != plan.estimated_upper_cost_usd
        or checked.destination_sha256 != _destination_sha256(run_dir)
    ):
        raise ClaimValidationV2QualificationError(
            "qualification authorization differs from current inputs"
        )
    return checked


def build_qualification_preflight(
    plan: V2QualificationExecutionPlan,
    rehearsal: V2QualificationRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
    authorization: V2QualificationAuthorization | None,
    run_dir: Path,
) -> V2QualificationPreflight:
    blockers: list[str] = []
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
    ):
        blockers.append("repository_not_clean_synchronized_main")
    if not credential_present:
        blockers.append("credential_missing")
    if authorization is None:
        blockers.append("authorization_pending")
    else:
        validate_qualification_authorization(
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
            "claim_support_validation_v2_qualification_live_ready"
            if not ordered
            else "claim_support_validation_v2_qualification_live_blocked"
        ),
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "source_commit_ref": plan.source_commit_ref,
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_present": credential_present,
        "request_count": QUALIFICATION_REQUEST_COUNT,
        "exact_message_input_token_count": plan.exact_message_input_token_count,
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
    return V2QualificationPreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def build_qualification_gateway_requests(
    root: Path,
    plan: V2QualificationExecutionPlan,
    authorization: V2QualificationAuthorization,
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Bind the authorized synthetic probes to immutable gateway identities."""

    amendment, _ = _load_frozen_inputs(root)
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    policy = _openai_policy(root)
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{plan.amendment_sha256}",
        snapshot_id=f"p3-snapshot-{plan.plan_sha256}",
        manifest_content_sha256=plan.plan_sha256,
        source_commit_ref=authorization.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authorization.authorization_ref,
        provenance_sha256=plan.amendment_sha256,
        created_at=authorization.authorized_at,
        frozen_at=authorization.authorized_at,
        visibility="diagnosis",
    )
    prepared: list[PreparedClaimCorpusRequest] = []
    for probe in amendment.qualification_probes:
        variant = registry.require(probe.variant)
        schema = json.loads(probe.response_schema_json)
        if not isinstance(schema, dict):
            raise ClaimValidationV2QualificationError(
                "qualification response schema is not an object"
            )
        authority = _request_authority(
            registry=registry,
            request_sha256=probe.qualification_request_sha256,
            variant=probe.variant,
            context=probe.context,
            observed_binding_sha256=canonical_execution_sha256(
                {
                    "synthetic_qualification_context": probe.context.context_sha256,
                    "amendment_sha256": amendment.amendment_sha256,
                }
            ),
        )
        case = EvaluationCaseReference.build(
            manifest=manifest,
            case_id=_opaque({"qualification_request": probe.qualification_request_sha256}),
            family_id=_opaque({"qualification": "synthetic"}),
            mechanism_id=_opaque({"qualification": "transport_and_expressiveness"}),
            dataset_id=_opaque({"dataset": "synthetic-v2-qualification"}),
            variant_id=_opaque({"variant": probe.variant}),
            variant_content_sha256=variant.variant_content_sha256,
            case_content_sha256=probe.qualification_request_sha256,
            evidence_bundle_id=f"p3-evidence-bundle-{probe.context.context_sha256}",
            evidence_content_sha256=probe.context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{probe.qualification_request_sha256}",
            lineage_sha256=probe.qualification_request_sha256,
            visibility_projection_sha256=probe.context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        model_policy = _model_policy(
            manifest=manifest,
            route="model_gateway",
            variant_content_sha256=variant.variant_content_sha256,
            prompt_policy_sha256=variant.prompt_policy_sha256,
            response_schema=schema,
            openai_policy=policy,
        )
        runtime = RuntimePolicyReference.build(
            manifest=manifest,
            model_policy=model_policy,
            retry_policy_ref=_opaque(
                {
                    "transport_sha256": plan.transport_sha256,
                    "maximum_attempts": 2,
                }
            ),
            timeout_ns=int(policy.timeout_seconds * 1_000_000_000),
            max_attempts=2,
            max_response_bytes=32_768,
            provenance_sha256=probe.qualification_request_sha256,
        )
        request = prepare_gateway_request(
            manifest=manifest,
            case=case,
            model_policy=model_policy,
            context=probe.context,
            prompt_text=probe.prompt_text,
            response_schema=schema,
            runtime_policy=runtime,
        )
        prepared.append(
            PreparedClaimCorpusRequest(
                request_sha256=probe.qualification_request_sha256,
                route="model_gateway",
                authority=authority,
                request=request,
            )
        )
    if (
        tuple(item.request_sha256 for item in prepared) != plan.qualification_request_sha256s
        or len({item.request.initial_attempt.request_identity_sha256 for item in prepared})
        != QUALIFICATION_REQUEST_COUNT
    ):
        raise ClaimValidationV2QualificationError(
            "authorized qualification request census differs from the plan"
        )
    return tuple(prepared)


def publish_qualification_result(path: Path, model: object) -> str:
    if not hasattr(model, "model_dump"):
        raise TypeError("qualification publication requires a validated model")
    payload = model.model_dump(mode="json")
    return publish_immutable_file(
        path,
        (canonical_project_json(payload) + "\n").encode("utf-8"),
    )


__all__ = [
    "ClaimValidationV2QualificationError",
    "V2QualificationAuthorization",
    "V2QualificationExecutionPlan",
    "V2QualificationPreflight",
    "V2QualificationRehearsal",
    "build_qualification_authorization",
    "build_qualification_gateway_requests",
    "build_qualification_plan",
    "build_qualification_preflight",
    "checked_qualification_run_directory",
    "inspect_repository_state",
    "load_qualification_authorization",
    "publish_qualification_result",
    "rehearse_qualification",
]

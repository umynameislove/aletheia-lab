"""One-use execution and independent closeout for the V2 diagnosis cohort."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from aletheia_lab.diagnosis.variant_registry import build_variant_registry
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import (
    NeverCancelled,
    PreparedClaimCorpusRequest,
    SystemMonotonicClock,
    _model_policy,
    _opaque,
    _request_authority,
)
from aletheia_lab.evaluation.claim_corpus_live_store import (
    ClaimCorpusAttemptStore,
    verified_complete_claim_corpus_store_sha256,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    provider_response_schema_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_corpus_terminal_reader import (
    ClaimCorpusTerminalReader,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    _request_projections,
    build_cohort_plan,
    checked_cohort_run_directory,
    load_cohort_authorization,
    load_verified_qualification,
    publish_cohort_result,
    rehearse_cohort,
    validate_cohort_authorization,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_contracts import (
    LEASE_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    ClaimValidationV2CohortError,
    V2CohortAuthorization,
    V2CohortExecutionPlan,
    V2CohortLease,
    V2CohortReceipt,
    V2CohortRehearsal,
    V2CohortTerminalOutcome,
)
from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    build_measurement_witness_claim,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    _load_frozen_inputs,
    _openai_policy,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    ClaimSupportValidationV2RuntimeManifest,
    evaluate_v2_technical_admission,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    Clock,
    GloballyPacedProviderAdapter,
    OpenAIValidationV2Adapter,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    RuntimePolicyReference,
    UsageMetadata,
    V2RetryController,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_B0_PROMPT = "Emit the exact deterministic measurement witness for this visible context."


class V2DeterministicB0Adapter:
    """Return the prospectively defined measurement witness without a provider."""

    def __init__(self, request: PreparedClaimCorpusRequest) -> None:
        self._request = request.request
        self._binding = ProviderBinding.from_model_policy(
            request.request.initial_attempt.model_policy
        )

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        context = self._request.context
        if not isinstance(context, ModelVisibleEvidenceContext):
            raise ClaimValidationV2CohortError(
                "V2 deterministic B0 requires measured visible evidence"
            )
        claim = build_measurement_witness_claim(context)
        payload = {
            "schema_version": "diagnosis-provider-output/2",
            "result": {
                "output_status": "completed",
                "atomic_claims": [
                    {
                        "claim_type": claim.claim_type,
                        "claim_text": claim.claim_text,
                        "material_parts": [
                            {"text": item.text} for item in claim.material_parts
                        ],
                        "visible_evidence_ids": list(claim.visible_evidence_ids),
                    }
                ],
            },
        }
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_opaque(
                {
                    "executor": "claim-support-validation-v2-deterministic-b0/1",
                    "request_identity_sha256": call.request_identity_sha256,
                }
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(
                canonical_project_json(payload).encode("utf-8")
            ),
            usage=UsageMetadata(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _cohort_manifest(
    plan: V2CohortExecutionPlan,
    authorization: V2CohortAuthorization,
) -> EvaluationManifestReference:
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{plan.protocol_sha256}",
        snapshot_id=f"p3-snapshot-{plan.plan_sha256}",
        manifest_content_sha256=plan.plan_sha256,
        source_commit_ref=authorization.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authorization.authorization_ref,
        provenance_sha256=plan.runtime_manifest_sha256,
        created_at=authorization.authorized_at,
        frozen_at=authorization.authorized_at,
        visibility="diagnosis",
    )


def build_v2_cohort_gateway_requests(
    root: Path,
    plan: V2CohortExecutionPlan,
    authorization: V2CohortAuthorization,
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Rebuild all 360 exact requests without invoking any adapter."""

    root = root.resolve()
    amendment, _ = _load_frozen_inputs(root)
    manifest = build_v2_runtime_manifest(root)
    _, census, evidence = _load_inputs(root)
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    openai_policy = _openai_policy(root)
    execution_manifest = _cohort_manifest(plan, authorization)
    sources = {item.request_sha256: item for item in census.primary_requests}
    bindings = {
        (item.family_id, item.evidence_condition): item for item in evidence.bindings
    }
    projections = _request_projections(root)
    prepared: list[PreparedClaimCorpusRequest] = []
    for scheduled, projection in zip(
        manifest.diagnosis_schedule, projections, strict=True
    ):
        source = sources.get(scheduled.source_request_sha256)
        binding = bindings.get((scheduled.family_id, scheduled.evidence_condition))
        if (
            source is None
            or binding is None
            or source.variant != scheduled.variant
            or source.family_sha256 != scheduled.family_sha256
            or binding.family_sha256 != scheduled.family_sha256
            or binding.visible_context.context_sha256
            != scheduled.visible_context_sha256
            or projection.v2_request_sha256 != scheduled.v2_request_sha256
        ):
            raise ClaimValidationV2CohortError(
                "V2 request construction lost a frozen schedule binding"
            )
        context = ModelVisibleEvidenceContext.model_validate(
            binding.visible_context.model_dump(mode="python")
        )
        schema = provider_response_schema_v2(
            tuple(item.evidence_id for item in context.items)
        )
        variant = registry.require(scheduled.variant)
        authority = _request_authority(
            registry=registry,
            request_sha256=scheduled.v2_request_sha256,
            variant=scheduled.variant,
            context=context,
            observed_binding_sha256=binding.binding_sha256,
        )
        case = EvaluationCaseReference.build(
            manifest=execution_manifest,
            case_id=_opaque({"v2_request_sha256": scheduled.v2_request_sha256}),
            family_id=_opaque({"family_sha256": scheduled.family_sha256}),
            mechanism_id=_opaque({"mechanism": scheduled.mechanism}),
            dataset_id=_opaque({"dataset": "telco-customer-churn-development"}),
            variant_id=_opaque({"variant": scheduled.variant}),
            variant_content_sha256=variant.variant_content_sha256,
            case_content_sha256=scheduled.v2_request_sha256,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{binding.source_projection_sha256}",
            lineage_sha256=binding.source_projection_sha256,
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        prompt = (
            _B0_PROMPT
            if scheduled.execution_route == "deterministic_local"
            else "\n\n".join(
                (
                    freeze.prompt_policies[
                        scheduled.variant
                    ].instruction_contract,
                    amendment.shared_instruction_suffix,
                )
            )
        )
        model_policy = _model_policy(
            manifest=execution_manifest,
            route=scheduled.execution_route,
            variant_content_sha256=variant.variant_content_sha256,
            prompt_policy_sha256=variant.prompt_policy_sha256,
            response_schema=schema,
            openai_policy=openai_policy,
        )
        runtime = RuntimePolicyReference.build(
            manifest=execution_manifest,
            model_policy=model_policy,
            retry_policy_ref=_opaque(
                {
                    "protocol_sha256": plan.protocol_sha256,
                    "maximum_attempts": projection.maximum_attempts,
                    "fallback": "forbidden",
                }
            ),
            timeout_ns=(
                1_000_000_000
                if scheduled.execution_route == "deterministic_local"
                else int(openai_policy.timeout_seconds * 1_000_000_000)
            ),
            max_attempts=projection.maximum_attempts,
            max_response_bytes=32_768,
            provenance_sha256=projection.projection_sha256,
        )
        request = prepare_gateway_request(
            manifest=execution_manifest,
            case=case,
            model_policy=model_policy,
            context=context,
            prompt_text=prompt,
            response_schema=schema,
            runtime_policy=runtime,
        )
        if scheduled.execution_route == "model_gateway" and (
            content_sha256(prompt.encode("utf-8")) != projection.prompt_sha256
            or content_sha256(canonical_project_json(schema).encode("utf-8"))
            != projection.response_schema_sha256
        ):
            raise ClaimValidationV2CohortError(
                "V2 provider request differs from its frozen projection"
            )
        prepared.append(
            PreparedClaimCorpusRequest(
                request_sha256=scheduled.v2_request_sha256,
                route=scheduled.execution_route,
                authority=authority,
                request=request,
            )
        )
    result = tuple(prepared)
    if (
        tuple(item.request_sha256 for item in result)
        != tuple(item.v2_request_sha256 for item in manifest.diagnosis_schedule)
        or canonical_execution_sha256(tuple(item.request_sha256 for item in result))
        != plan.request_census_sha256
        or len(
            {
                item.request.initial_attempt.request_identity_sha256
                for item in result
            }
        )
        != 360
    ):
        raise ClaimValidationV2CohortError(
            "V2 gateway request census differs from the authorized plan"
        )
    return result


def build_openai_cohort_adapter(
    root: Path,
    prepared: tuple[PreparedClaimCorpusRequest, ...],
) -> ProviderAdapter:
    """Build one shared model adapter behind the frozen global pace."""

    model_requests = tuple(item for item in prepared if item.route == "model_gateway")
    if len(model_requests) != 315:
        raise ClaimValidationV2CohortError(
            "V2 cohort adapter requires exactly 315 model requests"
        )
    binding = ProviderBinding.from_model_policy(
        model_requests[0].request.initial_attempt.model_policy
    )
    if any(
        ProviderBinding.from_model_policy(item.request.initial_attempt.model_policy)
        != binding
        for item in model_requests
    ):
        raise ClaimValidationV2CohortError(
            "V2 cohort model requests do not share one provider binding"
        )
    base = OpenAIValidationV2Adapter.from_environment(
        model_policy=model_requests[0].request.initial_attempt.model_policy,
        policy=_openai_policy(root),
    )
    return GloballyPacedProviderAdapter(base, minimum_interval_ms=1000)


def build_cohort_lease(
    authorization: V2CohortAuthorization,
) -> V2CohortLease:
    payload = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "destination_sha256": authorization.destination_sha256,
        "registered_attempts": 1,
    }
    return V2CohortLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )


def acquire_cohort_lease(
    run_dir: Path,
    authorization: V2CohortAuthorization,
) -> V2CohortLease:
    lease = build_cohort_lease(authorization)
    publish_cohort_result(run_dir / "lease.json", lease)
    return lease


def _load_lease(run_dir: Path, authorization: V2CohortAuthorization) -> V2CohortLease:
    try:
        actual = V2CohortLease.model_validate_json(
            (run_dir / "lease.json").read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2CohortError(
            "V2 cohort lease is unavailable or invalid"
        ) from exc
    if actual != build_cohort_lease(authorization):
        raise ClaimValidationV2CohortError(
            "V2 cohort lease differs from its authorization"
        )
    return actual


def _reader(store_root: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _usage_census(
    store_root: Path,
    prepared: tuple[PreparedClaimCorpusRequest, ...],
) -> tuple[bool, int | None, int | None, int | None]:
    records = tuple(
        record
        for item in prepared
        if item.route == "model_gateway"
        for record in _reader(
            store_root,
            item.request.initial_attempt.request_identity_sha256,
        ).terminal_attempt_records(
            item.request.initial_attempt.request_identity_sha256
        )
    )
    complete = bool(records) and all(
        record.usage is not None
        and record.usage.input_tokens is not None
        and record.usage.output_tokens is not None
        and record.usage.total_tokens is not None
        for record in records
    )
    if not complete:
        return False, None, None, None
    input_tokens = sum(cast(int, record.usage.input_tokens) for record in records if record.usage)
    output_tokens = sum(
        cast(int, record.usage.output_tokens) for record in records if record.usage
    )
    return True, input_tokens, output_tokens, input_tokens + output_tokens


def build_cohort_receipt(
    manifest: ClaimSupportValidationV2RuntimeManifest,
    plan: V2CohortExecutionPlan,
    rehearsal: V2CohortRehearsal,
    authorization: V2CohortAuthorization,
    prepared: tuple[PreparedClaimCorpusRequest, ...],
    store_root: Path,
) -> V2CohortReceipt:
    """Rebuild the receipt exclusively from authenticated terminal shards."""

    store_sha = verified_complete_claim_corpus_store_sha256(store_root, prepared)
    schedule = {
        item.v2_request_sha256: item for item in manifest.diagnosis_schedule
    }
    outcomes: list[V2CohortTerminalOutcome] = []
    for item in prepared:
        scheduled = schedule[item.request_sha256]
        identity = item.request.initial_attempt.request_identity_sha256
        reader = _reader(store_root, identity)
        inventory = reader.terminal_inventory(identity)
        records = reader.terminal_attempt_records(identity)
        categories = tuple(
            record.provider_failure_category
            for record in records
            if record.provider_failure_category is not None
        )
        if (
            item.route == "model_gateway"
            and inventory.gateway_status != "parsed"
            and any(record.provider_failure_category is None for record in records)
        ):
            raise ClaimValidationV2CohortError(
                "V2 technical failure lacks its public-safe provider category"
            )
        outcome_payload: dict[str, object] = {
            "sequence": scheduled.sequence,
            "schedule_round": scheduled.schedule_round,
            "v2_request_sha256": scheduled.v2_request_sha256,
            "source_request_sha256": scheduled.source_request_sha256,
            "gateway_request_identity_sha256": identity,
            "mechanism": scheduled.mechanism,
            "evidence_condition": scheduled.evidence_condition,
            "variant": scheduled.variant,
            "execution_route": scheduled.execution_route,
            "gateway_status": inventory.gateway_status,
            "attempt_count": len(records),
            "provider_failure_categories": categories,
            "parsed_response_sha256": inventory.parsed_response_sha256,
            "issue_sha256": inventory.issue_sha256,
        }
        outcomes.append(
            V2CohortTerminalOutcome.model_validate(
                {
                    **outcome_payload,
                    "outcome_sha256": canonical_execution_sha256(outcome_payload),
                }
            )
        )
    terminal_ids = tuple(item.v2_request_sha256 for item in outcomes)
    parsed_ids = tuple(
        item.v2_request_sha256 for item in outcomes if item.gateway_status == "parsed"
    )
    admission = evaluate_v2_technical_admission(
        manifest,
        terminal_request_sha256=terminal_ids,
        parsed_request_sha256=parsed_ids,
    )
    blockers = cast(tuple[str, ...], admission["blocker_codes"])
    passed = cast(bool, admission["technical_admission_passed"])
    usage_complete, input_tokens, output_tokens, total_tokens = _usage_census(
        store_root, prepared
    )
    statuses = dict(
        sorted(Counter(item.gateway_status for item in outcomes).items())
    )
    category_counts = dict(
        sorted(
            Counter(
                category
                for item in outcomes
                for category in item.provider_failure_categories
            ).items()
        )
    )
    receipt_payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": (
            "claim_support_validation_v2_cohort_complete_technical_admission_passed"
            if passed
            else "claim_support_validation_v2_cohort_complete_technical_admission_failed"
        ),
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "qualification_receipt_sha256": plan.qualification_receipt_sha256,
        "protocol_sha256": plan.protocol_sha256,
        "runtime_manifest_sha256": manifest.manifest_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "terminal_store_sha256": store_sha,
        "terminal_request_count": 360,
        "parsed_count": len(parsed_ids),
        "technical_failure_count": 360 - len(parsed_ids),
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "provider_attempt_count": sum(
            item.attempt_count
            for item in outcomes
            if item.execution_route == "model_gateway"
        ),
        "technical_attempt_count": sum(item.attempt_count for item in outcomes),
        "gateway_status_counts": statuses,
        "provider_failure_category_counts": category_counts,
        "provider_usage_complete": usage_complete,
        "observed_provider_input_token_count": input_tokens,
        "observed_provider_output_token_count": output_tokens,
        "observed_provider_total_token_count": total_tokens,
        "outcomes": tuple(outcomes),
        "technical_admission_blockers": blockers,
        "technical_admission_passed": passed,
        "missingness_exchangeability_established": False,
        "relation_execution_unlocked": passed,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    identity_payload = {
        **receipt_payload,
        "outcomes": tuple(item.model_dump(mode="json") for item in outcomes),
    }
    return V2CohortReceipt.model_validate(
        {
            **receipt_payload,
            "receipt_sha256": canonical_execution_sha256(identity_payload),
        }
    )


def _validated_execution_inputs(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    qualification_run_dir: Path,
    run_dir: Path,
) -> tuple[
    ClaimSupportValidationV2RuntimeManifest,
    V2CohortExecutionPlan,
    V2CohortRehearsal,
    V2CohortAuthorization,
    tuple[PreparedClaimCorpusRequest, ...],
]:
    qualification = load_verified_qualification(root, qualification_run_dir)
    plan = build_cohort_plan(
        root,
        source_commit_ref=repository_state.head_commit,
        qualification_receipt=qualification,
    )
    rehearsal = rehearse_cohort(root, plan, qualification)
    authorization = load_cohort_authorization(run_dir / "authorization.json")
    validate_cohort_authorization(
        authorization,
        plan,
        rehearsal,
        repository_state=repository_state,
        run_dir=run_dir,
    )
    manifest = build_v2_runtime_manifest(root)
    if manifest.manifest_sha256 != plan.runtime_manifest_sha256:
        raise ClaimValidationV2CohortError(
            "V2 runtime manifest differs from the authorized cohort plan"
        )
    return (
        manifest,
        plan,
        rehearsal,
        authorization,
        build_v2_cohort_gateway_requests(root, plan, authorization),
    )


def execute_v2_cohort(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    qualification_run_dir: Path,
    run_dir: Path,
    confirm_authorization_sha256: str,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
    retry_controller: V2RetryController | None = None,
) -> V2CohortReceipt:
    """Consume the authority, resume only sealed terminals, and publish closeout."""

    run = checked_cohort_run_directory(root, run_dir)
    manifest, plan, rehearsal, authorization, prepared = _validated_execution_inputs(
        root,
        repository_state=repository_state,
        qualification_run_dir=qualification_run_dir,
        run_dir=run,
    )
    if confirm_authorization_sha256 != authorization.authorization_sha256:
        raise ClaimValidationV2CohortError(
            "V2 cohort authorization confirmation differs"
        )
    receipt_path = run / "receipt.json"
    if receipt_path.exists():
        return verify_completed_v2_cohort(
            root,
            qualification_run_dir=qualification_run_dir,
            run_dir=run,
        )
    lease_exists = (run / "lease.json").exists()
    store_exists = (run / "attempt-store").exists()
    if store_exists and not lease_exists:
        raise ClaimValidationV2CohortError(
            "V2 cohort attempt store exists without its registered lease"
        )
    if lease_exists:
        _load_lease(run, authorization)
    else:
        acquire_cohort_lease(run, authorization)
    active_clock = clock or SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(run / "attempt-store", clock=active_clock)
    shards = store.shards(prepared)
    states = {
        identity: shard.current_state(identity) for identity, shard in shards.items()
    }
    if any(state not in {None, "terminal_published"} for state in states.values()):
        raise ClaimValidationV2CohortError(
            "partial V2 request state forbids continuation"
        )
    controller = retry_controller or V2RetryController()
    for item in prepared:
        identity = item.request.initial_attempt.request_identity_sha256
        if states[identity] == "terminal_published":
            continue
        shard = shards[identity]
        shard.prepare(item.request)
        shard.start(item.request)
        local = item.route == "deterministic_local"
        result = execute_gateway_request(
            item.request,
            adapter=V2DeterministicB0Adapter(item) if local else adapter,
            clock=active_clock,
            cancellation=NeverCancelled(),
            retry_controller=None if local else controller,
        )
        for attempt in result.attempts:
            shard.record_attempt(item.request, attempt)
        if result.raw_response is not None:
            shard.record_response(item.request, result)
        shard.record_parsed_or_failed(item.request, result)
        shard.mark_closeout_pending(item.request, result)
        shard.publish_terminal(item.request, result)
    receipt = build_cohort_receipt(
        manifest, plan, rehearsal, authorization, prepared, store.root
    )
    publish_cohort_result(receipt_path, receipt)
    return receipt


def verify_completed_v2_cohort(
    root: Path,
    *,
    qualification_run_dir: Path,
    run_dir: Path,
) -> V2CohortReceipt:
    """Independently reproduce a completed receipt without write authority."""

    run = checked_cohort_run_directory(root, run_dir)
    authorization = load_cohort_authorization(run / "authorization.json")
    historical_state = RepositoryExecutionState(
        branch="main",
        head_commit=authorization.source_commit_ref,
        origin_main_commit=authorization.source_commit_ref,
        clean=True,
    )
    manifest, plan, rehearsal, authorization, prepared = _validated_execution_inputs(
        root,
        repository_state=historical_state,
        qualification_run_dir=qualification_run_dir,
        run_dir=run,
    )
    _load_lease(run, authorization)
    expected = build_cohort_receipt(
        manifest,
        plan,
        rehearsal,
        authorization,
        prepared,
        run / "attempt-store",
    )
    try:
        actual = V2CohortReceipt.model_validate_json(
            (run / "receipt.json").read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2CohortError(
            "V2 cohort receipt is unavailable or invalid"
        ) from exc
    if actual != expected:
        raise ClaimValidationV2CohortError(
            "V2 cohort receipt differs from authenticated terminal state"
        )
    return expected


__all__ = [
    "V2DeterministicB0Adapter",
    "acquire_cohort_lease",
    "build_cohort_lease",
    "build_cohort_receipt",
    "build_openai_cohort_adapter",
    "build_v2_cohort_gateway_requests",
    "execute_v2_cohort",
    "verify_completed_v2_cohort",
]

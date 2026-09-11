"""One-use V3.1 source-cohort execution and independent terminal replay."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from aletheia_lab.diagnosis.variant_registry import build_variant_registry
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
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
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH, REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_support_v3_cohort import (
    DETERMINISTIC_REQUEST_COUNT,
    MAXIMUM_PROVIDER_ATTEMPTS,
    MINIMUM_PROVIDER_INTERVAL_MS,
    MODEL_REQUEST_COUNT,
    SOURCE_CLAIM_INSTANCE_COUNT,
    SOURCE_REQUEST_COUNT,
    ClaimSupportV3CohortError,
    load_authorization,
    source_request_projections,
    validate_authorization,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import _openai_policy
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    render_source_payload,
    source_instance_id,
    source_payload,
    source_payload_issue,
    source_prompt,
    source_response_schema,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import publish, read_document, seal
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
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

_B0_PROMPT = "Emit the exact deterministic source-measurement payload for this visible context."


class V3DeterministicB0Adapter:
    """Emit only the pre-registered readings; it never calls a provider."""

    def __init__(self, request: PreparedClaimCorpusRequest, expected: list[dict[str, Any]]) -> None:
        self._binding = ProviderBinding.from_model_policy(
            request.request.initial_attempt.model_policy
        )
        self._expected = expected

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        payload = source_payload(self._expected)
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_opaque(
                {
                    "executor": "claim-support-v3-source-deterministic-b0/1",
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


def _manifest(plan: dict[str, Any], authorization: dict[str, Any]) -> EvaluationManifestReference:
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{plan['protocol_sha256']}",
        snapshot_id=f"p3-snapshot-{plan['plan_sha256']}",
        manifest_content_sha256=plan["plan_sha256"],
        source_commit_ref=authorization["source_commit_ref"],
        authorization_state="authorized",
        authorization_ref=authorization["authorization_ref"],
        provenance_sha256=plan["design_sha256"],
        created_at=authorization["authorized_at"],
        frozen_at=authorization["authorized_at"],
        visibility="diagnosis",
    )


def _prompt(freeze: Any, slot: dict[str, Any]) -> str:
    schedule = slot["source_schedule"]
    if schedule["execution_route"] == "deterministic_local":
        return _B0_PROMPT
    return "\n\n".join(
        (
            freeze.prompt_policies[schedule["variant"]].instruction_contract,
            source_prompt(slot["expected"], synthetic=False),
        )
    )


def prepare_source_requests(
    root: Path,
    plan: dict[str, Any],
    authorization: dict[str, Any],
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Rebuild all 360 requests while keeping expected values evaluator-side."""

    design = build_design(root)
    projections = source_request_projections(root)
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    policy = _openai_policy(root)
    execution_manifest = _manifest(plan, authorization)
    prepared: list[PreparedClaimCorpusRequest] = []
    for slot, projection in zip(design["slots"], projections, strict=True):
        schedule = slot["source_schedule"]
        context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(slot["context"]))
        variant = registry.require(schedule["variant"])
        request_sha = projection["source_request_sha256"]
        authority = _request_authority(
            registry=registry,
            request_sha256=request_sha,
            variant=schedule["variant"],
            context=context,
            observed_binding_sha256=slot["source_binding_sha256"],
        )
        case = EvaluationCaseReference.build(
            manifest=execution_manifest,
            case_id=_opaque({"v3_source_request_sha256": request_sha}),
            family_id=_opaque({"family_sha256": schedule["family_sha256"]}),
            mechanism_id=_opaque({"mechanism": schedule["mechanism"]}),
            dataset_id=_opaque({"dataset": "telco-customer-churn-development"}),
            variant_id=_opaque({"variant": schedule["variant"]}),
            variant_content_sha256=variant.variant_content_sha256,
            case_content_sha256=request_sha,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{slot['source_binding_sha256']}",
            lineage_sha256=slot["source_binding_sha256"],
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        prompt = _prompt(freeze, slot)
        schema = source_response_schema(slot["expected"])
        model = _model_policy(
            manifest=execution_manifest,
            route=schedule["execution_route"],
            variant_content_sha256=variant.variant_content_sha256,
            prompt_policy_sha256=content_sha256(prompt.encode("utf-8")),
            response_schema=schema,
            openai_policy=policy,
        )
        local = schedule["execution_route"] == "deterministic_local"
        runtime = RuntimePolicyReference.build(
            manifest=execution_manifest,
            model_policy=model,
            retry_policy_ref=_opaque(
                {
                    "protocol_sha256": plan["protocol_sha256"],
                    "maximum_attempts": 1 if local else MAXIMUM_PROVIDER_ATTEMPTS,
                    "fallback": "forbidden",
                }
            ),
            timeout_ns=1_000_000_000 if local else int(policy.timeout_seconds * 1_000_000_000),
            max_attempts=1 if local else MAXIMUM_PROVIDER_ATTEMPTS,
            max_response_bytes=32_768,
            provenance_sha256=projection["projection_sha256"],
        )
        gateway = prepare_gateway_request(
            manifest=execution_manifest,
            case=case,
            model_policy=model,
            context=context,
            prompt_text=prompt,
            response_schema=schema,
            runtime_policy=runtime,
        )
        if (
            content_sha256(prompt.encode("utf-8")) != projection["prompt_sha256"]
            or content_sha256(canonical_project_json(schema).encode("utf-8"))
            != projection["response_schema_sha256"]
            or context.context_sha256 != projection["visible_context_sha256"]
        ):
            raise ClaimSupportV3CohortError("source gateway request differs from frozen projection")
        prepared.append(
            PreparedClaimCorpusRequest(
                request_sha256=request_sha,
                route=schedule["execution_route"],
                authority=authority,
                request=gateway,
            )
        )
    result = tuple(prepared)
    if (
        len(result) != SOURCE_REQUEST_COUNT
        or len({item.request_sha256 for item in result}) != SOURCE_REQUEST_COUNT
        or len({item.request.initial_attempt.request_identity_sha256 for item in result})
        != SOURCE_REQUEST_COUNT
        or content_sha256(
            canonical_project_json([item.request_sha256 for item in result]).encode("utf-8")
        )
        != plan["request_census_sha256"]
    ):
        raise ClaimSupportV3CohortError("source gateway request census differs")
    return result


def build_openai_adapter(
    root: Path, prepared: tuple[PreparedClaimCorpusRequest, ...]
) -> ProviderAdapter:
    model_requests = tuple(item for item in prepared if item.route == "model_gateway")
    if len(model_requests) != MODEL_REQUEST_COUNT:
        raise ClaimSupportV3CohortError("source cohort requires exactly 315 model calls")
    binding = ProviderBinding.from_model_policy(
        model_requests[0].request.initial_attempt.model_policy
    )
    if any(
        ProviderBinding.from_model_policy(item.request.initial_attempt.model_policy) != binding
        for item in model_requests
    ):
        raise ClaimSupportV3CohortError(
            "source cohort model requests do not share one provider binding"
        )
    base = OpenAIValidationV2Adapter.from_environment(
        model_policy=model_requests[0].request.initial_attempt.model_policy,
        policy=_openai_policy(root),
    )
    return GloballyPacedProviderAdapter(base, minimum_interval_ms=MINIMUM_PROVIDER_INTERVAL_MS)


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _lease(plan: dict[str, Any], authorization: dict[str, Any]) -> dict[str, Any]:
    return seal(
        {
            "schema_version": "claim-support-v3-source-cohort-lease/1",
            "authorization_sha256": authorization["authorization_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "destination_sha256": authorization["destination_sha256"],
            "registered_attempts": 1,
        },
        "lease_sha256",
    )


def _source_requests(root: Path) -> dict[str, Any]:
    census = ClaimCorpusRequestCensus.model_validate_json((root / REQUEST_CENSUS_PATH).read_bytes())
    return {item.request_sha256: item for item in census.primary_requests}


def _semantic_issue(
    root: Path,
    slot: dict[str, Any],
    payload: dict[str, Any] | None,
    source_requests: dict[str, Any],
) -> str | None:
    if payload is None:
        return None
    issue = source_payload_issue(payload, slot["expected"])
    if issue is not None:
        return issue
    try:
        rendered = render_source_payload(payload, slot["expected"])
        schedule = slot["source_schedule"]
        request = source_requests[schedule["source_request_sha256"]]
        context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(slot["context"]))
        output = normalize_provider_output_v2(
            request,
            rendered,
            source_record_sha256=content_sha256(canonical_project_json(payload).encode("utf-8")),
            visible_evidence_ids=[item.evidence_id for item in context.items],
        )
    except (KeyError, TypeError, ValueError):
        return "source_normalization_mismatch"
    expected_text = [item["claim_text"] for item in slot["expected"]]
    observed_text = [item.claim_text for item in output.atomic_claims]
    return None if observed_text == expected_text else "source_normalization_mismatch"


def _usage_census(
    store: Path, prepared: tuple[PreparedClaimCorpusRequest, ...]
) -> tuple[bool, int | None, int | None, int | None]:
    records = tuple(
        record
        for item in prepared
        if item.route == "model_gateway"
        for record in _reader(
            store, item.request.initial_attempt.request_identity_sha256
        ).terminal_attempt_records(item.request.initial_attempt.request_identity_sha256)
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
    inputs = sum(cast(int, record.usage.input_tokens) for record in records if record.usage)
    outputs = sum(cast(int, record.usage.output_tokens) for record in records if record.usage)
    return True, inputs, outputs, inputs + outputs


def rebuild_receipt(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    authorization: dict[str, Any],
    prepared: tuple[PreparedClaimCorpusRequest, ...],
) -> dict[str, Any]:
    """Reproduce source acceptance only from authenticated terminal shards."""

    lease = read_document(run / "lease.json", "lease_sha256")
    expected_lease = _lease(plan, authorization)
    if lease != expected_lease:
        raise ClaimSupportV3CohortError("source cohort lease differs")
    store = run / "attempt-store"
    store_hash = verified_complete_claim_corpus_store_sha256(store, prepared)
    design = build_design(root)
    original_requests = _source_requests(root)
    rows: list[dict[str, Any]] = []
    for item, slot in zip(prepared, design["slots"], strict=True):
        identity = item.request.initial_attempt.request_identity_sha256
        reader = _reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        payload = reader.terminal_parsed_payload(identity)
        records = reader.terminal_attempt_records(identity)
        categories = Counter(
            str(record.provider_failure_category)
            for record in records
            if record.provider_failure_category is not None
        )
        semantic_issue = (
            _semantic_issue(root, slot, payload, original_requests)
            if inventory.gateway_status == "parsed"
            else None
        )
        accepted = inventory.gateway_status == "parsed" and semantic_issue is None
        instance_ids: list[str] = []
        payload_sha: str | None = None
        if payload is not None:
            payload_sha = content_sha256(canonical_project_json(payload).encode("utf-8"))
        if accepted and payload_sha is not None:
            instance_ids = [
                source_instance_id(
                    plan["protocol_sha256"], slot["slot_sha256"], payload_sha, ordinal
                )
                for ordinal in (1, 2)
            ]
        outcome = {
            "sequence": slot["source_schedule"]["sequence"],
            "source_request_sha256": item.request_sha256,
            "slot_sha256": slot["slot_sha256"],
            "gateway_request_identity_sha256": identity,
            "execution_route": item.route,
            "gateway_status": inventory.gateway_status,
            "accepted": accepted,
            "semantic_issue_code": semantic_issue,
            "attempt_count": len(records),
            "failure_categories": dict(sorted(categories.items())),
            "parsed_payload_sha256": payload_sha,
            "source_instance_sha256s": instance_ids,
        }
        rows.append(seal(outcome, "outcome_sha256"))
    parsed = sum(row["gateway_status"] == "parsed" for row in rows)
    accepted_count = sum(row["accepted"] for row in rows)
    technical_failures = SOURCE_REQUEST_COUNT - parsed
    semantic_failures = parsed - accepted_count
    passed = accepted_count == SOURCE_REQUEST_COUNT
    statuses = dict(sorted(Counter(row["gateway_status"] for row in rows).items()))
    category_counts = dict(
        sorted(
            Counter(
                category
                for row in rows
                for category, count in row["failure_categories"].items()
                for _ in range(count)
            ).items()
        )
    )
    semantic_issues = dict(
        sorted(
            Counter(
                row["semantic_issue_code"] for row in rows if row["semantic_issue_code"] is not None
            ).items()
        )
    )
    usage_complete, input_tokens, output_tokens, total_tokens = _usage_census(store, prepared)
    payload = {
        "schema_version": "claim-support-v3-source-cohort-receipt/1",
        "status": (
            "v3_1_source_cohort_passed" if passed else "v3_1_source_cohort_complete_with_failures"
        ),
        "authorization_sha256": authorization["authorization_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "rehearsal_sha256": rehearsal["rehearsal_sha256"],
        "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
        "protocol_sha256": plan["protocol_sha256"],
        "design_sha256": plan["design_sha256"],
        "source_commit_ref": authorization["source_commit_ref"],
        "terminal_store_sha256": store_hash,
        "terminal_request_count": SOURCE_REQUEST_COUNT,
        "parsed_count": parsed,
        "accepted_count": accepted_count,
        "technical_failure_count": technical_failures,
        "semantic_failure_count": semantic_failures,
        "source_claim_instance_count": accepted_count * 2,
        "expected_source_claim_instance_count": SOURCE_CLAIM_INSTANCE_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "provider_attempt_count": sum(
            row["attempt_count"] for row in rows if row["execution_route"] == "model_gateway"
        ),
        "technical_attempt_count": sum(row["attempt_count"] for row in rows),
        "gateway_status_counts": statuses,
        "provider_failure_category_counts": category_counts,
        "semantic_issue_counts": semantic_issues,
        "provider_usage_complete": usage_complete,
        "observed_provider_input_token_count": input_tokens,
        "observed_provider_output_token_count": output_tokens,
        "observed_provider_total_token_count": total_tokens,
        "outcomes": rows,
        "failures_preserved_without_adaptive_replacement": True,
        "relation_request_count_planned": 240,
        "relation_execution_authorized": False,
        "relation_planning_unlocked": passed,
        "source_measurements_collected": True,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "automatic_labels_generated": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "admitted_to_corpus": False,
    }
    return seal(payload, "receipt_sha256")


def _validated_inputs(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    *,
    repository_state: RepositoryExecutionState,
    completed: bool = False,
) -> tuple[dict[str, Any], tuple[PreparedClaimCorpusRequest, ...]]:
    authorization = load_authorization(run / "authorization.json")
    validate_authorization(
        root,
        run,
        plan,
        rehearsal,
        authorization,
        repository_state=repository_state,
        completed=completed,
    )
    return authorization, prepare_source_requests(root, plan, authorization)


def _registered_store(
    run: Path,
    plan: dict[str, Any],
    authorization: dict[str, Any],
    prepared: tuple[PreparedClaimCorpusRequest, ...],
    clock: Clock,
) -> tuple[ClaimCorpusAttemptStore, dict[str, Any]]:
    """Create or validate the single lease and resumable terminal inventory."""

    lease_path = run / "lease.json"
    store_path = run / "attempt-store"
    if store_path.exists() and not lease_path.exists():
        raise ClaimSupportV3CohortError("attempt store exists without registered lease")
    if lease_path.exists():
        if read_document(lease_path, "lease_sha256") != _lease(plan, authorization):
            raise ClaimSupportV3CohortError("source cohort lease differs")
    else:
        publish(lease_path, _lease(plan, authorization))
    store = ClaimCorpusAttemptStore(store_path, clock=clock)
    shards = store.shards(prepared)
    states = {identity: shard.current_state(identity) for identity, shard in shards.items()}
    if any(state not in {None, "terminal_published"} for state in states.values()):
        raise ClaimSupportV3CohortError("partial source request state forbids continuation")
    return store, states


def execute_source_cohort(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    *,
    repository_state: RepositoryExecutionState,
    confirmation: str,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
    retry: V2RetryController | None = None,
) -> dict[str, Any]:
    """Consume the authority once, resuming only already sealed terminals."""

    authorization, prepared = _validated_inputs(
        root, run, plan, rehearsal, repository_state=repository_state
    )
    if confirmation != authorization["authorization_sha256"]:
        raise ClaimSupportV3CohortError("authorization confirmation differs")
    receipt_path = run / "receipt.json"
    if receipt_path.exists():
        return verify_source_cohort(root, run, plan, rehearsal)
    active_clock = clock or SystemMonotonicClock()
    store, states = _registered_store(run, plan, authorization, prepared, active_clock)
    shards = store.shards(prepared)
    design = build_design(root)
    controller = retry or V2RetryController()
    for item, slot in zip(prepared, design["slots"], strict=True):
        identity = item.request.initial_attempt.request_identity_sha256
        if states[identity] == "terminal_published":
            continue
        shard = shards[identity]
        shard.prepare(item.request)
        shard.start(item.request)
        local = item.route == "deterministic_local"
        result = execute_gateway_request(
            item.request,
            adapter=(V3DeterministicB0Adapter(item, slot["expected"]) if local else adapter),
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
    receipt = rebuild_receipt(root, run, plan, rehearsal, authorization, prepared)
    publish(receipt_path, receipt)
    return receipt


def verify_source_cohort(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
) -> dict[str, Any]:
    """Independently reproduce a completed receipt without write authority."""

    authorization = load_authorization(run / "authorization.json")
    historical = RepositoryExecutionState(
        branch="main",
        head_commit=authorization["source_commit_ref"],
        origin_main_commit=authorization["source_commit_ref"],
        clean=True,
    )
    authorization, prepared = _validated_inputs(
        root,
        run,
        plan,
        rehearsal,
        repository_state=historical,
        completed=True,
    )
    actual = read_document(run / "receipt.json", "receipt_sha256")
    expected = rebuild_receipt(root, run, plan, rehearsal, authorization, prepared)
    if actual != expected:
        raise ClaimSupportV3CohortError(
            "source cohort receipt differs from authenticated terminal state"
        )
    return expected


__all__ = [
    "V3DeterministicB0Adapter",
    "build_openai_adapter",
    "execute_source_cohort",
    "prepare_source_requests",
    "rebuild_receipt",
    "verify_source_cohort",
]

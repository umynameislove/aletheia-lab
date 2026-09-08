"""Provider request construction and pacing for blind claim relations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceSemanticsPolicy,
    ClaimRelationAssignmentRequest,
    load_evidence_semantics_policy,
)
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    EXPECTED_RELATION_REQUEST_COUNT,
    MINIMUM_PROVIDER_INTERVAL_MS,
    ClaimRelationExecutionError,
    ClaimRelationExecutionPlan,
    RelationRequestAuthority,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    ClaimRelationProviderContext,
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RuntimePolicyReference,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256


class RelationAuthorizationBinding(Protocol):
    """Minimum authority fields embedded in provider-neutral request identity."""

    authorization_ref: str
    authorized_at: str


class PreparedRelationRequest:
    def __init__(
        self,
        *,
        assignment: ClaimRelationAssignmentRequest,
        authority: RelationRequestAuthority,
        request: GatewayRequest,
    ) -> None:
        self.assignment = assignment
        self.authority = authority
        self.request = request


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def _provider_policy(root: Path, policy: ClaimEvidenceSemanticsPolicy) -> OpenAIGatewayPolicy:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    gateway = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    if (
        gateway.model != policy.model
        or gateway.model_version != policy.model_snapshot
        or gateway.max_output_tokens != policy.maximum_output_tokens
        or gateway.provider_attempt_ceiling != policy.maximum_attempts
    ):
        raise ClaimRelationExecutionError("relation policy differs from gateway policy")
    return gateway


def build_relation_gateway_requests(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    plan: ClaimRelationExecutionPlan,
    authorization: RelationAuthorizationBinding,
) -> tuple[PreparedRelationRequest, ...]:
    policy = load_evidence_semantics_policy(root)
    gateway_policy = _provider_policy(root, policy)
    if (
        tuple(item.assignment_request_sha256 for item in preparation.relation_requests)
        != plan.assignment_request_sha256s
    ):
        raise ClaimRelationExecutionError("relation preparation differs from execution plan")
    project_sha = canonical_execution_sha256(
        {"boundary": "claim-relation-execution/v1", "plan": plan.plan_sha256}
    )
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{project_sha}",
        snapshot_id=f"p3-snapshot-{canonical_execution_sha256({'commit': plan.source_commit_ref})}",
        manifest_content_sha256=plan.plan_sha256,
        source_commit_ref=plan.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authorization.authorization_ref,
        provenance_sha256=preparation.preparation_sha256,
        created_at=authorization.authorized_at,
        frozen_at=authorization.authorized_at,
        visibility="evaluator",
    )
    response_schema_sha = content_sha256(canonical_project_json(policy.response_schema).encode())
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=gateway_policy.model_policy_sha256(),
        provider_ref=_opaque({"provider": policy.provider}),
        model_ref=_opaque({"model": policy.model}),
        model_version_ref=_opaque({"model_snapshot": policy.model_snapshot}),
        resource_policy_ref=_opaque(
            {"policy": policy.policy_sha256, "pace_ms": MINIMUM_PROVIDER_INTERVAL_MS}
        ),
        prompt_policy_ref=_opaque({"prompt": content_sha256(policy.prompt.encode())}),
        response_schema_sha256=response_schema_sha,
        provenance_sha256=policy.policy_sha256,
        visibility="evaluator",
    )
    runtime = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque(
            {"maximum_attempts": policy.maximum_attempts, "fallback": "forbidden"}
        ),
        timeout_ns=int(gateway_policy.timeout_seconds * 1_000_000_000),
        max_attempts=policy.maximum_attempts,
        max_response_bytes=32_768,
        provenance_sha256=plan.plan_sha256,
    )
    prepared: list[PreparedRelationRequest] = []
    for assignment in preparation.relation_requests:
        context = ClaimRelationProviderContext.from_provider_payload(assignment.provider_payload())
        authority_payload = {
            "assignment_request_sha256": assignment.assignment_request_sha256,
            "preparation_sha256": preparation.preparation_sha256,
            "policy_sha256": policy.policy_sha256,
            "provider_payload_sha256": context.context_sha256,
        }
        authority = RelationRequestAuthority.model_validate(
            {**authority_payload, "authority_sha256": canonical_execution_sha256(authority_payload)}
        )
        request_sha = assignment.assignment_request_sha256
        case = EvaluationCaseReference.build(
            manifest=manifest,
            case_id=_opaque({"relation_request": request_sha}),
            family_id=_opaque({"relation_boundary": request_sha}),
            mechanism_id=_opaque({"relation_boundary": "withheld"}),
            dataset_id=_opaque({"dataset": "development-claim-corpus"}),
            variant_id=_opaque({"relation_policy": policy.policy_sha256}),
            variant_content_sha256=policy.policy_sha256,
            case_content_sha256=request_sha,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{assignment.visible_context_sha256}",
            lineage_sha256=assignment.visible_context_sha256,
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="evaluator",
        )
        request = prepare_gateway_request(
            manifest=manifest,
            case=case,
            model_policy=model_policy,
            context=context,
            prompt_text=policy.prompt,
            response_schema=policy.response_schema,
            runtime_policy=runtime,
        )
        prepared.append(
            PreparedRelationRequest(assignment=assignment, authority=authority, request=request)
        )
    if (
        len(prepared) != EXPECTED_RELATION_REQUEST_COUNT
        or len({item.request.initial_attempt.request_identity_sha256 for item in prepared})
        != EXPECTED_RELATION_REQUEST_COUNT
    ):
        raise ClaimRelationExecutionError("gateway construction lost relation census identity")
    return tuple(prepared)


class PacedProviderAdapter:
    """Apply one global lower-bound interval to every provider attempt, including retries."""

    def __init__(
        self,
        delegate: ProviderAdapter,
        *,
        minimum_interval_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if minimum_interval_seconds <= 0:
            raise ClaimRelationExecutionError("provider start interval must be positive")
        self._delegate = delegate
        self._minimum_interval = minimum_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_started: float | None = None

    @property
    def binding(self) -> ProviderBinding:
        return self._delegate.binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        with self._lock:
            now = self._monotonic()
            if self._last_started is not None:
                remaining = self._minimum_interval - (now - self._last_started)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_started = self._monotonic()
            return self._delegate.invoke(call)


__all__ = [
    "PacedProviderAdapter",
    "PreparedRelationRequest",
    "RelationAuthorizationBinding",
    "build_relation_gateway_requests",
]

"""Independent authority validation for development requests and responses."""

from __future__ import annotations

from aletheia_lab.diagnosis._development.contracts import (
    DevelopmentCase,
    DevelopmentPilotError,
    DevelopmentPilotPlan,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
)
from aletheia_lab.diagnosis._development.policy import (
    build_tool_ledger,
    context_payload,
    evidence_sha256,
    response_schema_ref,
)
from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    ResolvedDiagnosisVariant,
    validate_variant_request_binding,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import DiagnosisVariantFairnessFreeze


def validate_request_against_authority(
    request: DevelopmentVariantRequest,
    *,
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    case: DevelopmentCase,
) -> None:
    """Reconcile a persisted request against the plan, freeze and registry."""

    request = DevelopmentVariantRequest.model_validate(request.model_dump(mode="python"))
    if (
        request.plan_sha256 != plan.plan_sha256
        or request.case_id != case.case_id
        or request.case_sha256 != case.case_sha256
    ):
        raise DevelopmentPilotError("development request differs from plan or case")
    spec = next(item for item in freeze.variants if item.variant_id == request.variant_id)
    prompt = freeze.prompt_policies[spec.prompt_policy_ref]
    if request.prompt_text != prompt.instruction_contract:
        raise DevelopmentPilotError("development request prompt differs from freeze")
    expected_context = context_payload(case)
    expected_evidence_hash = evidence_sha256(case)
    expected_ledger = build_tool_ledger(case, registry.require(request.variant_id))
    expected_ledger_hash = expected_ledger.ledger_sha256 if expected_ledger else None
    if request.context_payload != expected_context:
        raise DevelopmentPilotError("development request context differs from case")
    validate_variant_request_binding(
        registry,
        request.binding,
        context_sha256=canonical_execution_sha256(expected_context),
        evidence_content_sha256=expected_evidence_hash,
        tool_ledger_sha256=expected_ledger_hash,
    )
    if request.tool_ledger != expected_ledger:
        raise DevelopmentPilotError("development request tool ledger differs from policy")


def validate_response_against_authority(
    request: DevelopmentVariantRequest,
    response: DevelopmentVariantResponse,
    variant: ResolvedDiagnosisVariant,
    case: DevelopmentCase,
) -> None:
    response = DevelopmentVariantResponse.model_validate(response.model_dump(mode="python"))
    if (
        response.request_sha256 != request.request_sha256
        or response.variant_id != variant.variant_id
        or response.response_schema_ref != response_schema_ref(variant)
    ):
        raise DevelopmentPilotError("development response differs from request or variant")
    visible_ids = {item.evidence_id for item in case.evidence}
    if not set(response.cited_evidence_ids).issubset(visible_ids):
        raise DevelopmentPilotError("development response cites non-visible evidence")
    if variant.capabilities.citation_required and not response.cited_evidence_ids:
        raise DevelopmentPilotError("citation-required variant omitted citations")
    if not variant.capabilities.citation_required and response.cited_evidence_ids:
        raise DevelopmentPilotError("non-citation variant gained unregistered citations")
    expected_abstention = (
        variant.capabilities.abstention_required and case.expected_evidence_state == "insufficient"
    )
    if response.abstained != expected_abstention:
        raise DevelopmentPilotError("development abstention behavior differs from contract")
    if (variant.strategy == "deterministic_rules") != bool(response.rule_trace):
        raise DevelopmentPilotError("development deterministic rule trace is inconsistent")
    if (variant.strategy == "native_external") != (response.native_result is not None):
        raise DevelopmentPilotError("development native output shape is inconsistent")

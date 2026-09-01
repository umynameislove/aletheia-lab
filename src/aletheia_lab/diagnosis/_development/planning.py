"""Outcome-blind plan, case and request construction."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from aletheia_lab.diagnosis._development.contracts import (
    DEVELOPMENT_CASE_SCHEMA_VERSION,
    DEVELOPMENT_MODE,
    DEVELOPMENT_PLAN_SCHEMA_VERSION,
    DEVELOPMENT_REQUEST_SCHEMA_VERSION,
    DevelopmentCase,
    DevelopmentEvidenceItem,
    DevelopmentPilotError,
    DevelopmentPilotPlan,
    DevelopmentVariantRequest,
)
from aletheia_lab.diagnosis._development.policy import (
    build_tool_ledger,
    context_payload,
    evidence_sha256,
)
from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    ResolvedDiagnosisVariant,
    bind_variant_request,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import (
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
)
from aletheia_lab.project.identity import content_sha256


def build_development_evidence_item(
    *,
    evidence_id: str,
    kind: Literal["metric", "config", "log", "artifact", "lineage", "human_note"],
    title: str,
    content: str,
) -> DevelopmentEvidenceItem:
    """Build one synthetic visible item with a content-bound identity."""

    return DevelopmentEvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        title=title,
        content=content,
        content_sha256=content_sha256(content.encode("utf-8")),
    )


def build_development_case(
    *,
    case_id: str,
    expected_evidence_state: Literal["sufficient", "insufficient", "conflicting"],
    evidence: tuple[DevelopmentEvidenceItem, ...],
) -> DevelopmentCase:
    """Build an explicitly non-scientific synthetic development case."""

    identity_payload = {
        "schema_version": DEVELOPMENT_CASE_SCHEMA_VERSION,
        "case_id": case_id,
        "source": "synthetic_fixture",
        "protected_outcome_visible": False,
        "evaluator_metadata_visible": False,
        "expected_evidence_state": expected_evidence_state,
        "evidence": tuple(item.model_dump(mode="json") for item in evidence),
    }
    return DevelopmentCase(
        case_id=case_id,
        expected_evidence_state=expected_evidence_state,
        evidence=evidence,
        case_sha256=canonical_execution_sha256(identity_payload),
    )


def build_development_plan(
    *,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    cases: tuple[DevelopmentCase, ...],
) -> DevelopmentPilotPlan:
    """Bind the synthetic plan to the authoritative freeze and registry."""

    checked_freeze = DiagnosisVariantFairnessFreeze.model_validate(freeze.model_dump(mode="python"))
    checked_registry = DiagnosisVariantRegistry.model_validate(registry.model_dump(mode="python"))
    freeze_sha256 = canonical_execution_sha256(checked_freeze.model_dump(mode="json"))
    if checked_registry.freeze_sha256 != freeze_sha256:
        raise DevelopmentPilotError("registry does not belong to supplied freeze")
    identity_payload = {
        "schema_version": DEVELOPMENT_PLAN_SCHEMA_VERSION,
        "mode": DEVELOPMENT_MODE,
        "plan_version": "diagnosis-development/1",
        "freeze_sha256": freeze_sha256,
        "registry_sha256": checked_registry.registry_sha256,
        "variant_ids": REQUIRED_VARIANTS,
        "cases": tuple(item.model_dump(mode="json") for item in cases),
        "executor_identity": "deterministic-development-executor/1",
        "external_network_permitted": False,
        "live_provider_calls_permitted": False,
        "protected_outcomes_opened": False,
        "registered_attempts_consumed": 0,
        "scientific_interpretation_permitted": False,
    }
    return DevelopmentPilotPlan(
        plan_version="diagnosis-development/1",
        freeze_sha256=freeze_sha256,
        registry_sha256=checked_registry.registry_sha256,
        variant_ids=REQUIRED_VARIANTS,
        cases=cases,
        executor_identity="deterministic-development-executor/1",
        plan_sha256=canonical_execution_sha256(identity_payload),
    )


def load_development_plan(path: str | Path) -> DevelopmentPilotPlan:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DevelopmentPilotError("development plan must be a real file")
    return DevelopmentPilotPlan.model_validate_json(candidate.read_bytes())


def _validate_plan_bindings(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
) -> None:
    freeze_sha = canonical_execution_sha256(freeze.model_dump(mode="json"))
    if (
        plan.freeze_sha256 != freeze_sha
        or plan.freeze_sha256 != registry.freeze_sha256
        or plan.registry_sha256 != registry.registry_sha256
    ):
        raise DevelopmentPilotError("development plan differs from frozen registry")


def _build_request(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    case: DevelopmentCase,
    variant: ResolvedDiagnosisVariant,
) -> DevelopmentVariantRequest:
    spec = next(item for item in freeze.variants if item.variant_id == variant.variant_id)
    prompt = freeze.prompt_policies[spec.prompt_policy_ref]
    visible_context = context_payload(case)
    context_sha = canonical_execution_sha256(visible_context)
    evidence_sha = evidence_sha256(case)
    ledger = build_tool_ledger(case, variant)
    ledger_sha = ledger.ledger_sha256 if ledger else None
    binding = bind_variant_request(
        registry,
        variant_id=variant.variant_id,
        context_sha256=context_sha,
        evidence_content_sha256=evidence_sha,
        tool_ledger_sha256=ledger_sha,
    )
    identity_payload = {
        "schema_version": DEVELOPMENT_REQUEST_SCHEMA_VERSION,
        "mode": DEVELOPMENT_MODE,
        "plan_sha256": plan.plan_sha256,
        "case_id": case.case_id,
        "case_sha256": case.case_sha256,
        "variant_id": variant.variant_id,
        "binding": binding.model_dump(mode="json"),
        "prompt_text": prompt.instruction_contract,
        "prompt_content_sha256": content_sha256(prompt.instruction_contract.encode("utf-8")),
        "context_payload": visible_context,
        "context_sha256": context_sha,
        "evidence_content_sha256": evidence_sha,
        "tool_ledger": ledger.model_dump(mode="json") if ledger else None,
        "external_network_permitted": False,
        "live_provider_call": False,
        "protected_outcome_visible": False,
        "registered_attempt_consumed": False,
    }
    return DevelopmentVariantRequest(
        plan_sha256=plan.plan_sha256,
        case_id=case.case_id,
        case_sha256=case.case_sha256,
        variant_id=variant.variant_id,
        binding=binding,
        prompt_text=prompt.instruction_contract,
        prompt_content_sha256=content_sha256(prompt.instruction_contract.encode("utf-8")),
        context_payload=visible_context,
        context_sha256=context_sha,
        evidence_content_sha256=evidence_sha,
        tool_ledger=ledger,
        request_sha256=canonical_execution_sha256(identity_payload),
    )

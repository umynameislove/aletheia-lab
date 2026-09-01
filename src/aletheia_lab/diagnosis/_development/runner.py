"""Orchestration for the complete synthetic diagnosis development matrix."""

from __future__ import annotations

from aletheia_lab.diagnosis._development.contracts import (
    DEVELOPMENT_MANIFEST_SCHEMA_VERSION,
    DEVELOPMENT_MODE,
    DEVELOPMENT_RECORD_SCHEMA_VERSION,
    DevelopmentPilotError,
    DevelopmentPilotManifest,
    DevelopmentPilotPlan,
    DevelopmentRecordPointer,
    DevelopmentRunRecord,
    DevelopmentTerminalReceipt,
)
from aletheia_lab.diagnosis._development.executor import (
    DeterministicDevelopmentExecutor,
    DevelopmentVariantExecutor,
)
from aletheia_lab.diagnosis._development.planning import _build_request, _validate_plan_bindings
from aletheia_lab.diagnosis._development.resources import _resource_observation
from aletheia_lab.diagnosis._development.store import DevelopmentArtifactStore, _store_model_object
from aletheia_lab.diagnosis._development.validation import validate_response_against_authority
from aletheia_lab.diagnosis.variant_registry import DiagnosisVariantRegistry
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import DiagnosisVariantFairnessFreeze


def run_development_pilot(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    store: DevelopmentArtifactStore,
    *,
    executor: DevelopmentVariantExecutor | None = None,
) -> DevelopmentTerminalReceipt:
    """Exercise the complete synthetic matrix and atomically publish its terminal."""

    checked_plan = DevelopmentPilotPlan.model_validate(plan.model_dump(mode="python"))
    checked_registry = DiagnosisVariantRegistry.model_validate(registry.model_dump(mode="python"))
    checked_freeze = DiagnosisVariantFairnessFreeze.model_validate(freeze.model_dump(mode="python"))
    active_executor = executor or DeterministicDevelopmentExecutor()
    stage = "preflight"
    try:
        _validate_plan_bindings(checked_plan, checked_freeze, checked_registry)
        if type(active_executor) is not DeterministicDevelopmentExecutor:
            raise DevelopmentPilotError(
                "development mode permits only the frozen deterministic executor"
            )
        if active_executor.identity != checked_plan.executor_identity:
            raise DevelopmentPilotError("development executor identity differs from plan")
        if active_executor.external_calls:
            raise DevelopmentPilotError("external-call executor is forbidden in development mode")
        by_spec = {item.variant_id: item for item in checked_freeze.variants}
        objects: dict[str, bytes] = {}
        pointers: list[DevelopmentRecordPointer] = []
        for case in checked_plan.cases:
            _store_model_object(objects, case)
            for variant_id in checked_plan.variant_ids:
                stage = "build_request"
                variant = checked_registry.require(variant_id)
                request = _build_request(
                    checked_plan,
                    checked_freeze,
                    checked_registry,
                    case,
                    variant,
                )
                stage = "execute_fixture"
                response = active_executor.execute(request, variant, case)
                stage = "validate_response"
                validate_response_against_authority(request, response, variant, case)
                request_object = _store_model_object(objects, request)
                response_object = _store_model_object(objects, response)
                ledger_object = (
                    _store_model_object(objects, request.tool_ledger)
                    if request.tool_ledger is not None
                    else None
                )
                budget = checked_freeze.information_budgets[
                    by_spec[variant_id].information_budget_ref
                ]
                resources = _resource_observation(request)
                if (
                    resources.context_tokens_upper_bound > budget.maximum_context_tokens
                    or resources.retrieved_items > budget.maximum_retrieved_items
                    or resources.turns > budget.maximum_turns
                ):
                    raise DevelopmentPilotError("development request exceeds frozen budget")
                record_identity_payload = {
                    "schema_version": DEVELOPMENT_RECORD_SCHEMA_VERSION,
                    "case_id": case.case_id,
                    "variant_id": variant_id,
                    "request_object_sha256": request_object,
                    "response_object_sha256": response_object,
                    "tool_ledger_object_sha256": ledger_object,
                    "request_sha256": request.request_sha256,
                    "response_sha256": response.response_sha256,
                    "binding_sha256": request.binding.binding_sha256,
                    "resources": resources.model_dump(mode="json"),
                    "validation_status": "validated",
                    "fallback_used": False,
                    "protected_outcome_visible": False,
                }
                record = DevelopmentRunRecord(
                    case_id=case.case_id,
                    variant_id=variant_id,
                    request_object_sha256=request_object,
                    response_object_sha256=response_object,
                    tool_ledger_object_sha256=ledger_object,
                    request_sha256=request.request_sha256,
                    response_sha256=response.response_sha256,
                    binding_sha256=request.binding.binding_sha256,
                    resources=resources,
                    record_sha256=canonical_execution_sha256(record_identity_payload),
                )
                record_object = _store_model_object(objects, record)
                pointers.append(
                    DevelopmentRecordPointer(
                        case_id=case.case_id,
                        variant_id=variant_id,
                        record_object_sha256=record_object,
                    )
                )
        stage = "publish_terminal"
        manifest_identity_payload = {
            "schema_version": DEVELOPMENT_MANIFEST_SCHEMA_VERSION,
            "mode": DEVELOPMENT_MODE,
            "plan_sha256": checked_plan.plan_sha256,
            "freeze_sha256": checked_registry.freeze_sha256,
            "registry_sha256": checked_registry.registry_sha256,
            "executor_identity": active_executor.identity,
            "case_ids": tuple(item.case_id for item in checked_plan.cases),
            "variant_ids": checked_plan.variant_ids,
            "records": tuple(item.model_dump(mode="json") for item in pointers),
            "object_sha256s": tuple(sorted(objects)),
            "protected_outcomes_opened": False,
            "live_provider_calls": 0,
            "registered_attempts_consumed": 0,
            "scientific_interpretation_permitted": False,
        }
        manifest = DevelopmentPilotManifest(
            plan_sha256=checked_plan.plan_sha256,
            freeze_sha256=checked_registry.freeze_sha256,
            registry_sha256=checked_registry.registry_sha256,
            executor_identity="deterministic-development-executor/1",
            case_ids=tuple(item.case_id for item in checked_plan.cases),
            variant_ids=checked_plan.variant_ids,
            records=tuple(pointers),
            object_sha256s=tuple(sorted(objects)),
            manifest_sha256=canonical_execution_sha256(manifest_identity_payload),
        )
        return store.publish(manifest, objects)
    except Exception as exc:
        store.record_failure(
            plan_sha256=checked_plan.plan_sha256,
            registry_sha256=checked_registry.registry_sha256,
            stage=stage,
            exception=exc,
        )
        if isinstance(exc, DevelopmentPilotError):
            raise
        raise DevelopmentPilotError(f"development pilot failed closed at {stage}") from exc

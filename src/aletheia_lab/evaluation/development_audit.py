"""Offline fairness and integrity audit for synthetic diagnosis development runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis._development.contracts import (
    DevelopmentPilotError,
    DevelopmentPilotPlan,
    DevelopmentRunRecord,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
)
from aletheia_lab.diagnosis._development.resources import (
    resource_observation_for_request,
)
from aletheia_lab.diagnosis._development.store import (
    DevelopmentArtifactStore,
    load_run_record,
    load_run_request,
    load_run_response,
)
from aletheia_lab.diagnosis._development.validation import (
    validate_request_against_authority,
    validate_response_against_authority,
)
from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    VariantId,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import (
    MATCHED_MODEL_VARIANTS,
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
    audit_diagnosis_variant_fairness,
)
from aletheia_lab.project.identity import SHA256_PATTERN, canonical_project_json, content_sha256

DEVELOPMENT_AUDIT_FINDING_SCHEMA_VERSION: Final = (
    "diagnosis-development-audit-finding/v1"
)
DEVELOPMENT_AUDIT_SCHEMA_VERSION: Final = "diagnosis-development-audit/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
AuditFindingStatus = Literal["pass", "block"]
AuditStatus = Literal[
    "development_pilot_validated",
    "development_pilot_blocked",
]


class DevelopmentPilotAuditError(ValueError):
    """Raised when a development run cannot be safely audited."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class DevelopmentAuditFinding(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-audit-finding/v1"] = (
        DEVELOPMENT_AUDIT_FINDING_SCHEMA_VERSION
    )
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: AuditFindingStatus
    scope: tuple[str, ...]
    evidence: str


class DevelopmentPilotAuditReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-audit/v1"] = (
        DEVELOPMENT_AUDIT_SCHEMA_VERSION
    )
    run_id: str = Field(pattern=r"^devrun-[0-9a-f]{64}$")
    plan_sha256: Sha256
    freeze_sha256: Sha256
    registry_sha256: Sha256
    manifest_sha256: Sha256
    variant_ids: tuple[VariantId, ...]
    case_count: int = Field(gt=0)
    record_count: int = Field(gt=0)
    expected_record_count: int = Field(gt=0)
    findings: tuple[DevelopmentAuditFinding, ...]
    blocker_codes: tuple[str, ...]
    status: AuditStatus
    protected_outcomes_opened: Literal[False] = False
    live_provider_calls: Literal[0] = 0
    registered_attempts_consumed: Literal[0] = 0
    scientific_interpretation_permitted: Literal[False] = False
    audit_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"audit_sha256"})

    @model_validator(mode="after")
    def _receipt_reconciles(self) -> Self:
        codes = tuple(item.code for item in self.findings)
        if len(codes) != len(set(codes)):
            raise ValueError("development audit finding codes must be unique")
        blockers = tuple(sorted(item.code for item in self.findings if item.status == "block"))
        expected_status: AuditStatus = (
            "development_pilot_blocked"
            if blockers
            else "development_pilot_validated"
        )
        if self.blocker_codes != blockers or self.status != expected_status:
            raise ValueError("development audit status does not match findings")
        if (
            self.record_count != self.expected_record_count
            and "complete_variant_matrix" not in blockers
        ):
            raise ValueError("incomplete development matrix must block")
        if self.audit_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development audit hash does not match receipt")
        return self


class _AuditState:
    def __init__(self) -> None:
        self.requests: list[DevelopmentVariantRequest] = []
        self.responses: list[DevelopmentVariantResponse] = []
        self.records: list[DevelopmentRunRecord] = []
        self.expected_object_sha256s: set[str] = set()


def audit_development_pilot(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    store: DevelopmentArtifactStore,
    run_id: str,
) -> DevelopmentPilotAuditReceipt:
    """Recompute every engineering and fairness invariant from stored bytes."""

    try:
        checked_plan = DevelopmentPilotPlan.model_validate(plan.model_dump(mode="python"))
        checked_freeze = DiagnosisVariantFairnessFreeze.model_validate(
            freeze.model_dump(mode="python")
        )
        checked_registry = DiagnosisVariantRegistry.model_validate(
            registry.model_dump(mode="python")
        )
        store.verify_run(run_id)
        terminal = store.load_terminal(run_id)
        manifest = store.load_manifest(run_id)
        freeze_sha256 = canonical_execution_sha256(
            checked_freeze.model_dump(mode="json")
        )
        if (
            manifest.plan_sha256 != checked_plan.plan_sha256
            or manifest.freeze_sha256 != freeze_sha256
            or manifest.freeze_sha256 != checked_registry.freeze_sha256
            or manifest.registry_sha256 != checked_registry.registry_sha256
            or terminal.run_id != run_id
        ):
            raise DevelopmentPilotAuditError(
                "development terminal is not bound to the supplied authorities"
            )
        state = _load_and_reconcile_records(
            checked_plan,
            checked_freeze,
            checked_registry,
            store,
            run_id,
        )
        findings = _findings(
            checked_plan,
            checked_freeze,
            checked_registry,
            manifest.object_sha256s,
            state,
        )
    except (
        DevelopmentPilotError,
        OSError,
        ValueError,
        KeyError,
        StopIteration,
    ) as exc:
        if isinstance(exc, DevelopmentPilotAuditError):
            raise
        raise DevelopmentPilotAuditError("development pilot audit failed closed") from exc

    blocker_codes = tuple(sorted(item.code for item in findings if item.status == "block"))
    expected_count = len(checked_plan.cases) * len(REQUIRED_VARIANTS)
    status: AuditStatus = (
        "development_pilot_blocked"
        if blocker_codes
        else "development_pilot_validated"
    )
    identity_payload = {
        "schema_version": DEVELOPMENT_AUDIT_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_sha256": checked_plan.plan_sha256,
        "freeze_sha256": checked_registry.freeze_sha256,
        "registry_sha256": checked_registry.registry_sha256,
        "manifest_sha256": manifest.manifest_sha256,
        "variant_ids": checked_plan.variant_ids,
        "case_count": len(checked_plan.cases),
        "record_count": len(state.records),
        "expected_record_count": expected_count,
        "findings": tuple(item.model_dump(mode="json") for item in findings),
        "blocker_codes": blocker_codes,
        "status": status,
        "protected_outcomes_opened": False,
        "live_provider_calls": 0,
        "registered_attempts_consumed": 0,
        "scientific_interpretation_permitted": False,
    }
    return DevelopmentPilotAuditReceipt(
        run_id=run_id,
        plan_sha256=checked_plan.plan_sha256,
        freeze_sha256=checked_registry.freeze_sha256,
        registry_sha256=checked_registry.registry_sha256,
        manifest_sha256=manifest.manifest_sha256,
        variant_ids=checked_plan.variant_ids,
        case_count=len(checked_plan.cases),
        record_count=len(state.records),
        expected_record_count=expected_count,
        findings=findings,
        blocker_codes=blocker_codes,
        status=status,
        audit_sha256=canonical_execution_sha256(identity_payload),
    )


def require_development_pilot_ready(receipt: DevelopmentPilotAuditReceipt) -> None:
    """Block later registration work unless every development invariant passed."""

    checked = DevelopmentPilotAuditReceipt.model_validate(
        receipt.model_dump(mode="python")
    )
    if checked.status != "development_pilot_validated" or checked.blocker_codes:
        raise DevelopmentPilotAuditError("development pilot has unresolved blockers")


def _load_and_reconcile_records(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    store: DevelopmentArtifactStore,
    run_id: str,
) -> _AuditState:
    manifest = store.load_manifest(run_id)
    cases = {item.case_id: item for item in plan.cases}
    state = _AuditState()
    for case in plan.cases:
        state.expected_object_sha256s.add(_model_object_sha256(case))
    for pointer in manifest.records:
        case = cases[pointer.case_id]
        record = load_run_record(store, run_id, pointer.record_object_sha256)
        if record.case_id != pointer.case_id or record.variant_id != pointer.variant_id:
            raise DevelopmentPilotAuditError("record pointer identifies different content")
        request = load_run_request(store, run_id, record.request_object_sha256)
        response = load_run_response(store, run_id, record.response_object_sha256)
        variant = registry.require(pointer.variant_id)
        validate_request_against_authority(
            request,
            plan=plan,
            freeze=freeze,
            registry=registry,
            case=case,
        )
        validate_response_against_authority(request, response, variant, case)
        if record.request_sha256 != request.request_sha256:
            raise DevelopmentPilotAuditError("record request identity differs from object")
        if record.response_sha256 != response.response_sha256:
            raise DevelopmentPilotAuditError("record response identity differs from object")
        if record.binding_sha256 != request.binding.binding_sha256:
            raise DevelopmentPilotAuditError("record binding identity differs from request")
        if record.resources != resource_observation_for_request(request):
            raise DevelopmentPilotAuditError("record resources differ from request")
        expected_ledger_object = (
            _model_object_sha256(request.tool_ledger)
            if request.tool_ledger is not None
            else None
        )
        if record.tool_ledger_object_sha256 != expected_ledger_object:
            raise DevelopmentPilotAuditError("record tool ledger link differs from request")
        state.requests.append(request)
        state.responses.append(response)
        state.records.append(record)
        state.expected_object_sha256s.update(
            {
                pointer.record_object_sha256,
                record.request_object_sha256,
                record.response_object_sha256,
            }
        )
        if record.tool_ledger_object_sha256 is not None:
            state.expected_object_sha256s.add(record.tool_ledger_object_sha256)
    return state


def _findings(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    manifest_objects: tuple[str, ...],
    state: _AuditState,
) -> tuple[DevelopmentAuditFinding, ...]:
    expected_count = len(plan.cases) * len(REQUIRED_VARIANTS)
    fairness = audit_diagnosis_variant_fairness(freeze)
    by_variant = {item.variant_id: item for item in registry.variants}
    requests_by_case: dict[str, dict[str, DevelopmentVariantRequest]] = defaultdict(dict)
    for request in state.requests:
        requests_by_case[request.case_id][request.variant_id] = request

    exact_matrix = (
        len(state.records) == expected_count
        and all(
            tuple(requests_by_case[case.case_id]) == REQUIRED_VARIANTS
            for case in plan.cases
        )
    )
    matched_models = {
        by_variant[item].model_policy_sha256 for item in MATCHED_MODEL_VARIANTS
    }
    matched_budgets = {
        by_variant[item].information_budget_sha256 for item in MATCHED_MODEL_VARIANTS
    }
    matched_context = all(
        len(
            {
                requests_by_case[case.case_id][variant_id].context_sha256
                for variant_id in MATCHED_MODEL_VARIANTS
            }
        )
        == 1
        and len(
            {
                requests_by_case[case.case_id][variant_id].evidence_content_sha256
                for variant_id in MATCHED_MODEL_VARIANTS
            }
        )
        == 1
        for case in plan.cases
    ) if exact_matrix else False
    budget_by_variant = {
        item.variant_id: freeze.information_budgets[item.information_budget_ref]
        for item in freeze.variants
    }
    budgets_pass = all(
        record.resources.context_tokens_upper_bound
        <= budget_by_variant[record.variant_id].maximum_context_tokens
        and record.resources.retrieved_items
        <= budget_by_variant[record.variant_id].maximum_retrieved_items
        and record.resources.turns <= budget_by_variant[record.variant_id].maximum_turns
        and record.resources.provider_calls == 0
        for record in state.records
    )
    tools_pass = all(
        (request.tool_ledger is not None)
        == by_variant[request.variant_id].capabilities.tool_ledger_required
        and (
            request.tool_ledger is None
            or (
                not request.tool_ledger.web_used
                and not request.tool_ledger.shell_used
                and not request.tool_ledger.project_execution_used
                and not request.tool_ledger.fallback_used
            )
        )
        for request in state.requests
    )
    boundary_pass = (
        not plan.external_network_permitted
        and not plan.live_provider_calls_permitted
        and not plan.protected_outcomes_opened
        and plan.registered_attempts_consumed == 0
        and not plan.scientific_interpretation_permitted
        and all(
            not request.live_provider_call
            and not request.protected_outcome_visible
            and not request.registered_attempt_consumed
            for request in state.requests
        )
        and all(not response.scientific_interpretation_permitted for response in state.responses)
    )
    strata_pass = (
        all(by_variant[item].pooling_policy == "matched_primary" for item in MATCHED_MODEL_VARIANTS)
        and by_variant["B0"].pooling_policy == "separate"
        and by_variant["B3"].pooling_policy == "external_only"
        and by_variant["CodeGraph"].pooling_policy == "separate"
        and by_variant["FULL"].pooling_policy == "separate"
    )
    objects_pass = set(manifest_objects) == state.expected_object_sha256s

    return (
        _finding(
            "implementation_artifacts_resolve",
            not fairness.blocker_codes and tuple(by_variant) == REQUIRED_VARIANTS,
            REQUIRED_VARIANTS,
            "all frozen factories resolve through the authoritative nine-variant registry",
        ),
        _finding(
            "complete_variant_matrix",
            exact_matrix,
            tuple(item.case_id for item in plan.cases),
            f"{len(state.records)} of {expected_count} development records are terminal",
        ),
        _finding(
            "request_authority_binding",
            len(state.requests) == expected_count,
            REQUIRED_VARIANTS,
            "every persisted request reconciles with the plan, case, freeze and registry",
        ),
        _finding(
            "content_addressed_artifacts",
            objects_pass,
            ("objects", "manifest", "terminal"),
            "the referenced object census is exact and the store verified every byte hash",
        ),
        _finding(
            "response_contracts",
            len(state.responses) == expected_count,
            REQUIRED_VARIANTS,
            "every synthetic response satisfies its registered capability envelope",
        ),
        _finding(
            "matched_model_parity",
            len(matched_models) == 1 and None not in matched_models,
            MATCHED_MODEL_VARIANTS,
            "matched variants share one frozen provider and model policy hash",
        ),
        _finding(
            "matched_information_parity",
            len(matched_budgets) == 1,
            MATCHED_MODEL_VARIANTS,
            "matched variants share one frozen information-budget hash",
        ),
        _finding(
            "matched_context_parity",
            matched_context,
            MATCHED_MODEL_VARIANTS,
            "matched requests share case-level context and evidence identities",
        ),
        _finding(
            "resource_budget_compliance",
            budgets_pass,
            REQUIRED_VARIANTS,
            "observed context, retrieval, turn, tool and provider counts stay within policy",
        ),
        _finding(
            "tool_ledger_and_fallback_policy",
            tools_pass and all(not item.fallback_used for item in state.records),
            REQUIRED_VARIANTS,
            "required ledgers are exact; web, shell, project execution and fallback are absent",
        ),
        _finding(
            "reporting_strata_preserved",
            strata_pass,
            REQUIRED_VARIANTS,
            "matched, reference, external and system configurations retain separate pooling",
        ),
        _finding(
            "development_only_boundary",
            boundary_pass,
            ("synthetic fixtures",),
            "no live provider, protected outcome, registered attempt or scientific result exists",
        ),
    )


def _finding(
    code: str,
    passed: bool,
    scope: tuple[str, ...],
    evidence: str,
) -> DevelopmentAuditFinding:
    return DevelopmentAuditFinding(
        code=code,
        status="pass" if passed else "block",
        scope=scope,
        evidence=evidence,
    )


def _model_object_sha256(model: BaseModel) -> str:
    payload = (canonical_project_json(model.model_dump(mode="json")) + "\n").encode(
        "utf-8"
    )
    return content_sha256(payload)


__all__ = [
    "DevelopmentAuditFinding",
    "DevelopmentPilotAuditError",
    "DevelopmentPilotAuditReceipt",
    "audit_development_pilot",
    "require_development_pilot_ready",
]

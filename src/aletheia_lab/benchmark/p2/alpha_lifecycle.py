"""Outcome-blind orchestration for the Phase 2 alpha lifecycle.

This module does not execute a model and does not choose measurements.  It
consumes mechanism candidates that already passed the mechanism trust boundary,
derives every lifecycle decision allowed by the frozen contract, activates
reserves only for primary technical rejections, and emits the complete nine-file
artifact set.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.artifacts import P2ContractArtifacts
from aletheia_lab.benchmark.p2.binary_evaluation import MetricComparison
from aletheia_lab.benchmark.p2.contracts import (
    AdmissionLedger,
    AdmissionRecord,
    AlphaValidityReport,
    CandidateExecution,
    CandidatePlan,
    CandidateSlot,
    ClassificationLedger,
    ClassificationRecord,
    ContextCensus,
    DuplicateAudit,
    ExecutedCandidate,
    FamilyCensus,
    FamilyCensusEntry,
    TechnicalDisposition,
    TechnicalDispositionEntry,
    TechnicalRejectionReason,
    ValidExclusionReason,
)
from aletheia_lab.benchmark.p2.data_drift import DriftMetricComparison
from aletheia_lab.benchmark.p2.evidence_projection import (
    MechanismDiagnosisEvidence,
    build_diagnosis_contexts,
    performance_evidence_from,
)
from aletheia_lab.benchmark.p2.identity import candidate_id_for, proposed_family_sha256
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_plan

_POLICY_BY_FAULT = {
    "data_drift": "accuracy-regression/v1",
    "label_noise": "label-noise-impact/alpha-v1",
    "preprocessing_bug": "preprocessing-bug-impact/alpha-v1",
}
_CLASS_BY_OUTCOME = {
    "regression": "eligible_failure",
    "stable": "stable_control",
    "improvement": "improvement_control",
}
_EXTERNAL_EXCLUSIONS = {
    "condition_construction_failure",
    "evidence_leakage",
    "artifact_binding_failure",
}
_DUPLICATE_EXCLUSIONS = {
    "exact_identity_duplicate",
    "effective_intervention_duplicate",
}

AlphaMetricComparison = MetricComparison | DriftMetricComparison


class AlphaLifecycleError(ContractViolation):
    """Raised when measured candidates cannot form one honest alpha run."""


def _fail(message: str) -> NoReturn:
    raise AlphaLifecycleError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvaluatedAlphaCandidate(_StrictFrozenModel):
    """A technically valid mechanism candidate plus its measured evaluation."""

    kind: Literal["evaluated"] = "evaluated"
    candidate: ValidatedMechanismCandidate
    comparison: AlphaMetricComparison
    diagnosis_evidence: MechanismDiagnosisEvidence | None = None
    equivalence_checks_passed: bool | None = None
    exclusion_reason: ValidExclusionReason | None = None
    exclusion_detail: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _input_boundaries_hold(self) -> EvaluatedAlphaCandidate:
        candidate = ValidatedMechanismCandidate.model_validate(self.candidate.model_dump())
        comparison = type(self.comparison).model_validate(self.comparison.model_dump())
        if candidate.disposition.disposition != "technically_valid":
            raise ValueError("an evaluated alpha candidate must be technically valid")
        if self.exclusion_reason in _DUPLICATE_EXCLUSIONS | {"control_direction_violation"}:
            raise ValueError("duplicate and control-direction exclusions are lifecycle-derived")
        if self.exclusion_reason is not None and self.exclusion_reason not in _EXTERNAL_EXCLUSIONS:
            raise ValueError("unsupported externally reviewed exclusion reason")
        if self.exclusion_reason is None and self.exclusion_detail is not None:
            raise ValueError("exclusion_detail requires an exclusion_reason")

        benign = candidate.execution.role == "designed_benign_control"
        if benign:
            if self.equivalence_checks_passed is not True:
                raise ValueError("a technically valid benign control requires verified equivalence")
            if self.diagnosis_evidence is not None:
                raise ValueError("a benign control must not carry diagnosis evidence")
        elif self.equivalence_checks_passed is not None:
            raise ValueError("equivalence_checks_passed is reserved for benign controls")

        if self.diagnosis_evidence is not None:
            expected_performance = performance_evidence_from(comparison)
            if self.diagnosis_evidence.performance != expected_performance:
                raise ValueError(
                    "diagnosis evidence performance must be derived from the measured comparison"
                )
        return self


class RejectedAlphaCandidate(_StrictFrozenModel):
    """A failed execution retained in the technical rejection ledger."""

    kind: Literal["technical_rejection"] = "technical_rejection"
    execution: ExecutedCandidate
    disposition: TechnicalDispositionEntry

    @model_validator(mode="after")
    def _is_a_rejection(self) -> RejectedAlphaCandidate:
        execution = ExecutedCandidate.model_validate(self.execution.model_dump())
        disposition = TechnicalDispositionEntry.model_validate(self.disposition.model_dump())
        if disposition.disposition != "technical_rejected":
            raise ValueError("a rejected alpha candidate must carry a technical rejection")
        if disposition.candidate_id != execution.candidate_id:
            raise ValueError("technical rejection must describe the executed candidate")
        return self


AlphaCandidateResult = EvaluatedAlphaCandidate | RejectedAlphaCandidate


def execution_for_slot(slot: CandidateSlot) -> ExecutedCandidate:
    """Return the only execution record that can describe a frozen slot."""

    fingerprint = proposed_family_sha256(slot.identity)
    return ExecutedCandidate(
        candidate_id=candidate_id_for(slot_id=slot.slot_id, family_fingerprint=fingerprint),
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        role=slot.role,
        slot_kind=slot.slot_kind,
        proposed_family_sha256=fingerprint,
        dataset_sha256=slot.identity.dataset_sha256,
        model_data_split_manifest_sha256=slot.identity.model_data_split_manifest_sha256,
    )


def record_technical_rejection(
    *,
    slot: CandidateSlot,
    reason: TechnicalRejectionReason,
    detail: str | None = None,
) -> RejectedAlphaCandidate:
    """Record a failed slot without inventing a classification or family."""

    execution = execution_for_slot(slot)
    return RejectedAlphaCandidate(
        execution=execution,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technical_rejected",
            rejection_reason=reason,
            detail=detail,
        ),
    )


def _execution(result: AlphaCandidateResult) -> ExecutedCandidate:
    if isinstance(result, EvaluatedAlphaCandidate):
        return result.candidate.execution
    return result.execution


def _disposition(result: AlphaCandidateResult) -> TechnicalDispositionEntry:
    if isinstance(result, EvaluatedAlphaCandidate):
        return result.candidate.disposition
    return result.disposition


def _validate_result_binding(result: AlphaCandidateResult, slot: CandidateSlot) -> None:
    expected = execution_for_slot(slot)
    actual = _execution(result)
    if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
        _fail(f"result for {slot.slot_id} does not bind the frozen slot identity")
    if isinstance(result, EvaluatedAlphaCandidate):
        candidate = ValidatedMechanismCandidate.model_validate(result.candidate.model_dump())
        if candidate.slot_id != slot.slot_id:
            _fail(f"validated mechanism candidate belongs to another slot: {slot.slot_id}")


def _validate_execution_set(
    *, plan: CandidatePlan, by_slot: dict[str, AlphaCandidateResult]
) -> None:
    plan_slots = {slot.slot_id: slot for slot in plan.slots}
    unknown = set(by_slot) - set(plan_slots)
    if unknown:
        _fail(f"alpha run contains slots outside the frozen plan: {sorted(unknown)}")
    primary = {slot.slot_id for slot in plan.slots if slot.slot_kind == "primary"}
    missing_primary = primary - set(by_slot)
    if missing_primary:
        _fail(f"all primary slots must execute: missing={sorted(missing_primary)}")

    rejected_primary: Counter[str] = Counter()
    for slot_id in primary:
        if _disposition(by_slot[slot_id]).disposition == "technical_rejected":
            rejected_primary[plan_slots[slot_id].fault_type] += 1

    for fault_type in _POLICY_BY_FAULT:
        reserves = sorted(
            (
                slot
                for slot in plan.slots
                if slot.slot_kind == "reserve" and slot.fault_type == fault_type
            ),
            key=lambda slot: slot.reserve_order or 0,
        )
        required = min(rejected_primary[fault_type], len(reserves))
        expected = {slot.slot_id for slot in reserves[:required]}
        observed = {slot.slot_id for slot in reserves if slot.slot_id in by_slot}
        if observed != expected:
            _fail(
                f"reserve execution for {fault_type} must replace primary technical "
                f"rejections in order; expected={sorted(expected)}; observed={sorted(observed)}"
            )


def _classification(result: EvaluatedAlphaCandidate) -> ClassificationRecord:
    execution = result.candidate.execution
    comparison = type(result.comparison).model_validate(result.comparison.model_dump())
    outcome = comparison.measured_primary_outcome
    family_class: str | None
    deviation: str | None = None
    if execution.role == "designed_benign_control":
        measured_outcome = "benign"
        family_class = "benign_control"
    elif execution.role == "designed_improvement_control" and outcome == "regression":
        measured_outcome = "regression"
        family_class = None
        deviation = "prespecified improvement control moved the primary metric downward"
    else:
        measured_outcome = outcome
        family_class = _CLASS_BY_OUTCOME[outcome]
        if execution.role == "designed_improvement_control" and outcome == "stable":
            deviation = "prespecified improvement control remained inside the stable band"
    return ClassificationRecord(
        schema_version="p2-classification-record/1",
        candidate_id=execution.candidate_id,
        role=execution.role,
        eligibility_policy_version=_POLICY_BY_FAULT[execution.fault_type],  # type: ignore[arg-type]
        primary_metric="accuracy",
        reference_value=comparison.reference.accuracy,
        observed_value=comparison.observed.accuracy,
        delta=comparison.accuracy_delta,
        threshold=comparison.primary_threshold,
        measured_outcome=measured_outcome,  # type: ignore[arg-type]
        family_class=family_class,  # type: ignore[arg-type]
        equivalence_checks_passed=result.equivalence_checks_passed,
        deviation_note=deviation,
    )


def _duplicate_exclusions(audit: DuplicateAudit) -> dict[str, ValidExclusionReason]:
    exclusions: dict[str, ValidExclusionReason] = {}
    for finding in audit.findings:
        if finding.kind == "near_duplicate":
            continue
        reason: ValidExclusionReason = (
            "exact_identity_duplicate"
            if finding.kind == "exact_identity"
            else "effective_intervention_duplicate"
        )
        if finding.candidate_id in exclusions:
            _fail("one candidate cannot carry multiple exact/effective duplicate exclusions")
        exclusions[finding.candidate_id] = reason
    return exclusions


def _gate_report(
    *,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
    admissions: AdmissionLedger,
    census: FamilyCensus,
    contexts: ContextCensus,
) -> AlphaValidityReport:
    accepted_ids = {
        record.candidate_id for record in admissions.entries if record.admission == "accepted"
    }
    accepted_execution = [item for item in execution.executed if item.candidate_id in accepted_ids]
    mechanism_coverage = all(
        sum(
            item.fault_type == fault_type and item.role == "fault_directed"
            for item in accepted_execution
        )
        >= 2
        and sum(
            item.fault_type == fault_type and item.role != "fault_directed"
            for item in accepted_execution
        )
        >= 1
        for fault_type in _POLICY_BY_FAULT
    )
    accepted = len(admissions.entries) - sum(
        record.admission == "excluded_valid" for record in admissions.entries
    )
    if accepted < 12 or not mechanism_coverage:
        gate_status = "fail"
        deviation = (
            "alpha acceptance floor or per-mechanism coverage requirement was not satisfied"
        )
    elif accepted < 15:
        gate_status = "pass_with_deviation"
        deviation = "alpha passed the 12-family floor but did not reach the 15-family target"
    else:
        gate_status = "pass"
        deviation = None
    classes = Counter(entry.family_class for entry in census.entries)
    return AlphaValidityReport(
        schema_version="p2-alpha-validity-report/1",
        primary_planned=plan.primary_planned,
        reserve_planned=plan.reserve_planned,
        planned_total=len(plan.slots),
        executed=len(execution.executed),
        activated_reserve=sum(item.slot_kind == "reserve" for item in execution.executed),
        inactive_reserve=len(execution.inactive_reserve_slot_ids),
        technically_valid=sum(
            entry.disposition == "technically_valid" for entry in disposition.entries
        ),
        technical_rejected=sum(
            entry.disposition == "technical_rejected" for entry in disposition.entries
        ),
        accepted=accepted,
        excluded_valid=len(admissions.entries) - accepted,
        eligible_failure=classes["eligible_failure"],
        stable_control=classes["stable_control"],
        improvement_control=classes["improvement_control"],
        benign_control=classes["benign_control"],
        context_count=len(contexts.entries),
        mechanism_coverage_passed=mechanism_coverage,
        gate_status=gate_status,  # type: ignore[arg-type]
        deviation_note=deviation,
    )


def assemble_alpha_artifacts(
    *,
    plan: CandidatePlan,
    results: tuple[AlphaCandidateResult, ...],
    duplicate_audit: DuplicateAudit,
) -> P2ContractArtifacts:
    """Derive and cross-validate the complete alpha lifecycle artifact set."""

    plan = CandidatePlan.model_validate(plan.model_dump())
    duplicate_audit = DuplicateAudit.model_validate(duplicate_audit.model_dump())
    validate_frozen_alpha_plan(plan)
    by_slot: dict[str, AlphaCandidateResult] = {}
    for raw_result in results:
        result = type(raw_result).model_validate(raw_result.model_dump())
        slot_id = _execution(result).slot_id
        if slot_id in by_slot:
            _fail(f"alpha run contains duplicate result for slot {slot_id}")
        by_slot[slot_id] = result
    _validate_execution_set(plan=plan, by_slot=by_slot)

    ordered_results = tuple(by_slot[slot.slot_id] for slot in plan.slots if slot.slot_id in by_slot)
    slot_by_id = {slot.slot_id: slot for slot in plan.slots}
    for result in ordered_results:
        _validate_result_binding(result, slot_by_id[_execution(result).slot_id])

    execution = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=tuple(_execution(result) for result in ordered_results),
        inactive_reserve_slot_ids=tuple(
            slot.slot_id
            for slot in plan.slots
            if slot.slot_kind == "reserve" and slot.slot_id not in by_slot
        ),
    )
    disposition = TechnicalDisposition(
        schema_version="p2-technical-disposition/1",
        entries=tuple(_disposition(result) for result in ordered_results),
    )
    evaluated = tuple(
        result for result in ordered_results if isinstance(result, EvaluatedAlphaCandidate)
    )
    classifications = ClassificationLedger(
        schema_version="p2-classification-ledger/1",
        entries=tuple(_classification(result) for result in evaluated),
    )
    classification_by_id = {entry.candidate_id: entry for entry in classifications.entries}
    result_by_id = {result.candidate.candidate_id: result for result in evaluated}
    duplicate_exclusions = _duplicate_exclusions(duplicate_audit)

    admission_entries: list[AdmissionRecord] = []
    census_entries: list[FamilyCensusEntry] = []
    for result in evaluated:
        candidate = result.candidate
        classification = classification_by_id[candidate.candidate_id]
        exclusion = duplicate_exclusions.get(candidate.candidate_id)
        detail = None
        if exclusion is not None and result.exclusion_reason is not None:
            _fail(
                f"candidate {candidate.candidate_id} has conflicting duplicate and "
                "reviewed exclusion reasons"
            )
        if classification.family_class is None:
            exclusion = "control_direction_violation"
            detail = classification.deviation_note
        elif exclusion is None and result.exclusion_reason is not None:
            exclusion = result.exclusion_reason
            detail = result.exclusion_detail
        if exclusion is not None:
            admission_entries.append(
                AdmissionRecord(
                    schema_version="p2-admission-record/1",
                    candidate_id=candidate.candidate_id,
                    admission="excluded_valid",
                    exclusion_reason=exclusion,
                    detail=detail,
                )
            )
            continue
        family_id = f"p2-family-{candidate.proposed_family_sha256}"
        family_class = cast(
            Literal[
                "eligible_failure",
                "stable_control",
                "improvement_control",
                "benign_control",
            ],
            classification.family_class,
        )
        admission_entries.append(
            AdmissionRecord(
                schema_version="p2-admission-record/1",
                candidate_id=candidate.candidate_id,
                admission="accepted",
                case_family_id=family_id,
                family_class=family_class,
            )
        )
        census_entries.append(
            FamilyCensusEntry(
                case_family_id=family_id,
                candidate_id=candidate.candidate_id,
                fault_type=candidate.fault_type,
                family_class=family_class,
                proposed_family_sha256=candidate.proposed_family_sha256,
            )
        )

    admissions = AdmissionLedger(
        schema_version="p2-admission-ledger/1", entries=tuple(admission_entries)
    )
    census = FamilyCensus(schema_version="p2-family-census/1", entries=tuple(census_entries))
    contexts = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for family in census.entries
            for context in build_diagnosis_contexts(
                candidate=result_by_id[family.candidate_id].candidate,
                family=family,
                evidence=result_by_id[family.candidate_id].diagnosis_evidence,
            )
        ),
    )
    report = _gate_report(
        plan=plan,
        execution=execution,
        disposition=disposition,
        admissions=admissions,
        census=census,
        contexts=contexts,
    )
    artifacts = P2ContractArtifacts(
        plan=plan,
        execution=execution,
        disposition=disposition,
        classifications=classifications,
        admissions=admissions,
        census=census,
        contexts=contexts,
        duplicate_audit=duplicate_audit,
        report=report,
    )
    artifacts.validate()
    return artifacts

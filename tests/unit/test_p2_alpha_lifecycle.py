"""End-to-end tests for frozen alpha planning and lifecycle assembly."""

from __future__ import annotations

import json
import unicodedata

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.alpha_lifecycle import (
    AlphaLifecycleError,
    EvaluatedAlphaCandidate,
    assemble_alpha_artifacts,
    execution_for_slot,
    record_technical_rejection,
)
from aletheia_lab.benchmark.p2.alpha_plan import AlphaSystemBinding, build_frozen_alpha_plan
from aletheia_lab.benchmark.p2.artifacts import load_contract_store, save_contract_store
from aletheia_lab.benchmark.p2.binary_evaluation import (
    BinaryMetricSnapshot,
    ConfusionMatrix,
    MetricComparison,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    DuplicateAudit,
    DuplicateFinding,
    ReserveRecoveryAuthorization,
    ReserveRecoveryObservation,
)
from aletheia_lab.benchmark.p2.coverage import (
    CoverageContractError,
    assess_mechanism_coverage,
    build_candidate_census,
)
from aletheia_lab.benchmark.p2.data_drift import DriftMetricComparison
from aletheia_lab.benchmark.p2.evidence_projection import (
    CategoryShare,
    DataDriftDiagnosisEvidence,
    DistributionSnapshot,
    LabelDiagnosisEvidence,
    PreprocessingDiagnosisEvidence,
    SchemaComparison,
    SecondaryComparison,
    TargetProjectionComparison,
    TransformSignatureComparison,
    performance_evidence_from,
)
from aletheia_lab.benchmark.p2.label_noise import (
    TargetDistributionComparison,
    TargetQualityAudit,
    wilson_interval,
)
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate

_HEX = {letter: letter * 64 for letter in "abcdef"}


def _binding() -> AlphaSystemBinding:
    return AlphaSystemBinding(
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=_HEX["a"],
        model_data_split_manifest_sha256=_HEX["b"],
        model_specification_sha256=_HEX["c"],
        preprocessing_specification_sha256=_HEX["d"],
        reference_construction_id="clean-test-reference/v1",
        data_drift_injector_contract_version="categorical-distribution-shift/v1",
        label_noise_injector_contract_version="label-corruption/v1",
        preprocessing_injector_contract_version="encoder-mapping-mismatch/v1",
        empirical_contract_distribution={
            "Month-to-month": 0.55,
            "One year": 0.25,
            "Two year": 0.20,
        },
        data_drift_output_size=1409,
    )


def _snapshot(confusion: ConfusionMatrix) -> BinaryMetricSnapshot:
    return BinaryMetricSnapshot(
        schema_version="p2-binary-metric-snapshot/v1",
        metric_protocol_version="binary-alpha-metrics/v1",
        zero_division_policy="zero/v1",
        minority_label=1,
        prediction_count=confusion.total,
        accuracy=confusion.accuracy(),
        macro_f1=confusion.macro_f1(),
        minority_recall=confusion.minority_recall(),
        confusion=confusion,
    )


def _comparison(outcome: str) -> MetricComparison:
    high = ConfusionMatrix(true_negative=70, false_positive=10, false_negative=10, true_positive=10)
    low = ConfusionMatrix(true_negative=60, false_positive=20, false_negative=10, true_positive=10)
    reference_confusion, observed_confusion = {
        "regression": (high, low),
        "stable": (high, high),
        "improvement": (low, high),
    }[outcome]
    reference = _snapshot(reference_confusion)
    observed = _snapshot(observed_confusion)
    return MetricComparison(
        schema_version="p2-binary-metric-comparison/v1",
        metric_protocol_version="binary-alpha-metrics/v1",
        primary_metric="accuracy",
        primary_threshold=0.01,
        reference=reference,
        observed=observed,
        accuracy_delta=observed.accuracy - reference.accuracy,
        macro_f1_delta=observed.macro_f1 - reference.macro_f1,
        minority_recall_delta=observed.minority_recall - reference.minority_recall,
        measured_primary_outcome=outcome,  # type: ignore[arg-type]
        evaluation_source_sha256=_HEX["a"],
        reference_predictions_sha256=_HEX["b"],
        observed_predictions_sha256=_HEX["c"],
    )


def _drift_comparison(outcome: str) -> DriftMetricComparison:
    comparison = _comparison(outcome)
    return DriftMetricComparison(
        schema_version="p2-drift-binary-metric-comparison/v1",
        metric_protocol_version=comparison.metric_protocol_version,
        primary_metric=comparison.primary_metric,
        primary_threshold=comparison.primary_threshold,
        reference=comparison.reference,
        observed=comparison.observed,
        accuracy_delta=comparison.accuracy_delta,
        macro_f1_delta=comparison.macro_f1_delta,
        minority_recall_delta=comparison.minority_recall_delta,
        measured_primary_outcome=comparison.measured_primary_outcome,
        reference_evaluation_source_sha256=_HEX["a"],
        observed_evaluation_source_sha256=_HEX["d"],
        reference_predictions_sha256=_HEX["b"],
        observed_predictions_sha256=_HEX["c"],
    )


def _secondary() -> SecondaryComparison:
    return SecondaryComparison(
        reference_value=0.40,
        observed_value=0.405,
        absolute_delta=0.005,
        stability_bound=0.01,
    )


def _evidence(
    fault_type: str,
    comparison: MetricComparison | DriftMetricComparison,
    outcome: str,
):  # type: ignore[no-untyped-def]
    performance = performance_evidence_from(comparison)
    secondary = _secondary() if outcome == "regression" else None
    if fault_type == "data_drift":
        return DataDriftDiagnosisEvidence(
            performance=performance,
            reference_distribution=DistributionSnapshot(
                sample_size=100,
                categories=(
                    CategoryShare(category="annual", proportion=0.40),
                    CategoryShare(category="monthly", proportion=0.60),
                ),
            ),
            observed_distribution=DistributionSnapshot(
                sample_size=100,
                categories=(
                    CategoryShare(category="annual", proportion=0.20),
                    CategoryShare(category="monthly", proportion=0.80),
                ),
            ),
            population_stability_index=0.19,
            secondary_comparison=secondary,
        )
    if fault_type == "label_noise":
        lower, upper = wilson_interval(successes=10, trials=100)
        return LabelDiagnosisEvidence(
            performance=performance,
            target_distribution_comparison=TargetDistributionComparison(
                reference_positive_count=50,
                reference_negative_count=50,
                observed_positive_count=48,
                observed_negative_count=52,
            ),
            target_quality_audit_summary=TargetQualityAudit(
                schema_version="p2-target-quality-audit/v1",
                audited_record_count=100,
                disagreeing_record_count=10,
                disagreement_rate=0.1,
                disagreement_rate_lower_bound=lower,
                disagreement_rate_upper_bound=upper,
                interval_method="wilson-score/95",
                protocol_version="target-quality-audit/v1",
            ),
            secondary_comparison=secondary,
        )
    return PreprocessingDiagnosisEvidence(
        performance=performance,
        transform_signature_comparison=TransformSignatureComparison(
            reference_signature_sha256=_HEX["a"],
            observed_signature_sha256=_HEX["b"],
            signatures_equal=False,
        ),
        target_projection_comparison=TargetProjectionComparison(
            sample_size=100,
            differing_record_count=20,
            difference_rate=0.2,
            reference_projection_sha256=_HEX["c"],
            observed_projection_sha256=_HEX["d"],
        ),
        schema_comparison=SchemaComparison(
            reference_field_count=24,
            observed_field_count=24,
            field_sets_equal=True,
        ),
        secondary_comparison=secondary,
    )


def _evaluated(slot, *, outcome: str | None = None):  # type: ignore[no-untyped-def]
    execution = execution_for_slot(slot)
    if outcome is None:
        if slot.role == "designed_benign_control":
            outcome = "stable"
        elif slot.role == "designed_improvement_control":
            outcome = "improvement"
        else:
            outcome = "regression"
    comparison = (
        _drift_comparison(outcome) if slot.fault_type == "data_drift" else _comparison(outcome)
    )
    candidate = ValidatedMechanismCandidate(
        candidate_id=execution.candidate_id,
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        proposed_family_sha256=execution.proposed_family_sha256,
        slot_sha256=canonical_sha256(slot.model_dump(mode="json")),
        artifact_sha256=canonical_sha256({"slot_id": slot.slot_id, "kind": "artifact"}),
        execution=execution,
        disposition={
            "candidate_id": execution.candidate_id,
            "disposition": "technically_valid",
        },
    )
    benign = slot.role == "designed_benign_control"
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        diagnosis_evidence=(
            None if benign else _evidence(slot.fault_type, comparison, outcome)
        ),
        equivalence_checks_passed=True if benign else None,
    )


def _recovery_authorization(plan) -> ReserveRecoveryAuthorization:  # type: ignore[no-untyped-def]
    rates = {"M2-F1": 0.01, "M2-F2": 0.05, "M2-F3": 0.20}
    return ReserveRecoveryAuthorization(
        schema_version="p2-reserve-recovery-authorization/1",
        protocol_version="complete-prespecified-reserve-recovery/v1",
        trigger="missing_mechanism_coverage",
        root_cause="effective_intervention_below_frozen_primary_threshold",
        fault_type="label_noise",
        source_store_sha256=_HEX["a"],
        source_candidate_plan_sha256=canonical_sha256(plan.model_dump(mode="json")),
        source_candidate_census_sha256=_HEX["b"],
        source_coverage_audit_sha256=_HEX["c"],
        source_observations=tuple(
            ReserveRecoveryObservation(
                slot_id=slot_id,
                declared_intervention_rate=rate,
                achieved_intervention_rate=rate,
                primary_metric_delta=0.0,
                threshold=0.01,
                measured_outcome="stable",
            )
            for slot_id, rate in rates.items()
        ),
        activated_reserve_slot_ids=("M2-R1", "M2-R2", "M2-R3"),
        probe_slot_ids=("M2-R1", "M2-R2"),
        promoted_reserve_slot_id="M2-R3",
        superseded_primary_slot_id="M2-F3",
        primary_metric="accuracy",
        threshold=0.01,
        preserves_primary_measurements=True,
        executes_complete_reserve_set=True,
    )


def _primary_results(plan):  # type: ignore[no-untyped-def]
    return tuple(_evaluated(slot) for slot in plan.slots if slot.slot_kind == "primary")


def test_frozen_plan_builder_materialises_exact_grid_deterministically() -> None:
    first = build_frozen_alpha_plan(_binding())
    second = build_frozen_alpha_plan(_binding())

    assert first == second
    assert len(first.slots) == 24
    assert sum(slot.slot_kind == "primary" for slot in first.slots) == 15
    assert sum(slot.slot_kind == "reserve" for slot in first.slots) == 9
    benign = next(slot for slot in first.slots if slot.slot_id == "M1-B1")
    assert (
        benign.identity.canonical_intervention_parameters.target_distribution
        == _binding().empirical_contract_distribution
    )


def test_plan_builder_rejects_a_non_empirical_or_incomplete_contract_distribution() -> None:
    with pytest.raises(ValidationError, match="three frozen Contract labels"):
        AlphaSystemBinding(
            **{
                **_binding().model_dump(),
                "empirical_contract_distribution": {"Month-to-month": 1.0},
            }
        )


def test_complete_primary_alpha_emits_nine_valid_artifacts_and_passes(tmp_path) -> None:
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )

    assert artifacts.report.accepted == 15
    assert artifacts.report.eligible_failure == 10
    assert artifacts.report.improvement_control == 2
    assert artifacts.report.benign_control == 3
    assert artifacts.report.context_count == 32
    assert artifacts.report.gate_status == "pass"
    manifest = save_contract_store(artifacts, tmp_path / "alpha")
    loaded = load_contract_store(tmp_path / "alpha")
    assert manifest.artifact_count == 9
    assert loaded.artifacts == artifacts


def test_candidate_census_reconciles_every_planned_slot_with_one_terminal_reason() -> None:
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )

    candidate_census = build_candidate_census(
        plan=artifacts.plan,
        execution=artifacts.execution,
        disposition=artifacts.disposition,
        classifications=artifacts.classifications.entries,
        admissions=artifacts.admissions.entries,
        census=artifacts.census,
        contexts=artifacts.contexts,
    )

    assert len(candidate_census.entries) == 24
    assert sum(entry.lifecycle_status == "accepted" for entry in candidate_census.entries) == 15
    assert (
        sum(entry.lifecycle_status == "inactive_reserve" for entry in candidate_census.entries) == 9
    )
    assert (
        candidate_census.canonical_sha256()
        == build_candidate_census(
            plan=artifacts.plan,
            execution=artifacts.execution,
            disposition=artifacts.disposition,
            classifications=artifacts.classifications.entries,
            admissions=artifacts.admissions.entries,
            census=artifacts.census,
            contexts=artifacts.contexts,
        ).canonical_sha256()
    )


def test_candidate_census_rejects_an_unexplained_valid_candidate() -> None:
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )

    with pytest.raises(CoverageContractError, match="classification/admission"):
        build_candidate_census(
            plan=artifacts.plan,
            execution=artifacts.execution,
            disposition=artifacts.disposition,
            classifications=artifacts.classifications.entries[1:],
            admissions=artifacts.admissions.entries,
            census=artifacts.census,
            contexts=artifacts.contexts,
        )


@pytest.mark.parametrize("ledger", ["classification", "admission"])
def test_candidate_census_rejects_duplicate_terminal_records(ledger: str) -> None:
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    classifications = artifacts.classifications.entries
    admissions = artifacts.admissions.entries
    if ledger == "classification":
        classifications = (*classifications, classifications[0])
    else:
        admissions = (*admissions, admissions[0])

    with pytest.raises(CoverageContractError, match=f"duplicate {ledger}"):
        build_candidate_census(
            plan=artifacts.plan,
            execution=artifacts.execution,
            disposition=artifacts.disposition,
            classifications=classifications,
            admissions=admissions,
            census=artifacts.census,
            contexts=artifacts.contexts,
        )


def test_candidate_census_preserves_rejection_and_exclusion_reason_codes() -> None:
    plan = build_frozen_alpha_plan(_binding())
    by_slot = {slot.slot_id: slot for slot in plan.slots}
    results = []
    for slot in plan.slots:
        if slot.slot_kind != "primary":
            continue
        if slot.slot_id == "M1-F1":
            results.append(record_technical_rejection(slot=slot, reason="provenance_hash_mismatch"))
            continue
        evaluated = _evaluated(slot)
        if slot.slot_id == "M2-F1":
            evaluated = evaluated.model_copy(
                update={
                    "exclusion_reason": "evidence_leakage",
                    "exclusion_detail": "diagnosis projection failed structural review",
                }
            )
        results.append(evaluated)
    results.append(_evaluated(by_slot["M1-R1"]))
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=tuple(results),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )

    candidate_census = build_candidate_census(
        plan=artifacts.plan,
        execution=artifacts.execution,
        disposition=artifacts.disposition,
        classifications=artifacts.classifications.entries,
        admissions=artifacts.admissions.entries,
        census=artifacts.census,
        contexts=artifacts.contexts,
    )
    by_id = {entry.slot_id: entry for entry in candidate_census.entries}

    assert by_id["M1-F1"].lifecycle_status == "technical_rejected"
    assert by_id["M1-F1"].technical_rejection_reason == "provenance_hash_mismatch"
    assert by_id["M1-R1"].lifecycle_status == "accepted"
    assert by_id["M2-F1"].lifecycle_status == "excluded_valid"
    assert by_id["M2-F1"].admission_exclusion_reason == "evidence_leakage"


def test_fault_directed_stable_label_candidates_cannot_make_coverage_pass() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = tuple(
        _evaluated(slot, outcome="stable")
        if slot.fault_type == "label_noise" and slot.role == "fault_directed"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary"
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    candidate_census = build_candidate_census(
        plan=artifacts.plan,
        execution=artifacts.execution,
        disposition=artifacts.disposition,
        classifications=artifacts.classifications.entries,
        admissions=artifacts.admissions.entries,
        census=artifacts.census,
        contexts=artifacts.contexts,
    )
    audit = assess_mechanism_coverage(
        census=artifacts.census,
        contexts=artifacts.contexts,
        candidate_census=candidate_census,
        reserve_recovery_authorization=artifacts.execution.reserve_recovery_authorization,
    )
    label = next(entry for entry in audit.mechanisms if entry.fault_type == "label_noise")

    assert artifacts.report.accepted == 15
    assert artifacts.report.mechanism_coverage_passed is False
    assert artifacts.report.gate_status == "fail"
    assert label.eligible_family_ids == ()
    assert {finding.reason_code for finding in label.findings} == {"no_eligible_failure"}


def test_result_input_order_cannot_change_any_lifecycle_artifact() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = _primary_results(plan)
    audit = DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=())

    forward = assemble_alpha_artifacts(plan=plan, results=results, duplicate_audit=audit)
    reverse = assemble_alpha_artifacts(
        plan=plan,
        results=tuple(reversed(results)),
        duplicate_audit=audit,
    )

    assert reverse == forward


def test_measured_outcome_changes_classification_but_not_candidate_or_family_identity() -> None:
    plan = build_frozen_alpha_plan(_binding())
    slot = next(slot for slot in plan.slots if slot.slot_id == "M1-S1")
    regression = _evaluated(slot, outcome="regression")
    stable = _evaluated(slot, outcome="stable")

    assert stable.candidate.candidate_id == regression.candidate.candidate_id
    assert stable.candidate.proposed_family_sha256 == regression.candidate.proposed_family_sha256


def test_primary_technical_rejection_activates_only_first_same_mechanism_reserve() -> None:
    plan = build_frozen_alpha_plan(_binding())
    by_id = {slot.slot_id: slot for slot in plan.slots}
    results = tuple(
        record_technical_rejection(
            slot=slot,
            reason="source_contract_mismatch",
            detail="source attestation did not match the frozen snapshot",
        )
        if slot.slot_id == "M1-F1"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary"
    ) + (_evaluated(by_id["M1-R1"]),)

    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    assert artifacts.report.executed == 16
    assert artifacts.report.activated_reserve == 1
    assert artifacts.report.technical_rejected == 1
    assert artifacts.report.accepted == 15


def test_recovery_executes_all_reserves_but_only_predeclared_promotion_can_count() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = tuple(
        _evaluated(slot, outcome="stable")
        if slot.fault_type == "label_noise"
        and slot.slot_kind == "primary"
        and slot.role == "fault_directed"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary"
    ) + tuple(
        _evaluated(slot, outcome="regression")
        for slot in plan.slots
        if slot.slot_id in {"M2-R1", "M2-R2", "M2-R3"}
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
        reserve_recovery_authorization=_recovery_authorization(plan),
    )
    admissions = {
        execution.slot_id: next(
            record
            for record in artifacts.admissions.entries
            if record.candidate_id == execution.candidate_id
        )
        for execution in artifacts.execution.executed
    }
    promoted = next(
        family
        for family in artifacts.census.entries
        if family.candidate_id == next(
            item.candidate_id
            for item in artifacts.execution.executed
            if item.slot_id == "M2-R3"
        )
    )

    assert artifacts.report.executed == 18
    assert artifacts.report.accepted == 15
    assert artifacts.report.excluded_valid == 3
    assert artifacts.report.mechanism_coverage_passed
    assert artifacts.report.gate_status == "pass"
    assert admissions["M2-R1"].exclusion_reason == "protocol_amendment_probe"
    assert admissions["M2-R2"].exclusion_reason == "protocol_amendment_probe"
    assert admissions["M2-F3"].exclusion_reason == "protocol_amendment_superseded"
    assert admissions["M2-R3"].admission == "accepted"
    assert promoted.origin_slot_kind == "reserve"
    assert promoted.reserve_promotion_authorized


def test_recovery_remains_failed_when_the_predeclared_promotion_is_stable() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = tuple(
        _evaluated(slot, outcome="stable")
        if slot.fault_type == "label_noise"
        and slot.role == "fault_directed"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary" or slot.slot_id in {"M2-R1", "M2-R2", "M2-R3"}
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
        reserve_recovery_authorization=_recovery_authorization(plan),
    )

    assert artifacts.report.mechanism_coverage_passed is False
    assert artifacts.report.gate_status == "fail"
    assert "label_noise=no_eligible_failure" in (artifacts.report.deviation_note or "")


@pytest.mark.parametrize("reserve_id", [None, "M1-R2", "M2-R1"])
def test_reserve_cannot_be_omitted_skipped_or_borrowed(reserve_id: str | None) -> None:
    plan = build_frozen_alpha_plan(_binding())
    by_id = {slot.slot_id: slot for slot in plan.slots}
    results = tuple(
        record_technical_rejection(slot=slot, reason="source_contract_mismatch")
        if slot.slot_id == "M1-F1"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary"
    )
    if reserve_id is not None:
        results += (_evaluated(by_id[reserve_id]),)
    with pytest.raises(AlphaLifecycleError, match="reserve execution"):
        assemble_alpha_artifacts(
            plan=plan,
            results=results,
            duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
        )


def test_valid_exclusion_does_not_unlock_a_reserve() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = list(_primary_results(plan))
    first = results[0]
    results[0] = first.model_copy(
        update={
            "exclusion_reason": "evidence_leakage",
            "exclusion_detail": "structural scan found evaluator-only metadata",
        }
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=tuple(results),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    assert artifacts.report.activated_reserve == 0
    assert artifacts.report.excluded_valid == 1
    assert artifacts.report.gate_status == "pass_with_deviation"


def test_improvement_control_regression_is_honestly_excluded_without_reserve() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = tuple(
        _evaluated(slot, outcome="regression")
        if slot.slot_id == "M2-I1"
        else _evaluated(slot)
        for slot in plan.slots
        if slot.slot_kind == "primary"
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    record = next(
        entry
        for entry in artifacts.admissions.entries
        if entry.candidate_id == execution_for_slot(
            next(slot for slot in plan.slots if slot.slot_id == "M2-I1")
        ).candidate_id
    )
    assert record.exclusion_reason == "control_direction_violation"
    assert artifacts.report.activated_reserve == 0
    assert artifacts.report.accepted == 14


def test_effective_duplicate_is_excluded_and_retains_accepted_representative() -> None:
    plan = build_frozen_alpha_plan(_binding())
    first, second = plan.slots[:2]
    first_execution = execution_for_slot(first)
    second_execution = execution_for_slot(second)
    basis = _HEX["e"]
    audit = DuplicateAudit(
        schema_version="p2-duplicate-audit/1",
        findings=(
            DuplicateFinding(
                kind="effective_intervention",
                fault_type="data_drift",
                measure="effective_fingerprint_equality",
                candidate_id=second_execution.candidate_id,
                duplicate_of_candidate_id=first_execution.candidate_id,
                candidate_basis_sha256=basis,
                duplicate_of_basis_sha256=basis,
            ),
        ),
    )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=audit,
    )
    admission = {entry.candidate_id: entry for entry in artifacts.admissions.entries}
    assert admission[first_execution.candidate_id].admission == "accepted"
    assert admission[second_execution.candidate_id].exclusion_reason == (
        "effective_intervention_duplicate"
    )


def test_forged_candidate_binding_is_rejected_even_if_counts_still_balance() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = list(_primary_results(plan))
    first = results[0]
    forged_execution = first.candidate.execution.model_copy(update={"dataset_sha256": _HEX["f"]})
    forged_candidate = first.candidate.model_copy(update={"execution": forged_execution})
    results[0] = first.model_copy(update={"candidate": forged_candidate})

    with pytest.raises((AlphaLifecycleError, ValidationError), match="frozen slot|dataset"):
        assemble_alpha_artifacts(
            plan=plan,
            results=tuple(results),
            duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"equivalence_checks_passed": False},
        {"diagnosis_evidence": _evidence("data_drift", _drift_comparison("stable"), "stable")},
    ],
)
def test_benign_candidate_requires_equivalence_and_forbids_diagnosis_evidence(
    updates: dict[str, object],
) -> None:
    plan = build_frozen_alpha_plan(_binding())
    slot = next(item for item in plan.slots if item.slot_id == "M1-B1")
    candidate = _evaluated(slot)
    with pytest.raises(ValidationError, match="equivalence|must not carry"):
        type(candidate).model_validate({**candidate.model_dump(), **updates})


def test_non_benign_candidate_forbids_equivalence_flag() -> None:
    plan = build_frozen_alpha_plan(_binding())
    slot = next(item for item in plan.slots if item.slot_id == "M2-F1")
    candidate = _evaluated(slot)
    with pytest.raises(ValidationError, match="reserved for benign controls"):
        type(candidate).model_validate(
            {**candidate.model_dump(), "equivalence_checks_passed": True}
        )


def test_candidate_rejects_performance_evidence_from_another_measurement() -> None:
    plan = build_frozen_alpha_plan(_binding())
    slot = next(item for item in plan.slots if item.slot_id == "M2-F1")
    candidate = _evaluated(slot)
    unrelated = _evidence("label_noise", _comparison("stable"), "stable")
    with pytest.raises(ValidationError, match="derived from the measured comparison"):
        type(candidate).model_validate(
            {**candidate.model_dump(), "diagnosis_evidence": unrelated.model_dump()}
        )


def test_rejected_candidate_must_bind_rejection_to_execution() -> None:
    plan = build_frozen_alpha_plan(_binding())
    slot = next(item for item in plan.slots if item.slot_id == "M1-F1")
    execution = execution_for_slot(slot)
    with pytest.raises(ValidationError, match="describe the executed candidate"):
        record_technical_rejection(
            slot=slot,
            reason="source_contract_mismatch",
        ).model_copy(
            update={
                "disposition": {
                    "candidate_id": f"p2-candidate-{'f' * 64}",
                    "disposition": "technical_rejected",
                    "rejection_reason": "source_contract_mismatch",
                }
            }
        ).model_validate(
            {
                "kind": "technical_rejection",
                "execution": execution.model_dump(),
                "disposition": {
                    "candidate_id": f"p2-candidate-{'f' * 64}",
                    "disposition": "technical_rejected",
                    "rejection_reason": "source_contract_mismatch",
                },
            }
        )


def test_duplicate_result_for_one_slot_is_rejected() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = _primary_results(plan)
    with pytest.raises(AlphaLifecycleError, match="duplicate result"):
        assemble_alpha_artifacts(
            plan=plan,
            results=(*results, results[0]),
            duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
        )


def test_multiple_duplicate_findings_cannot_assign_two_exclusions() -> None:
    plan = build_frozen_alpha_plan(_binding())
    first, second, third = plan.slots[:3]
    executions = tuple(execution_for_slot(slot) for slot in (first, second, third))
    audit = DuplicateAudit(
        schema_version="p2-duplicate-audit/1",
        findings=tuple(
            DuplicateFinding(
                kind="effective_intervention",
                fault_type="data_drift",
                measure="effective_fingerprint_equality",
                candidate_id=executions[1].candidate_id,
                duplicate_of_candidate_id=representative.candidate_id,
                candidate_basis_sha256=_HEX["e"],
                duplicate_of_basis_sha256=_HEX["e"],
            )
            for representative in (executions[0], executions[2])
        ),
    )
    with pytest.raises(AlphaLifecycleError, match="multiple exact/effective"):
        assemble_alpha_artifacts(
            plan=plan,
            results=_primary_results(plan),
            duplicate_audit=audit,
        )


def test_alpha_gate_fails_when_acceptance_floor_is_not_met() -> None:
    plan = build_frozen_alpha_plan(_binding())
    results = list(_primary_results(plan))
    for index in range(4):
        results[index] = results[index].model_copy(
            update={
                "exclusion_reason": "evidence_leakage",
                "exclusion_detail": "independent review rejected the projection",
            }
        )
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=tuple(results),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    assert artifacts.report.accepted == 11
    assert artifacts.report.gate_status == "fail"
    assert artifacts.report.deviation_note is not None


@pytest.mark.parametrize(
    ("unsafe", "expected"),
    [
        ("file:///private/result.json", "file URI"),
        ("api_key=abcdefgh", "secret-like"),
        (unicodedata.normalize("NFD", "bằng chứng"), "Unicode NFC"),
    ],
)
def test_contract_store_rejects_unsafe_text_payload(
    tmp_path,
    unsafe: str,
    expected: str,
) -> None:  # type: ignore[no-untyped-def]
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    first = artifacts.disposition.entries[0]
    changed = artifacts.__class__(
        **{
            **artifacts.__dict__,
            "disposition": artifacts.disposition.model_copy(
                update={"entries": (first.model_copy(update={"detail": unsafe}), *artifacts.disposition.entries[1:])}
            ),
        }
    )
    with pytest.raises(ValueError, match=expected):
        save_contract_store(changed, tmp_path / "unsafe")


def test_contract_store_rejects_noncanonical_manifest_and_missing_payload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = build_frozen_alpha_plan(_binding())
    artifacts = assemble_alpha_artifacts(
        plan=plan,
        results=_primary_results(plan),
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
    store = tmp_path / "store"
    save_contract_store(artifacts, store)
    manifest = store / "store-manifest.json"
    manifest.write_text(json.dumps(json.loads(manifest.read_text())), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest is not canonically encoded"):
        load_contract_store(store)

    missing = tmp_path / "missing"
    save_contract_store(artifacts, missing)
    (missing / "candidate-plan.json").unlink()
    with pytest.raises(ValueError, match="file set differs"):
        load_contract_store(missing)

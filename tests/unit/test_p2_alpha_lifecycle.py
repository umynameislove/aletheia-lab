"""End-to-end tests for frozen alpha planning and lifecycle assembly."""

from __future__ import annotations

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
from aletheia_lab.benchmark.p2.contracts import DuplicateAudit, DuplicateFinding
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
    high = ConfusionMatrix(
        true_negative=70, false_positive=10, false_negative=10, true_positive=10
    )
    low = ConfusionMatrix(
        true_negative=60, false_positive=20, false_negative=10, true_positive=10
    )
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


def _secondary() -> SecondaryComparison:
    return SecondaryComparison(
        reference_value=0.40,
        observed_value=0.405,
        absolute_delta=0.005,
        stability_bound=0.01,
    )


def _evidence(fault_type: str, comparison: MetricComparison, outcome: str):  # type: ignore[no-untyped-def]
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
    comparison = _comparison(outcome)
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

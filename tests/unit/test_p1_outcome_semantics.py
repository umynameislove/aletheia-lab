"""Measured outcomes and versioned failure eligibility fail closed."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import (
    CaseGroundTruth,
    FailureEligibility,
    ObservedOutcome,
    classify_outcome,
    failure_eligibility_for,
)
from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1


def test_classify_outcome_thresholds():
    assert classify_outcome(-0.05) == "regression"
    assert classify_outcome(0.04) == "improvement"
    assert classify_outcome(0.005) == "stable"
    assert classify_outcome(-0.009) == "stable"
    assert classify_outcome(0.01) == "improvement"
    assert classify_outcome(-0.01) == "regression"
    assert failure_eligibility_for(-0.01) == "eligible_failure"
    assert failure_eligibility_for(-0.009999999) == "stable_control"
    assert failure_eligibility_for(0.009999999) == "stable_control"
    assert failure_eligibility_for(0.01) == "improvement_control"


def test_classify_outcome_rejects_nonfinite_delta_or_invalid_threshold():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="delta must be finite"):
            classify_outcome(bad)
    for bad in (0.0, -0.01, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="threshold must be finite and positive"):
            classify_outcome(0.0, bad)


def _outcome(**over):
    base = {
        "metric": "accuracy",
        "reference_split": "test",
        "reference": 0.8,
        "observed": 0.75,
        "delta": -0.05,
        "classification": "regression",
    }
    base.update(over)
    return base


def _eligibility(**over):
    base = {
        "policy_version": "accuracy-regression/v1",
        "metric_change_threshold": 0.01,
        "classification": "eligible_failure",
    }
    base.update(over)
    return base


def _cause():
    return {
        "cause_label": "data_drift",
        "causal_mechanism": "categorical_distribution_shift",
        "affected_components": ["Contract"],
        "expected_symptoms": ["metric_regression", "distribution_shift:Contract"],
    }


def _gt(**over):
    base = {
        "injected_change": {
            "intervention_type": "categorical_distribution_shift",
            "feature": "Contract",
            "distribution_reference": {"Month-to-month": 1.0},
            "distribution_achieved": {"Month-to-month": 1.0},
        },
        "injection_parameters": {"feature": "Contract", "seed": 1},
        "observed_outcome": _outcome(),
        "failure_eligibility": _eligibility(),
        "hidden_failure_cause": _cause(),
    }
    base.update(over)
    return base


def test_observed_outcome_rejects_out_of_range():
    with pytest.raises(ValidationError):
        ObservedOutcome.model_validate(_outcome(reference=1.2, observed=0.5, delta=-0.7))
    with pytest.raises(ValidationError):
        ObservedOutcome.model_validate(
            _outcome(reference=-0.1, observed=0.5, delta=0.6, classification="improvement")
        )


def test_observed_outcome_rejects_nonfinite():
    for field in ("reference", "observed", "delta"):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValidationError):
                ObservedOutcome.model_validate(_outcome(**{field: bad}))


def test_observed_outcome_rejects_delta_mismatch():
    with pytest.raises(ValidationError):
        ObservedOutcome.model_validate(_outcome(delta=0.2, classification="improvement"))


def test_observed_outcome_rejects_classification_mismatch():
    with pytest.raises(ValidationError, match="classification must be derived"):
        ObservedOutcome.model_validate(_outcome(classification="stable"))


def test_observed_outcome_rejects_unsupported_metric_or_split():
    with pytest.raises(ValidationError):
        ObservedOutcome.model_validate(_outcome(metric="f1"))
    with pytest.raises(ValidationError):
        ObservedOutcome.model_validate(_outcome(reference_split="train"))


def test_failure_eligibility_locks_policy_and_threshold():
    with pytest.raises(ValidationError, match="unexpected eligibility policy"):
        FailureEligibility.model_validate(_eligibility(policy_version="accuracy-regression/v2"))
    for bad in (0.02, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            FailureEligibility.model_validate(_eligibility(metric_change_threshold=bad))


def test_ground_truth_semantics_are_derived_and_controls_have_no_cause(
    p1_generator_config, tmp_path
):
    out = tmp_path / "cases"
    generate_p1(p1_generator_config, out)
    for case_dir in out.iterdir():
        gt = load_case_dir_schema_only(case_dir).ground_truth
        expected = failure_eligibility_for(gt.observed_outcome.delta)
        assert gt.observed_outcome.classification == classify_outcome(gt.observed_outcome.delta)
        assert gt.failure_eligibility.classification == expected
        if expected == "eligible_failure":
            assert gt.hidden_failure_cause is not None
            assert "metric_regression" in gt.hidden_failure_cause.expected_symptoms
        else:
            assert gt.hidden_failure_cause is None


def test_condition_evidence_shapes(p1_generator_config, tmp_path):
    out = tmp_path / "cases"
    generate_p1(p1_generator_config, out)
    full = load_case_dir_schema_only(out / "p1-data-drift-01-full").manifest.observable_signals
    miss = load_case_dir_schema_only(
        out / "p1-data-drift-01-missing-key"
    ).manifest.observable_signals
    noisy = load_case_dir_schema_only(out / "p1-data-drift-01-noisy").manifest.observable_signals

    assert full.baseline_metric_reference is not None
    assert full.psi is not None and not full.distractor_comparisons

    assert miss.baseline_metric_reference is None
    assert miss.psi is None and miss.distribution_reference is None
    assert not miss.distractor_comparisons

    assert noisy.baseline_metric_reference is not None
    assert len(noisy.distractor_comparisons) == 1
    distractor = noisy.distractor_comparisons[0]
    assert distractor.feature == "gender"
    assert distractor.distribution_reference and distractor.distribution_observed
    assert distractor.psi is not None


def test_distractor_over_threshold_fails_closed(p1_generator_config, tmp_path):
    # Tie gender to Contract, so shifting Contract shifts gender's marginal and
    # the distractor is no longer stable -> generator must fail closed.
    proc = p1_generator_config.parent / "data" / "processed" / "telco_customer_churn.csv"
    frame = pd.read_csv(proc)
    frame["gender"] = frame["Contract"].map(lambda c: "Male" if c == "Month-to-month" else "Female")
    frame.to_csv(proc, index=False, lineterminator="\n")
    with pytest.raises(GeneratorConfigError):
        generate_p1(p1_generator_config, tmp_path / "cases")


# --- Ground-truth semantics + distractor fail-closed ---


def _distractor(**over):
    base = {
        "feature": "gender",
        "distribution_reference": {"Male": 0.5, "Female": 0.5},
        "distribution_observed": {"Male": 0.5, "Female": 0.5},
        "psi": 0.0,
    }
    base.update(over)
    return base


def test_ground_truth_rejects_nonfinite_delta():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            CaseGroundTruth.model_validate(_gt(observed_outcome=_outcome(delta=bad)))


def test_ground_truth_rejects_outcome_delta_mismatch():
    with pytest.raises(ValidationError):
        CaseGroundTruth.model_validate(_gt(observed_outcome=_outcome(classification="improvement")))


def test_ground_truth_rejects_synced_fake_eligibility():
    with pytest.raises(ValidationError, match="must be recomputed"):
        CaseGroundTruth.model_validate(
            _gt(failure_eligibility=_eligibility(classification="stable_control"))
        )


def test_ground_truth_rejects_control_with_hidden_cause():
    with pytest.raises(ValidationError):
        CaseGroundTruth.model_validate(
            _gt(
                observed_outcome=_outcome(observed=0.8, delta=0.0, classification="stable"),
                failure_eligibility=_eligibility(classification="stable_control"),
            )
        )


def test_ground_truth_rejects_eligible_failure_without_hidden_cause():
    with pytest.raises(ValidationError):
        CaseGroundTruth.model_validate(_gt(hidden_failure_cause=None))


def test_ground_truth_rejects_conflicting_failure_symptom():
    cause = _cause()
    cause["expected_symptoms"] = ["metric_regression", "metric_improvement"]
    with pytest.raises(ValidationError):
        CaseGroundTruth.model_validate(_gt(hidden_failure_cause=cause))


def test_distractor_rejects_bad_psi():
    from aletheia_lab.benchmark.case_schema import DistractorComparison

    for bad in (0.5, -0.1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            DistractorComparison.model_validate(_distractor(psi=bad))


def test_distractor_rejects_bad_distribution():
    from aletheia_lab.benchmark.case_schema import DistractorComparison

    with pytest.raises(ValidationError):
        DistractorComparison.model_validate(_distractor(distribution_observed={}))
    with pytest.raises(ValidationError):
        DistractorComparison.model_validate(
            _distractor(distribution_observed={"Male": -0.1, "Female": 1.1})
        )
    with pytest.raises(ValidationError):
        DistractorComparison.model_validate(
            _distractor(distribution_observed={"Male": 0.3, "Female": 0.3})
        )
    with pytest.raises(ValidationError):
        DistractorComparison.model_validate(_distractor(feature="  "))


# --- Commit 2 v2: InjectionProvenance fail-closed ---


def _inj(**over):
    base = {
        "injection_id": "s",
        "injector": "X",
        "fault_type": "data_drift",
        "feature": "Contract",
        "seed": 1,
        "target_distribution": {"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
        "achieved_distribution": {"Month-to-month": 0.5, "One year": 0.3, "Two year": 0.2},
        "reference_distribution": {"Month-to-month": 0.5, "One year": 0.3, "Two year": 0.2},
        "psi": 0.0,
        "output_size": 100,
        "dataset_id": "d",
        "dataset_sha256": "a" * 64,
    }
    base.update(over)
    return base


def test_injection_provenance_rejects_bad_psi():
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    for bad in (float("nan"), float("inf"), -0.1):
        with pytest.raises(ValidationError):
            InjectionProvenance.model_validate(_inj(psi=bad))


def test_injection_provenance_rejects_bad_distribution():
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    with pytest.raises(ValidationError):
        InjectionProvenance.model_validate(_inj(achieved_distribution={}))
    with pytest.raises(ValidationError):
        InjectionProvenance.model_validate(
            _inj(achieved_distribution={"Month-to-month": -0.1, "One year": 0.6, "Two year": 0.5})
        )
    with pytest.raises(ValidationError):
        InjectionProvenance.model_validate(
            _inj(achieved_distribution={"Month-to-month": 0.3, "One year": 0.3, "Two year": 0.3})
        )


def test_injection_provenance_rejects_psi_not_matching_distributions():
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    with pytest.raises(ValidationError):
        InjectionProvenance.model_validate(
            _inj(
                reference_distribution={"Month-to-month": 0.8, "One year": 0.1, "Two year": 0.1},
                achieved_distribution={"Month-to-month": 0.5, "One year": 0.3, "Two year": 0.2},
                psi=0.0,  # real PSI is > 0, so a recorded 0.0 must be rejected
            )
        )


def test_injection_provenance_rejects_nonpositive_output_size():
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    with pytest.raises(ValidationError):
        InjectionProvenance.model_validate(_inj(output_size=0))


def test_case_models_reject_unknown_fields():
    from aletheia_lab.benchmark.case_schema import DiagnosisInput, ObservableSignals

    with pytest.raises(ValidationError):
        ObservableSignals.model_validate({"candidate_feature": "Contract", "evil": 1})
    with pytest.raises(ValidationError):
        DiagnosisInput.model_validate(
            {
                "public_id": "x",
                "evidence_condition": "full",
                "dataset_id": "d",
                "dataset_sha256": "a",
                "split_manifest_sha256": "b",
                "task_prompt": "t",
                "observable_signals": {},
                "cause_label": "data_drift",
            }
        )


@pytest.mark.parametrize("bad", [0, -1])
def test_observable_signals_rejects_nonpositive_sample_size(bad):
    from aletheia_lab.benchmark.case_schema import ObservableSignals

    with pytest.raises(ValidationError) as exc_info:
        ObservableSignals.model_validate({"sample_size": bad})
    assert exc_info.value.errors()[0]["loc"] == ("sample_size",)
    assert "must be positive" in exc_info.value.errors()[0]["msg"]


@pytest.mark.parametrize("bad", [True, 1.0, "10"])
def test_observable_signals_rejects_coerced_sample_size_types(bad):
    from aletheia_lab.benchmark.case_schema import ObservableSignals

    with pytest.raises(ValidationError) as exc_info:
        ObservableSignals.model_validate({"sample_size": bad})
    assert exc_info.value.errors()[0]["loc"] == ("sample_size",)
    assert exc_info.value.errors()[0]["type"] == "int_type"


@pytest.mark.parametrize("bad", [True, "1.0"])
def test_injection_provenance_rejects_coerced_target_weight_types(bad):
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    with pytest.raises(ValidationError) as exc_info:
        InjectionProvenance.model_validate(_inj(target_distribution={"Month-to-month": bad}))
    assert exc_info.value.errors()[0]["loc"] == ("target_distribution", "Month-to-month")
    assert exc_info.value.errors()[0]["type"] == "float_type"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({}, "must not be empty"),
        (
            {"Month-to-month": 0.0, "One year": 0.0, "Two year": 0.0},
            "total must be positive",
        ),
        (
            {"Month-to-month": 1.0, "One year": -0.1, "Two year": 0.1},
            "finite and non-negative",
        ),
        ({"Month-to-month": float("nan")}, "finite and non-negative"),
        ({"Month-to-month": float("inf")}, "finite and non-negative"),
        ({"Month-to-month": float("-inf")}, "finite and non-negative"),
        ({"not-in-reference": 1.0}, "not present in the reference distribution"),
    ],
)
def test_injection_provenance_rejects_invalid_target_distribution(target, expected):
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    with pytest.raises(ValidationError) as exc_info:
        InjectionProvenance.model_validate(_inj(target_distribution=target))
    assert expected in str(exc_info.value)


def test_injection_provenance_accepts_raw_target_weights():
    from aletheia_lab.benchmark.case_schema import InjectionProvenance

    provenance = InjectionProvenance.model_validate(
        _inj(target_distribution={"Month-to-month": 8, "One year": 1.2, "Two year": 0.8})
    )
    assert provenance.target_distribution == {
        "Month-to-month": 8.0,
        "One year": 1.2,
        "Two year": 0.8,
    }

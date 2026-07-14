"""Honest outcome classification, MetricComparison fail-closed, measured noisy."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import (
    MetricComparison,
    case_role_for,
    classify_outcome,
    expected_symptom_for,
)
from aletheia_lab.benchmark.case_writer import load_case_dir
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1


def test_classify_outcome_thresholds():
    assert classify_outcome(-0.05) == "regression"
    assert classify_outcome(0.04) == "improvement"
    assert classify_outcome(0.005) == "stable"
    assert classify_outcome(-0.009) == "stable"
    assert classify_outcome(0.01) == "improvement"
    assert classify_outcome(-0.01) == "regression"


def test_metric_comparison_rejects_out_of_range():
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="accuracy", reference_split="test", reference=1.2, observed=0.5, delta=-0.7
        )
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="accuracy", reference_split="test", reference=-0.1, observed=0.5, delta=0.6
        )


def test_metric_comparison_rejects_nonfinite():
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="accuracy",
            reference_split="test",
            reference=float("nan"),
            observed=0.5,
            delta=0.0,
        )


def test_metric_comparison_rejects_delta_mismatch():
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="accuracy", reference_split="test", reference=0.5, observed=0.4, delta=0.2
        )


def test_metric_comparison_rejects_unsupported_metric_or_split():
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="f1", reference_split="test", reference=0.5, observed=0.4, delta=-0.1
        )
    with pytest.raises(ValidationError):
        MetricComparison(
            metric="accuracy", reference_split="train", reference=0.5, observed=0.4, delta=-0.1
        )


def test_ground_truth_outcome_role_and_symptom_are_consistent(p1_generator_config, tmp_path):
    out = tmp_path / "cases"
    generate_p1(p1_generator_config, out)
    for case_dir in out.iterdir():
        gt = load_case_dir(case_dir).ground_truth
        assert gt.metric_outcome == classify_outcome(gt.metric_delta)
        assert gt.case_role == case_role_for(gt.metric_outcome)
        assert expected_symptom_for(gt.metric_outcome) in gt.expected_symptoms
        # A positive or stable delta must never be labelled a regression.
        if gt.metric_delta > 0:
            assert "metric_regression" not in gt.expected_symptoms


def test_condition_evidence_shapes(p1_generator_config, tmp_path):
    out = tmp_path / "cases"
    generate_p1(p1_generator_config, out)
    full = load_case_dir(out / "p1-data-drift-01-full").manifest.observable_signals
    miss = load_case_dir(out / "p1-data-drift-01-missing-key").manifest.observable_signals
    noisy = load_case_dir(out / "p1-data-drift-01-noisy").manifest.observable_signals

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

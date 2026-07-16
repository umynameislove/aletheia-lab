"""Shared fixtures: a small synthetic dataset matching the processed schema."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aletheia_lab.baseline.schema import (
    CATEGORICAL_FEATURES,
    ID_COLUMN,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from aletheia_lab.benchmark.case_schema import case_family_id_for
from aletheia_lab.evidence.rubric import expected_behavior_for

_CATEGORY_VALUES: dict[str, list[str]] = {
    "gender": ["Male", "Female"],
    "Partner": ["Yes", "No"],
    "Dependents": ["Yes", "No"],
    "PhoneService": ["Yes", "No"],
    "MultipleLines": ["No", "Yes", "No phone service"],
    "InternetService": ["DSL", "Fiber optic", "No"],
    "OnlineSecurity": ["Yes", "No", "No internet service"],
    "OnlineBackup": ["Yes", "No", "No internet service"],
    "DeviceProtection": ["Yes", "No", "No internet service"],
    "TechSupport": ["Yes", "No", "No internet service"],
    "StreamingTV": ["Yes", "No", "No internet service"],
    "StreamingMovies": ["Yes", "No", "No internet service"],
    "Contract": ["Month-to-month", "One year", "Two year"],
    "PaperlessBilling": ["Yes", "No"],
    "PaymentMethod": [
        "Electronic check",
        "Mailed check",
        "Bank transfer (automatic)",
        "Credit card (automatic)",
    ],
}


def build_frame(n: int = 240, seed: int = 0) -> pd.DataFrame:
    """Build a deterministic synthetic frame with the processed Telco schema."""

    rng = np.random.default_rng(seed)
    data: dict[str, object] = {ID_COLUMN: [f"{i:05d}-SYNTH" for i in range(n)]}
    for col in CATEGORICAL_FEATURES:
        data[col] = rng.choice(_CATEGORY_VALUES[col], size=n)
    data["SeniorCitizen"] = rng.integers(0, 2, size=n)
    data["tenure"] = rng.integers(0, 72, size=n)
    data["MonthlyCharges"] = np.round(rng.uniform(18.0, 120.0, size=n), 2)
    data["TotalCharges"] = np.round(data["tenure"] * data["MonthlyCharges"], 2)
    # Give the target real signal so both classes appear and the model can learn.
    logit = (
        -1.0
        + 0.03 * (60 - data["tenure"])
        + 0.8 * (np.asarray(data["Contract"]) == "Month-to-month")
    )
    prob = 1.0 / (1.0 + np.exp(-logit))
    data[TARGET_COLUMN] = np.where(rng.uniform(size=n) < prob, "Yes", "No")

    columns = [ID_COLUMN, *NUMERIC_FEATURES, *CATEGORICAL_FEATURES, TARGET_COLUMN]
    return pd.DataFrame(data)[columns]


@pytest.fixture
def make_frame():
    """Return the synthetic-frame builder."""

    return build_frame


@pytest.fixture
def project_config(tmp_path: Path, make_frame) -> Path:
    """Write a synthetic processed dataset + a project config and return its path."""

    processed_dir = tmp_path / "data" / "processed"
    processed_dir.mkdir(parents=True)
    make_frame(n=240, seed=0).to_csv(
        processed_dir / "telco_customer_churn.csv", index=False, lineterminator="\n"
    )
    config = tmp_path / "project.yaml"
    config.write_text(
        "dataset:\n"
        "  id: telco_customer_churn\n"
        "paths:\n"
        f"  processed_data: {processed_dir.as_posix()}\n"
        "baseline:\n"
        "  seed: 42\n"
        "  split:\n"
        "    train: 0.70\n"
        "    validation: 0.15\n"
        "    test: 0.15\n"
        "    stratify: true\n"
        "  model:\n"
        "    type: logistic_regression\n"
        "    max_iter: 500\n"
        "    C: 1.0\n"
        f"  output_dir: {(tmp_path / 'runs').as_posix()}\n"
        "  tolerance: 1.0e-9\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
def p1_generator_config(tmp_path: Path, make_frame) -> Path:
    """Write a synthetic processed dataset + project config + fault_types config."""

    processed_dir = tmp_path / "data" / "processed"
    processed_dir.mkdir(parents=True)
    make_frame(n=4000, seed=0).to_csv(
        processed_dir / "telco_customer_churn.csv", index=False, lineterminator="\n"
    )
    config = tmp_path / "project.yaml"
    config.write_text(
        "dataset:\n  id: telco_customer_churn\n"
        "paths:\n"
        f"  processed_data: {processed_dir.as_posix()}\n"
        "baseline:\n  seed: 42\n"
        "  split:\n    train: 0.70\n    validation: 0.15\n    test: 0.15\n    stratify: true\n",
        encoding="utf-8",
    )
    bench_dir = tmp_path / "benchmark"
    bench_dir.mkdir()
    (bench_dir / "fault_types.yaml").write_text(
        "fault_types:\n"
        "  data_drift:\n"
        "    injection:\n"
        "      feature: Contract\n"
        "      settings:\n"
        "        - injection_id: drift_contract_s1\n"
        "          seed: 1\n"
        "          target_distribution: {Month-to-month: 0.80, One year: 0.12, Two year: 0.08}\n"
        "        - injection_id: drift_contract_s2\n"
        "          seed: 2\n"
        "          target_distribution: {Month-to-month: 0.70, One year: 0.18, Two year: 0.12}\n"
        "        - injection_id: drift_contract_s3\n"
        "          seed: 3\n"
        "          target_distribution: {Month-to-month: 0.90, One year: 0.06, Two year: 0.04}\n"
        "        - injection_id: drift_contract_s4\n"
        "          seed: 4\n"
        "          target_distribution: {Month-to-month: 0.40, One year: 0.30, Two year: 0.30}\n"
        "        - injection_id: drift_contract_s5\n"
        "          seed: 5\n"
        "          target_distribution: {Month-to-month: 0.60, One year: 0.20, Two year: 0.20}\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
def p1_manifest_factory():
    """Factory for a valid P1 case manifest dict (override id/public_id/condition)."""

    def build(
        case_id: str = "p1-data-drift-01-full",
        public_id: str = "p1-case-01-full",
        condition: str = "full",
    ) -> dict:
        family_id = case_family_id_for(
            fault_type="data_drift",
            dataset_id="telco_customer_churn",
            dataset_sha256="a" * 64,
            split_manifest_sha256="b" * 64,
            injection_id="drift_contract_s1",
            injector="X",
            feature="Contract",
            seed=1,
            target_distribution={
                "Month-to-month": 0.8,
                "One year": 0.12,
                "Two year": 0.08,
            },
            output_size=100,
        )
        return {
            "case_id": case_id,
            "case_family_id": family_id,
            "public_id": public_id,
            "fault_type": "data_drift",
            "dataset_id": "telco_customer_churn",
            "dataset_sha256": "a" * 64,
            "split_manifest_sha256": "b" * 64,
            "injection_id": "drift_contract_s1",
            "injection_seed": 1,
            "injection_parameters": {"feature": "Contract", "seed": 1},
            "injection_setting": "drift_contract_s1",
            "severity_rank": 1,
            "evidence_condition": condition,
            "evidence_bundle_id": f"eb-{public_id}",
            "expected_diagnosis_behavior": (
                expected_behavior_for(condition)
                if condition in {"full", "missing_key", "noisy"}
                else "diagnose_with_citations"
            ),
            "observable_signals": {
                "candidate_feature": "Contract",
                "psi": 0.5,
                "distribution_reference": {"Month-to-month": 1.0},
            },
            "artifacts": {"manifest": "manifest.json"},
            "reproduction": {"command": "python -m aletheia_lab benchmark generate-p1"},
            "ground_truth_ref": "ground_truth.json",
            "split": "dev",
            "tag": "P1",
        }

    return build


@pytest.fixture
def p1_ground_truth_factory():
    """Factory for a valid P1 ground-truth dict."""

    def build() -> dict:
        return {
            "injected_change": {
                "intervention_type": "categorical_distribution_shift",
                "feature": "Contract",
                "distribution_reference": {"Month-to-month": 1.0},
                "distribution_achieved": {"Month-to-month": 1.0},
            },
            "injection_parameters": {"feature": "Contract", "seed": 1},
            "observed_outcome": {
                "metric": "accuracy",
                "reference_split": "test",
                "reference": 0.8,
                "observed": 0.75,
                "delta": -0.05,
                "classification": "regression",
            },
            "failure_eligibility": {
                "policy_version": "accuracy-regression/v1",
                "metric_change_threshold": 0.01,
                "classification": "eligible_failure",
            },
            "hidden_failure_cause": {
                "cause_label": "data_drift",
                "causal_mechanism": "categorical_distribution_shift",
                "affected_components": ["Contract"],
                "expected_symptoms": ["metric_regression", "distribution_shift:Contract"],
            },
        }

    return build


@pytest.fixture
def p1_injection_factory():
    """Factory for a valid P1 injection-provenance dict."""

    def build() -> dict:
        return {
            "injection_id": "drift_contract_s1",
            "injector": "X",
            "fault_type": "data_drift",
            "feature": "Contract",
            "seed": 1,
            "target_distribution": {"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
            "achieved_distribution": {"Month-to-month": 0.5, "One year": 0.3, "Two year": 0.2},
            "reference_distribution": {"Month-to-month": 0.5, "One year": 0.3, "Two year": 0.2},
            "psi": 0.0,
            "output_size": 100,
            "dataset_id": "telco_customer_churn",
            "dataset_sha256": "a" * 64,
        }

    return build

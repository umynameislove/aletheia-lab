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
    make_frame(n=300, seed=0).to_csv(
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

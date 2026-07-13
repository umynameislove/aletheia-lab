"""Baseline estimator: an explainable, CPU-only, seeded logistic regression.

The model is packaged with its preprocessing in one ``Pipeline`` so the exact
same transforms are applied at fit and predict time, which avoids
training-serving skew. Hyperparameters are explicit and read from config.
"""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from aletheia_lab.baseline.preprocess import build_preprocessor


@dataclass(frozen=True)
class ModelConfig:
    """Explicit, reproducible baseline hyperparameters."""

    kind: str = "logistic_regression"
    seed: int = 42
    max_iter: int = 1000
    C: float = 1.0
    class_weight: str | None = None


def build_pipeline(config: ModelConfig) -> Pipeline:
    """Build the full preprocessing + estimator pipeline for ``config``."""

    if config.kind != "logistic_regression":
        msg = f"unsupported baseline model kind: {config.kind!r}"
        raise ValueError(msg)
    estimator = LogisticRegression(
        max_iter=config.max_iter,
        C=config.C,
        class_weight=config.class_weight,
        random_state=config.seed,
        solver="lbfgs",
    )
    return Pipeline(
        steps=[
            ("preprocess", build_preprocessor()),
            ("model", estimator),
        ]
    )

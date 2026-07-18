"""Column roles and artifact schemas for the P1 baseline.

Column roles are pinned here next to the code so the loader, preprocessor and
model agree on what is an identifier, a target, a numeric feature or a
categorical feature. The identifier and the target are never part of the feature
matrix, which keeps the target out of the model input by construction.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# These column roles are derived from the pinned processed Telco schema.
ID_COLUMN = "customerID"
TARGET_COLUMN = "Churn"
POSITIVE_LABEL = "Yes"
NEGATIVE_LABEL = "No"

NUMERIC_FEATURES: tuple[str, ...] = (
    "SeniorCitizen",
    "tenure",
    "MonthlyCharges",
    "TotalCharges",
)
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "gender",
    "Partner",
    "Dependents",
    "PhoneService",
    "MultipleLines",
    "InternetService",
    "OnlineSecurity",
    "OnlineBackup",
    "DeviceProtection",
    "TechSupport",
    "StreamingTV",
    "StreamingMovies",
    "Contract",
    "PaperlessBilling",
    "PaymentMethod",
)
FEATURE_COLUMNS: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# All columns the loader expects in the processed table (order-independent).
EXPECTED_COLUMNS: frozenset[str] = frozenset({ID_COLUMN, TARGET_COLUMN, *FEATURE_COLUMNS})


class SplitCounts(BaseModel):
    """Per-split record accounting used for reproducibility checks."""

    n: int
    n_positive: int
    n_negative: int
    positive_rate: float
    # SHA-256 of the sorted record identifiers in this split; proves identical
    # membership across runs without embedding a wall-clock or file order.
    record_id_sha256: str


class SplitManifest(BaseModel):
    """Deterministic description of a train/validation/test partition.

    Everything here is a pure function of the dataset, the seed and the split
    ratios. ``created_at`` is metadata only and is excluded from reproducibility
    comparisons.
    """

    dataset_id: str
    dataset_sha256: str
    n_rows: int
    seed: int
    stratified: bool
    ratios: dict[str, float]
    splits: dict[str, SplitCounts]
    id_column: str = ID_COLUMN
    target_column: str = TARGET_COLUMN
    created_at: str | None = None


class SplitMetrics(BaseModel):
    """Classification metrics for one split."""

    n: int
    n_positive: int
    n_negative: int
    accuracy: float
    balanced_accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float | None
    confusion_matrix: list[list[int]] = Field(
        description="[[tn, fp], [fn, tp]] with labels ordered [negative, positive]."
    )
    zero_division_policy: str = "zero"


class MetricsReport(BaseModel):
    """Metrics across all splits plus the selection/eval protocol."""

    positive_label: str = POSITIVE_LABEL
    selection_split: str = "validation"
    final_eval_split: str = "test"
    splits: dict[str, SplitMetrics]

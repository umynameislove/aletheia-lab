"""Frozen CPU runtime used by confirmatory execution and synthetic readiness.

The mutation kernel receives no features.  This adapter is a later boundary:
it verifies feature attestations, fits the single registered model and returns a
probability vector whose fitted parameters and inputs are canonically hashed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final, cast

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from sklearn.compose import ColumnTransformer  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    BINARY_LABELS,
    ConfirmatoryExecutionError,
    ConfirmatoryTrainingSource,
    PredictionRole,
    ProbabilityVector,
    labelled_targets_sha256,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
)

PREPROCESSING_RUNTIME_VERSION: Final[str] = "mixed-tabular-train-only/v1"


def frozen_preprocessing_sha256() -> str:
    """Identity of the only preprocessing algorithm allowed by the protocol."""

    return canonical_sha256(
        {
            "version": PREPROCESSING_RUNTIME_VERSION,
            "numeric": ("median_imputation", "standard_scaling"),
            "categorical": (
                "most_frequent_imputation",
                "one_hot_encoding",
                "ignore_unknown_categories",
                "dense_output",
            ),
            "fit_scope": "training_features_only",
        }
    )


def frozen_model_specification_sha256(protocol: ConfirmatoryProtocol) -> str:
    protocol = ConfirmatoryProtocol.model_validate(protocol.model_dump())
    return canonical_sha256(
        {
            "protocol_sha256": protocol.canonical_sha256(),
            "model": protocol.model.model_dump(mode="json"),
        }
    )


def _json_scalar(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    try:
        missing = bool(pd.isna(cast(Any, value)))
    except (TypeError, ValueError):
        missing = False
    if missing:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        result = float(value)
        if not math.isfinite(result):
            raise ConfirmatoryExecutionError("feature frames may not contain infinite values")
        return result
    if isinstance(value, str):
        return value
    raise ConfirmatoryExecutionError(
        f"unsupported feature scalar type for canonical hashing: {type(value).__name__}"
    )


def feature_frame_sha256(frame: pd.DataFrame, record_ids: Sequence[str]) -> str:
    """Hash row order, column order, dtypes and normalized scalar values."""

    if frame.empty or frame.shape[1] == 0:
        raise ConfirmatoryExecutionError("a feature frame must contain rows and columns")
    if len(frame) != len(record_ids):
        raise ConfirmatoryExecutionError("feature rows and record identifiers must align")
    columns = tuple(str(column) for column in frame.columns)
    if len(set(columns)) != len(columns):
        raise ConfirmatoryExecutionError("feature column names must be unique")
    if any(not column or column != column.strip() for column in columns):
        raise ConfirmatoryExecutionError("feature column names must be non-blank and trimmed")
    rows = [
        {
            "record_id": record_id,
            "values": tuple(_json_scalar(value) for value in row),
        }
        for record_id, row in zip(record_ids, frame.itertuples(index=False, name=None), strict=True)
    ]
    return canonical_sha256(
        {
            "schema_version": "p2-confirmatory-feature-frame/1",
            "columns": columns,
            "dtypes": tuple(str(dtype) for dtype in frame.dtypes),
            "rows": rows,
        }
    )


def _preprocessor(
    frame: pd.DataFrame,
) -> tuple[ColumnTransformer, tuple[str, ...], tuple[str, ...]]:
    numeric = tuple(str(column) for column in frame.columns if is_numeric_dtype(frame[column]))
    categorical = tuple(str(column) for column in frame.columns if str(column) not in numeric)
    transformers: list[tuple[str, Pipeline, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    steps=(
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    )
                ),
                list(numeric),
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    steps=(
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    )
                ),
                list(categorical),
            )
        )
    return (
        ColumnTransformer(
            transformers=transformers,
            remainder="drop",
            verbose_feature_names_out=False,
        ),
        numeric,
        categorical,
    )


def fit_frozen_probability_vector(
    *,
    protocol: ConfirmatoryProtocol,
    dataset: DatasetBinding,
    source: ConfirmatoryTrainingSource,
    training_features: pd.DataFrame,
    training_targets: Sequence[int],
    evaluation_features: pd.DataFrame,
    evaluation_record_ids: Sequence[str],
    role: PredictionRole,
) -> ProbabilityVector:
    """Fit the registered logistic pipeline and bind predictions to all inputs."""

    protocol = ConfirmatoryProtocol.model_validate(protocol.model_dump())
    dataset = DatasetBinding.model_validate(dataset.model_dump())
    source = ConfirmatoryTrainingSource.model_validate(source.model_dump())
    if dataset not in protocol.datasets:
        raise ConfirmatoryExecutionError("runtime dataset is outside the frozen protocol")
    if (
        source.dataset_id != dataset.dataset_id
        or source.dataset_role != dataset.role
        or source.dataset_sha256 != dataset.snapshot_sha256
        or source.protocol_sha256 != protocol.canonical_sha256()
    ):
        raise ConfirmatoryExecutionError("runtime source does not match the frozen dataset")
    if tuple(str(column) for column in training_features.columns) != tuple(
        str(column) for column in evaluation_features.columns
    ):
        raise ConfirmatoryExecutionError("training and evaluation feature schemas must match")
    excluded = set(dataset.excluded_features)
    if excluded & set(str(column) for column in training_features.columns):
        raise ConfirmatoryExecutionError("an excluded feature reached the model runtime")
    observed_training_sha = feature_frame_sha256(training_features, source.record_ids)
    if observed_training_sha != source.feature_matrix_sha256:
        raise ConfirmatoryExecutionError("training features do not match their source attestation")
    if source.preprocessing_sha256 != frozen_preprocessing_sha256():
        raise ConfirmatoryExecutionError("preprocessing attestation does not match the runtime")
    if source.model_specification_sha256 != frozen_model_specification_sha256(protocol):
        raise ConfirmatoryExecutionError("model attestation does not match the frozen runtime")
    targets = tuple(int(value) for value in training_targets)
    if len(targets) != len(source.record_ids) or any(
        value not in BINARY_LABELS for value in targets
    ):
        raise ConfirmatoryExecutionError("runtime training targets must be aligned and binary")
    if set(targets) != BINARY_LABELS:
        raise ConfirmatoryExecutionError("runtime training targets must retain both classes")
    evaluation_ids = tuple(str(value) for value in evaluation_record_ids)
    evaluation_sha256 = feature_frame_sha256(evaluation_features, evaluation_ids)
    preprocessor, numeric, categorical = _preprocessor(training_features)
    model = LogisticRegression(
        max_iter=protocol.model.max_iter,
        C=protocol.model.c,
        class_weight=protocol.model.class_weight,
        solver=protocol.model.solver,
        random_state=protocol.model.training_seed,
    )
    pipeline = Pipeline(steps=(("preprocess", preprocessor), ("model", model)))
    try:
        pipeline.fit(training_features, np.asarray(targets, dtype=int))
        probabilities = np.asarray(pipeline.predict_proba(evaluation_features), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ConfirmatoryExecutionError("frozen logistic runtime failed") from exc
    classes = tuple(int(value) for value in model.classes_)
    if classes != (0, 1) or probabilities.shape != (len(evaluation_ids), 2):
        raise ConfirmatoryExecutionError("runtime did not produce canonical binary probabilities")
    target_sha256 = labelled_targets_sha256(source.record_ids, targets)
    model_sha256 = canonical_sha256(
        {
            "schema_version": "p2-confirmatory-fitted-model/1",
            "protocol_sha256": protocol.canonical_sha256(),
            "model_specification_sha256": source.model_specification_sha256,
            "preprocessing_sha256": source.preprocessing_sha256,
            "training_feature_matrix_sha256": observed_training_sha,
            "training_targets_sha256": target_sha256,
            "numeric_columns": numeric,
            "categorical_columns": categorical,
            "output_features": tuple(str(value) for value in preprocessor.get_feature_names_out()),
            "classes": classes,
            "coefficients": tuple(tuple(float(value) for value in row) for row in model.coef_),
            "intercept": tuple(float(value) for value in model.intercept_),
            "iterations": tuple(int(value) for value in model.n_iter_),
        }
    )
    return ProbabilityVector(
        role=role,
        record_ids=evaluation_ids,
        positive_probabilities=tuple(float(value) for value in probabilities[:, 1]),
        model_artifact_sha256=model_sha256,
        training_targets_sha256=target_sha256,
        evaluation_feature_matrix_sha256=evaluation_sha256,
        split_manifest_sha256=source.split_manifest_sha256,
        protocol_sha256=source.protocol_sha256,
    )

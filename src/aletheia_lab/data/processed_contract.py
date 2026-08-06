"""Authoritative schema validation for the processed Telco dataset.

The baseline loader validates the fields needed for modelling.  Manifest and
quality artifacts need a stronger boundary: the processed table must contain
exactly the pinned columns, every value must satisfy its declared logical type,
and the returned column contracts must describe the file that was actually
loaded.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from aletheia_lab.baseline.loader import DatasetSchemaError, load_processed
from aletheia_lab.baseline.schema import (
    CATEGORICAL_FEATURES,
    EXPECTED_COLUMNS,
    ID_COLUMN,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from aletheia_lab.data.manifest import ColumnRole, DatasetColumn, LogicalType

_INTEGER_NUMERIC = frozenset({"SeniorCitizen", "tenure"})


def _validate_exact_columns(frame: pd.DataFrame) -> None:
    names = tuple(str(value) for value in frame.columns)
    if len(names) != len(set(names)):
        raise DatasetSchemaError("processed dataset has duplicate column names")
    missing = sorted(EXPECTED_COLUMNS - set(names))
    extra = sorted(set(names) - EXPECTED_COLUMNS)
    if missing or extra or len(names) != len(EXPECTED_COLUMNS):
        raise DatasetSchemaError(
            "processed dataset columns differ from the pinned schema; "
            f"missing={missing}; extra={extra}"
        )


def _validate_values(frame: pd.DataFrame) -> None:
    null_columns = sorted(str(name) for name in frame.columns if frame[name].isna().any())
    if null_columns:
        raise DatasetSchemaError(
            f"processed dataset has null values in non-nullable columns: {null_columns}"
        )

    for name in NUMERIC_FEATURES:
        numeric = pd.to_numeric(frame[name], errors="coerce")
        if numeric.isna().any() or not numeric.map(math.isfinite).all():
            raise DatasetSchemaError(f"processed numeric column {name!r} contains non-finite data")
        if name in _INTEGER_NUMERIC and not (numeric % 1 == 0).all():
            raise DatasetSchemaError(f"processed integer column {name!r} contains fractional data")

    for name in (ID_COLUMN, *CATEGORICAL_FEATURES, TARGET_COLUMN):
        if not frame[name].map(lambda value: isinstance(value, str)).all():
            raise DatasetSchemaError(f"processed categorical column {name!r} must contain strings")


def dataset_columns_for_frame(frame: pd.DataFrame) -> tuple[DatasetColumn, ...]:
    """Validate a processed frame and return contracts in its real column order."""

    _validate_exact_columns(frame)
    _validate_values(frame)
    columns: list[DatasetColumn] = []
    for name_value in frame.columns:
        name = str(name_value)
        if name == ID_COLUMN:
            logical_type: LogicalType = "string"
            role: ColumnRole = "identifier"
        elif name == TARGET_COLUMN:
            logical_type = "category"
            role = "target"
        elif name in NUMERIC_FEATURES:
            logical_type = "integer" if name in _INTEGER_NUMERIC else "float"
            role = "numeric_feature"
        else:
            logical_type = "category"
            role = "categorical_feature"
        columns.append(
            DatasetColumn(
                name=name,
                logical_type=logical_type,
                role=role,
                nullable=False,
            )
        )
    return tuple(columns)


def load_validated_processed(
    processed_path: str | Path,
) -> tuple[pd.DataFrame, tuple[DatasetColumn, ...]]:
    """Load a processed CSV and enforce the complete manifest-facing contract."""

    frame = load_processed(processed_path)
    return frame, dataset_columns_for_frame(frame)

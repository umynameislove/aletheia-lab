"""Data-quality measurement models for validated P2 manifests.

All models are strict, frozen and versioned.  Measurements are always
recomputed from source artifacts by the measurement functions; callers cannot
self-declare quality.  A report cannot be forged or replayed to a different
dataset because it embeds the cryptographic identity of the manifest it was
derived from.

Constraints (job_02.md §2.5):
  - ``extra="forbid"``, strict, frozen (``_QualityModel`` base)
  - NaN/Inf fail on every float field
  - Column/key names are unique and canonically sorted
  - No ``passed``, ``valid``, ``verdict``, ``eligible``, ``expected_behavior``
  - Measurement functions recompute from source data; forged values fail
  - No quality thresholds or research semantics
"""

from __future__ import annotations

import math
from typing import Final, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.baseline.loader import LoadedSplits, SplitData
from aletheia_lab.baseline.schema import (
    ID_COLUMN,
    NEGATIVE_LABEL,
    NUMERIC_FEATURES,
    POSITIVE_LABEL,
    TARGET_COLUMN,
)
from aletheia_lab.data.manifest import (
    DatasetSnapshotManifest,
    ModelDataSplitManifest,
)

# --------------------------------------------------------------------------- #
# Schema version constants
# --------------------------------------------------------------------------- #

DATASET_QUALITY_SCHEMA_VERSION: Final[Literal["p2-dataset-quality/v1"]] = (
    "p2-dataset-quality/v1"
)
MODEL_SPLIT_QUALITY_SCHEMA_VERSION: Final[Literal["p2-model-split-quality/v1"]] = (
    "p2-model-split-quality/v1"
)


# --------------------------------------------------------------------------- #
# Base model
# --------------------------------------------------------------------------- #


class _QualityModel(BaseModel):
    """Base for all quality measurement models.

    Applies the same discipline as ``_StrictFrozenModel`` in ``data.manifest``:
    no extra fields, strict types, immutable after construction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# --------------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------------- #


class ColumnMissingCount(_QualityModel):
    """Missing value count for one column in the processed dataset."""

    column: str = Field(min_length=1, max_length=256)
    n_missing: int = Field(ge=0)

    @field_validator("column")
    @classmethod
    def _column_is_canonical(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("column name must be trimmed")
        return value


class NumericNonfiniteCount(_QualityModel):
    """Non-finite (NaN or infinite) value count for one numeric column."""

    column: str = Field(min_length=1, max_length=256)
    n_nonfinite: int = Field(ge=0)

    @field_validator("column")
    @classmethod
    def _column_is_canonical(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("column name must be trimmed")
        return value


class SplitCountDetail(_QualityModel):
    """Per-split record and class count measurements.

    ``positive_rate`` must be derived exactly from ``n_positive / n_records``;
    callers cannot supply an independent value.  All counts must be positive
    so that stratified splitting is verifiable.
    """

    name: str = Field(min_length=1, max_length=32)
    n_records: int = Field(gt=0)
    n_positive: int = Field(gt=0)
    n_negative: int = Field(gt=0)
    positive_rate: float = Field(ge=0.0, le=1.0)

    @field_validator("positive_rate")
    @classmethod
    def _rate_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("positive_rate must be finite")
        return value

    @model_validator(mode="after")
    def _accounting_is_consistent(self) -> SplitCountDetail:
        if self.n_positive + self.n_negative != self.n_records:
            raise ValueError("n_positive + n_negative must equal n_records")
        expected_rate = self.n_positive / self.n_records
        if abs(self.positive_rate - expected_rate) > 1e-12:
            raise ValueError("positive_rate must be derived from label counts")
        return self


# --------------------------------------------------------------------------- #
# Report models
# --------------------------------------------------------------------------- #


class DatasetQualityReport(_QualityModel):
    """Recomputed quality measurements bound to one dataset snapshot identity.

    Every field is derived by ``measure_dataset`` from the actual source frame
    and the validated snapshot manifest.  The ``dataset_identity_sha256`` binding
    prevents replay against a different snapshot.
    """

    schema_version: Literal["p2-dataset-quality/v1"] = DATASET_QUALITY_SCHEMA_VERSION
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_sha256: str = Field(min_length=64, max_length=64)
    dataset_identity_sha256: str = Field(min_length=64, max_length=64)
    n_rows: int = Field(gt=0)
    n_cols: int = Field(gt=0)
    n_duplicate_ids: int = Field(ge=0)
    n_blank_ids: int = Field(ge=0)
    n_positive: int = Field(ge=0)
    n_negative: int = Field(ge=0)
    missing_per_column: tuple[ColumnMissingCount, ...]
    nonfinite_per_numeric: tuple[NumericNonfiniteCount, ...]

    @model_validator(mode="after")
    def _report_is_self_consistent(self) -> DatasetQualityReport:
        miss_cols = tuple(c.column for c in self.missing_per_column)
        if len(miss_cols) != len(set(miss_cols)):
            raise ValueError("missing_per_column must have unique column names")
        if miss_cols != tuple(sorted(miss_cols)):
            raise ValueError("missing_per_column must be sorted by column name")
        nonf_cols = tuple(c.column for c in self.nonfinite_per_numeric)
        if len(nonf_cols) != len(set(nonf_cols)):
            raise ValueError("nonfinite_per_numeric must have unique column names")
        if nonf_cols != tuple(sorted(nonf_cols)):
            raise ValueError("nonfinite_per_numeric must be sorted by column name")
        if self.n_positive + self.n_negative != self.n_rows:
            raise ValueError("n_positive + n_negative must equal n_rows")
        return self


class ModelSplitQualityReport(_QualityModel):
    """Recomputed quality measurements bound to one model-data split identity.

    Bound to both ``snapshot.identity_sha256()`` and
    ``model_split.identity_sha256()``; a report cannot be replayed against
    different artifacts.
    """

    schema_version: Literal["p2-model-split-quality/v1"] = (
        MODEL_SPLIT_QUALITY_SCHEMA_VERSION
    )
    dataset_identity_sha256: str = Field(min_length=64, max_length=64)
    model_split_identity_sha256: str = Field(min_length=64, max_length=64)
    n_rows: int = Field(gt=0)
    n_overlap: int = Field(ge=0)
    n_missing: int = Field(ge=0)
    n_extra: int = Field(ge=0)
    train: SplitCountDetail
    validation: SplitCountDetail
    test: SplitCountDetail

    @model_validator(mode="after")
    def _split_totals_match_n_rows(self) -> ModelSplitQualityReport:
        total = self.train.n_records + self.validation.n_records + self.test.n_records
        if total != self.n_rows:
            raise ValueError("split record counts must sum to n_rows")
        return self


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #


def _to_split_count_detail(split_data: SplitData, name: str) -> SplitCountDetail:
    """Recompute per-split quality counts from a SplitData partition.

    ``SplitData.target`` is already encoded as 0/1 integers by
    ``split_dataset``; positives are rows where target == 1.
    """
    n_records = int(len(split_data.target))
    n_positive = int(split_data.target.sum())
    n_negative = n_records - n_positive
    return SplitCountDetail(
        name=name,
        n_records=n_records,
        n_positive=n_positive,
        n_negative=n_negative,
        positive_rate=n_positive / n_records,
    )


# --------------------------------------------------------------------------- #
# Measurement functions
# --------------------------------------------------------------------------- #


def measure_dataset(
    snapshot: DatasetSnapshotManifest,
    frame: pd.DataFrame,
) -> DatasetQualityReport:
    """Recompute all dataset quality measurements from source artifacts.

    Every field is derived from ``frame``; no caller-supplied measurement is
    accepted.  The report is bound to ``snapshot.identity_sha256()`` so it
    cannot be replayed against a different snapshot.

    Args:
        snapshot: The validated dataset snapshot manifest.
        frame: The processed DataFrame produced by ``load_processed``.

    Returns:
        A ``DatasetQualityReport`` bound to the snapshot identity.
    """
    # Missing count for every column, sorted alphabetically
    sorted_columns: list[str] = sorted(str(col) for col in frame.columns.tolist())
    missing_per_column: tuple[ColumnMissingCount, ...] = tuple(
        ColumnMissingCount(column=col, n_missing=int(frame[col].isna().sum()))
        for col in sorted_columns
    )

    # Non-finite count for numeric columns only, sorted alphabetically
    nonfinite_per_numeric: tuple[NumericNonfiniteCount, ...] = tuple(
        NumericNonfiniteCount(
            column=col,
            n_nonfinite=int(
                pd.to_numeric(frame[col], errors="coerce")
                .apply(lambda x: not math.isfinite(x))
                .sum()
            ),
        )
        for col in sorted(NUMERIC_FEATURES)
        if col in frame.columns
    )

    # Target label counts from raw label strings ("Yes" / "No")
    n_positive = int((frame[TARGET_COLUMN] == POSITIVE_LABEL).sum())
    n_negative = int((frame[TARGET_COLUMN] == NEGATIVE_LABEL).sum())

    # Duplicate and blank ID counts
    n_duplicate_ids = int(frame[ID_COLUMN].duplicated().sum())
    n_blank_ids = int(
        (
            frame[ID_COLUMN].isna()
            | (frame[ID_COLUMN].astype(str).str.strip() == "")
        ).sum()
    )

    return DatasetQualityReport(
        dataset_id=snapshot.dataset_id,
        dataset_sha256=snapshot.dataset_sha256,
        dataset_identity_sha256=snapshot.identity_sha256(),
        n_rows=len(frame),
        n_cols=len(frame.columns),
        n_duplicate_ids=n_duplicate_ids,
        n_blank_ids=n_blank_ids,
        n_positive=n_positive,
        n_negative=n_negative,
        missing_per_column=missing_per_column,
        nonfinite_per_numeric=nonfinite_per_numeric,
    )


def measure_model_split(
    snapshot: DatasetSnapshotManifest,
    model_split: ModelDataSplitManifest,
    splits: LoadedSplits,
) -> ModelSplitQualityReport:
    """Recompute all model-split quality measurements from source artifacts.

    Overlap, missing and extra record counts are computed directly from the
    manifest hash sets (not from ``splits``); split counts are computed from
    the binary target vectors in ``splits``.  The report is bound to both
    manifest identities.

    Args:
        snapshot: The validated dataset snapshot manifest.
        model_split: The validated model-data split manifest.
        splits: The ``LoadedSplits`` produced by ``split_dataset`` for the
            same processed file.

    Returns:
        A ``ModelSplitQualityReport`` bound to both manifest identities.
    """
    # Cross-artifact membership checks using hash sets from manifests
    train_set = set(model_split.train.record_id_hashes)
    val_set = set(model_split.validation.record_id_hashes)
    test_set = set(model_split.test.record_id_hashes)
    dataset_set = set(snapshot.record_id_hashes)
    all_split_hashes = train_set | val_set | test_set

    n_overlap = len(
        (train_set & val_set) | (train_set & test_set) | (val_set & test_set)
    )
    n_missing = len(dataset_set - all_split_hashes)
    n_extra = len(all_split_hashes - dataset_set)

    return ModelSplitQualityReport(
        dataset_identity_sha256=snapshot.identity_sha256(),
        model_split_identity_sha256=model_split.identity_sha256(),
        n_rows=model_split.n_rows,
        n_overlap=n_overlap,
        n_missing=n_missing,
        n_extra=n_extra,
        train=_to_split_count_detail(splits.train, "train"),
        validation=_to_split_count_detail(splits.validation, "validation"),
        test=_to_split_count_detail(splits.test, "test"),
    )

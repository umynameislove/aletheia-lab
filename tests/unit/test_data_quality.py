"""Unit tests for data-quality measurement models and functions (job_02.md §2.5).

Coverage (job_02.md §2.7):
  - valid DatasetQualityReport and ModelSplitQualityReport construction
  - quality expected computed independently in test
  - forged count/rate via model_copy and model_construct
  - forged quality report (replay to different snapshot)
  - unknown field (extra="forbid")
  - NaN/Inf rejection on float fields
  - inconsistent counts (n_pos + n_neg != n_rec)
  - wrong positive_rate (not derived from counts)
  - no self-declared pass/eligibility fields
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.baseline.loader import split_dataset
from aletheia_lab.baseline.schema import NUMERIC_FEATURES, TARGET_COLUMN
from aletheia_lab.data.manifest_generation import (
    build_dataset_snapshot,
    build_model_data_split,
)
from aletheia_lab.data.quality import (
    DatasetQualityReport,
    ModelSplitQualityReport,
    SplitCountDetail,
    measure_dataset,
    measure_model_split,
)

_DATASET_ID = "telco_customer_churn"
_SNAP_REL = "manifests/snapshot.json"
_SPLIT_REL = "manifests/model-split.json"
_FORBIDDEN_FIELDS = frozenset(
    {"passed", "valid", "verdict", "eligible", "expected_behavior"}
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def processed_csv(tmp_path: Path, make_frame) -> Path:
    csv = tmp_path / "processed.csv"
    make_frame(n=240, seed=0).to_csv(csv, index=False, lineterminator="\n")
    return csv


@pytest.fixture
def quality_fixtures(tmp_path: Path, make_frame, processed_csv: Path):
    """Return (snapshot, model_split, splits, frame, dataset_report, split_report)."""
    frame: pd.DataFrame = make_frame(n=240, seed=0)
    out = tmp_path / "out"
    snap = build_dataset_snapshot(
        processed_csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    model_split = build_model_data_split(
        snap, processed_csv, output_root=out, relative_path=_SPLIT_REL
    )
    splits = split_dataset(
        frame,
        dataset_id=snap.dataset_id,
        dataset_sha256=snap.dataset_sha256,
        seed=42,
        ratios={"train": 0.7, "validation": 0.15, "test": 0.15},
        stratified=True,
    )
    dq = measure_dataset(snap, frame)
    sq = measure_model_split(snap, model_split, splits)
    return snap, model_split, splits, frame, dq, sq


# ---------------------------------------------------------------------------
# No forbidden fields
# ---------------------------------------------------------------------------


def test_dataset_quality_report_no_forbidden_fields() -> None:
    """DatasetQualityReport must not expose pass/eligibility/verdict fields."""
    assert not (set(DatasetQualityReport.model_fields) & _FORBIDDEN_FIELDS)


def test_model_split_quality_report_no_forbidden_fields() -> None:
    """ModelSplitQualityReport must not expose pass/eligibility/verdict fields."""
    assert not (set(ModelSplitQualityReport.model_fields) & _FORBIDDEN_FIELDS)


def test_split_count_detail_no_forbidden_fields() -> None:
    """SplitCountDetail must not expose pass/eligibility/verdict fields."""
    assert not (set(SplitCountDetail.model_fields) & _FORBIDDEN_FIELDS)


# ---------------------------------------------------------------------------
# Extra/unknown field rejection
# ---------------------------------------------------------------------------


def test_split_count_detail_unknown_field_rejected() -> None:
    """SplitCountDetail rejects unknown fields (extra='forbid')."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=5,
            n_negative=5,
            positive_rate=0.5,
            verdict="ok",  # forbidden extra field
        )


def test_dataset_quality_report_unknown_field_rejected(
    quality_fixtures,
) -> None:
    """DatasetQualityReport rejects unknown fields (extra='forbid')."""
    snap, _, _, frame, dq, _ = quality_fixtures
    raw = dq.model_dump(warnings=False)
    raw["verdict"] = "pass"  # inject forbidden field
    with pytest.raises(ValidationError, match="Extra inputs"):
        DatasetQualityReport.model_validate(raw)


# ---------------------------------------------------------------------------
# NaN / Inf rejection
# ---------------------------------------------------------------------------


def test_split_count_detail_nan_rate_rejected() -> None:
    """SplitCountDetail rejects NaN in positive_rate."""
    with pytest.raises(ValidationError):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=5,
            n_negative=5,
            positive_rate=float("nan"),
        )


def test_split_count_detail_inf_rate_rejected() -> None:
    """SplitCountDetail rejects Inf in positive_rate."""
    with pytest.raises(ValidationError):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=5,
            n_negative=5,
            positive_rate=float("inf"),
        )


# ---------------------------------------------------------------------------
# Inconsistent count / rate rejection
# ---------------------------------------------------------------------------


def test_split_count_detail_inconsistent_counts_rejected() -> None:
    """SplitCountDetail rejects n_positive + n_negative != n_records."""
    with pytest.raises(ValidationError, match="n_positive \\+ n_negative"):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=6,
            n_negative=6,  # 6 + 6 = 12 != 10
            positive_rate=0.6,
        )


def test_split_count_detail_wrong_rate_rejected() -> None:
    """SplitCountDetail rejects positive_rate not derived from counts."""
    with pytest.raises(ValidationError, match="derived"):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=5,
            n_negative=5,
            positive_rate=0.9,  # correct would be 0.5
        )


def test_dataset_quality_report_inconsistent_row_count_rejected(
    quality_fixtures,
) -> None:
    """DatasetQualityReport rejects n_positive + n_negative != n_rows."""
    _, _, _, _, dq, _ = quality_fixtures
    raw = dq.model_dump(warnings=False)
    raw["n_positive"] = raw["n_positive"] + 100  # break the accounting
    with pytest.raises(ValidationError, match="n_positive \\+ n_negative"):
        DatasetQualityReport.model_validate(raw)


def test_model_split_quality_report_wrong_total_rejected(
    quality_fixtures,
) -> None:
    """ModelSplitQualityReport rejects split totals that don't sum to n_rows."""
    _, _, _, _, _, sq = quality_fixtures
    raw = sq.model_dump(warnings=False)
    raw["train"]["n_records"] = raw["train"]["n_records"] + 99
    raw["train"]["n_positive"] = raw["train"]["n_positive"] + 99
    raw["train"]["positive_rate"] = raw["train"]["n_positive"] / raw["train"]["n_records"]
    with pytest.raises(ValidationError, match="sum to n_rows"):
        ModelSplitQualityReport.model_validate(raw)


# ---------------------------------------------------------------------------
# measure_dataset — correctness (computed independently in test)
# ---------------------------------------------------------------------------


def test_measure_dataset_row_count_matches_frame(quality_fixtures) -> None:
    """DatasetQualityReport.n_rows equals the frame length."""
    _, _, _, frame, dq, _ = quality_fixtures
    assert dq.n_rows == len(frame)


def test_measure_dataset_col_count_matches_frame(quality_fixtures) -> None:
    """DatasetQualityReport.n_cols equals the number of frame columns."""
    _, _, _, frame, dq, _ = quality_fixtures
    assert dq.n_cols == len(frame.columns)


def test_measure_dataset_target_counts_computed_independently(
    quality_fixtures,
) -> None:
    """Target counts in DatasetQualityReport match independently computed values."""
    _, _, _, frame, dq, _ = quality_fixtures
    # Compute expected values INDEPENDENTLY from measure_dataset
    expected_n_positive = int((frame[TARGET_COLUMN] == "Yes").sum())
    expected_n_negative = int((frame[TARGET_COLUMN] == "No").sum())
    assert dq.n_positive == expected_n_positive
    assert dq.n_negative == expected_n_negative
    assert dq.n_positive + dq.n_negative == dq.n_rows


def test_measure_dataset_no_duplicate_ids_on_clean_frame(quality_fixtures) -> None:
    """Clean synthetic frame has zero duplicate/blank IDs."""
    _, _, _, _, dq, _ = quality_fixtures
    assert dq.n_duplicate_ids == 0
    assert dq.n_blank_ids == 0


def test_measure_dataset_missing_per_column_sorted_unique(quality_fixtures) -> None:
    """missing_per_column is sorted alphabetically with unique column names."""
    _, _, _, frame, dq, _ = quality_fixtures
    cols = tuple(c.column for c in dq.missing_per_column)
    assert cols == tuple(sorted(cols))
    assert len(cols) == len(set(cols))
    assert set(cols) == set(str(c) for c in frame.columns)


def test_measure_dataset_nonfinite_per_numeric_sorted_unique(
    quality_fixtures,
) -> None:
    """nonfinite_per_numeric is sorted alphabetically with unique numeric column names."""
    _, _, _, _, dq, _ = quality_fixtures
    cols = tuple(c.column for c in dq.nonfinite_per_numeric)
    assert cols == tuple(sorted(cols))
    assert len(cols) == len(set(cols))
    assert set(cols) == set(sorted(NUMERIC_FEATURES))


def test_measure_dataset_missing_count_computed_independently(
    quality_fixtures,
) -> None:
    """Missing counts match independently computed values for every column."""
    _, _, _, frame, dq, _ = quality_fixtures
    # Compute expected missing per column INDEPENDENTLY
    expected = {
        str(col): int(frame[col].isna().sum()) for col in sorted(frame.columns)
    }
    actual = {c.column: c.n_missing for c in dq.missing_per_column}
    assert actual == expected


def test_measure_dataset_identity_bound_to_snapshot(quality_fixtures) -> None:
    """DatasetQualityReport is bound to the exact snapshot identity."""
    snap, _, _, _, dq, _ = quality_fixtures
    assert dq.dataset_identity_sha256 == snap.identity_sha256()
    assert dq.dataset_sha256 == snap.dataset_sha256


# ---------------------------------------------------------------------------
# measure_model_split — correctness
# ---------------------------------------------------------------------------


def test_measure_model_split_total_rows(quality_fixtures) -> None:
    """ModelSplitQualityReport.n_rows matches the model_split.n_rows."""
    _, model_split, _, _, _, sq = quality_fixtures
    assert sq.n_rows == model_split.n_rows


def test_measure_model_split_no_overlap_clean(quality_fixtures) -> None:
    """A clean split has zero overlap, zero missing, zero extra records."""
    _, _, _, _, _, sq = quality_fixtures
    assert sq.n_overlap == 0
    assert sq.n_missing == 0
    assert sq.n_extra == 0


def test_measure_model_split_positive_rates_computed_independently(
    quality_fixtures,
) -> None:
    """Positive rates in each split match independently computed values."""
    _, _, splits, _, _, sq = quality_fixtures
    # Compute expected values INDEPENDENTLY from measure_model_split
    for split_name, split_data, detail in (
        ("train", splits.train, sq.train),
        ("validation", splits.validation, sq.validation),
        ("test", splits.test, sq.test),
    ):
        n_pos = int(split_data.target.sum())
        n_rec = int(len(split_data.target))
        expected_rate = n_pos / n_rec
        assert detail.n_records == n_rec, f"{split_name}: n_records mismatch"
        assert detail.n_positive == n_pos, f"{split_name}: n_positive mismatch"
        assert abs(detail.positive_rate - expected_rate) < 1e-12, (
            f"{split_name}: positive_rate mismatch"
        )


def test_measure_model_split_identity_bound_to_manifests(quality_fixtures) -> None:
    """ModelSplitQualityReport is bound to both snapshot and model_split identities."""
    snap, model_split, _, _, _, sq = quality_fixtures
    assert sq.dataset_identity_sha256 == snap.identity_sha256()
    assert sq.model_split_identity_sha256 == model_split.identity_sha256()


# ---------------------------------------------------------------------------
# Forged quality reports
# ---------------------------------------------------------------------------


def test_forged_dataset_quality_report_model_copy_detected(quality_fixtures) -> None:
    """model_copy tampering on DatasetQualityReport is detected on re-validation."""
    _, _, _, _, dq, _ = quality_fixtures
    forged = dq.model_copy(update={"n_positive": dq.n_positive + 999})
    with pytest.raises(ValidationError):
        DatasetQualityReport.model_validate(forged.model_dump(warnings=False))


def test_forged_dataset_quality_report_model_construct_detected(
    quality_fixtures,
) -> None:
    """model_construct bypasses validators but re-validation exposes the tamper."""
    _, _, _, _, dq, _ = quality_fixtures
    forged = DatasetQualityReport.model_construct(
        **{**dq.model_dump(warnings=False), "n_positive": 999}
    )
    with pytest.raises(ValidationError):
        DatasetQualityReport.model_validate(forged.model_dump(warnings=False))


def test_forged_model_split_quality_report_detected(quality_fixtures) -> None:
    """model_copy tampering on ModelSplitQualityReport is detected on re-validation."""
    _, _, _, _, _, sq = quality_fixtures
    raw = sq.model_dump(warnings=False)
    # Forge: inflate train record count so totals no longer sum to n_rows
    raw["train"]["n_records"] = raw["train"]["n_records"] + 999
    raw["train"]["n_positive"] = raw["train"]["n_positive"] + 999
    raw["train"]["positive_rate"] = raw["train"]["n_positive"] / raw["train"]["n_records"]
    with pytest.raises(ValidationError):
        ModelSplitQualityReport.model_validate(raw)


def test_quality_report_replay_different_snapshot(
    tmp_path: Path, make_frame
) -> None:
    """A report from snapshot_A has a different identity binding than snapshot_B."""
    frame_a = make_frame(n=240, seed=0)
    frame_b = make_frame(n=240, seed=99)
    csv_a = tmp_path / "pa.csv"
    csv_b = tmp_path / "pb.csv"
    frame_a.to_csv(csv_a, index=False, lineterminator="\n")
    frame_b.to_csv(csv_b, index=False, lineterminator="\n")
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    snap_a = build_dataset_snapshot(
        csv_a, dataset_id=_DATASET_ID, output_root=out_a, relative_path=_SNAP_REL
    )
    snap_b = build_dataset_snapshot(
        csv_b, dataset_id=_DATASET_ID, output_root=out_b, relative_path=_SNAP_REL
    )
    report_a = measure_dataset(snap_a, frame_a)
    # The report is bound to snap_a; replaying it for snap_b shows the mismatch
    assert report_a.dataset_identity_sha256 != snap_b.identity_sha256(), (
        "Replay detection: identity sha256 must differ across distinct snapshots"
    )
    # A fresh measurement from snap_b yields a different report
    report_b = measure_dataset(snap_b, frame_b)
    assert report_a.dataset_identity_sha256 != report_b.dataset_identity_sha256

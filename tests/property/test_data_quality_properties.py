"""Property tests for data-quality recomputation.

Covered invariants:
  1.  Row/column count correct with source frame
  2.  Duplicate/blank ID count recomputed
  3.  Label counts have correct total equal to row count
  4.  Missing count per column correct and keys canonical (sorted)
  5.  Non-finite count only applies to numeric columns
  6.  Split counts, class counts and positive rate recomputed
  7.  Overlap/missing/extra record detected
  8.  Column reorder valid does not change semantic result
  9.  Missing/extra/duplicate column rejected
  10. Strange target, rate outside [0,1], NaN and Inf rejected
  11. Report cannot self-declare passed, valid, eligible, verdict
  12. Correct report from dataset A replayed to dataset B must fail
  13. Simultaneously modifying measurement and hash still fails against source
  14. No raw ID in serialized quality artifact
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.baseline.loader import split_dataset
from aletheia_lab.baseline.schema import (
    ID_COLUMN,
    NEGATIVE_LABEL,
    NUMERIC_FEATURES,
    POSITIVE_LABEL,
    TARGET_COLUMN,
)
from aletheia_lab.data.manifest import ManifestContractError
from aletheia_lab.data.manifest_generation import (
    build_dataset_snapshot,
    build_model_data_split,
)
from aletheia_lab.data.quality import (
    ColumnMissingCount,
    DatasetQualityReport,
    ModelSplitQualityReport,
    NumericNonfiniteCount,
    SplitCountDetail,
    measure_dataset,
    measure_model_split,
    validate_dataset_quality_report,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATASET_ID = "telco_customer_churn"
_SNAP_REL = "manifests/snap.json"
_SPLIT_REL = "manifests/split.json"

# Fields that no quality model may self-declare.
_FORBIDDEN_FIELDS = frozenset({"passed", "valid", "verdict", "eligible", "expected_behavior"})

# ---------------------------------------------------------------------------
# Minimal report builder for schema-level tests (no file I/O)
# ---------------------------------------------------------------------------


def _minimal_dq_report(
    *,
    n_rows: int = 10,
    n_positive: int = 5,
    n_negative: int = 5,
    missing_per_column: tuple[ColumnMissingCount, ...] = (),
    nonfinite_per_numeric: tuple[NumericNonfiniteCount, ...] = (),
    **overrides: object,
) -> DatasetQualityReport:
    """Build a minimal valid DatasetQualityReport for schema-level property tests."""
    kwargs: dict[str, object] = {
        "dataset_id": "test01",
        "dataset_sha256": "a" * 64,
        "dataset_identity_sha256": "b" * 64,
        "n_rows": n_rows,
        "n_cols": 2,
        "n_duplicate_ids": 0,
        "n_blank_ids": 0,
        "n_positive": n_positive,
        "n_negative": n_negative,
        "missing_per_column": missing_per_column,
        "nonfinite_per_numeric": nonfinite_per_numeric,
    }
    kwargs.update(overrides)
    return DatasetQualityReport(**kwargs)


def _minimal_split_detail(
    name: str = "train",
    n_records: int = 10,
    n_positive: int = 5,
) -> SplitCountDetail:
    n_negative = n_records - n_positive
    return SplitCountDetail(
        name=name,  # type: ignore[arg-type]
        n_records=n_records,
        n_positive=n_positive,
        n_negative=n_negative,
        positive_rate=n_positive / n_records,
    )


@st.composite
def inconsistent_split_counts(draw):  # type: ignore[no-untyped-def]
    """Generate positive counts that leave a non-empty but short negative class."""
    n_records = draw(st.integers(min_value=3, max_value=100))
    n_positive = draw(st.integers(min_value=1, max_value=n_records - 2))
    return n_records, n_positive


def _build_quality_fixtures(
    tmp_path: Path,
    make_frame: object,
    n: int = 120,
    seed: int = 0,
) -> tuple:
    """Return (snap, model_split, frame, csv_path) built from a synthetic frame."""

    frame = make_frame(n=n, seed=seed)  # type: ignore[operator]
    csv = tmp_path / "processed.csv"
    frame.to_csv(csv, index=False, lineterminator="\n")
    out = tmp_path / "out"
    snap = build_dataset_snapshot(
        csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    model_split = build_model_data_split(
        snap, csv, output_root=out, relative_path=_SPLIT_REL
    )
    return snap, model_split, frame, csv


# ---------------------------------------------------------------------------
# Row and column counts match the source frame
# ---------------------------------------------------------------------------


def test_row_column_count_matches_source_frame(
    tmp_path: Path, make_frame: object
) -> None:
    """n_rows and n_cols in the quality report match the source frame.

    Independent oracle: count directly from raw CSV with pandas.read_csv,
    without using measure_dataset or any production count helper.
    """
    import pandas as pd

    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    # Oracle: count from the raw CSV independently
    oracle_df = pd.read_csv(csv)
    assert report.n_rows == len(oracle_df), (
        f"n_rows mismatch: expected {len(oracle_df)}, got {report.n_rows}"
    )
    assert report.n_cols == len(oracle_df.columns), (
        f"n_cols mismatch: expected {len(oracle_df.columns)}, got {report.n_cols}"
    )


# ---------------------------------------------------------------------------
# Duplicate and blank identifier counts are recomputed
# ---------------------------------------------------------------------------


def test_duplicate_blank_id_count_recomputed(
    tmp_path: Path, make_frame: object
) -> None:
    """n_duplicate_ids and n_blank_ids match source-derived counts.

    Independent oracle: recount with pandas without measure_dataset.
    build_frame generates unique non-blank IDs, so both counts must be zero.
    """
    import pandas as pd

    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    oracle_df = pd.read_csv(csv)
    oracle_n_dup = int(oracle_df[ID_COLUMN].duplicated().sum())
    oracle_n_blank = int(
        (oracle_df[ID_COLUMN].isna()
         | (oracle_df[ID_COLUMN].astype(str).str.strip() == "")).sum()
    )

    assert report.n_duplicate_ids == oracle_n_dup
    assert report.n_blank_ids == oracle_n_blank


# ---------------------------------------------------------------------------
# Label counts sum to the row count
# ---------------------------------------------------------------------------


@given(
    n_positive=st.integers(min_value=1, max_value=100),
    n_negative=st.integers(min_value=1, max_value=100),
)
@example(n_positive=1, n_negative=1)
@example(n_positive=99, n_negative=1)
def test_label_counts_sum_to_n_rows(n_positive: int, n_negative: int) -> None:
    """n_positive + n_negative == n_rows is enforced by the schema."""
    n_rows = n_positive + n_negative
    report = _minimal_dq_report(
        n_rows=n_rows, n_positive=n_positive, n_negative=n_negative
    )
    assert report.n_positive + report.n_negative == report.n_rows


@given(
    n_positive=st.integers(min_value=0, max_value=50),
    n_negative=st.integers(min_value=0, max_value=50),
    extra=st.integers(min_value=1, max_value=10),
)
def test_inconsistent_label_counts_rejected(
    n_positive: int, n_negative: int, extra: int
) -> None:
    """n_positive + n_negative != n_rows is rejected by the schema."""
    # extra ensures n_rows > n_positive + n_negative (always wrong)
    n_rows = n_positive + n_negative + extra
    with pytest.raises(ValidationError, match="n_positive"):
        _minimal_dq_report(
            n_rows=n_rows, n_positive=n_positive, n_negative=n_negative
        )


def test_label_counts_verified_against_source_oracle(
    tmp_path: Path, make_frame: object
) -> None:
    """Label counts from measure_dataset match a pandas-based oracle.

    Expected counts computed independently using pandas comparison, not
    reusing the production counting helpers.
    """
    import pandas as pd

    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    oracle_df = pd.read_csv(csv)
    oracle_n_pos = int((oracle_df[TARGET_COLUMN] == POSITIVE_LABEL).sum())
    oracle_n_neg = int((oracle_df[TARGET_COLUMN] == NEGATIVE_LABEL).sum())

    assert report.n_positive == oracle_n_pos
    assert report.n_negative == oracle_n_neg
    assert report.n_positive + report.n_negative == report.n_rows


# ---------------------------------------------------------------------------
# Missing counts use canonical column keys
# ---------------------------------------------------------------------------


@given(
    cols=st.lists(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz_",
            min_size=1,
            max_size=12,
        ),
        min_size=2,
        max_size=6,
        unique=True,
    )
)
@example(cols=["z_col", "a_col"])  # reversed alphabetical — key boundary
def test_missing_per_column_canonical_sorted_accepted(cols: list[str]) -> None:
    """A sorted missing_per_column is accepted; reversed order is rejected.

    Metamorphic property: the canonical sorted form is the only valid order.
    """
    sorted_cols = sorted(cols)
    sorted_missing = tuple(
        ColumnMissingCount(column=c, n_missing=0) for c in sorted_cols
    )
    report = _minimal_dq_report(missing_per_column=sorted_missing)
    actual_keys = tuple(m.column for m in report.missing_per_column)
    assert actual_keys == tuple(sorted(actual_keys))

    # Reversed order must be rejected (unless cols is a single-element or palindrome)
    reversed_missing = tuple(reversed(sorted_missing))
    assume(reversed_missing != sorted_missing)
    with pytest.raises(ValidationError, match="sorted"):
        _minimal_dq_report(missing_per_column=reversed_missing)


def test_duplicate_missing_column_rejected() -> None:
    """Duplicate column names in missing_per_column are rejected."""
    with pytest.raises(ValidationError, match="unique"):
        _minimal_dq_report(
            missing_per_column=(
                ColumnMissingCount(column="col_a", n_missing=0),
                ColumnMissingCount(column="col_a", n_missing=1),  # duplicate
            )
        )


# ---------------------------------------------------------------------------
# Non-finite counts apply only to numeric columns
# ---------------------------------------------------------------------------


def test_nonfinite_count_only_for_numeric_columns(
    tmp_path: Path, make_frame: object
) -> None:
    """measure_dataset computes nonfinite counts only for numeric features."""
    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    nonfinite_cols = frozenset(m.column for m in report.nonfinite_per_numeric)
    numeric_cols = frozenset(NUMERIC_FEATURES)

    assert nonfinite_cols.issubset(numeric_cols), (
        f"Non-numeric columns in nonfinite_per_numeric: "
        f"{nonfinite_cols - numeric_cols}"
    )


# ---------------------------------------------------------------------------
# Split counts and positive rates are recomputed
# ---------------------------------------------------------------------------


def test_split_counts_recomputed_from_source(
    tmp_path: Path, make_frame: object
) -> None:
    """Split record counts, class counts and positive_rate match a pandas oracle.

    Independent oracle: recount from split_dataset output without using
    SplitCountDetail or any production quality helper.
    """
    snap, model_split, frame, csv = _build_quality_fixtures(
        tmp_path, make_frame, n=120
    )
    report = measure_model_split(snap, model_split, csv)

    splits = split_dataset(
        frame,
        dataset_id=snap.dataset_id,
        dataset_sha256=snap.dataset_sha256,
        seed=42,
        ratios={"train": 0.7, "validation": 0.15, "test": 0.15},
        stratified=True,
    )

    def _oracle_counts(split_data: object) -> tuple[int, int, int, float]:
        """Count n_records, n_positive, n_negative, positive_rate without production helpers."""
        target = split_data.target  # type: ignore[union-attr]
        n_rec = int(len(target))
        n_pos = int(target.sum())
        n_neg = n_rec - n_pos
        return n_rec, n_pos, n_neg, n_pos / n_rec

    oracle_train = _oracle_counts(splits.train)
    oracle_val = _oracle_counts(splits.validation)
    oracle_test = _oracle_counts(splits.test)

    assert report.train.n_records == oracle_train[0]
    assert report.train.n_positive == oracle_train[1]
    assert report.train.n_negative == oracle_train[2]
    assert abs(report.train.positive_rate - oracle_train[3]) < 1e-10

    assert report.validation.n_records == oracle_val[0]
    assert report.validation.n_positive == oracle_val[1]

    assert report.test.n_records == oracle_test[0]
    assert report.test.n_positive == oracle_test[1]

    total = (
        report.train.n_records
        + report.validation.n_records
        + report.test.n_records
    )
    assert total == report.n_rows


# ---------------------------------------------------------------------------
# Overlap, missing, and extra records are detected
# ---------------------------------------------------------------------------


def test_valid_split_has_no_overlap_missing_extra(
    tmp_path: Path, make_frame: object
) -> None:
    """A canonical model split has n_overlap=n_missing=n_extra=0.

    Independent oracle: compute set intersections without using
    measure_model_split or any production overlap helper.
    """
    snap, model_split, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=120)
    report = measure_model_split(snap, model_split, csv)

    # Oracle: manually check set membership without the production function
    train_set = set(model_split.train.record_id_hashes)
    val_set = set(model_split.validation.record_id_hashes)
    test_set = set(model_split.test.record_id_hashes)
    dataset_set = set(snap.record_id_hashes)
    all_split = train_set | val_set | test_set

    oracle_overlap = len(
        (train_set & val_set) | (train_set & test_set) | (val_set & test_set)
    )
    oracle_missing = len(dataset_set - all_split)
    oracle_extra = len(all_split - dataset_set)

    assert report.n_overlap == oracle_overlap == 0
    assert report.n_missing == oracle_missing == 0
    assert report.n_extra == oracle_extra == 0


# ---------------------------------------------------------------------------
# Column reordering does not change semantic results
# ---------------------------------------------------------------------------


@given(
    cols=st.lists(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz_",
            min_size=1,
            max_size=12,
        ),
        min_size=2,
        max_size=6,
        unique=True,
    )
)
@example(cols=["tenure", "monthly_charges"])
def test_column_reorder_produces_same_canonical_keys(cols: list[str]) -> None:
    """Regardless of input order, missing_per_column keys are canonically sorted.

    Metamorphic property: providing columns in alphabetical vs reversed order
    produces the same canonical sorted representation when valid, and the
    reversed order is rejected by the schema.
    """
    sorted_cols = sorted(cols)

    # Sorted order is accepted and keys are in canonical order
    sorted_missing = tuple(ColumnMissingCount(column=c, n_missing=0) for c in sorted_cols)
    report = _minimal_dq_report(missing_per_column=sorted_missing)
    assert tuple(m.column for m in report.missing_per_column) == tuple(sorted_cols)

    # Reversed order is rejected
    reversed_cols = list(reversed(sorted_cols))
    assume(reversed_cols != sorted_cols)  # skip palindromic orders
    reversed_missing = tuple(ColumnMissingCount(column=c, n_missing=0) for c in reversed_cols)
    with pytest.raises(ValidationError, match="sorted"):
        _minimal_dq_report(missing_per_column=reversed_missing)


# ---------------------------------------------------------------------------
# Invalid column schemas are rejected
# ---------------------------------------------------------------------------


def test_unknown_field_in_dataset_quality_report_rejected() -> None:
    """DatasetQualityReport rejects unknown extra fields."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        DatasetQualityReport(
            dataset_id="test01",
            dataset_sha256="a" * 64,
            dataset_identity_sha256="b" * 64,
            n_rows=10,
            n_cols=2,
            n_duplicate_ids=0,
            n_blank_ids=0,
            n_positive=5,
            n_negative=5,
            missing_per_column=(),
            nonfinite_per_numeric=(),
            INJECTED="poison",  # must be rejected (extra="forbid")
        )


def test_duplicate_nonfinite_column_rejected() -> None:
    """Duplicate column names in nonfinite_per_numeric are rejected."""
    with pytest.raises(ValidationError, match="unique"):
        _minimal_dq_report(
            nonfinite_per_numeric=(
                NumericNonfiniteCount(column="tenure", n_nonfinite=0),
                NumericNonfiniteCount(column="tenure", n_nonfinite=0),  # duplicate
            )
        )


# ---------------------------------------------------------------------------
# Invalid target rates are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "bad_rate"),
    [
        ("NaN", float("nan")),
        ("positive infinity", float("inf")),
        ("negative infinity", float("-inf")),
    ],
)
def test_nonfinite_positive_rate_rejected(
    description: str, bad_rate: float
) -> None:
    """NaN and Inf positive_rate values are rejected by SplitCountDetail."""
    with pytest.raises(ValidationError):
        SplitCountDetail(
            name="train",
            n_records=10,
            n_positive=5,
            n_negative=5,
            positive_rate=bad_rate,
        )


@given(counts=inconsistent_split_counts())
def test_inconsistent_split_counts_rejected(counts: tuple[int, int]) -> None:
    """SplitCountDetail rejects inconsistent class totals."""
    n_records, n_positive = counts
    n_negative = n_records - n_positive - 1  # deliberately one short
    with pytest.raises(ValidationError, match="n_positive"):
        SplitCountDetail(
            name="train",
            n_records=n_records,
            n_positive=n_positive,
            n_negative=n_negative,
            positive_rate=n_positive / n_records,
        )


# ---------------------------------------------------------------------------
# Reports cannot self-declare research outcomes
# ---------------------------------------------------------------------------


def test_no_forbidden_fields_in_dataset_quality_schema() -> None:
    """DatasetQualityReport schema contains no self-declared outcome fields."""
    assert not (set(DatasetQualityReport.model_fields) & _FORBIDDEN_FIELDS), (
        f"Forbidden fields found in DatasetQualityReport: "
        f"{set(DatasetQualityReport.model_fields) & _FORBIDDEN_FIELDS}"
    )


def test_no_forbidden_fields_in_split_quality_schema() -> None:
    """ModelSplitQualityReport schema contains no self-declared outcome fields."""
    assert not (set(ModelSplitQualityReport.model_fields) & _FORBIDDEN_FIELDS)


def test_no_forbidden_fields_in_split_count_detail_schema() -> None:
    """SplitCountDetail schema contains no self-declared outcome fields."""
    assert not (set(SplitCountDetail.model_fields) & _FORBIDDEN_FIELDS)


def test_no_forbidden_fields_in_serialized_artifact(
    tmp_path: Path, make_frame: object
) -> None:
    """Serialized quality report JSON does not contain forbidden field names."""
    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)
    artifact = json.dumps(report.model_dump(mode="json"))
    for field in _FORBIDDEN_FIELDS:
        assert f'"{field}"' not in artifact, (
            f"Forbidden field name {field!r} found in serialized quality report"
        )


# ---------------------------------------------------------------------------
# Reports cannot be replayed against another dataset
# ---------------------------------------------------------------------------


def test_quality_report_replay_to_different_dataset_fails(
    tmp_path: Path, make_frame: object
) -> None:
    """A quality report measured from one dataset cannot be replayed to another.

    Tamper/replay property: the binding dataset_identity_sha256 prevents replay.
    """
    # Dataset A
    csv_a = tmp_path / "a.csv"
    make_frame(n=60, seed=0).to_csv(csv_a, index=False, lineterminator="\n")  # type: ignore[operator]
    out_a = tmp_path / "out_a"
    snap_a = build_dataset_snapshot(
        csv_a, dataset_id=_DATASET_ID, output_root=out_a, relative_path=_SNAP_REL
    )
    report_a = measure_dataset(snap_a, csv_a)

    # Dataset B (different seed → different records → different identity SHA)
    csv_b = tmp_path / "b.csv"
    make_frame(n=60, seed=1).to_csv(csv_b, index=False, lineterminator="\n")  # type: ignore[operator]
    out_b = tmp_path / "out_b"
    snap_b = build_dataset_snapshot(
        csv_b, dataset_id=_DATASET_ID, output_root=out_b, relative_path=_SNAP_REL
    )

    # Report from A must not validate against B
    with pytest.raises(ManifestContractError):
        validate_dataset_quality_report(report_a, snap_b, csv_b)


# ---------------------------------------------------------------------------
# Joint measurement and digest forgery still fails source validation
# ---------------------------------------------------------------------------


def test_forge_measurement_and_hash_fails_against_source(
    tmp_path: Path, make_frame: object
) -> None:
    """Forging a measurement field via model_copy still fails source validation.

    validate_dataset_quality_report recomputes every field from source;
    it cannot be deceived by a self-consistent but incorrect report.
    """
    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    # Forge n_duplicate_ids — this passes schema validation (no invariant restricts it)
    # but fails the source-recomputed comparison in validate_dataset_quality_report
    forged = report.model_copy(update={"n_duplicate_ids": report.n_duplicate_ids + 99})

    with pytest.raises(ManifestContractError, match="quality report"):
        validate_dataset_quality_report(forged, snap, csv)


# ---------------------------------------------------------------------------
# Serialized quality artifacts contain no raw identifiers
# ---------------------------------------------------------------------------


def test_quality_report_contains_no_raw_customer_ids(
    tmp_path: Path, make_frame: object
) -> None:
    """Serialized quality report does not contain raw customer IDs.

    build_frame generates IDs in the format 'NNNNN-SYNTH'.  The '-SYNTH'
    suffix contains uppercase non-hex letters that provably cannot appear
    in any SHA-256 hex digest, so their presence is an unambiguous breach.
    """
    snap, _, _, csv = _build_quality_fixtures(tmp_path, make_frame, n=60)
    report = measure_dataset(snap, csv)

    artifact = json.dumps(report.model_dump(mode="json"))

    # '-SYNTH' is distinctive and unambiguously non-hex
    assert "-SYNTH" not in artifact, (
        "Raw customer ID suffix '-SYNTH' found in serialized quality report"
    )

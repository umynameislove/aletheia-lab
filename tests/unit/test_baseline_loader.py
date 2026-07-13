"""Tests for the seeded, order-independent loader and split."""

from __future__ import annotations

import pytest

from aletheia_lab.baseline.loader import (
    DatasetSchemaError,
    assert_no_overlap,
    load_processed,
    split_dataset,
)
from aletheia_lab.baseline.schema import (
    FEATURE_COLUMNS,
    ID_COLUMN,
    TARGET_COLUMN,
)

RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}


def _split(frame, seed=42, stratified=True):
    return split_dataset(
        frame,
        dataset_id="telco_customer_churn",
        dataset_sha256="deadbeef",
        seed=seed,
        ratios=RATIOS,
        stratified=stratified,
    )


def test_same_seed_same_split(make_frame):
    frame = make_frame()
    a = _split(frame, seed=42)
    b = _split(frame, seed=42)
    for name in ("train", "validation", "test"):
        assert a.manifest.splits[name].record_id_sha256 == b.manifest.splits[name].record_id_sha256


def test_different_seed_changes_split_but_stays_valid(make_frame):
    frame = make_frame()
    a = _split(frame, seed=1)
    b = _split(frame, seed=2)
    assert (
        a.manifest.splits["train"].record_id_sha256 != b.manifest.splits["train"].record_id_sha256
    )
    assert_no_overlap(b)  # still a valid partition
    assert sum(b.manifest.splits[s].n for s in ("train", "validation", "test")) == len(frame)


def test_no_overlap_and_totals(make_frame):
    splits = _split(make_frame())
    assert_no_overlap(splits)
    ids = [set(splits.as_dict()[s].ids) for s in ("train", "validation", "test")]
    assert ids[0].isdisjoint(ids[1]) and ids[0].isdisjoint(ids[2]) and ids[1].isdisjoint(ids[2])
    assert sum(len(s) for s in ids) == len(make_frame())


def test_target_and_id_absent_from_features(make_frame):
    splits = _split(make_frame())
    for data in splits.as_dict().values():
        assert TARGET_COLUMN not in data.features.columns
        assert ID_COLUMN not in data.features.columns
        assert list(data.features.columns) == list(FEATURE_COLUMNS)


def test_split_is_order_independent(make_frame):
    frame = make_frame()
    shuffled = frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
    a = _split(frame, seed=42)
    b = _split(shuffled, seed=42)
    for name in ("train", "validation", "test"):
        assert a.manifest.splits[name].record_id_sha256 == b.manifest.splits[name].record_id_sha256


def test_stratification_preserves_class_balance(make_frame):
    frame = make_frame()
    splits = _split(frame, stratified=True)
    overall = (frame[TARGET_COLUMN] == "Yes").mean()
    for name in ("train", "validation", "test"):
        assert splits.manifest.splits[name].positive_rate == pytest.approx(overall, abs=0.08)


def test_load_processed_missing_file(tmp_path):
    with pytest.raises(DatasetSchemaError):
        load_processed(tmp_path / "nope.csv")


def test_load_processed_missing_column(tmp_path, make_frame):
    frame = make_frame().drop(columns=[TARGET_COLUMN])
    path = tmp_path / "bad.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(DatasetSchemaError):
        load_processed(path)


def test_load_processed_duplicate_id(tmp_path, make_frame):
    frame = make_frame(n=10)
    frame.loc[1, ID_COLUMN] = frame.loc[0, ID_COLUMN]
    path = tmp_path / "dup.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(DatasetSchemaError):
        load_processed(path)


def test_load_processed_bad_target_label(tmp_path, make_frame):
    frame = make_frame(n=10)
    frame.loc[0, TARGET_COLUMN] = "Maybe"
    path = tmp_path / "badlabel.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(DatasetSchemaError):
        load_processed(path)


def test_ratios_must_sum_to_one(make_frame):
    with pytest.raises(ValueError):
        split_dataset(
            make_frame(),
            dataset_id="x",
            dataset_sha256="y",
            seed=1,
            ratios={"train": 0.5, "validation": 0.2, "test": 0.2},
        )

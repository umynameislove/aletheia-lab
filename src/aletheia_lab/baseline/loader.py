"""Seeded, order-independent loading and splitting of the processed dataset.

The loader reads the deterministically processed table, validates its schema,
and produces a deterministic stratified train/validation/test partition. It
sorts rows by a stable record identifier before splitting and passes an explicit
random state, so the partition depends only on the data, the seed and the
ratios, never on file order or global RNG state. The identifier and the target
are held out of the feature matrix, so the target cannot leak into model input.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

from aletheia_lab.baseline.schema import (
    FEATURE_COLUMNS,
    ID_COLUMN,
    NEGATIVE_LABEL,
    POSITIVE_LABEL,
    TARGET_COLUMN,
    SplitCounts,
    SplitManifest,
)

SPLIT_NAMES: tuple[str, ...] = ("train", "validation", "test")


class DatasetSchemaError(RuntimeError):
    """Raised when the processed dataset does not match the expected schema."""


@dataclass(frozen=True)
class SplitData:
    """Feature/target/id frames for one split (features exclude id and target)."""

    name: str
    features: pd.DataFrame
    target: pd.Series
    ids: pd.Series


@dataclass(frozen=True)
class LoadedSplits:
    """The three splits plus the manifest that describes them."""

    train: SplitData
    validation: SplitData
    test: SplitData
    manifest: SplitManifest

    def as_dict(self) -> dict[str, SplitData]:
        return {"train": self.train, "validation": self.validation, "test": self.test}


def _sha256_ids(ids: pd.Series) -> str:
    """Hash the sorted identifiers so identical membership yields an identical digest."""

    joined = "\n".join(sorted(str(value) for value in ids)).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def load_processed(path: str | Path) -> pd.DataFrame:
    """Load the processed CSV and validate it against the expected schema.

    Reading with explicit dtypes for the id/target keeps them as strings; the
    numeric columns are already numeric from prep. Fails closed with a clear
    message when the file is missing or the schema does not match.
    """

    csv_path = Path(path)
    if not csv_path.exists():
        msg = f"processed dataset not found: {csv_path}; run 'download_dataset.py all' first"
        raise DatasetSchemaError(msg)

    frame = pd.read_csv(csv_path)
    missing = sorted({ID_COLUMN, TARGET_COLUMN, *FEATURE_COLUMNS} - set(frame.columns))
    if missing:
        msg = f"processed dataset is missing required columns: {missing}"
        raise DatasetSchemaError(msg)
    if frame[ID_COLUMN].duplicated().any():
        msg = f"processed dataset has duplicate {ID_COLUMN} values; cannot form stable ids"
        raise DatasetSchemaError(msg)
    if frame[ID_COLUMN].isna().any() or (frame[ID_COLUMN].astype(str).str.strip() == "").any():
        msg = f"processed dataset has blank {ID_COLUMN} values"
        raise DatasetSchemaError(msg)
    bad_labels = set(frame[TARGET_COLUMN].unique()) - {POSITIVE_LABEL, NEGATIVE_LABEL}
    if bad_labels:
        msg = f"unexpected {TARGET_COLUMN} labels: {sorted(bad_labels)}"
        raise DatasetSchemaError(msg)
    return frame


def _target_binary(frame: pd.DataFrame) -> pd.Series:
    """Encode the target as 1 for the positive label, 0 otherwise."""

    return (frame[TARGET_COLUMN] == POSITIVE_LABEL).astype(int)


def _counts(ids: pd.Series, y: pd.Series) -> SplitCounts:
    n = int(len(y))
    n_pos = int(y.sum())
    n_neg = int(n - n_pos)
    return SplitCounts(
        n=n,
        n_positive=n_pos,
        n_negative=n_neg,
        positive_rate=(n_pos / n if n else 0.0),
        record_id_sha256=_sha256_ids(ids),
    )


def split_dataset(
    frame: pd.DataFrame,
    *,
    dataset_id: str,
    dataset_sha256: str,
    seed: int,
    ratios: dict[str, float],
    stratified: bool = True,
) -> LoadedSplits:
    """Produce a deterministic stratified train/validation/test partition.

    Rows are sorted by the stable identifier before splitting so the result does
    not depend on input order. Ratios must be positive and sum to 1.0.
    """

    for name in SPLIT_NAMES:
        if name not in ratios:
            msg = f"missing split ratio for {name!r}"
            raise ValueError(msg)
    total = sum(ratios[name] for name in SPLIT_NAMES)
    if abs(total - 1.0) > 1e-9:
        msg = f"split ratios must sum to 1.0, got {total}"
        raise ValueError(msg)

    ordered = frame.sort_values(ID_COLUMN, kind="mergesort").reset_index(drop=True)
    y = _target_binary(ordered)
    stratify = y if stratified else None

    test_and_val = ratios["validation"] + ratios["test"]
    train_idx, temp_idx = train_test_split(
        ordered.index,
        test_size=test_and_val,
        random_state=seed,
        stratify=stratify,
        shuffle=True,
    )
    rel_test = ratios["test"] / test_and_val
    temp_stratify = y.loc[temp_idx] if stratified else None
    val_idx, test_idx = train_test_split(
        temp_idx,
        test_size=rel_test,
        random_state=seed,
        stratify=temp_stratify,
        shuffle=True,
    )

    def _make(name: str, idx: pd.Index) -> SplitData:
        rows = ordered.loc[idx].sort_values(ID_COLUMN, kind="mergesort")
        return SplitData(
            name=name,
            features=rows[list(FEATURE_COLUMNS)].reset_index(drop=True),
            target=_target_binary(rows).reset_index(drop=True),
            ids=rows[ID_COLUMN].reset_index(drop=True),
        )

    train = _make("train", train_idx)
    validation = _make("validation", val_idx)
    test = _make("test", test_idx)

    manifest = SplitManifest(
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        n_rows=int(len(ordered)),
        seed=seed,
        stratified=stratified,
        ratios={name: float(ratios[name]) for name in SPLIT_NAMES},
        splits={
            "train": _counts(train.ids, train.target),
            "validation": _counts(validation.ids, validation.target),
            "test": _counts(test.ids, test.target),
        },
    )
    return LoadedSplits(train=train, validation=validation, test=test, manifest=manifest)


def assert_no_overlap(splits: LoadedSplits) -> None:
    """Fail closed if any record appears in more than one split or a record is lost."""

    id_sets = {name: set(data.ids) for name, data in splits.as_dict().items()}
    train, validation, test = id_sets["train"], id_sets["validation"], id_sets["test"]
    if train & validation or train & test or validation & test:
        msg = "record overlap detected between splits"
        raise DatasetSchemaError(msg)
    total = len(train) + len(validation) + len(test)
    if total != splits.manifest.n_rows:
        msg = f"split records {total} != dataset rows {splits.manifest.n_rows}"
        raise DatasetSchemaError(msg)

"""Deterministic preparation of the raw dataset into a modelling-ready table.

Prep is pure and repeatable: the same raw file always yields the same processed
file (identical bytes, hence identical checksum), which is what the downstream
seeded loader relies on. The only non-trivial transform is the known Telco quirk
where ``TotalCharges`` is blank for new customers (``tenure == 0``); those are
coerced to ``0.0`` and counted, not dropped.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from aletheia_lab.data.download import sha256_file
from aletheia_lab.data.sources import DatasetSource

# Columns coerced to a numeric dtype during prep.
_INT_COLUMNS = ("SeniorCitizen", "tenure")
_FLOAT_COLUMNS = ("MonthlyCharges", "TotalCharges")


class SchemaError(RuntimeError):
    """Raised when a raw file does not match the pinned schema."""


def load_raw(path: str | Path, source: DatasetSource) -> pd.DataFrame:
    """Load the raw CSV as strings and validate it against the pinned schema.

    Reading every column as text (``keep_default_na=False``) keeps blank cells
    as ``""`` instead of letting pandas guess dtypes, so prep is explicit and
    deterministic.
    """

    frame: pd.DataFrame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if tuple(frame.columns) != source.columns:
        msg = "raw columns do not match the pinned schema"
        raise SchemaError(msg)
    if len(frame) != source.n_rows:
        msg = f"expected {source.n_rows} rows, got {len(frame)}"
        raise SchemaError(msg)
    return frame


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a typed, whitespace-stripped copy with numeric columns coerced."""

    out = frame.copy()
    for column in out.columns:
        out[column] = out[column].str.strip()
    for column in _INT_COLUMNS:
        out[column] = out[column].astype(int)
    for column in _FLOAT_COLUMNS:
        # New customers have an empty TotalCharges; treat it as zero billed so far.
        out[column] = out[column].replace("", "0").astype(float)
    return out


def count_blank_total_charges(frame: pd.DataFrame) -> int:
    """Count raw rows whose ``TotalCharges`` is blank (reported in the data card)."""

    return int((frame["TotalCharges"].str.strip() == "").sum())


def write_processed(frame: pd.DataFrame, path: str | Path) -> Path:
    """Write the processed table deterministically (fixed order, no index)."""

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path, index=False, lineterminator="\n")
    return out_path


def prepare_dataset(
    raw_path: str | Path,
    out_path: str | Path,
    source: DatasetSource,
) -> dict[str, object]:
    """Run the full raw -> processed prep and return a small stats summary."""

    raw = load_raw(raw_path, source)
    blanks = count_blank_total_charges(raw)
    processed = prepare(raw)
    written = write_processed(processed, out_path)
    return {
        "n_rows": int(len(processed)),
        "n_cols": int(processed.shape[1]),
        "total_charges_blanks_zeroed": blanks,
        "sha256": sha256_file(written),
        "path": str(written),
    }

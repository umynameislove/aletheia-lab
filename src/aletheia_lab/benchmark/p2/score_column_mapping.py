"""Decode binary class scores without assuming a fixed positive-class column.

This is a pure boundary primitive. It does not select rows for intervention,
score a metric, or decide whether a mapping is authoritative.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from numbers import Integral, Real
from typing import NoReturn


class ScoreColumnMappingError(ValueError):
    """The score source or its row/class binding is malformed."""


def _fail(message: str) -> NoReturn:
    raise ScoreColumnMappingError(message)


def _checked_ids(values: Sequence[str]) -> tuple[str, ...]:
    try:
        ids = tuple(values)
    except TypeError as exc:
        raise ScoreColumnMappingError("record IDs must be a sequence") from exc
    if not ids:
        _fail("score rows need at least one record ID")
    if any(
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        for value in ids
    ):
        _fail("record IDs must be nonempty canonical strings")
    if len(set(ids)) != len(ids):
        _fail("record IDs must be unique")
    return ids


def _checked_classes(column_classes: Sequence[int]) -> tuple[int, ...]:
    try:
        classes = tuple(column_classes)
    except TypeError as exc:
        raise ScoreColumnMappingError("column classes must be a sequence") from exc
    if len(classes) != 2 or any(
        isinstance(value, bool) or not isinstance(value, Integral) for value in classes
    ):
        _fail("column classes must contain binary integer labels")
    if set(classes) != {0, 1}:
        _fail("column classes must be a permutation of (0, 1)")
    return classes


def _checked_probability_pair(row: Sequence[float], index: int) -> tuple[float, float]:
    if len(row) != 2:
        _fail(f"score row {index} must have exactly two columns")
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in row):
        _fail(f"score row {index} must contain real numeric values")
    left, right = float(row[0]), float(row[1])
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (left, right)):
        _fail(f"score row {index} must contain finite probabilities")
    if not math.isclose(left + right, 1.0, rel_tol=0.0, abs_tol=1e-12):
        _fail(f"score row {index} probabilities must sum to one")
    return left, right


def decode_positive_class_scores(
    *,
    record_ids: Sequence[str],
    expected_record_ids: Sequence[str],
    score_rows: Sequence[Sequence[float]],
    column_classes: Sequence[int],
) -> tuple[float, ...]:
    """Return label-1 scores with an exact row and class-column binding.

    ``column_classes`` describes how the caller interprets each score pair.
    Whether that interpretation matches the model's recorded ``classes_`` is
    deliberately checked by a separate source/witness layer.
    """

    ids = _checked_ids(record_ids)
    if ids != _checked_ids(expected_record_ids):
        _fail("score rows and expected records differ in identity or order")
    classes = _checked_classes(column_classes)
    try:
        rows = tuple(tuple(row) for row in score_rows)
    except TypeError as exc:
        raise ScoreColumnMappingError("score rows must be two-column sequences") from exc
    if len(rows) != len(ids):
        _fail("score rows must align one-to-one with record IDs")

    positive_column = classes.index(1)
    positive_scores: list[float] = []
    for index, row in enumerate(rows):
        left, right = _checked_probability_pair(row, index)
        positive_scores.append((left, right)[positive_column])
    return tuple(positive_scores)

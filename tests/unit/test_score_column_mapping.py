"""Synthetic oracle for the evaluator-only positive-class mapping construct."""

from __future__ import annotations

import math

import numpy as np
import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_column_mapping import (
    ScoreColumnMappingError,
    decode_positive_class_scores,
)

_IDS = ("r0", "r1", "r2", "r3", "r4", "r5")
_SCORES = (
    (0.9, 0.1),
    (0.1, 0.9),
    (0.8, 0.2),
    (0.2, 0.8),
    (0.7, 0.3),
    (0.6, 0.4),
)
_TARGETS = (0, 1, 0, 1, 0, 0)


def _decode(
    ids: tuple[str, ...] = _IDS,
    scores: tuple[tuple[float, float], ...] = _SCORES,
    classes: tuple[int, int] = (0, 1),
) -> tuple[float, ...]:
    return decode_positive_class_scores(
        record_ids=ids,
        expected_record_ids=ids,
        score_rows=scores,
        column_classes=classes,
    )


def test_synthetic_oracle_fault_and_correction_use_identical_source_scores() -> None:
    source_before = _SCORES
    healthy = _decode()
    assert healthy == (0.1, 0.9, 0.2, 0.8, 0.3, 0.4)

    # Only the first synthetic shard is decoded with the wrong assumed order.
    faulty = _decode(_IDS[:2], _SCORES[:2], (1, 0)) + _decode(_IDS[2:], _SCORES[2:])
    corrected = _decode(_IDS[:2], _SCORES[:2], (0, 1)) + _decode(_IDS[2:], _SCORES[2:])
    zero_dose = _decode()

    assert faulty == (0.9, 0.1, 0.2, 0.8, 0.3, 0.4)
    assert corrected == healthy == zero_dose
    assert _SCORES is source_before

    healthy_loss = reference_prior_standardized_log_loss(_TARGETS, healthy)
    faulty_loss = reference_prior_standardized_log_loss(_TARGETS, faulty)
    corrected_loss = reference_prior_standardized_log_loss(_TARGETS, corrected)
    zero_dose_loss = reference_prior_standardized_log_loss(_TARGETS, zero_dose)
    assert healthy_loss == pytest.approx(0.23162659607760389)
    assert faulty_loss == pytest.approx(1.0555858125786861)
    assert faulty_loss > healthy_loss
    assert corrected_loss == healthy_loss == zero_dose_loss


def test_actual_reversed_class_order_is_not_assumed_to_have_positive_column_one() -> None:
    reversed_scores = tuple((right, left) for left, right in _SCORES)
    assert _decode(_IDS, reversed_scores, (1, 0)) == _decode()


def test_numpy_integer_model_classes_and_scores_are_accepted() -> None:
    assert decode_positive_class_scores(
        record_ids=("r0",),
        expected_record_ids=("r0",),
        score_rows=np.array([[0.8, 0.2]]),
        column_classes=np.array([0, 1], dtype=np.int64),
    ) == (0.2,)


def test_unused_fault_metadata_and_score_ties_do_not_fabricate_effect() -> None:
    tied_scores = ((0.5, 0.5), (0.8, 0.2))
    actual = _decode(("tie", "changed"), tied_scores, (0, 1))
    assumed = _decode(("tie", "changed"), tied_scores, (1, 0))
    assert actual == (0.5, 0.2)
    assert assumed == (0.5, 0.8)
    assert sum(left != right for left, right in zip(actual, assumed, strict=True)) == 1
    # Both rows have the faulty assumption, but only one score changes.
    assert len(actual) == 2
    assumed_wrong_order = (1, 0)
    assert assumed_wrong_order != (0, 1)
    actual_classes = (0, 1)
    healthy = _decode()
    assert _decode(classes=actual_classes) == healthy


@pytest.mark.parametrize(
    ("record_ids", "expected_ids"),
    [
        (("r0", "r1"), ("r1", "r0")),
        (("r0", "r0"), ("r0", "r0")),
        (("r0", " r1"), ("r0", " r1")),
        (("r0", "r\x001"), ("r0", "r\x001")),
        (("r0", "e\u0301"), ("r0", "e\u0301")),
        ((), ()),
    ],
)
def test_misaligned_or_noncanonical_record_ids_fail_closed(
    record_ids: tuple[str, ...], expected_ids: tuple[str, ...]
) -> None:
    with pytest.raises(ScoreColumnMappingError):
        decode_positive_class_scores(
            record_ids=record_ids,
            expected_record_ids=expected_ids,
            score_rows=((0.8, 0.2),) * len(record_ids),
            column_classes=(0, 1),
        )


@pytest.mark.parametrize("classes", [(0, 0), (0, 2), (True, 0), (), (0, 1, 2)])
def test_malformed_class_metadata_fails_closed(classes: tuple[int, ...]) -> None:
    with pytest.raises(ScoreColumnMappingError):
        decode_positive_class_scores(
            record_ids=("r0",),
            expected_record_ids=("r0",),
            score_rows=((0.8, 0.2),),
            column_classes=classes,
        )


@pytest.mark.parametrize(
    "scores",
    [
        (),
        ((0.2,),),
        ((0.2, 0.8, 0.0),),
        ((0.2, 0.7),),
        ((-0.1, 1.1),),
        ((math.nan, 0.2),),
        ((math.inf, 0.2),),
        ((True, 0.0),),
        (("0.2", 0.8),),
    ],
)
def test_malformed_score_matrix_fails_closed(scores: tuple[tuple[object, ...], ...]) -> None:
    with pytest.raises(ScoreColumnMappingError):
        decode_positive_class_scores(
            record_ids=("r0",),
            expected_record_ids=("r0",),
            score_rows=scores,  # type: ignore[arg-type]
            column_classes=(0, 1),
        )

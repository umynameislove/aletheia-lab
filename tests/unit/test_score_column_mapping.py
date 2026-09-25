"""Synthetic oracle for the evaluator-only positive-class mapping construct."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
    reference_prior_standardized_losses,
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
    scores: Sequence[Sequence[float]] = _SCORES,
    classes: tuple[int, int] = (0, 1),
) -> tuple[float, ...]:
    return decode_positive_class_scores(
        record_ids=ids,
        expected_record_ids=ids,
        score_rows=scores,
        column_classes=classes,
    )


def test_synthetic_oracle_fault_and_correction_use_identical_source_scores() -> None:
    source_scores = [list(row) for row in _SCORES]
    source_before = [row.copy() for row in source_scores]
    healthy = _decode(scores=source_scores)
    assert healthy == (0.1, 0.9, 0.2, 0.8, 0.3, 0.4)

    # Only the first synthetic shard is decoded with the wrong assumed order.
    faulty = _decode(_IDS[:2], source_scores[:2], (1, 0)) + _decode(_IDS[2:], source_scores[2:])
    corrected = _decode(_IDS[:2], source_scores[:2], (0, 1)) + _decode(_IDS[2:], source_scores[2:])
    zero_dose = _decode(scores=source_scores)

    assert faulty == (0.9, 0.1, 0.2, 0.8, 0.3, 0.4)
    assert corrected == healthy == zero_dose
    assert source_scores == source_before

    healthy_loss = reference_prior_standardized_log_loss(
        true_labels=_TARGETS, probabilities=healthy
    )
    faulty_loss = reference_prior_standardized_log_loss(true_labels=_TARGETS, probabilities=faulty)
    corrected_loss = reference_prior_standardized_log_loss(
        true_labels=_TARGETS, probabilities=corrected
    )
    zero_dose_loss = reference_prior_standardized_log_loss(
        true_labels=_TARGETS, probabilities=zero_dose
    )
    assert healthy_loss == pytest.approx(0.23162659607760389)
    assert faulty_loss == pytest.approx(1.0555858125786861)
    assert faulty_loss > healthy_loss
    assert corrected_loss == healthy_loss == zero_dose_loss


def test_target_flip_and_row_target_misalignment_match_faulty_mapping_loss() -> None:
    # Equal metrics do not identify the corrupted boundary. Distinguishing
    # these rivals needs pre-intervention class and (row ID, target) witnesses.
    healthy = _decode()
    faulty_mapping = _decode(_IDS[:2], _SCORES[:2], (1, 0)) + _decode(_IDS[2:], _SCORES[2:])
    flipped_targets = (1, 0, *_TARGETS[2:])
    reordered_target_ids = (_IDS[1], _IDS[0], *_IDS[2:])
    target_by_id = dict(zip(reordered_target_ids, _TARGETS, strict=True))
    misaligned_targets = tuple(target_by_id[record_id] for record_id in _IDS)

    assert misaligned_targets == flipped_targets
    assert flipped_targets.count(0) == _TARGETS.count(0)
    assert flipped_targets.count(1) == _TARGETS.count(1)
    mapping_contributions = reference_prior_standardized_losses(
        true_labels=_TARGETS, probabilities=faulty_mapping
    )
    flipped_contributions = reference_prior_standardized_losses(
        true_labels=flipped_targets, probabilities=healthy
    )
    raw_mapping_losses = tuple(
        -math.log(probability) if label else -math.log1p(-probability)
        for label, probability in zip(_TARGETS, faulty_mapping, strict=True)
    )
    raw_flipped_losses = tuple(
        -math.log(probability) if label else -math.log1p(-probability)
        for label, probability in zip(flipped_targets, healthy, strict=True)
    )
    assert raw_mapping_losses == pytest.approx(raw_flipped_losses)
    assert tuple(
        abs(label - probability)
        for label, probability in zip(_TARGETS, faulty_mapping, strict=True)
    ) == pytest.approx(
        tuple(
            abs(label - probability)
            for label, probability in zip(flipped_targets, healthy, strict=True)
        )
    )
    # The runtime's class-prior weights expose a row-level difference even
    # when this class-count-preserving flip has the same aggregate score.
    assert mapping_contributions[0] != pytest.approx(flipped_contributions[0])
    assert mapping_contributions[0] == pytest.approx(flipped_contributions[1])
    assert mapping_contributions[1] == pytest.approx(flipped_contributions[0])
    assert reference_prior_standardized_log_loss(
        true_labels=_TARGETS, probabilities=faulty_mapping
    ) == pytest.approx(
        reference_prior_standardized_log_loss(true_labels=misaligned_targets, probabilities=healthy)
    )


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

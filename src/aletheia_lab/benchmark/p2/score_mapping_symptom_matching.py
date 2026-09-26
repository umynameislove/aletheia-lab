"""Development-only, class-count-preserving target-binding hard negative.

The search deliberately uses development labels and scores to construct a hard
negative. It is not an estimate of naturally occurring target errors, and its
result must never be used to select a confirmatory case after opening holdout.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_column_mapping import validate_score_record_ids

# A visible metric at six decimals is substantially more precise than the
# usual 2-3 decimal summary. This resolution and half-unit tolerance are fixed
# before running the development search, not widened to rescue a failed cell.
VISIBLE_METRIC_DECIMALS = 6
MAX_ABSOLUTE_LOSS_GAP = 0.5 * 10**-VISIBLE_METRIC_DECIMALS


class SymptomMatchingError(ValueError):
    """A malformed source or rival request cannot be matched safely."""


@dataclass(frozen=True, slots=True)
class TargetSwapMatch:
    """A constructed rival, with unchanged score rows and class counts."""

    scoring_target_rows: tuple[tuple[str, int], ...]
    swapped_pairs: tuple[tuple[str, str], ...]
    observed_log_loss: float
    absolute_loss_gap: float
    resolution_matched: bool


def _checked_inputs(
    record_ids: Sequence[str], targets: Sequence[int], probabilities: Sequence[float]
) -> tuple[tuple[str, ...], tuple[int, ...], tuple[float, ...]]:
    try:
        ids = validate_score_record_ids(record_ids)
        labels = tuple(targets)
        scores = tuple(float(value) for value in probabilities)
    except (TypeError, ValueError) as exc:
        raise SymptomMatchingError("source rows, labels or scores are invalid") from exc
    if (
        len(labels) != len(ids)
        or len(scores) != len(ids)
        or any(type(value) is not int or value not in (0, 1) for value in labels)
        or set(labels) != {0, 1}
        or any(not math.isfinite(value) or not 0.0 < value < 1.0 for value in scores)
    ):
        raise SymptomMatchingError("source rows, labels and probabilities must align")
    return ids, labels, scores


def _loss_at_visible_resolution(value: float) -> float:
    rounded = round(value, VISIBLE_METRIC_DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def _best_available_pair(
    *,
    zero_terms: list[tuple[float, str, int]],
    one_terms: list[tuple[float, str, int]],
    residual: float,
) -> tuple[int, int] | None:
    """Find the closest admissible opposite-label swap without reusing rows."""

    best: tuple[float, str, str, int, int] | None = None
    for zero_position, (zero_term, zero_id, _) in enumerate(zero_terms):
        insertion = bisect_left(one_terms, (residual - zero_term, "", -1))
        for one_position in (insertion - 1, insertion):
            if not 0 <= one_position < len(one_terms):
                continue
            one_term, one_id, _ = one_terms[one_position]
            change = zero_term + one_term
            if not 0.0 < change <= residual + MAX_ABSOLUTE_LOSS_GAP:
                continue
            candidate = (
                abs(residual - change),
                zero_id,
                one_id,
                zero_position,
                one_position,
            )
            if best is None or candidate < best:
                best = candidate
    return None if best is None else (best[3], best[4])


def match_target_swaps(
    *,
    record_ids: Sequence[str],
    targets: Sequence[int],
    probabilities: Sequence[float],
    mapping_log_loss: float,
    max_changed_targets: int,
) -> TargetSwapMatch:
    """Greedily match a mapping symptom using opposite-label target swaps.

    Each pair exchanges one 0 and one 1 under fixed row IDs. Consequently the
    scorer's class-prior weights, score rows and source model stay unchanged.
    The footprint cannot exceed the mapping fault's affected-row count passed
    by the caller. The search is deterministic but may honestly return no
    match; it does not adjust the metric, tolerance, model or mapping dose.
    """

    ids, labels, scores = _checked_inputs(record_ids, targets, probabilities)
    if not math.isfinite(mapping_log_loss) or type(max_changed_targets) is not int:
        raise SymptomMatchingError("mapping metric and rival footprint must be finite integers")
    if not 2 <= max_changed_targets <= len(ids):
        raise SymptomMatchingError("rival footprint is outside the source census")
    healthy_loss = reference_prior_standardized_log_loss(true_labels=labels, probabilities=scores)
    if mapping_log_loss <= healthy_loss:
        raise SymptomMatchingError("positive-dose matching requires a positive metric shift")

    n_zero = labels.count(0)
    n_one = labels.count(1)
    # For a 0->1 row i and 1->0 row j, the exact change under fixed class
    # counts is a_i + b_j. No scorer implementation is copied for the final
    # verdict: the runtime scorer recomputes the complete rival vector below.
    zero_terms = tuple(
        (
            -math.log(scores[index]) / (2 * n_one) + math.log1p(-scores[index]) / (2 * n_zero),
            ids[index],
            index,
        )
        for index, label in enumerate(labels)
        if label == 0
    )
    one_terms = sorted(
        (
            -math.log1p(-scores[index]) / (2 * n_zero) + math.log(scores[index]) / (2 * n_one),
            ids[index],
            index,
        )
        for index, label in enumerate(labels)
        if label == 1
    )
    available_zero = list(zero_terms)
    available_one = list(one_terms)
    changed = list(labels)
    selected: list[tuple[str, str]] = []
    observed_loss = healthy_loss
    max_pairs = min(n_zero, n_one, max_changed_targets // 2)

    for _ in range(max_pairs + 1):
        gap = abs(observed_loss - mapping_log_loss)
        matched = gap <= MAX_ABSOLUTE_LOSS_GAP and _loss_at_visible_resolution(
            observed_loss
        ) == _loss_at_visible_resolution(mapping_log_loss)
        if matched or len(selected) == max_pairs or not available_one or not available_zero:
            break
        residual = mapping_log_loss - observed_loss
        positions = _best_available_pair(
            zero_terms=available_zero, one_terms=available_one, residual=residual
        )
        if positions is None:
            break
        zero_position, one_position = positions
        _, zero_id, zero_index = available_zero.pop(zero_position)
        _, one_id, one_index = available_one.pop(one_position)
        changed[zero_index] = 1
        changed[one_index] = 0
        selected.append((zero_id, one_id))
        observed_loss = reference_prior_standardized_log_loss(
            true_labels=changed, probabilities=scores
        )

    if changed.count(0) != n_zero or changed.count(1) != n_one:
        raise SymptomMatchingError("target rival changed the original class counts")
    gap = abs(observed_loss - mapping_log_loss)
    return TargetSwapMatch(
        scoring_target_rows=tuple(zip(ids, changed, strict=True)),
        swapped_pairs=tuple(selected),
        observed_log_loss=observed_loss,
        absolute_loss_gap=gap,
        resolution_matched=(
            bool(selected)
            and gap <= MAX_ABSOLUTE_LOSS_GAP
            and _loss_at_visible_resolution(observed_loss)
            == _loss_at_visible_resolution(mapping_log_loss)
        ),
    )


def verify_target_swap_match(
    *,
    record_ids: Sequence[str],
    targets: Sequence[int],
    probabilities: Sequence[float],
    mapping_log_loss: float,
    max_changed_targets: int,
    result: TargetSwapMatch,
) -> None:
    """Rebuild the rival from its pair ledger and score it independently."""

    ids, labels, scores = _checked_inputs(record_ids, targets, probabilities)
    if not isinstance(result, TargetSwapMatch):
        raise SymptomMatchingError("rival result must be typed")
    if type(max_changed_targets) is not int or not 2 <= max_changed_targets <= len(ids):
        raise SymptomMatchingError("rival footprint is outside the source census")
    lookup = {record_id: index for index, record_id in enumerate(ids)}
    changed = list(labels)
    used: set[str] = set()
    for zero_id, one_id in result.swapped_pairs:
        if (
            zero_id not in lookup
            or one_id not in lookup
            or zero_id in used
            or one_id in used
            or labels[lookup[zero_id]] != 0
            or labels[lookup[one_id]] != 1
        ):
            raise SymptomMatchingError("rival ledger repeats or mislabels a target row")
        changed[lookup[zero_id]] = 1
        changed[lookup[one_id]] = 0
        used.update((zero_id, one_id))
    rows = tuple(zip(ids, changed, strict=True))
    if rows != result.scoring_target_rows or len(used) > max_changed_targets:
        raise SymptomMatchingError("rival target rows disagree with its pair ledger")
    observed = reference_prior_standardized_log_loss(true_labels=changed, probabilities=scores)
    gap = abs(observed - mapping_log_loss)
    matched = (
        bool(used)
        and gap <= MAX_ABSOLUTE_LOSS_GAP
        and _loss_at_visible_resolution(observed) == _loss_at_visible_resolution(mapping_log_loss)
    )
    if (
        observed != result.observed_log_loss
        or gap != result.absolute_loss_gap
        or matched != result.resolution_matched
        or changed.count(0) != labels.count(0)
        or changed.count(1) != labels.count(1)
    ):
        raise SymptomMatchingError("rival metric or class counts failed independent replay")

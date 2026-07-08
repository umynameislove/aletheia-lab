"""Small metric helpers used by evaluators."""

from __future__ import annotations

from collections.abc import Iterable


def binary_score(condition: bool) -> float:
    """Return 1.0 if condition is true, otherwise 0.0."""

    return 1.0 if condition else 0.0


def mean(values: Iterable[float]) -> float:
    """Return arithmetic mean for a non-empty iterable."""

    items = list(values)
    if not items:
        msg = "Cannot compute mean of an empty iterable"
        raise ValueError(msg)
    return sum(items) / len(items)


def divergence_label(faithful: bool, correct: bool) -> str:
    """Classify the relation between faithfulness and correctness."""

    if faithful and correct:
        return "faithful_and_correct"
    if faithful and not correct:
        return "faithful_but_wrong"
    if not faithful and correct:
        return "correct_but_unfaithful"
    return "unfaithful_and_wrong"

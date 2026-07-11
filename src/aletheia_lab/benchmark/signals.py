"""Distribution signals for data-drift benchmarking.

These helpers are pure and deterministic. They compute the observable signals a
diagnosis assistant is allowed to see (category proportions, Population Stability
Index). They never encode the hidden ground-truth label; they only describe what
the data looks like, which is legitimate evidence rather than an answer key.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# Smoothing constant so empty categories do not produce div-by-zero or log(0).
_EPSILON = 1e-6


def categorical_distribution(values: Sequence[object]) -> dict[str, float]:
    """Return the normalized category proportions of a sequence.

    Categories are stringified so the result is JSON-serializable and stable.
    """

    counts: dict[str, int] = {}
    total = 0
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
        total += 1
    if total == 0:
        return {}
    return {category: count / total for category, count in counts.items()}


def population_stability_index(
    expected: Mapping[str, float],
    actual: Mapping[str, float],
) -> float:
    """Population Stability Index between two categorical distributions.

    PSI = sum_c (a_c - e_c) * ln(a_c / e_c), computed over the union of
    categories with epsilon smoothing. Rule-of-thumb reading: < 0.1 stable,
    0.1-0.25 moderate shift, > 0.25 significant shift.
    """

    categories = set(expected) | set(actual)
    psi = 0.0
    for category in categories:
        e = max(expected.get(category, 0.0), _EPSILON)
        a = max(actual.get(category, 0.0), _EPSILON)
        psi += (a - e) * math.log(a / e)
    return psi

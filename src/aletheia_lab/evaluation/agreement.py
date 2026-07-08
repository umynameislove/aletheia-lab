"""Inter-annotator agreement metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


def cohens_kappa(rater_a: Sequence[str], rater_b: Sequence[str]) -> float:
    """Compute Cohen's kappa for two categorical rating sequences."""

    if len(rater_a) != len(rater_b):
        msg = "Rater sequences must have the same length"
        raise ValueError(msg)
    if not rater_a:
        msg = "Rater sequences must not be empty"
        raise ValueError(msg)

    observed = sum(a == b for a, b in zip(rater_a, rater_b, strict=True)) / len(rater_a)
    labels = set(rater_a) | set(rater_b)
    counts_a = Counter(rater_a)
    counts_b = Counter(rater_b)
    expected = sum(
        (counts_a[label] / len(rater_a)) * (counts_b[label] / len(rater_b)) for label in labels
    )

    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1.0 - expected)

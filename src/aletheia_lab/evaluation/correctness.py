"""Correctness evaluation helpers."""

from __future__ import annotations

from aletheia_lab.evaluation.metrics import binary_score


def normalized_label_match(predicted_label: str, true_label: str) -> float:
    """Exact label correctness after simple normalization."""

    return binary_score(predicted_label.strip().casefold() == true_label.strip().casefold())

"""Faithfulness evaluation helpers."""

from __future__ import annotations

from collections.abc import Mapping

from aletheia_lab.evaluation.metrics import mean


def claim_support_ratio(claim_to_supported: Mapping[str, bool]) -> float:
    """Compute the share of atomic claims supported by allowed evidence."""

    if not claim_to_supported:
        return 0.0
    return mean(1.0 if supported else 0.0 for supported in claim_to_supported.values())

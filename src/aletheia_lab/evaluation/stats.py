"""Statistical helper functions."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def bootstrap_mean_ci(values: Sequence[float], seed: int = 0, n_bootstrap: int = 1000) -> tuple[float, float]:
    """Return a simple 95% bootstrap CI for the mean."""

    if not values:
        msg = "values must not be empty"
        raise ValueError(msg)
    rng = np.random.default_rng(seed)
    samples = []
    array = np.array(values, dtype=float)
    for _ in range(n_bootstrap):
        samples.append(float(rng.choice(array, size=len(array), replace=True).mean()))
    low, high = np.quantile(samples, [0.025, 0.975])
    return float(low), float(high)

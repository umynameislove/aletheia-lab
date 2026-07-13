"""Deterministic evaluation of the baseline pipeline on each split.

Metrics are computed with an explicit zero-division policy so a degenerate split
never raises. ROC-AUC is only reported when both classes are present in the
split and the model exposes probabilities. Reporting several metrics together
(not a single headline number) keeps the baseline honest about class imbalance.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline

from aletheia_lab.baseline.loader import SplitData
from aletheia_lab.baseline.schema import SplitMetrics


def predict_split(
    pipeline: Pipeline, split: SplitData
) -> tuple[npt.NDArray[np.int_], npt.NDArray[np.float64]]:
    """Return (predicted labels, positive-class probabilities) for a split."""

    y_pred = pipeline.predict(split.features)
    proba = pipeline.predict_proba(split.features)[:, 1]
    return np.asarray(y_pred, dtype=int), np.asarray(proba, dtype=float)


def evaluate_split(pipeline: Pipeline, split: SplitData) -> SplitMetrics:
    """Compute the metric bundle for one split."""

    y_true = split.target.to_numpy(dtype=int)
    y_pred, proba = predict_split(pipeline, split)

    n = int(len(y_true))
    n_pos = int(y_true.sum())
    both_classes = 0 < n_pos < n

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    roc = float(roc_auc_score(y_true, proba)) if both_classes else None

    return SplitMetrics(
        n=n,
        n_positive=n_pos,
        n_negative=int(n - n_pos),
        accuracy=float((y_true == y_pred).mean()),
        balanced_accuracy=float(balanced_accuracy_score(y_true, y_pred)),
        precision=float(precision_score(y_true, y_pred, zero_division=0)),
        recall=float(recall_score(y_true, y_pred, zero_division=0)),
        f1=float(f1_score(y_true, y_pred, zero_division=0)),
        roc_auc=roc,
        confusion_matrix=[[int(tn), int(fp)], [int(fn), int(tp)]],
        zero_division_policy="zero",
    )

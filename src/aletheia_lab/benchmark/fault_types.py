"""Canonical fault type labels."""

from __future__ import annotations

from enum import StrEnum


class FaultType(StrEnum):
    """Fault types planned for the benchmark."""

    DATA_DRIFT = "data_drift"
    LABEL_NOISE = "label_noise"
    PREPROCESSING_BUG = "preprocessing_bug"
    TRAIN_EVAL_MISMATCH = "train_eval_mismatch"
    PROMPT_REGRESSION = "prompt_regression"

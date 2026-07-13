"""Tests for preprocessing (train-only fit, unknown categories) and the model."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aletheia_lab.baseline.loader import split_dataset
from aletheia_lab.baseline.model import ModelConfig, build_pipeline
from aletheia_lab.baseline.preprocess import build_preprocessor
from aletheia_lab.baseline.schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS

RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}


def _fit(frame, seed=42):
    splits = split_dataset(
        frame, dataset_id="x", dataset_sha256="y", seed=seed, ratios=RATIOS, stratified=True
    )
    pipe = build_pipeline(ModelConfig(seed=seed, max_iter=500))
    pipe.fit(splits.train.features, splits.train.target)
    return pipe, splits


def test_pipeline_fits_and_predicts(make_frame):
    pipe, splits = _fit(make_frame())
    preds = pipe.predict(splits.test.features)
    proba = pipe.predict_proba(splits.test.features)
    assert len(preds) == len(splits.test.features)
    assert proba.shape == (len(splits.test.features), 2)
    assert set(np.unique(preds)).issubset({0, 1})


def test_unknown_category_does_not_break_inference(make_frame):
    pipe, splits = _fit(make_frame())
    row = splits.test.features.iloc[[0]].copy()
    row[CATEGORICAL_FEATURES[0]] = "a-brand-new-unseen-category"
    pred = pipe.predict(row)
    assert pred.shape == (1,)  # handle_unknown='ignore' keeps inference alive


def test_preprocessor_fit_uses_training_data_only(make_frame):
    # A category value that exists only in validation must be unknown to the
    # fitted encoder (i.e. produce an all-zero one-hot block), proving fit did
    # not see validation rows.
    pipe, splits = _fit(make_frame())
    encoder = (
        pipe.named_steps["preprocess"].named_transformers_["categorical"].named_steps["onehot"]
    )
    train_categories = {
        col: set(cats) for col, cats in zip(CATEGORICAL_FEATURES, encoder.categories_, strict=True)
    }
    for col in CATEGORICAL_FEATURES:
        assert train_categories[col].issubset(set(splits.train.features[col].unique()))


def test_missing_values_are_imputed(make_frame):
    pipe, splits = _fit(make_frame())
    row = splits.test.features.iloc[[0]].copy()
    row["MonthlyCharges"] = np.nan
    row[CATEGORICAL_FEATURES[0]] = np.nan
    frame = pd.DataFrame(row, columns=list(FEATURE_COLUMNS))
    pred = pipe.predict(frame)
    assert pred.shape == (1,)


def test_preprocessor_does_not_learn_a_holdout_only_category(make_frame):
    # Simulate a category that appears only in validation/test: hold every
    # "Two year" contract out of the training frame, fit the preprocessor on the
    # rest (no target passed), and prove the encoder never learned it and that a
    # held-out row with that value is one-hot-zeroed at transform time.
    frame = make_frame(n=240, seed=0)
    train = frame[frame["Contract"] != "Two year"]
    holdout = frame[frame["Contract"] == "Two year"].head(1)
    assert len(train) > 0 and len(holdout) == 1

    pre = build_preprocessor()
    pre.fit(train[list(FEATURE_COLUMNS)])  # unsupervised: target is never used to fit

    encoder = pre.named_transformers_["categorical"].named_steps["onehot"]
    contract_pos = list(CATEGORICAL_FEATURES).index("Contract")
    assert "Two year" not in set(encoder.categories_[contract_pos])

    transformed = pre.transform(holdout[list(FEATURE_COLUMNS)])
    names = list(pre.get_feature_names_out())
    contract_cols = [i for i, name in enumerate(names) if name.startswith("Contract_")]
    assert contract_cols  # Contract produced one-hot columns
    assert (transformed[0, contract_cols] == 0).all()  # unseen category -> all zeros

"""Deterministic preprocessing for the baseline.

Numeric and categorical features are transformed by a single
``ColumnTransformer`` that is fit only on the training split (it is embedded in a
Pipeline with the estimator, so ``fit`` sees training data only and validation
and test are transform-only). Unknown categories at inference are ignored rather
than raising, and missing values have an explicit imputation strategy. The
target is never used to fit any transformer.
"""

from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from aletheia_lab.baseline.schema import CATEGORICAL_FEATURES, NUMERIC_FEATURES


def build_preprocessor() -> ColumnTransformer:
    """Return the feature preprocessor (numeric scaling + one-hot categoricals)."""

    numeric = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("numeric", numeric, list(NUMERIC_FEATURES)),
            ("categorical", categorical, list(CATEGORICAL_FEATURES)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )

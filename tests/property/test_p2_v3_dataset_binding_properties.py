"""Property invariants for the outcome-free v3 dataset bindings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    ArchiveBinding,
    V3DatasetBinding,
)

_MANIFEST = Path("configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json")


def _dataset_payload(index: int) -> dict[str, object]:
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    datasets = manifest["datasets"]
    assert isinstance(datasets, list)
    payload = datasets[index]
    assert isinstance(payload, dict)
    return payload


@given(dataset_index=st.integers(min_value=0, max_value=1), feature_index=st.integers(min_value=0))
def test_a_source_column_cannot_occupy_two_schema_roles(
    dataset_index: int, feature_index: int
) -> None:
    payload = _dataset_payload(dataset_index)
    categorical = payload["categorical_features"]
    numeric = payload["numeric_features"]
    excluded = payload["excluded_feature_columns"]
    assert isinstance(categorical, list)
    assert isinstance(numeric, list)
    assert isinstance(excluded, list)
    features = categorical + numeric
    excluded.append(features[feature_index % len(features)])

    with pytest.raises(ValidationError):
        V3DatasetBinding.model_validate_json(json.dumps(payload))


@given(
    dataset_index=st.integers(min_value=0, max_value=1),
    delta=st.floats(min_value=1e-6, max_value=0.1, allow_nan=False, allow_infinity=False),
)
def test_any_positive_split_fraction_drift_is_rejected(dataset_index: int, delta: float) -> None:
    payload = _dataset_payload(dataset_index)
    fractions = payload["split_fractions"]
    assert isinstance(fractions, list)
    fractions[0] += delta
    fractions[1] -= delta

    with pytest.raises(ValidationError):
        V3DatasetBinding.model_validate_json(json.dumps(payload))


@given(
    bad_digest=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=0,
        max_size=80,
    ).filter(
        lambda value: len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
    )
)
def test_non_sha256_archive_identity_is_never_accepted(bad_digest: str) -> None:
    payload = _dataset_payload(1)
    archive = payload["archive"]
    assert isinstance(archive, dict)
    archive["sha256"] = bad_digest

    with pytest.raises(ValidationError):
        ArchiveBinding.model_validate_json(json.dumps(archive))

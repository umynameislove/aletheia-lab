"""Synthetic in-memory conformance test for both registered P2R mechanisms."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    load_v3_3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PreparedRuntimeDataset,
    RuntimeSplit,
    fit_preprocessor,
    transform_features,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    load_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_runtime import execute_p2r_dataset


def _feature_frame(columns: tuple[str, ...], *, count: int, offset: int = 0) -> pd.DataFrame:
    values: dict[str, list[object]] = {}
    categorical = {"SEX", "EDUCATION", "MARRIAGE"}
    for column_index, column in enumerate(columns):
        if column in categorical:
            values[column] = [1 if (index + column_index) % 5 else 2 for index in range(count)]
        else:
            values[column] = [
                float(((index + offset) * (column_index + 3)) % 31) for index in range(count)
            ]
    return pd.DataFrame(values, columns=list(columns))


def _prepared():  # type: ignore[no-untyped-def]
    binding = load_v3_dataset_binding_manifest().datasets[0]
    receipt = load_v3_3_confirmatory_protocol().dataset_splits[0]
    train = _feature_frame(binding.analysis_features, count=60)
    development = _feature_frame(binding.analysis_features, count=20, offset=60)
    sealed = _feature_frame(binding.analysis_features, count=20, offset=80)
    state = fit_preprocessor(binding, train)
    train_targets = tuple(index % 2 for index in range(60))
    development_targets = tuple(index % 2 for index in range(20))
    sealed_targets = tuple(index % 2 for index in range(20))
    record_ids = tuple(f"synthetic-{index:03d}" for index in range(100))
    split = RuntimeSplit(
        protocol_sha256="1" * 64,
        dataset_id=binding.dataset_id,
        record_ids=record_ids,
        labels=train_targets + development_targets + sealed_targets,
        partitions=("train",) * 60 + ("development",) * 20 + ("sealed_test",) * 20,
        membership_sha256=receipt.membership_sha256,
        group_assignment_sha256="2" * 64,
        sealed_membership_sha256=receipt.sealed_membership_sha256,
    )
    prepared = PreparedRuntimeDataset(
        binding=binding,
        split_receipt=receipt,
        split=split,
        preprocessor=state,
        train_record_ids=record_ids[:60],
        development_record_ids=record_ids[60:80],
        sealed_record_ids=record_ids[80:],
        train_targets=train_targets,
        development_targets=development_targets,
        sealed_targets=sealed_targets,
        train_matrix=transform_features(dataset=binding, state=state, frame=train),
        development_matrix=transform_features(dataset=binding, state=state, frame=development),
        sealed_matrix=transform_features(dataset=binding, state=state, frame=sealed),
    )
    assert all(
        np.isfinite(matrix).all()
        for matrix in (
            prepared.train_matrix,
            prepared.development_matrix,
            prepared.sealed_matrix,
        )
    )
    return prepared, train, sealed


def test_both_mechanisms_execute_five_seed_census_without_mutating_inputs() -> None:
    prepared, training, sealed = _prepared()
    training_before = training.copy(deep=True)
    sealed_before = sealed.copy(deep=True)
    protocols = (
        load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH),
        load_lightweight_confirmatory_protocol(DEFAULT_PREPROCESSING_PROTOCOL_PATH),
    )

    results = [
        execute_p2r_dataset(
            protocol=protocol,
            prepared=prepared,
            training_frame=training,
            sealed_frame=sealed,
        )
        for protocol in protocols
    ]

    assert all(len(measurements) == 5 for _, measurements in results)
    assert all(
        tuple(item.seed for item in measurements) == (8201, 8202, 8203, 8204, 8205)
        for _, measurements in results
    )
    assert all(
        item.achieved_manipulation_magnitude == 0.20 for _, items in results for item in items
    )
    assert all(len({item.measurement_sha256 for item in items}) == 5 for _, items in results)
    assert all(
        item.canonical_sha256() == canonical_sha256(item.model_dump(mode="json"))
        for _, items in results
        for item in items
    )
    for receipt, _ in results:
        assert receipt.canonical_sha256() == canonical_sha256(receipt.model_dump(mode="json"))
        forged = receipt.model_dump(exclude={"model_sha256"})
        forged["model_parameters"] = ("C=2.0",)
        with pytest.raises(ValidationError, match="registered parameters"):
            type(receipt).model_validate(
                {**forged, "model_sha256": canonical_sha256(forged)}
            )
    pd.testing.assert_frame_equal(training, training_before)
    pd.testing.assert_frame_equal(sealed, sealed_before)

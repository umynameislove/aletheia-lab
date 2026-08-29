"""Synthetic replication-path execution for the P2R v1.2 amendment."""

from __future__ import annotations

import numpy as np
import pandas as pd

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
from aletheia_lab.benchmark.p2.p2r_runtime import execute_p2r_dataset
from aletheia_lab.benchmark.p2.p2r_v1_2_execution import compile_execution_protocol
from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    load_p2r_v1_2_protocol,
)


def _frame(columns: tuple[str, ...], *, count: int, offset: int) -> pd.DataFrame:
    categorical = {
        "Month",
        "OperatingSystems",
        "Browser",
        "Region",
        "TrafficType",
        "VisitorType",
        "Weekend",
    }
    values: dict[str, list[object]] = {}
    for column_index, column in enumerate(columns):
        if column == "OperatingSystems":
            values[column] = ["2" if index % 5 else "3" for index in range(count)]
        elif column in categorical:
            values[column] = [
                f"category-{(index + column_index) % 3}" for index in range(count)
            ]
        else:
            values[column] = [
                float(((index + offset) * (column_index + 3)) % 37)
                for index in range(count)
            ]
    return pd.DataFrame(values, columns=list(columns))


def _prepared() -> tuple[PreparedRuntimeDataset, pd.DataFrame, pd.DataFrame]:
    binding = load_v3_dataset_binding_manifest().datasets[1]
    split_receipt = load_v3_3_confirmatory_protocol().dataset_splits[1]
    train = _frame(binding.analysis_features, count=60, offset=0)
    development = _frame(binding.analysis_features, count=20, offset=60)
    sealed = _frame(binding.analysis_features, count=20, offset=80)
    state = fit_preprocessor(binding, train)
    record_ids = tuple(f"v1-2-synthetic-{index:03d}" for index in range(100))
    train_targets = tuple(index % 2 for index in range(60))
    development_targets = tuple(index % 2 for index in range(20))
    sealed_targets = tuple(index % 2 for index in range(20))
    split = RuntimeSplit(
        protocol_sha256="1" * 64,
        dataset_id=binding.dataset_id,
        record_ids=record_ids,
        labels=train_targets + development_targets + sealed_targets,
        partitions=("train",) * 60 + ("development",) * 20 + ("sealed_test",) * 20,
        membership_sha256=split_receipt.membership_sha256,
        group_assignment_sha256="2" * 64,
        sealed_membership_sha256=split_receipt.sealed_membership_sha256,
    )
    prepared = PreparedRuntimeDataset(
        binding=binding,
        split_receipt=split_receipt,
        split=split,
        preprocessor=state,
        train_record_ids=record_ids[:60],
        development_record_ids=record_ids[60:80],
        sealed_record_ids=record_ids[80:],
        train_targets=train_targets,
        development_targets=development_targets,
        sealed_targets=sealed_targets,
        train_matrix=transform_features(dataset=binding, state=state, frame=train),
        development_matrix=transform_features(
            dataset=binding, state=state, frame=development
        ),
        sealed_matrix=transform_features(dataset=binding, state=state, frame=sealed),
    )
    assert np.isfinite(prepared.train_matrix).all()
    return prepared, train, sealed


def test_replication_uses_registered_operating_systems_target_for_both_mechanisms() -> None:
    prepared, training, sealed = _prepared()
    amendments = (
        load_p2r_v1_2_protocol(DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH),
        load_p2r_v1_2_protocol(DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH),
    )
    protocols = tuple(compile_execution_protocol(item) for item in amendments)

    results = tuple(
        execute_p2r_dataset(
            protocol=protocol,
            prepared=prepared,
            training_frame=training,
            sealed_frame=sealed,
        )
        for protocol in protocols
    )

    assert all(len(measurements) == 5 for _, measurements in results)
    assert all(
        item.target_feature == "OperatingSystems"
        for _, measurements in results
        for item in measurements
    )
    assert all(
        item.achieved_manipulation_magnitude == 0.20
        for _, measurements in results
        for item in measurements
    )
    assert all(
        len({item.intervention_sha256 for item in measurements}) == 5
        for _, measurements in results
    )

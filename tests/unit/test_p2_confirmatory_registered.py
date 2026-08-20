"""Registered dataset, full-census execution and input-tampering tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    labelled_targets_sha256,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
    load_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    RegisteredDataset,
    RegisteredDatasetReceipt,
    execute_registered_dataset,
    load_registered_dataset,
    validate_dose_monotonicity,
)
from aletheia_lab.benchmark.p2.confirmatory_runtime import feature_frame_sha256


def _protocol_with_dataset(dataset: DatasetBinding) -> ConfirmatoryProtocol:
    original = load_confirmatory_protocol()
    datasets = tuple(dataset if item.role == dataset.role else item for item in original.datasets)
    return ConfirmatoryProtocol.model_validate({**original.model_dump(), "datasets": datasets})


def test_primary_loader_binds_bytes_ids_targets_and_features(tmp_path: Path) -> None:
    path = tmp_path / "telco.csv"
    path.write_text(
        "customerID,signal,Churn\n"
        "c-1,1.0,Yes\n"
        "c-2,0.0,No\n"
        "c-3,1.1,Yes\n"
        "c-4,0.1,No\n"
        "c-5,1.2,Yes\n"
        "c-6,0.2,No\n",
        encoding="utf-8",
    )
    original = load_confirmatory_protocol().datasets[0]
    dataset = DatasetBinding.model_validate(
        {
            **original.model_dump(),
            "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    protocol = _protocol_with_dataset(dataset)

    registered = load_registered_dataset(protocol=protocol, dataset=dataset, snapshot_path=path)

    assert registered.record_ids == ("c-1", "c-2", "c-3", "c-4", "c-5", "c-6")
    assert registered.targets == (1, 0, 1, 0, 1, 0)
    assert tuple(registered.features.columns) == ("signal",)
    assert registered.receipt.snapshot_sha256 == dataset.snapshot_sha256


def test_external_loader_requires_archive_and_excludes_duration(tmp_path: Path) -> None:
    snapshot = tmp_path / "bank-additional-full.csv"
    snapshot.write_text(
        "age;duration;job;y\n"
        "30;100;admin.;yes\n"
        "40;200;tech;no\n"
        "31;101;admin.;yes\n"
        "41;201;tech;no\n"
        "32;102;admin.;yes\n"
        "42;202;tech;no\n",
        encoding="utf-8",
    )
    archive = tmp_path / "bank.zip"
    archive.write_bytes(b"pinned archive fixture")
    original = load_confirmatory_protocol().datasets[1]
    dataset = DatasetBinding.model_validate(
        {
            **original.model_dump(),
            "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
    )
    protocol = _protocol_with_dataset(dataset)

    registered = load_registered_dataset(
        protocol=protocol,
        dataset=dataset,
        snapshot_path=snapshot,
        archive_path=archive,
    )

    assert registered.record_ids[0] == "bank-row-00000"
    assert tuple(registered.features.columns) == ("age", "job")
    assert "duration" not in registered.features
    with pytest.raises(ConfirmatoryExecutionError, match="archive"):
        load_registered_dataset(protocol=protocol, dataset=dataset, snapshot_path=snapshot)


def test_registered_loader_rejects_byte_and_target_tampering(tmp_path: Path) -> None:
    path = tmp_path / "telco.csv"
    path.write_text("customerID,signal,Churn\na,1,Maybe\nb,2,Maybe\n", encoding="utf-8")
    dataset = load_confirmatory_protocol().datasets[0]
    with pytest.raises(ConfirmatoryExecutionError, match="checksum"):
        load_registered_dataset(
            protocol=load_confirmatory_protocol(), dataset=dataset, snapshot_path=path
        )


def _synthetic_registered(role: str) -> RegisteredDataset:
    protocol = load_confirmatory_protocol()
    dataset = next(item for item in protocol.datasets if item.role == role)
    size = 120
    record_ids = tuple(f"{role}-{index:03d}" for index in range(size))
    targets = tuple(index % 2 for index in range(size))
    features = pd.DataFrame(
        {
            "signal": [
                float(target * 2 + (index % 5) / 20) for index, target in enumerate(targets)
            ],
            "auxiliary": [float(index % 7) for index in range(size)],
            "category": ["a" if index % 3 else "b" for index in range(size)],
        }
    )
    receipt = RegisteredDatasetReceipt(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        snapshot_sha256=dataset.snapshot_sha256,
        archive_sha256=dataset.archive_sha256,
        source_path_name="synthetic-readiness.csv",
        row_count=size,
        feature_columns=tuple(features.columns),
        excluded_features=dataset.excluded_features,
        target_column=dataset.target_column,
        positive_label=dataset.positive_label,
        negative_label="No" if role == "primary" else "no",
        record_membership_sha256=canonical_sha256(
            {
                "schema_version": "p2-confirmatory-registered-dataset/1",
                "record_ids": sorted(record_ids),
            }
        ),
        feature_matrix_sha256=feature_frame_sha256(features, record_ids),
        target_artifact_sha256=labelled_targets_sha256(record_ids, targets),
    )
    return RegisteredDataset(
        binding=dataset,
        receipt=receipt,
        record_ids=record_ids,
        targets=targets,
        features=features,
    )


def test_registered_dataset_executes_complete_census_once() -> None:
    protocol = load_confirmatory_protocol()
    outcome = execute_registered_dataset(
        protocol=protocol, registered=_synthetic_registered("primary")
    )

    assert len(outcome.replicates) == 180
    assert outcome.analysis.mode == "registered_confirmatory"
    assert outcome.analysis.admission_authorized
    assert outcome.analysis.batch_technical_gates_pass
    assert {item.replicate.cell_id for item in outcome.replicates} == {
        item.cell_id for item in protocol.intervention_cells
    }
    assert all(all(gate.passed for gate in item.replicate.controls) for item in outcome.replicates)
    assert set(validate_dose_monotonicity(outcome)) == {"yes_to_no", "no_to_yes"}


def test_dataset_outcome_cannot_drop_a_replicate() -> None:
    protocol = load_confirmatory_protocol()
    outcome = execute_registered_dataset(
        protocol=protocol, registered=_synthetic_registered("primary")
    )
    with pytest.raises(ValueError, match="180"):
        type(outcome).model_validate(
            {**outcome.model_dump(), "replicates": outcome.replicates[:-1]}
        )


def test_execution_rejects_in_memory_data_substitution_after_receipt() -> None:
    registered = _synthetic_registered("primary")
    tampered_features = registered.features.copy()
    tampered_features.loc[0, "signal"] = 999.0
    tampered = RegisteredDataset(
        binding=registered.binding,
        receipt=registered.receipt,
        record_ids=registered.record_ids,
        targets=registered.targets,
        features=tampered_features,
    )
    with pytest.raises(ConfirmatoryExecutionError, match="receipt"):
        execute_registered_dataset(protocol=load_confirmatory_protocol(), registered=tampered)

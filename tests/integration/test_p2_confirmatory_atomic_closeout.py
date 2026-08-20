"""Full synthetic census through joint atomic confirmatory publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

import aletheia_lab.benchmark.p2.confirmatory_closeout as closeout_module
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_closeout import (
    ProtocolRegistrationReceipt,
    build_closeout,
    capture_execution_environment,
    load_and_verify_result_store,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    labelled_targets_sha256,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    RegisteredDataset,
    RegisteredDatasetReceipt,
    execute_registered_dataset,
)
from aletheia_lab.benchmark.p2.confirmatory_runtime import feature_frame_sha256

_COMMIT = "f" * 40


def _registered(role: str) -> RegisteredDataset:
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


@pytest.fixture(scope="module")
def completed_batch():
    protocol = load_confirmatory_protocol()
    primary = execute_registered_dataset(protocol=protocol, registered=_registered("primary"))
    replication = execute_registered_dataset(
        protocol=protocol, registered=_registered("external_replication")
    )
    registration = ProtocolRegistrationReceipt(
        protocol_sha256=protocol.canonical_sha256(),
        tag_name="p2-label-noise-confirmatory-v2",
        tagged_protocol_commit="eaa16121e8c42b846fbc80eaadb77bf4ab157a08",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-confirmatory-v2"
        ),
        release_id=123,
        release_created_at=datetime(2026, 8, 17, 6, 39, tzinfo=UTC),
        release_published_at=datetime(2026, 8, 17, 7, 0, tzinfo=UTC),
        immutable=True,
        draft=False,
        prerelease=False,
    )
    environment = capture_execution_environment(_COMMIT)
    closeout = build_closeout(
        protocol=protocol,
        registration=registration,
        environment=environment,
        execution_commit=_COMMIT,
        primary=primary,
        replication=replication,
        executed_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )
    return registration, environment, primary, replication, closeout


def test_complete_primary_and_replication_publish_and_verify_together(
    tmp_path: Path, completed_batch
) -> None:
    registration, environment, primary, replication, closeout = completed_batch
    destination = tmp_path / "result-store"

    manifest = write_result_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )

    assert len(manifest.entries) == 5
    assert load_and_verify_result_store(destination) == manifest
    assert closeout.outcomes_released_together
    assert closeout.primary_replicate_count == closeout.replication_replicate_count == 180
    with pytest.raises(ConfirmatoryExecutionError, match="already exists"):
        write_result_store(
            output_dir=destination,
            registration=registration,
            environment=environment,
            primary=primary,
            replication=replication,
            closeout=closeout,
        )


def test_result_store_detects_post_publication_tampering(tmp_path: Path, completed_batch) -> None:
    registration, environment, primary, replication, closeout = completed_batch
    destination = tmp_path / "result-store"
    write_result_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    artifact = destination / "primary-outcome.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")

    with pytest.raises(ConfirmatoryExecutionError, match="checksum"):
        load_and_verify_result_store(destination)


def test_failed_atomic_write_leaves_no_partial_outcome(
    tmp_path: Path, completed_batch, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration, environment, primary, replication, closeout = completed_batch
    destination = tmp_path / "result-store"
    original = closeout_module._write_exclusive
    calls = 0

    def fail_on_replication(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ConfirmatoryExecutionError("injected write failure")
        original(path, content)

    monkeypatch.setattr(closeout_module, "_write_exclusive", fail_on_replication)
    with pytest.raises(ConfirmatoryExecutionError, match="injected"):
        write_result_store(
            output_dir=destination,
            registration=registration,
            environment=environment,
            primary=primary,
            replication=replication,
            closeout=closeout,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".result-store-*"))

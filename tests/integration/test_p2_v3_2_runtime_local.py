"""Outcome-free v3.2 runtime binding against the two pinned archives."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    load_v3_2_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import ExecutionPlan
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    V3_2_PROTOCOL_SHA256,
    prepare_runtime_dataset,
)

_DATA_DIR = Path("data/raw/p2-v3")


@pytest.mark.skipif(not _DATA_DIR.is_dir(), reason="local pinned v3 archives unavailable")
def test_v3_2_prepares_both_frozen_datasets_without_model_fit() -> None:
    protocol = load_v3_2_confirmatory_protocol()
    manifest = load_v3_dataset_binding_manifest()
    receipts = {item.dataset_id: item for item in protocol.dataset_splits}
    plan = ExecutionPlan.registered(protocol)

    assert protocol.canonical_sha256() == V3_2_PROTOCOL_SHA256
    assert plan.mode == "registered_execution"
    for dataset in manifest.datasets:
        checked, frame = load_v3_dataset_snapshot_for_registration(
            dataset=dataset,
            archive_path=_DATA_DIR / dataset.archive.file_name,
        )
        prepared = prepare_runtime_dataset(
            protocol=protocol,
            dataset=checked,
            split_receipt=receipts[dataset.dataset_id],
            frame=frame,
        )
        assert prepared.split.protocol_sha256 == V3_2_PROTOCOL_SHA256
        assert prepared.split.membership_sha256 == receipts[dataset.dataset_id].membership_sha256
        assert prepared.train_matrix.shape[1] == prepared.development_matrix.shape[1]
        assert prepared.train_matrix.shape[1] == prepared.sealed_matrix.shape[1]

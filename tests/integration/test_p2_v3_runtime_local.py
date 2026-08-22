"""Local outcome-free integration against the two SHA-pinned v3 archives."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import prepare_runtime_dataset

_DATA_DIR = Path("data/raw/p2-v3")
_EXPECTED_PREPROCESSORS = {
    "uci_default_of_credit_card_clients": (
        "0e6ccf85e9ef53659875141781ffe4656e8a8259a6d4bdd734e6b6c42bb10cb9",
        "a3b8e2381dbea8acfe8fd577d0d95f3a658b3338cbcb08ef43b4f0b65e37ac19",
    ),
    "uci_online_shoppers_purchasing_intention": (
        "326dfde2e88f02ab373d5f931cc032f8364fd41ca168980be9a3519c7aa40978",
        "00f034f3748de019dddfbf61b2cade3c8f821be01f148083515c04801a98166f",
    ),
}


@pytest.mark.skipif(not _DATA_DIR.is_dir(), reason="local pinned v3 archives unavailable")
def test_registered_splits_and_preprocessors_reconcile_without_model_fit() -> None:
    protocol = load_v3_confirmatory_protocol()
    manifest = load_v3_dataset_binding_manifest()
    receipts = {item.dataset_id: item for item in protocol.dataset_splits}
    prepared_hashes: dict[str, tuple[str, str]] = {}
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
        prepared_hashes[dataset.dataset_id] = (
            prepared.preprocessor.canonical_sha256(),
            prepared.preprocessor.output_columns_sha256,
        )
        assert prepared.split.membership_sha256 == receipts[dataset.dataset_id].membership_sha256
        assert prepared.train_matrix.shape[1] == prepared.development_matrix.shape[1]
        assert prepared.train_matrix.shape[1] == prepared.sealed_matrix.shape[1]
        assert set(prepared.train_targets) == {0, 1}
        assert set(prepared.development_targets) == {0, 1}
        assert set(prepared.sealed_targets) == {0, 1}
    assert prepared_hashes == _EXPECTED_PREPROCESSORS

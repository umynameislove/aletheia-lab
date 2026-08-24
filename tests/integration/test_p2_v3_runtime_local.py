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
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    fit_registered_model,
    prepare_runtime_dataset,
    reciprocal_control_targets,
)

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


@pytest.mark.skipif(not _DATA_DIR.is_dir(), reason="local pinned v3 archives unavailable")
def test_four_audited_reciprocal_cells_now_calibrate_on_development_data() -> None:
    """Regression over the exact cells implicated by the interrupted attempt."""

    protocol = load_v3_confirmatory_protocol()
    dataset = load_v3_dataset_binding_manifest().datasets[0]
    checked, frame = load_v3_dataset_snapshot_for_registration(
        dataset=dataset,
        archive_path=_DATA_DIR / dataset.archive.file_name,
    )
    receipt = next(
        item for item in protocol.dataset_splits if item.dataset_id == dataset.dataset_id
    )
    prepared = prepare_runtime_dataset(
        protocol=protocol,
        dataset=checked,
        split_receipt=receipt,
        frame=frame,
    )
    cells = (
        ("yes_to_no", 0.1, 6103),
        ("yes_to_no", 0.1, 6111),
        ("yes_to_no", 0.1, 6112),
        ("no_to_yes", 0.3, 6118),
    )
    observed: list[tuple[str, float, int]] = []
    for direction, rate, seed in cells:
        targets, _, _ = reciprocal_control_targets(
            dataset_id=dataset.dataset_id,
            record_ids=prepared.train_record_ids,
            clean_targets=prepared.train_targets,
            direction=direction,  # type: ignore[arg-type]
            conditional_rate=rate,
            seed=seed,
        )
        fitted = fit_registered_model(
            protocol=protocol,
            dataset=dataset,
            model_kind="logistic_regression",
            training_role=f"reciprocal-{direction}-{rate:g}-{seed}",
            state=prepared.preprocessor,
            training_matrix=prepared.train_matrix,
            training_record_ids=prepared.train_record_ids,
            training_targets=targets,
            development_matrix=prepared.development_matrix,
            development_record_ids=prepared.development_record_ids,
            development_targets=prepared.development_targets,
            evaluation_matrix=prepared.development_matrix,
            evaluation_record_ids=prepared.development_record_ids,
        )
        assert fitted.calibration.gradient_scale == "mean_per_development_record"
        assert fitted.calibration.gradient_infinity_norm <= 1e-8
        observed.append((direction, rate, seed))
    assert tuple(observed) == cells

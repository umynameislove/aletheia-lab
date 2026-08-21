"""End-to-end verification of locally acquired, Git-ignored v3 source bytes."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    build_v3_dataset_binding_receipt,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    verify_v3_dataset_binding_design,
    verify_v3_dataset_binding_receipt,
)

_DATA_DIRECTORY = Path("data/raw/p2-v3")


@pytest.mark.skipif(
    not all(
        (_DATA_DIRECTORY / name).is_file()
        for name in (
            "uci-default-of-credit-card-clients.zip",
            "uci-online-shoppers-purchasing-intention.zip",
        )
    ),
    reason="pinned v3 source archives are intentionally excluded from Git",
)
def test_local_official_archives_reproduce_the_tracked_receipt() -> None:
    manifest = load_v3_dataset_binding_manifest()
    expected = load_v3_dataset_binding_receipt()

    verify_v3_dataset_binding_design(manifest)
    observed = build_v3_dataset_binding_receipt(
        manifest,
        archive_directory=_DATA_DIRECTORY,
    )
    verify_v3_dataset_binding_receipt(observed, expected)
    assert observed.all_datasets_eligible
    assert not observed.model_fitted
    assert not observed.predictive_metrics_generated
    assert not observed.sealed_outcomes_generated

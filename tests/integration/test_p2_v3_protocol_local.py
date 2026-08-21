"""Local reproduction of frozen v3 protocol split membership."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    compile_v3_split_receipts,
    load_v3_confirmatory_protocol,
    verify_compiled_split_receipts,
    verify_v3_protocol_artifacts,
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
    reason="pinned v3 archives are intentionally excluded from Git",
)
def test_pinned_archives_reproduce_frozen_group_aware_membership() -> None:
    protocol = load_v3_confirmatory_protocol()
    _, manifest, receipt = verify_v3_protocol_artifacts(protocol)
    observed = compile_v3_split_receipts(
        manifest,
        receipt,
        archive_directory=_DATA_DIRECTORY,
    )

    verify_compiled_split_receipts(protocol, observed)
    assert all(item.duplicate_group_cross_partition_count == 0 for item in observed)
    assert all(not item.model_fitted for item in observed)
    assert all(not item.predictive_metrics_generated for item in observed)

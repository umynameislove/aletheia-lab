"""Local reproduction of the v3.2 recovery protocol split membership."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    load_v3_2_confirmatory_protocol,
    verify_v3_2_compiled_split_receipts,
    verify_v3_2_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    compile_v3_split_receipts,
)

_DATA_DIRECTORY = Path("data/raw/p2-v3")
_ARCHIVES = (
    "uci-default-of-credit-card-clients.zip",
    "uci-online-shoppers-purchasing-intention.zip",
)


@pytest.mark.skipif(
    not all((_DATA_DIRECTORY / name).is_file() for name in _ARCHIVES),
    reason="pinned v3 archives are intentionally excluded from Git",
)
def test_pinned_archives_reproduce_v3_2_without_opening_outcomes() -> None:
    protocol = load_v3_2_confirmatory_protocol()
    _, manifest, receipt, _, _ = verify_v3_2_protocol_artifacts(protocol)
    observed = compile_v3_split_receipts(
        manifest,
        receipt,
        archive_directory=_DATA_DIRECTORY,
    )

    verify_v3_2_compiled_split_receipts(protocol, observed)
    assert tuple(item.membership_sha256 for item in observed) == tuple(
        item.membership_sha256 for item in protocol.dataset_splits
    )
    assert all(item.duplicate_group_cross_partition_count == 0 for item in observed)
    assert all(not item.model_fitted for item in observed)
    assert all(not item.predictive_metrics_generated for item in observed)
    assert not protocol.model_fitted
    assert not protocol.predictive_metrics_generated
    assert not protocol.sealed_outcomes_generated

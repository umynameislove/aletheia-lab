"""Integration checks for preparing reviewer-isolated P2 validity packets."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.alpha_execution import (
    execute_primary_alpha,
    prepare_alpha_runtime,
)
from aletheia_lab.benchmark.p2.artifacts import load_contract_store, save_contract_store
from aletheia_lab.benchmark.p2.evidence_conditions import rebuild_evidence_bundles_from_census
from aletheia_lab.benchmark.p2.human_validity_review import (
    HumanValidityReviewError,
    build_human_review_packets,
)

_PROCESSED = Path("data/processed/telco_customer_churn.csv")


@pytest.fixture(scope="module")
def incomplete_alpha_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialise the pinned real-alpha store without relying on local run output."""

    if not _PROCESSED.is_file():
        pytest.skip("processed Telco split is not present")
    store = tmp_path_factory.mktemp("p2-human-review") / "alpha-primary-seed42"
    artifacts = execute_primary_alpha(prepare_alpha_runtime())
    save_contract_store(artifacts, store)
    return store


def test_persisted_alpha_store_fails_closed_without_complete_mechanism_sample(
    incomplete_alpha_store: Path,
) -> None:
    artifacts = load_contract_store(incomplete_alpha_store).artifacts
    bundles = rebuild_evidence_bundles_from_census(
        execution=artifacts.execution,
        census=artifacts.census,
        contexts=artifacts.contexts,
    )
    assert len(bundles) == len(artifacts.contexts.entries)
    with pytest.raises(
        HumanValidityReviewError,
        match="no complete eligible family for label_noise",
    ):
        build_human_review_packets(bundles)


def test_prepare_cli_refuses_incomplete_real_alpha_sample(
    tmp_path: Path,
    incomplete_alpha_store: Path,
) -> None:
    output = tmp_path / "review"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/p2_human_review.py",
            "prepare",
            "--store",
            str(incomplete_alpha_store),
            "--output",
            str(output),
            "--reviewer",
            "reviewer-quan",
            "--reviewer",
            "reviewer-kien",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "no complete eligible family for label_noise" in result.stderr
    assert not output.exists()

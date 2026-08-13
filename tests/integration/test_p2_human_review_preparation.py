"""Integration checks for preparing reviewer-isolated P2 validity packets."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.artifacts import load_contract_store
from aletheia_lab.benchmark.p2.evidence_conditions import rebuild_evidence_bundles_from_census
from aletheia_lab.benchmark.p2.human_validity_review import (
    HumanValidityReviewError,
    build_human_review_packets,
)

STORE = Path("experiments/p2/runs/alpha-primary-seed42")


def test_persisted_alpha_store_fails_closed_without_complete_mechanism_sample() -> None:
    artifacts = load_contract_store(STORE).artifacts
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


def test_prepare_cli_refuses_incomplete_real_alpha_sample(tmp_path: Path) -> None:
    output = tmp_path / "review"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/p2_human_review.py",
            "prepare",
            "--store",
            str(STORE),
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

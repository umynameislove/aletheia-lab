"""Local verification of the outcome-blind P2R v1.2 amendment pair."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIRECTORY = ROOT / "data/raw/p2-v3"
ARCHIVE_NAMES = (
    "uci-default-of-credit-card-clients.zip",
    "uci-online-shoppers-purchasing-intention.zip",
)


def _run(command: str) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "scripts/p2r_v1_2_protocol_registration.py", command],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)  # type: ignore[no-any-return]


def test_tracked_v1_2_pair_verifies_without_outcomes() -> None:
    payload = _run("verify")
    assert payload["status"] == ("p2r_v1_2_methodological_amendments_verified_not_registered")
    assert payload["dataset_target_features"] == {
        "uci_default_of_credit_card_clients": "EDUCATION",
        "uci_online_shoppers_purchasing_intention": "OperatingSystems",
    }
    assert payload["dataset_target_row_counts"] == {
        "uci_default_of_credit_card_clients": 1200,
        "uci_online_shoppers_purchasing_intention": 493,
    }
    assert payload["dataset_minimum_capacity_counts"] == {
        "uci_default_of_credit_card_clients": 2795,
        "uci_online_shoppers_purchasing_intention": 1140,
    }
    assert payload["scientific_semantics_changed_and_disclosed"] is True
    assert payload["all_other_scientific_sections_inherited_by_hash"] is True
    assert payload["independent_new_dataset_replication"] is False
    assert payload["feasibility_recompiled_from_pinned_archives"] is False
    assert payload["model_fitted"] is False
    assert payload["predictive_metrics_generated"] is False
    assert payload["sealed_outcomes_generated"] is False
    assert payload["registration_authorized"] is False
    assert payload["execution_authorized"] is False


@pytest.mark.skipif(
    not all((ARCHIVE_DIRECTORY / name).is_file() for name in ARCHIVE_NAMES),
    reason="ignored SHA-pinned dataset archives are unavailable",
)
def test_archives_reproduce_frozen_feasibility_without_outcomes() -> None:
    payload = _run("compile-feasibility")
    assert payload["feasibility_recompiled_from_pinned_archives"] is True
    assert payload["feasibility_receipt_sha256"] == (
        "2234d0c7bf9b1a35e971792a34134c3917ce35b8ff6410afec8f64625f673c13"
    )
    assert payload["model_fitted"] is False
    assert payload["predictive_metrics_generated"] is False
    assert payload["sealed_outcomes_generated"] is False

"""Local outcome-blind verification for the P2R v1.1 recovery pair."""

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
        [sys.executable, "scripts/p2r_v1_1_protocol_registration.py", command],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)  # type: ignore[no-any-return]


def test_tracked_v1_1_pair_verifies_without_outcomes() -> None:
    payload = _run("verify")
    assert payload["status"] == "p2r_v1_1_recovery_protocols_verified_not_registered"
    assert payload["scientific_sections_unchanged"] is True
    assert payload["archive_readiness_recompiled"] is False
    assert payload["maximum_registered_execution_attempts"] == 1
    assert payload["predecessor_rerun_forbidden"] is True
    assert payload["model_fitted"] is False
    assert payload["predictive_metrics_generated"] is False
    assert payload["sealed_outcomes_generated"] is False
    assert payload["registration_authorized"] is False
    assert payload["execution_authorized"] is False


@pytest.mark.skipif(
    not all((ARCHIVE_DIRECTORY / name).is_file() for name in ARCHIVE_NAMES),
    reason="ignored SHA-pinned dataset archives are unavailable",
)
def test_local_archives_reproduce_frozen_v1_1_readiness_without_model_fit() -> None:
    payload = _run("compile-readiness")
    assert payload["archive_readiness_recompiled"] is True
    assert payload["archive_readiness_sha256"] == (
        "528e5d1d25f905c450faeafe6c35c87b7cc09f25f4f9fe77666f85da5c36403c"
    )
    assert payload["model_fitted"] is False
    assert payload["predictive_metrics_generated"] is False
    assert payload["sealed_outcomes_generated"] is False

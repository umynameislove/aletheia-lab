"""Local integration check for the tracked outcome-blind P2R protocol."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_tracked_protocol_verifies_without_generating_outcomes() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "scripts/p2r_instrument_validity.py", "verify"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "p2r_instrument_validity_protocol_verified_outcome_blind"
    assert payload["required_mechanisms"] == ["data_drift", "preprocessing_bug"]
    assert payload["model_fitted"] is False
    assert payload["confirmatory_outcomes_generated"] is False
    assert payload["empty_evidence_visible_artifact_count"] == 0
    assert len(payload["protocol_sha256"]) == 64
    assert set(payload["mechanism_protocols"]) == {
        "data_drift",
        "preprocessing_bug",
    }
    for registered in payload["mechanism_protocols"].values():
        assert len(registered["protocol_sha256"]) == 64
        assert registered["required_git_tag"].startswith("p2r-")

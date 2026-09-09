"""Local-process reproducibility for the offline V2 expressiveness review."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "claim_support_validation_v2_expressiveness.py"


def _verify(seed: int) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--root", str(ROOT)],
        check=True,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_tracked_expressiveness_review_is_hash_seed_stable_and_offline():
    first = _verify(1)
    second = _verify(104729)
    assert first == second
    assert first["status"] == "v2_expressiveness_review_pass_qualification_pending"
    assert first["provider_calls_executed"] is False
    assert first["claims_materialized"] is False
    assert first["blind_packets_generated"] is False
    assert first["next_gate"] == "separately_authorized_seven_request_live_qualification"

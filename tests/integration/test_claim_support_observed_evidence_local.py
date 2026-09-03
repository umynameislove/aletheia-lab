from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_observed_evidence.py"


def _verify(seed: int) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "verify"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
    )


def test_measured_census_is_byte_stable_across_process_hash_seeds() -> None:
    first = _verify(1)
    second = _verify(104729)

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == b""
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == "claim_observed_evidence_census_complete_zero_outcome"
    assert payload["context_count"] == 45
    assert payload["census_disposition"] == "verified"
    assert payload["receipt_disposition"] == "verified"
    assert payload["observed_evidence_census_pending"] is False
    assert payload["provider_calls_executed"] is False
    assert payload["main_or_sealed_outcomes_opened"] is False

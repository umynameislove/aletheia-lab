from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_normalization_recovery.py"


def _verify(seed: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = seed
    return subprocess.run(
        [sys.executable, str(SCRIPT), "verify"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
    )


def test_recovery_registration_is_hash_seed_stable_and_non_executing() -> None:
    first = _verify("1")
    second = _verify("104729")

    assert first.returncode == 0, first.stderr.decode()
    assert second.returncode == 0, second.stderr.decode()
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == ("normalization_recovery_contract_frozen_authorization_pending")
    assert payload["predecessor_terminal_request_count"] == 360
    assert payload["predecessor_schema_rejection_count"] == 209
    assert payload["predecessor_claim_candidate_count"] == 152
    assert payload["target_claim_count"] == 200
    assert payload["new_authorization_required"] is True
    assert payload["provider_calls_executed"] is False
    assert payload["claims_materialized"] is False
    assert payload["automatic_labels_generated"] is False
    assert payload["blind_packets_generated"] is False
    assert payload["human_annotations_collected"] is False
    assert payload["main_or_sealed_outcomes_opened"] is False

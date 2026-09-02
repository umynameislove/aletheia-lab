from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_claim_support_corpus_protocol.py"


def _run(command: str, seed: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    return subprocess.run(
        [sys.executable, str(SCRIPT), command],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_tracked_protocol_verifies_byte_stably_across_process_hash_seeds() -> None:
    first = _run("verify", "1")
    second = _run("verify", "104729")

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == "corpus_protocol_frozen_source_expansion_required"
    assert payload["current_maximum_selectable_claims_per_label"] == 25
    assert payload["target_claims_per_label"] == 50
    assert payload["development_claim_pool_materialized"] is False
    assert payload["main_or_sealed_outcomes_opened"] is False


def test_materialization_gate_blocks_with_the_complete_prespecified_census() -> None:
    completed = _run("require-materialization-ready", "209759")

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["materialization_ready"] is False
    assert payload["blocker_codes"] == [
        "automatic_instrument_manifest_pending",
        "diagnosis_output_v2_schema_pending",
        "insufficient_development_family_census",
        "label_noise_family_manifest_pending",
        "preprocessing_family_manifest_pending",
        "reserve_family_census_pending",
    ]

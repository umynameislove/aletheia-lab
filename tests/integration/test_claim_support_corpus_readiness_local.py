from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_corpus_readiness.py"


def _run(seed: int) -> bytes:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    return subprocess.check_output(
        [sys.executable, str(SCRIPT), "verify"],
        cwd=ROOT,
        env=environment,
    )


def test_tracked_readiness_is_reproducible_across_process_hash_seeds() -> None:
    first = _run(1)
    second = _run(8675309)

    assert first == second
    payload = json.loads(first)
    assert payload["status"] == "claim_corpus_materialization_ready_zero_outcome"
    assert payload["materialization_ready"] is True
    assert payload["primary_request_count"] == 360
    assert payload["reserve_request_count"] == 144
    assert payload["provider_calls_executed"] is False
    assert payload["outputs_generated"] is False
    assert payload["development_claim_pool_materialized"] is False
    assert payload["automatic_labels_generated"] is False
    assert payload["human_annotations_collected"] is False
    assert payload["main_or_sealed_outcomes_opened"] is False

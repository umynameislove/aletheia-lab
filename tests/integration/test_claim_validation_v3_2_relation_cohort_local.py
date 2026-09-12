"""Cross-process stability for the offline V3.2 relation cohort freeze."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_validation_v3_2_relation_cohort.py"


def _run(command: str, seed: str) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONHASHSEED": seed,
    }
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), command],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )


def test_protocol_is_hash_seed_stable_without_credential() -> None:
    for command in ("review", "verify-protocol"):
        first = _run(command, "1")
        second = _run(command, "104729")
        assert first.returncode == second.returncode == 0
        assert first.stdout == second.stdout
        payload = json.loads(first.stdout)
        assert payload["provider_calls_executed"] is False
        assert payload["relation_execution_authorized"] is False
        assert payload["claims_materialized"] is False
        assert payload["blind_packets_generated"] is False

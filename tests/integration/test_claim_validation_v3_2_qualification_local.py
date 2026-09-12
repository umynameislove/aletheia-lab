"""Cross-process stability and credential isolation for V3.2 qualification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_validation_v3_2_qualification.py"


def _run(command: str, seed: str, run: Path) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONHASHSEED": seed,
    }
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--run-dir", str(run)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )


def test_protocol_plan_and_rehearsal_are_hash_seed_stable_without_credential(
    tmp_path: Path,
) -> None:
    for command in ("verify-protocol", "plan", "rehearse"):
        first = _run(command, "1", tmp_path / "first")
        second = _run(command, "104729", tmp_path / "second")
        assert first.returncode == second.returncode == 0
        assert first.stdout == second.stdout
        payload = json.loads(first.stdout)
        assert payload["provider_calls_executed"] is False
        assert payload["claims_materialized"] is False
        assert payload["blind_packets_generated"] is False
        if command != "verify-protocol":
            assert payload.get("relation_planning_unlocked") is False


def test_live_gate_without_authorization_does_not_register_execution(
    tmp_path: Path,
) -> None:
    run = tmp_path / "qualification"
    result = _run("require-live-ready", "7", run)
    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["blocker_code"] == "authorization_absent"
    assert payload["provider_calls_executed"] is False
    assert payload["execution_registered"] is False
    assert not run.exists()

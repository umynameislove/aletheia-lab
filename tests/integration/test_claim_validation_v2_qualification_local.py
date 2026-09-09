"""Process-level reproducibility and fail-closed tests for V2 qualification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "claim_support_validation_v2_qualification.py"


def _run(
    command: str,
    seed: int,
    run_dir: Path,
    *,
    credential: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    if credential is None:
        environment.pop("OPENAI_API_KEY", None)
    else:
        environment["OPENAI_API_KEY"] = credential
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            command,
            "--root",
            str(ROOT),
            "--run-dir",
            str(run_dir),
        ],
        check=False,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )


def test_plan_and_rehearsal_are_hash_seed_stable_and_outcome_free(tmp_path: Path):
    first_plan = _run("plan", 1, tmp_path / "run")
    second_plan = _run("plan", 104729, tmp_path / "run")
    assert first_plan.returncode == second_plan.returncode == 0
    assert first_plan.stdout == second_plan.stdout
    plan = json.loads(first_plan.stdout)
    assert plan["request_count"] == 7
    assert plan["variants"] == ["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
    assert plan["provider_calls_executed"] is False
    assert plan["claims_materialized"] is False
    assert plan["blind_packets_generated"] is False

    first_rehearsal = _run("rehearse", 1, tmp_path / "run")
    second_rehearsal = _run("rehearse", 104729, tmp_path / "run")
    assert first_rehearsal.returncode == second_rehearsal.returncode == 0
    assert first_rehearsal.stdout == second_rehearsal.stdout
    rehearsal = json.loads(first_rehearsal.stdout)
    assert rehearsal["exact_first_witness_accepted"] is True
    assert rehearsal["changed_witness_rejected"] is True
    assert rehearsal["abstention_rejected_for_qualification"] is True
    assert rehearsal["unknown_evidence_rejected"] is True


def test_live_gate_without_authorization_or_credential_fails_before_provider(
    tmp_path: Path,
):
    completed = _run("require-live-ready", 1, tmp_path / "run")
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "claim_support_validation_v2_qualification_live_blocked"
    assert "authorization_pending" in payload["live_blockers"]
    assert "credential_missing" in payload["live_blockers"]
    assert payload["provider_calls_executed"] is False
    assert payload["claims_materialized"] is False
    assert payload["blind_packets_generated"] is False


def test_live_gate_never_renders_the_credential(tmp_path: Path):
    secret = "sk-this-value-must-never-be-rendered"
    completed = _run("require-live-ready", 1, tmp_path / "run", credential=secret)
    assert completed.returncode == 2
    assert secret.encode() not in completed.stdout
    assert secret.encode() not in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["credential_present"] is True
    assert payload["provider_calls_executed"] is False

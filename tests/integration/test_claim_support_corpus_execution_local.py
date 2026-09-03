from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_corpus_execution.py"


def _run(
    command: str, seed: int, *, credential: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    if credential is None:
        environment.pop("OPENAI_API_KEY", None)
    else:
        environment["OPENAI_API_KEY"] = credential
    return subprocess.run(
        [sys.executable, str(SCRIPT), command],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
    )


def test_offline_rehearsal_is_byte_stable_across_hash_seeds() -> None:
    first = _run("rehearse", 1)
    second = _run("rehearse", 104729)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == "claim_corpus_execution_rehearsal_passed_live_blocked"
    assert payload["model_request_count"] == 315
    assert payload["deterministic_request_count"] == 45
    assert payload["reserve_request_count_scheduled"] == 0
    assert payload["provider_calls_executed"] is False


def test_live_gate_fails_closed_and_never_prints_the_credential() -> None:
    secret = "sk-this-value-must-never-be-rendered"
    completed = _run("require-live-ready", 1, credential=secret)

    assert completed.returncode == 2
    assert secret.encode() not in completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["credential_present"] is True
    assert "observed_evidence_census_pending" in payload["live_blockers"]
    assert payload["relation_assignment_request_ceiling"] == 1800
    assert payload["provider_calls_executed"] is False

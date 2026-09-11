"""CLI hash stability and missing-input boundaries without private artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, seed: str = "1"):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONHASHSEED": seed,
        "OPENAI_API_KEY": "sk-test-must-not-be-rendered",
    }
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/claim_support_validation_v3.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )


def test_protocol_is_byte_stable_across_process_hash_seeds():
    first = _run("verify-protocol", seed="1")
    second = _run("verify-protocol", seed="927")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["capacity_passed"]


def test_failed_qualification_closeout_is_byte_stable_across_hash_seeds():
    first = _run("verify-failure-closeout", seed="1")
    second = _run("verify-failure-closeout", seed="927")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["receipt_sha256"] == (
        "7e45169d1167e72a0e76eb3011c3e4495c7981780db26f84e8518e29123beec2"
    )
    assert payload["rerun_forbidden"] is True


def test_no_credential_or_paid_attempt_on_missing_predecessor(tmp_path):
    result = _run(
        "execute",
        "--predecessor-closeout",
        str(tmp_path / "absent/closeout.json"),
        "--run-dir",
        str(tmp_path / "run"),
    )
    assert result.returncode == 2
    assert b"sk-test-must-not-be-rendered" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["provider_calls_executed"] is False
    assert not (tmp_path / "run").exists()

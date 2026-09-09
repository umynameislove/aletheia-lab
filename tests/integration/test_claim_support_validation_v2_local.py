from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_validation_v2.py"


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


def test_v2_freeze_is_hash_seed_stable_and_non_executing() -> None:
    first = _verify("1")
    second = _verify("104729")

    assert first.returncode == 0, first.stderr.decode()
    assert second.returncode == 0, second.stderr.decode()
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == (
        "claim_support_validation_v2_protocol_frozen_implementation_pending"
    )
    assert payload["v1_technical_failure_count"] == 78
    assert payload["v2_diagnosis_request_count"] == 360
    assert payload["v2_relation_request_ceiling"] == 1440
    assert payload["provider_calls_executed"] is False
    assert payload["blind_packets_generated"] is False


def test_v1_audit_requires_all_private_inputs_without_creating_them(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "audit-v1"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "claim_support_validation_v2_verification_failed"
    assert not tuple(tmp_path.iterdir())

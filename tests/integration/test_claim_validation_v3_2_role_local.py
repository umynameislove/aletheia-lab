"""Cross-process stability for the provider-free V3.2 role freeze."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_validation_v3_2.py"


def _run(command: str, seed: str) -> subprocess.CompletedProcess[bytes]:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed}
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), command],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )


def test_role_protocol_is_stable_without_credential_or_provider() -> None:
    first = _run("verify-protocol", "1")
    second = _run("verify-protocol", "104729")

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["source_provider_request_count"] == 0
    assert payload["provider_calls_executed"] is False
    assert payload["blind_packets_generated"] is False


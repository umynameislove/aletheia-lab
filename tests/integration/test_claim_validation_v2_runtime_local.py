"""Fresh-process reproducibility of the offline V2 runtime artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_verify_is_hash_seed_stable_and_does_not_authorize_execution():
    outputs = []
    for seed in ("1", "104729"):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONHASHSEED=seed)
        environment.pop("OPENAI_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/claim_support_validation_v2_runtime.py"),
             "verify", "--root", str(ROOT)],
            cwd=ROOT, env=environment, capture_output=True, check=False, timeout=60,
        )
        assert completed.returncode == 0, completed.stdout.decode() + completed.stderr.decode()
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    payload = json.loads(outputs[0])
    assert payload["diagnosis_request_count"] == 360
    assert payload["qualification_request_count"] == 7
    assert not payload["provider_calls_executed"]
    assert not payload["claims_materialized"]
    assert not payload["blind_packets_generated"]

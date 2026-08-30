"""Cross-process determinism check for the complete fake-runtime result."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _execution_result(hash_seed: str) -> str:
    helper_path = Path(__file__).parents[1] / "unit" / "test_model_gateway_runtime.py"
    script = f"""
import runpy

helpers = runpy.run_path({str(helper_path)!r})
request = helpers["_request"](max_attempts=2)
steps = (
    helpers["_error"]("transient_error"),
    helpers["_response"]("valid_response"),
)
print(helpers["_execute"](request, steps).model_dump_json())
"""
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed.stdout.strip()


def test_full_fake_result_is_stable_across_process_hash_seeds() -> None:
    assert _execution_result("1") == _execution_result("987654")

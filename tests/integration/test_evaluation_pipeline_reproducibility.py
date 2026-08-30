"""Environment-independent fingerprint for the complete offline evaluation path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _pipeline_fingerprint(
    store_root: Path,
    *,
    hash_seed: str,
    timezone: str,
    locale: str,
) -> dict[str, str]:
    helper = Path(__file__).parents[1] / "unit" / "test_evaluation_structural_closeout.py"
    script = f"""
import json
import runpy

helpers = runpy.run_path({str(helper)!r})
manifest = helpers["_manifest"]()
request = helpers["_request"](manifest, case_character="7", prompt="synthetic")
result = helpers["_result"](request)
store = helpers["ImmutableAttemptStore"]({str(store_root)!r}, clock=helpers["_Clock"]())
helpers["_record_terminal"](store, request, result)
plan = helpers["_plan"](
    manifest,
    (helpers["_expectation"](request, attempt_count=len(result.attempts)),),
)
receipt = helpers["reduce_structural_closeout"](plan, store=store)
print(json.dumps({{
    "request_identity_sha256": request.initial_attempt.request_identity_sha256,
    "raw_response_sha256": result.raw_response.content_sha256,
    "receipt_sha256": receipt.receipt_sha256,
    "store_sha256": receipt.store_sha256,
}}, sort_keys=True, separators=(",", ":")))
"""
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONHASHSEED": hash_seed,
            "TZ": timezone,
            "LANG": locale,
            "LC_ALL": locale,
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
    )
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in parsed.items())
    return parsed


def test_fake_provider_store_and_reducer_are_environment_independent(tmp_path: Path) -> None:
    first = _pipeline_fingerprint(
        tmp_path / "short-root",
        hash_seed="1",
        timezone="UTC0",
        locale="C",
    )
    second = _pipeline_fingerprint(
        tmp_path / "a" / "different" / "root" / "shape",
        hash_seed="982451653",
        timezone="Pacific/Honolulu",
        locale="C.UTF-8",
    )

    assert first == second
    assert set(first) == {
        "raw_response_sha256",
        "receipt_sha256",
        "request_identity_sha256",
        "store_sha256",
    }
    assert all(len(value) == 64 for value in first.values())

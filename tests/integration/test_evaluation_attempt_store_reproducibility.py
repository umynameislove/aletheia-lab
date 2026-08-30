"""Cross-process/hash-seed reproducibility for the immutable attempt store."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _store_fingerprint(tmp_path: Path, hash_seed: str) -> str:
    helper = Path(__file__).parents[1] / "unit" / "test_evaluation_attempt_store.py"
    output = tmp_path / f"store-{hash_seed}"
    script = f"""
import runpy

helpers = runpy.run_path({str(helper)!r})
request = helpers["_request"]()
result = helpers["_result"](request)
store = helpers["ImmutableAttemptStore"]({str(output)!r}, clock=helpers["_Clock"]())
helpers["_record_until"](store, request, result, "terminal_published")
terminal = {str(output)!r} + "/terminal/" + result.request_identity_sha256 + ".json"
print(store.store_sha256())
print(open(terminal, encoding="utf-8").read(), end="")
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
    return completed.stdout


def test_store_is_reproducible_across_process_hash_seeds(tmp_path: Path) -> None:
    assert _store_fingerprint(tmp_path, "1") == _store_fingerprint(tmp_path, "918273")

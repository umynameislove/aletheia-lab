"""Cross-process reproducibility for offline structural closeout receipts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _receipt_json(tmp_path: Path, hash_seed: str) -> str:
    helper = Path(__file__).parents[1] / "unit" / "test_evaluation_structural_closeout.py"
    output = tmp_path / f"store-{hash_seed}"
    script = f"""
import runpy

helpers = runpy.run_path({str(helper)!r})
manifest = helpers["_manifest"]()
first = helpers["_request"](manifest, case_character="7", prompt="first")
second = helpers["_request"](manifest, case_character="8", prompt="second")
first_result = helpers["_result"](first)
second_result = helpers["_result"](second, kind="abstention")
store = helpers["ImmutableAttemptStore"]({str(output)!r}, clock=helpers["_Clock"]())
helpers["_record_terminal"](store, first, first_result)
helpers["_record_terminal"](store, second, second_result)
expectations = (
    helpers["_expectation"](second, attempt_count=1),
    helpers["_expectation"](first, attempt_count=1),
)
plan = helpers["_plan"](manifest, expectations)
receipt = helpers["reduce_structural_closeout"](plan, store=store)
print(receipt.model_dump_json())
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


def test_receipt_is_reproducible_across_process_hash_seeds(tmp_path: Path) -> None:
    assert _receipt_json(tmp_path, "1") == _receipt_json(tmp_path, "76213")

"""Local CLI boundary tests for provider-free V2 extraction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_validation_v2_extraction.py"


def test_cli_refuses_repository_destination_without_reading_private_inputs() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            os.fspath(SCRIPT),
            "prepare",
            "--root",
            os.fspath(ROOT),
            "--qualification-run-dir",
            os.fspath(ROOT / "absent-qualification"),
            "--cohort-run-dir",
            os.fspath(ROOT / "absent-cohort"),
            "--run-dir",
            os.fspath(ROOT / "private-extraction"),
        ),
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": os.fspath(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "claim_support_validation_v2_extraction_failed"
    assert payload["additional_provider_calls_executed"] is False
    assert payload["relation_execution_authorized"] is False
    assert payload["blind_packets_generated"] is False

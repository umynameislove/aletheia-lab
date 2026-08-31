"""Tracked diagnosis freezes must verify without opening protected outcomes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_versioned_diagnosis_freezes_are_outcome_blind_and_fail_closed() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/diagnosis_evaluation_freeze.py", "all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "diagnosis_freezes_verified_with_execution_blockers"
    assert payload["blocker_count"] == 2
    assert payload["protected_outcomes_opened"] is False
    assert payload["execution_authorized"] is False
    assert payload["feasibility"]["blocker_codes"] == ["runtime.production-gateway-adapter"]
    assert payload["fairness"]["blocker_codes"] == ["implementation_artifacts_resolve"]


def test_require_ready_exits_nonzero_while_blockers_remain() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/diagnosis_evaluation_freeze.py", "all", "--require-ready"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout)["blocker_count"] == 2

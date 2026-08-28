"""Local reconciliation of ignored P2R failure and archive evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TERMINAL_STORE = ROOT / "experiments/p2/outputs/p2r-confirmatory-v1"
ARCHIVE_DIRECTORY = ROOT / "data/raw/p2-v3"
ARCHIVE_NAMES = (
    "uci-default-of-credit-card-clients.zip",
    "uci-online-shoppers-purchasing-intention.zip",
)


def _run(command: str) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "scripts/p2r_archive_recovery.py", command],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)  # type: ignore[no-any-return]


@pytest.mark.skipif(
    not TERMINAL_STORE.is_dir(),
    reason="ignored immutable P2R v1 terminal store is unavailable",
)
def test_local_v1_terminal_failure_reconciles_with_tracked_audit() -> None:
    payload = _run("verify-failure")
    assert payload["status"] == "p2r_v1_technical_failure_audit_verified"
    assert payload["failure_stage"] == "load_primary"
    assert payload["scientific_disposition_generated"] is False
    assert payload["rerun_forbidden"] is True


@pytest.mark.skipif(
    not all((ARCHIVE_DIRECTORY / name).is_file() for name in ARCHIVE_NAMES),
    reason="ignored SHA-pinned dataset archives are unavailable",
)
def test_local_archives_reproduce_the_outcome_free_readiness_receipt() -> None:
    payload = _run("verify-readiness")
    assert payload["status"] == "p2r_archive_readiness_verified"
    assert payload["all_pinned_archives_reproduced"] is True
    assert payload["sealed_partition_opened"] is False
    assert payload["model_fitted"] is False
    assert payload["predictive_metrics_generated"] is False
    assert payload["execution_attempt_consumed"] is False

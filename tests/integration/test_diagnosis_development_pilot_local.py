"""Cross-process checks for the tracked offline diagnosis development pilot."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.evaluation.development_audit import DevelopmentPilotAuditReceipt

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/diagnosis_development_pilot.py"
TRACKED_RECEIPT = ROOT / "configs/evaluation/diagnosis_development_pilot_receipt.json"


def _run(store: Path, seed: str, command: str = "all") -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = (
        source
        if not environment.get("PYTHONPATH")
        else os.pathsep.join((source, environment["PYTHONPATH"]))
    )
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), command, "--store", str(store)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def test_pilot_is_byte_stable_across_process_hash_seeds(tmp_path: Path) -> None:
    first = _run(tmp_path / "seed-1", "1")
    second = _run(tmp_path / "seed-104729", "104729")

    assert first["status"] == "development_pilot_validated"
    assert second["status"] == "development_pilot_validated"
    assert first["terminal"] == second["terminal"]
    assert first["audit"] == second["audit"]


def test_independent_validation_reproduces_original_receipt(tmp_path: Path) -> None:
    store = tmp_path / "store"
    executed = _run(store, "1")
    validated = _run(store, "209759", "validate")

    assert validated["status"] == "development_pilot_validated"
    assert validated["audit"] == executed["audit"]
    assert "terminal" not in validated


def test_tracked_receipt_matches_a_fresh_independent_run(tmp_path: Path) -> None:
    tracked = DevelopmentPilotAuditReceipt.model_validate_json(
        TRACKED_RECEIPT.read_bytes()
    )
    fresh = _run(tmp_path / "fresh", "104729")

    assert fresh["audit"] == tracked.model_dump(mode="json")

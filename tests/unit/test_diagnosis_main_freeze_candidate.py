from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_diagnosis_main_freeze.py"
MANIFEST = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate.json"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), *extra],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_candidate_integrity_passes_but_main_execution_remains_blocked() -> None:
    completed = _run()
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "pass"
    assert report["readiness_status"] == "blocked"
    assert report["execution_authorized"] is False
    assert "analysis_plan_contains_unresolved_tbd_fields" in report["blocker_codes"]

    ready_gate = _run("--require-ready")
    assert ready_gate.returncode == 2


def test_resigned_policy_drift_fails_cross_artifact_reconciliation(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["frozen_model_policy"]["max_output_tokens"] = 601
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "fail"
    finding = next(item for item in report["findings"] if item["code"] == "model_policy_parity")
    assert finding["status"] == "fail"

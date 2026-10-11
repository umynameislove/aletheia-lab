from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mlflow", reason="requires the pinned provenance optional extra")
pytest.importorskip("in_toto", reason="requires the pinned provenance optional extra")
ROOT = Path(__file__).resolve().parents[2]


def _run(action: str, report: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "src"), environment.get("PYTHONPATH", ""))
    )
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/model_load_provenance.py"),
            action,
            "--root",
            str(ROOT),
            "--report",
            str(report),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_real_sdk_private_report_and_hash_only_replay(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    completed = _run("run", path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["disposition"] == "bounded_capture_finding_no_new_checker_advantage"
    assert result["validation_locked"] is False
    assert result["completed_load_attempts"] == 7
    assert "raw_hex" not in completed.stdout and str(tmp_path) not in completed.stdout
    before = path.read_bytes()
    verified = _run("verify", path, tmp_path)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["verification"] == "pass"
    assert path.read_bytes() == before
    repeated = _run("run", path, tmp_path)
    assert repeated.returncode == 1 and path.read_bytes() == before


def test_cli_rejects_inside_repo_oversized_and_symlink_reports(tmp_path: Path) -> None:
    result = _run("run", ROOT / "forbidden-provenance-report.json", tmp_path)
    assert result.returncode == 1
    assert not (ROOT / "forbidden-provenance-report.json").exists()
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"x" * 2_000_001)
    assert _run("verify", oversized, tmp_path).returncode == 1
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(oversized)
    except OSError:
        pytest.skip("creating symlinks requires OS privileges")
    assert _run("verify", link, tmp_path).returncode == 1

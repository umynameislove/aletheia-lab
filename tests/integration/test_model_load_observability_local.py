from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/model_load_observability.py"


def _run(action: str, path: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), action, "--root", str(ROOT)]
    if path is not None:
        command.extend(("--report", str(path)))
    return subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def test_cli_executes_local_runtime_then_verifies_without_private_stdout(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    executed = _run("run", path)
    assert executed.returncode == 0, executed.stderr or executed.stdout
    assert path.is_file()
    result = json.loads(executed.stdout)
    assert result["episode_count"] == 12 and result["provider_calls"] == 0
    assert "raw_loader_buffers" not in executed.stdout
    assert str(tmp_path) not in executed.stdout
    verified = _run("verify", path)
    assert verified.returncode == 0, verified.stderr or verified.stdout
    assert json.loads(verified.stdout)["verification"] == "pass"
    before = path.read_bytes()
    assert _run("run", path).returncode == 1
    assert path.read_bytes() == before


def test_cli_requires_private_new_destination_and_bounded_valid_report(tmp_path: Path) -> None:
    assert _run("run", ROOT / "generated-report.json").returncode == 1
    assert not (ROOT / "generated-report.json").exists()
    assert _run("verify").returncode == 1
    assert _run("run", tmp_path / "missing" / "report.json").returncode == 1
    invalid = tmp_path / "invalid.json"
    invalid.write_text("PRIVATE_ERROR_NOT_JSON", encoding="utf-8")
    failed = _run("verify", invalid)
    assert failed.returncode == 1 and "PRIVATE_ERROR" not in failed.stdout
    invalid.write_bytes(b"x" * 2_000_001)
    assert _run("verify", invalid).returncode == 1


def test_cli_refuses_symlink_report(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(real)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert _run("verify", linked).returncode == 1

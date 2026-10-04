from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(action: str, path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/model_load_validation.py"),
            action,
            "--root",
            str(ROOT),
            "--plan",
            str(path),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_private_design_lock_replay_no_execution_or_optional_import(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    prepared = _run("prepare", plan, tmp_path)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    result = json.loads(prepared.stdout)
    assert result["planned_slots"] == 48 and result["observation_load_slots"] == 22
    assert result["protocol_locked"] is True and result["execution_ready"] is False
    assert result["provider_calls"] == result["native_loader_entries"] == 0
    assert str(tmp_path) not in prepared.stdout
    before = plan.read_bytes()
    replay = _run("preflight", plan, tmp_path)
    assert replay.returncode == 0 and plan.read_bytes() == before
    assert json.loads(replay.stdout)["plan_sha256"] == result["plan_sha256"]
    repeated = _run("prepare", plan, tmp_path)
    assert repeated.returncode == 1 and plan.read_bytes() == before
    execute = _run("execute", plan, tmp_path)
    assert execute.returncode == 2 and plan.read_bytes() == before


def test_cli_refuses_private_plan_in_repository_without_creating_file(tmp_path: Path) -> None:
    path = ROOT / "forbidden-model-load-validation-plan.json"
    assert not path.exists()
    result = _run("prepare", path, tmp_path)
    assert result.returncode == 1 and not path.exists()
    assert str(ROOT) not in result.stdout


def test_import_never_imports_new_loader_sdks_or_previous_private_outcomes(tmp_path: Path) -> None:
    code = """
import builtins
import sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'onnx', 'onnxruntime', 'skops', 'sklearn', 'in_toto'}:
        raise AssertionError('new backend imported by design-only preflight')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from pathlib import Path
from aletheia_lab.evaluation.model_load_validation import prepare, preflight
root, plan = map(Path, sys.argv[1:])
prepare(root, plan)
assert preflight(root, plan)['validation_outcomes_observed'] is False
"""
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT), str(tmp_path / "guarded.json")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

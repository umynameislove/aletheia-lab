"""Contracts for test profiles and duplicate-free continuous integration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github/workflows/ci.yml"
_PROFILE_SCRIPT = _ROOT / "scripts/run_test_profile.py"


def _workflow() -> dict[object, object]:
    payload = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _triggers(payload: dict[object, object]) -> dict[str, object]:
    # PyYAML 1.1 reads the unquoted GitHub key `on` as boolean true.
    value = payload.get("on", payload.get(True))
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return value


def test_feature_push_does_not_duplicate_pull_request_matrix() -> None:
    triggers = _triggers(_workflow())
    push = triggers.get("push")
    assert push == {"branches": ["main"]}
    assert "pull_request" in triggers


def test_stale_runs_are_cancelled_without_weakening_latest_commit() -> None:
    concurrency = _workflow().get("concurrency")
    assert isinstance(concurrency, dict)
    assert concurrency.get("cancel-in-progress") is True
    assert "pull_request.number" in str(concurrency.get("group", ""))


def test_coverage_runs_once_and_remains_blocking() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    test_job = jobs.get("test")
    assert isinstance(test_job, dict)
    steps = test_job.get("steps")
    assert isinstance(steps, list)
    coverage_steps = [
        step
        for step in steps
        if isinstance(step, dict) and "--cov-fail-under=88" in str(step.get("run", ""))
    ]
    assert len(coverage_steps) == 1
    assert coverage_steps[0].get("if") == "matrix.python-version == '3.11'"
    assert coverage_steps[0].get("continue-on-error") is None


def test_python_312_still_runs_the_complete_unfiltered_suite() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    test_job = jobs.get("test")
    assert isinstance(test_job, dict)
    steps = test_job.get("steps")
    assert isinstance(steps, list)
    compatibility = [
        step
        for step in steps
        if isinstance(step, dict) and step.get("if") == "matrix.python-version == '3.12'"
    ]
    assert len(compatibility) == 1
    run = str(compatibility[0].get("run", ""))
    assert run.startswith("pytest ")
    assert "-m" not in run and "--ignore" not in run


def test_named_profiles_resolve_without_collecting_tests() -> None:
    for profile in ("fast", "project", "research", "full"):
        completed = subprocess.run(
            [sys.executable, str(_PROFILE_SCRIPT), profile, "--show-command"],
            cwd=_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert " -m pytest " in completed.stdout


def test_full_profile_preserves_coverage_floor() -> None:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), "full", "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--cov=aletheia_lab" in completed.stdout
    assert "--cov-fail-under=88" in completed.stdout

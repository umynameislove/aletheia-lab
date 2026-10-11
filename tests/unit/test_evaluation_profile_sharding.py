"""Shard scheduling keeps the complete module census and failure boundaries."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_test_profile.py"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("owned_profile_runner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("count", [2, 3, 4])
def test_shards_are_disjoint_complete_and_keep_every_module_fixture(runner, count):
    original = runner.profile_command("evaluation")
    paths = [item for item in original if item.startswith("tests/")]
    assert len(paths) == len(set(paths))
    partitions = []
    for index in range(count):
        command = runner.profile_command("evaluation", shard_index=index, shard_count=count)
        selected = [item for item in command if item.startswith("tests/")]
        assert selected == paths[index::count]
        assert command[:3] == (sys.executable, "-m", "pytest")
        assert command[-5:] == ("--durations=20", "-n", "2", "--dist=loadscope", "-vv")
        assert not {"-k", "-m", "--ignore", "--ignore-glob"} & set(command[3:])
        assert selected and all((ROOT / item).is_file() for item in selected)
        partitions.extend(selected)
    assert Counter(partitions) == Counter(paths)
    assert runner.profile_command("evaluation") == original


def test_shard_contract_is_in_the_early_official_gate(runner):
    assert "tests/unit/test_evaluation_profile_sharding.py" in runner.profile_command("contract")


@pytest.mark.parametrize("index,count", [(-1, 2), (2, 2), (0, 0), (0, 5), (True, 2), (0, 1.5)])
def test_invalid_shard_stops_before_dispatch(runner, monkeypatch, index, count):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid shard must not execute pytest")

    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="bounded profile shard"):
        runner.run_profile("evaluation", shard_index=index, shard_count=count)


@pytest.mark.parametrize("profile", ["full", "project", "contract", "application"])
def test_non_evaluation_profile_cannot_reduce_the_full_or_boundary_gate(runner, profile):
    with pytest.raises(ValueError, match="only the evaluation"):
        runner.profile_command(profile, shard_count=2)


def test_cli_show_command_and_invalid_input_never_start_a_worker(runner, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "evaluation", "--shard-index", "1", "--shard-count", "2", "--show-command"],
    )
    assert runner.main() == 0
    assert json.loads(capsys.readouterr().out) == list(
        runner.profile_command("evaluation", shard_index=1, shard_count=2)
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "evaluation", "--shard-index", "-1"])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2


@pytest.mark.parametrize("result", [0, 7, "timeout"])
def test_each_shard_keeps_budget_hash_seed_and_failure_exit(runner, monkeypatch, result):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if result == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=result)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(
        runner,
        "_run_windows_evaluation",
        lambda command, environment, timeout: (
            run(command, env=environment, timeout=timeout).returncode
        ),
    )
    observed = runner.run_profile("evaluation", repeat=3, shard_index=1, shard_count=2)
    assert observed == (124 if result == "timeout" else result)
    assert len(calls) == (3 if result == 0 else 1)
    assert all(
        command == runner.profile_command("evaluation", shard_index=1, shard_count=2)
        for command, _ in calls
    )
    assert all(kwargs["timeout"] == runner._evaluation_timeout_seconds() for _, kwargs in calls)
    assert [kwargs["env"]["PYTHONHASHSEED"] for _, kwargs in calls] == [
        str(seed) for seed in runner._REPRODUCIBILITY_HASH_SEEDS[: len(calls)]
    ]


def test_windows_matrix_requires_both_shards_without_budget_or_census_reduction():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["windows-project"]
    assert job["strategy"] == {"fail-fast": False, "matrix": {"shard": [0, 1]}}
    assert job["timeout-minutes"] == 50 and not job.get("continue-on-error", False)
    steps = [
        step for step in job["steps"] if "run_test_profile.py evaluation" in step.get("run", "")
    ]
    assert len(steps) == 1 and not steps[0].get("continue-on-error", False)
    assert steps[0]["run"].strip() == (
        "python scripts/run_test_profile.py evaluation "
        "--shard-index ${{ matrix.shard }} --shard-count 2"
    )

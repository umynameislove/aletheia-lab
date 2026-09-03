"""Contract tests for the bounded, reproducible evaluation test gate."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_SCRIPT = _ROOT / "scripts" / "run_test_profile.py"
_INVENTORY_SCRIPT = _ROOT / "scripts" / "report_dependency_inventory.py"


def _runner_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_profile_runner", _PROFILE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evaluation_command() -> tuple[str, ...]:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), "evaluation", "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, list)
    assert all(isinstance(item, str) for item in payload)
    return tuple(payload)


def test_evaluation_profile_has_required_boundaries_without_deselection() -> None:
    command = _evaluation_command()
    required = {
        "tests/unit/test_evaluation_execution_contracts.py",
        "tests/property/test_evaluation_contract_properties.py",
        "tests/unit/test_context_evaluation_boundary.py",
        "tests/property/test_context_visibility_properties.py",
        "tests/unit/test_model_gateway_runtime.py",
        "tests/integration/test_evaluation_pipeline_reproducibility.py",
        "tests/unit/test_diagnosis_protocol_feasibility.py",
        "tests/unit/test_diagnosis_variant_fairness.py",
        "tests/property/test_diagnosis_freeze_properties.py",
        "tests/integration/test_diagnosis_evaluation_freeze_local.py",
        "tests/unit/test_claim_support_corpus_protocol.py",
        "tests/property/test_claim_support_corpus_protocol_properties.py",
        "tests/integration/test_claim_support_corpus_protocol_local.py",
        "tests/unit/test_claim_support_corpus_materialization.py",
        "tests/property/test_claim_support_corpus_materialization_properties.py",
        "tests/integration/test_claim_support_corpus_readiness_local.py",
        "tests/unit/test_claim_support_corpus_execution.py",
        "tests/integration/test_claim_support_corpus_execution_local.py",
        "tests/unit/test_claim_support_observed_evidence.py",
        "tests/integration/test_claim_support_observed_evidence_local.py",
        "tests/unit/test_claim_support_instrument_validation.py",
        "tests/unit/test_claim_support_human_workflow.py",
        "tests/integration/test_claim_support_human_workflow_local.py",
        "tests/unit/test_leakage_guard.py",
        "tests/unit/test_project_bundle_contract.py",
        "tests/unit/test_evidence_contract_v2.py",
        "tests/unit/test_test_runtime_contract.py",
    }

    assert required <= set(command)
    assert "--durations=20" in command
    assert command[:3] == (sys.executable, "-m", "pytest")
    assert not {"-k", "-m", "--ignore", "--ignore-glob"} & set(command[3:])


@pytest.mark.parametrize(
    "executable",
    (
        "/Users/research owner/đồ án/.venv/bin/python",
        r"C:\\Research Workspace\\.venv\\Scripts\\python.exe",
    ),
)
def test_show_command_round_trips_executable_paths_without_shell_splitting(
    executable: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _runner_module()
    monkeypatch.setattr(runner.sys, "executable", executable)
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [str(_PROFILE_SCRIPT), "evaluation", "--show-command"],
    )

    assert runner.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[:3] == [executable, "-m", "pytest"]
    assert all(isinstance(item, str) for item in payload)


def test_repeated_profile_uses_distinct_hash_seeds_and_five_minute_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner_module()
    calls: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.run_profile("evaluation", repeat=3) == 0

    assert [call["timeout"] for call in calls] == [300, 300, 300]
    seeds = [str(call["env"]["PYTHONHASHSEED"]) for call in calls]
    assert seeds == ["1", "104729", "209759"]
    assert len(set(seeds)) == 3


def test_profile_timeout_is_a_blocking_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _runner_module()

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", raise_timeout)
    assert runner.run_profile("evaluation") == 124
    assert "300-second runtime budget" in capsys.readouterr().err


def test_selected_evaluation_tests_do_not_use_real_sleep() -> None:
    for argument in _evaluation_command():
        path = _ROOT / argument
        if path.suffix != ".py" or not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sleep_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == "sleep"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
            )
        ]
        assert sleep_calls == [], f"real sleep call found in {argument}"


def test_resolved_dependency_inventory_is_canonical_and_self_hashing() -> None:
    first = subprocess.run(
        [sys.executable, str(_INVENTORY_SCRIPT)],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, str(_INVENTORY_SCRIPT)],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert first == second

    payload = json.loads(first)
    inventory_sha256 = payload.pop("inventory_sha256")
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    assert inventory_sha256 == hashlib.sha256(canonical.encode("ascii")).hexdigest()
    names = [item["name"] for item in payload["distributions"]]
    assert names == sorted(set(names))
    assert len(payload["pyproject_sha256"]) == 64

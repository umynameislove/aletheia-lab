"""Contracts for test profiles and duplicate-free continuous integration."""

from __future__ import annotations

import json
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
        if isinstance(step, dict)
        and step.get("if") == "matrix.python-version == '3.12'"
        and str(step.get("run", "")).startswith("pytest ")
    ]
    assert len(compatibility) == 1
    run = str(compatibility[0].get("run", ""))
    assert run.startswith("pytest ")
    assert "-m" not in run and "--ignore" not in run


def test_windows_project_boundary_is_a_blocking_ci_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    windows_job = jobs.get("windows-project")
    assert isinstance(windows_job, dict)
    assert windows_job.get("runs-on") == "windows-latest"
    steps = windows_job.get("steps")
    assert isinstance(steps, list)
    boundary_steps = [
        step
        for step in steps
        if isinstance(step, dict) and "run_test_profile.py project" in str(step.get("run", ""))
    ]
    assert len(boundary_steps) == 1
    assert boundary_steps[0].get("continue-on-error") is None


def test_evaluation_reproducibility_and_compatibility_are_distinct_gates() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    test_job = jobs.get("test")
    assert isinstance(test_job, dict)
    steps = test_job.get("steps")
    assert isinstance(steps, list)
    evaluation_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and "run_test_profile.py evaluation --repeat 3" in str(step.get("run", ""))
    ]
    assert len(evaluation_steps) == 1
    assert evaluation_steps[0].get("if") == "matrix.python-version == '3.11'"
    assert evaluation_steps[0].get("continue-on-error") is None
    compatibility_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and "run_test_profile.py evaluation" in str(step.get("run", ""))
        and "--repeat" not in str(step.get("run", ""))
    ]
    assert len(compatibility_steps) == 1
    assert compatibility_steps[0].get("if") == "matrix.python-version == '3.12'"
    assert compatibility_steps[0].get("continue-on-error") is None


def test_evaluation_mutation_audit_is_a_blocking_quality_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    quality_job = jobs.get("quality")
    assert isinstance(quality_job, dict)
    steps = quality_job.get("steps")
    assert isinstance(steps, list)
    mutation_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and "run_evaluation_mutation_audit.py" in str(step.get("run", ""))
    ]
    assert len(mutation_steps) == 1
    assert mutation_steps[0].get("continue-on-error") is None


def test_maintainability_audit_is_a_blocking_quality_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    quality_job = jobs.get("quality")
    assert isinstance(quality_job, dict)
    steps = quality_job.get("steps")
    assert isinstance(steps, list)
    matches = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("run", "")) == "python scripts/check_maintainability.py"
    ]
    assert len(matches) == 1
    assert matches[0].get("continue-on-error") is None


def test_windows_evaluation_profile_is_a_blocking_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    windows_job = jobs.get("windows-project")
    assert isinstance(windows_job, dict)
    steps = windows_job.get("steps")
    assert isinstance(steps, list)
    evaluation_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and "run_test_profile.py evaluation" in str(step.get("run", ""))
    ]
    assert len(evaluation_steps) == 1
    assert evaluation_steps[0].get("continue-on-error") is None


def test_windows_prepares_pinned_dataset_before_evaluation() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    windows_job = jobs.get("windows-project")
    assert isinstance(windows_job, dict)
    steps = windows_job.get("steps")
    assert isinstance(steps, list)
    commands = [
        str(step.get("run", ""))
        for step in steps
        if isinstance(step, dict)
    ]
    dataset_index = commands.index(
        "python scripts/download_dataset.py all --config configs/project.yaml"
    )
    evaluation_index = commands.index("python scripts/run_test_profile.py evaluation")

    assert dataset_index < evaluation_index


def test_windows_publication_profile_is_a_blocking_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    windows_job = jobs.get("windows-project")
    assert isinstance(windows_job, dict)
    steps = windows_job.get("steps")
    assert isinstance(steps, list)
    matches = [
        step
        for step in steps
        if isinstance(step, dict)
        and "run_test_profile.py windows-publication" in str(step.get("run", ""))
    ]
    assert len(matches) == 1
    assert matches[0].get("continue-on-error") is None


def test_pip_caches_bind_the_dependency_input() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    cached_setup_steps = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps")
        assert isinstance(steps, list)
        for step in steps:
            if not isinstance(step, dict):
                continue
            inputs = step.get("with")
            if isinstance(inputs, dict) and inputs.get("cache") == "pip":
                cached_setup_steps.append(inputs)
    assert len(cached_setup_steps) == 2
    assert all(
        inputs.get("cache-dependency-path") == "pyproject.toml"
        for inputs in cached_setup_steps
    )


def test_named_profiles_resolve_without_collecting_tests() -> None:
    for profile in (
        "contract",
        "fast",
        "project",
        "research",
        "evaluation",
        "windows-publication",
        "full",
    ):
        completed = subprocess.run(
            [sys.executable, str(_PROFILE_SCRIPT), profile, "--show-command"],
            cwd=_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        command = json.loads(completed.stdout)
        assert command[:3] == [sys.executable, "-m", "pytest"]


def test_evaluation_profile_includes_the_production_gateway_contract() -> None:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), "evaluation", "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = json.loads(completed.stdout)
    assert "tests/unit/test_model_gateway_runtime.py" in command
    assert "tests/unit/test_openai_gateway_adapter.py" in command


def test_evaluation_profile_includes_claim_corpus_and_human_validation_contracts() -> None:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), "evaluation", "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = json.loads(completed.stdout)
    assert "tests/unit/test_claim_support_corpus_protocol.py" in command
    assert "tests/property/test_claim_support_corpus_protocol_properties.py" in command
    assert "tests/integration/test_claim_support_corpus_protocol_local.py" in command
    assert "tests/unit/test_claim_support_corpus_materialization.py" in command
    assert "tests/property/test_claim_support_corpus_materialization_properties.py" in command
    assert "tests/integration/test_claim_support_corpus_readiness_local.py" in command
    assert "tests/unit/test_claim_support_observed_evidence.py" in command
    assert "tests/integration/test_claim_support_observed_evidence_local.py" in command
    assert "tests/unit/test_claim_support_instrument_validation.py" in command
    assert "tests/unit/test_claim_support_human_workflow.py" in command
    assert "tests/integration/test_claim_support_human_workflow_local.py" in command


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


def test_publication_profile_resolves_the_shared_core_and_all_immutable_stores() -> None:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), "windows-publication", "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = json.loads(completed.stdout)
    assert "tests/unit/test_filesystem_publication.py" in command
    assert "tests/unit/test_evaluation_attempt_store.py" in command
    assert "tests/unit/test_diagnosis_development_runner.py" in command
    assert "tests/unit/test_project_persistence.py" in command

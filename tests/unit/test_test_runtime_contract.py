"""Contracts for test profiles and duplicate-free continuous integration."""

from __future__ import annotations

import ast
import json
import os
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github/workflows/ci.yml"
_PROFILE_SCRIPT = _ROOT / "scripts/run_test_profile.py"
_WINDOWS_EVALUATION_COMMAND = (
    "python scripts/run_test_profile.py evaluation "
    "--shard-index ${{ matrix.shard }} --shard-count 2"
)


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


def test_full_coverage_records_child_processes_without_source_exclusions() -> None:
    from coverage.results import should_fail_under

    configuration = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    coverage = configuration["tool"]["coverage"]
    assert set(coverage["run"]["patch"]) == {"subprocess", "_exit"}
    assert not coverage["run"].get("omit") and not coverage["run"].get("include")
    assert not coverage["report"].get("omit")
    assert not coverage["report"].get("ignore_errors", False)
    assert coverage["report"]["precision"] == 2
    assert should_fail_under(87.83, 88, coverage["report"]["precision"])
    dependencies = configuration["project"]["optional-dependencies"]["dev"]
    assert "pytest-cov>=7.1" in dependencies and "coverage>=7.10.6" in dependencies


@pytest.mark.parametrize("module", ["cache_lifecycle_analysis", "cache_lifecycle_study"])
def test_coverage_merges_only_identical_replay_copies_without_losing_lines(
    tmp_path: Path, module: str
) -> None:
    from coverage import Coverage, CoverageData

    relative = Path(f"src/aletheia_lab/evaluation/{module}.py")
    original = (_ROOT / relative).resolve()
    alias = tmp_path / "test_cli_archived_replay_prese0/snapshot" / relative
    alias.parent.mkdir(parents=True)
    alias.write_bytes(original.read_bytes())
    assert alias.read_bytes() == original.read_bytes()
    unrelated = tmp_path / "unrelated" / relative
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"# A different source, not an alias.\n")
    data = CoverageData(basename=str(tmp_path / "measured.owned"))
    data.add_lines({str(original): {1, 10}, str(alias): {10, 11}, str(unrelated): {1}})
    data.write()
    measured = Coverage(
        config_file=str(_ROOT / "pyproject.toml"), data_file=str(tmp_path / "measured")
    )
    measured.combine(data_paths=[str(tmp_path)], strict=True)
    combined = measured.get_data()
    assert set(combined.measured_files()) == {str(original), str(unrelated)}
    assert set(combined.lines(str(original)) or []) == {1, 10, 11}
    assert combined.lines(str(unrelated)) == [1]


def test_abrupt_child_exit_preserves_real_measurements_and_exit_code(tmp_path: Path) -> None:
    from coverage import Coverage

    probe = tmp_path / "owned_probe.py"
    probe.write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(__file__).with_suffix('.ack').write_bytes(b'owned acknowledgement')\n"
        "os._exit(19)\nraise AssertionError('unreachable')\n",
        encoding="utf-8",
    )
    config = tmp_path / "coverage.ini"
    config.write_text("[run]\npatch = _exit\ndata_file = .coverage-owned\n", encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_", "PYTEST_"))
    }
    completed = subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--rcfile={config}", str(probe)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 19 and completed.stdout == completed.stderr == b""
    assert probe.with_suffix(".ack").read_bytes() == b"owned acknowledgement"
    measured = Coverage(config_file=str(config), data_file=str(tmp_path / ".coverage-owned"))
    measured.load()
    assert measured.get_data().lines(str(probe)) == [1, 2, 3, 4]


def test_dev_install_keeps_native_regressions_on_the_pinned_sklearn_runtime() -> None:
    configuration = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = configuration["project"]["optional-dependencies"]["dev"]
    assert [item for item in dependencies if item.startswith("scikit-learn")] == [
        "scikit-learn==1.9.0"
    ]
    for name in ("test", "windows-project"):
        installs = [
            step for step in _workflow()["jobs"][name]["steps"] if step.get("name") == "Install"
        ]
        assert len(installs) == 1 and '".[dev,provenance]"' in installs[0]["run"]


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


def test_windows_job_budget_cannot_truncate_the_final_publication_gate() -> None:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    windows_job = jobs.get("windows-project")
    assert isinstance(windows_job, dict)

    # Bind the aggregate limit to the runner's actual Windows budget, not just
    # a second copy of a duration that could silently become inconsistent.
    runner = ast.parse(_PROFILE_SCRIPT.read_text(encoding="utf-8"))
    evaluation_budget = next(
        ast.literal_eval(node.value)
        for node in runner.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_WINDOWS_EVALUATION_TIMEOUT_SECONDS"
    )
    assert evaluation_budget == 2100
    assert windows_job.get("timeout-minutes") == 50
    assert windows_job["timeout-minutes"] * 60 - evaluation_budget >= 15 * 60

    steps = windows_job.get("steps")
    assert isinstance(steps, list)
    commands = [str(step.get("run", "")) for step in steps if isinstance(step, dict)]
    assert commands.index(_WINDOWS_EVALUATION_COMMAND) < commands.index(
        "python scripts/run_test_profile.py windows-publication"
    )


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
        if isinstance(step, dict) and "run_evaluation_mutation_audit.py" in str(step.get("run", ""))
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
        if isinstance(step, dict) and "run_test_profile.py evaluation" in str(step.get("run", ""))
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
    commands = [str(step.get("run", "")) for step in steps if isinstance(step, dict)]
    dataset_index = commands.index(
        "python scripts/download_dataset.py all --config configs/project.yaml"
    )
    evaluation_index = commands.index(_WINDOWS_EVALUATION_COMMAND)

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
        inputs.get("cache-dependency-path") == "pyproject.toml" for inputs in cached_setup_steps
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
    assert "tests/unit/test_claim_support_execution_reconciliation.py" in command
    assert "tests/unit/test_claim_support_pool_construction.py" in command
    assert "tests/unit/test_claim_corpus_recovery_closeout.py" in command
    assert "tests/unit/test_claim_relation_execution.py" in command
    assert "tests/unit/test_claim_relation_recovery.py" in command
    assert "tests/unit/test_claim_support_normalization_recovery.py" in command
    assert "tests/integration/test_claim_support_normalization_recovery_local.py" in command
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
    command = json.loads(completed.stdout)
    assert "--cov=aletheia_lab" in command
    assert "--cov-fail-under=88" in command
    assert command[-3:] == ["-n", "2", "--dist=loadscope"]
    assert not {"-k", "-m", "--ignore", "--ignore-glob"} & set(command[3:])


@pytest.mark.parametrize("profile", ["evaluation", "windows-publication"])
def test_study_replay_and_artifact_guards_are_selected_by_blocking_profiles(profile) -> None:
    completed = subprocess.run(
        [sys.executable, str(_PROFILE_SCRIPT), profile, "--show-command"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = json.loads(completed.stdout)
    assert "tests/unit/test_active_diagnosis_replay.py" in command
    assert "tests/integration/test_study_artifacts_local.py" in command


def test_profile_subprocesses_import_from_the_checked_checkout(monkeypatch) -> None:
    runner = runpy.run_path(str(_PROFILE_SCRIPT))
    captured = []

    def run(command, *, cwd, check, env, timeout):
        from types import SimpleNamespace

        captured.append((command, cwd, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("PYTHONPATH", "SYNTHETIC_OTHER_CHECKOUT")
    monkeypatch.setattr(runner["subprocess"], "run", run)
    assert runner["run_profile"]("contract") == 0
    assert captured[0][1] == _ROOT
    assert captured[0][2]["PYTHONPATH"].split(os.pathsep)[0] == str(_ROOT / "src")
    assert "SYNTHETIC_OTHER_CHECKOUT" in captured[0][2]["PYTHONPATH"]
    assert os.environ["PYTHONPATH"] == "SYNTHETIC_OTHER_CHECKOUT"


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

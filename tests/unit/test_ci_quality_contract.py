"""CI configuration quality contract tests.

These tests verify that .github/workflows/ci.yml satisfies the published
quality contract:

  - Python matrix uses exactly ["3.11", "3.12"] and the matrix variable
    (not a hard-coded version) inside the matrix job;
  - mypy and coverage steps do not carry ``continue-on-error``;
  - ``--cov-fail-under`` preserves the approved 88 percent baseline;
  - ``pandas-stubs`` is declared in dev optional-dependencies so the CI
    mypy command works without ``ignore_missing_imports``;
  - no shell escape valve (``|| true``) appears in any step.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_CI_YAML = _ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_MINIMUM_COVERAGE_BASELINE = 88.0


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _string_mapping(value: object, *, label: str) -> dict[str, object]:
    """Validate a YAML node before exposing it to typed contract checks."""

    assert isinstance(value, dict), f"{label} must be a mapping"
    assert all(isinstance(key, str) for key in value), f"{label} keys must be strings"
    return cast(dict[str, object], value)


def _steps(job: Mapping[str, object]) -> list[dict[str, object]]:
    """Return validated step mappings for one CI job."""

    raw_steps = job.get("steps")
    assert isinstance(raw_steps, list), "CI job steps must be a list"
    return [_string_mapping(step, label=f"CI step {index}") for index, step in enumerate(raw_steps)]


@pytest.fixture(scope="module")
def jobs() -> dict[str, object]:
    payload: object = yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "ci.yml root must be a mapping"
    return _string_mapping(payload.get("jobs"), label="ci.yml jobs")


@pytest.fixture(scope="module")
def test_job(jobs: Mapping[str, object]) -> dict[str, object]:
    return _string_mapping(jobs.get("test"), label="ci.yml test job")


@pytest.fixture(scope="module")
def all_step_runs(jobs: Mapping[str, object]) -> list[str]:
    """Collect every 'run:' string from every job in ci.yml."""
    runs: list[str] = []
    for job_name, raw_job in jobs.items():
        job = _string_mapping(raw_job, label=f"ci.yml job {job_name!r}")
        for step in _steps(job):
            if "run" in step:
                runs.append(str(step["run"]))
    return runs


# ---------------------------------------------------------------------------
# Matrix correctness
# ---------------------------------------------------------------------------


def test_matrix_python_versions(test_job: Mapping[str, object]) -> None:
    """Matrix must declare exactly Python 3.11 and 3.12 — no more, no less."""
    strategy = _string_mapping(test_job.get("strategy"), label="test strategy")
    matrix = _string_mapping(strategy.get("matrix"), label="test matrix")
    versions = matrix.get("python-version")
    assert versions == ["3.11", "3.12"], (
        f"expected matrix python-version == ['3.11', '3.12'], got {versions!r}"
    )


def test_setup_python_uses_matrix_variable(test_job: Mapping[str, object]) -> None:
    """setup-python in the matrix job must use ${{ matrix.python-version }},
    not a hard-coded version string."""
    for step in _steps(test_job):
        if "actions/setup-python" in str(step.get("uses", "")):
            inputs = _string_mapping(step.get("with"), label="setup-python inputs")
            pv = str(inputs.get("python-version", ""))
            assert "${{ matrix.python-version }}" in pv, (
                f"setup-python must use ${{{{ matrix.python-version }}}}, got {pv!r}"
            )
            return
    pytest.fail("No actions/setup-python step found in the 'test' job")


# ---------------------------------------------------------------------------
# No continue-on-error on critical steps
# ---------------------------------------------------------------------------


def test_mypy_step_has_no_continue_on_error(test_job: Mapping[str, object]) -> None:
    """The mypy step in the matrix job must block the build on failure."""
    for step in _steps(test_job):
        if "mypy" in str(step.get("run", "")):
            assert not step.get("continue-on-error", False), (
                "mypy step must not set continue-on-error"
            )
            return


def test_coverage_step_has_no_continue_on_error(test_job: Mapping[str, object]) -> None:
    """The coverage step in the matrix job must block the build on failure."""
    for step in _steps(test_job):
        if "--cov-fail-under" in str(step.get("run", "")):
            assert not step.get("continue-on-error", False), (
                "coverage step must not set continue-on-error"
            )
            return


# ---------------------------------------------------------------------------
# Coverage threshold
# ---------------------------------------------------------------------------


def test_cov_fail_under_preserves_baseline(test_job: Mapping[str, object]) -> None:
    """The blocking threshold cannot be weakened below the approved baseline."""
    for step in _steps(test_job):
        run = str(step.get("run", ""))
        if "--cov-fail-under" in run:
            match = re.search(r"--cov-fail-under[=\s]+(\d+(?:\.\d+)?)", run)
            assert match, f"could not parse a numeric value from --cov-fail-under in: {run!r}"
            threshold = float(match.group(1))
            assert threshold >= _MINIMUM_COVERAGE_BASELINE, (
                f"--cov-fail-under must preserve the 88 percent baseline, got {threshold}"
            )
            return
    pytest.fail(
        "No step containing '--cov-fail-under' found in the 'test' job. "
        "Add a blocking coverage threshold to ci.yml."
    )


# ---------------------------------------------------------------------------
# Shell escape valves
# ---------------------------------------------------------------------------


def test_no_or_true_in_any_step(all_step_runs: list[str]) -> None:
    """No step in any job may use '|| true' to swallow a non-zero exit code."""
    offenders = [r for r in all_step_runs if "|| true" in r]
    assert not offenders, f"'|| true' found in {len(offenders)} step(s):\n" + "\n".join(
        f"  {r!r}" for r in offenders
    )


# ---------------------------------------------------------------------------
# Dependency completeness
# ---------------------------------------------------------------------------


def test_pandas_stubs_declared_in_dev_deps() -> None:
    """pandas-stubs must appear in [project.optional-dependencies.dev] so the
    CI mypy step can resolve pandas types without broad ignore_missing_imports."""
    with open(_PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    dev_deps: list[str] = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    assert any("pandas-stubs" in dep for dep in dev_deps), (
        "pandas-stubs is missing from [project.optional-dependencies.dev] in pyproject.toml"
    )


# ---------------------------------------------------------------------------
# Expanded mypy scope contract
# ---------------------------------------------------------------------------

_MYPY_EXPANDED_MODULES: tuple[str, ...] = (
    "src/aletheia_lab/baseline",
    "src/aletheia_lab/data",
    "src/aletheia_lab/benchmark",
    "src/aletheia_lab/context",
    "src/aletheia_lab/diagnosis",
    "src/aletheia_lab/evidence",
    "src/aletheia_lab/evaluation",
    "src/aletheia_lab/model_gateway",
    "src/aletheia_lab/reporting",
    "src/aletheia_lab/cli.py",
    "src/aletheia_lab/config.py",
)


def _mypy_run(test_job: Mapping[str, object]) -> str:
    """Return the 'run' string of the mypy step; fail the test if missing."""
    for step in _steps(test_job):
        run = str(step.get("run", ""))
        if "mypy" in run:
            return run
    pytest.fail("No mypy step found in the 'test' job of ci.yml")


def test_mypy_uses_strict_flag(test_job: Mapping[str, object]) -> None:
    """The CI mypy step must pass --strict; widening to permissive is not allowed."""
    run = _mypy_run(test_job)
    assert "--strict" in run, f"--strict flag missing from mypy step: {run!r}"


def test_mypy_preserves_original_data_baseline_scope(
    test_job: Mapping[str, object],
) -> None:
    """The original data + baseline scope must not be silently dropped from CI."""
    run = _mypy_run(test_job)
    for path in ("src/aletheia_lab/data", "src/aletheia_lab/baseline"):
        assert path in run, f"Original mypy scope {path!r} must still be present in the CI step."


def test_mypy_includes_expanded_non_p2_scope(test_job: Mapping[str, object]) -> None:
    """All non-P2 aletheia_lab modules must appear in the blocking CI mypy step."""
    run = _mypy_run(test_job)
    missing = [m for m in _MYPY_EXPANDED_MODULES if m not in run]
    assert not missing, (
        "The following required modules are absent from the CI mypy step:\n"
        + "\n".join(f"  {m}" for m in missing)
    )


def test_mypy_excludes_benchmark_p2(test_job: Mapping[str, object]) -> None:
    """benchmark/p2 must be explicitly excluded; type debt in P2 is tracked separately."""
    run = _mypy_run(test_job)
    assert "--exclude" in run and "benchmark/p2" in run, (
        "The CI mypy step must explicitly --exclude src/aletheia_lab/benchmark/p2. "
        f"Current run string: {run!r}"
    )


_MYPY_BROAD_SUPPRESS_FLAGS: tuple[str, ...] = (
    "--ignore-missing-imports",
    "--no-strict",
    "--allow-untyped-defs",
    "--allow-untyped-calls",
    "--disable-error-code",
    "--no-error-summary",
)


def test_mypy_has_no_broad_suppress_flags(test_job: Mapping[str, object]) -> None:
    """The CI mypy step must not carry broad suppression flags that silently weaken strict mode."""
    run = _mypy_run(test_job)
    offenders = [f for f in _MYPY_BROAD_SUPPRESS_FLAGS if f in run]
    assert not offenders, (
        f"Broad suppression flags found in mypy step: {offenders!r}. "
        "These would silently swallow type errors without raising CI failure."
    )

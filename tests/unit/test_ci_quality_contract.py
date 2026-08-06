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
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_CI_YAML = _ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_MINIMUM_COVERAGE_BASELINE = 88.0


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def test_job(ci: dict) -> dict:  # type: ignore[type-arg]
    job = ci.get("jobs", {}).get("test")
    assert job is not None, "ci.yml must have a 'test' job"
    return job  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def all_step_runs(ci: dict) -> list[str]:  # type: ignore[type-arg]
    """Collect every 'run:' string from every job in ci.yml."""
    runs: list[str] = []
    for job in ci.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                runs.append(str(step["run"]))
    return runs


# ---------------------------------------------------------------------------
# Matrix correctness
# ---------------------------------------------------------------------------


def test_matrix_python_versions(test_job: dict) -> None:  # type: ignore[type-arg]
    """Matrix must declare exactly Python 3.11 and 3.12 — no more, no less."""
    versions = test_job["strategy"]["matrix"]["python-version"]
    assert versions == ["3.11", "3.12"], (
        f"expected matrix python-version == ['3.11', '3.12'], got {versions!r}"
    )


def test_setup_python_uses_matrix_variable(test_job: dict) -> None:  # type: ignore[type-arg]
    """setup-python in the matrix job must use ${{ matrix.python-version }},
    not a hard-coded version string."""
    for step in test_job.get("steps", []):
        if "actions/setup-python" in step.get("uses", ""):
            pv = str(step.get("with", {}).get("python-version", ""))
            assert "${{ matrix.python-version }}" in pv, (
                f"setup-python must use ${{{{ matrix.python-version }}}}, got {pv!r}"
            )
            return
    pytest.fail("No actions/setup-python step found in the 'test' job")


# ---------------------------------------------------------------------------
# No continue-on-error on critical steps
# ---------------------------------------------------------------------------


def test_mypy_step_has_no_continue_on_error(test_job: dict) -> None:  # type: ignore[type-arg]
    """The mypy step in the matrix job must block the build on failure."""
    for step in test_job.get("steps", []):
        if "mypy" in step.get("run", ""):
            assert not step.get("continue-on-error", False), (
                "mypy step must not set continue-on-error"
            )
            return


def test_coverage_step_has_no_continue_on_error(test_job: dict) -> None:  # type: ignore[type-arg]
    """The coverage step in the matrix job must block the build on failure."""
    for step in test_job.get("steps", []):
        if "--cov-fail-under" in step.get("run", ""):
            assert not step.get("continue-on-error", False), (
                "coverage step must not set continue-on-error"
            )
            return


# ---------------------------------------------------------------------------
# Coverage threshold
# ---------------------------------------------------------------------------


def test_cov_fail_under_preserves_baseline(test_job: dict) -> None:  # type: ignore[type-arg]
    """The blocking threshold cannot be weakened below the approved baseline."""
    for step in test_job.get("steps", []):
        run = step.get("run", "")
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

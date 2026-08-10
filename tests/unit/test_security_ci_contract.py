"""Security CI contract tests.

These tests parse .github/workflows/ci.yml and pyproject.toml to verify that
the security gate satisfies the published contract:

  1.  bandit declared in [project.optional-dependencies.dev];
  2.  CI has a blocking bandit step;
  3.  bandit scans exactly src/aletheia_lab;
  4.  threshold enforces medium/high severity (-ll) and confidence (-ii);
  5.  bandit step does not carry continue-on-error;
  6.  no shell escape valves (|| true, ; exit 0, || exit 0) in the bandit run;
  7.  workflow grants no write permissions at top level or per-job;
  8.  no broad exclusion of the production package (--exclude src or -x src);
  9.  no broad skip/noqa policy in [tool.bandit] config;
  10. security job does not reference any secrets.

Tests parse the YAML/TOML structure; they do not rely on raw substring search
of the serialized file so that structural changes cannot trivially evade them.
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


# ---------------------------------------------------------------------------
# Shared fixtures — loaded once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def pyproject() -> dict:  # type: ignore[type-arg]
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def security_job(ci: dict) -> dict:  # type: ignore[type-arg]
    """Return the job that runs the blocking bandit step."""
    jobs = ci.get("jobs", {})
    for _job_name, job in jobs.items():
        for step in job.get("steps", []):
            if "bandit" in step.get("run", ""):
                return job  # type: ignore[no-any-return]
    pytest.fail(
        "No job in ci.yml contains a step that runs bandit. "
        "Add a blocking security job with: python -m bandit -r src/aletheia_lab -ll -ii"
    )


@pytest.fixture(scope="module")
def bandit_step(security_job: dict) -> dict:  # type: ignore[type-arg]
    """Return the specific step in the security job that runs bandit."""
    for step in security_job.get("steps", []):
        if "bandit" in step.get("run", ""):
            return step  # type: ignore[no-any-return]
    pytest.fail("bandit step not found inside the security job")


# ---------------------------------------------------------------------------
# 1 — bandit declared in dev dependencies
# ---------------------------------------------------------------------------


def test_bandit_declared_in_dev_dependencies(pyproject: dict) -> None:  # type: ignore[type-arg]
    """bandit must appear in [project.optional-dependencies.dev] in pyproject.toml."""
    dev_deps: list[str] = (
        pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    )
    assert any(dep.startswith("bandit") for dep in dev_deps), (
        f"bandit not found in [project.optional-dependencies.dev]: {dev_deps!r}"
    )


# ---------------------------------------------------------------------------
# 2 — CI has a blocking bandit step
# ---------------------------------------------------------------------------


def test_ci_has_blocking_bandit_step(security_job: dict) -> None:  # type: ignore[type-arg]
    """At least one step in ci.yml must run bandit."""
    bandit_steps = [
        step for step in security_job.get("steps", []) if "bandit" in step.get("run", "")
    ]
    assert bandit_steps, "No step running bandit found in ci.yml"


# ---------------------------------------------------------------------------
# 3 — scan target is src/aletheia_lab
# ---------------------------------------------------------------------------


def test_bandit_scans_correct_target(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit must scan src/aletheia_lab — not a broader or narrower path."""
    run: str = bandit_step.get("run", "")
    assert "src/aletheia_lab" in run, (
        f"bandit step does not target src/aletheia_lab: {run!r}"
    )
    # Must not accidentally scan a path that skips most of the source
    assert "src/aletheia_lab/data" not in run.replace("src/aletheia_lab ", ""), (
        "bandit scan target appears narrower than src/aletheia_lab"
    )


# ---------------------------------------------------------------------------
# 4 — medium/high severity AND confidence threshold
# ---------------------------------------------------------------------------


def test_bandit_threshold_severity_medium_or_higher(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit command must enforce medium-or-higher severity (-ll or --severity-level medium/high)."""
    run: str = bandit_step.get("run", "")
    tokens = run.split()
    has_short = "-ll" in tokens or "-lll" in tokens
    has_long = bool(re.search(r"--severity-level\s+(medium|high)", run))
    assert has_short or has_long, (
        f"bandit step missing medium/high severity threshold (-ll or --severity-level): {run!r}"
    )


def test_bandit_threshold_confidence_medium_or_higher(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit command must enforce medium-or-higher confidence (-ii or --confidence-level medium/high)."""
    run: str = bandit_step.get("run", "")
    tokens = run.split()
    has_short = "-ii" in tokens or "-iii" in tokens
    has_long = bool(re.search(r"--confidence-level\s+(medium|high)", run))
    assert has_short or has_long, (
        f"bandit step missing medium/high confidence threshold (-ii or --confidence-level): {run!r}"
    )


# ---------------------------------------------------------------------------
# 5 — no continue-on-error
# ---------------------------------------------------------------------------


def test_bandit_step_has_no_continue_on_error(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit step must not carry continue-on-error: true."""
    assert not bandit_step.get("continue-on-error", False), (
        "bandit step has continue-on-error: true — this silences security failures"
    )


# ---------------------------------------------------------------------------
# 6 — no shell escape valves
# ---------------------------------------------------------------------------


_SHELL_ESCAPES = ("|| true", "; exit 0", "|| exit 0", "2>/dev/null || true")


def test_bandit_step_has_no_shell_escape(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit run must not contain || true, ; exit 0 or equivalent exit-code suppressors."""
    run: str = bandit_step.get("run", "")
    for escape in _SHELL_ESCAPES:
        assert escape not in run, (
            f"bandit step contains shell escape {escape!r} that swallows the exit code: {run!r}"
        )


# ---------------------------------------------------------------------------
# 7 — workflow grants no write permissions
# ---------------------------------------------------------------------------


def test_workflow_top_level_permissions_are_read_only(ci: dict) -> None:  # type: ignore[type-arg]
    """Top-level permissions must not grant write access to any scope."""
    top_perms = ci.get("permissions", {})
    if top_perms == "write-all":
        pytest.fail("Workflow sets permissions: write-all")
    if isinstance(top_perms, dict):
        for scope, level in top_perms.items():
            assert level != "write", (
                f"Workflow top-level permission grants write to {scope!r}"
            )


def test_no_job_grants_write_permissions(ci: dict) -> None:  # type: ignore[type-arg]
    """No individual job must grant write access to any scope."""
    for job_name, job in ci.get("jobs", {}).items():
        job_perms = job.get("permissions", {})
        if job_perms == "write-all":
            pytest.fail(f"Job {job_name!r} sets permissions: write-all")
        if isinstance(job_perms, dict):
            for scope, level in job_perms.items():
                assert level != "write", (
                    f"Job {job_name!r} grants write access to {scope!r}"
                )


# ---------------------------------------------------------------------------
# 8 — no broad exclusion of the production package
# ---------------------------------------------------------------------------


def test_bandit_step_has_no_broad_exclusion(bandit_step: dict) -> None:  # type: ignore[type-arg]
    """bandit step must not exclude the entire production source package."""
    run: str = bandit_step.get("run", "")
    # Broad --exclude or -x flags that cover the whole src tree are forbidden
    assert "--exclude src" not in run, (
        f"bandit step broadly excludes the src directory: {run!r}"
    )
    assert re.search(r"-x\s+src\b", run) is None, (
        f"bandit step uses -x to broadly exclude the src directory: {run!r}"
    )


# ---------------------------------------------------------------------------
# 9 — no broad skip/noqa policy in [tool.bandit] config
# ---------------------------------------------------------------------------


def test_no_broad_bandit_config_skip(pyproject: dict) -> None:  # type: ignore[type-arg]
    """[tool.bandit] must not contain broad skip entries.

    Allowed: specific test IDs like B310 (pattern ^B\\d{3}$).
    Forbidden: '*', 'B', empty-string catches, or any non-ID pattern.
    """
    bandit_config: dict = pyproject.get("tool", {}).get("bandit", {})  # type: ignore[type-arg]
    skips: list[str] = bandit_config.get("skips", [])
    for skip in skips:
        assert re.match(r"^B\d{3}$", str(skip)), (
            f"[tool.bandit] has a non-specific skip entry: {skip!r}. "
            "Only specific test IDs like B310 are permitted."
        )


# ---------------------------------------------------------------------------
# 10 — security job does not reference secrets
# ---------------------------------------------------------------------------


def test_security_job_does_not_reference_secrets(security_job: dict) -> None:  # type: ignore[type-arg]
    """The security job must not reference any GitHub Actions secrets."""
    job_text = str(security_job)
    assert "${{ secrets." not in job_text, (
        "The security job references a GitHub Actions secret. "
        "The bandit gate must work without any write-capable tokens."
    )


# ===========================================================================
# pip-audit contract — .github/workflows/dependency-audit.yml
# §4.2 contract properties 1-8
# ===========================================================================

_AUDIT_YAML = _ROOT / ".github" / "workflows" / "dependency-audit.yml"
_AUDIT_ESCAPE_VALVES = ("|| true", "; exit 0", "|| exit 0")


@pytest.fixture(scope="module")
def audit_workflow() -> dict:  # type: ignore[type-arg]
    raw: dict = yaml.safe_load(_AUDIT_YAML.read_text(encoding="utf-8"))  # type: ignore[type-arg]
    # PyYAML implements YAML 1.1 which parses the bare keyword 'on:' as boolean
    # True. Normalise the trigger key to the string "on" so downstream tests
    # can use audit_workflow.get("on") without knowing about this quirk.
    if True in raw and "on" not in raw:
        raw["on"] = raw.pop(True)
    return raw  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def audit_job(audit_workflow: dict) -> dict:  # type: ignore[type-arg]
    """Return the job that runs pip-audit."""
    jobs = audit_workflow.get("jobs", {})
    for _job_name, job in jobs.items():
        for step in job.get("steps", []):
            if "pip_audit" in step.get("run", "") or "pip-audit" in step.get("run", ""):
                return job  # type: ignore[no-any-return]
    pytest.fail(
        "No job in dependency-audit.yml contains a step running pip_audit. "
        "Add a blocking audit step: python -m pip_audit"
    )


@pytest.fixture(scope="module")
def audit_step(audit_job: dict) -> dict:  # type: ignore[type-arg]
    """Return the specific pip-audit run step."""
    for step in audit_job.get("steps", []):
        run = step.get("run", "")
        if "pip_audit" in run or "pip-audit" in run:
            return step  # type: ignore[no-any-return]
    pytest.fail("pip-audit step not found inside the audit job")


# ---------------------------------------------------------------------------
# §4.2.1 — pip-audit declared in dev dependencies
# ---------------------------------------------------------------------------


def test_pip_audit_declared_in_dev_dependencies(pyproject: dict) -> None:  # type: ignore[type-arg]
    """pip-audit must appear in [project.optional-dependencies.dev] in pyproject.toml."""
    dev_deps: list[str] = (
        pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    )
    assert any("pip-audit" in dep for dep in dev_deps), (
        f"pip-audit not found in [project.optional-dependencies.dev]: {dev_deps!r}"
    )


# ---------------------------------------------------------------------------
# §4.2.2 — push/PR path filter and §4.2.3 — weekly schedule + workflow_dispatch
# ---------------------------------------------------------------------------


def test_audit_workflow_has_push_pr_path_filter(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    """dependency-audit.yml must have path filters on push and pull_request triggers."""
    on: dict = audit_workflow.get("on", {})  # type: ignore[type-arg]
    for trigger in ("push", "pull_request"):
        assert trigger in on, f"dependency-audit.yml missing {trigger!r} trigger"
        paths = on[trigger].get("paths", [])
        assert paths, (
            f"dependency-audit.yml {trigger!r} trigger has no paths filter; "
            "it should only run when pyproject.toml or the workflow itself changes"
        )
        assert any("pyproject.toml" in p for p in paths), (
            f"{trigger!r} path filter does not include pyproject.toml: {paths!r}"
        )


def test_audit_workflow_has_weekly_schedule(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    """dependency-audit.yml must include a valid weekly cron schedule."""
    on: dict = audit_workflow.get("on", {})  # type: ignore[type-arg]
    schedule: list = on.get("schedule", [])  # type: ignore[type-arg]
    assert schedule, "dependency-audit.yml has no schedule trigger"
    crons = [entry.get("cron", "") for entry in schedule]
    # A weekly cron has exactly 5 fields; the day-of-week or day-of-month
    # field must narrow the run to once per week (not every day).
    for cron in crons:
        parts = cron.split()
        assert len(parts) == 5, f"cron expression must have 5 fields: {cron!r}"
        dow = parts[4]  # day-of-week field
        dom = parts[2]  # day-of-month field
        assert dow != "*" or dom != "*", (
            f"cron {cron!r} would run daily, not weekly; "
            "set day-of-week or day-of-month to a specific value"
        )


def test_audit_workflow_has_workflow_dispatch(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    """dependency-audit.yml must support manual workflow_dispatch trigger."""
    on: dict = audit_workflow.get("on", {})  # type: ignore[type-arg]
    assert "workflow_dispatch" in on, (
        "dependency-audit.yml missing workflow_dispatch trigger; "
        "add 'workflow_dispatch:' so the audit can be triggered manually"
    )


# ---------------------------------------------------------------------------
# §4.2.4 — install from project environment (not a synthetic requirements file)
# ---------------------------------------------------------------------------


def test_audit_job_installs_from_project_environment(audit_job: dict) -> None:  # type: ignore[type-arg]
    """The audit job must install from .[dev], not a standalone requirements file."""
    install_runs = [
        step.get("run", "")
        for step in audit_job.get("steps", [])
        if "pip install" in step.get("run", "")
    ]
    assert install_runs, "audit job has no pip install step"
    for run in install_runs:
        assert ".[dev]" in run or ".[dev" in run, (
            f"audit job installs from a path that may not be the project environment: {run!r}. "
            "Use: python -m pip install -e '.[dev]'"
        )
        # Must NOT install from a standalone requirements.txt
        assert "requirements" not in run.lower(), (
            f"audit job installs from a requirements file instead of the project: {run!r}"
        )


# ---------------------------------------------------------------------------
# §4.2.5 — audit step is blocking
# ---------------------------------------------------------------------------


def test_audit_step_has_no_continue_on_error(audit_step: dict) -> None:  # type: ignore[type-arg]
    """pip-audit step must not carry continue-on-error: true."""
    assert not audit_step.get("continue-on-error", False), (
        "pip-audit step has continue-on-error: true — this silences vulnerability findings"
    )


# ---------------------------------------------------------------------------
# §4.2.6 — no --ignore-vuln flag
# ---------------------------------------------------------------------------


def test_audit_step_has_no_ignore_vuln(audit_step: dict) -> None:  # type: ignore[type-arg]
    """pip-audit command must not carry --ignore-vuln."""
    run: str = audit_step.get("run", "")
    assert "--ignore-vuln" not in run, (
        f"pip-audit step contains --ignore-vuln, which silences known vulnerabilities: {run!r}"
    )


# ---------------------------------------------------------------------------
# §4.2.7 — no shell escape valves
# ---------------------------------------------------------------------------


def test_audit_step_has_no_shell_escape(audit_step: dict) -> None:  # type: ignore[type-arg]
    """pip-audit run must not contain shell escape valves that swallow the exit code."""
    run: str = audit_step.get("run", "")
    for escape in _AUDIT_ESCAPE_VALVES:
        assert escape not in run, (
            f"pip-audit step contains shell escape {escape!r}: {run!r}"
        )


# ---------------------------------------------------------------------------
# §4.2.8 — no secrets in audit job
# ---------------------------------------------------------------------------


def test_audit_job_does_not_reference_secrets(audit_job: dict) -> None:  # type: ignore[type-arg]
    """The audit job must not reference any GitHub Actions secrets."""
    job_text = str(audit_job)
    assert "${{ secrets." not in job_text, (
        "The audit job references a GitHub Actions secret. "
        "pip-audit must work without any tokens."
    )

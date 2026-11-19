"""Regression tests for blocking security and dependency workflows."""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_CI_PATH = _ROOT / ".github" / "workflows" / "ci.yml"
_HOSTED_PATH = _ROOT / ".github" / "workflows" / "hosted.yml"
_AUDIT_PATH = _ROOT / ".github" / "workflows" / "dependency-audit.yml"
_PYPROJECT_PATH = _ROOT / "pyproject.toml"
_PRODUCTION_ROOT = _ROOT / "src" / "aletheia_lab"
_PROPERTY_ROOT = _ROOT / "tests" / "property"
_SHELL_OPERATORS = {"|", "||", "&&", ";", "&", ">", ">>", "<"}


def _workflow(path: Path) -> dict:  # type: ignore[type-arg]
    parsed: dict = yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[type-arg]
    if True in parsed and "on" not in parsed:
        parsed["on"] = parsed.pop(True)
    return parsed


def _steps(workflow: dict) -> list[tuple[str, int, dict]]:  # type: ignore[type-arg]
    collected: list[tuple[str, int, dict]] = []  # type: ignore[type-arg]
    for job_name, job in workflow.get("jobs", {}).items():
        for index, step in enumerate(job.get("steps", [])):
            collected.append((job_name, index, step))
    return collected


def _commands(run: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in run.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = shlex.split(stripped, posix=True)
        assert not (_SHELL_OPERATORS & set(tokens)), (
            f"workflow command contains a shell control operator: {stripped!r}"
        )
        commands.append(tokens)
    return commands


@pytest.fixture(scope="module")
def ci_workflow() -> dict:  # type: ignore[type-arg]
    return _workflow(_CI_PATH)


@pytest.fixture(scope="module")
def audit_workflow() -> dict:  # type: ignore[type-arg]
    return _workflow(_AUDIT_PATH)


@pytest.fixture(scope="module")
def pyproject() -> dict:  # type: ignore[type-arg]
    with _PYPROJECT_PATH.open("rb") as file:
        return tomllib.load(file)  # type: ignore[no-any-return]


def _dev_dependencies(pyproject: dict) -> tuple[str, ...]:  # type: ignore[type-arg]
    return tuple(pyproject["project"]["optional-dependencies"]["dev"])


def _find_exact_command(
    workflow: dict,
    expected: list[str],  # type: ignore[type-arg]
) -> tuple[str, int, dict]:  # type: ignore[type-arg]
    matches = []
    for job_name, index, step in _steps(workflow):
        if _commands(str(step.get("run", ""))) == [expected]:
            matches.append((job_name, index, step))
    assert len(matches) == 1, f"expected exactly one command {expected!r}, found {matches!r}"
    return matches[0]


def _assert_blocking_job(workflow: dict, job_name: str, step: dict) -> None:  # type: ignore[type-arg]
    job = workflow["jobs"][job_name]
    assert step.get("continue-on-error") is not True
    assert job.get("continue-on-error") is not True
    assert str(step.get("if", "")).strip().lower() not in {"false", "${{ false }}"}
    assert str(job.get("if", "")).strip().lower() not in {"false", "${{ false }}"}
    assert "secrets." not in str(job)


def test_security_tools_are_declared_as_development_dependencies(pyproject: dict) -> None:  # type: ignore[type-arg]
    dependencies = _dev_dependencies(pyproject)
    assert any(dependency.startswith("bandit>=") for dependency in dependencies)
    assert any(dependency.startswith("pip-audit>=") for dependency in dependencies)


def test_bandit_command_is_exact_and_blocking(ci_workflow: dict) -> None:  # type: ignore[type-arg]
    expected = ["python", "-m", "bandit", "-r", "src/aletheia_lab", "-ll", "-ii"]
    job_name, _, step = _find_exact_command(ci_workflow, expected)
    _assert_blocking_job(ci_workflow, job_name, step)


def test_ci_permissions_are_read_only(ci_workflow: dict) -> None:  # type: ignore[type-arg]
    assert ci_workflow.get("permissions") == {"contents": "read"}
    for job in ci_workflow["jobs"].values():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}


def test_bandit_has_no_global_exclusions_or_skips(pyproject: dict) -> None:  # type: ignore[type-arg]
    config = pyproject.get("tool", {}).get("bandit", {})
    assert not config.get("skips")
    assert not config.get("exclude_dirs")


def test_bandit_suppression_is_narrow_and_documented() -> None:
    suppressions: list[tuple[str, str, str]] = []
    for path in _PRODUCTION_ROOT.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = re.search(r"#\s*nosec(?:\s+([A-Z]\d{3}))?\s*$", line)
            if match:
                preceding = lines[index - 1].strip() if index else ""
                suppressions.append(
                    (path.relative_to(_ROOT).as_posix(), match.group(1) or "", preceding)
                )
    # These exact execution sites require pinned source and reject changed bytes;
    # the regression below checks that compensating control before execution.
    assert sorted(suppressions) == sorted(
        [
            (
                "src/aletheia_lab/data/download.py",
                "B310",
                "# The scheme allowlist above is the compensating control for Bandit B310.",
            ),
            (
                "src/aletheia_lab/evaluation/serving_native_development.py",
                "B102",
                "# Only the fixed, hash-checked upstream method census is executable here.",
            ),
            (
                "src/aletheia_lab/evaluation/serving_native_development.py",
                "B102",
                "# Compile the exact pinned buffer; never reopen the path or use a bytecode cache.",
            ),
            (
                "src/aletheia_lab/evaluation/serving_protocol_native.py",
                "B102",
                "# Execution consumes the same exact, pinned buffer checked above.",
            ),
            (
                "src/aletheia_lab/evaluation/serving_protocol_native.py",
                "B102",
                "checked_source(roots, filename)",
            ),
        ]
    )


@pytest.mark.parametrize("entrypoint", ["methods", "module", "class"])
def test_suppressed_execution_rejects_changed_pinned_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from aletheia_lab.evaluation import serving_native_development as development
    from aletheia_lab.evaluation import serving_protocol_native as protocol
    from aletheia_lab.project.identity import content_sha256

    raw = b"class Fixture:\n    def value(self):\n        return 17\n"
    path = tmp_path / "fixture.py"
    path.write_bytes(raw)
    owner = development if entrypoint == "methods" else protocol
    monkeypatch.setattr(owner, "PINS", {path.name: content_sha256(raw)})
    path.write_bytes(b"raise RuntimeError('changed source must not execute')\n")
    roots = protocol.SourceRoots(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="pin mismatch"):
        if entrypoint == "methods":
            development._methods(path, "Fixture", ("value",), {})
        elif entrypoint == "module":
            protocol._source_module(roots, "serving_security_fixture", path.name)
        else:
            protocol._exact_class(roots, path.name, "Fixture", {})


@pytest.mark.parametrize("entrypoint", ["methods", "module"])
def test_execution_uses_checked_buffer_when_path_changes_after_pin_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from aletheia_lab.evaluation import serving_native_development as development
    from aletheia_lab.project.identity import content_sha256

    raw = b"class Fixture:\n    def value(self):\n        return 17\n"
    path = tmp_path / "fixture.py"
    path.write_bytes(raw)
    monkeypatch.setattr(development, "PINS", {path.name: content_sha256(raw)})
    original = development._check_execution_source

    def replace_after_check(source: Path) -> bytes:
        checked = original(source)
        source.write_bytes(raw.replace(b"return 17", b"return 99"))
        return checked

    monkeypatch.setattr(development, "_check_execution_source", replace_after_check)
    if entrypoint == "methods":
        fixture = development._methods(path, "Fixture", ("value",), {})
    else:
        fixture = development._source_module(path, "security_buffer_fixture").Fixture
    assert fixture().value() == 17


def test_dependency_workflow_triggers_are_complete(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    triggers = audit_workflow["on"]
    expected_paths = {
        "pyproject.toml",
        "scripts/report_dependency_inventory.py",
        ".github/workflows/dependency-audit.yml",
    }
    assert set(triggers["push"]["paths"]) == expected_paths
    assert set(triggers["pull_request"]["paths"]) == expected_paths
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"] == [{"cron": "0 2 * * 1"}]


def test_dependency_audit_upgrades_pip_before_install_and_audit(
    audit_workflow: dict,  # type: ignore[type-arg]
) -> None:
    audit_job = audit_workflow["jobs"]["audit"]
    commands_with_positions = [
        (step_index, command)
        for step_index, step in enumerate(audit_job["steps"])
        for command in _commands(str(step.get("run", "")))
    ]
    pip_upgrade = ["python", "-m", "pip", "install", "--upgrade", "pip"]
    project_install = ["python", "-m", "pip", "install", "-e", ".[dev]"]
    audit = ["python", "-m", "pip_audit"]
    assert [command for _, command in commands_with_positions].count(pip_upgrade) == 1
    assert [command for _, command in commands_with_positions].count(project_install) == 1
    assert [command for _, command in commands_with_positions].count(audit) == 1
    positions = {tuple(command): position for position, command in commands_with_positions}
    assert positions[tuple(pip_upgrade)] <= positions[tuple(project_install)]
    assert positions[tuple(project_install)] < positions[tuple(audit)]


def test_dependency_audit_command_is_exact_and_blocking(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    job_name, _, step = _find_exact_command(audit_workflow, ["python", "-m", "pip_audit"])
    _assert_blocking_job(audit_workflow, job_name, step)
    assert "--ignore-vuln" not in str(step.get("run", ""))


def test_dependency_inventory_evidence_is_exact_and_blocking(
    audit_workflow: dict,  # type: ignore[type-arg]
) -> None:
    job_name, _, step = _find_exact_command(
        audit_workflow, ["python", "scripts/report_dependency_inventory.py"]
    )
    _assert_blocking_job(audit_workflow, job_name, step)


def test_dependency_cache_binds_pyproject(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    setup_steps = [
        step
        for _, _, step in _steps(audit_workflow)
        if "actions/setup-python" in str(step.get("uses", ""))
    ]
    assert len(setup_steps) == 1
    inputs = setup_steps[0].get("with")
    assert isinstance(inputs, dict)
    assert inputs.get("cache") == "pip"
    assert inputs.get("cache-dependency-path") == "pyproject.toml"


def test_dependency_audit_permissions_are_read_only(audit_workflow: dict) -> None:  # type: ignore[type-arg]
    assert audit_workflow.get("permissions") == {"contents": "read"}
    for job in audit_workflow["jobs"].values():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}


def test_workflows_use_current_first_party_action_runtimes() -> None:
    for path in (_CI_PATH, _AUDIT_PATH, _HOSTED_PATH):
        workflow = _workflow(path)
        uses = [str(step["uses"]) for _, _, step in _steps(workflow) if step.get("uses")]
        expected = {"actions/checkout@v5", "actions/setup-python@v6"}
        if path == _HOSTED_PATH:
            expected.add("actions/setup-node@v6")
        assert set(uses) == expected


def test_dashboard_job_checks_locked_dependencies_current_producers_and_all_builds(ci_workflow: dict) -> None:  # type: ignore[type-arg]
    workflow = _workflow(_HOSTED_PATH)
    job = workflow["jobs"]["contracts"]
    assert job["name"] == "dashboard", "Keep the existing dashboard check-run label when moving the job."
    assert "dashboard" not in ci_workflow["jobs"], "Do not run the historical consumer job in parallel with current producers."
    setup = [step for step in job["steps"] if step.get("uses") == "actions/setup-node@v6"]
    assert len(setup) == 1
    assert setup[0]["with"] == {
        "node-version": "24",
        "cache": "npm",
        "cache-dependency-path": "dashboard/web/package-lock.json",
    }
    commands = []
    for step in job["steps"]:
        _assert_blocking_job(workflow, "contracts", step)
        if step.get("working-directory") == "dashboard/web" or "npm --prefix" in str(step.get("run", "")):
            commands.extend(_commands(str(step.get("run", ""))))
    assert commands == [
        ["npm", "--prefix", "dashboard/web", "ci", "--ignore-scripts"],
        ["npm", "test"],
        ["npm", "run", "build"],
        ["npm", "run", "build:live"],
        ["npm", "run", "budget"],
        ["npm", "run", "build:hosted"],
    ]
    consumer = next(step for step in job["steps"] if step.get("run") == "npm test")
    assert consumer["env"] == {
        "J01_FIXTURES": "${{ runner.temp }}/j01-producer-current",
        "K04_FIXTURES": "${{ runner.temp }}/k04-producer-current",
    }
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    for exporter in ("export_j01_review.py", "export_k04_contract.py", "export_k07_contract.py", "export_k02_project_operations.py", "export_k13_k15_contract.py", "export_hosted_openapi.py"):
        assert exporter in runs
    for task in range(2, 19):
        assert f'test_k{task:02}_*.py' in runs
    assert '-m "not integration"' in runs


def test_manual_contract_job_is_independent_of_runtime_services() -> None:
    workflow = _workflow(_HOSTED_PATH)
    contracts = workflow["jobs"]["contracts"]
    assert not contracts.get("services") and not contracts.get("needs")
    assert not any(name.endswith("DATABASE_URL") for name in contracts.get("env", {}))
    assert workflow["on"]["workflow_dispatch"]["inputs"]["run_postgres"]["default"] is False
    backend = workflow["jobs"]["backend"]
    assert backend["needs"] == "contracts"
    assert backend["if"] == "${{ github.event_name != 'workflow_dispatch' || inputs.run_postgres }}"
    assert backend["services"]["postgres"]["image"] == "postgres:16-bookworm@sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca"
    integration = next(step for step in backend["steps"]
        if "tests/integration/" in str(step.get("run", "")))
    _assert_blocking_job(workflow, "backend", integration)
    for task in range(3, 19):
        assert integration["env"][f"K{task:02}_TEST_DATABASE_URL"] == "${{ env.K02_TEST_DATABASE_URL }}"
    command = _commands(integration["run"])[0]
    paths = {path.relative_to(_ROOT).as_posix() for pattern in command if pattern.startswith("tests/integration/")
        for path in _ROOT.glob(pattern)}
    required = {path.relative_to(_ROOT).as_posix() for task in range(2, 19)
        for path in (_ROOT / 'tests/integration').glob(f'test_k{task:02}_*.py')}
    assert paths == required and paths, "Hosted PostgreSQL CI must cover every current K02–K18 integration module."
    assert any('-m integration tests/unit/test_k05_query_probe.py' in str(step.get("run", "")) for step in backend["steps"])
    for step in contracts["steps"]:
        assert "docker" not in str(step.get("run", "")).lower()
        assert "tests/integration/" not in str(step.get("run", ""))


def test_public_property_tests_contain_no_internal_tracking_language() -> None:
    forbidden = (
        re.compile(r"\b(?:handoff|job[_ -]?\d+|task[_ -]?\d+)\b", re.IGNORECASE),
        re.compile(r"\bgroup\s+[a-d]\b", re.IGNORECASE),
        re.compile(r"\b(?:bao|bảo|kien|kiên)\b", re.IGNORECASE),
        re.compile(r"§\d"),
        re.compile(r"def\s+test_[a-d]\d+_", re.IGNORECASE),
    )
    findings: list[str] = []
    for path in sorted(_PROPERTY_ROOT.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if match := pattern.search(content):
                findings.append(f"{path.name}: {match.group(0)!r}")
    assert findings == []

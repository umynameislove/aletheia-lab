"""Architecture regression tests for diagnosis development boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import aletheia_lab.diagnosis.development as development

_ROOT: Final = Path(__file__).resolve().parents[2]
_PACKAGE: Final = _ROOT / "src" / "aletheia_lab" / "diagnosis" / "_development"
_INTERNAL_PREFIX: Final = "aletheia_lab.diagnosis._development."
_MAX_INTERNAL_MODULE_LINES: Final = 600

_ALLOWED_INTERNAL_DEPENDENCIES: Final[dict[str, frozenset[str]]] = {
    "contracts": frozenset(),
    "policy": frozenset({"contracts"}),
    "planning": frozenset({"contracts", "policy"}),
    "executor": frozenset({"contracts", "policy"}),
    "resources": frozenset({"contracts"}),
    "store": frozenset({"contracts"}),
    "validation": frozenset({"contracts", "policy"}),
    "runner": frozenset({"contracts", "executor", "planning", "resources", "store", "validation"}),
}

_DEPENDENCY_LEVEL: Final = {
    "contracts": 0,
    "policy": 1,
    "planning": 2,
    "executor": 2,
    "resources": 2,
    "store": 2,
    "validation": 2,
    "runner": 3,
}

_PUBLIC_API: Final = (
    "DEVELOPMENT_MODE",
    "DevelopmentArtifactStore",
    "DevelopmentCase",
    "DevelopmentEvidenceItem",
    "DevelopmentFailureReceipt",
    "DevelopmentPilotError",
    "DevelopmentPilotManifest",
    "DevelopmentPilotPlan",
    "DevelopmentResourceObservation",
    "DevelopmentRunRecord",
    "DevelopmentTerminalReceipt",
    "DevelopmentToolEvent",
    "DevelopmentToolLedger",
    "DevelopmentVariantExecutor",
    "DevelopmentVariantRequest",
    "DevelopmentVariantResponse",
    "DeterministicDevelopmentExecutor",
    "build_development_case",
    "build_development_evidence_item",
    "build_development_plan",
    "load_development_plan",
    "load_run_record",
    "load_run_request",
    "load_run_response",
    "run_development_pilot",
    "resource_observation_for_request",
    "validate_request_against_authority",
    "validate_response_against_authority",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def _internal_dependencies(path: Path) -> set[str]:
    return {
        module.removeprefix(_INTERNAL_PREFIX).split(".", maxsplit=1)[0]
        for module in _imports(path)
        if module.startswith(_INTERNAL_PREFIX)
    }


def test_internal_dependency_graph_is_acyclic_and_explicit() -> None:
    modules = {path.stem for path in _PACKAGE.glob("*.py") if path.name != "__init__.py"}
    assert modules == set(_ALLOWED_INTERNAL_DEPENDENCIES)
    assert set(_DEPENDENCY_LEVEL) == modules
    for name, allowed in _ALLOWED_INTERNAL_DEPENDENCIES.items():
        observed = _internal_dependencies(_PACKAGE / f"{name}.py")
        assert observed <= allowed
        assert all(
            _DEPENDENCY_LEVEL[dependency] < _DEPENDENCY_LEVEL[name]
            for dependency in observed
        )


def test_independent_audit_does_not_import_executor_runner_or_facade() -> None:
    imports = _imports(_ROOT / "src" / "aletheia_lab" / "evaluation" / "development_audit.py")
    forbidden = {
        "aletheia_lab.diagnosis.development",
        "aletheia_lab.diagnosis._development.executor",
        "aletheia_lab.diagnosis._development.runner",
    }
    assert imports.isdisjoint(forbidden)


def test_public_facade_preserves_the_accepted_api() -> None:
    assert tuple(development.__all__) == _PUBLIC_API
    assert all(getattr(development, name, None) is not None for name in _PUBLIC_API)


def test_internal_modules_remain_within_review_budget() -> None:
    for name in _ALLOWED_INTERNAL_DEPENDENCIES:
        lines = (_PACKAGE / f"{name}.py").read_text(encoding="utf-8").splitlines()
        assert len(lines) <= _MAX_INTERNAL_MODULE_LINES, name

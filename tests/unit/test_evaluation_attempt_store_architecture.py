"""Architecture and compatibility gates for the isolated attempt store."""

from __future__ import annotations

import ast
from pathlib import Path

import aletheia_lab.evaluation.attempt_store as public_store

_INTERNAL_ROOT = (
    Path(__file__).parents[2] / "src" / "aletheia_lab" / "evaluation" / "_attempt_store"
)
_PUBLIC_EXPORTS = {
    "FAILURE_RECEIPT_SCHEMA_VERSION",
    "STORE_ENTRY_SCHEMA_VERSION",
    "STORE_RECEIPT_SCHEMA_VERSION",
    "TERMINAL_INVENTORY_SCHEMA_VERSION",
    "AttemptStoreConflictError",
    "AttemptStoreError",
    "AttemptStoreIntegrityError",
    "AttemptStoreTransitionError",
    "ImmutableAttemptStore",
    "StoreClock",
    "StoreLedgerEntry",
    "StoreWriteReceipt",
    "TechnicalFailureReceipt",
    "TerminalExecutionInventory",
}
_ALLOWED_INTERNAL_IMPORTS = {
    "__init__": set(),
    "contracts": set(),
    "transitions": {"contracts"},
    "writer": {"contracts"},
    "reader": {"contracts", "transitions"},
    "integrity": {"contracts", "reader"},
    "reconciliation": {"contracts", "reader"},
    "store": {
        "contracts",
        "integrity",
        "reconciliation",
        "transitions",
        "writer",
    },
}


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imports.add(node.module.split(".", maxsplit=1)[0])
    return imports


def test_public_attempt_store_facade_preserves_the_frozen_export_set() -> None:
    assert set(public_store.__all__) == _PUBLIC_EXPORTS
    assert set(vars(public_store)) >= _PUBLIC_EXPORTS


def test_internal_attempt_store_dependency_direction_is_frozen() -> None:
    for module, allowed in _ALLOWED_INTERNAL_IMPORTS.items():
        observed = _internal_imports(_INTERNAL_ROOT / f"{module}.py")
        assert observed <= allowed, (module, observed - allowed)


def test_reader_verifier_and_reconciler_have_no_write_authority_import() -> None:
    for module in ("reader", "integrity", "reconciliation"):
        source = (_INTERNAL_ROOT / f"{module}.py").read_text(encoding="utf-8")
        assert ".writer" not in source
        assert "AttemptStoreWriter" not in source
        assert "_atomic_create" not in source


def test_internal_modules_stay_within_the_review_budget() -> None:
    for path in sorted(_INTERNAL_ROOT.glob("*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 800, (path.name, line_count)

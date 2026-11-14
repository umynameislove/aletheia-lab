"""Inventory literal local error codes. Does not infer hosted status or completeness."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    root = Path(__file__).resolve().parents[1]
    entries: dict[str, list[dict[str, object]]] = {}
    unresolved: list[dict[str, object]] = []
    for package in ("src/aletheia_lab/product", "src/aletheia_lab/dashboard"):
        for path in sorted((root / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                if name not in ("ProductError", "BridgeError"):
                    continue
                location = {"path": path.relative_to(root).as_posix(), "line": node.lineno}
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    entries.setdefault(node.args[0].value, []).append(location)
                else:
                    unresolved.append(location)
    document = {
        "scope": "literal ProductError and BridgeError calls in local product and dashboard only",
        "hosted_http_status": None,
        "limitations": "Dynamic/indirect calls and other packages are not fully resolved; review before use.",
        "codes": entries,
        "unresolved_calls": unresolved,
    }
    with output.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(document, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    print(f"LITERAL_CODES={len(entries)}")
    print(f"UNRESOLVED_CALLS={len(unresolved)}")


if __name__ == "__main__":
    main()

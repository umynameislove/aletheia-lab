"""Print canonical evidence for the exact Python environment resolved by CI."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]
_NORMALIZE_NAME: Final = re.compile(r"[-_.]+")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def resolved_inventory() -> dict[str, object]:
    """Return path-free, deterministically ordered resolved dependency evidence."""

    resolved: dict[str, str] = {}
    for distribution in distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            continue
        name = _NORMALIZE_NAME.sub("-", raw_name).lower()
        version = distribution.version
        existing = resolved.get(name)
        if existing is not None and existing != version:
            raise RuntimeError(f"conflicting installed versions for {name}")
        resolved[name] = version

    pyproject_bytes = (_ROOT / "pyproject.toml").read_bytes()
    payload: dict[str, object] = {
        "schema_version": "resolved-dependency-inventory/v1",
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "pyproject_sha256": hashlib.sha256(pyproject_bytes).hexdigest(),
        "distributions": [
            {"name": name, "version": resolved[name]} for name in sorted(resolved)
        ],
    }
    payload["inventory_sha256"] = hashlib.sha256(
        _canonical_json(payload).encode("ascii")
    ).hexdigest()
    return payload


def main() -> int:
    print(_canonical_json(resolved_inventory()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

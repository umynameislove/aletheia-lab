"""Deterministic artifact serialization and provenance capture.

JSON is written with sorted keys and a trailing newline so identical content
produces byte-identical files. Provenance records the dataset checksum, the
resolved seed/config and the package versions needed to reproduce a run;
``created_at`` is metadata only and never participates in reproducibility
comparisons.
"""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def dumps_deterministic(payload: Any) -> str:
    """Serialize to a stable JSON string (sorted keys, trailing newline)."""

    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(payload: Any, path: str | Path) -> Path:
    """Write ``payload`` as deterministic JSON and return the path."""

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps_deterministic(payload), encoding="utf-8")
    return out


def sha256_file(path: str | Path) -> str:
    """SHA-256 of a file, streamed."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    """Return versions of the packages that affect numeric reproducibility."""

    import numpy
    import pandas
    import sklearn

    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "scikit_learn": sklearn.__version__,
    }


def utc_now_iso() -> str:
    """Current UTC timestamp (metadata only, excluded from reproducibility)."""

    return datetime.now(UTC).isoformat(timespec="seconds")

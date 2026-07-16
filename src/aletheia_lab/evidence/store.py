"""Evidence store interface.

The first version can be file-based JSON. Later versions can plug into SQLite,
projmem, MLflow, or a dedicated experiment database.
"""

from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.evidence.schema import EvidenceBundle


def save_bundle(bundle: EvidenceBundle, path: str | Path) -> None:
    """Save a bundle deterministically (full store contract remains P1-G5B)."""

    bundle_path = Path(path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        bundle.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    bundle_path.write_text(payload + "\n", encoding="utf-8")


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Load an evidence bundle from JSON."""

    bundle_path = Path(path)
    return EvidenceBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))

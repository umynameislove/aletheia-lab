"""Evidence store interface.

The first version can be file-based JSON. Later versions can plug into SQLite,
projmem, MLflow, or a dedicated experiment database.
"""

from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.evidence.schema import EvidenceBundle


def save_bundle(bundle: EvidenceBundle, path: str | Path) -> None:
    """Save an evidence bundle as JSON."""

    bundle_path = Path(path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Load an evidence bundle from JSON."""

    bundle_path = Path(path)
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    return EvidenceBundle.model_validate(data)

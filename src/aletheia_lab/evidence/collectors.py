"""Evidence collectors.

Collectors turn raw experiment artifacts into structured evidence items.
Implementation should stay deterministic and should not expose hidden ground truth.
"""

from __future__ import annotations

from pathlib import Path

from aletheia_lab.evidence.schema import EvidenceItem


def collect_text_log(path: str | Path, evidence_id: str, title: str) -> EvidenceItem:
    """Create a log evidence item from a text file."""

    log_path = Path(path)
    content = log_path.read_text(encoding="utf-8")
    return EvidenceItem(
        evidence_id=evidence_id,
        kind="log",
        title=title,
        content=content,
        source_path=str(log_path),
    )

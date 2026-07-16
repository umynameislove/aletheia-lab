"""Evidence collectors.

Collectors turn raw experiment artifacts into structured evidence items.
Implementation should stay deterministic and should not expose hidden ground truth.
"""

from __future__ import annotations

from pathlib import Path

from aletheia_lab.evidence.rubric import EvidenceRole
from aletheia_lab.evidence.schema import EvidenceItem


def collect_text_log(
    path: str | Path,
    evidence_id: str,
    title: str,
    *,
    source_root: str | Path,
    evidence_roles: tuple[EvidenceRole, ...] = ("symptom",),
    collector_version: str = "text-log/1",
) -> EvidenceItem:
    """Create a diagnosis-visible log item using an allowlisted relative source path.

    The real P1 context collector is intentionally outside P1-G5A.  This helper
    remains usable without accepting absolute paths as evidence provenance.
    """

    root = Path(source_root).resolve()
    log_path = Path(path).resolve()
    try:
        relative_path = log_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("log path must be inside source_root") from exc
    content = log_path.read_text(encoding="utf-8")
    return EvidenceItem.from_content(
        evidence_id=evidence_id,
        kind="log",
        evidence_roles=evidence_roles,
        title=title,
        content=content,
        source_path=relative_path,
        collector_version=collector_version,
        visibility="diagnosis",
    )

"""Adapter boundary for projmem reuse.

Keep this thin. Aletheia should consume memory/lineage records from projmem
without making projmem responsible for the research claim.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjmemRunRef:
    """Reference to a projmem-tracked run or failure object."""

    run_id: str
    project_path: str
    notes: str | None = None

"""Integrity-checked loading of diagnosis-visible P6 evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence import (
    ProductEvidenceProjection,
    build_diagnosis_evidence_projection,
)
from aletheia_lab.project.identity import PROJECT_ID_PATTERN, SNAPSHOT_ID_PATTERN
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import ProjectEvidenceBundle, ProjectEvidenceReference
from aletheia_lab.project.snapshots import ProjectSnapshot

_EVIDENCE_NOT_AVAILABLE_MESSAGE: Final[str] = "Evidence is not available for this snapshot."
_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored evidence failed its integrity check."


def _checked_scope(project_id: str, snapshot_id: str) -> tuple[str, str]:
    if (
        not isinstance(project_id, str)
        or re.fullmatch(PROJECT_ID_PATTERN, project_id) is None
        or not isinstance(snapshot_id, str)
        or re.fullmatch(SNAPSHOT_ID_PATTERN, snapshot_id) is None
    ):
        raise ProductError("invalid_id", "The supplied evidence scope is invalid.")
    return project_id, snapshot_id


def _after_snapshot_reference(bundle: ProjectEvidenceBundle) -> ProjectEvidenceReference:
    matches = tuple(item for item in bundle.items if item.role == "after_snapshot")
    if len(matches) != 1:
        raise ValueError("evidence bundle does not have one after-snapshot reference")
    return matches[0]


def _load_scoped_snapshot(
    store: ProjectStore,
    project_id: str,
    snapshot_id: str,
) -> ProjectSnapshot:
    snapshots = {record.record_id for record in store.list_records(project_id, "snapshot")}
    if snapshot_id not in snapshots:
        raise ProductError("evidence_not_available", _EVIDENCE_NOT_AVAILABLE_MESSAGE)
    snapshot = store.load(snapshot_id)
    if not isinstance(snapshot, ProjectSnapshot) or snapshot.project_id != project_id:
        raise ValueError("stored snapshot does not match evidence scope")
    return snapshot


def _matching_evidence_bundles(
    store: ProjectStore,
    project_id: str,
    snapshot: ProjectSnapshot,
) -> tuple[ProjectEvidenceBundle, ...]:
    matches: list[ProjectEvidenceBundle] = []
    for record in store.list_records(project_id, "evidence_bundle"):
        candidate = store.load(record.record_id)
        if not isinstance(candidate, ProjectEvidenceBundle) or candidate.project_id != project_id:
            raise ValueError("stored evidence bundle does not match project scope")
        after = _after_snapshot_reference(candidate)
        if after.source_id != snapshot.snapshot_id:
            continue
        if after.source_sha256 != snapshot.state_sha256:
            raise ValueError("evidence snapshot hash does not reconcile")
        matches.append(candidate)
    return tuple(matches)


def load_diagnosis_evidence_projection(
    store_root: Path,
    *,
    project_id: str,
    snapshot_id: str,
) -> ProductEvidenceProjection:
    """Load one exact project/snapshot evidence generation and filter it in backend."""

    checked_project_id, checked_snapshot_id = _checked_scope(project_id, snapshot_id)
    try:
        with ProjectStore(store_root) as store:
            snapshot = _load_scoped_snapshot(store, checked_project_id, checked_snapshot_id)
            matches = _matching_evidence_bundles(store, checked_project_id, snapshot)
            if not matches:
                raise ProductError("evidence_not_available", _EVIDENCE_NOT_AVAILABLE_MESSAGE)
            if len(matches) != 1:
                raise ValueError("evidence scope resolves to multiple bundles")
            return build_diagnosis_evidence_projection(matches[0])
    except ProductError:
        raise
    except Exception:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None

"""Construct deterministic item and numeric deltas for snapshot comparison."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .identity import canonical_project_sha256
from .snapshots import ProjectSnapshotItem

if TYPE_CHECKING:
    from .mapping import AnyMetricObservation
    from .regression import (
        AnyProjectMetricChange,
        ChangedItemField,
        ItemChangeKind,
        MetricChangeKind,
        ProjectItemChange,
    )

def _changed_item_fields(
    before: ProjectSnapshotItem, after: ProjectSnapshotItem
) -> tuple[ChangedItemField, ...]:
    fields: tuple[ChangedItemField, ...] = (
        "project_item_id",
        "relative_path",
        "source_type",
        "source_sha256",
        "artifact_sha256",
        "observation_sha256",
        "visibility",
        "redaction_state",
    )
    return tuple(field for field in fields if getattr(before, field) != getattr(after, field))


def _item_change(
    kind: ItemChangeKind,
    before: ProjectSnapshotItem | None,
    after: ProjectSnapshotItem | None,
    *,
    rename_evidence_sha256: str | None = None,
) -> ProjectItemChange:
    from .regression import ProjectItemChange, _item_change_payload

    if before is None:
        fields: tuple[ChangedItemField, ...] = (
            "project_item_id",
            "relative_path",
            "source_type",
            "source_sha256",
            "artifact_sha256",
            "observation_sha256",
            "visibility",
            "redaction_state",
        )
    elif after is None:
        fields = (
            "project_item_id",
            "relative_path",
            "source_type",
            "source_sha256",
            "artifact_sha256",
            "observation_sha256",
            "visibility",
            "redaction_state",
        )
    else:
        fields = _changed_item_fields(before, after)
    fields = tuple(sorted(fields))
    payload = _item_change_payload(
        kind=kind,
        before=before,
        after=after,
        changed_fields=fields,
        rename_evidence_sha256=rename_evidence_sha256,
    )
    return ProjectItemChange(
        change_id=f"p3-change-{canonical_project_sha256(payload)}",
        kind=kind,
        before=before,
        after=after,
        changed_fields=fields,
        rename_evidence_sha256=rename_evidence_sha256,
    )


def _metric_change(
    before: AnyMetricObservation | None, after: AnyMetricObservation | None,
    *, version2: bool = False,
) -> AnyProjectMetricChange:
    from .regression import ProjectMetricChange, ProjectMetricChangeV2, _metric_change_payload

    if before is None:
        kind: MetricChangeKind = "added"
        delta = None
    elif after is None:
        kind = "removed"
        delta = None
    else:
        delta = (float(Decimal(str(after.metric_value)) - Decimal(str(before.metric_value)))
                 if version2 else after.metric_value - before.metric_value)
        kind = "increased" if delta > 0 else "decreased"
    payload = _metric_change_payload(kind=kind, before=before, after=after, delta=delta)
    model = ProjectMetricChangeV2 if version2 else ProjectMetricChange
    return model.model_validate(dict(
        metric_change_id=f"p3-metric-change-{canonical_project_sha256(payload)}",
        kind=kind,
        before=before,
        after=after,
        delta=delta,
    ))



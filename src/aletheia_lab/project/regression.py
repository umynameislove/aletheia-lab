"""Deterministic snapshot comparison and project-mode regression evidence.

The models in this module report observed change.  They deliberately retain an
``unverified`` causal status: temporal proximity, Git history and metric deltas
do not by themselves establish a regression cause.
"""

from __future__ import annotations

import math
from typing import Annotated, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.collectors import ProjectGitState
from aletheia_lab.project.identity import (
    PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
    PROJECT_EVIDENCE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    REGRESSION_EVENT_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_COMPARISON_ID_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_sha256,
)
from aletheia_lab.project.mapping import MetricObservation
from aletheia_lab.project.snapshots import ProjectSnapshot, ProjectSnapshotItem

PROJECT_SNAPSHOT_COMPARISON_SCHEMA_VERSION: Final[
    Literal["project-snapshot-comparison/v1"]
] = "project-snapshot-comparison/v1"
PROJECT_REGRESSION_EVENT_SCHEMA_VERSION: Final[
    Literal["project-regression-event/v1"]
] = "project-regression-event/v1"
PROJECT_EVIDENCE_BUNDLE_SCHEMA_VERSION: Final[
    Literal["project-evidence-bundle/v1"]
] = "project-evidence-bundle/v1"
PROJECT_EVIDENCE_VIEW_SCHEMA_VERSION: Final[
    Literal["project-evidence-view/v1"]
] = "project-evidence-view/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_CHANGE_ID_PATTERN: Final[str] = r"^p3-change-[0-9a-f]{64}$"
_METRIC_CHANGE_ID_PATTERN: Final[str] = r"^p3-metric-change-[0-9a-f]{64}$"
_SOURCE_ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"

ItemChangeKind = Literal["added", "removed", "modified", "renamed"]
MetricChangeKind = Literal["added", "removed", "increased", "decreased"]
SourceChange = Literal[
    "source_bundle",
    "project_manifest",
    "file_collection",
    "git_state",
    "mapping_configuration",
    "mapping_result",
    "baseline_selection",
    "collector_binding",
]
ChangedItemField = Literal[
    "project_item_id",
    "relative_path",
    "source_type",
    "source_sha256",
    "artifact_sha256",
    "observation_sha256",
    "visibility",
    "redaction_state",
]


class ProjectRegressionError(ValueError):
    """Raised when snapshot comparison or evidence must fail closed."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


def _item_change_payload(
    *,
    kind: ItemChangeKind,
    before: ProjectSnapshotItem | None,
    after: ProjectSnapshotItem | None,
    changed_fields: tuple[ChangedItemField, ...],
    rename_evidence_sha256: str | None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "before": None if before is None else before.model_dump(mode="json"),
        "after": None if after is None else after.model_dump(mode="json"),
        "changed_fields": list(changed_fields),
        "rename_evidence_sha256": rename_evidence_sha256,
    }


class ProjectItemChange(_StrictFrozenModel):
    change_id: str = Field(pattern=_CHANGE_ID_PATTERN)
    kind: ItemChangeKind
    before: ProjectSnapshotItem | None = None
    after: ProjectSnapshotItem | None = None
    changed_fields: tuple[ChangedItemField, ...]
    rename_evidence_sha256: Sha256 | None = None

    @field_validator("changed_fields")
    @classmethod
    def _canonical_fields(
        cls, values: tuple[ChangedItemField, ...]
    ) -> tuple[ChangedItemField, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("item change fields must be non-empty and unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _change_reconciles(self) -> ProjectItemChange:
        if self.kind == "added" and (self.before is not None or self.after is None):
            raise ValueError("added item change requires only an after item")
        if self.kind == "removed" and (self.before is None or self.after is not None):
            raise ValueError("removed item change requires only a before item")
        if self.kind in {"modified", "renamed"} and (
            self.before is None or self.after is None
        ):
            raise ValueError("modified and renamed changes require before and after items")
        if (
            self.kind == "modified"
            and self.before is not None
            and self.after is not None
            and self.before.relative_path != self.after.relative_path
        ):
            raise ValueError("modified items must retain their path")
        if self.kind == "renamed":
            if (
                self.before is not None
                and self.after is not None
                and self.before.relative_path == self.after.relative_path
            ):
                raise ValueError("renamed items must change path")
            if self.rename_evidence_sha256 is None:
                raise ValueError("renamed items require explicit Git rename evidence")
        elif self.rename_evidence_sha256 is not None:
            raise ValueError("rename evidence is valid only for renamed items")
        payload = _item_change_payload(
            kind=self.kind,
            before=self.before,
            after=self.after,
            changed_fields=self.changed_fields,
            rename_evidence_sha256=self.rename_evidence_sha256,
        )
        if self.change_id != f"p3-change-{canonical_project_sha256(payload)}":
            raise ValueError("change_id does not match item change")
        return self


def _metric_key(value: MetricObservation) -> tuple[str, str, int | None]:
    return value.run_id, value.metric_name, value.step


def _metric_change_payload(
    *,
    kind: MetricChangeKind,
    before: MetricObservation | None,
    after: MetricObservation | None,
    delta: float | None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "before": None if before is None else before.model_dump(mode="json"),
        "after": None if after is None else after.model_dump(mode="json"),
        "delta": delta,
    }


class ProjectMetricChange(_StrictFrozenModel):
    metric_change_id: str = Field(pattern=_METRIC_CHANGE_ID_PATTERN)
    kind: MetricChangeKind
    before: MetricObservation | None = None
    after: MetricObservation | None = None
    delta: float | None = None

    @model_validator(mode="after")
    def _metric_change_reconciles(self) -> ProjectMetricChange:
        if self.kind == "added" and (self.before is not None or self.after is None):
            raise ValueError("added metric requires only an after observation")
        if self.kind == "removed" and (self.before is None or self.after is not None):
            raise ValueError("removed metric requires only a before observation")
        if self.kind in {"increased", "decreased"}:
            if self.before is None or self.after is None or self.delta is None:
                raise ValueError("changed metric requires before, after and delta")
            if _metric_key(self.before) != _metric_key(self.after):
                raise ValueError("changed metric observations must share an identity")
            expected = self.after.metric_value - self.before.metric_value
            if self.delta == 0:
                raise ValueError("changed metric delta must be non-zero")
            if not math.isclose(self.delta, expected, rel_tol=0.0, abs_tol=1e-15):
                raise ValueError("metric delta does not match observations")
            if (self.kind == "increased") != (self.delta > 0):
                raise ValueError("metric direction does not match delta")
        elif self.delta is not None:
            raise ValueError("added or removed metrics do not define a delta")
        payload = _metric_change_payload(
            kind=self.kind, before=self.before, after=self.after, delta=self.delta
        )
        if self.metric_change_id != f"p3-metric-change-{canonical_project_sha256(payload)}":
            raise ValueError("metric_change_id does not match metric change")
        return self


class ProjectSnapshotComparison(_StrictFrozenModel):
    schema_version: Literal["project-snapshot-comparison/v1"] = (
        PROJECT_SNAPSHOT_COMPARISON_SCHEMA_VERSION
    )
    comparison_id: str = Field(pattern=SNAPSHOT_COMPARISON_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    before_snapshot_id: str = Field(pattern=SNAPSHOT_ID_PATTERN)
    after_snapshot_id: str = Field(pattern=SNAPSHOT_ID_PATTERN)
    before_record_sha256: Sha256
    after_record_sha256: Sha256
    status: Literal["unchanged", "changed"]
    source_changes: tuple[SourceChange, ...]
    item_changes: tuple[ProjectItemChange, ...]
    metric_changes: tuple[ProjectMetricChange, ...]
    comparison_sha256: Sha256

    @field_validator("source_changes")
    @classmethod
    def _canonical_sources(cls, values: tuple[SourceChange, ...]) -> tuple[SourceChange, ...]:
        if len(values) != len(set(values)):
            raise ValueError("source changes must be unique")
        return tuple(sorted(values))

    @field_validator("item_changes")
    @classmethod
    def _canonical_item_changes(
        cls, values: tuple[ProjectItemChange, ...]
    ) -> tuple[ProjectItemChange, ...]:
        ids = tuple(value.change_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("item changes must be unique")
        return tuple(sorted(values, key=lambda value: value.change_id))

    @field_validator("metric_changes")
    @classmethod
    def _canonical_metric_changes(
        cls, values: tuple[ProjectMetricChange, ...]
    ) -> tuple[ProjectMetricChange, ...]:
        ids = tuple(value.metric_change_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("metric changes must be unique")
        return tuple(sorted(values, key=lambda value: value.metric_change_id))

    @model_validator(mode="after")
    def _comparison_reconciles(self) -> ProjectSnapshotComparison:
        changed = bool(self.source_changes or self.item_changes or self.metric_changes)
        if (self.status == "changed") != changed:
            raise ValueError("comparison status does not reconcile with change census")
        if self.before_snapshot_id == self.after_snapshot_id and changed:
            raise ValueError("identical snapshot states cannot contain changes")
        values = self.model_dump(
            mode="json", exclude={"comparison_id", "comparison_sha256"}
        )
        digest = canonical_project_sha256(values)
        if self.comparison_sha256 != digest:
            raise ValueError("comparison_sha256 does not match comparison")
        if self.comparison_id != f"p3-comparison-{digest}":
            raise ValueError("comparison_id does not match comparison")
        return self


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
    before: MetricObservation | None, after: MetricObservation | None
) -> ProjectMetricChange:
    if before is None:
        kind: MetricChangeKind = "added"
        delta = None
    elif after is None:
        kind = "removed"
        delta = None
    else:
        delta = after.metric_value - before.metric_value
        kind = "increased" if delta > 0 else "decreased"
    payload = _metric_change_payload(kind=kind, before=before, after=after, delta=delta)
    return ProjectMetricChange(
        metric_change_id=f"p3-metric-change-{canonical_project_sha256(payload)}",
        kind=kind,
        before=before,
        after=after,
        delta=delta,
    )


def _explicit_renames(
    before_items: dict[str, ProjectSnapshotItem],
    after_items: dict[str, ProjectSnapshotItem],
    git_state: ProjectGitState | None,
) -> dict[str, str]:
    if git_state is None:
        return {}
    renames: dict[str, str] = {}
    targets: set[str] = set()
    for changed in git_state.changed_files:
        if changed.previous_path is None or "R" not in changed.status:
            continue
        if changed.previous_path not in before_items or changed.relative_path not in after_items:
            raise ProjectRegressionError("Git rename evidence does not match snapshot census")
        if changed.previous_path in renames or changed.relative_path in targets:
            raise ProjectRegressionError("Git rename evidence is ambiguous")
        renames[changed.previous_path] = changed.relative_path
        targets.add(changed.relative_path)
    return renames


def compare_project_snapshots(
    before: ProjectSnapshot,
    after: ProjectSnapshot,
    *,
    after_git_state: ProjectGitState | None = None,
) -> ProjectSnapshotComparison:
    """Compare two validated states; rename inference requires exact Git evidence."""

    checked_before = _checked(before)
    checked_after = _checked(after)
    if checked_before.project_id != checked_after.project_id:
        raise ProjectRegressionError("cannot compare snapshots from different projects")
    checked_git = None if after_git_state is None else _checked(after_git_state)
    if checked_git is not None and (
        checked_git.project_id != checked_after.project_id
        or checked_git.state_sha256 != checked_after.git_state_sha256
    ):
        raise ProjectRegressionError("Git rename evidence does not bind the after snapshot")

    before_items = {value.relative_path: value for value in checked_before.items}
    after_items = {value.relative_path: value for value in checked_after.items}
    renames = _explicit_renames(before_items, after_items, checked_git)
    renamed_targets = set(renames.values())
    item_changes: list[ProjectItemChange] = []
    for path in sorted(set(before_items) & set(after_items)):
        if before_items[path] != after_items[path]:
            item_changes.append(_item_change("modified", before_items[path], after_items[path]))
    for path in sorted(set(before_items) - set(after_items)):
        target = renames.get(path)
        if target is None:
            item_changes.append(_item_change("removed", before_items[path], None))
        else:
            if checked_git is None:  # pragma: no cover - guarded by rename construction
                raise ProjectRegressionError("rename evidence disappeared")
            item_changes.append(
                _item_change(
                    "renamed",
                    before_items[path],
                    after_items[target],
                    rename_evidence_sha256=checked_git.state_sha256,
                )
            )
    for path in sorted(set(after_items) - set(before_items) - renamed_targets):
        item_changes.append(_item_change("added", None, after_items[path]))

    before_metrics = {_metric_key(value): value for value in checked_before.metric_observations}
    after_metrics = {_metric_key(value): value for value in checked_after.metric_observations}
    metric_changes: list[ProjectMetricChange] = []
    for key in sorted(set(before_metrics) | set(after_metrics), key=str):
        old = before_metrics.get(key)
        new = after_metrics.get(key)
        if old is None or new is None or old.metric_value != new.metric_value:
            metric_changes.append(_metric_change(old, new))

    source_pairs: tuple[tuple[SourceChange, object, object], ...] = (
        ("source_bundle", checked_before.source_bundle_sha256, checked_after.source_bundle_sha256),
        ("project_manifest", checked_before.project_manifest_sha256, checked_after.project_manifest_sha256),
        ("file_collection", checked_before.file_collection_sha256, checked_after.file_collection_sha256),
        ("git_state", checked_before.git_state_sha256, checked_after.git_state_sha256),
        (
            "mapping_configuration",
            checked_before.mapping_configuration_sha256,
            checked_after.mapping_configuration_sha256,
        ),
        ("mapping_result", checked_before.mapping_result_sha256, checked_after.mapping_result_sha256),
        ("baseline_selection", checked_before.baseline_run_id, checked_after.baseline_run_id),
        ("collector_binding", checked_before.collectors, checked_after.collectors),
    )
    source_changes = tuple(name for name, old, new in source_pairs if old != new)
    canonical_items = tuple(sorted(item_changes, key=lambda value: value.change_id))
    canonical_metrics = tuple(
        sorted(metric_changes, key=lambda value: value.metric_change_id)
    )
    status: Literal["unchanged", "changed"] = (
        "changed" if source_changes or canonical_items or canonical_metrics else "unchanged"
    )
    values = {
        "schema_version": PROJECT_SNAPSHOT_COMPARISON_SCHEMA_VERSION,
        "project_id": checked_before.project_id,
        "before_snapshot_id": checked_before.snapshot_id,
        "after_snapshot_id": checked_after.snapshot_id,
        "before_record_sha256": checked_before.record_sha256,
        "after_record_sha256": checked_after.record_sha256,
        "status": status,
        "source_changes": list(sorted(source_changes)),
        "item_changes": [value.model_dump(mode="json") for value in canonical_items],
        "metric_changes": [value.model_dump(mode="json") for value in canonical_metrics],
    }
    digest = canonical_project_sha256(values)
    return ProjectSnapshotComparison(
        comparison_id=f"p3-comparison-{digest}",
        project_id=checked_before.project_id,
        before_snapshot_id=checked_before.snapshot_id,
        after_snapshot_id=checked_after.snapshot_id,
        before_record_sha256=checked_before.record_sha256,
        after_record_sha256=checked_after.record_sha256,
        status=status,
        source_changes=source_changes,
        item_changes=canonical_items,
        metric_changes=canonical_metrics,
        comparison_sha256=digest,
    )


class ProjectRegressionEvent(_StrictFrozenModel):
    schema_version: Literal["project-regression-event/v1"] = (
        PROJECT_REGRESSION_EVENT_SCHEMA_VERSION
    )
    event_id: str = Field(pattern=REGRESSION_EVENT_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    comparison_id: str = Field(pattern=SNAPSHOT_COMPARISON_ID_PATTERN)
    comparison_sha256: Sha256
    before_snapshot_id: str = Field(pattern=SNAPSHOT_ID_PATTERN)
    after_snapshot_id: str = Field(pattern=SNAPSHOT_ID_PATTERN)
    observed_metric_change_ids: tuple[str, ...]
    qualification: Literal["regression_candidate"] = "regression_candidate"
    causal_status: Literal["unverified"] = "unverified"
    event_sha256: Sha256

    @field_validator("observed_metric_change_ids")
    @classmethod
    def _canonical_metrics(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("regression event requires unique observed metric changes")
        if any(
            not value.startswith("p3-metric-change-") or len(value) != 81
            for value in values
        ):
            raise ValueError("regression event contains an invalid metric-change ID")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _event_reconciles(self) -> ProjectRegressionEvent:
        values = self.model_dump(mode="json", exclude={"event_id", "event_sha256"})
        digest = canonical_project_sha256(values)
        if self.event_sha256 != digest or self.event_id != f"p3-event-{digest}":
            raise ValueError("regression event identity does not reconcile")
        return self


def build_project_regression_event(
    comparison: ProjectSnapshotComparison,
) -> ProjectRegressionEvent:
    """Mint a non-causal regression candidate from observed metric changes."""

    checked = _checked(comparison)
    if checked.status != "changed" or not checked.metric_changes:
        raise ProjectRegressionError("regression event requires an observed metric change")
    values = {
        "schema_version": PROJECT_REGRESSION_EVENT_SCHEMA_VERSION,
        "project_id": checked.project_id,
        "comparison_id": checked.comparison_id,
        "comparison_sha256": checked.comparison_sha256,
        "before_snapshot_id": checked.before_snapshot_id,
        "after_snapshot_id": checked.after_snapshot_id,
        "observed_metric_change_ids": sorted(
            value.metric_change_id for value in checked.metric_changes
        ),
        "qualification": "regression_candidate",
        "causal_status": "unverified",
    }
    digest = canonical_project_sha256(values)
    return ProjectRegressionEvent(
        event_id=f"p3-event-{digest}",
        project_id=checked.project_id,
        comparison_id=checked.comparison_id,
        comparison_sha256=checked.comparison_sha256,
        before_snapshot_id=checked.before_snapshot_id,
        after_snapshot_id=checked.after_snapshot_id,
        observed_metric_change_ids=tuple(values["observed_metric_change_ids"]),
        event_sha256=digest,
    )


EvidenceRole = Literal[
    "before_snapshot",
    "after_snapshot",
    "snapshot_comparison",
    "metric_change",
    "regression_candidate",
]


class ProjectEvidenceReference(_StrictFrozenModel):
    evidence_id: str = Field(pattern=PROJECT_EVIDENCE_ID_PATTERN)
    role: EvidenceRole
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    source_sha256: Sha256
    visibility: Literal["public", "diagnosis", "evaluator"]
    redaction_state: Literal["none", "withheld"] = "none"
    provenance_links: tuple[str, ...] = ()

    @field_validator("provenance_links")
    @classmethod
    def _canonical_links(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("evidence provenance links must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _reference_reconciles(self) -> ProjectEvidenceReference:
        if self.redaction_state == "withheld" and self.visibility != "evaluator":
            raise ValueError("withheld project evidence must be evaluator-only")
        if self.evidence_id in self.provenance_links:
            raise ValueError("project evidence cannot cite itself")
        values = self.model_dump(mode="json", exclude={"evidence_id"})
        if self.evidence_id != f"p3-evidence-{canonical_project_sha256(values)}":
            raise ValueError("evidence_id does not match project evidence reference")
        return self


def _evidence_reference(
    *,
    role: EvidenceRole,
    source_id: str,
    source_sha256: str,
    provenance_links: tuple[str, ...] = (),
) -> ProjectEvidenceReference:
    values = {
        "role": role,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "visibility": "diagnosis",
        "redaction_state": "none",
        "provenance_links": sorted(provenance_links),
    }
    return ProjectEvidenceReference(
        evidence_id=f"p3-evidence-{canonical_project_sha256(values)}",
        role=role,
        source_id=source_id,
        source_sha256=source_sha256,
        visibility="diagnosis",
        provenance_links=provenance_links,
    )


class ProjectEvidenceBundle(_StrictFrozenModel):
    schema_version: Literal["project-evidence-bundle/v1"] = (
        PROJECT_EVIDENCE_BUNDLE_SCHEMA_VERSION
    )
    evidence_bundle_id: str = Field(pattern=PROJECT_EVIDENCE_BUNDLE_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    event_id: str = Field(pattern=REGRESSION_EVENT_ID_PATTERN)
    items: tuple[ProjectEvidenceReference, ...]
    bundle_sha256: Sha256

    @field_validator("items")
    @classmethod
    def _canonical_items(
        cls, values: tuple[ProjectEvidenceReference, ...]
    ) -> tuple[ProjectEvidenceReference, ...]:
        ids = tuple(value.evidence_id for value in values)
        if not values or len(ids) != len(set(ids)):
            raise ValueError("project evidence items must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.evidence_id))

    @model_validator(mode="after")
    def _bundle_reconciles(self) -> ProjectEvidenceBundle:
        by_role: dict[EvidenceRole, list[ProjectEvidenceReference]] = {}
        for item in self.items:
            by_role.setdefault(item.role, []).append(item)
        roles = set(by_role)
        required = {
            "before_snapshot",
            "after_snapshot",
            "snapshot_comparison",
            "metric_change",
            "regression_candidate",
        }
        if not required.issubset(roles):
            raise ValueError("project evidence bundle is missing a required role")
        if any(
            len(by_role[role]) != 1
            for role in (
                "before_snapshot",
                "after_snapshot",
                "snapshot_comparison",
                "regression_candidate",
            )
        ):
            raise ValueError("project evidence singleton roles must occur exactly once")
        known = {value.evidence_id for value in self.items}
        if any(link not in known for value in self.items for link in value.provenance_links):
            raise ValueError("project evidence provenance references an unknown item")
        before = by_role["before_snapshot"][0]
        after = by_role["after_snapshot"][0]
        comparison = by_role["snapshot_comparison"][0]
        event = by_role["regression_candidate"][0]
        metrics = by_role["metric_change"]
        snapshot_links = tuple(sorted((before.evidence_id, after.evidence_id)))
        if comparison.provenance_links != snapshot_links:
            raise ValueError("comparison evidence must cite both snapshots exactly")
        if any(value.provenance_links != snapshot_links for value in metrics):
            raise ValueError("metric-change evidence must cite both snapshots exactly")
        expected_event_links = tuple(
            sorted((comparison.evidence_id, *(value.evidence_id for value in metrics)))
        )
        if event.provenance_links != expected_event_links or event.source_id != self.event_id:
            raise ValueError("event evidence does not reconcile its comparison and metrics")
        values = self.model_dump(
            mode="json", exclude={"evidence_bundle_id", "bundle_sha256"}
        )
        digest = canonical_project_sha256(values)
        if self.bundle_sha256 != digest:
            raise ValueError("bundle_sha256 does not match project evidence bundle")
        if self.evidence_bundle_id != f"p3-evidence-bundle-{digest}":
            raise ValueError("evidence_bundle_id does not match project evidence bundle")
        return self


class ProjectEvidenceViewItem(_StrictFrozenModel):
    evidence_id: str = Field(pattern=PROJECT_EVIDENCE_ID_PATTERN)
    role: EvidenceRole
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    source_sha256: Sha256
    provenance_links: tuple[str, ...]

    @field_validator("provenance_links")
    @classmethod
    def _canonical_links(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("view provenance links must be unique")
        return tuple(sorted(values))


class ProjectEvidenceView(_StrictFrozenModel):
    schema_version: Literal["project-evidence-view/v1"] = PROJECT_EVIDENCE_VIEW_SCHEMA_VERSION
    evidence_bundle_id: str = Field(pattern=PROJECT_EVIDENCE_BUNDLE_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    items: tuple[ProjectEvidenceViewItem, ...]
    view_sha256: Sha256

    @field_validator("items")
    @classmethod
    def _canonical_items(
        cls, values: tuple[ProjectEvidenceViewItem, ...]
    ) -> tuple[ProjectEvidenceViewItem, ...]:
        ids = tuple(value.evidence_id for value in values)
        if not values or len(ids) != len(set(ids)):
            raise ValueError("project evidence view items must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.evidence_id))

    @model_validator(mode="after")
    def _view_reconciles(self) -> ProjectEvidenceView:
        known = {value.evidence_id for value in self.items}
        if any(link not in known for value in self.items for link in value.provenance_links):
            raise ValueError("project evidence view contains an unknown provenance link")
        values = self.model_dump(mode="json", exclude={"view_sha256"})
        if self.view_sha256 != canonical_project_sha256(values):
            raise ValueError("view_sha256 does not match project evidence view")
        return self


def build_project_regression_evidence(
    before: ProjectSnapshot,
    after: ProjectSnapshot,
    comparison: ProjectSnapshotComparison,
    event: ProjectRegressionEvent,
) -> ProjectEvidenceBundle:
    """Build a payload-free evidence graph for one regression candidate."""

    checked_before = _checked(before)
    checked_after = _checked(after)
    checked_comparison = _checked(comparison)
    checked_event = _checked(event)
    if (
        checked_before.project_id != checked_event.project_id
        or checked_after.project_id != checked_event.project_id
        or checked_comparison.project_id != checked_event.project_id
        or checked_comparison.comparison_id != checked_event.comparison_id
        or checked_comparison.comparison_sha256 != checked_event.comparison_sha256
        or checked_before.snapshot_id != checked_event.before_snapshot_id
        or checked_after.snapshot_id != checked_event.after_snapshot_id
        or checked_before.record_sha256 != checked_comparison.before_record_sha256
        or checked_after.record_sha256 != checked_comparison.after_record_sha256
        or set(checked_event.observed_metric_change_ids)
        != {value.metric_change_id for value in checked_comparison.metric_changes}
    ):
        raise ProjectRegressionError("regression evidence sources do not reconcile")

    before_ref = _evidence_reference(
        role="before_snapshot",
        source_id=checked_before.snapshot_id,
        source_sha256=checked_before.state_sha256,
    )
    after_ref = _evidence_reference(
        role="after_snapshot",
        source_id=checked_after.snapshot_id,
        source_sha256=checked_after.state_sha256,
    )
    comparison_ref = _evidence_reference(
        role="snapshot_comparison",
        source_id=checked_comparison.comparison_id,
        source_sha256=checked_comparison.comparison_sha256,
        provenance_links=(before_ref.evidence_id, after_ref.evidence_id),
    )
    metric_refs = tuple(
        _evidence_reference(
            role="metric_change",
            source_id=value.metric_change_id,
            source_sha256=value.metric_change_id.removeprefix("p3-metric-change-"),
            provenance_links=(before_ref.evidence_id, after_ref.evidence_id),
        )
        for value in checked_comparison.metric_changes
    )
    event_ref = _evidence_reference(
        role="regression_candidate",
        source_id=checked_event.event_id,
        source_sha256=checked_event.event_sha256,
        provenance_links=(comparison_ref.evidence_id, *(value.evidence_id for value in metric_refs)),
    )
    items = tuple(
        sorted(
            (before_ref, after_ref, comparison_ref, *metric_refs, event_ref),
            key=lambda value: value.evidence_id,
        )
    )
    values = {
        "schema_version": PROJECT_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "project_id": checked_event.project_id,
        "event_id": checked_event.event_id,
        "items": [value.model_dump(mode="json") for value in items],
    }
    digest = canonical_project_sha256(values)
    return ProjectEvidenceBundle(
        evidence_bundle_id=f"p3-evidence-bundle-{digest}",
        project_id=checked_event.project_id,
        event_id=checked_event.event_id,
        items=items,
        bundle_sha256=digest,
    )


def project_diagnosis_project_evidence(bundle: ProjectEvidenceBundle) -> ProjectEvidenceView:
    """Whitelist only non-withheld public/diagnosis project evidence references."""

    checked = _checked(bundle)
    items = tuple(
        ProjectEvidenceViewItem(
            evidence_id=value.evidence_id,
            role=value.role,
            source_id=value.source_id,
            source_sha256=value.source_sha256,
            provenance_links=value.provenance_links,
        )
        for value in checked.items
        if value.visibility in {"public", "diagnosis"} and value.redaction_state != "withheld"
    )
    values = {
        "schema_version": PROJECT_EVIDENCE_VIEW_SCHEMA_VERSION,
        "evidence_bundle_id": checked.evidence_bundle_id,
        "project_id": checked.project_id,
        "items": [value.model_dump(mode="json") for value in items],
    }
    return ProjectEvidenceView(
        schema_version=PROJECT_EVIDENCE_VIEW_SCHEMA_VERSION,
        evidence_bundle_id=checked.evidence_bundle_id,
        project_id=checked.project_id,
        items=items,
        view_sha256=canonical_project_sha256(values),
    )

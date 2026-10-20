"""Persisted product evidence loading remains scoped and fail closed."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence_store import load_diagnosis_evidence_projection
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricObservation
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import (
    EvidenceRole,
    ProjectEvidenceBundle,
    ProjectEvidenceReference,
)
from aletheia_lab.project.snapshots import (
    PROJECT_SNAPSHOT_SCHEMA_VERSION,
    ProjectSnapshot,
    ProjectSnapshotItem,
    SnapshotCollectorBinding,
    _record_digest,
    _state_payload,
)

pytestmark = pytest.mark.integration

_PROJECT_ID = "p3-project-" + "1" * 64
_FOREIGN_PROJECT_ID = "p3-project-" + "9" * 64
_BUNDLE_ID = "p3-bundle-" + "2" * 64
_EVENT_ID = "p3-event-" + "3" * 64
_METRIC_ITEM_ID = "p3-item-" + "4" * 64


def _metric(run_id: str, value: float) -> MetricObservation:
    payload = {
        "source_mapping_id": "metrics",
        "project_item_id": _METRIC_ITEM_ID,
        "run_id": run_id,
        "metric_name": "loss",
        "metric_value": value,
        "step": None,
        "source_record_index": 0 if run_id == "baseline" else 1,
    }
    return MetricObservation(
        observation_id=f"p3-metric-{canonical_project_sha256(payload)}",
        **payload,
    )


def _snapshot() -> ProjectSnapshot:
    item = ProjectSnapshotItem(
        project_item_id=_METRIC_ITEM_ID,
        relative_path="metrics.csv",
        source_type="metrics",
        source_sha256="4" * 64,
        artifact_sha256="4" * 64,
        observation_sha256=canonical_project_sha256({"item": _METRIC_ITEM_ID}),
        visibility="diagnosis",
        redaction_state="none",
    )
    collectors = (
        SnapshotCollectorBinding(
            role="file_catalog",
            name="project-file-catalog",
            version="project-file-catalog/1.0.0",
            output_sha256="5" * 64,
        ),
        SnapshotCollectorBinding(
            role="git_state",
            name="project-git-state",
            version="project-git-state/1.0.0",
            output_sha256="6" * 64,
        ),
        SnapshotCollectorBinding(
            role="import",
            name="project-importer",
            version="1.0.0",
            output_sha256="7" * 64,
        ),
    )
    metrics = (_metric("baseline", 0.5), _metric("candidate", 0.7))
    state = _state_payload(
        project_id=_PROJECT_ID,
        source_bundle_id=_BUNDLE_ID,
        source_bundle_sha256="2" * 64,
        project_manifest_sha256="8" * 64,
        file_collection_sha256="5" * 64,
        git_state_sha256="6" * 64,
        mapping_configuration_sha256="a" * 64,
        mapping_result_sha256="b" * 64,
        baseline_run_id="baseline",
        collectors=collectors,
        items=(item,),
        metric_observations=metrics,
    )
    state_sha256 = canonical_project_sha256(state)
    captured_at = "2026-10-02T00:00:00Z"
    return ProjectSnapshot(
        schema_version=PROJECT_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=f"p3-snapshot-{state_sha256}",
        project_id=_PROJECT_ID,
        source_bundle_id=_BUNDLE_ID,
        source_bundle_sha256="2" * 64,
        project_manifest_sha256="8" * 64,
        file_collection_sha256="5" * 64,
        git_state_sha256="6" * 64,
        mapping_configuration_sha256="a" * 64,
        mapping_result_sha256="b" * 64,
        baseline_run_id="baseline",
        collectors=collectors,
        items=(item,),
        metric_observations=metrics,
        captured_at=captured_at,
        state_sha256=state_sha256,
        record_sha256=_record_digest(
            state_sha256=state_sha256,
            captured_at=captured_at,
        ),
    )


def _reference(
    role: EvidenceRole,
    suffix: str,
    *,
    source_id: str,
    source_sha256: str | None = None,
    visibility: Literal["public", "diagnosis", "evaluator"] = "diagnosis",
    redaction_state: Literal["none", "withheld"] = "none",
    provenance_links: tuple[str, ...] = (),
) -> ProjectEvidenceReference:
    resolved_sha256 = suffix * 64 if source_sha256 is None else source_sha256
    payload = {
        "role": role,
        "source_id": source_id,
        "source_sha256": resolved_sha256,
        "visibility": visibility,
        "redaction_state": redaction_state,
        "provenance_links": sorted(provenance_links),
    }
    return ProjectEvidenceReference(
        evidence_id=f"p3-evidence-{canonical_project_sha256(payload)}",
        role=role,
        source_id=source_id,
        source_sha256=resolved_sha256,
        visibility=visibility,
        redaction_state=redaction_state,
        provenance_links=provenance_links,
    )


def _evidence(
    snapshot: ProjectSnapshot, *, after_sha256: str | None = None
) -> ProjectEvidenceBundle:
    before = _reference(
        "before_snapshot",
        "c",
        source_id="p3-snapshot-" + "c" * 64,
        visibility="public",
    )
    after = _reference(
        "after_snapshot",
        "d",
        source_id=snapshot.snapshot_id,
        source_sha256=snapshot.state_sha256 if after_sha256 is None else after_sha256,
    )
    snapshot_links = tuple(sorted((before.evidence_id, after.evidence_id)))
    comparison = _reference(
        "snapshot_comparison",
        "e",
        source_id="p3-comparison-" + "e" * 64,
        provenance_links=snapshot_links,
    )
    metric = _reference(
        "metric_change",
        "f",
        source_id="p3-metric-change-" + "f" * 64,
        visibility="evaluator",
        redaction_state="withheld",
        provenance_links=snapshot_links,
    )
    event = _reference(
        "regression_candidate",
        "1",
        source_id=_EVENT_ID,
        visibility="evaluator",
        provenance_links=tuple(sorted((comparison.evidence_id, metric.evidence_id))),
    )
    items = tuple(
        sorted((before, after, comparison, metric, event), key=lambda item: item.evidence_id)
    )
    payload = {
        "schema_version": "project-evidence-bundle/v1",
        "project_id": _PROJECT_ID,
        "event_id": _EVENT_ID,
        "items": [item.model_dump(mode="json") for item in items],
    }
    digest = canonical_project_sha256(payload)
    return ProjectEvidenceBundle(
        evidence_bundle_id=f"p3-evidence-bundle-{digest}",
        project_id=_PROJECT_ID,
        event_id=_EVENT_ID,
        items=items,
        bundle_sha256=digest,
    )


def test_store_bridge_loads_exact_diagnosis_scope_after_restart(tmp_path: Path) -> None:
    snapshot = _snapshot()
    evidence = _evidence(snapshot)
    with ProjectStore(tmp_path) as store:
        store.persist((snapshot, evidence))

    projection = load_diagnosis_evidence_projection(
        tmp_path,
        project_id=_PROJECT_ID,
        snapshot_id=snapshot.snapshot_id,
    )

    assert projection.project_id == _PROJECT_ID
    assert projection.evidence_bundle_id == evidence.evidence_bundle_id
    assert projection.visibility == "diagnosis"
    assert {item.role for item in projection.items} == {
        "before_snapshot",
        "after_snapshot",
        "snapshot_comparison",
    }
    assert "withheld" not in projection.model_dump_json()


@pytest.mark.parametrize(
    ("project_id", "snapshot_id"),
    (
        (_FOREIGN_PROJECT_ID, "p3-snapshot-" + "8" * 64),
        (_PROJECT_ID, "p3-snapshot-" + "8" * 64),
    ),
)
def test_store_bridge_hides_foreign_and_missing_scope(
    tmp_path: Path,
    project_id: str,
    snapshot_id: str,
) -> None:
    snapshot = _snapshot()
    with ProjectStore(tmp_path) as store:
        store.persist((snapshot, _evidence(snapshot)))

    with pytest.raises(ProductError) as captured:
        load_diagnosis_evidence_projection(
            tmp_path,
            project_id=project_id,
            snapshot_id=snapshot_id,
        )

    assert captured.value.code == "evidence_not_available"
    assert project_id not in captured.value.safe_message
    assert snapshot_id not in captured.value.safe_message


def test_store_bridge_rejects_snapshot_hash_mismatch(tmp_path: Path) -> None:
    snapshot = _snapshot()
    with ProjectStore(tmp_path) as store:
        store.persist((snapshot, _evidence(snapshot, after_sha256="0" * 64)))

    with pytest.raises(ProductError) as captured:
        load_diagnosis_evidence_projection(
            tmp_path,
            project_id=_PROJECT_ID,
            snapshot_id=snapshot.snapshot_id,
        )

    assert captured.value.code == "store_integrity_error"
    assert captured.value.safe_message == "Stored evidence failed its integrity check."


def test_store_bridge_maps_object_tamper_to_safe_error(tmp_path: Path) -> None:
    snapshot = _snapshot()
    evidence = _evidence(snapshot)
    with ProjectStore(tmp_path) as store:
        records = store.persist((snapshot, evidence))
        evidence_record = next(
            record for record in records if record.record_type == "evidence_bundle"
        )
        store._object_path(evidence_record.object_sha256).write_bytes(b"tampered")

    with pytest.raises(ProductError) as captured:
        load_diagnosis_evidence_projection(
            tmp_path,
            project_id=_PROJECT_ID,
            snapshot_id=snapshot.snapshot_id,
        )

    assert captured.value.code == "store_integrity_error"
    assert captured.value.safe_message == "Stored evidence failed its integrity check."
    assert str(tmp_path) not in captured.value.safe_message

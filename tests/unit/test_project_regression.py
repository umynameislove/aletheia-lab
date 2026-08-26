"""Snapshot refresh, regression-candidate and project-evidence contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aletheia_lab.project.collectors import GitChangedFile, ProjectGitState
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricObservation
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectItemChange,
    ProjectMetricChange,
    ProjectRegressionError,
    _evidence_reference,
    build_project_regression_event,
    build_project_regression_evidence,
    compare_project_snapshots,
    project_diagnosis_project_evidence,
)
from aletheia_lab.project.snapshots import (
    PROJECT_SNAPSHOT_SCHEMA_VERSION,
    ProjectSnapshot,
    ProjectSnapshotItem,
    SnapshotCollectorBinding,
    _record_digest,
    _state_payload,
)

_PROJECT = "p3-project-" + "1" * 64
_BUNDLE = "p3-bundle-" + "2" * 64
_METRIC_ITEM = "p3-item-" + "3" * 64
_CONFIG_ITEM = "p3-item-" + "4" * 64


def _item(item_id: str, path: str, *, source: str, source_type: str = "config") -> ProjectSnapshotItem:
    return ProjectSnapshotItem(
        project_item_id=item_id,
        relative_path=path,
        source_type=source_type,
        source_sha256=source,
        artifact_sha256=source,
        observation_sha256=canonical_project_sha256({"path": path, "source": source}),
        visibility="diagnosis",
        redaction_state="none",
    )


def _metric(run: str, value: float) -> MetricObservation:
    values = {
        "source_mapping_id": "metrics",
        "project_item_id": _METRIC_ITEM,
        "run_id": run,
        "metric_name": "loss",
        "metric_value": value,
        "step": None,
        "source_record_index": 0 if run == "baseline" else 1,
    }
    return MetricObservation(
        observation_id=f"p3-metric-{canonical_project_sha256(values)}", **values
    )


def _snapshot(
    *,
    captured_at: str,
    config_path: str = "config.json",
    config_source: str = "4" * 64,
    candidate_value: float = 0.6,
    git_sha: str = "7" * 64,
    project_id: str = _PROJECT,
) -> ProjectSnapshot:
    items = tuple(
        sorted(
            (
                _item(
                    _METRIC_ITEM,
                    "metrics.csv",
                    source="3" * 64,
                    source_type="metrics",
                ),
                _item(_CONFIG_ITEM, config_path, source=config_source),
            ),
            key=lambda value: value.relative_path,
        )
    )
    metrics = (_metric("baseline", 0.5), _metric("candidate", candidate_value))
    collectors = (
        SnapshotCollectorBinding(
            role="file_catalog",
            name="project-file-catalog",
            version="project-file-catalog/1.0.0",
            output_sha256="6" * 64,
        ),
        SnapshotCollectorBinding(
            role="git_state",
            name="project-git-state",
            version="project-git-state/1.0.0",
            output_sha256=git_sha,
        ),
        SnapshotCollectorBinding(
            role="import",
            name="project-importer",
            version="1.0.0",
            output_sha256="5" * 64,
        ),
    )
    mapping_result_sha = canonical_project_sha256({"candidate_value": candidate_value})
    state = _state_payload(
        project_id=project_id,
        source_bundle_id=_BUNDLE,
        source_bundle_sha256="2" * 64,
        project_manifest_sha256="5" * 64,
        file_collection_sha256="6" * 64,
        git_state_sha256=git_sha,
        mapping_configuration_sha256="8" * 64,
        mapping_result_sha256=mapping_result_sha,
        baseline_run_id="baseline",
        collectors=collectors,
        items=items,
        metric_observations=metrics,
    )
    state_sha = canonical_project_sha256(state)
    return ProjectSnapshot(
        schema_version=PROJECT_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=f"p3-snapshot-{state_sha}",
        project_id=project_id,
        source_bundle_id=_BUNDLE,
        source_bundle_sha256="2" * 64,
        project_manifest_sha256="5" * 64,
        file_collection_sha256="6" * 64,
        git_state_sha256=git_sha,
        mapping_configuration_sha256="8" * 64,
        mapping_result_sha256=mapping_result_sha,
        baseline_run_id="baseline",
        collectors=collectors,
        items=items,
        metric_observations=metrics,
        captured_at=captured_at,
        state_sha256=state_sha,
        record_sha256=_record_digest(state_sha256=state_sha, captured_at=captured_at),
    )


def _git_state() -> ProjectGitState:
    changed = (
        GitChangedFile(status="R ", relative_path="settings.json", previous_path="config.json"),
    )
    values = {
        "schema_version": "project-git-state/v1",
        "collector_version": "project-git-state/1.0.0",
        "project_id": _PROJECT,
        "repository_state": "attached",
        "commit_sha": "a" * 40,
        "branch": "main",
        "dirty": True,
        "changed_files": [value.model_dump(mode="json") for value in changed],
        "git_version": "git version 2.50.1",
    }
    return ProjectGitState(
        schema_version="project-git-state/v1",
        collector_version="project-git-state/1.0.0",
        project_id=_PROJECT,
        repository_state="attached",
        commit_sha="a" * 40,
        branch="main",
        dirty=True,
        changed_files=changed,
        git_version="git version 2.50.1",
        state_sha256=canonical_project_sha256(values),
    )


def test_unchanged_refresh_is_idempotent_and_does_not_mint_event() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    after = _snapshot(captured_at="2026-08-26T02:00:00Z")
    comparison = compare_project_snapshots(before, after)

    assert comparison.status == "unchanged"
    assert comparison.item_changes == ()
    assert comparison.metric_changes == ()
    with pytest.raises(ProjectRegressionError, match="metric change"):
        build_project_regression_event(comparison)


def test_metric_change_mints_noncausal_event_and_visibility_safe_evidence() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    after = _snapshot(captured_at="2026-08-26T02:00:00Z", candidate_value=0.8)
    comparison = compare_project_snapshots(before, after)
    event = build_project_regression_event(comparison)
    evidence = build_project_regression_evidence(before, after, comparison, event)
    view = project_diagnosis_project_evidence(evidence)

    assert comparison.status == "changed"
    assert len(comparison.metric_changes) == 1
    assert comparison.metric_changes[0].kind == "increased"
    assert comparison.metric_changes[0].delta == pytest.approx(0.2)
    assert event.qualification == "regression_candidate"
    assert event.causal_status == "unverified"
    assert {value.role for value in evidence.items} == {
        "before_snapshot",
        "after_snapshot",
        "snapshot_comparison",
        "metric_change",
        "regression_candidate",
    }
    assert len(view.items) == len(evidence.items)
    serialized = view.model_dump_json()
    assert "metric_value" not in serialized
    assert "content" not in serialized


def test_rename_requires_exact_git_evidence_and_otherwise_is_add_remove() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    git_state = _git_state()
    after = _snapshot(
        captured_at="2026-08-26T02:00:00Z",
        config_path="settings.json",
        git_sha=git_state.state_sha256,
    )

    without_git = compare_project_snapshots(before, after)
    with_git = compare_project_snapshots(before, after, after_git_state=git_state)
    assert sorted(value.kind for value in without_git.item_changes) == ["added", "removed"]
    assert [value.kind for value in with_git.item_changes] == ["renamed"]
    assert with_git.item_changes[0].rename_evidence_sha256 == git_state.state_sha256


def test_cross_project_and_stale_git_evidence_fail_closed() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    foreign = _snapshot(
        captured_at="2026-08-26T02:00:00Z", project_id="p3-project-" + "9" * 64
    )
    with pytest.raises(ProjectRegressionError, match="different projects"):
        compare_project_snapshots(before, foreign)

    changed = _snapshot(captured_at="2026-08-26T02:00:00Z", candidate_value=0.7)
    with pytest.raises(ProjectRegressionError, match="does not bind"):
        compare_project_snapshots(before, changed, after_git_state=_git_state())


def test_comparison_and_evidence_reject_forged_hashes_or_unknown_links() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    after = _snapshot(captured_at="2026-08-26T02:00:00Z", candidate_value=0.7)
    comparison = compare_project_snapshots(before, after)
    event = build_project_regression_event(comparison)
    evidence = build_project_regression_evidence(before, after, comparison, event)

    payload = comparison.model_dump(mode="python")
    payload["comparison_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="comparison_sha256"):
        type(comparison).model_validate(payload)

    forged_item = evidence.items[0].model_copy(
        update={"provenance_links": ("p3-evidence-" + "0" * 64,)}
    )
    payload = evidence.model_dump(mode="python")
    payload["items"] = (forged_item, *evidence.items[1:])
    with pytest.raises(ValidationError):
        type(evidence).model_validate(payload)


def test_change_nodes_reject_inconsistent_state_before_accepting_identity() -> None:
    item = _item(_CONFIG_ITEM, "config.json", source="4" * 64)
    with pytest.raises(ValidationError, match="added item"):
        ProjectItemChange(
            change_id="p3-change-" + "0" * 64,
            kind="added",
            before=item,
            after=item,
            changed_fields=("project_item_id",),
        )

    before = _metric("candidate", 0.6)
    after = _metric("candidate", 0.8)
    with pytest.raises(ValidationError, match="delta"):
        ProjectMetricChange(
            metric_change_id="p3-metric-change-" + "0" * 64,
            kind="increased",
            before=before,
            after=after,
            delta=0.1,
        )


def test_evidence_topology_is_fail_closed() -> None:
    before = _snapshot(captured_at="2026-08-26T01:00:00Z")
    after = _snapshot(captured_at="2026-08-26T02:00:00Z", candidate_value=0.7)
    comparison = compare_project_snapshots(before, after)
    event = build_project_regression_event(comparison)
    evidence = build_project_regression_evidence(before, after, comparison, event)

    payload = evidence.model_dump(mode="python")
    payload["items"] = tuple(
        value for value in evidence.items if value.role != "metric_change"
    )
    with pytest.raises(ValidationError, match="missing"):
        ProjectEvidenceBundle.model_validate(payload)

    unknown = "p3-evidence-" + "0" * 64
    comparison_item = next(
        value for value in evidence.items if value.role == "snapshot_comparison"
    )
    forged = _evidence_reference(
        role="snapshot_comparison",
        source_id=comparison_item.source_id,
        source_sha256=comparison_item.source_sha256,
        provenance_links=(unknown,),
    )
    payload = evidence.model_dump(mode="python")
    payload["items"] = tuple(
        forged if value.role == "snapshot_comparison" else value
        for value in evidence.items
    )
    with pytest.raises(ValidationError, match="unknown"):
        ProjectEvidenceBundle.model_validate(payload)

    with pytest.raises(ProjectRegressionError, match="do not reconcile"):
        build_project_regression_evidence(before, before, comparison, event)

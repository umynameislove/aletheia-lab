"""Immutable project snapshot contracts and source reconciliation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.project import (
    collect_git_state,
    collect_project_files,
    grant_project_root,
    import_local_project,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import (
    DatasetTargetMapping,
    MetricSourceMapping,
    RunMapping,
    bind_project_mapping,
    build_project_mapping_configuration,
    validate_project_mapping,
)
from aletheia_lab.project.snapshots import (
    ProjectSnapshot,
    ProjectSnapshotError,
    build_project_snapshot,
)


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def _sources(root: Path) -> tuple[object, ...]:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Snapshot Test")
    _git(root, "config", "user.email", "snapshot@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    (root / "config.json").write_text('{"seed":42}', encoding="utf-8")
    _git(root, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(root, "commit", "-q", "-m", "snapshot fixture")

    grant = grant_project_root(root)
    imported = import_local_project(
        grant,
        display_name="Snapshot fixture",
        ingested_at="2026-08-26T00:00:00Z",
    )
    assert imported.bundle is not None
    collection = collect_project_files(imported.bundle, imported.artifacts)
    items = {item.relative_path: item for item in imported.bundle.items}
    configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=collection.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="id",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="metrics",
                project_item_id=items["metrics.csv"].project_item_id,
                format="csv",
                metric_name_field="name",
                metric_value_field="value",
                run_id_field="run",
            ),
        ),
        runs=(RunMapping(run_id="baseline"), RunMapping(run_id="candidate")),
        baseline_run_id="baseline",
    )
    result = validate_project_mapping(
        imported.bundle, imported.artifacts, collection, configuration
    )
    bound = bind_project_mapping(imported.bundle, configuration, result)
    return imported.bundle, bound, collection, collect_git_state(grant), configuration, result


def test_snapshot_is_content_addressed_but_capture_record_is_auditable(
    tmp_path: Path,
) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    first = build_project_snapshot(
        bundle,
        collection,
        git_state,
        configuration,
        result,
        captured_at="2026-08-26T01:00:00Z",
    )
    second = build_project_snapshot(
        bundle,
        collection,
        git_state,
        configuration,
        result,
        captured_at="2026-08-26T02:00:00Z",
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.state_sha256 == second.state_sha256
    assert first.record_sha256 != second.record_sha256
    assert first.items == tuple(sorted(first.items, key=lambda value: value.relative_path))
    assert {value.role for value in first.collectors} == {
        "import",
        "file_catalog",
        "git_state",
    }
    assert {value.run_id for value in first.metric_observations} == {
        "baseline",
        "candidate",
    }


def test_snapshot_identity_changes_when_observable_git_state_changes(tmp_path: Path) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    clean = build_project_snapshot(
        bundle,
        collection,
        git_state,
        configuration,
        result,
        captured_at="2026-08-26T01:00:00Z",
    )
    (tmp_path / "config.json").write_text('{"seed":43}', encoding="utf-8")
    dirty = build_project_snapshot(
        bundle,
        collection,
        collect_git_state(grant_project_root(tmp_path)),
        configuration,
        result,
        captured_at="2026-08-26T02:00:00Z",
    )

    assert clean.snapshot_id != dirty.snapshot_id
    assert clean.git_state_sha256 != dirty.git_state_sha256


def test_snapshot_fails_closed_for_unbound_or_cross_project_sources(tmp_path: Path) -> None:
    unbound, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    with pytest.raises(ProjectSnapshotError, match="not bound"):
        build_project_snapshot(
            unbound,
            collection,
            git_state,
            configuration,
            result,
            captured_at="2026-08-26T01:00:00Z",
        )

    other = tmp_path / "other"
    other.mkdir()
    _, _, _, foreign_git, _, _ = _sources(other)
    with pytest.raises(ProjectSnapshotError, match="different projects"):
        build_project_snapshot(
            bundle,
            collection,
            foreign_git,
            configuration,
            result,
            captured_at="2026-08-26T01:00:00Z",
        )


def test_snapshot_rejects_forged_hash_record_and_duplicate_census(tmp_path: Path) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    snapshot = build_project_snapshot(
        bundle,
        collection,
        git_state,
        configuration,
        result,
        captured_at="2026-08-26T01:00:00Z",
    )

    payload = snapshot.model_dump(mode="python")
    payload["state_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="state_sha256"):
        type(snapshot).model_validate(payload)

    payload = snapshot.model_dump(mode="python")
    payload["snapshot_id"] = "p3-snapshot-" + "0" * 64
    with pytest.raises(ValidationError, match="snapshot_id"):
        ProjectSnapshot.model_validate(payload)

    payload = snapshot.model_dump(mode="python")
    payload["captured_at"] = "2026-08-26T03:00:00Z"
    with pytest.raises(ValidationError, match="record_sha256"):
        type(snapshot).model_validate(payload)

    payload = snapshot.model_dump(mode="python")
    payload["items"] = (*snapshot.items, snapshot.items[0])
    with pytest.raises(ValidationError, match="unique"):
        type(snapshot).model_validate(payload)


def test_snapshot_rejects_missing_collector_baseline_and_foreign_metric(tmp_path: Path) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    snapshot = build_project_snapshot(
        bundle,
        collection,
        git_state,
        configuration,
        result,
        captured_at="2026-08-26T01:00:00Z",
    )

    payload = snapshot.model_dump(mode="python")
    payload["collectors"] = tuple(
        value for value in snapshot.collectors if value.role != "git_state"
    )
    with pytest.raises(ValidationError, match="must bind"):
        ProjectSnapshot.model_validate(payload)

    payload = snapshot.model_dump(mode="python")
    payload["baseline_run_id"] = "missing"
    with pytest.raises(ValidationError, match="baseline"):
        ProjectSnapshot.model_validate(payload)

    metric = snapshot.metric_observations[0]
    values = metric.model_dump(mode="python", exclude={"observation_id"})
    values["project_item_id"] = "p3-item-" + "9" * 64
    values["observation_id"] = f"p3-metric-{canonical_project_sha256(values)}"
    payload = snapshot.model_dump(mode="python")
    payload["metric_observations"] = (type(metric).model_validate(values),)
    payload["baseline_run_id"] = metric.run_id
    with pytest.raises(ValidationError, match="outside"):
        ProjectSnapshot.model_validate(payload)


@pytest.mark.parametrize("captured_at", ["2026-08-26T01:00:00", "not-a-timeZ"])
def test_snapshot_rejects_noncanonical_capture_time(
    tmp_path: Path, captured_at: str
) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    with pytest.raises(ValueError, match="captured_at"):
        build_project_snapshot(
            bundle,
            collection,
            git_state,
            configuration,
            result,
            captured_at=captured_at,
        )


def test_snapshot_revalidates_nested_models_after_unsafe_copy(tmp_path: Path) -> None:
    _, bundle, collection, git_state, configuration, result = _sources(tmp_path)
    forged_collection = collection.model_copy(update={"collection_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="collection_sha256"):
        build_project_snapshot(
            bundle,
            forged_collection,
            git_state,
            configuration,
            result,
            captured_at="2026-08-26T01:00:00Z",
        )

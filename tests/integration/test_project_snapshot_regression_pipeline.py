"""End-to-end import-to-evidence refresh pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aletheia_lab.project import (
    build_project_regression_event,
    build_project_regression_evidence,
    build_project_snapshot,
    collect_git_state,
    collect_project_files,
    compare_project_snapshots,
    grant_project_root,
    import_local_project,
    project_diagnosis_project_evidence,
)
from aletheia_lab.project.mapping import (
    DatasetTargetMapping,
    MetricSourceMapping,
    RunMapping,
    bind_project_mapping,
    build_project_mapping_configuration,
    validate_project_mapping,
)

pytestmark = pytest.mark.integration


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def _snapshot(root: Path, captured_at: str):  # type: ignore[no-untyped-def]
    grant = grant_project_root(root)
    imported = import_local_project(
        grant, display_name="Refresh fixture", ingested_at=captured_at
    )
    assert imported.bundle is not None
    collection = collect_project_files(imported.bundle, imported.artifacts)
    items = {value.relative_path: value for value in imported.bundle.items}
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
    return build_project_snapshot(
        bound,
        collection,
        collect_git_state(grant),
        configuration,
        result,
        captured_at=captured_at,
    )


def test_project_refresh_becomes_traceable_payload_free_evidence(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Pipeline Test")
    _git(tmp_path, "config", "user.email", "pipeline@example.invalid")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    (tmp_path / "config.json").write_text('{"seed":42}', encoding="utf-8")
    _git(tmp_path, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(tmp_path, "commit", "-q", "-m", "before")
    before = _snapshot(tmp_path, "2026-08-26T01:00:00Z")

    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "metrics.csv")
    _git(tmp_path, "commit", "-q", "-m", "after")
    after = _snapshot(tmp_path, "2026-08-26T02:00:00Z")

    comparison = compare_project_snapshots(before, after)
    event = build_project_regression_event(comparison)
    evidence = build_project_regression_evidence(before, after, comparison, event)
    diagnosis_view = project_diagnosis_project_evidence(evidence)

    assert comparison.status == "changed"
    assert comparison.source_changes
    assert len(comparison.metric_changes) == 1
    assert event.causal_status == "unverified"
    assert evidence.project_id == before.project_id == after.project_id
    assert diagnosis_view.project_id == before.project_id
    serialized = diagnosis_view.model_dump_json()
    assert "0.8" not in serialized
    assert "seed" not in serialized
    assert _git(tmp_path, "status", "--porcelain=v1", "-z") == b""

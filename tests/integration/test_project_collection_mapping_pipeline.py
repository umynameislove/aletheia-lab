"""End-to-end local collector and semantic mapping integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aletheia_lab.project import (
    collect_git_state,
    collect_project_files,
    grant_project_root,
    import_local_project,
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


def test_import_collect_map_pipeline_is_read_only_and_fail_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Synthetic Test")
    _git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    (tmp_path / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8"
    )
    (tmp_path / "config.json").write_text('{"seed":42}', encoding="utf-8")
    _git(tmp_path, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    before = _git(tmp_path, "status", "--porcelain=v1", "-z")
    grant = grant_project_root(tmp_path)

    imported = import_local_project(
        grant, display_name="Integration fixture", ingested_at="2026-08-25T00:00:00Z"
    )
    assert imported.bundle is not None
    files = collect_project_files(imported.bundle, imported.artifacts)
    git_state = collect_git_state(grant)
    items = {item.relative_path: item for item in imported.bundle.items}
    configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=files.collection_sha256,
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
    validation = validate_project_mapping(
        imported.bundle, imported.artifacts, files, configuration
    )
    bound = bind_project_mapping(imported.bundle, configuration, validation)

    assert validation.status == "valid"
    assert git_state.repository_state == "attached"
    assert not git_state.dirty
    assert bound.snapshot_refs == ()
    assert bound.evidence_bundle_refs == ()
    assert _git(tmp_path, "status", "--porcelain=v1", "-z") == before

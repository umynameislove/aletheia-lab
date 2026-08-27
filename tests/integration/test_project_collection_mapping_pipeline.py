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
    ProjectMappingError,
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
    # Do not inherit Git for Windows' usual global CRLF conversion policy.
    # The fixture is intended to start from an exactly clean repository on
    # every supported platform.
    _git(tmp_path, "config", "core.autocrlf", "false")
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


def test_public_pipeline_is_deterministic_payload_free_and_read_only(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Synthetic Test")
    _git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "dataset.csv").write_text(
        "entity_id,target\ncustomer-401,1\ncustomer-902,0\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.375\ncandidate,loss,0.625\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "dataset.csv", "metrics.csv")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    before = _git(tmp_path, "status", "--porcelain=v1", "-z")
    grant = grant_project_root(tmp_path)

    imported = import_local_project(
        grant,
        display_name="Deterministic public pipeline fixture",
        ingested_at="2026-08-25T00:00:00Z",
    )
    assert imported.bundle is not None
    items = {item.relative_path: item for item in imported.bundle.items}

    first_files = collect_project_files(imported.bundle, imported.artifacts)
    first_configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=first_files.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="entity_id",
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
    first_validation = validate_project_mapping(
        imported.bundle,
        imported.artifacts,
        first_files,
        first_configuration,
    )
    first_bound = bind_project_mapping(
        imported.bundle,
        first_configuration,
        first_validation,
    )

    second_files = collect_project_files(
        imported.bundle,
        tuple(reversed(imported.artifacts)),
    )
    second_configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=second_files.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="entity_id",
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
    second_validation = validate_project_mapping(
        imported.bundle,
        tuple(reversed(imported.artifacts)),
        second_files,
        second_configuration,
    )
    second_bound = bind_project_mapping(
        imported.bundle,
        second_configuration,
        second_validation,
    )

    assert first_files == second_files
    assert first_configuration == second_configuration
    assert first_validation == second_validation
    assert first_validation.status == "valid"
    assert first_bound == second_bound
    assert "customer-401" not in first_bound.model_dump_json()
    assert "customer-902" not in first_bound.model_dump_json()
    assert "0.375" not in first_bound.model_dump_json()
    assert collect_git_state(grant).dirty is False
    assert _git(tmp_path, "status", "--porcelain=v1", "-z") == before


def test_public_pipeline_blocks_stale_tampered_and_wrong_type_mappings_before_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / "dataset.csv").write_text(
        "entity_id,target\none,0\ntwo,1\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.7\n",
        encoding="utf-8",
    )
    grant = grant_project_root(tmp_path)

    initial = import_local_project(
        grant,
        display_name="Stale mapping fixture",
        ingested_at="2026-08-26T00:00:00Z",
    )
    assert initial.bundle is not None
    initial_files = collect_project_files(initial.bundle, initial.artifacts)
    initial_items = {item.relative_path: item for item in initial.bundle.items}
    stale_configuration = build_project_mapping_configuration(
        project_id=initial.bundle.project_id,
        project_bundle_id=initial.bundle.project_bundle_id,
        file_collection_sha256=initial_files.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=initial_items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="entity_id",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="metrics",
                project_item_id=initial_items["metrics.csv"].project_item_id,
                format="csv",
                metric_name_field="name",
                metric_value_field="value",
                run_id_field="run",
            ),
        ),
        runs=(RunMapping(run_id="baseline"), RunMapping(run_id="candidate")),
        baseline_run_id="baseline",
    )

    (tmp_path / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    updated = import_local_project(
        grant,
        display_name="Stale mapping fixture",
        ingested_at="2026-08-26T00:00:00Z",
    )
    assert updated.bundle is not None
    updated_files = collect_project_files(updated.bundle, updated.artifacts)
    updated_items = {item.relative_path: item for item in updated.bundle.items}

    stale_result = validate_project_mapping(
        updated.bundle,
        updated.artifacts,
        updated_files,
        stale_configuration,
    )

    assert stale_result.status == "blocked"
    assert stale_result.metric_observations == ()
    assert {"bundle_mismatch", "collection_mismatch"} <= {
        issue.code for issue in stale_result.issues
    }
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(updated.bundle, stale_configuration, stale_result)

    tampered_configuration = build_project_mapping_configuration(
        project_id=updated.bundle.project_id,
        project_bundle_id=updated.bundle.project_bundle_id,
        file_collection_sha256="0" * 64,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=updated_items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="entity_id",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="metrics",
                project_item_id=updated_items["metrics.csv"].project_item_id,
                format="csv",
                metric_name_field="name",
                metric_value_field="value",
                run_id_field="run",
            ),
        ),
        runs=(RunMapping(run_id="baseline"), RunMapping(run_id="candidate")),
        baseline_run_id="baseline",
    )
    tampered_result = validate_project_mapping(
        updated.bundle,
        updated.artifacts,
        updated_files,
        tampered_configuration,
    )

    assert tampered_result.status == "blocked"
    assert tampered_result.metric_observations == ()
    assert any(issue.code == "collection_mismatch" for issue in tampered_result.issues)
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(updated.bundle, tampered_configuration, tampered_result)

    wrong_type_configuration = build_project_mapping_configuration(
        project_id=updated.bundle.project_id,
        project_bundle_id=updated.bundle.project_bundle_id,
        file_collection_sha256=updated_files.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=updated_items["metrics.csv"].project_item_id,
            target_field="name",
            identifier_field="run",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="metrics",
                project_item_id=updated_items["metrics.csv"].project_item_id,
                format="csv",
                metric_name_field="name",
                metric_value_field="value",
                run_id_field="run",
            ),
        ),
        runs=(RunMapping(run_id="baseline"), RunMapping(run_id="candidate")),
        baseline_run_id="baseline",
    )
    wrong_type_result = validate_project_mapping(
        updated.bundle,
        updated.artifacts,
        updated_files,
        wrong_type_configuration,
    )

    assert wrong_type_result.status == "blocked"
    assert wrong_type_result.metric_observations == ()
    assert any(issue.code == "wrong_item_type" for issue in wrong_type_result.issues)
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(updated.bundle, wrong_type_configuration, wrong_type_result)


def test_public_pipeline_blocks_malformed_metric_payload_before_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / "dataset.csv").write_text(
        "entity_id,target\none,0\ntwo,1\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.csv").write_text(
        "run,name\nbaseline,loss\ncandidate,loss\n",
        encoding="utf-8",
    )

    imported = import_local_project(
        grant_project_root(tmp_path),
        display_name="Malformed metric fixture",
        ingested_at="2026-08-26T00:00:00Z",
    )
    assert imported.bundle is not None
    files = collect_project_files(imported.bundle, imported.artifacts)
    items = {item.relative_path: item for item in imported.bundle.items}
    configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=files.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=items["dataset.csv"].project_item_id,
            target_field="target",
            identifier_field="entity_id",
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
        imported.bundle,
        imported.artifacts,
        files,
        configuration,
    )

    assert result.status == "blocked"
    assert result.metric_observations == ()
    assert any(issue.code == "metric_payload_invalid" for issue in result.issues)
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(imported.bundle, configuration, result)

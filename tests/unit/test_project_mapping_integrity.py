"""Adversarial integrity checks for explicit project mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from aletheia_lab.project import (
    ProjectBundle,
    ProjectFileCollection,
    ProjectImportArtifact,
    collect_project_files,
    grant_project_root,
    import_local_project,
)
from aletheia_lab.project.identity import project_id_for_root
from aletheia_lab.project.mapping import (
    DatasetTargetMapping,
    MetricSourceMapping,
    ProjectMappingConfiguration,
    ProjectMappingError,
    RunMapping,
    bind_project_mapping,
    build_project_mapping_configuration,
    validate_project_mapping,
)

_STAMP = "2026-08-26T00:00:00Z"


@dataclass(frozen=True)
class _MappingCase:
    bundle: ProjectBundle
    artifacts: tuple[ProjectImportArtifact, ...]
    collection: ProjectFileCollection
    dataset_item_id: str
    metrics_item_id: str
    config_item_id: str


def _case(tmp_path: Path, metrics: str) -> _MappingCase:
    (tmp_path / "dataset.csv").write_text(
        "entity_id,target\none,0\ntwo,1\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.csv").write_text(metrics, encoding="utf-8")
    (tmp_path / "settings.json").write_text('{"seed":42}', encoding="utf-8")

    imported = import_local_project(
        grant_project_root(tmp_path),
        display_name="Mapping integrity fixture",
        ingested_at=_STAMP,
    )

    assert imported.bundle is not None
    items = {item.relative_path: item for item in imported.bundle.items}
    collection = collect_project_files(imported.bundle, imported.artifacts)
    return _MappingCase(
        bundle=imported.bundle,
        artifacts=imported.artifacts,
        collection=collection,
        dataset_item_id=items["dataset.csv"].project_item_id,
        metrics_item_id=items["metrics.csv"].project_item_id,
        config_item_id=items["settings.json"].project_item_id,
    )


def _configuration(
    case: _MappingCase,
    *,
    project_id: str | None = None,
) -> ProjectMappingConfiguration:
    return build_project_mapping_configuration(
        project_id=case.bundle.project_id if project_id is None else project_id,
        project_bundle_id=case.bundle.project_bundle_id,
        file_collection_sha256=case.collection.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="primary-target",
            project_item_id=case.dataset_item_id,
            target_field="target",
            identifier_field="entity_id",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="primary-metrics",
                project_item_id=case.metrics_item_id,
                format="csv",
                metric_name_field="metric_name",
                metric_value_field="value",
                run_id_field="run_id",
                step_field="step",
            ),
        ),
        runs=(
            RunMapping(run_id="baseline", config_item_ids=(case.config_item_id,)),
            RunMapping(run_id="candidate", config_item_ids=(case.config_item_id,)),
        ),
        baseline_run_id="baseline",
    )


def test_numeric_string_metric_values_are_typed_and_canonically_ordered(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        (
            "run_id,metric_name,value,step\n"
            "candidate,loss,0.7,1\n"
            "baseline,loss,0.5,1\n"
        ),
    )

    result = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        _configuration(case),
    )

    assert result.status == "valid"
    assert [(item.run_id, item.metric_value, item.step) for item in result.metric_observations] == [
        ("baseline", 0.5, 1),
        ("candidate", 0.7, 1),
    ]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "True"])
def test_nonfinite_or_boolean_like_metric_values_fail_closed(tmp_path: Path, value: str) -> None:
    case = _case(
        tmp_path,
        (
            "run_id,metric_name,value,step\n"
            f"baseline,loss,{value},1\n"
            "candidate,loss,0.7,1\n"
        ),
    )

    result = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        _configuration(case),
    )

    assert result.status == "blocked"
    assert result.metric_observations == ()
    assert any(issue.code == "metric_value_invalid" for issue in result.issues)


@pytest.mark.parametrize("missing_field", ["value", "run_id", "metric_name"])
def test_missing_metric_identity_or_value_field_fails_closed(
    tmp_path: Path,
    missing_field: str,
) -> None:
    headers = ["run_id", "metric_name", "value", "step"]
    headers.remove(missing_field)
    row = {
        "run_id": "baseline",
        "metric_name": "loss",
        "value": "0.5",
        "step": "1",
    }
    metric_rows = [
        ",".join(headers),
        ",".join(row[field] for field in headers),
        ",".join(
            "candidate" if field == "run_id" else "0.7" if field == "value" else row[field]
            for field in headers
        ),
    ]
    case = _case(tmp_path, "\n".join(metric_rows) + "\n")

    result = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        _configuration(case),
    )

    assert result.status == "blocked"
    assert result.metric_observations == ()
    assert any(issue.code == "metric_payload_invalid" for issue in result.issues)


def test_foreign_project_configuration_cannot_validate_or_bind(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        (
            "run_id,metric_name,value,step\n"
            "baseline,loss,0.5,1\n"
            "candidate,loss,0.7,1\n"
        ),
    )
    foreign_configuration = _configuration(
        case,
        project_id=project_id_for_root("0" * 64),
    )

    result = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        foreign_configuration,
    )

    assert result.status == "blocked"
    assert result.metric_observations == ()
    assert any(issue.code == "project_mismatch" for issue in result.issues)
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(case.bundle, foreign_configuration, result)


def test_missing_artifact_prevents_observation_release_and_bundle_binding(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        (
            "run_id,metric_name,value,step\n"
            "baseline,loss,0.5,1\n"
            "candidate,loss,0.7,1\n"
        ),
    )

    result = validate_project_mapping(
        case.bundle,
        (),
        case.collection,
        _configuration(case),
    )

    assert result.status == "blocked"
    assert result.metric_observations == ()
    assert any(issue.code == "artifact_mismatch" for issue in result.issues)


def test_valid_mapping_binds_only_mapping_identity_at_this_boundary(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        (
            "run_id,metric_name,value,step\n"
            "baseline,loss,0.5,1\n"
            "candidate,loss,0.7,1\n"
        ),
    )
    configuration = _configuration(case)

    result = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        configuration,
    )
    bound = bind_project_mapping(case.bundle, configuration, result)

    assert result.status == "valid"
    assert bound.mapping_configuration_sha256 == configuration.mapping_sha256
    assert bound.project_bundle_id != case.bundle.project_bundle_id
    assert bound.snapshot_refs == ()
    assert bound.evidence_bundle_refs == ()
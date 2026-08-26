"""Fail-closed target, metric, run and baseline mapping contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.project import (
    ProjectBundle,
    ProjectFileCollection,
    ProjectImportArtifact,
    ProjectImportResult,
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

_NOW = "2026-08-25T00:00:00Z"


@dataclass(frozen=True)
class _Case:
    result: ProjectImportResult
    bundle: ProjectBundle
    artifacts: tuple[ProjectImportArtifact, ...]
    collection: ProjectFileCollection
    dataset_item_id: str
    metrics_item_id: str
    config_item_id: str


def _case(tmp_path: Path, *, metrics: str | None = None) -> _Case:
    (tmp_path / "dataset.csv").write_text(
        "entity_id,outcome\n1,0\n2,1\n", encoding="utf-8"
    )
    (tmp_path / "metrics.csv").write_text(
        metrics
        or "run_id,metric_name,value,step\nbaseline,loss,0.5,1\ncandidate,loss,0.7,1\n",
        encoding="utf-8",
    )
    (tmp_path / "config.json").write_text('{"model":"linear","seed":42}', encoding="utf-8")
    result = import_local_project(
        grant_project_root(tmp_path), display_name="Mapping fixture", ingested_at=_NOW
    )
    assert result.bundle is not None
    items = {item.relative_path: item for item in result.bundle.items}
    collection = collect_project_files(result.bundle, result.artifacts)
    return _Case(
        result=result,
        bundle=result.bundle,
        artifacts=result.artifacts,
        collection=collection,
        dataset_item_id=items["dataset.csv"].project_item_id,
        metrics_item_id=items["metrics.csv"].project_item_id,
        config_item_id=items["config.json"].project_item_id,
    )


def _configuration(case: _Case, **overrides: object) -> ProjectMappingConfiguration:
    values: dict[str, object] = {
        "project_id": case.bundle.project_id,
        "project_bundle_id": case.bundle.project_bundle_id,
        "file_collection_sha256": case.collection.collection_sha256,
        "target": DatasetTargetMapping(
            mapping_id="primary-target",
            project_item_id=case.dataset_item_id,
            target_field="outcome",
            identifier_field="entity_id",
        ),
        "metric_sources": (
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
        "runs": (
            RunMapping(run_id="baseline", config_item_ids=(case.config_item_id,)),
            RunMapping(run_id="candidate", config_item_ids=(case.config_item_id,)),
        ),
        "baseline_run_id": "baseline",
    }
    values.update(overrides)
    return build_project_mapping_configuration(**values)  # type: ignore[arg-type]


def test_valid_mapping_reconciles_every_candidate_and_binds_bundle(tmp_path: Path) -> None:
    case = _case(tmp_path)
    configuration = _configuration(case)

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, configuration
    )

    assert mapped.status == "valid"
    assert mapped.issues == ()
    assert len(mapped.metric_observations) == 2
    assert {value.run_id for value in mapped.metric_observations} == {"baseline", "candidate"}
    assert all(value.status == "accepted" for value in mapped.decisions)
    assert len(mapped.decisions) == 6

    bound = bind_project_mapping(case.bundle, configuration, mapped)
    assert bound.mapping_configuration_sha256 == configuration.mapping_sha256
    assert bound.project_bundle_id != case.bundle.project_bundle_id
    assert bound.project_manifest == case.bundle.project_manifest


def test_mapping_rejects_foreign_project_bundle_and_collection(tmp_path: Path) -> None:
    case = _case(tmp_path)
    foreign_project = project_id_for_root("0" * 64)
    configuration = _configuration(
        case,
        project_id=foreign_project,
        project_bundle_id="p3-bundle-" + "0" * 64,
        file_collection_sha256="0" * 64,
    )

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, configuration
    )

    assert mapped.status == "blocked"
    assert mapped.metric_observations == ()
    assert {issue.code for issue in mapped.issues} == {
        "project_mismatch",
        "bundle_mismatch",
        "collection_mismatch",
    }
    assert {decision.candidate_id for decision in mapped.decisions if decision.status == "rejected"} == {
        "configuration"
    }


@pytest.mark.parametrize("field", ["missing_target", "missing_identifier"])
def test_target_mapping_requires_declared_dataset_fields(tmp_path: Path, field: str) -> None:
    case = _case(tmp_path)
    target = DatasetTargetMapping(
        mapping_id="primary-target",
        project_item_id=case.dataset_item_id,
        target_field="missing_target" if field == "missing_target" else "outcome",
        identifier_field="missing_identifier" if field == "missing_identifier" else "entity_id",
    )

    mapped = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        _configuration(case, target=target),
    )

    assert mapped.status == "blocked"
    assert mapped.metric_observations == ()
    assert any(issue.code == "unknown_field" for issue in mapped.issues)
    assert next(
        value for value in mapped.decisions if value.candidate_id == "target:primary-target"
    ).status == "rejected"


def test_metric_mapping_rejects_non_finite_value(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        metrics="run_id,metric_name,value,step\nbaseline,loss,NaN,1\ncandidate,loss,0.7,1\n",
    )

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, _configuration(case)
    )

    assert mapped.status == "blocked"
    assert mapped.metric_observations == ()
    assert any(issue.code == "metric_value_invalid" for issue in mapped.issues)


def test_metric_mapping_rejects_duplicate_identity(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        metrics=(
            "run_id,metric_name,value,step\n"
            "baseline,loss,0.5,1\n"
            "baseline,loss,0.6,1\n"
            "candidate,loss,0.7,1\n"
        ),
    )

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, _configuration(case)
    )

    assert mapped.status == "blocked"
    assert any(issue.code == "metric_identity_duplicate" for issue in mapped.issues)


def test_json_metric_records_path_is_explicit_and_validated(tmp_path: Path) -> None:
    (tmp_path / "dataset.csv").write_text("id,outcome\n1,0\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(
        '{"records":[{"run":"baseline","name":"loss","value":0.5},'
        '{"run":"candidate","name":"loss","value":0.6}]}',
        encoding="utf-8",
    )
    imported = import_local_project(
        grant_project_root(tmp_path), display_name="JSON metrics", ingested_at=_NOW
    )
    assert imported.bundle is not None
    items = {item.relative_path: item for item in imported.bundle.items}
    collection = collect_project_files(imported.bundle, imported.artifacts)
    configuration = build_project_mapping_configuration(
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=collection.collection_sha256,
        target=DatasetTargetMapping(
            mapping_id="target",
            project_item_id=items["dataset.csv"].project_item_id,
            target_field="outcome",
            identifier_field="id",
        ),
        metric_sources=(
            MetricSourceMapping(
                mapping_id="metrics",
                project_item_id=items["metrics.json"].project_item_id,
                format="json",
                records_path=("records",),
                metric_name_field="name",
                metric_value_field="value",
                run_id_field="run",
            ),
        ),
        runs=(RunMapping(run_id="baseline"), RunMapping(run_id="candidate")),
        baseline_run_id="baseline",
    )

    mapped = validate_project_mapping(
        imported.bundle, imported.artifacts, collection, configuration
    )

    assert mapped.status == "valid"
    assert [value.metric_value for value in mapped.metric_observations] == [0.5, 0.6]


def test_run_config_reference_must_point_to_config_item(tmp_path: Path) -> None:
    case = _case(tmp_path)
    runs = (
        RunMapping(run_id="baseline", config_item_ids=(case.dataset_item_id,)),
        RunMapping(run_id="candidate", config_item_ids=(case.config_item_id,)),
    )

    mapped = validate_project_mapping(
        case.bundle,
        case.artifacts,
        case.collection,
        _configuration(case, runs=runs),
    )

    assert mapped.status == "blocked"
    assert any(issue.code == "wrong_item_type" for issue in mapped.issues)


def test_unknown_run_and_run_without_metrics_are_both_reconciled(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        metrics="run_id,metric_name,value,step\nbaseline,loss,0.5,1\nrogue,loss,0.7,1\n",
    )

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, _configuration(case)
    )

    assert mapped.status == "blocked"
    assert {issue.code for issue in mapped.issues} >= {"unknown_run", "run_without_metrics"}
    rejected = {value.candidate_id for value in mapped.decisions if value.status == "rejected"}
    assert rejected >= {"metric:primary-metrics", "run:candidate"}


def test_unknown_baseline_fails_closed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    configuration = _configuration(case, baseline_run_id="not-declared")

    mapped = validate_project_mapping(
        case.bundle, case.artifacts, case.collection, configuration
    )

    assert mapped.status == "blocked"
    assert any(issue.code == "baseline_invalid" for issue in mapped.issues)
    assert next(value for value in mapped.decisions if value.candidate_id == "baseline").status == "rejected"


def test_missing_artifact_blocks_mapping_without_partial_observations(tmp_path: Path) -> None:
    case = _case(tmp_path)

    mapped = validate_project_mapping(
        case.bundle, (), case.collection, _configuration(case)
    )

    assert mapped.status == "blocked"
    assert mapped.metric_observations == ()
    assert any(issue.code == "artifact_mismatch" for issue in mapped.issues)
    with pytest.raises(ProjectMappingError, match="only the matching valid"):
        bind_project_mapping(case.bundle, _configuration(case), mapped)


def test_mapping_configuration_rejects_tampered_digest(tmp_path: Path) -> None:
    configuration = _configuration(_case(tmp_path))
    payload = configuration.model_dump(mode="python")
    payload["mapping_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="mapping_sha256"):
        ProjectMappingConfiguration.model_validate(payload)

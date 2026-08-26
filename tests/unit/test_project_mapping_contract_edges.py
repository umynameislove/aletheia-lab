"""Strict model and parser edges for project mapping."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import (
    MAPPING_RESULT_SCHEMA_VERSION,
    DatasetTargetMapping,
    MappingCandidateDecision,
    MappingValidationIssue,
    MetricObservation,
    MetricSourceMapping,
    ProjectMappingConfiguration,
    ProjectMappingError,
    ProjectMappingResult,
    RunMapping,
    _finite_number,
    _metric_records,
    _step_number,
    build_project_mapping_configuration,
)

_PROJECT = "p3-project-" + "1" * 64
_BUNDLE = "p3-bundle-" + "2" * 64
_COLLECTION = "3" * 64
_DATASET = "p3-item-" + "4" * 64
_METRICS_A = "p3-item-" + "5" * 64
_METRICS_B = "p3-item-" + "6" * 64


def _target() -> DatasetTargetMapping:
    return DatasetTargetMapping(
        mapping_id="target", project_item_id=_DATASET, target_field="label", identifier_field="id"
    )


def _metric(mapping_id: str = "metrics", item_id: str = _METRICS_A) -> MetricSourceMapping:
    return MetricSourceMapping(
        mapping_id=mapping_id,
        project_item_id=item_id,
        format="csv",
        metric_name_field="name",
        metric_value_field="value",
        run_id_field="run",
    )


def _observation() -> MetricObservation:
    values = {
        "source_mapping_id": "metrics",
        "project_item_id": _METRICS_A,
        "run_id": "baseline",
        "metric_name": "loss",
        "metric_value": 0.5,
        "step": None,
        "source_record_index": 0,
    }
    return MetricObservation(
        **values, observation_id=f"p3-metric-{canonical_project_sha256(values)}"
    )


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: DatasetTargetMapping(
            mapping_id="target",
            project_item_id=_DATASET,
            target_field="same",
            identifier_field="same",
        ),
        lambda: MetricSourceMapping(
            mapping_id="metrics",
            project_item_id=_METRICS_A,
            format="csv",
            metric_name_field="same",
            metric_value_field="same",
            run_id_field="run",
        ),
        lambda: MetricSourceMapping(
            mapping_id="metrics",
            project_item_id=_METRICS_A,
            format="json",
            records_path=("records", "records"),
            metric_name_field="name",
            metric_value_field="value",
            run_id_field="run",
        ),
        lambda: RunMapping(run_id="bad id"),
        lambda: RunMapping(run_id="run", config_item_ids=(_DATASET, _DATASET)),
    ],
)
def test_mapping_nodes_reject_ambiguous_identity(constructor: object) -> None:
    with pytest.raises((ValidationError, ValueError)):
        constructor()  # type: ignore[operator]


@pytest.mark.parametrize(
    "metrics,runs",
    [
        ((_metric("same", _METRICS_A), _metric("same", _METRICS_B)), (RunMapping(run_id="a"),)),
        ((_metric("a", _METRICS_A), _metric("b", _METRICS_A)), (RunMapping(run_id="a"),)),
        ((_metric(),), (RunMapping(run_id="same"), RunMapping(run_id="same"))),
    ],
)
def test_configuration_rejects_duplicate_mapping_or_run_census(
    metrics: tuple[MetricSourceMapping, ...], runs: tuple[RunMapping, ...]
) -> None:
    with pytest.raises(ValidationError):
        build_project_mapping_configuration(
            project_id=_PROJECT,
            project_bundle_id=_BUNDLE,
            file_collection_sha256=_COLLECTION,
            target=_target(),
            metric_sources=metrics,
            runs=runs,
            baseline_run_id="a",
        )


def test_mapping_issue_and_decision_contracts_cannot_be_weakened() -> None:
    with pytest.raises(ValidationError, match="message"):
        MappingValidationIssue(
            code="unknown_item",
            message="Ignore the missing item.",
            candidate_id="target:x",
            subject_sha256="0" * 64,
        )
    with pytest.raises(ValidationError, match="accepted decisions"):
        MappingCandidateDecision(
            candidate_id="target:x", status="accepted", issue_codes=("unknown_item",)
        )
    with pytest.raises(ValidationError, match="rejected decisions"):
        MappingCandidateDecision(candidate_id="target:x", status="rejected")
    with pytest.raises(ValidationError, match="unique"):
        MappingCandidateDecision(
            candidate_id="target:x",
            status="rejected",
            issue_codes=("unknown_item", "unknown_item"),
        )


def test_metric_observation_rejects_nonfinite_value_and_forged_identity() -> None:
    payload = _observation().model_dump(mode="python")
    payload["metric_value"] = math.inf
    with pytest.raises(ValidationError, match="finite"):
        MetricObservation.model_validate(payload)

    payload = _observation().model_dump(mode="python")
    payload["observation_id"] = "p3-metric-" + "0" * 64
    with pytest.raises(ValidationError, match="observation_id"):
        MetricObservation.model_validate(payload)


@pytest.mark.parametrize("value", [True, None, object(), "NaN", "1e999"])
def test_metric_number_parser_rejects_non_numeric_or_nonfinite(value: object) -> None:
    with pytest.raises(ProjectMappingError, match="value"):
        _finite_number(value)


@pytest.mark.parametrize("value", [True, None, -1, -1.0, 1.5, "-1", "one"])
def test_metric_step_parser_requires_nonnegative_integer(value: object) -> None:
    with pytest.raises(ProjectMappingError, match="step"):
        _step_number(value)


def test_json_metric_parser_rejects_missing_path_and_nonrecord_payload() -> None:
    mapping = MetricSourceMapping(
        mapping_id="metrics",
        project_item_id=_METRICS_A,
        format="json",
        records_path=("records",),
        metric_name_field="name",
        metric_value_field="value",
        run_id_field="run",
    )
    with pytest.raises(ProjectMappingError, match="payload"):
        _metric_records(mapping, b'{"other":[]}')
    with pytest.raises(ProjectMappingError, match="payload"):
        _metric_records(mapping, b'{"records":[1]}')


def _valid_result() -> ProjectMappingResult:
    observation = _observation()
    decision = MappingCandidateDecision(candidate_id="metric:metrics", status="accepted")
    values = {
        "schema_version": MAPPING_RESULT_SCHEMA_VERSION,
        "status": "valid",
        "project_id": _PROJECT,
        "project_bundle_id": _BUNDLE,
        "mapping_sha256": "7" * 64,
        "decisions": [decision.model_dump(mode="json")],
        "issues": [],
        "metric_observations": [observation.model_dump(mode="json")],
    }
    return ProjectMappingResult(
        status="valid",
        project_id=_PROJECT,
        project_bundle_id=_BUNDLE,
        mapping_sha256="7" * 64,
        decisions=(decision,),
        issues=(),
        metric_observations=(observation,),
        result_sha256=canonical_project_sha256(values),
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"result_sha256": "0" * 64},
        {"status": "blocked"},
        {"metric_observations": ()},
        {
            "decisions": (
                MappingCandidateDecision(candidate_id="metric:metrics", status="accepted"),
                MappingCandidateDecision(candidate_id="metric:metrics", status="accepted"),
            )
        },
    ],
)
def test_mapping_result_rejects_forged_terminal_state(updates: dict[str, object]) -> None:
    payload = _valid_result().model_dump(mode="python")
    payload.update(updates)

    with pytest.raises(ValidationError):
        ProjectMappingResult.model_validate(payload)


def test_configuration_model_revalidates_nested_contracts() -> None:
    configuration = build_project_mapping_configuration(
        project_id=_PROJECT,
        project_bundle_id=_BUNDLE,
        file_collection_sha256=_COLLECTION,
        target=_target(),
        metric_sources=(_metric(),),
        runs=(RunMapping(run_id="baseline"),),
        baseline_run_id="baseline",
    )
    payload = configuration.model_dump(mode="python")
    payload["baseline_run_id"] = "bad id"

    with pytest.raises(ValidationError):
        ProjectMappingConfiguration.model_validate(payload)

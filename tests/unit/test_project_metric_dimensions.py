"""Class/unit observations are explicit, scoped and versioned without V1 coercion."""
from __future__ import annotations

import json

import pytest
from test_project_mapping import _case
from test_project_regression import _snapshot

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.mapping import (
    build_confirm_mapping,
    load_stored_mapping,
    metric_definitions_match_observations,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import (
    MetricObservationV2,
    ProjectMappingConfigurationV2,
    ProjectMappingError,
    ProjectMappingResultV2,
    metric_label,
    metric_sort_key,
    parse_mapping_result,
    validate_mapping_dimensions,
    validate_project_mapping,
)
from aletheia_lab.project.regression import (
    ProjectMetricChangeV2,
    ProjectRegressionError,
    ProjectSnapshotComparisonV2,
    _metric_change,
    compare_project_snapshots,
)
from aletheia_lab.project.snapshots import ProjectSnapshotV2, _record_digest, _state_payload


def _mapping(case, *, unit="%", class_field="class", unit_field="unit"):
    return {"schema_version": "project-mapping-request/v2",
        "target": {"mapping_id": "target", "project_item_id": case.dataset_item_id,
                   "target_field": "outcome", "identifier_field": "entity_id"},
        "metric_sources": [{"mapping_id": "metric", "project_item_id": case.metrics_item_id,
            "format": "csv", "metric_name_field": "metric_name", "metric_value_field": "value",
            "run_id_field": "run_id", "step_field": "step", "class_field": class_field, "unit_field": unit_field}],
        "runs": [{"run_id": name, "config_item_ids": []} for name in ("baseline", "candidate")],
        "baseline_run_id": "baseline", "metric_definitions": [{"metric_name": "f1", "direction": "higher_is_better",
            "regression_threshold": 0.1, "unit": unit}]}


def _configured(case, mapping):
    return build_confirm_mapping(mapping, project_id=case.bundle.project_id,
        project_bundle_id=case.bundle.project_bundle_id, file_collection_sha256=case.collection.collection_sha256)


def _rows(*, unit="%", label="không bệnh"):
    return ("run_id,metric_name,value,step,class,unit\n"
            f"baseline,f1,0.8,1,{label},{unit}\ncandidate,f1,0.7,1,{label},{unit}\n")


def test_source_class_and_unit_survive_typed_roundtrip_and_keep_distinct_classes(tmp_path):
    rows = _rows() + "baseline,f1,0.9,1,có bệnh,%\ncandidate,f1,0.6,1,có bệnh,%\n"
    case = _case(tmp_path, metrics=rows)
    configuration = _configured(case, _mapping(case))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    assert isinstance(configuration, ProjectMappingConfigurationV2)
    assert isinstance(result, ProjectMappingResultV2) and result.status == "valid"
    assert len(result.metric_observations) == 4
    assert {row.class_label for row in result.metric_observations} == {"không bệnh", "có bệnh"}
    assert {(row.unit, row.unit_origin) for row in result.metric_observations} == {("%", "source")}
    assert load_stored_mapping(configuration.model_dump(mode="json")) == configuration
    assert parse_mapping_result(result.model_dump_json()) == result
    validate_mapping_dimensions(configuration, result)


@pytest.mark.parametrize(("unit", "source_unit", "field", "origin"), [
    ("items/s", "%", None, "declared"), (None, "%", None, "unknown"), (None, "", "unit", "unknown"),
])
def test_declared_and_unknown_units_are_not_inferred_from_unmapped_source(tmp_path, unit, source_unit, field, origin):
    case = _case(tmp_path, metrics=_rows(unit=source_unit))
    configuration = _configured(case, _mapping(case, unit=unit, unit_field=field))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    assert result.status == "valid"
    assert {(row.unit, row.unit_origin) for row in result.metric_observations} == {(unit, origin)}


@pytest.mark.parametrize("unit", ["ghp_abcdefghijklmnop", "sk_abcdefghijklmnop", "AKIA0123456789ABCDEF"])
def test_declared_unit_reuses_existing_source_secret_minimization_before_publication(tmp_path, unit):
    case = _case(tmp_path, metrics=_rows())
    configuration = _configured(case, _mapping(case, unit=unit, unit_field=None))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    assert result.status == "valid"
    assert not metric_definitions_match_observations(configuration, result)


@pytest.mark.parametrize(("declared", "source", "label", "field"), [
    ("%", "ratio", "class A", "class"), (None, "%", "class A", "class"),
    ("%", "%", "", "class"), ("%", "%", "class A", "missing"),
])
def test_missing_class_and_incompatible_units_fail_without_partial_observations(tmp_path, declared, source, label, field):
    case = _case(tmp_path, metrics=_rows(unit=source, label=label))
    configuration = _configured(case, _mapping(case, unit=declared, class_field=field))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    assert result.status == "blocked" and result.metric_observations == ()
    assert {issue.code for issue in result.issues} >= {"metric_payload_invalid"}
    assert source not in " ".join(issue.message for issue in result.issues)


def test_v2_must_be_requested_and_unit_is_explicit_even_when_unknown(tmp_path):
    case = _case(tmp_path, metrics=_rows())
    request = _mapping(case)
    del request["schema_version"]
    with pytest.raises(ProductError):
        _configured(case, request)
    request["schema_version"] = "project-mapping-request/v2"
    del request["metric_definitions"][0]["unit"]
    with pytest.raises(ProductError):
        _configured(case, request)


def test_rehashed_unit_origin_forgery_is_rejected_against_mapping(tmp_path):
    case = _case(tmp_path, metrics=_rows())
    configuration = _configured(case, _mapping(case))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    payload = result.model_dump(mode="json")
    observation = payload["metric_observations"][0]
    observation["unit_origin"] = "declared"
    observation["observation_id"] = "p3-metric-" + canonical_project_sha256({key: value for key, value in observation.items() if key != "observation_id"})
    payload["result_sha256"] = canonical_project_sha256({key: value for key, value in payload.items() if key != "result_sha256"})
    forged = parse_mapping_result(json.dumps(payload))
    with pytest.raises(ProjectMappingError, match="provenance"):
        validate_mapping_dimensions(configuration, forged)


def test_fully_rehashed_result_cannot_publish_one_metric_in_two_units(tmp_path):
    case = _case(tmp_path, metrics=_rows())
    configuration = _configured(case, _mapping(case))
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, configuration)
    payload = result.model_dump(mode="json")
    observation = payload["metric_observations"][0]
    observation["unit"] = "ms"
    observation["observation_id"] = "p3-metric-" + canonical_project_sha256({key: value for key, value in observation.items() if key != "observation_id"})
    payload["result_sha256"] = canonical_project_sha256({key: value for key, value in payload.items() if key != "result_sha256"})
    with pytest.raises(ValueError, match="incompatible units"):
        parse_mapping_result(json.dumps(payload))


def _dimension_snapshot_payload(*, unit="%", conflicting=False, candidate_value=0.6):
    legacy = _snapshot(captured_at="2026-08-26T00:00:00Z", candidate_value=candidate_value)
    payload = legacy.model_dump(mode="python")
    observations = []
    for row in legacy.metric_observations:
        values = row.model_dump(mode="python", exclude={"observation_id"})
        values.update(class_label="nhóm A", unit=unit, unit_origin="declared")
        observations.append(MetricObservationV2(observation_id="p3-metric-" + canonical_project_sha256(values), **values))
    if conflicting:
        values = observations[0].model_dump(mode="python", exclude={"observation_id"})
        values["unit"] = "ms"
        observations.append(MetricObservationV2(observation_id="p3-metric-" + canonical_project_sha256(values), **values))
    payload.update(schema_version="project-snapshot/v2", metric_observations=tuple(sorted(observations, key=metric_sort_key)), collectors=legacy.collectors, items=legacy.items)
    state = _state_payload(**{key: payload[key] for key in (
        "project_id", "source_bundle_id", "source_bundle_sha256", "project_manifest_sha256", "file_collection_sha256",
        "git_state_sha256", "mapping_configuration_sha256", "mapping_result_sha256", "baseline_run_id",
        "collectors", "items", "metric_observations")}, schema_version="project-snapshot-state/v2")
    state_hash = canonical_project_sha256(state)
    payload.update(state_sha256=state_hash, snapshot_id="p3-snapshot-" + state_hash,
        record_sha256=_record_digest(state_sha256=state_hash, captured_at=payload["captured_at"], schema_version="project-snapshot-record/v2"))
    return payload


def test_fully_rehashed_snapshot_cannot_publish_one_metric_in_two_units():
    with pytest.raises(ValueError, match="incompatible units"):
        ProjectSnapshotV2.model_validate(_dimension_snapshot_payload(conflicting=True))


def test_comparison_blocks_unit_mismatch_and_legacy_dimension_fabrication():
    before = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload())
    after = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload(unit="ms"))
    with pytest.raises(ProjectRegressionError, match="units differ"):
        compare_project_snapshots(before, after)
    legacy = _snapshot(captured_at="2026-08-26T00:00:00Z")
    with pytest.raises(ProjectRegressionError, match="versions differ"):
        compare_project_snapshots(legacy, before)
    valid = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload(candidate_value=0.7))
    comparison = compare_project_snapshots(before, valid)
    assert isinstance(comparison, ProjectSnapshotComparisonV2)
    assert comparison.metric_changes[0].after.class_label == "nhóm A"
    assert comparison.metric_changes[0].after.unit == "%"


def test_v2_delta_uses_decimal_and_rejects_rehashed_tiny_forgery():
    observation = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload()).metric_observations[0]

    def value(number):
        payload = observation.model_dump(mode="python", exclude={"observation_id"})
        payload["metric_value"] = number
        return MetricObservationV2(observation_id="p3-metric-" + canonical_project_sha256(payload), **payload)

    assert _metric_change(value(0.8), value(0.7), version2=True).delta == -0.1
    change = _metric_change(value(0.0), value(1e-16), version2=True)
    assert change.delta == 1e-16
    payload = change.model_dump(mode="python", exclude={"metric_change_id"})
    payload["delta"] = 2e-16
    payload["metric_change_id"] = "p3-metric-change-" + canonical_project_sha256(payload)
    with pytest.raises(ValueError, match="delta does not match"):
        ProjectMetricChangeV2.model_validate(payload)


def test_rehashed_comparison_cannot_disguise_unit_mismatch_as_removed_and_added():
    before = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload())
    after = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload(candidate_value=0.7))
    mismatched = ProjectSnapshotV2.model_validate(_dimension_snapshot_payload(unit="ms", candidate_value=0.7))
    comparison = compare_project_snapshots(before, after)
    payload = comparison.model_dump(mode="json", exclude={"comparison_id", "comparison_sha256"})
    changes = [*(_metric_change(row, None, version2=True) for row in before.metric_observations),
               *(_metric_change(None, row, version2=True) for row in mismatched.metric_observations)]
    payload["metric_changes"] = [change.model_dump(mode="json") for change in sorted(changes, key=lambda change: change.metric_change_id)]
    payload["after_snapshot_id"] = mismatched.snapshot_id
    payload["after_record_sha256"] = mismatched.record_sha256
    digest = canonical_project_sha256(payload)
    payload.update(comparison_id="p3-comparison-" + digest, comparison_sha256=digest)
    with pytest.raises(ValueError, match="units differ"):
        ProjectSnapshotComparisonV2.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("label", ["không bệnh", "%", "items/s", "µg/mL", "class A"])
def test_visible_dimension_labels_allow_spaces_and_scientific_punctuation(label):
    assert metric_label(label, label="metric label") == label


@pytest.mark.parametrize("label", ["", " label", "label\n", "x\u034fy", "b\u0301\U00011f41"])
def test_dimension_labels_reject_noncanonical_or_invisible_text(label):
    with pytest.raises(ValueError):
        metric_label(label, label="metric label")

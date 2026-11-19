"""Strict JSON-container adapter for the existing P3 mapping contract."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Final, cast

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.import_content import secret_occurrences
from aletheia_lab.project.mapping import (
    AnyMappingConfiguration,
    AnyMappingResult,
    AnyMetricDefinition,
    AnyMetricSourceMapping,
    DatasetTargetMapping,
    MetricDefinition,
    MetricDefinitionV2,
    MetricSourceMapping,
    MetricSourceMappingV2,
    RunMapping,
    build_project_mapping_configuration,
    parse_mapping_configuration,
    validate_mapping_dimensions,
)
from aletheia_lab.project.regression import AnySnapshotComparison

_MAPPING_KEYS: Final[frozenset[str]] = frozenset(
    {"target", "metric_sources", "runs", "baseline_run_id", "metric_definitions"}
)
_MAPPING_INVALID_MESSAGE: Final[str] = "The project mapping is invalid or incomplete."


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("mapping node must be an object with text keys")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("mapping collection must be a JSON array")
    return value


def _metric_mapping(value: object, *, version2: bool = False) -> AnyMetricSourceMapping:
    payload = dict(_object(value))
    if "records_path" in payload:
        payload["records_path"] = tuple(_array(payload["records_path"]))
    model = MetricSourceMappingV2 if version2 else MetricSourceMapping
    return model.model_validate(payload)


def _run_mapping(value: object) -> RunMapping:
    payload = dict(_object(value))
    if "config_item_ids" in payload:
        payload["config_item_ids"] = tuple(_array(payload["config_item_ids"]))
    return RunMapping.model_validate(payload)


def _metric_definition(value: object, *, version2: bool = False) -> AnyMetricDefinition:
    model = MetricDefinitionV2 if version2 else MetricDefinition
    return model.model_validate(_object(value))


def _parse_mapping(
    mapping: dict[str, object],
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
) -> AnyMappingConfiguration | None:
    try:
        payload = _object(mapping)
        version2 = payload.get("schema_version") == "project-mapping-request/v2"
        expected = _MAPPING_KEYS | {"schema_version"} if version2 else _MAPPING_KEYS
        if set(payload) != expected:
            raise ValueError("mapping keys do not match the P3 adapter contract")
        target = DatasetTargetMapping.model_validate(_object(payload["target"]))
        metrics = tuple(_metric_mapping(value, version2=version2) for value in _array(payload["metric_sources"]))
        runs = tuple(_run_mapping(value) for value in _array(payload["runs"]))
        definitions = tuple(
            _metric_definition(value, version2=version2) for value in _array(payload["metric_definitions"])
        )
        if not definitions:
            raise ValueError("metric_definitions must not be empty")
        baseline_run_id = payload["baseline_run_id"]
        if not isinstance(baseline_run_id, str):
            raise TypeError("baseline_run_id must be text")
        return build_project_mapping_configuration(
            project_id=project_id,
            project_bundle_id=project_bundle_id,
            file_collection_sha256=file_collection_sha256,
            target=target,
            metric_sources=metrics,
            runs=runs,
            baseline_run_id=baseline_run_id,
            metric_definitions=definitions,
            schema_version="project-mapping-configuration/v2" if version2 else "project-mapping-configuration/v1",
        )
    except (KeyError, TypeError, ValueError):
        return None


def build_confirm_mapping(
    mapping: dict[str, object],
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
) -> AnyMappingConfiguration:
    """Bind one JSON-shaped request to P3 identities without a second schema."""

    configuration = _parse_mapping(
        mapping,
        project_id=project_id,
        project_bundle_id=project_bundle_id,
        file_collection_sha256=file_collection_sha256,
    )
    if configuration is None:
        raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
    return configuration


def load_stored_mapping(value: object) -> AnyMappingConfiguration | None:
    """Rehydrate one strict P3 mapping from its canonical JSON-shaped payload."""

    try:
        return parse_mapping_configuration(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError):
        return None


def metric_definitions_match_observations(
    configuration: AnyMappingConfiguration,
    result: AnyMappingResult,
) -> bool:
    """Require one explicit definition for every observed metric name."""

    observed = {value.metric_name for value in result.metric_observations}
    declared = {value.metric_name for value in configuration.metric_definitions}
    try:
        validate_mapping_dimensions(configuration, result)
    except ValueError:
        return False
    from aletheia_lab.product.candidates import _contains_sensitive_text

    if any(_contains_sensitive_text(label) or secret_occurrences(label) for observation in result.metric_observations
           for label in (getattr(observation, "class_label", None), getattr(observation, "unit", None))
           if isinstance(label, str)):
        return False
    return bool(observed) and observed == declared


def adverse_metric_change_ids(
    comparison: AnySnapshotComparison,
    definitions: tuple[AnyMetricDefinition, ...],
) -> tuple[str, ...]:
    """Classify adverse deltas with decimal arithmetic over persisted numbers."""

    by_name = {value.metric_name: value for value in definitions}
    adverse: list[str] = []
    for change in comparison.metric_changes:
        if change.before is None or change.after is None:
            continue
        definition = by_name.get(change.after.metric_name)
        if definition is None:
            continue
        delta = Decimal(str(change.after.metric_value)) - Decimal(str(change.before.metric_value))
        threshold = Decimal(str(definition.regression_threshold))
        if (definition.direction == "lower_is_better" and delta > 0 and delta >= threshold) or (
            definition.direction == "higher_is_better" and delta < 0 and -delta >= threshold
        ):
            adverse.append(change.metric_change_id)
    return tuple(sorted(adverse))


def rebind_stored_mapping(
    configuration: AnyMappingConfiguration,
    before_bundle: ProjectBundle,
    after_bundle: ProjectBundle,
    *,
    file_collection_sha256: str,
) -> AnyMappingConfiguration:
    """Rebind validated mapping choices to the same paths in a new import state."""

    if not (configuration.project_id == before_bundle.project_id == after_bundle.project_id):
        raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
    before_items = {item.project_item_id: item for item in before_bundle.items}
    after_items = {item.relative_path: item for item in after_bundle.items}

    def translated_item_id(item_id: str, expected_type: str) -> str:
        before = before_items.get(item_id)
        if before is None or before.source_type != expected_type:
            raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
        after = after_items.get(before.relative_path)
        if after is None or after.source_type != expected_type:
            raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
        return after.project_item_id

    target = DatasetTargetMapping(
        mapping_id=configuration.target.mapping_id,
        project_item_id=translated_item_id(
            configuration.target.project_item_id,
            "dataset",
        ),
        target_field=configuration.target.target_field,
        identifier_field=configuration.target.identifier_field,
    )
    metric_sources = tuple(
        type(metric).model_validate({**metric.model_dump(mode="python"),
            "project_item_id": translated_item_id(metric.project_item_id, "metrics")})
        for metric in configuration.metric_sources
    )
    runs = tuple(
        RunMapping(
            run_id=run.run_id,
            config_item_ids=tuple(
                translated_item_id(item_id, "config") for item_id in run.config_item_ids
            ),
        )
        for run in configuration.runs
    )
    return build_project_mapping_configuration(
        project_id=after_bundle.project_id,
        project_bundle_id=after_bundle.project_bundle_id,
        file_collection_sha256=file_collection_sha256,
        target=target,
        metric_sources=metric_sources,
        runs=runs,
        baseline_run_id=configuration.baseline_run_id,
        metric_definitions=configuration.metric_definitions,
        schema_version=configuration.schema_version,
    )

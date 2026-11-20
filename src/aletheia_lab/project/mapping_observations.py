"""Parse metric records and produce source-bound numeric observations."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from typing import TYPE_CHECKING

from .identity import canonical_project_sha256
from .mapping_identifiers import mapping_identifier as _identifier
from .mapping_identifiers import metric_label

if TYPE_CHECKING:
    from .contracts import ProjectItem
    from .importer import ProjectImportArtifact
    from .mapping import AnyMetricDefinition, AnyMetricObservation, AnyMetricSourceMapping

class ProjectMappingError(ValueError):
    """Raised when a valid mapping result is required but unavailable."""


def _record_value(record: dict[str, object], field: str) -> object:
    if field not in record:
        raise KeyError(field)
    return record[field]


def _metric_records(mapping: AnyMetricSourceMapping, content: bytes) -> list[dict[str, object]]:
    try:
        text = content.decode("utf-8", errors="strict")
        if mapping.format == "csv":
            parsed = list(csv.DictReader(io.StringIO(text, newline=""), strict=True))
            return [dict(record) for record in parsed]
        payload: object = json.loads(text)
        for component in mapping.records_path:
            if not isinstance(payload, dict) or component not in payload:
                raise ValueError("records path is absent")
            payload = payload[component]
        if not isinstance(payload, list) or any(not isinstance(record, dict) for record in payload):
            raise ValueError("metric records must be a list of objects")
        return [dict(record) for record in payload]
    except (UnicodeDecodeError, csv.Error, json.JSONDecodeError, ValueError) as exc:
        raise ProjectMappingError("metric payload invalid") from exc


def _finite_number(value: object) -> float:
    if isinstance(value, bool):
        raise ProjectMappingError("metric value invalid")
    try:
        number = float(value) if isinstance(value, str | int | float) else math.nan
    except ValueError as exc:
        raise ProjectMappingError("metric value invalid") from exc
    if not math.isfinite(number):
        raise ProjectMappingError("metric value invalid")
    return 0.0 if number == 0.0 else number


def _step_number(value: object) -> int:
    if isinstance(value, bool):
        raise ProjectMappingError("metric step invalid")
    if isinstance(value, int):
        step = value
    elif (isinstance(value, str) and re.fullmatch(r"\d+", value) is not None) or (
        isinstance(value, float) and value.is_integer()
    ):
        step = int(value)
    else:
        raise ProjectMappingError("metric step invalid")
    if step < 0:
        raise ProjectMappingError("metric step invalid")
    return step


def _metric_observations(
    mapping: AnyMetricSourceMapping, item: ProjectItem, artifact: ProjectImportArtifact,
    definitions: tuple[AnyMetricDefinition, ...] = (),
) -> tuple[AnyMetricObservation, ...]:
    from .mapping import (
        MetricDefinitionV2,
        MetricObservation,
        MetricObservationV2,
        MetricSourceMappingV2,
    )

    observations: list[AnyMetricObservation] = []
    units = {value.metric_name: value.unit for value in definitions if isinstance(value, MetricDefinitionV2)}
    for index, record in enumerate(_metric_records(mapping, artifact.content)):
        try:
            raw_name = _record_value(record, mapping.metric_name_field)
            raw_run = _record_value(record, mapping.run_id_field)
            raw_value = _record_value(record, mapping.metric_value_field)
            raw_step = None if mapping.step_field is None else _record_value(record, mapping.step_field)
        except KeyError as exc:
            raise ProjectMappingError("metric field missing") from exc
        try:
            if not isinstance(raw_name, str) or not isinstance(raw_run, str):
                raise TypeError("metric identity fields must be strings")
            name = _identifier(raw_name, label="metric_name")
            run_id = _identifier(raw_run, label="run_id")
        except (TypeError, ValueError) as exc:
            raise ProjectMappingError("metric identity invalid") from exc
        value = _finite_number(raw_value)
        step = None if raw_step is None else _step_number(raw_step)
        provisional = {
            "source_mapping_id": mapping.mapping_id,
            "project_item_id": item.project_item_id,
            "run_id": run_id,
            "metric_name": name,
            "metric_value": value,
            "step": step,
            "source_record_index": index,
        }
        observation_model: type[MetricObservation] | type[MetricObservationV2] = MetricObservation
        if isinstance(mapping, MetricSourceMappingV2):
            if name not in units:
                raise ProjectMappingError("metric unit definition missing")
            try:
                unit = units[name]
                class_label = None
                if mapping.class_field is not None:
                    raw_class = _record_value(record, mapping.class_field)
                    if not isinstance(raw_class, str):
                        raise ValueError("metric class must be text")
                    class_label = metric_label(raw_class, label="metric class")
                if mapping.unit_field is not None:
                    raw_unit = _record_value(record, mapping.unit_field)
                    if raw_unit == "" and mapping.format == "csv":
                        raw_unit = None
                    if raw_unit is not None:
                        if not isinstance(raw_unit, str):
                            raise ValueError("metric unit must be text")
                        metric_label(raw_unit, label="metric unit", max_length=64)
                    if raw_unit != unit:
                        raise ValueError("metric source unit differs from declared unit")
            except (KeyError, ValueError) as exc:
                raise ProjectMappingError("metric dimension invalid") from exc
            provisional.update(class_label=class_label, unit=unit,
                unit_origin="unknown" if unit is None else "source" if mapping.unit_field is not None else "declared")
            observation_model = MetricObservationV2
        observations.append(
            observation_model.model_validate(dict(
                observation_id=f"p3-metric-{canonical_project_sha256(provisional)}",
                **provisional,
            ))
        )
    return tuple(observations)



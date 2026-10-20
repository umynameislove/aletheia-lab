from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import cast

import pytest

from aletheia_lab.product import ProductError
from aletheia_lab.product.mapping import build_confirm_mapping
from aletheia_lab.project.mapping import ProjectMappingConfiguration

_PROJECT_ID = f"p3-project-{'1' * 64}"
_BUNDLE_ID = f"p3-bundle-{'2' * 64}"
_COLLECTION_SHA256 = "3" * 64
_DATASET_ID = f"p3-item-{'4' * 64}"
_METRICS_A_ID = f"p3-item-{'5' * 64}"
_METRICS_B_ID = f"p3-item-{'6' * 64}"
_CONFIG_ID = f"p3-item-{'7' * 64}"


def _mapping() -> dict[str, object]:
    return {
        "target": {
            "mapping_id": "target",
            "project_item_id": _DATASET_ID,
            "target_field": "label",
            "identifier_field": "row_id",
        },
        "metric_sources": [
            {
                "mapping_id": "metrics-b",
                "project_item_id": _METRICS_B_ID,
                "format": "json",
                "records_path": ["records"],
                "metric_name_field": "name",
                "metric_value_field": "value",
                "run_id_field": "run",
                "step_field": "step",
            },
            {
                "mapping_id": "metrics-a",
                "project_item_id": _METRICS_A_ID,
                "format": "csv",
                "metric_name_field": "metric",
                "metric_value_field": "score",
                "run_id_field": "run_id",
            },
        ],
        "runs": [
            {"run_id": "candidate", "config_item_ids": [_CONFIG_ID]},
            {"run_id": "baseline", "config_item_ids": []},
        ],
        "baseline_run_id": "baseline",
        "metric_definitions": [
            {
                "metric_name": "loss",
                "direction": "lower_is_better",
                "regression_threshold": 0.08,
            }
        ],
    }


def _build(mapping: dict[str, object]) -> ProjectMappingConfiguration:
    return build_confirm_mapping(
        mapping,
        project_id=_PROJECT_ID,
        project_bundle_id=_BUNDLE_ID,
        file_collection_sha256=_COLLECTION_SHA256,
    )


def test_mapping_adapter_uses_exact_p3_models_and_canonical_order() -> None:
    first = _build(_mapping())
    reversed_mapping = _mapping()
    metric_sources = cast(list[object], reversed_mapping["metric_sources"])
    runs = cast(list[object], reversed_mapping["runs"])
    reversed_mapping["metric_sources"] = list(reversed(metric_sources))
    reversed_mapping["runs"] = list(reversed(runs))
    second = _build(reversed_mapping)

    assert first == second
    assert first.mapping_sha256 == second.mapping_sha256
    assert [value.mapping_id for value in first.metric_sources] == ["metrics-a", "metrics-b"]
    assert first.metric_sources[1].records_path == ("records",)
    assert [value.run_id for value in first.runs] == ["baseline", "candidate"]
    assert first.runs[1].config_item_ids == (_CONFIG_ID,)
    assert first.metric_definitions[0].metric_name == "loss"


def _add_extra_top_level_key(value: dict[str, object]) -> None:
    value["extra"] = "not-allowed"


def _remove_baseline(value: dict[str, object]) -> None:
    value.pop("baseline_run_id")


def _remove_metric_definitions(value: dict[str, object]) -> None:
    value.pop("metric_definitions")


def _use_invalid_direction(value: dict[str, object]) -> None:
    definitions = cast(list[dict[str, object]], value["metric_definitions"])
    definitions[0]["direction"] = "unknown"


def _use_negative_threshold(value: dict[str, object]) -> None:
    definitions = cast(list[dict[str, object]], value["metric_definitions"])
    definitions[0]["regression_threshold"] = -0.01


def _duplicate_metric_definition(value: dict[str, object]) -> None:
    definitions = cast(list[object], value["metric_definitions"])
    value["metric_definitions"] = [definitions[0], deepcopy(definitions[0])]


def _use_non_json_metric_collection(value: dict[str, object]) -> None:
    value["metric_sources"] = ()


def _add_extra_target_key(value: dict[str, object]) -> None:
    _target(value)["unknown"] = "not-allowed"


def _make_target_fields_ambiguous(value: dict[str, object]) -> None:
    _target(value)["identifier_field"] = "label"


def _duplicate_metric_source(value: dict[str, object]) -> None:
    metric_sources = cast(list[object], value["metric_sources"])
    value["metric_sources"] = [metric_sources[0], deepcopy(metric_sources[0])]


@pytest.mark.parametrize(
    "mutate",
    [
        _add_extra_top_level_key,
        _remove_baseline,
        _remove_metric_definitions,
        _use_invalid_direction,
        _use_negative_threshold,
        _duplicate_metric_definition,
        _use_non_json_metric_collection,
        _add_extra_target_key,
        _make_target_fields_ambiguous,
        _duplicate_metric_source,
    ],
)
def test_mapping_adapter_rejects_each_non_p3_shape(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    mapping = deepcopy(_mapping())
    mutate(mapping)

    with pytest.raises(ProductError) as captured:
        _build(mapping)

    assert captured.value.code == "mapping_invalid"
    assert captured.value.__context__ is None


def test_mapping_error_does_not_retain_private_input() -> None:
    mapping = _mapping()
    mapping["private_note"] = r"C:\private\mapping\secret.txt"

    with pytest.raises(ProductError) as captured:
        _build(mapping)

    assert captured.value.code == "mapping_invalid"
    assert "private" not in captured.value.safe_message.lower()
    assert captured.value.__context__ is None


def _target(mapping: dict[str, object]) -> dict[str, object]:
    target = mapping["target"]
    assert isinstance(target, dict)
    return cast(dict[str, object], target)

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from aletheia_lab.product import ProductService


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _candidates(preview: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    return cast(dict[str, list[dict[str, object]]], preview["mapping_candidates"])


def test_preview_mapping_candidates_match_the_shared_ui_contract(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _write(source_root / "dataset.csv", "entity_id,target\n1,0\n2,1\n")
    _write(source_root / "sensitive" / "subjects.csv", "patient_ssn,target\n1,0\n")
    _write(
        source_root / "resolved" / "metrics.json",
        json.dumps(
            {
                "runs": [
                    {"run_id": "run-before", "name": "loss", "value": 0.5}
                ],
                "records": [
                    {"run_id": "run-after", "name": "loss", "value": 0.6}
                ],
            }
        ),
    )
    _write(
        source_root / "csv" / "metrics.csv",
        "run,metric,val\nrun-before,loss,0.5\nrun-after,loss,0.6\n",
    )
    _write(
        source_root / "flat" / "metrics.json",
        json.dumps([{"run_id": "run-after", "name": "accuracy", "value": 0.8}]),
    )
    _write(
        source_root / "sensitive" / "metrics.csv",
        "run,name,value\nrun-after,patient_ssn,0.8\n",
    )
    _write(source_root / "ambiguous" / "metrics.json", json.dumps({"k": "v"}))
    _write(source_root / "config.json", json.dumps({"seed": 42}))

    preview = ProductService(tmp_path / "store").preview_import(str(source_root.resolve()))
    candidates = _candidates(preview)

    assert set(candidates) == {"targets", "metric_sources", "configs", "runs"}
    assert preview["withheld_count"] == 0
    issue = cast(list[dict[str, object]], preview["warnings"])[0]
    assert set(issue) == {
        "code",
        "severity",
        "stage",
        "message",
        "relative_path",
        "occurrences",
    }

    targets = candidates["targets"]
    available_target = next(value for value in targets if value["available"] is True)
    assert available_target["field_names"] == ["entity_id", "target"]
    assert isinstance(available_target["mapping_id"], str)
    sensitive_target = next(value for value in targets if value["available"] is False)
    assert sensitive_target == {
        "project_item_id": sensitive_target["project_item_id"],
        "label": "A dataset with withheld field labels",
        "mapping_id": None,
        "available": False,
        "unavailable_reason": "sensitive_fields_withheld",
        "relative_path": None,
        "field_names": [],
    }

    metrics = {str(value["relative_path"]): value for value in candidates["metric_sources"]}
    assert metrics["csv/metrics.csv"]["format"] == "csv"
    assert metrics["csv/metrics.csv"]["records_path_candidates"] == []
    assert metrics["csv/metrics.csv"]["run_id_field_candidates"] == ["run"]
    assert metrics["csv/metrics.csv"]["metric_name_candidates"] == ["loss"]
    assert metrics["flat/metrics.json"]["records_path_candidates"] == [[]]
    assert metrics["flat/metrics.json"]["metric_name_candidates"] == ["accuracy"]
    assert metrics["ambiguous/metrics.json"]["available"] is True
    assert metrics["ambiguous/metrics.json"]["records_path_candidates"] == []
    assert metrics["ambiguous/metrics.json"]["run_id_field_candidates"] == []
    assert metrics["ambiguous/metrics.json"]["metric_name_candidates"] == []
    assert metrics["resolved/metrics.json"]["records_path_candidates"] == [
        ["records"],
        ["runs"],
    ]
    assert metrics["resolved/metrics.json"]["metric_name_candidates"] == ["loss"]
    sensitive_metric = next(
        value
        for value in candidates["metric_sources"]
        if value["available"] is False
    )
    assert sensitive_metric["label"] == "A metric source with withheld field labels"
    assert sensitive_metric["relative_path"] is None
    assert sensitive_metric["metric_name_candidates"] == []

    configs = candidates["configs"]
    assert len(configs) == 1
    assert configs[0]["relative_path"] == "config.json"
    config_id = configs[0]["project_item_id"]
    runs = candidates["runs"]
    assert [value["run_id"] for value in runs] == ["run-after", "run-before"]
    assert all(value["available_config_item_ids"] == [config_id] for value in runs)
    assert all(value["source_metric_item_ids"] for value in runs)
    assert "patient_ssn" not in json.dumps(preview)


def test_blocked_preview_has_empty_mapping_candidates(tmp_path: Path) -> None:
    source_root = tmp_path / "empty"
    source_root.mkdir()

    preview = ProductService(tmp_path / "store").preview_import(str(source_root.resolve()))

    assert _candidates(preview) == {
        "targets": [],
        "metric_sources": [],
        "configs": [],
        "runs": [],
    }
    blockers = cast(list[dict[str, object]], preview["blockers"])
    assert blockers
    assert all(value["severity"] == "blocker" for value in blockers)

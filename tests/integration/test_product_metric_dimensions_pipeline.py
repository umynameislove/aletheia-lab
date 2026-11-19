"""Local imports compare class-specific observations and preserve immutable V2 history."""
from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.view import ProductViewV2
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.snapshots import AnyProjectSnapshot

pytestmark = pytest.mark.integration
_RUNS = [f"lần-{number}" for number in range(5)]
_CLASSES = ["âm tính", "dương tính"]


def _metrics(root: Path, *, changed: bool = False, unit: str = "%") -> None:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["run", "name", "value", "class", "unit", "step"])
    for run in _RUNS:
        for label in _CLASSES:
            writer.writerow([run, "f1", 0.7 if changed and run == _RUNS[-1] and label == _CLASSES[-1] else 0.8,
                             label, unit, 1])
    (root / "metrics.csv").write_text(stream.getvalue(), encoding="utf-8")


def _mapping(preview):
    candidates = preview["mapping_candidates"]
    target, metric = candidates["targets"][0], candidates["metric_sources"][0]
    return {
        "schema_version": "project-mapping-request/v2",
        "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                   "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                            "format": "csv", "metric_name_field": "name", "metric_value_field": "value",
                            "run_id_field": "run", "step_field": "step", "class_field": "class", "unit_field": "unit"}],
        "runs": [{"run_id": run, "config_item_ids": []} for run in _RUNS],
        "baseline_run_id": _RUNS[0],
        "metric_definitions": [{"metric_name": "f1", "direction": "higher_is_better",
                                "regression_threshold": 0.1, "unit": "%"}],
    }


def test_five_unicode_runs_two_classes_refresh_history_reports_and_failed_unit_change(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n")
    _metrics(root)
    for arguments in (("init", "-q"), ("config", "user.name", "Synthetic dimensions"),
                      ("config", "user.email", "dimensions@example.invalid"),
                      ("add", "dataset.csv", "metrics.csv"), ("commit", "-qm", "Synthetic metric dimensions")):
        subprocess.run(("git", *arguments), cwd=root, check=True, capture_output=True)
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(root))
    confirmed = service.confirm_import(preview["preview_id"], _mapping(preview))
    project_id, before_id = confirmed["project_id"], confirmed["snapshot_id"]
    before_view = ProductViewV2.model_validate_json(json.dumps(service.analyze_observations(project_id, before_id, "What is observed?")))
    with ProjectStore(store_root) as store:
        before = store.load(before_id)
        assert isinstance(before, AnyProjectSnapshot)
        assert len(before.metric_observations) == 10
        before_bytes = before.model_dump_json()
    _metrics(root, changed=True)
    refreshed = service.refresh(project_id)
    after_id = refreshed["snapshot_id"]
    assert after_id != before_id
    view = ProductViewV2.model_validate_json(json.dumps(service.analyze_observations(project_id, after_id, "Which class changed?")))
    assert len(view.snapshot.metric_changes) == 1
    change = view.snapshot.metric_changes[0]
    assert change.before.class_label == change.after.class_label == _CLASSES[-1]
    assert change.before.unit == change.after.unit == "%"
    assert change.before.unit_origin == change.after.unit_origin == "source"
    assert change.after.run_id == _RUNS[-1] and change.delta == -0.1
    assert change.adverse_status == "adverse"
    assert service.view(before_view.result.id) == before_view.model_dump(mode="json")
    assert service.view(view.result.id) == view.model_dump(mode="json")
    report = service.export_report(view.result.id, "markdown")
    assert "dương tính" in report.decode() and "unit_origin=`source`" in report.decode()
    _metrics(root, changed=True, unit="ratio")
    with pytest.raises(ProductError):
        service.refresh(project_id)
    assert service.view(view.result.id) == view.model_dump(mode="json")
    with ProjectStore(store_root) as store:
        assert store.load(before_id).model_dump_json() == before_bytes
        assert len(store.load(after_id).metric_observations) == 10

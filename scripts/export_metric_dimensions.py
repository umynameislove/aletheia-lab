"""Capture a real synthetic V2 producer journey in a new consumer review directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from aletheia_lab.product.service import ProductService
from aletheia_lab.product.view import ProductViewV2
from aletheia_lab.project.persistence import ProjectStore


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    outputs: dict[str, bytes] = {}

    def record(name: str, value: object) -> None:
        outputs[name] = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()

    with tempfile.TemporaryDirectory(prefix="metric-dimensions-producer-") as owned:
        root, store_root = Path(owned) / "source", Path(owned) / "store"
        root.mkdir()
        (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8", newline="\n")
        runs = [f"lần-{index}" for index in range(5)]
        rows = [f"{run},f1,{value},{label},%,1" for run in runs for label, value in (("âm tính", "0.8"), ("dương tính", "0.8"))]
        before = "run,name,value,class,unit,step\n" + "\n".join(rows) + "\n"
        (root / "metrics.csv").write_text(before, encoding="utf-8", newline="\n")
        for arguments in (("init", "-q"), ("config", "user.name", "Synthetic producer"),
                          ("config", "user.email", "producer@example.invalid"),
                          ("add", "dataset.csv", "metrics.csv"), ("commit", "-qm", "Synthetic metric dimensions")):
            subprocess.run(("git", *arguments), cwd=root, check=True, capture_output=True)
        service = ProductService(store_root)
        preview = service.preview_import(str(root))
        target, metric = (preview["mapping_candidates"][key][0] for key in ("targets", "metric_sources"))
        mapping = {
            "schema_version": "project-mapping-request/v2",
            "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                       "target_field": "target", "identifier_field": "id"},
            "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
                "run_id_field": "run", "step_field": "step", "class_field": "class", "unit_field": "unit"}],
            "runs": [{"run_id": run, "config_item_ids": []} for run in runs],
            "baseline_run_id": runs[0],
            "metric_definitions": [{"metric_name": "f1", "direction": "higher_is_better", "regression_threshold": 0.1, "unit": "%"}],
        }
        confirmed = service.confirm_import(preview["preview_id"], mapping)
        before_id = confirmed["snapshot_id"]
        after = before.replace("lần-4,f1,0.8,dương tính", "lần-4,f1,0.7,dương tính")
        (root / "metrics.csv").write_text(after, encoding="utf-8", newline="\n")
        refreshed = service.refresh(confirmed["project_id"])
        view = ProductViewV2.model_validate_json(json.dumps(service.analyze_observations(
            confirmed["project_id"], refreshed["snapshot_id"], "Which class changed under the declared threshold?")))
        assert len(view.snapshot.metric_changes) == 1
        change = view.snapshot.metric_changes[0]
        assert change.after.class_label == "dương tính" and change.delta == -0.1 and change.adverse_status == "adverse"
        shape = ProductViewV2.model_json_schema()
        Draft202012Validator.check_schema(shape)
        Draft202012Validator(shape).validate(view.model_dump(mode="json"))
        record("mapping-request.json", mapping)
        record("product-view.json", view.model_dump(mode="json"))
        record("product-view.schema.json", shape)
        record("confirmed.json", confirmed)
        record("refreshed.json", refreshed)
        with ProjectStore(store_root) as store:
            for name, identity in (("snapshot-before", before_id), ("snapshot-after", refreshed["snapshot_id"])):
                model = store.load(identity)
                record(name + ".json", model.model_dump(mode="json"))
                record(name + ".schema.json", type(model).model_json_schema())
        outputs["report.md"] = service.export_report(view.result.id, "markdown")
        outputs["metrics-before.csv"], outputs["metrics-after.csv"] = before.encode(), after.encode()
        record("capture.json", {"scope": "Actual disposable local Git import, mapping, refresh, analysis and report; five Unicode runs and two classes. Snapshot IDs are tied to this capture's granted root; this is not a hosted/browser/joint sign-off.",
                                "schema": "p6-product-view/v2", "observations_per_snapshot": 10,
                                "changes": 1, "unit_conversion": False})
    for name, payload in outputs.items():
        (destination / name).write_bytes(payload)
    (destination / "SHA256SUMS.txt").write_text("".join(f"{hashlib.sha256(payload).hexdigest()}  {name}\n" for name, payload in sorted(outputs.items())), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)


if __name__ == "__main__":
    main()

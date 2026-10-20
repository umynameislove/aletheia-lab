"""Self-contained offline ProductService demonstration over synthetic data."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final, cast

from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.product.service import ProductService
from aletheia_lab.project.identity import content_sha256

_DEMO_SCHEMA_VERSION: Final[str] = "p6-product-demo/v1"
_REPORT_EXTENSIONS: Final[dict[str, str]] = {
    "json": "json",
    "markdown": "md",
    "pdf": "pdf",
}


class ProductDemoError(ValueError):
    """Raised when the bounded synthetic demo cannot complete safely."""


def _git(root: Path, *arguments: str) -> None:
    try:
        subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        raise ProductDemoError("the synthetic Git project could not be prepared") from None


def _prepare_workspace(workspace: Path) -> tuple[Path, Path, Path]:
    if workspace.is_symlink() or (workspace.exists() and not workspace.is_dir()):
        raise ProductDemoError("the demo workspace must be a real directory")
    workspace.mkdir(parents=True, exist_ok=True)
    if any(workspace.iterdir()):
        raise ProductDemoError("the demo workspace must be empty")
    source_root = workspace / "synthetic-project"
    report_root = workspace / "reports"
    source_root.mkdir()
    report_root.mkdir()
    return source_root.resolve(), (workspace / "store").resolve(), report_root.resolve()


def _create_synthetic_project(source_root: Path) -> None:
    _git(source_root, "init", "-q")
    _git(source_root, "config", "user.name", "Aletheia Product Demo")
    _git(source_root, "config", "user.email", "product-demo@example.invalid")
    _git(source_root, "config", "core.autocrlf", "false")
    (source_root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    (source_root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    _git(source_root, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(source_root, "commit", "-q", "-m", "synthetic baseline")


def _demo_mapping(preview: dict[str, object]) -> dict[str, object]:
    candidates = cast(dict[str, list[dict[str, object]]], preview["mapping_candidates"])
    try:
        target = candidates["targets"][0]
        metric = candidates["metric_sources"][0]
        config = candidates["configs"][0]
    except (KeyError, IndexError, TypeError):
        raise ProductDemoError(
            "the synthetic project did not produce the expected mappings"
        ) from None
    return {
        "target": {
            "mapping_id": target["mapping_id"],
            "project_item_id": target["project_item_id"],
            "target_field": "target",
            "identifier_field": "id",
        },
        "metric_sources": [
            {
                "mapping_id": metric["mapping_id"],
                "project_item_id": metric["project_item_id"],
                "format": metric["format"],
                "metric_name_field": "name",
                "metric_value_field": "value",
                "run_id_field": "run",
                "step_field": None,
            }
        ],
        "runs": [
            {"run_id": "baseline", "config_item_ids": []},
            {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]},
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


def _record_adverse_metric(source_root: Path) -> None:
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "synthetic adverse metric")


def _source_bytes(source_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    }


def _publish_reports(
    service: ProductService,
    result_id: str,
    report_root: Path,
) -> dict[str, object]:
    exports: dict[str, object] = {}
    for report_format, extension in _REPORT_EXTENSIONS.items():
        payload = service.export_report(result_id, report_format)
        relative_path = f"product-report.{extension}"
        publish_immutable_file(report_root / relative_path, payload)
        exports[report_format] = {
            "relative_path": f"reports/{relative_path}",
            "byte_size": len(payload),
            "sha256": content_sha256(payload),
        }
    return exports


def _run_product_demo(workspace: Path) -> dict[str, object]:
    source_root, store_root, report_root = _prepare_workspace(workspace)
    _create_synthetic_project(source_root)
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _demo_mapping(preview))
    _record_adverse_metric(source_root)
    refreshed = service.refresh(str(confirmed["project_id"]))
    if refreshed.get("status") != "new_snapshot":
        raise ProductDemoError("the synthetic metric change did not create a new snapshot")
    view = service.analyze_mock(
        str(refreshed["project_id"]),
        str(refreshed["snapshot_id"]),
        "Summarize the stored synthetic regression evidence.",
    )
    claims = cast(list[dict[str, object]], view["claims"])
    result = cast(dict[str, object], view["result"])
    if not claims:
        raise ProductDemoError("the synthetic analysis did not produce a selectable claim")
    followed = service.follow_up(
        str(result["id"]),
        "claim",
        str(claims[0]["id"]),
        "What remains uncertain within the stored evidence?",
    )
    followed_result = cast(dict[str, object], followed["result"])
    result_id = str(followed_result["id"])
    if service.view(result_id) != followed:
        raise ProductDemoError("the persisted follow-up did not reload identically")
    graph = cast(dict[str, list[dict[str, object]]], followed["graph"])
    evidence = cast(list[dict[str, object]], followed["evidence"])
    if not graph["nodes"] or not graph["edges"] or not evidence:
        raise ProductDemoError("the diagnosis projection is missing graph or evidence data")
    exports = _publish_reports(service, result_id, report_root)
    before_delete = _source_bytes(source_root)
    receipt = service.delete_project(str(refreshed["project_id"]))
    if _source_bytes(source_root) != before_delete:
        raise ProductDemoError("project deletion modified the synthetic source")
    runtime = cast(dict[str, object], followed["runtime"])
    if runtime.get("external_call") is not False:
        raise ProductDemoError("the synthetic demo left the offline runtime boundary")
    return {
        "schema_version": _DEMO_SCHEMA_VERSION,
        "offline": True,
        "project_id": refreshed["project_id"],
        "snapshot_id": refreshed["snapshot_id"],
        "result_id": result_id,
        "evidence_count": len(evidence),
        "graph": {"node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])},
        "exports": exports,
        "delete_receipt": receipt,
        "source_preserved_after_delete": True,
    }


def run_product_demo(workspace: Path) -> dict[str, object]:
    """Run the complete provider-free product flow in one empty workspace."""

    try:
        return _run_product_demo(workspace)
    except ProductDemoError:
        raise
    except Exception:
        raise ProductDemoError("the offline product demo failed safely") from None


__all__ = ["ProductDemoError", "run_product_demo"]

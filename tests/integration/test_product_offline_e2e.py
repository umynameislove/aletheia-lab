from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from aletheia_lab.cli import app
from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.project import ProjectStore

pytestmark = pytest.mark.integration

_runner = CliRunner()


def _reject_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline product demo attempted a network operation")


def _source_bytes(source_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    }


def test_product_demo_runs_complete_offline_flow_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "socket", _reject_network)
    monkeypatch.setattr(socket, "create_connection", _reject_network)
    workspace = tmp_path / "demo"

    result = _runner.invoke(app, ["product-demo", str(workspace)])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["schema_version"] == "p6-product-demo/v1"
    assert summary["offline"] is True
    assert summary["source_preserved_after_delete"] is True
    assert summary["evidence_count"] > 0
    assert summary["graph"]["node_count"] > 0
    assert summary["graph"]["edge_count"] > 0
    assert summary["delete_receipt"] == {
        "project_id": summary["project_id"],
        "status": "deleted",
        "deleted_count": summary["delete_receipt"]["deleted_count"],
        "retained_shared_count": 0,
    }
    assert summary["delete_receipt"]["deleted_count"] > 0

    source_root = workspace / "synthetic-project"
    source_after = _source_bytes(source_root)
    assert source_after
    assert (
        (source_root / "metrics.csv").read_text(encoding="utf-8").endswith("candidate,loss,0.8\n")
    )
    assert str(workspace) not in json.dumps(summary)

    exports = cast(dict[str, dict[str, object]], summary["exports"])
    assert set(exports) == {"json", "markdown", "pdf"}
    for metadata in exports.values():
        report = workspace / str(metadata["relative_path"])
        payload = report.read_bytes()
        assert len(payload) == metadata["byte_size"]
        assert len(str(metadata["sha256"])) == 64
    assert (
        json.loads((workspace / "reports/product-report.json").read_bytes())["result"]["id"]
        == summary["result_id"]
    )
    assert (workspace / "reports/product-report.pdf").read_bytes().startswith(b"%PDF")

    project_id = str(summary["project_id"])
    store_root = workspace / "store"
    with ProjectStore(store_root) as store:
        assert store.list_records(project_id) == ()
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        scoped = int(
            connection.execute(
                "SELECT COUNT(*) FROM product_records "
                "WHERE project_id = ? AND record_kind != 'deletion'",
                (project_id,),
            ).fetchone()[0]
        )
    assert scoped == 0
    with pytest.raises(ProductError) as old_link:
        ProductService(store_root).view(str(summary["result_id"]))
    assert old_link.value.code == "record_not_found"
    assert _source_bytes(source_root) == source_after


def test_product_demo_rejects_nonempty_workspace_without_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "occupied"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")

    result = _runner.invoke(app, ["product-demo", str(workspace)])

    assert result.exit_code == 1
    assert "workspace must be empty" in result.output
    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert tuple(workspace.iterdir()) == (marker,)

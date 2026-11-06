"""Actual local metric computation through the public product boundary."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.dashboard.bridge import BridgeError, dispatch, load_service
from aletheia_lab.product.lifecycle import ProductLifecycleStore

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise AssertionError("local metric analysis attempted network access")

    monkeypatch.setattr(socket, "socket", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def _synthetic_project(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8"
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Metric Test",
            "-c",
            "user.email=metric-test@example.invalid",
            "commit",
            "-qm",
            "synthetic baseline",
        ],
        check=True,
        capture_output=True,
    )


def _ui_mapping(preview):
    candidates = preview["mapping_candidates"]
    target, metric, config = (
        candidates[key][0] for key in ("targets", "metric_sources", "configs")
    )
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
                "records_path": [],
                "metric_name_field": "name",
                "metric_value_field": "value",
                "run_id_field": "run",
                "step_field": None,
            }
        ],
        "runs": [
            {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]},
            {"run_id": "baseline", "config_item_ids": []},
        ],
        "baseline_run_id": "baseline",
        "metric_definitions": [
            {"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}
        ],
    }


def call(service, method, **args):
    response = dispatch(service, method, args)
    assert response.status == 200, response.body
    return json.loads(response.body)


def test_actual_metric_analysis_binds_operands_and_preserves_mock(tmp_path: Path):
    root = tmp_path / "source"
    _synthetic_project(root)
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = call(service, "preview_import", root=str(root))
    confirmed = call(
        service, "confirm_import", preview_id=preview["preview_id"], mapping=_ui_mapping(preview)
    )
    (root / "metrics.csv").write_text("run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n")
    current = call(service, "refresh", project_id=confirmed["project_id"])
    args = dict(
        project_id=current["project_id"],
        snapshot_id=current["snapshot_id"],
        question="What changed in loss?",
    )
    actual = call(service, "analyze_observations", **args)
    assert actual["runtime"] == {
        "provider": "local_evidence",
        "model": "snapshot-metric-analysis/v1",
        "external_call": False,
    }
    assert (
        actual["claims"][0]["text"]
        == "Run candidate, metric loss, step None: 0.6 -> 0.8; delta 0.2; classification adverse."
    )
    assert actual["result"]["causal_status"] == "unverified"
    by_id = {item["id"]: item for item in actual["evidence"]}
    for claim in actual["claims"]:
        assert claim["text"] in by_id[claim["citation_ids"][0]]["text"]
        assert claim["max_strength"] == "observation"
    assert call(service, "view", result_id=actual["result"]["id"]) == actual
    child = call(
        service,
        "follow_up",
        result_id=actual["result"]["id"],
        selection_kind="claim",
        selection_id=actual["claims"][0]["id"],
        question="What remains uncertain?",
    )
    assert child["runtime"] == actual["runtime"] and child["evidence"] == actual["evidence"]
    assert len(child["conversation"]["turns"]) == 2
    assert child["conversation"]["turns"][-1]["abstained"] is True
    assert call(service, "view", result_id=child["result"]["id"]) == child
    for report_format, magic in (("json", b"{"), ("markdown", b"#"), ("pdf", b"%PDF")):
        report = dispatch(
            service, "export_report", {"result_id": child["result"]["id"], "format": report_format}
        )
        assert report.status == 200 and report.body.startswith(magic)
        if report_format == "json":
            assert json.loads(report.body)["result"]["id"] == child["result"]["id"]
        if report_format == "markdown":
            assert b"delta 0.2; classification adverse." in report.body
    mock = call(service, "analyze_mock", **args)
    assert mock["runtime"]["provider"] == "deterministic_mock"
    assert mock["result"]["id"] != actual["result"]["id"]
    deleted = call(service, "delete_project", project_id=current["project_id"])
    assert deleted["status"] == "deleted"
    assert dispatch(service, "view", {"result_id": actual["result"]["id"]}).status == 422


def test_initial_snapshot_is_explicitly_local_without_fabricated_change(tmp_path: Path):
    root = tmp_path / "source"
    _synthetic_project(root)
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = call(service, "preview_import", root=str(root))
    current = call(
        service, "confirm_import", preview_id=preview["preview_id"], mapping=_ui_mapping(preview)
    )
    actual = call(
        service,
        "analyze_observations",
        project_id=current["project_id"],
        snapshot_id=current["snapshot_id"],
        question="What is observed?",
    )
    assert actual["runtime"]["provider"] == "local_evidence"
    assert actual["snapshot"]["metric_changes"] == []
    assert "no comparable metric change" in actual["claims"][0]["text"]


@pytest.mark.parametrize("value", [None, "0.55", "0.8"])
def test_graph_keeps_previous_snapshot_outside_current_observations(
    tmp_path: Path, value: str | None
) -> None:
    root = tmp_path / "source"
    _synthetic_project(root)
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = call(service, "preview_import", root=str(root))
    current = call(
        service, "confirm_import", preview_id=preview["preview_id"], mapping=_ui_mapping(preview)
    )
    if value is not None:
        (root / "metrics.csv").write_text(
            f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{value}\n", encoding="utf-8"
        )
        current = call(service, "refresh", project_id=current["project_id"])
    actual = call(
        service,
        "analyze_observations",
        project_id=current["project_id"],
        snapshot_id=current["snapshot_id"],
        question="What changed?",
    )
    nodes = {node["id"]: node for node in actual["graph"]["nodes"]}
    observed = {
        nodes[edge["source"]]["source_id"]
        for edge in actual["graph"]["edges"]
        if edge["kind"] == "OBSERVED_IN"
    }
    current_evidence = {
        item["id"]
        for item in actual["evidence"]
        if item["kind"] == "snapshot"
        and item["reproduction_ref"]["record_id"] == current["snapshot_id"]
    }
    previous_evidence = {
        item["id"]
        for item in actual["evidence"]
        if item["kind"] == "snapshot"
        and item["reproduction_ref"]["record_id"] != current["snapshot_id"]
    }
    assert current_evidence and current_evidence <= observed
    assert not previous_evidence & observed
    if value is not None:
        assert previous_evidence
    for edge in actual["graph"]["edges"]:
        if edge["kind"] == "OBSERVED_IN":
            assert nodes[edge["target"]]["source_id"] == current["snapshot_id"]


def test_added_removed_and_step_scoped_changes_keep_exact_operands(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _synthetic_project(root)
    (root / "metrics.csv").write_text(
        "run,name,value,step\nbaseline,loss,0.5,0\ncandidate,loss,0.6,0\ncandidate,loss,0.7,1\n",
        encoding="utf-8",
    )
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = call(service, "preview_import", root=str(root))
    mapping = _ui_mapping(preview)
    mapping["metric_sources"][0]["step_field"] = "step"
    current = call(service, "confirm_import", preview_id=preview["preview_id"], mapping=mapping)
    (root / "metrics.csv").write_text(
        "run,name,value,step\nbaseline,loss,0.5,0\ncandidate,loss,0.8,0\ncandidate,loss,0.9,2\n",
        encoding="utf-8",
    )
    current = call(service, "refresh", project_id=current["project_id"])
    actual = call(
        service,
        "analyze_observations",
        project_id=current["project_id"],
        snapshot_id=current["snapshot_id"],
        question="What changed?",
    )
    assert {claim["text"] for claim in actual["claims"]} == {
        "Run candidate, metric loss, step 0: 0.6 -> 0.8; delta 0.2; classification adverse.",
        "Run candidate, metric loss, step 1: removed previous value 0.7; no comparable current value.",
        "Run candidate, metric loss, step 2: added value 0.9; no comparable previous value.",
    }
    assert len(actual["snapshot"]["metric_changes"]) == 3


def test_oversized_analysis_refuses_without_persisting_partial_result(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _synthetic_project(root)
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\n" + "".join(f"candidate,m{i},0.6\n" for i in range(21)),
        encoding="utf-8",
    )
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = call(service, "preview_import", root=str(root))
    mapping = _ui_mapping(preview)
    mapping["metric_definitions"] += [
        {"metric_name": f"m{i}", "direction": "lower_is_better", "regression_threshold": 0.08}
        for i in range(21)
    ]
    current = call(service, "confirm_import", preview_id=preview["preview_id"], mapping=mapping)
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\n" + "".join(f"candidate,m{i},0.8\n" for i in range(21)),
        encoding="utf-8",
    )
    current = call(service, "refresh", project_id=current["project_id"])
    with ProductLifecycleStore(tmp_path / "store") as store:
        before = store.connection.execute(
            "SELECT COUNT(*) FROM product_records WHERE record_kind='result'"
        ).fetchone()[0]
    response = dispatch(
        service,
        "analyze_observations",
        {
            "project_id": current["project_id"],
            "snapshot_id": current["snapshot_id"],
            "question": "What changed?",
        },
    )
    assert response.status == 422 and json.loads(response.body)["code"] == "analysis_too_large"
    with ProductLifecycleStore(tmp_path / "store") as store:
        after = store.connection.execute(
            "SELECT COUNT(*) FROM product_records WHERE record_kind='result'"
        ).fetchone()[0]
    assert before == after == 0

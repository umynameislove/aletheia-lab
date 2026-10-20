"""The dashboard's Live path end to end: every call goes through the bridge into the real
ProductService, with the payload shapes the web UI sends."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.dashboard.bridge import BridgeError, dispatch, load_service

pytestmark = pytest.mark.integration


def _reject_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("the dashboard bridge attempted a network operation")


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=root, check=True, capture_output=True)


def _synthetic_project(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Dashboard Test")
    _git(root, "config", "user.email", "dashboard-test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8"
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")


def _call(service: Any, method: str, **arguments: object) -> Any:
    response = dispatch(service, method, arguments)
    assert response.status == 200, response.body
    if method == "export_report":
        return response
    return json.loads(response.body)


def _ui_mapping(preview: dict[str, Any]) -> dict[str, object]:
    """The shape Connect's confirmPayload builds, including the CSV records_path []."""

    candidates = preview["mapping_candidates"]
    target = candidates["targets"][0]
    metric = candidates["metric_sources"][0]
    config = candidates["configs"][0]
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


def test_live_flow_through_the_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "project"
    _synthetic_project(source)
    monkeypatch.setattr(socket, "socket", _reject_network)
    monkeypatch.setattr(socket, "create_connection", _reject_network)
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)

    demo = _call(service, "demo_view")
    assert demo["schema_version"] == "p6-product-view/v1"

    preview = _call(service, "preview_import", root=str(source))
    confirmed = _call(
        service, "confirm_import", preview_id=preview["preview_id"], mapping=_ui_mapping(preview)
    )
    project_id = confirmed["project_id"]

    (source / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n", encoding="utf-8"
    )
    _git(source, "commit", "-q", "-am", "adverse")
    refreshed = _call(service, "refresh", project_id=project_id)
    assert refreshed["status"] == "new_snapshot"
    assert refreshed["snapshot_id"] != confirmed["snapshot_id"]

    view = _call(
        service,
        "analyze_mock",
        project_id=project_id,
        snapshot_id=refreshed["snapshot_id"],
        question="What explains the loss change?",
    )
    assert view["snapshot"]["id"] == refreshed["snapshot_id"]
    assert view["claims"] and view["graph"]["nodes"]

    child = _call(
        service,
        "follow_up",
        result_id=view["result"]["id"],
        selection_kind="claim",
        selection_id=view["claims"][0]["id"],
        question="What remains uncertain?",
    )
    assert len(child["conversation"]["turns"]) == len(view["conversation"]["turns"]) + 1
    assert _call(service, "view", result_id=child["result"]["id"]) == child

    for report_format, magic in (("json", b"{"), ("markdown", b"#"), ("pdf", b"%PDF")):
        report = _call(
            service, "export_report", result_id=child["result"]["id"], format=report_format
        )
        assert report.body.startswith(magic)
    assert (
        json.loads(
            _call(service, "export_report", result_id=child["result"]["id"], format="json").body
        )["result"]["id"]
        == child["result"]["id"]
    )

    receipt = _call(service, "delete_project", project_id=project_id)
    assert receipt["status"] == "deleted" and receipt["deleted_count"] > 0
    gone = dispatch(service, "view", {"result_id": child["result"]["id"]})
    assert gone.status == 422
    assert json.loads(gone.body)["code"] == "record_not_found"
    assert str(tmp_path) not in gone.body.decode("utf-8")


def _confirmed(tmp_path: Path) -> tuple[Any, Path, dict[str, Any]]:
    source = tmp_path / "project"
    _synthetic_project(source)
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)
    preview = _call(service, "preview_import", root=str(source))
    confirmed = _call(
        service, "confirm_import", preview_id=preview["preview_id"], mapping=_ui_mapping(preview)
    )
    return service, source, confirmed


def _assert_observation_only(view: dict[str, Any]) -> None:
    assert view["result"]["status"] == "complete"
    assert view["result"]["disposition"] == "abstain"
    assert view["result"]["causal_status"] == "unverified"
    assert view["evidence"]
    assert view["result"]["denominators"]["independent_families"] is None


def test_the_initial_snapshot_can_be_asked_about_right_after_confirm(tmp_path: Path) -> None:
    service, _, confirmed = _confirmed(tmp_path)

    view = _call(
        service,
        "analyze_mock",
        project_id=confirmed["project_id"],
        snapshot_id=confirmed["snapshot_id"],
        question="What does this snapshot record?",
    )

    _assert_observation_only(view)
    assert view["snapshot"]["id"] == confirmed["snapshot_id"]
    assert view["snapshot"]["metric_changes"] == []


def test_a_non_adverse_refresh_can_be_asked_about(tmp_path: Path) -> None:
    service, source, confirmed = _confirmed(tmp_path)
    (source / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.55\n", encoding="utf-8"
    )
    _git(source, "commit", "-q", "-am", "improvement")
    refreshed = _call(service, "refresh", project_id=confirmed["project_id"])
    assert refreshed["status"] == "new_snapshot"

    view = _call(
        service,
        "analyze_mock",
        project_id=confirmed["project_id"],
        snapshot_id=refreshed["snapshot_id"],
        question="What changed?",
    )

    _assert_observation_only(view)
    changes = view["snapshot"]["metric_changes"]
    assert changes
    assert all(change["adverse_status"] != "adverse" for change in changes)


def test_service_errors_reach_the_dashboard_as_safe_codes(tmp_path: Path) -> None:
    service = load_service(tmp_path / "store")
    assert not isinstance(service, BridgeError)

    malformed = dispatch(service, "view", {"result_id": "not a result id"})
    bad_format = dispatch(service, "export_report", {"result_id": "x", "format": "PDF"})

    assert json.loads(malformed.body)["code"] == "invalid_id"
    assert malformed.status == 422
    assert bad_format.status == 422
    for response in (malformed, bad_format):
        assert str(tmp_path) not in response.body.decode("utf-8")

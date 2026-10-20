"""End-to-end observations without fabricated regression records."""

from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
from pathlib import Path
from typing import cast

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.observations import analyze_product_observation
from aletheia_lab.product.view import ProductView
from aletheia_lab.project import ProjectSnapshot, ProjectSnapshotComparison, ProjectStore

pytestmark = pytest.mark.integration


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Observation Test")
    _git(root, "config", "user.email", "observation@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8"
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    _git(root, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(root, "commit", "-q", "-m", "synthetic observation fixture")
    return root.resolve()


def _mapping(preview: dict[str, object]) -> dict[str, object]:
    candidates = cast(dict[str, list[dict[str, object]]], preview["mapping_candidates"])
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
                "format": "csv",
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


def _confirmed(tmp_path: Path) -> tuple[ProductService, Path, Path, dict[str, object]]:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source))
    return (
        service,
        source,
        store_root,
        service.confirm_import(str(preview["preview_id"]), _mapping(preview)),
    )


def _deny_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("observation attempted network access")


@pytest.mark.parametrize("change", ["initial", "improvement", "config_only", "below_threshold"])
def test_product_observation_full_offline_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    monkeypatch.setattr(socket, "socket", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    service, source, store_root, confirmed = _confirmed(tmp_path)
    project_id, initial_id = str(confirmed["project_id"]), str(confirmed["snapshot_id"])
    if change == "config_only":
        (source / "config.json").write_text('{"seed":43}\n', encoding="utf-8")
    elif change != "initial":
        value = "0.55" if change == "improvement" else "0.65"
        (source / "metrics.csv").write_text(
            f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{value}\n", encoding="utf-8"
        )
    if change != "initial":
        _git(source, "add", "config.json", "metrics.csv")
        _git(source, "commit", "-q", "-m", "synthetic non-adverse change")
    refreshed = service.refresh(project_id)
    snapshot_id = str(refreshed["snapshot_id"])
    source_bytes = {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }
    with ProjectStore(store_root) as store:
        original_index = store.export_index(project_id)
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM product_records").fetchone()[0]
    with pytest.raises(ProductError) as rejected:
        service.analyze_mock(project_id, snapshot_id, "Counterfactual comparison: not_available")
    assert rejected.value.code == "invalid_question"
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM product_records").fetchone()[0] == count
    view = ProductView.model_validate_json(
        json.dumps(service.analyze_mock(project_id, snapshot_id, "What is observed?"))
    )
    assert view.result.disposition == "abstain" and view.result.causal_status == "unverified"
    assert view.result.denominators.independent_families is None
    assert view.runtime.external_call is False and view.demo_only is False
    assert view.snapshot.baseline_id == initial_id
    assert bool(view.snapshot.metric_changes) == (change in {"improvement", "below_threshold"})
    assert all(metric.adverse_status == "not_adverse" for metric in view.snapshot.metric_changes)
    if view.snapshot.metric_changes:
        assert view.snapshot.metric_changes[0].delta == (-0.05 if change == "improvement" else 0.05)
    assert "unverified" in view.conversation.turns[-1].answer
    assert all(
        edge.kind in {"OBSERVED_IN", "CITES", "ASSIGNED_DISPOSITION"} for edge in view.graph.edges
    )
    assert len([edge for edge in view.graph.edges if edge.kind == "OBSERVED_IN"]) == 1
    with ProjectStore(store_root) as store:
        assert store.export_index(project_id) == original_index
        for kind in ("regression_event", "evidence_bundle", "lineage_graph"):
            assert store.list_records(project_id, kind) == ()  # type: ignore[arg-type]
        for evidence in view.evidence:
            record = store.load(evidence.reproduction_ref.record_id)
            if evidence.kind == "snapshot":
                assert isinstance(record, ProjectSnapshot)
                assert evidence.source_sha256 == record.state_sha256
            elif evidence.kind == "snapshot_comparison":
                assert isinstance(record, ProjectSnapshotComparison)
                assert evidence.source_sha256 == record.comparison_sha256
            else:
                assert isinstance(record, ProjectSnapshotComparison)
                assert evidence.source_sha256 in {
                    metric.metric_change_id.removeprefix("p3-metric-change-")
                    for metric in record.metric_changes
                }
    assert ProductService(store_root).view(view.result.id) == view.model_dump(mode="json")
    assert (
        analyze_product_observation(store_root, project_id, snapshot_id, "What is observed?")
        == view
    )
    child = service.follow_up(view.result.id, "claim", view.claims[0].id, "What remains unknown?")
    child_id = str(cast(dict[str, object], child["result"])["id"])
    assert child_id != view.result.id
    assert service.view(view.result.id) == view.model_dump(mode="json")
    assert json.loads(service.export_report(child_id, "json")) == child
    assert child_id.encode() in service.export_report(child_id, "markdown")
    assert service.export_report(child_id, "pdf").startswith(b"%PDF-")
    assert str(source) not in json.dumps(child)
    assert service.delete_project(project_id)["status"] == "deleted"
    assert {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    } == source_bytes
    with pytest.raises(ProductError):
        service.view(child_id)


def test_product_observation_does_not_rescue_corrupt_or_foreign_sources(tmp_path: Path) -> None:
    service, source, store_root, confirmed = _confirmed(tmp_path)
    project_id, snapshot_id = str(confirmed["project_id"]), str(confirmed["snapshot_id"])
    with pytest.raises(ProductError):
        analyze_product_observation(store_root, "p3-project-" + "f" * 64, snapshot_id, "Question?")
    with pytest.raises(ProductError, match="analysis question"):
        analyze_product_observation(store_root, project_id, snapshot_id, "")
    (source / "config.json").write_text('{"seed":43}\n', encoding="utf-8")
    _git(source, "add", "config.json")
    _git(source, "commit", "-q", "-m", "non-adverse comparison")
    refreshed = service.refresh(project_id)
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        row = connection.execute(
            "SELECT object_sha256 FROM records WHERE record_type='snapshot_comparison'"
        ).fetchone()
        assert row is not None
        digest = str(row[0])
    target = store_root / "objects" / "sha256" / digest[:2] / digest[2:]
    target.write_bytes(b"corrupted synthetic comparison")
    with pytest.raises(ProductError) as caught:
        analyze_product_observation(
            store_root, project_id, str(refreshed["snapshot_id"]), "Question?"
        )
    assert caught.value.code == "store_integrity_error"
    assert str(source) not in caught.value.safe_message


@pytest.mark.parametrize(
    ("before", "after", "result_invalid"),
    [
        (0.9876543210987654, 0.12345678901234568, False),
        (8.881489377429504e292, -1.797693134862315e308, True),
        (-8.881489377429504e292, 1.797693134862315e308, True),
    ],
)
def test_public_analysis_preserves_finite_precision_or_fails_safely(
    tmp_path: Path, before: float, after: float, result_invalid: bool
) -> None:
    source = _project(tmp_path / "source")
    metric_path = source / "metrics.csv"
    metric_path.write_text(
        f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{before}\n", encoding="utf-8"
    )
    _git(source, "add", "metrics.csv")
    _git(source, "commit", "-q", "-m", "synthetic numeric baseline")
    service = ProductService(tmp_path / "store")
    preview = service.preview_import(str(source))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    metric_path.write_text(
        f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{after}\n", encoding="utf-8"
    )
    _git(source, "add", "metrics.csv")
    _git(source, "commit", "-q", "-m", "synthetic finite numeric change")
    refreshed = service.refresh(str(confirmed["project_id"]))
    with ProjectStore(tmp_path / "store") as store:
        store.verify_integrity()
    arguments = (
        str(confirmed["project_id"]),
        str(refreshed["snapshot_id"]),
        "What is observed?",
    )
    if result_invalid:
        with sqlite3.connect(tmp_path / "store" / "project-store.sqlite3") as connection:
            count = connection.execute("SELECT COUNT(*) FROM product_records").fetchone()[0]
        with pytest.raises(ProductError) as caught:
            service.analyze_mock(*arguments)
        assert caught.value.code == "result_invalid"
        assert caught.value.__suppress_context__
        with sqlite3.connect(tmp_path / "store" / "project-store.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM product_records").fetchone()[0] == count
    else:
        view = ProductView.model_validate_json(json.dumps(service.analyze_mock(*arguments)))
        assert view.result.disposition == "abstain"
        assert view.snapshot.metric_changes[0].delta == -0.8641975320864197

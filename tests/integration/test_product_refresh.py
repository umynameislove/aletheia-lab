from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import cast

import pytest

import aletheia_lab.product.refresh as refresh_module
import aletheia_lab.project.importer as importer_module
from aletheia_lab.product import ProductService
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.lifecycle import (
    ProductLifecycleStore,
    build_product_lifecycle_record,
)
from aletheia_lab.project import (
    GrantedProjectRoot,
    ProjectEvidenceBundle,
    ProjectLineageGraph,
    ProjectRegressionEvent,
    ProjectSnapshot,
    ProjectSnapshotComparison,
    ProjectStore,
)
from aletheia_lab.project.import_policy import ProjectImportPolicy
from aletheia_lab.project.importer import ProjectImportInspection

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
    _git(root, "config", "user.name", "Refresh Test")
    _git(root, "config", "user.email", "refresh@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    _git(root, "add", "dataset.csv", "metrics.csv", "config.json")
    _git(root, "commit", "-q", "-m", "fixture")
    return root.resolve()


def _mapping(
    preview: dict[str, object],
    *,
    direction: str = "lower_is_better",
    threshold: float = 0.08,
) -> dict[str, object]:
    candidates = cast(
        dict[str, list[dict[str, object]]],
        preview["mapping_candidates"],
    )
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
                "metric_name_field": "name",
                "metric_value_field": "value",
                "run_id_field": "run",
            }
        ],
        "runs": [
            {"run_id": "baseline", "config_item_ids": []},
            {
                "run_id": "candidate",
                "config_item_ids": [config["project_item_id"]],
            },
        ],
        "baseline_run_id": "baseline",
        "metric_definitions": [
            {
                "metric_name": "loss",
                "direction": direction,
                "regression_threshold": threshold,
            }
        ],
    }


def _source_state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.iterdir()
        if path.is_file()
    }


def _store_census(store_root: Path, project_id: str) -> tuple[tuple[str, ...], int]:
    with ProjectStore(store_root) as store:
        record_ids = tuple(value.record_id for value in store.list_records(project_id))
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        state_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM product_records WHERE record_kind = 'project_state'"
            ).fetchone()[0]
        )
    return record_ids, state_count


def _export_index(store_root: Path, project_id: str) -> bytes:
    with ProjectStore(store_root) as store:
        return store.export_index(project_id)


def test_unchanged_refresh_reuses_snapshot_without_persisting_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before_source = _source_state(source_root)
    before_store = _store_census(store_root, project_id)
    before_index = _export_index(store_root, project_id)

    def reject_persist(*args: object, **kwargs: object) -> None:
        raise AssertionError("unchanged refresh attempted to persist a new generation")

    monkeypatch.setattr(ProjectStore, "persist", reject_persist)
    refreshed = ProductService(store_root).refresh(project_id)

    assert refreshed == {
        "project_id": project_id,
        "snapshot_id": confirmed["snapshot_id"],
        "status": "unchanged",
    }
    assert _source_state(source_root) == before_source
    assert _store_census(store_root, project_id) == before_store
    assert _export_index(store_root, project_id) == before_index


def test_changed_metric_refresh_persists_one_traceable_immutable_generation(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before_snapshot_id = str(confirmed["snapshot_id"])
    pinned_result = build_product_lifecycle_record(
        record_kind="result",
        project_id=project_id,
        snapshot_id=before_snapshot_id,
        payload={"status": "pinning_probe"},
    )
    with ProductLifecycleStore(store_root) as lifecycle:
        lifecycle.put(pinned_result)

    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "metric changed")
    changed_source = _source_state(source_root)

    refreshed = ProductService(store_root).refresh(project_id)

    assert refreshed["project_id"] == project_id
    assert refreshed["status"] == "new_snapshot"
    assert refreshed["snapshot_id"] != before_snapshot_id
    assert _source_state(source_root) == changed_source

    with ProjectStore(store_root) as reopened:
        old_snapshot = reopened.load(before_snapshot_id)
        assert isinstance(old_snapshot, ProjectSnapshot)
        assert old_snapshot.snapshot_id == before_snapshot_id
        comparisons = reopened.list_records(project_id, "snapshot_comparison")
        events = reopened.list_records(project_id, "regression_event")
        evidence_bundles = reopened.list_records(project_id, "evidence_bundle")
        lineage_graphs = reopened.list_records(project_id, "lineage_graph")
        assert len(comparisons) == len(events) == len(evidence_bundles) == len(lineage_graphs) == 1
        comparison = reopened.load(comparisons[0].record_id)
        event = reopened.load(events[0].record_id)
        evidence = reopened.load(evidence_bundles[0].record_id)
        lineage = reopened.load(lineage_graphs[0].record_id)

    assert isinstance(comparison, ProjectSnapshotComparison)
    assert comparison.before_snapshot_id == before_snapshot_id
    assert comparison.after_snapshot_id == refreshed["snapshot_id"]
    assert comparison.status == "changed"
    assert len(comparison.metric_changes) == 1
    assert isinstance(event, ProjectRegressionEvent)
    assert event.comparison_id == comparison.comparison_id
    assert event.causal_status == "unverified"
    assert isinstance(evidence, ProjectEvidenceBundle)
    assert evidence.event_id == event.event_id
    assert isinstance(lineage, ProjectLineageGraph)
    assert "causes" not in lineage.model_dump_json()

    with ProductLifecycleStore(store_root) as lifecycle:
        current = lifecycle.get_project_state(project_id)
    payload = current.payload()
    assert current.snapshot_id is None
    assert payload["snapshot_id"] == refreshed["snapshot_id"]
    assert payload["previous_snapshot_id"] == before_snapshot_id
    assert payload["comparison_id"] == comparison.comparison_id
    assert payload["event_id"] == event.event_id
    assert payload["evidence_bundle_id"] == evidence.evidence_bundle_id
    assert payload["lineage_graph_id"] == lineage.graph_id
    assert payload["adverse_metric_change_ids"] == [
        comparison.metric_changes[0].metric_change_id
    ]

    before_replay = _store_census(store_root, project_id)
    replay = ProductService(store_root).refresh(project_id)
    assert replay == {
        "project_id": project_id,
        "snapshot_id": refreshed["snapshot_id"],
        "status": "unchanged",
    }
    assert _store_census(store_root, project_id) == before_replay
    with ProductLifecycleStore(store_root) as lifecycle:
        assert (
            lifecycle.get(
                pinned_result.record_id,
                expected_kind="result",
                project_id=project_id,
                snapshot_id=before_snapshot_id,
            )
            == pinned_result
        )
        with pytest.raises(ProductError) as foreign_snapshot:
            lifecycle.get(
                pinned_result.record_id,
                expected_kind="result",
                project_id=project_id,
                snapshot_id=str(refreshed["snapshot_id"]),
            )
    assert foreign_snapshot.value.code == "record_not_found"


def test_adverse_threshold_uses_decimal_boundary_comparison(tmp_path: Path) -> None:
    source_root = _project(tmp_path / "source")
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.82\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "threshold baseline")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    mapping_payload = cast(
        dict[str, object],
        json.loads(
            json.dumps(
                _mapping(
                    preview,
                    direction="higher_is_better",
                    threshold=0.08,
                ),
                allow_nan=False,
            )
        ),
    )
    confirmed = service.confirm_import(
        str(preview["preview_id"]),
        mapping_payload,
    )

    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.74\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "exact threshold")

    refreshed = ProductService(store_root).refresh(str(confirmed["project_id"]))

    assert refreshed["status"] == "new_snapshot"
    with ProductLifecycleStore(store_root) as lifecycle:
        payload = lifecycle.get_project_state(str(confirmed["project_id"])).payload()
    adverse_change_ids = payload["adverse_metric_change_ids"]
    assert isinstance(adverse_change_ids, list)
    assert len(adverse_change_ids) == 1
    assert isinstance(payload["event_id"], str)


def test_non_adverse_refresh_persists_comparison_without_regression_claims(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])

    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.55\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "metric improved")

    refreshed = ProductService(store_root).refresh(project_id)

    assert refreshed["status"] == "new_snapshot"
    with ProjectStore(store_root) as reopened:
        assert len(reopened.list_records(project_id, "snapshot_comparison")) == 1
        assert reopened.list_records(project_id, "regression_event") == ()
        assert reopened.list_records(project_id, "evidence_bundle") == ()
        assert reopened.list_records(project_id, "lineage_graph") == ()
    with ProductLifecycleStore(store_root) as lifecycle:
        payload = lifecycle.get_project_state(project_id).payload()
    assert payload["adverse_metric_change_ids"] == []
    assert payload["event_id"] is None
    assert payload["evidence_bundle_id"] is None
    assert payload["lineage_graph_id"] is None
    assert ProductService(store_root).refresh(project_id)["status"] == "unchanged"


def test_refresh_rejects_missing_required_mapping_source_without_new_state(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before = _store_census(store_root, project_id)
    (source_root / "metrics.csv").unlink()
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "remove required metrics")

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).refresh(project_id)

    assert captured.value.code == "mapping_invalid"
    assert captured.value.__context__ is None
    assert _store_census(store_root, project_id) == before


def test_refresh_missing_source_root_returns_safe_error_without_raw_context(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before = _store_census(store_root, project_id)
    missing_root = source_root.with_name("source-moved")
    source_root.rename(missing_root)

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).refresh(project_id)

    assert captured.value.code == "refresh_source_unavailable"
    assert str(source_root) not in captured.value.safe_message
    assert str(missing_root) not in captured.value.safe_message
    assert captured.value.__context__ is None
    assert _store_census(store_root, project_id) == before


def test_refresh_rolls_back_visible_generation_when_state_write_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before = _store_census(store_root, project_id)
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "changed before crash")

    def fail_state_write(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated transaction failure")

    monkeypatch.setattr(refresh_module, "put_current_project_state", fail_state_write)
    with pytest.raises(ProductError) as captured:
        ProductService(store_root).refresh(project_id)

    assert captured.value.code == "refresh_failed"
    assert captured.value.__context__ is None
    assert _store_census(store_root, project_id) == before
    with ProductLifecycleStore(store_root) as lifecycle:
        current = lifecycle.get_project_state(project_id)
    assert current.payload()["snapshot_id"] == confirmed["snapshot_id"]


def test_refresh_rejects_source_race_without_persisting_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    confirmed = service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    project_id = str(confirmed["project_id"])
    before = _store_census(store_root, project_id)
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "metrics.csv")
    _git(source_root, "commit", "-q", "-m", "first refresh state")
    real_inspect = importer_module.inspect_local_project
    calls = 0

    def race_inspection(
        grant: GrantedProjectRoot,
        *,
        ingested_at: str,
        policy: ProjectImportPolicy | None = None,
    ) -> ProjectImportInspection:
        nonlocal calls
        calls += 1
        if calls == 2:
            (source_root / "metrics.csv").write_text(
                "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.9\n",
                encoding="utf-8",
            )
        return real_inspect(grant, ingested_at=ingested_at, policy=policy)

    monkeypatch.setattr(
        "aletheia_lab.product.refresh.inspect_local_project",
        race_inspection,
    )
    with pytest.raises(ProductError) as captured:
        ProductService(store_root).refresh(project_id)

    assert captured.value.code == "refresh_stale"
    assert captured.value.__context__ is None
    assert _store_census(store_root, project_id) == before

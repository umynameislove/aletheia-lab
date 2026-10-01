from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

import aletheia_lab.project.importer as importer_module
from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import ProductLifecycleStore
from aletheia_lab.project import ProjectStore

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
    _git(root, "config", "user.name", "Product Test")
    _git(root, "config", "user.email", "product@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "subjects.csv").write_text("patient_ssn,target\n1,0\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    _git(root, "add", "dataset.csv", "subjects.csv", "metrics.csv", "config.json")
    _git(root, "commit", "-q", "-m", "fixture")
    return root.resolve()


def _mapping(preview: dict[str, object]) -> dict[str, object]:
    candidates = cast(
        dict[str, list[dict[str, object]]],
        preview["mapping_candidates"],
    )
    target = next(value for value in candidates["targets"] if value["available"] is True)
    metric = next(
        value
        for value in candidates["metric_sources"]
        if value["available"] is True and value["format"] == "csv"
    )
    config = next(value for value in candidates["configs"] if value["available"] is True)
    runs = candidates["runs"]
    assert isinstance(target["mapping_id"], str)
    assert isinstance(metric["mapping_id"], str)
    assert {value["run_id"] for value in runs} == {"baseline", "candidate"}
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
                "direction": "lower_is_better",
                "regression_threshold": 0.08,
            }
        ],
    }


def _source_files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns)
        for name in ("config.json", "dataset.csv", "metrics.csv", "subjects.csv")
    }


def _record_counts(store_root: Path) -> tuple[int, int, int]:
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        return (
            int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM product_records WHERE record_kind = 'project_state'"
                ).fetchone()[0]
            ),
            int(
                connection.execute("SELECT COUNT(*) FROM product_confirmations").fetchone()[0]
            ),
        )


def test_confirm_revalidates_persists_and_replays_after_restart(tmp_path: Path) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    before = _source_files(source_root)
    preview = ProductService(store_root).preview_import(str(source_root))
    mapping = _mapping(preview)

    first = ProductService(store_root).confirm_import(str(preview["preview_id"]), mapping)
    reordered = deepcopy(mapping)
    metric_sources = reordered["metric_sources"]
    runs = reordered["runs"]
    assert isinstance(metric_sources, list)
    assert isinstance(runs, list)
    reordered["metric_sources"] = list(reversed(metric_sources))
    reordered["runs"] = list(reversed(runs))
    second = ProductService(store_root).confirm_import(
        str(preview["preview_id"]),
        reordered,
    )

    assert first == second
    assert set(first) == {"project_id", "snapshot_id", "status"}
    assert first["status"] == "confirmed"
    assert str(first["project_id"]).startswith("p3-project-")
    assert str(first["snapshot_id"]).startswith("p3-snapshot-")
    assert str(source_root) not in repr(first)
    assert _source_files(source_root) == before
    assert _record_counts(store_root) == (2, 1, 1)

    with ProjectStore(store_root) as store:
        records = store.list_records(str(first["project_id"]))
        assert {record.record_type for record in records} == {"project_bundle", "snapshot"}
        assert store.load(str(first["snapshot_id"])).project_id == first["project_id"]
    with ProductLifecycleStore(store_root) as lifecycle:
        state = lifecycle.get_confirmation(str(preview["preview_id"]))
        current = lifecycle.get_project_state(str(first["project_id"]))
    assert state is not None
    assert current == state
    assert state.project_id == first["project_id"]
    assert state.payload()["snapshot_id"] == first["snapshot_id"]


def test_confirm_rejects_changed_source_without_persisting_project(tmp_path: Path) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    mapping = _mapping(preview)
    (source_root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.9\n",
        encoding="utf-8",
    )

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), mapping)

    assert captured.value.code == "preview_stale"
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)


def test_confirm_rejects_symlink_added_after_preview_without_reading_target(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    mapping = _mapping(preview)
    target = tmp_path / "outside-private-metrics.csv"
    target.write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
        encoding="utf-8",
    )
    source_metric = source_root / "metrics.csv"
    source_metric.unlink()
    try:
        source_metric.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink capability unavailable: {exc}")

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), mapping)

    assert captured.value.code == "preview_stale"
    assert str(target) not in captured.value.safe_message
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)


def test_confirm_rejects_same_bytes_hardlink_added_after_preview(
    tmp_path: Path,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    mapping = _mapping(preview)
    source_metric = source_root / "metrics.csv"
    target = tmp_path / "outside-metrics.csv"
    target.write_bytes(source_metric.read_bytes())
    source_metric.unlink()
    try:
        os.link(target, source_metric)
    except OSError as exc:
        pytest.skip(f"hardlink capability unavailable: {exc}")

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), mapping)

    assert captured.value.code == "preview_stale"
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)


def test_confirm_rejects_reparse_point_detected_after_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    mapping = _mapping(preview)
    real_reparse_check = importer_module._is_reparse_point

    def ordinary_file_is_reparse(value: os.stat_result) -> bool:
        return stat.S_ISREG(value.st_mode) or real_reparse_check(value)

    monkeypatch.setattr(importer_module, "_is_reparse_point", ordinary_file_is_reparse)

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), mapping)

    assert captured.value.code == "preview_stale"
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)


def test_expired_preview_cannot_create_a_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    monkeypatch.setattr(
        "aletheia_lab.product.imports._utc_now",
        lambda: "2026-10-01T10:00:00.000000Z",
    )
    preview = service.preview_import(str(source_root))
    mapping = _mapping(preview)
    monkeypatch.setattr(
        "aletheia_lab.product.imports._utc_now",
        lambda: "2026-10-01T10:30:00.000000Z",
    )

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), mapping)

    assert captured.value.code == "preview_expired"
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)


def test_blocker_and_invalid_mapping_cannot_create_a_project(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    blocked_store = tmp_path / "blocked-store"
    blocked_service = ProductService(blocked_store)
    blocked = blocked_service.preview_import(str(empty_root.resolve()))

    with pytest.raises(ProductError) as blocker:
        blocked_service.confirm_import(str(blocked["preview_id"]), {})

    assert blocker.value.code == "preview_blocked"
    assert _record_counts(blocked_store) == (0, 0, 0)

    source_root = _project(tmp_path / "source")
    invalid_store = tmp_path / "invalid-store"
    invalid_service = ProductService(invalid_store)
    preview = invalid_service.preview_import(str(source_root))
    valid_mapping = _mapping(preview)
    invalid_mapping = deepcopy(valid_mapping)
    invalid_mapping["runs"] = []

    with pytest.raises(ProductError) as invalid:
        invalid_service.confirm_import(str(preview["preview_id"]), invalid_mapping)

    assert invalid.value.code == "mapping_invalid"
    assert _record_counts(invalid_store) == (0, 0, 0)

    sensitive_mapping = deepcopy(valid_mapping)
    candidates = cast(
        dict[str, list[dict[str, object]]],
        preview["mapping_candidates"],
    )
    sensitive_target = next(
        value for value in candidates["targets"] if value["available"] is False
    )
    sensitive_mapping["target"] = {
        "mapping_id": "forged-sensitive-target",
        "project_item_id": sensitive_target["project_item_id"],
        "target_field": "target",
        "identifier_field": "patient_ssn",
    }

    with pytest.raises(ProductError) as sensitive:
        invalid_service.confirm_import(str(preview["preview_id"]), sensitive_mapping)

    assert sensitive.value.code == "mapping_invalid"
    assert sensitive.value.__context__ is None
    assert _record_counts(invalid_store) == (0, 0, 0)

    csv_path_mapping = deepcopy(valid_mapping)
    metric_sources = cast(list[dict[str, object]], csv_path_mapping["metric_sources"])
    metric_sources[0]["records_path"] = ["records"]

    with pytest.raises(ProductError) as csv_path:
        invalid_service.confirm_import(str(preview["preview_id"]), csv_path_mapping)

    assert csv_path.value.code == "mapping_invalid"
    assert _record_counts(invalid_store) == (0, 0, 0)

    unmatched_definition = deepcopy(valid_mapping)
    definitions = cast(
        list[dict[str, object]],
        unmatched_definition["metric_definitions"],
    )
    definitions[0]["metric_name"] = "accuracy"

    with pytest.raises(ProductError) as unmatched:
        invalid_service.confirm_import(
            str(preview["preview_id"]),
            unmatched_definition,
        )

    assert unmatched.value.code == "mapping_invalid"
    assert _record_counts(invalid_store) == (0, 0, 0)


def test_confirmation_failure_rolls_back_all_visible_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source_root))
    private_detail = str(source_root / "private-confirmation.txt")

    def fail_confirmation(*args: object, **kwargs: object) -> None:
        raise OSError(private_detail)

    monkeypatch.setattr(
        "aletheia_lab.product.imports.put_product_confirmation",
        fail_confirmation,
    )

    with pytest.raises(ProductError) as captured:
        service.confirm_import(str(preview["preview_id"]), _mapping(preview))

    assert captured.value.code == "confirm_failed"
    assert private_detail not in captured.value.safe_message
    assert captured.value.__context__ is None
    assert _record_counts(store_root) == (0, 0, 0)

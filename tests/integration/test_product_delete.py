from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import ProductView
from aletheia_lab.project import ProjectBundle, ProjectStore

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
    _git(root, "config", "user.name", "Delete Test")
    _git(root, "config", "user.email", "delete@example.invalid")
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


def _mapping(preview: dict[str, object]) -> dict[str, object]:
    candidates = cast(dict[str, list[dict[str, object]]], preview["mapping_candidates"])
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


def _confirm(service: ProductService, source: Path) -> dict[str, object]:
    preview = service.preview_import(str(source))
    return service.confirm_import(str(preview["preview_id"]), _mapping(preview))


def _analyze(service: ProductService, source: Path) -> tuple[dict[str, object], dict[str, object]]:
    confirmed = _confirm(service, source)
    (source / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n",
        encoding="utf-8",
    )
    _git(source, "add", "metrics.csv")
    _git(source, "commit", "-q", "-m", "adverse metric")
    refreshed = service.refresh(str(confirmed["project_id"]))
    view = service.analyze_mock(
        str(refreshed["project_id"]),
        str(refreshed["snapshot_id"]),
        "Summarize the stored regression evidence.",
    )
    return refreshed, view


def _tree_state(root: Path) -> dict[str, tuple[bool, int, int, bytes | None]]:
    state: dict[str, tuple[bool, int, int, bytes | None]] = {}
    for path in (root, *sorted(root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        stat = path.lstat()
        state[relative] = (
            path.is_dir(),
            stat.st_size,
            stat.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return state


def _expected_counts(store_root: Path, project_id: str) -> tuple[int, int, tuple[str, ...]]:
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT record_id, project_id, record_type, object_sha256 FROM records "
            "ORDER BY record_id"
        ).fetchall()
        product_records = int(
            connection.execute(
                "SELECT COUNT(*) FROM product_records WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )
        previews = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM product_confirmations AS confirmations
                JOIN product_records AS records
                  ON records.record_id = confirmations.project_state_record_id
                WHERE records.project_id = ?
                """,
                (project_id,),
            ).fetchone()[0]
        )
    references: dict[str, set[str]] = {}
    for row in rows:
        references.setdefault(str(row["project_id"]), set()).add(str(row["object_sha256"]))
    with ProjectStore(store_root) as store:
        for row in rows:
            if str(row["record_type"]) != "project_bundle":
                continue
            bundle = store.load(str(row["record_id"]))
            assert isinstance(bundle, ProjectBundle)
            references.setdefault(bundle.project_id, set()).update(
                item.artifact.sha256 for item in bundle.items
            )
    target = references[project_id]
    surviving = set().union(
        *(digests for owner, digests in references.items() if owner != project_id)
    )
    purge_digests = tuple(sorted(target - surviving))
    retained_digests = tuple(sorted(target & surviving))
    project_records = sum(str(row["project_id"]) == project_id for row in rows)
    deleted = product_records + previews + project_records + len(purge_digests)
    return deleted, len(retained_digests), purge_digests


def _database_bytes(store_root: Path) -> bytes:
    database = store_root / "project-store.sqlite3"
    return b"".join(
        path.read_bytes()
        for path in (database, database.with_name(database.name + "-wal"))
        if path.exists()
    )


def test_delete_purges_full_project_and_denies_old_deep_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    refreshed, view = _analyze(service, source)
    result_id = str(view["result"]["id"])
    deletion_sentinel = "K11 deletion sentinel 7e7f8a19"
    follow_up = service.follow_up(
        result_id,
        "claim",
        str(cast(list[dict[str, object]], view["claims"])[0]["id"]),
        deletion_sentinel,
    )
    follow_up_id = str(cast(dict[str, object], follow_up["result"])["id"])
    assert service.export_report(follow_up_id, "json")
    project_id = str(refreshed["project_id"])
    expected_deleted, expected_retained, purge_digests = _expected_counts(store_root, project_id)
    source_before = _tree_state(source)
    escaped_source = json.dumps(str(source), ensure_ascii=False)[1:-1].encode("utf-8")
    assert escaped_source in _database_bytes(store_root)
    assert deletion_sentinel.encode() in _database_bytes(store_root)

    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("delete attempted a network operation")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    receipt = service.delete_project(project_id)

    assert receipt == {
        "project_id": project_id,
        "status": "deleted",
        "deleted_count": expected_deleted,
        "retained_shared_count": expected_retained,
    }
    assert _tree_state(source) == source_before
    assert source.is_dir()
    with ProjectStore(store_root) as store:
        assert store.list_records(project_id) == ()
        assert all(not store._object_path(digest).exists() for digest in purge_digests)
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        assert all(
            connection.execute("SELECT 1 FROM objects WHERE sha256 = ?", (digest,)).fetchone()
            is None
            for digest in purge_digests
        )
    database_bytes = _database_bytes(store_root)
    assert escaped_source not in database_bytes
    assert deletion_sentinel.encode() not in database_bytes
    for operation in (
        lambda: ProductService(store_root).refresh(project_id),
        lambda: ProductService(store_root).view(result_id),
        lambda: ProductService(store_root).follow_up(
            result_id, "claim", "p6-claim-" + "0" * 64, "Question?"
        ),
        lambda: ProductService(store_root).export_report(follow_up_id, "json"),
        lambda: ProductService(store_root).analyze_mock(
            project_id, str(refreshed["snapshot_id"]), "Question?"
        ),
    ):
        with pytest.raises(ProductError):
            operation()
    assert ProductService(store_root).delete_project(project_id) == {
        "project_id": project_id,
        "status": "already_deleted",
        "deleted_count": 0,
        "retained_shared_count": expected_retained,
    }
    with pytest.raises(ProductError) as malformed:
        service.delete_project("private-project-id")
    with pytest.raises(ProductError) as foreign:
        service.delete_project("p3-project-" + "f" * 64)
    assert malformed.value.code == "invalid_id"
    assert foreign.value.code == "record_not_found"


def test_delete_retains_shared_objects_and_other_project_remains_usable(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    first_state, first_view = _analyze(service, _project(tmp_path / "first"))
    second_state, second_view = _analyze(service, _project(tmp_path / "second"))
    first_id = str(first_state["project_id"])
    second_id = str(second_state["project_id"])
    second_result_id = str(cast(dict[str, object], second_view["result"])["id"])
    expected_deleted, expected_retained, _ = _expected_counts(store_root, first_id)
    assert expected_retained > 0
    before_export = service.export_report(second_result_id, "json")

    receipt = service.delete_project(first_id)

    assert receipt["deleted_count"] == expected_deleted
    assert receipt["retained_shared_count"] == expected_retained
    assert ProductService(store_root).view(second_result_id) == second_view
    assert ProductService(store_root).export_report(second_result_id, "json") == before_export
    assert ProductService(store_root).refresh(second_id)["status"] == "unchanged"
    with ProjectStore(store_root) as store:
        assert store.list_records(first_id) == ()
        assert store.list_records(second_id)
        store.verify_integrity()
    assert first_view != second_view


def test_delete_reconciles_partial_file_failure_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    confirmed = _confirm(service, source)
    project_id = str(confirmed["project_id"])
    expected_deleted, expected_retained, _ = _expected_counts(store_root, project_id)
    original = ProjectStore.remove_purged_object_files
    interrupted = False

    def fail_after_one(self: ProjectStore, digests: Iterable[str]) -> int:
        nonlocal interrupted
        values = tuple(digests)
        if not interrupted:
            interrupted = True
            original(self, values[:1])
            raise OSError("synthetic interruption")
        return original(self, values)

    monkeypatch.setattr(ProjectStore, "remove_purged_object_files", fail_after_one)
    with pytest.raises(ProductError) as failure:
        service.delete_project(project_id)
    assert failure.value.code == "store_integrity_error"
    with ProjectStore(store_root) as store:
        assert store.list_records(project_id) == ()

    monkeypatch.setattr(ProjectStore, "remove_purged_object_files", original)
    receipt = ProductService(store_root).delete_project(project_id)
    assert receipt == {
        "project_id": project_id,
        "status": "deleted",
        "deleted_count": expected_deleted,
        "retained_shared_count": expected_retained,
    }


def test_delete_fails_closed_when_artifact_reference_index_is_tampered(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    confirmed = _confirm(service, source)
    project_id = str(confirmed["project_id"])
    with ProjectStore(store_root) as store:
        bundle_record = store.list_records(project_id, "project_bundle")[0]
        bundle = store.load(bundle_record.record_id)
        assert isinstance(bundle, ProjectBundle)
        artifact_digest = bundle.items[0].artifact.sha256
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM objects WHERE sha256 = ?", (artifact_digest,))

    with pytest.raises(ProductError) as captured:
        service.delete_project(project_id)

    assert captured.value.code == "store_integrity_error"
    assert str(source) not in captured.value.safe_message


def test_delete_tombstone_blocks_reimport_and_in_flight_result(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    refreshed, raw_view = _analyze(service, source)
    project_id = str(refreshed["project_id"])
    stale_view = ProductView.model_validate_json(
        json.dumps(raw_view, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )

    assert service.delete_project(project_id)["status"] == "deleted"
    replay = service.preview_import(str(source))
    with pytest.raises(ProductError) as confirmation:
        service.confirm_import(str(replay["preview_id"]), _mapping(replay))
    with pytest.raises(ProductError) as result:
        persist_product_result(store_root, stale_view)

    assert confirmation.value.code == "record_not_found"
    assert result.value.code == "record_not_found"
    with ProjectStore(store_root) as store:
        assert store.list_records(project_id) == ()


@pytest.mark.parametrize(
    "tamper",
    ["missing_pointer", "missing_confirmation", "mismatched_preview", "lease_hash"],
)
def test_delete_fails_closed_for_broken_product_ownership(tmp_path: Path, tamper: str) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    confirmed = _confirm(service, source)
    project_id = str(confirmed["project_id"])
    source_before = _tree_state(source)
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        if tamper == "missing_pointer":
            connection.execute("DELETE FROM product_projects WHERE project_id = ?", (project_id,))
        elif tamper == "missing_confirmation":
            connection.execute("DELETE FROM product_confirmations")
        elif tamper == "mismatched_preview":
            connection.execute(
                "UPDATE product_confirmations SET preview_id = ?",
                ("p6-preview-" + "f" * 64,),
            )
        else:
            connection.execute(
                "UPDATE product_preview_leases SET canonical_sha256 = ?", ("0" * 64,)
            )

    with pytest.raises(ProductError) as captured:
        service.delete_project(project_id)

    assert captured.value.code == "store_integrity_error"
    assert _tree_state(source) == source_before
    with ProjectStore(store_root) as store:
        assert store.list_records(project_id)


def test_delete_rejects_tampered_p3_record_ownership(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    confirmed = _confirm(service, source)
    project_id = str(confirmed["project_id"])
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        record_id = str(
            connection.execute(
                "SELECT record_id FROM records WHERE project_id = ? ORDER BY record_id LIMIT 1",
                (project_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "UPDATE records SET project_id = ? WHERE record_id = ?",
            ("p3-project-" + "e" * 64, record_id),
        )

    with pytest.raises(ProductError) as captured:
        service.delete_project(project_id)

    assert captured.value.code == "store_integrity_error"
    assert _tree_state(source)["."][0]


def test_purge_unlink_retains_a_digest_that_is_still_indexed(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    confirmed = _confirm(ProductService(store_root), _project(tmp_path / "source"))
    project_id = str(confirmed["project_id"])
    with ProjectStore(store_root) as store:
        record = store.list_records(project_id)[0]
        payload = store.read_object(record.object_sha256)

        assert store.remove_purged_object_files((record.object_sha256,)) == 1
        assert store.read_object(record.object_sha256) == payload


def test_repeat_delete_reconciles_completed_tombstone_state(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    confirmed = _confirm(service, _project(tmp_path / "source"))
    project_id = str(confirmed["project_id"])
    assert service.delete_project(project_id)["status"] == "deleted"
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        tombstone_id = str(
            connection.execute(
                "SELECT record_id FROM product_records WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO product_projects(project_id, project_state_record_id) VALUES (?, ?)",
            (project_id, tombstone_id),
        )

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).delete_project(project_id)

    assert captured.value.code == "store_integrity_error"

"""Matched durable commits and inclusive dependency-safe serving retention."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.model_load_serving_store import ServingStore


def append(store: ServingStore, scope: str, step: int, parent: str | None = None) -> None:
    store.append(
        scope,
        step,
        {"scope": scope, "records": [{"kind": "closure", "load_count": 0}]},
        parent,
        {"http_status": 200, "output": [float(step)]},
    )


@pytest.mark.parametrize("arm", ["static", "full"])
@pytest.mark.parametrize("horizon", [0, 2, 8])
def test_inclusive_horizon_current_audit_then_expiry(
    tmp_path: Path, arm: str, horizon: int
) -> None:
    store = ServingStore(tmp_path / "receipts.sqlite", arm, horizon)
    append(store, "load", 0)
    store.retire(horizon)
    assert store.query("load") is not None
    store.retire(horizon + 1)
    assert store.query("load") is None
    assert store.stats()["deleted_rows"] == 1
    assert store.close() > 0


@pytest.mark.parametrize("arm", ["static", "full"])
@pytest.mark.parametrize("horizon", [0, 2, 8])
def test_active_object_and_surviving_child_keep_old_load(
    tmp_path: Path,
    arm: str,
    horizon: int,
) -> None:
    store = ServingStore(tmp_path / "receipts.sqlite", arm, horizon)
    append(store, "old-load", 0)
    store.keep_parent("old-load")
    store.retire(horizon + 1)
    assert store.query("old-load") is not None
    child_step = horizon + 2
    append(store, "child", child_step, "old-load")
    store.retire(child_step)
    assert store.query("child") is not None
    append(store, "new-load", child_step + 1)
    store.keep_parent("new-load")
    store.retire(max(child_step + 1, child_step + horizon))
    child = store.query("child")
    if horizon:
        assert child is not None and child["parent_frame"]["scope"] == "old-load"
    else:
        assert child is None and store.query("old-load") is None
    store.retire(child_step + 1 + horizon)
    assert store.query("child") is None and store.query("old-load") is None
    assert store.query("new-load") is not None
    store.keep_parent(None)
    store.retire(child_step + 2 + horizon)
    assert store.stats()["rows"] == 0
    store.close()


@pytest.mark.parametrize("arm", ["static", "full"])
def test_transitive_ancestry_and_all_expired_chain_delete_atomically(
    tmp_path: Path, arm: str
) -> None:
    store = ServingStore(tmp_path / "receipts.sqlite", arm, 2)
    append(store, "root", 0)
    append(store, "middle", 1, "root")
    append(store, "leaf", 2, "middle")
    store.retire(4)
    assert store.query("root") is not None and store.query("middle") is not None
    leaf = store.query("leaf")
    assert leaf is not None and leaf["parent_frame"]["scope"] == "middle"
    store.retire(5)
    assert store.stats()["rows"] == 0
    store.close()


@pytest.mark.parametrize("arm", ["static", "full"])
def test_every_append_is_visible_to_independent_connection(tmp_path: Path, arm: str) -> None:
    path = tmp_path / "receipts.sqlite"
    store = ServingStore(path, arm, 0)
    observer = sqlite3.connect(path)
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    append(store, "load", 0)
    assert observer.execute("SELECT count(*) FROM receipt").fetchone()[0] == 1
    append(store, "infer", 1, "load")
    assert observer.execute("SELECT count(*) FROM receipt").fetchone()[0] == 2
    assert store.stats()["append_commits"] == 2
    observer.close()
    assert store.checkpoint() > 0
    store.close()
    with sqlite3.connect(path) as reopened:
        assert reopened.execute("SELECT scope FROM receipt ORDER BY step").fetchall() == [
            ("load",),
            ("infer",),
        ]


@pytest.mark.parametrize("arm", ["static", "full"])
def test_sixteen_inferences_share_epoch_before_retirement(tmp_path: Path, arm: str) -> None:
    path = tmp_path / "receipts.sqlite"
    store = ServingStore(path, arm, 0)
    append(store, "load", 0)
    store.keep_parent("load")
    with sqlite3.connect(path) as observer:
        for index in range(16):
            append(store, f"infer-{index}", 0, "load")
            assert observer.execute("SELECT count(*) FROM receipt").fetchone()[0] == index + 2
            result = store.query(f"infer-{index}")
            assert result is not None and result["parent_frame"]["scope"] == "load"
    assert store.stats()["append_commits"] == 17
    store.retire(0)
    assert store.stats()["rows"] == 17
    with pytest.raises(ValueError, match="unretired"):
        append(store, "late", 0, "load")
    append(store, "next-infer", 1, "load")
    store.retire(1)
    assert store.query("infer-0") is None
    assert store.query("next-infer") is not None
    store.close()


def test_arms_have_identical_schema_query_frames_and_commit_counts(tmp_path: Path) -> None:
    stores = [ServingStore(tmp_path / f"{arm}.sqlite", arm, 2) for arm in ("static", "full")]
    for store in stores:
        append(store, "load", 0)
        append(store, "infer", 1, "load")
        store.retire(2)
    schema = [
        store.db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        for store in stores
    ]
    assert schema[0] == schema[1]
    assert stores[0].query("infer") == stores[1].query("infer")
    static, full = (store.stats() for store in stores)
    assert static["logical_frame_bytes"] == full["logical_frame_bytes"]
    assert static["logical_detail_bytes"] < full["logical_detail_bytes"]
    assert static["append_commits"] == full["append_commits"] == 2
    assert static["retire_commits"] == full["retire_commits"] == 1
    assert static["bookkeeping_index_rows"] == full["bookkeeping_index_rows"] == 6
    assert all(row["peak_storage_bytes"] > 0 for row in (static, full))
    for store in stores:
        store.close()


@pytest.mark.parametrize("arm", ["static", "full"])
def test_discarded_static_detail_is_not_serialized_but_frames_always_are(
    tmp_path: Path,
    arm: str,
) -> None:
    store = ServingStore(tmp_path / "receipts.sqlite", arm, 2)
    detail = {"unused_application_object": object()}
    if arm == "static":
        store.append("load", 0, {"kind": "load"}, None, detail)
        assert store.db.execute("SELECT detail FROM receipt").fetchone()[0] == "{}"
    else:
        with pytest.raises(ValueError, match="finite JSON"):
            store.append("load", 0, {"kind": "load"}, None, detail)
        assert store.stats()["append_commits"] == 0
    with pytest.raises(ValueError, match="finite JSON"):
        store.append("invalid-frame", 1, {"invalid": object()}, None, {})
    with pytest.raises(ValueError, match="JSON object"):
        store.append("invalid-detail", 1, {"kind": "load"}, None, None)  # type: ignore[arg-type]
    store.close()


@pytest.mark.parametrize("arm", ["static", "full"])
def test_frames_are_detached_and_duplicate_or_missing_parent_rejected(
    tmp_path: Path, arm: str
) -> None:
    store = ServingStore(tmp_path / "receipts.sqlite", arm, 2)
    frame: dict[str, Any] = {"records": [{"kind": "load", "digest": "a" * 64}]}
    store.append("load", 0, frame, None, {})
    frame["records"].clear()
    result = store.query("load")
    assert result is not None and len(result["frame"]["records"]) == 1
    result["frame"]["records"].clear()
    queried = store.query("load")
    assert queried is not None and len(queried["frame"]["records"]) == 1
    with pytest.raises(ValueError, match="duplicate"):
        append(store, "load", 1)
    with pytest.raises(ValueError, match="retained parent"):
        append(store, "child", 1, "missing")
    with pytest.raises(ValueError, match="unavailable"):
        store.keep_parent("missing")
    assert store.stats()["append_commits"] == 1
    store.close()


@pytest.mark.parametrize("mutation", ["missing-parent", "cycle", "bad-json"])
def test_external_tamper_fails_closed(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "receipts.sqlite"
    store = ServingStore(path, "static", 2)
    append(store, "root", 0)
    append(store, "child", 1, "root")
    with sqlite3.connect(path) as other:
        if mutation == "missing-parent":
            other.execute("DELETE FROM receipt WHERE scope='root'")
        elif mutation == "cycle":
            other.execute("UPDATE receipt SET parent='child' WHERE scope='root'")
        else:
            other.execute("UPDATE receipt SET frame='[]' WHERE scope='root'")
    with pytest.raises(ValueError):
        store.query("child")
    if mutation == "missing-parent":
        with pytest.raises(ValueError, match="missing parent"):
            store.retire(1)
    store.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"arm": "hash_only", "horizon": 2},
        {"arm": "static", "horizon": True},
        {"arm": "static", "horizon": 1},
    ],
)
def test_invalid_configuration_rejected(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="arm/horizon"):
        ServingStore(tmp_path / "receipts.sqlite", **kwargs)

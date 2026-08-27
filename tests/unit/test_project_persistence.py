"""Project-store migration, immutability and transaction contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.project.lineage import (
    build_lineage_edge,
    build_lineage_graph,
    build_lineage_node,
)
from aletheia_lab.project.persistence import (
    PROJECT_STORE_SCHEMA_VERSION,
    ProjectStore,
    ProjectStoreError,
)

_PROJECT = "p3-project-" + "1" * 64


def _graph(suffix: str = "2"):  # type: ignore[no-untyped-def]
    project = build_lineage_node(
        project_id=_PROJECT,
        kind="project",
        source_id=_PROJECT,
        source_sha256="1" * 64,
        visibility="public",
    )
    snapshot = build_lineage_node(
        project_id=_PROJECT,
        kind="snapshot",
        source_id="p3-snapshot-" + suffix * 64,
        source_sha256=suffix * 64,
        visibility="diagnosis",
    )
    return build_lineage_graph(
        _PROJECT,
        (project, snapshot),
        (build_lineage_edge(project, snapshot, "contains"),),
    )


def test_store_round_trips_restarts_and_exports_byte_stably(tmp_path: Path) -> None:
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        records = store.persist((graph,))
        first_export = store.export_index(_PROJECT)
        assert store.load(graph.graph_id) == graph
        assert records[0].record_id == graph.graph_id
        assert len(records[0].canonical_sha256) == 64
        assert store.persist((graph,)) == records

    with ProjectStore(tmp_path) as reopened:
        assert reopened.load(graph.graph_id) == graph
        assert reopened.export_index(_PROJECT) == first_export
        assert len(reopened.list_records(_PROJECT, "lineage_graph")) == 1


def test_store_detects_object_tampering_on_read_and_restart(tmp_path: Path) -> None:
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        record = store.persist((graph,))[0]
        path = store._object_path(record.object_sha256)
        path.write_bytes(b"tampered")
        with pytest.raises(ProjectStoreError, match="integrity"):
            store.load(graph.graph_id)

    with pytest.raises(ProjectStoreError, match="integrity"):
        ProjectStore(tmp_path)


def test_artifact_hash_mismatch_fails_before_record_visibility(tmp_path: Path) -> None:
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        with pytest.raises(ProjectStoreError, match="declared"):
            store.persist(
                (graph,), artifacts={"0" * 64: ("application/octet-stream", b"bytes")}
            )
        assert store.list_records(_PROJECT) == ()


def test_database_transaction_rolls_back_all_visible_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _graph("2")
    second = _graph("3")
    with ProjectStore(tmp_path) as store:
        original = store._insert_record
        calls = 0

        def fail_second(model, value):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected transaction failure")
            return original(model, value)

        monkeypatch.setattr(store, "_insert_record", fail_second)
        with pytest.raises(RuntimeError, match="injected"):
            store.persist((first, second))
        assert store.list_records(_PROJECT) == ()


def test_migrations_are_versioned_and_future_schema_fails_closed(tmp_path: Path) -> None:
    with ProjectStore(tmp_path) as store:
        version = store._connection.execute("PRAGMA user_version").fetchone()[0]
        history = store._connection.execute(
            "SELECT version FROM migration_history ORDER BY version"
        ).fetchall()
        assert version == PROJECT_STORE_SCHEMA_VERSION
        assert [row[0] for row in history] == list(range(1, PROJECT_STORE_SCHEMA_VERSION + 1))

    database = tmp_path / "project-store.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA user_version = {PROJECT_STORE_SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(ProjectStoreError, match="newer"):
        ProjectStore(tmp_path)


def test_schema_v1_store_migrates_to_v2_without_rewriting_objects(tmp_path: Path) -> None:
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        record = store.persist((graph,))[0]
        object_bytes = store.read_object(record.object_sha256)
        store._connection.execute("DELETE FROM lineage_edges")
        store._connection.execute("DELETE FROM lineage_nodes")
        store._connection.execute("DROP TABLE lineage_edges")
        store._connection.execute("DROP TABLE lineage_nodes")
        store._connection.execute("DELETE FROM migration_history WHERE version = 2")
        store._connection.execute("PRAGMA user_version = 1")
        store._connection.commit()

    with ProjectStore(tmp_path) as migrated:
        assert migrated.read_object(record.object_sha256) == object_bytes
        assert migrated.load(graph.graph_id) == graph
        assert migrated._connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migration_history_tampering_fails_closed(tmp_path: Path) -> None:
    with ProjectStore(tmp_path):
        pass
    connection = sqlite3.connect(tmp_path / "project-store.sqlite3")
    connection.execute("UPDATE migration_history SET migration_sha256 = ? WHERE version = 1", ("0" * 64,))
    connection.commit()
    connection.close()
    with pytest.raises(ProjectStoreError, match="migration history"):
        ProjectStore(tmp_path)


def test_lineage_index_tampering_is_detected_on_restart(tmp_path: Path) -> None:
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        store.persist((graph,))
        store._connection.execute(
            "UPDATE lineage_edges SET relationship = 'supports' WHERE graph_id = ?",
            (graph.graph_id,),
        )
        store._connection.commit()
    with pytest.raises(ProjectStoreError, match="lineage index"):
        ProjectStore(tmp_path)

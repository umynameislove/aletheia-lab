from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import (
    ProductLifecycleStore,
    build_product_lifecycle_record,
    put_current_project_state,
)

_PROJECT_ID = f"p3-project-{'1' * 64}"
_FOREIGN_PROJECT_ID = f"p3-project-{'2' * 64}"
_SNAPSHOT_ID = f"p3-snapshot-{'3' * 64}"
_NEXT_SNAPSHOT_ID = f"p3-snapshot-{'4' * 64}"


def test_lifecycle_record_is_byte_stable_and_reloads_after_restart(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    record = build_product_lifecycle_record(
        record_kind="result",
        project_id=_PROJECT_ID,
        snapshot_id=_SNAPSHOT_ID,
        payload={"mode": "project_audit", "external_call": False},
    )

    with ProductLifecycleStore(store_root) as first:
        assert first.put(record) == "created"
        assert first.put(record) == "identical"

    with ProductLifecycleStore(store_root) as reopened:
        loaded = reopened.get(
            record.record_id,
            expected_kind="result",
            project_id=_PROJECT_ID,
            snapshot_id=_SNAPSHOT_ID,
        )

    assert loaded == record
    assert loaded.model_dump_json() == record.model_dump_json()
    assert loaded.payload() == {"external_call": False, "mode": "project_audit"}


def test_lifecycle_lookup_rejects_invalid_and_foreign_ids_without_echoing_them(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    record = build_product_lifecycle_record(
        record_kind="result",
        project_id=_PROJECT_ID,
        snapshot_id=_SNAPSHOT_ID,
        payload={"status": "saved"},
    )

    with ProductLifecycleStore(store_root) as lifecycle:
        lifecycle.put(record)
        with pytest.raises(ProductError) as malformed:
            lifecycle.get("p6-result-private-text", expected_kind="result")
        tampered_id = f"{record.record_id[:-1]}{'0' if record.record_id[-1] != '0' else '1'}"
        with pytest.raises(ProductError) as tampered:
            lifecycle.get(tampered_id, expected_kind="result")
        with pytest.raises(ProductError) as foreign:
            lifecycle.get(
                record.record_id,
                expected_kind="result",
                project_id=_FOREIGN_PROJECT_ID,
            )

    assert malformed.value.code == "invalid_id"
    assert "private-text" not in malformed.value.safe_message
    assert tampered.value.code == "record_not_found"
    assert tampered_id not in tampered.value.safe_message
    assert foreign.value.code == "record_not_found"
    assert record.record_id not in foreign.value.safe_message
    assert _FOREIGN_PROJECT_ID not in foreign.value.safe_message


def test_lifecycle_lookup_detects_tampered_persistent_payload(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    record = build_product_lifecycle_record(
        record_kind="preview",
        payload={"included_count": 1},
    )
    with ProductLifecycleStore(store_root) as lifecycle:
        lifecycle.put(record)

    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.execute(
            "UPDATE product_records SET payload_json = ? WHERE record_id = ?",
            ('{"included_count":2}', record.record_id),
        )

    with ProductLifecycleStore(store_root) as reopened, pytest.raises(ProductError) as captured:
        reopened.get(record.record_id, expected_kind="preview")

    assert captured.value.code == "store_integrity_error"
    assert record.record_id not in captured.value.safe_message
    assert captured.value.__context__ is None


def test_current_project_state_advances_without_rewriting_history(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    first = build_product_lifecycle_record(
        record_kind="project_state",
        project_id=_PROJECT_ID,
        payload={"snapshot_id": _SNAPSHOT_ID, "status": "confirmed"},
    )
    second = build_product_lifecycle_record(
        record_kind="project_state",
        project_id=_PROJECT_ID,
        payload={"snapshot_id": _NEXT_SNAPSHOT_ID, "status": "refreshed"},
    )
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert put_current_project_state(connection, first) == "created"
        assert put_current_project_state(connection, second) == "created"

    with ProductLifecycleStore(store_root) as reopened:
        assert reopened.get_project_state(_PROJECT_ID) == second
        assert reopened.get(first.record_id, expected_kind="project_state") == first


def test_current_project_state_rejects_invalid_or_tampered_scope(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    state = build_product_lifecycle_record(
        record_kind="project_state",
        project_id=_PROJECT_ID,
        payload={"snapshot_id": _SNAPSHOT_ID, "status": "confirmed"},
    )
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        put_current_project_state(connection, state)
        connection.execute(
            "UPDATE product_projects SET project_id = ? WHERE project_id = ?",
            (_FOREIGN_PROJECT_ID, _PROJECT_ID),
        )

    with ProductLifecycleStore(store_root) as reopened:
        with pytest.raises(ProductError) as malformed:
            reopened.get_project_state("private-project")
        with pytest.raises(ProductError) as tampered:
            reopened.get_project_state(_FOREIGN_PROJECT_ID)

    assert malformed.value.code == "invalid_id"
    assert "private-project" not in malformed.value.safe_message
    assert tampered.value.code == "store_integrity_error"
    assert _FOREIGN_PROJECT_ID not in tampered.value.safe_message

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

import aletheia_lab.product.lifecycle as lifecycle_module
from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    ProductLifecycleStore,
    build_product_lifecycle_record,
    put_product_lifecycle_record,
)

_PROJECT_ID = f"p3-project-{'1' * 64}"
_SNAPSHOT_ID = f"p3-snapshot-{'2' * 64}"
_LEASED_AT = "2026-10-10T00:00:00.000000Z"
_RecordKind = Literal["preview", "result"]


def _record(kind: _RecordKind, token: str) -> ProductLifecycleRecord:
    if kind == "preview":
        return build_product_lifecycle_record(
            record_kind=kind,
            payload={"p3_preview": {"project_id": _PROJECT_ID}, "probe": token},
        )
    return build_product_lifecycle_record(
        record_kind=kind,
        project_id=_PROJECT_ID,
        snapshot_id=_SNAPSHOT_ID,
        payload={"probe": token},
    )


def _write(store: ProductLifecycleStore, record: ProductLifecycleRecord) -> str:
    if record.record_kind == "preview":
        return store.put_preview(record, leased_at=_LEASED_AT)
    return store.put(record)


@pytest.mark.parametrize("kind", ["preview", "result"])
def test_lifecycle_guard_and_write_serialize_against_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: _RecordKind,
) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    record = _record(kind, "before_deletion")
    late_record = _record(kind, "after_deletion")
    tombstone = build_product_lifecycle_record(
        record_kind="deletion",
        project_id=_PROJECT_ID,
        payload={
            "schema_version": "p6-project-deletion/v1",
            "status": "deleted",
            "deleted_count": 0,
            "retained_shared_count": 0,
            "purge_object_sha256s": [],
        },
    )
    real_guard = lifecycle_module.ensure_product_project_active
    observed_transactions: list[bool] = []

    with ProductLifecycleStore(store_root) as writer:
        contender = sqlite3.connect(store_root / "project-store.sqlite3", timeout=0)
        try:

            def guarded(connection: sqlite3.Connection, project_id: str) -> None:
                observed_transactions.append(connection.in_transaction)
                assert connection.in_transaction
                # A deletion must not acquire its writer lock between eligibility
                # inspection and publication of the lifecycle record or lease.
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    contender.execute("BEGIN IMMEDIATE")
                assert not contender.in_transaction
                real_guard(connection, project_id)

            monkeypatch.setattr(lifecycle_module, "ensure_product_project_active", guarded)
            assert _write(writer, record) == "created"
            assert not writer.connection.in_transaction
            assert writer.get(record.record_id, expected_kind=kind) == record

            # Once the first write releases its lock, deletion can commit. The
            # next write must then observe the tombstone and roll back completely.
            contender.execute("BEGIN IMMEDIATE")
            put_product_lifecycle_record(contender, tombstone)
            contender.commit()
            with pytest.raises(ProductError) as rejected:
                _write(writer, late_record)
            assert rejected.value.code == "record_not_found"
            assert not writer.connection.in_transaction
            assert observed_transactions == [True, True]
        finally:
            contender.close()

    with ProductLifecycleStore(store_root) as reopened:
        assert reopened.get(tombstone.record_id, expected_kind="deletion") == tombstone
        with pytest.raises(ProductError) as absent:
            reopened.get(late_record.record_id, expected_kind=kind)
        assert absent.value.code == "record_not_found"
        if kind == "preview":
            assert reopened.preview_leased_at(record.record_id) == _LEASED_AT
            assert reopened.preview_leased_at(late_record.record_id) is None

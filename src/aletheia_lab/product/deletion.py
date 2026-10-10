"""Reference-safe, restart-reconcilable product project deletion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, Literal

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    ProductLifecycleStore,
    _canonical_utc_timestamp,
    _preview_lease_sha256,
    build_product_lifecycle_record,
    put_product_lifecycle_record,
    validate_product_project_id,
    validate_product_record_id,
)
from aletheia_lab.project.persistence import ProjectPurgePlan, ProjectStore

_RECORD_NOT_FOUND_MESSAGE: Final[str] = "The requested product record is not available."
_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored product state failed its integrity check."
_DELETION_SCHEMA_VERSION: Final[str] = "p6-project-deletion/v1"


@dataclass(frozen=True, slots=True)
class _ProductDeletionScope:
    project_id: str
    record_ids: tuple[str, ...]
    preview_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DeletionState:
    status: Literal["pending", "deleted"]
    deleted_count: int
    retained_shared_count: int
    purge_object_sha256s: tuple[str, ...]


def _record_from_row(row: sqlite3.Row) -> ProductLifecycleRecord:
    try:
        record = ProductLifecycleRecord.model_validate(
            {
                "record_id": row["record_id"],
                "schema_version": row["schema_version"],
                "record_kind": row["record_kind"],
                "project_id": row["project_id"],
                "snapshot_id": row["snapshot_id"],
                "parent_result_id": row["parent_result_id"],
                "payload_json": row["payload_json"],
            }
        )
    except (TypeError, ValueError):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None
    if record.canonical_sha256() != str(row["canonical_sha256"]):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return record


def _project_state_scope(
    records: tuple[ProductLifecycleRecord, ...],
    current_id: str,
) -> tuple[tuple[ProductLifecycleRecord, ...], str]:
    project_states = tuple(record for record in records if record.record_kind == "project_state")
    current = next(record for record in records if record.record_id == current_id)
    if not project_states or current.record_kind != "project_state":
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    try:
        preview_ids = {
            validate_product_record_id(
                str(record.payload().get("preview_id")), expected_kind="preview"
            )
            for record in project_states
        }
    except (TypeError, ValueError, ProductError):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None
    if len(preview_ids) != 1:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return project_states, next(iter(preview_ids))


def _validate_confirmed_preview(
    connection: sqlite3.Connection,
    project_id: str,
    project_states: tuple[ProductLifecycleRecord, ...],
    preview_id: str,
) -> None:
    confirmation_rows = connection.execute(
        """
        SELECT confirmations.preview_id, confirmations.project_state_record_id
        FROM product_confirmations AS confirmations
        JOIN product_records AS records
          ON records.record_id = confirmations.project_state_record_id
        WHERE records.project_id = ? ORDER BY confirmations.preview_id
        """,
        (project_id,),
    ).fetchall()
    state_ids = {record.record_id for record in project_states}
    if (
        len(confirmation_rows) != 1
        or str(confirmation_rows[0]["preview_id"]) != preview_id
        or str(confirmation_rows[0]["project_state_record_id"]) not in state_ids
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    preview_row = connection.execute(
        """
        SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
               parent_result_id, payload_json, canonical_sha256
        FROM product_records WHERE record_id = ?
        """,
        (preview_id,),
    ).fetchone()
    if preview_row is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    preview = _record_from_row(preview_row)
    if preview.record_kind != "preview" or preview.record_id != preview_id:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    lease = connection.execute(
        "SELECT leased_at, canonical_sha256 FROM product_preview_leases WHERE preview_id = ?",
        (preview_id,),
    ).fetchone()
    if lease is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    try:
        leased_at = _canonical_utc_timestamp(str(lease["leased_at"]))
    except (TypeError, ValueError):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None
    if str(lease["canonical_sha256"]) != _preview_lease_sha256(preview_id, leased_at):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)


def _product_scope(
    connection: sqlite3.Connection,
    project_id: str,
) -> _ProductDeletionScope | None:
    pointer = connection.execute(
        "SELECT project_state_record_id FROM product_projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if pointer is None:
        orphan = connection.execute(
            "SELECT 1 FROM product_records "
            "WHERE project_id = ? AND record_kind != 'deletion' LIMIT 1",
            (project_id,),
        ).fetchone()
        if orphan is not None:
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        return None
    rows = connection.execute(
        """
        SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
               parent_result_id, payload_json, canonical_sha256
        FROM product_records WHERE project_id = ? ORDER BY record_id
        """,
        (project_id,),
    ).fetchall()
    records = tuple(_record_from_row(row) for row in rows)
    current_id = str(pointer["project_state_record_id"])
    if (
        not records
        or current_id not in {record.record_id for record in records}
        or any(record.project_id != project_id for record in records)
        or any(record.record_kind == "deletion" for record in records)
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)

    project_states, preview_id = _project_state_scope(records, current_id)
    _validate_confirmed_preview(connection, project_id, project_states, preview_id)
    return _ProductDeletionScope(
        project_id=project_id,
        record_ids=tuple(record.record_id for record in records),
        preview_ids=(preview_id,),
    )


def _deletion_record(
    connection: sqlite3.Connection,
    project_id: str,
) -> ProductLifecycleRecord | None:
    rows = connection.execute(
        """
        SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
               parent_result_id, payload_json, canonical_sha256
        FROM product_records
        WHERE project_id = ? AND record_kind = 'deletion' ORDER BY record_id
        """,
        (project_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    record = _record_from_row(rows[0])
    if record.record_kind != "deletion" or record.project_id != project_id:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return record


def _deletion_state(record: ProductLifecycleRecord) -> _DeletionState:
    payload = record.payload()
    status = payload.get("status")
    expected_keys = {
        "schema_version",
        "status",
        "deleted_count",
        "retained_shared_count",
    }
    expected_keys.add("purge_object_sha256s")
    if set(payload) != expected_keys or payload.get("schema_version") != _DELETION_SCHEMA_VERSION:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    deleted_count = payload.get("deleted_count")
    retained_count = payload.get("retained_shared_count")
    digests = payload.get("purge_object_sha256s")
    if (
        status not in {"pending", "deleted"}
        or not isinstance(deleted_count, int)
        or isinstance(deleted_count, bool)
        or deleted_count < 0
        or not isinstance(retained_count, int)
        or isinstance(retained_count, bool)
        or retained_count < 0
        or not isinstance(digests, list)
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in digests
        )
        or digests != sorted(set(digests))
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    assert isinstance(deleted_count, int)
    assert isinstance(retained_count, int)
    assert isinstance(digests, list)
    checked_status: Literal["pending", "deleted"] = "pending" if status == "pending" else "deleted"
    return _DeletionState(
        checked_status,
        deleted_count,
        retained_count,
        tuple(str(value) for value in digests),
    )


def _pending_record(
    project_id: str,
    scope: _ProductDeletionScope,
    plan: ProjectPurgePlan,
) -> ProductLifecycleRecord:
    deleted_count = (
        len(scope.record_ids)
        + len(scope.preview_ids)
        + len(plan.record_ids)
        + len(plan.purge_object_sha256s)
    )
    return build_product_lifecycle_record(
        record_kind="deletion",
        project_id=project_id,
        payload={
            "schema_version": _DELETION_SCHEMA_VERSION,
            "status": "pending",
            "deleted_count": deleted_count,
            "retained_shared_count": len(plan.retained_shared_sha256s),
            "purge_object_sha256s": list(plan.purge_object_sha256s),
        },
    )


def _write_pending_deletion(
    connection: sqlite3.Connection,
    *,
    scope: _ProductDeletionScope,
    pending: ProductLifecycleRecord,
) -> None:
    if _product_scope(connection, scope.project_id) != scope:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    for preview_id in scope.preview_ids:
        connection.execute("DELETE FROM product_confirmations WHERE preview_id = ?", (preview_id,))
        connection.execute("DELETE FROM product_preview_leases WHERE preview_id = ?", (preview_id,))
    connection.execute("DELETE FROM product_projects WHERE project_id = ?", (scope.project_id,))
    for record_id in (*scope.record_ids, *scope.preview_ids):
        connection.execute("DELETE FROM product_records WHERE record_id = ?", (record_id,))
    put_product_lifecycle_record(connection, pending)


def _complete_deletion(
    lifecycle: ProductLifecycleStore,
    pending: ProductLifecycleRecord,
    state: _DeletionState,
) -> None:
    if pending.project_id is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    completed = build_product_lifecycle_record(
        record_kind="deletion",
        project_id=pending.project_id,
        payload={
            "schema_version": _DELETION_SCHEMA_VERSION,
            "status": "deleted",
            "deleted_count": state.deleted_count,
            "retained_shared_count": state.retained_shared_count,
            "purge_object_sha256s": list(state.purge_object_sha256s),
        },
    )
    with lifecycle.connection:
        if _deletion_record(lifecycle.connection, pending.project_id) != pending:
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        put_product_lifecycle_record(lifecycle.connection, completed)
        lifecycle.connection.execute(
            "DELETE FROM product_records WHERE record_id = ?", (pending.record_id,)
        )


def _assert_deleted_product_scope(
    connection: sqlite3.Connection,
    project_id: str,
    tombstone: ProductLifecycleRecord,
) -> None:
    pointer = connection.execute(
        "SELECT 1 FROM product_projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    rows = connection.execute(
        "SELECT record_id FROM product_records WHERE project_id = ? ORDER BY record_id",
        (project_id,),
    ).fetchall()
    confirmations = connection.execute(
        """
        SELECT confirmations.preview_id
        FROM product_confirmations AS confirmations
        JOIN product_records AS records
          ON records.record_id = confirmations.project_state_record_id
        WHERE records.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    if (
        pointer is not None
        or confirmations
        or [str(row[0]) for row in rows] != [tombstone.record_id]
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)


def _receipt(project_id: str, status: str, deleted: int, retained: int) -> dict[str, object]:
    return {
        "project_id": project_id,
        "status": status,
        "deleted_count": deleted,
        "retained_shared_count": retained,
    }


def _load_or_begin_deletion(
    store_root: Path,
    project_id: str,
) -> tuple[ProductLifecycleRecord, _DeletionState, bool]:
    with ProductLifecycleStore(store_root) as lifecycle:
        tombstone = _deletion_record(lifecycle.connection, project_id)
        if tombstone is not None:
            state = _deletion_state(tombstone)
            completed = state.status == "deleted"
            if completed:
                _assert_deleted_product_scope(lifecycle.connection, project_id, tombstone)
            return tombstone, state, completed
        scope = _product_scope(lifecycle.connection, project_id)
    if scope is None:
        raise ProductError("record_not_found", _RECORD_NOT_FOUND_MESSAGE)
    with ProjectStore(store_root) as store:
        plan = store.plan_project_purge(project_id)
        tombstone = _pending_record(project_id, scope, plan)
        store.commit_project_purge(
            plan,
            transaction_write=partial(
                _write_pending_deletion,
                scope=scope,
                pending=tombstone,
            ),
        )
    return tombstone, _deletion_state(tombstone), False


def _reconcile_purged_objects(
    store_root: Path,
    project_id: str,
    state: _DeletionState,
    *,
    completed: bool,
) -> _DeletionState:
    with ProjectStore(store_root) as store:
        if store.list_records(project_id):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        newly_retained = store.remove_purged_object_files(state.purge_object_sha256s)
        store.checkpoint_after_purge()
    if completed or not newly_retained:
        return state
    if newly_retained > state.deleted_count:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return _DeletionState(
        state.status,
        state.deleted_count - newly_retained,
        state.retained_shared_count + newly_retained,
        state.purge_object_sha256s,
    )


def delete_product_project(store_root: Path, project_id: str) -> dict[str, object]:
    """Delete one confirmed project without touching its source root."""

    checked_id = validate_product_project_id(project_id)
    try:
        tombstone, state, completed = _load_or_begin_deletion(store_root, checked_id)
        state = _reconcile_purged_objects(
            store_root,
            checked_id,
            state,
            completed=completed,
        )
        if completed:
            return _receipt(checked_id, "already_deleted", 0, state.retained_shared_count)
        with ProductLifecycleStore(store_root) as lifecycle:
            if _product_scope(lifecycle.connection, checked_id) is not None:
                raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
            _assert_deleted_product_scope(lifecycle.connection, checked_id, tombstone)
            _complete_deletion(lifecycle, tombstone, state)
        return _receipt(
            checked_id,
            "deleted",
            state.deleted_count,
            state.retained_shared_count,
        )
    except ProductError:
        raise
    except Exception:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None


__all__ = ["delete_product_project"]

"""Append-only, author-owned reviews of current authorized canonical results."""
from __future__ import annotations

import re
from typing import cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _view_from_record

from .action_audit import record_action
from .application import VERSION
from .authorization import AuthorizationDenied, PostgresAuthorization
from .native_upload_records import (
    UploadResultRecord,
    UploadResultRecordV2,
    parse_upload_result_record,
)
from .postgres_catalog import PostgresCatalog
from .transactions import run_transaction
from .workspace_management import WorkspaceConflict, _revision

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_STATUSES = frozenset({"comment", "approved", "rejected"})


def _annotation_id(value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("A bounded annotation identifier is required.")


def _item(workspace: str, project: str, result: str, annotation: str, row: tuple[object, ...]) -> dict[str, object]:
    return {"schema_version": VERSION, "workspace_id": workspace, "project_id": project,
            "result_id": result, "annotation_id": annotation, "revision": row[0],
            "author_id": row[1], "body": row[2], "status": row[3], "created_at": row[4]}


class PostgresReviews:
    def __init__(self, pool: Pool, catalog: PostgresCatalog, *, audit: bool = False) -> None:
        self._pool, self._catalog = pool, catalog
        self._audit = audit
        self._policy = PostgresAuthorization()

    async def _load_result(self, digest: SecretStr, workspace: str, result: str, action: str) -> str:
        async with self._pool.connection() as conn:
            auth = await self._policy.authorize(conn, _digest(digest), workspace, action)
            row = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id=%s "
                "AND record_id=%s AND record_type IN ('result','upload_result')", (workspace, result))).fetchone()
            if row is None or not isinstance(row[0], str):
                raise AuthorizationDenied()
            project = row[0]
            await self._policy.authorize_record(conn, auth, workspace, project, result, action)
        record = await self._catalog.read_record(credential_digest=digest, workspace_id=workspace,
                                                 project_id=project, record_id=result)
        if isinstance(record, (UploadResultRecord, UploadResultRecordV2)):
            record = parse_upload_result_record(record.model_dump(mode="python"))
            if (record.workspace_id, record.project_id, record.record_id) != (workspace, project, result):
                raise AuthorizationDenied()
        elif isinstance(record, ProductLifecycleRecord):
            if (record.record_kind != "result" or record.record_id != result or record.project_id != project):
                raise AuthorizationDenied()
            _view_from_record(record)
        else:
            raise AuthorizationDenied()
        return project

    async def _current(self, conn: Connection, digest: SecretStr, workspace: str, project: str,
                       result: str, action: str) -> str:
        auth = await self._policy.authorize(conn, _digest(digest), workspace, action)
        kind = await self._policy.authorize_record(conn, auth, workspace, project, result, action,
                                                   for_update=action == "review")
        if kind not in ("result", "upload_result"):
            raise AuthorizationDenied()
        return auth.user_id

    async def put(self, *, credential_digest: SecretStr, workspace_id: str, result_id: str,
                  annotation_id: str, body: str, status: str, expected_revision: int) -> dict[str, object]:
        _annotation_id(annotation_id)
        _revision(expected_revision)
        if (not isinstance(body, str) or not body.strip() or len(body) > 4000
                or not isinstance(status, str) or status not in _STATUSES):
            raise ValueError("A bounded review body and valid status are required.")
        project = await self._load_result(credential_digest, workspace_id, result_id, "review")
        async def commit(conn: Connection) -> dict[str, object]:
            author = await self._current(conn, credential_digest, workspace_id, project, result_id, "review")
            latest = await (await conn.execute("SELECT revision,author_id FROM j01_annotations WHERE workspace_id=%s "
                "AND result_id=%s AND annotation_id=%s ORDER BY revision DESC LIMIT 1",
                (workspace_id, result_id, annotation_id))).fetchone()
            if latest is not None and latest[1] != author:
                raise AuthorizationDenied()
            if (0 if latest is None else latest[0]) != expected_revision:
                raise WorkspaceConflict()
            revision = expected_revision + 1
            row = await (await conn.execute("INSERT INTO j01_annotations(workspace_id,project_id,result_id,annotation_id,"
                "revision,author_id,body,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING "
                "RETURNING revision,author_id,body,status,created_at",
                (workspace_id, project, result_id, annotation_id, revision, author, body, status))).fetchone()
            if row is None:
                raise WorkspaceConflict()
            if self._audit:
                await record_action(conn, actor_id=author, workspace_id=workspace_id, action="review.put",
                    effect_id=annotation_id, revision=revision, parent_id=result_id)
            return _item(workspace_id, project, result_id, annotation_id, row)

        return await run_transaction(self._pool, commit)

    async def list_history(self, *, credential_digest: SecretStr, workspace_id: str, result_id: str,
                           annotation_id: str, after_revision: int = 0, limit: int = 200) -> dict[str, object]:
        _annotation_id(annotation_id)
        _revision(after_revision)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("A bounded review page is required.")
        project = await self._load_result(credential_digest, workspace_id, result_id, "read")
        async with self._pool.connection() as conn:
            await self._current(conn, credential_digest, workspace_id, project, result_id, "read")
            cursor = await conn.execute("SELECT revision,author_id,body,status,created_at FROM j01_annotations "
                "WHERE workspace_id=%s AND project_id=%s AND result_id=%s AND annotation_id=%s AND revision>%s "
                "ORDER BY revision LIMIT %s", (workspace_id, project, result_id, annotation_id, after_revision, limit + 1))
            rows = []
            while (row := await cursor.fetchone()) is not None:
                rows.append(row)
            selected = rows[:limit]
            return {"schema_version": VERSION, "workspace_id": workspace_id, "project_id": project,
                    "result_id": result_id, "annotation_id": annotation_id,
                    "items": [_item(workspace_id, project, result_id, annotation_id, row) for row in selected],
                    "next_after_revision": cast(int, selected[-1][0]) if len(rows) > limit else None}

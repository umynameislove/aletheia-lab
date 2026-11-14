"""job output for the durable PostgreSQL job repository."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.project_operations import FollowUpResult
from aletheia_lab.hosted_contract.project_refresh import RefreshResult
from aletheia_lab.product.lifecycle import ProductLifecycleRecord

from .authorization import Authorization, PostgresAuthorization
from .native_upload_records import (
    UploadResultRecord,
    UploadResultRecordV2,
    result_view,
)
from .postgres_catalog import CatalogIntegrityError
from .postgres_sources import PostgresSources, SourceNotFound
from .upload_snapshot import (
    UploadSnapshot,
    UploadSnapshotV2,
    parse_upload_snapshot,
)
from .workspace_management import _revision

if TYPE_CHECKING:
    from .postgres_jobs import PostgresJobs


async def output(self: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> dict[str, object]:

    digest = _digest(credential_digest)
    async with self._pool.connection() as conn:
        await self._access._access(conn, digest, workspace_id, action="read")
        row = await (await conn.execute("SELECT operation,request,result,to_jsonb(j01_jobs)->>'canonical_result_id' "
            "FROM j01_jobs WHERE job_id=%s "
            "AND workspace_id=%s AND state='succeeded'", (job_id, workspace_id))).fetchone()
        if row is None or not isinstance(row[0], str) or not isinstance(row[1], dict):
            raise SourceNotFound()
        await self._authorize(conn, digest, workspace_id, row[0], row[1], reading=True)
        if not isinstance(row[2], dict):
            raise RuntimeError("Invalid job output")
        operation, request, result = row[0], row[1], row[2]
        linked = row[3] if len(row) > 3 else None
        project = None
        if linked is not None:
            linked, project = await _linked_project(conn, workspace_id, linked)
    if operation == "refresh":
        if linked is not None:
            raise CatalogIntegrityError()
        result = await self._refresh_output(credential_digest=credential_digest, workspace_id=workspace_id,
                                            request=request, result=result)
    if operation == "confirm":
        snapshot_id = result.get('snapshot_id')
        if not isinstance(snapshot_id, str):
            raise CatalogIntegrityError()
        await self._access.snapshot(credential_digest=credential_digest, workspace_id=workspace_id,
                                    snapshot_id=snapshot_id)
    if linked is not None:
        if project is None:
            raise SourceNotFound()
        result = await self._canonical_output(credential_digest=credential_digest, workspace_id=workspace_id,
            operation=operation, request=request, result=result, linked=linked, project=project)
    return {"schema_version": "product-api/v1-draft", "workspace_id": workspace_id,
            "job_id": job_id, "operation": operation, "result": result}


async def _canonical_output(self: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str, operation: str,
                            request: dict[str, object], result: dict[str, object], linked: str,
                            project: str) -> dict[str, object]:
    from .postgres_jobs import _ANALYSIS_MODELS

    digest = _digest(credential_digest)
    if self._read_catalog is None:
        raise TimeoutError("Canonical result storage is unavailable.")
    record = await self._read_catalog.read_record(credential_digest=credential_digest, workspace_id=workspace_id,
                                                   project_id=project, record_id=linked)
    if (not isinstance(record, (ProductLifecycleRecord, UploadResultRecord, UploadResultRecordV2))
            or record.record_id != linked or record.project_id != project):
        raise CatalogIntegrityError()
    view = result_view(record)
    if operation == "analyze":
        if (record.parent_result_id is not None or record.snapshot_id != request.get("snapshot_id")
                or len(view.conversation.turns) != 1 or view.conversation.turns[0].question != request.get("question")
                or view.runtime.model != request.get("analysis_model", _ANALYSIS_MODELS[0])
                or result != {"result_id": linked}):
            raise CatalogIntegrityError()
        result = view.model_dump(mode="json")
    elif operation == "follow_up":
        summary = FollowUpResult.model_validate(result, strict=True)
        if (summary.result_id != linked or summary.project_id != project
                or summary.parent_result_id != record.parent_result_id
                or summary.snapshot_id != record.snapshot_id or request.get("project_id") != project
                or request.get("parent_result_id") != record.parent_result_id):
            raise CatalogIntegrityError()
        result = summary.model_dump(mode="json")
    else:
        raise CatalogIntegrityError()
    async with self._pool.connection() as conn:
        await self._authorize(conn, digest, workspace_id, operation, request, reading=True)
    return result


async def _refresh_output(self: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str,
                          request: dict[str, object], result: dict[str, object]) -> dict[str, object]:

    if self._read_catalog is None:
        raise TimeoutError("Canonical refresh storage is unavailable.")
    summary = RefreshResult.model_validate(result, strict=True)
    revision = _revision(request.get("expected_revision"))
    if (summary.project_id != request.get("project_id")
            or summary.previous_snapshot_id != request.get("parent_snapshot_id")
            or summary.revision != revision + (summary.status == "new_snapshot")):
        raise CatalogIntegrityError()
    record = await self._read_catalog.read_record(credential_digest=credential_digest, workspace_id=workspace_id,
        project_id=summary.project_id, record_id=summary.snapshot_id)
    if (not isinstance(record, (UploadSnapshot, UploadSnapshotV2)) or record.workspace_id != workspace_id
            or record.mapping.project_id != summary.project_id or record.snapshot_id != summary.snapshot_id
            or summary.status == "new_snapshot" and record.source_id != request.get("source_id")):
        raise CatalogIntegrityError()
    async with self._pool.connection() as conn:
        await self._authorize(conn, _digest(credential_digest), workspace_id, "refresh", request, reading=True)
        saved = await (await conn.execute("SELECT canonical_record_id,payload::text FROM j01_upload_snapshots "
            "WHERE workspace_id=%s AND snapshot_id=%s AND source_id=%s",
            (workspace_id, summary.snapshot_id, record.source_id))).fetchone()
        source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE workspace_id=%s AND source_id=%s",
            (workspace_id, record.source_id))).fetchone()
        if (saved is None or saved[0] != summary.snapshot_id or not isinstance(saved[1], str)
                or parse_upload_snapshot(saved[1]) != record or source != (record.source_sha256,)):
            raise CatalogIntegrityError()
    return summary.model_dump(mode="json")

async def _linked_project(conn: Connection, workspace_id: str, linked: object) -> tuple[str, str]:
    if not isinstance(linked, str):
        raise CatalogIntegrityError()
    saved = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id=%s "
        "AND record_id=%s AND record_type IN ('result','upload_result')", (workspace_id, linked))).fetchone()
    if saved is None or not isinstance(saved[0], str):
        raise SourceNotFound()
    project = saved[0]
    return linked, project



class JobCatalogPolicy(PostgresAuthorization):
    def __init__(self, access: PostgresSources) -> None:
        self._access = access

    async def authorize(self, conn: Connection, digest: str, workspace_id: str, action: str) -> Authorization:
        return await self._access._access(conn, digest, workspace_id, action="analyze" if action == "read" else action)

    async def authorize_project(self, conn: Connection, auth: Authorization, workspace_id: str,
                                project_id: str, action: str, *, allow_deleted: bool = False) -> None:
        await super().authorize_project(conn, auth, workspace_id, project_id, "analyze" if action == "read" else action,
                                         allow_deleted=allow_deleted)

    async def authorize_source(self, conn: Connection, auth: Authorization, workspace_id: str,
                               source_id: str, action: str) -> None:
        await super().authorize_source(conn, auth, workspace_id, source_id, "analyze" if action == "read" else action)



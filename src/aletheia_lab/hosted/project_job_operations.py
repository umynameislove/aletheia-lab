"""Canonical project admission on the existing durable job queue and catalog."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.project.identity import (
    granted_root_fingerprint,
    project_id_for_root,
)

from .authorization import Authorization
from .postgres_sources import SourceConflict, SourceNotFound
from .upload_snapshot import AnyUploadSnapshot, UploadSnapshot, UploadSnapshotV2
from .workspace_management import WorkspaceConflict, _revision

if TYPE_CHECKING:
    from .postgres_jobs import JobClaim, PostgresJobs

_REQUEST_FIELDS = {"project_id", "parent_snapshot_id", "expected_revision", "source_id", "origin_source_sha256"}


def validate_refresh(request: dict[str, object]) -> None:
    if set(request) != _REQUEST_FIELDS:
        raise ValueError("Invalid refresh request.")
    patterns = {"project_id": r"p3-project-[0-9a-f]{64}", "parent_snapshot_id": r"upload-snapshot-[0-9a-f]{64}",
                "source_id": r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", "origin_source_sha256": r"[0-9a-f]{64}"}
    for field, pattern in patterns.items():
        if not isinstance(request[field], str) or re.fullmatch(pattern, cast(str, request[field])) is None:
            raise ValueError("Invalid refresh scope.")
    _revision(request["expected_revision"])
    if project_id_for_root(granted_root_fingerprint("hosted-upload-source/v1:" + cast(str, request["origin_source_sha256"]))) != request["project_id"]:
        raise ValueError("Invalid refresh origin.")


async def authorize_refresh(jobs: PostgresJobs, conn: Connection, digest: str, workspace: str,
                            request: dict[str, object], *, reading: bool) -> Authorization:
    validate_refresh(request)
    action = "read" if reading else "refresh"
    auth = await jobs._access._access(conn, digest, workspace, action=action)
    project, parent = cast(str, request["project_id"]), cast(str, request["parent_snapshot_id"])
    await jobs._access._authorization.authorize_project(conn, auth, workspace, project, action)
    row = await (await conn.execute("SELECT source_id FROM k05_records WHERE workspace_id=%s AND project_id=%s "
        "AND record_id=%s AND record_type='upload_snapshot'", (workspace, project, parent))).fetchone()
    if row is None or not isinstance(row[0], str):
        raise SourceNotFound()
    for source in set((row[0], cast(str, request["source_id"]))):
        await jobs._access._authorization.authorize_source(conn, auth, workspace, source, action)
    return auth


async def enqueue_refresh(jobs: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str,
                          project_id: str, expected_revision: int, request_key: SecretStr,
                          source_id: str) -> JobReceipt:
    _revision(expected_revision)
    key = request_key.get_secret_value()
    if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key) is None:
        raise ValueError("A bounded request key is required.")
    async with jobs._pool.connection() as conn:
        auth = await jobs._access._access(conn, _digest(credential_digest), workspace_id, action="refresh")
        await jobs._access._authorization.authorize_project(conn, auth, workspace_id, project_id, "refresh")
        previous = await (await conn.execute("SELECT request FROM j01_jobs WHERE workspace_id=%s AND creator_id=%s "
            "AND operation='refresh' AND key_hash=%s", (workspace_id, auth.user_id, content_hasher(key.encode()).hexdigest()))).fetchone()
        if previous is not None:
            request = previous[0]
            if not isinstance(request, dict) or any(request.get(k) != v for k, v in {
                "project_id": project_id, "expected_revision": expected_revision, "source_id": source_id}.items()):
                raise SourceConflict()
        else:
            current = await (await conn.execute("SELECT revision,provenance_kind FROM k05_projects "
                "WHERE workspace_id=%s AND project_id=%s", (workspace_id, project_id))).fetchone()
            if current != (expected_revision, "upload"):
                raise WorkspaceConflict()
            parent = await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id=%s "
                "AND project_id=%s AND pointer_name='snapshot'", (workspace_id, project_id))).fetchone()
            origin = await (await conn.execute("SELECT s.sha256 FROM k05_records r JOIN j01_sources s "
                "ON s.workspace_id=r.workspace_id AND s.source_id=r.source_id WHERE r.workspace_id=%s "
                "AND r.project_id=%s AND r.record_type='upload_snapshot' ORDER BY r.created_at,r.record_id LIMIT 1",
                (workspace_id, project_id))).fetchone()
            if parent is None or origin is None:
                raise SourceNotFound()
            request = {"project_id": project_id, "parent_snapshot_id": parent[0], "expected_revision": expected_revision,
                       "source_id": source_id, "origin_source_sha256": origin[0]}
    return await jobs.enqueue(credential_digest=credential_digest, workspace_id=workspace_id, operation="refresh",
                              request_key=request_key, request=request)


async def refresh_parent(jobs: PostgresJobs, *, claim: JobClaim) -> tuple[AnyUploadSnapshot, str]:
    if claim.operation != "refresh" or jobs._catalog is None:
        raise ValueError("A canonical refresh claim is required.")
    request = claim.request
    async with jobs._pool.connection() as conn:
        await authorize_refresh(jobs, conn, _digest(claim.credential_digest), claim.workspace_id, request, reading=False)
        current = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s",
            (claim.workspace_id, request["project_id"]))).fetchone()
        pointer = await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id=%s "
            "AND project_id=%s AND pointer_name='snapshot'", (claim.workspace_id, request["project_id"]))).fetchone()
        if current != (request["expected_revision"],) or pointer != (request["parent_snapshot_id"],):
            raise WorkspaceConflict()
    record = await jobs._catalog.read_record(credential_digest=claim.credential_digest, workspace_id=claim.workspace_id,
        project_id=cast(str, request["project_id"]), record_id=cast(str, request["parent_snapshot_id"]))
    if not isinstance(record, (UploadSnapshot, UploadSnapshotV2)):
        raise SourceNotFound()
    return record, cast(str, request["origin_source_sha256"])


async def enqueue_analysis(jobs: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str,
                           project_id: str, snapshot_id: str, question: str,
                           request_key: SecretStr) -> JobReceipt:
    if jobs._catalog is None:
        raise TimeoutError("Canonical project storage is unavailable.")
    # Admission persists an authorized immutable reference. The worker verifies
    # full canonical bytes before computing; large snapshots stay out of HTTP ACK.
    async with jobs._pool.connection() as conn:
        auth = await jobs._access._access(conn, _digest(credential_digest), workspace_id, action='analyze')
        kind = await jobs._access._authorization.authorize_record(conn, auth, workspace_id,
            project_id, snapshot_id, 'analyze')
        if kind != 'upload_snapshot':
            raise SourceNotFound()
    return await jobs.enqueue(credential_digest=credential_digest, workspace_id=workspace_id, operation="analyze",
        request_key=request_key, request={"snapshot_id": snapshot_id, "question": question})

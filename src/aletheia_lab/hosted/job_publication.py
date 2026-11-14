"""job publication for the durable PostgreSQL job repository."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.project_operations import FollowUpRequest, FollowUpResult
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)

from .async_work import run_blocking
from .canonical_results import CanonicalResults, PreparedResult
from .job_lifecycle import PUBLICATION_BUDGET
from .native_upload_records import (
    CanonicalResultRecord,
    UploadResultRecord,
    UploadResultRecordV2,
    result_view,
)
from .postgres_catalog import CatalogIntegrityError, PostgresCatalog
from .postgres_sources import SourceNotFound
from .transactions import run_transaction
from .upload_publication import PublishedUploadSnapshot
from .upload_snapshot import (
    AnyUploadSnapshot,
    UploadSnapshot,
    UploadSnapshotV2,
    parse_upload_snapshot,
)
from .workspace_management import _revision

if TYPE_CHECKING:
    from .postgres_jobs import JobClaim, PostgresJobs


async def complete_result(self: PostgresJobs, *, claim: JobClaim, lease_digest: SecretStr,
                          record: CanonicalResultRecord) -> bool:

    if self._canonical is None or claim.operation not in ("analyze", "follow_up"):
        raise ValueError("A canonical result job is required.")
    canonical = self._canonical
    lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
    request_payload = json.dumps(claim.request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    request = json.loads(request_payload)
    expected_revision: int | None
    if claim.operation == "follow_up":
        expected_revision = _revision(request.get("expected_revision"))
        parent = await self._read_parent(credential_digest=claim.credential_digest, workspace_id=claim.workspace_id,
            parent_result_id=cast(str, request.get("parent_result_id")), project_id=cast(str, request.get("project_id")))
        _check_followup_view(parent, record, request)
        prepared = await canonical.prepare_result(claim.workspace_id, parent=parent, record=record)
    else:
        prepared = await _prepare_initial_result(self, claim, record, request, canonical)
        expected_revision = None
    project = prepared.result.project_id
    if project is None:
        raise ValueError("A canonical result project is required.")

    async def publish(conn: Connection) -> bool:
        from psycopg import sql

        auth = await self._access._access(conn, digest, claim.workspace_id, action="analyze")
        current = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s "
            "AND project_id=%s FOR UPDATE", (claim.workspace_id, project))).fetchone()
        if current is None:
            raise SourceNotFound()
        await self._access._authorization.authorize_project(conn, auth, claim.workspace_id, project, "analyze")
        if claim.operation == "analyze":
            await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
        fenced = await (await conn.execute(sql.SQL(
                                               'SELECT job_id FROM j01_jobs WHERE job_id=%s AND workspace_id=%s AND credential_digest=%s AND operation=%s'
                                               " AND state='running' AND lease_digest=%s AND lease_until>clock_timestamp() AND {} AND request=%s::jsonb "
                                               'FOR UPDATE'
                                           ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
            (claim.job_id, claim.workspace_id, digest, claim.operation, lease, request_payload))).fetchone()
        if fenced is None:
            return False
        published = await canonical.enlist_result(conn, authorization=auth, prepared=prepared,
            expected_revision=_revision(current[0]) if expected_revision is None else expected_revision)
        result: dict[str, object] = {"result_id": published.record_id}
        if claim.operation == "follow_up":
            result = FollowUpResult.model_validate({"result_id": published.record_id, "project_id": project,
                "parent_result_id": prepared.result.parent_result_id, "snapshot_id": prepared.result.snapshot_id,
                "revision": published.revision}).model_dump(mode="json")
        terminal = await (await conn.execute(sql.SQL(
                                                 "UPDATE j01_jobs SET state='succeeded',result=%s::jsonb,canonical_result_id=%s WHERE job_id=%s AND "
                                                 "workspace_id=%s AND credential_digest=%s AND operation=%s AND state='running' AND lease_digest=%s AND "
                                                 'lease_until>clock_timestamp() AND {} AND request=%s::jsonb RETURNING job_id'
                                             ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
            (json.dumps(result), published.record_id, claim.job_id, claim.workspace_id, digest,
             claim.operation, lease, request_payload))).fetchone()
        if terminal is None:
            raise TimeoutError("The result publication lease expired.")
        await self._record_action(conn, claim.workspace_id, claim.job_id, "job.publish", effect=published.record_id, parent=project)
        return True

    return await run_transaction(self._pool, publish)


async def _complete_canonical_snapshot(self: PostgresJobs, *, claim: JobClaim, lease_digest: SecretStr,
                                        snapshot: AnyUploadSnapshot) -> bool:

    from .postgres_catalog import CatalogIntegrityError
    from .upload_publication import (
        PreparedUploadSnapshot,
        SnapshotAlreadyPublished,
        UploadPublication,
    )

    catalog = self._catalog
    if catalog is None:
        raise RuntimeError("Canonical snapshot storage is unavailable.")
    lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
    request_payload = json.dumps(claim.request, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True, separators=(",", ":"))
    request = json.loads(request_payload)
    snapshot = await run_blocking(lambda: parse_upload_snapshot(snapshot.model_dump(mode="python")))
    parent = await _snapshot_parent(self, claim, request, snapshot)
    snapshot = await _reuse_confirm_snapshot(self, claim, digest, request, snapshot, catalog)
    async with self._pool.connection() as conn:
        await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
        source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE workspace_id=%s AND source_id=%s",
            (claim.workspace_id, snapshot.source_id))).fetchone()
        if source != (snapshot.source_sha256,):
            raise SourceNotFound()
        projection = await (await conn.execute("SELECT source_id,payload::text,canonical_record_id FROM j01_upload_snapshots "
            "WHERE snapshot_id=%s AND workspace_id=%s", (snapshot.snapshot_id, claim.workspace_id))).fetchone()
        canonical_exists = await (await conn.execute("SELECT 1 FROM k05_records WHERE workspace_id=%s AND record_id=%s",
            (claim.workspace_id, snapshot.snapshot_id))).fetchone()
    existing = await _existing_snapshot(claim, snapshot, catalog, projection, canonical_exists)
    publication = UploadPublication(catalog.objects)
    # Bound preparation separately; every database publication keeps its 5s transaction budget.
    async with asyncio.timeout(30):
        prepared = await publication.prepare_snapshot(snapshot, existing=existing,
            parent=parent if claim.operation == "refresh" and self.execution else None)
        for attempt in range(2):
            first_json = await run_blocking(prepared.snapshot.model_dump_json)
            terminal_json = json.dumps({"snapshot_id": prepared.snapshot.snapshot_id})

            async def publish(conn: Connection, snapshot_attempt: PreparedUploadSnapshot = prepared,
                              encoded_snapshot: str = first_json, encoded_terminal: str = terminal_json) -> bool:
                from psycopg import sql

                auth = await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
                # This read takes no job lock: project publication must precede the job lock.
                fence = await (await conn.execute(sql.SQL(
                                                      'SELECT job_id FROM j01_jobs WHERE job_id=%s AND workspace_id=%s AND credential_digest=%s AND operation=%s'
                                                      " AND state='running' AND lease_digest=%s AND lease_until>clock_timestamp() AND {} AND "
                                                      "request->>'source_id'=%s AND request=%s::jsonb"
                                                  ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                    (claim.job_id, claim.workspace_id, digest, claim.operation, lease, request["source_id"], request_payload))).fetchone()
                if fence is None:
                    return False
                published = await publication.enlist_snapshot(conn, authorization=auth, prepared=snapshot_attempt,
                    expected_revision=_revision(request["expected_revision"]) if claim.operation == "refresh" else None)
                first = snapshot_attempt.snapshot
                await _enlist_snapshot_projection(conn, first, encoded_snapshot)
                encoded_terminal = _snapshot_terminal(claim, first, published, request, encoded_terminal)
                terminal = await (await conn.execute(sql.SQL(
                                                         "UPDATE j01_jobs SET state='succeeded',result=%s::jsonb WHERE job_id=%s AND workspace_id=%s AND "
                                                         "credential_digest=%s AND operation=%s AND state='running' AND lease_digest=%s AND "
                                                         "lease_until>clock_timestamp() AND {} AND request->>'source_id'=%s AND request=%s::jsonb RETURNING job_id"
                                                     ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                    (encoded_terminal, claim.job_id, claim.workspace_id,
                     digest, claim.operation, lease, request["source_id"], request_payload))).fetchone()
                if terminal is None:
                    raise TimeoutError("The job publication fence changed.")
                await self._record_action(conn, claim.workspace_id, claim.job_id, "job.publish",
                                          effect=first.snapshot_id, parent=published.project_id)
                return True

            try:
                return await run_transaction(self._pool, publish, timeout_seconds=5)
            except SnapshotAlreadyPublished as error:
                if attempt:
                    raise CatalogIntegrityError() from None
                # The failed transaction has rolled back before canonical bytes are read/staged.
                reconciled = await catalog.read_record(credential_digest=claim.credential_digest,
                    workspace_id=claim.workspace_id, project_id=snapshot.mapping.project_id,
                    record_id=error.snapshot_id)
                if not isinstance(reconciled, (UploadSnapshot, UploadSnapshotV2)):
                    raise CatalogIntegrityError() from None
                prepared = await publication.prepare_snapshot(snapshot, existing=reconciled,
                    parent=parent if claim.operation == "refresh" and self.execution else None)
    raise TimeoutError("Canonical snapshot publication timed out.")

def _check_followup_view(parent: CanonicalResultRecord, record: CanonicalResultRecord,
                         request: dict[str, object]) -> None:
    parent_view = result_view(parent)
    command = FollowUpRequest.model_validate({"schema_version": "product-api/v1-draft",
        "selection_kind": request.get("selection_kind"), "selection_id": request.get("selection_id"),
        "question": request.get("question")})
    selection = resolve_product_follow_up_selection(parent_view, selection_kind=command.selection_kind,
        selection_id=command.selection_id, question=command.question)
    expected = build_product_follow_up_view(parent_view, selection).conversation.turns[-1]
    child_view = result_view(record)
    actual = child_view.conversation.turns[-1]
    if (actual.id != expected.id or actual.question != expected.question
            or actual.visible_evidence_ids != expected.visible_evidence_ids or actual.abstained is not True
            or child_view.result.disposition != (None if parent_view.result.status == "technical_failure" else "abstain")):
        raise ValueError("The follow-up result does not match its request.")

async def _prepare_initial_result(self: PostgresJobs, claim: JobClaim, record: CanonicalResultRecord,
                                  request: dict[str, object], canonical: CanonicalResults) -> PreparedResult:
    from .postgres_jobs import _ANALYSIS_MODELS

    if (set(request) not in ({"snapshot_id", "question"}, {"snapshot_id", "question", "analysis_model"})
            or request.get("analysis_model", _ANALYSIS_MODELS[0]) not in _ANALYSIS_MODELS
            or not isinstance(record, (UploadResultRecord, UploadResultRecordV2))):
        raise ValueError("An initial upload analysis is required.")
    snapshot = await self._access.snapshot(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, snapshot_id=cast(str, request["snapshot_id"]))
    if (not isinstance(request["question"], str) or record.view.conversation.turns[0].question != request["question"]
            or record.view.runtime.model != request.get("analysis_model", _ANALYSIS_MODELS[0])):
        raise ValueError("The initial result does not match its request.")
    prepared = await canonical.prepare_initial_result(claim.workspace_id, snapshot=snapshot, record=record)
    return prepared


async def _snapshot_parent(self: PostgresJobs, claim: JobClaim, request: dict[str, object],
                           snapshot: AnyUploadSnapshot) -> AnyUploadSnapshot | None:
    parent = None
    if claim.operation == "refresh":
        parent, _ = await self.refresh_parent(claim=claim)
        if (snapshot.workspace_id != claim.workspace_id or snapshot.mapping.project_id != request["project_id"]
                or (snapshot.snapshot_id == parent.snapshot_id and snapshot != parent)
                or (snapshot.snapshot_id != parent.snapshot_id and snapshot.source_id != request["source_id"])):
            raise ValueError("Invalid refresh snapshot scope.")
    elif (claim.operation != "confirm" or snapshot.workspace_id != claim.workspace_id
            or request.get("source_id") != snapshot.source_id):
        raise ValueError("Invalid snapshot scope.")
    return parent

async def _reuse_confirm_snapshot(self: PostgresJobs, claim: JobClaim, digest: str,
                                 request: dict[str, object], snapshot: AnyUploadSnapshot,
                                 catalog: PostgresCatalog) -> AnyUploadSnapshot:
    if claim.operation == "confirm":
        async with self._pool.connection() as conn:
            await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
            current = await (await conn.execute("SELECT record_id FROM k05_current_pointers "
                "WHERE workspace_id=%s AND project_id=%s AND pointer_name='snapshot'",
                (claim.workspace_id, snapshot.mapping.project_id))).fetchone()
        if current is not None and current[0] != snapshot.snapshot_id:
            original = await catalog.read_record(credential_digest=claim.credential_digest,
                workspace_id=claim.workspace_id, project_id=snapshot.mapping.project_id, record_id=cast(str, current[0]))
            if isinstance(original, (UploadSnapshot, UploadSnapshotV2)):
                # A new upload intent can name identical content; retain its first authorized capture.
                before, after = original.state_payload(), snapshot.state_payload()
                before.pop('source_id')
                after.pop('source_id')
                if before == after:
                    snapshot = original
    return snapshot

async def _existing_snapshot(claim: JobClaim, snapshot: AnyUploadSnapshot, catalog: PostgresCatalog,
                             projection: tuple[object, ...] | None,
                             canonical_exists: tuple[object, ...] | None) -> AnyUploadSnapshot | None:
    existing: AnyUploadSnapshot | None = None
    if projection is not None:
        if projection[0] != snapshot.source_id or not isinstance(projection[1], str):
            raise CatalogIntegrityError()
        if projection[2] is None and canonical_exists is None:
            existing = await run_blocking(_checked_projection, projection[1], claim, snapshot)
        else:
            if projection[2] is not None and projection[2] != snapshot.snapshot_id:
                raise CatalogIntegrityError()
            canonical = await catalog.read_record(credential_digest=claim.credential_digest,
                workspace_id=claim.workspace_id, project_id=snapshot.mapping.project_id, record_id=snapshot.snapshot_id)
            if not isinstance(canonical, (UploadSnapshot, UploadSnapshotV2)):
                raise CatalogIntegrityError()
            existing = canonical
    elif canonical_exists is not None:
        canonical = await catalog.read_record(credential_digest=claim.credential_digest,
            workspace_id=claim.workspace_id, project_id=snapshot.mapping.project_id, record_id=snapshot.snapshot_id)
        if not isinstance(canonical, (UploadSnapshot, UploadSnapshotV2)):
            raise CatalogIntegrityError()
        existing = canonical
    return existing



def _checked_projection(payload: str, claim: JobClaim, snapshot: AnyUploadSnapshot) -> AnyUploadSnapshot:
    try:
        projected = parse_upload_snapshot(payload)
    except ValueError:
        raise CatalogIntegrityError() from None
    if (projected.workspace_id != claim.workspace_id or projected.source_id != snapshot.source_id
            or projected.snapshot_id != snapshot.snapshot_id or projected.state_payload() != snapshot.state_payload()):
        raise CatalogIntegrityError()
    return projected
async def _enlist_snapshot_projection(conn: Connection, first: AnyUploadSnapshot,
                                      encoded_snapshot: str) -> None:
    await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,canonical_record_id) "
        "VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT(snapshot_id) DO NOTHING",
        (first.snapshot_id, first.workspace_id, first.source_id, encoded_snapshot, first.snapshot_id))
    await conn.execute("UPDATE j01_upload_snapshots SET canonical_record_id=%s WHERE snapshot_id=%s "
        "AND workspace_id=%s AND source_id=%s AND canonical_record_id IS NULL AND payload=%s::jsonb",
        (first.snapshot_id, first.snapshot_id, first.workspace_id, first.source_id, encoded_snapshot))
    saved = await (await conn.execute("SELECT canonical_record_id,payload=%s::jsonb FROM j01_upload_snapshots "
        "WHERE snapshot_id=%s AND workspace_id=%s AND source_id=%s",
        (encoded_snapshot, first.snapshot_id, first.workspace_id, first.source_id))).fetchone()
    if saved != (first.snapshot_id, True):
        raise CatalogIntegrityError()

def _snapshot_terminal(claim: JobClaim, first: AnyUploadSnapshot, published: PublishedUploadSnapshot,
                       request: dict[str, object], encoded_terminal: str) -> str:
    if claim.operation == "refresh":
        from aletheia_lab.hosted_contract.project_refresh import RefreshResult

        encoded_terminal = RefreshResult.model_validate({"project_id": published.project_id,
            "status": "unchanged" if first.snapshot_id == request["parent_snapshot_id"] else "new_snapshot",
            "previous_snapshot_id": request["parent_snapshot_id"], "snapshot_id": first.snapshot_id,
            "revision": published.revision}).model_dump_json()
    return encoded_terminal


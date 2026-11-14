"""Durable jobs authorized against current membership and their source parent."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.hosted_contract.job_attempts import JobAttemptPage
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.hosted_contract.project_operations import FollowUpRequest
from aletheia_lab.product.follow_up import (
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.view import AnyProductView, parse_product_view_json

from . import job_admission, job_output, job_publication
from .action_audit import current_request_id, record_action
from .authorization import Authorization, AuthorizationDenied
from .canonical_results import CanonicalResults
from .job_execution import QueuePolicy, execution_profile
from .job_lifecycle import CLAIM_BUDGET, DEADLINE, PUBLICATION_BUDGET, expire_job, public_job
from .native_upload_records import (
    CanonicalResultRecord,
    UploadResultRecord,
    UploadResultRecordV2,
    build_initial_upload_result,
    result_view,
)
from .postgres_catalog import PostgresCatalog
from .postgres_sources import PostgresSources, SourceConflict, SourceNotFound
from .transactions import run_transaction
from .upload_snapshot import (
    AnyUploadSnapshot,
    parse_upload_snapshot,
)
from .workspace_management import _revision

AnalysisModel = Literal["upload-snapshot-observation/v1", "upload-snapshot-metric-observation/v2"]
_ANALYSIS_MODELS = ("upload-snapshot-observation/v1", "upload-snapshot-metric-observation/v2")

Operation = Literal["preview", "confirm", "analyze", "follow_up", "refresh"]
_AVAILABLE = "(state='queued' OR (state='running' AND lease_until<clock_timestamp()))"


class JobStateConflict(ValueError):
    def __init__(self) -> None:
        super().__init__("The job cannot be retried in its current state.")


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    workspace_id: str
    operation: str
    request: dict[str, object]
    credential_digest: SecretStr


class PostgresJobs:
    def __init__(self, pool: Pool, access: PostgresSources, *, audit: bool = False,
                 catalog: PostgresCatalog | None = None, execution: bool = False,
                 queue_policy: QueuePolicy | None = None,
                 analysis_model: AnalysisModel = "upload-snapshot-observation/v1") -> None:
        if type(execution) is not bool:
            raise ValueError('Explicit job execution profile required.')
        self._pool, self._access, self._audit = pool, access, audit
        if analysis_model not in _ANALYSIS_MODELS:
            raise ValueError("A supported analysis model is required.")
        self.analysis_model = analysis_model
        self.execution = execution
        self.queue_policy = QueuePolicy() if queue_policy is None else queue_policy
        self._read_catalog = catalog
        self._catalog = None if catalog is None else PostgresCatalog(pool, catalog.objects, policy=job_output.JobCatalogPolicy(access))
        self._canonical = None if catalog is None else CanonicalResults(catalog.objects)

    async def _record_action(self, conn: Connection, workspace: str, job_id: str, action: str,
                             *, actor: str | None = None, effect: str | None = None,
                             parent: str | None = None) -> None:
        if not self._audit:
            return
        row = await (await conn.execute("SELECT creator_id,request_id FROM j01_jobs WHERE workspace_id=%s AND job_id=%s",
                                        (workspace, job_id))).fetchone()
        if row is None or not isinstance(row[0], str) or not isinstance(row[1], str):
            raise RuntimeError("Invalid job audit identity")
        await record_action(conn, actor_id=actor or row[0], workspace_id=workspace, action=action,
                            effect_id=effect or job_id, operation_id=job_id, job_id=job_id,
                            request_id=current_request_id() or row[1], parent_id=parent)

    async def enqueue_refresh(self, *, credential_digest: SecretStr, workspace_id: str,
                              project_id: str, expected_revision: int, request_key: SecretStr,
                              source_id: str) -> JobReceipt:
        from .project_job_operations import enqueue_refresh

        return await enqueue_refresh(self, credential_digest=credential_digest, workspace_id=workspace_id,
            project_id=project_id, expected_revision=expected_revision, request_key=request_key, source_id=source_id)

    async def refresh_parent(self, *, claim: JobClaim) -> tuple[AnyUploadSnapshot, str]:
        from .project_job_operations import refresh_parent

        return await refresh_parent(self, claim=claim)

    async def enqueue_analysis(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str,
                               snapshot_id: str, question: str, request_key: SecretStr) -> JobReceipt:
        from .project_job_operations import enqueue_analysis

        return await enqueue_analysis(self, credential_digest=credential_digest, workspace_id=workspace_id,
            project_id=project_id, snapshot_id=snapshot_id, question=question, request_key=request_key)

    async def complete_refresh(self, *, claim: JobClaim, lease_digest: SecretStr, snapshot: AnyUploadSnapshot) -> bool:
        return await self._complete_canonical_snapshot(claim=claim, lease_digest=lease_digest, snapshot=snapshot)

    async def _read_parent(self, *, credential_digest: SecretStr, workspace_id: str,
                           parent_result_id: str, project_id: str | None = None) -> CanonicalResultRecord:
        if self._catalog is None:
            raise TimeoutError("Canonical project storage is unavailable.")
        if not isinstance(parent_result_id, str) or re.fullmatch(r"p6-result-[0-9a-f]{64}", parent_result_id) is None:
            raise ValueError("A canonical parent result is required.")
        async with self._pool.connection() as conn:
            auth = await self._access._access(conn, _digest(credential_digest), workspace_id, action="analyze")
            row = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id=%s "
                "AND record_id=%s AND record_type IN ('result','upload_result')", (workspace_id, parent_result_id))).fetchone()
            if row is None or not isinstance(row[0], str) or project_id is not None and row[0] != project_id:
                raise SourceNotFound()
            project_id = row[0]
            await self._access._authorization.authorize_project(conn, auth, workspace_id, project_id, "analyze")
        record = await self._catalog.read_record(credential_digest=credential_digest, workspace_id=workspace_id,
                                                 project_id=project_id, record_id=parent_result_id)
        if (not isinstance(record, (ProductLifecycleRecord, UploadResultRecord, UploadResultRecordV2))
                or isinstance(record, ProductLifecycleRecord) and record.record_kind != "result"
                or record.project_id != project_id or record.record_id != parent_result_id):
            raise SourceNotFound()
        result_view(record)
        return record

    async def enqueue_follow_up(self, *, credential_digest: SecretStr, workspace_id: str,
                                parent_result_id: str, expected_revision: int, request_key: SecretStr,
                                selection_kind: Literal["claim", "node"], selection_id: str,
                                question: str) -> JobReceipt:
        _revision(expected_revision)
        command = FollowUpRequest.model_validate({"schema_version": "product-api/v1-draft",
            "selection_kind": selection_kind, "selection_id": selection_id, "question": question})
        parent = await self._read_parent(credential_digest=credential_digest, workspace_id=workspace_id,
                                         parent_result_id=parent_result_id)
        view = result_view(parent)
        resolve_product_follow_up_selection(view, selection_kind=command.selection_kind,
                                            selection_id=command.selection_id, question=command.question)
        return await self.enqueue(credential_digest=credential_digest, workspace_id=workspace_id,
            operation="follow_up", request_key=request_key, request={"project_id": view.project.id,
                "parent_result_id": parent_result_id, "expected_revision": expected_revision,
                "selection_kind": command.selection_kind, "selection_id": command.selection_id,
                "question": command.question})

    async def follow_up_parent(self, *, claim: JobClaim) -> AnyProductView:
        from psycopg import sql

        if claim.operation != "follow_up":
            raise ValueError("A follow-up job is required.")
        request_payload = json.dumps(claim.request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        request = json.loads(request_payload)
        async with self._pool.connection() as conn:
            await self._authorize(conn, _digest(claim.credential_digest), claim.workspace_id, "follow_up", request)
            row = await (await conn.execute(sql.SQL(
                                                'SELECT job_id FROM j01_jobs WHERE job_id=%s AND workspace_id=%s AND credential_digest=%s AND '
                                                "operation='follow_up' AND state='running' AND lease_until>clock_timestamp() AND {} AND request=%s::jsonb"
                                            ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                (claim.job_id, claim.workspace_id, _digest(claim.credential_digest), request_payload))).fetchone()
            if row is None:
                raise SourceNotFound()
        parent = await self._read_parent(credential_digest=claim.credential_digest, workspace_id=claim.workspace_id,
            parent_result_id=cast(str, request["parent_result_id"]), project_id=cast(str, request["project_id"]))
        return result_view(parent)

    async def complete_result(self, *, claim: JobClaim, lease_digest: SecretStr,
                              record: CanonicalResultRecord) -> bool:
        return await job_publication.complete_result(self, claim=claim, lease_digest=lease_digest, record=record)

    async def is_ready(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT job_id,workspace_id,creator_id,credential_digest,operation,"
                    "key_hash,request_hash,request,request_id,state,lease_digest,lease_until,result,created_at "
                    "FROM j01_jobs LIMIT 0")
                await conn.execute("SELECT source_id,workspace_id,creator_id,request_key_hash,sha256,"
                    "payload,manifest,created_at FROM j01_sources LIMIT 0")
                await conn.execute("SELECT snapshot_id,workspace_id,source_id,payload "
                    "FROM j01_upload_snapshots LIMIT 0")
                await conn.execute("SELECT workspace_id,source_id,user_id,actions,revision FROM j01_source_grants LIMIT 0")
                await conn.execute("SELECT version,checksum,applied_at FROM k04_workspace_migrations LIMIT 0")
                constraint = await (await conn.execute("SELECT convalidated FROM pg_constraint WHERE "
                    "conrelid='j01_upload_snapshots'::regclass AND conname='j01_snapshots_source_scope'")).fetchone()
                if constraint != (True,):
                    return False
            return not self.execution or await self.execution_ready()
        except Exception:
            return False

    async def execution_ready(self) -> bool:
        if not self.execution:
            return False
        try:
            async with self._pool.connection() as conn:
                if not await execution_profile(conn):
                    return False
                await conn.execute('SELECT failure_kind,available_at,attempt_count,deadline_at FROM j01_jobs LIMIT 0')
                await conn.execute('SELECT epoch,heartbeat_at,outcome FROM k09_job_attempts LIMIT 0')
                await conn.execute('SELECT workspace_id,operation,last_claim_at FROM k09_queue_turns LIMIT 0')
            return True
        except Exception:
            return False

    async def attempts(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> JobAttemptPage:
        if not self.execution:
            raise SourceNotFound()
        await self.status(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)
        async with self._pool.connection() as conn:
            row = await (await conn.execute('SELECT operation,request,retry_of_job_id FROM j01_jobs '
                'WHERE workspace_id=%s AND job_id=%s', (workspace_id, job_id))).fetchone()
            if row is None or not isinstance(row[0], str) or not isinstance(row[1], dict):
                raise SourceNotFound()
            await self._authorize(conn, _digest(credential_digest), workspace_id, row[0], row[1], reading=True)
            cursor = await conn.execute('SELECT to_jsonb(a) FROM k09_job_attempts a '
                'WHERE workspace_id=%s AND job_id=%s ORDER BY epoch LIMIT 4', (workspace_id, job_id))
            items = []
            while (item := await cursor.fetchone()) is not None:
                if len(item) != 1 or not isinstance(item[0], dict):
                    raise ValueError('Invalid attempt history.')
                items.append({key: item[0][key] for key in ('epoch', 'started_at', 'heartbeat_at',
                    'lease_until', 'finished_at', 'outcome', 'failure_kind')})
            conflict = None
            if items and items[-1]['failure_kind'] == 'conflict':
                project = row[1].get('project_id')
                current = await (await conn.execute('SELECT revision FROM k05_projects '
                    'WHERE workspace_id=%s AND project_id=%s', (workspace_id, project))).fetchone()
                if current is None:
                    raise SourceNotFound()
                conflict = {'project_id': project, 'current_revision': current[0]}
        return JobAttemptPage.model_validate_json(json.dumps({'schema_version': 'product-api/v1-draft',
            'workspace_id': workspace_id, 'job_id': job_id, 'retry_of_job_id': row[2], 'items': items,
            'conflict': conflict}))

    async def conflicts_ready(self) -> bool:
        if not self.execution:
            return False
        async with self._pool.connection() as conn:
            return await (await conn.execute("SELECT count(*) FROM pg_constraint WHERE "
                "(conrelid='j01_jobs'::regclass AND conname='k09_job_failure_kind_v2' "
                "OR conrelid='k09_job_attempts'::regclass AND conname='k09_attempt_failure_kind_v2') "
                "AND convalidated")).fetchone() == (2,)

    async def heartbeat(self, *, claim: JobClaim, lease_digest: SecretStr, lease_seconds: int) -> bool:
        if not self.execution or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError('Current execution profile and bounded heartbeat required.')
        lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
        async def beat(conn: Connection) -> bool:
            from psycopg import sql

            try:
                await self._authorize(conn, digest, claim.workspace_id, claim.operation, claim.request)
            except (SourceNotFound, AuthorizationDenied):
                return False
            row = await (await conn.execute(sql.SQL(
                                                "UPDATE j01_jobs SET lease_until=LEAST(clock_timestamp()+%s*interval '1 second',deadline_at) WHERE "
                                                'workspace_id=%s AND job_id=%s AND credential_digest=%s AND operation=%s AND request=%s::jsonb AND '
                                                "state='running' AND lease_digest=%s AND lease_until>clock_timestamp() AND {} RETURNING job_id"
                                            ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                (lease_seconds, claim.workspace_id, claim.job_id, digest, claim.operation,
                 json.dumps(claim.request, sort_keys=True, separators=(',', ':'), allow_nan=False), lease))).fetchone()
            return row is not None
        return await run_transaction(self._pool, beat)

    async def acknowledge_cancel(self, *, claim: JobClaim, lease_digest: SecretStr) -> bool:
        if not self.execution:
            return False
        async def acknowledge(conn: Connection) -> bool:
            row = await (await conn.execute("UPDATE j01_jobs SET state='cancelled',failure_kind='cancelled',result=NULL,"
                "lease_digest=NULL,lease_until=NULL WHERE workspace_id=%s AND job_id=%s AND credential_digest=%s "
                "AND operation=%s AND request=%s::jsonb AND state='cancel_requested' AND lease_digest=%s "
                "AND lease_until>clock_timestamp() RETURNING job_id",
                (claim.workspace_id, claim.job_id, _digest(claim.credential_digest), claim.operation,
                 json.dumps(claim.request, sort_keys=True, separators=(',', ':'), allow_nan=False), _digest(lease_digest)))).fetchone()
            if row is not None:
                await self._record_action(conn, claim.workspace_id, claim.job_id, 'job.cancelled')
            return row is not None
        return await run_transaction(self._pool, acknowledge)

    async def reconcile(self, *, limit: int = 64) -> int:
        if not self.execution or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('Current execution profile and bounded reconciliation required.')
        async def sweep(conn: Connection) -> int:
            cursor = await conn.execute("SELECT workspace_id,job_id FROM j01_jobs WHERE state IN ('queued','running','cancel_requested') "
                "AND (deadline_at<=clock_timestamp() OR (lease_until<=clock_timestamp() "
                "AND (state='cancel_requested' OR attempt_count>=max_attempts))) ORDER BY created_at,job_id LIMIT %s", (limit,))
            rows = []
            while (row := await cursor.fetchone()) is not None:
                rows.append(row)
            count = 0
            for workspace, job_id in rows:
                if not isinstance(workspace, str) or not isinstance(job_id, str):
                    raise ValueError('Invalid job scope.')
                count += await self._expire(conn, workspace, job_id)
            return count
        return await run_transaction(self._pool, sweep)

    async def follow_up_ready(self) -> bool:
        if self._catalog is None:
            return False
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT canonical_project_id,parent_result_id,canonical_result_id FROM j01_jobs LIMIT 0")
                await conn.execute("SELECT source_id FROM k05_records LIMIT 0")
                await conn.execute("SELECT provenance_kind FROM k05_projects LIMIT 0")
                await conn.execute("SELECT canonical_record_id FROM j01_upload_snapshots LIMIT 0")
                expected = {"k05_job_operation_v6": ("c", True), "k05_job_request_shape_v6": ("c", True),
                    "k05_follow_up_parent_scope": ("f", True), "k05_job_canonical_result_scope": ("f", True),
                    "k05_upload_snapshot_canonical_scope": ("f", True)}
                rows = await conn.execute("SELECT conname,contype,convalidated FROM pg_constraint WHERE "
                    "conrelid IN ('j01_jobs'::regclass,'j01_upload_snapshots'::regclass) AND conname=ANY(%s)",
                    (list(expected),))
                actual = {}
                while (row := await rows.fetchone()) is not None:
                    actual[row[0]] = row[1:]
                return actual == expected
        except Exception:
            return False

    @staticmethod
    async def _require_control_schema(conn: Connection) -> None:
        try:
            # Pin the relation against DDL for this transaction without serializing writers.
            await conn.execute("SELECT job_id FROM j01_jobs LIMIT 0")
            column = await (await conn.execute("SELECT attnotnull,attgenerated,format_type(atttypid,atttypmod) "
                "FROM pg_attribute WHERE attrelid='j01_jobs'::regclass AND attname='retry_of_job_id' "
                "AND NOT attisdropped")).fetchone()
            if column != (False, "", "text"):
                raise TimeoutError()
            expected = {
                "k05_job_state_v3": ("c", True, "CHECK ((state = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))"),
                "k05_job_identity_scope": ("u", True, "UNIQUE (workspace_id, job_id)"),
                "k05_job_retry_scope": ("f", True, "FOREIGN KEY (workspace_id, retry_of_job_id) REFERENCES j01_jobs(workspace_id, job_id)"),
            }
            result = await conn.execute("SELECT conname,contype,convalidated,pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid='j01_jobs'::regclass AND conname=ANY(%s)", (list(expected),))
            actual = {}
            while (row := await result.fetchone()) is not None:
                actual[row[0]] = row[1:]
            modern = dict(expected)
            modern['k05_job_state_v3'] = ('c', True, "CHECK ((state = ANY (ARRAY['queued'::text, 'running'::text, 'cancel_requested'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'expired'::text])))")
            if actual != expected and actual != modern:
                raise TimeoutError()
        except Exception:
            raise TimeoutError("Job controls are temporarily unavailable.") from None

    async def controls_ready(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await self._require_control_schema(conn)
            return True
        except Exception:
            return False

    async def _source(self, conn: Connection, workspace_id: str, operation: str,
                      request: dict[str, object]) -> str:
        if operation not in ("preview", "confirm", "analyze"):
            raise SourceNotFound()
        if operation == "analyze":
            snapshot_id = request.get("snapshot_id")
            if not isinstance(snapshot_id, str):
                raise SourceNotFound()
            row = await (await conn.execute("SELECT source_id FROM j01_upload_snapshots "
                "WHERE snapshot_id=%s AND workspace_id=%s", (snapshot_id, workspace_id))).fetchone()
            if row is None or not isinstance(row[0], str):
                raise SourceNotFound()
            return row[0]
        source_id = request.get("source_id")
        if not isinstance(source_id, str):
            raise SourceNotFound()
        return source_id

    async def _authorize(self, conn: Connection, digest: str, workspace_id: str, operation: str,
                         request: dict[str, object], *, reading: bool = False) -> Authorization:
        if operation == "refresh":
            from .project_job_operations import authorize_refresh

            return await authorize_refresh(self, conn, digest, workspace_id, request, reading=reading)
        if operation == "follow_up":
            if self._canonical is None:
                raise TimeoutError("Canonical project storage is unavailable.")
            action = "read" if reading else "analyze"
            auth = await self._access._access(conn, digest, workspace_id, action=action)
            project, parent = request.get("project_id"), request.get("parent_result_id")
            if not isinstance(project, str) or not isinstance(parent, str):
                raise SourceNotFound()
            kind = await self._access._authorization.authorize_record(conn, auth, workspace_id, project, parent, action)
            if kind not in ("result", "upload_result"):
                raise SourceNotFound()
            return auth
        if operation == "analyze" and (
                set(request) not in ({"snapshot_id", "question"}, {"snapshot_id", "question", "analysis_model"})
                or request.get("analysis_model", _ANALYSIS_MODELS[0]) not in _ANALYSIS_MODELS):
            raise SourceNotFound()
        action = "read" if reading else operation
        auth = await self._access._access(conn, digest, workspace_id, action=action)
        source_id = await self._source(conn, workspace_id, operation, request)
        await self._access._authorization.authorize_source(conn, auth, workspace_id, source_id, action)
        if operation == "analyze" and self._catalog is not None:
            parent = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id=%s "
                "AND record_id=%s AND record_type='upload_snapshot'", (workspace_id, request["snapshot_id"]))).fetchone()
            if parent is None or not isinstance(parent[0], str):
                raise SourceNotFound()
            await self._access._authorization.authorize_project(conn, auth, workspace_id, parent[0], action)
        return auth

    async def _status_response(self, conn: Connection, digest: str, workspace: str, job_id: str,
                               auth: Authorization) -> Job:
        row = await (await conn.execute("SELECT to_jsonb(j01_jobs) FROM j01_jobs WHERE workspace_id=%s AND job_id=%s",
                                        (workspace, job_id))).fetchone()
        if row is None or not isinstance(row[0], dict):
            raise SourceNotFound()
        metadata = row[0]
        retryable: bool | None = None
        if all(key in metadata for key in ("deadline_at", "attempt_count", "max_attempts")):
            retryable = False
            live_budget = True
            if self.execution:
                live_budget = await (await conn.execute('SELECT deadline_at>clock_timestamp() AND NOT EXISTS('
                    'SELECT 1 FROM j01_jobs child WHERE child.workspace_id=j01_jobs.workspace_id '
                    'AND child.retry_of_job_id=j01_jobs.job_id) FROM j01_jobs '
                    'WHERE workspace_id=%s AND job_id=%s', (workspace, job_id))).fetchone() == (True,)
            budget = live_budget and (not self.execution or (metadata.get('failure_kind') in ('transient', 'cancelled')
                and type(metadata.get('attempt_count')) is int and type(metadata.get('max_attempts')) is int
                and cast(int, metadata['attempt_count']) < cast(int, metadata['max_attempts'])))
            if budget and metadata.get("state") in ("failed", "cancelled") and (
                    auth.user_id == metadata.get("creator_id") or auth.role in ("owner", "admin")):
                try:
                    await self._authorize(conn, digest, workspace, metadata["operation"], metadata["request"])
                except (SourceNotFound, AuthorizationDenied):
                    pass
                else:
                    retryable = True
        return public_job(workspace, job_id, metadata, retryable=retryable)

    async def _expire(self, conn: Connection, workspace: str, job_id: str) -> bool:
        expired = await expire_job(conn, workspace, job_id, execution=self.execution)
        if expired:
            action = "job.fail"
            if self.execution:
                row = await (await conn.execute('SELECT state FROM j01_jobs WHERE workspace_id=%s AND job_id=%s',
                                                (workspace, job_id))).fetchone()
                if row is not None and row[0] in ('expired', 'cancelled'):
                    action = 'job.' + row[0]
            await self._record_action(conn, workspace, job_id, action)
        return expired

    async def status(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job:
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            # Authenticate before object lookup, then authorize its current parent.
            await self._access._access(conn, digest, workspace_id, action="read")
            row = await (await conn.execute("SELECT state,operation,request FROM j01_jobs WHERE job_id=%s "
                "AND workspace_id=%s", (job_id, workspace_id))).fetchone()
            if row is None or not isinstance(row[1], str) or not isinstance(row[2], dict):
                raise SourceNotFound()
            auth = await self._authorize(conn, digest, workspace_id, row[1], row[2], reading=True)
            await self._expire(conn, workspace_id, job_id)
            return await self._status_response(conn, digest, workspace_id, job_id, auth)

    async def output(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> dict[str, object]:
        return await job_output.output(self, credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)

    async def _canonical_output(self, *, credential_digest: SecretStr, workspace_id: str, operation: str,
                                request: dict[str, object], result: dict[str, object], linked: str,
                                project: str) -> dict[str, object]:
        return await job_output._canonical_output(self, credential_digest=credential_digest, workspace_id=workspace_id, operation=operation, request=request, result=result, linked=linked, project=project)

    async def _refresh_output(self, *, credential_digest: SecretStr, workspace_id: str,
                              request: dict[str, object], result: dict[str, object]) -> dict[str, object]:
        return await job_output._refresh_output(self, credential_digest=credential_digest, workspace_id=workspace_id, request=request, result=result)

    async def _control_job(self, conn: Connection, digest: str, workspace_id: str,
                            job_id: str) -> tuple[Authorization, tuple[object, ...]]:
        await self._access._access(conn, digest, workspace_id, action="read")
        await self._require_control_schema(conn)
        candidate = await (await conn.execute("SELECT state,creator_id,operation,request FROM j01_jobs "
            "WHERE job_id=%s AND workspace_id=%s", (job_id, workspace_id))).fetchone()
        if (candidate is None or len(candidate) != 4 or any(not isinstance(candidate[i], str) for i in (0, 1, 2))
                or not isinstance(candidate[3], dict)):
            raise SourceNotFound()
        auth = await self._authorize(conn, digest, workspace_id, cast(str, candidate[2]), candidate[3])
        if auth.user_id != candidate[1] and auth.role not in ("owner", "admin"):
            raise SourceNotFound()
        # Parent authorization locks precede the job row, matching publication.
        locked = await (await conn.execute("SELECT state,creator_id,operation,request FROM j01_jobs "
            "WHERE job_id=%s AND workspace_id=%s FOR UPDATE", (job_id, workspace_id))).fetchone()
        if (locked is None or locked[1:3] != candidate[1:3]
                or json.dumps(locked[3], sort_keys=True, separators=(",", ":"), allow_nan=False)
                != json.dumps(candidate[3], sort_keys=True, separators=(",", ":"), allow_nan=False)):
            raise SourceNotFound()
        return auth, locked

    async def cancel(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job:
        async def cancel(conn: Connection) -> Job:
            auth, row = await self._control_job(conn, _digest(credential_digest), workspace_id, job_id)
            if await self._expire(conn, workspace_id, job_id):
                return await self._status_response(conn, _digest(credential_digest), workspace_id, job_id, auth)
            state = row[0]
            if state in ("queued", "running"):
                if self.execution:
                    await conn.execute("UPDATE j01_jobs SET state=CASE WHEN state='running' THEN 'cancel_requested' ELSE 'cancelled' END, "
                        "failure_kind='cancelled',result=NULL,lease_digest=CASE WHEN state='running' THEN lease_digest END, "
                        "lease_until=CASE WHEN state='running' THEN lease_until END "
                        "WHERE job_id=%s AND workspace_id=%s AND state IN ('queued','running')", (job_id, workspace_id))
                else:
                    await conn.execute("UPDATE j01_jobs SET state='cancelled',result=NULL,lease_digest=NULL,lease_until=NULL "
                        "WHERE job_id=%s AND workspace_id=%s AND state IN ('queued','running')", (job_id, workspace_id))
                await self._record_action(conn, workspace_id, job_id, "job.cancel", actor=auth.user_id)
                state = "cancelled"
            return await self._status_response(conn, _digest(credential_digest), workspace_id, job_id, auth)

        return await run_transaction(self._pool, cancel)

    async def retry(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str,
                    request_key: SecretStr) -> JobReceipt:
        async with self._pool.connection() as conn:
            _, row = await self._control_job(conn, _digest(credential_digest), workspace_id, job_id)
            operation, request = cast(Operation, row[2]), cast(dict[str, object], row[3])
        # enqueue rechecks the original job and current authority in its own transaction.
        return await self.enqueue(credential_digest=credential_digest, workspace_id=workspace_id,
            operation=operation, request_key=request_key, request=request, retry_of_job_id=job_id)

    async def enqueue(self, *, credential_digest: SecretStr, workspace_id: str, operation: Operation,
                      request_key: SecretStr, request: dict[str, object],
                      retry_of_job_id: str | None = None) -> JobReceipt:
        return await job_admission.enqueue(self, credential_digest=credential_digest, workspace_id=workspace_id, operation=operation, request_key=request_key, request=request, retry_of_job_id=retry_of_job_id)

    @staticmethod
    def _receipt(workspace_id: str, row: tuple[object, ...] | None, request_hash: str) -> JobReceipt:
        if row is None:
            raise RuntimeError("Invalid job receipt")
        if row[1] != request_hash:
            raise SourceConflict()
        job_id, request_id = row[0], row[2]
        return JobReceipt.model_validate({"schema_version":"product-api/v1-draft", "workspace_id":workspace_id,
            "job_id":job_id, "status":"queued", "request_id":request_id,
            "status_url":f"/v1/workspaces/{workspace_id}/jobs/{job_id}"})

    async def claim(self, *, lease_digest: SecretStr, lease_seconds: int,
                    operations: tuple[Operation, ...] = ("preview", "confirm", "analyze")) -> JobClaim | None:
        lease = _digest(lease_digest)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("invalid lease")
        if not operations or any(value not in ("preview", "confirm", "analyze", "follow_up", "refresh") for value in operations):
            raise ValueError("invalid operations")
        tried: list[str] = []

        async def claim_one(conn: Connection) -> tuple[str | None, JobClaim | None]:
            return await self._claim_one(conn, lease, lease_seconds, operations, tried)

        # Bound the entire claim, including retries and released-connection backoffs.
        async with asyncio.timeout(5):
            for _ in range(32):
                job_id, claim = await run_transaction(self._pool, claim_one)
                if job_id is None or claim is not None:
                    return claim
                tried.append(job_id)
        return None

    async def _claim_candidate(self, conn: Connection, operations: tuple[Operation, ...],
                               tried: list[str]) -> tuple[object, ...] | None:
        from psycopg import sql

        if self.execution:
            candidate = await (await conn.execute("SELECT j.job_id,j.workspace_id,j.operation,j.request,j.credential_digest "
                "FROM j01_jobs j LEFT JOIN k09_queue_turns t ON t.workspace_id=j.workspace_id AND t.operation=j.operation "
                "WHERE j.operation=ANY(%s) AND (j.state='queued' AND j.available_at<=clock_timestamp() "
                "OR j.state='running' AND j.lease_until<clock_timestamp()) AND NOT(j.job_id=ANY(%s)) "
                "AND NOT(('workspace:'||j.workspace_id)=ANY(%s)) AND NOT(('class:'||j.workspace_id||':'||j.operation)=ANY(%s)) "
                "AND (SELECT count(*) FROM j01_jobs a WHERE a.workspace_id=j.workspace_id "
                "AND a.state IN ('running','cancel_requested') AND a.lease_until>clock_timestamp())<%s "
                "AND (SELECT count(*) FROM j01_jobs a WHERE a.workspace_id=j.workspace_id AND a.operation=j.operation "
                "AND a.state IN ('running','cancel_requested') AND a.lease_until>clock_timestamp())<%s "
                "ORDER BY COALESCE((SELECT max(last_claim_at) FROM k09_queue_turns w WHERE w.workspace_id=j.workspace_id),"
                "'-infinity'::timestamptz),COALESCE(t.last_claim_at,'-infinity'::timestamptz),j.created_at,j.job_id LIMIT 1",
                (list(operations), tried, tried, tried, self.queue_policy.running_per_workspace,
                 self.queue_policy.running_per_class))).fetchone()
        else:
            candidate = await (await conn.execute(sql.SQL(
                                                      'SELECT job_id,workspace_id,operation,request,credential_digest FROM j01_jobs WHERE operation=ANY(%s) AND '
                                                      '{} AND NOT(job_id=ANY(%s)) ORDER BY created_at,job_id LIMIT 1'
                                                  ).format(sql.SQL(_AVAILABLE)).as_string(), (list(operations), tried))).fetchone()
        return candidate

    async def _claim_capacity(self, conn: Connection, workspace: str,
                              operation: str) -> str | None:
        gate = await (await conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended("
            "current_schema()||':k09-queue:'||%s,0))", (workspace,))).fetchone()
        if gate != (True,):
            return 'workspace:' + workspace
        live = await (await conn.execute("SELECT count(*),count(*) FILTER(WHERE operation=%s) FROM j01_jobs "
            "WHERE workspace_id=%s AND state IN ('running','cancel_requested') AND lease_until>clock_timestamp()",
            (operation, workspace))).fetchone()
        if live is None or cast(int, live[0]) >= self.queue_policy.running_per_workspace:
            return 'workspace:' + workspace
        if cast(int, live[1]) >= self.queue_policy.running_per_class:
            return 'class:' + workspace + ':' + operation
        return None

    async def _may_claim(self, conn: Connection, digest: str, workspace: str,
                         operation: str, request: dict[str, object]) -> bool:
        allowed = True
        try:
            await self._authorize(conn, digest, workspace, operation, request)
        except (SourceNotFound, AuthorizationDenied):
            allowed = False
        return allowed

    async def _claim_one(self, conn: Connection, lease: str, lease_seconds: int,
                         operations: tuple[Operation, ...], tried: list[str]) -> tuple[str | None, JobClaim | None]:
        from psycopg import sql

        candidate = await self._claim_candidate(conn, operations, tried)
        if candidate is None:
            return None, None
        if not all(isinstance(candidate[i], str) for i in (0, 1, 2, 4)) or not isinstance(candidate[3], dict):
            raise RuntimeError("invalid job record")
        job_id, workspace, operation, _, digest = candidate
        request = cast(dict[str, object], candidate[3])
        allowed = await self._may_claim(conn, cast(str, digest), cast(str, workspace), cast(str, operation), request)
        if self.execution:
            busy = await self._claim_capacity(conn, cast(str, workspace), cast(str, operation))
            if busy is not None:
                return busy, None
        ready = " AND (state<>'queued' OR available_at<=clock_timestamp())" if self.execution else ''
        locked = await (await conn.execute(sql.SQL(
                                               'SELECT job_id FROM j01_jobs WHERE job_id=%s AND {}{} FOR UPDATE SKIP LOCKED'
                                           ).format(sql.SQL(_AVAILABLE), sql.SQL(ready)).as_string(), (job_id,))).fetchone()
        if locked is None:
            return cast(str, job_id), None
        if await self._expire(conn, cast(str, workspace), cast(str, job_id)):
            return cast(str, job_id), None
        if not allowed:
            failure = ",failure_kind='authorization' " if self.execution else ''
            await conn.execute(sql.SQL(
                                   "UPDATE j01_jobs SET state='failed',result=NULL,lease_digest=NULL,lease_until=NULL {}WHERE job_id=%s"
                               ).format(sql.SQL(failure)).as_string(), (job_id,))
            await self._record_action(conn, cast(str, workspace), cast(str, job_id), "job.fail")
            return cast(str, job_id), None
        claimed = await (await conn.execute(sql.SQL(
                                                "UPDATE j01_jobs SET state='running',lease_digest=%s, lease_until=LEAST(clock_timestamp()+%s*interval '1 "
                                                "second',{}) WHERE job_id=%s AND {}{} RETURNING job_id"
                                            ).format(sql.SQL(DEADLINE), sql.SQL(CLAIM_BUDGET), sql.SQL(ready)).as_string(),
            (lease, lease_seconds, job_id))).fetchone()
        if claimed is None:
            await self._expire(conn, cast(str, workspace), cast(str, job_id))
            return cast(str, job_id), None
        if self.execution:
            await conn.execute('INSERT INTO k09_queue_turns(workspace_id,operation,last_claim_at) VALUES(%s,%s,clock_timestamp()) '
                'ON CONFLICT(workspace_id,operation) DO UPDATE SET last_claim_at=EXCLUDED.last_claim_at', (workspace, operation))
        return cast(str, job_id), JobClaim(cast(str, job_id), cast(str, workspace), cast(str, operation),
                                         request, SecretStr(cast(str, digest)))

    async def complete(self, *, claim: JobClaim, lease_digest: SecretStr, result: dict[str, object]) -> bool:
        lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
        request_payload = json.dumps(claim.request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        request = json.loads(request_payload)
        payload = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(payload.encode()) > 1024 * 1024:
            raise ValueError("job result too large")
        if claim.operation == "analyze" and self._canonical is not None:
            snapshot = await self._access.snapshot(credential_digest=claim.credential_digest,
                workspace_id=claim.workspace_id, snapshot_id=cast(str, request.get("snapshot_id")))
            record = build_initial_upload_result(snapshot, cast(str, request.get("question")),
                                                 parse_product_view_json(payload))
            frozen = JobClaim(claim.job_id, claim.workspace_id, claim.operation, request, claim.credential_digest)
            return await self.complete_result(claim=frozen, lease_digest=lease_digest, record=record)
        async def complete(conn: Connection) -> bool:
            from psycopg import sql

            await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
            row = await (await conn.execute(sql.SQL(
                                                "UPDATE j01_jobs SET state='succeeded',result=%s::jsonb WHERE job_id=%s AND workspace_id=%s AND "
                                                "credential_digest=%s AND operation=%s AND state='running' AND lease_digest=%s AND "
                                                'lease_until>clock_timestamp() AND {} AND request=%s::jsonb RETURNING job_id'
                                            ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                (payload, claim.job_id, claim.workspace_id, digest, claim.operation, lease, request_payload))).fetchone()
            if row is not None:
                await self._record_action(conn, claim.workspace_id, claim.job_id, "job.complete")
            return row is not None

        return await run_transaction(self._pool, complete)

    async def fail(self, *, claim: JobClaim, lease_digest: SecretStr, retryable: bool = False,
                   failure_kind: Literal['transient', 'permanent', 'authorization', 'conflict'] | None = None) -> bool:
        """A worker may preserve terminal failure after revoke without publishing data."""
        lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
        if (type(retryable) is not bool or failure_kind not in (None, 'transient', 'permanent', 'authorization', 'conflict')
                or retryable and failure_kind not in (None, 'transient')):
            raise ValueError('A known failure disposition is required.')
        async def fail(conn: Connection) -> bool:
            if conn.autocommit:
                raise RuntimeError("transactional connection required")
            if await self._expire(conn, claim.workspace_id, claim.job_id):
                return True
            if self.execution:
                kind = failure_kind or ('transient' if retryable else 'permanent')
                row = await (await conn.execute("UPDATE j01_jobs SET state=CASE WHEN %s AND attempt_count<max_attempts "
                    "AND deadline_at>clock_timestamp()+interval '1 second' THEN 'queued' ELSE 'failed' END, "
                    "failure_kind=CASE WHEN %s AND attempt_count>=max_attempts THEN 'attempts_exhausted' ELSE %s END, "
                    "available_at=LEAST(clock_timestamp()+LEAST(30,power(2,attempt_count))*interval '1 second',deadline_at), "
                    "result=NULL,lease_digest=NULL,lease_until=NULL WHERE job_id=%s AND workspace_id=%s "
                    "AND credential_digest=%s AND operation=%s AND request=%s::jsonb AND state='running' "
                    "AND lease_digest=%s AND lease_until>clock_timestamp() RETURNING state,attempt_count",
                    (retryable, retryable, kind, claim.job_id, claim.workspace_id, digest, claim.operation,
                     json.dumps(claim.request, sort_keys=True, separators=(',', ':'), allow_nan=False), lease))).fetchone()
                if row is not None:
                    await self._record_action(conn, claim.workspace_id, claim.job_id,
                        'job.retrying' if row[0] == 'queued' else 'job.fail', effect=f'{claim.job_id}:attempt:{row[1]}')
                return row is not None
            row = await (await conn.execute("UPDATE j01_jobs SET state='failed',result=NULL "
                "WHERE job_id=%s AND workspace_id=%s AND credential_digest=%s AND state='running' "
                "AND lease_digest=%s AND lease_until>clock_timestamp() RETURNING job_id",
                (claim.job_id, claim.workspace_id, digest, lease))).fetchone()
            if row is not None:
                await self._record_action(conn, claim.workspace_id, claim.job_id, "job.fail")
            return row is not None

        return await run_transaction(self._pool, fail)

    async def _complete_canonical_snapshot(self, *, claim: JobClaim, lease_digest: SecretStr,
                                            snapshot: AnyUploadSnapshot) -> bool:
        return await job_publication._complete_canonical_snapshot(self, claim=claim, lease_digest=lease_digest, snapshot=snapshot)

    async def complete_snapshot(self, *, claim: JobClaim, lease_digest: SecretStr, snapshot: AnyUploadSnapshot) -> bool:
        """Publish snapshot and durable terminal truth in one current-authorized transaction."""
        if self._catalog is not None:
            return await self._complete_canonical_snapshot(claim=claim, lease_digest=lease_digest, snapshot=snapshot)
        lease, digest = _digest(lease_digest), _digest(claim.credential_digest)
        request_payload = json.dumps(claim.request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        request = json.loads(request_payload)
        snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
        if (claim.operation != "confirm" or snapshot.workspace_id != claim.workspace_id
                or request.get("source_id") != snapshot.source_id):
            raise ValueError("invalid snapshot scope")
        async def publish(conn: Connection) -> bool:
            from psycopg import sql

            await self._authorize(conn, digest, claim.workspace_id, claim.operation, request)
            row = await (await conn.execute(sql.SQL(
                                                'SELECT job_id FROM j01_jobs WHERE job_id=%s AND workspace_id=%s AND credential_digest=%s AND '
                                                "operation='confirm' AND state='running' AND lease_digest=%s AND lease_until>clock_timestamp() AND {} AND "
                                                "request->>'source_id'=%s AND request=%s::jsonb FOR UPDATE"
                                            ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                (claim.job_id, claim.workspace_id, digest, lease, snapshot.source_id, request_payload))).fetchone()
            if row is None:
                return False
            source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE source_id=%s AND workspace_id=%s",
                (snapshot.source_id, snapshot.workspace_id))).fetchone()
            if source is None or source[0] != snapshot.source_sha256:
                raise ValueError("source mismatch")
            await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload) "
                "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (snapshot_id) DO NOTHING",
                (snapshot.snapshot_id, snapshot.workspace_id, snapshot.source_id, snapshot.model_dump_json()))
            saved = await (await conn.execute("SELECT payload::text FROM j01_upload_snapshots WHERE snapshot_id=%s "
                "AND workspace_id=%s AND source_id=%s", (snapshot.snapshot_id, snapshot.workspace_id,
                                                       snapshot.source_id))).fetchone()
            if saved is None or not isinstance(saved[0], str) or (
                    parse_upload_snapshot(saved[0]).state_payload() != snapshot.state_payload()):
                raise ValueError("stored snapshot mismatch")
            published = await (await conn.execute(sql.SQL(
                                                      "UPDATE j01_jobs SET state='succeeded',result=%s::jsonb WHERE job_id=%s AND lease_until>clock_timestamp() "
                                                      'AND {} RETURNING job_id'
                                                  ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
                (json.dumps({"snapshot_id": snapshot.snapshot_id}), claim.job_id))).fetchone()
            if published is None:
                raise TimeoutError("job lease expired")
            await self._record_action(conn, claim.workspace_id, claim.job_id, "job.publish", effect=snapshot.snapshot_id)
            return True

        return await run_transaction(self._pool, publish)

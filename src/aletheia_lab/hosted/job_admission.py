"""job admission for the durable PostgreSQL job repository."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.hosted_contract.project_operations import FollowUpRequest
from aletheia_lab.project.identity import PROJECT_ID_PATTERN

from .action_audit import current_request_id
from .authorization import Authorization
from .job_execution import QueueFull, execution_profile, queue_lock
from .postgres_sources import SourceConflict, SourceNotFound
from .transactions import run_transaction
from .workspace_management import WorkspaceConflict, _revision

if TYPE_CHECKING:
    from .postgres_jobs import Operation, PostgresJobs


async def enqueue(self: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str, operation: Operation,
                  request_key: SecretStr, request: dict[str, object],
                  retry_of_job_id: str | None = None) -> JobReceipt:

    digest = _digest(credential_digest)
    implicit_analysis = operation == "analyze" and "analysis_model" not in request and retry_of_job_id is None
    if operation not in ("preview", "confirm", "analyze", "follow_up", "refresh") or not isinstance(request_key, SecretStr):
        raise ValueError("invalid job")
    key = request_key.get_secret_value()
    if not 16 <= len(key) <= 128:
        raise ValueError("invalid request key")
    if retry_of_job_id is not None and (not isinstance(retry_of_job_id, str) or not 1 <= len(retry_of_job_id) <= 128):
        raise ValueError("invalid retry reference")
    request = _validated_request(request, operation, self.analysis_model, retry_of_job_id)
    payload = json.dumps(request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode()) > 65536:
        raise ValueError("job request too large")
    request = json.loads(payload)
    fingerprint = payload if retry_of_job_id is None else json.dumps(
        {"retry_of_job_id": retry_of_job_id, "request": request}, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"))
    key_hash, request_hash = content_hasher(key.encode()).hexdigest(), content_hasher(fingerprint.encode()).hexdigest()
    receipt_columns = 'job_id,request_hash,request_id' + (',request' if implicit_analysis else '')

    admission = _Admission(self, digest, workspace_id, operation, request, retry_of_job_id, payload, key_hash, request_hash, receipt_columns, implicit_analysis)
    return await run_transaction(self._pool, admission.admit)




@dataclass(frozen=True)
class _Admission:
    jobs: PostgresJobs
    digest: str
    workspace_id: str
    operation: Operation
    request: dict[str, object]
    retry_of_job_id: str | None
    payload: str
    key_hash: str
    request_hash: str
    receipt_columns: str
    implicit_analysis: bool

    def replay(self, row: tuple[object, ...]) -> JobReceipt:
        from .postgres_jobs import _ANALYSIS_MODELS

        if self.implicit_analysis:
            stored = row[3]
            if (not isinstance(stored, dict)
                    or stored.get('analysis_model', _ANALYSIS_MODELS[0]) not in _ANALYSIS_MODELS
                    or {key: value for key, value in stored.items() if key != 'analysis_model'} !=
                       {key: value for key, value in self.request.items() if key != 'analysis_model'}):
                raise SourceConflict()
            original = json.dumps(stored, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))
            return self.jobs._receipt(self.workspace_id, row, content_hasher(original.encode()).hexdigest())
        return self.jobs._receipt(self.workspace_id, row, self.request_hash)

    async def _queue_budget(self, conn: Connection) -> tuple[object, ...] | None:
        from .postgres_jobs import JobStateConflict

        retry_budget = None
        counts = await (await conn.execute("SELECT count(*),count(*) FILTER(WHERE operation=%s) FROM j01_jobs "
            "WHERE workspace_id=%s AND state IN ('queued','running','cancel_requested')",
            (self.operation, self.workspace_id))).fetchone()
        if counts is None or cast(int, counts[0]) >= self.jobs.queue_policy.pending_per_workspace or cast(int, counts[1]) >= self.jobs.queue_policy.pending_per_class:
            raise QueueFull()
        if self.retry_of_job_id is not None:
            retry_budget = await (await conn.execute("SELECT deadline_at,attempt_count,max_attempts,failure_kind,"
                "deadline_at>clock_timestamp() FROM j01_jobs WHERE workspace_id=%s AND job_id=%s",
                (self.workspace_id, self.retry_of_job_id))).fetchone()
            child = await (await conn.execute('SELECT job_id FROM j01_jobs WHERE workspace_id=%s '
                'AND retry_of_job_id=%s LIMIT 1', (self.workspace_id, self.retry_of_job_id))).fetchone()
            if (child is not None or retry_budget is None or retry_budget[4] is not True or cast(int, retry_budget[1]) >= cast(int, retry_budget[2])
                    or retry_budget[3] not in ('transient', 'cancelled')):
                raise JobStateConflict()
        return retry_budget

    async def _insert_receipt(self, conn: Connection, auth: Authorization,
                              retry_budget: tuple[object, ...] | None) -> JobReceipt:
        from psycopg import sql

        from .postgres_jobs import JobStateConflict

        columns = "job_id,workspace_id,creator_id,credential_digest,operation,key_hash,request_hash,request,request_id,state"
        values = "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'queued'"
        params: tuple[object, ...] = ("job_" + uuid4().hex, self.workspace_id, auth.user_id, self.digest,
            self.operation, self.key_hash, self.request_hash, self.payload, current_request_id() or "req_" + uuid4().hex)
        if self.retry_of_job_id is not None:
            columns, values, params = columns + ",retry_of_job_id", values + ",%s", params + (self.retry_of_job_id,)
        insert_values = ' VALUES (' + values + ') '
        if retry_budget is not None:
            columns, values = columns + ',deadline_at,attempt_count,created_at', values + ',%s,%s,stamp'
            params += (retry_budget[0], retry_budget[1], retry_budget[0])
            insert_values = ' SELECT ' + values + ' FROM (SELECT clock_timestamp() AS stamp) t WHERE %s>=stamp '
        inserted = await (await conn.execute("INSERT INTO j01_jobs(" + columns + ")" + insert_values +
            "ON CONFLICT (workspace_id,creator_id,operation,key_hash) DO NOTHING RETURNING job_id", params)).fetchone()
        row = await (await conn.execute(sql.SQL(
                                            'SELECT {} FROM j01_jobs WHERE workspace_id=%s AND creator_id=%s AND operation=%s AND key_hash=%s'
                                        ).format(sql.SQL(self.receipt_columns)).as_string(),
            (self.workspace_id, auth.user_id, self.operation, self.key_hash))).fetchone()
        if row is None:
            if retry_budget is not None:
                raise JobStateConflict()
            raise RuntimeError("Invalid job receipt")
        receipt = self.replay(row)
        if inserted is not None:
            await self.jobs._record_action(conn, self.workspace_id, receipt.job_id, "job.admit")
        return receipt

    async def admit(self, conn: Connection) -> JobReceipt:
        from psycopg import sql

        from .postgres_jobs import JobStateConflict

        if self.retry_of_job_id is None:
            auth = await self.jobs._authorize(conn, self.digest, self.workspace_id, self.operation, self.request)
        else:
            auth, original = await self.jobs._control_job(conn, self.digest, self.workspace_id, self.retry_of_job_id)
            if (original[2] != self.operation or json.dumps(original[3], ensure_ascii=True, allow_nan=False,
                    sort_keys=True, separators=(",", ":")) != self.payload):
                raise SourceNotFound()
        row = await (await conn.execute(sql.SQL(
                                            'SELECT {} FROM j01_jobs WHERE workspace_id=%s AND creator_id=%s AND operation=%s AND key_hash=%s'
                                        ).format(sql.SQL(self.receipt_columns)).as_string(),
            (self.workspace_id, auth.user_id, self.operation, self.key_hash))).fetchone()
        if row is not None:
            return self.replay(row)
        retry_budget = None
        if self.jobs.execution:
            if not await execution_profile(conn):
                raise TimeoutError('Current job execution schema is required.')
            await queue_lock(conn, self.workspace_id)
            repeated = await (await conn.execute(sql.SQL(
                                                     'SELECT {} FROM j01_jobs WHERE workspace_id=%s AND creator_id=%s AND operation=%s AND key_hash=%s'
                                                 ).format(sql.SQL(self.receipt_columns)).as_string(),
                (self.workspace_id, auth.user_id, self.operation, self.key_hash))).fetchone()
            if repeated is not None:
                return self.replay(repeated)
            retry_budget = await self._queue_budget(conn)
        if self.operation in ("follow_up", "refresh"):
            current = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s",
                (self.workspace_id, self.request["project_id"]))).fetchone()
            if current != (self.request["expected_revision"],):
                raise WorkspaceConflict()
        if self.retry_of_job_id is not None and original[0] not in ("failed", "cancelled"):
            raise JobStateConflict()
        return await self._insert_receipt(conn, auth, retry_budget)

def _validated_request(request: dict[str, object], operation: Operation, analysis_model: str,
                       retry_of_job_id: str | None) -> dict[str, object]:
    from .postgres_jobs import _ANALYSIS_MODELS

    if operation == "refresh":
        from .project_job_operations import validate_refresh

        validate_refresh(request)
    elif operation == "follow_up":
        if (set(request) != {"project_id", "parent_result_id", "expected_revision", "selection_kind", "selection_id", "question"}
                or not isinstance(request.get("project_id"), str)
                or re.fullmatch(PROJECT_ID_PATTERN, cast(str, request["project_id"])) is None
                or not isinstance(request.get("parent_result_id"), str)
                or re.fullmatch(r"p6-result-[0-9a-f]{64}", cast(str, request["parent_result_id"])) is None):
            raise ValueError("invalid canonical follow-up request")
        _revision(request["expected_revision"])
        FollowUpRequest.model_validate({"schema_version": "product-api/v1-draft", "selection_kind": request["selection_kind"],
            "selection_id": request["selection_id"], "question": request["question"]})
    elif operation == "analyze":
        from aletheia_lab.product.questions import checked_product_question

        if (set(request) not in ({"snapshot_id", "question"}, {"snapshot_id", "question", "analysis_model"})
                or request.get("analysis_model", _ANALYSIS_MODELS[0]) not in _ANALYSIS_MODELS
                or not isinstance(request.get("snapshot_id"), str)
                or not isinstance(request.get("question"), str)):
            raise ValueError("invalid analysis request")
        checked_product_question(cast(str, request["question"]))
        if "analysis_model" not in request and analysis_model == _ANALYSIS_MODELS[1] and retry_of_job_id is None:
            request = dict(request, analysis_model=analysis_model)
    elif (set(request) != ({"source_id"} if operation == "preview" else {"source_id", "mapping"})
          or not isinstance(request.get("source_id"), str)
          or (operation == "confirm" and not isinstance(request.get("mapping"), dict))):
        raise ValueError("invalid source request")
    return request


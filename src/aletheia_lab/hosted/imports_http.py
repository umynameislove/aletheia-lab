"""Import resources backed by the existing durable queue and scoped preview leases."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.imports import ConfirmImport, PreviewRequest
from aletheia_lab.hosted_contract.job_attempts import JobAttemptPage
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt

from .application import AccessDenied, Send, _cookie, _Response
from .import_errors import PreviewRequired
from .import_failures import ImportFailures
from .job_http import JobApplication, Jobs
from .postgres_jobs import Operation, PostgresJobs
from .postgres_sources import SourceNotFound
from .preview_leases import PreviewChanged, PreviewExpired, PreviewLeases
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from .settings import RuntimeSettings

_IMPORT = re.compile(r'/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/imports/(preview|prev_[0-9a-f]{32})(/confirm)?')


def import_rejection(error: ValueError) -> RequestRejected:
    if isinstance(error, PreviewExpired):
        return RequestRejected(410, 'preview_expired', 'The preview has expired. Create a new preview.')
    if isinstance(error, PreviewChanged):
        return RequestRejected(409, 'source_changed', 'The source changed. Create a new preview.')
    return RequestRejected(422, 'preview_required', 'A current preview is required.')


class LeasedJobs:
    """Apply preview admission to the existing queue; accepted receipts outlive leases."""

    def __init__(self, jobs: PostgresJobs, leases: PreviewLeases, failures: ImportFailures | None) -> None:
        self._jobs, self._leases, self._failures = jobs, leases, failures

    async def _lookup(self, conn: Connection, *, actor_id: str, workspace_id: str, operation: Operation,
                      request_key: SecretStr, request: dict[str, object],
                      retry_of_job_id: str | None = None) -> JobReceipt | None:
        if not isinstance(request_key, SecretStr) or not 16 <= len(request_key.get_secret_value()) <= 128:
            raise ValueError('Invalid request key.')
        encoded = json.dumps(request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))
        if len(encoded.encode()) > 65536:
            raise ValueError('Job request is too large.')
        fingerprint = encoded if retry_of_job_id is None else json.dumps(
            {'retry_of_job_id': retry_of_job_id, 'request': request}, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(',', ':'))
        row = await (await conn.execute('SELECT job_id,request_hash,request_id FROM j01_jobs '
            'WHERE workspace_id=%s AND creator_id=%s AND operation=%s AND key_hash=%s',
            (workspace_id, actor_id, operation,
             content_hasher(request_key.get_secret_value().encode()).hexdigest()))).fetchone()
        return None if row is None else self._jobs._receipt(
            workspace_id, row, content_hasher(fingerprint.encode()).hexdigest())

    async def _validate_confirm(self, *, credential_digest: SecretStr, workspace_id: str,
                                request: dict[str, object]) -> None:
        mapping, source_id = request.get('mapping'), request.get('source_id')
        preview_id = mapping.get('_preview_id') if isinstance(mapping, dict) else None
        if not isinstance(preview_id, str) or not isinstance(source_id, str):
            raise import_rejection(PreviewRequired())
        try:
            await self._leases.validate(credential_digest=credential_digest, workspace_id=workspace_id,
                                       source_id=source_id, preview_id=preview_id)
        except (PreviewExpired, PreviewChanged) as error:
            raise import_rejection(error) from None

    async def enqueue(self, *, credential_digest: SecretStr, workspace_id: str, operation: Operation,
                      request_key: SecretStr, request: dict[str, object]) -> JobReceipt:
        if operation == 'confirm':
            async with self._jobs._pool.connection() as conn:
                auth = await self._jobs._authorize(conn, _digest(credential_digest), workspace_id, operation, request)
                receipt = await self._lookup(conn, actor_id=auth.user_id, workspace_id=workspace_id,
                    operation=operation, request_key=request_key, request=request)
                if receipt is not None:
                    return receipt
            await self._validate_confirm(credential_digest=credential_digest, workspace_id=workspace_id, request=request)
        return await self._jobs.enqueue(credential_digest=credential_digest, workspace_id=workspace_id,
            operation=operation, request_key=request_key, request=request)

    async def retry(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str,
                    request_key: SecretStr) -> JobReceipt:
        async with self._jobs._pool.connection() as conn:
            auth, row = await self._jobs._control_job(conn, _digest(credential_digest), workspace_id, job_id)
            operation, request = cast(Operation, row[2]), cast(dict[str, object], row[3])
            if operation == 'confirm':
                receipt = await self._lookup(conn, actor_id=auth.user_id, workspace_id=workspace_id,
                    operation=operation, request_key=request_key, request=request, retry_of_job_id=job_id)
                if receipt is not None:
                    return receipt
        if operation == 'confirm':
            await self._validate_confirm(credential_digest=credential_digest, workspace_id=workspace_id, request=request)
        # The queue rechecks current authority/state and owns the only insert/transaction.
        return await self._jobs.retry(credential_digest=credential_digest, workspace_id=workspace_id,
                                       job_id=job_id, request_key=request_key)

    async def status(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job:
        return await self._jobs.status(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)

    async def attempts(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> JobAttemptPage:
        return await self._jobs.attempts(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)

    async def cancel(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job:
        return await self._jobs.cancel(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)

    async def output(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> dict[str, object]:
        job = await self._jobs.status(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)
        if job.status == 'failed' and self._failures is not None:
            failure = await self._failures.read(workspace_id=workspace_id, job_id=job_id)
            if failure is not None:
                # Diagnostics I/O must not retain authority captured before a concurrent revoke.
                current = await self._jobs.status(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)
                if current.status != 'failed':
                    raise SourceNotFound()
                return {'schema_version': 'product-api/v1-draft', 'workspace_id': workspace_id,
                        'job_id': job_id, 'operation': 'confirm', 'result': failure}
        return await self._jobs.output(credential_digest=credential_digest, workspace_id=workspace_id, job_id=job_id)


class ImportApplication(JobApplication):
    def __init__(self, fallback: Callable[[Mapping[str, object], Receive, Send], Awaitable[None]],
                 jobs: Jobs, sessions: SessionRepository, tokens: CsrfTokens, runtime: RuntimeSettings, *,
                 leases: PreviewLeases, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        super().__init__(fallback, jobs, sessions, tokens, runtime, body_limits=body_limits, clock_check=clock_check)
        self._leases = leases

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get('type') != 'http' or _IMPORT.fullmatch(str(scope.get('path', ''))) is None:
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    @staticmethod
    def _allow(path: str) -> bytes:
        match = _IMPORT.fullmatch(path)
        return b'POST' if match is None or match[2] == 'preview' or match[3] else b'GET, HEAD'

    async def _credential(self, scope: Mapping[str, object]) -> SecretStr:
        if await self._clock() is not True:
            raise TimeoutError()
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied('session_expired')
        digest = credential_digest(token)
        return digest

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        match = _IMPORT.fullmatch(str(scope.get('path', '')))
        if match is None:
            raise RequestRejected(404, 'not_found', 'The requested resource is unavailable.')
        workspace, preview_id, suffix = match.groups()
        if (scope.get('method') not in self._allow(str(scope.get('path', ''))).decode().split(', ')
                or (preview_id == 'preview' and suffix is not None)):
            raise RequestRejected(405, 'method_not_allowed', 'This method is not supported.')
        if scope.get('query_string', b''):
            raise RequestRejected(400, 'invalid_request', 'Query parameters are not supported.')
        digest = await self._credential(scope)
        try:
            if preview_id != 'preview' and suffix is None:
                value = await self._leases.read(credential_digest=digest, workspace_id=workspace, preview_id=preview_id)
                response = _Response(200, value.model_dump_json().encode())
            else:
                headers = cast(Sequence[tuple[bytes, bytes]], scope.get('headers', ()))
                key = self._request_key(headers)
                raw = await read_json_body(receive, headers, self._limits)
                request: dict[str, object]
                operation: Operation
                if preview_id == 'preview':
                    preview_request = decode_json_model(raw, PreviewRequest, maximum_bytes=self._limits.maximum_bytes,
                        maximum_depth=self._runtime.api.maximum_json_depth)
                    request, operation = {'source_id': preview_request.source_id}, 'preview'
                else:
                    confirmation = decode_json_model(raw, ConfirmImport, maximum_bytes=self._limits.maximum_bytes,
                        maximum_depth=self._runtime.api.maximum_json_depth)
                    preview = await self._leases.for_submission(credential_digest=digest, workspace_id=workspace,
                                                                preview_id=preview_id)
                    request = {'source_id': preview.source_id, 'mapping': {**confirmation.mapping, '_preview_id': preview_id}}
                    operation = 'confirm'
                receipt = await self._jobs.enqueue(credential_digest=digest, workspace_id=workspace,
                    operation=operation, request_key=key, request=request)
                if receipt.workspace_id != workspace:
                    raise ValueError('Invalid import receipt scope.')
                response = _Response(202, receipt.model_dump_json().encode())
        except (PreviewExpired, PreviewChanged, PreviewRequired) as error:
            raise import_rejection(error) from None
        if await self._clock() is not True:
            raise TimeoutError()
        return response

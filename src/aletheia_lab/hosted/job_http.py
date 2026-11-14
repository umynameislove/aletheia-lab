"""Opt-in preview/confirm job submission and status HTTP boundary."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Literal, Protocol, Self, cast
from urllib.parse import parse_qs
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.job_attempts import JobAttemptPage
from aletheia_lab.hosted_contract.job_page import Job, JobOutput, JobReceipt
from aletheia_lab.hosted_contract.job_updates import JobUpdate
from aletheia_lab.hosted_contract.project_operations import FollowUpJobOutput
from aletheia_lab.hosted_contract.project_refresh import RefreshResult
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.view import parse_product_view_json

from .action_audit import report_outcome, request_context
from .application import (
    _ACCESS_ERRORS,
    AccessDenied,
    Send,
    _cookie,
    _problem,
    _Response,
    _validated,
)
from .authorization import AuthorizationDenied
from .cursors import CursorExpired, CursorInvalid
from .edge import EdgeGuard
from .observability import process_telemetry
from .poll_admission import PollAdmission
from .postgres_jobs import JobStateConflict
from .postgres_sources import SourceConflict, SourceNotFound
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from .resource_admission import ResourceBusy
from .settings import RuntimeSettings

_ROUTE = re.compile(r"/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/jobs(?:/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})(/(?:output|cancel|retry|attempts|updates))?)?")


class EmptyAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["preview", "confirm", "analyze"]
    source_id: Identifier | None = None
    mapping: dict[str, object] | None = None
    snapshot_id: Identifier | None = None
    question: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.operation == "analyze":
            if self.source_id is not None or self.mapping is not None or self.snapshot_id is None or self.question is None:
                raise ValueError("invalid job shape")
            checked_product_question(self.question)
        elif (self.source_id is None or self.snapshot_id is not None or self.question is not None
              or (self.operation == "preview") != (self.mapping is None)):
            raise ValueError("invalid job shape")
        return self


class Jobs(Protocol):
    async def enqueue(self, *, credential_digest: SecretStr, workspace_id: str, operation: Literal["preview", "confirm", "analyze"],
                      request_key: SecretStr, request: dict[str, object]) -> JobReceipt: ...
    async def status(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job: ...
    async def output(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> dict[str, object]: ...
    async def cancel(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> Job: ...
    async def retry(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str,
                    request_key: SecretStr) -> JobReceipt: ...
    async def attempts(self, *, credential_digest: SecretStr, workspace_id: str, job_id: str) -> JobAttemptPage: ...


class JobUpdates(Protocol):
    async def read(self, *, credential_digest: SecretStr, workspace_id: str,
                   job_id: str, cursor: str | None) -> JobUpdate: ...


class JobApplication:
    def __init__(self, fallback: Callable[[Mapping[str, object], Receive, Send], Awaitable[None]],
                 jobs: Jobs, sessions: SessionRepository, tokens: CsrfTokens, runtime: RuntimeSettings, *,
                 body_limits: BodyLimits, clock_check: Callable[[], Awaitable[bool]],
                 updates: JobUpdates | None = None) -> None:
        self._fallback, self._jobs, self._sessions = fallback, jobs, sessions
        self._runtime, self._limits, self._clock = runtime, body_limits, clock_check
        self._updates, self._polls = updates, PollAdmission()
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _ROUTE.fullmatch(str(scope.get("path", ""))) is None:
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _route(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        request_id = "req_" + uuid4().hex
        with request_context(request_id):
            await self._route_with_context(scope, receive, send, request_id)

    async def _route_with_context(self, scope: Mapping[str, object], receive: Receive, send: Send,
                                  request_id: str) -> None:
        response = await self._guarded_response(scope, receive, request_id)
        if 400 <= response.status < 500:
            report_outcome("rejected")
        headers = [(b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"x-request-id", request_id.encode())]
        if response.allow is not None:
            headers.append((b"allow", response.allow))
        if response.status in (429, 503):
            headers.append((b"retry-after", b"1"))
        if response.status == 200 and str(scope.get('path', '')).endswith('/updates'):
            delay = json.loads(response.body)['poll_after_ms']
            if delay is not None:
                headers.append((b'retry-after', str((delay + 999) // 1000).encode()))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _checked_dispatch(self, scope: Mapping[str, object], receive: Receive,
                                request_id: str) -> _Response:
        response = await self._dispatch(scope, receive)
        if len(response.body) > self._runtime.api.maximum_response_bytes:
            return _problem(500, "response_invalid", "The service could not produce a valid response.", request_id)
        return response

    async def _guarded_response(self, scope: Mapping[str, object], receive: Receive,
                                request_id: str) -> _Response:
        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                response = await self._checked_dispatch(scope, receive, request_id)
        except RequestRejected as error:
            allow = self._allow(str(scope.get("path", "")))
            return _problem(error.status, error.code, error.safe_message, request_id,
                                allow=allow if error.status == 405 else None)
        except (SourceNotFound, AuthorizationDenied):
            return _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except SourceConflict:
            return _problem(409, "idempotency_conflict", "The request key was already used.", request_id)
        except JobStateConflict:
            return _problem(409, "job_state_conflict", "The job cannot be retried in its current state.", request_id)
        except CursorInvalid:
            return _problem(400, 'cursor_invalid', 'The continuation is invalid for this request.', request_id)
        except CursorExpired:
            return _problem(410, 'cursor_expired', 'The continuation has expired. Read a fresh snapshot.', request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            return _problem(status, error.code, detail, request_id)
        except TimeoutError:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            return _problem(500, "service_failure", "The service could not complete the request.", request_id)
        return response

    async def _read_attempts(self, digest: SecretStr, workspace: str, job_id: str) -> _Response:
        history = _validated(await self._jobs.attempts(credential_digest=digest,
            workspace_id=workspace, job_id=job_id), JobAttemptPage)
        if history.workspace_id != workspace or history.job_id != job_id:
            raise ValueError('Invalid attempt history scope.')
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(200, history.model_dump_json(
            exclude={'conflict'} if history.conflict is None else None).encode())

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        match = _ROUTE.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise SourceNotFound()
        workspace, job_id, suffix = match.groups()
        method = scope.get("method")
        if method not in self._allow(str(scope.get("path", ""))).decode("ascii").split(", "):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if suffix != '/updates' and scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        digest = await self._credential(scope)
        if job_id is not None and suffix == '/updates':
            return await self._read_updates(scope, digest, workspace, job_id)
        if job_id is not None and suffix == '/attempts':
            return await self._read_attempts(digest, workspace, job_id)
        if job_id is not None and suffix not in ("/cancel", "/retry"):
            return await self._read_job(digest, workspace, job_id, suffix == "/output")
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        key = self._request_key(headers) if suffix != "/cancel" else None
        body = await read_json_body(receive, headers, self._limits)
        if job_id is not None:
            return await self._mutate_job(digest, workspace, job_id, suffix, key, body)
        submission = decode_json_model(body, Submission, maximum_bytes=self._limits.maximum_bytes,
                                       maximum_depth=self._runtime.api.maximum_json_depth)
        request = submission.model_dump(exclude={"operation"}, exclude_none=True)
        receipt = await self._jobs.enqueue(credential_digest=digest, workspace_id=workspace,
            operation=submission.operation, request_key=cast(SecretStr, key), request=request)
        receipt = _validated(receipt, JobReceipt)
        if receipt.workspace_id != workspace:
            raise ValueError("invalid receipt scope")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(202, receipt.model_dump_json().encode())

    async def _read_updates(self, scope: Mapping[str, object], digest: SecretStr,
                            workspace: str, job_id: str) -> _Response:
        if self._updates is None:
            raise TimeoutError('Status snapshot polling is unavailable.')
        query = scope.get('query_string', b'')
        if not isinstance(query, bytes) or len(query) > 8192:
            raise RequestRejected(400, 'invalid_request', 'A bounded continuation is required.')
        try:
            values = parse_qs(query.decode('ascii'), keep_blank_values=True, strict_parsing=True, max_num_fields=1)
        except (UnicodeError, ValueError):
            raise RequestRejected(400, 'invalid_request', 'A bounded continuation is required.') from None
        if values and (set(values) != {'cursor'} or len(values['cursor']) != 1 or not 1 <= len(values['cursor'][0]) <= 4096):
            raise RequestRejected(400, 'invalid_request', 'A single continuation is required.')
        telemetry = process_telemetry()
        telemetry.increment('job_poll_requests')
        try:
            self._polls.acquire(workspace)
        except ResourceBusy:
            telemetry.increment('job_poll_rejected')
            raise RequestRejected(429, 'rate_limited', 'Job polling is temporarily busy.') from None
        telemetry.set_gauge('job_poll_inflight', self._polls.slots.snapshot().active)
        try:
            update = _validated(await self._updates.read(credential_digest=digest, workspace_id=workspace,
                job_id=job_id, cursor=None if not values else values['cursor'][0]), JobUpdate)
            if update.workspace_id != workspace or update.job_id != job_id:
                raise ValueError('Job update scope mismatch.')
            return _Response(200, update.model_dump_json().encode())
        finally:
            self._polls.release(workspace)
            telemetry.set_gauge('job_poll_inflight', self._polls.slots.snapshot().active)

    async def _credential(self, scope: Mapping[str, object]) -> SecretStr:
        if await self._clock() is not True:
            raise TimeoutError()
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied("session_expired")
        return credential_digest(token)

    async def _mutate_job(self, digest: SecretStr, workspace: str, job_id: str, suffix: str | None,
                          key: SecretStr | None, body: bytes) -> _Response:
        decode_json_model(body, EmptyAction, maximum_bytes=self._limits.maximum_bytes,
                          maximum_depth=self._runtime.api.maximum_json_depth)
        if suffix == "/cancel":
            job = _validated(await self._jobs.cancel(credential_digest=digest, workspace_id=workspace, job_id=job_id), Job)
            if job.workspace_id != workspace or job.job_id != job_id:
                raise ValueError("invalid cancelled job scope")
            if await self._clock() is not True:
                raise TimeoutError()
            return _Response(200, job.model_dump_json().encode())
        if key is None:
            raise ValueError("retry key required")
        receipt = _validated(await self._jobs.retry(credential_digest=digest, workspace_id=workspace,
                                                    job_id=job_id, request_key=key), JobReceipt)
        if receipt.workspace_id != workspace or receipt.job_id == job_id:
            raise ValueError("invalid retry receipt scope")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(202, receipt.model_dump_json().encode())

    @staticmethod
    def _allow(path: str) -> bytes:
        route = _ROUTE.fullmatch(path)
        return b"POST" if route is None or route[2] is None or route[3] in ("/cancel", "/retry") else b"GET, HEAD"

    @staticmethod
    def _request_key(headers: Sequence[tuple[bytes, bytes]]) -> SecretStr:
        keys = [value for name, value in headers if name.lower() == b"idempotency-key"]
        if len(keys) != 1 or not re.fullmatch(rb"[A-Za-z0-9_-]{16,128}", keys[0]):
            raise RequestRejected(400, "invalid_request", "A valid request key is required.")
        return SecretStr(keys[0].decode("ascii"))

    async def _read_job(self, digest: SecretStr, workspace: str, job_id: str, output: bool) -> _Response:
        if output:
            return await self._read_output(digest, workspace, job_id)
        job = _validated(await self._jobs.status(
            credential_digest=digest, workspace_id=workspace, job_id=job_id), Job)
        if job.workspace_id != workspace or job.job_id != job_id:
            raise ValueError("invalid job scope")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(200, job.model_dump_json().encode())

    async def _read_output(self, digest: SecretStr, workspace: str, job_id: str) -> _Response:
        value = await self._jobs.output(credential_digest=digest, workspace_id=workspace, job_id=job_id)
        envelope = JobOutput[dict[str, object]].model_validate(value, strict=True)
        if envelope.workspace_id != workspace or envelope.job_id != job_id:
            raise ValueError("invalid job output scope")
        value = envelope.model_dump(mode="json")
        if value["operation"] == "follow_up":
            value = FollowUpJobOutput.model_validate(value, strict=True).model_dump(mode="json")
        elif value["operation"] == "refresh":
            value["result"] = RefreshResult.model_validate(value["result"], strict=True).model_dump(mode="json")
        elif value["operation"] == "analyze":
            view = parse_product_view_json(json.dumps(value["result"], allow_nan=False))
            if view.demo_only or view.visibility != "diagnosis":
                raise ValueError("invalid analysis output")
            value["result"] = view.model_dump(mode="json")
        body = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(body) > self._runtime.api.maximum_response_bytes:
            raise ValueError("job output too large")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(200, body)

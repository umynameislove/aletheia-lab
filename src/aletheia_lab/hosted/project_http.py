"""Canonical result follow-up submission; authority and durable publication stay in the queue."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Literal, Protocol, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.hosted_contract.project_operations import FollowUpRequest
from aletheia_lab.product.boundary import ProductError

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
from .composition import Application
from .edge import EdgeGuard
from .job_http import JobApplication
from .postgres_sources import SourceConflict, SourceNotFound
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from .settings import RuntimeSettings
from .workspace_http import WorkspaceApplication
from .workspace_management import WorkspaceConflict

_FOLLOW_UP = re.compile(
    r"/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/results/(p6-result-[0-9a-f]{64})/follow-ups"
)


class FollowUpJobs(Protocol):
    async def enqueue_follow_up(self, *, credential_digest: SecretStr, workspace_id: str,
                                parent_result_id: str, expected_revision: int, request_key: SecretStr,
                                selection_kind: Literal["claim", "node"], selection_id: str,
                                question: str) -> JobReceipt: ...


class ProjectOperationsApplication:
    def __init__(self, fallback: Application, jobs: FollowUpJobs, sessions: SessionRepository,
                 tokens: CsrfTokens, runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        self._fallback, self._jobs, self._sessions = fallback, jobs, sessions
        self._runtime, self._limits, self._clock = runtime, body_limits, clock_check
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _FOLLOW_UP.fullmatch(str(scope.get("path", ""))) is None:
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
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _guarded_response(self, scope: Mapping[str, object], receive: Receive,
                                request_id: str) -> _Response:
        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                response = await self._dispatch(scope, receive)
                if len(response.body) > self._runtime.api.maximum_response_bytes:
                    raise ValueError("invalid receipt size")
        except RequestRejected as error:
            return _problem(error.status, error.code, error.safe_message, request_id,
                                allow=b"POST" if error.status == 405 else None)
        except (AuthorizationDenied, SourceNotFound):
            return _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except ProductError:
            return _problem(400, "invalid_request", "The follow-up request is invalid.", request_id)
        except WorkspaceConflict:
            return _problem(412, "revision_conflict", "The project changed. Refresh before trying again.", request_id)
        except SourceConflict:
            return _problem(409, "idempotency_conflict", "The request key was already used.", request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            return _problem(status, error.code, detail, request_id)
        except TimeoutError:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            return _problem(500, "service_failure", "The service could not complete the request.", request_id)
        return response

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        match = _FOLLOW_UP.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise SourceNotFound()
        if scope.get("method") != "POST":
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        if await self._clock() is not True:
            raise TimeoutError()
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied("session_expired")
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        revision = WorkspaceApplication._revision(headers)
        key = JobApplication._request_key(headers)
        raw = await read_json_body(receive, headers, self._limits)
        request = decode_json_model(raw, FollowUpRequest, maximum_bytes=self._limits.maximum_bytes,
                                    maximum_depth=self._runtime.api.maximum_json_depth)
        receipt = _validated(await self._jobs.enqueue_follow_up(
            credential_digest=credential_digest(token), workspace_id=match[1], parent_result_id=match[2],
            expected_revision=revision, request_key=key, selection_kind=request.selection_kind,
            selection_id=request.selection_id, question=request.question), JobReceipt)
        if receipt.workspace_id != match[1]:
            raise ValueError("invalid receipt scope")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(202, receipt.model_dump_json().encode())

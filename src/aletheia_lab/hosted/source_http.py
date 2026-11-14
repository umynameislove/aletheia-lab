"""Opt-in experimental ZIP upload route with mandatory edge/CSRF/clock guards.

Receipt wire shape is for review; runtime has no automatic source route.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.session_capabilities import Identifier, Version
from aletheia_lab.project.identity import content_hasher

from .action_audit import report_outcome, request_context
from .application import _ACCESS_ERRORS, VERSION, AccessDenied, Send, _cookie, _problem, _Response
from .edge import EdgeGuard
from .postgres_sources import SourceConflict, SourceNotFound, SourceReceipt
from .read_backend import SessionRepository
from .request_boundary import BodyLimits, Receive, RequestRejected, read_zip_body
from .settings import RuntimeSettings
from .source_bundle import SourceRejected
from .upload_staging import UploadRejected

_ROUTE = re.compile(r"/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/sources")


class SourceUploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Version
    source_id: Identifier
    workspace_id: Identifier
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceWriter(Protocol):
    async def put(self, *, credential_digest: SecretStr, workspace_id: str, request_key: SecretStr,
                  payload: bytes) -> SourceReceipt: ...


class SourceUploadApplication:
    def __init__(self, fallback: Callable[[Mapping[str, object], Receive, Send], Awaitable[None]],
                 sources: SourceWriter, sessions: SessionRepository, tokens: CsrfTokens,
                 runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        self._fallback, self._sources, self._sessions = fallback, sources, sessions
        self._runtime, self._limits, self._clock = runtime, body_limits, clock_check
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds,
            allowed_media_types=frozenset({"application/zip"}),
        ))

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
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _guarded_response(self, scope: Mapping[str, object], receive: Receive,
                                request_id: str) -> _Response:
        try:
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                response = await self._upload(scope, receive)
        except RequestRejected as error:
            return _problem(error.status, error.code, error.safe_message, request_id,
                            allow=b"POST" if error.status == 405 else None)
        except UploadRejected as error:
            return _problem(error.status, error.code, error.detail, request_id)
        except SourceNotFound:
            return _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except SourceConflict:
            return _problem(409, "idempotency_conflict", "The request key was already used.", request_id)
        except SourceRejected:
            return _problem(422, "source_invalid", "The source bundle could not be accepted.", request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            return _problem(status, error.code, detail, request_id)
        except TimeoutError:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            return _problem(500, "service_failure", "The service could not complete the request.", request_id)
        return response

    async def _upload(self, scope: Mapping[str, object], receive: Receive) -> _Response:
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
        keys = [value for name, value in headers if name.lower() == b"idempotency-key"]
        if len(keys) != 1 or not re.fullmatch(rb"[A-Za-z0-9_-]{16,128}", keys[0]):
            raise RequestRejected(400, "invalid_request", "A valid request key is required.")
        match = _ROUTE.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise SourceNotFound()
        payload = await read_zip_body(receive, headers, self._limits)
        receipt = await self._sources.put(credential_digest=credential_digest(token), workspace_id=match[1],
                                          request_key=SecretStr(keys[0].decode("ascii")), payload=payload)
        output = SourceUploadReceipt.model_validate({"schema_version": VERSION, **asdict(receipt)}, strict=True)
        if output.workspace_id != match[1] or output.sha256 != content_hasher(payload).hexdigest():
            raise ValueError("Invalid source receipt scope or digest.")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(201, output.model_dump_json().encode("utf-8"))

"""P06/P29-P31 plus scoped resume reads, using the existing source authority."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest

from .application import _ACCESS_ERRORS, AccessDenied, Send, _cookie, _problem, _Response
from .edge import EdgeGuard
from .postgres_sources import SourceConflict, SourceNotFound
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    _collect_body,
    _header_values,
    decode_json_model,
    read_json_body,
)
from .settings import RuntimeSettings
from .source_bundle import SourceRejected
from .upload_staging import UploadManifest, UploadRejected, UploadStatus

_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_ROUTE = re.compile(rf"/v1/workspaces/({_ID})/uploads(?:/({_ID})(?:(/complete)|/parts/([1-9][0-9]{{0,2}}))?)?")


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Uploads(Protocol):
    async def create_upload(self, *, credential_digest: SecretStr, workspace_id: str, request_key: SecretStr,
                            manifest: UploadManifest) -> UploadStatus: ...
    async def upload_status(self, *, credential_digest: SecretStr, workspace_id: str, upload_id: str) -> UploadStatus: ...
    async def put_upload_part(self, *, credential_digest: SecretStr, workspace_id: str, upload_id: str,
                              part_number: int, payload: bytes) -> UploadStatus: ...
    async def complete_upload(self, *, credential_digest: SecretStr, workspace_id: str, upload_id: str) -> UploadStatus: ...
    async def cancel_upload(self, *, credential_digest: SecretStr, workspace_id: str, upload_id: str) -> UploadStatus: ...


class UploadStagingApplication:
    def __init__(self, fallback: Callable[[Mapping[str, object], Receive, Send], Awaitable[None]],
                 sources: Uploads, sessions: SessionRepository, tokens: CsrfTokens, runtime: RuntimeSettings, *,
                 body_limits: BodyLimits, part_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        self._fallback, self._sources, self._sessions = fallback, sources, sessions
        self._runtime, self._limits, self._part_limits, self._clock = runtime, body_limits, part_limits, clock_check
        self._complete_slots = asyncio.Semaphore(1)
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(self._route, tokens, SessionBindingLoader(sessions),
            runtime, lookup_timeout_seconds=runtime.api.backend_timeout_seconds,
            allowed_media_types=frozenset({"application/json", "application/octet-stream"})))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _ROUTE.fullmatch(str(scope.get("path", ""))) is None:
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _route(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        request_id = "req_" + uuid4().hex
        match = _ROUTE.fullmatch(str(scope.get("path", "")))
        allow = self._allow(match)
        deadline = 120.0 if match is not None and match[3] is not None else max(
            self._limits.total_timeout_seconds, self._runtime.api.backend_timeout_seconds)
        try:
            async with asyncio.timeout(deadline):
                value, status = await self._dispatch(scope, receive)
                response = _Response(status, value.model_dump_json().encode())
        except RequestRejected as error:
            response = _problem(error.status, error.code, error.safe_message, request_id,
                                allow=allow if error.status == 405 else None)
        except UploadRejected as error:
            response = _problem(error.status, error.code, error.detail, request_id)
        except SourceNotFound:
            response = _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except SourceConflict:
            response = _problem(409, "idempotency_conflict", "The request key was already used.", request_id)
        except SourceRejected:
            response = _problem(422, "source_invalid", "The source bundle could not be accepted.", request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            response = _problem(status, error.code, detail, request_id)
        except TimeoutError:
            response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            response = _problem(500, "service_failure", "The service could not complete the request.", request_id)
        headers = [(b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"x-request-id", request_id.encode())]
        if response.allow is not None:
            headers.append((b"allow", response.allow))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    @staticmethod
    def _allow(match: re.Match[str] | None) -> bytes:
        if match is None or match[2] is None or match[3] is not None:
            return b"POST"
        return b"PUT" if match[4] is not None else b"GET, HEAD, DELETE"

    async def _credential(self, scope: Mapping[str, object]) -> SecretStr:
        if await self._clock() is not True:
            raise TimeoutError()
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied("session_expired")
        digest = credential_digest(token)
        return digest

    async def _create(self, digest: SecretStr, workspace: str,
                      headers: Sequence[tuple[bytes, bytes]], receive: Receive) -> tuple[UploadStatus, int]:
        keys = _header_values(headers, b"idempotency-key")
        if len(keys) != 1 or re.fullmatch(rb"[A-Za-z0-9_-]{16,128}", keys[0]) is None:
            raise RequestRejected(400, "invalid_request", "A valid request key is required.")
        body = await read_json_body(receive, headers, self._limits)
        manifest = decode_json_model(body, UploadManifest, maximum_bytes=self._limits.maximum_bytes,
                                     maximum_depth=self._runtime.api.maximum_json_depth)
        value = await self._sources.create_upload(credential_digest=digest, workspace_id=workspace,
            request_key=SecretStr(keys[0].decode()), manifest=manifest)
        status = 201
        return value, status

    async def _put_part(self, digest: SecretStr, workspace: str, upload_id: str,
                        part: str, headers: Sequence[tuple[bytes, bytes]],
                        receive: Receive) -> tuple[UploadStatus, int]:
        if (_header_values(headers, b"content-type") != [b"application/octet-stream"]
                or _header_values(headers, b"content-encoding") not in ([], [b"identity"])):
            raise RequestRejected(415, "unsupported_media_type", "A binary upload part is required.")
        payload = await _collect_body(receive, headers, self._part_limits)
        value = await self._sources.put_upload_part(credential_digest=digest, workspace_id=workspace,
                                                    upload_id=upload_id, part_number=int(part), payload=payload)
        status = 200
        return value, status

    async def _checked_status(self, value: UploadStatus, workspace: str,
                              upload_id: str | None) -> UploadStatus:
        value = UploadStatus.model_validate(value.model_dump(mode="python"))
        if value.workspace_id != workspace or (upload_id is not None and value.upload_id != upload_id):
            raise ValueError("Invalid upload response scope")
        if await self._clock() is not True:
            raise TimeoutError()
        return value

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> tuple[UploadStatus, int]:
        match = _ROUTE.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise SourceNotFound()
        workspace, upload_id, complete, part = match.groups()
        method = scope.get("method")
        if method not in self._allow(match).decode().split(", "):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        digest = await self._credential(scope)
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        if upload_id is None:
            value, status = await self._create(digest, workspace, headers, receive)
        elif part is not None:
            value, status = await self._put_part(digest, workspace, upload_id, part, headers, receive)
        elif complete is not None:
            body = await read_json_body(receive, headers, self._limits)
            decode_json_model(body, _Empty, maximum_bytes=self._limits.maximum_bytes,
                              maximum_depth=self._runtime.api.maximum_json_depth)
            async with self._complete_slots:
                value = await self._sources.complete_upload(credential_digest=digest, workspace_id=workspace, upload_id=upload_id)
            status = 200
        elif method == "DELETE":
            if await _collect_body(receive, headers, BodyLimits(1, self._limits.idle_timeout_seconds,
                    self._limits.total_timeout_seconds, self._limits.maximum_events)):
                raise RequestRejected(400, "invalid_request", "This operation requires an empty body.")
            value = await self._sources.cancel_upload(credential_digest=digest, workspace_id=workspace, upload_id=upload_id)
            status = 200
        else:
            value = await self._sources.upload_status(credential_digest=digest, workspace_id=workspace, upload_id=upload_id)
            status = 200
        value = await self._checked_status(value, workspace, upload_id)
        return value, status

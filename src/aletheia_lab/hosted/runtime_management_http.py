"""Guarded compact project metadata and workspace policy routes."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.runtime_management import (
    ChangeProject,
    ChangeWorkspaceSettings,
    ProjectDetails,
    WorkspaceSettings,
)

from .action_audit import report_outcome, request_context
from .application import _ACCESS_ERRORS, AccessDenied, Send, _cookie, _problem, _Response
from .authorization import AuthorizationDenied
from .edge import EdgeGuard
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from .runtime_management import PolicyRejected
from .settings import RuntimeSettings
from .workspace_http import WorkspaceApplication
from .workspace_management import WorkspaceConflict, WorkspaceDenied

_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_PROJECT = re.compile(rf"/v1/workspaces/({_ID})/projects/(p3-project-[0-9a-f]{{64}})")
_SETTINGS = re.compile(rf"/v1/workspaces/({_ID})/settings")
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]


class Management(Protocol):
    async def project(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str) -> ProjectDetails: ...
    async def update_project(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str,
                             expected_revision: int, request: ChangeProject) -> ProjectDetails: ...
    async def settings(self, *, credential_digest: SecretStr, workspace_id: str) -> WorkspaceSettings: ...
    async def update_settings(self, *, credential_digest: SecretStr, workspace_id: str,
                              expected_revision: int, request: ChangeWorkspaceSettings) -> WorkspaceSettings: ...


class RuntimeManagementApplication:
    def __init__(self, fallback: Application, management: Management, sessions: SessionRepository,
                 tokens: CsrfTokens, runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        self._fallback, self._management, self._sessions = fallback, management, sessions
        self._runtime, self._limits, self._clock = runtime, body_limits, clock_check
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        project = _PROJECT.fullmatch(path)
        if (scope.get("type") != "http" or (project is None and _SETTINGS.fullmatch(path) is None)
                or project is not None and scope.get("method") == "DELETE"):
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _route(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        request_id = "req_" + uuid4().hex
        with request_context(request_id):
            await self._respond(scope, receive, send, request_id)

    async def _respond(self, scope: Mapping[str, object], receive: Receive, send: Send, request_id: str) -> None:
        response, revision = await self._response(scope, receive, request_id)
        if 400 <= response.status < 500:
            report_outcome("rejected")
        headers = [(b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"x-request-id", request_id.encode())]
        if response.status == 200 and revision is not None:
            headers.append((b"etag", f'"{revision}"'.encode()))
        if response.allow is not None:
            headers.append((b"allow", response.allow))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _response(self, scope: Mapping[str, object], receive: Receive, request_id: str) -> tuple[_Response, int | None]:
        revision = None
        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                value = await self._dispatch(scope, receive)
                revision = value.revision
                response = _Response(200, value.model_dump_json().encode())
                if len(response.body) > self._runtime.api.maximum_response_bytes:
                    raise ValueError("The management response exceeds its budget.")
        except RequestRejected as error:
            response = _problem(error.status, error.code, error.safe_message, request_id,
                                allow=self._allow(scope) if error.status == 405 else None)
        except AuthorizationDenied:
            response = _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except WorkspaceDenied:
            response = _problem(403, "permission_denied", "This operation is not permitted.", request_id)
        except WorkspaceConflict:
            response = _problem(412, "revision_conflict", "The resource changed. Refresh before trying again.", request_id)
        except PolicyRejected:
            response = _problem(422, "schema_invalid", "Request body does not match the operation schema.", request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            response = _problem(status, error.code, detail, request_id)
        except TimeoutError:
            response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            response = _problem(500, "service_failure", "The service could not complete the request.", request_id)
        return response, revision

    @staticmethod
    def _allow(scope: Mapping[str, object]) -> bytes:
        return b"GET, HEAD, PATCH, DELETE" if _PROJECT.fullmatch(str(scope.get("path", ""))) else b"GET, HEAD, PATCH"

    async def _credential(self, scope: Mapping[str, object]) -> SecretStr:
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied("session_expired")
        return credential_digest(token)

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> ProjectDetails | WorkspaceSettings:
        if scope.get("method") not in ("GET", "HEAD", "PATCH"):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        if await self._clock() is not True:
            raise TimeoutError()
        digest = await self._credential(scope)
        project = _PROJECT.fullmatch(str(scope.get("path", "")))
        match = project or _SETTINGS.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise AuthorizationDenied()
        workspace = match[1]
        if scope.get("method") == "PATCH":
            headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
            revision = WorkspaceApplication._revision(headers)
            body = await read_json_body(receive, headers, self._limits)
            value = await self._update(digest, workspace, project[2] if project else None, revision, body)
        elif project:
            value = await self._management.project(credential_digest=digest, workspace_id=workspace, project_id=project[2])
        else:
            value = await self._management.settings(credential_digest=digest, workspace_id=workspace)
        model = ProjectDetails if project else WorkspaceSettings
        parsed = model.model_validate(value.model_dump(mode="python"), strict=True)
        if parsed.workspace_id != workspace or project and getattr(parsed, "project_id", None) != project[2]:
            raise ValueError("The management response scope is invalid.")
        if scope.get("method") == "PATCH" and parsed.revision != revision + 1:
            raise ValueError("The management response revision is invalid.")
        if await self._clock() is not True:
            raise TimeoutError()
        return parsed

    async def _update(self, digest: SecretStr, workspace: str, project: str | None,
                      revision: int, body: bytes) -> ProjectDetails | WorkspaceSettings:
        if project:
            request = decode_json_model(body, ChangeProject, maximum_bytes=self._limits.maximum_bytes,
                                        maximum_depth=self._runtime.api.maximum_json_depth)
            return await self._management.update_project(credential_digest=digest, workspace_id=workspace,
                project_id=project, expected_revision=revision, request=request)
        change = decode_json_model(body, ChangeWorkspaceSettings, maximum_bytes=self._limits.maximum_bytes,
                                   maximum_depth=self._runtime.api.maximum_json_depth)
        return await self._management.update_settings(credential_digest=digest, workspace_id=workspace,
                                                      expected_revision=revision, request=change)

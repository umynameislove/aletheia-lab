"""Authenticated workspace onboarding and membership routes using shared guards."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TypeVar, cast
from urllib.parse import parse_qsl
from uuid import uuid4

from pydantic import BaseModel, SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.workspaces import (
    AcceptInvitation,
    ChangeGrant,
    ChangeMember,
    CreateInvitation,
    CreateWorkspace,
    Empty,
    Invitation,
    InvitationPage,
    InvitationTargetPage,
    LookupInvitationTargets,
    Member,
    MemberPage,
    RevokedInvitation,
    SourceGrant,
    TransferOwner,
    Workspace,
    WorkspacePage,
)

from .action_audit import report_outcome, request_context
from .application import _ACCESS_ERRORS, AccessDenied, Send, _cookie, _problem, _Response
from .authorization import AuthorizationDenied
from .cursors import CursorExpired, CursorInvalid
from .edge import EdgeGuard
from .postgres_sources import SourceNotFound
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    _collect_body,
    decode_json_model,
    read_json_body,
)
from .settings import RuntimeSettings
from .workspace_management import (
    PostgresWorkspaceManagement,
    WorkspaceConflict,
    WorkspaceDenied,
    WorkspaceInvitationError,
)

_ID = r"([A-Za-z0-9][A-Za-z0-9._:-]{0,127})"
_MEMBERS = re.compile(r"/v1/workspaces/" + _ID + r"/members(?:/" + _ID + r")?")
_TRANSFER = re.compile(r"/v1/workspaces/" + _ID + r"/transfer-owner")
_INVITES = re.compile(r"/v1/workspaces/" + _ID + r"/invitations(?:/" + _ID + r")?")
_GRANT = re.compile(r"/v1/workspaces/" + _ID + r"/sources/" + _ID + r"/grants/" + _ID)
_WORKSPACE = re.compile(r"/v1/workspaces/" + _ID)
_TARGETS = re.compile(r"/v1/workspaces/" + _ID + r"/invitation-targets")
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]
Model = TypeVar("Model", bound=BaseModel)


class WorkspaceApplication:
    def __init__(self, fallback: Application, workspaces: PostgresWorkspaceManagement,
                 sessions: SessionRepository, tokens: CsrfTokens, runtime: RuntimeSettings, *,
                 body_limits: BodyLimits, clock_check: Callable[[], Awaitable[bool]]) -> None:
        self._fallback, self._workspaces, self._sessions = fallback, workspaces, sessions
        self._runtime, self._limits, self._clock = runtime, body_limits, clock_check
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not (
            path in ("/v1/workspaces", "/v1/invitations/accept")
            or any(pattern.fullmatch(path) for pattern in (_MEMBERS, _TRANSFER, _INVITES, _GRANT, _WORKSPACE, _TARGETS))
        ):
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _route(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        request_id = "req_" + uuid4().hex
        with request_context(request_id):
            await self._route_with_context(scope, receive, send, request_id)

    async def _deliver(self, response: _Response, scope: Mapping[str, object],
                       send: Send, request_id: str) -> None:
        if 400 <= response.status < 500:
            report_outcome("rejected")
        headers = [(b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"x-request-id", request_id.encode())]
        if 200 <= response.status < 300:
            revision = json.loads(response.body).get("revision")
            if type(revision) is int:
                headers.append((b"etag", ('"' + str(revision) + '"').encode()))
        if response.allow:
            headers.append((b"allow", response.allow))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    @staticmethod
    def _cursor_error(error: CursorExpired | CursorInvalid, request_id: str) -> _Response:
        if isinstance(error, CursorExpired):
            return _problem(410, "cursor_expired", "The continuation has expired.", request_id)
        return _problem(400, "cursor_invalid", "The continuation is invalid for this request.", request_id)

    async def _route_with_context(self, scope: Mapping[str, object], receive: Receive, send: Send,
                                  request_id: str) -> None:
        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                response = await self._dispatch(scope, receive)
        except RequestRejected as error:
            response = _problem(error.status, error.code, error.safe_message, request_id,
                                allow=self._allow(str(scope.get("path", ""))) if error.status == 405 else None)
        except (CursorExpired, CursorInvalid) as error:
            response = self._cursor_error(error, request_id)
        except (SourceNotFound, AuthorizationDenied):
            response = _problem(404, "not_found", "The requested resource is unavailable.", request_id)
        except WorkspaceInvitationError as error:
            response = _problem(error.status, error.code, "The invitation cannot be used.", request_id)
        except WorkspaceDenied:
            response = _problem(403, "permission_denied", "This operation is not permitted.", request_id)
        except WorkspaceConflict as error:
            status = 412 if error.code == "revision_conflict" else 409
            response = _problem(status, error.code, "The request conflicts with current state.", request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            response = _problem(status, error.code, detail, request_id)
        except TimeoutError:
            response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            response = _problem(500, "service_failure", "The service could not complete the request.", request_id)
        await self._deliver(response, scope, send, request_id)
    @staticmethod
    def _allow(path: str) -> bytes:
        if path == "/v1/workspaces":
            return b"GET, HEAD, POST"
        if _WORKSPACE.fullmatch(path):
            return b"GET, HEAD"
        members = _MEMBERS.fullmatch(path)
        invites = _INVITES.fullmatch(path)
        if members:
            return b"PATCH" if members[2] else b"GET, HEAD"
        if invites and invites[2]:
            return b"DELETE"
        if invites:
            return b"GET, HEAD, POST"
        return b"GET, HEAD, PUT" if _GRANT.fullmatch(path) else b"POST"

    @staticmethod
    def _revision(headers: Sequence[tuple[bytes, bytes]]) -> int:
        values = [value for key, value in headers if key.lower() == b"if-match"]
        if not values:
            raise RequestRejected(428, "precondition_required", "A current revision is required.")
        if len(values) != 1 or not re.fullmatch(rb'"(?:0|[1-9][0-9]{0,18})"', values[0]):
            raise RequestRejected(400, "invalid_request", "The revision is invalid.")
        value = int(values[0][1:-1])
        if value > 9223372036854775806:
            raise RequestRejected(400, "invalid_request", "The revision is invalid.")
        return value

    @staticmethod
    def _member_query(query: object) -> tuple[int, str | None]:
        try:
            if not isinstance(query, bytes) or len(query) > 4608:
                raise ValueError()
            text = query.decode("ascii")
            if re.search(r"%(?![0-9A-Fa-f]{2})", text):
                raise ValueError()
            pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            values = dict(pairs)
            if len(values) != len(pairs) or set(values) - {"limit", "cursor"}:
                raise ValueError()
            raw_limit = values.get("limit", "50")
            if re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit) is None or int(raw_limit) > 200:
                raise ValueError()
            cursor = values.get("cursor")
            if cursor is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,4096}", cursor) is None:
                raise ValueError()
            return int(raw_limit), cursor
        except (ValueError, UnicodeError):
            raise RequestRejected(400, "invalid_request", "The member page query is invalid.") from None

    def _parsed(self, body: bytes, model: type[Model]) -> Model:
        return decode_json_model(body, model, maximum_bytes=self._limits.maximum_bytes,
                                 maximum_depth=self._runtime.api.maximum_json_depth)

    @staticmethod
    def _workspace_id(path: str) -> str | None:
        for pattern in (_MEMBERS, _TRANSFER, _INVITES, _GRANT, _TARGETS, _WORKSPACE):
            match = pattern.fullmatch(path)
            if match is not None:
                return match[1]
        return None

    async def _serialize(self, value: object, response_model: type[BaseModel],
                         expected_ids: dict[str, str], path: str, status: int) -> _Response:
        # Validate repository output through the same producer contract used by fixtures.
        validated = response_model.model_validate(value)
        if any(getattr(validated, key, None) != target for key, target in expected_ids.items()):
            raise ValueError("invalid workspace response target")
        response_workspace = getattr(validated, "workspace_id", None)
        if response_workspace:
            expected = self._workspace_id(path)
            if expected is not None and response_workspace != expected:
                raise ValueError("invalid workspace response scope")
        payload = validated.model_dump_json().encode("utf-8")
        if len(payload) > self._runtime.api.maximum_response_bytes:
            raise ValueError("workspace response too large")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(status, payload)

    async def _invitations(self, invites: re.Match[str], method: str, digest: SecretStr,
                           body: bytes, limit: int, cursor: str | None) -> _Response:
        status = 200
        response_model: type[BaseModel]
        expected_ids: dict[str, str] = {}
        if invites[2]:
            if body:
                self._parsed(body, Empty)
            expected_ids = {"invitation_id": invites[2]}
            value = await self._workspaces.revoke_invite(credential_digest=digest, workspace_id=invites[1],
                                                        invitation_id=invites[2])
            response_model = RevokedInvitation
        elif invites and method in ("GET", "HEAD"):
            value = await self._workspaces.list_invitations(credential_digest=digest, workspace_id=invites[1],
                                                           limit=limit, cursor=cursor)
            response_model = InvitationPage
        elif invites:
            invitation = self._parsed(body, CreateInvitation)
            expected_ids = {"user_id": invitation.user_id}
            value = await self._workspaces.invite(credential_digest=digest, workspace_id=invites[1],
                user_id=invitation.user_id, role=invitation.role, expires_in_seconds=invitation.expires_in_seconds)
            status, response_model = 201, Invitation
        return await self._serialize(value, response_model, expected_ids, invites[0], status)

    async def _membership_operation(self, members: re.Match[str] | None,
                                    transfer: re.Match[str] | None, digest: SecretStr,
                                    body: bytes, revision: int, limit: int,
                                    cursor: str | None) -> _Response:
        response_model: type[BaseModel]
        expected_ids: dict[str, str] = {}
        if members and members[2]:
            change = self._parsed(body, ChangeMember)
            expected_ids = {"user_id": members[2]}
            value = await self._workspaces.mutate_member(credential_digest=digest, workspace_id=members[1],
                user_id=members[2], expected_revision=revision, role=change.role, active=change.active)
            response_model = Member
        elif members:
            value = await self._workspaces.list_members(credential_digest=digest, workspace_id=members[1],
                                                         limit=limit, cursor=cursor)
            response_model = MemberPage
        elif transfer:
            target = self._parsed(body, TransferOwner).user_id
            expected_ids = {"user_id": target}
            value = await self._workspaces.transfer_owner(credential_digest=digest, workspace_id=transfer[1],
                user_id=target, expected_revision=revision)
            response_model = Member
        else:
            raise SourceNotFound()
        route = members if members is not None else transfer
        if route is None:
            raise SourceNotFound()
        return await self._serialize(value, response_model, expected_ids, route[0], 200)

    async def _execute(self, path: str, method: str, digest: SecretStr, body: bytes,
                       revision: int, limit: int, cursor: str | None) -> _Response:
        members, invites = _MEMBERS.fullmatch(path), _INVITES.fullmatch(path)
        transfer, grant = _TRANSFER.fullmatch(path), _GRANT.fullmatch(path)
        workspace_read, targets = _WORKSPACE.fullmatch(path), _TARGETS.fullmatch(path)
        status = 200
        response_model: type[BaseModel] = Workspace
        expected_ids: dict[str, str] = {}
        if path == "/v1/workspaces" and method in ("GET", "HEAD"):
            value = await self._workspaces.list_workspaces(credential_digest=digest, limit=limit, cursor=cursor)
            response_model = WorkspacePage
        elif path == "/v1/workspaces":
            value = await self._workspaces.create(credential_digest=digest, name=self._parsed(body, CreateWorkspace).name)
            status = 201
        elif path == "/v1/invitations/accept":
            value = await self._workspaces.accept_invite(credential_digest=digest, token=self._parsed(body, AcceptInvitation).token)
        elif workspace_read:
            value = await self._workspaces.read_workspace(credential_digest=digest, workspace_id=workspace_read[1])
        elif targets:
            lookup = self._parsed(body, LookupInvitationTargets)
            value = await self._workspaces.lookup_invitation_targets(credential_digest=digest, workspace_id=targets[1],
                email=lookup.email, limit=lookup.limit, cursor=lookup.cursor)
            response_model = InvitationTargetPage
        elif members or transfer:
            return await self._membership_operation(members, transfer, digest, body, revision, limit, cursor)
        elif invites:
            return await self._invitations(invites, method, digest, body, limit, cursor)
        elif grant and method in ("GET", "HEAD"):
            expected_ids = {"source_id": grant[2], "user_id": grant[3]}
            value = await self._workspaces.read_source_grant(credential_digest=digest, workspace_id=grant[1],
                                                            source_id=grant[2], user_id=grant[3])
            response_model = SourceGrant
        elif grant:
            expected_ids = {"source_id": grant[2], "user_id": grant[3]}
            value = await self._workspaces.set_source_grant(credential_digest=digest, workspace_id=grant[1],
                source_id=grant[2], user_id=grant[3], actions=cast(list[str], self._parsed(body, ChangeGrant).actions),
                expected_revision=revision)
            response_model = SourceGrant
        else:
            raise SourceNotFound()
        return await self._serialize(value, response_model, expected_ids, path, status)

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        path, method = str(scope.get("path", "")), str(scope.get("method", ""))
        if method.encode() not in self._allow(path).split(b", "):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        members = _MEMBERS.fullmatch(path)
        invites = _INVITES.fullmatch(path)
        paged_read = (method in ("GET", "HEAD") and (path == "/v1/workspaces"
            or (members is not None and members[2] is None) or (invites is not None and invites[2] is None)))
        query = scope.get("query_string", b"")
        if paged_read:
            limit, cursor = self._member_query(query)
        elif query:
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        else:
            limit, cursor = 50, None
        if await self._clock() is not True:
            raise TimeoutError()
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if token is None or cookie is None or await self._sessions.resolve_cookie(cookie) is None:
            raise AccessDenied("session_expired")
        digest = credential_digest(token)
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        transfer = _TRANSFER.fullmatch(path)
        revision = self._revision(headers) if method in ("PATCH", "PUT") or transfer else 0
        if method == "DELETE" and not any(name.lower() == b"content-type" for name, _ in headers):
            body = await _collect_body(receive, headers, self._limits)
            if body:
                raise RequestRejected(415, "unsupported_media_type", "A UTF-8 JSON body is required.")
        else:
            body = b"" if method in ("GET", "HEAD") else await read_json_body(receive, headers, self._limits)

        return await self._execute(path, method, digest, body, revision, limit, cursor)

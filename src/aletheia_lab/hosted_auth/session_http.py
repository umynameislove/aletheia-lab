"""Opt-in renewal/logout-all routes using existing Session/Problem wire models."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, SecretStr

from aletheia_lab.hosted.application import VERSION, AccessDenied, Send, _cookie, _problem
from aletheia_lab.hosted.request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_contract.session_capabilities import PROBLEM_STATUS, Session

from .boundary import SESSION_COOKIE, clear_opaque_cookie, read_opaque_cookie, set_opaque_cookie
from .csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader, clear_csrf_cookie, csrf_cookie
from .repositories import RepositoryDataRejected
from .sessions import RenewalConflict, ServerSessions, SessionRenewal, credential_digest

Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]
RENEW_PATH = "/v1/auth/renew"
LOGOUT_ALL_PATH = "/v1/auth/logout-all"


class _EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _now() -> datetime:
    return datetime.now(UTC)


class SessionMutationApplication:
    """CSRF precedes the body/effect; one deadline covers the complete request.

    Route names and renewal_conflict/409 are draft extensions for J-01 review.
    Logout-all derives its actor from current server state, never a request ID.
    """

    def __init__(
        self, fallback: Application, sessions: ServerSessions, tokens: CsrfTokens,
        runtime: RuntimeSettings, *, body_limits: BodyLimits,
        clock_check: Callable[[], Awaitable[bool]], clock: Callable[[], datetime] = _now,
        creation_hint: Callable[[SecretStr], Awaitable[bool]] | None = None,
    ) -> None:
        self._runtime = RuntimeSettings.model_validate(runtime.model_dump(warnings=False))
        self._fallback, self._sessions, self._limits = fallback, sessions, body_limits
        self._clock_check, self._clock = clock_check, clock
        self._creation_hint = creation_hint
        self._guard = CsrfMiddleware(
            self._mutate, tokens, SessionBindingLoader(sessions), self._runtime,
            lookup_timeout_seconds=self._runtime.api.backend_timeout_seconds, clock=clock,
        )

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in (RENEW_PATH, LOGOUT_ALL_PATH):
            await self._fallback(scope, receive, send)
            return
        started = False

        async def tracked_send(event: Mapping[str, object]) -> None:
            nonlocal started
            if event.get("type") == "http.response.start":
                started = True
            if scope.get("method") == "HEAD" and event.get("type") == "http.response.body":
                event = {**event, "body": b""}
            await send(event)

        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                await self._guard(scope, receive, tracked_send)
        except TimeoutError:
            if started:
                raise
            await self._failure(send, 503, "service_unavailable")

    async def _mutate(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        try:
            if scope.get("method") != "POST":
                raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
            if await self._clock_check() is not True:
                raise TimeoutError()
            headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
            # The same bounded body parser as the existing hosted HTTP adapters.
            body = await read_json_body(receive, headers, self._limits)
            decode_json_model(body, _EmptyBody, maximum_bytes=self._limits.maximum_bytes)
            cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
            if scope.get("path") == RENEW_PATH:
                allowed = await self._can_create_workspace(cookie)
                result = await self._sessions.renew(cookie)
                response, extra = self._renewed(result, can_create_workspace=allowed)
                status = 200
            else:
                await self._sessions.logout_all(cookie)
                response, status = b"", 204
                extra = [
                    (b"set-cookie", clear_opaque_cookie(name=SESSION_COOKIE)),
                    (b"set-cookie", clear_csrf_cookie()),
                ]
            if await self._clock_check() is not True:
                raise TimeoutError()
        except RequestRejected as error:
            await self._failure(send, error.status, error.code)
            return
        except AccessDenied as error:
            await self._failure(send, PROBLEM_STATUS[error.code], error.code)
            return
        except RenewalConflict:
            await self._failure(send, 409, "renewal_conflict")
            return
        except TimeoutError:
            await self._failure(send, 503, "service_unavailable")
            return
        except Exception:
            await self._failure(send, 500, "service_failure")
            return
        await self._send(send, status, response, extra)

    async def _can_create_workspace(self, cookie: bytes | None) -> bool:
        if self._creation_hint is None:
            return False
        credential = read_opaque_cookie(
            cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if credential is None:
            raise RepositoryDataRejected()
        allowed = await self._creation_hint(credential_digest(credential))
        if type(allowed) is not bool:
            raise RepositoryDataRejected()
        return allowed

    def _renewed(self, result: SessionRenewal, *, can_create_workspace: bool = False) -> tuple[bytes, list[tuple[bytes, bytes]]]:
        record, credential = result.record, result.credential
        now = self._clock()
        if (
            now.utcoffset() is None or record.state != "active" or record.actor_enabled is not True
            or record.expires_at.utcoffset() is None or record.expires_at <= now
        ):
            raise RepositoryDataRejected()
        value = Session.model_validate({
            "schema_version": VERSION, "actor": record.actor.model_dump(warnings=False),
            "session": {"context_id": record.context_id, "expires_at": record.expires_at},
            "csrf_token": record.csrf_token.get_secret_value(), "onboarding": record.onboarding,
            "last_workspace_id": record.last_workspace_id,
            "can_create_workspace": can_create_workspace,
        }, strict=True)
        response = value.model_dump_json().encode("utf-8")
        if len(response) > self._runtime.api.maximum_response_bytes:
            raise RepositoryDataRejected()
        extra: list[tuple[bytes, bytes]] = []
        if credential is not None:
            age = min(int((record.expires_at - now).total_seconds()), 604800)
            if age < 1:
                raise RepositoryDataRejected()
            extra = [
                (b"set-cookie", set_opaque_cookie(name=SESSION_COOKIE, value=credential, max_age=age)),
                (b"set-cookie", csrf_cookie(record.csrf_token, max_age=age)),
            ]
        return response, extra

    @staticmethod
    async def _failure(send: Send, status: int, code: str) -> None:
        problem = _problem(status, code, "The request could not be completed.", "req_" + uuid4().hex)
        extra = [(b"content-type", problem.content_type)]
        if status == 405:
            extra.append((b"allow", b"POST"))
        await SessionMutationApplication._send(send, status, problem.body, extra)

    @staticmethod
    async def _send(send: Send, status: int, body: bytes, extra: list[tuple[bytes, bytes]]) -> None:
        headers = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"content-length", str(len(body)).encode("ascii")), *extra]
        if status == 200:
            headers.append((b"content-type", b"application/json"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

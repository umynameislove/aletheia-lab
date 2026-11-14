"""CSRF-protected single-session logout for the experimental runtime."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from aletheia_lab.hosted.application import Send, _cookie, _problem
from aletheia_lab.hosted.composition import Application
from aletheia_lab.hosted.request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from aletheia_lab.hosted.settings import RuntimeSettings

from .boundary import SESSION_COOKIE, clear_opaque_cookie
from .csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader, clear_csrf_cookie
from .sessions import ServerSessions


class _EmptyBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class LogoutApplication:
    def __init__(self, fallback: Application, sessions: ServerSessions, tokens: CsrfTokens,
                 runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]],
                 idp_logout_url: Callable[[], Awaitable[str]] | None = None) -> None:
        self._fallback, self._sessions, self._runtime = fallback, sessions, runtime
        self._limits, self._clock = body_limits, clock_check
        self._idp_logout_url = idp_logout_url
        self._guard = CsrfMiddleware(self._logout, tokens, SessionBindingLoader(sessions), runtime,
                                     lookup_timeout_seconds=runtime.api.backend_timeout_seconds)

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get('type') != 'http' or scope.get('path') != '/v1/auth/logout':
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _logout(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        try:
            if scope.get('method') != 'POST':
                raise RequestRejected(405, 'method_not_allowed', 'This method is not supported.')
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                if await self._clock() is not True:
                    raise TimeoutError()
                headers = cast(Sequence[tuple[bytes, bytes]], scope.get('headers', ()))
                body = await read_json_body(receive, headers, self._limits)
                decode_json_model(body, _EmptyBody, maximum_bytes=self._limits.maximum_bytes)
                destination = None
                if self._idp_logout_url is not None:
                    try:
                        async with asyncio.timeout(min(1.0, self._runtime.api.backend_timeout_seconds / 4)):
                            destination = await self._idp_logout_url()
                    except Exception:
                        pass
                await self._sessions.logout(_cookie(scope, self._runtime.api.maximum_cookie_bytes))
            response = (json.dumps({'logout_url': destination}, separators=(',', ':')).encode()
                        if self._idp_logout_url is not None else b'')
            status = 200 if self._idp_logout_url is not None else 204
            extra = [(b'set-cookie', clear_opaque_cookie(name=SESSION_COOKIE)),
                     (b'set-cookie', clear_csrf_cookie())]
            if self._idp_logout_url is not None:
                extra.append((b'content-type', b'application/json'))
        except RequestRejected as error:
            problem = _problem(error.status, error.code, error.safe_message, 'req_' + uuid4().hex)
            response, status, extra = problem.body, problem.status, [(b'content-type', problem.content_type)]
        except TimeoutError:
            problem = _problem(503, 'service_unavailable', 'The service is temporarily unavailable.', 'req_' + uuid4().hex)
            response, status, extra = problem.body, problem.status, [(b'content-type', problem.content_type)]
        except Exception:
            problem = _problem(500, 'service_failure', 'The service could not complete the request.', 'req_' + uuid4().hex)
            response, status, extra = problem.body, problem.status, [(b'content-type', problem.content_type)]
        await send({'type':'http.response.start','status':status,'headers':extra + [
            (b'cache-control',b'no-store'),(b'content-length',str(len(response)).encode())]})
        await send({'type':'http.response.body','body':response})

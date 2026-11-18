"""Same-origin login initiation for the experimental hosted runtime."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import parse_qsl, urlsplit

from ._diagnostics import auth_stage
from .boundary import LOGIN_COOKIE, set_opaque_cookie
from .login import LoginRejected, LoginStart

Receive = Callable[[], Awaitable[Mapping[str, object]]]
Send = Callable[[Mapping[str, object]], Awaitable[None]]
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]


class Coordinator(Protocol):
    async def begin(self, cookie: bytes | None, *, return_to: str | None = None,
                    reauthenticate: bool = False) -> LoginStart: ...


class LoginStartApplication:
    def __init__(self, fallback: Application, coordinator: Coordinator, *, maximum_cookie_bytes: int,
                 timeout_seconds: float, clock_check: Callable[[], Awaitable[bool]],
                 allow_direct_switch: bool = True) -> None:
        self._fallback, self._coordinator = fallback, coordinator
        self._maximum_cookie_bytes, self._timeout, self._clock = maximum_cookie_bytes, timeout_seconds, clock_check
        self._allow_direct_switch = allow_direct_switch

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get('type') != 'http' or scope.get('path') != '/v1/auth/start':
            await self._fallback(scope, receive, send)
            return
        status, headers = 400, []
        try:
            if scope.get('method') != 'GET':
                status, headers = 405, [(b'allow', b'GET')]
            elif scope.get('scheme') == 'https':
                headers = await self._start(scope)
                status = 303
        except (LoginRejected, ValueError, UnicodeError):
            status, headers = 400, []
        except TimeoutError:
            status, headers = 503, []
        except Exception:
            status, headers = 500, []
        body = b'' if status == 303 else b'Authentication request could not be completed.'
        await send({'type': 'http.response.start', 'status': status, 'headers': headers + [
            (b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer'),
            (b'x-content-type-options', b'nosniff'), (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async def _start(self, scope: Mapping[str, object]) -> list[tuple[bytes, bytes]]:
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get('headers', ()))
        sites = [v for k, v in headers if k.lower() == b'sec-fetch-site']
        if sites != [b'same-origin']:
            raise LoginRejected()
        cookies = [v for k, v in headers if k.lower() == b'cookie']
        cookie = b'; '.join(cookies) if cookies else None
        if cookie is not None and len(cookie) > self._maximum_cookie_bytes:
            raise LoginRejected()
        query = scope.get('query_string', b'')
        if not isinstance(query, bytes) or len(query) > 2048:
            raise LoginRejected()
        pairs = parse_qsl(query.decode('ascii'), keep_blank_values=True, strict_parsing=True,
                          encoding='utf-8', errors='strict', max_num_fields=2)
        params = dict(pairs)
        if len(params) != len(pairs) or set(params) - {'return_to', 'mode'} or params.get('mode', 'login') not in ('login', 'switch'):
            raise LoginRejected()
        if params.get('mode') == 'switch' and not self._allow_direct_switch:
            raise LoginRejected()
        async with asyncio.timeout(self._timeout):
            with auth_stage("start", "readiness_before"):
                if await self._clock() is not True:
                    raise TimeoutError()
            with auth_stage("start", "coordinator"):
                result = await self._coordinator.begin(cookie, return_to=params.get('return_to'),
                                                       reauthenticate=params.get('mode') == 'switch')
            with auth_stage("start", "readiness_after"):
                if await self._clock() is not True:
                    raise TimeoutError()
        url = result.authorization_url.get_secret_value()
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or any(ord(c) < 33 or ord(c) > 126 for c in url)):
            raise LoginRejected()
        age = min(int((result.expires_at - datetime.now(UTC)).total_seconds()), 600)
        if age < 1:
            raise LoginRejected()
        return [(b'location', url.encode('ascii')),
                (b'set-cookie', set_opaque_cookie(name=LOGIN_COOKIE, value=result.browser_credential, max_age=age))]

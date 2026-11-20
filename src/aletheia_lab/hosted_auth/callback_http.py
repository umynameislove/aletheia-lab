"""Opt-in ASGI callback adapter; depends on a real transactional coordinator.

This module does not install a route or provide an identity/session store. The
outer runtime must enforce the configured HTTPS origin and trusted Host before
calling it. No redirect, cookie, query or exception is written to logs here.
"""

from __future__ import annotations

import asyncio
import hmac
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

from ._diagnostics import auth_stage
from .boundary import (
    SESSION_COOKIE,
    allowed_return_to,
    set_opaque_cookie,
)
from .csrf import csrf_cookie
from .identity import IdentityRejected
from .login import LoginCompletion, LoginRejected
from .repositories import SessionSnapshot
from .sessions import credential_digest
from .settings import AuthPolicy

Receive = Callable[[], Awaitable[Mapping[str, object]]]
Send = Callable[[Mapping[str, object]], Awaitable[None]]
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]


class Coordinator(Protocol):
    async def complete(self, cookie: bytes | None, query: bytes) -> LoginCompletion: ...


class CallbackApplication:
    """Handle only GET callback; all unrelated routes pass to an injected app."""

    def __init__(self, application: Application, coordinator: Coordinator, policy: AuthPolicy, *,
                 maximum_cookie_bytes: int, timeout_seconds: float,
                 clock_check: Callable[[], Awaitable[bool]] | None = None) -> None:
        if type(maximum_cookie_bytes) is not int or maximum_cookie_bytes <= 0:
            raise ValueError("positive cookie budget required")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("positive callback timeout required")
        self._application, self._coordinator = application, coordinator
        self._policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        self._maximum_cookie_bytes, self._timeout = maximum_cookie_bytes, timeout_seconds
        self._clock_check = clock_check

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/auth/callback":
            await self._application(scope, receive, send)
            return
        status, headers = 400, []
        failure: str | None = None
        try:
            if scope.get("method") != "GET":
                status, headers = 405, [(b"allow", b"GET")]
            elif scope.get("scheme") != "https":
                status = 400
            else:
                headers = await self._complete(scope)
                status = 303
        except LoginRejected as denied:
            failure = "login_cancelled" if denied.reason == "provider_cancelled" else "login_failed"
        except IdentityRejected as denied:
            failure = "account_disabled" if denied.reason == "disabled" else "login_failed"
        except TimeoutError:
            # Clock/backend deadlines share the safe sign-in-unavailable presentation.
            failure = "idp_unavailable"
        except Exception:
            failure = "login_failed"
        if failure is not None:
            status, headers = 303, [(b"location", b"/#/signed-out?reason=" + failure.encode("ascii"))]
        body = b"" if status == 303 else b"Authentication request could not be completed."
        common = [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer"),
                  (b"x-content-type-options", b"nosniff"), (b"content-length", str(len(body)).encode("ascii"))]
        await send({"type": "http.response.start", "status": status, "headers": common + headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})

    async def _complete(self, scope: Mapping[str, object]) -> list[tuple[bytes, bytes]]:
        query = scope.get("query_string")
        if not isinstance(query, bytes) or not query or len(query) > self._policy.maximum_callback_query_bytes:
            raise LoginRejected()
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        cookies = [value for name, value in headers if name.lower() == b"cookie"]
        if sum(map(len, cookies)) + max(0, len(cookies) - 1) * 2 > self._maximum_cookie_bytes:
            raise LoginRejected()
        cookie = b"; ".join(cookies) if cookies else None
        async with asyncio.timeout(self._timeout):
            with auth_stage("callback", "readiness_before"):
                if self._clock_check is not None and await self._clock_check() is not True:
                    raise TimeoutError("Clock readiness unavailable")
            with auth_stage("callback", "coordinator"):
                completed = await self._coordinator.complete(cookie, query)
            with auth_stage("callback", "readiness_after"):
                if self._clock_check is not None and await self._clock_check() is not True:
                    raise TimeoutError("Clock readiness unavailable")
        # Revalidate the trusted producer result before emitting any credentials.
        snapshot = SessionSnapshot.model_validate(completed.session.model_dump(warnings=False))
        now = datetime.now(UTC)
        age = int((min(snapshot.idle_expires_at, snapshot.absolute_expires_at) - now).total_seconds())
        digest = credential_digest(completed.credential).get_secret_value()
        if (snapshot.state != "active" or not snapshot.actor_enabled
                or snapshot.account_epoch != snapshot.session_epoch or age < 1
                or not hmac.compare_digest(digest, snapshot.credential_digest.get_secret_value())):
            raise LoginRejected()
        destination = allowed_return_to(completed.return_to, self._policy).encode("ascii")
        age = min(age, 604800)
        return [(b"location", destination),
                (b"set-cookie", set_opaque_cookie(name=SESSION_COOKIE, value=completed.credential, max_age=age)),
                (b"set-cookie", csrf_cookie(snapshot.csrf_token, max_age=age))]

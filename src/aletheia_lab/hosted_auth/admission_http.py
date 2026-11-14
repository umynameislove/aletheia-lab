"""Shared auth-admission HTTP adapter; requires an explicit authoritative gate."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted.application import Send, _problem
from aletheia_lab.hosted.request_boundary import Receive

from .abuse import AuthAdmission, AuthOperation, AuthRateLimited
from .repositories import RepositoryDataRejected
from .session_http import LOGOUT_ALL_PATH, RENEW_PATH
from .settings import AuthConfigurationRejected

Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]
_AUDIT = logging.getLogger("aletheia.auth.admission")
_PATHS: dict[str, AuthOperation] = {
    "/v1/auth/start": "login_start", "/v1/auth/callback": "callback",
    RENEW_PATH: "renew", "/v1/auth/logout": "logout", LOGOUT_ALL_PATH: "logout_all",
}


def admission_key(value: str) -> SecretStr:
    """Validate an explicit stable operator key; no random per-process fallback."""
    if not isinstance(value, str):
        raise AuthConfigurationRejected()
    key = value.encode("utf-8")
    if not 32 <= len(key) <= 4096 or any(byte < 33 or byte == 127 for byte in key):
        raise AuthConfigurationRejected()
    return SecretStr(value)


class AuthAdmissionApplication:
    """Use only after trusted edge/proxy normalization, before auth effects.

    One gate covers mandatory global AND source quotas. Keys use the server's
    peer address, never a caller cookie/identity/X-Forwarded-For header. The
    configured deadline covers quota lookup and inner auth work; the caller owns
    the shared store's namespace.
    """

    def __init__(
        self, application: Application, admission: AuthAdmission, *, subject_key: SecretStr,
        timeout_seconds: float,
    ) -> None:
        if (not isinstance(subject_key, SecretStr) or isinstance(timeout_seconds, bool)
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise AuthConfigurationRejected()
        self._app, self._admission = application, admission
        self._key = admission_key(subject_key.get_secret_value())
        self._timeout = timeout_seconds

    def _subject(self, scope: Mapping[str, object]) -> SecretStr:
        client = scope.get("client")
        if not isinstance(client, (tuple, list)) or len(client) != 2:
            raise AuthConfigurationRejected()
        host = client[0]
        if not isinstance(host, str) or len(host) > 64 or "%" in host:
            raise AuthConfigurationRejected()
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        raw = b"aletheia-auth-source-v1\x00" + address.packed
        return SecretStr(hmac.digest(self._key.get_secret_value().encode("utf-8"), raw, "sha256").hex())

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        path = scope.get("path")
        if scope.get("type") != "http" or not isinstance(path, str) or path not in _PATHS:
            await self._app(scope, receive, send)
            return
        started = False

        async def tracked_send(event: Mapping[str, object]) -> None:
            nonlocal started
            if event.get("type") == "http.response.start":
                started = True
            await send(event)

        try:
            async with asyncio.timeout(self._timeout):
                await self._request(scope, receive, tracked_send, _PATHS[path])
        except TimeoutError:
            if started:
                raise
            await self._reject(scope, send, 503, "service_unavailable")

    async def _request(
        self, scope: Mapping[str, object], receive: Receive, send: Send, operation: AuthOperation,
    ) -> None:
        try:
            await self._admission.check(operation, self._subject(scope))
        except AuthRateLimited as error:
            await self._reject(scope, send, 429, "rate_limited", error.retry_after_seconds)
            return
        except RepositoryDataRejected:
            await self._reject(scope, send, 500, "service_failure")
            return
        except Exception:
            await self._reject(scope, send, 503, "service_unavailable")
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: Mapping[str, object], send: Send, status: int, code: str, retry_after: int | None = None,
    ) -> None:
        request_id = "req_" + uuid4().hex
        value = _problem(status, code, "The authentication request could not be completed.", request_id)
        _AUDIT.info("auth_admission_rejected", extra={
            "request_id": request_id, "auth_operation": _PATHS.get(str(scope.get("path", ""))),
            "auth_outcome": code, "http_status": status, "retry_after_seconds": retry_after,
        })
        headers = [(b"content-type", value.content_type), (b"cache-control", b"no-store"),
                   (b"x-content-type-options", b"nosniff"),
                   (b"content-length", str(len(value.body)).encode("ascii"))]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        body = b"" if scope.get("method") == "HEAD" else value.body
        await send({"type": "http.response.body", "body": body})

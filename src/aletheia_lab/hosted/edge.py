"""Fail-closed Host boundary for the current no-trusted-proxy configuration."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

from .application import Send
from .request_boundary import Receive

Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]
_AUTHORITY = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")


def _authority(value: bytes, default_port: int) -> tuple[str, int] | None:
    try:
        text = value.decode("ascii")
        if len(text) > 512 or text.endswith(":") or not _AUTHORITY.fullmatch(text):
            return None
        parsed = urlsplit("//" + text)
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            return None
        if parsed.path or parsed.query or parsed.fragment or parsed.port == 0:
            return None
        return parsed.hostname.lower(), parsed.port or default_port
    except (UnicodeError, ValueError):
        return None


class EdgeGuard:
    """Validate one configured authority and drop all untrusted proxy hints.

    The ASGI server must itself disable proxy-header processing: middleware
    cannot undo a server that already rewrote scope client or scheme.
    """

    def __init__(self, public_origin: str, application: Application) -> None:
        origin = urlsplit(public_origin)
        hostname = origin.hostname
        if origin.scheme not in ("http", "https") or not hostname:
            raise ValueError("validated public origin is required")
        self._default_port = 443 if origin.scheme == "https" else 80
        self._expected = (hostname.encode("idna").decode("ascii").lower(), origin.port or self._default_port)
        self._application = application

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._application(scope, receive, send)
            return
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        hosts = [value for name, value in headers if name.lower() == b"host"]
        if len(hosts) != 1 or _authority(hosts[0], self._default_port) != self._expected:
            body = b"Bad Request"
            await send({"type": "http.response.start", "status": 400, "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ]})
            await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})
            return
        filtered = [
            (name, value) for name, value in headers
            if name.lower() not in (b"forwarded", b"x-real-ip")
            and not name.lower().startswith(b"x-forwarded-")
        ]
        checked_scope = dict(scope)
        checked_scope["headers"] = filtered
        await self._application(checked_scope, receive, send)

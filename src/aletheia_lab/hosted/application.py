"""ASGI read adapter; real session/membership repositories must be injected.

There is deliberately no fixture, local bridge, or in-memory auth fallback.
This module neither implements OIDC nor executes domain/worker operations.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeVar, cast, get_args
from uuid import uuid4

from pydantic import BaseModel

from aletheia_lab.hosted_contract.session_capabilities import (
    Capabilities,
    Problem,
    Session,
    Version,
)

from .request_boundary import Receive, RequestRejected

Send = Callable[[Mapping[str, object]], Awaitable[None]]
Model = TypeVar("Model", bound=BaseModel)
VERSION = cast(Version, get_args(Version)[0])
_CAPABILITIES_PATH = re.compile(r"/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/capabilities")
AccessCode = Literal["session_expired", "session_revoked", "membership_revoked", "permission_denied"]
_ACCESS_ERRORS: dict[AccessCode, tuple[int, str]] = {
    "session_expired": (401, "The session has expired."),
    "session_revoked": (401, "The session is no longer active."),
    "membership_revoked": (403, "Workspace access is no longer active."),
    "permission_denied": (403, "This operation is not permitted."),
}


class AccessDenied(Exception):
    """Repository denial using the existing draft contract's public codes."""

    def __init__(self, code: AccessCode) -> None:
        if code not in _ACCESS_ERRORS:
            raise ValueError("unsupported access denial")
        self.code = code
        super().__init__(_ACCESS_ERRORS[code][1])


class HostedBackend(Protocol):
    """Trusted server adapter, to be implemented by the auth/repository layer.

    Implementations must use bounded asynchronous I/O. load_session verifies
    the server session (including rotation, idle/absolute expiry and revocation)
    using only the cookie, never caller-declared identity/role/bridge headers.
    capabilities rechecks current membership and policy for that exact actor and
    workspace. A client role or previously captured grant is not authorization.
    is_ready probes required dependencies, including durable workers when used.
    """

    async def is_ready(self) -> bool: ...

    async def load_session(self, cookie: bytes | None) -> Session: ...

    async def capabilities(self, session: Session, workspace_id: str) -> Capabilities: ...


@dataclass(frozen=True)
class AdapterLimits:
    """Explicit operational budgets, not agreed product-capability limits."""

    backend_timeout_seconds: float
    maximum_response_bytes: int
    maximum_cookie_bytes: int

    def __post_init__(self) -> None:
        timeout = self.backend_timeout_seconds
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("backend timeout must be finite and positive")
        # Reserve enough space for a safe Problem when a normal payload is too large.
        if type(self.maximum_response_bytes) is not int or self.maximum_response_bytes < 4096:
            raise ValueError("response budget must be at least 4096 bytes")
        if type(self.maximum_cookie_bytes) is not int or self.maximum_cookie_bytes <= 0:
            raise ValueError("cookie budget must be a positive integer")


@dataclass(frozen=True)
class _Response:
    status: int
    body: bytes
    content_type: bytes = b"application/json"
    allow: bytes | None = None


def _validated(value: Model, model: type[Model]) -> Model:
    # frozen models can still contain mutated lists/dicts; validate their fields
    # again instead of trusting an already-created model or model_copy(update=...).
    return model.model_validate(value.model_dump(mode="python", warnings=False), strict=True)


def _problem(status: int, code: str, detail: str, request_id: str, *, allow: bytes | None = None) -> _Response:
    value = Problem(
        schema_version=VERSION,
        type=f"urn:aletheia:problem:{code.replace('_', '-')}",
        title="Request could not be completed",
        status=status,
        code=code,
        detail=detail,
        request_id=request_id,
    )
    return _Response(status, value.model_dump_json().encode("utf-8"), b"application/problem+json", allow)


def _cookie(scope: Mapping[str, object], maximum: int) -> bytes | None:
    # ASGI headers are server-decoded bytes. Multiple HTTP/2 Cookie fields may
    # be joined with '; ' before the maintained auth adapter parses them.
    headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", []))
    values = [value for name, value in headers if name.lower() == b"cookie"]
    if sum(map(len, values)) + max(0, len(values) - 1) * 2 > maximum:
        raise RequestRejected(431, "headers_too_large", "Request headers exceed the configured limit.")
    return b"; ".join(values) if values else None


class HostedApplication:
    """Read-only ASGI wiring; readiness is unavailable without a real backend."""

    def __init__(self, backend: HostedBackend | None, *, limits: AdapterLimits) -> None:
        self.backend = backend
        self.limits = limits

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise ValueError("unsupported ASGI scope")
        request_id = f"req_{uuid4().hex}"
        response = await self._guarded_response(scope, request_id)
        headers = [
            (b"content-type", response.content_type),
            (b"content-length", str(len(response.body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-request-id", request_id.encode("ascii")),
        ]
        if response.allow is not None:
            headers.append((b"allow", response.allow))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        # No repositories, pools or workers are constructed here. Their runtime
        # composition and lifecycle must be supplied with the backend.
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
            else:
                raise ValueError("unsupported lifespan event")

    async def _guarded_response(self, scope: Mapping[str, object], request_id: str) -> _Response:
        try:
            async with asyncio.timeout(self.limits.backend_timeout_seconds):
                response = await self._dispatch(scope, request_id)
            if len(response.body) > self.limits.maximum_response_bytes:
                return _problem(500, "response_invalid", "The service could not produce a valid response.", request_id)
            return response
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            return _problem(status, error.code, detail, request_id)
        except RequestRejected as error:
            return _problem(error.status, error.code, error.safe_message, request_id)
        except TimeoutError:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        except Exception:
            # Never return or interpolate parser, repository or model diagnostics.
            # Transport send failures and external cancellation propagate normally.
            return _problem(500, "service_failure", "The service could not complete the request.", request_id)

    async def _dispatch(self, scope: Mapping[str, object], request_id: str) -> _Response:
        route = str(scope.get("path", ""))
        match = _CAPABILITIES_PATH.fullmatch(route)
        if route not in ("/health/live", "/health/ready", "/v1/session") and match is None:
            return _problem(404, "not_found", "The requested route is unavailable.", request_id)
        if scope.get("method") not in ("GET", "HEAD"):
            return _problem(405, "method_not_allowed", "This method is not supported.", request_id, allow=b"GET, HEAD")
        if route == "/health/live":
            return _Response(200, b'{"status":"alive"}')
        if self.backend is None:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        if route == "/health/ready":
            return await self._readiness(request_id)
        return await self._protected(scope, match.group(1) if match else None)

    async def _readiness(self, request_id: str) -> _Response:
        backend = self.backend
        if backend is None or await backend.is_ready() is not True:
            return _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
        return _Response(200, b'{"status":"ready"}')

    async def _protected(self, scope: Mapping[str, object], workspace_id: str | None) -> _Response:
        backend = self.backend
        if backend is None:
            raise RuntimeError("backend is not configured")
        cookie = _cookie(scope, self.limits.maximum_cookie_bytes)
        if cookie is None:
            raise AccessDenied("session_expired")
        session = _validated(await backend.load_session(cookie), Session)
        if session.session.expires_at <= datetime.now(UTC):
            raise AccessDenied("session_expired")
        if workspace_id is None:
            return _Response(200, session.model_dump_json().encode("utf-8"))
        capabilities = _validated(await backend.capabilities(session, workspace_id), Capabilities)
        capabilities.require_workspace(workspace_id)
        if session.session.expires_at <= datetime.now(UTC):
            raise AccessDenied("session_expired")
        return _Response(200, capabilities.model_dump_json().encode("utf-8"))

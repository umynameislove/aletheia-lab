"""Process-local ASGI resource lifetime; repository implementations are injected.

No pool or worker is constructed at import time. The factory must yield a real
HostedBackend in deployment and must clean up partial acquisition itself.
Tests may inject synthetic resources, which do not qualify hosted identity.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from typing import cast
from uuid import uuid4

from .application import AdapterLimits, HostedApplication, HostedBackend, Send, _problem
from .edge import EdgeGuard
from .observability import process_telemetry
from .request_boundary import BodyLimits, Receive, RequestRejected
from .resource_admission import RequestAdmission, ResourceBusy
from .settings import RuntimeSettings

ResourceFactory = Callable[[], AbstractAsyncContextManager[HostedBackend]]
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]


class ProcessApplication:
    """One application instance per server process and request event loop."""

    def __init__(self, settings: RuntimeSettings, resources: ResourceFactory, *,
                 routes: Callable[[Application], Application] | None = None,
                 scheduler: Callable[[asyncio.Event], Awaitable[None]] | None = None) -> None:
        self.settings = settings
        self._resources = resources
        self._scheduler = scheduler
        self._scheduler_task: asyncio.Future[None] | None = None
        self._used = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self.admission = RequestAdmission(settings.api.concurrency_limit)
        api = settings.api
        self.body_limits = BodyLimits(
            maximum_bytes=api.maximum_json_bytes,
            idle_timeout_seconds=api.body_idle_timeout_seconds,
            total_timeout_seconds=api.body_total_timeout_seconds,
            maximum_events=api.maximum_body_events,
        )
        self._application = HostedApplication(None, limits=AdapterLimits(
            backend_timeout_seconds=api.backend_timeout_seconds,
            maximum_response_bytes=api.maximum_response_bytes,
            maximum_cookie_bytes=api.maximum_cookie_bytes,
        ))
        self._edge = EdgeGuard(settings.public_origin, self._application)
        self._routed = EdgeGuard(settings.public_origin, routes(self._application)) if routes is not None else self._edge

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            raise RuntimeError("runtime requests must use the owning event loop")
        healthy = self._scheduler_task is None or not self._scheduler_task.done()
        if not healthy:
            self._application.backend = None
        application = self._routed if self._application.backend is not None else self._edge
        if scope["type"] == "http":
            await self._http(application, scope, receive, send)
        else:
            await application(scope, receive, send)

    async def _http(self, application: Application, scope: Mapping[str, object],
                    receive: Receive, send: Send) -> None:
        slots = self.admission.slots(str(scope.get("path", "")))
        telemetry = process_telemetry()
        try:
            slots.acquire()
        except ResourceBusy:
            telemetry.increment("http_rejected")
            request_id = "req_" + uuid4().hex
            response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
            async with asyncio.timeout(self.settings.api.backend_timeout_seconds):
                await send({"type": "http.response.start", "status": 503, "headers": [
                    (b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"retry-after", b"1"), (b"x-request-id", request_id.encode()),
                ]})
                await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})
            return
        telemetry.set_gauge("http_inflight", self.admission.regular.snapshot().active
                            + (self.admission.health.snapshot().active if self.admission.health is not self.admission.regular else 0))
        try:
            await self._admitted_http(application, scope, receive, send)
        finally:
            slots.release()
            telemetry.set_gauge("http_inflight", self.admission.regular.snapshot().active
                                + (self.admission.health.snapshot().active if self.admission.health is not self.admission.regular else 0))

    async def _admitted_http(self, application: Application, scope: Mapping[str, object],
                             receive: Receive, send: Send) -> None:
        started = False

        async def tracked_send(event: Mapping[str, object]) -> None:
            nonlocal started
            if event.get("type") == "http.response.start":
                started = True
                if event.get("status") in (429, 503):
                    response_headers = list(cast(Sequence[tuple[bytes, bytes]], event.get("headers", ())))
                    if not any(name.lower() == b"retry-after" for name, _ in response_headers):
                        response_headers.append((b"retry-after", b"1"))
                    event = dict(event, headers=response_headers)
            await send(event)

        try:
            async with asyncio.timeout(self.settings.api.request_timeout_seconds):
                headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
                api = self.settings.api
                if (len(headers) > api.maximum_header_count
                        or sum(len(name) + len(value) + 4 for name, value in headers) > api.maximum_header_bytes):
                    raise RequestRejected(431, "headers_too_large", "Request headers exceed the configured limit.")
                await application(scope, receive, tracked_send)
        except (TimeoutError, RequestRejected) as error:
            # Headers may already be on the wire; let the server close that response.
            if started:
                raise
            request_id = "req_" + uuid4().hex
            if isinstance(error, RequestRejected):
                response = _problem(error.status, error.code, error.safe_message, request_id)
            else:
                response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
            headers = [(b"content-type", response.content_type),
                       (b"content-length", str(len(response.body)).encode("ascii")),
                       (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                       (b"x-request-id", request_id.encode("ascii"))]
            async with asyncio.timeout(self.settings.api.backend_timeout_seconds):
                await tracked_send({"type": "http.response.start", "status": response.status, "headers": headers})
                await tracked_send({"type": "http.response.body",
                            "body": b"" if scope.get("method") == "HEAD" else response.body})

    async def _open(self, stack: AsyncExitStack) -> HostedBackend | None:
        try:
            async with asyncio.timeout(self.settings.api.startup_timeout_seconds):
                backend = await stack.enter_async_context(self._resources())
                if await backend.is_ready() is not True:
                    return None
                return backend
        except Exception:
            # Fixed lifespan failure below; never forward factory/driver errors.
            return None

    async def _close(self, stack: AsyncExitStack) -> bool:
        try:
            async with asyncio.timeout(self.settings.api.resource_close_timeout_seconds):
                await stack.aclose()
            return True
        except Exception:
            # Cancellation from outside the configured deadline still propagates.
            return False

    @staticmethod
    async def _shutdown_requested(receive: Receive) -> bool:
        try:
            return (await receive()).get("type") == "lifespan.shutdown"
        except Exception:
            return False

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        first = await receive()
        if self._used or first.get("type") != "lifespan.startup":
            await send({"type": "lifespan.startup.failed", "message": "Runtime startup is unavailable."})
            return
        self._used = True
        self._owner_loop = asyncio.get_running_loop()
        stack = AsyncExitStack()
        try:
            backend = await self._open(stack)
            if backend is None:
                await self._close(stack)
                await send({"type": "lifespan.startup.failed", "message": "Runtime dependencies are unavailable."})
                return
            if self._scheduler is not None:
                stop = asyncio.Event()
                task = asyncio.ensure_future(self._scheduler(stop))
                self._scheduler_task = task

                async def close_scheduler() -> None:
                    stopped_early = task.done()
                    stop.set()
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    if stopped_early:
                        raise RuntimeError("scheduler stopped before shutdown")

                stack.push_async_callback(close_scheduler)
                await asyncio.sleep(0)
                if task.done():
                    await self._close(stack)
                    await send({"type": "lifespan.startup.failed", "message": "Runtime dependencies are unavailable."})
                    return
            self._application.backend = backend
            await send({"type": "lifespan.startup.complete"})
            requested = await self._shutdown_requested(receive)
            # Stop publishing the backend before any resource is closed.
            self._application.backend = None
            closed = await self._close(stack)
            if requested and closed:
                await send({"type": "lifespan.shutdown.complete"})
            else:
                await send({"type": "lifespan.shutdown.failed", "message": "Runtime shutdown did not complete."})
        finally:
            self._application.backend = None
            try:
                # AsyncExitStack removes executed callbacks, so this also covers
                # send/receive errors and cancellation without double-closing.
                await self._close(stack)
            finally:
                self._scheduler_task = None
                self._owner_loop = None

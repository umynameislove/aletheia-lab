"""Bounded process observations: fixed metric dimensions and allowlisted JSON logs."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import re
import sys
import threading
import time
import weakref
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO
from uuid import uuid4

from .application import Send
from .request_boundary import Receive

if TYPE_CHECKING:
    from .composition import Application

_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_RELEASE = re.compile(r"[0-9a-f]{64}")
_LOG = logging.getLogger("aletheia.telemetry")
_JOB: ContextVar[str | None] = ContextVar("observed_job_id", default=None)
_STAGES = frozenset(("database_transaction", "blocking_execution", "report_render"))
_OPERATIONS = frozenset(("preview", "confirm", "analyze", "follow_up", "refresh"))
_METHODS = frozenset(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"))
_ROUTES = frozenset(
    (
        "health",
        "session",
        "auth",
        "capabilities",
        "jobs",
        "sources",
        "uploads",
        "imports",
        "projects",
        "results",
        "exports",
        "workspaces",
        "static",
        "other",
    )
)
_GAUGES = frozenset(
    (
        "pool_waiting",
        "pool_size",
        "pool_available",
        "event_loop_lag_ms",
        "queue_oldest_seconds",
        "queue_pending",
        "worker_inflight",
        "download_inflight",
        "gc_pending",
        "blocking_active",
        "blocking_waiting",
        "http_inflight",
        "job_poll_inflight",
    )
)
_COUNTERS = frozenset(
    ("blocking_rejected", "http_rejected", "job_poll_requests", "job_poll_rejected")
)
_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 15.0, 60.0, 300.0)
_EVENTS = frozenset(
    (
        "http.completed",
        "job.execution",
        "action_rejected",
        "action_rolled_back",
        "action_outcome_unknown",
        "auth_admission_rejected",
        "metric.snapshot",
        "stage.execution",
    )
)


def _identity(value: object) -> str | None:
    return value if isinstance(value, str) and _IDENT.fullmatch(value) is not None else None


def route_family(path: object) -> str:
    """Return only a fixed family, never a path segment or an object identifier."""
    if not isinstance(path, str) or len(path) > 4096:
        return "other"
    parts = path.split("/")
    if len(parts) > 1 and parts[1] == "health":
        return "health"
    if len(parts) > 2 and parts[1] == "v1":
        if parts[2] in ("session", "auth"):
            return parts[2]
        if parts[2] == "workspaces":
            if len(parts) > 4 and parts[4] in _ROUTES:
                return parts[4]
            return "workspaces"
        return "other"
    return "static" if not path.startswith("/v1") else "other"


class RuntimeTelemetry:
    """Thread-safe cumulative counters; identifiers never enter metric dimensions."""

    def __init__(self, *, role: str = "api", release_id: str | None = None) -> None:
        if role not in ("api", "worker") or (
            release_id is not None and _RELEASE.fullmatch(release_id) is None
        ):
            raise ValueError("Known process role and release SHA-256 required.")
        self.role, self.release_id = role, release_id
        self.process_id = "proc_" + uuid4().hex
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, ...]] = Counter()
        self._seconds: defaultdict[tuple[str, ...], float] = defaultdict(float)
        self._buckets: Counter[tuple[str, ...]] = Counter()
        self._gauges: dict[str, float] = {}
        self._scalar_counts: Counter[str] = Counter()
        self._pool: weakref.ReferenceType[object] | None = None
        self._loop_timer: asyncio.TimerHandle | None = None
        self._loop_closed = False

    def bind_pool(self, pool: object) -> bool:
        """Observe one owned pool's public stats; test/fallback pools may lack them."""
        if not callable(getattr(pool, "get_stats", None)):
            return False
        try:
            self._pool = weakref.ref(pool)
        except TypeError:
            return False
        return True

    def watch_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """One process timer, measuring its scheduling delay without a polling task."""
        if self._loop_timer is not None or self._loop_closed:
            return

        def tick(expected: float) -> None:
            if self._loop_closed:
                return
            self.set_gauge("event_loop_lag_ms", max(0.0, loop.time() - expected) * 1000)
            next_tick = loop.time() + 1.0
            self._loop_timer = loop.call_at(next_tick, tick, next_tick)

        expected = loop.time() + 1.0
        self._loop_timer = loop.call_at(expected, tick, expected)

    def close(self) -> None:
        self._loop_closed = True
        if self._loop_timer is not None:
            self._loop_timer.cancel()
            self._loop_timer = None

    def record(
        self, *, kind: str, operation: str, method: str, status: str, outcome: str, seconds: float
    ) -> int:
        if (
            kind not in ("http", "job", "stage")
            or operation
            not in (_ROUTES if kind == "http" else _OPERATIONS if kind == "job" else _STAGES)
            or method not in (*_METHODS, "OTHER", "WORKER", "INTERNAL")
            or status not in ("none", "1xx", "2xx", "3xx", "4xx", "5xx")
            or outcome
            not in (
                "complete",
                "interrupted",
                "cancelled",
                "transport_error",
                "error",
                "settled",
                "unsettled",
            )
            or isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError("Only fixed metric dimensions and finite durations are accepted.")
        key = kind, operation, method, status, outcome
        with self._lock:
            self._counts[key] += 1
            self._seconds[key] += seconds
            for bound in _BUCKETS:
                if seconds <= bound:
                    self._buckets[(*key, str(bound))] += 1
            self._buckets[(*key, "+Inf")] += 1
            return self._counts[key]

    def set_gauge(self, name: str, value: float) -> None:
        if (
            name not in _GAUGES
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 1e15
        ):
            raise ValueError("Only bounded known gauges are accepted.")
        with self._lock:
            self._gauges[name] = value

    def increment(self, name: str, count: int = 1) -> None:
        if name not in _COUNTERS or type(count) is not int or not 1 <= count <= 10**9:
            raise ValueError("Known counter and bounded positive increment required.")
        with self._lock:
            self._scalar_counts[name] += count

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            items = [
                {
                    "kind": k[0],
                    "operation": k[1],
                    "method": k[2],
                    "status": k[3],
                    "outcome": k[4],
                    "count": v,
                    "seconds_sum": self._seconds[k],
                    "seconds_buckets": {
                        str(b): self._buckets[(*k, str(b))] for b in (*_BUCKETS, "+Inf")
                    },
                }
                for k, v in sorted(self._counts.items())
            ]
            gauges, counters = dict(self._gauges), dict(self._scalar_counts)
        pool = self._pool() if self._pool is not None else None
        if pool is not None:
            try:
                getter = getattr(pool, "get_stats", None)
                stats = getter() if callable(getter) else {}
            except Exception:
                stats = {}
            if isinstance(stats, dict):
                for name, key in (
                    ("pool_size", "pool_size"),
                    ("pool_available", "pool_available"),
                    ("pool_waiting", "requests_waiting"),
                ):
                    value = stats.get(key)
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(value)
                        and 0 <= value <= 1e15
                    ):
                        gauges[name] = value
        # Observe queued callers while they are still awaiting admission. Do not
        # import/start a blocking-work component solely for its metric snapshot.
        module = sys.modules.get("aletheia_lab.hosted.async_work")
        getter = getattr(module, "blocking_slots", None)
        if callable(getter):
            slots = getter().snapshot()
            gauges.update(blocking_active=slots.active, blocking_waiting=slots.waiting)
        return {
            "schema_version": "hosted-telemetry/v1",
            "role": self.role,
            "release_id": self.release_id,
            "process_id": self.process_id,
            "scope": "Cumulative process-local observations; no cluster or user-load claim.",
            "http_success_log_sample_every": 100,
            "items": items,
            "gauges": gauges,
            "counters": counters,
        }


_PROCESS = RuntimeTelemetry(role="worker")


def process_telemetry() -> RuntimeTelemetry:
    return _PROCESS


class SafeJsonFormatter(logging.Formatter):
    """Never renders message arguments, exceptions, headers, URLs, or arbitrary extras."""

    def __init__(self, telemetry: RuntimeTelemetry) -> None:
        super().__init__()
        self.telemetry = telemetry

    def format(self, record: logging.LogRecord) -> str:
        event = (
            record.msg
            if isinstance(record.msg, str) and record.msg in _EVENTS
            else "runtime.diagnostic"
        )
        family = (
            "telemetry"
            if record.name == "aletheia.telemetry"
            else "action"
            if record.name == "aletheia.action"
            else "auth"
            if record.name == "aletheia.auth.admission"
            else "server"
            if record.name.startswith("uvicorn")
            else "database"
            if record.name.startswith("psycopg")
            else "runtime"
        )
        result: dict[str, object] = {
            "schema_version": "hosted-log/v1",
            "event": event,
            "component": family,
            "severity": "error"
            if record.levelno >= logging.ERROR
            else "warning"
            if record.levelno >= logging.WARNING
            else "info"
            if record.levelno >= logging.INFO
            else "debug",
            "role": self.telemetry.role,
            "release_id": self.telemetry.release_id,
            "process_id": self.telemetry.process_id,
        }
        _correlation_fields(record, result)
        for name, values in (
            ("operation", _OPERATIONS | _ROUTES | _STAGES),
            ("method", _METHODS | {"OTHER", "WORKER", "INTERNAL"}),
            (
                "outcome",
                {
                    "complete",
                    "interrupted",
                    "cancelled",
                    "transport_error",
                    "error",
                    "settled",
                    "unsettled",
                },
            ),
            ("action_outcome", {"rejected", "rolled_back", "outcome_unknown"}),
            ("auth_operation", {"login_start", "callback", "logout", "logout_all", "renew"}),
            ("auth_outcome", {"rate_limited", "service_failure", "service_unavailable"}),
        ):
            value = getattr(record, name, None)
            if isinstance(value, str) and value in values:
                result[name] = value
        for name in ("http_status", "duration_ms", "bytes_sent", "retry_after_seconds"):
            value = getattr(record, name, None)
            if type(value) is int and 0 <= value <= 2**63 - 1:
                result[name] = value
        if record.msg == "hosted_readiness_unavailable reason=%s":
            result["event"] = "readiness.unavailable"
            reasons = {
                "jobs_schema",
                "polling_retention",
                "workspace_management",
                "upload_staging",
                "follow_up",
                "audit",
                "catalog",
                "runtime_operations",
                "admission",
                "clock_bound",
                "timeout",
            }
            if (
                isinstance(record.args, tuple)
                and len(record.args) == 1
                and record.args[0] in reasons
            ):
                result["reason"] = record.args[0]
        return json.dumps(result, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


class BoundedJsonHandler(logging.Handler):
    """One daemon writer per process; saturation drops safe records instead of blocking API I/O."""

    def __init__(
        self,
        telemetry: RuntimeTelemetry,
        stream: TextIO,
        *,
        capacity: int = 256,
        snapshot_seconds: float = 15.0,
    ) -> None:
        if (
            type(capacity) is not int
            or not 1 <= capacity <= 4096
            or isinstance(snapshot_seconds, bool)
            or not isinstance(snapshot_seconds, (int, float))
            or not math.isfinite(snapshot_seconds)
            or not 1 <= snapshot_seconds <= 300
        ):
            raise ValueError("Bounded log capacity and snapshot interval required.")
        super().__init__(logging.INFO)
        self.setFormatter(SafeJsonFormatter(telemetry))
        self._telemetry, self._snapshot_seconds = telemetry, snapshot_seconds
        self._stream, self._queue = stream, queue.Queue[str](maxsize=capacity)
        self._stop, self._drops, self._closed_once = threading.Event(), 0, False
        self._drop_lock, self._next_snapshot = threading.Lock(), 0.0
        self._thread = threading.Thread(
            target=self._write, name="aletheia-observation-log", daemon=True
        )
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._stop.is_set():
                self._drop()
                return
            if self._queue.full():
                self._drop()
                return
            self._queue.put_nowait(self.format(record))
        except Exception:
            self._drop()

    def _drop(self) -> None:
        with self._drop_lock:
            self._drops += 1

    def _write(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            if time.monotonic() >= self._next_snapshot:
                self._next_snapshot = time.monotonic() + self._snapshot_seconds
                try:
                    value = self._telemetry.snapshot()
                    value.update(event="metric.snapshot", log_buffer=self.stats())
                    self._stream.write(
                        json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
                        + "\n"
                    )
                    self._stream.flush()
                except Exception:
                    self._drop()
            try:
                line = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except Exception:
                self._drop()
            finally:
                self._queue.task_done()

    def stats(self) -> dict[str, int | bool]:
        with self._drop_lock:
            dropped = self._drops
        return {
            "capacity": self._queue.maxsize,
            "queued": self._queue.qsize(),
            "dropped": dropped,
            "writer_alive": self._thread.is_alive(),
        }

    def close(self) -> None:
        if not self._closed_once:
            self._closed_once = True
            self._telemetry.close()
            self._stop.set()
            self._thread.join(timeout=2)
        super().close()


def configure_process_observability(
    *, role: str, release_id: str | None = None, stream: TextIO | None = None
) -> BoundedJsonHandler:
    """Configure only process entrypoints; IPC renderer children must not call this."""
    global _PROCESS
    _PROCESS = RuntimeTelemetry(role=role, release_id=release_id)
    handler = BoundedJsonHandler(_PROCESS, sys.stderr if stream is None else stream)
    # Include third-party failures, which may otherwise interpolate private driver/HTTP diagnostics.
    root = logging.getLogger()
    for existing in tuple(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "aletheia.telemetry",
        "aletheia.action",
        "aletheia.auth.admission",
    ):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    return handler


class ObservedApplication:
    def __init__(self, app: Application, *, telemetry: RuntimeTelemetry | None = None) -> None:
        self.app, self.telemetry = app, telemetry if telemetry is not None else process_telemetry()

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started, completed, status, sent, request_id = time.monotonic(), False, 0, 0, None
        outcome = "interrupted"
        transport_failed = False
        operation = route_family(scope.get("path"))
        raw_method = scope.get("method")
        method = raw_method if isinstance(raw_method, str) and raw_method in _METHODS else "OTHER"

        async def tracked(event: Mapping[str, object]) -> None:
            nonlocal completed, status, sent, request_id, transport_failed
            if event.get("type") == "http.response.start":
                raw = event.get("status")
                status = raw if type(raw) is int and 100 <= raw <= 599 else 0
                request_id = _response_request_id(event, request_id)
            try:
                await send(event)
            except Exception:
                transport_failed = True
                raise
            sent, completed = _response_body_state(event, sent, completed)
        try:
            await self.app(scope, receive, tracked)
            outcome = "complete" if completed else "interrupted"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "transport_error" if transport_failed else "error"
            raise
        finally:
            elapsed = max(0.0, time.monotonic() - started)
            count = self.telemetry.record(
                kind="http",
                operation=operation,
                method=method,
                status=str(status // 100) + "xx" if status else "none",
                outcome=outcome,
                seconds=elapsed,
            )
            # Counters cover every request. First/every100th success is logged;
            # errors remain observable until the bounded log queue reports drops.
            if status >= 400 or outcome != "complete" or count == 1 or count % 100 == 0:
                _LOG.info(
                    "http.completed",
                    extra={
                        "request_id": request_id,
                        "operation": operation,
                        "method": method,
                        "http_status": status,
                        "outcome": outcome,
                        "duration_ms": int(elapsed * 1000),
                        "bytes_sent": sent,
                    },
                )


@dataclass
class JobObservation:
    settled: bool = False


@contextmanager
def timed_job(job_id: str, operation: str) -> Iterator[JobObservation]:
    """Settled means the worker handled an outcome, not proof of business success."""
    telemetry, started, observed, outcome = (
        process_telemetry(),
        time.monotonic(),
        JobObservation(),
        "error",
    )
    if _identity(job_id) is None or operation not in _OPERATIONS:
        raise ValueError("Known job metadata required.")
    token = _JOB.set(job_id)
    try:
        yield observed
        outcome = "settled" if observed.settled else "unsettled"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        _JOB.reset(token)
        elapsed = max(0.0, time.monotonic() - started)
        telemetry.record(
            kind="job",
            operation=operation,
            method="WORKER",
            status="none",
            outcome=outcome,
            seconds=elapsed,
        )
        _LOG.info(
            "job.execution",
            extra={
                "job_id": job_id,
                "operation": operation,
                "outcome": outcome,
                "duration_ms": int(elapsed * 1000),
            },
        )


@contextmanager
def timed_stage(stage: str) -> Iterator[None]:
    """Routine completion is a timing outcome; committed effects still require audit proof."""
    if stage not in _STAGES:
        raise ValueError("Known execution stage required.")
    telemetry, started, outcome = process_telemetry(), time.monotonic(), "error"
    try:
        yield
        outcome = "complete"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        elapsed = max(0.0, time.monotonic() - started)
        count = telemetry.record(
            kind="stage",
            operation=stage,
            method="INTERNAL",
            status="none",
            outcome=outcome,
            seconds=elapsed,
        )
        if outcome != "complete" or count == 1 or count % 100 == 0:
            _LOG.info(
                "stage.execution",
                extra={"operation": stage, "outcome": outcome, "duration_ms": int(elapsed * 1000)},
            )
def _correlation_fields(record: logging.LogRecord, result: dict[str, object]) -> None:
    for name in ("request_id", "job_id"):
        value = _identity(getattr(record, name, None))
        if name == "job_id" and value is None:
            value = _JOB.get()
        if name == "request_id" and value is None:
            module = sys.modules.get("aletheia_lab.hosted.action_audit")
            getter = getattr(module, "current_request_id", None)
            if callable(getter):
                value = _identity(getter())
        if value is not None:
            result[name] = value

def _response_request_id(event: Mapping[str, object], request_id: str | None) -> str | None:
    headers = event.get("headers", ())
    if isinstance(headers, (tuple, list)):
        for name, value in headers:
            if name == b"x-request-id" and isinstance(value, bytes):
                request_id = _identity(value.decode("ascii", errors="replace"))
    return request_id

def _response_body_state(event: Mapping[str, object], sent: int, completed: bool) -> tuple[int, bool]:
    if event.get("type") == "http.response.body":
        body = event.get("body", b"")
        if isinstance(body, bytes):
            sent += len(body)
        if not event.get("more_body", False):
            completed = True
    return sent, completed


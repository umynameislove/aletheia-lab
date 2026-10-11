"""Opt-in LitServe 0.2.19 transport evidence with locally owned JSON models.

Native batching, queue expiry, UID allocation and transport remain unchanged.
``cooperative`` is an explicit application-level whole-batch deadline policy.
Terminal events describe application computation, not HTTP delivery; the actual
transport send is recorded separately. Producer journals are trusted-host gold,
not authenticated evidence. See https://github.com/Lightning-AI/LitServe.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import socket
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_type_hints

from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ARMS = ("native", "cooperative")
NATIVE_VERSION = "0.2.19"
MAX_EVENTS = 2500
_MAX_PID_EVENTS = 500
_MAX_EVENT_BYTES = 65536

if TYPE_CHECKING:
    _LitAPI = object
else:
    try:
        _LitAPI = importlib.import_module("litserve").LitAPI
    except ModuleNotFoundError as _missing:
        if _missing.name != "litserve":
            raise
        _LitAPI = object

_DIRECTORY: Path | None = None
_WRITER: _EventWriter | None = None
_GUARD_INSTALLED = False
_TRANSPORT_PID: int | None = None


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write_all(descriptor: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise OSError("producer write made no progress")
        view = view[count:]


class _EventWriter:
    """One locked binary journal per PID; write errors are never suppressed."""

    def __init__(self, directory: Path) -> None:
        self.pid = os.getpid()
        self.directory = directory
        self.sequence = 0
        self.event_bytes = 0
        self.event_write_ns = 0
        self.stats_write_ns = 0
        self.lock = threading.RLock()

    def emit(
        self, kind: str, endpoint: str | None, token: str | None, values: dict[str, Any]
    ) -> None:
        with self.lock:
            if self.sequence >= _MAX_PID_EVENTS:
                raise ValueError("per-process producer event bound exceeded")
            began = time.monotonic_ns()
            event = {
                "pid": self.pid,
                "sequence": self.sequence,
                "time_ns": began,
                "kind": kind,
                "endpoint": endpoint,
                "token": token,
                **values,
            }
            raw = _encode(event) + b"\n"
            if len(raw) > _MAX_EVENT_BYTES:
                raise ValueError("producer event byte bound exceeded")
            flags = (
                os.O_WRONLY
                | os.O_APPEND
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
            )
            descriptor = os.open(self.directory / f"producer-{self.pid}.jsonl", flags, 0o600)
            try:
                _write_all(descriptor, raw)
            finally:
                os.close(descriptor)
            self.event_write_ns += time.monotonic_ns() - began
            self.sequence += 1
            self.event_bytes += len(raw)
            stats_began = time.monotonic_ns()
            stats = {
                "pid": self.pid,
                "events": self.sequence,
                "event_bytes": self.event_bytes,
                "event_write_ns": self.event_write_ns,
                "stats_write_ns": self.stats_write_ns,
                "capture_write_ns": self.event_write_ns + self.stats_write_ns,
                "one_current_stats_update_not_included": True,
                "meaning": "instrumented encoding/binary IO, not uninstrumented serving overhead",
            }
            flags = (
                os.O_WRONLY
                | os.O_TRUNC
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
            )
            descriptor = os.open(self.directory / f"capture-cost-{self.pid}.json", flags, 0o600)
            try:
                _write_all(descriptor, _encode(stats))
            finally:
                os.close(descriptor)
            self.stats_write_ns += time.monotonic_ns() - stats_began


def _event(
    kind: str, *, endpoint: str | None = None, token: str | None = None, **values: Any
) -> None:
    global _WRITER
    if _DIRECTORY is None:
        raise RuntimeError("producer directory not configured")
    if _WRITER is None or _WRITER.pid != os.getpid():
        _WRITER = _EventWriter(_DIRECTORY)
    _WRITER.emit(kind, endpoint, token, values)


def _network_guard(event: str, arguments: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.bind", "socket.sendto"}:
        sock, address = arguments[0], arguments[-1]
        unix_family = getattr(socket, "AF_UNIX", None)
        allowed = (unix_family is not None and sock.family == unix_family) or (
            isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
        )
        _event(
            "network", action=event, family=int(sock.family), address=str(address), allowed=allowed
        )
        if not allowed:
            raise PermissionError("source permits only loopback and AF_UNIX sockets")
    elif event == "socket.getaddrinfo":
        allowed = arguments[0] in {"127.0.0.1", "::1", None}
        _event("network", action=event, address=str(arguments[0]), allowed=allowed)
        if not allowed:
            raise PermissionError("source forbids external name resolution")


def _configure(directory: Path) -> None:
    global _DIRECTORY, _GUARD_INSTALLED
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("owned producer directory must be a real directory")
    _DIRECTORY = directory.resolve()
    if not _GUARD_INSTALLED:
        sys.addaudithook(_network_guard)
        _GUARD_INSTALLED = True


def _plain(value: Any) -> Any:
    """Keep bytes opaque: native exception pickles are not deserialized."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"raw_hex": value.hex(), "encoding": "opaque_native_bytes"}
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "status_code") and hasattr(value, "detail"):
        return {"status_code": value.status_code, "detail": _plain(value.detail)}
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__, "error": str(value)}
    raise TypeError(f"unsupported native evidence value: {type(value).__name__}")


def _install_transport(endpoint: str) -> None:
    """Called after setup inside each endpoint's single native worker."""
    global _TRANSPORT_PID
    if os.getpid() == _TRANSPORT_PID:
        raise RuntimeError("one API setup per native worker required")
    native = importlib.import_module("litserve.transport.process_transport").MPQueueTransport
    original = native.send

    def observed(transport: Any, item: Any, consumer_id: int) -> Any:
        started = time.monotonic_ns()
        result = original(transport, item, consumer_id)
        returned = time.monotonic_ns()
        uid, response = item
        data, status, response_type, worker_id = response
        if status != "START":
            _event(
                "transport",
                endpoint=endpoint,
                uid=uid,
                status=status,
                worker_id=worker_id,
                consumer_id=consumer_id,
                response_data=_plain(data),
                response_type=_plain(response_type),
                send_enter_ns=started,
                send_return_ns=returned,
            )
        return result

    native.send = observed
    _TRANSPORT_PID = os.getpid()


def _install_submit() -> tuple[Any, Any]:
    handler = importlib.import_module("litserve.server").BaseRequestHandler
    original = handler._submit_request

    async def observed(owner: Any, payload: dict[str, Any]) -> Any:
        started = time.monotonic_ns()
        result = await original(owner, payload)
        returned = time.monotonic_ns()
        uid, queue = result
        _event(
            "submit",
            endpoint=owner.lit_api.api_path,
            token=payload.get("token"),
            uid=uid,
            payload=_plain(payload),
            queue=queue,
            enqueue_enter_ns=started,
            enqueue_return_ns=returned,
        )
        return result

    handler._submit_request = observed
    return handler, original


@dataclass
class _OwnedModel:
    coefficient: float
    intercept: float

    def predict(self, operand: float) -> float:
        return self.coefficient * operand + self.intercept


def _load_model(path: Path) -> tuple[_OwnedModel, bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("owned JSON model must be a regular file")
    raw = path.read_bytes()
    if not 0 < len(raw) <= 4096:
        raise ValueError("owned model byte bound exceeded")
    data = json.loads(raw)
    if (
        set(data) != {"format", "coefficient", "intercept"}
        or data["format"] != "owned-affine-json/v1"
    ):
        raise ValueError("owned numerical model format mismatch")
    coefficient, intercept = float(data["coefficient"]), float(data["intercept"])
    if not math.isfinite(coefficient) or not math.isfinite(intercept):
        raise ValueError("finite owned parameters required")
    return _OwnedModel(coefficient, intercept), raw


class EvidenceAPI(_LitAPI):
    """Global picklable API; default native batch/unbatch are inherited."""

    def __init__(self, directory: Path, endpoint: str, arm: str) -> None:
        if arm not in ARMS or endpoint not in {"/a", "/b"}:
            raise ValueError("fixed source arm and endpoint required")
        if _LitAPI is not object:
            importlib.import_module("litserve").LitAPI.__init__(
                self, api_path=endpoint, max_batch_size=2, batch_timeout=0.03
            )
        self.directory, self.endpoint, self.arm = directory, endpoint, arm
        self.batch_number = 0
        self.cancel_token: str | None = None
        self.model: _OwnedModel
        self.artifact_raw: bytes

    def setup(self, device: Any) -> None:
        _configure(self.directory)
        self.model, self.artifact_raw = _load_model(
            self.directory / f"owned-{self.endpoint[1:]}.json"
        )
        _event(
            "setup",
            endpoint=self.endpoint,
            device=device,
            object_id=id(self.model),
            coefficient=self.model.coefficient,
            intercept=self.model.intercept,
            artifact_sha256=content_sha256(self.artifact_raw),
            artifact_hex=self.artifact_raw.hex(),
        )
        _install_transport(self.endpoint)

    def decode_request(self, request: dict[str, Any], context: Any) -> dict[str, Any]:
        _event(
            "decoded",
            endpoint=self.endpoint,
            token=request.get("token"),
            x=request.get("x"),
            payload=_plain(request),
            native_context=_plain(context),
        )
        return request

    def _deadline(self, inputs: list[dict[str, Any]]) -> None:
        if self.arm == "cooperative":
            now = time.monotonic_ns()
            for item in inputs:
                if now >= int(item["deadline_ns"]):
                    self.cancel_token = str(item["token"])
                    exception = importlib.import_module("fastapi").HTTPException
                    raise exception(
                        504, "cooperative deadline expired before designated computation"
                    )

    def _delay(self, duration: float, inputs: list[dict[str, Any]]) -> None:
        until = time.monotonic() + duration
        while True:
            self._deadline(inputs)
            remaining = until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.005, remaining))

    def predict(self, inputs: list[dict[str, Any]], context: Any) -> list[dict[str, float]]:
        self.batch_number += 1
        batch = f"{os.getpid()}:{self.batch_number}"
        self.cancel_token = None
        object_id = id(self.model)
        for slot, item in enumerate(inputs):
            _event(
                "predict_enter",
                endpoint=self.endpoint,
                token=item.get("token"),
                x=item.get("x"),
                batch=batch,
                slot=slot,
                object_id=object_id,
                coefficient=self.model.coefficient,
                intercept=self.model.intercept,
                native_context=_plain(context),
            )
        computed: set[int] = set()
        active_slot: int | None = None
        error: BaseException | None = None
        outputs: list[dict[str, float]] = []
        try:
            for slot, item in enumerate(inputs):
                active_slot = slot
                operand, duration = float(item["x"]), float(item["delay"])
                if (
                    not math.isfinite(operand)
                    or not math.isfinite(duration)
                    or not 0 <= duration <= 0.6
                ):
                    raise ValueError("finite operand and bounded delay required")
                self._delay(duration, inputs)
                self._deadline(inputs)
                if item["fail"]:
                    raise ValueError("owned application failure after delayed work")
                output = self.model.predict(operand)
                _event(
                    "computed",
                    endpoint=self.endpoint,
                    token=item["token"],
                    batch=batch,
                    slot=slot,
                    x=operand,
                    output=output,
                    object_id=id(self.model),
                )
                computed.add(slot)
                outputs.append({"output": output})
            return outputs
        except BaseException as caught:
            error = caught
            raise
        finally:
            cancelled = self.cancel_token is not None
            cause_token = self.cancel_token
            if cause_token is None and active_slot is not None:
                cause_token = str(inputs[active_slot].get("token"))
            for slot, item in enumerate(inputs):
                outcome = "success" if slot in computed else "cancelled" if cancelled else "error"
                _event(
                    "terminal",
                    endpoint=self.endpoint,
                    token=item.get("token"),
                    batch=batch,
                    slot=slot,
                    object_id=object_id,
                    outcome=outcome,
                    meaning="application_computation",
                    collateral=error is not None and item.get("token") != cause_token,
                    trigger_token=self.cancel_token,
                    error_type=type(error).__name__ if error else None,
                    error=str(error) if error else None,
                )

    def encode_response(self, output: dict[str, float], context: Any) -> dict[str, float]:
        return output


# LitServe 0.2.19 reads inspect.signature annotations directly, without resolving
# postponed annotations. Resolve only these public HTTP type contracts; otherwise
# FastAPI mistakes the declared JSON body for a query parameter (HTTP 422).
EvidenceAPI.decode_request.__annotations__ = get_type_hints(EvidenceAPI.decode_request)
EvidenceAPI.encode_response.__annotations__ = get_type_hints(EvidenceAPI.encode_response)


def serve(directory: Path, port: int, arm: str) -> None:
    """Run one caller-owned native child; caller owns the bounded shutdown."""
    if arm not in ARMS or not 1024 <= port <= 65535:
        raise ValueError("fixed arm and unprivileged port required")
    if version("litserve") != NATIVE_VERSION:
        raise ValueError("exact optional LitServe 0.2.19 required")
    directory.mkdir(parents=True, exist_ok=True)
    _configure(directory)
    for endpoint, coefficient in (("a", 2.0), ("b", 3.0)):
        write_new_file(
            directory / f"owned-{endpoint}.json",
            _encode(
                {
                    "format": "owned-affine-json/v1",
                    "coefficient": coefficient,
                    "intercept": 1.0,
                }
            ),
        )
    native = importlib.import_module("litserve")
    handler, original_submit = _install_submit()
    _event("source_start", arm=arm, native_version=NATIVE_VERSION)
    try:
        apis = [EvidenceAPI(directory.resolve(), endpoint, arm) for endpoint in ("/a", "/b")]
        server = native.LitServer(
            apis,
            accelerator="cpu",
            devices=1,
            workers_per_device=1,
            timeout=0.15,
            fast_queue=False,
            restart_workers=False,
            enable_shutdown_api=False,
        )
        server.run(
            host="127.0.0.1",
            port=port,
            num_api_servers=1,
            generate_client_file=False,
            log_level="debug",
            loop="asyncio",
        )
    finally:
        handler._submit_request = original_submit
        _event("source_stop", arm=arm)


def read_events(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Read complete per-PID gold journals, rejecting truncation or missing rows."""
    events: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError("regular producer journal required")
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("incomplete producer journal tail")
        for sequence, line in enumerate(raw.splitlines()):
            item = json.loads(line)
            if not isinstance(item, dict) or item.get("sequence") != sequence:
                raise ValueError("producer journal sequence mismatch")
            pid = item.get("pid")
            if not isinstance(pid, int) or path.name != f"producer-{pid}.jsonl":
                raise ValueError("producer journal PID mismatch")
            if not isinstance(item.get("time_ns"), int) or not isinstance(item.get("kind"), str):
                raise ValueError("producer journal event shape mismatch")
            if (pid, sequence) in seen:
                raise ValueError("duplicate producer event")
            seen.add((pid, sequence))
            events.append(item)
            if len(events) > MAX_EVENTS:
                raise ValueError("cell event bound exceeded")
    return sorted(events, key=lambda item: (item["time_ns"], item["pid"], item["sequence"]))

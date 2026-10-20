"""Local transport between the P6 web dashboard and the in-process ProductService.

The browser cannot import Python, so this adapter owns one ``ProductService`` instance and
forwards the nine contract methods to it unchanged. It adds no product behaviour: argument
names, return values and ``ProductError`` codes are the service's own.

Boundary rules:

- Bound to the loopback interface only. Requests must name the bridge's own loopback host,
  carry the ``X-P6-Bridge`` header and, when the browser sends one, a same-origin ``Origin``.
  Cross-origin pages therefore cannot drive the service (no CORS headers are ever sent).
- Requests are served one at a time, so service calls never overlap.
- Responses carry only what the service returned, or ``{code, safe_message}``. An unexpected
  exception becomes a fixed generic error; no traceback, path or payload is logged or echoed.
- The store root is fixed when the bridge starts and is never accepted from a request.
"""

from __future__ import annotations

import json
import mimetypes
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Final, Protocol

BRIDGE_HEADER: Final = "X-P6-Bridge"
MAX_REQUEST_BYTES: Final = 1 << 20
REQUEST_TIMEOUT_SECONDS: Final = 5.0


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("nonfinite JSON value")


REPORT_MEDIA_TYPES: Final[Mapping[str, str]] = {
    "json": "application/json",
    "markdown": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
}

# Exact parameter names per method, in the service's order. ``mapping`` is the only object.
METHOD_PARAMETERS: Final[Mapping[str, tuple[str, ...]]] = {
    "demo_view": (),
    "preview_import": ("root",),
    "confirm_import": ("preview_id", "mapping"),
    "refresh": ("project_id",),
    "analyze_mock": ("project_id", "snapshot_id", "question"),
    "follow_up": ("result_id", "selection_kind", "selection_id", "question"),
    "view": ("result_id",),
    "export_report": ("result_id", "format"),
    "delete_project": ("project_id",),
}
_OBJECT_PARAMETERS: Final = frozenset({"mapping"})

_STATIC_HEADERS: Final[Mapping[str, str]] = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ProductServiceLike(Protocol):
    """The nine-method surface of ``aletheia_lab.product.service.ProductService``."""

    def demo_view(self) -> dict[str, object]: ...
    def preview_import(self, root: str) -> dict[str, object]: ...
    def confirm_import(self, preview_id: str, mapping: dict[str, object]) -> dict[str, object]: ...
    def refresh(self, project_id: str) -> dict[str, object]: ...
    def analyze_mock(
        self, project_id: str, snapshot_id: str, question: str
    ) -> dict[str, object]: ...
    def follow_up(
        self, result_id: str, selection_kind: str, selection_id: str, question: str
    ) -> dict[str, object]: ...
    def view(self, result_id: str) -> dict[str, object]: ...
    def export_report(self, result_id: str, format: str) -> bytes: ...
    def delete_project(self, project_id: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class BridgeError:
    """A public failure in the service's own ``{code, safe_message}`` shape."""

    code: str
    safe_message: str


@dataclass(frozen=True)
class BridgeResponse:
    status: int
    content_type: str
    body: bytes


INVALID_REQUEST: Final = BridgeError("invalid_request", "The request is not a valid service call.")
UNKNOWN_METHOD: Final = BridgeError("invalid_request", "The requested method is not available.")
SERVICE_FAILURE: Final = BridgeError("service_failure", "The product service failed safely.")
BACKEND_UNAVAILABLE: Final = BridgeError(
    "backend_unavailable", "The product service is not installed in this checkout."
)
FORBIDDEN: Final = BridgeError("forbidden", "The request did not come from this dashboard.")


def _json(status: int, payload: object) -> BridgeResponse:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return BridgeResponse(status, "application/json; charset=utf-8", body)


def error_response(
    error: BridgeError, status: int = HTTPStatus.UNPROCESSABLE_ENTITY
) -> BridgeResponse:
    return _json(status, {"code": error.code, "safe_message": error.safe_message})


def _product_error(exc: BaseException) -> BridgeError | None:
    """Recognise ``ProductError`` without importing the backend at module load."""

    code = getattr(exc, "code", None)
    message = getattr(exc, "safe_message", None)
    if type(exc).__name__ == "ProductError" and isinstance(code, str) and isinstance(message, str):
        return BridgeError(code, message)
    return None


def _arguments(method: str, payload: object) -> dict[str, Any] | None:
    names = METHOD_PARAMETERS[method]
    if not isinstance(payload, dict) or set(payload) != set(names):
        return None
    for name in names:
        value = payload[name]
        expected: type = dict if name in _OBJECT_PARAMETERS else str
        if not isinstance(value, expected):
            return None
    return {name: payload[name] for name in names}


def dispatch(
    service: ProductServiceLike | BridgeError, method: str, payload: object
) -> BridgeResponse:
    """Forward one decoded call to the service and encode its answer."""

    if method not in METHOD_PARAMETERS:
        return error_response(UNKNOWN_METHOD, HTTPStatus.NOT_FOUND)
    arguments = _arguments(method, payload)
    if arguments is None:
        return error_response(INVALID_REQUEST, HTTPStatus.BAD_REQUEST)
    if isinstance(service, BridgeError):
        return error_response(service, HTTPStatus.SERVICE_UNAVAILABLE)
    call: Callable[..., object] = getattr(service, method)
    try:
        value = call(**arguments)
    except Exception as exc:  # noqa: BLE001 - every failure must leave as a safe code
        known = _product_error(exc)
        if known is not None:
            return error_response(known)
        return error_response(SERVICE_FAILURE, HTTPStatus.INTERNAL_SERVER_ERROR)
    if method == "export_report":
        if not isinstance(value, bytes):
            return error_response(SERVICE_FAILURE, HTTPStatus.INTERNAL_SERVER_ERROR)
        return BridgeResponse(HTTPStatus.OK, REPORT_MEDIA_TYPES[arguments["format"]], value)
    try:
        return _json(HTTPStatus.OK, value)
    except (TypeError, ValueError):
        return error_response(SERVICE_FAILURE, HTTPStatus.INTERNAL_SERVER_ERROR)


def load_service(store_root: Path) -> ProductServiceLike | BridgeError:
    """Import the backend lazily so the dashboard still starts, and says why, without it."""

    try:
        from aletheia_lab.product.service import ProductService
    except ImportError:
        return BACKEND_UNAVAILABLE
    try:
        return ProductService(store_root)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a safe code only
        return _product_error(exc) or SERVICE_FAILURE


def _static_file(static_root: Path, request_path: str) -> Path | None:
    relative = request_path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
    if "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        return None
    candidate = (static_root / relative).resolve()
    if not candidate.is_relative_to(static_root) or not candidate.is_file():
        return None
    return candidate


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "aletheia-p6-dashboard"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep request lines and payloads out of logs."""

    def _send(self, response: BridgeResponse, extra: Mapping[str, str] | None = None) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _host_allowed(self) -> bool:
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{host}" for host in allowed}

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if not self._host_allowed():
            self._send(error_response(FORBIDDEN, HTTPStatus.FORBIDDEN))
            return
        static_root = self.server.static_root
        target = None if static_root is None else _static_file(static_root, self.path)
        if target is None:
            self._send(error_response(BridgeError("not_found", "Not found."), HTTPStatus.NOT_FOUND))
            return
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(BridgeResponse(HTTPStatus.OK, media, target.read_bytes()), _STATIC_HEADERS)

    do_HEAD = do_GET

    def _read_body(self) -> bytes | None:
        """Read the whole request body before any answer, so no refusal leaves unread bytes.

        Closing a socket with unread data makes Windows reset the connection, and the client then
        sees ConnectionAbortedError instead of the refusal. The bytes are read, never parsed or
        logged, unless every check below passes. ``None`` means the length is missing, malformed
        or over the limit; that request is refused and the connection closed.
        """

        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self.close_connection = True
            return None
        try:
            body = self.rfile.read(length)
        except OSError:
            self.close_connection = True
            return None
        if len(body) != length:
            self.close_connection = True
            return None
        return body

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        body = self._read_body()
        if not self._host_allowed() or self.headers.get(BRIDGE_HEADER) != "1":
            self._send(error_response(FORBIDDEN, HTTPStatus.FORBIDDEN))
            return
        prefix = "/api/"
        if not self.path.startswith(prefix):
            self._send(error_response(UNKNOWN_METHOD, HTTPStatus.NOT_FOUND))
            return
        media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if media != "application/json" or body is None:
            self._send(error_response(INVALID_REQUEST, HTTPStatus.BAD_REQUEST))
            return
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, ValueError):
            self._send(error_response(INVALID_REQUEST, HTTPStatus.BAD_REQUEST))
            return
        self._send(dispatch(self.server.service, self.path[len(prefix) :], payload))


class DashboardServer(HTTPServer):
    """Single-threaded loopback server: one service call at a time."""

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        return connection, address

    def __init__(
        self,
        service: ProductServiceLike | BridgeError,
        *,
        port: int = 0,
        static_root: Path | None = None,
    ) -> None:
        self.service = service
        self.static_root = None if static_root is None else static_root.resolve()
        super().__init__(("127.0.0.1", port), _Handler)

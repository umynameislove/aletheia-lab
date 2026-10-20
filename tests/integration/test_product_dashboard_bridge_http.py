from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from aletheia_lab.dashboard.bridge import BRIDGE_HEADER, DashboardServer

pytestmark = pytest.mark.integration


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def demo_view(self) -> dict[str, object]:
        self.calls.append("demo_view")
        return {"method": "demo_view"}

    def view(self, result_id: str) -> dict[str, object]:
        self.calls.append("view")
        return {"method": "view"}


@pytest.fixture
def server(tmp_path: Path) -> Iterator[DashboardServer]:
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>P6</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    running = DashboardServer(_RecordingService(), static_root=static)  # type: ignore[arg-type]
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()
        thread.join(timeout=5)


def _request(
    server: DashboardServer,
    method: str,
    path: str,
    body: object = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = {"Host": f"127.0.0.1:{port}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        sent |= {"Content-Type": "application/json", BRIDGE_HEADER: "1"}
    sent |= headers or {}
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_http_call_reaches_the_service(server: DashboardServer) -> None:
    status, headers, body = _request(server, "POST", "/api/view", {"result_id": "r-1"})

    assert status == 200
    assert json.loads(body) == {"method": "view"}
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize(
    "headers",
    [
        {BRIDGE_HEADER: ""},
        {"Host": "evil.example:80"},
        {"Origin": "http://evil.example"},
    ],
)
def test_cross_site_and_rebinding_requests_are_refused(
    server: DashboardServer, headers: dict[str, str]
) -> None:
    status, _, body = _request(server, "POST", "/api/view", {"result_id": "r-1"}, headers)

    assert status == 403
    assert json.loads(body)["code"] == "forbidden"
    assert server.service.calls == []  # type: ignore[union-attr]


def test_same_origin_requests_are_accepted(server: DashboardServer) -> None:
    port = server.server_address[1]
    origin = {"Origin": f"http://127.0.0.1:{port}"}

    status, _, _ = _request(server, "POST", "/api/demo_view", {}, origin)

    assert status == 200


def test_static_files_are_served_inside_the_build_only(server: DashboardServer) -> None:
    status, headers, body = _request(server, "GET", "/")
    assert status == 200
    assert body.startswith(b"<!doctype html>")
    assert "default-src 'self'" in headers["Content-Security-Policy"]

    assert _request(server, "GET", "/assets/app.js")[0] == 200
    for path in ("/../outside.txt", "/assets/../../outside.txt", "/%2e%2e/outside.txt"):
        assert _request(server, "GET", path)[0] == 404


def test_oversized_or_non_json_bodies_are_refused(server: DashboardServer) -> None:
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/api/view",
        body=b"result_id=r",
        headers={
            "Host": f"127.0.0.1:{port}",
            BRIDGE_HEADER: "1",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert connection.getresponse().status == 400
    connection.close()


@pytest.mark.parametrize(
    "headers",
    [
        {BRIDGE_HEADER: ""},
        {"Host": "evil.example:80"},
        {"Origin": "http://evil.example"},
    ],
)
def test_refusals_with_a_large_body_still_reach_the_client(
    server: DashboardServer, headers: dict[str, str]
) -> None:
    # The bridge reads the whole body before refusing; a refusal sent over unread bytes made
    # Windows reset the connection (WinError 10053) instead of delivering the 403.
    large = {"result_id": "r" * (512 * 1024)}

    status, _, body = _request(server, "POST", "/api/view", large, headers)

    assert status == 403
    assert json.loads(body) == {
        "code": "forbidden",
        "safe_message": "The request did not come from this dashboard.",
    }
    assert server.service.calls == []  # type: ignore[union-attr]


def test_an_over_limit_body_is_refused_without_being_read(server: DashboardServer) -> None:
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest("POST", "/api/view", skip_host=True)
    for name, value in {
        "Host": f"127.0.0.1:{port}",
        BRIDGE_HEADER: "1",
        "Content-Type": "application/json",
        "Content-Length": str(2 * 1024 * 1024),
    }.items():
        connection.putheader(name, value)
    connection.endheaders()

    response = connection.getresponse()

    assert response.status == 400
    assert json.loads(response.read())["code"] == "invalid_request"
    connection.close()


def test_incomplete_body_is_bounded_and_next_request_can_proceed(
    server: DashboardServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aletheia_lab.dashboard import bridge

    monkeypatch.setattr(bridge, "REQUEST_TIMEOUT_SECONDS", 0.1)
    port = server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(
            (
                f"POST /api/view HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                "X-P6-Bridge: 1\r\nContent-Type: application/json\r\n"
                "Content-Length: 2\r\n\r\n{"
            ).encode("ascii")
        )
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 400
        assert json.loads(response.read())["code"] == "invalid_request"

    assert server.service.calls == []  # type: ignore[union-attr]
    assert _request(server, "POST", "/api/demo_view", {})[0] == 200


@pytest.mark.parametrize(
    "body",
    (b'{"result_id":"a","result_id":"b"}', b'{"result_id":NaN}'),
)
def test_ambiguous_or_nonfinite_json_never_reaches_service(
    server: DashboardServer, body: bytes
) -> None:
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "POST",
        "/api/view",
        body,
        {"Content-Type": "application/json", BRIDGE_HEADER: "1"},
    )
    response = connection.getresponse()
    assert response.status == 400
    assert json.loads(response.read())["code"] == "invalid_request"
    connection.close()
    assert server.service.calls == []  # type: ignore[union-attr]

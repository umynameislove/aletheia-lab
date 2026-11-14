"""Own-port verified HTTPS checks; no database, identity provider or external traffic."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta

import h11
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted.header_protocol import header_deadline_protocol
from aletheia_lab.hosted.request_boundary import (
    BodyLimits,
    RequestRejected,
    read_json_body,
)
from aletheia_lab.hosted.runtime import api_server_options

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("K02_TEST_HTTPS") != "1",
    reason="Explicit own-loopback HTTPS test opt-in required")]

HEADER_SECONDS = 0.3


def _certificate(root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "K02 synthetic header deadline")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
    cert, private = root / "synthetic-cert.pem", root / "synthetic-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    return cert, private


@pytest.fixture
def https_server(tmp_path):
    assert (uvicorn.__version__, h11.__version__) == ("0.48.0", "0.16.0")
    cert, private = _certificate(tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    paths, failures = [], []

    async def app(scope, receive, send):
        paths.append(scope["path"])
        body, status = b"ok", 200
        if scope["path"] == "/slow-body":
            try:
                await read_json_body(receive, scope["headers"], BodyLimits(1024, 0.6, 0.9, 20))
            except RequestRejected as error:
                body, status = json.dumps({"code": error.code}).encode(), error.status
        elif scope["path"] == "/slow-response":
            await asyncio.sleep(0.5)
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    settings = _settings(tmp_path, port=port, header_timeout_seconds=HEADER_SECONDS,
        keep_alive_seconds=1, graceful_shutdown_seconds=1, h11_max_incomplete_event_bytes=1024)
    options = api_server_options(settings)
    assert options["http"]._header_timeout_seconds == HEADER_SECONDS
    options.update(lifespan="off", log_config=None, log_level="critical",
                   ssl_certfile=str(cert), ssl_keyfile=str(private))
    config = uvicorn.Config(app, **options)
    server = uvicorn.Server(config)

    def run():
        try:
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(server.serve(sockets=[listener]))
        except (OSError, RuntimeError, ValueError) as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=run, daemon=True, name="k02-owned-header-https")
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and not failures
        context = ssl.create_default_context(cafile=str(cert))

        def connect():
            raw = socket.create_connection(("127.0.0.1", port), timeout=3)
            return context.wrap_socket(raw, server_hostname="127.0.0.1")

        yield connect, paths, server, port
    finally:
        server.should_exit = True
        thread.join(5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(5)
        listener.close()
        assert not thread.is_alive() and not failures
        assert not server.server_state.connections and not server.server_state.tasks


def _read(connection):
    response = http.client.HTTPResponse(connection)
    response.begin()
    return response.status, dict(response.getheaders()), response.read()


def _healthy(connect):
    with connect() as connection:
        connection.sendall(b"GET /healthy HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        assert _read(connection)[0] == 200


@pytest.mark.parametrize("prefix", [b"", b"GET /never-line", b"GET /never-header HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Private: synthetic"])
def test_incomplete_head_expires_before_asgi_and_other_connections_work(https_server, prefix):
    connect, paths, _, _ = https_server
    with connect() as connection:
        began = time.monotonic()
        if prefix:
            connection.sendall(prefix)
        _healthy(connect)
        status, headers, body = _read(connection)
        assert status == 408 and headers["cache-control"] == "no-store"
        assert b"synthetic" not in body and b"never" not in body
        assert connection.recv(1) == b""
        assert time.monotonic() - began < 2
    assert paths == ["/healthy"]


def test_header_trickle_does_not_extend_total_deadline(https_server):
    connect, paths, _, _ = https_server
    with connect() as connection:
        began = time.monotonic()
        connection.sendall(b"GET /trickle HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: ")
        for _ in range(4):
            time.sleep(0.06)
            connection.sendall(b"x")
        assert _read(connection)[0] == 408
        assert time.monotonic() - began < 0.8
    assert paths == []


def test_idle_keepalive_survives_header_budget_then_partial_next_head_expires(https_server):
    connect, paths, _, _ = https_server
    with connect() as connection:
        connection.sendall(b"GET /first HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert _read(connection)[0] == 200
        time.sleep(0.45)
        connection.sendall(b"GET /second HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert _read(connection)[0] == 200
        connection.sendall(b"GET /never-third HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: ")
        _healthy(connect)
        assert _read(connection)[0] == 408
    assert paths == ["/first", "/second", "/healthy"]


def test_complete_head_cancels_deadline_for_response_and_existing_body_timeout(https_server):
    connect, paths, _, _ = https_server
    with connect() as connection:
        connection.sendall(b"GET /slow-response HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        assert _read(connection)[0] == 200
    with connect() as connection:
        began = time.monotonic()
        connection.sendall(b"POST /slow-body HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\nContent-Length: 50\r\nConnection: close\r\n\r\n{")
        _healthy(connect)
        status, _, body = _read(connection)
        assert status == 408 and json.loads(body)["code"] == "body_timeout"
        assert time.monotonic() - began >= 0.45
    assert paths == ["/slow-response", "/slow-body", "/healthy"]


def test_partial_pipelined_head_expires_after_active_response_completes(https_server):
    connect, paths, _, _ = https_server
    with connect() as connection:
        connection.sendall(b"GET /slow-response HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            b"GET /never-pipelined HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: ")
        assert _read(connection)[0] == 200
        assert _read(connection)[0] == 408
    assert paths == ["/slow-response"]


@pytest.mark.parametrize("bad", [
    b"GET /bad HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
    b"GET /bad HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Large: " + b"x" * 2048,
])
def test_native_h11_framing_and_incomplete_size_rejection_remain(https_server, bad):
    connect, paths, _, _ = https_server
    with connect() as connection:
        connection.sendall(bad)
        assert _read(connection)[0] == 400
    assert paths == []


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_header_budget_rejected(value):
    with pytest.raises(ValueError, match="finite positive"):
        header_deadline_protocol(value)

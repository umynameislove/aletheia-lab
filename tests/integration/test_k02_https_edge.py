"""Opt-in local HTTPS transport smoke; synthetic backend, no IdP or database."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.request_boundary import (
    BodyLimits,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from aletheia_lab.hosted.runtime import api_server_options
from aletheia_lab.hosted.settings import RuntimeSettings


def _certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "K02 synthetic local transport")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()))
    certificate_path, key_path = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    return certificate_path, key_path


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class _SyntheticBackend:
    async def is_ready(self):
        return True

    async def load_session(self, cookie):
        raise AssertionError("Transport test must not authenticate")

    async def capabilities(self, session, workspace_id):
        raise AssertionError("Transport test must not authorize")


def _probe_routes(fallback):
    async def probe(scope, receive, send):
        if scope.get("path") == "/deadline":
            await asyncio.Event().wait()
        if scope.get("path") != "/probe":
            await fallback(scope, receive, send)
            return
        status = 200
        value = {"client": scope["client"][0], "scheme": scope["scheme"]}
        try:
            if scope.get("method") == "POST":
                body = await read_json_body(receive, scope["headers"], BodyLimits(1000, 1.0, 2.0, 20))
                decode_json_model(body, _Input, maximum_bytes=1000)
        except RequestRejected as error:
            status, value = error.status, {"code": error.code}
        encoded = json.dumps(value).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"cache-control", b"no-store"),
            (b"content-length", str(len(encoded)).encode("ascii")),
        ]})
        await send({"type": "http.response.body", "body": encoded})

    return probe


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("K02_TEST_HTTPS") != "1", reason="Explicit local HTTPS test opt-in required")
def test_real_https_host_forwarding_json_and_slow_body(tmp_path):
    import httpx
    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    settings = RuntimeSettings.model_validate({
        "environment": "local", "public_origin": origin,
        "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                     "redirect_uri": origin + "/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        "api": {"port": port, "graceful_shutdown_seconds": 1, "request_timeout_seconds": 2.0,
                "maximum_header_bytes": 1024, "maximum_header_count": 12},
    })
    certificate_path, key_path = _certificate(tmp_path)
    closed = []

    @asynccontextmanager
    async def resources():
        try:
            yield _SyntheticBackend()
        finally:
            closed.append(True)

    app = ProcessApplication(settings, resources, routes=_probe_routes)
    options = api_server_options(settings)
    options.update(ssl_certfile=str(certificate_path), ssl_keyfile=str(key_path),
                   log_config=None, log_level="critical")
    server = uvicorn.Server(uvicorn.Config(app, **options))
    failures = []

    def serve():
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except BaseException as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=serve, daemon=True, name="k02-local-https")
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and not failures, "Local HTTPS test server failed to start"
        context = ssl.create_default_context(cafile=str(certificate_path))
        with httpx.Client(base_url=origin, verify=context, timeout=5, trust_env=False) as client:
            health = client.get("/health/ready")
            assert health.status_code == 200
            assert health.headers["cache-control"] == "no-store"
            oversized = client.get("/probe", headers={"X-Private": "synthetic-private" * 64})
            assert oversized.status_code == 431 and oversized.json()["code"] == "headers_too_large"
            assert "synthetic-private" not in oversized.text
            duplicates = client.get("/probe", headers=[("X-Repeat", str(i)) for i in range(13)])
            assert duplicates.status_code == 431 and duplicates.json()["code"] == "headers_too_large"
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready", headers={"Host": "forged.invalid"}).status_code == 400
            forwarded = client.get("/probe", headers={"Forwarded": "for=203.0.113.7;proto=http",
                "X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "http", "X-Real-IP": "203.0.113.7"})
            assert forwarded.json() == {"client": "127.0.0.1", "scheme": "https"}
            deadline_response = client.get("/deadline")
            assert deadline_response.status_code == 503
            assert deadline_response.json()["code"] == "service_unavailable"
            assert deadline_response.headers["cache-control"] == "no-store"
            invalid = client.post("/probe", content=b'{"value":"synthetic-private","value":"other"}',
                                  headers={"Content-Type": "application/json"})
            assert invalid.status_code == 400 and invalid.json()["code"] == "invalid_json"
            assert "synthetic-private" not in invalid.text
            with context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5),
                                     server_hostname="127.0.0.1") as slow:
                slow.settimeout(5)
                slow.sendall((f"POST /probe HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    "Content-Type: application/json\r\nContent-Length: 50\r\nConnection: close\r\n\r\n{").encode("ascii"))
                assert client.get("/health/live").status_code == 200
                response = b""
                while part := slow.recv(4096):
                    response += part
                assert response.startswith(b"HTTP/1.1 408") and b"body_timeout" in response
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "Local HTTPS test server did not stop"
    assert closed == [True] and failures == []

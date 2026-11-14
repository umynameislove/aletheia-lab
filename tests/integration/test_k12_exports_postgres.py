"""Real restricted PG exports, verified HTTPS bytes and slow-reader backpressure."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import socket
import ssl
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress

import httpx
import pytest
import uvicorn
from pypdf import PdfReader
from test_k02_durable_exports_postgres import _expired_copy
from test_k02_https_edge import _certificate
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run
from test_k06_follow_up_http_postgres import _problem

from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K12_TEST_DATABASE_URL"),
    reason="Explicit owned K12 PostgreSQL required")]
ORIGIN = "https://app.example.invalid"


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K12_TEST_DATABASE_URL"])


async def _fixture_identity(case):
    async with case.api.connection() as conn:
        schema, api_role = await (await conn.execute("SELECT current_schema(),current_user")).fetchone()
    assert api_role.startswith("k04_api_") and schema.startswith("k04_runtime_")
    return {"schema": schema, "api_role": api_role,
            "worker_role": "k04_worker_" + api_role.removeprefix("k04_api_")}


async def _verify_fixture_removed(identity, tmp_path):
    import psycopg

    # Independent owner connection after fixture pools/namespace have closed.
    async with await psycopg.AsyncConnection.connect(os.environ["K12_TEST_DATABASE_URL"], autocommit=True) as conn:
        assert await (await conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s",
            (identity["schema"],))).fetchone() == (0,)
        assert await (await conn.execute("SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
            ([identity["api_role"], identity["worker_role"]],))).fetchone() == (0,)
    (tmp_path / "independent-cleanup.json").write_text(json.dumps({**identity,
        "schema_remaining": 0, "roles_remaining": 0, "status": "passed"}, indent=2) + "\n")


def test_real_three_format_export_parity_replay_expiry_and_authenticated_ranges(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            identity = await _fixture_identity(case)
            assert await case.app._access.exports.pdf_ready()
            base = f"/v1/workspaces/{case.workspace}"
            endpoint = base + f"/results/{case.child.record_id}/exports"
            observations = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client:
                for fmt, media, extension in (("json", "application/json", "json"),
                        ("markdown", "text/markdown; charset=utf-8", "md"), ("pdf", "application/pdf", "pdf")):
                    headers = {**case.headers["analyst"], "idempotency-key": "k12-real-format-" + fmt + "-0001"}
                    response = await client.post(endpoint, headers=headers, json={"format": fmt})
                    assert response.status_code == 201, response.text
                    item = ExportDescriptor.model_validate_json(response.content)
                    replay = await client.post(endpoint, headers=headers, json={"format": fmt})
                    assert replay.status_code == 201 and replay.json() == response.json()
                    full = await client.get(item.download_url, headers=case.headers["analyst"])
                    assert full.status_code == 200 and full.headers["content-type"] == media
                    assert full.content == render_product_report(case.child.view, fmt)
                    assert len(full.content) == item.byte_size and hashlib.sha256(full.content).hexdigest() == item.sha256
                    assert full.headers["content-disposition"].endswith('.' + extension + '"')
                    assert full.headers["cache-control"] == "no-store" and full.headers["x-content-type-options"] == "nosniff"
                    part = await client.get(item.download_url, headers={**case.headers["analyst"], "range": "bytes=2-31"})
                    assert part.status_code == 206 and part.content == full.content[2:32]
                    assert part.headers["content-range"] == f"bytes 2-31/{item.byte_size}"
                    head = await client.head(item.download_url, headers=case.headers["analyst"])
                    assert head.status_code == 200 and not head.content and int(head.headers["content-length"]) == item.byte_size
                    if fmt == "pdf":
                        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(full.content)).pages)
                        assert case.child.record_id in text and case.snapshot.snapshot_id in text
                        expired = await _expired_copy(case, item, key="k12-expired-pdf-0001")
                        _problem(await client.get(expired, headers=case.headers["analyst"]), 410, "export_expired")
                        await PostgresProjectAccess(case.api).set_grant(credential_digest=case.digests["owner_a"],
                            workspace_id=case.workspace, project_id=case.initial.project_id, user_id="analyst",
                            actions=["read"], expected_revision=1)
                        denied = await client.get(item.download_url, headers={**case.headers["analyst"], "range": "bytes=99999999-"})
                        _problem(denied, 404, "not_found")
                        assert "content-range" not in denied.headers and "etag" not in denied.headers
                    observations.append({"format": fmt, "bytes": item.byte_size, "sha256": item.sha256,
                                         "range_bytes_verified": 30, "same_key_same_artifact": True})
                async with case.owner.connection() as conn:
                    assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s",
                        (case.workspace, case.initial.project_id))).fetchone() == (3,)
                    assert await (await conn.execute("SELECT count(*) FROM k02_exports")).fetchone() == (4,)
            (tmp_path / "three-formats.json").write_text(json.dumps(observations, indent=2) + "\n")
        await _verify_fixture_removed(identity, tmp_path)
    _run(scenario())


@asynccontextmanager
async def _https(case, tmp_path, delivered):
    async with AsyncExitStack() as cleanup:
        certificate, key = _certificate(tmp_path)
        cleanup.callback(key.unlink, missing_ok=True)
        key.chmod(0o600)
        listener = cleanup.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(8)

        async def app(scope, receive, send):
            async def observed(message):
                await send(message)
                if scope.get("path", "").endswith("/download") and message.get("type") == "http.response.body":
                    delivered.append((time.perf_counter(), len(message.get("body", b"")), message.get("more_body", False)))
            await case.app(scope, receive, observed)

        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", ws="none", access_log=False,
            log_level="critical", ssl_certfile=str(certificate), ssl_keyfile=str(key)))
        task = asyncio.create_task(server.serve(sockets=[listener]))

        async def stop():
            server.should_exit = True
            try:
                await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        cleanup.push_async_callback(stop)
        async with asyncio.timeout(10):
            while not server.started:
                assert not task.done()
                await asyncio.sleep(0.01)
        yield f"https://127.0.0.1:{port}", ssl.create_default_context(cafile=str(certificate)), port


def test_verified_https_large_download_slow_client_frees_single_pg_connection_and_range_retry(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            identity = await _fixture_identity(case)
            await case.api.resize(min_size=1, max_size=1)
            await case.api.wait(timeout=10)
            # A distribution fixture, not a claim that a 100k-row PDF was rendered:
            # whitespace makes a large valid JSON file with exactly the same immutable view.
            canonical = render_product_report(case.child.view, "json")
            payload = canonical + b" " * (8 * 1024 * 1024)
            assert json.loads(payload) == case.child.view.model_dump(mode="json")
            item = await case.app._access.exports.publish(case.digests["owner_a"], case.workspace,
                case.initial.project_id, case.child.record_id, "json", payload, None)
            path = f"/v1/workspaces/{case.workspace}/exports/{item.export_id}/download"
            result_path = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}"
            delivered = []
            async with _https(case, tmp_path, delivered) as (origin, tls, port):
                headers = {**case.headers["owner_a"], "host": "app.example.invalid"}
                async with httpx.AsyncClient(base_url=origin, headers=headers, verify=tls, trust_env=False, timeout=10) as client:
                    started, first, last = time.perf_counter(), None, None
                    digest, count = hashlib.sha256(), 0
                    async with client.stream("GET", path) as response:
                        assert response.status_code == 200
                        async for block in response.aiter_bytes():
                            first = first or time.perf_counter()
                            digest.update(block)
                            count += len(block)
                        last = time.perf_counter()
                    assert count == len(payload) and digest.hexdigest() == item.descriptor.sha256
                    delivered.clear()
                    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=tls, server_hostname="127.0.0.1", limit=1024)
                    try:
                        writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                        request = (f"GET {path} HTTP/1.1\r\nHost: app.example.invalid\r\n"
                                   f"Cookie: {case.headers['owner_a']['cookie']}\r\nConnection: close\r\n\r\n")
                        writer.write(request.encode())
                        await writer.drain()
                        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                        assert b"200 OK" in head
                        assert await asyncio.wait_for(reader.readexactly(1), 5) == payload[:1]
                        read_started = time.perf_counter()
                        metadata = await client.get(result_path)
                        read_seconds = time.perf_counter() - read_started
                        assert metadata.status_code == 200 and metadata.json()["result_id"] == case.child.record_id
                        assert read_seconds < 1
                        await asyncio.sleep(1.4)
                        assert delivered and not any(not more for _, _, more in delivered)
                        assert sum(size for _, size, _ in delivered) < len(payload)
                        async with case.api.connection(timeout=1) as conn:
                            assert await (await conn.execute("SELECT 1")).fetchone() == (1,)
                    finally:
                        writer.transport.abort()
                        with suppress(ssl.SSLError, ConnectionError, TimeoutError):
                            await asyncio.wait_for(writer.wait_closed(), 2)
                    retry = await client.get(path, headers={"range": "bytes=2-31"})
                    assert retry.status_code == 206 and retry.content == payload[2:32]
            assert first is not None and last is not None
            assert not (tmp_path / "test-key.pem").exists()
            (tmp_path / "network.json").write_text(json.dumps({"bytes": count, "sha256": digest.hexdigest(),
                "ttfb_seconds": first - started, "last_byte_seconds": last - started,
                "throughput_bytes_per_second": count / (last - started), "metadata_while_slow_seconds": read_seconds,
                "api_pool_max": 1, "slow_client_false_completion": False, "retry_range_passed": True,
                "tls_private_key_removed": not (tmp_path / "test-key.pem").exists()}, indent=2) + "\n")
        await _verify_fixture_removed(identity, tmp_path)
    _run(scenario())


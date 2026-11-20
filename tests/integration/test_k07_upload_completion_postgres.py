"""Completion avoids a second client payload write and preserves sealed-byte truth."""
from __future__ import annotations

import asyncio
import hashlib
import os
import socket
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k06_transactions_postgres import _database as _transaction_database
from test_k07_upload_sizes_postgres import _physical_bundle
from test_k07_upload_staging_postgres import _database, _digest, _manifest, _run

from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.transactions import TransactionUnavailable, run_transaction
from aletheia_lab.hosted.upload_staging import UploadRejected

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit owned K07 PostgreSQL required")]


def _sources(pool):
    return PostgresSources(pool, maximum_upload_bytes=2 * 1024 * 1024,
        maximum_expanded_bytes=4 * 1024 * 1024, maximum_entries=100)


async def _stage(sources, payload):
    manifest, parts = _manifest(payload, part_size=128 * 1024)
    receipt = await sources.create_upload(credential_digest=_digest(), workspace_id="a",
        request_key=SecretStr("synthetic-server-assembly-0001"), manifest=manifest)
    for index in reversed(range(len(parts))):
        await sources.put_upload_part(credential_digest=_digest(), workspace_id="a",
            upload_id=receipt.upload_id, part_number=index + 1, payload=parts[index])
    return receipt


def test_completion_publishes_verified_bytes_without_binding_the_bundle_again():
    async def run():
        async with _database() as (pool, _):
            await pool.resize(min_size=1, max_size=2)
            sources = _sources(pool)
            payload = _physical_bundle(1024 * 1024)
            staged = await _stage(sources, payload)
            bound_binary_bytes = 0

            class ObservedPool:
                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as actual:
                        class Connection:
                            autocommit = False

                            async def execute(self, query, params=(), **kwargs):
                                nonlocal bound_binary_bytes
                                bound_binary_bytes += sum(len(value) for value in params
                                    if isinstance(value, bytes | bytearray | memoryview))
                                return await actual.execute(query, params, **kwargs)
                        yield Connection()

            completed = await _sources(ObservedPool()).complete_upload(credential_digest=_digest(),
                workspace_id="a", upload_id=staged.upload_id)
            assert completed.state == "ready" and completed.received_bytes == len(payload)
            assert bound_binary_bytes == 0, "The verified staged bundle must not be re-uploaded at completion"
            saved = await sources.read(credential_digest=_digest(), workspace_id="a", source_id=completed.source_id)
            assert saved == payload and hashlib.sha256(saved).hexdigest() == completed.sha256
            assert await sources.complete_upload(credential_digest=_digest(), workspace_id="a",
                upload_id=staged.upload_id) == completed
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k07_upload_parts")).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("changed", ["bytes", "missing"])
def test_changed_staged_bytes_after_inspection_never_publish_as_the_validated_source(monkeypatch, changed):
    async def run():
        async with _database() as (pool, _):
            await pool.resize(min_size=1, max_size=2)
            sources = _sources(pool)
            staged = await _stage(sources, _physical_bundle(1024 * 1024))
            original = sources._inspect

            async def inspect_then_corrupt(payload):
                result = await original(payload)
                async with pool.connection() as conn:
                    # Preserve stored length/hash metadata. Final assembly must verify
                    # actual bytes again, rather than trust a now-stale Python read.
                    if changed == "bytes":
                        await conn.execute("UPDATE k07_upload_parts SET payload=set_byte(payload,0,"
                            "get_byte(payload,0) # 1) WHERE workspace_id='a' AND upload_id=%s AND part_number=2",
                            (staged.upload_id,))
                    else:
                        await conn.execute("DELETE FROM k07_upload_parts WHERE workspace_id='a' "
                            "AND upload_id=%s AND part_number=2", (staged.upload_id,))
                return result

            monkeypatch.setattr(sources, "_inspect", inspect_then_corrupt)
            with pytest.raises(UploadRejected) as rejected:
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=staged.upload_id)
            assert rejected.value.code == "upload_integrity_failed"
            status = await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=staged.upload_id)
            assert status.state == "cancelled" and status.source_id is None
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("external_cancel", [False, True])
def test_real_driver_flush_cancel_rolls_back_without_replay_and_discards_connection(monkeypatch, external_cancel):
    async def run():
        from psycopg import waiting

        async with _transaction_database() as pool:
            original = waiting.wait_async
            attempts, first_state = 0, None
            entered, connections, backend_pids = asyncio.Event(), [], []

            async def operation(conn):
                nonlocal attempts, first_state
                attempts += 1
                connections.append(conn)
                backend_pids.append(conn.info.backend_pid)
                await conn.execute("UPDATE counters SET value=99 WHERE id=1")
                await conn.execute("INSERT INTO effects VALUES('cancelled-large-flush')")
                raw = socket.socket(fileno=conn.pgconn.socket)
                try:
                    raw.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                finally:
                    raw.detach()
                injected = False

                async def controlled(gen, fileno, interval=0.0, timeout=None):
                    nonlocal injected, first_state
                    if not injected:
                        injected = True
                        try:
                            first_state = next(gen)
                        except StopIteration as done:
                            return done.value
                        assert first_state == 3  # actual libpq pending-send WAIT_RW
                        entered.set()
                        await asyncio.Event().wait()
                    return await original(gen, fileno, interval=interval, timeout=timeout)

                monkeypatch.setattr(waiting, "wait_async", controlled)
                try:
                    await conn.execute("SELECT octet_length(%b::bytea)", (b"x" * (16 * 1024 * 1024),))
                finally:
                    monkeypatch.setattr(waiting, "wait_async", original)

            if external_cancel:
                pending = asyncio.create_task(run_transaction(pool, operation, timeout_seconds=5))
                await entered.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert pending.cancelled()
            else:
                with pytest.raises(TransactionUnavailable):
                    await run_transaction(pool, operation, timeout_seconds=0.2)
            assert attempts == 1 and first_state == 3
            assert len(connections) == 1 and connections[0].closed
            async with pool.connection() as conn:
                assert conn.info.backend_pid != backend_pids[0]
                assert await (await conn.execute("SELECT value FROM counters WHERE id=1")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM effects")).fetchone() == (0,)
                assert await (await conn.execute("SELECT 1")).fetchone() == (1,)
    _run(run())

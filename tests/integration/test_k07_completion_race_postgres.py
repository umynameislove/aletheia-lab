"""A concurrent winner can finish between admission and locked assembly."""
from __future__ import annotations

import asyncio
import os

import pytest
from test_k07_upload_staging_postgres import (
    _bundle,
    _create,
    _database,
    _digest,
    _parts,
    _run,
    _sources,
)

from aletheia_lab.hosted import postgres_sources as module
from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.resource_admission import ByteAdmission
from aletheia_lab.hosted.upload_staging import UploadRejected

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K07_TEST_DATABASE_URL"), reason="Explicit owned K07 PostgreSQL required",
)]


@pytest.mark.parametrize("waiters", [2, 7], ids=["three-callers", "eight-callers"])
@pytest.mark.parametrize("resolution", ["ready", "cancelled", "expired", "membership", "session"])
def test_completion_reconciles_winner_at_assembly_without_bypassing_authority(
    monkeypatch, record_property, waiters, resolution,
):
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate_audit(conn, schema)
            payload = _bundle()
            sources = _sources(pool, audit=True)
            staged, parts = await _create(sources, payload)
            await _parts(sources, staged, parts)
            weight = 2 * len(payload)
            budget = ByteAdmission(weight * (waiters + 1))
            monkeypatch.setattr(module, "_COMPLETION_BYTES", budget)
            entered, release = asyncio.Event(), asyncio.Event()
            blocked = 0

            async def unexpected_inspection(payload):
                pytest.fail("A terminal or revoked upload must not be inspected again")

            tasks = []
            for _ in range(waiters):
                loser = _sources(pool, audit=True)
                original = loser._assemble_upload

                async def paused(digest, workspace, upload, assemble=original):
                    nonlocal blocked
                    blocked += 1
                    if blocked == waiters:
                        entered.set()
                    await release.wait()
                    return await assemble(digest, workspace, upload)

                monkeypatch.setattr(loser, "_assemble_upload", paused)
                monkeypatch.setattr(loser, "_inspect", unexpected_inspection)
                tasks.append(asyncio.create_task(loser.complete_upload(
                    credential_digest=_digest(), workspace_id="a", upload_id=staged.upload_id,
                )))
            ready = None
            try:
                await asyncio.wait_for(entered.wait(), 10)
                assert budget.snapshot().active == waiters * weight
                status = await sources.upload_status(credential_digest=_digest(), workspace_id="a",
                                                     upload_id=staged.upload_id)
                assert status.state == "active" and status.received_bytes == len(payload)
                if resolution in ("ready", "membership", "session"):
                    ready = await sources.complete_upload(credential_digest=_digest(), workspace_id="a",
                                                          upload_id=staged.upload_id)
                    assert ready.state == "ready"
                    assert await sources.read(credential_digest=_digest(), workspace_id="a",
                                              source_id=ready.source_id) == payload
                elif resolution == "cancelled":
                    await sources.cancel_upload(credential_digest=_digest(), workspace_id="a",
                                                upload_id=staged.upload_id)
                async with pool.connection() as conn:
                    if resolution == "expired":
                        await conn.execute("UPDATE k07_uploads SET created_at=clock_timestamp()-interval '2 days', "
                                           "expires_at=clock_timestamp()-interval '1 second' "
                                           "WHERE upload_id=%s", (staged.upload_id,))
                    elif resolution == "membership":
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                           "WHERE workspace_id='a' AND user_id='analyst'")
                    elif resolution == "session":
                        await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id='ctx_analyst'")
                    resolved_at = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
            finally:
                release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if resolution == "ready":
                assert results == [ready] * waiters
                outcome = "identical_ready_receipts"
            elif resolution in ("membership", "session"):
                assert all(isinstance(result, AuthorizationDenied) for result in results), repr(results)
                outcome = "authorization_denied"
            else:
                outcome = "upload_expired" if resolution == "expired" else "upload_conflict"
                assert all(isinstance(result, UploadRejected) and result.code == outcome
                           for result in results), repr(results)
            assert budget.snapshot().active == 0
            expected = int(ready is not None)
            assert budget.snapshot().peak_active == (waiters + expected) * weight
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (expected,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                                                 "WHERE action='source.put'")).fetchone() == (expected,)
                if expected:
                    assert await (await conn.execute("SELECT count(*) FROM k07_upload_parts")).fetchone() == (0,)
                finished_at = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
            for key, value in {
                "upload_id": staged.upload_id, "resolution": resolution, "callers": waiters + 1,
                "payload_bytes": len(payload), "parts": len(parts), "outcome": outcome,
                "source_count": expected, "source_put_audit_count": expected,
                "reservation_peak_bytes": budget.snapshot().peak_active, "reservation_final_bytes": 0,
                "resolved_db_at": resolved_at.isoformat(), "settled_db_at": finished_at.isoformat(),
            }.items():
                record_property(key, value)
    _run(run())

"""Bounded native faults while another tenant reads verified source bytes.

This is a small repository-boundary workload, not the primary HTTP campaign,
container failover, browser recovery, or a production fairness/SLO measurement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial

from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K17_TEST_DATABASE_URL"),
        reason="Explicit private K17 PostgreSQL URL required",
    ),
]


@pytest.mark.parametrize("fault", ["row_lock", "pool_exhaustion"])
def test_fault_is_bounded_and_replay_has_one_effect_while_foreign_tenant_reads(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K17_TEST_DATABASE_URL"])

    async def run():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            other = await case.sources.put(
                credential_digest=case.b,
                workspace_id="b",
                request_key=SecretStr("synthetic-k17-fault-background"),
                payload=case.payload,
            )
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
            application_name = "k17-fault-" + uuid4().hex
            target = make_conninfo(
                os.environ["K17_TEST_DATABASE_URL"],
                application_name=application_name,
                options=f"-csearch_path={schema} -clock_timeout=250ms -cstatement_timeout=2s",
            )
            samples = []
            stop = asyncio.Event()
            reader_started = asyncio.Event()

            async def background():
                while not stop.is_set():
                    start = time.monotonic()
                    data = await case.sources.read(
                        credential_digest=case.b, workspace_id="b", source_id=other.source_id
                    )
                    assert data == case.payload and hashlib.sha256(data).hexdigest() == other.sha256
                    samples.append((start, time.monotonic() - start))
                    reader_started.set()
                    await asyncio.sleep(0.005)

            async with AsyncConnectionPool(
                target, min_size=1, max_size=1, max_waiting=2, timeout=0.25, open=False
            ) as affected:
                await affected.wait(timeout=5)
                mutations = PostgresProjectMutations(
                    affected, PostgresCatalog(affected, case.objects)
                )
                arguments = dict(
                    credential_digest=case.a,
                    workspace_id="a",
                    project_id=initial.project_id,
                    expected_revision=2,
                )
                reader = asyncio.create_task(background())
                task = None
                try:
                    await asyncio.wait_for(reader_started.wait(), 3)
                    holder_pool = affected if fault == "pool_exhaustion" else case.pool
                    async with holder_pool.connection() as holder:
                        if fault == "row_lock":
                            await holder.execute(
                                "SELECT project_id FROM k05_projects WHERE workspace_id='a' "
                                "AND project_id=%s FOR UPDATE",
                                (initial.project_id,),
                            )
                        fault_start = time.monotonic()
                        task = asyncio.create_task(mutations.delete(**arguments))
                        actual_wait_observed = False
                        while not task.done():
                            assert time.monotonic() - fault_start < 2, "Fault exceeded its finite observation budget"
                            if fault == "pool_exhaustion":
                                actual_wait_observed |= affected.get_stats().get("requests_waiting", 0) > 0
                            else:
                                async with case.pool.connection() as observer:
                                    row = await (
                                        await observer.execute(
                                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                            "WHERE application_name=%s AND wait_event_type='Lock')",
                                            (application_name,),
                                        )
                                    ).fetchone()
                                    actual_wait_observed |= row == (True,)
                            await asyncio.sleep(0.01)
                        expected = PoolTimeout if fault == "pool_exhaustion" else psycopg.errors.LockNotAvailable
                        with pytest.raises(expected):
                            await task
                        fault_end = time.monotonic()
                        assert actual_wait_observed
                        during = [latency for start, latency in samples if fault_start <= start < fault_end]
                        assert len(during) >= 3 and fault_end - fault_start < 2
                        async with case.pool.connection() as observer:
                            assert await (
                                await observer.execute(
                                    "SELECT revision,deletion_revision FROM k05_projects "
                                    "WHERE workspace_id='a' AND project_id=%s",
                                    (initial.project_id,),
                                )
                            ).fetchone() == (2, None)
                    receipt = await mutations.delete(**arguments)
                    assert await mutations.delete(**arguments) == receipt
                    assert receipt["revision"] == 3 and receipt["status"] == "tombstoned"
                    async with case.pool.connection() as conn:
                        assert await (
                            await conn.execute(
                                "SELECT count(*) FROM k05_records WHERE workspace_id='a' "
                                "AND project_id=%s AND record_type='deletion'",
                                (initial.project_id,),
                            )
                        ).fetchone() == (1,)
                    assert affected.get_stats().get("requests_waiting", 0) == 0
                    assert affected.get_stats()["pool_available"] == 1
                finally:
                    if task is not None:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    stop.set()
                    await asyncio.wait_for(reader, 3)
            if output := os.environ.get("K17_FAULT_EVIDENCE_DIR"):
                Path(output).mkdir(parents=True, exist_ok=True)
                with (Path(output) / (fault + ".json")).open("x", encoding="utf-8") as receipt:
                    receipt.write(json.dumps(
                        {
                            "fault": fault,
                            "actual_wait_observed": actual_wait_observed,
                            "fault_seconds": fault_end - fault_start,
                            "background_reads_during_fault": len(during),
                            "background_max_latency_seconds": max(during),
                            "background_read_failures": 0,
                            "background_payload_sha256": other.sha256,
                            "failed_attempt_visible_effects": 0,
                            "recovered_delete_effects": 1,
                            "same_operation_replay_receipt_equal": True,
                            "pool_waiters_after_recovery": 0,
                            "scope": "Actual native PostgreSQL, two tenants, one ongoing source-read loop. Repository boundaries only; no primary load, HTTP/browser, image, storage fault or statistical SLO claim.",
                        },
                        indent=2,
                    ) + "\n")

    _run(run())

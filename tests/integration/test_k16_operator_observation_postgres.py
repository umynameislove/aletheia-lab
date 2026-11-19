"""Real read-only operator sampling/trace and lock-failure semantics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case

from aletheia_lab.hosted.action_audit import request_context
from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.observability import RuntimeTelemetry
from aletheia_lab.hosted.operator_observation import operator_snapshot, trace_journey
from aletheia_lab.hosted.postgres_jobs import PostgresJobs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K16_TEST_DATABASE_URL"),
        reason="Explicit K16 private PostgreSQL test URL required",
    ),
]


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K16_TEST_DATABASE_URL"])


async def _gone(schema):
    import psycopg

    async with await psycopg.AsyncConnection.connect(os.environ["K16_TEST_DATABASE_URL"]) as conn:
        assert await (await conn.execute("SELECT to_regnamespace(%s)", (schema,))).fetchone() == (
            None,
        )


def test_snapshot_is_bounded_and_job_trace_joins_committed_request_without_payload(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            telemetry = RuntimeTelemetry()
            assert telemetry.bind_pool(case.pool)
            async with case.pool.connection():
                gauges = telemetry.snapshot()["gauges"]
                assert gauges["pool_size"] >= 1 and gauges["pool_waiting"] >= 0
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_audit(conn, schema)
            jobs = PostgresJobs(case.pool, case.sources, audit=True, execution=True)
            request_id = "req_" + "a" * 32
            with request_context(request_id):
                for i in range(3):
                    receipt = await jobs.enqueue(
                        credential_digest=case.a,
                        workspace_id="a",
                        operation="preview",
                        request_key=SecretStr("synthetic-observe-" + str(i)),
                        request={"source_id": case.receipt.source_id},
                    )
            async with case.pool.connection() as conn:
                await conn.execute("SET TRANSACTION READ ONLY")
                snapshot = await operator_snapshot(conn, schema, maximum_jobs=1)
                trace = await trace_journey(conn, schema, job_id=receipt.job_id)
            pending = next(
                r
                for r in snapshot["jobs"]
                if r["operation"] == "preview" and r["state"] == "queued"
            )
            assert pending["sampled_rows"] == 1 and pending["truncated"]
            assert (
                snapshot["pending_count_lower_bound"] == 1 and not snapshot["pending_count_exact"]
            )
            assert trace["found"] and trace["request_id"] == request_id
            assert trace["job"]["job_id"] == receipt.job_id and trace["job"]["state"] == "queued"
            assert len(trace["actions"]) == 3 and all(
                row["request_id"] == request_id for row in trace["actions"]
            )
            text = json.dumps({"snapshot": snapshot, "trace": trace}, default=str)
            assert all(
                word not in text
                for word in (
                    "payload",
                    "credential_digest",
                    "cookie",
                    "dataset.csv",
                    "metrics.csv",
                    "example.invalid",
                )
            )
            assert snapshot["invalid_indexes"] == 0
            evidence = os.environ.get("K16_EVIDENCE_DIR")
            if evidence:
                (Path(evidence) / "native-observation.json").write_text(
                    json.dumps(
                        {
                            "snapshot": snapshot,
                            "trace": trace,
                            "scope": "Actual private PostgreSQL queue/audit, three admitted jobs. Sampletruncated means lowerbound, not exacttotal. No user/privatepayload output.",
                        },
                        default=str,
                        indent=2,
                    )
                    + "\n"
                )
        await _gone(schema)

    _run(run())


def test_operator_refuses_write_transaction_and_does_not_hide_real_lock_timeout_as_zero(tmp_path):
    import psycopg

    async def run():
        async with _case(tmp_path) as case:
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                with pytest.raises(ValueError, match="read-only"):
                    await operator_snapshot(conn, schema)
            async with case.pool.connection() as holder:
                await holder.execute("LOCK TABLE j01_jobs IN ACCESS EXCLUSIVE MODE")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    async with case.pool.connection() as observer:
                        await observer.execute("SET TRANSACTION READ ONLY")
                        await operator_snapshot(observer, schema)
            async with case.pool.connection() as conn:
                await conn.execute("SET TRANSACTION READ ONLY")
                result = await operator_snapshot(conn, schema)
                assert result["pending_count_lower_bound"] == 0 and result["pending_count_exact"]
        await _gone(schema)

    _run(run())


def test_unknown_job_and_wrong_scope_are_explicit_not_a_false_successful_journey(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
            async with case.pool.connection() as conn:
                await conn.execute("SET TRANSACTION READ ONLY")
                unknown = await trace_journey(conn, schema, job_id="job_missing")
                assert not unknown["found"] and unknown["actions"] == []
                with pytest.raises(ValueError, match="matching"):
                    await trace_journey(conn, "different_scope", job_id="job_missing")
                with pytest.raises(ValueError, match="identifier"):
                    await trace_journey(conn, schema, job_id="bad\nPRIVATE")
        await _gone(schema)

    _run(run())

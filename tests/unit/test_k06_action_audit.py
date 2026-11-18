"""Safe metadata boundaries and task-local request correlation, without DB claims."""
import asyncio
import logging
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.action_audit import (
    is_ready,
    record_action,
    report_outcome,
    request_context,
)
from aletheia_lab.hosted.action_audit_migration import ActionAuditMigrationRejected, migrate


class Connection:
    autocommit = False

    def __init__(self):
        self.queries = []

    async def execute(self, query, params=()):
        self.queries.append((query, params))


def test_record_enlists_only_safe_metadata_and_does_not_commit():
    conn = Connection()
    with request_context("req_synthetic"):
        identity = asyncio.run(record_action(conn, actor_id="user_a", workspace_id="wa", action="job.admit",
            effect_id="job_a", revision=3, operation_id="job_a", job_id="job_a", parent_id="project_a", subject_id="user_b"))
    assert identity.startswith("audit_")
    assert len(conn.queries) == 1
    query, values = conn.queries[0]
    assert query.startswith("INSERT INTO k06_action_audit")
    assert values == (identity, "req_synthetic", "job_a", "user_a", "wa", "job.admit", "job_a", 3, "job_a", "project_a", "user_b")
    assert not any(word in query for word in ("payload", "credential", "request_hash", "key_hash"))


@pytest.mark.parametrize("change", [
    {"actor_id": "private email@example.invalid"}, {"workspace_id": "wa/other"}, {"effect_id": "x\nprivate"},
    {"action": "Payload body"}, {"operation_id": "x" * 129}, {"request_id": "cookie=value"},
    {"job_id": "../job"}, {"revision": True}, {"revision": -1}, {"revision": 9223372036854775808},
    {"parent_id": "parent/private"}, {"subject_id": "private subject"},
])
def test_invalid_metadata_never_executes_sql(change):
    conn = Connection()
    args = {"actor_id": "user_a", "workspace_id": "wa", "action": "job.admit", "effect_id": "job_a"} | change
    with pytest.raises(ValueError):
        asyncio.run(record_action(conn, **args))
    assert not conn.queries


def test_autocommit_cannot_report_a_business_commit():
    conn = Connection()
    conn.autocommit = True
    with pytest.raises(RuntimeError):
        asyncio.run(record_action(conn, actor_id="user_a", workspace_id="wa", action="job.admit", effect_id="job_a"))
    assert not conn.queries


def test_request_context_is_nested_and_task_local(caplog):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def other():
            with request_context("req_other"):
                entered.set()
                await release.wait()
                report_outcome("rejected")

        with request_context("req_outer"):
            task = asyncio.create_task(other())
            await entered.wait()
            with request_context("req_inner"):
                report_outcome("rolled_back")
            report_outcome("outcome_unknown")
            release.set()
            await task
        report_outcome("rejected")

    with caplog.at_level(logging.INFO, logger="aletheia.action"):
        asyncio.run(scenario())
    assert [(row.action_outcome, row.request_id) for row in caplog.records] == [
        ("rolled_back", "req_inner"), ("outcome_unknown", "req_outer"), ("rejected", "req_other"), ("rejected", None)]
    assert all(row.exc_info is None and row.args == () for row in caplog.records)


def test_unknown_or_success_outcome_cannot_emit_a_false_commit_log(caplog):
    with caplog.at_level(logging.INFO, logger="aletheia.action"), pytest.raises(ValueError):
        report_outcome("committed")
    assert not caplog.records


@pytest.mark.parametrize("schema", ["public", "pg_private", "information_schema", "a;DROP", "UPPER", ""])
def test_migration_rejects_unsafe_schema_before_sql(schema):
    conn = Connection()
    with pytest.raises(ActionAuditMigrationRejected):
        asyncio.run(migrate(conn, schema))
    assert not conn.queries


@pytest.mark.parametrize("privilege", [True, False, None])
def test_readiness_is_read_only_and_requires_every_insert_column(privilege):
    queries = []

    class Probe:
        @asynccontextmanager
        async def connection(self):
            yield self

        async def execute(self, query, params=()):
            queries.append((query, params))
            return self

        async def fetchone(self):
            return (privilege,)

    assert asyncio.run(is_ready(Probe())) is (privilege is True)
    assert len(queries) == 3 and all(query.startswith("SELECT") for query, _ in queries)
    assert "LIMIT 0" in queries[0][0] and "LIMIT 0" in queries[1][0]
    assert queries[2][1] == (["audit_id", "request_id", "operation_id", "actor_id", "workspace_id", "action",
                            "effect_id", "revision", "job_id", "parent_id", "subject_id"],)


def test_readiness_redacts_driver_failure_and_closes_connection(caplog):
    closed = []

    class Probe:
        @asynccontextmanager
        async def connection(self):
            try:
                yield self
            finally:
                closed.append(True)

        async def execute(self, query, params=()):
            raise RuntimeError("synthetic-private-database-diagnostic")

    assert asyncio.run(is_ready(Probe())) is False
    assert closed == [True] and not caplog.records

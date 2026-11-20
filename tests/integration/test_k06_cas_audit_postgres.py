"""Actual CAS business transactions enlist audit and reread authority on retry."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager

import pytest
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.action_audit import request_context
from aletheia_lab.hosted.action_audit_migration import migrate
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.workspace_management import WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


async def _prepare(pool, schema, tmp_path):
    values = await _setup(pool, schema, tmp_path)
    async with pool.connection() as conn:
        assert await migrate(conn, schema) == "created"
    return values


class AuditFaultPool:
    """Inject a PostgreSQL error after the business INSERT, before its audit INSERT."""

    def __init__(self, pool, sqlstate, *, after_rollback=None):
        assert sqlstate in ("40001", "23514")
        self.pool, self.sqlstate, self.after_rollback = pool, sqlstate, after_rollback
        self.audit_calls, self.rechecks = 0, 0

    @asynccontextmanager
    async def connection(self):
        import psycopg

        owner = self

        class Connection:
            autocommit = False

            async def execute(self, query, params=()):
                if query.startswith("SELECT record_type,source_id,snapshot_id FROM k05_records") and "FOR UPDATE" in query:
                    owner.rechecks += 1
                if query.startswith("INSERT INTO k06_action_audit"):
                    owner.audit_calls += 1
                    if owner.audit_calls == 1:
                        # Real server abort; transport and commit remain the real Psycopg pool.
                        await actual.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic audit fault' "
                                             "USING ERRCODE='" + owner.sqlstate + "'; END $$")
                return await actual.execute(query, params)

        try:
            async with self.pool.connection() as actual:
                yield Connection()
        except psycopg.errors.SerializationFailure:
            if self.after_rollback is not None:
                await self.after_rollback()
            raise


def test_real_mutation_audit_commits_with_effect_and_contains_only_safe_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, _ = await _prepare(pool, schema, tmp_path)
            payload = b"synthetic source private payload marker"
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                    "VALUES('audit-source','wa','analyst','audit-source-key',%s,%s,'[]')",
                    (hashlib.sha256(payload).hexdigest(), payload))
            management = WorkspaceManagement(pool, audit=True)
            grants = PostgresProjectAccess(pool, audit=True)
            reviews = PostgresReviews(pool, catalog, audit=True)
            mutations = PostgresProjectMutations(pool, catalog, audit=True)
            with request_context("req_cas_metadata"):
                await management.mutate_member(credential_digest=_credential("owner"), workspace_id="wa",
                    user_id="viewer", expected_revision=1, role="reviewer")
                await management.set_source_grant(credential_digest=_credential("owner"), workspace_id="wa",
                    source_id="audit-source", user_id="analyst", actions=["read"], expected_revision=0)
                await grants.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    user_id="analyst", actions=["read", "review"], expected_revision=0)
                await reviews.put(credential_digest=_credential("reviewer"), workspace_id="wa", result_id=result,
                    annotation_id="audit-review", body="private review marker", status="approved", expected_revision=0)
                deleted = await mutations.delete(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    expected_revision=0)
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT row_to_json(a) FROM k06_action_audit a ORDER BY action")
                rows = []
                while (row := await cursor.fetchone()) is not None:
                    rows.append(row[0])
                assert [row["action"] for row in rows] == ["member.update", "project.delete", "project_grant.set", "review.put", "source_grant.set"]
                assert all(row["request_id"] == "req_cas_metadata" and row["workspace_id"] == "wa" for row in rows)
                assert len({row["operation_id"] for row in rows}) == 5
                assert "private" not in json.dumps(rows)
                by_action = {row["action"]: row for row in rows}
                assert by_action["review.put"]["parent_id"] == result
                assert by_action["source_grant.set"]["parent_id"] == "audit-source"
                assert by_action["source_grant.set"]["subject_id"] == "analyst"
                assert by_action["project_grant.set"]["parent_id"] == project
                assert by_action["project_grant.set"]["subject_id"] == "analyst"
                assert by_action["project.delete"]["parent_id"] == project
                assert by_action["project.delete"]["effect_id"] == deleted["tombstone_id"]
                assert await (await conn.execute("SELECT revision FROM j01_memberships WHERE workspace_id='wa' AND user_id='viewer'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (1,)
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='wa' AND project_id=%s", (project,))).fetchone() == (1,)
    _run(run())


def test_real_audit_distinguishes_same_annotation_on_two_results_and_grant_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, project, _, _ = await _prepare(pool, schema, tmp_path)
            payload = b"synthetic collision source"
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT record_id FROM k05_records WHERE workspace_id='wa' "
                    "AND project_id=%s AND record_type='result' ORDER BY record_id", (project,))
                results = []
                while (row := await cursor.fetchone()) is not None:
                    results.append(row[0])
                assert len(results) == 2
                await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                    "VALUES('collision-source','wa','analyst','collision-source-key',%s,%s,'[]')",
                    (hashlib.sha256(payload).hexdigest(), payload))
            reviews = PostgresReviews(pool, catalog, audit=True)
            management = WorkspaceManagement(pool, audit=True)
            grants = PostgresProjectAccess(pool, audit=True)
            for result in results:
                await reviews.put(credential_digest=_credential("reviewer"), workspace_id="wa", result_id=result,
                    annotation_id="shared-annotation", body="Private comment stays out of audit", status="comment", expected_revision=0)
            for target in ("analyst", "admin"):
                await management.set_source_grant(credential_digest=_credential("owner"), workspace_id="wa",
                    source_id="collision-source", user_id=target, actions=["read"], expected_revision=0)
                await grants.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    user_id=target, actions=["read"], expected_revision=0)
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT action,effect_id,parent_id,subject_id,revision "
                    "FROM k06_action_audit ORDER BY action,parent_id,subject_id")
                rows = []
                while (row := await cursor.fetchone()) is not None:
                    rows.append(row)
                assert len(rows) == 6
                review_rows = [row for row in rows if row[0] == "review.put"]
                assert {row[1] for row in review_rows} == {"shared-annotation"}
                assert {row[2] for row in review_rows} == set(results)
                assert all(row[4] == 1 for row in rows)
                for action, parent in (("source_grant.set", "collision-source"), ("project_grant.set", project)):
                    scoped = [row for row in rows if row[0] == action]
                    assert {row[2] for row in scoped} == {parent}
                    assert {row[3] for row in scoped} == {"analyst", "admin"}
    _run(run())


def test_real_audit_failure_rolls_back_review_effect_and_releases_pool(tmp_path, monkeypatch):
    import psycopg

    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, _, result, _ = await _prepare(pool, schema, tmp_path)
            fault = AuditFaultPool(pool, "23514")
            with pytest.raises(psycopg.errors.CheckViolation):
                await PostgresReviews(fault, catalog, audit=True).put(credential_digest=_credential("reviewer"),
                    workspace_id="wa", result_id=result, annotation_id="rollback-review", body="private rejected body",
                    status="comment", expected_revision=0)
            assert fault.audit_calls == 1 and fault.rechecks == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit")).fetchone() == (0,)
                assert await (await conn.execute("SELECT 1")).fetchone() == (1,)
    _run(run())


def test_real_retry_after_server_abort_rechecks_revoke_and_never_audits_success(tmp_path, monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _database() as (pool, schema):
            _, manager, catalog, project, result, _ = await _prepare(pool, schema, tmp_path)

            async def revoke():
                await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    user_id="reviewer", actions=[], expected_revision=1)

            fault = AuditFaultPool(pool, "40001", after_rollback=revoke)
            with pytest.raises(AuthorizationDenied):
                await PostgresReviews(fault, catalog, audit=True).put(credential_digest=_credential("reviewer"),
                    workspace_id="wa", result_id=result, annotation_id="revoke-retry", body="private revoked body",
                    status="comment", expected_revision=0)
            assert fault.audit_calls == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit")).fetchone() == (0,)
                assert await (await conn.execute("SELECT actions,revision FROM j01_project_grants WHERE workspace_id='wa' "
                    "AND project_id=%s AND user_id='reviewer'", (project,))).fetchone() == ([], 2)
    _run(run())


def test_real_retry_after_server_abort_commits_one_review_and_one_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, _, result, _ = await _prepare(pool, schema, tmp_path)
            fault = AuditFaultPool(pool, "40001")
            value = await PostgresReviews(fault, catalog, audit=True).put(credential_digest=_credential("reviewer"),
                workspace_id="wa", result_id=result, annotation_id="one-retry", body="private retry body",
                status="approved", expected_revision=0)
            assert value["revision"] == 1 and fault.audit_calls == 2 and fault.rechecks == 2
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='review.put' AND revision=1")).fetchone() == (1,)
    _run(run())

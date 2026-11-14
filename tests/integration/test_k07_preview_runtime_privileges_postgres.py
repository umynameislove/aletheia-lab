"""Real nonowner API/worker roles for K-07 preview and publication guard access."""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_k07_previews_postgres import _bundle
from test_k07_upload_staging_postgres import _database, _digest, _run, _sources

from aletheia_lab.hosted.import_errors import MappingInvalid
from aletheia_lab.hosted.import_failures import ImportFailures
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.preview_leases import PreviewLeases
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_policy
from aletheia_lab.hosted.upload_project import UploadProject

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


def _role_url(database, schema, role, password):
    value = urlsplit(database)
    host = value.hostname
    assert host is not None
    if ":" in host:
        host = "[" + host + "]"
    if value.port is not None:
        host += ":" + str(value.port)
    pairs = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    pairs.append(("options", "-csearch_path=" + schema + ",pg_catalog"))
    return urlunsplit(value._replace(netloc=quote(role, safe="") + ":" + quote(password, safe="") + "@" + host,
                                    query=urlencode(pairs)))


def test_real_api_worker_preview_failure_and_successful_confirm_have_only_needed_grants(tmp_path):
    import psycopg
    from psycopg import sql

    class RolePool:
        def __init__(self, url):
            self.url = url

        @asynccontextmanager
        async def connection(self):
            async with await psycopg.AsyncConnection.connect(self.url) as conn:
                yield conn

    async def run():
        suffix, created = uuid4().hex, []
        database = os.environ["K07_TEST_DATABASE_URL"]
        try:
            async with _database() as (pool, schema):
                sources = _sources(pool)
                payload = _bundle()
                source = await sources.put(credential_digest=_digest(), workspace_id="a",
                                           request_key=SecretStr("synthetic-grant-source-001"), payload=payload)
                async with pool.connection() as conn:
                    await migrate_policy(conn, schema)
                    await migrate_preview(conn, schema)
                    for profile in ("api", "worker"):
                        role, password = "k07_role_" + profile + "_" + suffix, secrets.token_urlsafe(32)
                        await conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                        created.append((profile, role, password))
                        await conn.execute("SET LOCAL search_path TO pg_catalog")
                        await grant_preview_runtime(conn, schema, role, profile=profile)
                roles = {profile: RolePool(_role_url(database, schema, role, password)) for profile, role, password in created}
                api, worker = roles["api"], roles["worker"]
                api_sources, worker_sources = _sources(api), _sources(worker)
                (tmp_path / "source-worker").mkdir()
                project = UploadProject(tmp_path / "source-worker", maximum_upload_bytes=10000,
                                        maximum_expanded_bytes=20000, maximum_entries=10)
                raw = project.preview(payload, captured_at="2026-11-10T00:00:00Z")
                preview = await PreviewLeases(worker_sources).create(credential_digest=_digest(), workspace_id="a",
                    source_id=source.source_id, preview_payload=raw)
                assert await PreviewLeases(api_sources).read(credential_digest=_digest(), workspace_id="a",
                                                            preview_id=preview.preview_id) == preview
                jobs, worker_jobs = PostgresJobs(api, api_sources), PostgresJobs(worker, worker_sources)
                bad = await jobs.enqueue(credential_digest=_digest(), workspace_id="a", operation="confirm",
                    request_key=SecretStr("synthetic-grant-bad-001"),
                    request={"source_id": source.source_id, "mapping": {"_preview_id": preview.preview_id}})
                lease = SecretStr("e" * 64)
                claim = await worker_jobs.claim(lease_digest=lease, lease_seconds=60)
                assert claim is not None and claim.job_id == bad.job_id
                failures = ImportFailures(worker)
                for code in ("metric_missing", "metric_unknown"):
                    await failures.record(claim=claim, lease_digest=lease, failure=MappingInvalid({
                        "schema_version": "hosted-upload-error/v1", "code": "mapping_invalid",
                        "issues": [{"code": code, "field": "metric_definitions"}]}))
                assert await worker_jobs.fail(claim=claim, lease_digest=lease)
                failure = await ImportFailures(api).read(workspace_id="a", job_id=bad.job_id)
                assert failure["issues"][0]["code"] == "metric_unknown"
                candidates = raw["mapping_candidates"]
                target, metric, config = (candidates[key][0] for key in ("targets", "metric_sources", "configs"))
                mapping = {
                    "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                               "target_field": "target", "identifier_field": "id"},
                    "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                        "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
                        "run_id_field": "run", "step_field": None}],
                    "runs": [{"run_id": "baseline", "config_item_ids": []},
                             {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]}],
                    "baseline_run_id": "baseline",
                    "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}],
                }
                snapshot = project.confirm(payload, workspace_id="a", source_id=source.source_id,
                                           mapping=mapping, captured_at="2026-11-10T00:00:00Z")
                receipt = await jobs.enqueue(credential_digest=_digest(), workspace_id="a", operation="confirm",
                    request_key=SecretStr("synthetic-grant-good-001"), request={"source_id": source.source_id,
                        "mapping": {**mapping, "_preview_id": preview.preview_id}})
                claim = await worker_jobs.claim(lease_digest=lease, lease_seconds=60)
                assert claim is not None and claim.job_id == receipt.job_id
                assert await worker_jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
                assert (await jobs.status(credential_digest=_digest(), workspace_id="a", job_id=receipt.job_id)).status == "succeeded"
                assert (await api_sources.snapshot(credential_digest=_digest(), workspace_id="a", snapshot_id=snapshot.snapshot_id)).source_sha256 == source.sha256
                for profile, role, _password in created:
                    async with roles[profile].connection() as conn:
                        assert await (await conn.execute("SELECT current_user,session_user")).fetchone() == (role, role)
                        assert await (await conn.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE'),"
                            "has_table_privilege(current_user,'j01_sources','DELETE'),"
                            "has_table_privilege(current_user,'k07_import_previews','DELETE'),"
                            "has_column_privilege(current_user,'k07_import_previews','payload','UPDATE')")).fetchone() == (False, False, False, False)
                    for statement in ("DELETE FROM j01_sources", "DELETE FROM k07_import_previews",
                                      "UPDATE k07_import_previews SET payload=payload",
                                      "UPDATE k07_preview_migrations SET version=version", "CREATE TABLE forbidden_probe(x int)"):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            async with roles[profile].connection() as conn:
                                await conn.execute(statement)
                for statement in ("INSERT INTO k07_import_failures(job_id,workspace_id,payload) VALUES('x','a','{}')",
                                  "UPDATE k07_import_failures SET payload=payload"):
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        async with api.connection() as conn:
                            await conn.execute(statement)
                print("K07_NONOWNER_GRANTS api_read=true worker_create=true failure_upsert=true confirm_trigger=true source_delete=false preview_cleanup=false ddl=false")
        finally:
            async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                for profile, role, _password in created:
                    assert role == "k07_role_" + profile + "_" + suffix
                    await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
    _run(run())

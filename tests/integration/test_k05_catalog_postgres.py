"""Actual PostgreSQL catalog policy, scoped corruption and bounded mixed maintenance."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _database, _run, _scoped

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.object_store import FilesystemObjectStore, ObjectIntegrityError
from aletheia_lab.hosted.persistence_maintenance import maintain, observe
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.postgres_catalog import (
    CatalogIntegrityError,
    PostgresCatalog,
    scrub_catalog,
)
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.lineage import build_lineage_graph, build_lineage_node
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K05_TEST_DATABASE_URL"),
    reason="Explicit K-05 PostgreSQL test URL required")]
PROJECT = "p3-project-" + "1" * 64


async def _seed(pool, *, catalog=True):
    if catalog:
        from aletheia_lab.hosted.project_policy_migration import migrate as policy_migrate

        async with pool.connection() as conn:
            row = await (await conn.execute("SELECT current_schema()")).fetchone()
            await policy_migrate(conn, row[0])
    credentials = {}
    async with pool.connection() as conn:
        for workspace in ("a", "b"):
            user = "user_" + workspace
            digest = hashlib.sha256(user.encode()).hexdigest()
            credentials[workspace] = SecretStr(digest)
            await conn.execute("INSERT INTO hosted_identity_bindings VALUES(%s,%s,%s,'Synthetic',%s,true,true,0,'ready',NULL,NULL)",
                ("https://issuer.example.invalid", workspace, user, workspace + "@example.invalid"))
            await conn.execute("INSERT INTO j01_workspaces VALUES(%s,'Synthetic')", (workspace,))
            await conn.execute("INSERT INTO j01_memberships VALUES(%s,%s,'owner',true,0)", (workspace, user))
            if catalog:
                await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES(%s,%s,%s)",
                                   (workspace, PROJECT, user))
            await conn.execute("INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',statement_timestamp(),"
                "statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
                "statement_timestamp()+interval '28000 seconds',0,0)", ("ctx_" + workspace, user))
            await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)", (digest, "ctx_" + workspace))
    return credentials


def _graph(number):
    node = build_lineage_node(project_id=PROJECT, kind="project", source_id=PROJECT,
        source_sha256=hashlib.sha256(str(number).encode()).hexdigest(), visibility="public")
    return build_lineage_graph(PROJECT, (node,), ())


async def _put(pool, objects, workspace, graph):
    obj = await objects.stage(workspace, _json_bytes(graph.model_dump(mode="json")),
                              media_type="application/json", schema_version=graph.schema_version)
    async with pool.connection() as conn:
        await objects.ready(conn, workspace, obj)
        await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
            "VALUES(%s,%s,%s,'lineage_graph',%s,%s,%s)",
            (workspace, graph.graph_id, graph.project_id, graph.schema_version,
             canonical_project_sha256(graph.model_dump(mode="json")), obj.descriptor.sha256))
        await objects.finish(conn, workspace, obj)
    return obj.descriptor


def test_real_catalog_paging_policy_parent_projection_and_canonical_corruption(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "managed"))
            catalog = PostgresCatalog(pool, objects)
            first, second = _graph(1), _graph(2)
            descriptor = await _put(pool, objects, "a", first)
            await _put(pool, objects, "a", second)
            kwargs = dict(credential_digest=credentials["a"], workspace_id="a", project_id=PROJECT)
            page = await catalog.list_records(**kwargs, record_type="lineage_graph", limit=1)
            assert len(page.records) == 1 and page.next_cursor
            next_page = await catalog.list_records(**kwargs, record_type="lineage_graph", cursor=page.next_cursor, limit=1)
            assert not next_page.next_cursor and next_page.records[0].record_id != page.records[0].record_id
            workspace_page = await catalog.list_workspace_records(credential_digest=credentials["a"], workspace_id="a",
                                                                   kind="lineage_graph", limit=1)
            assert workspace_page.next_cursor
            final_page = await catalog.list_workspace_records(credential_digest=credentials["a"], workspace_id="a",
                kind="lineage_graph", after=workspace_page.next_cursor.after, limit=1)
            assert len(final_page.records) == 1 and not final_page.next_cursor
            with pytest.raises(ValueError, match="cursor"):
                await catalog.list_records(**kwargs, record_type="lineage_graph", cursor=page.next_cursor, search="%")
            with pytest.raises(ValueError, match="cursor"):
                await catalog.list_records(**kwargs, record_type="snapshot", cursor=page.next_cursor)
            assert await catalog.read_record(**kwargs, record_id=first.graph_id) == first
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(credential_digest=credentials["b"], workspace_id="a", project_id=PROJECT, record_id=first.graph_id)
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(**kwargs, record_id="p3-lineage-graph-" + "f" * 64)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO k05_record_parents VALUES('a',%s,%s,'bogus_projection',%s)",
                                   (PROJECT, first.graph_id, second.graph_id))
            with pytest.raises(CatalogIntegrityError):
                await catalog.read_record(**kwargs, record_id=first.graph_id)
            report = await scrub_catalog(pool, objects, "a")
            assert report.corrupt_record_ids == (first.graph_id,)
            assert await catalog.read_record(**kwargs, record_id=second.graph_id) == second
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM k05_record_parents WHERE workspace_id='a' AND record_id=%s", (first.graph_id,))
            path = objects.store.path(descriptor.sha256)
            path.write_bytes(b"x" * descriptor.byte_size)
            with pytest.raises(ObjectIntegrityError):
                await catalog.read_record(**kwargs, record_id=first.graph_id)
            report = await scrub_catalog(pool, objects, "a")
            assert report.corrupt_record_ids == (first.graph_id,)
            assert await catalog.read_record(**kwargs, record_id=second.graph_id) == second
    _run(run())


def test_real_mixed_workload_vacuum_reader_and_numeric_observations(tmp_path):
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "managed"))
            catalog = PostgresCatalog(pool, objects)
            baseline = _graph(0)
            await _put(pool, objects, "a", baseline)
            kwargs = dict(credential_digest=credentials["a"], workspace_id="a", project_id=PROJECT)
            start, done = asyncio.Event(), asyncio.Event()
            reads = 0
            gc_report = None

            async def reader():
                nonlocal reads
                await start.wait()
                while not done.is_set():
                    assert await catalog.read_record(**kwargs, record_id=baseline.graph_id) == baseline
                    reads += 1

            async def writer():
                nonlocal gc_report
                start.set()
                try:
                    for number in range(1, 31):
                        graph = _graph(number)
                        descriptor = await _put(pool, objects, "a", graph)
                        async with pool.connection() as conn:
                            await conn.execute("UPDATE k05_projects SET revision=revision+1 WHERE workspace_id='a' AND project_id=%s", (PROJECT,))
                            if number % 2 == 0:
                                await conn.execute("DELETE FROM k05_records WHERE workspace_id='a' AND record_id=%s", (graph.graph_id,))
                        if number % 2 == 0:
                            os.utime(objects.store.path(descriptor.sha256), (1, 1))
                    async with await psycopg.AsyncConnection.connect(_scoped(os.environ["K05_TEST_DATABASE_URL"], schema), autocommit=True) as conn:
                        await maintain(conn, schema, vacuum=True)
                    report = await objects.reconcile(grace_seconds=60, limit=1000)
                    assert report.removed == 15
                    gc_report = report
                finally:
                    done.set()

            await asyncio.gather(reader(), writer())
            assert reads > 0
            async with pool.connection() as conn:
                await conn.execute("SET TRANSACTION READ ONLY")
                metrics = await observe(conn, schema)
                count = await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone()
                assert count == (16,)
                assert metrics["invalid_indexes"] == []
                assert len(metrics["relations"]) == 11
            metrics.update({"successful_reads": reads, "writes": 31, "updates": 30, "deletes": 15,
                            "gc": asdict(gc_report), "limit": "short local correctness workload; not 2h soak, backup/restore or capacity"})
            evidence = os.environ.get("K05_EVIDENCE_DIR")
            if evidence:
                Path(evidence).mkdir(parents=True, exist_ok=True)
                (Path(evidence) / "mixed-maintenance.json").write_text(json.dumps(metrics, default=str, indent=2), encoding="utf-8")
    _run(run())


def test_real_lock_timeout_rolls_back_and_connection_is_reusable():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            await _seed(pool)
            async with pool.connection() as blocker:
                await blocker.execute("UPDATE k05_projects SET revision=revision+1 WHERE workspace_id='a' AND project_id=%s", (PROJECT,))
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    async with pool.connection() as contender:
                        await contender.execute("SET LOCAL lock_timeout='100ms'")
                        await contender.execute("UPDATE k05_projects SET revision=revision+1 WHERE workspace_id='a' AND project_id=%s", (PROJECT,))
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (PROJECT,))).fetchone()
                assert row == (1,)
                assert await (await conn.execute("SELECT 1")).fetchone() == (1,)
    _run(run())

def test_imported_tombstone_hides_project_and_all_ordinary_reads(tmp_path):
    from aletheia_lab.product.lifecycle import build_product_lifecycle_record

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "managed"))
            catalog = PostgresCatalog(pool, objects)
            graph = _graph(0)
            await _put(pool, objects, "a", graph)
            deletion = build_product_lifecycle_record(record_kind="deletion", project_id=PROJECT,
                                                       payload={"state": "deleted", "synthetic": True})
            obj = await objects.stage("a", _json_bytes(deletion.model_dump(mode="json")),
                                      media_type="application/json", schema_version=deletion.schema_version)
            async with pool.connection() as conn:
                await objects.ready(conn, "a", obj)
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
                    "VALUES('a',%s,%s,'deletion',%s,%s,%s)",
                    (deletion.record_id, PROJECT, deletion.schema_version, deletion.canonical_sha256(), obj.descriptor.sha256))
                await objects.finish(conn, "a", obj)
            kwargs = dict(credential_digest=credentials["a"], workspace_id="a", project_id=PROJECT)
            assert await catalog.list_projects(credential_digest=credentials["a"], workspace_id="a") == ()
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(**kwargs, record_id=graph.graph_id)
            with pytest.raises(AuthorizationDenied):
                await catalog.list_records(**kwargs, record_type="lineage_graph")
    _run(run())


def test_operator_cli_refuses_unmigrated_schema_and_object_lock_wait_is_bounded(tmp_path, monkeypatch):
    import importlib.util

    import psycopg

    path = Path(__file__).parents[2] / "scripts" / "k05_storage_admin.py"
    spec = importlib.util.spec_from_file_location("k05_storage_admin", path)
    admin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admin)

    async def run():
        async with _database() as (pool, schema):
            args = admin.arguments(["verify", "--schema", schema, "--database-env", "K05_TEST_DATABASE_URL"])
            with pytest.raises(ValueError, match="verification failed"):
                await admin.execute(args)
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "managed"))
            descriptor = await _put(pool, objects, "a", _graph(1))
            args = admin.arguments(["scrub", "--schema", schema, "--database-env", "K05_TEST_DATABASE_URL",
                                    "--object-root", str(objects.store.root), "--workspace", "a"])
            async with pool.connection() as blocker:
                await objects.lock(blocker, descriptor.sha256)
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    await asyncio.wait_for(admin.execute(args), timeout=5)
            catalog = PostgresCatalog(pool, objects)
            assert await catalog.read_record(credential_digest=credentials["a"], workspace_id="a",
                project_id=PROJECT, record_id=_graph(1).graph_id) == _graph(1)
    _run(run())


def test_runtime_login_role_uses_catalog_without_schema_or_ledger_authority(tmp_path):
    import secrets
    from uuid import uuid4

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            store = FilesystemObjectStore(tmp_path / "managed")
            graph = _graph(0)
            await _put(pool, PostgresObjects(pool, store), "a", graph)
            role = "k05_runtime_" + uuid4().hex
            password = secrets.token_urlsafe(32)
            async with await psycopg.AsyncConnection.connect(os.environ["K05_TEST_DATABASE_URL"], autocommit=True) as admin:
                await admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                try:
                    await admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
                    async with pool.connection() as conn:
                        tables = await (await conn.execute(
                            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname=%s AND c.relkind='r' AND c.relname NOT LIKE '%%migration%%' ORDER BY c.relname", (schema,))).fetchall()
                    for (table,) in tables:
                        await admin.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {}.{} TO {}").format(
                            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
                    url = make_conninfo(_scoped(os.environ["K05_TEST_DATABASE_URL"], schema), user=role, password=password,
                                        options="-csearch_path=" + schema + " -clock_timeout=1000 -cstatement_timeout=10000")
                    async with AsyncConnectionPool(url, min_size=1, max_size=1, open=False) as runtime:
                        await runtime.wait(timeout=5)
                        catalog = PostgresCatalog(runtime, PostgresObjects(runtime, store))
                        assert await catalog.read_record(credential_digest=credentials["a"], workspace_id="a",
                            project_id=PROJECT, record_id=graph.graph_id) == graph
                        for query in ("CREATE TABLE forbidden_schema_write(id integer)",
                                      "ALTER TABLE k05_projects ADD COLUMN forbidden integer",
                                      "SELECT * FROM k05_persistence_migrations"):
                            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                                async with runtime.connection() as conn:
                                    await conn.execute(query)
                    assert await (await admin.execute("SELECT count(*) FROM pg_roles WHERE rolname=%s", (role,))).fetchone() == (1,)
                finally:
                    assert role.startswith("k05_runtime_") and len(role) == 44
                    await admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    _run(run())


def test_existing_source_job_ports_keep_data_and_receipt_across_schema_adoption():
    from io import BytesIO
    from zipfile import ZipFile

    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.workspace_migration import migrate as workspace_migrate

    async def run():
        async with _database(legacy=True) as (pool, schema):
            async with pool.connection() as conn:
                await workspace_migrate(conn, schema)
            credentials = await _seed(pool, catalog=False)
            old_sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
            old_jobs = PostgresJobs(pool, old_sources)
            stream = BytesIO()
            with ZipFile(stream, "w") as archive:
                archive.writestr("README.md", "Synthetic source, no executable code.\n")
            payload = stream.getvalue()
            source = await old_sources.put(credential_digest=credentials["a"], workspace_id="a",
                                            request_key=SecretStr("source-key-for-adoption"), payload=payload)
            request = dict(credential_digest=credentials["a"], workspace_id="a", operation="preview",
                           request_key=SecretStr("job-key-for-adoption"), request={"source_id": source.source_id})
            receipt = await old_jobs.enqueue(**request)
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == "adopted"
            assert await old_sources.read(credential_digest=credentials["a"], workspace_id="a", source_id=source.source_id) == payload
            assert (await old_jobs.enqueue(**request)).job_id == receipt.job_id
            assert (await old_jobs.status(credential_digest=credentials["a"], workspace_id="a", job_id=receipt.job_id)).status == "queued"
            lease = SecretStr("d" * 64)
            claim = await old_jobs.claim(lease_digest=lease, lease_seconds=60, operations=("preview",))
            assert claim.job_id == receipt.job_id
            assert await old_jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            new_sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
            new_jobs = PostgresJobs(pool, new_sources)
            assert await new_sources.read(credential_digest=credentials["a"], workspace_id="a", source_id=source.source_id) == payload
            assert (await new_jobs.output(credential_digest=credentials["a"], workspace_id="a", job_id=receipt.job_id))["result"] == {"synthetic": True}
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
    _run(run())


def test_shared_project_grant_filters_before_limit_and_revoke_during_object_read(tmp_path, monkeypatch):
    from aletheia_lab.hosted.project_access import PostgresProjectAccess

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            credentials = await _seed(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "managed"))
            catalog = PostgresCatalog(pool, objects)
            graph = _graph(0)
            await _put(pool, objects, "a", graph)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_memberships VALUES('a','user_b','viewer',true,0)")
                await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES('a',%s,'user_a')",
                                   ("p3-project-" + "0" * 64,))
            shared = dict(credential_digest=credentials["b"], workspace_id="a", project_id=PROJECT)
            assert await catalog.list_projects(credential_digest=credentials["b"], workspace_id="a", limit=1) == ()
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(**shared, record_id=graph.graph_id)
            access = PostgresProjectAccess(pool)
            grant = await access.set_grant(credential_digest=credentials["a"], workspace_id="a", project_id=PROJECT,
                                           user_id="user_b", actions=["read"], expected_revision=0)
            assert grant["revision"] == 1
            assert await catalog.list_projects(credential_digest=credentials["b"], workspace_id="a", limit=1) == ((PROJECT, 0),)
            assert await catalog.read_record(**shared, record_id=graph.graph_id) == graph
            assert await catalog.list_projects(credential_digest=credentials["b"], workspace_id="a", search=PROJECT) == ((PROJECT, 0),)
            assert await catalog.list_projects(credential_digest=credentials["b"], workspace_id="a", search="%") == ()
            page = await catalog.list_workspace_records(credential_digest=credentials["b"], workspace_id="a",
                                                        kind="lineage_graph", limit=1, search=PROJECT)
            assert [row.record_id for row in page.records] == [graph.graph_id]
            assert not (await catalog.list_workspace_records(credential_digest=credentials["b"], workspace_id="a",
                                                             kind="lineage_graph", search="%")).records
            assert not (await catalog.list_records(**shared, record_type="lineage_graph", search="%")).records
            read_complete, release = asyncio.Event(), asyncio.Event()
            original = objects.read

            async def held(*args, **kwargs):
                payload = await original(*args, **kwargs)
                read_complete.set()
                await release.wait()
                return payload

            monkeypatch.setattr(objects, "read", held)
            reading = asyncio.create_task(catalog.read_record(**shared, record_id=graph.graph_id))
            await asyncio.wait_for(read_complete.wait(), timeout=10)
            await access.set_grant(credential_digest=credentials["a"], workspace_id="a", project_id=PROJECT,
                                   user_id="user_b", actions=[], expected_revision=1)
            release.set()
            with pytest.raises(AuthorizationDenied):
                await reading
            assert await catalog.list_projects(credential_digest=credentials["b"], workspace_id="a", limit=1) == ()
    _run(run())

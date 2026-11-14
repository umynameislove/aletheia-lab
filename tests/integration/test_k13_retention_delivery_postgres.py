"""Tiny concurrent retention and restored HTTP consumer proofs; no full-load claim."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _initial, _run
from test_k06_follow_up_http_postgres import _identity, _problem

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.canonical_results import CanonicalResults
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.project_refresh_http import ProjectRefreshApplication
from aletheia_lab.hosted.recovery_checkpoint import apply_checkpoint, capture_checkpoint
from aletheia_lab.hosted.recovery_points import capture_bundle, restore_bundle
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_publication import UploadPublication
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, new_opaque_secret
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K13_TEST_DATABASE_URL"),
        reason="Explicit private native K13 PostgreSQL required",
    ),
]
ORIGIN = "https://app.example.invalid"


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K13_TEST_DATABASE_URL"])


async def _ready(case):
    async with case.pool.connection() as conn:
        schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
        await migrate_reviews(conn, schema)
        await migrate_audit(conn, schema)
    return schema


async def _protected_b(case):
    receipt = await case.sources.put(
        credential_digest=case.b,
        workspace_id="b",
        request_key=SecretStr("k13-concurrent-shared-source"),
        payload=case.payload,
    )
    snapshot = build_upload_snapshot(
        workspace_id="b",
        source_id=receipt.source_id,
        source_sha256=receipt.sha256,
        mapping=case.snapshot.mapping,
        mapping_result=case.snapshot.mapping_result,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    question = "What remains observed in the protected workspace source?"
    record = build_initial_upload_result(
        snapshot, question, analyze_initial_upload(snapshot, question)
    )
    upload = await UploadPublication(case.objects).prepare_snapshot(snapshot)
    result = await CanonicalResults(case.objects).prepare_initial_result(
        "b", snapshot=snapshot, record=record
    )
    async with case.pool.connection() as conn:
        auth = await PostgresAuthorization().authorize(
            conn, case.b.get_secret_value(), "b", "confirm"
        )
        published = await UploadPublication(case.objects).enlist_snapshot(
            conn, authorization=auth, prepared=upload
        )
        await CanonicalResults(case.objects).enlist_result(
            conn, authorization=auth, prepared=result, expected_revision=published.revision
        )
    return receipt, record


def _receipt(name, value):
    folder = os.environ.get("K13_EVIDENCE_DIR")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value, indent=2) + "\n")


def test_bounded_retention_under_concurrent_protected_readers_waits_for_real_active_job(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            await _ready(case)
            protected_source, protected = await _protected_b(case)
            retention = PostgresRetention(case.pool, case.catalog)
            await retention.configure(project_days=0, terminal_job_days=0, object_grace_seconds=60)
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog, execution=True)
            active = await jobs.enqueue_refresh(
                credential_digest=case.a,
                workspace_id="a",
                project_id=initial.project_id,
                expected_revision=2,
                request_key=SecretStr("k13-retention-active-refresh"),
                source_id=case.receipt.source_id,
            )
            lease = SecretStr("e" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("refresh",))
            assert claim and claim.job_id == active.job_id
            for index in range(3):
                old = await jobs.enqueue(
                    credential_digest=case.a,
                    workspace_id="a",
                    operation="preview",
                    request_key=SecretStr(f"k13-expired-terminal-preview-{index}"),
                    request={"source_id": case.receipt.source_id},
                )
                assert (
                    await jobs.cancel(credential_digest=case.a, workspace_id="a", job_id=old.job_id)
                ).status == "cancelled"
            # Represent already aged eligible files without changing the real
            # object grace policy, database clock or any accepted job deadline.
            for path in case.objects.store.object_root.glob("*/*"):
                if path.is_file():
                    os.utime(path, (time.time() - 120, time.time() - 120))
            await PostgresProjectMutations(case.pool, case.catalog).delete(
                credential_digest=case.a,
                workspace_id="a",
                project_id=initial.project_id,
                expected_revision=2,
            )
            stop = asyncio.Event()
            started = asyncio.Event()
            reads = 0

            async def reader():
                nonlocal reads
                while not stop.is_set():
                    saved = await case.catalog.read_record(
                        credential_digest=case.b,
                        workspace_id="b",
                        project_id=protected.project_id,
                        record_id=protected.record_id,
                    )
                    assert saved == protected
                    assert (
                        await case.sources.read(
                            credential_digest=case.b,
                            workspace_id="b",
                            source_id=protected_source.source_id,
                        )
                        == case.payload
                    )
                    reads += 1
                    started.set()
                    await asyncio.sleep(0)

            task = asyncio.create_task(reader())
            actions = []
            try:
                await asyncio.wait_for(started.wait(), 5)
                for _ in range(3):
                    batch = await retention.purge(
                        workspace_id="a", project_id=initial.project_id, limit=1
                    )
                    assert batch.action == "active_job_wait" and batch.changed == 0
                    expired = await retention.expire(limit=1)
                    assert expired["terminal_jobs"] == 1
                    async with case.pool.connection() as conn:
                        assert await (
                            await conn.execute(
                                "SELECT state FROM j01_jobs WHERE job_id=%s", (active.job_id,)
                            )
                        ).fetchone() == ("running",)
                # Use the real worker's fenced terminal path after authority loss;
                # no persisted deadline is rewritten or production timeout relaxed.
                with pytest.raises(AuthorizationDenied):
                    await jobs.complete_refresh(
                        claim=claim, lease_digest=lease, snapshot=case.snapshot
                    )
                assert await jobs.fail(
                    claim=claim, lease_digest=lease, failure_kind="authorization"
                )
                for _ in range(200):
                    batch = await retention.purge(
                        workspace_id="a", project_id=initial.project_id, limit=1
                    )
                    actions.append(batch.action)
                    assert batch.changed <= 1
                    if batch.status == "purged":
                        break
                else:
                    pytest.fail("Bounded purge did not finish while protected reads continued.")
            finally:
                stop.set()
                await asyncio.wait_for(task, 10)
            assert reads >= 3
            async with case.pool.connection() as conn:
                assert await (
                    await conn.execute(
                        "SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_type<>'deletion'"
                    )
                ).fetchone() == (0,)
                assert await (
                    await conn.execute(
                        "SELECT count(*) FROM j01_jobs WHERE job_id=%s", (active.job_id,)
                    )
                ).fetchone() == (0,)
                logical_bytes = (
                    await (
                        await conn.execute(
                            "SELECT COALESCE(sum(octet_length(payload)),0) FROM j01_sources"
                        )
                    ).fetchone()
                )[0]
                logical_bytes += (
                    await (
                        await conn.execute("SELECT COALESCE(sum(byte_size),0) FROM k05_objects")
                    ).fetchone()
                )[0]
            assert logical_bytes <= 100 * 1024
            _receipt(
                "concurrent-retention.json",
                {
                    "status": "passed",
                    "scope": "Tiny native concurrent PostgreSQL reader/retention fixture; no normal-load or 50k claim.",
                    "protected_read_cycles": reads,
                    "active_job_waits": 3,
                    "terminal_preview_expirations": 3,
                    "real_worker_authorization_failure_before_purge": True,
                    "batch_limit": 1,
                    "purge_actions": actions,
                    "logical_payload_bytes_after": int(logical_bytes),
                    "shared_workspace_source_preserved": True,
                },
            )

    _run(scenario())


async def _fresh(pool, user, tokens):
    context = "k13fresh_" + uuid4().hex
    raw = new_opaque_secret()
    proof = tokens.issue(context)
    digest = credential_digest(raw)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO j01_sessions VALUES(%s,%s,%s,'active',statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28800 seconds',0,0)",
            (context, user, proof.get_secret_value()),
        )
        await conn.execute(
            "INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
            (digest.get_secret_value(), context),
        )
    return digest, {
        "origin": ORIGIN,
        "x-csrf-token": proof.get_secret_value(),
        "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}",
    }


@asynccontextmanager
async def _http(pool, catalog, root, tokens):
    runtime = RuntimeSettings.model_validate(
        {
            "environment": "local",
            "public_origin": ORIGIN,
            "identity": {
                "issuer": "https://issuer.example.invalid",
                "client_id": "synthetic-k13-restore",
                "redirect_uri": ORIGIN + "/v1/auth/callback",
                "client_kind": "public",
            },
            "storage": {
                "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
                "object_root": str(root),
            },
        }
    )
    sessions = ServerSessions(
        PostgresSessionStore(pool),
        AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
        timeout_seconds=3,
        maximum_cookie_bytes=4096,
    )
    renderer = ReportRenderer(timeout_seconds=5)
    access = CatalogAccess(
        catalog,
        ScopedCursors(SecretStr("synthetic-k13-cursor-signing-secret-not-production")),
        renderer,
    )

    async def fallback(scope, receive, send):
        raise AssertionError("Unexpected restored HTTP route")

    async def clock():
        return True

    limits = BodyLimits(20000, 1, 3, 100)
    sources = PostgresSources(
        pool,
        maximum_upload_bytes=10000,
        maximum_expanded_bytes=20000,
        maximum_entries=10,
        catalog=catalog,
    )
    jobs = PostgresJobs(pool, sources, catalog=catalog, execution=True)
    app = ProjectRefreshApplication(
        fallback, jobs, sessions, tokens, runtime, body_limits=limits, clock_check=clock
    )
    app = CatalogApplication(
        app,
        access,
        PostgresProjectAccess(pool),
        PostgresReviews(pool, catalog),
        sessions,
        tokens,
        runtime,
        body_limits=limits,
        clock_check=clock,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            yield client, sources
    finally:
        await renderer.aclose()


@pytest.mark.skipif(
    not os.environ.get("K18_TEST_PG_BIN"),
    reason="Explicit native PG16 dump/restore binary directory required",
)
def test_restored_http_export_download_and_refresh_deny_deleted_project_with_valid_fresh_sessions(
    tmp_path,
):
    async def scenario():
        database = os.environ["K13_TEST_DATABASE_URL"]
        pg_bin = Path(os.environ["K18_TEST_PG_BIN"])
        target_name = "k13_http_restore_" + uuid4().hex
        options = conninfo_to_dict(database)
        options["dbname"] = target_name
        options.pop("options", None)
        target = make_conninfo(**options)
        tokens = CsrfTokens(SecretStr("synthetic-k13-restore-csrf-secret-not-production"))
        key = b"synthetic-k13-restore-checkpoint-key-not-production"
        async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
            await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))
            try:
                (tmp_path / "source").mkdir()
                async with _case(tmp_path / "source") as case:
                    _, initial = await _initial(case)
                    schema = await _ready(case)
                    source_b = await case.sources.put(
                        credential_digest=case.b,
                        workspace_id="b",
                        request_key=SecretStr("k13-http-shared-source"),
                        payload=case.payload,
                    )
                    owner = await _identity(
                        case, "a", new_opaque_secret().get_secret_value(), tokens
                    )
                    async with _http(
                        case.pool, case.catalog, tmp_path / "source/native-objects", tokens
                    ) as (client, _):
                        export = await client.post(
                            f"/v1/workspaces/a/results/{initial.record_id}/exports",
                            headers=owner,
                            json={"format": "json"},
                        )
                        assert export.status_code == 201, export.text
                        descriptor = export.json()
                        assert (
                            await client.get(descriptor["download_url"], headers=owner)
                        ).status_code == 200
                    bundle = tmp_path / "backup"
                    await asyncio.to_thread(
                        capture_bundle,
                        database,
                        schema,
                        case.objects.store.root,
                        bundle,
                        pg_bin=pg_bin,
                        signing_key=key,
                    )
                    await PostgresProjectMutations(case.pool, case.catalog).delete(
                        credential_digest=case.a,
                        workspace_id="a",
                        project_id=initial.project_id,
                        expected_revision=2,
                    )
                    async with case.pool.connection() as conn:
                        await conn.execute(
                            "UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'"
                        )
                    cp = tmp_path / "latest-checkpoint"
                    checkpoint = await capture_checkpoint(
                        PostgresRetention(case.pool, case.catalog),
                        cp,
                        schema=schema,
                        signing_key=key,
                    )
                    restored_root = tmp_path / "restored-objects"
                    assert (
                        await asyncio.to_thread(
                            restore_bundle,
                            target,
                            bundle,
                            restored_root,
                            pg_bin=pg_bin,
                            signing_key=key,
                        )
                    )["status"] == "restored_not_ready"
                    async with AsyncConnectionPool(
                        make_conninfo(target, options="-csearch_path=" + schema),
                        min_size=1,
                        max_size=2,
                        open=False,
                    ) as pool:
                        await pool.wait(timeout=10)
                        catalog = PostgresCatalog(
                            pool, PostgresObjects(pool, FilesystemObjectStore(restored_root))
                        )
                        await apply_checkpoint(
                            PostgresRetention(pool, catalog),
                            cp,
                            schema=schema,
                            signing_key=key,
                            expected_manifest_sha256=checkpoint["manifest_sha256"],
                        )
                        fresh_a, headers = await _fresh(pool, "owner_a", tokens)
                        fresh_b, _ = await _fresh(pool, "owner_b", tokens)
                        async with _http(pool, catalog, restored_root, tokens) as (client, sources):
                            _problem(
                                await client.get(descriptor["download_url"], headers=owner),
                                401,
                                "session_revoked",
                            )
                            _problem(
                                await client.get(
                                    f"/v1/workspaces/a/results/{initial.record_id}", headers=headers
                                ),
                                404,
                                "not_found",
                            )
                            _problem(
                                await client.post(
                                    f"/v1/workspaces/a/results/{initial.record_id}/exports",
                                    headers=headers,
                                    json={"format": "json"},
                                ),
                                404,
                                "not_found",
                            )
                            for extra in ({}, {"range": "bytes=0-15"}):
                                denied = await client.get(
                                    descriptor["download_url"], headers={**headers, **extra}
                                )
                                _problem(denied, 404, "not_found")
                                assert (
                                    "etag" not in denied.headers
                                    and "content-range" not in denied.headers
                                )
                            _problem(
                                await client.post(
                                    f"/v1/workspaces/a/projects/{initial.project_id}/refresh",
                                    headers={
                                        **headers,
                                        "if-match": '"2"',
                                        "idempotency-key": "k13-restored-fresh-refresh-denied",
                                    },
                                    json={
                                        "schema_version": "product-api/v1-draft",
                                        "source_id": case.receipt.source_id,
                                    },
                                ),
                                404,
                                "not_found",
                            )
                            assert (
                                await sources.read(
                                    credential_digest=fresh_a,
                                    workspace_id="a",
                                    source_id=case.receipt.source_id,
                                )
                                == case.payload
                            )
                            assert (
                                await sources.read(
                                    credential_digest=fresh_b,
                                    workspace_id="b",
                                    source_id=source_b.source_id,
                                )
                                == case.payload
                            )
                        async with pool.connection() as conn:
                            assert await (
                                await conn.execute("SELECT count(*) FROM k02_exports")
                            ).fetchone() == (1,)
                            assert await (
                                await conn.execute("SELECT count(*) FROM j01_jobs")
                            ).fetchone() == (0,)
                            logical_bytes = (
                                await (
                                    await conn.execute(
                                        "SELECT sum(octet_length(payload)) FROM j01_sources"
                                    )
                                ).fetchone()
                            )[0]
                            logical_bytes += (
                                await (
                                    await conn.execute("SELECT sum(byte_size) FROM k05_objects")
                                ).fetchone()
                            )[0]
                        assert logical_bytes <= 100 * 1024
                        _receipt(
                            "restored-http-denial.json",
                            {
                                "status": "passed",
                                "scope": "Actual ASGI HTTP boundary + native PG logical restore/current signed checkpoint; no socket TLS, IdP or deployed grant/readiness claim.",
                                "old_session_download_status": 401,
                                "fresh_authenticated_deleted_result_export_download_range_refresh_status": 404,
                                "valid_fresh_session_and_signed_csrf": True,
                                "retained_sources_exact": True,
                                "new_export_effects": 0,
                                "new_job_effects": 0,
                                "logical_payload_bytes": int(logical_bytes),
                            },
                        )
            finally:
                await admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                    (target_name,),
                )
                await admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target_name)))

    _run(scenario())

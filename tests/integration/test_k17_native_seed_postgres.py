"""Small real native multi-generation seed with authentic server-session consumers."""

from __future__ import annotations

import json
import os
import stat
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from test_k05_native_results_postgres import _run

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_seed import SeedLimits, SeedRejected, seed_native
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_policy
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K17_TEST_DATABASE_URL"),
        reason="Explicit private K17 PostgreSQL database required",
    ),
]


@asynccontextmanager
async def _target():
    database = os.environ["K17_TEST_DATABASE_URL"]
    schema = "k17_seed_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(
                make_conninfo(database, options="-csearch_path=" + schema),
                min_size=1,
                max_size=2,
                open=False,
            ) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    await migrate(conn, schema)
                    await migrate_policy(conn, schema)
                    await migrate_reviews(conn, schema)
                yield pool, schema
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_small_streamed_native_seed_has_real_scoped_records_role_mix_and_independent_physical_sizes(
    tmp_path,
):
    async def scenario():
        plan = SeedPlan(namespace="k17_nativeproof")
        secret = SecretStr("synthetic-k17-csrf-signing-key-not-production")
        async with _target() as (pool, schema):
            output = tmp_path / "private-seed"
            result = await seed_native(
                pool,
                schema=schema,
                output=output,
                plan=plan,
                csrf_secret=secret,
                limits=SeedLimits(500 * 1024**2, 10000, 100, maximum_seconds=300),
            )
            assert result["logical_counts"] == {
                "principals": 30,
                "workspaces": 2,
                "projects": 6,
                "snapshots": 12,
                "results": 24,
                "sources": 12,
                "projections": 24,
                "upload_projections": 12,
            }
            assert not result["primary_dataset_qualified"] and result["primary_gaps"]
            assert result["physical"]["unique_source_payloads"] == 12
            assert (
                result["physical"]["all_source_payload_hashes_verified"]
                and result["physical"]["all_object_file_hashes_verified"]
            )
            assert result["actors_issued"]["roles"] == dict.fromkeys(
                ("owner", "admin", "analyst", "reviewer", "viewer"), 6
            )
            assert result["actors_issued"]["sizes"] == dict.fromkeys(("S", "M", "L"), 10)
            assert result["strata"]["S"]["actual_claims_per_result"] == 2
            assert result["strata"]["M"]["actual_claims_per_result"] == 16
            assert result["strata"]["L"]["actual_claims_per_result"] == 64
            assert (
                result["strata"]["S"]["maximum_source_zip_bytes"]
                < result["strata"]["M"]["minimum_source_zip_bytes"]
                < result["strata"]["L"]["minimum_source_zip_bytes"]
            )
            assert (
                result["physical"]["schema_allocated_bytes"]
                + result["physical"]["physical_object_file_bytes"]
                < 500 * 1024**2
            )
            assert not (output / "INCOMPLETE").exists() and not (output / "scratch").exists()
            if os.name != "nt":
                assert stat.S_IMODE((output / "actors.jsonl").stat().st_mode) == 0o600
            actors = [
                Actor.model_validate_json(line)
                for line in (output / "actors.jsonl").read_text().splitlines()
            ]
            assert len({actor.actor_id for actor in actors}) == 30
            assert len({actor.cookie.get_secret_value() for actor in actors}) == 30
            sessions = ServerSessions(
                PostgresSessionStore(pool),
                AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                timeout_seconds=5,
                maximum_cookie_bytes=4096,
            )
            catalog = PostgresCatalog(
                pool, PostgresObjects(pool, FilesystemObjectStore(output / "objects"))
            )
            sources = PostgresSources(
                pool,
                maximum_upload_bytes=16 * 1024**2,
                maximum_expanded_bytes=64 * 1024**2,
                maximum_entries=16,
            )
            for actor in actors:
                session = await sessions.resolve_cookie(actor.cookie.get_secret_value().encode())
                assert session and session.actor.user_id == actor.actor_id
                assert CsrfTokens(secret).matches_context(actor.csrf, session.context_id)
                assert session.expires_at > datetime.now(UTC)
                from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
                from aletheia_lab.hosted_auth.sessions import credential_digest

                raw = read_opaque_cookie(
                    actor.cookie.get_secret_value().encode(),
                    name=SESSION_COOKIE,
                    maximum_bytes=4096,
                )
                digest = credential_digest(raw)
                stored_session = await PostgresSessionStore(pool).inspect(digest)
                assert stored_session is not None
                assert actor.values["context_id"] == session.context_id
                assert (
                    datetime.fromisoformat(actor.values["absolute_expires_at"])
                    == stored_session.absolute_expires_at
                )
                values = actor.values
                record = await catalog.read_record(
                    credential_digest=digest,
                    workspace_id=values["workspace_id"],
                    project_id=values["project_id"],
                    record_id=values["result_id"],
                )
                assert (
                    len(record.view.claims) == int(values["claims"])
                    and record.view.runtime.external_call is False
                )
                source = await sources.read(
                    credential_digest=digest,
                    workspace_id=values["workspace_id"],
                    source_id=values["source_id"],
                )
                assert __import__("hashlib").sha256(source).hexdigest() == values["source_sha256"]
                if actor.role == "analyst":
                    jobs = PostgresJobs(pool, sources, catalog=catalog, execution=True)
                    receipt = await jobs.enqueue(
                        credential_digest=digest,
                        workspace_id=values["workspace_id"],
                        operation="analyze",
                        request_key=SecretStr("k17-real-analyze-probe-" + actor.actor_id),
                        request={
                            "snapshot_id": values["snapshot_id"],
                            "question": values["question"],
                            "analysis_model": values["analysis_model"],
                        },
                    )
                    assert (
                        await jobs.cancel(
                            credential_digest=digest,
                            workspace_id=values["workspace_id"],
                            job_id=receipt.job_id,
                        )
                    ).status == "cancelled"
                with pytest.raises(AuthorizationDenied):
                    await catalog.read_record(
                        credential_digest=digest,
                        workspace_id=values["foreign_workspace_id"],
                        project_id=values["foreign_project_id"],
                        record_id=values["foreign_result_id"],
                    )
            with pytest.raises(SeedRejected):
                await seed_native(
                    pool,
                    schema=schema,
                    output=tmp_path / "second",
                    plan=plan,
                    csrf_secret=secret,
                    limits=SeedLimits(500 * 1024**2, 10000, 100),
                )
            assert not (tmp_path / "second").exists()
            public = json.dumps(result)
            assert all(actor.cookie.get_secret_value() not in public for actor in actors)
            if os.environ.get("K17_EVIDENCE_DIR"):
                from pathlib import Path

                evidence = Path(os.environ["K17_EVIDENCE_DIR"])
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / "small-native-seed.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )

    _run(scenario())

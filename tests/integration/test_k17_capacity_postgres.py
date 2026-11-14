"""100 real principals on native PostgreSQL over TLS, with actual worker processes.

The composition uses production session/CSRF/catalog/jobs/rendering boundaries.
Login/IdP discovery and the primary 50k campaign are separate acceptance gates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import ssl
import subprocess
import sys
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import httpx2
import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _run
from test_k06_api_shutdown_postgres import ORIGIN, _application_url, _port
from test_k06_runtime_privileges_postgres import _role_url
from test_k17_native_seed_postgres import _target

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.capacity import CapacityHarness
from aletheia_lab.hosted.capacity_models import load_actors
from aletheia_lab.hosted.capacity_profiles import calibration_profile
from aletheia_lab.hosted.capacity_reconciliation import reconcile_mutations
from aletheia_lab.hosted.capacity_seed import SeedLimits, seed_native
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.runtime_privileges import grant_runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K17_TEST_DATABASE_URL") or os.name != "posix",
        reason="Explicit private native PostgreSQL and POSIX process fixture required",
    ),
]

_CHILD = r"""
import asyncio,json,os,signal,sys
from pathlib import Path
import uvicorn
from pydantic import SecretStr
from psycopg_pool import AsyncConnectionPool
from aletheia_lab.hosted.observability import configure_process_observability,process_telemetry,ObservedApplication
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.job_updates import PostgresJobUpdates
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.runtime_management import RuntimeManagement
from aletheia_lab.hosted.runtime import api_server_options
from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_runtime import compose_upload_runtime
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.abuse import AuthAdmission,RatePolicy
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Limits

async def main():
    mode,config,cert,key=sys.argv[1:]
    settings=RuntimeSettings.model_validate_json(Path(config).read_bytes())
    async with AsyncConnectionPool(os.environ['ALETHEIA_HOSTED_DATABASE_URL'],min_size=1,max_size=4 if mode=='api' else 2,open=False) as pool:
        await pool.wait(timeout=10)
        assert await verify_runtime_binding(pool,settings.storage)
        process_telemetry().bind_pool(pool)
        process_telemetry().watch_loop(asyncio.get_running_loop())
        objects=PostgresObjects(pool,FilesystemObjectStore(Path(settings.storage.object_root)/'objects'))
        catalog=PostgresCatalog(pool,objects)
        SourceType=PostgresSources
        if mode=='worker':
            from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources
            SourceType=WorkerPostgresSources
        sources=SourceType(pool,maximum_upload_bytes=200*1024**2,maximum_expanded_bytes=512*1024**2,maximum_entries=100000,audit=True,catalog=catalog)
        jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog,execution=True,analysis_model='upload-snapshot-metric-observation/v2')
        async def clock():
            return await jobs.is_ready() and await jobs.execution_ready() and await jobs.follow_up_ready() and await probe_clock(pool,max_skew_seconds=30,timeout_seconds=2)
        assert await clock()
        if mode=='worker':
            from aletheia_lab.hosted.payload_upload_worker import PayloadUploadWorkerSupervisor
            from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
            from aletheia_lab.hosted.upload_job_worker import run_upload_scheduler
            stop=asyncio.Event()
            loop=asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM,stop.set)
            supervisor=PayloadUploadWorkerSupervisor(python=Path(sys.executable).absolute(),source_root=Path(os.environ['PYTHONPATH']),
                private_root=Path(settings.storage.object_root)/'worker-private',slots=2,timeout_seconds=90)
            operations=CanonicalProjectOperations(sources,supervisor,jobs,private_root=Path(settings.storage.object_root)/'worker-private')
            try:
                await run_upload_scheduler(jobs,operations,stop=stop,slots=2,lease_seconds=120,idle_seconds=.1,clock_check=clock)
            finally:
                await supervisor.aclose()
            return
        policy=(AuthPolicy.model_validate_json(os.environ['K17_AUTH_POLICY']) if 'K17_AUTH_POLICY' in os.environ else AuthPolicy(default_return_to='/',return_to_paths=('/',)))
        sessions=ServerSessions(PostgresSessionStore(pool),policy,timeout_seconds=5,maximum_cookie_bytes=4096)
        secret=SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET'])
        cursors=ScopedCursors(secret)
        renderer=ReportRenderer(timeout_seconds=15)
        access=CatalogAccess(catalog,cursors,renderer)
        rate_policy=RatePolicy(600,30,60)
        rate_store=PostgresAuthAdmission(pool,namespace='k17-native-capacity')
        await rate_store.prepare(rate_policy)
        composed=compose_upload_runtime(settings=settings,sessions=sessions,
            workspaces=PostgresWorkspaceAccess(pool,sessions),sources=sources,jobs=jobs,
            tokens=CsrfTokens(secret),coordinator=object(),auth_policy=policy,
            limits=Limits(question_chars=4000,upload_bundle_bytes=200*1024**2,page_max=200,job_poll_min_ms=2000),
            clock_check=clock,admission=AuthAdmission(rate_store,rate_policy,timeout_seconds=5),admission_subject_key=secret,
            catalog_access=access,project_access=PostgresProjectAccess(pool,audit=True),reviews=PostgresReviews(pool,catalog,audit=True),
            project_mutations=PostgresProjectMutations(pool,catalog,audit=True),follow_up_available=True,
            job_updates=PostgresJobUpdates(jobs,cursors),retention=PostgresRetention(pool,catalog),runtime_management=RuntimeManagement(pool,audit=True))
        options=api_server_options(settings)
        options.update(ssl_certfile=cert,ssl_keyfile=key,log_config=None,log_level='warning')
        try:
            server=uvicorn.Server(uvicorn.Config(ObservedApplication(composed),**options))
            await server.serve()
        finally:
            await renderer.aclose()
owner=configure_process_observability(role=sys.argv[1],release_id=os.environ.get('ALETHEIA_RELEASE_ID'))
try:
    asyncio.run(main())
except Exception:
    print('K17 native composition failed.',file=sys.stderr)
    raise SystemExit(1)
finally:
    owner.close()
"""


def _identity(root):
    digest = hashlib.sha256()
    for path in sorted((root / "src/aletheia_lab").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            name, data = path.relative_to(root).as_posix().encode(), path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def _exercise(tmp_path, *, rotation=False):
    async def scenario():
        root = Path(__file__).resolve().parents[2]
        source_before = _identity(root)
        secret = secrets.token_urlsafe(48)
        database = os.environ["K17_TEST_DATABASE_URL"]
        roles = {mode: "k17_" + mode + "_" + uuid4().hex for mode in ("api", "worker")}
        passwords = {mode: secrets.token_urlsafe(32) for mode in roles}
        children, handles, created_roles = [], [], []
        saved = Path(os.environ["K17_EVIDENCE_DIR"]) if os.environ.get("K17_EVIDENCE_DIR") else None
        prefix = "renewal" if rotation else "calibration100"

        async def stop_children():
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    await asyncio.to_thread(child.wait, 15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    await asyncio.to_thread(child.wait, 5)

        try:
            async with _target() as (pool, schema):
                output = tmp_path / "seed"
                plan = SeedPlan(
                    namespace="k17_calibration1",
                    workspaces=4,
                    projects_per_workspace=5,
                    generations=1,
                    results_per_generation=1,
                    size_cycle=tuple(["S"] * 12 + ["M"] * 7 + ["L"]),
                    source_rows={"S": 32, "M": 128, "L": 512},
                )
                if rotation:
                    plan = SeedPlan(
                        namespace="k17_rotation1",
                        workspaces=2,
                        projects_per_workspace=3,
                        generations=1,
                        results_per_generation=1,
                        idle_seconds=60,
                        absolute_seconds=120,
                    )
                manifest = await seed_native(
                    pool,
                    schema=schema,
                    output=output,
                    plan=plan,
                    csrf_secret=SecretStr(secret),
                    limits=SeedLimits(500 * 1024**2, 100000, 100, maximum_seconds=300),
                )
                manifest_path = output / "manifest.json"
                assert manifest_path.is_file()
                async with pool.connection() as conn:
                    await migrate_audit(conn, schema)
                    await conn.execute(
                        files("aletheia_lab.hosted_auth")
                        .joinpath("admission_schema.sql")
                        .read_text()
                    )
                async with await psycopg.AsyncConnection.connect(
                    database, autocommit=True
                ) as admin:
                    for mode, role in roles.items():
                        await admin.execute(
                            sql.SQL(
                                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                            ).format(sql.Identifier(role), sql.Literal(passwords[mode]))
                        )
                        created_roles.append(role)
                async with pool.connection() as conn:
                    for mode, role in roles.items():
                        await grant_runtime(conn, schema, role, profile=mode, audit=True)
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                port = _port()
                (output / "worker-private").mkdir(mode=0o700)
                for mode in roles:
                    config = tmp_path / (mode + ".json")
                    config.write_text(
                        json.dumps(
                            {
                                "environment": "local",
                                "public_origin": ORIGIN,
                                "identity": {
                                    "issuer": "https://issuer.example.invalid",
                                    "client_id": "synthetic-preauthenticated",
                                    "redirect_uri": ORIGIN + "/v1/auth/callback",
                                    "client_kind": "public",
                                },
                                "storage": {
                                    "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
                                    "object_root": str(output),
                                    "expected_schema": schema,
                                    "expected_database_role": roles[mode],
                                },
                                "api": {
                                    "port": port,
                                    "database_pool_size": 4,
                                    "graceful_shutdown_seconds": 10,
                                },
                            }
                        )
                    )
                    config.chmod(0o600)
                    env = {
                        k: os.environ[k]
                        for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                        if k in os.environ
                    }
                    env.update(
                        PYTHONPATH=str(root / "src"),
                        ALETHEIA_HOSTED_CSRF_SECRET=secret,
                        ALETHEIA_RELEASE_ID=source_before,
                        ALETHEIA_HOSTED_DATABASE_URL=_application_url(
                            _role_url(database, schema, roles[mode], passwords[mode]),
                            "k17-" + mode + "-" + uuid4().hex,
                        ),
                    )
                    if rotation:
                        env["K17_AUTH_POLICY"] = json.dumps(
                            {
                                "default_return_to": "/",
                                "return_to_paths": ["/"],
                                "rotation_seconds": 1,
                                "idle_seconds": 60,
                                "absolute_seconds": 120,
                                "login_transaction_seconds": 30,
                            }
                        )
                    log = (tmp_path / (mode + ".safe.jsonl")).open("wb")
                    handles.append(log)
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            _CHILD,
                            mode,
                            str(config),
                            str(cert),
                            str(key),
                        ],
                        cwd=root,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                    )
                    children.append(child)
                target = f"https://127.0.0.1:{port}"
                profile = calibration_profile(
                    target=target,
                    origin=ORIGIN,
                    host="app.example.invalid",
                    tls_ca_file=str(cert),
                    release_sha256=source_before,
                    dataset_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    run_label="native100",
                    mode=os.environ.get("K17_CALIBRATION_MODE", "closed"),
                )
                profile = type(profile).model_validate(
                    {
                        **profile.model_dump(),
                        "duration_seconds": float(os.environ.get("K17_CALIBRATION_DURATION_SECONDS", "25")),
                        "warmup_seconds": float(os.environ.get("K17_CALIBRATION_WARMUP_SECONDS", "0")),
                        "arrivals_per_second": float(os.environ.get("K17_CALIBRATION_ARRIVALS_PER_SECOND", "20")),
                        "think_seconds": 5.0,
                        "max_inflight": 32,
                        "max_pending": 128,
                        "poll_seconds": 2.0,
                    }
                )
                actors, actor_file_hash = load_actors(output / "actors.jsonl", profile)
                if rotation:
                    actors = [next(a for a in actors if a.role == "analyst" and a.size == "S")]
                    profile = profile.model_copy(
                        update={
                            "mode": "closed",
                            "duration_seconds": 4.5,
                            "think_seconds": 0.2,
                            "renew_every_seconds": 1.1,
                            "max_inflight": 4,
                            "max_pending": 16,
                            "journeys": (profile.journeys[0],),
                        }
                    )
                assert len(actors) == len({a.actor_id for a in actors}) == (1 if rotation else 100)
                assert rotation or {
                    role: sum(a.role == role for a in actors)
                    for role in ("owner", "admin", "analyst", "reviewer", "viewer")
                } == dict.fromkeys(("owner", "admin", "analyst", "reviewer", "viewer"), 20)
                async with httpx2.AsyncClient(
                    base_url=target,
                    verify=ssl.create_default_context(cafile=str(cert)),
                    trust_env=False,
                    timeout=5,
                    headers={"host": "app.example.invalid"},
                ) as client:
                    async with asyncio.timeout(30):
                        while True:
                            assert all(c.poll() is None for c in children), (
                                "Native API or worker failed before calibration"
                            )
                            try:
                                if (await client.get("/health/ready")).status_code == 200:
                                    break
                            except httpx2.TransportError:
                                pass
                            await asyncio.sleep(0.1)
                if saved:
                    saved.mkdir(parents=True, exist_ok=True)
                    (saved / (prefix + "-profile.json")).write_text(
                        profile.model_dump_json(indent=2) + "\n"
                    )
                    (saved / (prefix + "-seed-manifest.json")).write_text(
                        json.dumps(manifest, indent=2) + "\n"
                    )
                started = datetime.now(UTC).isoformat()
                harness = CapacityHarness(profile, actors)
                result = await harness.run()
                if rotation:
                    current = harness.sessions.current(actors[0])
                    async with httpx2.AsyncClient(
                        base_url=target,
                        verify=ssl.create_default_context(cafile=str(cert)),
                        trust_env=False,
                        headers={"host": "app.example.invalid"},
                    ) as check:
                        old = await check.get(
                            "/v1/session", headers={"cookie": actors[0].cookie.get_secret_value()}
                        )
                        fresh = await check.get(
                            "/v1/session", headers={"cookie": current.cookie.get_secret_value()}
                        )
                    result["rotation_witness"] = {
                        "old_credential_status": old.status_code,
                        "current_credential_status": fresh.status_code,
                        "cookie_changed": current.cookie.get_secret_value()
                        != actors[0].cookie.get_secret_value(),
                        "context_unchanged": fresh.status_code == 200
                        and fresh.json()["session"]["context_id"] == actors[0].values["context_id"],
                        "policy": "Actual rotation_seconds=1,idle_seconds=60,absolute_seconds=120; real elapsed time, no DB timestamp rewrite",
                    }
                if saved:
                    (saved / (prefix + "-raw-before-reconciliation.json")).write_text(
                        json.dumps(result, indent=2) + "\n", encoding="utf-8"
                    )
                await stop_children()
                async with pool.connection() as conn:
                    await conn.execute("SET TRANSACTION READ ONLY")
                    reconciliation = await reconcile_mutations(conn, schema, result)
                    verified_exports = 0
                    from aletheia_lab.hosted.object_store import (
                        FilesystemObjectStore,
                        ObjectDescriptor,
                    )

                    store = FilesystemObjectStore(output / "objects")
                    for witness in reconciliation["observations"]:
                        for effect in witness.get("effects", []):
                            if "export_id" in effect:
                                row = await (
                                    await conn.execute(
                                        "SELECT byte_size,media_type,schema_version FROM k05_objects WHERE sha256=%s",
                                        (effect["object_sha256"],),
                                    )
                                ).fetchone()
                                assert row is not None
                                store.read(ObjectDescriptor(effect["object_sha256"], *row))
                                verified_exports += 1
                reconciliation.update(
                    quiescence={
                        "actual_child_processes_exited": all(
                            c.poll() is not None for c in children
                        ),
                        "process_exit_codes": [c.returncode for c in children],
                        "parent_controls_every_SUT_api_worker_in_this_fixture": True,
                    },
                    exports_full_hash_verified=verified_exports,
                    no_effect_after_quiescence=sum(
                        w["status"] == "no_effect_visible_at_observation"
                        for w in reconciliation["observations"]
                    ),
                    remaining_unobserved_or_unsupported=reconciliation["unobserved_or_unsupported"],
                )
                result.update(
                    bootstrap_started_at=started,
                    actor_file_sha256=actor_file_hash,
                    seed_primary_dataset_qualified=False,
                    runtime_tree_sha256_before=source_before,
                    runtime_tree_sha256_after=_identity(root),
                    native_topology={
                        "same_host_generator_sut": True,
                        "api_processes": 1,
                        "api_pool": 4,
                        "worker_processes": 1,
                        "worker_pool": 2,
                        "worker_slots": 2,
                        "preflight_unique_authenticated_principals": len(actors),
                        "source_payload_sizes": "Actual manifest S/M/L; debug dimensions, not primary guide fixture",
                        "composition": "Current production boundaries in seeded-session composition; no full ManagedRuntime/OIDC discovery",
                        "transport": "Actual verified TLS loopback socket, actual Uvicorn and PostgreSQL",
                        "telemetry_scope": "15s process snapshot window is partial and can omit tail; harness HTTP accounting is authoritative for its requests",
                        "active_window_scope": f"{profile.duration_seconds:g}s measured window, {profile.warmup_seconds:g}s warmup; local calibration only, no primary engaged/SLO/50k certification",
                    },
                    postrun_reconciliation=reconciliation,
                )
                if saved:
                    (saved / (prefix + "-result.json")).write_text(
                        json.dumps(result, indent=2) + "\n"
                    )
                assert result["runtime_tree_sha256_after"] == source_before, (
                    "Runtime changed during measurement"
                )
                assert not result["fatal_correctness_code"], (
                    "Correctness oracle stopped calibration; inspect safe result"
                )
                assert result["counts"].get("completed", 0) > 0
                assert rotation or result["counts"].get("jobs_admitted", 0) > 0
                assert rotation or result["counts"].get("exports_verified", 0) > 0
                if rotation:
                    assert result["session_maintenance"]["counts"].get("cookie_rotations", 0) >= 2
                    assert result["rotation_witness"] == {
                        "old_credential_status": 401,
                        "current_credential_status": 200,
                        "cookie_changed": True,
                        "context_unchanged": True,
                        "policy": "Actual rotation_seconds=1,idle_seconds=60,absolute_seconds=120; real elapsed time, no DB timestamp rewrite",
                    }
                assert result["reconciliation"]["offered_equals_terminal_plus_pending"]
                assert result["reconciliation"]["terminal_outcomes_match"]
                assert result["peak_action_inflight"] <= 32
        finally:
            await stop_children()
            for handle in handles:
                handle.close()
            if saved:
                for mode in roles:
                    log = tmp_path / (mode + ".safe.jsonl")
                    if log.exists():
                        content = log.read_bytes()
                        assert secret.encode() not in content
                        (saved / (mode + ".safe.jsonl")).write_bytes(content)
            async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                for role in created_roles:
                    await admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    _run(scenario())


def test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports(tmp_path):
    _exercise(tmp_path)


def test_actual_native_rotation_retains_actor_context_and_old_cookie_is_denied(tmp_path):
    _exercise(tmp_path, rotation=True)



def test_reconciliation_uses_native_job_key_hash():
    async def scenario():
        async with _target() as (pool, schema), pool.connection() as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            result = await reconcile_mutations(conn, schema, {"mutation_reconciliation": {
                "unresolved": 1, "witnesses": [{"workspace_id": "ws_fixture",
                "request_key_sha256": "a" * 64, "operation_id": "P13"}],
            }})
            assert result["observations"][0]["status"] == "no_effect_visible_at_observation"
            assert result["original_slo_outcomes_unchanged"]
    _run(scenario())

"""Isolated native restore rehearsal with real HTTPS, OIDC and durable job execution."""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import uvicorn
from psycopg import sql
from psycopg_pool import AsyncConnectionPool
from test_k02_refresh_postgres import _changed_metrics
from test_k05_legacy_import_postgres import _source
from test_k05_migrations_postgres import _run, _scoped
from test_k05_native_results_postgres import _case
from test_k07_import_journey_postgres import _certificate
from test_k13_retention_delivery_postgres import _ready
from test_k03_keycloak_account_switch import _Form

from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.managed_runtime import ManagedRuntime
from aletheia_lab.hosted.managed_worker import run_worker
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import verify
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted import project_policy_migration, review_migration, upload_job_worker
from aletheia_lab.hosted.recovery_checkpoint import apply_checkpoint, capture_checkpoint
from aletheia_lab.hosted.recovery_points import capture_bundle, file_digest, restore_bundle
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.product.demo_flow import _demo_mapping

PG_IMAGE = "sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94"
IDP_IMAGE = "quay.io/keycloak/keycloak:26.8.2"
VERSION = "product-api/v1-draft"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def port():
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], timeout=70, text=True).strip()


def issued_url(value, origin, prefix):
    url, expected = urlsplit(value), urlsplit(origin)
    assert (url.scheme, url.netloc) == (expected.scheme, expected.netloc)
    assert url.path.startswith(prefix) and not url.fragment
    return value


async def login(base, idp, tls, name, password):
    result = await base.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
    assert result.status_code == 303, result.status_code
    async with httpx.AsyncClient(verify=tls, trust_env=False) as browser:
        page = await browser.get(issued_url(result.headers["location"], idp, "/realms/j0803/"))
        assert page.status_code == 200
        form = _Form()
        form.feed(page.text)
        assert form.action and form.username_visible
        url = issued_url(urljoin(str(page.url), form.action), idp, "/realms/j0803/")
        result = await browser.post(url, data={"username": name, "password": password})
        assert result.status_code in (302, 303), result.status_code
    callback = issued_url(result.headers["location"], str(base.base_url), "/v1/auth/callback")
    complete = await base.get(callback)
    assert complete.status_code == 303, complete.status_code
    session = await checked(base, "GET", "/v1/session", 200)
    value = session.json()
    assert value["actor"]["user_id"] == "owner_" + name
    return value["csrf_token"]


async def checked(client, method, path, code, **kwargs):
    response = await client.request(method, path, **kwargs)
    assert response.status_code == code, (method, path.split("?", 1)[0], response.status_code, code)
    return response


def action(origin, csrf, key, revision=None):
    headers = {"origin": origin, "x-csrf-token": csrf, "idempotency-key": key}
    if revision is not None:
        headers["if-match"] = f'"{revision}"'
    return headers


async def finish(client, job):
    route = f"/v1/workspaces/a/jobs/{job['job_id']}"
    async with asyncio.timeout(75):
        while True:
            response = await checked(client, "GET", route, 200)
            state = response.json()["status"]
            if state == "succeeded":
                return (await checked(client, "GET", route + "/output", 200)).json()
            assert state in ("queued", "running"), state
            await asyncio.sleep(0.25)


async def export(client, route, headers):
    descriptor = (await checked(client, "POST", route + "/exports", 201, headers=headers,
                                json={"format": "json"})).json()
    received = await checked(client, "GET", descriptor["download_url"], 200)
    assert digest(received.content) == descriptor["sha256"]
    assert len(received.content) == descriptor["byte_size"]
    return descriptor


async def roles(database, schema):
    output = {}
    async with await psycopg.AsyncConnection.connect(database) as conn:
        for profile in ("api", "worker"):
            name, password = "j08_" + profile, secrets.token_hex(16)
            await conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)))
            await conn.execute(sql.SQL("GRANT CONNECT ON DATABASE j0803 TO {}").format(sql.Identifier(name)))
            await grant_runtime(conn, schema, name, profile=profile)
            address = urlsplit(database)
            output[profile] = _scoped(address._replace(netloc=f"{name}:{password}@{address.netloc.rsplit('@', 1)[1]}").geturl(), schema)
    return output


async def schema_observations(database, schema):
    result = {}
    async with await psycopg.AsyncConnection.connect(_scoped(database, schema)) as conn:
        result["catalog"] = await verify(conn, schema)
        for name, module in (("project_policy", project_policy_migration), ("reviews", review_migration)):
            rows, read = [], module._rows

            async def capture(connection, query):
                values = await read(connection, query)
                rows.append(values)
                return values

            with patch.object(module, "_rows", capture):
                result[name] = await module.verify(conn, schema)
            result[name + "_metadata"] = rows
    return result


async def reseal_restored_review_check(database, schema, source):
    target = await schema_observations(database, schema)
    expected = ("catalog", "project_policy", "reviews")
    assert all(source[name] for name in expected)
    if all(target[name] for name in expected):
        return False
    assert target["catalog"] and target["project_policy"]
    assert source["project_policy_metadata"] == target["project_policy_metadata"]
    before, after = source["reviews_metadata"], target["reviews_metadata"]
    assert [len(rows) for rows in before] == [len(rows) for rows in after]
    differences = [(lhs, rhs) for old, new in zip(before, after) for lhs, rhs in zip(old, new) if lhs != rhs]
    identity = ("j01_annotations", "j01_annotations_body_check", "c")
    nested = "CHECK ((((length(body) >= 1) AND (length(body) <= 4000)) AND (length(btrim(body)) > 0)))"
    flat = "CHECK (((length(body) >= 1) AND (length(body) <= 4000) AND (length(btrim(body)) > 0)))"
    assert differences == [((*identity, nested, True), (*identity, flat, True))], differences
    async with await psycopg.AsyncConnection.connect(_scoped(database, schema)) as conn:
        await conn.execute(sql.SQL("ALTER TABLE {}.j01_annotations DROP CONSTRAINT j01_annotations_body_check, "
            "ADD CONSTRAINT j01_annotations_body_check CHECK (length(body) BETWEEN 1 AND 4000 "
            "AND length(btrim(body)) > 0)").format(sql.Identifier(schema)))
        assert await review_migration.verify(conn, schema)
    return True


@asynccontextmanager
async def runtime(root, schema, db_roles, api_port, issuer, cert, key, *, stage):
    failure = None

    class DiagnosedRuntime(ManagedRuntime):
        async def _compose(self, stack):
            nonlocal failure
            try:
                return await super()._compose(stack)
            except Exception as error:
                detail = str(error) if type(error) is RuntimeError else getattr(error, "sqlstate", "")
                failure = (type(error).__name__, detail)
                raise

    origin = f"https://127.0.0.1:{api_port}"
    auth = root / "auth.toml"
    auth.write_text(f'default_return_to = "/"\nreturn_to_paths = ["/"]\nadmission_namespace = "{schema}"\n', encoding="utf-8")
    environments = {}
    for profile, db in db_roles.items():
        config = root / (profile + ".toml")
        config.write_text(f'''environment = "local"
public_origin = "{origin}"
[identity]
issuer = "{issuer}"
client_id = "j0803"
redirect_uri = "{origin}/v1/auth/callback"
client_kind = "public"
[storage]
database_url_env = "ALETHEIA_HOSTED_DATABASE_URL"
object_root = {json.dumps(str(root), ensure_ascii=False)}
expected_schema = "{schema}"
expected_database_role = "j08_{profile}"
[api]
port = {api_port}
database_pool_size = 3
backend_timeout_seconds = 10.0
''', encoding="utf-8")
        environments[profile] = {
            **os.environ, "ALETHEIA_HOSTED_CONFIG": str(config), "ALETHEIA_HOSTED_AUTH_POLICY": str(auth),
            "ALETHEIA_HOSTED_DATABASE_URL": db, "ALETHEIA_HOSTED_IDP_CA_FILE": str(cert),
            "ALETHEIA_HOSTED_CSRF_SECRET": secrets.token_urlsafe(48),
            "ALETHEIA_HOSTED_ADMISSION_SECRET": secrets.token_urlsafe(48),
            "ALETHEIA_HOSTED_LOGIN_PROOF_KEY": base64.b64encode(os.urandom(32)).decode(),
        }
    server = uvicorn.Server(uvicorn.Config(DiagnosedRuntime(environments["api"]), host="127.0.0.1", port=api_port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, lifespan="on", log_level="warning"))
    api = asyncio.create_task(server.serve())
    stop = asyncio.Event()
    worker = asyncio.create_task(run_worker(environments["worker"], stop))
    tls = ssl.create_default_context(cafile=str(cert))
    try:
        async with httpx.AsyncClient(base_url=origin, verify=tls, trust_env=False, timeout=20) as client:
            async with asyncio.timeout(30):
                while True:
                    if worker.done():
                        await worker
                        raise AssertionError("Worker exited during startup")
                    assert not api.done(), (stage, failure)
                    try:
                        if (await client.get("/health/ready")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.2)
            yield client, tls, origin
    finally:
        server.should_exit = True
        stop.set()
        await asyncio.wait_for(asyncio.gather(api, worker), 35)


async def exercise(tmp_path, monkeypatch, source_db, target_db, stop_source, resume_source, api_port, idp, cert, key,
                   subjects, password):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (tmp_path / "legacy").mkdir()
    legacy, old_view, _ = _source(tmp_path / "legacy")
    monkeypatch.setenv("K05_TEST_DATABASE_URL", source_db)
    signing_key, pg_bin = os.urandom(32), Path(os.environ["J0803_PG_BIN"])
    bundle, checkpoint_path = tmp_path / "backup", tmp_path / "checkpoint"
    live_route = "/v1/workspaces/a"
    async with _case(source_root) as case:
        schema = await _ready(case)
        importer = LegacyImporter(case.pool, case.objects)
        for workspace, credential in (("a", case.a), ("b", case.b)):
            await importer.import_project(legacy, ImportTarget(legacy.project_id, workspace, "owner_" + workspace),
                                          credential_digest=credential, batch_id="j0803-shared")
        async with case.pool.connection() as conn:
            await conn.execute(files("aletheia_lab.hosted_auth").joinpath("admission_schema.sql").read_text())
            for name, subject in subjects.items():
                await conn.execute("UPDATE hosted_identity_bindings SET issuer=%s,subject=%s WHERE user_id=%s",
                                   (idp + "/realms/j0803", subject, "owner_" + name))
            deletion_revision = (await (await conn.execute(
                "SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (legacy.project_id,))).fetchone())[0]
        catalog_root = source_root / "catalog"
        case.objects.store.root.rename(catalog_root)
        case.catalog = PostgresCatalog(case.pool, PostgresObjects(case.pool, FilesystemObjectStore(catalog_root)))
        before_roles = await roles(source_db, schema)
        before_schema = await schema_observations(before_roles["api"], schema)
        deleted = f"{live_route}/results/{old_view['result']['id']}"
        issuer = idp + "/realms/j0803"
        async with runtime(source_root, schema, before_roles, api_port, issuer, cert, key, stage="source") as (client, tls, origin):
            csrf = await login(client, idp, tls, "a", password)
            cookie = client.cookies.get(SESSION_COOKIE)
            assert cookie
            source = (await checked(client, "POST", live_route + "/sources", 201, content=case.payload,
                headers={**action(origin, csrf, "j0803-before-source"), "content-type": "application/zip"})).json()
            preview = (await checked(client, "POST", live_route + "/jobs", 202,
                headers=action(origin, csrf, "j0803-before-preview"),
                json={"operation": "preview", "source_id": source["source_id"]})).json()
            preview_output = await finish(client, preview)
            confirmed = (await checked(client, "POST", live_route + "/jobs", 202,
                headers=action(origin, csrf, "j0803-before-confirm"), json={"operation": "confirm",
                    "source_id": source["source_id"], "mapping": _demo_mapping(preview_output["result"])})).json()
            confirm_output = await finish(client, confirmed)
            snapshot_id = confirm_output["result"]["snapshot_id"]
            analyzed = (await checked(client, "POST", live_route + "/jobs", 202,
                headers=action(origin, csrf, "j0803-before-analyze"), json={"operation": "analyze",
                    "snapshot_id": snapshot_id, "question": "What is observed in this uploaded dataset?"})).json()
            analyze_output = await finish(client, analyzed)
            assert analyze_output["result"]["snapshot"]["id"] == snapshot_id
            route = live_route + "/results/" + analyze_output["result"]["result"]["id"]
            old_header = (await checked(client, "GET", route + "/header", 200)).json()
            prior_export = await export(client, route, action(origin, csrf, "j0803-before-export"))
            removed_export = await export(client, deleted, action(origin, csrf, "j0803-before-delete-export"))
            prior_outputs = (preview_output, confirm_output, analyze_output)
        captured = await asyncio.to_thread(capture_bundle, source_db, schema, catalog_root, bundle,
                                           pg_bin=pg_bin, signing_key=signing_key)
        tombstone = await PostgresProjectMutations(case.pool, case.catalog).delete(
            credential_digest=case.a, workspace_id="a", project_id=legacy.project_id, expected_revision=deletion_revision)
        async with case.pool.connection() as conn:
            await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'")
        checkpoint = await capture_checkpoint(PostgresRetention(case.pool, case.catalog), checkpoint_path,
                                               schema=schema, signing_key=signing_key)
        restore_root = tmp_path / "target"
        restore_root.mkdir()
        recovery_point_age = (datetime.now(UTC) - datetime.fromisoformat(captured["snapshot_at"])).total_seconds()
        assert 0 <= recovery_point_age < 300
        began = time.monotonic()
        await asyncio.to_thread(stop_source)
        try:
            await restored_journey(target_db, restore_root, bundle, checkpoint_path, captured, checkpoint, tombstone,
                                   signing_key, pg_bin, schema, api_port, idp, cert, key, password, cookie, case,
                                   route, deleted, old_header, prior_export, removed_export, prior_outputs, legacy,
                                   old_view, began, recovery_point_age, before_schema)
        finally:
            await asyncio.to_thread(resume_source)


async def restored_journey(database, root, bundle, checkpoint_path, captured, checkpoint, tombstone,
                           signing_key, pg_bin, schema, api_port, idp, cert, key, password, cookie, case,
                           route, deleted, old_header, prior_export, removed_export, prior_outputs, legacy,
                           old_view, began, recovery_point_age, before_schema):
    outcome = await asyncio.to_thread(restore_bundle, database, bundle, root / "catalog",
                                      pg_bin=pg_bin, signing_key=signing_key)
    assert outcome["status"] == "restored_not_ready" and outcome["objects"] == captured["objects"]
    store = FilesystemObjectStore(root / "catalog")
    inventory = [json.loads(line) for line in (bundle / "objects.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(inventory) == outcome["objects"] > 0
    assert all(file_digest(store.path(row["sha256"])) == row["sha256"] for row in inventory)
    async with AsyncConnectionPool(_scoped(database, schema), min_size=1, max_size=2, open=False) as pool:
        await pool.wait(timeout=10)
        async with pool.connection() as conn:
            assert await verify(conn, schema)
            assert await (await conn.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('j08_api','j08_worker')")).fetchone() == (0,)
        catalog = PostgresCatalog(pool, PostgresObjects(pool, store))
        fence = await apply_checkpoint(PostgresRetention(pool, catalog), checkpoint_path,
            schema=schema, signing_key=signing_key, expected_manifest_sha256=checkpoint["manifest_sha256"])
        assert fence["restored_sessions_revoked"] and fence["deletion_items_applied"] >= 1
        async with pool.connection() as conn:
            assert await (await conn.execute("SELECT count(*) FROM j01_sessions WHERE state='active'")).fetchone() == (0,)
            assert await (await conn.execute("SELECT deletion_tombstone_id FROM k05_projects WHERE workspace_id='a' AND project_id=%s",
                (legacy.project_id,))).fetchone() == (tombstone["tombstone_id"],)
        sealed_seconds = time.monotonic() - began
        reapplied_review_constraint = await reseal_restored_review_check(database, schema, before_schema)
        db_roles = await roles(database, schema)
        target_schema = await schema_observations(db_roles["api"], schema)
        assert all(target_schema[name] for name in ("catalog", "project_policy", "reviews"))
        workspace = "/v1/workspaces/a"
        old_jobs = {job["job_id"] for job in prior_outputs}
        assert len(old_jobs) == 3
        async with runtime(root, schema, db_roles, api_port, idp + "/realms/j0803", cert, key, stage="target") as (client, tls, origin):
            await checked(client, "GET", "/v1/session", 401, headers={"cookie": f"{SESSION_COOKIE}={cookie}"})
            csrf = await login(client, idp, tls, "a", password)
            assert client.cookies.get(SESSION_COOKIE) != cookie
            restored_header = (await checked(client, "GET", route + "/header", 200)).json()
            assert restored_header == old_header
            for old in prior_outputs:
                historical = (await checked(client, "GET", workspace + f"/jobs/{old['job_id']}/output", 200)).json()
                assert historical == old
            old_download = await checked(client, "GET", prior_export["download_url"], 200)
            assert digest(old_download.content) == prior_export["sha256"]
            assert len(old_download.content) == prior_export["byte_size"]
            recreated = await export(client, route, action(origin, csrf, "j0803-after-export"))
            assert recreated["sha256"] == prior_export["sha256"]
            readable_seconds = time.monotonic() - began
            await checked(client, "GET", deleted + "/header", 404)
            await checked(client, "GET", removed_export["download_url"], 404, headers={"range": "bytes=0-15"})
            async with httpx.AsyncClient(base_url=origin, verify=tls, trust_env=False) as other:
                await login(other, idp, tls, "b", password)
                await checked(other, "GET", route + "/header", 404)
                shared = f"/v1/workspaces/b/results/{old_view['result']['id']}/header"
                assert (await checked(other, "GET", shared, 200)).json()["result_id"] == old_view["result"]["id"]
            changed = _changed_metrics(case.payload)
            source = (await checked(client, "POST", workspace + "/sources", 201, content=changed,
                headers={**action(origin, csrf, "j0803-after-source"), "content-type": "application/zip"})).json()
            assert source["sha256"] == digest(changed)
            project = workspace + f"/projects/{restored_header['project_id']}"
            refresh = (await checked(client, "POST", project + "/refresh", 202,
                headers=action(origin, csrf, "j0803-after-refresh", revision=2),
                json={"schema_version": VERSION, "source_id": source["source_id"]})).json()
            refreshed = (await finish(client, refresh))["result"]
            assert refreshed["status"] == "new_snapshot" and refreshed["revision"] == 3
            assert refreshed["previous_snapshot_id"] == restored_header["snapshot_id"]
            assert refreshed["snapshot_id"] != restored_header["snapshot_id"]
            analysis = (await checked(client, "POST", workspace + "/jobs", 202,
                headers=action(origin, csrf, "j0803-after-analyze"), json={"operation": "analyze",
                    "snapshot_id": refreshed["snapshot_id"], "question": "What is observed in the changed dataset?"})).json()
            assert len(old_jobs | {refresh["job_id"], analysis["job_id"]}) == len(old_jobs) + 2
            output = (await finish(client, analysis))["result"]
            assert output["snapshot"]["id"] == refreshed["snapshot_id"]
            new_route = workspace + "/results/" + output["result"]["id"]
            current = (await checked(client, "GET", new_route + "/header", 200)).json()
            assert current["snapshot_id"] == refreshed["snapshot_id"] and current["result_id"] != old_header["result_id"]
            assert (await checked(client, "GET", route + "/header", 200)).json() == old_header
            complete_seconds = time.monotonic() - began
    assert complete_seconds < 1800
    report = {
        "status": "passed", "source_db_paused_through_rehearsal": True,
        "separate_postgresql_clusters": True, "scope": "Single-host logical restore with synthetic data, real Keycloak and HTTPS; no off-host tier claim.",
        "backup_id": captured["backup_id"], "checkpoint_sha256": checkpoint["manifest_sha256"],
        "objects_verifying_sha256": len(inventory), "checkpoint_deletions_applied": fence["deletion_items_applied"],
        "review_check_reapplied_before_runtime_grants": reapplied_review_constraint,
        "unchanged_catalog_policy_and_review_verifiers_passed": True,
        "old_session_status": 401, "new_oidc_logins": 2,
        "deleted_result_status": 404, "deleted_download_range_status": 404,
        "revoked_workspace_status": 404, "retained_other_workspace_result_status": 200,
        "historical_result_and_job_output_exact": True, "original_and_recreated_export_sha256": prior_export["sha256"],
        "original_and_recreated_export_bytes": prior_export["byte_size"],
        "pre_backup_acknowledged_job_effects_checked": len(old_jobs), "pre_backup_acknowledged_job_effects_lost": 0,
        "logical_snapshot_age_at_source_pause_seconds": recovery_point_age,
        "post_restore_new_terminal_jobs": ["refresh", "analyze"], "refresh_revision": refreshed["revision"],
        "seconds_from_source_pause_command_start": {"restore_through_checkpoint": sealed_seconds,
            "oidc_login_historical_read_export": readable_seconds, "confirmed_new_jobs_and_consumer_ready": complete_seconds},
    }
    (Path(os.environ["J0803_EVIDENCE_DIR"]) / "restore-consumer.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def test_j0803_native_restore_from_separate_postgres_to_new_oidc_and_jobs(tmp_path, monkeypatch):
    run = "aletheia-j0803-" + uuid4().hex[:10]
    owned = []
    cert, key = _certificate(tmp_path)
    api_port, idp_port = port(), port()
    origin, idp = f"https://127.0.0.1:{api_port}", f"https://127.0.0.1:{idp_port}"
    subjects = {name: str(uuid4()) for name in ("a", "b")}
    account_pw = secrets.token_urlsafe(21)
    realm = tmp_path / "j0803-realm.json"
    realm.write_text(json.dumps({"realm": "j0803", "enabled": True, "registrationAllowed": False,
        "clients": [{"clientId": "j0803", "enabled": True, "publicClient": True, "standardFlowEnabled": True,
            "redirectUris": [origin + "/v1/auth/callback"], "webOrigins": [origin],
            "attributes": {"pkce.code.challenge.method": "S256"}}],
        "users": [{"id": subject, "username": user, "email": f"{user}@example.invalid",
            "emailVerified": True, "enabled": True, "firstName": "Synthetic", "lastName": "User",
            "credentials": [{"type": "password", "value": account_pw, "temporary": False}]}
            for user, subject in subjects.items()] }), encoding="utf-8")
    idp_image = docker("image", "inspect", IDP_IMAGE, "--format", "{{.Id}}")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", idp_image)
    try:
        db_urls = []
        for label in ("source", "target"):
            name = run + "-" + label
            db_pw = secrets.token_hex(18)
            monkeypatch.setenv("POSTGRES_PASSWORD", db_pw)
            docker("run", "--pull=never", "-d", "--name", name, "-p", "127.0.0.1::5432",
                   "--tmpfs", "/var/lib/postgresql/data:rw,size=536870912", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB=j0803", PG_IMAGE)
            owned.append(name)
            address = docker("port", name, "5432/tcp")
            assert re.fullmatch(r"127\.0\.0\.1:[0-9]+", address)
            db_urls.append(f"postgresql://postgres:{db_pw}@{address}/j0803?" + urlencode({"connect_timeout": 3}))
        monkeypatch.delenv("POSTGRES_PASSWORD")
        name = run + "-idp"
        mounts = [entry for src, target in ((cert, "/j08-cert.pem"), (key, "/j08-key.pem"),
                    (realm, "/opt/keycloak/data/import/j0803-realm.json"))
                  for entry in ("--mount", f"type=bind,source={src},target={target},readonly")]
        docker("run", "--pull=never", "-d", "--name", name, "-p", f"127.0.0.1:{idp_port}:8443", *mounts, idp_image,
               "start-dev", "--http-enabled=false", f"--hostname={idp}", "--https-certificate-file=/j08-cert.pem",
               "--https-certificate-key-file=/j08-key.pem", "--import-realm")
        owned.append(name)
        cluster_ids = set()
        for db in db_urls:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with psycopg.connect(db) as conn:
                        assert conn.execute("SELECT current_setting('server_version_num')::int / 10000").fetchone() == (16,)
                        cluster_ids.add(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
                    break
                except psycopg.OperationalError:
                    assert time.monotonic() < deadline, "Local PostgreSQL did not start"
                    time.sleep(0.3)
        assert len(cluster_ids) == 2, "Source and target must be different PostgreSQL clusters"
        with httpx.Client(verify=ssl.create_default_context(cafile=str(cert)), trust_env=False, timeout=2) as client:
            deadline = time.monotonic() + 80
            while True:
                try:
                    if client.get(idp + "/realms/j0803/.well-known/openid-configuration").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "Local Keycloak did not start"
                time.sleep(0.5)
        run_claim = upload_job_worker._run_claim

        async def observe_claim(jobs, operation, claim, lease, clock=None):
            try:
                return await run_claim(jobs, operation, claim, lease, clock)
            except Exception as error:
                diagnostic = getattr(error, "sqlstate", None) or str(error)
                if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,90}", diagnostic):
                    diagnostic = "details omitted"
                print(f"worker {claim.operation} {type(error).__name__}: {diagnostic}", flush=True)
                raise

        with patch.object(upload_job_worker, "_run_claim", observe_claim):
            _run(exercise(tmp_path, monkeypatch, *db_urls, lambda: docker("pause", owned[0]),
                          lambda: docker("unpause", owned[0]), api_port, idp, cert, key, subjects, account_pw))
        evidence = Path(os.environ["J0803_EVIDENCE_DIR"])
        (evidence / "images.json").write_text(json.dumps({"postgresql_16": PG_IMAGE, "keycloak_26_8_2": idp_image}, indent=2) + "\n")
    finally:
        for name in reversed(owned):
            docker("rm", "-f", "-v", name)

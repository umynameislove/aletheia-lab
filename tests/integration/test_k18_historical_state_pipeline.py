"""Historical state evidence through HTTPS, the upload worker and canonical PostgreSQL objects."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import socket
import ssl
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from test_k05_migrations_postgres import _database, _seed
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k07_import_journey_postgres import _certificate

from aletheia_lab.hosted import persistence_migration
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.historical_state_evidence import HistoricalStateInput
from aletheia_lab.hosted.historical_state_registration import load_historical_state_inputs
from aletheia_lab.hosted.imports_http import ImportApplication, LeasedJobs
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.managed_worker import _supervisor_type
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.preview_leases import PreviewLeases
from aletheia_lab.hosted.preview_migration import migrate as migrate_previews
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_analysis_http import ProjectAnalysisApplication
from aletheia_lab.hosted.project_http import ProjectOperationsApplication
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.source_http import SourceUploadApplication
from aletheia_lab.hosted.upload_job_worker import _guard_claim
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.product.demo_flow import _demo_mapping
from aletheia_lab.product.view import HISTORICAL_STATE_MODEL
from aletheia_lab.project.identity import canonical_project_sha256

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit isolated PostgreSQL fixture required")]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures/historical-state/v1"
API_VERSION = "product-api/v1-draft"
QUESTION = "Was the observed state allowed by the original contract?"


def test_v19_upgrade_preserves_existing_analysis_jobs(monkeypatch):
    import psycopg

    snapshot_id = "upload-snapshot-" + "a" * 64

    async def insert_job(conn, index, model=None):
        request = {"snapshot_id": snapshot_id, "question": QUESTION}
        if model is not None:
            request["analysis_model"] = model
        await conn.execute(
            "INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,"
            "request_hash,request,request_id,state) VALUES(%s,'a','user_a',%s,'analyze',%s,%s,%s::jsonb,%s,'queued')",
            (f"job_{index}", "1" * 64, f"{index:064x}", "3" * 64, json.dumps(request), f"request_{index}"),
        )

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                with monkeypatch.context() as previous:
                    previous.setattr(persistence_migration, "MIGRATIONS", persistence_migration.MIGRATIONS[:18])
                    assert await persistence_migration.migrate(conn, schema) == "created"
                    assert await persistence_migration.verify(conn, schema)
            await _seed(pool)
            old_models = (None, "upload-snapshot-observation/v1", "upload-snapshot-metric-observation/v2",
                          "upload-snapshot-windowed-metric-observation/v3")
            rows = "SELECT job_id,request_hash,request,state FROM j01_jobs ORDER BY job_id"
            async with pool.connection() as conn:
                payload = _zip(0)
                await conn.execute(
                    "INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                    "VALUES('source_a','a','user_a',%s,%s,%s,'{}')",
                    ("0" * 64, hashlib.sha256(payload).hexdigest(), payload),
                )
                await conn.execute(
                    "INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload) VALUES(%s,'a','source_a','{}')",
                    (snapshot_id,),
                )
                for index, model in enumerate(old_models):
                    await insert_job(conn, index, model)
                before = await (await conn.execute(rows)).fetchall()
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await insert_job(conn, len(old_models), HISTORICAL_STATE_MODEL)
            async with pool.connection() as conn:
                assert await persistence_migration.migrate(conn, schema) == "upgraded"
                assert await persistence_migration.verify(conn, schema)
                assert await (await conn.execute(rows)).fetchall() == before
                await insert_job(conn, len(old_models), HISTORICAL_STATE_MODEL)
                assert await persistence_migration.migrate(conn, schema) == "unchanged"

    _run(run())


def _zip(marker):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, body in (
            ("dataset.csv", "id,target\n1,0\n2,1\n"),
            ("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n"),
            ("config.json", json.dumps({"seed": marker})),
        ):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), body)
    return data.getvalue()


def _inputs():
    replay = json.loads((FIXTURES / "replay.json").read_bytes())
    contract = replay["contract"]
    held, authorized = (replay["cases"][name] for name in ("held-A-denied", "B-authorized"))
    assert held["output"] == authorized["output"] == [4, 3]
    assert held["graph_sha256"] == authorized["graph_sha256"]
    assert held["current_alias"] == authorized["current_alias"] == "B"
    selected = [
        ("held", held, contract, "denied"),
        ("authorized", authorized, contract, "allowed"),
        ("alias", {**authorized, "current_alias": "renamed"}, contract, "allowed"),
        ("missing_state", {**authorized, "observation": None}, contract, "unknown"),
        ("missing_contract", authorized, None, "underdefined"),
        ("unsupported", {**authorized, "consumer_id": "unaudited-consumer"}, None, "unsupported"),
    ]
    fresh = os.environ.get("HISTORICAL_STATE_FRESH_INPUT")
    if fresh:
        path = Path(fresh)
        generated = json.loads(path.read_bytes())
        producer_bytes = path.with_suffix(".witness.json").read_bytes()
        producer = json.loads(producer_bytes)
        evidence = HistoricalStateInput.model_validate_json(json.dumps({
            "receipt": generated["cases"]["fresh-sqlite"], "contract": generated["contract"],
        }))
        assert evidence.receipt.execution_mode == "fresh"
        assert hashlib.sha256(producer_bytes).hexdigest() == evidence.receipt.source_receipt_sha256
        assert producer["offer"]["operation_id"] == evidence.receipt.operation_id
        assert producer["offer"]["request_id"] == evidence.receipt.request_id
        assert producer["commit"]["status"] == "committed" and producer["ack"]["status"] == "acked"
        assert producer["reopened"]["answer"]["authorized"] is True
        selected.append(("fresh", evidence.receipt.model_dump(mode="json"),
                         evidence.contract.model_dump(mode="json") if evidence.contract else None, "allowed"))
    return [(name, HistoricalStateInput.model_validate_json(json.dumps({"receipt": receipt, "contract": rule})), expected)
            for name, receipt, rule, expected in selected]


@asynccontextmanager
async def _https(app, sock, root):
    cert, key = _certificate(root)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", ssl_certfile=str(cert),
        ssl_keyfile=str(key), access_log=False, log_level="critical", proxy_headers=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Test API failed to start.")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"https://127.0.0.1:{sock.getsockname()[1]}",
                verify=ssl.create_default_context(cafile=str(cert)), trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        await task


async def _request(client, method, url, expected, **kwargs):
    response = await client.request(method, url, **kwargs)
    assert response.status_code == expected, (url, response.status_code, response.text[:1000])
    return response.json()


async def _complete(client, jobs, operations, ack, operation, headers):
    lease = SecretStr(uuid4().hex * 2)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=(operation,))
    assert claim and claim.job_id == ack["job_id"]
    assert await _guard_claim(jobs, operations, claim, lease, 120, None)
    return (await _request(client, "GET", ack["status_url"] + "/output", 200, headers=headers))["result"]


def _registration(root, payloads):
    # The operator computes project identity independently with the pinned import inputs.
    project = UploadProject(root, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    expected, entries = {}, []
    for name, raw, evidence, _ in payloads:
        if evidence is None:
            continue
        captured = "2026-11-10T00:00:00Z"
        preview = project.preview(raw, captured_at=captured)
        checked = project.confirm(raw, workspace_id="a", source_id="operator-preview",
            mapping=_demo_mapping(preview), captured_at=captured)
        expected[name] = checked.mapping.project_id
        entries.append({"workspace_id": "a", "project_id": checked.mapping.project_id,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "receipt_sha256": canonical_project_sha256(evidence.receipt.model_dump(mode="json")),
            "evidence": evidence.model_dump(mode="json")})
    path = root / "operator.json"
    raw = json.dumps({"schema_version": "historical-state-registrations/v1", "entries": entries}).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="configured digest"):
        load_historical_state_inputs(path, "0" * 64)
    invalid = json.loads(raw)
    invalid["entries"][0]["receipt_sha256"] = "0" * 64
    encoded = json.dumps(invalid).encode()
    path.write_bytes(encoded)
    with pytest.raises(ValueError, match="Invalid historical state"):
        load_historical_state_inputs(path, hashlib.sha256(encoded).hexdigest())
    path.write_bytes(raw)
    return load_historical_state_inputs(path, hashlib.sha256(raw).hexdigest()), expected


def test_historical_state_ordinary_handoff_https_and_durable_scope(tmp_path):
    async def run():
        payloads = [(name, _zip(index), evidence, verdict)
                    for index, (name, evidence, verdict) in enumerate(_inputs())]
        payloads.append(("metric", _zip(len(payloads)), None, None))
        operator = tmp_path / "operator"
        operator.mkdir()
        registrations, expected_projects = _registration(operator, payloads)
        async with _case(tmp_path) as case:
            await _setup(case)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_previews(conn, schema)
            await _exercise_pipeline(case, tmp_path, payloads, registrations, expected_projects)

    _run(run())


async def _exercise_pipeline(case, root, payloads, registrations, expected_projects):
    tokens = CsrfTokens(SecretStr("synthetic-historical-state-csrf-" + "s" * 43))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        origin = f"https://127.0.0.1:{sock.getsockname()[1]}"
        headers = {}
        async with case.pool.connection() as conn:
            for name in ("a", "b"):
                raw = SecretStr(("synthetic-historical-state-session-" + name).ljust(43, "t"))
                proof = tokens.issue("ctx_" + name).get_secret_value()
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id=%s",
                    (credential_digest(raw).get_secret_value(), "ctx_" + name))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s", (proof, "ctx_" + name))
                headers[name] = {"origin": origin, "x-csrf-token": proof,
                                 "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof}"}
        settings = RuntimeSettings.model_validate({"environment": "local", "public_origin": origin,
            "identity": {"issuer": "https://id.example.invalid", "client_id": "fixture",
                         "redirect_uri": origin + "/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(root)}})
        sessions = ServerSessions(PostgresSessionStore(case.pool),
            AuthPolicy(default_return_to="/", return_to_paths=("/",)), timeout_seconds=5, maximum_cookie_bytes=4096)
        jobs = PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog, execution=True)
        leases = PreviewLeases(case.sources)
        worker_root = root / "worker"
        worker_root.mkdir(mode=0o700)
        worker = _supervisor_type()(python=Path(sys.executable).absolute(), source_root=ROOT / "src",
                                    private_root=worker_root, slots=1, timeout_seconds=60)
        operations = CanonicalProjectOperations(case.sources, worker, jobs, leases=leases,
            private_root=worker_root, historical_state_inputs=registrations)

        async def clock():
            return True

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected API path.")

        limits = BodyLimits(65536, 2, 5, 100)
        renderer = ReportRenderer()
        access = CatalogAccess(case.catalog, ScopedCursors(SecretStr("synthetic-historical-state-cursor-" + "c" * 43)), renderer)
        app = CatalogApplication(fallback, access, PostgresProjectAccess(case.pool),
            PostgresReviews(case.pool, case.catalog), sessions, tokens, settings, body_limits=limits, clock_check=clock)
        app = JobApplication(app, jobs, sessions, tokens, settings, body_limits=limits, clock_check=clock)
        app = ProjectAnalysisApplication(app, jobs, sessions, tokens, settings, body_limits=limits, clock_check=clock)
        app = ProjectOperationsApplication(app, jobs, sessions, tokens, settings, body_limits=limits, clock_check=clock)
        app = ImportApplication(app, LeasedJobs(jobs, leases, None), sessions, tokens, settings,
                                leases=leases, body_limits=limits, clock_check=clock)
        app = SourceUploadApplication(app, case.sources, sessions, tokens, settings, body_limits=limits, clock_check=clock)
        try:
            async with _https(app, sock, root) as client:
                results = {}
                for name, raw, evidence, expected in payloads:
                    results[name] = await _journey(client, jobs, operations, headers["a"], case, name,
                                                   raw, evidence, expected, expected_projects.get(name))
                follow = await _follow_up(client, jobs, operations, case, headers["a"], results["authorized"])
                fences = await _check_fences(client, jobs, operations, case, headers, results)
                witness = {"cases": {name: entry[3] for name, entry in results.items()},
                           "follow_up": follow, "fences": fences}
                (root / "historical-state-product-witness.json").write_text(json.dumps(witness, indent=2) + "\n")
        finally:
            await renderer.aclose()
            await worker.aclose()


async def _journey(client, jobs, operations, headers, case, name, raw, evidence, expected, registered_project):
    scope = "/v1/workspaces/a"

    def key(operation):
        return {**headers, "idempotency-key": f"historical-state-{name}-{operation}-run"}

    source = await _request(client, "POST", scope + "/sources", 201, content=raw,
                           headers={**key("source"), "content-type": "application/zip"})
    ack = await _request(client, "POST", scope + "/imports/preview", 202, headers=key("preview"),
        json={"schema_version": API_VERSION, "source_id": source["source_id"]})
    preview = await _complete(client, jobs, operations, ack, "preview", headers)
    ack = await _request(client, "POST", f"{scope}/imports/{preview['preview_id']}/confirm", 202,
        headers=key("confirm"), json={"schema_version": API_VERSION, "mapping": _demo_mapping(preview)})
    stored = await _complete(client, jobs, operations, ack, "confirm", headers)
    async with case.pool.connection() as conn:
        row = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id='a' AND record_id=%s",
                                        (stored["snapshot_id"],))).fetchone()
    project = row[0]
    if registered_project:
        assert project == registered_project
    endpoint = f"{scope}/projects/{project}/analyses"
    command = {"schema_version": API_VERSION, "snapshot_id": stored["snapshot_id"], "question": QUESTION,
               "backend": "historical_state" if evidence else "local_evidence"}
    ack = await _request(client, "POST", endpoint, 202, headers=key("analyze"), json=command)
    view = await _complete(client, jobs, operations, ack, "analyze", headers)
    again = await _request(client, "POST", endpoint, 202, headers=key("analyze"), json=command)
    assert again["job_id"] == ack["job_id"]
    assert await _request(client, "GET", ack["status_url"] + "/output", 200, headers=headers) == {
        "schema_version": API_VERSION, "workspace_id": "a", "job_id": ack["job_id"],
        "operation": "analyze", "result": view,
    }
    await _request(client, "POST", endpoint, 409, headers=key("analyze"), json={**command, "question": "Changed?"})
    if evidence:
        await _request(client, "POST", endpoint, 409, headers=key("analyze"),
                       json={**command, "backend": "local_evidence"})
    record = await case.catalog.read_record(credential_digest=credential_digest(
        SecretStr(("synthetic-historical-state-session-a").ljust(43, "t"))), workspace_id="a",
        project_id=project, record_id=view["result"]["id"])
    assert record.view.model_dump(mode="json") == view
    async with case.pool.connection() as conn:
        publications = await (await conn.execute("SELECT count(*) FROM k06_action_audit "
            "WHERE action='job.publish' AND effect_id=%s", (view["result"]["id"],))).fetchone()
        hashes = await (await conn.execute("SELECT canonical_sha256,object_sha256 FROM k05_records "
            "WHERE workspace_id='a' AND record_id=%s AND snapshot_id=%s",
            (view["result"]["id"], stored["snapshot_id"]))).fetchone()
    assert publications == (1,) and hashes is not None
    raw_object = case.objects.store.path(hashes[1]).read_bytes()
    assert hashlib.sha256(raw_object).hexdigest() == hashes[1]
    assert json.loads(raw_object)["view"] == view
    witness = {"job_id": ack["job_id"], "project_id": project, "snapshot_id": stored["snapshot_id"],
               "source_id": source["source_id"], "source_sha256": source["sha256"], "result_id": view["result"]["id"],
               "result_sha256": hashes[0], "object_sha256": hashes[1], "publications": publications[0]}
    if evidence:
        citation = view["evidence"][0]
        detail = await _request(client, "GET",
            f"{scope}/results/{view['result']['id']}/evidence/{citation['id']}?claim_id={view['claims'][0]['id']}",
            200, headers=headers)
        assert citation["historical_state"]["verdict"] == expected
        assert citation["historical_state"]["execution_mode"] == evidence.receipt.execution_mode
        assert citation["source_sha256"] == source["sha256"]
        assert citation["reproduction_ref"]["record_id"] == stored["snapshot_id"]
        assert detail["evidence"] == citation
        assert citation["historical_state"]["receipt_sha256"] == canonical_project_sha256(
            evidence.receipt.model_dump(mode="json"))
        assert citation["historical_state"]["observed_state"] == (
            evidence.receipt.observation.model_dump(mode="json") if evidence.receipt.observation else None)
        assert citation["historical_state"]["contract_sha256"] == (
            canonical_project_sha256(evidence.contract.model_dump(mode="json")) if evidence.contract else None)
        witness.update({"citation_id": citation["id"], "expected": expected, **citation["historical_state"]})
    else:
        assert view["result"]["status"] == "complete"
        assert all("historical_state" not in row for row in view["evidence"])
        await _request(client, "POST", endpoint, 404, headers=key("unregistered"),
                       json={**command, "backend": "historical_state"})
    return endpoint, command, view, witness


async def _follow_up(client, jobs, operations, case, headers, authorized):
    _, command, view, witness = authorized
    async with case.pool.connection() as conn:
        (revision,) = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' "
            "AND project_id=%s", (witness["project_id"],))).fetchone()
    path = f"/v1/workspaces/a/results/{witness['result_id']}"
    ack = await _request(client, "POST", path + "/follow-ups", 202,
        headers={**headers, "idempotency-key": "historical-state-follow-up-on-history", "if-match": f'"{revision}"'},
        json={"schema_version": API_VERSION, "selection_kind": "claim", "selection_id": view["claims"][0]["id"],
              "question": "What evidence supports that historical decision?"})
    output = await _complete(client, jobs, operations, ack, "follow_up", headers)
    assert output["snapshot_id"] == command["snapshot_id"] and output["parent_result_id"] == witness["result_id"]
    record = await case.catalog.read_record(credential_digest=credential_digest(
        SecretStr(("synthetic-historical-state-session-a").ljust(43, "t"))), workspace_id="a",
        project_id=witness["project_id"], record_id=output["result_id"])
    assert record.view.evidence[0].model_dump(mode="json") == view["evidence"][0]
    return {"job_id": ack["job_id"], **output, "citation_unchanged": True}


async def _check_fences(client, jobs, operations, case, users, results):
    endpoint, command, view, witness = results["authorized"]
    headers = {**users["b"], "idempotency-key": "historical-state-final-scope-fence"}
    await _request(client, "POST", endpoint.replace("/workspaces/a/", "/workspaces/b/"),
                   404, headers=headers, json=command)
    citation = view["evidence"][0]
    citation_path = (f"/v1/workspaces/a/results/{view['result']['id']}/evidence/"
                     f"{citation['id']}?claim_id={view['claims'][0]['id']}")
    await _request(client, "GET", citation_path.replace("/workspaces/a/", "/workspaces/b/"),
                   404, headers=headers)

    async with case.pool.connection() as conn:
        await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 "
                           "WHERE workspace_id='a' AND user_id='owner_b'")
    owner_digest = credential_digest(SecretStr(("synthetic-historical-state-session-a").ljust(43, "t")))
    grants = {"credential_digest": owner_digest, "workspace_id": "a", "user_id": "owner_b"}
    await PostgresProjectAccess(case.pool).set_grant(**grants, project_id=witness["project_id"],
        actions=["read", "write", "export"], expected_revision=0)
    sources = WorkspaceManagement(case.pool)
    await sources.set_source_grant(**grants, source_id=witness["source_id"],
        actions=["read", "write", "export"], expected_revision=0)
    await _request(client, "POST", endpoint, 404, headers=users["a"] | {
        "idempotency-key": "historical-state-wrong-snapshot-key",
    }, json={**command, "snapshot_id": results["held"][1]["snapshot_id"]})

    exported = await _request(client, "POST", f"/v1/workspaces/a/results/{view['result']['id']}/exports", 201,
        headers={**users["b"], "idempotency-key": "historical-state-export-evidence"}, json={"format": "json"})
    download = await client.get(exported["download_url"], headers=users["b"])
    assert download.status_code == 200, download.text[:500]
    assert hashlib.sha256(download.content).hexdigest() == exported["sha256"]
    assert download.json()["evidence"][0]["historical_state"] == citation["historical_state"]

    ack = await _request(client, "POST", endpoint, 202, headers=headers,
                         json={**command, "question": "Is this still the observed state?"})
    lease = SecretStr(uuid4().hex * 2)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("analyze",))
    assert claim and claim.job_id == ack["job_id"]
    prepared = await operations.prepare_initial_analysis(claim)
    await sources.set_source_grant(**grants, source_id=witness["source_id"], actions=[], expected_revision=1)
    with pytest.raises(AuthorizationDenied):
        await jobs.complete_result(claim=claim, lease_digest=lease, record=prepared)
    async with case.pool.connection() as conn:
        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_id=%s",
                                         (prepared.record_id,))).fetchone() == (0,)
    await _request(client, "GET", citation_path, 404, headers=users["b"])
    assert (await client.get(exported["download_url"], headers=users["b"])).status_code == 404
    owner_read = await _request(client, "GET", citation_path, 200, headers=users["a"])
    assert owner_read["evidence"] == citation
    return {"wrong_workspace": 404, "wrong_snapshot": 404, "revoked_publish_count": 0,
            "revoked_job_id": ack["job_id"], "revoked_result_id": prepared.record_id,
            "revoked_citation": 404, "revoked_download": 404, "authorized_history_unchanged": True,
            "export_sha256": exported["sha256"]}

"""Scoped local reviewer across HTTPS, the worker and retained PostgreSQL objects."""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pypdf import PdfReader
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k18_historical_state_pipeline import (
    API_VERSION,
    _complete,
    _inputs,
    _journey,
    _registration,
    _request,
    _running_pipeline,
    _zip,
)

from aletheia_lab.hosted import claim_review_jobs, persistence_migration
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.claim_review_http import ClaimReviewApplication
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound
from aletheia_lab.hosted.preview_migration import migrate as migrate_previews
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.project.identity import canonical_project_sha256

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit isolated PostgreSQL fixture required")]
_SCOPE = "/v1/workspaces/a"
_MODE = "review_claim"


def _path(view):
    return f"{_SCOPE}/results/{view['result']['id']}/claims/{view['claims'][0]['id']}/reviews"


def _body(asserted_verdict):
    return {"schema_version": API_VERSION, "asserted_verdict": asserted_verdict}


def _key(headers, label):
    return {**headers, "idempotency-key": f"synthetic-claim-review-{label}"}


async def _issue(client, view, headers, assertion, label):
    return await _request(client, "POST", _path(view), 202, headers=_key(headers, label), json=_body(assertion))


async def _stored(case, job_id, receipt):
    binding = receipt["binding"]
    async with case.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT r.project_id,r.result_id,r.claim_id,r.object_sha256,r.binding,o.byte_size "
            "FROM k20_claim_reviews r JOIN k05_objects o ON (r.workspace_id,r.object_sha256)=(o.workspace_id,o.sha256) "
            "WHERE r.workspace_id='a' AND r.job_id=%s",
            (job_id,),
        )).fetchone()
        assert row is not None
        assert row[:3] == (binding["project_id"], binding["result_id"], binding["claim_id"])
        assert row[4] == binding
        assert await (await conn.execute(
            "SELECT state,result FROM j01_jobs WHERE workspace_id='a' AND job_id=%s", (job_id,),
        )).fetchone() == ("succeeded", {"review_id": job_id})
        assert await (await conn.execute(
            "SELECT count(*) FROM k06_action_audit WHERE action='job.publish' AND job_id=%s", (job_id,),
        )).fetchone() == (1,)
    payload = case.objects.store.path(row[3]).read_bytes()
    assert len(payload) == row[5] and hashlib.sha256(payload).hexdigest() == row[3]
    assert json.loads(payload) == receipt
    return row[3]


async def _issue_without_ack(client, jobs, view, user, assertion, label, monkeypatch):
    admit = jobs.enqueue_claim_review
    committed = []

    async def lose_response(**request):
        committed.append(await admit(**request))
        raise TimeoutError("Synthetic response failure after commit.")

    with monkeypatch.context() as fault:
        fault.setattr(jobs, "enqueue_claim_review", lose_response)
        await _request(client, "POST", _path(view), 503, headers=_key(user, label), json=_body(assertion))
    receipt, = committed
    return receipt.model_dump(mode="json")


async def _review(client, jobs, operations, case, user, view, witness, assertion, label, decision,
                  *, lost_ack=None):
    ack = (await _issue(client, view, user, assertion, label) if lost_ack is None else
           await _issue_without_ack(client, jobs, view, user, assertion, label, lost_ack))
    result = await _complete(client, jobs, operations, ack, _MODE, user)
    job_id, binding, finding = result["job_id"], result["binding"], result["finding"]
    assert (job_id, result["schema_version"], result["mode"], result["usage"]) == (
        ack["job_id"], "claim-evidence-review/v1", "local_check", "not_incurred",
    )
    claim = view["claims"][0]
    cited = [ev for ev in view["evidence"] if ev["id"] in claim["citation_ids"] + claim["counterevidence_ids"]]
    assert binding == {
        "workspace_id": "a",
        **{key: witness[key] for key in ("project_id", "snapshot_id", "result_id", "source_id", "source_sha256", "result_sha256")},
        "claim_id": claim["id"], "claim_sha256": canonical_project_sha256(claim),
        "evidence_sha256": canonical_project_sha256(cited),
        "receipt_sha256": witness.get("receipt_sha256"), "contract_sha256": witness.get("contract_sha256"),
        "asserted_verdict": assertion, "policy": "historical-state-authorization/v1",
        "candidate_sha256": canonical_project_sha256({key: value for key, value in binding.items() if key != "candidate_sha256"}),
    }
    assert finding["decision"] == decision and finding["reason"]
    if "citation_id" in witness and decision != "tool_error":
        assert finding["evidence_ids"] == [witness["citation_id"]]
        assert finding["execution_mode"] == witness["execution_mode"]
        assert finding["receipt_sha256"] == witness["receipt_sha256"]
        assert finding["contract_sha256"] == witness["contract_sha256"]
        assert finding["observed_state_sha256"] == (witness.get("observed_state") or {}).get("state_sha256")
    assert (await _request(client, "GET", ack["status_url"], 200, headers=user))["status"] == "succeeded"
    assert await _request(client, "GET", f"{_path(view)}/{job_id}", 200, headers=user) == result
    return ack, result, await _stored(case, job_id, result)


async def _check_scope_and_publication(client, jobs, case, users, views, witnesses, original):
    held, authorized = views["held"], views["authorized"]
    endpoint = _path(held)
    foreign_claim = endpoint.replace(held["claims"][0]["id"], authorized["claims"][0]["id"])
    await _request(client, "POST", foreign_claim, 404, headers=_key(users["a"], "cross-claim"), json=_body("denied"))
    await _request(client, "POST", endpoint.replace("/workspaces/a/", "/workspaces/b/"), 404,
                   headers=_key(users["b"], "cross-workspace"), json=_body("denied"))
    await _request(client, "GET", f"{_path(authorized)}/{original['job_id']}", 404, headers=users["a"])
    await _request(client, "POST", endpoint, 409,
                   headers=_key(users["a"], "held-compliant"), json=_body("allowed"))
    await _request(client, "POST", _path(authorized), 409,
                   headers=_key(users["a"], "held-compliant"), json=_body("denied"))

    witness = witnesses["held"]
    owner = credential_digest(SecretStr("synthetic-historical-state-session-a".ljust(43, "t")))
    async with case.pool.connection() as conn:
        await conn.execute("UPDATE j01_memberships SET role='reviewer',revision=revision+1 "
                           "WHERE workspace_id='a' AND user_id='owner_b'")
    grant = {"credential_digest": owner, "workspace_id": "a", "user_id": "owner_b"}
    await PostgresProjectAccess(case.pool).set_grant(**grant, project_id=witness["project_id"],
        actions=["read", "review"], expected_revision=0)
    sources = WorkspaceManagement(case.pool)
    await sources.set_source_grant(**grant, source_id=witness["source_id"], actions=["read", "review"], expected_revision=0)
    archived = f"{endpoint}/{original['job_id']}"
    assert await _request(client, "GET", archived, 200, headers=users["b"]) == original
    await _request(client, "GET", f"{archived}/export?format=json", 404, headers=users["b"])
    ack = await _issue(client, held, users["b"], "denied", "revoke-before-publication")
    lease = SecretStr(uuid4().hex * 2)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=(_MODE,))
    assert claim is not None and claim.job_id == ack["job_id"]
    receipt = await claim_review_jobs.prepare_claim_review(jobs, claim)
    invalid_binding = receipt.binding.model_copy(update={"candidate_sha256": "0" * 64})
    invalid = receipt.model_copy(update={"binding": invalid_binding})
    with pytest.raises(ValueError, match="receipt does not reconcile"):
        await claim_review_jobs.complete_claim_review(jobs, claim=claim, lease_digest=lease, receipt=invalid)
    await sources.set_source_grant(**grant, source_id=witness["source_id"], actions=["read"], expected_revision=1)
    with pytest.raises((AuthorizationDenied, SourceNotFound)):
        await claim_review_jobs.complete_claim_review(jobs, claim=claim, lease_digest=lease, receipt=receipt)
    async with case.pool.connection() as conn:
        assert await (await conn.execute("SELECT count(*) FROM k20_claim_reviews WHERE job_id=%s", (ack["job_id"],))).fetchone() == (0,)
        assert await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s", (ack["job_id"],))).fetchone() == (
            "running", None,
        )
    assert await _request(client, "GET", archived, 200, headers=users["b"]) == original
    await _request(client, "POST", endpoint, 404, headers=_key(users["b"], "read-only-actor"), json=_body("denied"))
    await sources.set_source_grant(**grant, source_id=witness["source_id"], actions=[], expected_revision=2)
    await _request(client, "GET", archived, 404, headers=users["b"])
    assert await _request(client, "GET", archived, 200, headers=users["a"]) == original


async def _check_tool_outcomes(client, jobs, operations, case, user, view, witness, monkeypatch):
    evaluate = claim_review_jobs.evaluate_claim_assertion

    def unavailable(view, claim_id, assertion, snapshot):
        # Inject a missing input at the verifier boundary, keeping the authorized binding intact.
        return evaluate(view, claim_id, assertion, None)

    with monkeypatch.context() as fault:
        fault.setattr(claim_review_jobs, "evaluate_claim_assertion", unavailable)
        checked = await _review(client, jobs, operations, case, user, view, witness, "denied", "tool-unavailable", "tool_error")
    assert checked[1]["finding"]["evidence_ids"] == []

    def offline(*args):
        raise OSError("Synthetic reviewer tool failure")

    ack = await _issue(client, view, user, "denied", "tool-failure")
    with monkeypatch.context() as fault:
        fault.setattr(claim_review_jobs, "evaluate_claim_assertion", offline)
        assert await run_one_upload_job(jobs, operations, lease_seconds=120)
    assert (await _request(client, "GET", ack["status_url"], 200, headers=user))["status"] == "failed"
    await _request(client, "GET", ack["status_url"] + "/output", 404, headers=user)
    await _request(client, "GET", f"{_path(view)}/{ack['job_id']}", 404, headers=user)
    return checked


async def _check_downloads(client, archive, headers, case, object_sha, receipt):
    expected_receipt = case.objects.store.path(object_sha).read_bytes()
    for fmt in ("json", "markdown", "pdf"):
        reply = await client.get(f"{archive}/export?format={fmt}", headers=headers)
        assert reply.status_code == 200, "Claim review download failed."
        assert reply.headers["x-aletheia-receipt-sha256"] == object_sha
        assert reply.headers["etag"] == f'"{hashlib.sha256(reply.content).hexdigest()}"'
        if fmt == "json":
            assert reply.content == expected_receipt and reply.json() == receipt
        else:
            text = (reply.text if fmt == "markdown" else
                    "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(reply.content)).pages))
            normalized = "".join(text.split())
            assert object_sha in normalized and receipt["binding"]["result_id"] in normalized
            assert receipt["finding"]["decision"] in text
            assert receipt["finding"]["evidence_ids"][0] in normalized


def test_claim_review_preserves_original_assertion_scope_and_outlives_job(tmp_path, monkeypatch):
    async def run():
        payloads = [(name, _zip(index), evidence, verdict)
                    for index, (name, evidence, verdict) in enumerate(_inputs())]
        payloads.append(("metric", _zip(len(payloads)), None, None))
        operator = tmp_path / "operator"
        operator.mkdir()
        registrations, projects = _registration(operator, payloads)
        async with AsyncExitStack() as stack:
            # Persist original results under the supported pre-review schema.
            with monkeypatch.context() as older:
                older.setattr(persistence_migration, "MIGRATIONS", persistence_migration.MIGRATIONS[:19])
                case = await stack.enter_async_context(_case(tmp_path))
            await _setup(case)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_previews(conn, schema)
            views, witnesses = {}, {}
            async with _running_pipeline(case, tmp_path, registrations) as (client, jobs, operations, users):
                for name, raw, evidence, verdict in payloads:
                    _, _, views[name], witnesses[name] = await _journey(
                        client, jobs, operations, users["a"], case, name, raw, evidence, verdict, projects.get(name),
                    )
            async with case.pool.connection() as conn:
                with monkeypatch.context() as older:
                    older.setattr(persistence_migration, "MIGRATIONS", persistence_migration.MIGRATIONS[:20])
                    assert await persistence_migration.migrate(conn, schema) == "upgraded"
                    assert await persistence_migration.verify(conn, schema)
                assert await persistence_migration.migrate(conn, schema) == "upgraded"
                assert await persistence_migration.verify(conn, schema)
            reviews = {}
            reviewing = tmp_path / "reviewing"
            reviewing.mkdir()
            async with _running_pipeline(case, reviewing, {}, extra_application=ClaimReviewApplication) as (client, jobs, operations, users):
                for name, assertion, label, expected in [
                    ("held", "denied", "held-compliant", "approve_in_scope"),
                    ("held", "allowed", "held-incorrect", "revise"),
                    ("authorized", "allowed", "lawful", "approve_in_scope"),
                    ("alias", "allowed", "lawful-alias", "approve_in_scope"),
                    ("missing_state", "allowed", "missing-state", "unknown"),
                    ("missing_contract", "allowed", "missing-contract", "unknown"),
                    ("unsupported", "allowed", "unsupported-consumer", "unsupported"),
                    ("metric", "allowed", "unsupported-claim", "unsupported"),
                ]:
                    reviews[label] = await _review(
                        client, jobs, operations, case, users["a"], views[name], witnesses[name],
                        assertion, label, expected, lost_ack=monkeypatch if label == "held-compliant" else None,
                    )
                first = reviews["held-compliant"][1]
                second = reviews["held-incorrect"][1]
                assert first["finding"]["evidence_ids"] == second["finding"]["evidence_ids"]
                assert first["binding"]["result_sha256"] == second["binding"]["result_sha256"]
                assert first["binding"]["candidate_sha256"] != second["binding"]["candidate_sha256"]
                assert reviews["missing-state"][1]["finding"]["reason"] != reviews["missing-contract"][1]["finding"]["reason"]
                if "fresh" in views:
                    reviews["fresh-producer"] = await _review(
                        client, jobs, operations, case, users["a"], views["fresh"], witnesses["fresh"],
                        "allowed", "fresh-producer", "approve_in_scope",
                    )
                    assert reviews["fresh-producer"][1]["finding"]["execution_mode"] == "fresh"
                reviews["tool-unavailable"] = await _check_tool_outcomes(
                    client, jobs, operations, case, users["a"], views["held"], witnesses["held"], monkeypatch,
                )
                await _check_scope_and_publication(client, jobs, case, users, views, witnesses, first)

            restarted = tmp_path / "restarted"
            restarted.mkdir()
            async with _running_pipeline(case, restarted, {}, extra_application=ClaimReviewApplication) as (client, _, _, users):
                ack, result, original_sha256 = reviews["held-compliant"]
                again = await _issue(client, views["held"], users["a"], "denied", "held-compliant")
                assert again["job_id"] == ack["job_id"]
                job_output = await _request(client, "GET", ack["status_url"] + "/output", 200, headers=users["a"])
                assert job_output["operation"] == _MODE and job_output["result"] == result
                assert await _stored(case, ack["job_id"], result) == original_sha256
                witness = witnesses["held"]
                original = await _request(client, "GET", f"{_SCOPE}/results/{witness['result_id']}", 200, headers=users["a"])
                assert original["view"] == views["held"]

                retention = PostgresRetention(case.pool, case.catalog)
                await retention.configure(terminal_job_days=0)
                assert (await retention.expire())["terminal_jobs"] > 0
                await _request(client, "GET", ack["status_url"] + "/output", 404, headers=users["a"])
                archive = f"{_path(views['held'])}/{ack['job_id']}"
                assert await _request(client, "GET", archive, 200, headers=users["a"]) == result
                await _check_downloads(client, archive, users["a"], case, original_sha256, result)
                await _request(client, "GET", archive, 404, headers=users["b"])
                project = witness["project_id"]
                async with case.pool.connection() as conn:
                    revision = (await (await conn.execute(
                        "SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (project,),
                    )).fetchone())[0]
                deleted = await _request(client, "DELETE", f"{_SCOPE}/projects/{project}", 200,
                    headers={**users["a"], "if-match": f'"{revision}"'}, json={})
                assert (deleted["status"], deleted["retention"]) == ("tombstoned", "retained")
                await _request(client, "GET", archive, 404, headers=users["a"])
                await _request(client, "GET", f"{archive}/export?format=json", 404, headers=users["a"])
                assert case.objects.store.path(original_sha256).exists()
                lawful = reviews["lawful"][1]
                other_path = f"{_path(views['authorized'])}/{lawful['job_id']}"
                assert await _request(client, "GET", other_path, 200, headers=users["a"]) == lawful
                (tmp_path / "claim-review-witness.json").write_text(json.dumps({
                    "schema_upgrade": [19, 20, len(persistence_migration.MIGRATIONS)],
                    "reviews": {label: {"receipt": outcome, "object_sha256": digest}
                                for label, (_, outcome, digest) in reviews.items()},
                    "same_id_after_restart": ack["job_id"],
                    "admission_ack_lost_after_commit": True,
                    "retained_review_after_job_expiry": ack["job_id"],
                    "tombstone": deleted,
                }, indent=2) + "\n", encoding="utf-8")

    _run(run())


def test_claim_review_publication_uses_granted_api_and_worker_principals(tmp_path):
    from psycopg import AsyncConnection, sql
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    async def run():
        database = os.environ["K05_TEST_DATABASE_URL"]
        roles = {}
        try:
            async with _case(tmp_path) as case:
                await _setup(case)
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                    await migrate_previews(conn, schema)
                async with _running_pipeline(case, tmp_path, {}) as (client, jobs, operations, users):
                    _, _, view, _ = await _journey(client, jobs, operations, users["a"], case,
                        "runtime-roles", _zip(0), None, None, None)
                digest = credential_digest(SecretStr("synthetic-historical-state-session-a".ljust(43, "t")))
                async with case.pool.connection() as conn:
                    for profile in ("api", "worker"):
                        name, password = f"k20_{profile}_{uuid4().hex}", secrets.token_urlsafe(32)
                        await conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
                        roles[profile] = (name, password)
                        await grant_runtime(conn, schema, name, profile=profile, audit=True)
                async with AsyncExitStack() as stack:
                    jobs = {}
                    for profile, (name, password) in roles.items():
                        url = make_conninfo(database, user=name, password=password, options=f"-csearch_path={schema},pg_catalog")
                        pool = await stack.enter_async_context(
                            AsyncConnectionPool(url, min_size=1, max_size=2, open=False))
                        await pool.wait(timeout=10)
                        async with pool.connection() as conn:
                            assert await (await conn.execute(
                                "SELECT current_user,has_table_privilege(current_user,'k20_claim_reviews','SELECT'),"
                                "has_any_column_privilege(current_user,'k20_claim_reviews','INSERT'),"
                                "has_any_column_privilege(current_user,'k20_claim_reviews','UPDATE'),"
                                "has_table_privilege(current_user,'k20_claim_reviews','DELETE')",
                            )).fetchone() == (name, profile == "api", profile == "worker", False, False)
                        catalog = PostgresCatalog(pool, PostgresObjects(pool, case.objects.store))
                        sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                                  maximum_entries=10, catalog=catalog)
                        jobs[profile] = PostgresJobs(pool, sources, audit=True, catalog=catalog, execution=True)
                    api, worker = jobs["api"], jobs["worker"]
                    scope = {"credential_digest": digest, "workspace_id": "a", "result_id": view["result"]["id"],
                             "claim_id": view["claims"][0]["id"]}
                    queued = await api.enqueue_claim_review(**scope, asserted_verdict="allowed",
                                                             request_key=SecretStr("synthetic-claim-review-roles"))
                    lease = SecretStr(uuid4().hex * 2)
                    claim = await worker.claim(lease_digest=lease, lease_seconds=120, operations=(_MODE,))
                    assert claim is not None and claim.job_id == queued.job_id
                    prepared = await claim_review_jobs.prepare_claim_review(worker, claim)
                    assert prepared.finding.decision == "unsupported"
                    assert await claim_review_jobs.complete_claim_review(worker, claim=claim, lease_digest=lease, receipt=prepared)
                    assert await api.read_claim_review(**scope, review_id=queued.job_id) == prepared
        finally:
            if roles:
                async with await AsyncConnection.connect(database, autocommit=True) as admin:
                    for name, _ in roles.values():
                        await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))

    _run(run())

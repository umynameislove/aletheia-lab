"""Cancellation/retry transactions and publication races on canonical PostgreSQL."""
from __future__ import annotations

import asyncio
import io
import os
import time
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_k04_authorization_postgres import BarrierPool
from test_k04_http_postgres import ORIGIN, _http_identity
from test_k04_member_pages_postgres import SECRET, _canonical
from test_k04_workspaces_postgres import _run

from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.postgres_jobs import JobStateConflict, PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceConflict, SourceNotFound
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def _sources(pool):
    return PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)


async def _setup(pool, tmp_path):
    async with pool.connection() as conn:
        present = await (await conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid='j01_jobs'::regclass "
            "AND attname='retry_of_job_id' AND NOT attisdropped")).fetchone()
        assert present == (1,), "Approved K-05 job-control migration is required"
    csrf = CsrfTokens(SECRET)
    identities = {user: await _http_identity(pool, user, char * 43, csrf) for user, char in
        (("owner", "a"), ("analyst", "b"), ("peer", "c"), ("admin", "d"), ("viewer", "e"), ("foreign", "f"))}
    accounts = {user: value[0] for user, value in identities.items()}
    manager = WorkspaceManagement(pool)
    workspace = (await manager.create(credential_digest=accounts["owner"], name="Synthetic A"))["workspace_id"]
    foreign = (await manager.create(credential_digest=accounts["foreign"], name="Synthetic B"))["workspace_id"]
    for user, role in (("analyst", "analyst"), ("peer", "analyst"), ("admin", "admin"), ("viewer", "viewer")):
        invitation = await manager.invite(credential_digest=accounts["owner"], workspace_id=workspace,
            user_id=user, role=role, expires_in_seconds=600)
        await manager.accept_invite(credential_digest=accounts[user], token=SecretStr(invitation["token"]))
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
        archive.writestr("config.json", '{"seed":42}\n')
    payload, sources = data.getvalue(), _sources(pool)
    source = await sources.put(credential_digest=accounts["owner"], workspace_id=workspace,
                                request_key=SecretStr("synthetic-control-source"), payload=payload)
    for user in ("analyst", "peer"):
        await manager.set_source_grant(credential_digest=accounts["owner"], workspace_id=workspace,
            source_id=source.source_id, user_id=user, actions=["read", "write"], expected_revision=0)
    await manager.set_source_grant(credential_digest=accounts["owner"], workspace_id=workspace,
        source_id=source.source_id, user_id="viewer", actions=["read", "export"], expected_revision=0)
    project = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    captured = "2026-11-10T00:00:00Z"
    candidates = project.preview(payload, captured_at=captured)["mapping_candidates"]
    target, metric, config = (candidates[key][0] for key in ("targets", "metric_sources", "configs"))
    mapping = {"target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
        "target_field": "target", "identifier_field": "id"}, "metric_sources": [{"mapping_id": metric["mapping_id"],
        "project_item_id": metric["project_item_id"], "format": "csv", "records_path": [], "metric_name_field": "name",
        "metric_value_field": "value", "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": "baseline", "config_item_ids": []}, {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]}],
        "baseline_run_id": "baseline", "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}]}
    snapshot = project.confirm(payload, workspace_id=workspace, source_id=source.source_id, mapping=mapping, captured_at=captured)
    return SimpleNamespace(pool=pool, manager=manager, sources=sources, jobs=PostgresJobs(pool, sources), accounts=accounts,
        identities=identities, csrf=csrf, workspace=workspace, foreign=foreign, source=source, mapping=mapping, snapshot=snapshot)


async def _enqueue(env, *, key, operation="preview"):
    request = {"source_id": env.source.source_id}
    if operation == "confirm":
        request["mapping"] = env.mapping
    return await env.jobs.enqueue(credential_digest=env.accounts["analyst"], workspace_id=env.workspace,
                                  operation=operation, request_key=SecretStr(key), request=request)


def test_real_cancel_requires_current_creator_or_manager_and_preserves_terminal_receipts(tmp_path):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            receipt = await _enqueue(env, key="synthetic-cancel-queued")
            for user, workspace in (("peer", env.workspace), ("viewer", env.workspace), ("foreign", env.workspace), ("owner", env.foreign)):
                with pytest.raises(SourceNotFound):
                    await env.jobs.cancel(credential_digest=env.accounts[user], workspace_id=workspace, job_id=receipt.job_id)
            assert (await env.jobs.status(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=receipt.job_id)).status == "queued"
            cancelled = await env.jobs.cancel(credential_digest=env.accounts["admin"], workspace_id=env.workspace, job_id=receipt.job_id)
            assert cancelled.status == "cancelled"
            assert await env.jobs.cancel(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=receipt.job_id) == cancelled
            assert await env.jobs.claim(lease_digest=SecretStr("1" * 64), lease_seconds=30) is None
            running = await _enqueue(env, key="synthetic-cancel-running")
            lease = SecretStr("2" * 64)
            claim = await env.jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == running.job_id
            await env.jobs.cancel(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=running.job_id)
            assert await env.jobs.complete(claim=claim, lease_digest=lease, result={"unpublished": True}) is False
            assert await env.jobs.fail(claim=claim, lease_digest=lease) is False
            with pytest.raises(SourceNotFound):
                await env.jobs.output(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=running.job_id)
            completed = await _enqueue(env, key="synthetic-cancel-completed")
            claim = await env.jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == completed.job_id and await env.jobs.complete(claim=claim, lease_digest=lease, result={"published": True})
            terminal = await env.jobs.cancel(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=completed.job_id)
            assert terminal.status == "succeeded"
            with pytest.raises(JobStateConflict):
                await env.jobs.retry(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=completed.job_id,
                                     request_key=SecretStr("synthetic-retry-succeeded-denied"))
            assert (await env.jobs.output(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=completed.job_id))["result"] == {"published": True}
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,result,lease_digest,lease_until,request_id FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("cancelled", None, None, None, receipt.request_id)

    _run(run())


@pytest.mark.parametrize("schedule", ["cancel_first", "publish_first", "simultaneous"])
def test_real_cancel_snapshot_publication_race_has_one_terminal_truth_and_no_orphan(tmp_path, schedule):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            receipt = await _enqueue(env, key="synthetic-cancel-snapshot", operation="confirm")
            lease = SecretStr("3" * 64)
            claim = await env.jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            if schedule == "cancel_first":
                barrier = BarrierPool(pool, "SELECT job_id FROM j01_jobs WHERE job_id=")
                blocked = PostgresJobs(barrier, _sources(barrier))
                publication = asyncio.create_task(blocked.complete_snapshot(claim=claim, lease_digest=lease, snapshot=env.snapshot))
                await asyncio.wait_for(barrier.entered.wait(), 5)
                cancellation = await env.jobs.cancel(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=receipt.job_id)
                barrier.release.set()
                published = await asyncio.wait_for(publication, 5)
            elif schedule == "publish_first":
                barrier = BarrierPool(pool, "SELECT state,creator_id,operation,request FROM j01_jobs ")
                blocked = PostgresJobs(barrier, _sources(barrier))
                cancel = asyncio.create_task(blocked.cancel(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=receipt.job_id))
                await asyncio.wait_for(barrier.entered.wait(), 5)
                published = await env.jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=env.snapshot)
                barrier.release.set()
                cancellation = await asyncio.wait_for(cancel, 5)
            else:
                barrier = asyncio.Barrier(2)

                async def publish():
                    await barrier.wait()
                    return await env.jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=env.snapshot)

                async def cancel():
                    await barrier.wait()
                    return await env.jobs.cancel(credential_digest=env.accounts["owner"], workspace_id=env.workspace, job_id=receipt.job_id)

                published, cancellation = await asyncio.wait_for(asyncio.gather(publish(), cancel()), 10)
            expected = "succeeded" if published else "cancelled"
            assert cancellation.status == expected
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,request_id FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == (expected, receipt.request_id)
                assert await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots WHERE snapshot_id=%s", (env.snapshot.snapshot_id,))).fetchone() == (int(published),)

    _run(run())


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_real_retry_is_new_parent_bound_idempotent_and_original_stays_terminal(tmp_path, terminal):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            original = await _enqueue(env, key="synthetic-retry-original")
            with pytest.raises(JobStateConflict):
                await env.jobs.retry(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=original.job_id,
                                     request_key=SecretStr("synthetic-retry-queued-denied"))
            if terminal == "failed":
                lease = SecretStr("4" * 64)
                claim = await env.jobs.claim(lease_digest=lease, lease_seconds=30)
                assert await env.jobs.fail(claim=claim, lease_digest=lease)
            else:
                await env.jobs.cancel(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=original.job_id)
            for user in ("peer", "viewer", "foreign"):
                with pytest.raises(SourceNotFound):
                    await env.jobs.retry(credential_digest=env.accounts[user], workspace_id=env.workspace, job_id=original.job_id,
                                         request_key=SecretStr("synthetic-retry-forbidden-" + user))
            kwargs = dict(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=original.job_id,
                          request_key=SecretStr("synthetic-retry-child-one"))
            first, repeated = await asyncio.gather(env.jobs.retry(**kwargs), env.jobs.retry(**kwargs))
            assert first == repeated and first.job_id != original.job_id
            another = await env.jobs.retry(**{**kwargs, "request_key": SecretStr("synthetic-retry-child-two")})
            assert another.job_id != first.job_id
            with pytest.raises(SourceConflict):
                await env.jobs.enqueue(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, operation="preview",
                    request_key=kwargs["request_key"], request={"source_id": env.source.source_id})
            with pytest.raises(SourceConflict):
                await env.jobs.retry(**{**kwargs, "request_key": SecretStr("synthetic-retry-original")})
            second_original = await _enqueue(env, key="synthetic-second-parent")
            await env.jobs.cancel(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=second_original.job_id)
            with pytest.raises(SourceConflict):
                await env.jobs.retry(**{**kwargs, "job_id": second_original.job_id})
            managed = await env.jobs.retry(credential_digest=env.accounts["admin"], workspace_id=env.workspace,
                job_id=original.job_id, request_key=SecretStr("synthetic-admin-retry-child"))
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,request_id FROM j01_jobs WHERE job_id=%s", (original.job_id,))).fetchone() == (terminal, original.request_id)
                assert await (await conn.execute("SELECT retry_of_job_id,creator_id,credential_digest=%s FROM j01_jobs WHERE job_id=%s",
                    (env.accounts["analyst"].get_secret_value(), first.job_id))).fetchone() == (original.job_id, "analyst", True)
                assert await (await conn.execute("SELECT retry_of_job_id,creator_id FROM j01_jobs WHERE job_id=%s", (managed.job_id,))).fetchone() == (original.job_id, "admin")
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (5,)

    _run(run())


class RetryBarrierPool:
    def __init__(self, pool):
        self.pool, self.count = pool, 0
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            self.count += 1
            if self.count == 2:
                self.entered.set()
                await self.release.wait()
            yield conn


@pytest.mark.parametrize("revoke", ["membership", "write_grant"])
def test_real_revoke_between_retry_request_copy_and_enqueue_creates_no_effect(tmp_path, revoke):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            original = await _enqueue(env, key="synthetic-revoked-retry-parent")
            await env.jobs.cancel(credential_digest=env.accounts["analyst"], workspace_id=env.workspace, job_id=original.job_id)
            barrier = RetryBarrierPool(pool)
            blocked = PostgresJobs(barrier, _sources(barrier))
            retry = asyncio.create_task(blocked.retry(credential_digest=env.accounts["analyst"], workspace_id=env.workspace,
                job_id=original.job_id, request_key=SecretStr("synthetic-revoked-retry-child")))
            await asyncio.wait_for(barrier.entered.wait(), 5)
            if revoke == "membership":
                await env.manager.mutate_member(credential_digest=env.accounts["owner"], workspace_id=env.workspace,
                    user_id="analyst", expected_revision=1, active=False)
            else:
                await env.manager.set_source_grant(credential_digest=env.accounts["owner"], workspace_id=env.workspace,
                    source_id=env.source.source_id, user_id="analyst", actions=["read"], expected_revision=1)
            committed = time.perf_counter()
            barrier.release.set()
            with pytest.raises(SourceNotFound):
                await asyncio.wait_for(retry, 5)
            latency = time.perf_counter() - committed
            assert latency <= 5
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                assert await (await conn.execute("SELECT state,request_id FROM j01_jobs WHERE job_id=%s", (original.job_id,))).fetchone() == ("cancelled", original.request_id)
            print(f"K04 revoke-to-denied retry: {latency:.6f}s; local PostgreSQL")

    _run(run())


def test_real_http_cancel_retry_acl_and_terminal_truth(tmp_path):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                                       timeout_seconds=3, maximum_cookie_bytes=4096)

            async def clock():
                return True

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected job control fallback")

            app = JobApplication(fallback, env.jobs, sessions, env.csrf, runtime,
                                  body_limits=BodyLimits(8192, 1, 3, 10), clock_check=clock)
            original = await _enqueue(env, key="synthetic-http-control-parent")
            base = "/v1/workspaces/" + env.workspace + "/jobs/" + original.job_id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                for user in ("peer", "viewer", "foreign"):
                    denied = await client.post(base + "/cancel", json={}, headers=env.identities[user][1])
                    assert denied.status_code == 404 and denied.json()["code"] == "not_found"
                cancel = await client.post(base + "/cancel", json={}, headers=env.identities["owner"][1])
                assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
                assert (await client.get(base + "/output", headers=env.identities["owner"][1])).status_code == 404
                headers = {**env.identities["analyst"][1], "Idempotency-Key": "synthetic-http-control-child"}
                first = await client.post(base + "/retry", json={}, headers=headers)
                repeated = await client.post(base + "/retry", json={}, headers=headers)
                assert first.status_code == repeated.status_code == 202 and first.json()["job_id"] == repeated.json()["job_id"]
                assert first.json()["job_id"] != original.job_id
                reused = await client.post(first.json()["status_url"] + "/retry", json={}, headers=headers)
                assert reused.status_code == 409 and reused.json()["code"] == "idempotency_conflict"
                denied = await client.post(first.json()["status_url"] + "/retry", json={},
                    headers={**headers, "Idempotency-Key": "synthetic-http-terminal-new-key"})
                assert denied.status_code == 409 and denied.json()["code"] == "job_state_conflict"
                assert (await client.get(base, headers=env.identities["analyst"][1])).json()["status"] == "cancelled"
                denied = await client.post(base + "/retry", json={}, headers={**headers, "X-CSRF-Token": "wrong"})
                assert denied.status_code == 403
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (2,)

    _run(run())

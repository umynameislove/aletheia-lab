"""Exercise the provider attempt ledger through HTTPS and a guarded fake provider."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k18_historical_state_pipeline import (
    _inputs,
    _journey,
    _registration,
    _request,
    _running_pipeline,
    _zip,
)
from test_k20_claim_review_postgres import _body, _key, _path, _stored

from aletheia_lab.hosted import claim_provider_attempts, claim_review_jobs
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.claim_provider_plan import ClaimProviderPlan
from aletheia_lab.hosted.claim_review_http import ClaimReviewApplication
from aletheia_lab.hosted.claim_review_provider import (
    CLAIM_PROVIDER_ENDPOINT,
    CLAIM_PROVIDER_HOLD_MICROUSD,
    CLAIM_PROVIDER_MAX_OUTPUT_TOKENS,
    CLAIM_PROVIDER_MODEL,
    prepare_claim_review_provider,
)
from aletheia_lab.hosted.postgres_sources import SourceConflict, SourceNotFound
from aletheia_lab.hosted.preview_migration import migrate as migrate_previews
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.upload_job_worker import _guard_claim
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.sessions import credential_digest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit isolated PostgreSQL fixture required")]

_TEST_KEY = SecretStr("synthetic-provider-key")
_OPERATION = "review_claim"
_REQUEST_ID = "req_k21_synthetic_receipt"


def _credential(user):
    return credential_digest(SecretStr(f"synthetic-historical-state-session-{user}".ljust(43, "t")))


@asynccontextmanager
async def _seed(tmp_path):
    operator = tmp_path / "operator"
    operator.mkdir()
    name, input_evidence, expected = next(item for item in _inputs() if item[0] == "held")
    raw = _zip(0)
    registrations, projects = _registration(operator, [(name, raw, input_evidence, expected)])
    async with _case(tmp_path) as case:
        await _setup(case)
        async with case.pool.connection() as conn:
            schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
            await migrate_previews(conn, schema)
        async with _running_pipeline(case, tmp_path, registrations,
                                     extra_application=ClaimReviewApplication) as pipeline:
            client, jobs, operations, users = pipeline
            _, _, view, witness = await _journey(
                client, jobs, operations, users["a"], case, name, raw, input_evidence, expected, projects[name],
            )
            yield case, pipeline, view, witness


async def _plan(jobs, view, *, user="a"):
    result = view["result"]["id"]
    claim = view["claims"][0]["id"]
    binding, canonical, snapshot = await claim_review_jobs._load_claim(
        jobs, _credential(user), "a", result, claim, "allowed",
    )
    proposed = prepare_claim_review_provider(canonical, claim, "allowed", snapshot)
    assert proposed is not None and proposed.finding.decision == "revise"
    plan = ClaimProviderPlan(
        schema_version="claim-provider-plan/v1", workspace_id="a", actor_id=f"owner_{user}",
        candidate_sha256=binding.candidate_sha256, request_sha256=proposed.request_sha256,
        endpoint=CLAIM_PROVIDER_ENDPOINT, model=CLAIM_PROVIDER_MODEL, max_calls=1,
        maximum_output_tokens=CLAIM_PROVIDER_MAX_OUTPUT_TOKENS, budget_microusd=CLAIM_PROVIDER_HOLD_MICROUSD,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    jobs.claim_provider_plan, jobs.claim_provider_key = plan, _TEST_KEY
    return plan


async def _issue(client, view, user, label, *, assertion="allowed", expected=202):
    return await _request(client, "POST", _path(view), expected,
                          headers=_key(user, label), json={**_body(assertion), "mode": "provider"})


async def _claim(jobs, job_id):
    lease = SecretStr(uuid4().hex * 2)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=(_OPERATION,))
    assert claim is not None and claim.job_id == job_id
    return lease, claim


async def _ledger(case, job_id):
    async with case.pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT attempt_id,plan_sha256,request_sha256,held_microusd,dispatched_at,"
            "deadline_at,received_at,outcome FROM k21_claim_provider_attempts "
            "WHERE workspace_id='a' AND job_id=%s", (job_id,),
        )).fetchall()
    row, = rows
    return dict(zip(("attempt_id", "plan_sha256", "request_sha256", "held_microusd", "dispatched_at",
                     "deadline_at", "received_at", "outcome"), row, strict=True))


def _simulate(monkeypatch, case, job_id, plan, *, outcome="complete", delayed=None):
    actual = claim_provider_attempts.dispatch_claim_review_provider
    observed = []

    async def provider(request):
        # The mock records a POST only after independently seeing a committed hold.
        ledger = await _ledger(case, job_id)
        assert ledger["plan_sha256"] == plan.sha256
        assert sha256(request.content).hexdigest() == ledger["request_sha256"] == plan.request_sha256
        assert ledger["held_microusd"] == CLAIM_PROVIDER_HOLD_MICROUSD
        assert request.headers["x-client-request-id"] == ledger["attempt_id"]
        assert ledger["dispatched_at"] < ledger["deadline_at"]
        assert ledger["outcome"] is None and ledger["received_at"] is None
        observed.append(ledger["attempt_id"])
        if delayed is not None:
            await delayed(ledger)
        if outcome == "lost_provider_ack":
            raise httpx2.ReadTimeout("Synthetic loss after provider received request.", request=request)
        if outcome == "worker_exit":
            raise asyncio.CancelledError()
        return httpx2.Response(
            200, headers={"x-request-id": _REQUEST_ID}, json={
                "id": "resp_k21_synthetic", "object": "response", "model": CLAIM_PROVIDER_MODEL,
                "status": "completed", "service_tier": "default",
                "output": [{
                    "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps({"decision": "approve_in_scope"})}],
                }],
                "usage": {"input_tokens": 325, "output_tokens": 12, "total_tokens": 337},
            },
        )

    async def intercept(prepared, **kwargs):
        return await actual(prepared, **kwargs, transport=httpx2.MockTransport(provider))

    monkeypatch.setattr(claim_provider_attempts, "dispatch_claim_review_provider", intercept)
    return observed


@pytest.mark.parametrize("outcome", ["complete", "lost_provider_ack", "worker_exit"])
def test_provider_plan_exact_scope_and_reclaimed_lease_reuses_one_attempt(tmp_path, monkeypatch, outcome):
    async def run():
        async with _seed(tmp_path) as (case, (client, jobs, _, users), view, _):
            user = users["a"]
            await _issue(client, view, user, "unapproved", expected=404)
            plan = await _plan(jobs, view)
            for field, value in (("actor_id", "owner_b"), ("candidate_sha256", "a" * 64),
                                 ("request_sha256", "b" * 64), ("workspace_id", "b")):
                jobs.claim_provider_plan = plan.model_copy(update={field: value})
                await _issue(client, view, user, f"mismatch-{field}", expected=404)
            jobs.claim_provider_plan = plan
            await _issue(client, view, user, "changed-assertion", assertion="denied", expected=404)
            ack = await _issue(client, view, user, "provider-once")
            job_id = ack["job_id"]
            await _issue(client, view, user, "provider-once", assertion="denied", expected=409)
            lease, claim = await _claim(jobs, job_id)
            observed = _simulate(monkeypatch, case, job_id, plan, outcome=outcome)
            # Keep provider execution separate from publication and simulate a
            # worker that loses its response before writing any outcome.
            if outcome == "worker_exit":
                with pytest.raises(asyncio.CancelledError):
                    await claim_review_jobs.dispatch_provider_review(jobs, claim, lease)
            else:
                await claim_review_jobs.dispatch_provider_review(jobs, claim, lease)
            before = await _ledger(case, job_id)
            assert observed == [before["attempt_id"]]
            expected_status = {"complete": "completed", "lost_provider_ack": "unresolved", "worker_exit": None}
            assert (before["outcome"] or {}).get("status") == expected_status[outcome]
            await _request(client, "GET", f"{_path(view)}/{job_id}", 404, headers=user)
            async with case.pool.connection() as conn:
                await conn.execute(
                    "UPDATE j01_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                    (job_id,),
                )

            restarted = tmp_path / "restart"
            restarted.mkdir()
            async with _running_pipeline(case, restarted, {}, extra_application=ClaimReviewApplication) as other:
                new_client, new_jobs, operations, new_users = other
                # An ACK retry can read the durable admission even after the
                # operator removed the one-call plan and key.
                retry = await _issue(new_client, view, new_users["a"], "provider-once")
                assert retry["job_id"] == job_id
                new_lease, new_claim = await _claim(new_jobs, job_id)
                assert new_lease != lease
                assert not await claim_review_jobs.complete_claim_review(
                    new_jobs, claim=claim, lease_digest=lease, receipt=None,
                )
                assert await _guard_claim(new_jobs, operations, new_claim, new_lease, 120, None)
                assert observed == [before["attempt_id"]]
                assert await _ledger(case, job_id) == before
                receipt = await _request(new_client, "GET", f"{_path(view)}/{job_id}", 200, headers=new_users["a"])
                assert receipt["finding"]["decision"] == "revise"
                assert receipt["schema_version"] == "claim-evidence-review/v2" and receipt["mode"] == "provider"
                assert receipt["attempt"]["attempt_id"] == before["attempt_id"]
                assert receipt["attempt"]["outcome"] == before["outcome"]
                await _stored(case, job_id, receipt)
                if outcome != "complete":
                    assert receipt["usage"] == "unsettled"
                    assert (before["outcome"] or {}).get("provider_request_id") is None
                    assert (before["outcome"] or {}).get("input_tokens") is None
                else:
                    assert receipt["usage"] == "settled"
                    assert before["outcome"] == {
                        "status": "completed", "provider_request_id": _REQUEST_ID,
                        "input_tokens": 325, "output_tokens": 12, "provider_decision": "approve_in_scope",
                        "agrees_with_host": False,
                    }
                # A new HTTP key can create another job for the same approved
                # candidate. It cannot consume a second provider attempt.
                new_jobs.claim_provider_plan, new_jobs.claim_provider_key = plan, _TEST_KEY
                another = await _issue(new_client, view, new_users["a"], "another-key-same-plan")
                assert another["job_id"] != job_id
                another_lease, another_claim = await _claim(new_jobs, another["job_id"])
                with pytest.raises(SourceConflict):
                    await claim_review_jobs.dispatch_provider_review(new_jobs, another_claim, another_lease)
                assert observed == [before["attempt_id"]]

    _run(run())


def test_provider_receipt_late_after_original_deadline_stays_in_ledger(tmp_path, monkeypatch):
    async def run():
        async with _seed(tmp_path) as (case, (client, jobs, _, users), view, _):
            plan = await _plan(jobs, view)
            job_id = (await _issue(client, view, users["a"], "late-receipt"))["job_id"]
            lease, claim = await _claim(jobs, job_id)
            async with case.pool.connection() as conn:
                # Shorten the fixture's budget before dispatch so the test need not wait for production TTL.
                original_deadline, = await (await conn.execute(
                    "UPDATE j01_jobs SET deadline_at=clock_timestamp()+interval '4 seconds' "
                    "WHERE job_id=%s RETURNING deadline_at", (job_id,),
                )).fetchone()

            async def release_after_deadline(ledger):
                assert ledger["deadline_at"] == original_deadline
                async with case.pool.connection() as conn:
                    await conn.execute(
                        "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM (%s::timestamptz-clock_timestamp())),0)+0.1)",
                        (original_deadline,),
                    )

            observed = _simulate(monkeypatch, case, job_id, plan, delayed=release_after_deadline)
            await claim_review_jobs.dispatch_provider_review(jobs, claim, lease)
            ledger = await _ledger(case, job_id)
            assert observed == [ledger["attempt_id"]]
            assert ledger["deadline_at"] == original_deadline < ledger["received_at"]
            assert ledger["outcome"]["provider_request_id"] == _REQUEST_ID
            assert ledger["outcome"]["output_tokens"] == 12
            prepared = await claim_review_jobs.prepare_claim_review(jobs, claim)
            assert prepared.attempt.deadline_at == original_deadline and prepared.attempt.outcome is None
            assert not await claim_review_jobs.complete_claim_review(
                jobs, claim=claim, lease_digest=lease, receipt=None,
            )
            await _request(client, "GET", f"{_path(view)}/{job_id}", 404, headers=users["a"])
            status = await _request(client, "GET", f"/v1/workspaces/a/jobs/{job_id}", 200, headers=users["a"])
            assert status["status"] == "expired"
            assert await _ledger(case, job_id) == ledger

    _run(run())


def test_provider_publication_reauthorizes_after_send(tmp_path, monkeypatch):
    async def run():
        async with _seed(tmp_path) as (case, (client, jobs, _, users), view, witness):
            owner = _credential("a")
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='reviewer',revision=revision+1 "
                                   "WHERE workspace_id='a' AND user_id='owner_b'")
            grant = {"credential_digest": owner, "workspace_id": "a", "user_id": "owner_b"}
            await PostgresProjectAccess(case.pool).set_grant(
                **grant, project_id=witness["project_id"], actions=["read", "review"], expected_revision=0,
            )
            sources = WorkspaceManagement(case.pool)
            await sources.set_source_grant(
                **grant, source_id=witness["source_id"], actions=["read", "review"], expected_revision=0,
            )
            plan = await _plan(jobs, view, user="b")
            job_id = (await _issue(client, view, users["b"], "revoke-before-publication"))["job_id"]
            lease, claim = await _claim(jobs, job_id)

            async def revoke_inflight(_):
                await sources.set_source_grant(
                    **grant, source_id=witness["source_id"], actions=["read"], expected_revision=1,
                )

            observed = _simulate(monkeypatch, case, job_id, plan, delayed=revoke_inflight)
            await claim_review_jobs.dispatch_provider_review(jobs, claim, lease)
            ledger = await _ledger(case, job_id)
            assert observed == [ledger["attempt_id"]]
            assert ledger["outcome"]["provider_request_id"] == _REQUEST_ID
            with pytest.raises((AuthorizationDenied, SourceNotFound)):
                await claim_review_jobs.complete_claim_review(jobs, claim=claim, lease_digest=lease, receipt=None)
            await _request(client, "GET", f"{_path(view)}/{job_id}", 404, headers=users["b"])
            async with case.pool.connection() as conn:
                assert await (await conn.execute(
                    "SELECT count(*) FROM k20_claim_reviews WHERE job_id=%s", (job_id,),
                )).fetchone() == (0,)

    _run(run())

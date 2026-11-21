"""Reserve a single provider send and persist accounting independently of publication."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.claim_review import (
    ClaimReviewProviderAttempt,
    ClaimReviewProviderResult,
    ProviderClaimReviewBinding,
)

from .authorization import AuthorizationDenied
from .claim_provider_plan import ClaimProviderPlan
from .claim_review_provider import (
    CLAIM_PROVIDER_HOLD_MICROUSD,
    ClaimReviewProviderInput,
    dispatch_claim_review_provider,
)
from .job_lifecycle import PUBLICATION_BUDGET
from .postgres_sources import SourceConflict, SourceNotFound
from .transactions import run_transaction

if TYPE_CHECKING:
    from .postgres_jobs import JobClaim, PostgresJobs

_IN_FLIGHT: set[asyncio.Task[None]] = set()


async def drain_provider_attempts(timeout_seconds: float) -> None:
    if _IN_FLIGHT:
        # A hard exit leaves the committed attempt held for later reconciliation.
        await asyncio.wait(tuple(_IN_FLIGHT), timeout=timeout_seconds)


async def read_provider_attempt(jobs: PostgresJobs, claim: JobClaim, binding: ProviderClaimReviewBinding
                                ) -> ClaimReviewProviderAttempt | None:
    async with jobs._pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT attempt_id,request_sha256,model,held_microusd,dispatched_at,deadline_at,"
            "CASE WHEN received_at<=deadline_at THEN received_at END,"
            "CASE WHEN received_at<=deadline_at THEN outcome END FROM k21_claim_provider_attempts "
            "WHERE workspace_id=%s AND job_id=%s AND plan_sha256=%s",
            (claim.workspace_id, claim.job_id, binding.provider_plan_sha256),
        )).fetchone()
    if row is None:
        return None
    return ClaimReviewProviderAttempt.model_validate(dict(zip(
        ("attempt_id", "request_sha256", "model", "held_microusd", "dispatched_at", "deadline_at",
         "received_at", "outcome"), row, strict=True)))


async def _fence(conn: Connection, jobs: PostgresJobs, claim: JobClaim, binding: ProviderClaimReviewBinding,
                 lease: SecretStr, plan: ClaimProviderPlan, prepared: ClaimReviewProviderInput) -> object:
    from psycopg import sql

    from .claim_review_jobs import authorize_claim_review

    digest = _digest(claim.credential_digest)
    auth = await authorize_claim_review(jobs, conn, digest, claim.workspace_id, binding, reading=False)
    if plan.sha256 != binding.provider_plan_sha256 or not plan.matches(binding, auth.user_id, prepared.request_sha256):
        raise AuthorizationDenied()
    row = await (await conn.execute(sql.SQL(
        "SELECT deadline_at FROM j01_jobs WHERE workspace_id=%s AND job_id=%s AND operation='review_claim' "
        "AND credential_digest=%s AND state='running' AND lease_digest=%s AND lease_until>clock_timestamp() "
        "AND {} AND request=%s::jsonb AND %s>clock_timestamp() FOR UPDATE"
    ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(), (
        claim.workspace_id, claim.job_id, digest, _digest(lease), binding.model_dump_json(), plan.expires_at,
    ))).fetchone()
    if row is None:
        raise AuthorizationDenied()
    return row[0]


async def _reserve(jobs: PostgresJobs, claim: JobClaim, binding: ProviderClaimReviewBinding, lease: SecretStr,
                   plan: ClaimProviderPlan, prepared: ClaimReviewProviderInput) -> str | None:
    async def reserve(conn: Connection) -> str | None:
        deadline = await _fence(conn, jobs, claim, binding, lease, plan, prepared)
        attempt_id = uuid4().hex
        row = await (await conn.execute(
            "INSERT INTO k21_claim_provider_attempts "
            "(workspace_id,plan_sha256,job_id,attempt_id,request_sha256,model,held_microusd,deadline_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING attempt_id",
            (claim.workspace_id, binding.provider_plan_sha256, claim.job_id, attempt_id,
             prepared.request_sha256, plan.model, CLAIM_PROVIDER_HOLD_MICROUSD, deadline),
        )).fetchone()
        return None if row is None else cast(str, row[0])

    attempt = await run_transaction(jobs._pool, reserve)
    if attempt is None and await read_provider_attempt(jobs, claim, binding) is None:
        raise SourceConflict()
    return attempt


async def _send_and_record(jobs: PostgresJobs, claim: JobClaim, binding: ProviderClaimReviewBinding,
                           lease: SecretStr, plan: ClaimProviderPlan, prepared: ClaimReviewProviderInput,
                           api_key: SecretStr, attempt_id: str) -> None:
    try:
        async with jobs._pool.connection() as conn:
            await _fence(conn, jobs, claim, binding, lease, plan, prepared)
    except AuthorizationDenied:
        outcome = ClaimReviewProviderResult(status="unavailable", provider_request_id=None, input_tokens=0,
            output_tokens=0, provider_decision=None, agrees_with_host=None)
    else:
        outcome = await dispatch_claim_review_provider(prepared, approved_request_sha256=plan.request_sha256,
                                                         api_key=api_key, attempt_id=attempt_id)
    # Accounting survives lease loss. A late response never extends a job deadline.
    async with jobs._pool.connection() as conn:
        await conn.execute(
            "UPDATE k21_claim_provider_attempts SET received_at=clock_timestamp(),outcome=%s::jsonb "
            "WHERE workspace_id=%s AND job_id=%s AND attempt_id=%s AND outcome IS NULL",
            (outcome.model_dump_json(), claim.workspace_id, claim.job_id, attempt_id),
        )


def _release_task(task: asyncio.Task[None]) -> None:
    _IN_FLIGHT.discard(task)
    if not task.cancelled():
        task.exception()


async def run_provider_attempt(jobs: PostgresJobs, claim: JobClaim, lease: SecretStr,
                               binding: ProviderClaimReviewBinding, prepared: ClaimReviewProviderInput) -> None:
    # Check the ledger before considering any send, including after a worker crash.
    if await read_provider_attempt(jobs, claim, binding) is not None:
        return
    plan, api_key = jobs.claim_provider_plan, jobs.claim_provider_key
    if plan is None or api_key is None or not api_key.get_secret_value():
        raise SourceNotFound()
    attempt_id = await _reserve(jobs, claim, binding, lease, plan, prepared)
    if attempt_id is None:
        return
    task = asyncio.create_task(_send_and_record(jobs, claim, binding, lease, plan, prepared, api_key, attempt_id))
    _IN_FLIGHT.add(task)
    task.add_done_callback(_release_task)
    await asyncio.shield(task)

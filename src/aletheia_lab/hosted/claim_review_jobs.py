"""Admit, verify and publish a claim review without mutating the cited result."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, Literal, cast

from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.claim_review import ClaimReviewBinding, ClaimReviewReceipt
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.view import AnyProductView, ProductHistoricalStateEvidenceItem
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import _json_bytes

from .async_work import run_blocking
from .authorization import Authorization
from .claim_review import evaluate_claim_assertion
from .job_lifecycle import PUBLICATION_BUDGET
from .native_upload_records import UPLOAD_RESULT_TYPES, CanonicalResultRecord, result_view
from .object_store import ObjectDescriptor
from .postgres_catalog import CatalogIntegrityError, PostgresCatalog, canonical_metadata
from .postgres_sources import SourceNotFound
from .transactions import run_transaction
from .upload_snapshot import UPLOAD_SNAPSHOT_TYPES, AnyUploadSnapshot

if TYPE_CHECKING:
    from .postgres_jobs import JobClaim, PostgresJobs

_VERSION: Final = "claim-evidence-review/v1"
_POLICY = "historical-state-authorization/v1"


def _catalog(jobs: PostgresJobs) -> PostgresCatalog:
    if jobs._read_catalog is None:
        raise TimeoutError("Canonical claim storage is unavailable.")
    return jobs._read_catalog


async def authorize_claim_review(jobs: PostgresJobs, conn: Connection, digest: str, workspace: str,
                                 binding: ClaimReviewBinding, *, reading: bool) -> Authorization:
    if binding.workspace_id != workspace:
        raise SourceNotFound()
    action = "read" if reading else "review"
    auth = await jobs._access._access(conn, digest, workspace, action=action)
    kind = await jobs._access._authorization.authorize_record(
        conn, auth, workspace, binding.project_id, binding.result_id, action,
    )
    row = await (await conn.execute(
        "SELECT snapshot_id,canonical_sha256 FROM k05_records WHERE workspace_id=%s AND record_id=%s",
        (workspace, binding.result_id),
    )).fetchone()
    if kind not in ("result", "upload_result") or row != (binding.snapshot_id, binding.result_sha256):
        raise SourceNotFound()
    if kind == "upload_result":
        parent = await (await conn.execute(
            "SELECT r.source_id,s.sha256 FROM k05_records r JOIN j01_sources s "
            "ON (s.workspace_id,s.source_id)=(r.workspace_id,r.source_id) WHERE r.workspace_id=%s "
            "AND r.project_id=%s AND r.record_id=%s AND r.record_type='upload_snapshot' FOR SHARE OF r,s",
            (workspace, binding.project_id, binding.snapshot_id),
        )).fetchone()
        if parent != (binding.source_id, binding.source_sha256):
            raise SourceNotFound()
    elif binding.source_id is not None or binding.source_sha256 is not None:
        raise SourceNotFound()
    return auth


def _bound_claim(record: CanonicalResultRecord, workspace: str, claim_id: str,
                 asserted_verdict: Literal["allowed", "denied"],
                 snapshot: AnyUploadSnapshot | None) -> tuple[ClaimReviewBinding, AnyProductView]:
    view = result_view(record)
    claim = next((item for item in view.claims if item.id == claim_id), None)
    if claim is None:
        raise SourceNotFound()
    cited = tuple(item for item in view.evidence if item.id in claim.citation_ids or item.id in claim.counterevidence_ids)
    historical = tuple(item.historical_state for item in cited if isinstance(item, ProductHistoricalStateEvidenceItem))
    source = historical[0] if len(historical) == 1 else None
    identity = {
        "workspace_id": workspace, "project_id": view.project.id, "snapshot_id": view.snapshot.id,
        "source_id": None if snapshot is None else snapshot.source_id,
        "source_sha256": None if snapshot is None else snapshot.source_sha256,
        "result_id": view.result.id, "claim_id": claim_id,
        "result_sha256": canonical_metadata(record)[-1],
        "claim_sha256": canonical_project_sha256(claim.model_dump(mode="json")),
        "evidence_sha256": canonical_project_sha256([item.model_dump(mode="json") for item in cited]),
        "receipt_sha256": None if source is None else source.receipt_sha256,
        "contract_sha256": None if source is None else source.contract_sha256,
        "asserted_verdict": asserted_verdict, "policy": _POLICY,
    }
    return ClaimReviewBinding.model_validate({
        **identity, "candidate_sha256": canonical_project_sha256(identity),
    }), view


async def _load_claim(jobs: PostgresJobs, credential: SecretStr, workspace: str, result_id: str,
                      claim_id: str, asserted_verdict: Literal["allowed", "denied"]
                      ) -> tuple[ClaimReviewBinding, AnyProductView, AnyUploadSnapshot | None]:
    catalog = _catalog(jobs)
    async with jobs._pool.connection() as conn:
        await jobs._access._access(conn, _digest(credential), workspace, action="read")
        row = await (await conn.execute(
            "SELECT project_id FROM k05_records WHERE workspace_id=%s AND record_id=%s "
            "AND record_type IN ('upload_result','result')", (workspace, result_id),
        )).fetchone()
        if row is None or not isinstance(row[0], str):
            raise SourceNotFound()
        project = row[0]
    record = await catalog.read_record(
        credential_digest=credential, workspace_id=workspace, project_id=project, record_id=result_id,
    )
    if not isinstance(record, (*UPLOAD_RESULT_TYPES, ProductLifecycleRecord)):
        raise SourceNotFound()
    snapshot = None
    if isinstance(record, UPLOAD_RESULT_TYPES):
        parent = await catalog.read_record(
            credential_digest=credential, workspace_id=workspace, project_id=project, record_id=record.snapshot_id,
        )
        if not isinstance(parent, UPLOAD_SNAPSHOT_TYPES):
            raise CatalogIntegrityError()
        snapshot = parent
    binding, view = await run_blocking(_bound_claim, record, workspace, claim_id, asserted_verdict, snapshot)
    return binding, view, snapshot


async def enqueue_claim_review(jobs: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str,
                               result_id: str, claim_id: str, asserted_verdict: Literal["allowed", "denied"],
                               request_key: SecretStr) -> JobReceipt:
    binding, _, _ = await _load_claim(jobs, credential_digest, workspace_id, result_id, claim_id, asserted_verdict)
    return await jobs.enqueue(credential_digest=credential_digest, workspace_id=workspace_id,
                              operation="review_claim", request_key=request_key,
                              request=binding.model_dump(mode="json"))


async def prepare_claim_review(jobs: PostgresJobs, claim: JobClaim) -> ClaimReviewReceipt:
    binding = ClaimReviewBinding.model_validate(claim.request)
    if claim.operation != "review_claim" or binding.workspace_id != claim.workspace_id:
        raise ValueError("Invalid review claim scope.")
    actual, view, snapshot = await _load_claim(jobs, claim.credential_digest, claim.workspace_id,
                                              binding.result_id, binding.claim_id, binding.asserted_verdict)
    if actual != binding:
        raise ValueError("Review binding differs from the canonical claim.")
    finding = await run_blocking(evaluate_claim_assertion, view, binding.claim_id, binding.asserted_verdict, snapshot)
    return ClaimReviewReceipt(schema_version=_VERSION, job_id=claim.job_id, binding=binding, finding=finding,
                              mode="local_check", usage="not_incurred")


async def complete_claim_review(jobs: PostgresJobs, *, claim: JobClaim, lease_digest: SecretStr,
                                receipt: ClaimReviewReceipt) -> bool:
    # This verifier is a pure replay over the authorized canonical bytes.
    expected = await prepare_claim_review(jobs, claim)
    if receipt != expected:
        raise ValueError("The claim review receipt does not reconcile.")
    binding = expected.binding
    workspace = claim.workspace_id
    objects = _catalog(jobs).objects
    staged = await objects.stage(workspace, _json_bytes(expected.model_dump(mode="json")),
                                 media_type="application/json", schema_version=_VERSION)
    request = binding.model_dump_json()
    digest, lease = _digest(claim.credential_digest), _digest(lease_digest)

    async def publish(conn: Connection) -> bool:
        from psycopg import sql

        await authorize_claim_review(jobs, conn, digest, workspace, binding, reading=False)
        # This row update fences expired and replaced workers. Failure anywhere
        # in the transaction rolls back the terminal job and receipt together.
        terminal = await (await conn.execute(sql.SQL(
            "UPDATE j01_jobs SET state='succeeded',result=%s::jsonb WHERE workspace_id=%s AND job_id=%s "
            "AND credential_digest=%s AND operation='review_claim' AND state='running' "
            "AND lease_digest=%s AND lease_until>clock_timestamp() AND {} AND request=%s::jsonb RETURNING job_id"
        ).format(sql.SQL(PUBLICATION_BUDGET)).as_string(),
            (json.dumps({"review_id": claim.job_id}), workspace, claim.job_id, digest, lease, request),
        )).fetchone()
        if terminal is None:
            return False
        await objects.ready(conn, workspace, staged)
        sha = staged.descriptor.sha256
        await conn.execute("INSERT INTO k05_object_references (workspace_id,record_id,sha256) VALUES (%s,%s,%s)",
                           (workspace, binding.result_id, sha))
        await conn.execute(
            "INSERT INTO k20_claim_reviews (workspace_id,job_id,project_id,result_id,claim_id,object_sha256,binding) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (workspace, claim.job_id, binding.project_id, binding.result_id, binding.claim_id, sha, request),
        )
        await objects.finish(conn, workspace, staged)
        await jobs._record_action(conn, workspace, claim.job_id, "job.publish", parent=binding.result_id)
        return True

    return await run_transaction(jobs._pool, publish)


async def read_claim_review(jobs: PostgresJobs, *, credential_digest: SecretStr, workspace_id: str,
                            result_id: str, claim_id: str, review_id: str) -> ClaimReviewReceipt:
    digest = _digest(credential_digest)
    async with jobs._pool.connection() as conn:
        await jobs._access._access(conn, digest, workspace_id, action="read")
        row = await (await conn.execute(
            "SELECT r.binding,o.sha256,o.byte_size,o.media_type,o.schema_version FROM k20_claim_reviews r "
            "JOIN k05_objects o ON (r.workspace_id,r.object_sha256)=(o.workspace_id,o.sha256) "
            "WHERE r.workspace_id=%s AND r.job_id=%s AND r.result_id=%s AND r.claim_id=%s",
            (workspace_id, review_id, result_id, claim_id),
        )).fetchone()
        if row is None:
            raise SourceNotFound()
        binding = ClaimReviewBinding.model_validate(row[0])
        await authorize_claim_review(jobs, conn, digest, workspace_id, binding, reading=True)
    descriptor = ObjectDescriptor(cast(str, row[1]), cast(int, row[2]), cast(str, row[3]), cast(str, row[4]))
    if descriptor.schema_version != _VERSION or descriptor.media_type != "application/json" or descriptor.byte_size > 65536:
        raise CatalogIntegrityError()
    data = await _catalog(jobs).objects.read(workspace_id, descriptor)
    try:
        receipt = ClaimReviewReceipt.model_validate_json(data)
    except ValidationError:
        raise CatalogIntegrityError() from None
    if receipt.job_id != review_id or receipt.binding != binding:
        raise CatalogIntegrityError()
    async with jobs._pool.connection() as conn:
        await authorize_claim_review(jobs, conn, digest, workspace_id, binding, reading=True)
    return receipt


async def read_job_claim_review(jobs: PostgresJobs, credential: SecretStr, workspace: str, job_id: str,
                               request: dict[str, object], result: dict[str, object], linked: object
                               ) -> dict[str, object]:
    binding = ClaimReviewBinding.model_validate(request)
    if linked is not None or result != {"review_id": job_id}:
        raise CatalogIntegrityError()
    receipt = await read_claim_review(jobs, credential_digest=credential, workspace_id=workspace,
                                     result_id=binding.result_id, claim_id=binding.claim_id, review_id=job_id)
    if receipt.binding != binding:
        raise CatalogIntegrityError()
    return receipt.model_dump(mode="json")

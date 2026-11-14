"""Follow-up compute through existing worker slots; the job queue owns publication."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import RootModel

from aletheia_lab.hosted_contract.project_operations import FollowUpRequest
from aletheia_lab.product.follow_up import resolve_product_follow_up_selection
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _validate_derived_result
from aletheia_lab.product.view import AnyProductView

from .application import VERSION
from .async_work import run_blocking
from .native_upload_records import (
    AnyUploadResultRecord,
    CanonicalResultRecord,
    UploadResultRecord,
    UploadResultRecordV2,
    build_initial_upload_result,
    result_view,
)
from .postgres_jobs import JobClaim
from .preview_leases import PreviewLeases
from .project_refresh import AnyPreparedRefresh, RefreshParents, prepare_refresh
from .request_boundary import decode_json_model
from .upload_operations import Sources, UploadOperations, Worker
from .worker_limits import MAX_OUTPUT_BYTES


class _CanonicalWorkerResult(RootModel[CanonicalResultRecord]):
    pass


class FollowUpParents(RefreshParents, Protocol):
    async def follow_up_parent(self, *, claim: JobClaim) -> AnyProductView: ...


class CanonicalProjectOperations(UploadOperations):
    def __init__(self, sources: Sources, worker: Worker, project_jobs: FollowUpParents, *,
                 leases: PreviewLeases | None = None, private_root: Path | None = None) -> None:
        super().__init__(sources, worker, leases=leases, private_root=private_root)
        self._project_jobs = project_jobs

    async def prepare_refresh(self, claim: JobClaim) -> AnyPreparedRefresh:
        return await prepare_refresh(self, self._project_jobs, claim)

    async def prepare_initial_analysis(self, claim: JobClaim) -> AnyUploadResultRecord:
        snapshot_id, question = claim.request.get("snapshot_id"), claim.request.get("question")
        if (claim.operation != "analyze" or set(claim.request) not in ({"snapshot_id", "question"}, {"snapshot_id", "question", "analysis_model"})
                or not isinstance(snapshot_id, str) or not isinstance(question, str)):
            raise ValueError("invalid canonical analysis request")
        view = await self.analyze(credential_digest=claim.credential_digest, workspace_id=claim.workspace_id,
                                  snapshot_id=snapshot_id, question=question,
                                  analysis_model=str(claim.request.get('analysis_model', 'upload-snapshot-observation/v1')))
        snapshot = await self._sources.snapshot(credential_digest=claim.credential_digest,
                                                workspace_id=claim.workspace_id, snapshot_id=snapshot_id)
        return await run_blocking(build_initial_upload_result, snapshot, question, view)

    async def prepare_follow_up(self, claim: JobClaim) -> CanonicalResultRecord:
        if claim.operation != "follow_up":
            raise ValueError("invalid canonical operation")
        request = FollowUpRequest.model_validate({"schema_version": VERSION,
            "selection_kind": claim.request.get("selection_kind"), "selection_id": claim.request.get("selection_id"),
            "question": claim.request.get("question")})
        parent = await self._project_jobs.follow_up_parent(claim=claim)
        if (parent.project.id != claim.request.get("project_id")
                or parent.result.id != claim.request.get("parent_result_id")):
            raise ValueError("invalid canonical parent scope")
        selection = resolve_product_follow_up_selection(parent, selection_kind=request.selection_kind,
                                                        selection_id=request.selection_id, question=request.question)
        payload = json.dumps({"operation": "follow_up", "workspace_id": claim.workspace_id, "parent": parent.model_dump(mode="json"),
            "selection_kind": request.selection_kind, "selection_id": request.selection_id, "question": request.question},
            ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(payload) > self._worker_input_limit():
            raise ValueError("canonical parent exceeds worker input budget")
        raw = await self._worker.run_upload(payload)
        record = decode_json_model(raw, _CanonicalWorkerResult, maximum_bytes=MAX_OUTPUT_BYTES).root
        if ((isinstance(record, ProductLifecycleRecord) and record.record_kind != "result")
                or (isinstance(record, (UploadResultRecord, UploadResultRecordV2)) and record.workspace_id != claim.workspace_id)
                or record.project_id != parent.project.id
                or record.snapshot_id != parent.snapshot.id or record.parent_result_id != parent.result.id):
            raise ValueError("invalid canonical worker scope")
        child = result_view(record)
        _validate_derived_result(parent, child)
        turn = child.conversation.turns[-1]
        if (turn.question != request.question or turn.visible_evidence_ids != selection.evidence_ids
                or not turn.abstained or child.result.disposition != (None if parent.result.status == "technical_failure" else "abstain")):
            raise ValueError("invalid canonical worker selection")
        return record

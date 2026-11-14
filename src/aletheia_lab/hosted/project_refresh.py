"""Refresh immutable hosted uploads through the existing importer and worker slot."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Generic, Literal, Protocol, Self, TypeAlias, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.mapping import (
    metric_definitions_match_observations,
    rebind_stored_mapping,
)
from aletheia_lab.project.collectors import collect_project_files
from aletheia_lab.project.identity import (
    granted_root_fingerprint,
    project_id_for_root,
)
from aletheia_lab.project.import_policy import ProjectImportPolicy
from aletheia_lab.project.importer import grant_immutable_project_root, import_local_project
from aletheia_lab.project.mapping import validate_project_mapping

from .async_work import run_blocking
from .postgres_jobs import JobClaim
from .postgres_sources import SourceNotFound
from .request_boundary import decode_json_model
from .upload_operations import UploadOperations
from .upload_project import (
    _CONTENT_PROJECTION_TIME,
    _materialize,
    _native_path,
    _numeric_underflow_issues,
    _plain_directory,
)
from .upload_snapshot import (
    AnyUploadSnapshot,
    UploadSnapshot,
    UploadSnapshotV2,
    build_upload_snapshot,
    parse_upload_snapshot,
)
from .worker_limits import MAX_UPLOAD_OUTPUT_BYTES

_SnapshotT = TypeVar('_SnapshotT', UploadSnapshot, UploadSnapshotV2)


class _PreparedRefreshBase(BaseModel, Generic[_SnapshotT]):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")

    schema_version: str
    status: Literal["unchanged", "new_snapshot"]
    parent_snapshot_id: str = Field(pattern=r"^upload-snapshot-[0-9a-f]{64}$")
    snapshot: _SnapshotT

    @model_validator(mode="after")
    def _disposition(self) -> Self:
        if (self.status == "unchanged") != (self.parent_snapshot_id == self.snapshot.snapshot_id):
            raise ValueError("Refresh disposition does not match the immutable snapshots.")
        return self


class PreparedRefresh(_PreparedRefreshBase[UploadSnapshot]):
    schema_version: Literal['hosted-refresh-computation/v1'] = 'hosted-refresh-computation/v1'


class PreparedRefreshV2(_PreparedRefreshBase[UploadSnapshotV2]):
    schema_version: Literal['hosted-refresh-computation/v2'] = 'hosted-refresh-computation/v2'


AnyPreparedRefresh: TypeAlias = PreparedRefresh | PreparedRefreshV2


class _RefreshOutput(RootModel[Annotated[AnyPreparedRefresh, Field(discriminator="schema_version")]]):
    pass


def _prepared(snapshot: AnyUploadSnapshot, *, status: Literal['unchanged', 'new_snapshot'],
              parent_snapshot_id: str) -> AnyPreparedRefresh:
    if isinstance(snapshot, UploadSnapshotV2):
        return PreparedRefreshV2(status=status, parent_snapshot_id=parent_snapshot_id, snapshot=snapshot)
    return PreparedRefresh(status=status, parent_snapshot_id=parent_snapshot_id, snapshot=snapshot)


class RefreshParents(Protocol):
    async def refresh_parent(self, *, claim: JobClaim) -> tuple[AnyUploadSnapshot, str]: ...


def _identity(parent: AnyUploadSnapshot, origin_source_sha256: str) -> None:
    if (re.fullmatch(r"[0-9a-f]{64}", origin_source_sha256) is None
            or project_id_for_root(granted_root_fingerprint("hosted-upload-source/v1:" + origin_source_sha256))
            != parent.mapping.project_id):
        raise ValueError("Refresh origin does not identify the canonical project.")


def refresh_uploaded_project(*, parent: AnyUploadSnapshot, origin_source_sha256: str,
                             before_payload: bytes, new_payload: bytes, source_id: str,
                             private_root: Path, captured_at: datetime,
                             maximum_upload_bytes: int, maximum_expanded_bytes: int,
                             maximum_entries: int) -> AnyPreparedRefresh:
    parent = parse_upload_snapshot(parent.model_dump(mode="python"))
    _identity(parent, origin_source_sha256)
    if content_hasher(before_payload).hexdigest() != parent.source_sha256:
        raise ValueError("Refresh parent bytes do not reconcile.")
    root = _native_path(private_root)
    if not root.is_absolute():
        raise ValueError("An absolute private worker root is required.")
    _plain_directory(root)
    budgets = dict(maximum_upload_bytes=maximum_upload_bytes,
                   maximum_expanded_bytes=maximum_expanded_bytes, maximum_entries=maximum_entries)
    policy = ProjectImportPolicy(max_item_bytes=maximum_expanded_bytes,
        max_total_bytes=maximum_expanded_bytes, max_items=maximum_entries,
        max_discovered_entries=maximum_entries, max_path_depth=32)
    with tempfile.TemporaryDirectory(prefix="refresh-job-", dir=root) as owned:
        before = _materialize(before_payload, Path(owned), **budgets)
        after = _materialize(new_payload, Path(owned), **budgets)
        imported = []
        for path, payload in ((before, before_payload), (after, new_payload)):
            grant = grant_immutable_project_root(path, source_sha256=content_hasher(payload).hexdigest(),
                                                identity_source_sha256=origin_source_sha256)
            item = import_local_project(grant, display_name="Uploaded Project",
                                        ingested_at=_CONTENT_PROJECTION_TIME, policy=policy)
            if item.bundle is None:
                raise ProductError("mapping_invalid", "The project mapping is invalid or incomplete.")
            imported.append(item)
    previous, current = imported
    assert previous.bundle is not None and current.bundle is not None
    before_collection = collect_project_files(previous.bundle, previous.artifacts)
    checked_before = validate_project_mapping(previous.bundle, previous.artifacts, before_collection, parent.mapping)
    if (previous.bundle.project_bundle_id != parent.mapping.project_bundle_id
            or checked_before != parent.mapping_result):
        raise ValueError("Refresh parent mapping does not reconcile with its immutable source.")
    collection = collect_project_files(current.bundle, current.artifacts)
    configuration = rebind_stored_mapping(parent.mapping, previous.bundle, current.bundle,
                                          file_collection_sha256=collection.collection_sha256)
    indices = {metric.mapping_id: index for index, metric in enumerate(configuration.metric_sources)}
    numeric_issues = _numeric_underflow_issues(configuration, current.bundle.items, current.artifacts, indices)
    result = validate_project_mapping(current.bundle, current.artifacts, collection, configuration)
    if numeric_issues or result.status != "valid" or not metric_definitions_match_observations(configuration, result):
        raise ProductError("mapping_invalid", "The project mapping is invalid or incomplete.")
    if configuration == parent.mapping and result == parent.mapping_result:
        return _prepared(parent, status="unchanged", parent_snapshot_id=parent.snapshot_id)
    snapshot = build_upload_snapshot(workspace_id=parent.workspace_id, source_id=source_id,
        source_sha256=content_hasher(new_payload).hexdigest(), mapping=configuration, mapping_result=result,
        captured_at=captured_at)
    return _prepared(snapshot, status="new_snapshot", parent_snapshot_id=parent.snapshot_id)


async def prepare_refresh(operations: UploadOperations, parents: RefreshParents, claim: JobClaim) -> AnyPreparedRefresh:
    source_id = claim.request.get("source_id")
    if claim.operation != "refresh" or not isinstance(source_id, str):
        raise ValueError("Invalid refresh claim.")
    parent, origin = await parents.refresh_parent(claim=claim)
    _identity(parent, origin)
    if (parent.workspace_id != claim.workspace_id or parent.mapping.project_id != claim.request.get("project_id")
            or parent.snapshot_id != claim.request.get("parent_snapshot_id")):
        raise ValueError("Invalid refresh parent scope.")
    root = operations._private_root
    if root is None:
        raise ValueError("An explicit private worker root is required.")
    before = await operations._sources.read(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, source_id=parent.source_id)
    new = await operations._sources.read(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, source_id=source_id)
    paths = [root / "inputs" / (uuid4().hex + ".zip") for _ in range(2)]
    try:
        for payload, path in zip((before, new), paths, strict=True):
            await operations._write_input_ready(payload, path)
        def encode() -> bytes:
            return json.dumps({"operation": "refresh", "workspace_id": claim.workspace_id, "source_id": source_id,
                "parent": parent.model_dump(mode="json"), "origin_source_sha256": origin,
                "before_source_path": str(paths[0]), "new_source_path": str(paths[1]),
                "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")},
                ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        request = await run_blocking(encode)
        if len(request) > operations._worker_input_limit():
            raise ValueError("Refresh exceeds the worker input budget.")
        raw = await operations._worker.run_upload(request)
    finally:
        for path in paths:
            operations._cleanup_input(path)
    current_parent = await parents.refresh_parent(claim=claim)
    current_before = await operations._sources.read(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, source_id=parent.source_id)
    current_new = await operations._sources.read(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, source_id=source_id)
    if (current_parent != (parent, origin) or content_hasher(current_before).digest() != content_hasher(before).digest()
            or content_hasher(current_new).digest() != content_hasher(new).digest()):
        raise SourceNotFound()
    value = (await run_blocking(decode_json_model, raw, _RefreshOutput,
             maximum_bytes=MAX_UPLOAD_OUTPUT_BYTES)).root
    snapshot = value.snapshot
    if (value.parent_snapshot_id != parent.snapshot_id or snapshot.workspace_id != parent.workspace_id
            or snapshot.mapping.project_id != parent.mapping.project_id
            or (value.status == "unchanged" and snapshot != parent)
            or (value.status == "new_snapshot" and (snapshot.source_id != source_id
                or snapshot.source_sha256 != content_hasher(new).hexdigest()))):
        raise ValueError("Invalid worker refresh scope.")
    return value

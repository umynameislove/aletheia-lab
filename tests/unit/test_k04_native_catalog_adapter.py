"""Typed K05 upload records enter the same authorized catalog HTTP projections."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k05_native_upload_records import upload_case

from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.postgres_catalog import RecordIndex, RecordPage
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.hosted_contract.workspaces import CanonicalRecord
from aletheia_lab.hosted_contract.workspaces import RecordPage as PublicRecordPage

DIGEST = SecretStr("a" * 64)


def _native(tmp_path):
    snapshot, view = upload_case(tmp_path)
    return snapshot, build_initial_upload_result(snapshot, "What is observed?", view)


def _access(record, project, *, deny=None):
    calls = []

    class Connection:
        async def execute(self, query, params):
            calls.append(("select_parent", query, params))
            assert "record_type IN ('result','upload_result')" in query
            return SimpleNamespace(fetchone=self.fetchone)

        async def fetchone(self):
            return (project,)

    @asynccontextmanager
    async def connection():
        yield Connection()

    class Policy:
        async def authorize(self, conn, digest, workspace, action):
            calls.append(("authorize", digest, workspace, action))
            if deny == "workspace":
                raise AuthorizationDenied()
            return Authorization("actor", "owner", 1, workspace)

        async def authorize_project(self, conn, auth, workspace, project_id, action):
            calls.append(("authorize_project", workspace, project_id, action))
            if deny == "project":
                raise AuthorizationDenied()

    async def read_record(**kwargs):
        calls.append(("read_record", kwargs))
        if deny == "read_record":
            raise AuthorizationDenied()
        return record

    catalog = SimpleNamespace(pool=SimpleNamespace(connection=connection), policy=Policy(), read_record=read_record)
    return CatalogAccess(catalog, None, None), calls


def test_native_result_uses_authorized_catalog_typed_wrapper_and_preserves_existing_p6_computation(tmp_path):
    snapshot, record = _native(tmp_path)
    access, calls = _access(record, record.project_id)
    project, view = asyncio.run(access.result(DIGEST, snapshot.workspace_id, record.record_id))
    assert project == snapshot.mapping.project_id
    assert view == analyze_initial_upload(snapshot, "What is observed?") == record.view
    assert [call[0] for call in calls] == ["authorize", "select_parent", "authorize_project", "read_record"]
    assert calls[-1][1] == {"credential_digest": DIGEST, "workspace_id": snapshot.workspace_id,
                          "project_id": project, "record_id": record.record_id}


@pytest.mark.parametrize("deny", ["workspace", "project", "read_record"])
def test_native_result_never_bypasses_current_workspace_project_or_catalog_read_policy(tmp_path, deny):
    snapshot, record = _native(tmp_path)
    access, calls = _access(record, record.project_id, deny=deny)
    with pytest.raises(AuthorizationDenied):
        asyncio.run(access.result(DIGEST, snapshot.workspace_id, record.record_id))
    assert sum(call[0] == "read_record" for call in calls) == (deny == "read_record")


@pytest.mark.parametrize("mismatch", ["workspace", "project", "requested_id", "self_id", "raw_job_json"])
def test_foreign_or_invalid_native_result_never_returns_a_view(tmp_path, mismatch):
    snapshot, record = _native(tmp_path)
    workspace, project, requested = snapshot.workspace_id, record.project_id, record.record_id
    if mismatch == "workspace":
        other = build_upload_snapshot(workspace_id="other", source_id=snapshot.source_id,
            source_sha256=snapshot.source_sha256, mapping=snapshot.mapping, mapping_result=snapshot.mapping_result,
            captured_at=snapshot.captured_at)
        record = build_initial_upload_result(other, "What is observed?", analyze_initial_upload(other, "What is observed?"))
        requested = record.record_id
    elif mismatch == "project":
        project = "p3-project-" + "b" * 64
    elif mismatch == "requested_id":
        requested = "p6-result-" + "b" * 64
    elif mismatch == "self_id":
        record = record.model_copy(update={"snapshot_id": "upload-snapshot-" + "b" * 64})
    else:
        record = record.view.model_dump(mode="python")
    access, _ = _access(record, project)
    with pytest.raises((ValueError, AuthorizationDenied)):
        asyncio.run(access.result(DIGEST, workspace, requested))


def _application(record):
    checks = []

    async def read_record(**kwargs):
        checks.append(("read_record", kwargs))
        return record

    def checkpoint(*args, record_id=None):
        async def checked():
            checks.append(("checkpoint", args, record_id))
        return checked

    app = object.__new__(CatalogApplication)
    app._access = SimpleNamespace(catalog=SimpleNamespace(read_record=read_record), checkpoint=checkpoint)
    app._runtime = SimpleNamespace(api=SimpleNamespace(maximum_response_bytes=1024 * 1024))
    return app, checks


@pytest.mark.parametrize("kind", ["snapshot", "result"])
def test_native_record_details_keep_actual_kind_and_existing_catalog_policy_checkpoint(tmp_path, kind):
    snapshot, result = _native(tmp_path)
    record = snapshot if kind == "snapshot" else result
    identity = snapshot.snapshot_id if kind == "snapshot" else result.record_id
    app, checks = _application(record)

    async def scenario():
        output = await app._record(DIGEST, snapshot.workspace_id, result.project_id, identity,
                                   "snapshot" if kind == "snapshot" else None)
        await output.checkpoint()
        return output

    output = asyncio.run(scenario())
    value = CanonicalRecord.model_validate_json(output.response.body)
    assert value.record_kind == "upload_" + kind and value.record_id == identity
    assert value.data == json.loads(record.model_dump_json())
    assert [call[0] for call in checks] == ["read_record", "checkpoint"]
    assert checks[-1][2] == identity


@pytest.mark.parametrize("mismatch", ["workspace", "project", "record_id", "self_hash"])
def test_native_snapshot_detail_rejects_foreign_scope_and_invalid_self_hash(tmp_path, mismatch):
    snapshot, result = _native(tmp_path)
    workspace, project, identity = snapshot.workspace_id, result.project_id, snapshot.snapshot_id
    if mismatch == "workspace":
        workspace = "foreign"
    elif mismatch == "project":
        project = "p3-project-" + "b" * 64
    elif mismatch == "record_id":
        identity = "upload-snapshot-" + "b" * 64
    else:
        snapshot = snapshot.model_copy(update={"record_sha256": "b" * 64})
    app, _ = _application(snapshot)
    with pytest.raises(ValueError):
        asyncio.run(app._record(DIGEST, workspace, project, identity, "snapshot"))


@pytest.mark.parametrize("workspace_wide", [False, True])
def test_record_pages_keep_public_native_kind_without_internal_source_or_object_handles(tmp_path, workspace_wide):
    snapshot, result = _native(tmp_path)
    index = RecordIndex(result.record_id, result.project_id, "upload_result", result.schema_version,
        "a" * 64, "b" * 64, result.snapshot_id, None, snapshot.source_id)

    async def listing(**kwargs):
        return RecordPage((index,), None)

    access = CatalogAccess(SimpleNamespace(list_records=listing, list_workspace_records=listing), None, None)

    async def binding(*args, **kwargs):
        return {"user_id": "actor", "membership_revision": 1}

    access._binding = binding
    if workspace_wide:
        value = asyncio.run(access.workspace_records(DIGEST, snapshot.workspace_id, "context"))
    else:
        value = asyncio.run(access.records(DIGEST, snapshot.workspace_id, result.project_id, "context", kind="result"))
    parsed = PublicRecordPage.model_validate(value)
    assert parsed.items[0].record_type == "upload_result"
    assert "object_sha256" not in value["items"][0] and "source_id" not in value["items"][0]

"""Typed canonical preparation; SQL publication is covered on real PostgreSQL."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from test_k05_legacy_import import legacy_journey

from aletheia_lab.hosted.authorization import Authorization
from aletheia_lab.hosted.canonical_results import CanonicalResultRejected, CanonicalResults
from aletheia_lab.hosted.native_upload_records import (
    build_initial_upload_result,
    build_upload_follow_up_result,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_objects import StagedObject
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import ProductLifecycleStore, build_product_lifecycle_record
from aletheia_lab.project.mapping import validate_project_mapping
from aletheia_lab.project.persistence import _json_bytes


class Objects:
    def __init__(self, root):
        self.store = FilesystemObjectStore(root)
        self.calls = []

    async def stage(self, workspace, payload, **kwargs):
        self.calls.append((workspace, payload, kwargs))
        return StagedObject(self.store.publish(payload, **kwargs), "synthetic-stage-lease")


@pytest.fixture
def results(tmp_path):
    root, _, original, followed = legacy_journey(tmp_path)
    with ProductLifecycleStore(root) as store:
        return (store.get(original["result"]["id"], expected_kind="result"),
                store.get(followed["result"]["id"], expected_kind="result"))


def test_prepare_validated_result_stages_exact_canonical_bytes_without_payload_in_receipt(tmp_path, results):
    parent, record = results
    objects = Objects(tmp_path / "managed")
    prepared = asyncio.run(CanonicalResults(objects).prepare_result("workspace-a", parent=parent, record=record))
    assert objects.calls == [("workspace-a", _json_bytes(record.model_dump(mode="json")),
                              {"media_type": "application/json", "schema_version": record.schema_version})]
    assert prepared.result.record_id == record.record_id
    assert prepared.parent.record_id == parent.record_id
    assert prepared.result_relations == (("parent_result", parent.record_id), ("snapshot", parent.snapshot_id))
    assert "payload_json" not in repr(asdict(prepared))
    assert objects.store.read(prepared.staged.descriptor) == _json_bytes(record.model_dump(mode="json"))


@pytest.mark.parametrize("fault", ["project", "snapshot", "parent", "kind", "payload"])
def test_prepare_rejects_scope_or_model_fault_before_staging(tmp_path, results, fault):
    parent, record = results
    if fault == "payload":
        record = record.model_copy(update={"payload_json": "private invalid content"})
    else:
        record = build_product_lifecycle_record(
            record_kind="turn" if fault == "kind" else "result",
            project_id="p3-project-" + "0" * 64 if fault == "project" else record.project_id,
            snapshot_id="p3-snapshot-" + "0" * 64 if fault == "snapshot" else record.snapshot_id,
            parent_result_id="p6-result-" + "0" * 64 if fault == "parent" else record.parent_result_id,
            payload=record.payload(),
        )
    objects = Objects(tmp_path / "managed")
    with pytest.raises(CanonicalResultRejected) as raised:
        asyncio.run(CanonicalResults(objects).prepare_result("workspace-a", parent=parent, record=record))
    assert str(raised.value) == "The canonical result does not match its parent scope."
    assert not objects.calls


def test_enlist_rejects_autocommit_before_any_sql(tmp_path):
    class AutoCommit:
        autocommit = True

        async def execute(self, *args):
            raise AssertionError("Autocommit enlist must not issue SQL")

    writer = CanonicalResults(Objects(tmp_path / "managed"))
    with pytest.raises(RuntimeError, match="Transactional connection required"):
        asyncio.run(writer.enlist_result(AutoCommit(), authorization=Authorization("u", "owner", 0, "w"),
                                        prepared=None, expected_revision=0))



@pytest.fixture
def native_results(tmp_path):
    from test_project_mapping import _case, _configuration

    root = tmp_path / "native-source"
    root.mkdir()
    case = _case(root)
    mapping = _configuration(case)
    mapping_result = validate_project_mapping(case.bundle, case.artifacts, case.collection, mapping)
    snapshot = build_upload_snapshot(workspace_id="workspace-a", source_id="source-a", source_sha256="a" * 64,
        mapping=mapping, mapping_result=mapping_result, captured_at=datetime(2026, 11, 10, tzinfo=UTC))
    view = analyze_initial_upload(snapshot, "What is observed?")
    initial = build_initial_upload_result(snapshot, "What is observed?", view)
    selection = resolve_product_follow_up_selection(view, selection_kind="claim", selection_id=view.claims[0].id,
                                                    question="What remains uncertain?")
    child = build_upload_follow_up_result("workspace-a", view, build_product_follow_up_view(view, selection))
    return snapshot, initial, child


def test_native_initial_and_followup_preserve_snapshot_and_existing_view_identity(tmp_path, native_results):
    snapshot, initial, child = native_results
    objects = Objects(tmp_path / "managed")
    writer = CanonicalResults(objects)
    prepared = asyncio.run(writer.prepare_initial_result("workspace-a", snapshot=snapshot, record=initial))
    assert prepared.parent.record_type == "upload_snapshot"
    assert prepared.parent.source_id == snapshot.source_id
    assert prepared.result.record_type == "upload_result" and prepared.result.record_id == initial.view.result.id
    assert prepared.result.snapshot_id == snapshot.snapshot_id and prepared.provenance_kind == "upload"
    followed = asyncio.run(writer.prepare_result("workspace-a", parent=initial, record=child))
    assert followed.parent.record_id == initial.record_id and followed.parent.source_id is None
    assert followed.result.parent_result_id == initial.record_id
    assert followed.result.record_id == child.view.result.id
    assert child.record_id != initial.record_id and child.view.conversation.id == initial.view.conversation.id
    assert objects.store.read(followed.staged.descriptor) == _json_bytes(child.model_dump(mode="json"))


@pytest.mark.parametrize("fault", ["workspace", "source", "projection"])
def test_native_initial_scope_or_unverified_projection_rejected_before_stage(tmp_path, native_results, fault):
    snapshot, initial, _ = native_results
    workspace = "other-workspace" if fault == "workspace" else "workspace-a"
    if fault == "source":
        snapshot = snapshot.model_copy(update={"source_id": "other-source"})
    elif fault == "projection":
        wrong = initial.view.model_copy(update={"result": initial.view.result.model_copy(update={"summary": "Unverified summary"})})
        initial = initial.model_copy(update={"view": wrong})
    objects = Objects(tmp_path / "managed")
    with pytest.raises(CanonicalResultRejected):
        asyncio.run(CanonicalResults(objects).prepare_initial_result(workspace, snapshot=snapshot, record=initial))
    assert not objects.calls


def test_native_and_local_result_domains_cannot_be_mixed(tmp_path, native_results, results):
    _, native_parent, native_child = native_results
    local_parent, local_child = results
    objects = Objects(tmp_path / "managed")
    writer = CanonicalResults(objects)
    for parent, record in ((local_parent, native_child), (native_parent, local_child)):
        with pytest.raises(CanonicalResultRejected):
            asyncio.run(writer.prepare_result("workspace-a", parent=parent, record=record))
    assert not objects.calls

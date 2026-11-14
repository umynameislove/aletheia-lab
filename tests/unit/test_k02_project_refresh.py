"""Genuine uploaded-project refresh retains identity and immutable past facts."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.postgres_jobs import JobClaim
from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.project_refresh import PreparedRefresh, refresh_uploaded_project
from aletheia_lab.hosted.source_bundle import SourceRejected
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.importer import grant_immutable_project_root

CAPTURED = datetime(2026, 11, 11, tzinfo=UTC)
BUDGETS = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)


def _zip(value="0.6", *, missing=False, comment=b""):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.comment = comment
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        if not missing:
            archive.writestr("metrics.csv", f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{value}\n")
        archive.writestr("config.json", '{"seed":42}\n')
    return output.getvalue()


@pytest.fixture
def refresh_case(tmp_path):
    data = _zip()
    project = UploadProject(tmp_path, **BUDGETS)
    candidates = project.preview(data, captured_at=CAPTURED.isoformat())["mapping_candidates"]
    target, metric, config = (candidates[key][0] for key in ("targets", "metric_sources", "configs"))
    mapping = {
        "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                   "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
            "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
            "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": "baseline", "config_item_ids": []},
                 {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]}],
        "baseline_run_id": "baseline",
        "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}],
    }
    parent = project.confirm(data, workspace_id="ws1", source_id="src_original", mapping=mapping,
                             captured_at=CAPTURED.isoformat())
    return SimpleNamespace(root=tmp_path, data=data, parent=parent, origin=hashlib.sha256(data).hexdigest())


def _refresh(case, new, *, parent=None, before=None, **changes):
    return refresh_uploaded_project(parent=case.parent if parent is None else parent,
        origin_source_sha256=case.origin, before_payload=case.data if before is None else before,
        new_payload=new, source_id="src_refresh", private_root=case.root, captured_at=CAPTURED,
        **{**BUDGETS, **changes})


@pytest.mark.parametrize("repackage", [False, True])
def test_refresh_unchanged_retains_exact_first_capture_despite_new_source_id(refresh_case, repackage):
    case = refresh_case
    before = case.parent.model_dump_json()
    new = _zip(comment=b"new archive metadata") if repackage else case.data
    result = _refresh(case, new)
    assert result.status == "unchanged" and result.snapshot.model_dump_json() == before
    assert result.snapshot.source_id == "src_original" and result.parent_snapshot_id == case.parent.snapshot_id
    assert case.parent.model_dump_json() == before and list(case.root.iterdir()) == []


def test_refresh_changed_metrics_make_new_snapshot_and_preserve_project_and_past(refresh_case):
    case = refresh_case
    before = case.parent.model_dump_json()
    new = _zip("0.9")
    result = _refresh(case, new)
    assert result.status == "new_snapshot" and result.snapshot.snapshot_id != case.parent.snapshot_id
    assert result.snapshot.mapping.project_id == case.parent.mapping.project_id
    assert result.snapshot.source_id == "src_refresh" and result.snapshot.source_sha256 == hashlib.sha256(new).hexdigest()
    assert {(r.run_id, r.metric_value) for r in result.snapshot.mapping_result.metric_observations} == {
        ("baseline", 0.5), ("candidate", 0.9)}
    assert case.parent.model_dump_json() == before
    replay = _refresh(case, new, parent=result.snapshot, before=new)
    assert replay.status == "unchanged" and replay.snapshot == result.snapshot
    assert list(case.root.iterdir()) == []


@pytest.mark.parametrize("new", [_zip(missing=True), _zip("1e-9999")])
def test_incompatible_or_underflow_mapping_is_not_reported_unchanged(refresh_case, new):
    with pytest.raises(ProductError):
        _refresh(refresh_case, new)
    assert list(refresh_case.root.iterdir()) == []


def test_refresh_rejects_other_origin_parent_bytes_and_budget_without_leaking_paths(refresh_case):
    case = refresh_case
    with pytest.raises(ValueError):
        refresh_uploaded_project(parent=case.parent, origin_source_sha256="b" * 64,
            before_payload=case.data, new_payload=case.data, source_id="src_refresh",
            private_root=case.root, captured_at=CAPTURED, **BUDGETS)
    with pytest.raises(ValueError):
        _refresh(case, case.data, before=b"unrelated bytes")
    with pytest.raises(SourceRejected):
        _refresh(case, case.data, maximum_upload_bytes=1)
    with pytest.raises(ValueError):
        grant_immutable_project_root(case.root, source_sha256=case.origin, identity_source_sha256="bad")
    assert list(case.root.iterdir()) == []


def _orchestration(case, failure):
    new = _zip("0.9")
    reads = []
    finished = False
    sentinel = case.root / "keep.txt"
    sentinel.write_text("unrelated")
    claim = JobClaim("job_refresh", "ws1", "refresh", {"project_id": case.parent.mapping.project_id,
        "source_id": "src_refresh", "parent_snapshot_id": case.parent.snapshot_id, "expected_revision": 3}, SecretStr("a" * 64))

    class Sources:
        async def read(self, *, source_id, **kwargs):
            reads.append((source_id, finished))
            if finished and failure == "revoke":
                raise SourceNotFound()
            if source_id == "src_original":
                return case.data
            return b"changed" if finished and failure == "source_changed" else new

    class Parents:
        async def refresh_parent(self, *, claim):
            if finished and failure == "parent_changed":
                raise SourceNotFound()
            return case.parent, case.origin

    class Worker:
        async def run_upload(self, payload):
            nonlocal finished
            request = json.loads(payload)
            assert request["operation"] == "refresh" and request["origin_source_sha256"] == case.origin
            assert Path(request["before_source_path"]).read_bytes() == case.data
            assert Path(request["new_source_path"]).read_bytes() == new
            value = _refresh(case, new)
            finished = True
            if failure == "worker_invalid":
                return b'{"private_metadata":"denied"}'
            if failure == "worker_scope":
                other = build_upload_snapshot(workspace_id="ws_other", source_id="src_refresh",
                    source_sha256=value.snapshot.source_sha256, mapping=value.snapshot.mapping,
                    mapping_result=value.snapshot.mapping_result, captured_at=CAPTURED)
                value = PreparedRefresh(status="new_snapshot", parent_snapshot_id=case.parent.snapshot_id,
                                        snapshot=other)
                return value.model_dump_json().encode()
            return value.model_dump_json().encode()

    operations = CanonicalProjectOperations(Sources(), Worker(), Parents(), private_root=case.root)
    return operations, claim, reads, sentinel


@pytest.mark.parametrize("failure", [None, "revoke", "source_changed", "parent_changed", "worker_scope", "worker_invalid"])
def test_refresh_orchestration_rechecks_both_sources_and_parent_and_cleans_only_owned_inputs(refresh_case, failure):
    case = refresh_case
    operations, claim, reads, sentinel = _orchestration(case, failure)
    if failure in {"revoke", "source_changed", "parent_changed"}:
        with pytest.raises(SourceNotFound):
            asyncio.run(operations.prepare_refresh(claim))
    elif failure in {"worker_invalid", "worker_scope"}:
        with pytest.raises(ValueError):
            asyncio.run(operations.prepare_refresh(claim))
    else:
        value = asyncio.run(operations.prepare_refresh(claim))
        assert value.status == "new_snapshot"
    assert sentinel.read_text() == "unrelated" and not list((case.root / "inputs").iterdir())
    if failure is None:
        assert reads == [("src_original", False), ("src_refresh", False), ("src_original", True), ("src_refresh", True)]

"""Native upload identities survive worker/coordinator follow-up computation."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_native_upload_records import upload_case

from aletheia_lab.hosted import project_worker
from aletheia_lab.hosted.native_upload_records import UploadResultRecord, result_view
from aletheia_lab.hosted.postgres_jobs import JobClaim
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.product.view import ProductView

ROOT = Path(project_worker.__file__).resolve().parents[3]
QUESTION = "What remains uncertain within the selected evidence?"


def _claim(parent, workspace):
    return JobClaim("job-native-follow-up", workspace, "follow_up", {
        "project_id": parent.project.id, "parent_result_id": parent.result.id,
        "selection_kind": "claim", "selection_id": parent.claims[0].id, "question": QUESTION,
    }, SecretStr("a" * 64))


def _input(parent, workspace):
    return {"parent": parent.model_dump(mode="json"), "workspace_id": workspace,
            "selection_kind": "claim", "selection_id": parent.claims[0].id, "question": QUESTION}


async def _coordinate(parent, workspace, raw):
    class Parents:
        async def follow_up_parent(self, *, claim):
            assert claim.workspace_id == workspace
            return parent

    class Worker:
        async def run_upload(self, payload):
            request = json.loads(payload)
            assert request["workspace_id"] == workspace
            assert request["parent"]["snapshot"]["id"] == parent.snapshot.id
            assert request["parent"]["result"]["id"] == parent.result.id
            return raw

    return await CanonicalProjectOperations(object(), Worker(), Parents()).prepare_follow_up(_claim(parent, workspace))


def test_native_upload_worker_and_coordinator_keep_canonical_scope(tmp_path):
    snapshot, parent = upload_case(tmp_path)
    before = parent.model_dump_json()
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    environment["PYTHONPATH"] = str(ROOT / "src")
    request = {"operation": "follow_up", **_input(parent, snapshot.workspace_id)}
    child = subprocess.run([sys.executable, "-B", "-m", "aletheia_lab.hosted.upload_worker", str(tmp_path)],
                           input=json.dumps(request).encode(), capture_output=True, cwd=ROOT,
                           env=environment, timeout=30, check=False)
    assert child.returncode == 0 and child.stderr == b""
    record = asyncio.run(_coordinate(parent, snapshot.workspace_id, child.stdout))
    assert isinstance(record, UploadResultRecord)
    assert UploadResultRecord.model_validate_json(record.model_dump_json()) == record
    view = result_view(record)
    assert record.workspace_id == snapshot.workspace_id and record.snapshot_id == snapshot.snapshot_id
    assert record.project_id == parent.project.id and record.parent_result_id == parent.result.id
    assert record.record_id == view.result.id and record.record_id != parent.result.id
    assert view.snapshot.id.startswith("upload-snapshot-") and view.result.id.startswith("p6-result-")
    assert view.conversation.turns[-1].result_id == view.result.id
    assert view.conversation.turns[:-1] == parent.conversation.turns and view.evidence == parent.evidence
    assert view.result.disposition == "abstain" and view.conversation.turns[-1].abstained
    assert parent.model_dump_json() == before


@pytest.mark.parametrize("mismatch", ["workspace", "snapshot"])
def test_native_coordinator_rejects_other_worker_scope(tmp_path, mismatch):
    snapshot, parent = upload_case(tmp_path)
    worker_parent, workspace = parent, snapshot.workspace_id
    if mismatch == "workspace":
        workspace = "private-other-workspace"
    else:
        worker_parent = ProductView.model_validate_json(parent.model_dump_json().replace(
            parent.snapshot.id, "upload-snapshot-" + "b" * 64))
    record = project_worker._follow_up(project_worker.WorkerInput.model_validate(_input(worker_parent, workspace)))
    assert isinstance(record, UploadResultRecord)
    with pytest.raises(ValueError):
        asyncio.run(_coordinate(parent, snapshot.workspace_id, record.model_dump_json().encode()))


def test_native_follow_up_requires_explicit_workspace(tmp_path):
    _, parent = upload_case(tmp_path)
    request = _input(parent, "ws1")
    del request["workspace_id"]
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(json.dumps(request).encode()),
                               stdout=out, stderr=err) == 2
    assert out.getvalue() == b"" and err.getvalue() == "project_worker_failed\n"

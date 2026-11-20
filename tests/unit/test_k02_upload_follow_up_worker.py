"""Follow-up dispatch reuses the existing upload worker process boundary."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_k02_project_worker import _parent, _request

from aletheia_lab.hosted import upload_worker
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot
from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _validate_derived_result, _view_from_record

ROOT = Path(__file__).resolve().parents[2]


def test_existing_upload_worker_cli_and_supervisor_return_canonical_follow_up(tmp_path):
    parent = _parent()
    request = {"operation": "follow_up", **_request(parent)}
    raw = json.dumps(request).encode()
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    environment["PYTHONPATH"] = str(ROOT / "src")
    child = subprocess.run([sys.executable, "-B", "-m", "aletheia_lab.hosted.upload_worker", str(tmp_path)],
                           input=raw, capture_output=True, cwd=ROOT, env=environment, timeout=30, check=False)
    assert child.returncode == 0 and child.stderr == b""
    record = ProductLifecycleRecord.model_validate_json(child.stdout)
    assert record.parent_result_id == parent.result.id
    _validate_derived_result(parent, _view_from_record(record))

    async def run():
        worker = UploadWorkerSupervisor(python=Path(sys.executable).absolute(), source_root=ROOT / "src",
                                        private_root=tmp_path, slots=1, timeout_seconds=30)
        try:
            assert json.loads(await worker.run_upload(raw)) == json.loads(child.stdout)
        finally:
            await worker.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["preview", "confirm", "analyze"])
@pytest.mark.parametrize("field,value", [("parent", {}), ("selection_kind", "claim"),
                                         ("selection_id", "private-selection"), ("parent", None)])
def test_follow_up_fields_are_rejected_on_every_legacy_operation(tmp_path, monkeypatch, operation, field, value):
    request = {"operation": operation, field: value}
    if operation == "analyze":
        request.update(snapshot={}, question="What is observed?")
    else:
        request.update(source_base64="eA==", captured_at="2026-11-10T00:00:00Z")
        if operation == "confirm":
            request.update(workspace_id="ws1", source_id="src1", mapping={})
    entered = []

    def project(*args, **kwargs):
        entered.append(True)
        raise AssertionError("Injected fields must be rejected before domain work")

    out, err = io.BytesIO(), io.StringIO()
    monkeypatch.setattr(upload_worker, "UploadProject", project)
    monkeypatch.setattr(UploadSnapshot, "model_validate_json", project)
    monkeypatch.setattr(upload_worker.sys, "argv", ["upload_worker", str(tmp_path)])
    monkeypatch.setattr(upload_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(upload_worker.sys, "stdout", SimpleNamespace(buffer=out))
    monkeypatch.setattr(upload_worker.sys, "stderr", err)
    assert upload_worker.main() == 2
    assert out.getvalue() == b"" and err.getvalue() == "worker_failed\n"
    assert entered == []


@pytest.mark.parametrize("extra", [{"source_base64": "eA=="}, {"snapshot": {}}, {"mapping": None}])
def test_legacy_fields_are_rejected_on_follow_up(tmp_path, monkeypatch, extra):
    request = {"operation": "follow_up", **_request(_parent()), **extra}
    out, err = io.BytesIO(), io.StringIO()
    monkeypatch.setattr(upload_worker.sys, "argv", ["upload_worker", str(tmp_path)])
    monkeypatch.setattr(upload_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(upload_worker.sys, "stdout", SimpleNamespace(buffer=out))
    monkeypatch.setattr(upload_worker.sys, "stderr", err)
    assert upload_worker.main() == 2
    assert out.getvalue() == b"" and err.getvalue() == "worker_failed\n"

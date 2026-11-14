"""Canonical follow-up worker reuses the domain without writing its local store."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_product_results import _technical_failure_view, _view

from aletheia_lab.hosted import project_worker
from aletheia_lab.product.lifecycle import ProductLifecycleRecord, build_product_lifecycle_record
from aletheia_lab.product.results import (
    ProductResultEnvelope,
    _validate_derived_result,
    _view_from_record,
)

ROOT = Path(__file__).resolve().parents[2]


def _parent(*, technical_failure=False):
    view = _technical_failure_view() if technical_failure else _view()
    record = build_product_lifecycle_record(record_kind="result", project_id=view.project.id,
        snapshot_id=view.snapshot.id, payload=ProductResultEnvelope(view=view.model_dump(mode="json")).model_dump(mode="json"))
    return _view_from_record(record)


def _request(parent, selection_kind="claim"):
    selection_id = parent.claims[0].id if selection_kind == "claim" else parent.graph.nodes[0].id
    return {"parent": parent.model_dump(mode="json"), "selection_kind": selection_kind,
            "selection_id": selection_id, "question": "What remains uncertain within this selection?"}


@pytest.mark.parametrize("kind", ["claim", "node"])
def test_subprocess_returns_stable_canonical_child_and_preserves_parent(kind):
    parent = _parent()
    before = parent.model_dump_json()
    raw = json.dumps(_request(parent, kind), allow_nan=False).encode()
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    environment["PYTHONPATH"] = str(ROOT / "src")
    first = subprocess.run([sys.executable, "-B", "-m", "aletheia_lab.hosted.project_worker", "follow-up"],
                           input=raw, capture_output=True, cwd=ROOT, env=environment, timeout=30, check=False)
    assert first.returncode == 0 and first.stderr == b""
    record = ProductLifecycleRecord.model_validate_json(first.stdout)
    child = _view_from_record(record)
    assert record.parent_result_id == parent.result.id
    assert record.project_id == parent.project.id and record.snapshot_id == parent.snapshot.id
    assert record.record_id == child.result.id and child.result.id != parent.result.id
    assert child.conversation.turns[-1].result_id == child.result.id
    _validate_derived_result(parent, child)
    assert parent.model_dump_json() == before
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(raw), stdout=out, stderr=err) == 0
    assert out.getvalue() == first.stdout and err.getvalue() == ""


def test_technical_failure_parent_produces_no_diagnosis():
    parent = _parent(technical_failure=True)
    out, err = io.BytesIO(), io.StringIO()
    request = _request(parent, "node")
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(json.dumps(request).encode()),
                               stdout=out, stderr=err) == 0
    child = _view_from_record(ProductLifecycleRecord.model_validate_json(out.getvalue()))
    assert child.result.status == "technical_failure" and child.result.disposition is None
    assert child.claims == () and child.conversation.turns[-1].abstained


@pytest.mark.parametrize("change", [
    {"selection_id": "private-foreign-selection"}, {"selection_kind": "private-kind"},
    {"question": "private-question\nnot-allowed"}, {"private_extra": "private-value"},
])
def test_invalid_selection_or_request_has_no_output_or_private_diagnostics(change):
    request = {**_request(_parent()), **change}
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(json.dumps(request).encode()),
                               stdout=out, stderr=err) == 2
    assert out.getvalue() == b"" and err.getvalue() == "project_worker_failed\n"


@pytest.mark.parametrize("raw", [b"not-json-private", b'{"private":1,"private":2}', b"[]"])
def test_invalid_json_is_rejected(raw):
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(raw), stdout=out, stderr=err) == 2
    assert out.getvalue() == b"" and err.getvalue() == "project_worker_failed\n"


def test_demo_parent_is_rejected():
    request = _request(_parent())
    request["parent"]["demo_only"] = True
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["follow-up"], stdin=io.BytesIO(json.dumps(request).encode()),
                               stdout=out, stderr=err) == 2
    assert out.getvalue() == b"" and err.getvalue() == "project_worker_failed\n"


def test_input_output_budgets_and_unknown_operation_fail_without_output(monkeypatch):
    raw = json.dumps(_request(_parent())).encode()
    for field in ("MAX_INPUT_BYTES", "MAX_OUTPUT_BYTES"):
        with monkeypatch.context() as patch:
            patch.setattr(project_worker, field, 5)
            out, err = io.BytesIO(), io.StringIO()
            assert project_worker.main(["follow-up"], stdin=io.BytesIO(raw), stdout=out, stderr=err) == 2
            assert out.getvalue() == b"" and err.getvalue() == "project_worker_failed\n"
    out, err = io.BytesIO(), io.StringIO()
    assert project_worker.main(["refresh"], stdin=io.BytesIO(raw), stdout=out, stderr=err) == 2
    assert out.getvalue() == b"" and err.getvalue() == "project_worker_invalid_operation\n"

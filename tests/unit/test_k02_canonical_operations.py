"""Canonical follow-up compute over synthetic parent/worker ports; no publication."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import SecretStr
from test_k02_project_worker import _parent, _request

from aletheia_lab.hosted import project_operations, project_worker
from aletheia_lab.hosted.postgres_jobs import JobClaim
from aletheia_lab.product.follow_up import resolve_product_follow_up_selection
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.product.results import _validate_derived_result, _view_from_record


def _worker_output(payload, mode):
    request = json.loads(payload)
    assert request.pop("operation") == "follow_up"
    if mode == "invalid":
        return b'{"private_invalid_output":true}'
    if mode == "question":
        request["question"] = "A different but valid question?"
    record = project_worker._follow_up(project_worker.WorkerInput.model_validate(request))
    view = record.payload()["view"]
    if mode == "demo":
        view["demo_only"] = True
    if mode == "expanded":
        view["conversation"]["turns"][-1]["visible_evidence_ids"] = [item["id"] for item in view["evidence"]]
    record = build_product_lifecycle_record(record_kind="result", payload={"schema_version": record.payload()["schema_version"],
        "view": view}, project_id=record.project_id, snapshot_id=record.snapshot_id,
        parent_result_id="p6-result-" + "b" * 64 if mode == "parent" else record.parent_result_id)
    return record.model_dump_json().encode()


def _system(*, kind="claim", technical=False, mode=None):
    parent = _parent(technical_failure=technical)
    request = _request(parent, kind)
    if mode == "expanded":
        request["selection_kind"] = "node"
        request["selection_id"] = next(node.id for node in parent.graph.nodes if node.kind == "Snapshot")
    claim = JobClaim("job1", "ws1", "follow_up",
        {"project_id": parent.project.id, "parent_result_id": parent.result.id, "expected_revision": 3,
         **{key: value for key, value in request.items() if key != "parent"}}, SecretStr("a" * 64))
    parent_calls, worker_calls = [], []

    class Parents:
        async def follow_up_parent(self, *, claim):
            parent_calls.append(claim)
            return parent

    class Worker:
        async def run_upload(self, payload):
            worker_calls.append(json.loads(payload))
            return _worker_output(payload, mode)

    operations = project_operations.CanonicalProjectOperations(object(), Worker(), Parents())
    return operations, claim, parent, parent_calls, worker_calls


@pytest.mark.parametrize("kind,technical", [("claim", False), ("node", False), ("node", True)])
def test_follow_up_uses_exact_claim_parent_and_keeps_canonical_scope(kind, technical):
    operations, claim, parent, parent_calls, worker_calls = _system(kind=kind, technical=technical)
    before = parent.model_dump_json()
    record = asyncio.run(operations.prepare_follow_up(claim))
    child = _view_from_record(record)
    assert parent_calls == [claim] and parent_calls[0] is claim
    assert len(worker_calls) == 1 and worker_calls[0]["parent"] == parent.model_dump(mode="json")
    assert parent.model_dump_json() == before
    assert record.parent_result_id == parent.result.id and record.record_id == child.result.id
    assert record.project_id == parent.project.id and record.snapshot_id == parent.snapshot.id
    selection = resolve_product_follow_up_selection(parent, selection_kind=claim.request["selection_kind"],
        selection_id=claim.request["selection_id"], question=claim.request["question"])
    assert child.conversation.turns[-1].visible_evidence_ids == selection.evidence_ids
    assert child.conversation.turns[-1].question == claim.request["question"]
    assert child.conversation.turns[-1].abstained
    assert child.result.disposition == (None if technical else "abstain")
    _validate_derived_result(parent, child)


@pytest.mark.parametrize("field,value", [
    ("operation", "analyze"), ("project_id", "p3-project-" + "c" * 64),
    ("parent_result_id", "p6-result-" + "d" * 64), ("selection_id", "foreign-selection"),
])
def test_wrong_claim_operation_parent_or_selection_never_runs_worker(field, value):
    operations, claim, _, parent_calls, worker_calls = _system()
    claim = replace(claim, operation=value) if field == "operation" else replace(
        claim, request={**claim.request, field: value})
    with pytest.raises(ValueError):
        asyncio.run(operations.prepare_follow_up(claim))
    assert not worker_calls
    assert len(parent_calls) == (field != "operation")


@pytest.mark.parametrize("mode", ["question", "parent", "invalid", "demo", "expanded"])
def test_worker_cannot_change_question_parent_or_selected_evidence_scope(mode):
    operations, claim, parent, _, worker_calls = _system(mode=mode)
    before = parent.model_dump_json()
    if mode == "expanded":
        selection = resolve_product_follow_up_selection(parent, selection_kind=claim.request["selection_kind"],
            selection_id=claim.request["selection_id"], question=claim.request["question"])
        assert not selection.evidence_ids and parent.evidence
    with pytest.raises(ValueError):
        asyncio.run(operations.prepare_follow_up(claim))
    assert len(worker_calls) == 1 and parent.model_dump_json() == before


@pytest.mark.parametrize("field", ["MAX_INPUT_BYTES", "MAX_OUTPUT_BYTES"])
def test_worker_budgets_reject_without_publishing_or_widening_scope(monkeypatch, field):
    operations, claim, parent, _, worker_calls = _system()
    before = parent.model_dump_json()
    if field == 'MAX_INPUT_BYTES':
        monkeypatch.setattr(operations, '_worker_input_limit', lambda: 5)
    else:
        monkeypatch.setattr(project_operations, field, 5)
    with pytest.raises(ValueError):
        asyncio.run(operations.prepare_follow_up(claim))
    assert len(worker_calls) == (field == "MAX_OUTPUT_BYTES")
    assert parent.model_dump_json() == before


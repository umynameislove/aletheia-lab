"""Producer interface checks; schema coverage does not replace runtime policy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from aletheia_lab.hosted_contract.job_page import Job, JobOutput
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor, GraphView
from aletheia_lab.product.view import ProductGraph

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "fixtures/k04/producer-20261110-native"
METADATA = ("operation", "created_at", "deadline_at", "terminal_at", "attempt",
            "max_attempts", "retryable", "output_url")
CREATED = "2026-11-11T10:00:00Z"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(model) -> Draft202012Validator:
    checker = FormatChecker()
    assert "date-time" in checker.checkers, "Independent date-time checking is required"
    schema = model.model_json_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=checker)


@pytest.fixture
def legacy_job():
    return _read(ROOT / "fixtures/j01/producer-review-v7/job.json")


@pytest.fixture
def metadata_job(legacy_job):
    return {**legacy_job, "status": "succeeded", "progress": None, "operation": "refresh",
            "created_at": CREATED, "deadline_at": "2026-11-11T17:10:00+07:00",
            "terminal_at": "2026-11-11T10:05:00Z", "attempt": 2, "max_attempts": 3,
            "retryable": False,
            "output_url": f"/v1/workspaces/{legacy_job['workspace_id']}/jobs/{legacy_job['job_id']}/output"}


def test_legacy_job_metadata_is_explicit_unknown_without_invented_values(legacy_job):
    job = Job.model_validate_json(json.dumps(legacy_job))
    serialized = job.model_dump(mode="json")
    assert {key: serialized[key] for key in legacy_job} == legacy_job
    assert {name: serialized[name] for name in METADATA} == dict.fromkeys(METADATA)
    _validator(Job).validate(legacy_job)
    _validator(Job).validate(serialized)


@pytest.mark.parametrize("same_instant", [False, True])
def test_recorded_job_metadata_is_aware_scoped_and_roundtrips(metadata_job, same_instant):
    if same_instant:
        metadata_job.update(deadline_at=CREATED, terminal_at=CREATED)
    job = Job.model_validate_json(json.dumps(metadata_job))
    assert job.operation == "refresh" and job.progress is None and job.retryable is False
    assert job.created_at.utcoffset() is not None
    assert job.created_at <= job.deadline_at and job.created_at <= job.terminal_at
    assert (job.attempt, job.max_attempts) == (2, 3)
    assert job.output_url == metadata_job["output_url"]
    assert Job.model_validate_json(job.model_dump_json()) == job
    _validator(Job).validate(json.loads(job.model_dump_json()))


@pytest.mark.parametrize("change", [
    {"created_at": "2026-11-11T10:00:00"}, {"deadline_at": "2026-11-11T10:10:00"},
    {"terminal_at": "2026-11-11T10:05:00"}, {"attempt": 0}, {"max_attempts": 0},
    {"attempt": True}, {"retryable": 1}, {"retryable": "false"},
    {"operation": "unsupported"}, {"output_url": ""}, {"output_url": "https://else.invalid/output"},
])
def test_job_scalar_boundaries_reject_in_model_and_independent_schema(metadata_job, change):
    body = {**metadata_job, **change}
    with pytest.raises(ValidationError):
        Job.model_validate_json(json.dumps(body))
    assert not _validator(Job).is_valid(body)


@pytest.mark.parametrize("change", [
    {"deadline_at": "2026-11-11T09:59:59Z"}, {"terminal_at": "2026-11-11T09:59:59Z"},
    {"status": "queued"}, {"attempt": 4},
    {"workspace_id": "ws_other"}, {"job_id": "job_other"},
])
def test_job_cross_field_constraints_are_model_checks_not_schema_promises(metadata_job, change):
    body = {**metadata_job, **change}
    with pytest.raises(ValidationError):
        Job.model_validate_json(json.dumps(body))
    _validator(Job).validate(body)


def test_generic_output_accepts_refresh_and_rejects_unknown_operation():
    body = {**_read(NATIVE / "job-output.json"), "operation": "refresh"}
    model = JobOutput[dict[str, object]]
    output = model.model_validate_json(json.dumps(body))
    assert output.operation == "refresh" and output.result == body["result"]
    _validator(model).validate(body)
    body["operation"] = "unsupported"
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(body))
    assert not _validator(model).is_valid(body)


def test_legacy_complete_graph_defaults_to_false_and_null_continuation():
    body = _read(NATIVE / "graph.json")
    body.pop("truncated", None)
    body.pop("next_cursor", None)
    value = GraphView.model_validate_json(json.dumps(body))
    assert isinstance(value.graph, ProductGraph)
    assert value.graph == ProductGraph.model_validate_json(json.dumps(body["graph"]))
    assert value.truncated is False and value.next_cursor is None
    _validator(GraphView).validate(json.loads(value.model_dump_json()))


def test_truncated_graph_keeps_a_nonempty_continuation():
    body = {**_read(NATIVE / "graph.json"), "truncated": True, "next_cursor": "opaque-next-page"}
    value = GraphView.model_validate_json(json.dumps(body))
    assert value.truncated is True and value.next_cursor == "opaque-next-page"
    _validator(GraphView).validate(body)


def test_full_graph_cannot_claim_a_continuation_even_when_schema_types_are_valid():
    body = {**_read(NATIVE / "graph.json"), "truncated": False, "next_cursor": "opaque-next-page"}
    with pytest.raises(ValidationError):
        GraphView.model_validate_json(json.dumps(body))
    _validator(GraphView).validate(body)


@pytest.mark.parametrize("change", [
    {"truncated": 0}, {"truncated": 1}, {"truncated": "false"},
    {"next_cursor": ""}, {"next_cursor": 1}, {"next_cursor": "x" * 4097},
])
def test_graph_boolean_and_cursor_boundaries_are_strict(change):
    body = {**_read(NATIVE / "graph.json"), "truncated": True, **change}
    with pytest.raises(ValidationError):
        GraphView.model_validate_json(json.dumps(body))
    assert not _validator(GraphView).is_valid(body)


@pytest.mark.parametrize("explicit_null", [False, True])
def test_legacy_export_expiry_is_unknown_not_an_infinite_or_clock_default(explicit_null):
    body = _read(NATIVE / "export-json.json")
    body.pop("expires_at", None)
    if explicit_null:
        body["expires_at"] = None
    value = ExportDescriptor.model_validate_json(json.dumps(body))
    assert value.expires_at is None
    assert value.model_dump(mode="json")["expires_at"] is None
    _validator(ExportDescriptor).validate(body)


@pytest.fixture
def durable_export():
    body = _read(NATIVE / "export-json.json")
    export_id = body["export_id"] + "_" + "0123456789abcdef" * 2
    return {**body, "export_id": export_id, "expires_at": "2026-11-11T12:00:00Z",
            "download_url": f"/v1/workspaces/{body['workspace_id']}/exports/{export_id}/download"}


@pytest.mark.parametrize("expires_at", ["2026-11-11T12:00:00Z", "2026-11-11T19:00:00+07:00"])
def test_export_records_a_real_timezone_aware_expiry(durable_export, expires_at):
    body = {**durable_export, "expires_at": expires_at}
    value = ExportDescriptor.model_validate_json(json.dumps(body))
    assert value.expires_at.utcoffset() is not None
    assert ExportDescriptor.model_validate_json(value.model_dump_json()) == value
    _validator(ExportDescriptor).validate(body)


def test_export_rejects_naive_expiry_in_model_and_format_checker(durable_export):
    body = {**durable_export, "expires_at": "2026-11-11T12:00:00"}
    with pytest.raises(ValidationError):
        ExportDescriptor.model_validate_json(json.dumps(body))
    assert not _validator(ExportDescriptor).is_valid(body)


@pytest.mark.parametrize("nonce", ["", "a" * 31, "a" * 33, "A" * 32, "g" * 32])
def test_export_rejects_missing_or_malformed_nonce_in_model_and_schema(durable_export, nonce):
    export_id = _read(NATIVE / "export-json.json")["export_id"] + "_" + nonce
    body = {**durable_export, "export_id": export_id,
            "download_url": f"/v1/workspaces/{durable_export['workspace_id']}/exports/{export_id}/download"}
    with pytest.raises(ValidationError):
        ExportDescriptor.model_validate_json(json.dumps(body))
    assert not _validator(ExportDescriptor).is_valid(body)


@pytest.mark.parametrize("omit", [False, True])
def test_nonce_handle_requires_an_actual_nonnull_expiry_in_model_and_schema(durable_export, omit):
    body = {**durable_export, "expires_at": None}
    if omit:
        body.pop("expires_at")
    with pytest.raises(ValidationError):
        ExportDescriptor.model_validate_json(json.dumps(body))
    assert not _validator(ExportDescriptor).is_valid(body)


def test_legacy_handle_cannot_claim_a_durable_expiry_in_model_or_schema():
    body = {**_read(NATIVE / "export-json.json"), "expires_at": "2026-11-11T12:00:00Z"}
    with pytest.raises(ValidationError):
        ExportDescriptor.model_validate_json(json.dumps(body))
    assert not _validator(ExportDescriptor).is_valid(body)


@pytest.mark.parametrize("change", [
    {"format": "markdown"}, {"result_id": "p6-result-" + "0" * 64},
    {"workspace_id": "ws_other"}, {"download_url": "https://attacker.invalid/download"},
    {"download_url": "/v1/workspaces/ws_other/exports/ex_other/download"},
])
def test_export_exact_bindings_are_model_checks_not_schema_equality_promises(durable_export, change):
    body = {**durable_export, **change}
    with pytest.raises(ValidationError):
        ExportDescriptor.model_validate_json(json.dumps(body))
    _validator(ExportDescriptor).validate(body)

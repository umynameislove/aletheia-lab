"""Tests for review shapes, not evidence of durable job or cursor behavior."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from aletheia_lab.hosted_contract.job_page import Job, JobReceipt, Page

V = "product-api/v1-draft"


def test_receipt_url_cannot_escape_its_workspace_or_job() -> None:
    body = {
        "schema_version": V,
        "workspace_id": "ws_1",
        "job_id": "job_1",
        "status": "queued",
        "request_id": "req_1",
        "status_url": "/v1/workspaces/ws_1/jobs/job_1",
    }
    assert JobReceipt.model_validate_json(json.dumps(body)).job_id == "job_1"
    for value in ("https://attacker.invalid/x", "/v1/workspaces/ws_2/jobs/job_1"):
        body["status_url"] = value
        with pytest.raises(ValidationError):
            JobReceipt.model_validate_json(json.dumps(body))


@pytest.mark.parametrize("state", ["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled", "expired"])
def test_job_allows_unknown_progress(state: str) -> None:
    body = {"schema_version": V, "workspace_id": "ws_1", "job_id": "job_1", "status": state, "progress": None}
    assert Job.model_validate_json(json.dumps(body)).progress is None
    body["progress"] = 1.1
    with pytest.raises(ValidationError):
        Job.model_validate_json(json.dumps(body))


def test_page_total_and_cursor_fail_closed() -> None:
    body = {
        "schema_version": V,
        "workspace_id": "ws_1",
        "items": [{"id": "result_1"}],
        "next_cursor": None,
        "total": None,
        "total_kind": "unknown",
    }
    model = Page[dict[str, str]]
    assert model.model_validate_json(json.dumps(body)).total is None
    body["total_kind"] = "exact"
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(body))
    body.update(total=0, next_cursor="")
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(body))


def test_legacy_job_page_fixtures_preserve_their_published_fields() -> None:
    root = Path(__file__).resolve().parents[2] / "fixtures" / "j01" / "producer-review-v7"
    checker = FormatChecker()
    assert "date-time" in checker.checkers
    for name, model in (("job-receipt", JobReceipt), ("job", Job), ("page", Page[dict[str, str]])):
        legacy = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
        schema = json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=checker).validate(legacy)
        current = model.model_validate_json(json.dumps(legacy)).model_dump(mode="json")
        assert {key: current[key] for key in legacy} == legacy
        if model is Job:
            assert current["progress"] is None
            for key in ("operation", "created_at", "deadline_at", "terminal_at", "attempt",
                        "max_attempts", "retryable", "output_url"):
                assert key not in legacy
                assert current[key] is None  # Missing historical metadata remains unknown.


def test_fresh_job_page_export_matches_current_models_and_independent_schemas(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "producer-current"
    runpy.run_path(str(root / "scripts" / "export_j01_review.py"))["main"]([str(output)])
    checker = FormatChecker()
    assert "date-time" in checker.checkers
    assert not checker.conforms("2026-11-11T10:00:00", "date-time")
    for name, model in (("job-receipt", JobReceipt), ("job", Job), ("page", Page[dict[str, str]])):
        payload = json.loads((output / f"{name}.json").read_text(encoding="utf-8"))
        schema = json.loads((output / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert schema == model.model_json_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=checker).validate(payload)
        assert model.model_validate_json(json.dumps(payload)).model_dump(mode="json") == payload
    assert json.loads((output / "job.json").read_text(encoding="utf-8"))["progress"] is None

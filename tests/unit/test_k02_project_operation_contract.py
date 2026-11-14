"""Independent schema validation of the additive follow-up producer export."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]


def test_export_is_generated_from_current_models_and_preserves_a_strict_request(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts/export_k02_project_operations.py"))
    namespace["export"](tmp_path)
    schemas = {}
    for path in tmp_path.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = schema
    assert len(schemas) == 4
    for fixture in ("follow-up-request", "job-receipt"):
        value = json.loads((tmp_path / (fixture + ".json")).read_text(encoding="utf-8"))
        Draft202012Validator(schemas[fixture + ".schema.json"], format_checker=FormatChecker()).validate(value)
    request = json.loads((tmp_path / "follow-up-request.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schemas["follow-up-request.schema.json"])
    for change in ({"schema_version": "unknown"}, {"selection_kind": "source"},
                   {"project_id": "caller-declared-project"}, {"parent_result_id": "caller-declared-parent"},
                   {"question": True}):
        with pytest.raises(ValidationError):
            validator.validate({**request, **change})
    document = json.loads((tmp_path / "openapi-project-operations.json").read_text(encoding="utf-8"))
    operation = document["paths"][namespace["ROUTE"]]["post"]
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/JobReceipt"}
    assert {item["name"] for item in operation["parameters"] if item["required"]} == {
        "workspace_id", "result_id", "If-Match", "Idempotency-Key", "Origin", "X-CSRF-Token"}


@pytest.mark.parametrize("length,status", [(4000, 202), (4001, 422)])
def test_analysis_submission_schema_and_http_agree_on_the_original_question_limit(tmp_path, length, status):
    from test_k02_job_boundary import PATH, _request, _system

    from aletheia_lab.hosted.job_http import Submission

    value = {"operation": "analyze", "snapshot_id": "upload-snapshot-" + "a" * 64, "question": "x" * length}
    validator = Draft202012Validator(Submission.model_json_schema())
    assert validator.is_valid(value) is (status == 202)
    app, jobs, headers = _system(tmp_path)
    observed, _, _ = _request(app, headers, method="POST", path=PATH, body=json.dumps(value).encode())
    assert observed == status and jobs.enqueue_calls == (1 if status == 202 else 0)

"""Export the additive canonical follow-up contract without changing frozen J01 reviews."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast, get_args

from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.hosted_contract.project_operations import FollowUpRequest
from aletheia_lab.hosted_contract.session_capabilities import Problem, Version

VERSION = cast(Version, get_args(Version)[0])
ROUTE = "/v1/workspaces/{workspace_id}/results/{result_id}/follow-ups"


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    models = {"FollowUpRequest": FollowUpRequest, "JobReceipt": JobReceipt, "Job": Job, "Problem": Problem}
    schemas = {name: model.model_json_schema() for name, model in models.items()}
    request = FollowUpRequest(schema_version=VERSION, selection_kind="claim",
        selection_id="p6-claim-" + "b" * 64, question="What remains uncertain about this selected claim?")
    receipt = JobReceipt(schema_version=VERSION, workspace_id="ws_example", job_id="job_example",
        status="queued", request_id="req_example", status_url="/v1/workspaces/ws_example/jobs/job_example")
    values = {"follow-up-request.json": request.model_dump(mode="json"),
              "job-receipt.json": receipt.model_dump(mode="json")}
    schema_files = {"FollowUpRequest": "follow-up-request", "JobReceipt": "job-receipt", "Job": "job", "Problem": "problem"}
    values.update({schema_files[name] + ".schema.json": schema for name, schema in schemas.items()})
    errors = (400, 401, 403, 404, 409, 412, 413, 415, 422, 428, 431, 500, 503)
    responses: dict[str, object] = {str(code): {"description": "Shared safe Problem",
        "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}}
        for code in errors}
    responses["202"] = {"description": "Durably accepted; reuse the original key after an unknown response outcome.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobReceipt"}}}}
    values["openapi-project-operations.json"] = {
        "openapi": "3.1.0", "info": {"title": "Aletheia canonical project operations review", "version": VERSION},
        "paths": {ROUTE: {"post": {"operationId": "submitCanonicalFollowUp",
            "description": "Parent/project resolve server-side. Analyze permission and project revision are rechecked at publication.",
            "security": [{"ServerSession": []}], "parameters": [
                {"in": "path", "name": "workspace_id", "required": True,
                 "schema": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}},
                {"in": "path", "name": "result_id", "required": True,
                 "schema": {"type": "string", "pattern": "^p6-result-[0-9a-f]{64}$"}},
                {"in": "header", "name": "If-Match", "required": True,
                 "description": "Strong quoted project revision, maximum 9223372036854775806.",
                 "schema": {"type": "string", "pattern": '^"(?:0|[1-9][0-9]{0,18})"$'}},
                {"in": "header", "name": "Idempotency-Key", "required": True,
                 "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,128}$"}},
                {"in": "header", "name": "Origin", "required": True, "schema": {"type": "string", "format": "uri"}},
                {"in": "header", "name": "X-CSRF-Token", "required": True, "schema": {"type": "string", "minLength": 1}},
            ], "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/FollowUpRequest"}}}}, "responses": responses}}},
        "components": {"schemas": schemas, "securitySchemes": {"ServerSession": {
            "type": "apiKey", "in": "cookie", "name": "__Host-aletheia_session"}}},
    }
    for name, value in values.items():
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args().output)

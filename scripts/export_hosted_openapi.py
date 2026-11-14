"""Compose the maintained hosted producer contracts; offline, no runtime or DB I/O.

Run with the existing project dependencies and --repo, --inventory, --output.
The inventory supplies requirement IDs only. Paths and payload schemas come from
the current exporters/models and are checked against the actual route matchers.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(repo: Path) -> dict[str, str]:
    paths = [path for group in ("hosted", "hosted_auth", "hosted_contract", "product", "project")
             for path in (repo / "src/aletheia_lab" / group).rglob("*")
             if path.suffix in (".py", ".sql", ".json")]
    paths += list((repo / "scripts").glob("export_*contract.py"))
    paths += [repo / "scripts/export_j01_review.py", repo / "scripts/export_k02_project_operations.py",
              repo / "scripts/export_hosted_openapi.py", repo / 'pyproject.toml']
    paths += [repo / 'src/aletheia_lab/product/fonts' / name
              for name in ('NotoSansMono-Regular.ttf', 'OFL.txt', 'provenance.json')]
    paths += [repo / "fixtures/k04/canonical-seed-v1.json", repo / "fixtures/k04/native-seed-v1.json"]
    return {path.relative_to(repo).as_posix(): digest(path) for path in sorted(set(paths))}


def document(repo: Path) -> tuple[dict, dict, list[tuple[object, object]], list[tuple[str, str, str]]]:
    sys.path[:0] = [str(repo / "src"), str(repo / "scripts")]
    import export_k04_contract as k04
    from pydantic import BaseModel, TypeAdapter

    from aletheia_lab.hosted.source_http import SourceUploadReceipt
    from aletheia_lab.hosted.upload_comparison import UploadGenerationComparison
    from aletheia_lab.hosted.upload_staging_http import _Empty as UploadEmpty
    from aletheia_lab.hosted_auth.logout_http import _EmptyBody as LogoutEmpty
    from aletheia_lab.hosted_auth.session_http import _EmptyBody as SessionEmpty
    from aletheia_lab.hosted_contract.deletions import DeletionStatus
    from aletheia_lab.hosted_contract.imports import ConfirmImport, PreviewImport, PreviewRequest
    from aletheia_lab.hosted_contract.job_attempts import JobAttemptPage
    from aletheia_lab.hosted_contract.job_page import JobOutput, JobReceipt
    from aletheia_lab.hosted_contract.job_updates import JobUpdate
    from aletheia_lab.hosted_contract.project_analysis import AnalysisRequest
    from aletheia_lab.hosted_contract.project_operations import FollowUpJobOutput, FollowUpRequest
    from aletheia_lab.hosted_contract.project_refresh import RefreshRequest, RefreshResult
    from aletheia_lab.hosted_contract.runtime_management import (
        ChangeProject,
        ChangeWorkspaceSettings,
        ProjectDetails,
        WorkspaceSettings,
    )
    from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Identifier, Session
    from aletheia_lab.hosted_contract.uploads import MAX_PART_BYTES, UploadManifest, UploadStatus
    from aletheia_lab.hosted_contract.workspaces import EvidencePage, ResultHeader
    from aletheia_lab.product.view import AnyProductView

    spec = k04.document()
    spec["info"] = {"title": "Aletheia current hosted producer API", "version": k04.VERSION,
        "description": "Actual composed handler contracts, including conditional extensions. Offline generation validates schemas/routes, not runtime deployment, response outcomes or joint acceptance."}
    spec.pop("x-extension-revision", None)
    spec["x-contract-scope"] = {
        "authority": "Current handler models and existing K04 exporter; original inventory supplies requirement IDs only.",
        "composition": "upload_runtime.compose_upload_runtime and managed_runtime.ManagedRuntime",
        "profiles": {"legacy_sources": "resumable_uploads=False; /sources is mounted; current effective ceiling is min(capabilities, repository, 10 MiB)",
                     "resumable_imports": "resumable_uploads=True; /uploads and /imports replace /sources; requires verified staging and preview extensions",
                     "catalog": "catalog_access is not None; current canonical schema/policy/review readiness required",
                     "management": "runtime_management is not None; project metadata and workspace limits",
                     "project_jobs": "follow_up_available=True; refresh, analyses and follow-ups share the durable queue"},
        "not_claimed": ["Every route enabled in every profile", "38-operation acceptance", "Production or capacity certification"],
    }
    schemas = spec["components"]["schemas"]
    checked_models: dict[str, type[BaseModel]] = {}

    def model(value: type[BaseModel], alias: str | None = None) -> dict:
        name = alias or value.__name__
        shape = value.model_json_schema(ref_template="#/components/schemas/{model}")
        for key, item in {**shape.pop("$defs", {}), name: shape}.items():
            if key in schemas and schemas[key] != item:
                raise ValueError(f"Producer schema name conflict: {key}")
            schemas[key] = item
        checked_models[name] = value
        return {"$ref": "#/components/schemas/" + name}

    id_shape = TypeAdapter(Identifier).json_schema()
    spec['x-canonical-payload-schemas'] = {
        'hosted-upload-generation-comparison/v1': model(UploadGenerationComparison)}
    problem = {"$ref": "#/components/schemas/Problem"}
    empty = model(LogoutEmpty, "LogoutEmpty")
    session_empty = model(SessionEmpty, "SessionMutationEmpty")
    upload_empty = model(UploadEmpty, "UploadCompleteEmpty")
    key = {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,128}$"}
    revision = {"type": "string", "pattern": '^"(?:0|[1-9][0-9]{0,18})"$'}
    checks: list[tuple[str, str, str]] = []

    def parameter(name: str, shape: dict, where: str = "query", required: bool = False, description: str = "") -> dict:
        return {"name": name, "in": where, "required": required, "schema": shape, "description": description}

    def add(path: str, method: str, request: dict | None, response: dict | None, status: str,
            handler: str, condition: str = "managed upload runtime", *, media: str = "application/json",
            request_media: str = "application/json", cas: bool = False, keyed: bool = False,
            public: bool = False, description: str = "") -> dict:
        parameters = [parameter(name, id_shape, "path", True) for name in re.findall(r"{([^}]+)}", path)]
        strict_ids = {}
        if handler.endswith(("ProjectRefreshApplication", "ProjectAnalysisApplication", "RuntimeManagementApplication")):
            strict_ids["project_id"] = "^p3-project-[0-9a-f]{64}$"
        if handler.endswith("ProjectOperationsApplication"):
            strict_ids["result_id"] = "^p6-result-[0-9a-f]{64}$"
        if handler.endswith("ImportApplication"):
            strict_ids["preview_id"] = "^prev_[0-9a-f]{32}$"
        for item in parameters:
            if item["name"] in strict_ids:
                item["schema"] = {"type": "string", "pattern": strict_ids[item["name"]]}
        mutation = method not in ("get", "head")
        if mutation:
            parameters.append(parameter("Origin", {"type": "string", "format": "uri"}, "header", True,
                                        "Must match the configured app origin; current session-bound CSRF checks also apply."))
        if cas:
            parameters.append(parameter("If-Match", revision, "header", True, "Strong quoted revision, maximum 9223372036854775806."))
        if keyed:
            parameters.append(parameter("Idempotency-Key", key, "header", True, "Identical request and scope reuse the original key after an unknown outcome."))
        success = {"description": description or "Actual handler response; server rechecks current authority.",
                   "headers": {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}}
        if response is not None and method != "head":
            success["content"] = {media: {"schema": response}}
        if cas or response in ({"$ref": "#/components/schemas/ProjectDetails"}, {"$ref": "#/components/schemas/WorkspaceSettings"}):
            success["headers"]["ETag"] = {"schema": revision}
        responses = {status: success}
        for code in (400, 401, 403, 404, 405, 409, 410, 412, 413, 415, 422, 428, 431, 500, 503):
            responses[str(code)] = {"description": "Safe Problem when the corresponding guard/repository rejects the operation."}
            if method != "head":
                responses[str(code)]["content"] = {"application/problem+json": {"schema": problem}}
        security = [] if public else [{"SessionCookie": [], **({"CsrfHeader": [], "CsrfCookie": []} if mutation else {})}]
        operation = {"parameters": parameters, "responses": responses, "security": security,
                     "x-handler": handler, "x-composition-condition": condition}
        if description:
            operation["description"] = description
        if request is not None:
            operation["requestBody"] = {"required": True, "content": {request_media: {"schema": request}}}
        spec["paths"].setdefault(path, {})[method] = operation
        checks.append((path, method, handler))
        return operation

    prefix = k04.PREFIX
    project = k04.PROJECT
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if "/jobs" in path:
                handler, condition = "hosted.job_http.JobApplication", "managed upload runtime; cancel/retry additionally need jobs.controls readiness"
            elif "/sources/" in path or path in ("/v1/workspaces", prefix, "/v1/invitations/accept") or any(
                    part in path for part in ("/members", "/invitations", "/transfer-owner", "/invitation-targets")):
                handler, condition = "hosted.workspace_http.WorkspaceApplication", "membership_management is not None"
            else:
                handler, condition = "hosted.catalog_http.CatalogApplication", "catalog_access is not None"
            operation.update({"x-handler": handler, "x-composition-condition": condition})
            checks.append((path, method, handler))
    # Keep inherited paging parameters unique when composing contract fragments.
    for method in ("get", "head"):
        parameters = spec["paths"][k04.RESULT + "/graph"][method]["parameters"]
        parameters.extend(item for item in [
            parameter("limit", {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}),
            parameter("cursor", {"type": "string", "minLength": 1, "maxLength": 4096})]
            if not any(old['name'] == item['name'] and old['in'] == item['in'] for old in parameters))
    export = spec["paths"][k04.RESULT + "/exports"]["post"]
    export["parameters"].append(parameter("Idempotency-Key", key, "header", False,
        "Optional actual durable-export key; the current response is 201 ExportDescriptor, not a 202 job receipt."))
    export["description"] = "Synchronous persisted export receipt; formats and expiry come from ExportDescriptor. This differs from the inventory's job_ack class."
    export['x-format-readiness'] = 'PDF requires supported_export_formats containing pdf; real renderer/font and migration readiness are checked. Rendering runs in a bounded child; the durable receipt is still synchronous 201.'
    download = spec['paths'][prefix + '/exports/{export_id}/download']['get']
    for status in ('200', '206'):
        if status in download['responses']:
            download['responses'][status]['content'] = {media: {'schema': {'type': 'string', 'format': 'binary'}}
                for media in ('application/json', 'text/markdown; charset=utf-8', 'application/pdf')}

    for method in ('get', 'head'):
        updates = add(prefix + '/jobs/{job_id}/updates', method, None, model(JobUpdate), '200',
            'hosted.job_http.JobApplication', 'verified jobs.polling_v1 adapter; coalesced status snapshots, not lossless event delivery')
        updates['parameters'].append(parameter('cursor', {'type': 'string', 'minLength': 1, 'maxLength': 4096},
            description='Signed actor/session/workspace/job continuation; invalid/expired returns400/410; gaps request resync.'))
        updates['responses']['200']['headers']['Retry-After'] = {'schema': {'type': 'integer', 'minimum': 1},
            'description': 'Present only for pending jobs; rounded-up poll_after_ms. Obey jitter and four-second private UI authority lease.'}
        updates['responses']['429'] = {'description': 'Bounded polling admission; keep cursor/key, apply jitter and Retry-After.'}
        add(project + '/deletion-status', method, None, model(DeletionStatus), '200',
            'hosted.catalog_http.CatalogApplication', 'verified projects.deletion_status; current project read authority even after logical deletion',
            description='Retained/purging/purged refers to managed project payloads. Workspace raw sources and operator backups have explicit separate retention policies.')
        add(prefix + '/jobs/{job_id}/attempts', method, None, model(JobAttemptPage), '200',
            'hosted.job_http.JobApplication', 'jobs.execution_v1; at most 3 fenced attempts, no lease credentials')
        add(k04.RESULT + '/header', method, None, model(ResultHeader), '200', 'hosted.catalog_http.CatalogApplication',
            'catalog.paged_read; bounded immutable header without loading the full view')
        evidence = add(k04.RESULT + '/evidence', method, None, model(EvidencePage), '200',
            'hosted.catalog_http.CatalogApplication', 'catalog.paged_read')
        evidence['parameters'].extend([
            parameter('limit', {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}),
            parameter('cursor', {'type': 'string', 'minLength': 1, 'maxLength': 4096})])
        for path in (k04.RESULT + '/claims', k04.RESULT + '/evidence'):
            parameters = spec['paths'][path][method]['parameters']
            if not any(item['name'] == 'search' and item['in'] == 'query' for item in parameters):
                parameters.append(parameter('search', {'type': 'string', 'minLength': 1, 'maxLength': 128},
                    description='Simple full-text query in the authorized immutable SQL projection; bound into the cursor.'))

    # These are the concrete branches validated by JobApplication._read_output.
    job_outputs = {
        "refresh": model(JobOutput[RefreshResult], "RefreshJobOutput"),
        "follow_up": model(FollowUpJobOutput),
        "analyze": model(JobOutput[AnyProductView], "AnalysisJobOutput"),
    }
    # The handler's discriminated parser requires the tag, despite model defaults.
    job_outputs["analyze"] = {"allOf": [job_outputs["analyze"], {
        "properties": {"result": {"required": ["schema_version"],
                                  "properties": {"demo_only": {"const": False}}}}}]}
    output = spec["paths"][prefix + "/jobs/{job_id}/output"]["get"]
    output["responses"]["200"]["content"]["application/json"]["schema"] = {
        "allOf": [{"$ref": "#/components/schemas/JobOutput"}, *(
            {"if": {"properties": {"operation": {"const": operation}}}, "then": shape}
            for operation, shape in job_outputs.items())]}
    output["description"] = (
        "Current job envelope with concrete refresh/follow-up summary and analysis v1/v2 results. "
        "Preview/confirm retain the generic result boundary of this handler. "
        "A follow-up summary requires a separate GET result to obtain ResultView.view.")

    for path, value in (("/v1/session", Session), (prefix + "/capabilities", Capabilities)):
        for method in ("get", "head"):
            add(path, method, None, model(value), "200", "hosted.application.HostedApplication")
    for path, state in (("/health/live", "alive"), ("/health/ready", "ready")):
        for method in ("get", "head"):
            add(path, method, None, {"type": "object", "properties": {"status": {"const": state}},
                "required": ["status"], "additionalProperties": False}, "200", "hosted.application.HostedApplication", public=True)

    logout_shape = {"type": "object", "properties": {"logout_url": {"type": ["string", "null"]}},
                    "required": ["logout_url"], "additionalProperties": False}
    op = add("/v1/auth/logout", "post", empty, logout_shape, "200", "hosted_auth.logout_http.LogoutApplication",
        description="Local context is revoked before success. With IdP handoff: 200 URL or null; without handoff: 204. A URL is not proof of completed IdP logout.")
    op["responses"]["204"] = {"description": "Local-only logout; empty body. Session and CSRF cookies cleared."}
    op["responses"]["200"]["headers"]["Set-Cookie"] = {"schema": {"type": "string"}, "description": "Two separate Set-Cookie fields clear session and CSRF cookies."}
    add("/v1/auth/renew", "post", session_empty, model(Session), "200", "hosted_auth.session_http.SessionMutationApplication",
        description="Rechecks the existing session; optional credentials/cookies may rotate. The creation hint currently defaults false; GET /v1/session refreshes current management policy.")
    add("/v1/auth/logout-all", "post", session_empty, None, "204", "hosted_auth.session_http.SessionMutationApplication",
        description="Revokes this actor's local sessions; not an IdP logout guarantee.")
    for path, handler in (("/v1/auth/start", "hosted_auth.start_http.LoginStartApplication"),
                          ("/v1/auth/callback", "hosted_auth.callback_http.CallbackApplication")):
        op = add(path, "get", None, None, "303", handler, public=True)
        op["responses"] = {str(code): {"description": "Fixed safe navigation response", **(
            {"headers": {"Location": {"schema": {"type": "string"}, "description": "Server-validated redirect; no untrusted open redirect."}}}
            if code == 303 else {"content": {"text/plain; charset=utf-8": {"schema": {"type": "string"}}}})}
            for code in ((303, 400, 405, 500, 503) if path.endswith("start") else (303, 400, 405))}
        if path.endswith("start"):
            op["parameters"] = [parameter("return_to", {"type": "string"}, description="Server allowlisted app path."),
                parameter("mode", {"type": "string", "enum": ["login", "switch"], "default": "login"},
                          description="Direct switch is rejected when IdP logout handoff is configured."),
                parameter("Sec-Fetch-Site", {"type": "string", "const": "same-origin"}, "header", True)]
        else:
            op["description"] = "OIDC callback validates one-use login transaction. Success redirects to allowed app path; handled failures redirect to /#/signed-out?reason=login_cancelled|login_failed|account_disabled|idp_unavailable. Provider query/cookies are never fixture examples."
            op["parameters"] = [parameter(name, {"type": "string"}) for name in ("code", "state", "iss", "error", "error_description", "error_uri")]

    add(prefix + "/sources", "post", {"type": "string", "format": "binary"}, model(SourceUploadReceipt), "201",
        "hosted.source_http.SourceUploadApplication", "legacy_sources only; disabled in resumable profile",
        request_media="application/zip", keyed=True, description="Exact immutable ZIP bytes, no multipart/path field; effective upload limit comes from capabilities.")
    upload_path = prefix + "/uploads/{upload_id}"
    staging = "resumable_uploads=True; verified upload and preview schemas; sources.resumable"
    add(prefix + "/uploads", "post", model(UploadManifest), model(UploadStatus), "201", "hosted.upload_staging_http.UploadStagingApplication", staging, keyed=True)
    for method in ("get", "head", "delete"):
        op = add(upload_path, method, None, model(UploadStatus), "200", "hosted.upload_staging_http.UploadStagingApplication", staging)
        if method == "delete":
            op["description"] = "Requires an empty body; preserves an already-ready source. This is not source deletion."
    op = add(upload_path + "/parts/{part_number}", "put", {"type": "string", "format": "binary"}, model(UploadStatus), "200",
        "hosted.upload_staging_http.UploadStagingApplication", staging, request_media="application/octet-stream")
    op["x-maximum-body-bytes"] = MAX_PART_BYTES
    op["parameters"] = [parameter("part_number", {"type": "integer", "minimum": 1, "maximum": 512}, "path", True)
                         if item["name"] == "part_number" else item for item in op["parameters"]]
    add(upload_path + "/complete", "post", upload_empty, model(UploadStatus), "200", "hosted.upload_staging_http.UploadStagingApplication", staging)
    add(prefix + "/imports/preview", "post", model(PreviewRequest), model(JobReceipt), "202", "hosted.imports_http.ImportApplication", staging, keyed=True)
    for method in ("get", "head"):
        add(prefix + "/imports/{preview_id}", method, None, model(PreviewImport), "200", "hosted.imports_http.ImportApplication", staging)
    add(prefix + "/imports/{preview_id}/confirm", "post", model(ConfirmImport), model(JobReceipt), "202", "hosted.imports_http.ImportApplication", staging, keyed=True)
    for suffix, request, handler, cas in (("/refresh", RefreshRequest, "project_refresh_http.ProjectRefreshApplication", True),
                                        ("/analyses", AnalysisRequest, "project_analysis_http.ProjectAnalysisApplication", False)):
        add(project + suffix, "post", model(request), model(JobReceipt), "202", "hosted." + handler,
            "follow_up_available=True; canonical project job extensions ready", cas=cas, keyed=True)
    add(k04.RESULT + "/follow-ups", "post", model(FollowUpRequest), model(JobReceipt), "202",
        "hosted.project_http.ProjectOperationsApplication", "follow_up_available=True; canonical result parent required", cas=True, keyed=True)
    for method in ("get", "head"):
        old = copy.deepcopy(spec["paths"][project][method])
        op = add(project, method, None, model(ProjectDetails), "200", "hosted.runtime_management_http.RuntimeManagementApplication",
                 "runtime_management is not None; otherwise CatalogApplication's CanonicalRecord response")
        op["x-fallback-response"] = old["responses"]["200"]
        if method != "head":
            op["responses"]["200"]["content"]["application/json"]["schema"] = {"oneOf": [model(ProjectDetails), {"$ref": "#/components/schemas/CanonicalRecord"}]}
        # The fallback CatalogApplication accepts the general ID grammar.
        for item in op["parameters"]:
            if item["name"] == "project_id":
                item["schema"] = id_shape
        add(prefix + "/settings", method, None, model(WorkspaceSettings), "200", "hosted.runtime_management_http.RuntimeManagementApplication", "runtime_management is not None")
    add(project, "patch", model(ChangeProject), model(ProjectDetails), "200", "hosted.runtime_management_http.RuntimeManagementApplication", "runtime_management is not None", cas=True)
    add(prefix + "/settings", "patch", model(ChangeWorkspaceSettings), model(WorkspaceSettings), "200", "hosted.runtime_management_http.RuntimeManagementApplication", "runtime_management is not None", cas=True)

    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            op["operationId"] = method + "_" + re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
            op["x-acceptance"] = "not_run_by_offline_export"
    synthetic = [(ChangeProject, {"schema_version": k04.VERSION, "display_name": "Synthetic project"}),
                 (ChangeWorkspaceSettings, {"schema_version": k04.VERSION, "maximum_upload_bytes": 1024}),
                 (RefreshRequest, {"schema_version": k04.VERSION, "source_id": "src_synthetic"}),
                 (AnalysisRequest, {"schema_version": k04.VERSION, "snapshot_id": "upload-snapshot-" + "a" * 64,
                                    "question": "What is observed?"})]
    envelope = {"schema_version": k04.VERSION, "workspace_id": "ws_synthetic",
                "job_id": "job_synthetic"}
    synthetic.extend([
        (JobOutput[RefreshResult], {**envelope, "operation": "refresh", "result": {
            "project_id": "p3-project-" + "a" * 64, "previous_snapshot_id": "upload-snapshot-" + "b" * 64,
            "snapshot_id": "upload-snapshot-" + "c" * 64, "revision": 2, "status": "new_snapshot"}}),
        (FollowUpJobOutput, {**envelope, "operation": "follow_up", "result": {
            "project_id": "p3-project-" + "a" * 64, "parent_result_id": "p6-result-" + "b" * 64,
            "result_id": "p6-result-" + "c" * 64, "snapshot_id": "upload-snapshot-" + "d" * 64,
            "revision": 2}}),
    ])
    for _, (kind, value) in k04.examples()[0].items():
        if isinstance(kind, type) and issubclass(kind, BaseModel):
            model(kind)
            synthetic.append((kind, value))
    return spec, checked_models, synthetic, checks


def disposition(spec: dict, inventory: Path) -> list[dict]:
    rows = list(csv.DictReader(inventory.open(encoding="utf-8-sig", newline="")))
    assert [row["id"] for row in rows] == [f"P{index:02d}" for index in range(1, 39)]
    output = []
    for row in rows:
        number = row["id"]
        path = row["path"].replace("{member_id}", "{user_id}")
        if number == "P02":
            path = "/v1/auth/logout"
        method = row["method"].lower()
        op = spec["paths"].get(path, {}).get(method)
        unsupported = number in ("P32", "P33", "P34")
        assert unsupported or op is not None, f"Missing core inventory route: {number}"
        delta = {"P02": "Actual logout path is /v1/auth/logout, not the inventory alias /v1/session/logout.",
                 "P05": "Current collection supports literal ID search and keyset limit/cursor, not arbitrary field sorting or full-text search.",
                 "P09": "Accepted receipt resolves to confirm result {snapshot_id}; project identity is not added to that result payload.",
                 "P13": "Current backend is local_evidence only; the model accepts p3/upload snapshot IDs, but actual admission requires a canonical UploadSnapshot belonging to this project. No external provider or general research-snapshot analysis is claimed.",
                 "P21": "Managed execution_v1 cancels queued jobs immediately; running jobs become cancel_requested until compute is reaped and fenced acknowledgement commits. A committed success wins. Legacy experimental callers retain their explicit old profile.",
                 "P22": "Chosen alternative is bounded paged JSON polling, not SSE; equivalent consumer acceptance remains separate.",
                 "P23": "Actual 201 durable ExportDescriptor is synchronous, not the inventory's job_ack response class.",
                 "P25": "Actual 200 logical tombstone/CAS response; not a 202 deletion job or physical purge.",
                 "P28": "Actual actor identifier is user_id; no client-declared role/actor authority.",
                 "P37": "Current settings expose bounded upload/workspace limits and fixed retained_no_purge_schedule, not configurable retention.",
                 "P38": "Current mutation changes upload/workspace limits only; no unlimited quota or retention schedule mutation."}.get(number, "")
        output.append({"id": number, "required_method": row["method"], "required_path": row["path"],
            "requirement_mode": row["requirement_mode"], "capability": row["capability"],
            "actual_method": None if unsupported else row["method"], "actual_path": None if unsupported else path,
            "disposition": "conditional_choice_disabled" if unsupported else "handler_present_acceptance_open",
            "handler": None if unsupported else op["x-handler"],
            "composition_condition": "sources.connector=false; no connector handler is claimed" if unsupported else op["x-composition-condition"],
            "success_statuses": [] if unsupported else [code for code in op["responses"] if code.startswith("2")],
            "requirement_delta": delta, "correctness_evidence": "offline route/schema check only", "latency_evidence": "not_run"})
    return output


def self_check(spec: dict, models: dict, synthetic: list, checks: list) -> dict:
    from export_k07_contract import build as k07_examples
    from jsonschema import Draft202012Validator, FormatChecker
    from pydantic import ValidationError

    from aletheia_lab.hosted_contract.job_page import JobOutput
    from aletheia_lab.hosted_contract.runtime_management import ChangeWorkspaceSettings
    schemas = spec["components"]["schemas"]
    assert "date-time" in FormatChecker().checkers, "Existing jsonschema[format] dependency is required"
    refs = 0

    def walk(value: object) -> None:
        nonlocal refs
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                assert ref.startswith("#/"), f"Unexpected external schema dependency: {ref}"
                resolved = spec
                for part in ref[2:].split("/"):
                    resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
                refs += 1
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(spec)
    for shape in schemas.values():
        Draft202012Validator.check_schema(shape)
    operations = [op for path in spec["paths"].values() for op in path.values()]
    assert len({op["operationId"] for op in operations}) == len(operations)
    for path, items in spec["paths"].items():
        for method, op in items.items():
            parameters = op["parameters"]
            assert set(re.findall(r"{([^}]+)}", path)) == {item["name"] for item in parameters if item["in"] == "path"}
            assert len({(item["in"], item["name"]) for item in parameters}) == len(parameters)
            assert all(item["required"] for item in parameters if item["in"] == "path")
            for security in op["security"]:
                assert set(security) <= set(spec["components"]["securitySchemes"])
            if method == "head":
                assert all("content" not in response for response in op["responses"].values())
    # Current handler matchers, not copied requirement paths, witness route presence.
    samples = {"workspace_id": "ws_synthetic", "user_id": "user_synthetic", "project_id": "p3-project-" + "a" * 64,
        "result_id": "p6-result-" + "b" * 64, "snapshot_id": "upload-snapshot-" + "c" * 64,
        "preview_id": "prev_" + "d" * 32, "part_number": "1", "export_id": "export_synthetic"}
    unique = {(path, method): handler for path, method, handler in checks}
    for (path, method), handler in unique.items():
        sample = re.sub(r"{([^}]+)}", lambda match: samples.get(match[1], "id_synthetic"), path)
        module_name, class_name = handler.rsplit(".", 1)
        module = importlib.import_module("aletheia_lab." + module_name)
        patterns = [value for value in vars(module).values() if isinstance(value, re.Pattern)]
        if patterns:
            assert any(pattern.fullmatch(sample) for pattern in patterns) or path in ("/v1/workspaces", "/v1/invitations/accept", "/v1/session", "/health/live", "/health/ready"), (path, handler)
        else:
            assert path in Path(module.__file__).read_text(encoding="utf-8"), (path, handler)
        allow = getattr(getattr(module, class_name), "_allow", None)
        if allow is not None:
            if class_name == "UploadStagingApplication":
                allowed = allow(module._ROUTE.fullmatch(sample))
            elif class_name == "RuntimeManagementApplication":
                allowed = allow({"path": sample})
            else:
                allowed = allow(sample)
            assert method.upper().encode() in allowed.split(b", "), (path, method, allowed)
    for kind, value in synthetic:
        parsed = kind.model_validate_json(json.dumps(value, ensure_ascii=True, allow_nan=False))
        alias = next(name for name, model in models.items() if model is kind)
        Draft202012Validator({"$ref": "#/components/schemas/" + alias, "components": spec["components"]},
            format_checker=FormatChecker()).validate(parsed.model_dump(mode="json"))
        if issubclass(kind, JobOutput):
            shape = spec["paths"]["/v1/workspaces/{workspace_id}/jobs/{job_id}/output"]["get"][
                "responses"]["200"]["content"]["application/json"]["schema"]
            Draft202012Validator({**shape, "components": spec["components"]},
                format_checker=FormatChecker()).validate(parsed.model_dump(mode="json"))
    for bad in ({"schema_version": "product-api/v1-draft"}, {"schema_version": "product-api/v1-draft", "maximum_upload_bytes": None}):
        try:
            ChangeWorkspaceSettings.model_validate_json(json.dumps(bad))
        except ValidationError:
            pass
        else:
            raise AssertionError("Empty/null policy mutation must fail its current producer validator")
    # Existing exporter already validates actual upload/import models, states and safe Problems.
    upload_examples = k07_examples()
    return {"operations": len(operations), "paths": len(spec["paths"]), "schemas": len(schemas), "resolved_local_refs": refs,
            "handler_method_checks": len(unique), "synthetic_existing_model_payloads": len(synthetic),
            "reused_k07_synthetic_outputs": len(upload_examples), "negative_policy_cases": 2}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before = sources(args.repo)
    spec, models, synthetic, checks = document(args.repo)
    rows = disposition(spec, args.inventory)
    result = self_check(spec, models, synthetic, checks)
    after = sources(args.repo)
    changes = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    if changes:
        raise RuntimeError("Producer source changed during export; retry on a frozen snapshot.")
    spec["x-requirement-disposition"] = rows
    args.output.mkdir(parents=True, exist_ok=False)
    outputs = {"openapi.json": spec, "operation-disposition.json": rows, "source-before.json": before, "source-after.json": after,
        "verification.json": {"scope": "Offline producer schema/route checks only; no DB, service, browser, latency or joint acceptance", **result,
            "source_manifest_scope": "Python/SQL/JSON under hosted, hosted_auth, hosted_contract, product, project; current K04 seed JSON and maintained exporter scripts",
            "inventory_sha256": digest(args.inventory), "generator_sha256": digest(Path(__file__)),
            "generated_utc": datetime.now(UTC).isoformat(), "source_stable": not changes, "changed_source_paths": changes}}
    sums = []
    for name, value in outputs.items():
        path = args.output / name
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        sums.append(f"{digest(path)}  {name}\n")
    (args.output / "SHA256SUMS").write_text("".join(sums), encoding="ascii")
    print(json.dumps({**result, "source_stable": not changes, "changed_source_paths": changes}))


if __name__ == "__main__":
    main()

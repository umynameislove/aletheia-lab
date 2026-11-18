"""Export the K-04 extension from maintained models into a new review directory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, TypeAdapter

from aletheia_lab.hosted.job_http import EmptyAction, Submission
from aletheia_lab.hosted.legacy_import import model_metadata
from aletheia_lab.hosted.native_upload_records import UploadResultRecord
from aletheia_lab.hosted.result_views import prepare_projection
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot
from aletheia_lab.hosted_contract import job_page
from aletheia_lab.hosted_contract import workspaces as wire
from aletheia_lab.hosted_contract.job_page import Job, JobOutput, JobReceipt
from aletheia_lab.hosted_contract.session_capabilities import Identifier, Problem
from aletheia_lab.product.lifecycle import ProductLifecycleRecord, build_product_lifecycle_record
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.results import _view_from_record
from aletheia_lab.project.regression import ProjectRegressionEvent
from aletheia_lab.project.snapshots import ProjectSnapshot

VERSION = "product-api/v1-draft"
PREFIX = "/v1/workspaces/{workspace_id}"
PROJECT = PREFIX + "/projects/{project_id}"
RESULT = PREFIX + "/results/{result_id}"
SEED = Path(__file__).resolve().parents[1] / "fixtures/k04/canonical-seed-v1.json"
ID_SCHEMA = TypeAdapter(Identifier).json_schema()
OPERATIONS = [
    ("/v1/workspaces", "get", None, wire.WorkspacePage, "200", False),
    ("/v1/workspaces", "post", wire.CreateWorkspace, wire.Workspace, "201", False),
    (PREFIX, "get", None, wire.Workspace, "200", False),
    (PREFIX + "/members", "get", None, wire.MemberPage, "200", False),
    (PREFIX + "/members/{user_id}", "patch", wire.ChangeMember, wire.Member, "200", True),
    (PREFIX + "/transfer-owner", "post", wire.TransferOwner, wire.Member, "200", True),
    (PREFIX + "/invitations", "get", None, wire.InvitationPage, "200", False),
    (PREFIX + "/invitations", "post", wire.CreateInvitation, wire.Invitation, "201", False),
    (PREFIX + "/invitations/{invitation_id}", "delete", wire.Empty, wire.RevokedInvitation, "200", False),
    ("/v1/invitations/accept", "post", wire.AcceptInvitation, wire.Workspace, "200", False),
    (PREFIX + "/invitation-targets", "post", wire.LookupInvitationTargets, wire.InvitationTargetPage, "200", False),
    (PREFIX + "/sources/{source_id}/grants/{user_id}", "get", None, wire.SourceGrant, "200", False),
    (PREFIX + "/sources/{source_id}/grants/{user_id}", "put", wire.ChangeGrant, wire.SourceGrant, "200", True),
    (PREFIX + "/projects", "get", None, wire.ProjectPage, "200", False),
    (PROJECT, "get", None, wire.CanonicalRecord, "200", False),
    (PROJECT, "delete", wire.Empty, wire.DeletedProject, "200", True),
    (PROJECT + "/snapshots", "get", None, wire.RecordPage, "200", False),
    (PROJECT + "/snapshots/{snapshot_id}", "get", None, wire.CanonicalRecord, "200", False),
    (PROJECT + "/records", "get", None, wire.RecordPage, "200", False),
    (PROJECT + "/records/{record_id}", "get", None, wire.CanonicalRecord, "200", False),
    (PROJECT + "/grants/{user_id}", "get", None, wire.ProjectGrant, "200", False),
    (PROJECT + "/grants/{user_id}", "put", wire.ChangeGrant, wire.ProjectGrant, "200", True),
    (PREFIX + "/results", "get", None, wire.RecordPage, "200", False),
    (RESULT, "get", None, wire.ResultView, "200", False),
    (RESULT + "/header", "get", None, wire.ResultHeader, "200", False),
    (RESULT + "/claims", "get", None, wire.ClaimPage, "200", False),
    (RESULT + "/evidence", "get", None, wire.EvidencePage, "200", False),
    (RESULT + "/evidence/{evidence_id}", "get", None, wire.EvidenceView, "200", False),
    (RESULT + "/graph", "get", None, wire.GraphView, "200", False),
    (PREFIX + "/events", "get", None, wire.RecordPage, "200", False),
    (RESULT + "/annotations/{annotation_id}", "get", None, wire.AnnotationPage, "200", False),
    (RESULT + "/annotations/{annotation_id}", "put", wire.PutAnnotation, wire.Annotation, "200", True),
    (RESULT + "/annotations/{annotation_id}/history", "get", None, wire.AnnotationPage, "200", False),
    (RESULT + "/exports", "post", wire.ExportRequest, wire.ExportDescriptor, "201", False),
    (PREFIX + "/exports/{export_id}/download", "get", None, "ExportBytes", "200", False),
    (PREFIX + "/jobs", "post", Submission, JobReceipt, "202", False),
    (PREFIX + "/jobs/{job_id}", "get", None, Job, "200", False),
    (PREFIX + "/jobs/{job_id}/output", "get", None, "JobOutput", "200", False),
    (PREFIX + "/jobs/{job_id}/cancel", "post", EmptyAction, Job, "200", False),
    (PREFIX + "/jobs/{job_id}/retry", "post", EmptyAction, JobReceipt, "202", False),
]
LOCAL_ERRORS = {"invitation_expired": 410, "invitation_unavailable": 404, "invitation_wrong_account": 403,
                "cursor_invalid": 400, "cursor_expired": 410, "job_state_conflict": 409,
                "idempotency_conflict": 409, "revision_conflict": 412, "precondition_required": 428,
                "response_too_large": 413}


def _ref(model: type[BaseModel] | str) -> dict[str, object]:
    if model is wire.ResultView:
        return {"oneOf": [{"$ref": "#/components/schemas/ResultView"},
                          {"$ref": "#/components/schemas/ResultViewV2"}]}
    return {"$ref": "#/components/schemas/" + (model if isinstance(model, str) else model.__name__)}


def _parameter(name: str, schema: dict[str, object], *, required: bool = False,
               location: str = "query", description: str = "") -> dict[str, object]:
    return {"in": location, "name": name, "required": required, "schema": schema, "description": description}


def _queries(path: str, model: type[BaseModel] | str) -> list[dict[str, object]]:
    page = model in (wire.WorkspacePage, wire.MemberPage, wire.InvitationPage, wire.ProjectPage,
                     wire.RecordPage, wire.ClaimPage, wire.EvidencePage, wire.GraphView, wire.AnnotationPage)
    result = []
    if page:
        maximum = 200 if model in (wire.WorkspacePage, wire.MemberPage, wire.InvitationPage) else 100
        result.extend([_parameter("limit", {"type": "integer", "minimum": 1, "maximum": maximum, "default": 50}),
            _parameter("cursor", {"type": "string", "minLength": 1, "maxLength": 4096},
                description="Opaque signed continuation bound to the current actor, session, authorization revision and query.")])
    if model in (wire.ProjectPage, wire.RecordPage):
        result.append(_parameter("search", {"type": "string", "minLength": 1, "maxLength": 128},
                                  description="Literal identifier search; SQL wildcard characters have no wildcard meaning."))
    if model in (wire.ClaimPage, wire.EvidencePage):
        result.append(_parameter('search', {'type': 'string', 'minLength': 1, 'maxLength': 128},
            description='PostgreSQL simple word search, not substring/semantic search. Cursor binds the current query and projection version.'))
    if path == PROJECT + "/records":
        result.append(_parameter("kind", {"type": "string", "enum": list(get_args(wire.CanonicalKind))}, required=True))
    if path in (PROJECT + "/snapshots", PROJECT + "/records", PREFIX + "/results", PREFIX + "/events"):
        result.append(_parameter("snapshot_id", ID_SCHEMA, description="Requires project_id on workspace-wide result/event collections."))
    if path in (PREFIX + "/results", PREFIX + "/events"):
        result.append(_parameter("project_id", ID_SCHEMA, description="Optional project filter; omitted means the authorized workspace collection."))
    if path.endswith("/evidence/{evidence_id}"):
        result.append(_parameter("claim_id", ID_SCHEMA, description="When supplied, the server verifies the claim-to-evidence parent relation."))
    return result


def document() -> dict[str, object]:
    components, paths = {}, {}
    models = {model for operation in OPERATIONS for model in operation[2:4]
              if isinstance(model, type) and issubclass(model, BaseModel)} | {Problem, wire.ResultViewV2}
    for model in sorted(models, key=lambda item: item.__name__):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, definition in schema.pop("$defs", {}).items():
            if name in components and components[name] != definition:
                raise ValueError("Conflicting producer definition")
            components[name] = definition
        if model.__name__ in components and components[model.__name__] != schema:
            raise ValueError("Conflicting producer model")
        components[model.__name__] = schema
    components["JobOutput"] = JobOutput[dict[str, object]].model_json_schema(
        ref_template="#/components/schemas/{model}")
    for path, method, request, response, status, revision in OPERATIONS:
        for verb in ((method, "head") if method == "get" else (method,)):
            parameters = [_parameter(part[1:-1], ID_SCHEMA, required=True, location="path")
                          for part in path.split("/") if part.startswith("{")]
            if verb in ("get", "head"):
                parameters.extend(_queries(path, response))
            if revision:
                parameters.append(_parameter("If-Match", {"type": "string", "pattern": '^"(0|[1-9][0-9]{0,18})"$'},
                    required=True, location="header", description="Current quoted revision, at most 9223372036854775806."))
            if verb == "post" and (path.endswith("/jobs") or path.endswith("/retry")):
                parameters.append(_parameter("Idempotency-Key", {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,128}$"},
                    required=True, location="header", description="Reuse only for the identical operation; retry identity includes its original job."))
            mutation = verb not in ("get", "head")
            if mutation:
                parameters.append(_parameter("Origin", {"type": "string", "format": "uri"}, required=True,
                    location="header", description="Must match the configured application origin."))
            success = {"description": "Committed scoped response", "headers": {
                "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                "Content-Length": {"schema": {"type": "integer", "minimum": 0}}}}
            if isinstance(response, type) and "revision" in response.model_fields:
                success["headers"]["ETag"] = {"schema": {"type": "string", "pattern": '^"[0-9]+"$'},
                                                "description": "Quoted current resource revision."}
            if response == "ExportBytes":
                parameters.append(_parameter("Range", {"type": "string", "pattern": "^bytes=(?:[0-9]{1,20}-[0-9]{0,20}|-[0-9]{1,20})$"},
                    location="header", description="One byte range only; authorization precedes range validation and all size headers."))
                success["headers"].update({"Accept-Ranges": {"schema": {"const": "bytes"}},
                    "ETag": {"schema": {"type": "string", "pattern": '^"[0-9a-f]{64}"$'}},
                    "Content-Disposition": {"schema": {"type": "string"}, "description": "Attachment filename derives from the verified byte digest."}})
                if verb != "head":
                    success["content"] = {media: {"schema": {"type": "string", "format": "binary"}}
                                          for media in ("application/json", "text/markdown", "application/pdf")}
            elif verb != "head":
                success["content"] = {"application/json": {"schema": _ref(response)}}
            responses = {status: success}
            for code in (400, 401, 403, 404, 405, 409, 410, 412, 413, 415, 422, 428, 500, 503):
                error = {"description": "Safe public Problem; no outside-scope metadata."}
                if verb != "head":
                    error["content"] = {"application/problem+json": {"schema": _ref(Problem)}}
                responses[str(code)] = error
            if response == "ExportBytes":
                partial = json.loads(json.dumps(success))
                partial["description"] = "Authorized partial byte response; HEAD returns the same headers without bytes."
                partial["headers"]["Content-Range"] = {"schema": {"type": "string"}, "required": True}
                responses["206"] = partial
                responses["416"] = {"description": "Authorized object range is unavailable.",
                    "headers": {"Content-Range": {"schema": {"type": "string"}, "required": True}}}
                if verb != "head":
                    responses["416"]["content"] = {"application/problem+json": {"schema": _ref(Problem)}}
            operation = {"security": [{"SessionCookie": [], **({"CsrfHeader": [], "CsrfCookie": []} if mutation else {})}],
                         "parameters": parameters, "responses": responses}
            if request is not None:
                operation["requestBody"] = {"required": verb != "delete", "content": {"application/json": {"schema": _ref(request)}}}
            if verb == "delete":
                operation["description"] = "Empty body without Content-Type or application/json {} is supported; existing session, Origin and bound CSRF checks remain mandatory."
            if response is wire.DeletedProject:
                operation["description"] += " Logical tombstone only: canonical history, object references and job receipts are retained; no physical purge."
            if path in (PREFIX + "/results", PREFIX + "/events"):
                operation["x-query-constraints"] = ["snapshot_id requires project_id"]
            if path == PREFIX + "/events":
                operation["description"] = "Finite paged JSON collection; this endpoint is not SSE."
            paths.setdefault(path, {})[verb] = operation
    return {"openapi": "3.1.0", "info": {"title": "Aletheia K04 hosted producer extension", "version": VERSION,
        "description": "Additive maintained producer schemas and synthetic wire examples; runtime qualification is recorded separately and no joint acceptance is implied. Server revalidates current session, membership, scope and grants on every request. Mutation authority never comes from browser role or user fields."},
        "x-extension-revision": "k04-k08-k12-contract-20261112", "paths": paths, "components": {"schemas": components,
        "securitySchemes": {"SessionCookie": {"type": "apiKey", "in": "cookie", "name": "__Host-aletheia_session"},
            "CsrfHeader": {"type": "apiKey", "in": "header", "name": "x-csrf-token"},
            "CsrfCookie": {"type": "apiKey", "in": "cookie", "name": "__Host-aletheia_csrf"}}}}


def examples() -> tuple[dict[str, tuple[type[BaseModel] | str, object]], dict[str, bytes], dict[str, object]]:
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    record = ProductLifecycleRecord.model_validate(seed["result_record"])
    snapshot = ProjectSnapshot.model_validate_json(json.dumps(seed["snapshot_record"]))
    view = _view_from_record(record)
    if view.model_dump(mode="json") != seed["view"] or (record.project_id, record.snapshot_id) != (snapshot.project_id, snapshot.snapshot_id):
        raise ValueError("Frozen canonical fixture identities changed")
    value = view.model_dump(mode="json")
    ws, project, result = "ws_synthetic", view.project.id, view.result.id
    page = {"schema_version": VERSION, "workspace_id": ws, "next_cursor": None, "total": None, "total_kind": "unknown"}
    workspace = {"schema_version": VERSION, "workspace_id": ws, "workspace_name": "Workspace tổng hợp",
                 "membership": {"role": "owner", "revision": 1}}
    member = {"schema_version": VERSION, "workspace_id": ws, "user_id": "user_synthetic", "display_name": "Synthetic analyst",
              "role": "analyst", "active": True, "revision": 2}
    owner = {**member, "user_id": "user_owner", "display_name": "Synthetic owner", "role": "owner", "revision": 1}
    invitation = {"schema_version": VERSION, "workspace_id": ws, "invitation_id": "inv_synthetic", "user_id": "user_invited",
                  "role": "viewer", "expires_at": "2026-11-11T00:00:00Z", "token": "synthetic_invitation_" + "x" * 23}
    pending = {key: item for key, item in invitation.items() if key != "token"}
    pending["display_name"] = "Invited synthetic user"
    annotation = {"schema_version": VERSION, "workspace_id": ws, "project_id": project, "result_id": result,
        "annotation_id": "annotation_synthetic", "revision": 1, "author_id": "user_synthetic", "body": "Observed evidence needs human review.",
        "status": "comment", "created_at": "2026-11-10T00:00:00Z"}
    summary = {"record_id": record.record_id, "project_id": project, "record_type": "result", "schema_version": record.schema_version,
               "canonical_sha256": record.canonical_sha256(), "snapshot_id": record.snapshot_id, "parent_result_id": record.parent_result_id}
    events = [ProjectRegressionEvent.model_validate_json(json.dumps(item)) for item in seed["events"]]
    event_rows = [{"record_id": item.event_id, "project_id": item.project_id, "record_type": "regression_event",
        "schema_version": item.schema_version, "canonical_sha256": model_metadata(item)[4],
        "snapshot_id": None, "parent_result_id": None} for item in events]
    evidence = value["evidence"][0]
    claim = next((item for item in value["claims"] if evidence["id"] in (*item["citation_ids"], *item["counterevidence_ids"])), None)
    relation = None if claim is None else "citation" if evidence["id"] in claim["citation_ids"] else "counterevidence"
    projection = prepare_projection(view, record.canonical_sha256())
    header = {**json.loads(projection.header), 'schema_version': VERSION, 'workspace_id': ws,
        'project_id': project, 'result_id': result, 'snapshot_id': view.snapshot.id,
        'claim_count': len(view.claims), 'evidence_count': len(view.evidence),
        'node_count': len(view.graph.nodes), 'edge_count': len(view.graph.edges)}
    fixtures = {
        "workspace": (wire.Workspace, workspace), "workspaces": (wire.WorkspacePage, {"schema_version": VERSION, "items": [workspace],
            "next_cursor": None, "total": None, "total_kind": "unknown"}),
        "member": (wire.Member, member), "members": (wire.MemberPage, {**page, "items": [owner, member]}),
        "invitation": (wire.Invitation, invitation), "invitations": (wire.InvitationPage, {**page, "items": [pending]}),
        "invitation-targets": (wire.InvitationTargetPage, {**page, "items": [{"user_id": "user_invited", "display_name": "Invited synthetic user"}]}),
        "grant": (wire.SourceGrant, {"schema_version": VERSION, "workspace_id": ws, "source_id": "src_synthetic", "user_id": member["user_id"],
            "actions": ["read", "write"], "revision": 1}),
        "project-grant": (wire.ProjectGrant, {"schema_version": VERSION, "workspace_id": ws, "project_id": project,
            "user_id": member["user_id"], "actions": ["read", "export"], "revision": 1}),
        "projects": (wire.ProjectPage, {**page, "items": [{"project_id": project, "revision": 1}]}),
        "records": (wire.RecordPage, {**page, "project_id": project, "items": [summary]}),
        "results-workspace": (wire.RecordPage, {**page, "project_id": None, "items": [summary]}),
        "events-workspace": (wire.RecordPage, {**page, "project_id": None, "items": event_rows}),
        "canonical-record": (wire.CanonicalRecord, {"schema_version": VERSION, "workspace_id": ws, "project_id": project,
            "record_id": record.record_id, "record_kind": "result", "data": record.model_dump(mode="json")}),
        "snapshot-record": (wire.CanonicalRecord, {"schema_version": VERSION, "workspace_id": ws, "project_id": project,
            "record_id": snapshot.snapshot_id, "record_kind": "snapshot", "data": snapshot.model_dump(mode="json")}),
        "result-view": (wire.ResultView, {"schema_version": VERSION, "workspace_id": ws, "project_id": project, "result_id": result, "view": value}),
        'result-header': (wire.ResultHeader, header),
        "claims": (wire.ClaimPage, {**page, "project_id": project, "result_id": result, "snapshot_id": view.snapshot.id,
            "items": value["claims"], 'total': len(view.claims), 'total_kind': 'exact'}),
        'evidence-page': (wire.EvidencePage, {**page, 'project_id': project, 'result_id': result,
            'snapshot_id': view.snapshot.id, 'items': value['evidence'], 'total': len(view.evidence), 'total_kind': 'exact'}),
        "evidence": (wire.EvidenceView, {"schema_version": VERSION, "workspace_id": ws, "project_id": project, "result_id": result,
            "evidence_id": evidence["id"], "claim_id": None if claim is None else claim["id"], "relation": relation, "evidence": evidence}),
        "graph": (wire.GraphView, {"schema_version": VERSION, "workspace_id": ws, "project_id": project, "result_id": result,
            "graph": value["graph"], "truncated": False}),
        "annotation": (wire.Annotation, annotation), "annotation-history": (wire.AnnotationPage, {**page, "project_id": project,
            "result_id": result, "annotation_id": annotation["annotation_id"], "items": [annotation]}),
    }
    receipt = {"schema_version": VERSION, "workspace_id": ws, "job_id": "job_synthetic", "status": "queued", "request_id": "req_synthetic",
               "status_url": f"/v1/workspaces/{ws}/jobs/job_synthetic"}
    fixtures.update({"job-receipt": (JobReceipt, receipt), "job-cancelled": (Job, {"schema_version": VERSION, "workspace_id": ws,
        "job_id": receipt["job_id"], "status": "cancelled", "progress": None}), "retry-receipt": (JobReceipt, {**receipt,
        "job_id": "job_retry_synthetic", "request_id": "req_retry_synthetic", "status_url": f"/v1/workspaces/{ws}/jobs/job_retry_synthetic"}),
        "job-output": ("JobOutput", {"schema_version": VERSION, "workspace_id": ws, "job_id": "job_analyze_synthetic",
            "operation": "analyze", "result": value})})
    binaries = {}
    for report_format in ("json", "markdown", "pdf"):
        payload = render_product_report(view, report_format)
        export_id = f"ex_{report_format}_{result}"
        fixtures["export-" + report_format] = (wire.ExportDescriptor, {"schema_version": VERSION, "workspace_id": ws,
            "project_id": project, "result_id": result, "export_id": export_id, "format": report_format, "byte_size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "download_url": f"/v1/workspaces/{ws}/exports/{export_id}/download"})
        durable_id = export_id + "_" + "0123456789abcdef" * 2
        fixtures["export-" + report_format + "-durable-synthetic"] = (wire.ExportDescriptor, {
            **fixtures["export-" + report_format][1], "export_id": durable_id,
            "download_url": f"/v1/workspaces/{ws}/exports/{durable_id}/download",
            "expires_at": "2026-11-11T12:00:00Z"})
        binaries["download-" + report_format + ".bin"] = payload
    tombstone = build_product_lifecycle_record(record_kind="deletion", project_id=project, payload={
        "schema_version": "p6-project-deletion/v1", "status": "pending", "deleted_count": 0,
        "retained_shared_count": 0, "purge_object_sha256s": []})
    fixtures["deleted-project"] = (wire.DeletedProject, {"schema_version": VERSION, "workspace_id": ws, "project_id": project,
        "revision": 2, "tombstone_id": tombstone.record_id, "status": "tombstoned", "retention": "retained"})
    for code, status in LOCAL_ERRORS.items():
        fixtures["problem-" + code.replace("_", "-")] = (Problem, {"schema_version": VERSION, "type": "urn:aletheia:problem:" + code,
            "title": "Request could not be completed", "status": status, "code": code,
            "detail": "The request could not be completed.", "request_id": "req_synthetic"})
    native_seed = json.loads(SEED.with_name("native-seed-v1.json").read_text(encoding="utf-8"))
    native_snapshot = UploadSnapshot.model_validate_json(json.dumps(native_seed["snapshot"]))
    native_result = UploadResultRecord.model_validate_json(json.dumps(native_seed["result"]))
    if (native_snapshot.workspace_id, native_snapshot.mapping.project_id, native_snapshot.snapshot_id) != (
            native_result.workspace_id, native_result.project_id, native_result.snapshot_id):
        raise ValueError("Frozen native fixture scope changed")
    for name, kind, identity, data in (
            ("native-snapshot-record", "upload_snapshot", native_snapshot.snapshot_id, native_snapshot.model_dump(mode="json")),
            ("native-result-record", "upload_result", native_result.record_id, native_result.model_dump(mode="json"))):
        fixtures[name] = (wire.CanonicalRecord, {"schema_version": VERSION,
            "workspace_id": native_result.workspace_id, "project_id": native_result.project_id,
            "record_id": identity, "record_kind": kind, "data": data})
    fixtures["native-result-view"] = (wire.ResultView, {"schema_version": VERSION,
        "workspace_id": native_result.workspace_id, "project_id": native_result.project_id,
        "result_id": native_result.record_id, "view": native_result.view.model_dump(mode="json")})
    return fixtures, binaries, {"project_id": project, "snapshot_id": view.snapshot.id, "result_id": result}


def export(destination: Path) -> None:
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("A new or empty export destination is required.")
    api = document()
    fixtures, binaries, identities = examples()
    outputs = {"openapi-workspaces.json": api}
    mapping = {}
    for name, (model, value) in fixtures.items():
        outputs[name + ".json"] = value if isinstance(model, str) else model.model_validate_json(json.dumps(value)).model_dump(mode="json")
        mapping[name + ".json"] = model if isinstance(model, str) else model.__name__
    outputs["inventory.json"] = {"fixture_only": True, "schema_version": VERSION, "payload_models": mapping,
        "canonical_identities": identities, "canonical_seed_sha256": hashlib.sha256(SEED.read_bytes()).hexdigest(),
        "producer_sources": {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                             for module in (wire, job_page)},
        "local_error_codes": LOCAL_ERRORS, "formats": ["json", "markdown", 'pdf'], "unsupported_formats": [],
        'pdf_scope': 'Deterministic producer bytes with the installed ReportLab and packaged font; runtime capability still requires verified renderer/font readiness.',
        "events_transport": "paged-json", "sse": False, "deletion": "logical-tombstone-retained",
        "physical_purge": False, "conditional_download_304": False,
        "binary_payloads": {name: {"sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data)} for name, data in binaries.items()}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for name, data in binaries.items():
        (destination / name).write_bytes(data)
    (destination / "README.md").write_text(
        "# K-04 local producer extension\n\nSynthetic fixtures only; no deployment or joint consumer acceptance is implied. "
        "Runtime qualification is recorded separately. Models come from the maintained workspace/Job producer definitions. "
        "Missing legacy Job metadata and export expiry are null/unknown; null expiry never means infinite TTL. "
        "Graph continuation is null for these complete synthetic graphs. Canonical result/project/snapshot IDs "
        "are frozen from a valid synthetic domain journey and are never rewritten for these examples.\n\n"
        "All page continuations are opaque signed values; current authorization is rechecked on every request. "
        "Examples with null continuation represent terminal pages; unfiltered claim/evidence totals are exact and catalog totals unknown. "
        "Header/claim/evidence/graph pages use the installed versioned read projection and do not open a full ProductView. "
        "Invitation creation contains "
        "a nonfunctional synthetic token placeholder; pending-list responses never return a token or digest. "
        "Real invitation tokens travel only in creation responses and acceptance JSON, never URLs.\n\n"
        "Export POST returns 201 with an ExportDescriptor, not a queued JobReceipt. Legacy synthetic descriptors "
        "have deterministic IDs and unknown expiry. Fixed synthetic nonce/expiry examples show the durable wire shape, "
        "not runtime TTL evidence. Download GET/HEAD "
        "supports 200/206 and one Range; server authorizes before 416/size headers and before each new chunk. "
        "Bytes validly delivered before revoke cannot be recalled; a later denied chunk aborts the response. "
        "Events are finite paged JSON, not SSE. PDF bytes use the installed ReportLab and packaged Unicode font; "
        "runtime PDF capability remains gated on actual renderer/font readiness. Reports are bounded result census, not every raw observation. "
        "Generic oversized canonical GET returns 413 response_too_large; no raw observation HTTP paging is advertised. Physical purge is not supported. "
        "Project deletion creates a retained logical tombstone; canonical rows, object references and receipts remain.\n",
        encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)
    print("K04 producer extension exported.")

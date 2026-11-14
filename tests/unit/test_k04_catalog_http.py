"""Catalog HTTP security/stream checks with real CSRF and canonical typed fixtures."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from k10_read_fakes import MemoryViews
from pydantic import SecretStr

import aletheia_lab
from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.durable_exports import StoredExport
from aletheia_lab.hosted.object_store import ObjectDescriptor
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceDenied
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.session_capabilities import Actor, Problem
from aletheia_lab.product.view import ProductView
from aletheia_lab.project.contracts import ProjectBundle

ROOT = Path(aletheia_lab.__file__).resolve().parents[2]
BUNDLE = ProjectBundle.model_validate_json((ROOT / "tests/fixtures/project_bundle_v1.json").read_bytes())
PROJECT = BUNDLE.project_id
RESULT = "p6-result-" + "a" * 64
BASE = "/v1/workspaces/ws1"


def _view():
    value = json.loads((ROOT / "tests/fixtures/synthetic_p6_view.json").read_text(encoding="utf-8"))
    old_project, old_result = value["project"]["id"], value["result"]["id"]
    value["project"]["id"], value["result"]["id"] = PROJECT, RESULT
    for turn in value["conversation"]["turns"]:
        if turn["result_id"] == old_result:
            turn["result_id"] = RESULT
    for node in value["graph"]["nodes"]:
        if node["source_id"] == old_project:
            node["source_id"] = PROJECT
        elif node["source_id"] == old_result:
            node["source_id"] = RESULT
    return ProductView.model_validate_json(json.dumps(value))


async def _scenario(tmp_path, *, path=None, method="GET", query=b"", body=None,
                    valid_csrf=True, origin=b"https://app.example.invalid", headers=(), denied=False,
                    error=None, payload=b"synthetic report", send_hook=None, output_scope="ws1",
                    content_type=b"application/json", raw_body=None, declared_length=None, maximum_response_bytes=1048576,
                    view_override=None, retention=None):
    view = _view() if view_override is None else view_override
    effects, messages, checkpoints, reads = [], [], [], []
    allowed = [not denied]
    tokens = CsrfTokens(SecretStr("synthetic-catalog-csrf-" + "k" * 43))
    csrf = tokens.issue("ctx1")
    session = SessionRecord(Actor(user_id="u1", display_name="Synthetic", email="u@example.invalid", email_verified=True),
        "ctx1", datetime.now(UTC) + timedelta(minutes=5), csrf, "ready", "ws1", "active", True)

    def access():
        if error:
            raise error
        if not allowed[0]:
            raise AuthorizationDenied()

    class Sessions:
        async def resolve_cookie(self, cookie):
            return session

    class Catalog:
        async def current(self, **kwargs):
            access()
            effects.append(("current", kwargs))
            return BUNDLE

        async def read_record(self, **kwargs):
            access()
            effects.append(("record", kwargs))
            return BUNDLE

    class Access:
        catalog = Catalog()
        cursors = ScopedCursors(SecretStr("synthetic-catalog-cursor-" + "c" * 43))

        async def projects(self, *args, **kwargs):
            access()
            effects.append(("projects", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope,
                    "items": [{"project_id": PROJECT, "revision": 1}], "next_cursor": None, "total": None, "total_kind": "unknown"}

        async def records(self, *args, **kwargs):
            access()
            effects.append(("records", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "items": [],
                    "next_cursor": None, "total": None, "total_kind": "unknown"}

        async def workspace_records(self, *args, **kwargs):
            access()
            effects.append(("workspace_records", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "items": [],
                    "next_cursor": None, "total": None, "total_kind": "unknown"}

        async def result(self, *args, **kwargs):
            access()
            effects.append(("result", args))
            return PROJECT, view

        async def authorize(self, *args):
            access()
            return Authorization("u1", "owner", 1, args[1])

        result_items = CatalogAccess.result_items
        result_evidence = CatalogAccess.result_evidence
        result_graph = CatalogAccess.result_graph
        result_header = CatalogAccess.result_header
        _view_binding = CatalogAccess._view_binding
        _after = CatalogAccess._after
        _page = CatalogAccess._page
        _unchanged = CatalogAccess._unchanged

        async def _binding(self, digest, workspace, context, query, *, project=None):
            access()
            effects.append(('projection', query))
            return {'workspace_id': workspace, 'user_id': 'u1', 'context_id': context,
                    'membership_revision': 1, 'query': query}

        def _artifact(self, workspace, report_format):
            export_id = f"ex_{report_format}_{RESULT}_" + "c" * 32
            media = "application/json" if report_format == "json" else "text/markdown; charset=utf-8"
            return StoredExport(workspace, export_id, PROJECT, RESULT, "u1", report_format,
                ObjectDescriptor(hashlib.sha256(payload).hexdigest(), len(payload), media),
                datetime.now(UTC) + timedelta(minutes=15))

        async def create_export(self, digest, workspace, result, report_format, *, key_hash=None):
            access()
            effects.append(("export", (digest, workspace, result, report_format)))
            return self._artifact(workspace, report_format)

        async def download_export(self, digest, workspace, export_id):
            access()
            effects.append(("download_export", (digest, workspace, export_id)))
            report_format = "markdown" if export_id.startswith("ex_markdown_") else "json"
            return self._artifact(workspace, report_format), payload

        def export_checkpoint(self, digest, item):
            return self.checkpoint(digest, item.workspace_id, item.project_id, "export")

        def records_checkpoint(self, digest, workspace, record_ids, *, project=None):
            assert isinstance(digest, SecretStr) and len(digest.get_secret_value()) == 64
            assert workspace == output_scope and record_ids == ()
            assert project in (None, PROJECT)
            return self.checkpoint(digest, workspace, project)

        def checkpoint(self, *args, record_id=None):
            async def checked():
                checkpoints.append(args)
                access()
            return checked

    class Projects:
        async def read_grant(self, **kwargs):
            access()
            effects.append(("read_grant", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "project_id": PROJECT,
                    "user_id": "target", "actions": [], "revision": 0}

        async def set_grant(self, **kwargs):
            access()
            effects.append(("set_grant", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "project_id": PROJECT,
                    "user_id": "target", "actions": kwargs["actions"], "revision": 1}

    class Reviews:
        async def put(self, **kwargs):
            access()
            effects.append(("review_put", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "project_id": PROJECT,
                    "result_id": RESULT, "annotation_id": "ann1", "revision": 1, "author_id": "u1",
                    "body": kwargs["body"], "status": kwargs["status"], "created_at": datetime.now(UTC)}

        async def list_history(self, **kwargs):
            access()
            effects.append(("history", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "project_id": PROJECT,
                    "result_id": RESULT, "annotation_id": "ann1", "items": [], "next_after_revision": None}

    class Mutations:
        async def delete(self, **kwargs):
            access()
            effects.append(("delete", kwargs))
            return {"schema_version": "product-api/v1-draft", "workspace_id": output_scope, "project_id": PROJECT,
                    "revision": kwargs["expected_revision"] + 1, "tombstone_id": "p6-deletion-" + "b" * 64,
                    "status": "tombstoned", "retention": "retained"}

    runtime = RuntimeSettings.model_validate({"environment": "staging", "public_origin": "https://app.example.invalid",
        "identity": {"issuer": "https://issuer.example.invalid", "client_id": "test", "client_kind": "public",
                     "redirect_uri": "https://app.example.invalid/v1/auth/callback"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        'api': {'maximum_response_bytes': maximum_response_bytes}})

    async def clock():
        return True

    async def fallback(scope, receive, send):
        messages.append({"fallback": True})

    instance = Access()

    async def projected_authorize(digest, workspace):
        return await instance.authorize(digest, workspace, PROJECT)

    instance.views = MemoryViews(lambda: view, projected_authorize)
    app = CatalogApplication(fallback, instance, Projects(), Reviews(), Sessions(), tokens, runtime,
                             body_limits=BodyLimits(10000, 1, 3, 10), clock_check=clock,
                             project_mutations=Mutations(), retention=retention)
    raw = raw_body if raw_body is not None else json.dumps({} if body is None else body).encode()
    request_headers = [(b"host", b"app.example.invalid"), (b"origin", origin),
        (b"content-length", str(len(raw) if declared_length is None else declared_length).encode()), (b"cookie", ("__Host-aletheia_session=" + "a" * 43 +
            "; __Host-aletheia_csrf=" + csrf.get_secret_value()).encode()),
        (b"x-csrf-token", csrf.get_secret_value().encode() if valid_csrf else b"invalid"), *headers]
    if content_type is not None:
        request_headers.append((b"content-type", content_type))

    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        messages.append(message)
        if send_hook:
            await send_hook(message, allowed)

    failure = None
    try:
        await app({"type": "http", "scheme": "https", "method": method,
                   "path": path or BASE + "/projects", "headers": request_headers, "query_string": query}, receive, send)
    except Exception as exception:
        failure = exception
    return messages, effects, checkpoints, reads, failure


def scenario(tmp_path, **kwargs):
    return asyncio.run(_scenario(tmp_path, **kwargs))


@pytest.mark.parametrize("path,query", [
    (BASE + "/projects", b"limit=20"), (BASE + "/projects/" + PROJECT, b""),
    (BASE + "/projects/" + PROJECT + "/snapshots", b""),
    (BASE + "/projects/" + PROJECT + "/records", b"kind=project_bundle"),
    (BASE + "/projects/" + PROJECT + "/records/" + BUNDLE.project_bundle_id, b""),
    (BASE + "/results", ("project_id=" + PROJECT).encode()), (BASE + "/results/" + RESULT, b""),
    (BASE + "/results/" + RESULT + "/claims", b"limit=1"), (BASE + "/results/" + RESULT + "/graph", b""),
    (BASE + "/results/" + RESULT + "/evidence", b"limit=1"),
    (BASE + "/events", ("project_id=" + PROJECT).encode()),
    (BASE + "/projects/" + PROJECT + "/grants/target", b""),
    (BASE + "/results/" + RESULT + "/annotations/ann1", b"limit=20"),
])
def test_current_scoped_read_routes_are_real_dispatches_and_deny_safely(tmp_path, path, query):
    messages, effects, _, reads, failure = scenario(tmp_path, path=path, query=query)
    assert failure is None and messages[0]["status"] == 200 and effects and not reads
    value = json.loads(messages[1]["body"])
    assert value["workspace_id"] == "ws1" and (b"cache-control", b"no-store") in messages[0]["headers"]
    messages, _, _, _, failure = scenario(tmp_path, path=path, query=query, denied=True)
    assert failure is None and messages[0]["status"] == 404
    assert Problem.model_validate_json(messages[1]["body"]).code == "not_found"


@pytest.mark.parametrize("query", [b"project_id=p&project_id=other", b"actor_id=evil", b"cursor=", b"limit=201", b"limit=01", b"kind=%GG"])
def test_bad_queries_never_reach_catalog(tmp_path, query):
    messages, effects, _, _, _ = scenario(tmp_path, query=query)
    assert messages[0]["status"] == 400 and not effects


def test_foreign_claim_cursor_and_wrong_evidence_parent_are_safe(tmp_path):
    view = _view()
    first = scenario(tmp_path, path=BASE + "/results/" + RESULT + "/claims", query=b"limit=1")[0]
    cursor = json.loads(first[1]["body"])["next_cursor"]
    if cursor:
        messages, _, _, _, _ = scenario(tmp_path, path=BASE.replace("ws1", "ws2") + "/results/" + RESULT + "/claims",
                                       query=("limit=1&cursor=" + cursor).encode())
        assert messages[0]["status"] == 400
    evidence = view.evidence[0]
    messages, _, _, _, _ = scenario(tmp_path, path=BASE + "/results/" + RESULT + "/evidence/" + evidence.id, query=b"claim_id=missing")
    assert messages[0]["status"] == 404
    messages, _, _, _, _ = scenario(tmp_path, path=BASE + "/results/" + RESULT + "/evidence/" + evidence.id)
    assert messages[0]["status"] == 200 and json.loads(messages[1]["body"])["relation"] is None


def test_bounded_header_has_exact_counts_without_full_result_arrays(tmp_path):
    messages, effects, _, _, failure = scenario(tmp_path, path=BASE + '/results/' + RESULT + '/header')
    assert failure is None and messages[0]['status'] == 200
    value = json.loads(messages[1]['body'])
    view = _view()
    assert value['project'] == view.project.model_dump(mode='json')
    assert value['result'] == view.result.model_dump(mode='json')
    assert value['claim_count'] == len(view.claims) and value['evidence_count'] == len(view.evidence)
    assert not ({'claims', 'evidence', 'graph', 'snapshot', 'conversation'} & value.keys())
    assert not any(name == 'result' for name, _ in effects)
    assert (b'cache-control', b'no-store') in messages[0]['headers']


def test_large_canonical_delivery_reports_a_bounded_413_without_partial_payload(tmp_path):
    from test_k02_graph_boundary import _large_view

    messages, _, _, _, failure = scenario(tmp_path, path=BASE + '/results/' + RESULT,
        maximum_response_bytes=4096, view_override=_large_view(4))
    assert failure is None and messages[0]['status'] == 413
    problem = Problem.model_validate_json(messages[1]['body'])
    assert problem.code == 'response_too_large' and len(messages[1]['body']) < 4096
    assert PROJECT.encode() not in messages[1]['body']


@pytest.mark.parametrize('query', [b'search=%00', b'search=' + b'x' * 129])
def test_bad_page_search_is_rejected_before_private_projection_reads(tmp_path, query):
    messages, effects, _, _, _ = scenario(tmp_path, path=BASE + '/results/' + RESULT + '/claims', query=query)
    assert messages[0]['status'] == 400 and not effects


@pytest.mark.parametrize("path,body", [
    (BASE + "/projects/" + PROJECT + "/grants/target", {"actions": ["read"]}),
    (BASE + "/results/" + RESULT + "/annotations/ann1", {"body": "Synthetic review", "status": "approved"}),
])
def test_mutation_csrf_cas_and_actor_spoof_prevent_effects(tmp_path, path, body):
    for options, expected in [({"valid_csrf": False}, 403), ({}, 428),
                              ({"headers": ((b"if-match", b'"0"'),), "body": {**body, "actor_id": "evil"}}, 422)]:
        messages, effects, _, _, failure = scenario(tmp_path, path=path, method="PUT", **{"body": body, **options})
        assert failure is None and messages[0]["status"] == expected
        assert not any(name in ("set_grant", "review_put") for name, _ in effects)
    messages, effects, _, _, failure = scenario(tmp_path, path=path, method="PUT", body=body, headers=((b"if-match", b'"0"'),))
    assert failure is None and messages[0]["status"] == 200
    assert any(name in ("set_grant", "review_put") for name, _ in effects)
    value = json.loads(messages[1]["body"])
    assert dict(messages[0]["headers"])[b"etag"] == f'"{value["revision"]}"'.encode()


def test_public_repository_error_mapping_and_foreign_page_validation(tmp_path):
    for error, status, code in [(WorkspaceDenied(), 403, "permission_denied"), (WorkspaceConflict(), 412, "revision_conflict")]:
        messages, _, _, _, _ = scenario(tmp_path, error=error)
        assert messages[0]["status"] == status and Problem.model_validate_json(messages[1]["body"]).code == code
        assert b"etag" not in dict(messages[0]["headers"])
    messages, _, _, _, _ = scenario(tmp_path, output_scope="ws2")
    assert messages[0]["status"] == 500


def test_export_descriptor_then_authorized_single_ranges_and_head(tmp_path):
    path = BASE + "/results/" + RESULT + "/exports"
    messages, _, _, _, failure = scenario(tmp_path, path=path, method="POST", body={"format": "markdown"})
    assert failure is None and messages[0]["status"] == 201
    descriptor = json.loads(messages[1]["body"])
    assert descriptor["export_id"] == "ex_markdown_" + RESULT + "_" + "c" * 32
    for value, expected in [(b"bytes=2-5", b"nthe"), (b"bytes=10-", b"report"), (b"bytes=-6", b"report")]:
        messages, _, checks, _, failure = scenario(tmp_path, path=descriptor["download_url"], headers=((b"range", value),))
        assert failure is None and messages[0]["status"] == 206 and messages[1]["body"] == expected and checks
        assert b"content-range" in dict(messages[0]["headers"])
    messages, _, _, _, _ = scenario(tmp_path, path=descriptor["download_url"], method="HEAD")
    assert messages[0]["status"] == 200 and messages[1]["body"] == b""
    assert int(dict(messages[0]["headers"])[b"content-length"]) == len(b"synthetic report")


def test_unauthorized_range_errors_do_not_reveal_size_or_existence(tmp_path):
    path = BASE + "/exports/ex_json_" + RESULT + "_" + "c" * 32 + "/download"
    for value in (b"bytes=999999-", b"not-a-range"):
        messages, _, _, _, _ = scenario(tmp_path, path=path, denied=True, headers=((b"range", value),))
        assert messages[0]["status"] == 404 and b"content-range" not in dict(messages[0]["headers"])
    messages, _, _, _, _ = scenario(tmp_path, path=path, headers=((b"range", b"bytes=99999-"),))
    assert messages[0]["status"] == 416 and dict(messages[0]["headers"])[b"content-range"] == b"bytes */16"


def test_revoke_between_chunks_aborts_partial_body_without_second_status(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        captured = []

        async def barrier(message, allowed):
            if message.get("type") == "http.response.body" and message.get("body"):
                captured.append(allowed)
                entered.set()
                await release.wait()

        pending = asyncio.create_task(_scenario(tmp_path, path=BASE + "/exports/ex_json_" + RESULT + "_" + "c" * 32 + "/download",
            payload=b"x" * (3 * 65536), send_hook=barrier))
        await asyncio.wait_for(entered.wait(), 5)
        captured[0][0] = False
        release.set()
        return await pending

    messages, _, checks, _, failure = asyncio.run(run())
    assert isinstance(failure, AuthorizationDenied)
    assert len([value for value in messages if value.get("type") == "http.response.start"]) == 1
    chunks = [value for value in messages if value.get("type") == "http.response.body"]
    assert len(chunks) == 1 and len(chunks[0]["body"]) == 65536 and chunks[0]["more_body"] is True and len(checks) >= 3


def test_slow_client_send_is_bounded_and_never_gets_false_complete(tmp_path):
    async def blocked(message, allowed):
        if message.get("type") == "http.response.body":
            await asyncio.Event().wait()
    messages, _, _, _, failure = scenario(tmp_path, path=BASE + "/exports/ex_json_" + RESULT + "_" + "c" * 32 + "/download",
                                          payload=b"x" * 131072, send_hook=blocked)
    assert isinstance(failure, TimeoutError)
    assert len([value for value in messages if value.get("type") == "http.response.start"]) == 1
    assert messages[-1].get("more_body") is True


def test_other_prefixes_fall_through_and_methods_report_allow(tmp_path):
    for path in (BASE + "/jobs/job1", BASE + "/sources", BASE + "/members"):
        assert scenario(tmp_path, path=path)[0] == [{"fallback": True}]
    messages, effects, _, _, _ = scenario(tmp_path, method="OPTIONS")
    assert messages[0]["status"] == 405 and (b"allow", b"GET, HEAD") in messages[0]["headers"] and not effects


@pytest.mark.parametrize("path,query,operation,search", [
    (BASE + "/projects", ("search=" + PROJECT).encode(), "projects", PROJECT),
    (BASE + "/projects/" + PROJECT + "/records", ("kind=result&search=" + RESULT).encode(), "records", RESULT),
    (BASE + "/results", ("search=" + RESULT).encode(), "workspace_records", RESULT),
    (BASE + "/events", b"limit=10", "workspace_records", None),
])
def test_workspace_wide_results_events_and_exact_id_search_are_actual_service_calls(tmp_path, path, query, operation, search):
    messages, effects, _, _, failure = scenario(tmp_path, path=path, query=query)
    assert failure is None and messages[0]["status"] == 200
    call = next(values for name, values in effects if name == operation)
    assert call["search"] == search
    if operation == "workspace_records":
        assert json.loads(messages[1]["body"])["project_id"] is None
        assert call["kind"] == ("regression_event" if path.endswith("events") else "result")


def test_search_is_bounded_literal_id_and_not_free_text_or_wildcard(tmp_path):
    for query in (b"search=private+text", b"search=%25", b"search=id&search=other"):
        messages, effects, _, _, _ = scenario(tmp_path, query=query)
        assert messages[0]["status"] == 400 and not effects


def test_delete_returns_tombstone_truth_and_preserved_retention_not_a_job_receipt(tmp_path):
    messages, effects, _, _, failure = scenario(tmp_path, path=BASE + "/projects/" + PROJECT,
        method="DELETE", body={}, headers=((b"if-match", b'"0"'),))
    assert failure is None and messages[0]["status"] == 200
    value = json.loads(messages[1]["body"])
    assert value["status"] == "tombstoned" and value["retention"] == "retained" and value["revision"] == 1
    assert dict(messages[0]["headers"])[b"etag"] == b'"1"'
    assert "job_id" not in value and "deleted_count" not in value
    assert next(values for name, values in effects if name == "delete")["expected_revision"] == 0


@pytest.mark.parametrize("raw,declared,status", [(b"", None, 200), (b"{}", None, 415), (b"{}", 0, 415)])
def test_delete_without_content_type_reads_actual_bounded_body(tmp_path, raw, declared, status):
    messages, effects, _, reads, failure = scenario(tmp_path, path=BASE + "/projects/" + PROJECT,
        method="DELETE", content_type=None, raw_body=raw, declared_length=declared, headers=((b"if-match", b'"0"'),))
    assert failure is None and messages[0]["status"] == status and reads
    assert any(name == "delete" for name, _ in effects) is (status == 200)


def test_delete_auth_csrf_precondition_and_spoofed_fields_have_no_effect(tmp_path):
    base = {"path": BASE + "/projects/" + PROJECT, "method": "DELETE", "headers": ((b"if-match", b'"0"'),)}
    for options, status in [({"valid_csrf": False}, 403), ({"headers": ()}, 428),
                            ({"body": {"role": "owner"}}, 422), ({"denied": True}, 404)]:
        messages, effects, _, _, failure = scenario(tmp_path, **{**base, **options})
        assert failure is None and messages[0]["status"] == status and not any(name == "delete" for name, _ in effects)


@pytest.mark.parametrize("ranges", [((b"range", b"bytes=-0"),), ((b"range", b"bytes=9-2"),),
    ((b"range", b"bytes=0-1,3-4"),), ((b"range", b"bytes=0-1"), (b"range", b"bytes=3-4")),
    ((b"range", b"bytes=-"),)])
def test_invalid_single_range_shapes_are_416_after_current_authorization(tmp_path, ranges):
    messages, _, checks, _, failure = scenario(tmp_path, path=BASE + "/exports/ex_json_" + RESULT + "_" + "c" * 32 + "/download", headers=ranges)
    assert failure is None and messages[0]["status"] == 416 and checks
    assert dict(messages[0]["headers"])[b"content-range"] == b"bytes */16"

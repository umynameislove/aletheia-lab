"""Durable export HTTP checks with canonical object files and a synthetic DB port."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from k10_read_fakes import MemoryViews
from pydantic import SecretStr
from test_k04_catalog_http import BASE, PROJECT, RESULT, _view

from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_objects import StagedObject
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.session_capabilities import Actor


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _Connection:
    autocommit = False

    def __init__(self, state):
        self.state = state

    async def execute(self, query, params=()):
        state = self.state
        state.queries.append((query, params))
        if query.startswith("SELECT project_id FROM k05_records"):
            return _Cursor((PROJECT,) if params[0] == "ws1" and params[1] in state.results else None)
        if query.startswith("SELECT record_id FROM k05_records"):
            return _Cursor((params[2],) if params[:2] == ("ws1", PROJECT) and params[2] in state.results else None)
        if query.startswith("SELECT export_id FROM k02_exports"):
            ws, creator, key = params
            return _Cursor(next(((item["export_id"],) for item in state.exports.values()
                if (item["workspace"], item["creator"], item["key"]) == (ws, creator, key)), None))
        if query.startswith("SELECT e.project_id"):
            item = state.exports.get(params)
            if item is None:
                return _Cursor()
            obj = state.objects[item["sha"]]
            return _Cursor((item["project"], item["result"], item["creator"], item["format"], item["sha"],
                obj.byte_size, obj.media_type, obj.schema_version, item["expires"], item["expires"] <= state.now))
        if query.startswith("INSERT INTO k05_object_references"):
            state.refs.add(params)
        elif query.startswith("INSERT INTO k02_exports"):
            ws, export, project, creator, result, fmt, sha, key = params
            assert (ws, result, sha) in state.refs
            state.exports[(ws, export)] = {"workspace": ws, "export_id": export, "project": project,
                "creator": creator, "result": result, "format": fmt, "sha": sha, "key": key,
                "expires": state.now + timedelta(minutes=15)}
        elif not query.startswith("SELECT pg_advisory_xact_lock"):
            raise AssertionError("Unexpected synthetic DB query")
        return _Cursor()


class _Pool:
    def __init__(self, state):
        self.state = state

    @asynccontextmanager
    async def connection(self):
        yield _Connection(self.state)


class _Objects:
    def __init__(self, state, pool, root):
        self.state, self.pool = state, pool
        self.store = FilesystemObjectStore(root)

    async def stage(self, workspace, payload, *, media_type):
        descriptor = self.store.publish(payload, media_type=media_type)
        self.state.publications += 1
        return StagedObject(descriptor, "synthetic-lease")

    async def ready(self, conn, workspace, staged):
        self.state.objects[staged.descriptor.sha256] = staged.descriptor

    async def finish(self, conn, workspace, staged):
        self.state.finished += 1

    async def read(self, workspace, descriptor):
        self.state.object_reads += 1
        return self.store.read(descriptor)


class _Policy:
    def __init__(self, state):
        self.state = state

    async def authorize(self, conn, digest, workspace, action):
        if not self.state.allowed or workspace != "ws1":
            raise AuthorizationDenied()
        return Authorization(self.state.user, "viewer", self.state.revision, workspace)

    async def authorize_project(self, conn, auth, workspace, project, action):
        if (not self.state.allowed or project != PROJECT or not self.state.read_grant
                or action == "export" and not self.state.export_grant):
            raise AuthorizationDenied()

    async def authorize_record(self, conn, auth, workspace, project, record, action, *, for_update=False):
        await self.authorize_project(conn, auth, workspace, project, action)
        if record not in self.state.results:
            raise AuthorizationDenied()
        return "result"


class _Renderer:
    def __init__(self, state, payload):
        self.state, self.payload = state, payload
        self.calls = 0
        self.hook = None

    async def render(self, report_format, payload):
        self.calls += 1
        if self.hook:
            self.hook()
        return self.payload


class Harness:
    def __init__(self, tmp_path, payload=b"synthetic durable report"):
        self.state = SimpleNamespace(now=datetime.now(UTC), exports={}, objects={}, refs=set(), queries=[], results={RESULT},
            publications=0, object_reads=0, finished=0, allowed=True, user="u1", revision=1,
            read_grant=True, export_grant=True, context="ctx1")
        self.pool = _Pool(self.state)
        self.objects = _Objects(self.state, self.pool, tmp_path / "catalog")
        self.catalog = SimpleNamespace(pool=self.pool, objects=self.objects, policy=_Policy(self.state))
        self.view = _view()
        self.views = {}
        self.renderer = _Renderer(self.state, payload)
        self.tokens = CsrfTokens(SecretStr("synthetic-durable-csrf-" + "k" * 43))
        self.runtime = RuntimeSettings.model_validate({"environment": "staging", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://issuer.example.invalid", "client_id": "test", "client_kind": "public",
                         "redirect_uri": "https://app.example.invalid/v1/auth/callback"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
        self.bind()

    def bind(self):
        harness = self

        class Access(CatalogAccess):
            async def result(self, digest, workspace, result, **kwargs):
                await self.authorize(digest, workspace, PROJECT, "read")
                view = harness.view if result == harness.view.result.id else harness.views.get(result)
                if view is None:
                    raise AuthorizationDenied()
                return view.project.id, view

            async def _binding(self, digest, workspace, context, query, *, project=None):
                auth = await self.authorize(digest, workspace, project or PROJECT)
                if context != harness.state.context:
                    raise AuthorizationDenied()
                return {'workspace_id': workspace, 'user_id': auth.user_id, 'context_id': context,
                        'membership_revision': auth.revision, 'query': query}

        class Sessions:
            async def resolve_cookie(self, cookie):
                state = harness.state
                return SessionRecord(Actor(user_id=state.user, display_name="Synthetic", email="u@example.invalid",
                    email_verified=True), state.context, datetime.now(UTC) + timedelta(minutes=5),
                    harness.csrf, "ready", "ws1", "active", True)

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected fallback")

        async def clock():
            return True

        self.access = Access(self.catalog, ScopedCursors(SecretStr("synthetic-durable-cursor-" + "c" * 43)), self.renderer)

        async def projected_authorize(digest, workspace):
            return await self.access.authorize(digest, workspace, PROJECT)

        self.access.views = MemoryViews(lambda: self.view, projected_authorize)
        self.app = CatalogApplication(fallback, self.access, object(), object(), Sessions(), self.tokens, self.runtime,
            body_limits=BodyLimits(10000, 1, 3, 10), clock_check=clock)

    async def request(self, path, *, method="GET", body=None, headers=(), query=b"", hook=None, valid_csrf=True):
        messages = []
        raw = json.dumps(body or {}).encode()
        self.csrf = self.tokens.issue(self.state.context)
        csrf = self.csrf.get_secret_value()
        request_headers = [(b"host", b"app.example.invalid"), (b"origin", b"https://app.example.invalid"),
            (b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
            (b"cookie", ("__Host-aletheia_session=" + "a" * 43 + "; __Host-aletheia_csrf=" + csrf).encode()),
            (b"x-csrf-token", csrf.encode() if valid_csrf else b"invalid"), *headers]

        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message):
            messages.append(message)
            if hook:
                await hook(message)

        failure = None
        try:
            await self.app({"type": "http", "scheme": "https", "method": method, "path": path,
                            "headers": request_headers, "query_string": query}, receive, send)
        except Exception as error:
            failure = error
        return messages, failure

    async def create(self, *, key=None, **options):
        headers = () if key is None else ((b"idempotency-key", key),)
        messages, failure = await self.request(BASE + "/results/" + RESULT + "/exports", method="POST",
            body={"format": "markdown"}, headers=headers, **options)
        assert failure is None and messages[0]["status"] == 201, messages
        return json.loads(messages[1]["body"])


def test_created_artifact_replays_after_access_restart_without_rendering_and_supports_ranges(tmp_path):
    async def run():
        h = Harness(tmp_path)
        first = await h.create(key=b"synthetic_export_request_key")
        replay = await h.create(key=b"synthetic_export_request_key")
        assert first == replay and h.renderer.calls == h.state.publications == 1
        assert first["expires_at"] is not None and first["byte_size"] == len(h.renderer.payload)
        assert h.state.finished == 1 and ("ws1", RESULT, first["sha256"]) in h.state.refs
        h.renderer = _Renderer(h.state, b"changed renderer output")
        h.bind()
        messages, failure = await h.request(first["download_url"], headers=((b"range", b"bytes=2-9"),))
        assert failure is None and messages[0]["status"] == 206
        assert messages[1]["body"] == b"synthetic durable report"[2:10] and h.renderer.calls == 0
        assert dict(messages[0]["headers"])[b"etag"] == ('"' + first["sha256"] + '"').encode()
        assert (b"cache-control", b"no-store") in messages[0]["headers"]
        head, _ = await h.request(first["download_url"], method="HEAD")
        assert head[0]["status"] == 200 and head[1]["body"] == b""
    asyncio.run(run())


def test_expiry_uses_current_db_boundary_replay_does_not_resurrect_and_new_request_gets_new_handle(tmp_path):
    async def run():
        h = Harness(tmp_path)
        first = await h.create(key=b"synthetic_export_request_key")
        h.state.now = next(iter(h.state.exports.values()))["expires"]
        messages, _ = await h.request(first["download_url"], headers=((b"range", b"bytes=999999-"),))
        assert messages[0]["status"] == 410 and b"content-range" not in dict(messages[0]["headers"])
        assert json.loads(messages[1]["body"])["code"] == "export_expired"
        messages, _ = await h.request(BASE + "/results/" + RESULT + "/exports", method="POST",
            body={"format": "markdown"}, headers=((b"idempotency-key", b"synthetic_export_request_key"),))
        assert messages[0]["status"] == 410 and h.renderer.calls == 1
        fresh = await h.create()
        assert fresh["export_id"] != first["export_id"]
        assert len(fresh["export_id"]) <= 128 and h.renderer.calls == 2
    asyncio.run(run())


@pytest.mark.parametrize("change", ["allowed", "read_grant", "export_grant", "user"])
def test_current_actor_and_both_grants_precede_expiry_range_and_object_reads(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        first = await h.create()
        h.state.now = next(iter(h.state.exports.values()))["expires"]
        setattr(h.state, change, "other-user" if change == "user" else False)
        messages, failure = await h.request(first["download_url"], headers=((b"range", b"bytes=999999-"),))
        assert failure is None and messages[0]["status"] == 404 and h.state.object_reads == 0
        assert b"content-range" not in dict(messages[0]["headers"])
        assert b"expired" not in messages[1]["body"] and b"sha256" not in messages[1]["body"]
    asyncio.run(run())


@pytest.mark.parametrize("expire", [False, True])
def test_revocation_or_expiry_between_chunks_aborts_without_second_status(tmp_path, expire):
    async def run():
        h = Harness(tmp_path, b"x" * (3 * 65536))
        first = await h.create()

        async def change_after_chunk(message):
            if message.get("type") == "http.response.body" and message.get("body"):
                if expire:
                    h.state.now = next(iter(h.state.exports.values()))["expires"]
                else:
                    h.state.allowed = False

        messages, failure = await h.request(first["download_url"], hook=change_after_chunk)
        assert failure is not None
        assert len([m for m in messages if m["type"] == "http.response.start"]) == 1
        chunks = [m for m in messages if m["type"] == "http.response.body"]
        assert len(chunks) == 1 and chunks[0]["more_body"] is True and len(chunks[0]["body"]) == 65536
    asyncio.run(run())


def test_csrf_invalid_keys_and_revocation_after_render_do_not_publish_metadata(tmp_path):
    async def run():
        h = Harness(tmp_path)
        path = BASE + "/results/" + RESULT + "/exports"
        for options, status in [({"valid_csrf": False}, 403),
            ({"headers": ((b"idempotency-key", b"bad"),)}, 400),
            ({"headers": ((b"idempotency-key", b"x" * 20), (b"idempotency-key", b"x" * 20))}, 400)]:
            messages, _ = await h.request(path, method="POST", body={"format": "markdown"}, **options)
            assert messages[0]["status"] == status and h.renderer.calls == 0 and not h.state.exports
        h.renderer.hook = lambda: setattr(h.state, "allowed", False)
        messages, _ = await h.request(path, method="POST", body={"format": "markdown"})
        assert messages[0]["status"] == 404 and not h.state.exports and not h.state.refs
    asyncio.run(run())


@pytest.mark.parametrize("changed_target", [False, True])
def test_same_actor_workspace_key_rejects_changed_format_or_authorized_result(tmp_path, changed_target):
    from aletheia_lab.product.view import ProductView

    async def run():
        h = Harness(tmp_path)
        key = b"synthetic_export_request_key"
        first = await h.create(key=key)
        result, fmt = RESULT, "json"
        if changed_target:
            result, fmt = "p6-result-" + "b" * 64, "markdown"
            data = h.view.model_dump_json().replace(RESULT, result)
            h.views[result] = ProductView.model_validate_json(data)
            h.state.results.add(result)
        messages, failure = await h.request(BASE + "/results/" + result + "/exports", method="POST",
            body={"format": fmt}, headers=((b"idempotency-key", key),))
        assert failure is None and messages[0]["status"] == 409
        assert json.loads(messages[1]["body"])["code"] == "idempotency_conflict"
        assert h.renderer.calls == h.state.publications == len(h.state.exports) == 1
        replay = await h.create(key=key)
        assert replay == first
    asyncio.run(run())

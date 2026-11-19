"""ASGI upload routes with actual CSRF/session guards and synthetic persistence."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_staging_http import UploadStagingApplication
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.session_capabilities import Actor
from aletheia_lab.hosted_contract.uploads import UploadStatus


def _scenario(tmp_path, *, method="POST", suffix="", payload=None, media="application/json", csrf=True, key=True,
              maximum=10000, scope_response="a", declared=None, query=b"", concurrent=1):
    async def run():
        tokens = CsrfTokens(SecretStr("synthetic-k07-" + "s" * 43))
        token = tokens.issue("ctx1")
        session = SessionRecord(Actor(user_id="u1", display_name="User", email="u@example.invalid", email_verified=True),
            "ctx1", datetime.now(UTC) + timedelta(minutes=5), token, "ready", "a", "active", True)
        calls, messages, reads = [], [], []

        class Sessions:
            async def is_ready(self): return True
            async def resolve_cookie(self, cookie): return session

        class Sources:
            active = 0
            maximum = 0
            async def _call(self, name, **kwargs):
                calls.append((name, kwargs))
                return UploadStatus(workspace_id=scope_response, upload_id="upl1", state="active", byte_size=5,
                    sha256="a" * 64, expires_at=datetime.now(UTC) + timedelta(days=1), received_bytes=0,
                    received_parts=[], missing_parts=[1])
            async def create_upload(self, **kwargs): return await self._call("create", **kwargs)
            async def upload_status(self, **kwargs): return await self._call("status", **kwargs)
            async def put_upload_part(self, **kwargs): return await self._call("part", **kwargs)
            async def complete_upload(self, **kwargs):
                Sources.active += 1
                Sources.maximum = max(Sources.maximum, Sources.active)
                try:
                    await asyncio.sleep(0.01)
                    return await self._call("complete", **kwargs)
                finally:
                    Sources.active -= 1
            async def cancel_upload(self, **kwargs): return await self._call("cancel", **kwargs)

        runtime = RuntimeSettings.model_validate({"environment": "staging", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://id.example.invalid", "client_id": "test",
                "redirect_uri": "https://app.example.invalid/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})

        async def fallback(scope, receive, send): raise AssertionError("unexpected fallback")
        async def clock(): return True

        app = UploadStagingApplication(fallback, Sources(), Sessions(), tokens, runtime,
            body_limits=BodyLimits(maximum, 1, 3, 10), part_limits=BodyLimits(maximum, 1, 3, 10), clock_check=clock)
        if payload is None:
            payload_bytes = json.dumps({"schema_version": "product-api/v1-draft", "source_type": "zip_bundle",
                "byte_size": 5, "sha256": "a" * 64,
                "parts": [{"part_number": 1, "byte_size": 5, "sha256": "a" * 64}]}).encode()
        else:
            payload_bytes = payload
        headers = [(b"host", b"app.example.invalid"), (b"origin", b"https://app.example.invalid"),
            (b"content-length", str(len(payload_bytes) if declared is None else declared).encode()),
            (b"cookie", f"__Host-aletheia_session={'a'*43}; __Host-aletheia_csrf={token.get_secret_value()}".encode()),
            (b"x-csrf-token", token.get_secret_value().encode() if csrf else b"wrong")]
        if media is not None:
            headers.append((b"content-type", media.encode()))
        if key:
            headers.append((b"idempotency-key", b"synthetic-k07-key-0001"))

        async def receive():
            reads.append(True)
            return {"type": "http.request", "body": payload_bytes, "more_body": False}
        async def send(message): messages.append(message)

        scope = {"type": "http", "method": method, "scheme": "https", "path": "/v1/workspaces/a/uploads" + suffix,
                 "headers": headers, "query_string": query}
        await asyncio.gather(*(app(scope, receive, send) for _ in range(concurrent)))
        assert Sources.maximum <= 1
        return messages, calls, reads
    return asyncio.run(run())


def test_create_and_resume_and_binary_parts_keep_exact_server_scope(tmp_path):
    for kwargs, status, call in (({}, 201, "create"),
        ({"method": "GET", "suffix": "/upl1", "payload": b"", "media": None}, 200, "status"),
        ({"method": "PUT", "suffix": "/upl1/parts/1", "payload": b"hello", "media": "application/octet-stream"}, 200, "part"),
        ({"suffix": "/upl1/complete", "payload": b"{}"}, 200, "complete"),
        ({"method": "DELETE", "suffix": "/upl1", "payload": b"", "media": None}, 200, "cancel")):
        messages, calls, _ = _scenario(tmp_path, **kwargs)
        assert messages[0]["status"] == status and calls[0][0] == call
        assert calls[0][1]["workspace_id"] == "a"
        assert (b"cache-control", b"no-store") in messages[0]["headers"]


def test_denied_bad_framing_schema_and_foreign_scope_are_safe(tmp_path):
    cases = [({"csrf": False}, 403), ({"key": False}, 400), ({"maximum": 2}, 413),
        ({"payload": b'{"server_path":"C:/private"}'}, 422),
        ({"method": "DELETE", "suffix": "/upl1", "media": None, "payload": b"x"}, 400),
        ({"method": "PUT", "suffix": "/upl1/parts/1", "payload": b"hello", "media": "application/octet-stream", "declared": 4}, 400),
        ({"query": b"actor=other"}, 400)]
    for kwargs, status in cases:
        messages, calls, _ = _scenario(tmp_path, **kwargs)
        assert messages[0]["status"] == status and not calls
    messages, _, _ = _scenario(tmp_path, scope_response="b")
    assert messages[0]["status"] == 500
    assert b"private" not in messages[1]["body"]


def test_head_keeps_length_without_body_and_wrong_method_lists_allow(tmp_path):
    messages, _, reads = _scenario(tmp_path, method="HEAD", suffix="/upl1", payload=b"", media=None)
    assert messages[0]["status"] == 200 and not messages[1]["body"] and not reads
    messages, calls, _ = _scenario(tmp_path, method="GET", payload=b"", media=None)
    assert messages[0]["status"] == 405 and (b"allow", b"POST") in messages[0]["headers"] and not calls


def test_large_completions_have_one_in_process_resource_slot(tmp_path):
    messages, calls, _ = _scenario(tmp_path, suffix="/upl1/complete", payload=b"{}", concurrent=3)
    assert len(calls) == 3 and all(message["status"] == 200 for message in messages if message["type"] == "http.response.start")

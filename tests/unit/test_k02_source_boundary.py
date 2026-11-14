"""Source upload uses real CSRF guards and validates scoped producer receipts."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace

import pytest
import test_j01_source_http as fixture
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aletheia_lab.hosted.source_http import SourceUploadApplication, SourceUploadReceipt
from aletheia_lab.hosted.upload_staging import UploadRejected

PRIVATE = "private_marker_do_not_disclose"


def _scenario(tmp_path, monkeypatch, *, error=None, receipt_patch=None, query=b"", method="POST",
              private_receipt=False, origin=None, expired=False, **options):
    def factory(fallback, sources, sessions, tokens, runtime, **kwargs):
        original = sources.put

        async def put(**values):
            receipt = await original(**values)
            if error is not None:
                raise error
            receipt = replace(receipt, sha256=hashlib.sha256(values["payload"]).hexdigest())
            if private_receipt:
                return PrivateReceipt(receipt.source_id, receipt.workspace_id, receipt.sha256, PRIVATE)
            return replace(receipt, **(receipt_patch or {}))

        sources.put = put
        if expired:
            async def resolve_cookie(cookie):
                return None
            sessions.resolve_cookie = resolve_cookie
        application = SourceUploadApplication(fallback, sources, sessions, tokens, runtime, **kwargs)

        async def app(scope, receive, send):
            scope = {**scope, "query_string": query, "method": method}
            if origin is not None:
                scope["headers"] = [(key, origin if key == b"origin" else value) for key, value in scope["headers"]]
            await application(scope, receive, send)
        return app

    monkeypatch.setattr(fixture, "SourceUploadApplication", factory)
    return fixture.scenario(tmp_path, **options)


@dataclass(frozen=True)
class PrivateReceipt:
    source_id: str
    workspace_id: str
    sha256: str
    private_metadata: str


def test_actual_receipt_matches_exact_source_bytes_and_strict_producer_schema(tmp_path, monkeypatch):
    payload = b"synthetic zip bytes for boundary tests"
    messages, effects, reads = _scenario(tmp_path, monkeypatch, payload=payload)
    start, end = messages
    assert start["status"] == 201 and len(effects) == len(reads) == 1
    receipt = SourceUploadReceipt.model_validate_json(end["body"])
    assert receipt.model_dump(mode="json") == {"schema_version": "product-api/v1-draft", "source_id": "src1",
        "workspace_id": "ws1", "sha256": hashlib.sha256(payload).hexdigest()}
    Draft202012Validator(SourceUploadReceipt.model_json_schema()).validate(json.loads(end["body"]))
    assert dict(start["headers"])[b"cache-control"] == b"no-store"
    assert int(dict(start["headers"])[b"content-length"]) == len(end["body"])
    with pytest.raises(ValidationError):
        receipt.workspace_id = "other"


@pytest.mark.parametrize("patch", [{"workspace_id": "foreign_ws"}, {"workspace_id": "../private"},
    {"source_id": "../private"}, {"source_id": 1}, {"source_id": ""}, {"sha256": "bad"}, {"sha256": "a" * 64}])
def test_foreign_or_invalid_receipt_is_a_safe_failure(tmp_path, monkeypatch, patch):
    messages, effects, reads = _scenario(tmp_path, monkeypatch, receipt_patch=patch)
    assert messages[0]["status"] == 500 and len(effects) == len(reads) == 1
    body = json.loads(messages[1]["body"])
    assert body["code"] == "service_failure" and "source_id" not in body and "workspace_id" not in body
    assert b"foreign_ws" not in messages[1]["body"] and b"../private" not in messages[1]["body"]


def test_extra_repository_metadata_is_rejected_without_reflecting_private_values(tmp_path, monkeypatch):
    messages, _, _ = _scenario(tmp_path, monkeypatch, private_receipt=True)
    assert messages[0]["status"] == 500 and PRIVATE.encode() not in messages[1]["body"]


@pytest.mark.parametrize("error,status,code", [
    (UploadRejected("upload_quota_exceeded", 413, "The workspace upload quota is exhausted."), 413, "upload_quota_exceeded"),
    (UploadRejected("upload_too_large", 413, "The source exceeds the configured upload limit."), 413, "upload_too_large"),
    (TimeoutError(PRIVATE), 503, "service_unavailable"),
])
def test_repository_quota_and_parser_busy_keep_public_status_and_safe_problem(tmp_path, monkeypatch, error, status, code):
    messages, effects, reads = _scenario(tmp_path, monkeypatch, error=error)
    assert messages[0]["status"] == status and len(effects) == len(reads) == 1
    assert json.loads(messages[1]["body"])["code"] == code
    assert PRIVATE.encode() not in messages[1]["body"] and (b"cache-control", b"no-store") in messages[0]["headers"]


@pytest.mark.parametrize("query", [b"unexpected=1", b"path=private_marker_do_not_disclose"])
def test_unexpected_query_is_rejected_before_body_reads_or_repository_effects(tmp_path, monkeypatch, query):
    messages, effects, reads = _scenario(tmp_path, monkeypatch, query=query)
    assert messages[0]["status"] == 400 and not effects and not reads
    assert json.loads(messages[1]["body"])["code"] == "invalid_request"
    assert PRIVATE.encode() not in messages[1]["body"]


@pytest.mark.parametrize("options,status", [({"valid_csrf": False}, 403), ({"maximum": 2}, 413),
    ({"key": False}, 400), ({"origin": b"https://foreign.example.invalid"}, 403), ({"expired": True}, 401)])
def test_body_budget_current_cookie_origin_csrf_and_key_guards_remain_before_effect(tmp_path, monkeypatch, options, status):
    messages, effects, reads = _scenario(tmp_path, monkeypatch, **options)
    assert messages[0]["status"] == status and not effects and not reads


def test_method_rejection_has_exact_allow_and_no_effect(tmp_path, monkeypatch):
    messages, effects, reads = _scenario(tmp_path, monkeypatch, method="GET")
    assert messages[0]["status"] == 405 and not effects and not reads
    assert dict(messages[0]["headers"])[b"allow"] == b"POST"


def test_quota_rejection_keeps_request_correlated_fixed_audit_outcome(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="aletheia.action"):
        messages, _, _ = _scenario(tmp_path, monkeypatch,
            error=UploadRejected("upload_quota_exceeded", 413, "The workspace upload quota is exhausted."))
    records = [record for record in caplog.records if record.name == "aletheia.action"]
    assert messages[0]["status"] == 413 and len(records) == 1
    assert records[0].getMessage() == "action_rejected" and records[0].action_outcome == "rejected"
    assert records[0].request_id == dict(messages[0]["headers"])[b"x-request-id"].decode()
    assert records[0].args == () and records[0].exc_info is None


@pytest.mark.parametrize("patch", [{"schema_version": "unknown"}, {"sha256": True},
                                  {"source_id": "../private"}, {"extra": PRIVATE}])
def test_source_receipt_model_and_independent_schema_both_reject_invalid_output(patch):
    value = {"schema_version": "product-api/v1-draft", "source_id": "src1", "workspace_id": "ws1", "sha256": "a" * 64}
    value.update(patch)
    with pytest.raises(ValidationError):
        SourceUploadReceipt.model_validate(value, strict=True)
    assert not Draft202012Validator(SourceUploadReceipt.model_json_schema()).is_valid(value)

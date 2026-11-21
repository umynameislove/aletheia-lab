"""Verify the provider adapter's wire contract using only a mock transport."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import httpx2
import pytest
from pydantic import SecretStr
from test_historical_state_projection import (
    _QUESTION,
    _STATE_A,
    _STATE_B,
    _historical_state_snapshot,
)
from test_k07_dimensions_v2 import snapshot_v2

from aletheia_lab.hosted.claim_review_provider import (
    CLAIM_PROVIDER_ENDPOINT,
    CLAIM_PROVIDER_MAX_OUTPUT_TOKENS,
    CLAIM_PROVIDER_MODEL,
    dispatch_claim_review_provider,
    prepare_claim_review_provider,
)
from aletheia_lab.hosted.upload_analysis import HISTORICAL_STATE_MODEL, analyze_upload

_FAKE_KEY = SecretStr("synthetic-test-credential")
_ATTEMPT_ID = "2c8fd7abcf1a4ab698029012b1692aac"
_REQUEST_ID = "req_synthetic_provider_receipt"


@pytest.fixture
def prepared_case(tmp_path):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, _STATE_A, _STATE_B, alias="private_alias_DO_NOT_SEND")
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    claim_id = view.claims[0].id
    prepared = prepare_claim_review_provider(view, claim_id, "allowed", snapshot)
    assert prepared is not None
    return prepared, view, snapshot


def _dispatch(prepared, handler, **overrides):
    options = {
        "approved_request_sha256": prepared.request_sha256,
        "api_key": _FAKE_KEY,
        "attempt_id": _ATTEMPT_ID,
        "transport": httpx2.MockTransport(handler),
    }
    options.update(overrides)
    return asyncio.run(dispatch_claim_review_provider(prepared, **options))


def _completion(decision="revise", **overrides):
    body = {
        "id": "resp_synthetic",
        "object": "response",
        "model": CLAIM_PROVIDER_MODEL,
        "service_tier": "default",
        "status": "completed",
        "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps({"decision": decision})}],
        }],
        "usage": {"input_tokens": 482, "output_tokens": 13, "total_tokens": 495},
    }
    body.update(overrides)
    return body


def test_projection_is_canonical_and_has_only_tagged_fields(prepared_case):
    prepared, view, snapshot = prepared_case
    body = json.loads(prepared.body)
    projected = json.loads(body["input"])

    assert set(projected) == {"asserted_verdict", "observed_state", "allowed_state"}
    assert projected["asserted_verdict"] == "allowed"
    observed, allowed = projected["observed_state"], projected["allowed_state"]
    assert set(observed) == set(allowed) == {"namespace", "generation", "state_sha256"}
    assert observed["namespace"] == allowed["namespace"]
    assert observed["generation"] != allowed["generation"]
    assert observed["state_sha256"] != allowed["state_sha256"]
    assert observed["state_sha256"] != _STATE_A.state_sha256
    assert allowed["state_sha256"] != _STATE_B.state_sha256

    assert prepared.request_sha256 == sha256(prepared.body).hexdigest()
    assert prepared == prepare_claim_review_provider(view, view.claims[0].id, "allowed", snapshot)
    for withheld in (
        snapshot.snapshot_id, snapshot.source_sha256, snapshot.source_id, view.result.id,
        view.claims[0].id, view.claims[0].text, view.evidence[0].id,
        "historical/operation", "original-request", "private_alias_DO_NOT_SEND",
        _FAKE_KEY.get_secret_value(), prepared.finding.receipt_sha256, prepared.finding.contract_sha256,
    ):
        assert withheld is not None
        assert withheld.encode() not in prepared.body
    assert body["store"] is False and body["stream"] is False
    assert body["tool_choice"] == "none" and body["tools"] == []
    assert body["reasoning"] == {"effort": "none"}
    assert body["service_tier"] == "default"
    assert body["max_output_tokens"] == CLAIM_PROVIDER_MAX_OUTPUT_TOKENS
    assert body["text"]["format"]["strict"] is True


def test_missing_observation_or_wrong_target_has_no_payload(prepared_case, tmp_path):
    _, view, _ = prepared_case
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, None, _STATE_B)
    unknown_view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    assert prepare_claim_review_provider(unknown_view, unknown_view.claims[0].id, "allowed", snapshot) is None
    assert prepare_claim_review_provider(view, view.claims[0].id, "allowed", snapshot) is None
    assert prepare_claim_review_provider(view, view.claims[0].id, "allowed", None) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_request_sha256": None},
        {"approved_request_sha256": "f" * 64},
        {"api_key": None},
        {"api_key": SecretStr("invalid key\r\n")},
        {"attempt_id": "workspace_alpha"},
    ],
)
def test_no_transport_without_exact_approval_and_valid_credentials(prepared_case, overrides):
    prepared, _, _ = prepared_case

    def unexpected(_: httpx2.Request):
        pytest.fail("unapproved request reached provider transport")

    result = _dispatch(prepared, unexpected, **overrides)
    assert result.status == "unavailable"
    assert result.provider_request_id is None
    assert result.input_tokens is None


def test_allowlist_is_enforced_after_digest_check(prepared_case):
    prepared, _, _ = prepared_case
    body = json.loads(prepared.body)
    payload = json.loads(body["input"])
    payload["extra"] = "prohibited"
    body["input"] = json.dumps(payload)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("ascii")
    tampered = replace(prepared, body=raw, request_sha256=sha256(raw).hexdigest())

    def unexpected(_: httpx2.Request):
        pytest.fail("unapproved payload shape reached transport")

    assert _dispatch(tampered, unexpected).status == "unavailable"


@pytest.mark.parametrize(("decision", "agreement"), [("revise", True), ("approve_in_scope", False)])
def test_provider_receipt_preserves_conflict_and_recorded_usage(prepared_case, decision, agreement):
    prepared, _, _ = prepared_case
    calls = []

    def fake(request: httpx2.Request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == CLAIM_PROVIDER_ENDPOINT
        assert request.content == prepared.body
        assert request.headers["X-Client-Request-Id"] == _ATTEMPT_ID
        assert request.headers["Authorization"] == "Bearer " + _FAKE_KEY.get_secret_value()
        return httpx2.Response(200, json=_completion(decision), headers={"x-request-id": _REQUEST_ID})

    result = _dispatch(prepared, fake)
    assert result.status == "completed" and len(calls) == 1
    assert result.provider_request_id == _REQUEST_ID
    assert result.provider_decision == decision and result.agrees_with_host is agreement
    assert result.input_tokens == 482 and result.output_tokens == 13
    assert prepared.finding.decision == "revise"


@pytest.mark.parametrize("code", [307, 429, 500])
def test_http_errors_keep_request_id_and_never_retry_or_redirect(prepared_case, code):
    prepared, _, _ = prepared_case
    calls = []

    def fake(request: httpx2.Request):
        calls.append(request)
        return httpx2.Response(code, headers={"x-request-id": _REQUEST_ID, "location": "https://other.invalid"})

    result = _dispatch(prepared, fake)
    assert result.status == "unresolved" and len(calls) == 1
    assert result.provider_request_id == _REQUEST_ID and result.input_tokens is None
    assert result.provider_decision is None


@pytest.mark.parametrize(
    ("change", "id_header", "usage_retained"),
    [
        ({"status": "incomplete"}, _REQUEST_ID, True),
        ({"model": "unapproved-model"}, _REQUEST_ID, False),
        ({"service_tier": "priority"}, _REQUEST_ID, False),
        ({"output": [{"type": "message", "role": "assistant", "status": "completed",
                      "content": [{"type": "refusal", "refusal": "provider refusal"}]}]}, _REQUEST_ID, True),
        ({"output": [{"type": "message", "role": "assistant", "status": "completed",
                      "content": [{"type": "output_text",
                                   "text": '{"decision":"revise","decision":"approve_in_scope"}'}]}]},
         _REQUEST_ID, True),
        ({"usage": {"input_tokens": True, "output_tokens": 13, "total_tokens": 14}}, _REQUEST_ID, False),
        ({"usage": {"input_tokens": 482, "output_tokens": 13, "total_tokens": 14}}, _REQUEST_ID, False),
        ({"usage": {"input_tokens": 9000, "output_tokens": 13, "total_tokens": 9013}}, _REQUEST_ID, False),
        ({}, "", True),
    ],
)
def test_invalid_provider_outputs_do_not_substitute_for_host(prepared_case, change, id_header, usage_retained):
    prepared, _, _ = prepared_case
    outcome = _dispatch(prepared, lambda _: httpx2.Response(
        200, json=_completion(**change), headers={"x-request-id": id_header},
    ))
    assert outcome.status == "unresolved"
    assert outcome.provider_request_id == (id_header or None)
    assert (outcome.input_tokens is not None) is usage_retained
    assert outcome.provider_decision is None and outcome.agrees_with_host is None
    assert prepared.finding.decision == "revise"


def test_transport_read_timeout_does_not_retry(prepared_case):
    prepared, _, _ = prepared_case
    calls = []

    def fake(request: httpx2.Request):
        calls.append(request)
        raise httpx2.ReadTimeout("simulated lost response", request=request)

    outcome = _dispatch(prepared, fake)
    assert outcome.status == "unresolved" and len(calls) == 1
    assert outcome.input_tokens is None


def test_overlong_response_retains_only_the_request_id(prepared_case):
    prepared, _, _ = prepared_case
    outcome = _dispatch(prepared, lambda _: httpx2.Response(
        200, json=_completion(oversized="x" * 40000), headers={"x-request-id": _REQUEST_ID},
    ))
    assert outcome.status == "unresolved"
    assert outcome.provider_request_id == _REQUEST_ID and outcome.input_tokens is None

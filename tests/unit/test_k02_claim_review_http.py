"""Claim review submits a scoped assertion and reads an immutable receipt."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from test_k02_job_boundary import PATH, PRIVATE, VERSION, _request, _system

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.claim_review_http import ClaimReviewApplication
from aletheia_lab.hosted.postgres_sources import SourceConflict, SourceNotFound
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.claim_review import (
    ClaimReviewBinding,
    ClaimReviewFinding,
    ClaimReviewReceipt,
)

WORKSPACE = "ws_boundary"
RESULT = "p6-result-" + "a" * 64
CLAIM = "p6-claim-" + "b" * 64
REVIEW = "job_boundary"
ROUTE = f"/v1/workspaces/{WORKSPACE}/results/{RESULT}/claims/{CLAIM}/reviews"
BODY = {"schema_version": VERSION, "asserted_verdict": "denied"}


def _receipt() -> ClaimReviewReceipt:
    return ClaimReviewReceipt(job_id=REVIEW, binding=ClaimReviewBinding(
        workspace_id=WORKSPACE, project_id="p3-project-" + "c" * 64,
        source_id="src_boundary", source_sha256="a" * 64,
        snapshot_id="upload-snapshot-" + "d" * 64, result_id=RESULT, claim_id=CLAIM,
        result_sha256="1" * 64, claim_sha256="2" * 64, evidence_sha256="3" * 64,
        receipt_sha256="4" * 64, contract_sha256="5" * 64,
        asserted_verdict="denied", candidate_sha256="6" * 64,
    ), finding=ClaimReviewFinding(decision="approve_in_scope", reason="The evidence supports the asserted verdict.",
        evidence_ids=("p6-evidence-" + "e" * 64,), observed_state_sha256="7" * 64,
        receipt_sha256="4" * 64, contract_sha256="5" * 64, execution_mode="replay"))


def _case(tmp_path):
    base, jobs, headers = _system(tmp_path)
    jobs.enqueue_claim_review = AsyncMock(return_value=jobs.receipt)
    jobs.read_claim_review = AsyncMock(return_value=_receipt())
    tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
    app = ClaimReviewApplication(base, jobs, base._sessions, tokens, base._runtime,
                                 body_limits=base._limits, clock_check=base._clock)
    return app, jobs, headers


@pytest.mark.parametrize("verdict", ["allowed", "denied"])
def test_submit_claim_review_binds_typed_assertion_and_route_scope(tmp_path, verdict):
    app, jobs, headers = _case(tmp_path)
    status, response_headers, raw = _request(app, headers, method="POST", path=ROUTE,
                                             body=json.dumps({**BODY, "asserted_verdict": verdict}).encode())
    assert status == 202 and json.loads(raw) == jobs.receipt.model_dump(mode="json")
    assert response_headers[b"cache-control"] == b"no-store"
    supplied = jobs.enqueue_claim_review.await_args.kwargs
    assert supplied.pop("request_key").get_secret_value() == "synthetic-request-key-0001"
    assert isinstance(supplied.pop("credential_digest"), SecretStr)
    assert supplied == {"workspace_id": WORKSPACE, "result_id": RESULT, "claim_id": CLAIM,
                        "asserted_verdict": verdict}


@pytest.mark.parametrize("body", [
    {"schema_version": VERSION}, {"schema_version": VERSION, "asserted_verdict": "unknown"},
    {"schema_version": PRIVATE, "asserted_verdict": "denied"}, {**BODY, "claim_id": PRIVATE},
    {**BODY, "mode": "provider"}, {**BODY, "candidate_sha256": PRIVATE},
])
def test_invalid_claim_review_payload_is_rejected_before_job_admission(tmp_path, body):
    app, jobs, headers = _case(tmp_path)
    status, _, raw = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(body).encode())
    assert status == 422 and PRIVATE.encode() not in raw
    jobs.enqueue_claim_review.assert_not_called()


@pytest.mark.parametrize("header, expected", [(b"x-csrf-token", 403), (b"origin", 403), (b"idempotency-key", 400)])
def test_claim_review_submission_keeps_existing_guards(tmp_path, header, expected):
    app, jobs, headers = _case(tmp_path)
    status, _, _ = _request(app, [(key, value) for key, value in headers if key != header],
                            method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert status == expected
    jobs.enqueue_claim_review.assert_not_called()


@pytest.mark.parametrize("error, expected", [(SourceNotFound(), 404), (AuthorizationDenied(), 404), (SourceConflict(), 409)])
def test_job_admission_reports_scope_and_request_conflicts_safely(tmp_path, error, expected):
    app, jobs, headers = _case(tmp_path)
    jobs.enqueue_claim_review.side_effect = error
    status, _, raw = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert status == expected and PRIVATE.encode() not in raw


def test_retained_read_uses_current_authority_and_exact_route_scope(tmp_path):
    app, jobs, headers = _case(tmp_path)
    receipt = _receipt()
    headers = [(key, value) for key, value in headers if key in {b"host", b"cookie"}]
    status, response_headers, raw = _request(app, headers, path=f"{ROUTE}/{REVIEW}")
    assert status == 200 and response_headers[b"cache-control"] == b"no-store"
    assert ClaimReviewReceipt.model_validate_json(raw) == receipt
    values = jobs.read_claim_review.await_args.kwargs
    assert isinstance(values.pop("credential_digest"), SecretStr)
    assert values == {"workspace_id": WORKSPACE, "result_id": RESULT, "claim_id": CLAIM, "review_id": REVIEW}

    jobs.read_claim_review.side_effect = AuthorizationDenied()
    status, _, raw = _request(app, headers, path=f"{ROUTE}/{REVIEW}")
    assert status == 404 and PRIVATE.encode() not in raw and b"evidence_ids" not in raw


def test_review_read_and_job_output_reject_wrong_stored_scope(tmp_path):
    app, jobs, headers = _case(tmp_path)
    receipt = _receipt()
    jobs.result.update(operation="review_claim", result=receipt.model_dump(mode="json"))
    status, _, raw = _request(app, headers, path=f"{PATH}/{REVIEW}/output")
    assert status == 200 and json.loads(raw) == jobs.result

    other_job = receipt.model_copy(update={"job_id": PRIVATE})
    jobs.result["result"] = other_job.model_dump(mode="json")
    jobs.read_claim_review.return_value = other_job
    for path in (f"{ROUTE}/{REVIEW}", f"{PATH}/{REVIEW}/output"):
        status, _, raw = _request(app, headers, path=path)
        assert status == 500 and PRIVATE.encode() not in raw

"""Claim-review downloads preserve the exact receipt and recheck authorization."""
from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader
from test_k02_claim_review_http import CLAIM, RESULT, REVIEW, ROUTE, WORKSPACE, _case, _receipt
from test_k02_job_boundary import _request

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.claim_review_reports import render_claim_review_report
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.worker_limits import MAX_REVIEW_RECEIPT_BYTES
from aletheia_lab.hosted.worker_supervisor import WorkerRejected
from aletheia_lab.hosted_contract.claim_review import (
    ClaimReviewProviderAttempt,
    ClaimReviewProviderResult,
    ProviderClaimReviewBinding,
    ProviderClaimReviewReceipt,
)
from aletheia_lab.project.persistence import _json_bytes

_ROUTE = f"{ROUTE}/{REVIEW}/export"


def _provider_receipt():
    local = _receipt()
    dispatched = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    return ProviderClaimReviewReceipt(job_id=REVIEW, usage="settled",
        binding=ProviderClaimReviewBinding(**local.binding.model_dump(), provider_plan_sha256="a" * 64),
        finding=local.finding.model_copy(update={"reason": "Mức đo 0.5 -> 0.6, kiểm tra bằng chứng."}),
        attempt=ClaimReviewProviderAttempt(attempt_id="b" * 32, request_sha256="c" * 64,
            model="synthetic-provider-A", dispatched_at=dispatched,
            deadline_at=dispatched + timedelta(seconds=10), received_at=dispatched + timedelta(seconds=1),
            held_microusd=13, outcome=ClaimReviewProviderResult(status="completed",
                provider_request_id="synthetic-request", input_tokens=42, output_tokens=13,
                provider_decision="revise", agrees_with_host=False)))


def _app(tmp_path, receipt):
    app, jobs, headers = _case(tmp_path)
    app._runtime = app._runtime.model_copy(update={
        "api": app._runtime.api.model_copy(update={"maximum_response_bytes": 65536}),
    })
    jobs.read_claim_review.return_value = receipt

    async def render(report_format, payload, *, claim_review):
        assert claim_review
        return render_claim_review_report(payload, report_format)

    renderer = AsyncMock(side_effect=render)
    app._review_renderer = SimpleNamespace(render=renderer)
    return app, jobs, headers, renderer


def test_claim_review_json_preserves_bytes_without_occupying_renderer_slot(monkeypatch):
    async def scenario():
        renderer = ReportRenderer()
        worker = AsyncMock(side_effect=AssertionError("JSON receipt requires no report child"))
        monkeypatch.setattr(renderer._worker, "render", worker)
        try:
            for receipt in (_receipt(), _provider_receipt()):
                canonical = _json_bytes(receipt.model_dump(mode="json"))
                assert await renderer.render("json", canonical, claim_review=True) == canonical
            for invalid in (b"{}", canonical + b" ", b" " * (MAX_REVIEW_RECEIPT_BYTES + 1)):
                with pytest.raises(WorkerRejected, match="worker_failed"):
                    await renderer.render("json", invalid, claim_review=True)
            worker.assert_not_awaited()
        finally:
            await renderer.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(("report_format", "extension", "media_type"), [
    ("json", "json", b"application/json"),
    ("markdown", "md", b"text/markdown; charset=utf-8"),
    ("pdf", "pdf", b"application/pdf"),
])
@pytest.mark.parametrize("provider", [False, True])
def test_download_is_deterministic_and_source_bound(tmp_path, report_format, extension, media_type, provider):
    receipt = _provider_receipt() if provider else _receipt()
    app, jobs, headers, renderer = _app(tmp_path, receipt)
    canonical = _json_bytes(receipt.model_dump(mode="json"))
    digest = sha256(canonical).hexdigest()
    query = f"format={report_format}".encode()
    status, fields, body = _request(app, headers, path=_ROUTE, query=query)
    repeated_status, repeated_fields, repeated_body = _request(app, headers, path=_ROUTE, query=query)
    assert status == repeated_status == 200 and body == repeated_body
    assert fields[b"cache-control"] == b"no-store" and fields[b"content-type"] == media_type
    assert fields[b"etag"] == repeated_fields[b"etag"] == f'"{sha256(body).hexdigest()}"'.encode()
    assert fields[b"x-aletheia-receipt-sha256"] == digest.encode()
    assert fields[b"content-disposition"] == f'attachment; filename="aletheia-review-{digest[:16]}.{extension}"'.encode()
    assert renderer.await_count == 2 and jobs.read_claim_review.await_count == 4
    for call in jobs.read_claim_review.await_args_list:
        assert {key: value for key, value in call.kwargs.items() if key != "credential_digest"} == {
            "workspace_id": WORKSPACE, "result_id": RESULT, "claim_id": CLAIM,
            "review_id": REVIEW, "action": "export",
        }
    if report_format == "json":
        assert body == canonical
        return
    rendered = (body.decode("utf-8") if report_format == "markdown"
                else "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(body)).pages))
    compact = "".join(rendered.split())
    assert all(value in compact for value in (digest, receipt.job_id, receipt.binding.source_sha256, RESULT,
                                               CLAIM, receipt.binding.snapshot_id, receipt.finding.evidence_ids[0]))
    assert receipt.mode in rendered and receipt.usage in rendered and receipt.finding.decision in rendered
    if provider:
        assert "0.5 -> 0.6" in rendered and "0.5 -\\> 0.6" not in rendered
        assert all(value in rendered for value in ("Mức đo", "revise", "synthetic-provider-A", "Input tokens",
                                                    "Budget held (micro-USD)", "Agrees with checked finding"))


@pytest.mark.parametrize("query", [b"", b"format=html", b"format=pdf&format=pdf", b"format=pdf&extra=1"])
def test_ambiguous_or_unsupported_export_format_does_not_read(tmp_path, query):
    app, jobs, headers, renderer = _app(tmp_path, _receipt())
    assert _request(app, headers, path=_ROUTE, query=query)[0] == 400
    jobs.read_claim_review.assert_not_called()
    renderer.assert_not_called()


@pytest.mark.parametrize("after_render", [False, True])
def test_export_rechecks_current_authority_before_delivering_any_bytes(tmp_path, after_render):
    receipt = _receipt()
    app, jobs, headers, renderer = _app(tmp_path, receipt)
    jobs.read_claim_review.side_effect = ([receipt, AuthorizationDenied()] if after_render else AuthorizationDenied())
    status, fields, body = _request(app, headers, path=_ROUTE, query=b"format=markdown")
    assert status == 404 and b"etag" not in fields and b"content-disposition" not in fields
    assert receipt.finding.reason.encode() not in body
    assert renderer.await_count == int(after_render)


def test_changed_scope_or_inconsistent_json_never_delivers_download(tmp_path):
    receipt = _receipt()
    app, jobs, headers, renderer = _app(tmp_path, receipt)
    jobs.read_claim_review.side_effect = [
        receipt, receipt.model_copy(update={"finding": receipt.finding.model_copy(update={"reason": "Changed"})}),
    ]
    status, fields, _ = _request(app, headers, path=_ROUTE, query=b"format=markdown")
    assert status == 500 and b"content-disposition" not in fields
    jobs.read_claim_review.side_effect = None
    renderer.side_effect = None
    renderer.return_value = json.dumps({"mutated": True}).encode()
    status, fields, body = _request(app, headers, path=_ROUTE, query=b"format=json")
    assert status == 500 and b"content-disposition" not in fields and b"mutated" not in body

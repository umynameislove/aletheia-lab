"""Render only the authorized immutable claim-review receipt."""
from __future__ import annotations

from hashlib import sha256

from aletheia_lab.hosted_contract.claim_review import (
    CLAIM_REVIEW_RECEIPT_ADAPTER,
    ProviderClaimReviewReceipt,
)
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.pdf import render_text_pdf
from aletheia_lab.product.reports import _PDF_LINES_PER_PAGE, _code, _paginate_pdf_lines, _pdf_lines
from aletheia_lab.project.persistence import _json_bytes

_TITLE = "Aletheia Lab Claim Evidence Review"


def _literal(value: str | None) -> str:
    return _code("not recorded" if value is None else " ".join(value.split()))


def _recorded(value: object | None) -> str:
    return _literal(None if value is None else str(value))


def _provider_lines(receipt: ProviderClaimReviewReceipt) -> list[str]:
    attempt = receipt.attempt
    lines = [
        "", "## Provider attempt", "",
        "The provider decision is recorded separately from the checked finding.",
        "",
        f"- Plan SHA-256: {_literal(receipt.binding.provider_plan_sha256)}",
        f"- Attempt ID: {_literal(attempt.attempt_id)}",
        f"- Request SHA-256: {_literal(attempt.request_sha256)}",
        f"- Model: {_literal(attempt.model)}",
        f"- Attempt reserved at: {_literal(attempt.dispatched_at.isoformat())}",
        f"- Deadline: {_literal(attempt.deadline_at.isoformat())}",
        f"- Received at: {_literal(attempt.received_at.isoformat() if attempt.received_at else None)}",
        f"- Budget held (micro-USD): {_recorded(attempt.held_microusd)}",
    ]
    outcome = attempt.outcome
    if outcome is None:
        lines.append("- No provider response recorded.")
    else:
        lines.extend([
            f"- Outcome status: {_literal(outcome.status)}",
            f"- Provider request ID: {_literal(outcome.provider_request_id)}",
            f"- Provider decision: {_literal(outcome.provider_decision)}",
            f"- Agrees with checked finding: {_recorded(outcome.agrees_with_host)}",
            f"- Input tokens: {_recorded(outcome.input_tokens)}",
            f"- Output tokens: {_recorded(outcome.output_tokens)}",
        ])
    return lines

def render_claim_review_report(payload: bytes, report_format: str) -> bytes:
    """Validate canonical receipt bytes without re-evaluating the finding."""
    receipt = CLAIM_REVIEW_RECEIPT_ADAPTER.validate_json(payload, strict=True)
    if _json_bytes(receipt.model_dump(mode="json")) != payload:
        raise ValueError("Claim review bytes are not canonical.")
    if report_format == "json":
        return payload
    if report_format not in ("markdown", "pdf"):
        raise ProductError("invalid_request", "The requested report format is not supported.")

    finding, binding = receipt.finding, receipt.binding
    lines = [
        f"# {_TITLE}", "",
        "Review of the asserted historical-state verdict for one saved claim.", "",
        "## Finding", "",
        f"- Review schema: {_literal(receipt.schema_version)}",
        f"- Review ID: {_literal(receipt.job_id)}",
        f"- Review SHA-256: {_literal(sha256(payload).hexdigest())}",
        f"- Review mode: {_literal(receipt.mode)}",
        f"- Usage: {_literal(receipt.usage)}",
        f"- Asserted verdict: {_literal(binding.asserted_verdict)}",
        f"- Decision: {_literal(finding.decision)}",
        f"- Reason: {_literal(finding.reason)}",
        f"- Producer mode: {_literal(finding.execution_mode)}",
        f"- Observed SHA-256: {_literal(finding.observed_state_sha256)}",
        "", "## Bound evidence", "",
        f"- Policy: {_literal(binding.policy)}",
        f"- Workspace ID: {_literal(binding.workspace_id)}",
        f"- Project ID: {_literal(binding.project_id)}",
        f"- Source ID: {_literal(binding.source_id)}",
        f"- Source SHA-256: {_literal(binding.source_sha256)}",
        f"- Snapshot ID: {_literal(binding.snapshot_id)}",
        f"- Result ID: {_literal(binding.result_id)}",
        f"- Result SHA-256: {_literal(binding.result_sha256)}",
        f"- Claim ID: {_literal(binding.claim_id)}",
        f"- Claim SHA-256: {_literal(binding.claim_sha256)}",
        f"- Evidence SHA-256: {_literal(binding.evidence_sha256)}",
        f"- Producer SHA-256: {_literal(binding.receipt_sha256)}",
        f"- Contract SHA-256: {_literal(binding.contract_sha256)}",
        f"- Candidate SHA-256: {_literal(binding.candidate_sha256)}",
        "", "## Citations", "",
        *[f"- {_literal(item)}" for item in finding.evidence_ids],
    ]
    if not finding.evidence_ids:
        lines.append("- No citations recorded.")
    provider = _provider_lines(receipt) if isinstance(receipt, ProviderClaimReviewReceipt) else []
    if report_format == "markdown":
        return ("\n".join(lines + provider) + "\n").encode("utf-8")
    pages = _paginate_pdf_lines(_pdf_lines("\n".join(lines)))
    if provider:
        attempt = _pdf_lines("\n".join(provider))
        if len(pages[-1]) + len(attempt) <= _PDF_LINES_PER_PAGE:
            pages = pages[:-1] + (pages[-1] + attempt,)
        else:
            pages += _paginate_pdf_lines(attempt)
    return render_text_pdf(pages, title=_TITLE)

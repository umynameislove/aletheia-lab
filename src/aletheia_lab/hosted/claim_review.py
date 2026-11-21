"""Verify a typed historical-state assertion against its canonical snapshot."""

from __future__ import annotations

from typing import Literal

from aletheia_lab.hosted_contract.claim_review import ClaimReviewFinding
from aletheia_lab.product.view import (
    HISTORICAL_STATE_MODEL,
    AnyProductView,
    ProductHistoricalStateEvidenceItem,
)
from aletheia_lab.project.identity import canonical_project_sha256

from .historical_state_evidence import evaluate_historical_state
from .upload_snapshot import AnyUploadSnapshot, HistoricalStateUploadSnapshot


def evaluate_claim_assertion(
    view: AnyProductView,
    claim_id: str,
    asserted_verdict: Literal["allowed", "denied"],
    snapshot: AnyUploadSnapshot | None,
) -> ClaimReviewFinding:
    """Check the requested verdict using the independently loaded scope and receipt."""
    claim = next((claim for claim in view.claims if claim.id == claim_id), None)
    if view.runtime.model != HISTORICAL_STATE_MODEL or claim is None:
        return ClaimReviewFinding(
            decision="unsupported", reason="This claim is outside the supported review scope.", evidence_ids=(),
        )
    citations = [
        evidence
        for evidence in view.evidence
        if evidence.id in claim.citation_ids and isinstance(evidence, ProductHistoricalStateEvidenceItem)
    ]
    if len(citations) != 1:
        return ClaimReviewFinding(
            decision="unsupported", reason="Review requires one historical-state citation.", evidence_ids=(),
        )
    evidence = citations[0]
    if not isinstance(snapshot, HistoricalStateUploadSnapshot):
        return ClaimReviewFinding(
            decision="tool_error", reason="The original historical snapshot is unavailable.", evidence_ids=(),
        )
    if (
        view.project.id != snapshot.mapping.project_id
        or view.snapshot.id != snapshot.snapshot_id
        or evidence.reproduction_ref.record_id != snapshot.snapshot_id
        or evidence.source_sha256 != snapshot.source_sha256
    ):
        return ClaimReviewFinding(
            decision="tool_error", reason="The loaded snapshot is outside the claim scope.", evidence_ids=(),
        )

    receipt = snapshot.historical_state.receipt
    contract = snapshot.historical_state.contract
    receipt_json = receipt.model_dump(mode="json")
    receipt_sha = canonical_project_sha256(receipt_json)
    contract_sha = canonical_project_sha256(contract.model_dump(mode="json")) if contract is not None else None
    expected = receipt.model_dump(mode="json", exclude={"schema_version", "observation"})
    expected.update(
        observed_state=receipt_json["observation"],
        allowed_state=contract.allowed_state.model_dump(mode="json") if contract is not None else None,
        receipt_sha256=receipt_sha,
        contract_sha256=contract_sha,
    )
    if evidence.historical_state.model_dump(mode="json", include=set(expected)) != expected:
        return ClaimReviewFinding(
            decision="tool_error", reason="The cited evidence does not match the original snapshot.", evidence_ids=(),
        )

    historical = evaluate_historical_state(snapshot.historical_state)
    decision: Literal["approve_in_scope", "revise", "unknown", "unsupported"]
    if historical.verdict == "unsupported":
        decision, reason = "unsupported", historical.reason
    elif historical.verdict in {"unknown", "underdefined"}:
        decision, reason = "unknown", historical.reason
    elif historical.verdict == asserted_verdict:
        decision, reason = "approve_in_scope", "The assertion agrees with the original receipt and contract."
    else:
        decision, reason = "revise", "The assertion conflicts with the original receipt and contract."
    return ClaimReviewFinding(
        decision=decision,
        reason=reason,
        evidence_ids=(evidence.id,),
        observed_state_sha256=receipt.observation.state_sha256 if receipt.observation is not None else None,
        receipt_sha256=receipt_sha,
        contract_sha256=contract_sha,
        execution_mode=receipt.execution_mode,
    )

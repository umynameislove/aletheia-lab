"""Scoped review compares a typed assertion with the original snapshot."""

from __future__ import annotations

from typing import Literal

import pytest
from test_historical_state_projection import (
    _CONSUMER,
    _QUESTION,
    _STATE_A,
    _STATE_B,
    _historical_state_snapshot,
)
from test_k07_dimensions_v2 import snapshot_v2

from aletheia_lab.hosted.claim_review import evaluate_claim_assertion
from aletheia_lab.hosted.upload_analysis import HISTORICAL_STATE_MODEL, analyze_upload
from aletheia_lab.product.view import ProductHistoricalStateEvidenceItem


@pytest.mark.parametrize(
    ("actual", "assertion", "decision"),
    [
        (_STATE_A, "allowed", "revise"),
        (_STATE_A, "denied", "approve_in_scope"),
        (_STATE_B, "allowed", "approve_in_scope"),
        (_STATE_B, "denied", "revise"),
    ],
)
def test_reviewer_recomputes_from_the_observed_state(tmp_path, actual, assertion, decision):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, actual, _STATE_B)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    evidence, = view.evidence
    assert isinstance(evidence, ProductHistoricalStateEvidenceItem)

    assert evidence.historical_state.output == (4.0, 3.0)
    assert evidence.historical_state.current_alias == "B"
    result = evaluate_claim_assertion(view, view.claims[0].id, assertion, snapshot)

    assert result.decision == decision
    assert result.evidence_ids == (evidence.id,)
    assert result.observed_state_sha256 == actual.state_sha256
    assert result.receipt_sha256 == evidence.historical_state.receipt_sha256
    assert result.contract_sha256 == evidence.historical_state.contract_sha256
    assert result.execution_mode == "replay"


@pytest.mark.parametrize(
    ("actual", "permitted", "consumer", "decision"),
    [
        (None, _STATE_B, _CONSUMER, "unknown"),
        (_STATE_B, None, _CONSUMER, "unknown"),
        (_STATE_B, _STATE_B, "unsupported-consumer", "unsupported"),
    ],
)
def test_incomplete_evidence_has_no_approval_or_revision(tmp_path, actual, permitted, consumer, decision):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, actual, permitted, consumer=consumer)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)

    result = evaluate_claim_assertion(view, view.claims[0].id, "allowed", snapshot)

    assert result.decision == decision
    assert result.evidence_ids == view.claims[0].citation_ids
    assert (result.observed_state_sha256 is None) == (actual is None)
    assert (result.contract_sha256 is None) == (permitted is None)


def test_claim_prose_and_projected_verdict_do_not_supply_the_answer(tmp_path):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, _STATE_A, _STATE_B)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    claim, = view.claims
    evidence, = view.evidence
    assert isinstance(evidence, ProductHistoricalStateEvidenceItem)
    drifted = view.model_copy(update={
        "claims": (claim.model_copy(update={"text": "The assertion is allowed."}),),
        "evidence": (evidence.model_copy(update={
            "historical_state": evidence.historical_state.model_copy(update={"verdict": "allowed"}),
        }),),
    })

    assert evaluate_claim_assertion(drifted, claim.id, "allowed", snapshot).decision == "revise"
    assert evaluate_claim_assertion(drifted, claim.id, "denied", snapshot).decision == "approve_in_scope"


@pytest.mark.parametrize("conflict", ["missing_snapshot", "wrong_snapshot", "wrong_source", "wrong_receipt"])
def test_scope_and_integrity_conflicts_stop_review(
    tmp_path, conflict: Literal["missing_snapshot", "wrong_snapshot", "wrong_source", "wrong_receipt"],
):
    _, base, _, _ = snapshot_v2(tmp_path)
    original = _historical_state_snapshot(base, _STATE_A, _STATE_B)
    view = analyze_upload(original, _QUESTION, HISTORICAL_STATE_MODEL)
    evidence, = view.evidence
    assert isinstance(evidence, ProductHistoricalStateEvidenceItem)
    snapshot = original
    if conflict == "missing_snapshot":
        snapshot = None
    elif conflict == "wrong_snapshot":
        snapshot = _historical_state_snapshot(base, _STATE_B, _STATE_B)
    elif conflict == "wrong_source":
        view = view.model_copy(update={
            "evidence": (evidence.model_copy(update={"source_sha256": "f" * 64}),),
        })
    else:
        view = view.model_copy(update={
            "evidence": (evidence.model_copy(update={
                "historical_state": evidence.historical_state.model_copy(update={"receipt_sha256": "f" * 64}),
            }),),
        })

    result = evaluate_claim_assertion(view, view.claims[0].id, "allowed", snapshot)

    assert result.decision == "tool_error"
    assert result.evidence_ids == ()
    assert result.receipt_sha256 is None


def test_metric_and_unrelated_claims_are_unsupported(tmp_path):
    _, base, _, _ = snapshot_v2(tmp_path)
    metric = analyze_upload(base, _QUESTION, "upload-snapshot-observation/v1")
    assert evaluate_claim_assertion(metric, metric.claims[0].id, "allowed", base).decision == "unsupported"

    snapshot = _historical_state_snapshot(base, _STATE_A, _STATE_B)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    assert evaluate_claim_assertion(view, "absent-claim", "denied", snapshot).decision == "unsupported"
    claim = view.claims[0].model_copy(update={"citation_ids": ()})
    unbound = view.model_copy(update={"claims": (claim,)})
    assert evaluate_claim_assertion(unbound, claim.id, "denied", snapshot).decision == "unsupported"

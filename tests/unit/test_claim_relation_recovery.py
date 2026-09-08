from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

import aletheia_lab.evaluation.claim_relation_recovery as recovery
import aletheia_lab.evaluation.claim_relation_recovery_execution as recovery_execution
from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimRelationResult,
    ClaimRelationResultBundle,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.claim_relation_provider import PreparedRelationRequest
from aletheia_lab.evaluation.claim_relation_recovery import (
    ClaimRelationRecoveryError,
    _VerifiedPredecessor,
    acquire_recovery_lease,
    build_predecessor_closeout,
    build_recovery_authorization,
    build_recovery_plan,
    build_recovery_preflight,
    publish_relation_result,
    rehearse_targeted_recovery,
)
from aletheia_lab.evaluation.claim_relation_recovery_contracts import (
    ClaimRelationRecoveryPlan,
)
from aletheia_lab.evaluation.claim_relation_recovery_execution import (
    _build_receipt,
    build_reconciled_bundle,
    finalize_targeted_recovery,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from scripts.claim_support_pool_construction import _load_relation_results

ROOT = Path(__file__).resolve().parents[2]


def _with_hash(payload: dict[str, object], field: str) -> dict[str, object]:
    return {**payload, field: canonical_execution_sha256(payload)}


def _assignment() -> ClaimRelationAssignmentRequest:
    evidence = build_visible_evidence_item(
        evidence_id="evidence-1",
        kind="metric",
        title="Observed metric",
        content="The observed metric is 0.75.",
        source_content_sha256="a" * 64,
    )
    provider_payload = {
        "claim_text": "The observed metric is 0.75.",
        "claim_type": "evidence_statement",
        "visible_evidence": (evidence.model_dump(mode="json"),),
    }
    identity_payload = {
        "schema_version": "claim-relation-assignment-request/v1",
        "source_output_sha256": "b" * 64,
        "claim_local_id": "claim-1",
        "provider_payload": provider_payload,
        "visible_context_sha256": "c" * 64,
    }
    digest = canonical_execution_sha256(identity_payload)
    return ClaimRelationAssignmentRequest.model_validate(
        {
            "assignment_request_id": f"ccrel-{digest}",
            "source_output_sha256": "b" * 64,
            "claim_local_id": "claim-1",
            **provider_payload,
            "visible_context_sha256": "c" * 64,
            "assignment_request_sha256": digest,
        }
    )


def _parsed_result(identity: str) -> ClaimRelationResult:
    response_payload: dict[str, object] = {
        "schema_version": "claim-relation-assignment-response/v1",
        "assignment_request_sha256": identity,
        "decisions": (
            {
                "evidence_id": "evidence-1",
                "relation_polarity": "neutral",
                "relation_scope": "none",
            },
        ),
    }
    response = ClaimRelationAssignmentResponse.model_validate(
        _with_hash(response_payload, "response_sha256")
    )
    result_payload: dict[str, object] = {
        "assignment_request_sha256": identity,
        "terminal_status": "parsed",
        "attempt_count": 1,
        "response": response,
        "issue_sha256": None,
    }
    identity_payload = {
        **result_payload,
        "response": response.model_dump(mode="json"),
    }
    return ClaimRelationResult.model_validate(
        {**result_payload, "result_sha256": canonical_execution_sha256(identity_payload)}
    )


def _failed_result(identity: str) -> ClaimRelationResult:
    payload: dict[str, object] = {
        "assignment_request_sha256": identity,
        "terminal_status": "technical_failure",
        "attempt_count": 2,
        "response": None,
        "issue_sha256": "d" * 64,
    }
    return ClaimRelationResult.model_validate(_with_hash(payload, "result_sha256"))


def _predecessor() -> tuple[_VerifiedPredecessor, RecoveryClaimPoolPreparation]:
    assignment = _assignment()
    successful = tuple(
        _parsed_result(canonical_execution_sha256({"assignment": ordinal}))
        for ordinal in range(961)
    )
    failed = _failed_result(assignment.assignment_request_sha256)
    results = (*successful, failed)
    bundle_payload: dict[str, object] = {
        "schema_version": "claim-relation-result-bundle/v1",
        "preparation_sha256": "1" * 64,
        "policy_sha256": "2" * 64,
        "results": tuple(item.model_dump(mode="json") for item in results),
        "parsed_count": 961,
        "technical_failure_count": 1,
        "registered_attempt_count": 963,
        "provider_calls_executed": True,
        "labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    bundle = ClaimRelationResultBundle.model_validate(
        {
            **bundle_payload,
            "results": results,
            "bundle_sha256": canonical_execution_sha256(bundle_payload),
        }
    )
    prepared = cast(
        PreparedRelationRequest,
        SimpleNamespace(
            assignment=assignment,
            authority=SimpleNamespace(provider_payload_sha256="3" * 64),
            request=SimpleNamespace(prompt_text="Judge only visible evidence."),
        ),
    )
    predecessor = _VerifiedPredecessor(
        authorization=cast(
            object,
            SimpleNamespace(authorization_sha256="4" * 64, source_commit_ref="5" * 40),
        ),
        plan=cast(
            object,
            SimpleNamespace(
                plan_sha256="6" * 64,
                model="gpt-4.1",
                model_snapshot="gpt-4.1-2025-04-14",
                maximum_output_tokens_per_request=600,
                maximum_provider_attempts_per_request=2,
            ),
        ),
        receipt_sha256="7" * 64,
        terminal_store_sha256="8" * 64,
        bundle=bundle,
        prepared=(prepared,),
        failed_result=failed,
        failed_prepared=prepared,
        failed_request_identity="9" * 64,
        failed_issue_sha256="d" * 64,
        failed_attempt_outcomes=("transient_error", "transient_error"),
    )
    preparation = cast(
        RecoveryClaimPoolPreparation,
        SimpleNamespace(
            preparation_sha256="1" * 64,
            evidence_semantics_policy_sha256="2" * 64,
        ),
    )
    return predecessor, preparation


def test_targeted_recovery_freezes_one_failure_and_preserves_961_successes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    predecessor, preparation = _predecessor()
    monkeypatch.setattr(recovery, "_verified_predecessor", lambda *_args: predecessor)
    closeout = build_predecessor_closeout(
        ROOT, preparation, predecessor_run=tmp_path / "predecessor"
    )
    plan = build_recovery_plan(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        source_commit_ref="5" * 40,
    )
    rehearsal = rehearse_targeted_recovery(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        plan=plan,
    )
    recovered = _parsed_result(closeout.failed_assignment_request_sha256)
    reconciled = build_reconciled_bundle(predecessor, closeout, recovered)

    assert closeout.failed_gateway_status == "retry_exhausted"
    assert closeout.failed_attempt_outcomes == ("transient_error", "transient_error")
    assert plan.request_count == 1
    assert plan.maximum_provider_attempts_per_request == 2
    assert rehearsal.predecessor_successes_unchanged
    assert reconciled.parsed_count == 962
    assert reconciled.unchanged_predecessor_result_count == 961
    assert reconciled.recovered_result_count == 1
    assert reconciled.total_provider_attempt_count == 964


def test_authorization_and_lease_fail_closed_after_one_registered_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    predecessor, preparation = _predecessor()
    monkeypatch.setattr(recovery, "_verified_predecessor", lambda *_args: predecessor)
    closeout = build_predecessor_closeout(
        ROOT, preparation, predecessor_run=tmp_path / "predecessor"
    )
    plan = build_recovery_plan(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        source_commit_ref="5" * 40,
    )
    rehearsal = rehearse_targeted_recovery(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        plan=plan,
    )
    run_dir = tmp_path / "targeted-recovery"
    run_dir.mkdir()
    state = RepositoryExecutionState(
        branch="main",
        head_commit="5" * 40,
        origin_main_commit="5" * 40,
        clean=True,
    )
    authorization = build_recovery_authorization(
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
        authorized_at="2026-09-07T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    ready = build_recovery_preflight(
        plan,
        rehearsal,
        repository_state=state,
        credential_present=True,
        authorization=authorization,
        run_dir=run_dir,
    )

    assert ready.status == "claim_relation_targeted_recovery_live_ready"
    assert ready.live_blockers == ()
    acquire_recovery_lease(run_dir, authorization)
    consumed = build_recovery_preflight(
        plan,
        rehearsal,
        repository_state=state,
        credential_present=True,
        authorization=authorization,
        run_dir=run_dir,
    )
    assert consumed.live_blockers == ("execution_already_started",)
    with pytest.raises(ClaimRelationRecoveryError, match="consumed"):
        acquire_recovery_lease(run_dir, authorization)


def test_recovery_plan_and_reconciled_bundle_reject_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    predecessor, preparation = _predecessor()
    monkeypatch.setattr(recovery, "_verified_predecessor", lambda *_args: predecessor)
    closeout = build_predecessor_closeout(
        ROOT, preparation, predecessor_run=tmp_path / "predecessor"
    )
    plan = build_recovery_plan(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        source_commit_ref="5" * 40,
    )
    altered_plan = plan.model_dump(mode="python")
    altered_plan["request_count"] = 2
    with pytest.raises(ValidationError):
        ClaimRelationRecoveryPlan.model_validate(altered_plan)

    recovered = _parsed_result(closeout.failed_assignment_request_sha256)
    reconciled = build_reconciled_bundle(predecessor, closeout, recovered)
    altered_bundle = reconciled.model_dump(mode="python")
    altered_bundle["results"] = tuple(altered_bundle["results"][:-1])
    with pytest.raises(ValidationError):
        type(reconciled).model_validate(altered_bundle)

    altered_binding = reconciled.model_dump(mode="python")
    altered_binding["recovery_result_sha256"] = "e" * 64
    altered_identity = reconciled.model_dump(mode="json", exclude={"bundle_sha256"})
    altered_identity["recovery_result_sha256"] = "e" * 64
    altered_binding["bundle_sha256"] = canonical_execution_sha256(altered_identity)
    with pytest.raises(ValidationError, match="reconciled relation result bundle differs"):
        type(reconciled).model_validate(altered_binding)


def test_reconciled_bundle_round_trips_through_publication_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    predecessor, preparation = _predecessor()
    monkeypatch.setattr(recovery, "_verified_predecessor", lambda *_args: predecessor)
    closeout = build_predecessor_closeout(
        ROOT, preparation, predecessor_run=tmp_path / "predecessor"
    )
    recovered = _parsed_result(closeout.failed_assignment_request_sha256)
    reconciled = build_reconciled_bundle(predecessor, closeout, recovered)
    path = tmp_path / "reconciled-results.json"
    path.write_text(reconciled.model_dump_json(), encoding="utf-8")

    loaded = _load_relation_results(path)

    assert loaded == reconciled


def test_finalization_recovers_publication_without_another_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    predecessor, preparation = _predecessor()
    monkeypatch.setattr(recovery, "_verified_predecessor", lambda *_args: predecessor)
    closeout = build_predecessor_closeout(
        ROOT, preparation, predecessor_run=tmp_path / "predecessor"
    )
    plan = build_recovery_plan(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        source_commit_ref="5" * 40,
    )
    rehearsal = rehearse_targeted_recovery(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        closeout=closeout,
        plan=plan,
    )
    run_dir = tmp_path / "targeted-recovery"
    run_dir.mkdir()
    state = RepositoryExecutionState(
        branch="main",
        head_commit="5" * 40,
        origin_main_commit="5" * 40,
        clean=True,
    )
    authorization = build_recovery_authorization(
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
        authorized_at="2026-09-07T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    publish_relation_result(run_dir / "predecessor-closeout.json", closeout)
    publish_relation_result(run_dir / "authorization.json", authorization)
    acquire_recovery_lease(run_dir, authorization)
    (run_dir / "attempt-store").mkdir()
    recovered = _parsed_result(closeout.failed_assignment_request_sha256)
    reconciled = build_reconciled_bundle(predecessor, closeout, recovered)
    receipt = _build_receipt(
        predecessor,
        closeout,
        authorization,
        recovered,
        store_sha256="f" * 64,
        gateway_status="parsed",
        reconciled=reconciled,
    )
    calls = 0

    def rebuild(*_args: object, **_kwargs: object) -> tuple[object, object, object]:
        nonlocal calls
        calls += 1
        return receipt, recovered, reconciled

    monkeypatch.setattr(recovery_execution, "_rebuild_targeted_recovery", rebuild)

    first = finalize_targeted_recovery(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        run_dir=run_dir,
        repository_state=state,
    )
    second = finalize_targeted_recovery(
        ROOT,
        preparation,
        predecessor_run=tmp_path / "predecessor",
        run_dir=run_dir,
        repository_state=state,
    )

    assert first == second == receipt
    assert calls == 2
    assert {item.name for item in run_dir.iterdir()} == {
        "predecessor-closeout.json",
        "authorization.json",
        "lease.json",
        "attempt-store",
        "recovery-result.json",
        "reconciled-results.json",
        "receipt.json",
    }

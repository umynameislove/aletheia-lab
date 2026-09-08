"""Execution and independent verification for targeted relation recovery."""

from __future__ import annotations

from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimRelationResult,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_relation_execution import (
    read_relation_result,
    require_complete_relation_store,
)
from aletheia_lab.evaluation.claim_relation_recovery import (
    _build_predecessor_closeout,
    _build_targeted_gateway_request,
    _load_model,
    _terminal_reader,
    _verified_predecessor,
    _VerifiedPredecessor,
    build_recovery_lease,
    build_recovery_plan,
    load_predecessor_closeout,
    load_recovery_authorization,
    publish_relation_result,
    rehearse_targeted_recovery,
    validate_predecessor_closeout,
    validate_recovery_authorization,
)
from aletheia_lab.evaluation.claim_relation_recovery_contracts import (
    RECEIPT_SCHEMA_VERSION,
    RECONCILED_BUNDLE_SCHEMA_VERSION,
    ClaimRelationPredecessorCloseout,
    ClaimRelationRecoveryAuthorization,
    ClaimRelationRecoveryError,
    ClaimRelationRecoveryLease,
    ClaimRelationRecoveryReceipt,
    ReconciledClaimRelationResultBundle,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import Clock, ProviderAdapter, execute_gateway_request


def build_reconciled_bundle(
    predecessor: _VerifiedPredecessor,
    closeout: ClaimRelationPredecessorCloseout,
    recovery_result: ClaimRelationResult,
) -> ReconciledClaimRelationResultBundle:
    """Replace only the registered failed terminal and lock all 961 successes."""

    if (
        recovery_result.assignment_request_sha256 != closeout.failed_assignment_request_sha256
        or recovery_result.terminal_status != "parsed"
    ):
        raise ClaimRelationRecoveryError("successful recovery result is unavailable")
    reconciled = tuple(
        recovery_result
        if item.assignment_request_sha256 == recovery_result.assignment_request_sha256
        else item
        for item in predecessor.bundle.results
    )
    unchanged = sum(
        current == original
        for current, original in zip(reconciled, predecessor.bundle.results, strict=True)
    )
    if unchanged != 961:
        raise ClaimRelationRecoveryError("reconciliation changed predecessor successes")
    payload: dict[str, object] = {
        "schema_version": RECONCILED_BUNDLE_SCHEMA_VERSION,
        "preparation_sha256": predecessor.bundle.preparation_sha256,
        "policy_sha256": predecessor.bundle.policy_sha256,
        "predecessor_result_bundle_sha256": predecessor.bundle.bundle_sha256,
        "predecessor_closeout_sha256": closeout.closeout_sha256,
        "recovery_result_sha256": recovery_result.result_sha256,
        "recovered_assignment_request_sha256": recovery_result.assignment_request_sha256,
        "results": tuple(item.model_dump(mode="json") for item in reconciled),
        "parsed_count": 962,
        "technical_failure_count": 0,
        "unchanged_predecessor_result_count": 961,
        "recovered_result_count": 1,
        "predecessor_provider_attempt_count": (predecessor.bundle.registered_attempt_count),
        "recovery_provider_attempt_count": recovery_result.attempt_count,
        "total_provider_attempt_count": (
            predecessor.bundle.registered_attempt_count + recovery_result.attempt_count
        ),
        "failures_preserved_in_history": True,
        "provider_calls_executed": True,
        "labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ReconciledClaimRelationResultBundle.model_validate(
        {
            **payload,
            "results": reconciled,
            "bundle_sha256": canonical_execution_sha256(payload),
        }
    )


def _build_receipt(
    predecessor: _VerifiedPredecessor,
    closeout: ClaimRelationPredecessorCloseout,
    authorization: ClaimRelationRecoveryAuthorization,
    recovery_result: ClaimRelationResult,
    *,
    store_sha256: str,
    gateway_status: str,
    reconciled: ReconciledClaimRelationResultBundle | None,
) -> ClaimRelationRecoveryReceipt:
    succeeded = recovery_result.terminal_status == "parsed"
    payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": (
            "claim_relation_targeted_recovery_complete"
            if succeeded
            else "claim_relation_targeted_recovery_complete_with_failure"
        ),
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "predecessor_closeout_sha256": closeout.closeout_sha256,
        "preparation_sha256": predecessor.bundle.preparation_sha256,
        "policy_sha256": predecessor.bundle.policy_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "recovery_terminal_store_sha256": store_sha256,
        "target_assignment_request_sha256": recovery_result.assignment_request_sha256,
        "recovery_result_sha256": recovery_result.result_sha256,
        "recovery_terminal_status": recovery_result.terminal_status,
        "recovery_provider_attempt_count": recovery_result.attempt_count,
        "recovery_gateway_status": gateway_status,
        "predecessor_unresolved_terminal_count": 1,
        "unresolved_terminal_count": 0 if succeeded else 1,
        "predecessor_provider_attempt_count": (predecessor.bundle.registered_attempt_count),
        "total_provider_attempt_count": (
            predecessor.bundle.registered_attempt_count + recovery_result.attempt_count
        ),
        "reconciled_result_bundle_sha256": (
            reconciled.bundle_sha256 if reconciled is not None else None
        ),
        "unchanged_predecessor_result_count": 961,
        "rerun_forbidden": True,
        "failures_preserved_in_history": True,
        "provider_calls_executed": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationRecoveryReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def execute_targeted_recovery(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    closeout: ClaimRelationPredecessorCloseout,
    authorization: ClaimRelationRecoveryAuthorization,
    run_dir: Path,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
) -> tuple[
    ClaimRelationRecoveryReceipt,
    ClaimRelationResult,
    ReconciledClaimRelationResultBundle | None,
]:
    """Consume the one-use lease and execute exactly the authorized target."""

    tracked_lease = ClaimRelationRecoveryLease.model_validate(
        _load_model(run_dir / "lease.json", ClaimRelationRecoveryLease, "recovery lease")
    )
    if tracked_lease != build_recovery_lease(authorization):
        raise ClaimRelationRecoveryError("targeted recovery lease differs")
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    if closeout != _build_predecessor_closeout(predecessor, preparation):
        raise ClaimRelationRecoveryError("targeted recovery closeout differs")
    target = _build_targeted_gateway_request(
        root,
        preparation,
        predecessor=predecessor,
        authorization=authorization,
    )
    active_clock = clock or SystemMonotonicClock()
    store_root = run_dir / "attempt-store"
    store = ClaimCorpusAttemptStore(store_root, clock=active_clock)
    prepared = (target,)
    shards = store.shards(prepared)  # type: ignore[arg-type]
    identity = target.request.initial_attempt.request_identity_sha256
    shard = shards[identity]
    if shard.current_state(identity) is not None:
        raise ClaimRelationRecoveryError("targeted recovery request state already exists")
    shard.prepare(target.request)
    shard.start(target.request)
    result = execute_gateway_request(
        target.request,
        adapter=adapter,
        clock=active_clock,
        cancellation=NeverCancelled(),
    )
    for attempt in result.attempts:
        shard.record_attempt(target.request, attempt)
    if result.raw_response is not None:
        shard.record_response(target.request, result)
    shard.record_parsed_or_failed(target.request, result)
    shard.mark_closeout_pending(target.request, result)
    shard.publish_terminal(target.request, result)
    recovery_result = read_relation_result(target, store_root)
    reconciled = (
        build_reconciled_bundle(predecessor, closeout, recovery_result)
        if recovery_result.terminal_status == "parsed"
        else None
    )
    inventory = _terminal_reader(store_root, identity).terminal_inventory(identity)
    receipt = _build_receipt(
        predecessor,
        closeout,
        authorization,
        recovery_result,
        store_sha256=store.store_sha256(shards),
        gateway_status=inventory.gateway_status,
        reconciled=reconciled,
    )
    _publish_recovery_artifacts(run_dir, recovery_result, reconciled, receipt)
    _verify_run_membership(run_dir, reconciled)
    return receipt, recovery_result, reconciled


def _rebuild_targeted_recovery(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    run_dir: Path,
) -> tuple[
    ClaimRelationRecoveryReceipt,
    ClaimRelationResult,
    ReconciledClaimRelationResultBundle | None,
]:
    """Rebuild the result and receipt from the immutable terminal store."""

    closeout = load_predecessor_closeout(run_dir / "predecessor-closeout.json")
    validate_predecessor_closeout(
        root,
        preparation,
        predecessor_run=predecessor_run,
        closeout=closeout,
    )
    authorization = load_recovery_authorization(run_dir / "authorization.json")
    state = _authorization_repository_state(authorization)
    plan = build_recovery_plan(
        root,
        preparation,
        predecessor_run=predecessor_run,
        closeout=closeout,
        source_commit_ref=authorization.source_commit_ref,
    )
    rehearsal = rehearse_targeted_recovery(
        root,
        preparation,
        predecessor_run=predecessor_run,
        closeout=closeout,
        plan=plan,
    )
    validate_recovery_authorization(
        authorization,
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
    )
    tracked_lease = ClaimRelationRecoveryLease.model_validate(
        _load_model(run_dir / "lease.json", ClaimRelationRecoveryLease, "recovery lease")
    )
    if tracked_lease != build_recovery_lease(authorization):
        raise ClaimRelationRecoveryError("targeted recovery lease differs")
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    target = _build_targeted_gateway_request(
        root,
        preparation,
        predecessor=predecessor,
        authorization=authorization,
    )
    prepared = (target,)
    store_root = run_dir / "attempt-store"
    require_complete_relation_store(store_root, prepared)
    store = ClaimCorpusAttemptStore(store_root, clock=SystemMonotonicClock())
    shards = store.shards(prepared)  # type: ignore[arg-type]
    recovery_result = read_relation_result(target, store_root)
    reconciled = (
        build_reconciled_bundle(predecessor, closeout, recovery_result)
        if recovery_result.terminal_status == "parsed"
        else None
    )
    identity = target.request.initial_attempt.request_identity_sha256
    inventory = _terminal_reader(store_root, identity).terminal_inventory(identity)
    rebuilt = _build_receipt(
        predecessor,
        closeout,
        authorization,
        recovery_result,
        store_sha256=store.store_sha256(shards),
        gateway_status=inventory.gateway_status,
        reconciled=reconciled,
    )
    return rebuilt, recovery_result, reconciled


def _publish_recovery_artifacts(
    run_dir: Path,
    recovery_result: ClaimRelationResult,
    reconciled: ReconciledClaimRelationResultBundle | None,
    receipt: ClaimRelationRecoveryReceipt,
) -> None:
    publish_relation_result(run_dir / "recovery-result.json", recovery_result)
    if reconciled is not None:
        publish_relation_result(run_dir / "reconciled-results.json", reconciled)
    publish_relation_result(run_dir / "receipt.json", receipt)


def finalize_targeted_recovery(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    run_dir: Path,
    repository_state: RepositoryExecutionState,
) -> ClaimRelationRecoveryReceipt:
    """Publish missing artifacts without executing another provider call."""

    authorization = load_recovery_authorization(run_dir / "authorization.json")
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != authorization.source_commit_ref
    ):
        raise ClaimRelationRecoveryError(
            "targeted recovery finalization requires its clean synchronized main commit"
        )
    receipt, recovery_result, reconciled = _rebuild_targeted_recovery(
        root,
        preparation,
        predecessor_run=predecessor_run,
        run_dir=run_dir,
    )
    _publish_recovery_artifacts(run_dir, recovery_result, reconciled, receipt)
    _verify_run_membership(run_dir, reconciled)
    return receipt


def verify_targeted_recovery(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    run_dir: Path,
) -> ClaimRelationRecoveryReceipt:
    """Independently rebuild the targeted result, reconciliation, and receipt."""

    rebuilt, recovery_result, reconciled = _rebuild_targeted_recovery(
        root,
        preparation,
        predecessor_run=predecessor_run,
        run_dir=run_dir,
    )
    _verify_run_membership(run_dir, reconciled)
    tracked_result = ClaimRelationResult.model_validate(
        _load_model(
            run_dir / "recovery-result.json",
            ClaimRelationResult,
            "targeted recovery result",
        )
    )
    if recovery_result != tracked_result:
        raise ClaimRelationRecoveryError("targeted recovery result differs from store")
    tracked_receipt = ClaimRelationRecoveryReceipt.model_validate(
        _load_model(run_dir / "receipt.json", ClaimRelationRecoveryReceipt, "recovery receipt")
    )
    if tracked_receipt != rebuilt:
        raise ClaimRelationRecoveryError("targeted recovery receipt does not verify")
    return tracked_receipt


def _authorization_repository_state(
    authorization: ClaimRelationRecoveryAuthorization,
) -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch="main",
        head_commit=authorization.source_commit_ref,
        origin_main_commit=authorization.source_commit_ref,
        clean=True,
    )


def _verify_run_membership(
    run_dir: Path,
    reconciled: ReconciledClaimRelationResultBundle | None,
) -> None:
    expected_names = {
        "predecessor-closeout.json",
        "authorization.json",
        "lease.json",
        "attempt-store",
        "recovery-result.json",
        "receipt.json",
    }
    if reconciled is not None:
        expected_names.add("reconciled-results.json")
        tracked = ReconciledClaimRelationResultBundle.model_validate(
            _load_model(
                run_dir / "reconciled-results.json",
                ReconciledClaimRelationResultBundle,
                "reconciled relation results",
            )
        )
        if tracked != reconciled:
            raise ClaimRelationRecoveryError("reconciled relation results differ")
    try:
        if {item.name for item in run_dir.iterdir()} != expected_names or any(
            item.is_symlink() for item in run_dir.iterdir()
        ):
            raise OSError("targeted recovery run membership differs")
    except OSError as exc:
        raise ClaimRelationRecoveryError("targeted recovery run is incomplete") from exc


__all__ = [
    "build_reconciled_bundle",
    "execute_targeted_recovery",
    "finalize_targeted_recovery",
    "verify_targeted_recovery",
]

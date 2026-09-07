from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    build_visible_evidence_item,
    load_evidence_semantics_policy,
)
from aletheia_lab.evaluation.claim_relation_execution import (
    ClaimRelationExecutionError,
    PacedProviderAdapter,
    _require_complete_relation_store,
    acquire_relation_lease,
    build_relation_authorization,
    build_relation_lease,
    build_relation_preflight,
    checked_relation_run_directory,
    execute_relation_census,
    rehearse_relation_execution,
    verify_relation_execution,
)
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    ClaimRelationExecutionPlan,
    ClaimRelationExecutionReceipt,
    ClaimRelationExecutionRehearsal,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PreparedRelationRequest,
    build_relation_gateway_requests,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    ClaimRelationProviderContext,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
)

ROOT = Path(__file__).resolve().parents[2]


def _with_hash(payload: dict[str, object], field: str) -> dict[str, object]:
    return {**payload, field: canonical_execution_sha256(payload)}


def _plan(
    identities: tuple[str, ...] | None = None,
    *,
    policy_sha256: str = "4" * 64,
) -> ClaimRelationExecutionPlan:
    if identities is None:
        identities = tuple(
            canonical_execution_sha256({"relation_request": ordinal}) for ordinal in range(962)
        )
    exact_input = 437_982
    conservative_input = exact_input * 2 + 962 * 1024 * 2
    output = 962 * 600 * 2
    payload: dict[str, object] = {
        "schema_version": "claim-relation-execution-plan/v1",
        "source_commit_ref": "1" * 40,
        "preparation_sha256": "2" * 64,
        "recovery_closeout_sha256": "3" * 64,
        "evidence_semantics_policy_sha256": policy_sha256,
        "request_census_sha256": canonical_execution_sha256(identities),
        "request_count": 962,
        "model": "gpt-4.1",
        "model_snapshot": "gpt-4.1-2025-04-14",
        "maximum_output_tokens_per_request": 600,
        "maximum_provider_attempts_per_request": 2,
        "minimum_provider_interval_ms": 1000,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": "0.14.0",
        "tokenizer_encoding": "o200k_base",
        "response_format_token_allowance_per_request": 1024,
        "input_usd_per_million_tokens": 2.0,
        "output_usd_per_million_tokens": 8.0,
        "exact_message_input_token_count": exact_input,
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output,
        "estimated_upper_cost_usd": round(
            conservative_input * 2.0 / 1_000_000 + output * 8.0 / 1_000_000,
            6,
        ),
        "assignment_request_sha256s": identities,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionPlan.model_validate(_with_hash(payload, "plan_sha256"))


def _assignment(ordinal: int) -> ClaimRelationAssignmentRequest:
    evidence = build_visible_evidence_item(
        evidence_id="evidence-1",
        kind="metric",
        title="Observed metric",
        content="The observed metric is 0.75.",
        source_content_sha256="a" * 64,
    )
    provider_payload = {
        "claim_text": f"The observed metric is 0.75 in request {ordinal}.",
        "claim_type": "evidence_statement",
        "visible_evidence": (evidence.model_dump(mode="json"),),
    }
    identity_payload = {
        "schema_version": "claim-relation-assignment-request/v1",
        "source_output_sha256": canonical_execution_sha256({"output": ordinal}),
        "claim_local_id": f"claim-{ordinal % 5 + 1}",
        "provider_payload": provider_payload,
        "visible_context_sha256": "b" * 64,
    }
    digest = canonical_execution_sha256(identity_payload)
    return ClaimRelationAssignmentRequest.model_validate(
        {
            "assignment_request_id": f"ccrel-{digest}",
            "source_output_sha256": identity_payload["source_output_sha256"],
            "claim_local_id": identity_payload["claim_local_id"],
            **provider_payload,
            "visible_context_sha256": "b" * 64,
            "assignment_request_sha256": digest,
        }
    )


def _rehearsal(plan: ClaimRelationExecutionPlan) -> ClaimRelationExecutionRehearsal:
    payload: dict[str, object] = {
        "schema_version": "claim-relation-execution-rehearsal/v1",
        "status": "claim_relation_execution_rehearsal_passed",
        "plan_sha256": plan.plan_sha256,
        "preparation_sha256": plan.preparation_sha256,
        "policy_sha256": plan.evidence_semantics_policy_sha256,
        "request_count": 962,
        "maximum_visible_evidence_items_observed": 4,
        "provider_input_fields": ("claim_text", "claim_type", "visible_evidence"),
        "valid_response_sha256": "5" * 64,
        "incoherent_relation_rejected": True,
        "unknown_evidence_rejected": True,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionRehearsal.model_validate(_with_hash(payload, "rehearsal_sha256"))


def _state(*, clean: bool = True) -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch="main" if clean else "feature",
        head_commit="1" * 40,
        origin_main_commit="1" * 40,
        clean=clean,
    )


def test_plan_reconciles_exact_census_and_conservative_cost() -> None:
    plan = _plan()

    assert plan.request_count == 962
    assert len(set(plan.assignment_request_sha256s)) == 962
    assert plan.exact_message_input_token_count == 437_982
    assert plan.estimated_upper_cost_usd == 14.92748


def test_authorization_binds_rehearsal_destination_and_one_attempt(tmp_path: Path) -> None:
    plan = _plan()
    rehearsal = _rehearsal(plan)
    authorization = build_relation_authorization(
        plan,
        rehearsal,
        repository_state=_state(),
        run_dir=tmp_path / "private-run",
        authorized_at=datetime(2026, 9, 7, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        operator_cost_ceiling_usd=15.0,
    )
    lease = build_relation_lease(authorization)

    assert authorization.rehearsal_sha256 == rehearsal.rehearsal_sha256
    assert authorization.registered_attempts == 1
    assert lease.authorization_sha256 == authorization.authorization_sha256
    assert lease.plan_sha256 == plan.plan_sha256

    run_dir = tmp_path / "private-run"
    run_dir.mkdir()
    published = acquire_relation_lease(run_dir, authorization)
    assert published == lease
    with pytest.raises(ClaimRelationExecutionError, match="already consumed"):
        acquire_relation_lease(run_dir, authorization)


def test_full_relation_census_rehearses_and_builds_only_blind_gateway_fields(
    tmp_path: Path,
) -> None:
    assignments = tuple(_assignment(ordinal) for ordinal in range(962))
    policy = load_evidence_semantics_policy(ROOT)
    identities = tuple(item.assignment_request_sha256 for item in assignments)
    plan = _plan(identities, policy_sha256=policy.policy_sha256)
    preparation = cast(
        RecoveryClaimPoolPreparation,
        SimpleNamespace(
            preparation_sha256=plan.preparation_sha256,
            evidence_semantics_policy_sha256=policy.policy_sha256,
            relation_requests=assignments,
        ),
    )
    rehearsal = rehearse_relation_execution(ROOT, preparation, plan)
    authorization = build_relation_authorization(
        plan,
        rehearsal,
        repository_state=_state(),
        run_dir=tmp_path / "private-run",
        authorized_at="2026-09-07T00:00:00Z",
        operator_cost_ceiling_usd=15.0,
    )

    prepared = build_relation_gateway_requests(ROOT, preparation, plan, authorization)

    assert rehearsal.request_count == 962
    assert len(prepared) == 962
    assert len({item.request.initial_attempt.request_identity_sha256 for item in prepared}) == 962
    assert all(
        isinstance(item.request.context, ClaimRelationProviderContext)
        and tuple(item.request.context.model_payload())
        == ("claim_text", "claim_type", "visible_evidence")
        for item in prepared
    )


def test_live_preflight_fails_closed_until_every_gate_is_present(tmp_path: Path) -> None:
    plan = _plan()
    rehearsal = _rehearsal(plan)
    run_dir = tmp_path / "private-run"

    blocked = build_relation_preflight(
        plan,
        rehearsal,
        repository_state=_state(clean=False),
        credential_present=False,
        authorization=None,
        run_dir=run_dir,
    )

    assert blocked.status == "claim_relation_execution_live_blocked"
    assert blocked.live_blockers == (
        "repository_not_clean_synchronized_main",
        "credential_missing",
        "authorization_pending",
    )


def test_receipt_separates_provider_and_semantic_failures() -> None:
    payload: dict[str, object] = {
        "schema_version": "claim-relation-execution-receipt/v1",
        "status": "claim_relation_execution_complete_with_technical_failures",
        "authorization_sha256": "1" * 64,
        "plan_sha256": "2" * 64,
        "preparation_sha256": "3" * 64,
        "policy_sha256": "4" * 64,
        "source_commit_ref": "5" * 40,
        "terminal_store_sha256": "6" * 64,
        "relation_result_bundle_sha256": "7" * 64,
        "terminal_request_count": 962,
        "parsed_count": 950,
        "technical_failure_count": 12,
        "provider_terminal_failure_count": 10,
        "semantic_validation_failure_count": 2,
        "provider_attempt_count": 980,
        "gateway_status_counts": {"parsed": 952, "retry_exhausted": 10},
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    receipt = ClaimRelationExecutionReceipt.model_validate(_with_hash(payload, "receipt_sha256"))
    tampered = receipt.model_dump(mode="python")
    tampered["semantic_validation_failure_count"] = 1
    tampered["receipt_sha256"] = canonical_execution_sha256(
        {key: value for key, value in tampered.items() if key != "receipt_sha256"}
    )

    with pytest.raises(ValidationError, match="counts or identity"):
        ClaimRelationExecutionReceipt.model_validate(tampered)


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def binding(self) -> ProviderBinding:
        return cast(ProviderBinding, object())

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        self.calls += 1
        return cast(ProviderEnvelope, call)


def test_provider_attempts_share_one_global_start_interval() -> None:
    adapter = _FakeAdapter()
    moments = iter((0.0, 0.0, 0.25, 1.0, 1.20, 2.0))
    sleeps: list[float] = []
    paced = PacedProviderAdapter(
        adapter,
        minimum_interval_seconds=1.0,
        monotonic=lambda: next(moments),
        sleep=sleeps.append,
    )
    call = cast(ProviderCall, object())

    paced.invoke(call)
    paced.invoke(call)
    paced.invoke(call)

    assert adapter.calls == 3
    assert sleeps == pytest.approx([0.75, 0.8])


def test_provider_pacing_cannot_be_disabled() -> None:
    with pytest.raises(ClaimRelationExecutionError, match="must be positive"):
        PacedProviderAdapter(_FakeAdapter(), minimum_interval_seconds=0.0)


def test_execution_rejects_an_incomplete_census_before_store_creation(
    tmp_path: Path,
) -> None:
    plan = _plan()
    rehearsal = _rehearsal(plan)
    authorization = build_relation_authorization(
        plan,
        rehearsal,
        repository_state=_state(),
        run_dir=tmp_path / "run",
        authorized_at="2026-09-07T00:00:00Z",
        operator_cost_ceiling_usd=15.0,
    )
    preparation = cast(
        RecoveryClaimPoolPreparation,
        SimpleNamespace(
            preparation_sha256=plan.preparation_sha256,
            evidence_semantics_policy_sha256=plan.evidence_semantics_policy_sha256,
            relation_requests=(),
        ),
    )
    store = tmp_path / "attempt-store"

    with pytest.raises(ClaimRelationExecutionError, match="exact census"):
        execute_relation_census(
            preparation,
            (),
            authorization=authorization,
            store_root=store,
            adapter=_FakeAdapter(),
        )

    assert not store.exists()


def test_private_run_must_be_isolated_and_have_known_membership(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    preparation = tmp_path / "recovery" / "preparation.json"
    preparation.parent.mkdir()
    preparation.write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "relation"
    run_dir.mkdir()
    (run_dir / "unexpected.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ClaimRelationExecutionError, match="unknown"):
        checked_relation_run_directory(root, run_dir, preparation)
    with pytest.raises(ClaimRelationExecutionError, match="isolated"):
        checked_relation_run_directory(root, preparation.parent / "child", preparation)


def test_verifier_refuses_incomplete_run_without_creating_a_store(tmp_path: Path) -> None:
    run_dir = tmp_path / "relation"
    run_dir.mkdir()

    with pytest.raises(ClaimRelationExecutionError, match="incomplete or unsafe"):
        verify_relation_execution(
            tmp_path,
            cast(RecoveryClaimPoolPreparation, object()),
            run_dir=run_dir,
        )

    assert tuple(run_dir.iterdir()) == ()


def test_store_membership_check_does_not_repair_a_partial_shard(tmp_path: Path) -> None:
    identity = "a" * 64
    store = tmp_path / "attempt-store"
    shard = store / "requests" / identity
    (shard / "objects" / "sha256").mkdir(parents=True)
    (shard / "requests").mkdir()
    (shard / "terminal").mkdir()
    authority = store / "authorities" / f"{identity}.json"
    authority.parent.mkdir()
    authority.write_text("{}", encoding="utf-8")
    prepared = cast(
        tuple[PreparedRelationRequest, ...],
        (
            SimpleNamespace(
                request=SimpleNamespace(
                    initial_attempt=SimpleNamespace(request_identity_sha256=identity)
                )
            ),
        ),
    )

    with pytest.raises(ClaimRelationExecutionError, match="incomplete or unsafe"):
        _require_complete_relation_store(store, prepared)

    assert not (shard / "failures").exists()

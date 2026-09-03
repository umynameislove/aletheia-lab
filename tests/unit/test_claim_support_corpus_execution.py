from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionError,
    RepositoryExecutionState,
    build_execution_plan,
    build_execution_preflight,
    canonical_json_bytes,
    plan_resume_actions,
    rehearse_execution,
)

ROOT = Path(__file__).resolve().parents[2]


def _repository_state(*, synchronized: bool = True) -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch="main" if synchronized else "research/claim-support-corpus-freeze",
        head_commit="1" * 40,
        origin_main_commit="1" * 40,
        clean=True,
    )


def test_execution_plan_routes_exact_primary_census_without_reserve() -> None:
    plan = build_execution_plan(ROOT)

    assert len(plan.requests) == 360
    assert len({item.request_sha256 for item in plan.requests}) == 360
    assert Counter(item.route for item in plan.requests) == Counter(
        {"model_gateway": 315, "deterministic_local": 45}
    )
    assert {item.variant for item in plan.requests if item.route == "deterministic_local"} == {
        "B0"
    }
    assert all(
        item.maximum_attempts == 2 and item.model_policy_sha256 is not None
        for item in plan.requests
        if item.route == "model_gateway"
    )
    assert plan.reserve_request_count_scheduled == 0
    assert plan.model_snapshot == "gpt-4.1-2025-04-14"
    assert plan.one_attempt_provider_call_ceiling == 315
    assert plan.retry_ceiling_provider_call_count == 630
    assert not plan.provider_calls_executed
    assert not plan.outputs_generated


def test_preflight_names_real_live_blockers_without_opening_outcomes() -> None:
    receipt = build_execution_preflight(
        ROOT,
        repository_state=_repository_state(),
        credential_present=True,
    )

    assert receipt.status == "claim_corpus_rehearsal_ready_live_blocked"
    assert receipt.clean_synchronized_main
    assert receipt.live_blockers == (
        "automatic_relation_assignment_not_implemented",
        "executable_evidence_boundary_not_implemented",
        "variant_execution_authorization_pending",
    )
    assert not receipt.exact_input_token_count_known
    assert not receipt.exact_cost_estimate_available
    assert not receipt.provider_calls_executed
    assert not receipt.outputs_generated
    assert not receipt.claims_materialized
    assert not receipt.human_annotations_collected
    assert not receipt.main_or_sealed_outcomes_opened


def test_preflight_adds_environment_and_repository_blockers() -> None:
    receipt = build_execution_preflight(
        ROOT,
        repository_state=_repository_state(synchronized=False),
        credential_present=False,
    )

    assert "credential_missing" in receipt.live_blockers
    assert "repository_not_clean_synchronized_main" in receipt.live_blockers
    assert not receipt.clean_synchronized_main


def test_resume_skips_terminal_requests_and_rejects_partial_attempts() -> None:
    plan = build_execution_plan(ROOT)
    first = plan.requests[0].request_sha256
    second = plan.requests[1].request_sha256

    actions = plan_resume_actions(plan, {first: "terminal"})
    assert actions[0] == "skip_terminal"
    assert actions[1:] == ("execute",) * 359
    with pytest.raises(ClaimCorpusExecutionError, match="partial request"):
        plan_resume_actions(plan, {second: "partial"})
    with pytest.raises(ClaimCorpusExecutionError, match="unknown request"):
        plan_resume_actions(plan, {"f" * 64: "terminal"})


def test_rehearsal_is_canonical_and_has_no_scientific_output() -> None:
    first = rehearse_execution(ROOT)
    second = rehearse_execution(ROOT)

    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first.initial_execute_count == 360
    assert first.terminal_replay_skip_count == 360
    assert first.partial_state_rejected
    assert first.reserve_request_count_scheduled == 0
    assert not first.provider_calls_executed
    assert not first.outputs_generated
    assert not first.claims_materialized


def test_preflight_module_has_no_provider_adapter_or_network_authority() -> None:
    path = ROOT / "src/aletheia_lab/evaluation/claim_corpus_execution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(module.startswith("aletheia_lab.model_gateway") for module in imported)
    assert "urllib.request" not in imported
    assert "requests" not in imported

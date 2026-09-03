from __future__ import annotations

import ast
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionError,
    RepositoryExecutionState,
    build_execution_plan,
    build_execution_preflight,
    canonical_json_bytes,
    inspect_repository_state,
    plan_resume_actions,
    rehearse_execution,
)
from aletheia_lab.evaluation.claim_evidence_census import (
    ObservedEvidenceCensus,
    build_observed_evidence_census,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    build_evidence_binding,
    build_visible_evidence_item,
)

ROOT = Path(__file__).resolve().parents[2]


def _repository_state(*, synchronized: bool = True) -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch="main" if synchronized else "research/claim-support-corpus-freeze",
        head_commit="1" * 40,
        origin_main_commit="1" * 40,
        clean=True,
    )


def _evidence_census() -> ObservedEvidenceCensus:
    census = ClaimCorpusRequestCensus.model_validate_json(
        (ROOT / "configs/evaluation/claim_support_request_census.json").read_bytes()
    )
    contexts = {}
    for request in census.primary_requests:
        contexts.setdefault((request.family_id, request.evidence_condition), request)
    bindings = tuple(
        build_evidence_binding(
            request,
            items=(
                build_visible_evidence_item(
                    evidence_id=f"observed-{index:02d}",
                    kind="artifact",
                    title=f"Observed context {index:02d}",
                    content=f"Observed development evidence for context {index:02d}.",
                    source_content_sha256=request.request_sha256,
                ),
            ),
            source_projection_sha256=request.request_sha256,
        )
        for index, request in enumerate(contexts.values(), start=1)
    )
    return build_observed_evidence_census(census, bindings)


def test_execution_plan_routes_exact_primary_census_without_reserve() -> None:
    plan = build_execution_plan(ROOT)

    assert len(plan.requests) == 360
    assert len({item.request_sha256 for item in plan.requests}) == 360
    assert Counter(item.route for item in plan.requests) == Counter(
        {"model_gateway": 315, "deterministic_local": 45}
    )
    assert {item.variant for item in plan.requests if item.route == "deterministic_local"} == {"B0"}
    assert all(
        item.maximum_attempts == 2 and item.model_policy_sha256 is not None
        for item in plan.requests
        if item.route == "model_gateway"
    )
    assert plan.reserve_request_count_scheduled == 0
    assert plan.model_snapshot == "gpt-4.1-2025-04-14"
    assert plan.relation_assignment_request_ceiling == 1800
    assert plan.one_attempt_provider_call_ceiling == 2115
    assert plan.retry_ceiling_provider_call_count == 4230
    assert plan.generation_output_token_ceiling == 189000
    assert plan.relation_output_token_ceiling == 1080000
    assert plan.one_attempt_output_token_ceiling == 1269000
    assert plan.retry_output_token_ceiling == 2538000
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
        "observed_evidence_census_pending",
        "variant_execution_authorization_pending",
    )
    assert receipt.relation_assignment_request_ceiling == 1800
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


def test_complete_evidence_census_clears_only_its_live_blocker() -> None:
    receipt = build_execution_preflight(
        ROOT,
        repository_state=_repository_state(),
        credential_present=True,
        evidence_census=_evidence_census(),
    )

    assert receipt.observed_evidence_context_count == 45
    assert receipt.observed_evidence_census_sha256 is not None
    assert "observed_evidence_census_pending" not in receipt.live_blockers
    assert receipt.live_blockers == ("variant_execution_authorization_pending",)


def test_missing_origin_main_is_reported_as_a_blocker_without_losing_preflight() -> None:
    receipt = build_execution_preflight(
        ROOT,
        repository_state=RepositoryExecutionState(
            branch="",
            head_commit="1" * 40,
            origin_main_commit=None,
            clean=True,
        ),
        credential_present=True,
    )

    assert receipt.credential_present
    assert "repository_not_clean_synchronized_main" in receipt.live_blockers
    assert not receipt.clean_synchronized_main
    assert not receipt.provider_calls_executed


def test_repository_inspection_tolerates_checkout_without_origin_main(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "init", "--quiet"), cwd=tmp_path, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Aletheia Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "--quiet",
            "--message=fixture",
        ),
        cwd=tmp_path,
        check=True,
    )

    state = inspect_repository_state(tmp_path)

    assert state.origin_main_commit is None
    assert state.clean
    assert not state.synchronized_main


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

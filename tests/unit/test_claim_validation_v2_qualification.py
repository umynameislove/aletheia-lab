"""Contract tests for the seven-request V2 live qualification boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    build_v2_expressiveness_amendment,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    ClaimValidationV2QualificationError,
    _expected_provider_payload,
    build_qualification_authorization,
    build_qualification_gateway_requests,
    build_qualification_plan,
    build_qualification_preflight,
    checked_qualification_run_directory,
    publish_qualification_result,
    rehearse_qualification,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    PROVIDER_VARIANTS,
    V2QualificationExecutionPlan,
    V2QualificationOutcome,
    V2QualificationReceipt,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_execution import (
    execute_qualification,
    verify_completed_qualification,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
    V2RetryController,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000

    def now_ns(self) -> int:
        self.value += 1_000
        return self.value


class _FakeAdapter:
    def __init__(
        self,
        binding: ProviderBinding,
        payloads: dict[str, dict[str, object]],
        *,
        failing_identity: str | None = None,
    ) -> None:
        self._binding = binding
        self._payloads = payloads
        self._failing_identity = failing_identity

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        if call.request_identity_sha256 == self._failing_identity:
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref=f"ev-{'f' * 64}",
                provider_failure_category="request_rejected",
            )
        raw = canonical_project_json(self._payloads[call.request_identity_sha256]).encode("utf-8")
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=f"ev-{canonical_execution_sha256({'call': call.attempt_id})}",
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _state(*, clean: bool = True, branch: str = "main") -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch=branch,
        head_commit=COMMIT,
        origin_main_commit=COMMIT,
        clean=clean,
    )


def _authorized(tmp_path: Path):
    state = _state()
    plan = build_qualification_plan(ROOT, source_commit_ref=COMMIT)
    rehearsal = rehearse_qualification(ROOT, plan)
    run_dir = checked_qualification_run_directory(ROOT, tmp_path / "qualification")
    authorization = build_qualification_authorization(
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
        authorized_at="2026-09-09T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    run_dir.mkdir()
    publish_qualification_result(run_dir / "authorization.json", authorization)
    prepared = build_qualification_gateway_requests(ROOT, plan, authorization)
    amendment = build_v2_expressiveness_amendment(ROOT)
    probes = {item.qualification_request_sha256: item for item in amendment.qualification_probes}
    payloads = {
        item.request.initial_attempt.request_identity_sha256: _expected_provider_payload(
            probes[item.request_sha256]
        )
        for item in prepared
    }
    return state, plan, rehearsal, authorization, run_dir, prepared, payloads


def test_plan_freezes_exact_equal_seven_request_transport_and_cost() -> None:
    first = build_qualification_plan(ROOT, source_commit_ref=COMMIT)
    second = build_qualification_plan(ROOT, source_commit_ref=COMMIT)

    assert first == second
    assert first.variants == PROVIDER_VARIANTS
    assert first.request_count == 7
    assert len(set(first.qualification_request_sha256s)) == 7
    assert first.maximum_output_tokens_per_request == 2048
    assert first.maximum_provider_attempts_per_request == 2
    assert first.minimum_provider_interval_ms == 1000
    assert first.retry_initial_backoff_ms == 5000
    assert first.estimated_upper_cost_usd < 0.5
    assert first.synthetic_only is True
    assert first.admitted_to_corpus is False


def test_plan_and_outcome_tampering_fail_model_validation() -> None:
    plan = build_qualification_plan(ROOT, source_commit_ref=COMMIT)
    changed = plan.model_dump(mode="python")
    changed["maximum_output_tokens_per_request"] = 600
    with pytest.raises(ValidationError):
        V2QualificationExecutionPlan.model_validate(changed)

    outcome = {
        "variant": "A1",
        "qualification_request_sha256": "1" * 64,
        "gateway_request_identity_sha256": "2" * 64,
        "gateway_status": "provider_failed",
        "attempt_count": 1,
        "first_witness_accepted": True,
        "issue_sha256": "3" * 64,
    }
    with pytest.raises(ValidationError):
        V2QualificationOutcome.model_validate(
            {
                **outcome,
                "outcome_sha256": canonical_execution_sha256(outcome),
            }
        )


def test_rehearsal_proves_exact_witness_and_negative_boundaries() -> None:
    plan = build_qualification_plan(ROOT, source_commit_ref=COMMIT)
    rehearsal = rehearse_qualification(ROOT, plan)

    assert rehearsal.variants == PROVIDER_VARIANTS
    assert rehearsal.exact_first_witness_accepted is True
    assert rehearsal.changed_witness_rejected is True
    assert rehearsal.abstention_rejected_for_qualification is True
    assert rehearsal.unknown_evidence_rejected is True
    assert rehearsal.provider_calls_executed is False


def test_authorization_and_preflight_require_clean_synchronized_main(
    tmp_path: Path,
) -> None:
    plan = build_qualification_plan(ROOT, source_commit_ref=COMMIT)
    rehearsal = rehearse_qualification(ROOT, plan)
    run_dir = checked_qualification_run_directory(ROOT, tmp_path / "run")

    with pytest.raises(ClaimValidationV2QualificationError, match="clean synchronized"):
        build_qualification_authorization(
            plan,
            rehearsal,
            repository_state=_state(clean=False),
            run_dir=run_dir,
            authorized_at="2026-09-09T00:00:00Z",
            operator_cost_ceiling_usd=0.5,
        )

    blocked = build_qualification_preflight(
        plan,
        rehearsal,
        repository_state=_state(branch="feature"),
        credential_present=False,
        authorization=None,
        run_dir=run_dir,
    )
    assert blocked.status.endswith("live_blocked")
    assert blocked.live_blockers == (
        "authorization_pending",
        "credential_missing",
        "repository_not_clean_synchronized_main",
    )


def test_private_run_directory_rejects_repo_and_unknown_artifacts(
    tmp_path: Path,
) -> None:
    with pytest.raises(ClaimValidationV2QualificationError, match="outside"):
        checked_qualification_run_directory(ROOT, ROOT / "qualification")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ClaimValidationV2QualificationError, match="unknown"):
        checked_qualification_run_directory(ROOT, run_dir)

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ClaimValidationV2QualificationError, match="symbolic link"):
        checked_qualification_run_directory(ROOT, linked)


def test_authorized_gateway_requests_bind_only_frozen_synthetic_probes(
    tmp_path: Path,
) -> None:
    _, plan, _, _, _, prepared, _ = _authorized(tmp_path)

    assert tuple(item.request_sha256 for item in prepared) == (plan.qualification_request_sha256s)
    assert len({item.request.initial_attempt.request_identity_sha256 for item in prepared}) == 7
    assert all(item.route == "model_gateway" for item in prepared)
    assert all(
        item.request.initial_attempt.model_policy.visibility == "diagnosis" for item in prepared
    )
    assert all(
        "synthetic V2 transport-and-expressiveness qualification" in item.request.prompt_text
        for item in prepared
    )


def test_seven_valid_provider_outputs_pass_and_verify_independently(
    tmp_path: Path,
) -> None:
    state, _, _, authorization, run_dir, prepared, payloads = _authorized(tmp_path)
    adapter = _FakeAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy),
        payloads,
    )

    receipt = execute_qualification(
        ROOT,
        repository_state=state,
        run_dir=run_dir,
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=adapter,
        clock=_Clock(),
        retry_controller=V2RetryController(sleep=lambda _seconds: None),
    )

    assert receipt.status == "claim_support_validation_v2_qualification_passed"
    assert receipt.parsed_count == 7
    assert receipt.first_witness_accepted_count == 7
    assert receipt.full_cohort_authorization_unlocked is True
    assert receipt.admitted_to_corpus is False
    changed_payload = receipt.identity_payload()
    changed_payload["gateway_status_counts"] = {"provider_failed": 7}
    with pytest.raises(ValidationError):
        V2QualificationReceipt.model_validate(
            {
                **changed_payload,
                "receipt_sha256": canonical_execution_sha256(changed_payload),
            }
        )
    assert verify_completed_qualification(ROOT, repository_state=state, run_dir=run_dir) == receipt
    with pytest.raises(ClaimValidationV2QualificationError, match="already started"):
        execute_qualification(
            ROOT,
            repository_state=state,
            run_dir=run_dir,
            confirm_authorization_sha256=authorization.authorization_sha256,
            adapter=adapter,
            clock=_Clock(),
        )


def test_schema_valid_but_changed_witness_fails_the_entire_gate(
    tmp_path: Path,
) -> None:
    state, _, _, authorization, run_dir, prepared, payloads = _authorized(tmp_path)
    identity = prepared[0].request.initial_attempt.request_identity_sha256
    payloads[identity]["result"]["atomic_claims"][0]["claim_text"] = (
        "payload.observed.probe_score = 0.8"
    )
    adapter = _FakeAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy),
        payloads,
    )

    receipt = execute_qualification(
        ROOT,
        repository_state=state,
        run_dir=run_dir,
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=adapter,
        clock=_Clock(),
        retry_controller=V2RetryController(sleep=lambda _seconds: None),
    )

    assert receipt.status == "claim_support_validation_v2_qualification_failed"
    assert receipt.parsed_count == 7
    assert receipt.first_witness_accepted_count == 6
    assert receipt.semantic_validation_failure_count == 1
    assert receipt.full_cohort_authorization_unlocked is False


def test_one_provider_failure_is_preserved_and_blocks_full_authorization(
    tmp_path: Path,
) -> None:
    state, _, _, authorization, run_dir, prepared, payloads = _authorized(tmp_path)
    failing = prepared[-1].request.initial_attempt.request_identity_sha256
    adapter = _FakeAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy),
        payloads,
        failing_identity=failing,
    )

    receipt = execute_qualification(
        ROOT,
        repository_state=state,
        run_dir=run_dir,
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=adapter,
        clock=_Clock(),
        retry_controller=V2RetryController(sleep=lambda _seconds: None),
    )

    assert receipt.status == "claim_support_validation_v2_qualification_failed"
    assert receipt.parsed_count == 6
    assert receipt.technical_failure_count == 1
    assert receipt.gateway_status_counts == {"parsed": 6, "provider_failed": 1}
    assert receipt.full_cohort_authorization_unlocked is False


def test_receipt_byte_tampering_is_rejected(tmp_path: Path) -> None:
    state, _, _, authorization, run_dir, prepared, payloads = _authorized(tmp_path)
    adapter = _FakeAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy),
        payloads,
    )
    execute_qualification(
        ROOT,
        repository_state=state,
        run_dir=run_dir,
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=adapter,
        clock=_Clock(),
        retry_controller=V2RetryController(sleep=lambda _seconds: None),
    )
    receipt_path = run_dir / "receipt.json"
    payload = V2QualificationReceipt.model_validate_json(receipt_path.read_bytes())
    receipt_path.write_bytes(
        receipt_path.read_bytes().replace(payload.receipt_sha256.encode(), b"0" * 64)
    )

    with pytest.raises(ClaimValidationV2QualificationError, match="receipt"):
        verify_completed_qualification(ROOT, repository_state=state, run_dir=run_dir)

"""Complete synthetic flow through visibility, fake execution, storage, and closeout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.context.evaluation_context import (
    ContextBoundaryError,
    EvaluationContextPayload,
    build_evaluation_context,
)
from aletheia_lab.evaluation.attempt_store import ImmutableAttemptStore
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
)
from aletheia_lab.evaluation.structural_closeout import (
    StructuralAuthorizationCheck,
    StructuralCloseoutPlan,
    StructuralCloseoutReceipt,
    StructuralRequestExpectation,
    reduce_structural_closeout,
)
from aletheia_lab.model_gateway import (
    DeterministicFakeAdapter,
    FakeFixture,
    FakeStep,
    GatewayExecutionResult,
    GatewayRequest,
    ProviderBinding,
    RuntimePolicyReference,
    UsageMetadata,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import (
    canonical_project_json,
    canonical_project_sha256,
    content_sha256,
)
from aletheia_lab.project.regression import ProjectEvidenceView, ProjectEvidenceViewItem

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


def _sha(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha(character)}"


class _Clock:
    def __init__(self, *, step: int = 1) -> None:
        self._value = 0
        self._step = step

    def now_ns(self) -> int:
        value = self._value
        self._value += self._step
        return value


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


@dataclass(frozen=True)
class _Inputs:
    manifest: EvaluationManifestReference
    case: EvaluationCaseReference
    context: EvaluationContextPayload
    request: GatewayRequest


def _manifest(
    *, project_character: str = "1", snapshot_character: str = "2"
) -> EvaluationManifestReference:
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{_sha(project_character)}",
        snapshot_id=f"p3-snapshot-{_sha(snapshot_character)}",
        manifest_content_sha256=_sha("3"),
        source_commit_ref="4" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("5"),
        provenance_sha256=_sha("6"),
        created_at="2026-08-30T00:00:00Z",
        frozen_at="2026-08-30T00:00:01Z",
        visibility="diagnosis",
    )


def _view(
    *,
    project_character: str = "1",
    case_character: str = "7",
    source_id: str = "p3-source-visible",
) -> ProjectEvidenceView:
    item = ProjectEvidenceViewItem(
        evidence_id=f"p3-evidence-{_sha(case_character)}",
        role="metric_change",
        source_id=source_id,
        source_sha256=_sha(case_character),
        provenance_links=(),
    )
    project_id = f"p3-project-{_sha(project_character)}"
    evidence_bundle_id = f"p3-evidence-bundle-{_sha(case_character)}"
    payload = {
        "schema_version": "project-evidence-view/v1",
        "evidence_bundle_id": evidence_bundle_id,
        "project_id": project_id,
        "items": [item.model_dump(mode="json")],
    }
    return ProjectEvidenceView(
        schema_version="project-evidence-view/v1",
        evidence_bundle_id=evidence_bundle_id,
        project_id=project_id,
        items=(item,),
        view_sha256=canonical_project_sha256(payload),
    )


def _case(
    manifest: EvaluationManifestReference,
    view: ProjectEvidenceView,
    *,
    case_character: str = "7",
) -> EvaluationCaseReference:
    return EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque(case_character),
        family_id=_opaque(case_character),
        mechanism_id=_opaque(case_character),
        dataset_id=_opaque(case_character),
        variant_id=_opaque(case_character),
        variant_content_sha256=_sha(case_character),
        case_content_sha256=_sha(case_character),
        evidence_bundle_id=view.evidence_bundle_id,
        evidence_content_sha256=_sha(case_character),
        lineage_graph_id=f"p3-lineage-graph-{_sha(case_character)}",
        lineage_sha256=_sha(case_character),
        visibility_projection_sha256=view.view_sha256,
        provenance_sha256=_sha(case_character),
        visibility="diagnosis",
    )


def _inputs(
    *,
    project_character: str = "1",
    snapshot_character: str = "2",
    case_character: str = "7",
) -> _Inputs:
    manifest = _manifest(
        project_character=project_character,
        snapshot_character=snapshot_character,
    )
    view = _view(
        project_character=project_character,
        case_character=case_character,
    )
    case = _case(manifest, view, case_character=case_character)
    context = build_evaluation_context(
        case=case,
        evidence_view=view,
        selected_evidence_ids=(view.items[0].evidence_id,),
    )
    schema_json = canonical_project_json(_SCHEMA)
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=_sha("4"),
        provider_ref=_opaque("5"),
        model_ref=_opaque("6"),
        model_version_ref=_opaque("7"),
        resource_policy_ref=_opaque("8"),
        prompt_policy_ref=_opaque("9"),
        response_schema_sha256=content_sha256(schema_json.encode()),
        provenance_sha256=_sha("a"),
        visibility="diagnosis",
    )
    runtime_policy = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque("b"),
        timeout_ns=1_000_000_000,
        max_attempts=2,
        max_response_bytes=256,
        provenance_sha256=_sha("c"),
    )
    request = prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=model_policy,
        context=context,
        prompt_text="synthetic neutral prompt",
        response_schema=_SCHEMA,
        runtime_policy=runtime_policy,
    )
    return _Inputs(manifest=manifest, case=case, context=context, request=request)


def _usage() -> UsageMetadata:
    return UsageMetadata(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_amount=None,
        cost_currency_ref=None,
    )


def _execute(
    inputs: _Inputs,
    steps: tuple[FakeStep, ...],
    *,
    clock_step: int = 1,
) -> GatewayExecutionResult:
    adapter = DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(inputs.request.initial_attempt.model_policy),
        fixtures=(
            FakeFixture(
                request_identity_sha256=(inputs.request.initial_attempt.request_identity_sha256),
                steps=steps,
            ),
        ),
    )
    return execute_gateway_request(
        inputs.request,
        adapter=adapter,
        clock=_Clock(step=clock_step),
        cancellation=_NeverCancelled(),
    )


def _publish(
    store: ImmutableAttemptStore,
    inputs: _Inputs,
    result: GatewayExecutionResult,
) -> None:
    store.prepare(inputs.request)
    store.start(inputs.request)
    for attempt in result.attempts:
        store.record_attempt(inputs.request, attempt)
    if result.raw_response is not None:
        store.record_response(inputs.request, result)
    store.record_parsed_or_failed(inputs.request, result)
    store.mark_closeout_pending(inputs.request, result)
    store.publish_terminal(inputs.request, result)


def _plan(
    inputs: _Inputs,
    *,
    authorized: bool = True,
    attempt_count: int = 1,
) -> StructuralCloseoutPlan:
    authorization = StructuralAuthorizationCheck(
        verification_ref=_opaque("e"),
        manifest_reference_id=inputs.manifest.reference_id,
        manifest_content_sha256=inputs.manifest.manifest_content_sha256,
        authorization_ref=inputs.manifest.authorization_ref,
        authorization_valid=authorized,
    )
    expectation = StructuralRequestExpectation.build(
        attempt=inputs.request.initial_attempt,
        retry_policy_ref=inputs.request.runtime_policy.retry_policy_ref,
        expected_attempt_count=attempt_count,
    )
    return StructuralCloseoutPlan.build(
        manifest=inputs.manifest,
        authorization_check=authorization,
        requests=(expectation,),
    )


@pytest.mark.parametrize(
    ("steps", "clock_step", "expected_status", "expected_mode"),
    [
        (
            (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
            1,
            "parsed",
            "structured",
        ),
        (
            (FakeStep(kind="abstention", raw_content=b'{"value":"none"}', usage=_usage()),),
            1,
            "parsed",
            "abstention",
        ),
        (
            (FakeStep(kind="malformed_response", raw_content=b"{", usage=_usage()),),
            1,
            "parse_failed",
            "structured",
        ),
        (
            (
                FakeStep(kind="timeout", raw_content=None, usage=None),
                FakeStep(kind="timeout", raw_content=None, usage=None),
            ),
            1,
            "retry_exhausted",
            None,
        ),
    ],
)
def test_synthetic_outcomes_remain_complete_and_uninterpreted(
    tmp_path: Path,
    steps: tuple[FakeStep, ...],
    clock_step: int,
    expected_status: str,
    expected_mode: str | None,
) -> None:
    inputs = _inputs()
    result = _execute(inputs, steps, clock_step=clock_step)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _publish(store, inputs, result)
    receipt = reduce_structural_closeout(
        _plan(inputs, attempt_count=len(result.attempts)),
        store=store,
    )

    assert result.status == expected_status
    assert receipt.structural_state == "complete_uninterpreted"
    assert receipt.requests[0].gateway_status == expected_status
    assert receipt.requests[0].response_mode == expected_mode
    assert receipt.reconciled_request_count == 1
    assert receipt.receipt_id == f"ev-{receipt.receipt_sha256}"
    request_receipt = receipt.requests[0]
    if expected_status == "parsed":
        assert request_receipt.raw_response_sha256 is not None
        assert request_receipt.parsed_response_sha256 is not None
        assert request_receipt.issue_sha256 is None
    elif expected_status == "parse_failed":
        assert request_receipt.raw_response_sha256 is not None
        assert request_receipt.parsed_response_sha256 is None
        assert request_receipt.issue_sha256 is not None
    else:
        assert request_receipt.raw_response_sha256 is None
        assert request_receipt.parsed_response_sha256 is None
        assert request_receipt.issue_sha256 is not None


def test_visibility_and_cross_project_injections_stop_before_gateway() -> None:
    manifest = _manifest()
    leaky_view = _view(source_id="hidden_ground_truth")
    leaky_case = _case(manifest, leaky_view)
    with pytest.raises(ContextBoundaryError, match="context_boundary"):
        build_evaluation_context(
            case=leaky_case,
            evidence_view=leaky_view,
            selected_evidence_ids=(leaky_view.items[0].evidence_id,),
        )

    foreign_view = _view(project_character="9")
    foreign_case = _case(manifest, foreign_view)
    with pytest.raises(ContextBoundaryError) as captured:
        build_evaluation_context(
            case=foreign_case,
            evidence_view=foreign_view,
            selected_evidence_ids=(foreign_view.items[0].evidence_id,),
        )
    assert captured.value.issue.code == "cross_project_evidence"


def test_unauthorized_manifest_cannot_reach_request_preparation() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["authorization_state"] = "not_authorized"

    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(payload)


def test_replay_is_idempotent_and_partial_store_never_closes_out(tmp_path: Path) -> None:
    inputs = _inputs()
    result = _execute(
        inputs,
        (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
    )
    store = ImmutableAttemptStore(tmp_path / "replay", clock=_Clock())
    store.prepare(inputs.request)
    store.start(inputs.request)
    created = store.record_attempt(inputs.request, result.attempts[0])
    replay = store.record_attempt(inputs.request, result.attempts[0])
    assert created.counted_attempt and not replay.counted_attempt
    assert replay.disposition == "idempotent"

    partial = ImmutableAttemptStore(tmp_path / "partial", clock=_Clock())
    partial.prepare(inputs.request)
    partial.start(inputs.request)
    partial.record_attempt(inputs.request, result.attempts[0])
    partial.record_response(inputs.request, result)
    receipt = reduce_structural_closeout(_plan(inputs), store=partial)
    assert receipt.structural_state == "incomplete"
    assert not partial.is_terminal(result.request_identity_sha256)


def test_tamper_current_authorization_and_cross_snapshot_fail_closed(tmp_path: Path) -> None:
    inputs = _inputs()
    result = _execute(
        inputs,
        (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
    )
    store = ImmutableAttemptStore(tmp_path / "main", clock=_Clock())
    _publish(store, inputs, result)

    unauthorized = reduce_structural_closeout(
        _plan(inputs, authorized=False),
        store=store,
    )
    assert unauthorized.structural_state == "not_authorized"

    object_path = next(
        path for path in (tmp_path / "main" / "objects" / "sha256").glob("*/*") if path.is_file()
    )
    object_path.write_bytes(b"forged")
    forged = reduce_structural_closeout(_plan(inputs), store=store)
    assert forged.structural_state == "technical_failure"

    expected = _inputs(case_character="7")
    foreign = _inputs(snapshot_character="9", case_character="8")
    mixed = ImmutableAttemptStore(tmp_path / "mixed", clock=_Clock())
    _publish(
        mixed,
        expected,
        _execute(
            expected,
            (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
        ),
    )
    _publish(
        mixed,
        foreign,
        _execute(
            foreign,
            (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
        ),
    )
    cross_snapshot = reduce_structural_closeout(_plan(expected), store=mixed)
    assert cross_snapshot.structural_state == "invalid_provenance"
    assert "unexpected_terminal_cross_manifest" in {
        finding.code for finding in cross_snapshot.findings
    }


def test_unexpected_same_manifest_terminal_is_duplicate_or_replay(tmp_path: Path) -> None:
    expected = _inputs(case_character="7")
    unexpected = _inputs(case_character="8")
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    for inputs in (expected, unexpected):
        result = _execute(
            inputs,
            (FakeStep(kind="valid_response", raw_content=b'{"value":"ok"}', usage=_usage()),),
        )
        _publish(store, inputs, result)

    receipt: StructuralCloseoutReceipt = reduce_structural_closeout(
        _plan(expected),
        store=store,
    )
    assert receipt.structural_state == "duplicate_or_replay"

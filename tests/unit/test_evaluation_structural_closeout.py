"""Offline structural closeout tests with no scientific interpretation."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Literal

import pytest

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.attempt_store import ImmutableAttemptStore
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.structural_closeout import (
    StructuralAuthorizationCheck,
    StructuralCloseoutPlan,
    StructuralCloseoutReceipt,
    StructuralRequestExpectation,
    StructuralRequestReceipt,
    assert_no_scientific_closeout_fields,
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
from aletheia_lab.project.identity import canonical_project_json, content_sha256

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
    def __init__(self, *, start: int = 0) -> None:
        self._value = start

    def now_ns(self) -> int:
        value = self._value
        self._value += 1
        return value


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _manifest(
    *,
    content_character: str = "3",
    snapshot_character: str = "2",
) -> EvaluationManifestReference:
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{_sha('1')}",
        snapshot_id=f"p3-snapshot-{_sha(snapshot_character)}",
        manifest_content_sha256=_sha(content_character),
        source_commit_ref="4" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("5"),
        provenance_sha256=_sha("6"),
        created_at="2026-08-30T00:00:00Z",
        frozen_at="2026-08-30T00:00:01Z",
        visibility="diagnosis",
    )


def _request(
    manifest: EvaluationManifestReference,
    *,
    case_character: str,
    prompt: str,
) -> GatewayRequest:
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque(case_character),
        family_id=_opaque(case_character),
        mechanism_id=_opaque(case_character),
        dataset_id=_opaque(case_character),
        variant_id=_opaque(case_character),
        variant_content_sha256=_sha(case_character),
        case_content_sha256=_sha(case_character),
        evidence_bundle_id=f"p3-evidence-bundle-{_sha(case_character)}",
        evidence_content_sha256=_sha(case_character),
        lineage_graph_id=f"p3-lineage-graph-{_sha(case_character)}",
        lineage_sha256=_sha(case_character),
        visibility_projection_sha256=_sha(case_character),
        provenance_sha256=_sha(case_character),
        visibility="diagnosis",
    )
    schema_json = canonical_project_json(_SCHEMA)
    policy = ModelPolicyReference.build(
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
    context_fields = {
        "schema_version": "evaluation-context/v1",
        "project_id": case.project_id,
        "snapshot_id": case.snapshot_id,
        "case_reference_id": case.reference_id,
        "case_id": case.case_id,
        "evidence_bundle_id": case.evidence_bundle_id,
        "visibility_projection_sha256": case.visibility_projection_sha256,
        "selected_evidence": [],
        "omitted_evidence_ids": [],
    }
    context_sha = canonical_execution_sha256(context_fields)
    context = EvaluationContextPayload(
        context_id=f"ev-{context_sha}",
        context_sha256=context_sha,
        project_id=case.project_id,
        snapshot_id=case.snapshot_id,
        case_reference_id=case.reference_id,
        case_id=case.case_id,
        evidence_bundle_id=case.evidence_bundle_id,
        visibility_projection_sha256=case.visibility_projection_sha256,
        selected_evidence=(),
        omitted_evidence_ids=(),
    )
    runtime_policy = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=policy,
        retry_policy_ref=_opaque("b"),
        timeout_ns=100,
        max_attempts=2,
        max_response_bytes=256,
        provenance_sha256=_sha("c"),
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=policy,
        context=context,
        prompt_text=prompt,
        response_schema=_SCHEMA,
        runtime_policy=runtime_policy,
    )


def _usage() -> UsageMetadata:
    return UsageMetadata(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_amount=None,
        cost_currency_ref=None,
    )


def _result(
    request: GatewayRequest,
    *,
    kind: Literal["valid_response", "abstention", "malformed_response"] = ("valid_response"),
) -> GatewayExecutionResult:
    raw = b"{" if kind == "malformed_response" else b'{"value":"ok"}'
    adapter = DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
        fixtures=(
            FakeFixture(
                request_identity_sha256=request.initial_attempt.request_identity_sha256,
                steps=(FakeStep(kind=kind, raw_content=raw, usage=_usage()),),
            ),
        ),
    )
    return execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_NeverCancelled(),
    )


def _record_terminal(
    store: ImmutableAttemptStore,
    request: GatewayRequest,
    result: GatewayExecutionResult,
) -> None:
    store.prepare(request)
    store.start(request)
    for attempt in result.attempts:
        store.record_attempt(request, attempt)
    if result.raw_response is not None:
        store.record_response(request, result)
    store.record_parsed_or_failed(request, result)
    store.mark_closeout_pending(request, result)
    store.publish_terminal(request, result)


def _authorization(
    manifest: EvaluationManifestReference,
    *,
    valid: bool = True,
) -> StructuralAuthorizationCheck:
    return StructuralAuthorizationCheck(
        verification_ref=_opaque("e"),
        manifest_reference_id=manifest.reference_id,
        manifest_content_sha256=manifest.manifest_content_sha256,
        authorization_ref=manifest.authorization_ref,
        authorization_valid=valid,
    )


def _expectation(
    request: GatewayRequest,
    *,
    attempt_count: int,
) -> StructuralRequestExpectation:
    return StructuralRequestExpectation.build(
        attempt=request.initial_attempt,
        retry_policy_ref=request.runtime_policy.retry_policy_ref,
        expected_attempt_count=attempt_count,
    )


def _plan(
    manifest: EvaluationManifestReference,
    expectations: tuple[StructuralRequestExpectation, ...],
    *,
    authorized: bool = True,
) -> StructuralCloseoutPlan:
    return StructuralCloseoutPlan.build(
        manifest=manifest,
        authorization_check=_authorization(manifest, valid=authorized),
        requests=expectations,
    )


def test_complete_store_has_deterministic_permutation_invariant_read_only_receipt(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    first_request = _request(manifest, case_character="7", prompt="first")
    second_request = _request(manifest, case_character="8", prompt="second")
    first_result = _result(first_request)
    second_result = _result(second_request, kind="abstention")
    store = ImmutableAttemptStore(tmp_path, clock=_Clock(start=100))
    _record_terminal(store, first_request, first_result)
    _record_terminal(store, second_request, second_result)
    first_expected = _expectation(first_request, attempt_count=1)
    second_expected = _expectation(second_request, attempt_count=1)
    forward = _plan(manifest, (first_expected, second_expected))
    reverse = _plan(manifest, (second_expected, first_expected))
    store_before = store.store_sha256()
    plan_before = forward.model_dump_json()

    first_receipt = reduce_structural_closeout(forward, store=store)
    second_receipt = reduce_structural_closeout(reverse, store=store)

    assert first_receipt == second_receipt
    assert first_receipt.structural_state == "complete_uninterpreted"
    assert first_receipt.reconciled_request_count == 2
    abstention_receipt = next(
        item
        for item in first_receipt.requests
        if item.request_identity_sha256 == second_request.initial_attempt.request_identity_sha256
    )
    assert abstention_receipt.response_mode == "abstention"
    assert store.store_sha256() == store_before
    assert forward.model_dump_json() == plan_before
    serialized = first_receipt.model_dump_json()
    for forbidden in (
        "threshold",
        "denominator",
        "primary_outcome",
        "scientific_pass",
        "scientific_fail",
        "admitted",
        "superior",
        "generalizable",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "partial_state",
    ("started", "attempt_recorded", "response_recorded"),
)
def test_partial_request_is_incomplete_without_reading_it_as_terminal(
    tmp_path: Path,
    partial_state: str,
) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="partial")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)
    if partial_state in {"attempt_recorded", "response_recorded"}:
        store.record_attempt(request, result.attempts[0])
    if partial_state == "response_recorded":
        store.record_response(request, result)
    plan = _plan(manifest, (_expectation(request, attempt_count=1),))

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "incomplete"
    assert receipt.observed_terminal_count == 0
    assert receipt.requests[0].reconciliation == "missing"


def test_unexpected_same_manifest_terminal_is_duplicate_or_replay(tmp_path: Path) -> None:
    manifest = _manifest()
    expected_request = _request(manifest, case_character="7", prompt="expected")
    duplicate_request = _request(manifest, case_character="8", prompt="unexpected")
    expected_result = _result(expected_request)
    duplicate_result = _result(duplicate_request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, expected_request, expected_result)
    _record_terminal(store, duplicate_request, duplicate_result)
    plan = _plan(
        manifest,
        (_expectation(expected_request, attempt_count=len(expected_result.attempts)),),
    )

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "duplicate_or_replay"
    assert {finding.code for finding in receipt.findings} == {"unexpected_terminal_same_manifest"}


def test_terminal_attempt_count_mismatch_is_incomplete(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="attempt-count")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    plan = _plan(manifest, (_expectation(request, attempt_count=2),))

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "incomplete"
    assert receipt.requests[0].reconciliation == "mismatch"
    assert "attempt_count_mismatch" in {item.code for item in receipt.findings}


def test_cross_manifest_terminal_is_invalid_provenance(tmp_path: Path) -> None:
    manifest = _manifest()
    foreign_manifest = _manifest(content_character="d", snapshot_character="9")
    request = _request(manifest, case_character="7", prompt="expected")
    foreign_request = _request(foreign_manifest, case_character="8", prompt="foreign")
    result = _result(request)
    foreign_result = _result(foreign_request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    _record_terminal(store, foreign_request, foreign_result)
    plan = _plan(manifest, (_expectation(request, attempt_count=1),))

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "invalid_provenance"
    assert "unexpected_terminal_cross_manifest" in {finding.code for finding in receipt.findings}


def test_manifest_supplied_context_equality_rule_is_enforced(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="context-binding")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    expected = _expectation(request, attempt_count=1)
    payload = expected.model_dump(mode="json")
    payload["context_sha256"] = _sha("f")
    identity_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"expectation_id", "expectation_sha256"}
    }
    forged_sha = canonical_execution_sha256(identity_payload)
    payload["expectation_id"] = f"ev-{forged_sha}"
    payload["expectation_sha256"] = forged_sha
    forged = StructuralRequestExpectation.model_validate(payload)
    plan = _plan(manifest, (forged,))

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "invalid_provenance"
    assert "context_mismatch" in {item.code for item in receipt.findings}


def test_corrupt_content_addressed_object_is_technical_failure(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="corrupt")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    plan = _plan(manifest, (_expectation(request, attempt_count=1),))
    object_path = next(
        path for path in (tmp_path / "objects" / "sha256").glob("*/*") if path.is_file()
    )
    object_path.write_bytes(b"forged")

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "technical_failure"
    assert receipt.store_sha256 is None
    assert "store_technical_failure" in {item.code for item in receipt.findings}


def test_parse_failure_remains_reconciled_and_uninterpreted(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="malformed")
    result = _result(request, kind="malformed_response")
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    plan = _plan(manifest, (_expectation(request, attempt_count=1),))

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "complete_uninterpreted"
    assert receipt.requests[0].gateway_status == "parse_failed"
    assert receipt.requests[0].parsed_response_sha256 is None
    assert receipt.requests[0].issue_sha256 is not None


def test_current_authorization_is_explicit_and_can_block_closeout(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="authorization")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    plan = _plan(
        manifest,
        (_expectation(request, attempt_count=1),),
        authorized=False,
    )

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "not_authorized"
    assert "authorization_not_current" in {item.code for item in receipt.findings}


def test_structural_contract_rejects_forged_expectations_and_empty_plans() -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="contract")
    expectation = _expectation(request, attempt_count=1)

    forged_expectation = expectation.model_dump(mode="python")
    forged_expectation["expectation_sha256"] = _sha("0")
    with pytest.raises(ValueError, match="expectation identity"):
        StructuralRequestExpectation.model_validate(forged_expectation)

    plan = _plan(manifest, (expectation,))
    empty_plan = plan.model_dump(mode="python")
    empty_plan["requests"] = ()
    with pytest.raises(ValueError, match="requires expected requests"):
        StructuralCloseoutPlan.model_validate(empty_plan)


def test_structural_receipt_rejects_inconsistent_counts_and_artifacts(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="receipt-contract")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    receipt = reduce_structural_closeout(
        _plan(manifest, (_expectation(request, attempt_count=1),)),
        store=store,
    )

    forged_count = receipt.model_dump(mode="python")
    forged_count["expected_request_count"] = 2
    with pytest.raises(ValueError, match="expected request count"):
        StructuralCloseoutReceipt.model_validate(forged_count)

    forged_identity = receipt.model_dump(mode="python")
    forged_identity["receipt_id"] = _opaque("0")
    with pytest.raises(ValueError, match="receipt identity"):
        StructuralCloseoutReceipt.model_validate(forged_identity)

    missing = StructuralRequestReceipt(
        request_identity_sha256=request.initial_attempt.request_identity_sha256,
        reconciliation="missing",
        expected_attempt_count=1,
        observed_attempt_count=None,
        terminal_inventory_sha256=None,
        gateway_status=None,
        response_mode=None,
        raw_response_sha256=None,
        parsed_response_sha256=None,
        issue_sha256=None,
    )
    forged_missing = missing.model_dump(mode="python")
    forged_missing["observed_attempt_count"] = 1
    with pytest.raises(ValueError, match="cannot contain terminal inventory"):
        StructuralRequestReceipt.model_validate(forged_missing)

    with pytest.raises(ValueError, match="forbidden scientific"):
        assert_no_scientific_closeout_fields({"threshold": "not-authorized"})


def test_reducer_does_not_open_network_or_reinvoke_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    request = _request(manifest, case_character="7", prompt="offline")
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_terminal(store, request, result)
    plan = _plan(manifest, (_expectation(request, attempt_count=1),))

    def forbidden_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline reducer attempted network access")

    def forbidden_provider(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline reducer attempted provider invocation")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    monkeypatch.setattr(DeterministicFakeAdapter, "invoke", forbidden_provider)

    receipt = reduce_structural_closeout(plan, store=store)

    assert receipt.structural_state == "complete_uninterpreted"

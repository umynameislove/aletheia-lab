"""Reusable conformance tests for the provider-neutral deterministic runtime."""

from __future__ import annotations

import builtins
import os
import socket
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    DeterministicFakeAdapter,
    FakeFixture,
    FakeStep,
    GatewayContractError,
    GatewayExecutionResult,
    GatewayRequest,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RuntimePolicyReference,
    UsageMetadata,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "abstain": {"type": "boolean"},
        "value": {"type": "string"},
    },
}


def _sha(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha(character)}"


def _unknown_usage() -> UsageMetadata:
    return UsageMetadata(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_amount=None,
        cost_currency_ref=None,
    )


def _known_usage() -> UsageMetadata:
    return UsageMetadata(
        input_tokens=4,
        output_tokens=2,
        total_tokens=6,
        cost_amount=Decimal("0.125"),
        cost_currency_ref=_opaque("d"),
    )


def _request(
    *,
    max_attempts: int = 1,
    timeout_ns: int = 1_000_000_000,
    max_response_bytes: int = 256,
) -> GatewayRequest:
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{_sha('1')}",
        snapshot_id=f"p3-snapshot-{_sha('2')}",
        manifest_content_sha256=_sha("3"),
        source_commit_ref="4" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("5"),
        provenance_sha256=_sha("6"),
        created_at="2026-08-30T00:00:00Z",
        frozen_at="2026-08-30T00:00:01Z",
        visibility="diagnosis",
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque("7"),
        family_id=_opaque("8"),
        mechanism_id=_opaque("9"),
        dataset_id=_opaque("a"),
        variant_id=_opaque("b"),
        variant_content_sha256=_sha("c"),
        case_content_sha256=_sha("d"),
        evidence_bundle_id=f"p3-evidence-bundle-{_sha('e')}",
        evidence_content_sha256=_sha("f"),
        lineage_graph_id=f"p3-lineage-graph-{_sha('0')}",
        lineage_sha256=_sha("1"),
        visibility_projection_sha256=_sha("2"),
        provenance_sha256=_sha("3"),
        visibility="diagnosis",
    )
    schema_text = canonical_project_json(_SCHEMA)
    policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=_sha("4"),
        provider_ref=_opaque("5"),
        model_ref=_opaque("6"),
        model_version_ref=_opaque("7"),
        resource_policy_ref=_opaque("8"),
        prompt_policy_ref=_opaque("9"),
        response_schema_sha256=content_sha256(schema_text.encode()),
        provenance_sha256=_sha("a"),
        visibility="diagnosis",
    )
    context_payload = {
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
    context_sha256 = canonical_execution_sha256(context_payload)
    context = EvaluationContextPayload(
        context_id=f"ev-{context_sha256}",
        context_sha256=context_sha256,
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
        timeout_ns=timeout_ns,
        max_attempts=max_attempts,
        max_response_bytes=max_response_bytes,
        provenance_sha256=_sha("c"),
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=policy,
        context=context,
        prompt_text="opaque fixture prompt",
        response_schema=_SCHEMA,
        runtime_policy=runtime_policy,
    )


class _Clock:
    def __init__(self, step: int = 1) -> None:
        self._value = 0
        self._step = step

    def now_ns(self) -> int:
        value = self._value
        self._value += self._step
        return value


class _Cancellation:
    def __init__(self, values: tuple[bool, ...] = (False,)) -> None:
        self._values = values
        self._index = 0

    def is_cancelled(self) -> bool:
        index = min(self._index, len(self._values) - 1)
        self._index += 1
        return self._values[index]


def _response(
    kind: Literal[
        "valid_response",
        "abstention",
        "malformed_response",
        "empty_response",
        "response_mutation",
        "oversized_response",
    ],
    raw: bytes = b'{"value":"ok"}',
    *,
    usage: UsageMetadata | None = None,
) -> FakeStep:
    return FakeStep(
        kind=kind,
        raw_content=None if kind == "empty_response" else raw,
        usage=usage or _unknown_usage(),
    )


def _error(
    kind: Literal["timeout", "transient_error", "permanent_error", "cancelled"],
) -> FakeStep:
    return FakeStep(kind=kind, raw_content=None, usage=None)


def _adapter(request: GatewayRequest, steps: tuple[FakeStep, ...]) -> DeterministicFakeAdapter:
    return DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
        fixtures=(
            FakeFixture(
                request_identity_sha256=request.initial_attempt.request_identity_sha256,
                steps=steps,
            ),
        ),
    )


def _execute(
    request: GatewayRequest,
    steps: tuple[FakeStep, ...],
    *,
    clock_step: int = 1,
    cancellation: _Cancellation | None = None,
) -> GatewayExecutionResult:
    return execute_gateway_request(
        request,
        adapter=_adapter(request, steps),
        clock=_Clock(clock_step),
        cancellation=cancellation or _Cancellation(),
    )


def assert_adapter_conformance(adapter: ProviderAdapter, request: GatewayRequest) -> None:
    """Reusable minimum conformance assertion for any neutral adapter fixture."""

    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )
    assert result.status == "parsed"
    assert result.raw_response is not None
    assert result.parsed_response is not None
    assert result.raw_response.artifact_ref != result.parsed_response.artifact_ref
    assert result.attempts[0].attempt.request_identity_sha256 == result.request_identity_sha256


def test_valid_response_conforms_and_keeps_unknown_usage_unknown() -> None:
    request = _request()
    adapter = _adapter(request, (_response("valid_response"),))

    assert_adapter_conformance(adapter, request)
    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )

    assert result.attempts[0].usage == _unknown_usage()
    assert result.attempts[0].usage is not None
    assert result.attempts[0].usage.total_tokens is None


def test_known_usage_is_structured_and_exact() -> None:
    result = _execute(_request(), (_response("valid_response", usage=_known_usage()),))

    assert result.attempts[0].usage == _known_usage()


def test_inconsistent_known_token_total_is_rejected() -> None:
    with pytest.raises(ValidationError, match="total token"):
        UsageMetadata(
            input_tokens=4,
            output_tokens=2,
            total_tokens=7,
            cost_amount=None,
            cost_currency_ref=None,
        )


def test_explicit_abstention_is_retained_without_scientific_disposition() -> None:
    result = _execute(
        _request(),
        (_response("abstention", b'{"abstain":true}'),),
    )

    assert result.status == "parsed"
    assert result.attempts[0].response_mode == "abstention"
    assert result.parsed_response is not None
    assert result.parsed_response.payload == {"abstain": True}


@pytest.mark.parametrize(
    "step",
    [
        _response("malformed_response", b'{"secret":"SYNTHETIC_SECRET"'),
        _response("empty_response"),
        _response("malformed_response", b"[]"),
        _response("malformed_response", b'{"value":12}'),
        _response("malformed_response", b'{"value":"a","value":"b"}'),
    ],
)
def test_parse_and_schema_failures_remain_visible_and_public_safe(step: FakeStep) -> None:
    result = _execute(_request(), (step,))

    assert result.status == "parse_failed"
    assert result.raw_response is not None
    assert result.parsed_response is None
    assert result.issue is not None
    assert result.issue.code == "response_parse_failure"
    assert "SYNTHETIC_SECRET" not in result.issue.model_dump_json()


def test_transient_error_retries_with_only_ordinal_changed() -> None:
    request = _request(max_attempts=3)
    result = _execute(
        request,
        (_error("transient_error"), _response("valid_response")),
    )

    assert result.status == "parsed"
    assert [record.attempt.attempt_ordinal for record in result.attempts] == [1, 2]
    assert {record.attempt.request_identity_sha256 for record in result.attempts} == {
        request.initial_attempt.request_identity_sha256
    }
    assert result.attempts[0].attempt.model_policy == result.attempts[1].attempt.model_policy
    assert result.attempts[0].attempt.context_sha256 == result.attempts[1].attempt.context_sha256
    assert result.attempts[0].attempt.manifest == result.attempts[1].attempt.manifest
    assert result.attempts[0].attempt.case == result.attempts[1].attempt.case
    assert (
        result.attempts[0].attempt.model_policy.resource_policy_ref
        == result.attempts[1].attempt.model_policy.resource_policy_ref
    )


def test_retry_exhaustion_never_mints_success() -> None:
    request = _request(max_attempts=3)
    result = _execute(request, (_error("transient_error"),) * 3)

    assert result.status == "retry_exhausted"
    assert len(result.attempts) == 3
    assert result.raw_response is None
    assert result.parsed_response is None
    assert result.issue is not None and result.issue.code == "retry_exhausted"


@pytest.mark.parametrize(
    ("step", "status"),
    [
        (_error("timeout"), "timed_out"),
        (_error("permanent_error"), "provider_failed"),
        (_error("cancelled"), "cancelled"),
    ],
)
def test_provider_terminal_errors_do_not_mint_success(step: FakeStep, status: str) -> None:
    result = _execute(_request(), (step,))

    assert result.status == status
    assert result.raw_response is None
    assert result.parsed_response is None


def test_elapsed_timeout_discards_late_response() -> None:
    result = _execute(
        _request(timeout_ns=1_000_000_000),
        (_response("valid_response"),),
        clock_step=1_000_000_001,
    )

    assert result.status == "timed_out"
    assert result.raw_response is None
    assert result.parsed_response is None


@dataclass
class _BlockingAdapter:
    binding: ProviderBinding
    delegate: DeterministicFakeAdapter
    release: threading.Event
    started: threading.Event
    invocation_count: int = 0

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        self.invocation_count += 1
        self.started.set()
        self.release.wait(timeout=2)
        return self.delegate.invoke(call)


class _EventCancellation:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


def _blocking_adapter(request: GatewayRequest) -> _BlockingAdapter:
    delegate = _adapter(request, (_response("valid_response"),) * request.runtime_policy.max_attempts)
    return _BlockingAdapter(
        binding=delegate.binding,
        delegate=delegate,
        release=threading.Event(),
        started=threading.Event(),
    )


def test_blocking_adapter_returns_by_hard_deadline_and_discards_late_response() -> None:
    request = _request(timeout_ns=25_000_000)
    adapter = _blocking_adapter(request)
    started = time.monotonic()
    try:
        result = execute_gateway_request(
            request,
            adapter=adapter,
            clock=_Clock(),
            cancellation=_Cancellation(),
        )
        elapsed = time.monotonic() - started
    finally:
        adapter.release.set()

    assert adapter.started.is_set()
    assert elapsed < 0.25
    assert result.status == "timed_out"
    assert result.attempts[0].outcome == "timeout"
    assert result.attempts[0].timing.latency_ns == request.runtime_policy.timeout_ns
    assert result.raw_response is None
    assert result.parsed_response is None


def test_blocking_adapter_timeout_retries_only_to_registered_attempt_limit() -> None:
    request = _request(max_attempts=2, timeout_ns=20_000_000)
    adapter = _blocking_adapter(request)
    try:
        result = execute_gateway_request(
            request,
            adapter=adapter,
            clock=_Clock(),
            cancellation=_Cancellation(),
        )
    finally:
        adapter.release.set()

    assert result.status == "retry_exhausted"
    assert adapter.invocation_count == 2
    assert [record.outcome for record in result.attempts] == ["timeout", "timeout"]
    assert result.issue is not None and result.issue.code == "retry_exhausted"


def test_cancellation_interrupts_gateway_wait_for_blocking_adapter() -> None:
    request = _request(timeout_ns=1_000_000_000)
    adapter = _blocking_adapter(request)
    cancellation = _EventCancellation()

    def cancel_after_start() -> None:
        assert adapter.started.wait(timeout=0.25)
        cancellation.cancelled.set()

    canceller = threading.Thread(target=cancel_after_start, daemon=True)
    canceller.start()
    started = time.monotonic()
    try:
        result = execute_gateway_request(
            request,
            adapter=adapter,
            clock=_Clock(),
            cancellation=cancellation,
        )
        elapsed = time.monotonic() - started
    finally:
        adapter.release.set()
        canceller.join(timeout=0.25)

    assert elapsed < 0.25
    assert result.status == "cancelled"
    assert result.issue is not None and result.issue.code == "cancelled_after_adapter_start"
    assert result.raw_response is None
    assert result.parsed_response is None


def test_response_identity_mutation_fails_closed() -> None:
    result = _execute(_request(), (_response("response_mutation"),))

    assert result.status == "identity_rejected"
    assert result.issue is not None
    assert result.issue.code == "provider_response_identity_mismatch"


def test_oversized_response_is_not_parsed() -> None:
    result = _execute(
        _request(max_response_bytes=8),
        (_response("oversized_response", b'{"value":"too-large"}'),),
    )

    assert result.status == "oversized_response"
    assert result.raw_response is not None
    assert result.parsed_response is None


def test_cancellation_after_response_prevents_publication() -> None:
    result = _execute(
        _request(),
        (_response("valid_response"),),
        cancellation=_Cancellation((False, True)),
    )

    assert result.status == "cancelled"
    assert result.raw_response is None
    assert result.parsed_response is None
    assert result.issue is not None and result.issue.code == "cancelled_after_adapter_start"


def test_cancellation_immediately_before_success_publication_is_honored() -> None:
    result = _execute(
        _request(),
        (_response("valid_response"),),
        cancellation=_Cancellation((False, False, True)),
    )

    assert result.status == "cancelled"
    assert result.raw_response is None
    assert result.parsed_response is None
    assert result.issue is not None and result.issue.code == "cancelled_after_adapter_start"


def test_cancellation_race_has_one_canonical_public_result() -> None:
    request = _request(timeout_ns=1_000_000_000)
    after_response = _execute(
        request,
        (_response("valid_response"),),
        cancellation=_Cancellation((False, True)),
    )

    adapter = _blocking_adapter(request)
    cancellation = _EventCancellation()

    def cancel_after_start() -> None:
        assert adapter.started.wait(timeout=0.25)
        cancellation.cancelled.set()

    canceller = threading.Thread(target=cancel_after_start, daemon=True)
    canceller.start()
    try:
        during_adapter = execute_gateway_request(
            request,
            adapter=adapter,
            clock=_Clock(),
            cancellation=cancellation,
        )
    finally:
        adapter.release.set()
        canceller.join(timeout=0.25)

    assert during_adapter.model_dump_json() == after_response.model_dump_json()


@dataclass
class _TrapAdapter:
    binding: ProviderBinding
    invoked: bool = False

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        self.invoked = True
        raise AssertionError("mismatched adapter must not be invoked")


def test_no_silent_fallback_provider_or_model() -> None:
    request = _request()
    trap = _TrapAdapter(
        ProviderBinding(
            provider_ref=_opaque("f"),
            model_ref=request.initial_attempt.model_policy.model_ref,
            model_version_ref=request.initial_attempt.model_policy.model_version_ref,
        )
    )

    result = execute_gateway_request(
        request,
        adapter=trap,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )

    assert result.status == "identity_rejected"
    assert not trap.invoked


@dataclass
class _UnsafeErrorAdapter:
    binding: ProviderBinding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        raise RuntimeError("SYNTHETIC_SECRET at C:\\private\\model.txt")


def test_untyped_provider_error_is_converted_to_public_safe_taxonomy() -> None:
    request = _request()
    adapter = _UnsafeErrorAdapter(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy)
    )

    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )

    assert result.status == "provider_failed"
    assert result.issue is not None and result.issue.code == "untyped_provider_error"
    assert "SYNTHETIC_SECRET" not in result.model_dump_json()
    assert "private" not in result.model_dump_json()


@dataclass
class _UnsafeTypedErrorAdapter:
    binding: ProviderBinding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        raise AdapterInvocationError(
            code="permanent_provider_error",
            retryable=False,
            provider_attempt_ref="SYNTHETIC_SECRET",
        )


def test_invalid_typed_provider_metadata_is_not_persisted() -> None:
    request = _request()
    result = execute_gateway_request(
        request,
        adapter=_UnsafeTypedErrorAdapter(
            ProviderBinding.from_model_policy(request.initial_attempt.model_policy)
        ),
        clock=_Clock(),
        cancellation=_Cancellation(),
    )

    assert result.status == "provider_failed"
    assert result.issue is not None
    assert result.issue.code == "invalid_provider_error_metadata"
    assert "SYNTHETIC_SECRET" not in result.model_dump_json()


def test_request_and_provider_call_reject_bound_content_mutation() -> None:
    request = _request()
    forged = request.model_dump(mode="python")
    forged["prompt_text"] = "rewritten"
    with pytest.raises(ValidationError, match="prompt"):
        GatewayRequest.model_validate(forged)

    call = ProviderCall(
        request_identity_sha256=request.initial_attempt.request_identity_sha256,
        attempt_id=request.initial_attempt.attempt_id,
        attempt_identity_sha256=request.initial_attempt.attempt_identity_sha256,
        attempt_ordinal=request.initial_attempt.attempt_ordinal,
        context_sha256=request.initial_attempt.context_sha256,
        prompt_sha256=request.initial_attempt.prompt_sha256,
        response_schema_sha256=request.initial_attempt.response_schema_sha256,
        context_json=request.context.canonical_json(),
        prompt_text=request.prompt_text,
        response_schema_json=request.response_schema_json,
        runtime_policy=request.runtime_policy,
    )
    forged_call = call.model_dump(mode="python")
    forged_call["context_json"] = "{}"
    with pytest.raises(ValidationError, match="context"):
        ProviderCall.model_validate(forged_call)


def test_adapter_call_exposes_no_case_mechanism_or_manifest_object() -> None:
    forbidden = {"case", "family", "mechanism", "manifest", "ground_truth"}
    field_names = set(ProviderCall.model_fields)

    assert not any(token in field.lower() for token in forbidden for field in field_names)
    assert set(FakeFixture.model_fields) == {"request_identity_sha256", "steps"}


@pytest.mark.parametrize(
    "invalid_schema",
    [
        {"type": "array"},
        {
            "type": "object",
            "properties": {"value": {"type": "array"}},
        },
    ],
)
def test_invalid_response_schema_is_rejected_before_adapter(
    invalid_schema: dict[str, object],
) -> None:
    request = _request()

    with pytest.raises(GatewayContractError, match="response schema"):
        prepare_gateway_request(
            manifest=request.initial_attempt.manifest,
            case=request.initial_attempt.case,
            model_policy=request.initial_attempt.model_policy,
            context=request.context,
            prompt_text=request.prompt_text,
            response_schema=invalid_schema,
            runtime_policy=request.runtime_policy,
        )


def test_fake_adapter_uses_no_filesystem_network_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    adapter = _adapter(request, (_response("valid_response"),))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ambient I/O is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)

    assert_adapter_conformance(adapter, request)


def test_fake_result_is_byte_deterministic_when_reconstructed() -> None:
    request = _request(max_attempts=2)
    steps = (_error("transient_error"), _response("valid_response"))

    first = _execute(request, steps).model_dump_json()
    second = _execute(request, steps).model_dump_json()

    assert first == second


def test_result_contract_rejects_terminal_status_attempt_mismatch() -> None:
    result = _execute(_request(), (_response("valid_response"),))
    forged = result.model_dump(mode="python")
    forged["status"] = "provider_failed"

    with pytest.raises(ValidationError, match="terminal status"):
        GatewayExecutionResult.model_validate(forged)


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "total_tokens", "cost_amount", "currency", "message"),
    [
        (-1, None, None, None, None, "non-negative"),
        (None, None, None, Decimal("-0.01"), _opaque("d"), "finite and non-negative"),
        (None, None, None, Decimal("0.01"), None, "known together"),
    ],
)
def test_usage_contract_rejects_invalid_known_values(
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cost_amount: Decimal | None,
    currency: str | None,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        UsageMetadata(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_amount=cost_amount,
            cost_currency_ref=currency,
        )


def test_fake_contract_rejects_incomplete_or_ambiguous_steps() -> None:
    with pytest.raises(ValidationError, match="raw content"):
        FakeStep(kind="valid_response", raw_content=None, usage=None)
    with pytest.raises(ValidationError, match="no content"):
        FakeStep(kind="empty_response", raw_content=b"{}", usage=_unknown_usage())
    with pytest.raises(ValidationError, match="cannot carry"):
        FakeStep(kind="timeout", raw_content=b"{}", usage=_unknown_usage())
    with pytest.raises(ValidationError, match="must contain"):
        FakeFixture(request_identity_sha256=_sha("1"), steps=())


def test_fake_adapter_rejects_duplicate_fixture_keys_and_unknown_requests() -> None:
    request = _request()
    fixture = FakeFixture(
        request_identity_sha256=request.initial_attempt.request_identity_sha256,
        steps=(_response("valid_response"),),
    )
    with pytest.raises(ValueError, match="unique"):
        DeterministicFakeAdapter(
            binding=ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
            fixtures=(fixture, fixture),
        )

    unmatched = DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
        fixtures=(
            FakeFixture(
                request_identity_sha256=_sha("f"),
                steps=(_response("valid_response"),),
            ),
        ),
    )
    result = execute_gateway_request(
        request,
        adapter=unmatched,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )
    assert result.status == "provider_failed"
    assert result.issue is not None and result.issue.code == "permanent_provider_error"


def test_gateway_rejects_invalid_unicode_before_adapter_access() -> None:
    request = _request()

    with pytest.raises(GatewayContractError, match="invalid Unicode"):
        prepare_gateway_request(
            manifest=request.initial_attempt.manifest,
            case=request.initial_attempt.case,
            model_policy=request.initial_attempt.model_policy,
            context=request.context,
            prompt_text="\ud800",
            response_schema=_SCHEMA,
            runtime_policy=request.runtime_policy,
        )


def test_gateway_handles_cancellation_and_unreadable_adapter_binding() -> None:
    request = _request()
    cancelled = execute_gateway_request(
        request,
        adapter=_adapter(request, (_response("valid_response"),)),
        clock=_Clock(),
        cancellation=_Cancellation((True,)),
    )
    assert cancelled.status == "cancelled"
    assert cancelled.issue is not None
    assert cancelled.issue.code == "cancelled_before_adapter"

    unreadable_binding = _TrapAdapter(binding=object())
    rejected = execute_gateway_request(
        request,
        adapter=unreadable_binding,
        clock=_Clock(),
        cancellation=_Cancellation(),
    )
    assert rejected.status == "identity_rejected"
    assert not unreadable_binding.invoked


@pytest.mark.parametrize(
    "invalid_schema",
    [
        {"type": "object", "unexpected": True},
        {"type": "object", "required": "value"},
        {"type": "object", "required": ["missing"], "properties": {}},
        {
            "type": "object",
            "properties": {"value": {"type": "unsupported"}},
        },
    ],
)
def test_response_schema_shape_rejects_unsupported_constraints(
    invalid_schema: dict[str, object],
) -> None:
    request = _request()

    with pytest.raises(GatewayContractError, match="response schema"):
        prepare_gateway_request(
            manifest=request.initial_attempt.manifest,
            case=request.initial_attempt.case,
            model_policy=request.initial_attempt.model_policy,
            context=request.context,
            prompt_text=request.prompt_text,
            response_schema=invalid_schema,
            runtime_policy=request.runtime_policy,
        )


def test_nested_array_enum_and_const_schema_is_validated_recursively() -> None:
    request = _request()
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": "diagnosis-output/2"},
            "labels": {
                "type": "array",
                "items": {"type": "string", "enum": ["supported", "unsupported"]},
            },
        },
        "required": ["schema_version", "labels"],
    }
    schema_json = canonical_project_json(schema)
    policy = request.initial_attempt.model_policy.model_copy(
        update={"response_schema_sha256": content_sha256(schema_json.encode())}
    )
    policy_payload = policy.model_dump(mode="python")
    policy_payload.pop("reference_id")
    rebuilt_policy = ModelPolicyReference.build(
        manifest=request.initial_attempt.manifest,
        **{
            key: value
            for key, value in policy_payload.items()
            if key
            not in {
                "schema_version",
                "manifest_reference_id",
                "manifest_content_sha256",
                "project_id",
                "snapshot_id",
                "authorization_ref",
            }
        },
    )
    runtime = RuntimePolicyReference.build(
        manifest=request.initial_attempt.manifest,
        model_policy=rebuilt_policy,
        retry_policy_ref=request.runtime_policy.retry_policy_ref,
        timeout_ns=request.runtime_policy.timeout_ns,
        max_attempts=request.runtime_policy.max_attempts,
        max_response_bytes=request.runtime_policy.max_response_bytes,
        provenance_sha256=request.runtime_policy.provenance_sha256,
    )
    nested = prepare_gateway_request(
        manifest=request.initial_attempt.manifest,
        case=request.initial_attempt.case,
        model_policy=rebuilt_policy,
        context=request.context,
        prompt_text=request.prompt_text,
        response_schema=schema,
        runtime_policy=runtime,
    )
    valid = _execute(
        nested,
        (
            _response(
                "valid_response",
                b'{"labels":["supported"],"schema_version":"diagnosis-output/2"}',
            ),
        ),
    )
    invalid = _execute(
        nested,
        (
            _response(
                "valid_response",
                b'{"labels":["other"],"schema_version":"diagnosis-output/2"}',
            ),
        ),
    )

    assert valid.status == "parsed"
    assert invalid.status == "parse_failed"


def test_nonfinite_response_and_terminal_artifact_mismatches_fail_closed() -> None:
    nonfinite = _execute(_request(), (_response("malformed_response", b'{"value":NaN}'),))
    assert nonfinite.status == "parse_failed"

    parsed = _execute(_request(), (_response("valid_response"),))
    missing_parsed = parsed.model_dump(mode="python")
    missing_parsed["parsed_response"] = None
    with pytest.raises(ValidationError, match="parsed result"):
        GatewayExecutionResult.model_validate(missing_parsed)

    parse_failed = _execute(_request(), (_response("malformed_response", b"{"),))
    missing_raw = parse_failed.model_dump(mode="python")
    missing_raw["raw_response"] = None
    with pytest.raises(ValidationError, match="parse failure"):
        GatewayExecutionResult.model_validate(missing_raw)

    oversized = _execute(
        _request(max_response_bytes=8),
        (_response("oversized_response", b'{"value":"too-large"}'),),
    )
    oversized_missing_raw = oversized.model_dump(mode="python")
    oversized_missing_raw["raw_response"] = None
    with pytest.raises(ValidationError, match="oversized result"):
        GatewayExecutionResult.model_validate(oversized_missing_raw)

    timed_out = _execute(_request(), (_error("timeout"),))
    timed_out_with_raw = timed_out.model_dump(mode="python")
    timed_out_with_raw["raw_response"] = parsed.raw_response.model_dump(mode="python")
    with pytest.raises(ValidationError, match="cannot publish raw"):
        GatewayExecutionResult.model_validate(timed_out_with_raw)


def test_low_level_gateway_guards_reject_in_memory_contract_forgery() -> None:
    parsed = _execute(_request(), (_response("valid_response"),))
    assert parsed.raw_response is not None and parsed.parsed_response is not None

    with pytest.raises(ValueError, match="exact bytes"):
        parsed.raw_response.model_copy(
            update={"byte_count": parsed.raw_response.byte_count + 1}
        )._content_matches_reference()
    with pytest.raises(ValueError, match="canonical payload"):
        parsed.parsed_response.model_copy(
            update={"content_sha256": _sha("0")}
        )._content_matches_reference()

    response_record = parsed.attempts[0]
    with pytest.raises(ValueError, match="timing"):
        response_record.timing.model_copy(update={"latency_ns": 99})._latency_reconciles()
    with pytest.raises(ValueError, match="requires provider metadata"):
        response_record.model_copy(update={"provider_attempt_ref": None})._outcome_shape_is_consistent()

    parse_failed = _execute(_request(), (_response("malformed_response", b"{"),))
    with pytest.raises(ValueError, match="retain metadata"):
        parse_failed.attempts[0].model_copy(update={"issue": None})._outcome_shape_is_consistent()

    timed_out = _execute(_request(), (_error("timeout"),))
    with pytest.raises(ValueError, match="cannot contain response metadata"):
        timed_out.attempts[0].model_copy(
            update={"response_mode": "structured"}
        )._outcome_shape_is_consistent()
    with pytest.raises(ValueError, match="at least one attempt"):
        parsed.model_copy(update={"attempts": ()})._terminal_shape_is_consistent()
    wrong_ordinal = response_record.attempt.model_copy(update={"attempt_ordinal": 2})
    with pytest.raises(ValueError, match="sequence"):
        parsed.model_copy(
            update={"attempts": (response_record.model_copy(update={"attempt": wrong_ordinal}),)}
        )._terminal_shape_is_consistent()
    with pytest.raises(ValueError, match="cannot contain parsed content"):
        timed_out.model_copy(
            update={"parsed_response": parsed.parsed_response}
        )._terminal_shape_is_consistent()

    with pytest.raises(ValueError, match="retryability"):
        AdapterInvocationError(
            code="transient_provider_error",
            retryable=False,
            provider_attempt_ref=_opaque("f"),
        )

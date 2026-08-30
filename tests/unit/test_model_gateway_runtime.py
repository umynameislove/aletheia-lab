"""Reusable conformance tests for the provider-neutral deterministic runtime."""

from __future__ import annotations

import builtins
import os
import socket
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
    timeout_ns: int = 100,
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
    result = _execute(_request(timeout_ns=5), (_response("valid_response"),), clock_step=6)

    assert result.status == "timed_out"
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
    assert result.issue is not None and result.issue.code == "cancelled_before_publication"


def test_cancellation_immediately_before_success_publication_is_honored() -> None:
    result = _execute(
        _request(),
        (_response("valid_response"),),
        cancellation=_Cancellation((False, False, True)),
    )

    assert result.status == "cancelled"
    assert result.raw_response is None
    assert result.parsed_response is None


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
            "properties": {"value": {"type": "string", "enum": ["x"]}},
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

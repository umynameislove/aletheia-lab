"""Production OpenAI adapter conformance without live provider access."""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    GatewayRequest,
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayConfigurationError,
    OpenAIGatewayPolicy,
    ProviderBinding,
    ProviderCall,
    RuntimePolicyReference,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

ROOT = Path(__file__).resolve().parents[2]
FAIRNESS_FREEZE = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "string", "const": "diagnosis-output/2"},
        "claim_strength": {
            "type": "string",
            "enum": ["observation", "comparison", "bounded_causal_hypothesis"],
        },
        "supporting_evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "abstain": {"type": "boolean"},
    },
    "required": [
        "schema_version",
        "claim_strength",
        "supporting_evidence_ids",
        "abstain",
    ],
}


def _sha(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha(character)}"


def _policy() -> OpenAIGatewayPolicy:
    freeze = load_diagnosis_variant_freeze(FAIRNESS_FREEZE)
    return OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])


def _request() -> GatewayRequest:
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
    schema_json = canonical_project_json(SCHEMA)
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=_policy().model_policy_sha256(),
        provider_ref=_opaque("5"),
        model_ref=_opaque("6"),
        model_version_ref=_opaque("7"),
        resource_policy_ref=_opaque("8"),
        prompt_policy_ref=_opaque("9"),
        response_schema_sha256=content_sha256(schema_json.encode()),
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
    runtime = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque("b"),
        timeout_ns=60_000_000_000,
        max_attempts=2,
        max_response_bytes=4096,
        provenance_sha256=_sha("c"),
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=model_policy,
        context=context,
        prompt_text="Use only the visible evidence and obey the response schema.",
        response_schema=SCHEMA,
        runtime_policy=runtime,
    )


def _call(request: GatewayRequest) -> ProviderCall:
    attempt = request.initial_attempt
    return ProviderCall(
        request_identity_sha256=attempt.request_identity_sha256,
        attempt_id=attempt.attempt_id,
        attempt_identity_sha256=attempt.attempt_identity_sha256,
        attempt_ordinal=attempt.attempt_ordinal,
        context_sha256=attempt.context_sha256,
        prompt_sha256=attempt.prompt_sha256,
        response_schema_sha256=attempt.response_schema_sha256,
        context_json=request.context.canonical_json(),
        prompt_text=request.prompt_text,
        response_schema_json=request.response_schema_json,
        runtime_policy=request.runtime_policy,
    )


def _content(*, abstain: bool = False) -> str:
    return canonical_project_json(
        {
            "schema_version": "diagnosis-output/2",
            "claim_strength": "observation" if abstain else "comparison",
            "supporting_evidence_ids": [],
            "abstain": abstain,
        }
    )


class _CapturingCompletions:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response or _response()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _Chat:
    def __init__(self, completions: _CapturingCompletions) -> None:
        self.completions = completions


class _Client:
    def __init__(self, completions: _CapturingCompletions) -> None:
        self.chat = _Chat(completions)


def _response(
    *,
    content: str | None = None,
    model: str = "gpt-4.1-2025-04-14",
    finish_reason: str = "stop",
    refusal: str | None = None,
    usage: object | None = None,
) -> object:
    return SimpleNamespace(
        id="chatcmpl-production-test",
        model=model,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=_content() if content is None else content,
                    refusal=refusal,
                ),
            )
        ],
        usage=(
            SimpleNamespace(prompt_tokens=120, completion_tokens=40, total_tokens=160)
            if usage is None
            else usage
        ),
    )


def _adapter(completions: _CapturingCompletions) -> OpenAIChatCompletionsGatewayAdapter:
    request = _request()
    return OpenAIChatCompletionsGatewayAdapter(
        client=_Client(completions),
        model_policy=request.initial_attempt.model_policy,
        policy=_policy(),
    )


def test_policy_is_copied_exactly_from_the_fairness_freeze() -> None:
    freeze = load_diagnosis_variant_freeze(FAIRNESS_FREEZE)
    policy = _policy()

    assert policy.model_version == "gpt-4.1-2025-04-14"
    assert (policy.temperature, policy.top_p, policy.seed) == (0.0, 1.0, 17)
    assert (policy.max_output_tokens, policy.provider_attempt_ceiling) == (600, 2)
    assert policy.hidden_sdk_retries == 0
    assert policy.store_provider_response is False
    assert policy.model_policy_sha256() == canonical_execution_sha256(
        freeze.model_policies["main_llm_v1"].model_dump(mode="json")
    )
    assert policy.canonical_sha256() != policy.model_policy_sha256()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model_version", "gpt-4.1"),
        ("temperature", 0.1),
        ("top_p", 0.9),
        ("seed", 18),
        ("max_output_tokens", 601),
        ("timeout_seconds", 30.0),
        ("provider_attempt_ceiling", 1),
        ("hidden_sdk_retries", 1),
        ("store_provider_response", True),
    ),
)
def test_policy_rejects_every_unfrozen_transport_setting(field: str, value: object) -> None:
    payload = _policy().model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        OpenAIGatewayPolicy.model_validate(payload)


def test_adapter_rejects_a_model_reference_that_does_not_bind_the_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    current = request.initial_attempt.model_policy
    mismatched = ModelPolicyReference.build(
        manifest=request.initial_attempt.manifest,
        policy_content_sha256=_sha("0"),
        provider_ref=current.provider_ref,
        model_ref=current.model_ref,
        model_version_ref=current.model_version_ref,
        resource_policy_ref=current.resource_policy_ref,
        prompt_policy_ref=current.prompt_policy_ref,
        response_schema_sha256=current.response_schema_sha256,
        provenance_sha256=current.provenance_sha256,
        visibility=current.visibility,
    )

    with pytest.raises(
        OpenAIGatewayConfigurationError,
        match="does not bind the frozen OpenAI policy",
    ):
        OpenAIChatCompletionsGatewayAdapter(
            client=_Client(_CapturingCompletions()),
            model_policy=mismatched,
            policy=_policy(),
        )

    def unexpected_sdk_access(_name: str) -> str:
        raise AssertionError("SDK state must not be read before policy validation")

    monkeypatch.setattr(importlib.metadata, "version", unexpected_sdk_access)
    with pytest.raises(
        OpenAIGatewayConfigurationError,
        match="does not bind the frozen OpenAI policy",
    ):
        OpenAIChatCompletionsGatewayAdapter.from_environment(
            model_policy=mismatched,
            policy=_policy(),
        )


def test_adapter_sends_the_exact_bounded_payload_and_returns_raw_content() -> None:
    request = _request()
    completions = _CapturingCompletions()
    adapter = _adapter(completions)

    envelope = adapter.invoke(_call(request))

    assert envelope.request_identity_sha256 == request.initial_attempt.request_identity_sha256
    assert envelope.binding == ProviderBinding.from_model_policy(
        request.initial_attempt.model_policy
    )
    assert envelope.raw_response.content == _content().encode()
    assert envelope.usage.model_dump(mode="python") == {
        "input_tokens": 120,
        "output_tokens": 40,
        "total_tokens": 160,
        "cost_amount": None,
        "cost_currency_ref": None,
    }
    payload = completions.calls[0]
    assert payload["model"] == "gpt-4.1-2025-04-14"
    assert payload["messages"] == [
        {"role": "system", "content": request.prompt_text},
        {"role": "user", "content": request.context.canonical_json()},
    ]
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["seed"] == 17
    assert payload["max_tokens"] == 600
    assert payload["timeout"] == 60.0
    assert payload["n"] == 1
    assert payload["store"] is False
    assert payload["stream"] is False
    assert payload["extra_headers"] == {
        "X-Client-Request-Id": request.initial_attempt.attempt_id
    }
    assert not ({"tools", "web_search", "retrieval"} & set(payload))
    response_format = payload["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    schema_block = response_format["json_schema"]
    assert isinstance(schema_block, dict)
    assert schema_block["strict"] is True
    assert schema_block["schema"] == SCHEMA


def test_explicit_model_abstention_is_preserved_as_response_metadata() -> None:
    request = _request()
    completions = _CapturingCompletions(_response(content=_content(abstain=True)))
    adapter = _adapter(completions)

    envelope = adapter.invoke(_call(request))

    assert envelope.response_mode == "abstention"
    assert envelope.raw_response.content == _content(abstain=True).encode()


@pytest.mark.parametrize(
    "response",
    (
        _response(model="gpt-4.1"),
        _response(finish_reason="length"),
        _response(refusal="unsafe request"),
        SimpleNamespace(
            id="chatcmpl-two-choices",
            model="gpt-4.1-2025-04-14",
            choices=[SimpleNamespace(), SimpleNamespace()],
            usage=None,
        ),
    ),
)
def test_model_switch_refusal_truncation_and_choice_mutation_fail_closed(
    response: object,
) -> None:
    adapter = _adapter(_CapturingCompletions(response))

    with pytest.raises(AdapterInvocationError) as captured:
        adapter.invoke(_call(_request()))

    assert captured.value.code == "permanent_provider_error"
    assert captured.value.retryable is False


def test_missing_usage_remains_unknown_and_inconsistent_usage_is_rejected() -> None:
    request = _request()
    missing = _adapter(_CapturingCompletions(_response(usage=SimpleNamespace())))

    envelope = missing.invoke(_call(request))
    assert envelope.usage.input_tokens is None
    assert envelope.usage.output_tokens is None
    assert envelope.usage.total_tokens is None

    inconsistent = _adapter(
        _CapturingCompletions(
            _response(
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=14,
                )
            )
        )
    )
    with pytest.raises(AdapterInvocationError) as captured:
        inconsistent.invoke(_call(request))
    assert captured.value.code == "permanent_provider_error"


@pytest.mark.parametrize(
    ("exception_name", "status_code", "expected_code", "retryable"),
    (
        ("APITimeoutError", None, "provider_timeout", True),
        ("APIConnectionError", None, "transient_provider_error", True),
        ("RateLimitError", 429, "transient_provider_error", True),
        ("InternalServerError", 503, "transient_provider_error", True),
        ("BadRequestError", 400, "permanent_provider_error", False),
        ("UnknownSDKError", None, "permanent_provider_error", False),
    ),
)
def test_provider_exceptions_map_to_the_public_safe_retry_taxonomy(
    exception_name: str,
    status_code: int | None,
    expected_code: str,
    retryable: bool,
) -> None:
    exception_type = type(exception_name, (RuntimeError,), {})
    error = exception_type("SYNTHETIC_SECRET provider detail")
    error.status_code = status_code
    error.request_id = "request-secret-provider-id"
    adapter = _adapter(_CapturingCompletions(error=error))

    with pytest.raises(AdapterInvocationError) as captured:
        adapter.invoke(_call(_request()))

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    serialized = str(captured.value)
    assert "SYNTHETIC_SECRET" not in serialized
    assert "request-secret-provider-id" not in serialized


def test_runtime_policy_mismatch_and_non_strict_schema_never_reach_provider() -> None:
    request = _request()
    completions = _CapturingCompletions()
    adapter = _adapter(completions)
    mismatched_runtime = RuntimePolicyReference.build(
        manifest=request.initial_attempt.manifest,
        model_policy=request.initial_attempt.model_policy,
        retry_policy_ref=request.runtime_policy.retry_policy_ref,
        timeout_ns=request.runtime_policy.timeout_ns,
        max_attempts=1,
        max_response_bytes=request.runtime_policy.max_response_bytes,
        provenance_sha256=request.runtime_policy.provenance_sha256,
    )
    mismatched_call = _call(request).model_copy(update={"runtime_policy": mismatched_runtime})

    with pytest.raises(AdapterInvocationError, match="permanent_provider_error"):
        adapter.invoke(mismatched_call)

    schema = json.loads(request.response_schema_json)
    assert isinstance(schema, dict)
    schema["additionalProperties"] = True
    schema_json = canonical_project_json(schema)
    call = _call(request).model_copy(
        update={
            "response_schema_json": schema_json,
            "response_schema_sha256": content_sha256(schema_json.encode()),
        }
    )
    with pytest.raises(AdapterInvocationError, match="permanent_provider_error"):
        adapter.invoke(call)
    assert completions.calls == []


def test_environment_factory_disables_sdk_retries_and_pins_official_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    completions = _CapturingCompletions()
    client = _Client(completions)
    captured: dict[str, object] = {}

    def factory(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "2.46.0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-persisted")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_ORG_ID", raising=False)
    monkeypatch.delenv("OPENAI_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        "aletheia_lab.model_gateway.openai.importlib.import_module",
        lambda _name: SimpleNamespace(OpenAI=factory),
    )

    adapter = OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=request.initial_attempt.model_policy,
        policy=_policy(),
    )

    assert adapter.binding == ProviderBinding.from_model_policy(
        request.initial_attempt.model_policy
    )
    assert captured == {
        "api_key": "test-key-never-persisted",
        "base_url": "https://api.openai.com/v1",
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    "environment",
    (
        "missing_key",
        "custom_base_url",
        "ambient_organization",
        "ambient_project",
        "malformed_key",
        "wrong_sdk",
        "broken_sdk_import",
    ),
)
def test_environment_factory_fails_closed_without_exposing_secret(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "2.46.0")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_ORG_ID", raising=False)
    monkeypatch.delenv("OPENAI_PROJECT_ID", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-never-exposed")
    if environment == "missing_key":
        monkeypatch.delenv("OPENAI_API_KEY")
    elif environment == "custom_base_url":
        monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    elif environment == "ambient_organization":
        monkeypatch.setenv("OPENAI_ORG_ID", "org-not-frozen")
    elif environment == "ambient_project":
        monkeypatch.setenv("OPENAI_PROJECT_ID", "project-not-frozen")
    elif environment == "malformed_key":
        monkeypatch.setenv("OPENAI_API_KEY", "secret-never-exposed\n")
    elif environment == "wrong_sdk":
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "2.45.0")
    else:
        def fail_import(_name: str) -> object:
            raise ImportError("synthetic broken installation")

        monkeypatch.setattr(
            "aletheia_lab.model_gateway.openai.importlib.import_module",
            fail_import,
        )

    with pytest.raises(OpenAIGatewayConfigurationError) as captured:
        OpenAIChatCompletionsGatewayAdapter.from_environment(
            model_policy=request.initial_attempt.model_policy,
            policy=_policy(),
        )
    assert "secret-never-exposed" not in str(captured.value)


@dataclass
class _Clock:
    value: int = 0

    def now_ns(self) -> int:
        self.value += 1
        return self.value


@dataclass(frozen=True)
class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_gateway_retries_one_transient_failure_without_changing_identity() -> None:
    request = _request()
    exception_type = type("RateLimitError", (RuntimeError,), {})
    error = exception_type("private provider text")
    error.status_code = 429
    error.request_id = "first-provider-request"

    class _OneFailureThenSuccess(_CapturingCompletions):
        def create(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise error
            return _response()

    completions = _OneFailureThenSuccess()
    result = execute_gateway_request(
        request,
        adapter=_adapter(completions),
        clock=_Clock(),
        cancellation=_NeverCancelled(),
    )

    assert result.status == "parsed"
    assert [item.outcome for item in result.attempts] == ["transient_error", "response"]
    assert len({item.attempt.request_identity_sha256 for item in result.attempts}) == 1
    assert [item.attempt.attempt_ordinal for item in result.attempts] == [1, 2]
    headers = [call["extra_headers"] for call in completions.calls]
    assert headers == [
        {"X-Client-Request-Id": result.attempts[0].attempt.attempt_id},
        {"X-Client-Request-Id": result.attempts[1].attempt.attempt_id},
    ]

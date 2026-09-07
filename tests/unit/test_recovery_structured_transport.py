"""Exercise the pinned SDK's actual HTTP serialization without network or credentials."""

import copy
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_recovery_audit import audit_recovery_store
from aletheia_lab.evaluation.claim_corpus_recovery_execution import prepare_recovery_rehearsal
from aletheia_lab.evaluation.claim_corpus_recovery_probe import build_compatibility_requests
from aletheia_lab.evaluation.claim_corpus_recovery_run import _execute_compatibility
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import OpenAIGatewayPolicy, execute_gateway_request
from aletheia_lab.model_gateway.contracts import AttemptRecord
from aletheia_lab.model_gateway.openai import _openai_response_format
from aletheia_lab.model_gateway.openai_recovery import OpenAIRecoveryAdapter
from aletheia_lab.model_gateway.recovery_transport import provider_wire_schema
from aletheia_lab.model_gateway.schema import validate_response_payload

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def probes():
    return build_compatibility_requests(prepare_recovery_rehearsal(ROOT)[0])


def _output(schema):
    citation = schema["properties"]["result"]["anyOf"][0]["properties"]["atomic_claims"]["items"][
        "properties"
    ]["visible_evidence_ids"]["items"]["enum"][0]
    return {
        "schema_version": "diagnosis-provider-output/2",
        "result": {
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_type": "evidence_statement",
                    "claim_text": "The synthetic counter is 7.",
                    "material_parts": [{"text": "counter is 7"}],
                    "visible_evidence_ids": [citation],
                }
            ],
        },
    }


def _adapter(request, *, mutate=None, finish="stop", captured=None):
    def respond(wire_request):
        payload = json.loads(wire_request.content)
        if captured is not None:
            captured.append(payload)
        output = _output(payload["response_format"]["json_schema"]["schema"])
        if mutate is not None:
            mutate(output)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4.1-2025-04-14",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish,
                        "logprobs": None,
                        "message": {"role": "assistant", "content": json.dumps(output)},
                    }
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 2048, "total_tokens": 2348},
            },
        )

    client = OpenAI(
        api_key="synthetic-not-a-credential",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    return OpenAIRecoveryAdapter(
        client=client,
        model_policy=request.initial_attempt.model_policy,
        policy=OpenAIGatewayPolicy.from_fairness_policy(
            load_diagnosis_variant_freeze(
                ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
            ).model_policies["main_llm_v1"]
        ).with_recovery_output_budget(),
    ), client


def test_actual_sdk_wire_and_independent_normalization_for_all_three_schemas(probes, tmp_path):
    captured = []
    adapter, client = _adapter(probes[0].request, captured=captured)
    clock = SystemMonotonicClock()
    try:
        _execute_compatibility(
            probes, ClaimCorpusAttemptStore(tmp_path / "store", clock=clock), adapter, clock
        )
    finally:
        client.close()
    receipt = audit_recovery_store(ROOT, tmp_path / "store", probes)
    assert receipt["gateway_status_counts"] == {"parsed": 3}
    assert receipt["normalized_output_count"] == receipt["completed_output_count"] == 3
    assert len(captured) == 3
    for payload, probe in zip(captured, probes, strict=True):
        assert payload["max_tokens"] == 2048
        assert payload["model"] == "gpt-4.1-2025-04-14"
        assert payload["store"] is False and payload["stream"] is False
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["response_format"]["json_schema"]["schema"] == provider_wire_schema(
            probe.request.response_schema_json
        )
        assert '"pattern"' not in json.dumps(payload["response_format"])
        assert payload["messages"][0]["content"] == probe.request.prompt_text


@pytest.mark.parametrize(
    "text", [" Claim.", "Claim. ", "Claim\ntext.", "", "e\u0301", "a\x00b", "x" * 2049]
)
def test_relaxed_wire_cannot_publish_noncanonical_text_as_parsed(probes, text):
    request = probes[0].request
    adapter, client = _adapter(
        request, mutate=lambda out: out["result"]["atomic_claims"][0].update(claim_text=text)
    )
    try:
        result = execute_gateway_request(
            request, adapter=adapter, clock=SystemMonotonicClock(), cancellation=NeverCancelled()
        )
    finally:
        client.close()
    assert result.status != "parsed"
    assert result.parsed_response is None


def test_length_retains_usage_and_fingerprint_without_partial_content(probes):
    request = probes[0].request
    secret_text = "Never persist this private response text"
    adapter, client = _adapter(
        request,
        finish="length",
        mutate=lambda out: out["result"]["atomic_claims"][0].update(claim_text=secret_text),
    )
    try:
        result = execute_gateway_request(
            request, adapter=adapter, clock=SystemMonotonicClock(), cancellation=NeverCancelled()
        )
    finally:
        client.close()
    assert result.status == "provider_failed"
    assert result.issue.code == "provider_output_truncated"
    record = result.attempts[0]
    diagnostics = record.failure_diagnostics
    assert diagnostics.finish_reason == "length"
    assert diagnostics.usage.output_tokens == diagnostics.configured_output_token_limit == 2048
    assert diagnostics.content_utf8_bytes > 0 and len(diagnostics.content_sha256) == 64
    assert secret_text not in result.model_dump_json()
    assert "synthetic-not-a-credential" not in result.model_dump_json()
    assert AttemptRecord.model_validate_json(record.model_dump_json()) == record
    legacy = record.model_dump(mode="json")
    legacy.pop("failure_diagnostics")
    assert AttemptRecord.model_validate_json(json.dumps(legacy)).model_dump(mode="json") == legacy


def test_projection_does_not_mutate_local_schema_or_relax_citation_and_shape(probes):
    original = json.loads(probes[0].request.response_schema_json)
    before = copy.deepcopy(original)
    projected = provider_wire_schema(probes[0].request.response_schema_json)
    payload = _output(projected)
    validate_response_payload(payload, original)
    validate_response_payload(payload, projected)
    payload["result"]["atomic_claims"][0]["visible_evidence_ids"] = ["invented-id"]
    for schema in (original, projected):
        with pytest.raises(ValueError):
            validate_response_payload(payload, schema)
    assert original == before
    broken = copy.deepcopy(original)
    broken["properties"]["result"]["anyOf"][0]["additionalProperties"] = True
    with pytest.raises(ValueError, match="closed"):
        _openai_response_format(json.dumps(broken))

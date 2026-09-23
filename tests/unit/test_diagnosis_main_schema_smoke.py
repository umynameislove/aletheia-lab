"""Outcome-blind tests for the one-call main-schema compatibility gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.diagnosis.main_schema_smoke import (
    MAIN_RECOVERY_TRANSPORT_SHA256,
    SMOKE_DESTINATION,
    SYNTHETIC_EVIDENCE_ID,
    OpenAIMainRecoveryAdapter,
    build_smoke_plan,
    build_synthetic_main_schema_request,
    exact_outbound_payload,
    main_provider_wire_schema,
    provider_call,
    validate_self_hash,
    validate_smoke_response,
)
from aletheia_lab.model_gateway import execute_gateway_request
from aletheia_lab.model_gateway.schema import validate_response_payload
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "1" * 40


def _response(*, claim_id: str = "claim-1", material_text: str | None = None) -> bytes:
    text = material_text or "The synthetic counter is 7."
    return canonical_project_json(
        {
            "schema_version": "diagnosis-main-provider-output/1",
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_local_id": claim_id,
                    "claim_type": "evidence_statement",
                    "claim_text": "The synthetic counter is 7.",
                    "material_parts": [{"part_id": "part-1", "text": text}],
                    "visible_evidence_ids": [SYNTHETIC_EVIDENCE_ID],
                }
            ],
            "abstention_reason": None,
        }
    ).encode()


def test_smoke_payload_is_synthetic_single_call_and_keeps_frozen_transport() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = exact_outbound_payload(request, policy)
    assert payload["model"] == "gpt-4.1-2025-04-14"
    assert payload["max_tokens"] == 600
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["seed"] == 17
    assert payload["store"] is False and payload["stream"] is False
    assert payload["timeout"] == 60.0
    assert '"pattern"' not in json.dumps(payload["response_format"])
    assert "synthetic" in str(payload["messages"][1]).lower()

    plan = build_smoke_plan(
        ROOT,
        source_commit_ref=COMMIT,
        predecessor_receipt_sha256="2" * 64,
        predecessor_tree_sha256="3" * 64,
    )
    validate_self_hash(plan, "plan_sha256")
    assert plan["destination"] == SMOKE_DESTINATION
    assert plan["provider_call_count"] == 1
    assert plan["sdk_retries"] == 0
    assert plan["synthetic_payload_only"] is True
    assert plan["recovery_authorized"] is False
    assert plan["transport_sha256"] == MAIN_RECOVERY_TRANSPORT_SHA256


def test_smoke_validates_against_original_schema_without_new_text_ceiling() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    validate_smoke_response(_response(), request=request)
    validate_smoke_response(_response(material_text="x" * 1500), request=request)

    with pytest.raises(ValueError):
        validate_smoke_response(_response(claim_id="claim-99"), request=request)


def test_smoke_plan_is_deterministic_and_tamper_evident() -> None:
    arguments = {
        "source_commit_ref": COMMIT,
        "predecessor_receipt_sha256": "2" * 64,
        "predecessor_tree_sha256": "3" * 64,
    }
    first = build_smoke_plan(ROOT, **arguments)
    second = build_smoke_plan(ROOT, **arguments)
    assert first == second
    first["provider_call_count"] = 2
    with pytest.raises(ValueError, match="plan_sha256"):
        validate_self_hash(first, "plan_sha256")


def test_plan_payload_equals_the_actual_adapter_invocation() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    captured: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> object:
            captured.append(kwargs)
            return SimpleNamespace(
                id="chatcmpl-synthetic-smoke",
                model="gpt-4.1-2025-04-14",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=_response().decode(),
                            refusal=None,
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    adapter = OpenAIMainRecoveryAdapter(
        client=client,
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    envelope = adapter.invoke(provider_call(request))
    validate_smoke_response(envelope.raw_response.content, request=request)
    assert captured == [exact_outbound_payload(request, policy)]


def test_projection_removes_only_patterns_and_preserves_original_local_gate() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    original = json.loads(request.response_schema_json)
    original["properties"]["pattern"] = {"type": "string"}
    original["required"].append("pattern")
    projected = main_provider_wire_schema(canonical_project_json(original))
    assert '"pattern"' in json.dumps(original)
    assert "pattern" in projected["properties"]
    assert '"pattern": "' not in json.dumps(projected)

    valid = json.loads(_response())
    valid["pattern"] = "a retained data field"
    validate_response_payload(valid, original)
    validate_response_payload(valid, projected)
    valid["atomic_claims"][0]["claim_local_id"] = "claim-99"
    with pytest.raises(ValueError):
        validate_response_payload(valid, original)
    validate_response_payload(valid, projected)


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


def test_gateway_revalidates_main_output_against_the_unprojected_schema() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    calls: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                id="chatcmpl-invalid-local-shape",
                model="gpt-4.1-2025-04-14",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=_response(claim_id="claim-99").decode(),
                            refusal=None,
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

    adapter = OpenAIMainRecoveryAdapter(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_NeverCancelled(),
    )
    assert result.status == "parse_failed"
    assert result.parsed_response is None
    assert len(calls) == 1

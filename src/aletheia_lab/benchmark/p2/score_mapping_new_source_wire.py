"""Offline audit of the complete, common diagnosis transport envelope.

The injected client cannot contact a provider. These are input-channel audits,
not diagnoses, provider responses, or permission to execute a final study.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    EvidenceCondition,
    serialize_development_evidence_view,
)
from aletheia_lab.diagnosis.main_schema_smoke import (
    OpenAIMainRecoveryAdapter,
    build_synthetic_main_schema_request,
    provider_call,
)
from aletheia_lab.evaluation.execution_contracts import EvaluationCaseReference
from aletheia_lab.evaluation.score_mapping_reader import (
    build_score_mapping_reader_context,
    validate_score_mapping_reader_context,
)
from aletheia_lab.model_gateway import prepare_gateway_request
from aletheia_lab.model_gateway.openai import OpenAIGatewayClient

CONDITIONS: tuple[EvidenceCondition, ...] = ("full", "missing_key", "noisy", "misleading")
_WIRE_FIELDS = {
    "model",
    "messages",
    "response_format",
    "temperature",
    "top_p",
    "seed",
    "max_tokens",
    "n",
    "store",
    "stream",
    "timeout",
    "extra_headers",
}


class NewSourceWireError(ValueError):
    """The real adapter exposes a different or unclassified input surface."""


class _Capture:
    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def create(self, **payload: Any) -> object:
        self.calls.append(deepcopy(payload))
        # Adapter conformance only: this object is never scored or interpreted.
        return SimpleNamespace(
            id="offline-input-channel-audit",
            model=self.model,
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="{}", refusal=None)
                )
            ],
            usage=None,
        )


def capture_reader_wire(
    root: Path,
    observation: DevelopmentObservation,
    condition: EvidenceCondition,
) -> dict[str, Any]:
    """Capture messages, schema and settings through the production adapter.

    A common frozen A2 prompt/schema is a transport witness, not a new LLM
    efficacy protocol. Case identity stays internal; the only excluded field is
    the adapter's allowlisted opaque request-ID header, not a model input.
    """

    base, policy = build_synthetic_main_schema_request(root, source_commit_ref="0" * 40)
    context = build_score_mapping_reader_context(observation, condition=condition)
    validate_score_mapping_reader_context(context, observation=observation, condition=condition)
    old = base.initial_attempt.case
    fields = {
        key: getattr(old, key)
        for key in (
            "case_id",
            "family_id",
            "mechanism_id",
            "dataset_id",
            "variant_id",
            "variant_content_sha256",
            "case_content_sha256",
            "evidence_bundle_id",
            "lineage_graph_id",
            "lineage_sha256",
            "provenance_sha256",
            "visibility",
        )
    }
    fields.update(
        evidence_content_sha256=context.context_sha256,
        visibility_projection_sha256=context.context_sha256,
    )
    request = prepare_gateway_request(
        manifest=base.initial_attempt.manifest,
        case=EvaluationCaseReference.build(manifest=base.initial_attempt.manifest, **fields),
        model_policy=base.initial_attempt.model_policy,
        context=context,
        prompt_text=base.prompt_text,
        response_schema=json.loads(base.response_schema_json),
        runtime_policy=base.runtime_policy,
    )
    capture = _Capture(policy.model_version)
    client = SimpleNamespace(chat=SimpleNamespace(completions=capture))
    adapter = OpenAIMainRecoveryAdapter(
        client=cast(OpenAIGatewayClient, client),
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    adapter.invoke(provider_call(request))
    if len(capture.calls) != 1 or set(capture.calls[0]) != _WIRE_FIELDS:
        raise NewSourceWireError("transport contains extra or missing unclassified fields")
    payload = capture.calls[0]
    header = payload.pop("extra_headers")
    if header != {"X-Client-Request-Id": request.initial_attempt.attempt_id}:
        raise NewSourceWireError("transport telemetry differs from the opaque request-ID header")
    return payload


def audit_reader_pair(
    root: Path,
    mapping: DevelopmentObservation,
    rival: DevelopmentObservation,
) -> dict[str, Any]:
    """Report equality at each declared channel, retaining unmatched pairs."""

    payloads = {
        condition: [capture_reader_wire(root, item, condition) for item in (mapping, rival)]
        for condition in CONDITIONS
    }
    # Fixed UTF-8 JSON encoding of the captured SDK-interface objects. This is
    # not a claim about the OpenAI SDK's eventual HTTP serialization.
    equal = {
        condition: json.dumps(pair[0], sort_keys=True, ensure_ascii=False).encode("utf-8")
        == json.dumps(pair[1], sort_keys=True, ensure_ascii=False).encode("utf-8")
        for condition, pair in payloads.items()
    }
    raw_equal = mapping.observed_log_loss == rival.observed_log_loss
    twelve_equal = serialize_development_evidence_view(
        mapping,
        condition="missing_key",
        metric_decimal_places=12,
    ) == serialize_development_evidence_view(
        rival,
        condition="missing_key",
        metric_decimal_places=12,
    )
    return {
        "schema_version": "score-mapping-input-channel-audit/v1",
        "wire_payloads": payloads,
        "wire_equal": equal,
        "wire_sha256": {
            key: [canonical_sha256(item) for item in pair] for key, pair in payloads.items()
        },
        "raw_metric_equal": raw_equal,
        "twelve_decimal_missing_key_equal": twelve_equal,
        "provider_calls": 0,
        "diagnoses_generated": False,
        "excluded_transport_field": "extra_headers.X-Client-Request-Id",
        "scope": "common-frozen-A2-SDK-interface-six-decimal-finite-pair",
    }


def synthetic_wire_preflight(root: Path) -> dict[str, Any]:
    """Check the actual serializer/adapter on synthetic observations only."""

    mapping = DevelopmentObservation(
        record_count=40,
        reference_log_loss=0.5,
        observed_log_loss=0.6000001,
        model_classes=(0, 1),
        evaluator_classes=(1, 0),
        example_score_pair=(0.1, 0.9),
        example_observed_positive=0.1,
        example_corrected_positive=0.9,
        target_binding_matches_source=True,
        corrected_log_loss=0.5,
        feature_width=2,
        reference_feature_count=80,
        feature_mean_distance=0.1,
        source_identity_sha256="a" * 64,
        reference_features_sha256="b" * 64,
    )
    rival = replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
        source_identity_sha256="c" * 64,
    )
    audit = audit_reader_pair(root, mapping, rival)
    expected = {condition: condition == "missing_key" for condition in CONDITIONS}
    if audit["wire_equal"] != expected or audit["twelve_decimal_missing_key_equal"]:
        raise NewSourceWireError("synthetic transport lost its declared precision boundary")
    return {
        "status": "synthetic_input_channel_pass",
        "synthetic_observations_only": True,
        "wire_equal": audit["wire_equal"],
        "wire_sha256": audit["wire_sha256"],
        "scope": audit["scope"],
        "provider_calls": 0,
        "new_source_models_fitted": False,
    }

"""Development artifact-lineage projections and an offline diagnosis input audit.

Private identities establish provenance, not model-visible cause labels. The
missing-key view removes only the trusted artifact-load witness. Other locus
witnesses and performance measurements remain; equality is measured, not forced.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

from aletheia_lab.diagnosis.main_schema_smoke import (
    OpenAIMainRecoveryAdapter,
    build_synthetic_main_schema_request,
    provider_call,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import prepare_gateway_request
from aletheia_lab.model_gateway.openai import OpenAIGatewayClient
from aletheia_lab.project.identity import content_sha256

Condition = Literal["full", "missing_key", "noisy", "misleading"]
CONDITIONS: tuple[Condition, ...] = ("full", "missing_key", "noisy", "misleading")
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


class ArtifactBindingReaderError(ValueError):
    """Retained measurements or the projected input boundary are inconsistent."""


@dataclass(frozen=True, slots=True)
class ArtifactBindingObservation:
    """A caller-verified observation; never serialize the entire dataclass."""

    record_count: int
    reference_log_loss: float
    observed_log_loss: float
    intended_artifact: str
    loaded_artifact: str
    reported_artifact: str
    model_classes: tuple[int, int]
    consumed_classes: tuple[int, int]
    source_targets: tuple[int, int]
    scoring_targets: tuple[int, int]
    raw_probe_scores: tuple[tuple[float, float], tuple[float, float]]
    scored_probe_positive: tuple[float, float]
    feature_count: int
    training_count: int
    calibration_count: int
    private_source_sha256: str


def _validate(observation: ArtifactBindingObservation) -> None:
    if not isinstance(observation, ArtifactBindingObservation):
        raise ArtifactBindingReaderError("a checked artifact observation is required")
    counts = (
        observation.record_count,
        observation.feature_count,
        observation.training_count,
        observation.calibration_count,
    )
    if any(type(value) is not int or value < 1 for value in counts):
        raise ArtifactBindingReaderError("observation counts are invalid")
    if observation.record_count < 2:
        raise ArtifactBindingReaderError("two distinct class probes are required")
    if any(
        value not in {"artifact-0", "artifact-1"}
        for value in (
            observation.intended_artifact,
            observation.loaded_artifact,
            observation.reported_artifact,
        )
    ):
        raise ArtifactBindingReaderError("artifact aliases are not authorized")
    witnesses = (
        observation.model_classes,
        observation.consumed_classes,
        observation.source_targets,
        observation.scoring_targets,
    )
    if any(
        not isinstance(pair, tuple) or len(pair) != 2 or any(type(y) is not int for y in pair)
        for pair in witnesses
    ) or (
        observation.model_classes != (0, 1)
        or observation.consumed_classes not in ((0, 1), (1, 0))
        or observation.source_targets != (0, 1)
        or observation.scoring_targets not in ((0, 1), (1, 0))
    ):
        raise ArtifactBindingReaderError("column or probe target witnesses are invalid")
    _validate_scores(observation)
    digest = observation.private_source_sha256
    if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise ArtifactBindingReaderError("private provenance identity is invalid")


def _validate_scores(observation: ArtifactBindingObservation) -> None:
    losses = (observation.reference_log_loss, observation.observed_log_loss)
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in losses
    ):
        raise ArtifactBindingReaderError("log loss is invalid")
    if len(observation.raw_probe_scores) != 2 or len(observation.scored_probe_positive) != 2:
        raise ArtifactBindingReaderError("two fixed row probes are required")
    column = observation.consumed_classes.index(1)
    for pair, scored in zip(
        observation.raw_probe_scores, observation.scored_probe_positive, strict=True
    ):
        if (
            len(pair) != 2
            or any(
                type(p) not in (int, float) or not math.isfinite(p) or not 0 < p < 1 for p in pair
            )
            or not math.isclose(sum(pair), 1, rel_tol=0, abs_tol=1e-12)
            or type(scored) not in (int, float)
            or scored != pair[column]
        ):
            raise ArtifactBindingReaderError("a pre-adapter probability witness is invalid")


def _bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def artifact_binding_projection(
    observation: ArtifactBindingObservation,
    *,
    condition: Condition,
    metric_decimal_places: int = 6,
) -> dict[str, Any]:
    """Project authentic measurements with one common, cause-blind field layout."""

    _validate(observation)
    if (
        condition not in CONDITIONS
        or type(metric_decimal_places) is not int
        or metric_decimal_places not in (6, 12)
    ):
        raise ArtifactBindingReaderError("condition or numerical precision is not authorized")

    def rounded(value: float) -> float:
        result = round(value, metric_decimal_places)
        return 0.0 if result == 0 else result

    reference = rounded(observation.reference_log_loss)
    observed = rounded(observation.observed_log_loss)
    items: list[dict[str, Any]] = [
        {
            "id": "performance-comparison",
            "payload": {
                "record_count": observation.record_count,
                "reference_role": "shared-historical-benchmark",
                "endpoint": "raw-reference-prior-standardized-log-loss/v1",
                "reference_log_loss": reference,
                "observed_log_loss": observed,
                "log_loss_change": rounded(
                    observation.observed_log_loss - observation.reference_log_loss
                ),
            },
        }
    ]
    if condition != "missing_key":
        items.append(
            {
                "id": "artifact-load-binding",
                "payload": {
                    "trusted_intended_artifact": observation.intended_artifact,
                    "deserialized_buffer_artifact": observation.loaded_artifact,
                    "measurement_boundary": "hash-of-buffer-passed-to-deserializer",
                },
            }
        )
    items.extend(
        [
            {
                "id": "probability-column-binding",
                "payload": {
                    "model_classes": list(observation.model_classes),
                    "consumed_column_classes": list(observation.consumed_classes),
                    "raw_probe_scores": [
                        [rounded(p) for p in pair] for pair in observation.raw_probe_scores
                    ],
                    "scored_probe_positive": [
                        rounded(p) for p in observation.scored_probe_positive
                    ],
                },
            },
            {
                "id": "row-target-binding",
                "payload": {
                    "probes": [
                        {"row": f"probe-{i}", "source_target": source, "scoring_target": scored}
                        for i, (source, scored) in enumerate(
                            zip(
                                observation.source_targets, observation.scoring_targets, strict=True
                            )
                        )
                    ],
                },
            },
        ]
    )
    if condition == "noisy":
        items.append(
            {
                "id": "execution-dimensions",
                "payload": {
                    "feature_count": observation.feature_count,
                    "training_count": observation.training_count,
                    "calibration_count": observation.calibration_count,
                },
            }
        )
    if condition == "misleading":
        items.append(
            {
                "id": "reported-manifest",
                "payload": {
                    "reported_artifact": observation.reported_artifact,
                    "source": "untrusted-manifest-text-not-load-measurement",
                },
            }
        )
    return {
        "schema_version": "artifact-binding-development-projection/v1",
        "metric_decimal_places": metric_decimal_places,
        "items": items,
    }


def build_artifact_binding_context(
    observation: ArtifactBindingObservation, *, condition: Condition
) -> ModelVisibleEvidenceContext:
    """Bind every visible identifier and digest only to the selected projection."""

    projected = _bytes(artifact_binding_projection(observation, condition=condition))
    digest = content_sha256(projected)
    item = ModelVisibleEvidenceItem(
        evidence_id="development-observation",
        kind="metric",
        title="Observed evaluation measurements",
        content=projected.decode("utf-8"),
        content_sha256=digest,
        source_content_sha256=digest,
    )
    identity = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": (item.model_dump(mode="json"),),
    }
    context_digest = canonical_execution_sha256(identity)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_digest}", items=(item,), context_sha256=context_digest
    )


def validate_artifact_binding_context(
    context: ModelVisibleEvidenceContext,
    *,
    observation: ArtifactBindingObservation,
    condition: Condition,
) -> None:
    expected = build_artifact_binding_context(observation, condition=condition)
    if (
        not isinstance(context, ModelVisibleEvidenceContext)
        or context.model_payload() != expected.model_payload()
    ):
        raise ArtifactBindingReaderError("context differs from the authorized artifact projection")


class _Capture:
    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def create(self, **payload: Any) -> object:
        self.calls.append(deepcopy(payload))
        return SimpleNamespace(
            id="offline-artifact-input-audit",
            model=self.model,
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="{}", refusal=None)
                )
            ],
            usage=None,
        )


def capture_artifact_binding_wire(
    root: Path,
    observation: ArtifactBindingObservation,
    condition: Condition,
    *,
    internal_case_id: str | None = None,
) -> dict[str, Any]:
    """Capture the real recovery adapter with a non-network SDK substitute.

    The frozen common A2 prompt/schema tests the transport, not a final M4
    diagnosis protocol or a live HTTP request. Only the allowlisted opaque
    request-ID header is excluded from the equality comparison.
    """

    base, policy = build_synthetic_main_schema_request(root, source_commit_ref="0" * 40)
    context = build_artifact_binding_context(observation, condition=condition)
    validate_artifact_binding_context(context, observation=observation, condition=condition)
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
    if internal_case_id is not None:
        fields["case_id"] = internal_case_id
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
        raise ArtifactBindingReaderError("transport has unclassified fields or extra invocations")
    payload = capture.calls[0]
    if payload.pop("extra_headers") != {"X-Client-Request-Id": request.initial_attempt.attempt_id}:
        raise ArtifactBindingReaderError("transport telemetry has an unclassified field")
    return payload


def _pair_result(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    equal = _bytes(left) == _bytes(right)
    return {"equal": equal, "finite_equal_prior_lookup_ceiling": 0.5 if equal else 1.0}


def audit_artifact_binding_observations(
    root: Path, observations: dict[str, ArtifactBindingObservation]
) -> dict[str, Any]:
    """Measure finite input collisions without treating a lookup as LLM accuracy."""

    expected = {
        "healthy",
        "faulty",
        "sham",
        "corrected",
        "legitimate_B",
        "manifest_text_only",
        "adapter_column_reversal",
        "two_row_target_swap",
    }
    if observations.keys() != expected:
        raise ArtifactBindingReaderError("retained observation census is incomplete")
    wires = {
        name: {
            condition: capture_artifact_binding_wire(root, observation, condition)
            for condition in CONDITIONS
        }
        for name, observation in observations.items()
    }
    pairs: dict[str, Any] = {}
    for rival in ("legitimate_B", "adapter_column_reversal", "two_row_target_swap"):
        a, b = observations["faulty"], observations[rival]
        pair: dict[str, Any] = {
            condition: _pair_result(wires["faulty"][condition], wires[rival][condition])
            for condition in CONDITIONS
        }
        pair["raw_metric_equal"] = a.observed_log_loss == b.observed_log_loss
        pair["twelve_decimal_missing_key_equal"] = _bytes(
            artifact_binding_projection(a, condition="missing_key", metric_decimal_places=12)
        ) == _bytes(
            artifact_binding_projection(b, condition="missing_key", metric_decimal_places=12)
        )
        pair["metric_only_lookup_ceiling"] = (
            0.5 if round(a.observed_log_loss, 6) == round(b.observed_log_loss, 6) else 1.0
        )
        pair["full_artifact_witness_distinguishes"] = (a.intended_artifact, a.loaded_artifact) != (
            b.intended_artifact,
            b.loaded_artifact,
        )
        pairs[rival] = pair
    checks = {
        "sham_complete_input_equal": wires["healthy"] == wires["sham"],
        "correction_complete_input_equal": wires["healthy"] == wires["corrected"],
        "manifest_only_trusted_full_equal": wires["healthy"]["full"]
        == wires["manifest_text_only"]["full"],
        "manifest_only_reported_text_retained": wires["healthy"]["misleading"]
        != wires["manifest_text_only"]["misleading"],
        "legitimate_B_missing_key_equal": pairs["legitimate_B"]["missing_key"]["equal"],
        "legitimate_B_full_distinct": not pairs["legitimate_B"]["full"]["equal"],
    }
    if not all(checks.values()):
        raise ArtifactBindingReaderError("a retained control lost its input-boundary meaning")
    return {
        "schema_version": "artifact-binding-development-input-audit/v1",
        "status": "development_input_boundary_audited",
        "observation_count": len(observations),
        "sdk_capture_count": len(observations) * len(CONDITIONS),
        "checks": checks,
        "pairwise": pairs,
        "wire_sha256": {
            name: {
                condition: canonical_execution_sha256(payload)
                for condition, payload in views.items()
            }
            for name, views in wires.items()
        },
        "scope": "one-retained-development-cell; common-frozen-A2-SDK-interface",
        "equal_payload_bound": "equal priors on each finite pair; fixed prompt schema and settings; no side channel",
        "lookup_is_llm_accuracy": False,
        "provider_calls": 0,
        "scientific_admission": False,
        "protected_execution_authorized": False,
        "diagnosis_protocol_frozen": False,
        "excluded_transport_field": "extra_headers.X-Client-Request-Id",
    }

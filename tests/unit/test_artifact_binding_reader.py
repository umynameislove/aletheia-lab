"""Synthetic falsification checks at the actual artifact diagnosis boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.artifact_binding_reader import (
    CONDITIONS,
    ArtifactBindingObservation,
    ArtifactBindingReaderError,
    artifact_binding_projection,
    audit_artifact_binding_observations,
    build_artifact_binding_context,
    capture_artifact_binding_wire,
    validate_artifact_binding_context,
)
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def observation() -> ArtifactBindingObservation:
    return ArtifactBindingObservation(
        record_count=40,
        reference_log_loss=0.4,
        observed_log_loss=0.7,
        intended_artifact="artifact-0",
        loaded_artifact="artifact-1",
        reported_artifact="artifact-0",
        model_classes=(0, 1),
        consumed_classes=(0, 1),
        source_targets=(0, 1),
        scoring_targets=(0, 1),
        raw_probe_scores=((0.6, 0.4), (0.4, 0.6)),
        scored_probe_positive=(0.4, 0.6),
        feature_count=3,
        training_count=80,
        calibration_count=40,
        private_source_sha256="a" * 64,
    )


def census() -> dict[str, ArtifactBindingObservation]:
    fault = observation()
    healthy = replace(fault, loaded_artifact="artifact-0", observed_log_loss=0.4)
    return {
        "faulty": fault,
        "healthy": healthy,
        "sham": healthy,
        "corrected": healthy,
        "legitimate_B": replace(
            fault, intended_artifact="artifact-1", reported_artifact="artifact-1"
        ),
        "manifest_text_only": replace(healthy, reported_artifact="artifact-1"),
        "adapter_column_reversal": replace(
            healthy,
            observed_log_loss=1.1,
            consumed_classes=(1, 0),
            scored_probe_positive=(0.6, 0.4),
        ),
        "two_row_target_swap": replace(healthy, observed_log_loss=0.41, scoring_targets=(1, 0)),
    }


def test_missing_key_is_exactly_full_without_the_load_witness() -> None:
    case = observation()
    full = artifact_binding_projection(case, condition="full")
    missing = artifact_binding_projection(case, condition="missing_key")
    assert missing == {
        **full,
        "items": [item for item in full["items"] if item["id"] != "artifact-load-binding"],
    }
    assert len(full["items"]) == 4 and len(missing["items"]) == 3
    assert full["items"][0]["payload"]["reference_role"] == "shared-historical-benchmark"
    assert '"artifact-0"' not in json.dumps(missing)
    assert '"artifact-1"' not in json.dumps(missing)
    for name in ("model_classes", "consumed_column_classes", "source_target", "scoring_target"):
        assert name in json.dumps(missing)


def test_legitimate_B_uses_its_own_intention_not_the_shared_benchmark() -> None:
    data = census()
    full = artifact_binding_projection(data["legitimate_B"], condition="full")
    binding = next(
        item["payload"] for item in full["items"] if item["id"] == "artifact-load-binding"
    )
    assert (
        binding["trusted_intended_artifact"]
        == binding["deserialized_buffer_artifact"]
        == "artifact-1"
    )
    assert "corrected" not in json.dumps(full)
    for precision in (6, 12):
        assert artifact_binding_projection(
            data["faulty"], condition="missing_key", metric_decimal_places=precision
        ) == artifact_binding_projection(
            data["legitimate_B"], condition="missing_key", metric_decimal_places=precision
        )


def test_complete_real_adapter_audit_preserves_unmatched_rivals() -> None:
    result = audit_artifact_binding_observations(ROOT, census())
    assert all(result["checks"].values())
    assert result["observation_count"] == 8 and result["sdk_capture_count"] == 32
    assert result["pairwise"]["legitimate_B"]["missing_key"] == {
        "equal": True,
        "finite_equal_prior_lookup_ceiling": 0.5,
    }
    assert result["pairwise"]["legitimate_B"]["full"]["equal"] is False
    for rival in ("adapter_column_reversal", "two_row_target_swap"):
        pair = result["pairwise"][rival]
        assert not pair["missing_key"]["equal"]
        assert pair["metric_only_lookup_ceiling"] == 1
        assert not pair["twelve_decimal_missing_key_equal"]
    assert result["provider_calls"] == 0 and result["lookup_is_llm_accuracy"] is False
    assert not result["scientific_admission"] and not result["protected_execution_authorized"]
    assert not result["diagnosis_protocol_frozen"]


def test_private_provenance_does_not_change_any_visible_field() -> None:
    original = observation()
    altered = replace(original, private_source_sha256="f" * 64)
    for condition in CONDITIONS:
        left = build_artifact_binding_context(original, condition=condition)
        right = build_artifact_binding_context(altered, condition=condition)
        assert left.model_payload() == right.model_payload()
        assert capture_artifact_binding_wire(
            ROOT, original, condition
        ) == capture_artifact_binding_wire(ROOT, altered, condition)
        visible = json.dumps(left.model_payload())
        assert "a" * 64 not in visible and "f" * 64 not in visible
        assert left.items[0].source_content_sha256 == left.items[0].content_sha256
        assert condition not in visible


def test_internal_case_id_changes_only_excluded_opaque_telemetry() -> None:
    from test_openai_gateway_adapter import _request

    base = _request()
    case_id = base.initial_attempt.case.case_id
    original = capture_artifact_binding_wire(ROOT, observation(), "missing_key")
    changed = capture_artifact_binding_wire(
        ROOT, observation(), "missing_key", internal_case_id=case_id
    )
    assert original == changed


def rewrap(
    original: ModelVisibleEvidenceContext, data: dict[str, Any]
) -> ModelVisibleEvidenceContext:
    item = type(original.items[0]).model_validate(data)
    identity = {"schema_version": original.schema_version, "items": (item.model_dump(mode="json"),)}
    digest = canonical_execution_sha256(identity)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}", items=(item,), context_sha256=digest
    )


@pytest.mark.parametrize(
    "mutation",
    ["title", "id", "private_hash", "raw_precision", "load_witness", "cause", "catalog", "reorder"],
)
def test_authorized_constructor_rejects_generic_valid_leaky_context(mutation: str) -> None:
    case = observation()
    original = build_artifact_binding_context(case, condition="missing_key")
    data = original.items[0].model_dump(mode="json")
    if mutation == "title":
        data["title"] = "Wrong artifact load"
    elif mutation == "id":
        data["evidence_id"] = "fault-1"
    elif mutation == "private_hash":
        data["source_content_sha256"] = "a" * 64
    else:
        content = json.loads(data["content"])
        if mutation == "raw_precision":
            content["metric_decimal_places"] = 12
        elif mutation == "load_witness":
            content["items"].append(artifact_binding_projection(case, condition="full")["items"][1])
        elif mutation == "cause":
            content["cause"] = "artifact"
        elif mutation == "catalog":
            content["catalog"] = {"source": "a" * 64}
        else:
            content["items"].reverse()
        data["content"] = json.dumps(content, separators=(",", ":"), sort_keys=True)
        data["content_sha256"] = content_sha256(data["content"].encode())
    changed = rewrap(original, data)
    with pytest.raises(ArtifactBindingReaderError, match="authorized artifact projection"):
        validate_artifact_binding_context(changed, observation=case, condition="missing_key")


@pytest.mark.parametrize(
    "changes",
    [
        {"record_count": True},
        {"record_count": 1},
        {"intended_artifact": "private-model-path"},
        {"observed_log_loss": float("nan")},
        {"observed_log_loss": -0.1},
        {"private_source_sha256": "invalid"},
        {"consumed_classes": (0, 0)},
        {"model_classes": (False, True)},
        {"scoring_targets": (False, True)},
        {"source_targets": (1, 0)},
        {"scored_probe_positive": (0.6, 0.6)},
        {"raw_probe_scores": ((0.6, 0.6), (0.4, 0.6))},
    ],
)
def test_invalid_observation_is_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ArtifactBindingReaderError):
        build_artifact_binding_context(replace(observation(), **changes), condition="full")


@pytest.mark.parametrize("precision", [0, 3, 7, True, 13])
def test_no_precision_coarsening_to_force_matching(precision: int) -> None:
    with pytest.raises(ArtifactBindingReaderError):
        artifact_binding_projection(
            observation(), condition="full", metric_decimal_places=precision
        )


def test_incomplete_census_and_altered_controls_fail() -> None:
    data = census()
    with pytest.raises(ArtifactBindingReaderError, match="census"):
        audit_artifact_binding_observations(
            ROOT, {k: v for k, v in data.items() if k != "legitimate_B"}
        )
    with pytest.raises(ArtifactBindingReaderError, match="control"):
        audit_artifact_binding_observations(
            ROOT, {**data, "corrected": replace(data["corrected"], observed_log_loss=0.5)}
        )


@pytest.mark.parametrize("mutation", ["sdk_field", "header", "second_call"])
def test_capture_rejects_unclassified_fields_or_extra_calls(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import aletheia_lab.evaluation.artifact_binding_reader as reader

    original = reader._Capture.create

    def altered(self: Any, **payload: Any) -> object:
        if mutation == "sdk_field":
            payload["metadata"] = {"cause": "wrong-artifact"}
        elif mutation == "header":
            payload["extra_headers"]["X-Private-Cause"] = "wrong-artifact"
        response = original(self, **payload)
        if mutation == "second_call":
            original(self, **payload)
        return response

    monkeypatch.setattr(reader._Capture, "create", altered)
    with pytest.raises(ArtifactBindingReaderError, match="unclassified|invocations"):
        capture_artifact_binding_wire(ROOT, observation(), "missing_key")


def test_visible_rounding_never_silently_proves_raw_metric_equality() -> None:
    data = census()
    # The inputs coincide at six decimals, but not at the diagnostic precision.
    data["two_row_target_swap"] = replace(
        data["healthy"], observed_log_loss=data["faulty"].observed_log_loss + 1e-8
    )
    result = audit_artifact_binding_observations(ROOT, data)
    pair = result["pairwise"]["two_row_target_swap"]
    assert pair["missing_key"]["equal"]
    assert pair["metric_only_lookup_ceiling"] == 0.5
    assert not pair["raw_metric_equal"] and not pair["twelve_decimal_missing_key_equal"]

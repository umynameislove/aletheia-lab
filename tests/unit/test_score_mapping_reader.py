"""M5 development observations at the actual gateway-visible boundary."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_model_gateway_runtime import _adapter, _Cancellation, _Clock, _request, _response
from test_openai_gateway_adapter import (
    _CapturingCompletions,
    _Client,
    _policy,
)
from test_openai_gateway_adapter import (
    _request as _openai_request,
)
from test_score_mapping_evidence import _case
from test_score_mapping_symptom_matching import _observation

from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    ScoreMappingEvidenceError,
    serialize_development_evidence_view,
    serialize_m5_diagnostic_view,
    zero_dose_observation,
)
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.score_mapping_reader import (
    build_score_mapping_reader_context,
    validate_score_mapping_reader_context,
)
from aletheia_lab.model_gateway import (
    OpenAIChatCompletionsGatewayAdapter,
    ProviderCall,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import content_sha256


def _rival():
    mapping = _observation()
    return replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
        source_identity_sha256="c" * 64,
        reference_features_sha256="d" * 64,
    )


def _bound_case(base, context, *, case_id=None):
    old_case = base.initial_attempt.case
    return EvaluationCaseReference.build(
        manifest=base.initial_attempt.manifest,
        case_id=old_case.case_id if case_id is None else case_id,
        family_id=old_case.family_id,
        mechanism_id=old_case.mechanism_id,
        dataset_id=old_case.dataset_id,
        variant_id=old_case.variant_id,
        variant_content_sha256=old_case.variant_content_sha256,
        case_content_sha256=old_case.case_content_sha256,
        evidence_bundle_id=old_case.evidence_bundle_id,
        evidence_content_sha256=context.context_sha256,
        lineage_graph_id=old_case.lineage_graph_id,
        lineage_sha256=old_case.lineage_sha256,
        visibility_projection_sha256=context.context_sha256,
        provenance_sha256=old_case.provenance_sha256,
        visibility=old_case.visibility,
    )


def test_complete_gateway_context_has_no_missing_key_shortcut() -> None:
    mapping = _observation()
    rival = _rival()
    left = build_score_mapping_reader_context(mapping, condition="missing_key")
    right = build_score_mapping_reader_context(rival, condition="missing_key")
    assert left.model_payload() == right.model_payload()
    assert canonical_execution_json(left.model_payload()) == canonical_execution_json(
        right.model_payload()
    )
    assert left.items[0].content.encode("utf-8") == serialize_m5_diagnostic_view(
        mapping, condition="missing_key"
    )
    assert left.items[0].content_sha256 == content_sha256(left.items[0].content.encode())
    assert left.items[0].source_content_sha256 == left.items[0].content_sha256
    assert left.items[0].evidence_id == "development-observation"
    assert len(left.items) == 1
    projected = json.loads(left.items[0].content)
    assert set(projected) == {"schema_version", "metric_decimal_places", "items"}
    assert [item["id"] for item in projected["items"]] == [
        "performance-comparison",
        "score-source-controls",
    ]
    assert set(projected["items"][0]["payload"]) == {
        "record_count",
        "reference_log_loss",
        "observed_log_loss",
        "log_loss_change",
    }
    assert set(projected["items"][1]["payload"]) == {"score_rows_shared", "score_row_count"}
    visible = canonical_execution_json(left.model_payload())
    for hidden in (
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "missing_key",
        "target_binding_matches_source",
        "source_identity_sha256",
    ):
        assert hidden not in visible


def test_full_witness_remains_visible_but_private_lineage_does_not() -> None:
    mapping = _observation()
    rival = _rival()
    for condition in ("full", "noisy", "misleading"):
        left = build_score_mapping_reader_context(mapping, condition=condition)
        right = build_score_mapping_reader_context(rival, condition=condition)
        assert left.model_payload() != right.model_payload()
        assert len(left.items) == len(right.items) == 1
    six_left = build_score_mapping_reader_context(mapping, condition="missing_key")
    twelve_left = serialize_development_evidence_view(
        mapping, condition="missing_key", metric_decimal_places=12
    )
    twelve_right = serialize_development_evidence_view(
        rival, condition="missing_key", metric_decimal_places=12
    )
    assert twelve_left != twelve_right
    assert six_left.items[0].content.encode() != twelve_left


def test_private_source_hashes_cannot_change_the_reader_envelope() -> None:
    mapping = _observation()
    altered_private_ledger = replace(
        mapping,
        source_identity_sha256="e" * 64,
        reference_features_sha256="f" * 64,
    )
    for condition in ("full", "missing_key", "noisy", "misleading"):
        assert build_score_mapping_reader_context(mapping, condition=condition).model_payload() == (
            build_score_mapping_reader_context(
                altered_private_ledger, condition=condition
            ).model_payload()
        )


@pytest.mark.parametrize("reversed_classes", [False, True])
def test_zero_dose_reader_shows_consumed_healthy_order_not_unused_metadata(
    tmp_path, reversed_classes
) -> None:
    case = _case(tmp_path, reversed_classes=reversed_classes)
    control = zero_dose_observation(
        witness=case.witness,
        source=case.source,
        reference_model=case.model,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
        reference_features=case.reference_features,
        evaluation_features=case.evaluation_features,
    )
    assert control.reference_log_loss == control.observed_log_loss == control.corrected_log_loss
    assert control.model_classes == control.evaluator_classes == case.source.model_classes
    assert control.target_binding_matches_source
    for condition in ("full", "missing_key", "noisy", "misleading"):
        context = build_score_mapping_reader_context(control, condition=condition)
        validate_score_mapping_reader_context(context, observation=control, condition=condition)
        visible = json.loads(context.items[0].content)
        assert visible["items"][0]["payload"]["log_loss_change"] == 0.0
        assert "selected_shard_count" not in context.items[0].content


@pytest.mark.parametrize(
    "mutation",
    ["source_hash", "title", "id", "raw_precision", "catalog", "raw_delta", "changed_rows"],
)
def test_reader_rejects_valid_generic_context_with_hidden_shortcuts(mutation) -> None:
    observation = _observation()
    original = build_score_mapping_reader_context(observation, condition="missing_key")
    item = original.items[0]
    data = item.model_dump(mode="python")
    if mutation == "source_hash":
        data["source_content_sha256"] = observation.source_identity_sha256
    elif mutation == "title":
        data["title"] = "Target binding fault"
    elif mutation == "id":
        data["evidence_id"] = "target-rival-01"
    else:
        content = json.loads(item.content)
        if mutation == "raw_precision":
            content = json.loads(
                serialize_development_evidence_view(
                    observation, condition="missing_key", metric_decimal_places=12
                )
            )
        elif mutation == "raw_delta":
            content["items"][0]["payload"]["raw_delta"] = 0.100000000000003
        elif mutation == "changed_rows":
            content["items"][0]["payload"]["changed_target_rows"] = 2
        else:
            content["catalog"] = [{"source_fingerprint": "a" * 64}]
        data["content"] = json.dumps(content, sort_keys=True, separators=(",", ":"))
        data["content_sha256"] = content_sha256(data["content"].encode())
    altered_item = type(item).model_validate(data)
    identity = {
        "schema_version": original.schema_version,
        "items": (altered_item.model_dump(mode="json"),),
    }
    digest = canonical_execution_sha256(identity)
    # These pass the production generic schema and immutable hash checks.
    # The reader-specific boundary must reject the additional information.
    altered = ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}", context_sha256=digest, items=(altered_item,)
    )
    with pytest.raises(ScoreMappingEvidenceError, match="authorized reader projection"):
        validate_score_mapping_reader_context(
            altered, observation=observation, condition="missing_key"
        )


def test_reader_does_not_hide_a_six_decimal_rounding_boundary() -> None:
    mapping = replace(_observation(), observed_log_loss=0.60000049)
    rival = replace(_rival(), observed_log_loss=0.60000051)
    assert abs(mapping.observed_log_loss - rival.observed_log_loss) < 0.0000005
    assert build_score_mapping_reader_context(mapping, condition="missing_key").model_payload() != (
        build_score_mapping_reader_context(rival, condition="missing_key").model_payload()
    )


def test_gateway_passes_only_projected_content_to_offline_adapter() -> None:
    base = _request()
    calls = []

    class CapturingAdapter:
        def __init__(self, fake):
            self._fake = fake
            self.binding = fake.binding

        def invoke(self, call):
            calls.append(call)
            return self._fake.invoke(call)

    for index, observation in enumerate((_observation(), _rival()), start=1):
        context = build_score_mapping_reader_context(observation, condition="missing_key")
        validate_score_mapping_reader_context(
            context, observation=observation, condition="missing_key"
        )
        request = prepare_gateway_request(
            manifest=base.initial_attempt.manifest,
            case=_bound_case(base, context, case_id=f"ev-{str(index) * 64}"),
            model_policy=base.initial_attempt.model_policy,
            context=context,
            prompt_text=base.prompt_text,
            response_schema=json.loads(base.response_schema_json),
            runtime_policy=base.runtime_policy,
        )
        fake = _adapter(request, (_response("valid_response"),))
        result = execute_gateway_request(
            request,
            adapter=CapturingAdapter(fake),
            clock=_Clock(),
            cancellation=_Cancellation(),
        )
        assert result.status == "parsed"
    assert len(calls) == 2
    assert calls[0].context_json == calls[1].context_json
    assert calls[0].prompt_text == calls[1].prompt_text
    assert calls[0].response_schema_json == calls[1].response_schema_json
    assert json.loads(calls[0].context_json) == json.loads(
        canonical_execution_json(
            build_score_mapping_reader_context(
                _observation(), condition="missing_key"
            ).model_payload()
        )
    )


@pytest.mark.parametrize(
    ("condition", "same_messages"),
    [("missing_key", True), ("full", False), ("noisy", False), ("misleading", False)],
)
def test_openai_mock_client_preserves_reader_information_boundary(condition, same_messages) -> None:
    base = _openai_request()
    completions = _CapturingCompletions()
    adapter = OpenAIChatCompletionsGatewayAdapter(
        client=_Client(completions),
        model_policy=base.initial_attempt.model_policy,
        policy=_policy(),
    )
    for index, observation in enumerate((_observation(), _rival()), start=1):
        context = build_score_mapping_reader_context(observation, condition=condition)
        validate_score_mapping_reader_context(context, observation=observation, condition=condition)
        request = prepare_gateway_request(
            manifest=base.initial_attempt.manifest,
            case=_bound_case(base, context, case_id=f"ev-{str(index) * 64}"),
            model_policy=base.initial_attempt.model_policy,
            context=context,
            prompt_text=base.prompt_text,
            response_schema=json.loads(base.response_schema_json),
            runtime_policy=base.runtime_policy,
        )
        attempt = request.initial_attempt
        adapter.invoke(
            ProviderCall(
                request_identity_sha256=attempt.request_identity_sha256,
                attempt_id=attempt.attempt_id,
                attempt_identity_sha256=attempt.attempt_identity_sha256,
                attempt_ordinal=attempt.attempt_ordinal,
                context_sha256=attempt.context_sha256,
                prompt_sha256=attempt.prompt_sha256,
                response_schema_sha256=attempt.response_schema_sha256,
                context_json=canonical_execution_json(context.model_payload()),
                prompt_text=request.prompt_text,
                response_schema_json=request.response_schema_json,
                runtime_policy=request.runtime_policy,
            )
        )
    assert len(completions.calls) == 2
    assert (completions.calls[0]["messages"] == completions.calls[1]["messages"]) is same_messages
    assert completions.calls[0]["messages"][0] == completions.calls[1]["messages"][0]
    for setting in (
        "model",
        "response_format",
        "temperature",
        "top_p",
        "seed",
        "max_tokens",
        "n",
        "store",
        "stream",
        "timeout",
    ):
        assert completions.calls[0][setting] == completions.calls[1][setting]
    assert completions.calls[0]["extra_headers"] != completions.calls[1]["extra_headers"]
    assert completions.calls[0]["messages"][1]["content"] == canonical_execution_json(
        build_score_mapping_reader_context(_observation(), condition=condition).model_payload()
    )


def test_invalid_condition_fails_closed() -> None:
    with pytest.raises(ScoreMappingEvidenceError):
        build_score_mapping_reader_context(  # type: ignore[arg-type]
            _observation(), condition="unregistered"
        )

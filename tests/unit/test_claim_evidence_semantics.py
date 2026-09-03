from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_evidence_census import (
    ObservedEvidenceCensus,
    build_observed_evidence_census,
    validate_observed_evidence_census,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
    ModelVisibleEvidenceItem,
    build_evidence_binding,
    build_evidence_semantics_policy,
    build_relation_assignment_request,
    build_visible_evidence_item,
    load_evidence_semantics_policy,
    parse_relation_assignment,
    validate_request_evidence_binding,
    visible_relations_from_assignment,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.runtime import validate_response_schema

ROOT = Path(__file__).resolve().parents[2]


def _request(*, condition: str = "full", variant: str = "A1") -> ClaimCorpusRequest:
    payload = {
        "family_id": "ccf-data-drift-categorical-contract-shift-80-1",
        "family_sha256": "1" * 64,
        "mechanism": "data_drift",
        "family_role": "primary",
        "evidence_condition": condition,
        "variant": variant,
        "seed": 9101,
        "source_partition": "development",
        "provider_call_authorized": False,
    }
    return ClaimCorpusRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _item(evidence_id: str = "metric-comparison") -> ModelVisibleEvidenceItem:
    return build_visible_evidence_item(
        evidence_id=evidence_id,
        kind="metric",
        title="Observed accuracy comparison",
        content="Reference accuracy is 0.81 and observed accuracy is 0.72.",
        source_content_sha256="2" * 64,
    )


def _assignment_request() -> ClaimRelationAssignmentRequest:
    binding = build_evidence_binding(
        _request(),
        items=(_item(),),
        source_projection_sha256="3" * 64,
    )
    return build_relation_assignment_request(
        source_output_sha256="4" * 64,
        claim_local_id="claim-1",
        claim_text="Observed accuracy decreased.",
        claim_type="evidence_statement",
        cited_evidence_ids=("metric-comparison",),
        evidence_binding=binding,
    )


def _complete_evidence_census() -> tuple[ClaimCorpusRequestCensus, ObservedEvidenceCensus]:
    census = ClaimCorpusRequestCensus.model_validate_json(
        (ROOT / "configs/evaluation/claim_support_request_census.json").read_bytes()
    )
    contexts: dict[tuple[str, str], ClaimCorpusRequest] = {}
    for request in census.primary_requests:
        contexts.setdefault((request.family_id, request.evidence_condition), request)
    bindings = tuple(
        build_evidence_binding(
            request,
            items=(
                build_visible_evidence_item(
                    evidence_id=f"observed-{index:02d}",
                    kind="artifact",
                    title=f"Observed context {index:02d}",
                    content=f"Observed development evidence for context {index:02d}.",
                    source_content_sha256=request.request_sha256,
                ),
            ),
            source_projection_sha256=request.request_sha256,
        )
        for index, request in enumerate(contexts.values(), start=1)
    )
    return census, build_observed_evidence_census(census, bindings)


def test_tracked_policy_matches_code_and_all_outcome_flags_are_false() -> None:
    expected = build_evidence_semantics_policy(ROOT)
    actual = load_evidence_semantics_policy(ROOT)

    assert actual == expected
    assert actual.maximum_relation_requests_primary == 1800
    assert actual.permitted_provider_input_fields == (
        "claim_text",
        "claim_type",
        "visible_evidence",
    )
    assert not actual.provider_calls_executed
    assert not actual.outputs_generated
    assert not actual.automatic_labels_generated
    assert not actual.human_annotations_collected
    assert not actual.main_or_sealed_outcomes_opened
    validate_response_schema(actual.response_schema)


def test_model_payload_excludes_evaluator_side_identity() -> None:
    binding = build_evidence_binding(
        _request(),
        items=(_item(),),
        source_projection_sha256="3" * 64,
    )

    payload = binding.model_payload()
    rendered = str(payload)
    assert set(payload) == {"schema_version", "context_id", "items", "context_sha256"}
    for forbidden in ("family_id", "mechanism", "evidence_condition", "variant"):
        assert forbidden not in rendered


def test_evidence_content_tamper_and_evaluator_markers_fail_closed() -> None:
    with pytest.raises(ValidationError, match="content hash"):
        ModelVisibleEvidenceItem(
            evidence_id="metric-comparison",
            kind="metric",
            title="Observed comparison",
            content="Observed accuracy is 0.72.",
            content_sha256="0" * 64,
            source_content_sha256="2" * 64,
        )
    with pytest.raises(ValidationError, match="evaluator-only"):
        build_visible_evidence_item(
            evidence_id="metric-comparison",
            kind="metric",
            title="Observed comparison",
            content="The hidden_ground_truth is data drift.",
            source_content_sha256="2" * 64,
        )


def test_condition_encoded_in_visible_id_fails_closed() -> None:
    with pytest.raises(ValidationError, match="condition"):
        _item("metric-full")


def test_cross_condition_binding_replay_is_rejected() -> None:
    binding = build_evidence_binding(
        _request(condition="full"),
        items=(_item(),),
        source_projection_sha256="3" * 64,
    )

    with pytest.raises(ClaimCorpusContractError, match="different request context"):
        validate_request_evidence_binding(_request(condition="noisy"), binding)


def test_observed_evidence_census_requires_exact_primary_contexts() -> None:
    census, observed = _complete_evidence_census()

    assert len(observed.bindings) == 45
    assert validate_observed_evidence_census(census, observed) == observed
    with pytest.raises(ClaimCorpusContractError, match="exactly once"):
        build_observed_evidence_census(census, observed.bindings[:-1])


def test_relation_provider_payload_contains_only_frozen_three_fields() -> None:
    request = _assignment_request()
    payload = request.provider_payload()

    assert tuple(payload) == ("claim_text", "claim_type", "visible_evidence")
    rendered = str(payload)
    for forbidden in ("family_id", "mechanism", "evidence_condition", "variant"):
        assert forbidden not in rendered


def test_relation_response_must_cover_each_cited_evidence_once_in_order() -> None:
    request = _assignment_request()
    with pytest.raises(ClaimCorpusContractError, match="cover cited evidence"):
        parse_relation_assignment(
            request,
            {
                "decisions": (
                    {
                        "evidence_id": "another-evidence",
                        "relation_polarity": "neutral",
                        "relation_scope": "none",
                    },
                )
            },
        )


def test_relation_response_cannot_be_replayed_for_another_claim() -> None:
    first = _assignment_request()
    binding = build_evidence_binding(
        _request(),
        items=(_item(),),
        source_projection_sha256="3" * 64,
    )
    second = build_relation_assignment_request(
        source_output_sha256="4" * 64,
        claim_local_id="claim-2",
        claim_text="Observed accuracy remained stable.",
        claim_type="evidence_statement",
        cited_evidence_ids=("metric-comparison",),
        evidence_binding=binding,
    )
    response = parse_relation_assignment(
        first,
        {
            "decisions": (
                {
                    "evidence_id": "metric-comparison",
                    "relation_polarity": "supports",
                    "relation_scope": "entire",
                },
            )
        },
    )

    with pytest.raises(ClaimCorpusContractError, match="another claim"):
        visible_relations_from_assignment(second, response)


def test_relation_text_is_joined_from_immutable_context_not_model_output() -> None:
    request = _assignment_request()
    response = parse_relation_assignment(
        request,
        {
            "decisions": (
                {
                    "evidence_id": "metric-comparison",
                    "relation_polarity": "supports",
                    "relation_scope": "entire",
                },
            )
        },
    )

    relations = visible_relations_from_assignment(
        request,
        ClaimRelationAssignmentResponse.model_validate(response.model_dump(mode="python")),
    )
    assert relations[0].text == "Reference accuracy is 0.81 and observed accuracy is 0.72."
    assert relations[0].relation_polarity == "supports"

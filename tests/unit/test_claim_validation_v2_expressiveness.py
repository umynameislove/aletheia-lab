"""Regression tests for the prospective V2 source-claim expressiveness review."""

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    AtomicClaimV2,
    DiagnosisOutputV2,
    MaterialClaimPart,
)
from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    PROVIDER_VARIANTS,
    SHARED_EXPRESSIVENESS_INSTRUCTION,
    ClaimValidationV2ExpressivenessError,
    build_measurement_witness_claim,
    build_v2_expressiveness_amendment,
    build_v2_expressiveness_review,
    canonical_json,
    select_amended_source_claims,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import _load_inputs
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def amendment():
    return build_v2_expressiveness_amendment(ROOT)


@pytest.fixture(scope="module")
def review(amendment):
    return build_v2_expressiveness_review(ROOT, amendment)


def _output(claims: tuple[AtomicClaimV2, ...]) -> DiagnosisOutputV2:
    payload = {
        "schema_version": "diagnosis-output/2",
        "output_status": "completed",
        "atomic_claims": tuple(item.model_dump(mode="json") for item in claims),
        "abstention_reason": None,
        "parse_failure_code": None,
        "source_record_sha256": "a" * 64,
    }
    return DiagnosisOutputV2.model_validate(
        {
            **payload,
            "atomic_claims": claims,
            "output_sha256": canonical_execution_sha256(payload),
        }
    )


def test_review_proves_exact_effective_capacity_without_provider_calls(review):
    assert review.status == "v2_expressiveness_review_pass_qualification_pending"
    assert review.evidence_context_count == review.witness_context_count == 45
    assert len({item.witness_sha256 for item in review.witnesses}) == 45
    assert {
        item.frame: (
            item.context_count,
            item.family_count,
            item.scheduled_diagnosis_cell_count,
        )
        for item in review.effective_frame_capacity
    } == {
        "natural_context": (45, 15, 360),
        "support_withdrawal": (30, 15, 240),
        "partial_support_projection": (30, 15, 240),
        "direct_counterevidence": (45, 15, 360),
    }
    assert review.source_claim_expressiveness_review_required is False
    assert review.live_qualification_authorized is False
    assert review.provider_calls_executed is False
    assert review.claims_materialized is False
    assert review.blind_packets_generated is False
    assert review.human_annotations_collected is False


def test_witness_census_preserves_missing_key_boundary(review):
    counts = Counter(
        (item.evidence_condition, item.challenge_frames_eligible)
        for item in review.witnesses
    )
    assert counts == {
        (
            "full",
            (
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            ),
        ): 15,
        ("missing_key", ("direct_counterevidence",)): 15,
        (
            "noisy",
            (
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            ),
        ): 15,
    }


def test_amendment_is_outcome_blind_and_supersedes_ambiguous_probes(amendment):
    assert amendment.v1_relation_outcomes_read is False
    assert amendment.v1_label_frequencies_read is False
    assert amendment.legacy_runtime_qualification_probes_superseded is True
    assert amendment.deterministic_b0_uses_same_witness_algorithm is True
    assert amendment.additional_variant_claims_permitted is True
    assert amendment.abstention_preserved is True
    assert tuple(item.variant for item in amendment.qualification_probes) == PROVIDER_VARIANTS
    assert len({item.qualification_request_sha256 for item in amendment.qualification_probes}) == 7
    for probe in amendment.qualification_probes:
        assert SHARED_EXPRESSIVENESS_INSTRUCTION in probe.prompt_text
        assert probe.synthetic_only and not probe.admitted_to_corpus
        assert all(item.title == "Synthetic V2 expressiveness probe" for item in probe.context.items)
        assert all("ccf-" not in item.content for item in probe.context.items)
        assert json.loads(probe.response_schema_json)["type"] == "object"
        assert build_measurement_witness_claim(probe.context).claim_local_id == "claim-1"


def test_witness_builder_uses_exact_visible_base10_values():
    _, _, evidence = _load_inputs(ROOT)
    by_condition = {
        item.evidence_condition: item.visible_context for item in evidence.bindings[:3]
    }
    full = build_measurement_witness_claim(by_condition["full"])
    missing = build_measurement_witness_claim(by_condition["missing_key"])
    assert full.visible_evidence_ids == (
        "ev-key-measurement",
        "ev-performance-summary",
    )
    assert len(full.material_parts) == 2
    assert len(missing.material_parts) == 1
    assert missing.visible_evidence_ids == ("ev-performance-summary",)
    for claim in (full, missing):
        assert claim.claim_text == "; ".join(part.text for part in claim.material_parts)
        assert all(" = " in part.text for part in claim.material_parts)


def test_source_selection_rejects_prose_or_reordered_witness():
    _, _, evidence = _load_inputs(ROOT)
    context = evidence.bindings[0].visible_context
    witness = build_measurement_witness_claim(context)
    prose = AtomicClaimV2(
        claim_local_id="claim-2",
        claim_type="cause_assertion",
        claim_text="A bounded diagnostic hypothesis.",
        material_parts=(
            MaterialClaimPart(part_id="part-1", text="A bounded diagnostic hypothesis."),
        ),
        visible_evidence_ids=("ev-performance-summary",),
    )
    output = _output((witness, prose))
    assert select_amended_source_claims(output, context) == (witness, prose)
    first_prose = prose.model_copy(update={"claim_local_id": "claim-1"})
    second_witness = witness.model_copy(update={"claim_local_id": "claim-2"})
    with pytest.raises(ClaimValidationV2ExpressivenessError, match="exact required"):
        select_amended_source_claims(_output((first_prose, second_witness)), context)


def test_hashes_are_stable_and_tampering_fails_closed(amendment, review):
    assert canonical_json(amendment) == canonical_json(
        build_v2_expressiveness_amendment(ROOT)
    )
    assert canonical_json(review) == canonical_json(
        build_v2_expressiveness_review(ROOT, amendment)
    )
    payload = amendment.model_dump(mode="python")
    payload["measurement_path_priority"] = tuple(
        reversed(payload["measurement_path_priority"])
    )
    with pytest.raises(ValidationError):
        type(amendment).model_validate(payload)

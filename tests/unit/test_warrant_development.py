"""Synthetic conformance is not a semantic reference for generated real prose."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.warrant_development import (
    WARRANT_JUDGE_PROMPT,
    WRITER_PROMPT,
    DevelopmentCall,
    WarrantCase,
    WarrantCaseResult,
    WarrantClaim,
    WarrantEvidence,
    WarrantReference,
    claim_sha256,
    output_sha256,
    run_warrant_case,
    validate_reference,
    writer_response_schema,
)
from aletheia_lab.evaluation.warrant_development_analysis import (
    _support_overgraded,
    analyze_warrant_results,
)
from aletheia_lab.model_gateway.openai import _openai_response_format


def case(
    *, case_id: str = "c1", family: str = "f1", output: str = "o1", component: str = "synthetic"
) -> WarrantCase:
    return WarrantCase(
        case_id=case_id,
        family_id=family,
        source_output_id=output,
        component=component,
        source_claim=WarrantClaim(
            claim_text="Loss was 0.4 and the change caused improvement.",
            claim_type="cause_assertion",
        ),
        visible_evidence=(
            WarrantEvidence(
                evidence_id="e1",
                kind="metric",
                title="Observation",
                content="Development loss was 0.4. No intervention was performed.",
            ),
        ),
        required_unit_ids=("loss_observation",),
    )


def call(value: object) -> DevelopmentCall:
    return DevelopmentCall(
        status="completed",
        payload_json=json.dumps(value),
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.00006,
        latency_seconds=0.01,
        provider_attempted=False,
    )


class FakeCaller:
    def __init__(
        self, *, writer_response: object | None = None, invalid_warrant: bool = False
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.writer_response = (
            writer_response
            if writer_response is not None
            else {
                "status": "completed",
                "claims": [
                    {
                        "claim_text": "Development loss was 0.4; no causal attribution is established.",
                        "claim_type": "evidence_statement",
                    }
                ],
            }
        )
        self.invalid_warrant = invalid_warrant

    def invoke(
        self, *, prompt: str, payload: dict[str, object], schema: dict[str, object]
    ) -> DevelopmentCall:
        self.calls.append({"prompt": prompt, "payload": payload, "schema": schema})
        if prompt == WRITER_PROMPT:
            return call(self.writer_response)
        if prompt == WARRANT_JUDGE_PROMPT and self.invalid_warrant:
            return call({"decisions": []})
        scope = (
            "partial"
            if prompt == WARRANT_JUDGE_PROMPT and payload["claim_type"] == "cause_assertion"
            else "entire"
        )
        evidence_id = payload["visible_evidence"][0]["evidence_id"]
        return call(
            {
                "decisions": [
                    {
                        "evidence_id": evidence_id,
                        "relation_polarity": "supports",
                        "relation_scope": scope,
                    }
                ]
            }
        )


def reference(
    result: WarrantCaseResult,
    writer_index: int,
    *,
    labels: tuple[str, ...],
    coverage: tuple[str, ...] | None,
) -> WarrantReference:
    output = result.writers[writer_index].output
    return WarrantReference(
        case_id=result.case.case_id,
        output_sha256=output_sha256(output),
        evidence_sha256=result.case.evidence_sha256(),
        claim_sha256=tuple(claim_sha256(c) for c in output.claims),
        labels=labels,
        warranted_unit_ids=coverage,
        basis="synthetic_oracle",
    )


def test_exact_writer_outputs_are_crossed_with_both_judges_without_private_fields() -> None:
    caller = FakeCaller()
    result = run_warrant_case(case(), caller)
    assert len(caller.calls) == 5
    assert result.writers[0].output.claims == (result.case.source_claim,)
    for index in (0, 1):
        output = result.writers[index]
        assert set(output.judgments) == {"legacy", "warrant"}
        assert (
            output.judgments["legacy"][0].claim_sha256
            == output.judgments["warrant"][0].claim_sha256
        )
    assert result.writers[0].judgments["legacy"][0].label == "fully_supported"
    assert result.writers[0].judgments["warrant"][0].label == "partially_supported"
    for invocation in caller.calls:
        assert not {
            "family_id",
            "case_id",
            "source_output_id",
            "required_unit_ids",
            "component",
            "human_final_label",
        }.intersection(invocation["payload"])
        assert (
            invocation["payload"]["visible_evidence"]
            == result.case.writer_payload()["visible_evidence"]
        )
    _openai_response_format(json.dumps(writer_response_schema()))


def test_factorial_interaction_is_apparent_support_not_human_correctness() -> None:
    result = run_warrant_case(case(), FakeCaller())
    refs = (
        reference(result, 0, labels=("partially_supported",), coverage=()),
        reference(result, 1, labels=("fully_supported",), coverage=("loss_observation",)),
    )
    report = analyze_warrant_results((result,), refs)
    cells = report["components"]["synthetic"]["cells"]
    assert cells["cached/legacy"]["judge_metrics"]["false_full_support_rate"] == 1.0
    assert cells["cached/warrant"]["judge_metrics"]["false_full_support_rate"] == 0.0
    assert cells["bounded_rewrite/warrant"]["family_macro_warranted_coverage"] == 1.0
    assert (
        report["components"]["synthetic"]["paired_apparent_support"]["contrasts"]["interaction"]
        == 1.0
    )
    assert report["candidate_selection_reference_complete"]
    assert report["selected_winner"] is None


def test_support_overgrading_respects_unordered_absence_and_conflict() -> None:
    assert not _support_overgraded("contradicted", "unsupported")
    assert not _support_overgraded("unsupported", "contradicted")
    assert _support_overgraded("contradicted", "partially_supported")
    assert _support_overgraded("unsupported", "fully_supported")
    assert not _support_overgraded("partially_supported", None)


def test_force_false_full_uses_nonfull_gold_denominator() -> None:
    result = run_warrant_case(case(), FakeCaller())
    ref = reference(result, 0, labels=("partially_supported",), coverage=())
    cell = analyze_warrant_results((result,), (ref,))["components"]["synthetic"]["cells"][
        "cached/legacy"
    ]
    assert cell["source_force_nonfull_reference_count"] == 1
    assert cell["source_force_false_full_count"] == 1
    assert cell["source_force_false_full_rate"] == 1.0
    assert cell["family_macro_source_force_false_full"] == 1.0


@pytest.mark.parametrize("changed", ["text", "type", "evidence", "foreign_output", "unit"])
def test_reference_is_invalidated_by_any_claim_or_evidence_change(changed: str) -> None:
    result = run_warrant_case(case(), FakeCaller())
    ref = reference(result, 1, labels=("fully_supported",), coverage=("loss_observation",))
    source = result.case
    output = result.writers[1].output
    if changed in ("text", "type"):
        c = output.claims[0].model_copy(
            update={"claim_text": "Changed observation."}
            if changed == "text"
            else {"claim_type": "other"}
        )
        output = output.model_copy(update={"claims": (c,)})
    elif changed == "evidence":
        source = source.model_copy(
            update={
                "visible_evidence": (
                    source.visible_evidence[0].model_copy(update={"content": "Changed evidence."}),
                )
            }
        )
    else:
        ref = ref.model_copy(
            update={"output_sha256": "other"}
            if changed == "foreign_output"
            else {"warranted_unit_ids": ("invented",)}
        )
    with pytest.raises(ValueError):
        validate_reference(source, output, ref)


def test_empty_writer_has_zero_coverage_and_undefined_precision_not_perfect_success() -> None:
    result = run_warrant_case(
        case(), FakeCaller(writer_response={"status": "abstained", "claims": []})
    )
    refs = (
        reference(result, 0, labels=("partially_supported",), coverage=()),
        reference(result, 1, labels=(), coverage=()),
    )
    report = analyze_warrant_results((result,), refs)
    cell = report["components"]["synthetic"]["cells"]["bounded_rewrite/warrant"]
    assert cell["family_macro_warranted_coverage"] == 0.0
    assert cell["family_macro_reference_full_support"] is None
    assert cell["judge_metrics"]["macro_f1_class_count"] == 0
    assert cell["terminal_counts"] == {"abstained": 1}
    assert report["components"]["synthetic"]["paired_apparent_support"]["paired_case_count"] == 0
    invented_credit = reference(result, 1, labels=(), coverage=("loss_observation",))
    with pytest.raises(ValueError):
        validate_reference(result.case, result.writers[1].output, invented_credit)


@pytest.mark.parametrize(
    "response",
    [
        {"status": "completed", "claims": []},
        {"status": "technical_failure", "claims": []},
        {
            "status": "completed",
            "claims": [{"claim_text": "Duplicate.", "claim_type": "other"}] * 2,
        },
        {
            "status": "abstained",
            "claims": [{"claim_text": "Still asserts.", "claim_type": "other"}],
        },
    ],
)
def test_malformed_or_duplicate_output_is_retained_as_technical_failure(response: object) -> None:
    result = run_warrant_case(case(), FakeCaller(writer_response=response))
    assert result.writers[1].output.status == "technical_failure"
    assert len(result.writers[1].output.claims) == 0


def test_invalid_relation_census_is_not_silently_scored_as_unsupported() -> None:
    result = run_warrant_case(case(), FakeCaller(invalid_warrant=True))
    report = analyze_warrant_results((result,))
    assert result.writers[1].judgments["warrant"][0].label is None
    assert (
        report["components"]["synthetic"]["cells"]["bounded_rewrite/warrant"][
            "invalid_judgment_count"
        ]
        == 1
    )
    assert not report["candidate_selection_reference_complete"]
    assert report["components"]["synthetic"]["paired_apparent_support"]["paired_case_count"] == 0
    ref = reference(result, 1, labels=("fully_supported",), coverage=())
    cell = analyze_warrant_results((result,), (ref,))["components"]["synthetic"]["cells"][
        "bounded_rewrite/warrant"
    ]
    assert (
        cell["judge_metrics"]["confusion_reference_by_prediction"]["fully_supported"][
            "invalid_response"
        ]
        == 1
    )
    assert cell["family_macro_judge_error_including_invalid"] == 1.0


def test_unreviewed_coverage_is_unknown_and_old_labels_cannot_gold_new_text() -> None:
    result = run_warrant_case(case(), FakeCaller())
    ref = reference(result, 0, labels=("partially_supported",), coverage=None)
    report = analyze_warrant_results((result,), (ref,))
    assert (
        report["components"]["synthetic"]["cells"]["cached/legacy"][
            "family_macro_warranted_coverage"
        ]
        is None
    )
    assert not report["candidate_selection_reference_complete"]
    assert report["selected_winner"] is None


def test_synthetic_reference_cannot_be_reused_for_real_prose() -> None:
    result = run_warrant_case(case(component="probability"), FakeCaller())
    ref = reference(result, 0, labels=("partially_supported",), coverage=())
    with pytest.raises(ValueError, match="synthetic oracle"):
        analyze_warrant_results((result,), (ref,))


def test_nested_mean_does_not_turn_more_claims_or_outputs_into_more_families() -> None:
    a = run_warrant_case(case(case_id="a", family="f1", output="same"), FakeCaller())
    b = run_warrant_case(case(case_id="b", family="f1", output="same"), FakeCaller())
    c = run_warrant_case(case(case_id="c", family="f2", output="other"), FakeCaller())
    refs = tuple(
        reference(
            result,
            0,
            labels=("fully_supported",),
            coverage=("loss_observation",) if result is c else (),
        )
        for result in (a, b, c)
    )
    cell = analyze_warrant_results((a, b, c), refs)["components"]["synthetic"]["cells"][
        "cached/legacy"
    ]
    assert cell["family_macro_warranted_coverage"] == 0.5


def test_foreign_duplicate_results_and_missing_judge_rejected() -> None:
    result = run_warrant_case(case(), FakeCaller())
    ref = reference(result, 0, labels=("partially_supported",), coverage=())
    with pytest.raises(ValueError):
        analyze_warrant_results((result, result), ())
    with pytest.raises(ValueError):
        analyze_warrant_results((result,), (ref, ref))
    values = result.model_dump(mode="json")
    del values["writers"][0]["judgments"]["legacy"]
    with pytest.raises(ValidationError):
        WarrantCaseResult.model_validate_json(json.dumps(values))


def test_moved_source_claim_cannot_replace_the_cached_control() -> None:
    result = run_warrant_case(case(), FakeCaller())
    values = result.model_dump(mode="json")
    values["case"]["source_claim"]["claim_text"] = "Different claim."
    with pytest.raises(ValidationError):
        WarrantCaseResult.model_validate_json(json.dumps(values))


def test_repeated_coverage_units_rejected_but_partial_claim_can_retain_core() -> None:
    result = run_warrant_case(case(), FakeCaller())
    with pytest.raises(ValidationError):
        reference(
            result,
            0,
            labels=("partially_supported",),
            coverage=("loss_observation", "loss_observation"),
        )
    ref = reference(result, 0, labels=("partially_supported",), coverage=("loss_observation",))
    validate_reference(result.case, result.writers[0].output, ref)
    report = analyze_warrant_results((result,), (ref,))
    assert (
        report["components"]["synthetic"]["cells"]["cached/legacy"][
            "family_macro_warranted_coverage"
        ]
        == 1.0
    )


def test_cost_and_tokens_include_generation_and_all_four_judge_cells() -> None:
    report = analyze_warrant_results((run_warrant_case(case(), FakeCaller()),))
    assert report["caller_invocation_count"] == 5
    assert report["provider_invocation_count"] == 0
    assert report["input_tokens"] == 50
    assert report["output_tokens"] == 25
    assert report["estimated_cost_usd"] == pytest.approx(0.0003)


def test_invalid_judge_response_stays_in_reference_denominator() -> None:
    result = run_warrant_case(case(), FakeCaller(invalid_warrant=True))
    ref = reference(result, 1, labels=("fully_supported",), coverage=("loss_observation",))
    cell = analyze_warrant_results((result,), (ref,))["components"]["synthetic"]["cells"][
        "bounded_rewrite/warrant"
    ]
    assert cell["judge_metrics"]["per_class"]["fully_supported"]["reference_count"] == 1
    assert cell["judge_metrics"]["per_class"]["fully_supported"]["recall"] == 0.0
    assert (
        cell["judge_metrics"]["confusion_reference_by_prediction"]["fully_supported"][
            "invalid_response"
        ]
        == 1
    )
    assert cell["family_macro_judge_error_including_invalid"] == 1.0


@pytest.mark.parametrize("values", [{"content": "x" * 4097}, {"evidence_id": "bad id"}])
def test_preflight_rejects_evidence_that_downstream_cannot_parse(values: dict[str, str]) -> None:
    raw = case().visible_evidence[0].model_dump()
    with pytest.raises(ValidationError):
        WarrantEvidence.model_validate({**raw, **values})

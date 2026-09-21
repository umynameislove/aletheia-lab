from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ALL_CONDITIONS,
    CONTROLLED_VARIANTS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainClaim,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
    DiagnosisMainObservedRecord,
    analyse_diagnosis_main,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "configs/evaluation/diagnosis_main_analysis_plan_v3.json"


def _plan() -> DiagnosisMainAnalysisPlan:
    return DiagnosisMainAnalysisPlan.model_validate_json(PLAN_PATH.read_bytes())


def _mechanism_and_dataset(index: int) -> tuple[str, str]:
    if index <= 10:
        return "data_drift", "dataset-a" if index <= 5 else "dataset-b"
    if index <= 20:
        return "preprocessing_mismatch", "dataset-a" if index <= 15 else "dataset-b"
    return "label_noise", "dataset-a" if index <= 26 else "dataset-b"


def _family(index: int) -> DiagnosisMainFamily:
    mechanism, dataset = _mechanism_and_dataset(index)
    payload = {
        "family_id": f"family-{index:02d}",
        "mechanism": mechanism,
        "dataset_id": dataset,
        "source_unit_id": f"source-unit-{index:02d}",
        "source_record_sha256": canonical_execution_sha256(
            {"source_unit": index, "mechanism": mechanism, "dataset": dataset}
        ),
        "source_artifact_sha256": canonical_execution_sha256(
            {"source_artifact": mechanism, "dataset": dataset}
        ),
        "intervention_template_id": f"template-{mechanism}",
        "superfamily_id": f"superfamily-{dataset}-{mechanism}",
        "source_partition": "prior_registered_outcome",
    }
    return DiagnosisMainFamily.model_validate(
        {**payload, "family_sha256": canonical_execution_sha256(payload)}
    )


def _counterevidence_pairs(
    families: tuple[DiagnosisMainFamily, ...],
) -> dict[str, str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for family in families:
        groups[family.superfamily_id].append(family.family_id)
    pairs: dict[str, str] = {}
    for family_ids in groups.values():
        ordered = sorted(family_ids)
        for offset, family_id in enumerate(ordered):
            pairs[family_id] = ordered[(offset + 1) % len(ordered)]
    return pairs


def _context(
    family: DiagnosisMainFamily,
    condition: str,
    *,
    counterevidence_source: str | None,
) -> DiagnosisMainContext:
    response_modes = {
        "full": "diagnose",
        "missing_key": "abstain_or_request_evidence",
        "noisy": "diagnose_with_uncertainty",
        "counterevidence": "acknowledge_conflict_or_abstain",
    }
    payload = {
        "context_id": f"context-{family.family_id}-{condition}",
        "case_family_id": family.family_id,
        "evidence_condition": condition,
        "expected_response_mode": response_modes[condition],
        "visible_context_sha256": canonical_execution_sha256(
            {"visible": family.family_id, "condition": condition}
        ),
        "normalized_content_sha256": canonical_execution_sha256(
            {"normalized": family.family_id, "condition": condition}
        ),
        "semantic_cluster_id": f"semantic-{family.family_id}",
        "counterevidence_source_family_id": counterevidence_source,
    }
    return DiagnosisMainContext.model_validate(
        {**payload, "context_sha256": canonical_execution_sha256(payload)}
    )


def _request(context: DiagnosisMainContext, variant: str) -> DiagnosisMainExpectedRequest:
    payload = {
        "request_id": f"request-{context.context_id}-{variant}",
        "context_id": context.context_id,
        "variant": variant,
    }
    return DiagnosisMainExpectedRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _census() -> DiagnosisMainAnalysisCensus:
    families = tuple(_family(index) for index in range(1, 33))
    pairs = _counterevidence_pairs(families)
    contexts = tuple(
        sorted(
            (
                _context(
                    family,
                    condition,
                    counterevidence_source=(
                        pairs[family.family_id] if condition == "counterevidence" else None
                    ),
                )
                for family in families
                for condition in ALL_CONDITIONS
            ),
            key=lambda item: item.context_id,
        )
    )
    requests = tuple(
        sorted(
            (_request(context, variant) for context in contexts for variant in CONTROLLED_VARIANTS),
            key=lambda item: item.request_id,
        )
    )
    payload = {
        "schema_version": "diagnosis-main-analysis-census/v2",
        "source_partition": "sealed_main",
        "family_count": 32,
        "context_count": 128,
        "request_count": 1024,
        "exact_duplicate_context_count": 0,
        "cross_family_semantic_duplicate_count": 0,
        "families": tuple(item.model_dump(mode="json") for item in families),
        "contexts": tuple(item.model_dump(mode="json") for item in contexts),
        "requests": tuple(item.model_dump(mode="json") for item in requests),
    }
    return DiagnosisMainAnalysisCensus.model_validate(
        {
            **payload,
            "families": families,
            "contexts": contexts,
            "requests": requests,
            "census_sha256": canonical_execution_sha256(payload),
        }
    )


def _claim(label: str, *, required: bool) -> DiagnosisMainClaim:
    return DiagnosisMainClaim(
        claim_id="claim-1",
        claim_type="cause_assertion",
        support_label=label,  # type: ignore[arg-type]
        citation_required=required,
        citation_present=required,
        citation_ids_valid=required,
    )


def _input(
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    *,
    omit_last: bool = False,
    fail_first_a3_full: bool = False,
    abstain_a3_full: bool = False,
    abstain_b1_counterevidence: bool = False,
) -> DiagnosisMainAnalysisInput:
    records: list[DiagnosisMainObservedRecord] = []
    failed = False
    citation_required_variants = {"A2", "A3", "CodeGraph", "FULL"}
    for request in census.requests:
        if (
            fail_first_a3_full
            and request.variant == "A3"
            and "-full-" in request.request_id
            and not failed
        ):
            records.append(
                DiagnosisMainObservedRecord(
                    request_id=request.request_id,
                    request_sha256=request.request_sha256,
                    technical_status="parse_failure",
                    output_status=None,
                    claims=(),
                )
            )
            failed = True
            continue
        should_abstain = (
            (request.variant == "A3" and "-missing_key-" in request.request_id)
            or (abstain_a3_full and request.variant == "A3" and "-full-" in request.request_id)
            or (
                abstain_b1_counterevidence
                and request.variant == "B1"
                and "-counterevidence-" in request.request_id
            )
        )
        if should_abstain:
            records.append(
                DiagnosisMainObservedRecord(
                    request_id=request.request_id,
                    request_sha256=request.request_sha256,
                    technical_status="success",
                    output_status="abstained",
                    claims=(),
                )
            )
            continue
        label = "unsupported" if request.variant == "B1" else "fully_supported"
        records.append(
            DiagnosisMainObservedRecord(
                request_id=request.request_id,
                request_sha256=request.request_sha256,
                technical_status="success",
                output_status="completed",
                claims=(
                    _claim(
                        label,
                        required=request.variant in citation_required_variants,
                    ),
                ),
            )
        )
    if omit_last:
        records.pop()
    payload = {
        "schema_version": "diagnosis-main-analysis-input/v2",
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "records": tuple(item.model_dump(mode="json") for item in records),
    }
    return DiagnosisMainAnalysisInput.model_validate(
        {
            **payload,
            "records": tuple(records),
            "input_sha256": canonical_execution_sha256(payload),
        }
    )


def test_versioned_plan_is_hash_locked_outcome_blind_and_noncausal() -> None:
    plan = _plan()

    assert plan.protected_outcomes_opened is False
    assert plan.scientific_scope == "finite_frozen_benchmark_policy_comparison"
    assert plan.causal_effect_claim_permitted is False
    assert plan.superpopulation_generalization_permitted is False
    assert plan.formal_null_hypothesis_test == "none_descriptive_registered_rule"
    assert plan.primary_variants == ("B1", "A3")
    assert plan.complete_case_primary_permitted is False
    assert plan.family_count == 32
    assert plan.context_count == 128
    assert plan.controlled_request_count == 1024
    assert plan.secondary_claim_status == "descriptive_only"
    assert plan.aggregation_order[-1] == "family_to_finite_census_equal_family_weight"


def test_finite_census_primary_keeps_claim_to_family_aggregation() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(plan, census, _input(plan, census))

    assert report.status == "valid_registered_analysis"
    assert report.causal_effect_claimed is False
    assert report.formal_p_value_reported is False
    assert report.primary_effect is not None
    assert report.primary_effect.family_count == 32
    assert report.primary_effect.estimate == 1.0
    assert report.primary_effect.lower == 1.0
    assert report.primary_effect.upper == 1.0
    assert report.primary_disposition == "benchmark_local_support"
    assert report.stability_diagnostics is not None
    assert report.stability_diagnostics.superfamily_count == 6
    assert report.raw_claim_count == 992


def test_terminal_parse_failure_remains_in_denominator_as_worst_case() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, fail_first_a3_full=True),
    )

    assert report.status == "valid_registered_analysis"
    assert report.technical_status_counts["parse_failure"] == 1
    assert report.primary_effect is not None
    assert report.primary_effect.estimate < 1.0
    assert report.primary_effect.estimate > 0.98


def test_condition_aware_abstention_separates_safety_from_claim_harm() -> None:
    plan = _plan()
    census = _census()
    baseline = analyse_diagnosis_main(plan, census, _input(plan, census))
    false_full = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, abstain_a3_full=True),
    )
    safe_counter = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, abstain_b1_counterevidence=True),
    )

    assert baseline.primary_effect is not None
    assert baseline.primary_effect.estimate == 1.0
    assert false_full.primary_effect is not None
    assert false_full.primary_effect.estimate == pytest.approx(2.0 / 3.0)
    assert safe_counter.counterevidence_effect is not None
    assert safe_counter.counterevidence_effect.estimate == 0.0
    assert safe_counter.abstention_rates is not None
    assert safe_counter.abstention_rates["counterevidence.B1"] == 1.0


def test_missing_terminal_invalidates_instead_of_complete_case_rescue() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, omit_last=True),
    )

    assert report.status == "invalid_registered_execution"
    assert report.primary_effect is None
    assert report.primary_disposition == "invalid_result"
    assert len(report.missing_request_ids) == 1


def test_census_rejects_an_incomplete_eight_path_matrix() -> None:
    payload = _census().model_dump(mode="json")
    payload["requests"].pop()
    payload["census_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "census_sha256"}
    )

    with pytest.raises(ValueError, match="request_count does not match"):
        DiagnosisMainAnalysisCensus.model_validate_json(json.dumps(payload))


def test_census_rejects_cross_family_semantic_duplicates() -> None:
    payload = _census().model_dump(mode="json")
    first_cluster = payload["contexts"][0]["semantic_cluster_id"]
    target = next(
        context
        for context in payload["contexts"]
        if context["case_family_id"] != payload["contexts"][0]["case_family_id"]
    )
    target["semantic_cluster_id"] = first_cluster
    target["context_sha256"] = canonical_execution_sha256(
        {key: value for key, value in target.items() if key != "context_sha256"}
    )
    payload["census_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "census_sha256"}
    )

    with pytest.raises(ValueError, match="semantic duplicate clusters"):
        DiagnosisMainAnalysisCensus.model_validate_json(json.dumps(payload))

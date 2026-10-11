"""Synthetic in-memory joins only; no human submissions or frozen outputs."""

from __future__ import annotations

import copy
from types import SimpleNamespace as Namespace

import pytest

from aletheia_lab.evaluation import diagnosis_main_measurement_impact as impact


def synthetic_inputs():
    plan = Namespace(
        plan_sha256="a" * 64,
        partial_support_primary_weight=0.0,
        partial_support_sensitivity_weight=0.5,
    )
    requests, records, contexts = [], [], []
    families = [Namespace(family_id=f"family-{i:02}") for i in range(32)]
    counter = 0
    for family in families:
        for condition in (*impact.CORE_CONDITIONS, "counterevidence"):
            context_id = f"{family.family_id}-{condition}"
            contexts.append(
                Namespace(
                    context_id=context_id,
                    case_family_id=family.family_id,
                    evidence_condition=condition,
                )
            )
            for arm in (
                *impact.ARMS,
                "A1",
                "A2",
                "B0",
                "B2",
                "CodeGraph",
                "FULL",
            ):
                request_id = f"request-{counter:04}"
                claim_count = 4 if counter < 109 else 3
                requests.append(
                    Namespace(
                        request_id=request_id,
                        context_id=context_id,
                        variant=arm,
                        request_sha256="b" * 64,
                    )
                )
                records.append(
                    Namespace(
                        request_id=request_id,
                        request_sha256="b" * 64,
                        technical_status="success",
                        output_status="completed",
                        claims=[
                            Namespace(
                                claim_id=f"{request_id}-claim-{i}", support_label="fully_supported"
                            )
                            for i in range(claim_count)
                        ],
                    )
                )
                counter += 1
    census = Namespace(
        requests=requests, contexts=contexts, families=families, census_sha256="c" * 64
    )
    recovery = Namespace(
        records=records,
        analysis_plan_sha256=plan.plan_sha256,
        census_sha256=census.census_sha256,
        input_sha256="d" * 64,
    )
    interval = Namespace(estimate=0.0)
    report = Namespace(
        status="valid_registered_analysis",
        analysis_plan_sha256=plan.plan_sha256,
        census_sha256=census.census_sha256,
        input_sha256=recovery.input_sha256,
        primary_effect=interval,
        partial_support_sensitivity_effect=interval,
        condition_primary_effects={condition: interval for condition in impact.CORE_CONDITIONS},
        raw_claim_count=3181,
        report_sha256="e" * 64,
    )
    frame = impact._build_frame(plan, census, recovery, report)
    sampled = set()
    for family in families:
        own = [unit.claim_id for unit in frame.units.values() if unit.family_id == family.family_id]
        sampled.update(own[:5])
    enriched = set(key for key in frame.units if key not in sampled)
    enriched = set(sorted(enriched)[:40])
    rows, mappings = [], []
    for claim_id in sorted(sampled | enriched):
        unit = frame.units[claim_id]
        component = "probability" if claim_id in sampled else "enriched"
        row = {
            "source_claim_id": claim_id,
            "human_final_label": "fully_supported",
            "request_id": unit.request_id,
            "family_id": unit.family_id,
            "arm": unit.arm,
            "condition": unit.condition,
            "component": component,
            "automatic_label": "fully_supported",
            "claim_type": "synthetic-observation",
        }
        rows.append(row)
        for reviewer in ("synthetic-a", "synthetic-b"):
            mappings.append(
                {
                    **row,
                    "reviewer": reviewer,
                    "phase": "main",
                    "inclusion_probability": 5 / frame.family_sizes[unit.family_id],
                }
            )
    audit_report = {"primary_design_weighted_automatic_human_agreement": {"estimate": 1.0}}
    return dict(
        plan=plan,
        census=census,
        recovery_input=recovery,
        recovery_report=report,
        audit_rows=rows,
        sample_map={"mapping": mappings},
        audit_report=audit_report,
    )


def test_complete_synthetic_mapping_preserves_probability_and_enrichment_denominators():
    inputs = synthetic_inputs()
    result = impact.analyze_measurement_impact(**inputs)
    assert result["sample"]["emitted_claim_population"] == 3181
    assert result["sample"]["probability_claim_count"] == 160
    assert result["sample"]["enriched_diagnostic_claim_count_excluded_from_estimators"] == 40
    assert result["sample"]["human_known_scored_core_claim_count"] == 171
    assert result["sample"]["probability_core_B1_A3_claim_count"] == 160
    assert result["human_audit_design_agreement_reproduced"] == 1.0
    assert result["sample"]["paired_family_condition_count"] == 96
    assert result["sample"]["paired_family_condition_with_both_arms_sampled"] == 32
    assert result["sample"]["fully_human_labeled_paired_family_condition_count"] == 0
    primary = result["primary_B1_minus_A3"]
    assert primary["automatic_loss"] == primary["design_weighted_human_relabel_estimate"] == 0.0
    assert primary["design_standard_error"] == 0.0
    assert primary["approx_95_design_interval_not_external_generalization"] is None
    assert (
        primary["assumption_free_label_bounds"][0] < 0 < primary["assumption_free_label_bounds"][1]
    )
    assert result["frozen_outcomes_mutated"] is False and result["provider_calls"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "request",
        "claim_duplicate",
        "records_missing",
        "frame_count",
        "human_duplicate",
        "human_missing",
        "human_label",
        "mapping",
        "weight",
        "agreement",
        "effect",
        "binding",
    ],
)
def test_synthetic_mapping_rejects_changed_identity_weights_and_recovery(mutation):
    inputs = synthetic_inputs()
    if mutation == "request":
        inputs["recovery_input"].records[0].request_sha256 = "f" * 64
    elif mutation == "claim_duplicate":
        inputs["recovery_input"].records[0].claims.append(
            copy.copy(inputs["recovery_input"].records[0].claims[0])
        )
    elif mutation == "records_missing":
        inputs["recovery_input"].records.pop()
    elif mutation == "frame_count":
        inputs["recovery_report"].raw_claim_count -= 1
    elif mutation == "human_duplicate":
        inputs["audit_rows"].append(copy.deepcopy(inputs["audit_rows"][0]))
    elif mutation == "human_missing":
        inputs["audit_rows"].pop()
    elif mutation == "human_label":
        inputs["audit_rows"][0]["human_final_label"] = "invented"
    elif mutation == "mapping":
        inputs["sample_map"]["mapping"][0]["automatic_label"] = "unsupported"
    elif mutation == "weight":
        row = next(
            value
            for value in inputs["sample_map"]["mapping"]
            if value["component"] == "probability"
        )
        row["inclusion_probability"] = 1.0
    elif mutation == "agreement":
        inputs["audit_report"]["primary_design_weighted_automatic_human_agreement"]["estimate"] = (
            0.5
        )
    elif mutation == "effect":
        inputs["recovery_report"].primary_effect = Namespace(estimate=1.0)
    else:
        inputs["recovery_input"].analysis_plan_sha256 = "f" * 64
    with pytest.raises(impact.MeasurementImpactError):
        impact.analyze_measurement_impact(**inputs)

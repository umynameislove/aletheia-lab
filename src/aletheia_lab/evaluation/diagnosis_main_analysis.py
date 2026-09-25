"""Outcome-blind contracts and deterministic analysis for the diagnosis study.

The registered target is a finite, frozen benchmark-policy contrast. It is not
a randomized treatment effect and this module therefore does not emit a
p-value. The BCa interval is a family-resampling *stability* interval,
supplemented by leave-one-family and leave-one-superfamily checks.

The module also keeps three operational states separate:

* an expected request without a terminal record invalidates the execution;
* a terminal provider/parse/unresolved result remains in the denominator with
  worst-case loss; and
* a parsed response is scored from its atomic support labels plus the frozen
  condition-specific abstention policy.

No provider call, protected-outcome read, or artifact publication occurs here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

import numpy as np
from scipy.stats import bootstrap  # type: ignore[import-untyped]

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    ALL_CONDITIONS,
    ANALYSIS_CENSUS_SCHEMA_VERSION,
    ANALYSIS_INPUT_SCHEMA_VERSION,
    ANALYSIS_PLAN_SCHEMA_VERSION,
    ANALYSIS_REPORT_SCHEMA_VERSION,
    CONTROLLED_VARIANTS,
    CORE_CONDITIONS,
    EXPECTED_MECHANISM_COUNTS,
    EXPECTED_RESPONSE_MODE,
    HARM_LABELS,
    PRIMARY_VARIANTS,
    ClaimType,
    ControlledVariant,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisError,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
    DiagnosisMainClaim,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
    DiagnosisMainObservedRecord,
    EmittedClaimSummary,
    EvidenceCondition,
    ExpectedResponseMode,
    Mechanism,
    MetricInterval,
    OutputStatus,
    StabilityDiagnostics,
    SupportLabel,
    TechnicalStatus,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


def _bca_stability_interval(
    family_values: Mapping[str, float], *, seed: int, replicates: int
) -> MetricInterval:
    if not family_values:
        raise DiagnosisMainAnalysisError("a family-resampling interval requires families")
    ordered = np.asarray([family_values[key] for key in sorted(family_values)], dtype=float)
    estimate = float(ordered.mean())
    if np.all(ordered == ordered[0]):
        lower = upper = estimate
    else:
        result = bootstrap(
            (ordered,),
            np.mean,
            confidence_level=0.95,
            n_resamples=replicates,
            method="BCa",
            vectorized=False,
            rng=np.random.default_rng(seed),
        )
        lower = float(result.confidence_interval.low)
        upper = float(result.confidence_interval.high)
        if not np.isfinite(lower) or not np.isfinite(upper):
            raise DiagnosisMainAnalysisError("BCa stability interval is undefined")
    return MetricInterval(
        estimate=estimate,
        lower=lower,
        upper=upper,
        family_count=len(ordered),
    )


def _claim_harm_score(record: DiagnosisMainObservedRecord, partial_weight: float) -> float:
    weights = {
        "contradicted": 1.0,
        "unsupported": 1.0,
        "partially_supported": partial_weight,
        "fully_supported": 0.0,
    }
    return float(np.mean([weights[item.support_label] for item in record.claims]))


def _accountability_loss(
    record: DiagnosisMainObservedRecord,
    *,
    condition: str,
    partial_weight: float,
) -> float:
    if record.technical_status != "success":
        return 1.0
    if record.output_status == "abstained":
        return 0.0 if condition in {"missing_key", "counterevidence"} else 1.0
    if not record.claims:
        return 1.0
    return _claim_harm_score(record, partial_weight)


def _cause_overclaim_score(record: DiagnosisMainObservedRecord) -> float:
    if record.technical_status != "success":
        return 1.0
    if record.output_status == "abstained":
        return 0.0
    cause_claims = [item for item in record.claims if item.claim_type == "cause_assertion"]
    if not cause_claims:
        return 0.0
    return sum(item.support_label in HARM_LABELS for item in cause_claims) / len(cause_claims)


def _false_abstention_score(record: DiagnosisMainObservedRecord) -> float:
    if record.technical_status != "success":
        return 1.0
    return float(record.output_status == "abstained")


def _family_condition_means(
    census: DiagnosisMainAnalysisCensus,
    observed: Mapping[str, DiagnosisMainObservedRecord],
    scorer: Callable[[DiagnosisMainObservedRecord, str], float],
) -> dict[tuple[str, str, str], float]:
    contexts = {item.context_id: item for item in census.contexts}
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for expected in census.requests:
        record = observed.get(expected.request_id)
        if record is None:
            continue
        context = contexts[expected.context_id]
        grouped[(context.case_family_id, context.evidence_condition, expected.variant)].append(
            float(scorer(record, context.evidence_condition))
        )
    return {key: float(np.mean(values)) for key, values in grouped.items()}


def _paired_family_effects(
    cells: Mapping[tuple[str, str, str], float],
    *,
    conditions: Sequence[str],
    left: str,
    right: str,
) -> dict[str, float]:
    families = sorted({key[0] for key in cells})
    effects: dict[str, float] = {}
    for family in families:
        try:
            left_values = [cells[(family, condition, left)] for condition in conditions]
            right_values = [cells[(family, condition, right)] for condition in conditions]
        except KeyError as exc:
            raise DiagnosisMainAnalysisError(
                "paired analysis lost a frozen family-condition-variant cell"
            ) from exc
        effects[family] = float(np.mean(left_values) - np.mean(right_values))
    return effects


def _variant_family_values(
    cells: Mapping[tuple[str, str, str], float],
    *,
    condition: str,
    variant: str,
) -> dict[str, float]:
    return {
        family: value
        for (family, cell_condition, cell_variant), value in cells.items()
        if cell_condition == condition and cell_variant == variant
    }


def _subset_values(values: Mapping[str, float], family_ids: set[str]) -> dict[str, float]:
    return {family_id: value for family_id, value in values.items() if family_id in family_ids}


def _leave_group_out_range(
    values: Mapping[str, float], groups: Mapping[str, str]
) -> tuple[float, float]:
    group_ids = sorted(set(groups.values()))
    means = []
    for group_id in group_ids:
        retained = [value for family, value in values.items() if groups[family] != group_id]
        if not retained:
            raise DiagnosisMainAnalysisError("leave-group-out removed every family")
        means.append(float(np.mean(retained)))
    return min(means), max(means)


def _stability_diagnostics(
    effects: Mapping[str, float], superfamilies: Mapping[str, str]
) -> StabilityDiagnostics:
    ordered = np.asarray([effects[key] for key in sorted(effects)], dtype=float)
    leave_family_min, leave_family_max = _leave_group_out_range(
        effects, {family: family for family in effects}
    )
    leave_super_min, leave_super_max = _leave_group_out_range(effects, superfamilies)
    return StabilityDiagnostics(
        positive_family_fraction=float(np.mean(ordered > 0.0)),
        zero_family_fraction=float(np.mean(ordered == 0.0)),
        family_effect_minimum=float(ordered.min()),
        family_effect_median=float(np.median(ordered)),
        family_effect_maximum=float(ordered.max()),
        leave_one_family_out_minimum=leave_family_min,
        leave_one_family_out_maximum=leave_family_max,
        leave_one_superfamily_out_minimum=leave_super_min,
        leave_one_superfamily_out_maximum=leave_super_max,
        superfamily_count=len(set(superfamilies.values())),
    )


def _emitted_claim_summary(
    records: Sequence[DiagnosisMainObservedRecord],
) -> EmittedClaimSummary:
    emitted = [claim for record in records for claim in record.claims]
    harmful = sum(claim.support_label in HARM_LABELS for claim in emitted)
    partial = sum(claim.support_label == "partially_supported" for claim in emitted)
    covered = sum(bool(record.claims) for record in records)
    return EmittedClaimSummary(
        total_output_count=len(records),
        output_with_claims_count=covered,
        emitted_claim_count=len(emitted),
        harmful_claim_count=harmful,
        partially_supported_claim_count=partial,
        output_coverage=covered / len(records),
        harmful_claim_rate=(harmful / len(emitted) if emitted else None),
    )


def _invalid_report_payload(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
    observed: Mapping[str, DiagnosisMainObservedRecord],
    missing: tuple[str, ...],
    unexpected: tuple[str, ...],
    technical_counts: dict[str, int],
    raw_claim_count: int,
) -> dict[str, object]:
    return {
        "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
        "status": "invalid_registered_execution",
        "scientific_scope": plan.scientific_scope,
        "causal_effect_claimed": False,
        "formal_p_value_reported": False,
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "input_sha256": analysis_input.input_sha256,
        "expected_request_count": len(census.requests),
        "terminal_request_count": len(observed),
        "missing_request_ids": missing,
        "unexpected_request_ids": unexpected,
        "technical_status_counts": technical_counts,
        "raw_claim_count": raw_claim_count,
        "primary_effect": None,
        "partial_support_sensitivity_effect": None,
        "primary_disposition": "invalid_result",
        "stability_diagnostics": None,
        "mechanism_primary_effects": None,
        "condition_primary_effects": None,
        "counterevidence_effect": None,
        "emitted_claim_summaries": None,
        "abstention_rates": None,
        "missing_key_causal_overclaim": None,
        "full_evidence_false_abstention_a3": None,
        "citation_validity": {},
    }


def _reconcile_analysis_input(
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
) -> tuple[
    dict[str, DiagnosisMainExpectedRequest],
    dict[str, DiagnosisMainObservedRecord],
    tuple[str, ...],
    tuple[str, ...],
    dict[str, int],
    int,
]:
    expected = {item.request_id: item for item in census.requests}
    observed = {item.request_id: item for item in analysis_input.records}
    missing = tuple(sorted(set(expected) - set(observed)))
    unexpected = tuple(sorted(set(observed) - set(expected)))
    technical_counts = {status: 0 for status in TechnicalStatus.__args__}  # type: ignore[attr-defined]
    for record in analysis_input.records:
        technical_counts[record.technical_status] += 1
    return (
        expected,
        observed,
        missing,
        unexpected,
        technical_counts,
        sum(len(item.claims) for item in analysis_input.records),
    )


def _validate_observed_contracts(
    expected: Mapping[str, DiagnosisMainExpectedRequest],
    observed: Mapping[str, DiagnosisMainObservedRecord],
) -> None:
    mismatched_hashes = tuple(
        request_id
        for request_id, record in observed.items()
        if record.request_sha256 != expected[request_id].request_sha256
    )
    if mismatched_hashes:
        raise DiagnosisMainAnalysisError(
            "observed records changed frozen request identity: " + ",".join(mismatched_hashes)
        )
    citation_required_variants = {"A2", "A3", "CodeGraph", "FULL"}
    citation_required_claim_types = {"cause_assertion", "evidence_statement"}
    citation_contract_mismatches = tuple(
        sorted(
            request_id
            for request_id, record in observed.items()
            for claim in record.claims
            if claim.citation_required
            != (
                expected[request_id].variant in citation_required_variants
                and claim.claim_type in citation_required_claim_types
            )
        )
    )
    if citation_contract_mismatches:
        raise DiagnosisMainAnalysisError(
            "observed records changed frozen citation policy: "
            + ",".join(citation_contract_mismatches)
        )


def _primary_disposition(
    plan: DiagnosisMainAnalysisPlan,
    primary: MetricInterval,
    diagnostics: StabilityDiagnostics,
) -> Literal[
    "benchmark_local_support",
    "benchmark_local_support_not_established",
    "unstable_or_precision_limited",
    "invalid_result",
]:
    criteria = (
        primary.estimate >= plan.minimum_observed_effect_for_support,
        primary.lower > 0.0,
        diagnostics.positive_family_fraction >= plan.positive_family_fraction_floor,
        diagnostics.leave_one_superfamily_out_minimum > 0.0,
    )
    if all(criteria):
        return "benchmark_local_support"
    if primary.estimate > 0.0 and any(criteria):
        return "unstable_or_precision_limited"
    return "benchmark_local_support_not_established"


def _primary_output_summaries(
    census: DiagnosisMainAnalysisCensus,
    expected: Mapping[str, DiagnosisMainExpectedRequest],
    observed: Mapping[str, DiagnosisMainObservedRecord],
) -> tuple[dict[str, EmittedClaimSummary], dict[str, float]]:
    contexts = {item.context_id: item for item in census.contexts}
    primary_records: dict[str, list[DiagnosisMainObservedRecord]] = defaultdict(list)
    abstentions: dict[tuple[str, str], list[float]] = defaultdict(list)
    for request_id, record in observed.items():
        expected_request = expected[request_id]
        if expected_request.variant not in PRIMARY_VARIANTS:
            continue
        context = contexts[expected_request.context_id]
        if context.evidence_condition in CORE_CONDITIONS:
            primary_records[expected_request.variant].append(record)
        abstentions[(context.evidence_condition, expected_request.variant)].append(
            float(record.technical_status == "success" and record.output_status == "abstained")
        )
    emitted = {
        variant: _emitted_claim_summary(tuple(primary_records[variant]))
        for variant in PRIMARY_VARIANTS
    }
    rates = {
        f"{condition}.{variant}": float(np.mean(values))
        for (condition, variant), values in sorted(abstentions.items())
    }
    return emitted, rates


def _citation_validity(
    expected: Mapping[str, DiagnosisMainExpectedRequest],
    observed: Mapping[str, DiagnosisMainObservedRecord],
) -> dict[str, float]:
    totals: dict[str, list[bool]] = defaultdict(list)
    for request_id, record in observed.items():
        variant = expected[request_id].variant
        for claim in record.claims:
            if claim.citation_required:
                totals[variant].append(claim.citation_present and claim.citation_ids_valid)
    return {variant: sum(values) / len(values) for variant, values in sorted(totals.items())}


def analyse_diagnosis_main(
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
) -> DiagnosisMainAnalysisReport:
    """Run the prespecified finite-census analysis once over sealed inputs."""

    if analysis_input.analysis_plan_sha256 != plan.plan_sha256:
        raise DiagnosisMainAnalysisError("analysis input is bound to a different plan")
    if analysis_input.census_sha256 != census.census_sha256:
        raise DiagnosisMainAnalysisError("analysis input is bound to a different census")

    expected, observed, missing, unexpected, technical_counts, raw_claim_count = (
        _reconcile_analysis_input(census, analysis_input)
    )

    if missing or unexpected:
        payload = _invalid_report_payload(
            plan=plan,
            census=census,
            analysis_input=analysis_input,
            observed=observed,
            missing=missing,
            unexpected=unexpected,
            technical_counts=technical_counts,
            raw_claim_count=raw_claim_count,
        )
        return DiagnosisMainAnalysisReport.model_validate(
            {**payload, "report_sha256": canonical_execution_sha256(payload)}
        )

    _validate_observed_contracts(expected, observed)

    primary_cells = _family_condition_means(
        census,
        observed,
        lambda record, condition: _accountability_loss(
            record,
            condition=condition,
            partial_weight=plan.partial_support_primary_weight,
        ),
    )
    sensitivity_cells = _family_condition_means(
        census,
        observed,
        lambda record, condition: _accountability_loss(
            record,
            condition=condition,
            partial_weight=plan.partial_support_sensitivity_weight,
        ),
    )
    primary_effects = _paired_family_effects(
        primary_cells,
        conditions=plan.primary_conditions,
        left="B1",
        right="A3",
    )
    sensitivity_effects = _paired_family_effects(
        sensitivity_cells,
        conditions=plan.primary_conditions,
        left="B1",
        right="A3",
    )
    primary = _bca_stability_interval(
        primary_effects,
        seed=plan.bootstrap_seed,
        replicates=plan.bootstrap_replicates,
    )
    sensitivity = _bca_stability_interval(
        sensitivity_effects,
        seed=plan.bootstrap_seed + 1,
        replicates=plan.bootstrap_replicates,
    )

    families = {item.family_id: item for item in census.families}
    superfamilies = {family_id: families[family_id].superfamily_id for family_id in primary_effects}
    diagnostics = _stability_diagnostics(primary_effects, superfamilies)
    disposition = _primary_disposition(plan, primary, diagnostics)

    family_ids_by_mechanism = {
        mechanism: {
            family_id for family_id, family in families.items() if family.mechanism == mechanism
        }
        for mechanism in EXPECTED_MECHANISM_COUNTS
    }
    mechanism_effects = {
        mechanism: _bca_stability_interval(
            _subset_values(primary_effects, family_ids),
            seed=plan.bootstrap_seed + 10 + offset,
            replicates=plan.bootstrap_replicates,
        )
        for offset, (mechanism, family_ids) in enumerate(sorted(family_ids_by_mechanism.items()))
    }
    condition_effects = {
        condition: _bca_stability_interval(
            _paired_family_effects(
                primary_cells,
                conditions=(condition,),
                left="B1",
                right="A3",
            ),
            seed=plan.bootstrap_seed + 20 + offset,
            replicates=plan.bootstrap_replicates,
        )
        for offset, condition in enumerate(CORE_CONDITIONS)
    }
    counterevidence = _bca_stability_interval(
        _paired_family_effects(
            primary_cells,
            conditions=("counterevidence",),
            left="B1",
            right="A3",
        ),
        seed=plan.bootstrap_seed + 30,
        replicates=plan.bootstrap_replicates,
    )

    emitted_summaries, abstention_rates = _primary_output_summaries(census, expected, observed)

    causal_cells = _family_condition_means(
        census, observed, lambda record, _condition: _cause_overclaim_score(record)
    )
    causal_intervals = {
        variant: _bca_stability_interval(
            _variant_family_values(causal_cells, condition="missing_key", variant=variant),
            seed=plan.bootstrap_seed + 40 + offset,
            replicates=plan.bootstrap_replicates,
        )
        for offset, variant in enumerate(PRIMARY_VARIANTS)
    }
    abstention_cells = _family_condition_means(
        census, observed, lambda record, _condition: _false_abstention_score(record)
    )
    false_abstention = _bca_stability_interval(
        _variant_family_values(abstention_cells, condition="full", variant="A3"),
        seed=plan.bootstrap_seed + 50,
        replicates=plan.bootstrap_replicates,
    )

    citation_validity = _citation_validity(expected, observed)

    payload = {
        "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
        "status": "valid_registered_analysis",
        "scientific_scope": plan.scientific_scope,
        "causal_effect_claimed": False,
        "formal_p_value_reported": False,
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "input_sha256": analysis_input.input_sha256,
        "expected_request_count": len(expected),
        "terminal_request_count": len(observed),
        "missing_request_ids": (),
        "unexpected_request_ids": (),
        "technical_status_counts": technical_counts,
        "raw_claim_count": raw_claim_count,
        "primary_effect": primary.model_dump(mode="json"),
        "partial_support_sensitivity_effect": sensitivity.model_dump(mode="json"),
        "primary_disposition": disposition,
        "stability_diagnostics": diagnostics.model_dump(mode="json"),
        "mechanism_primary_effects": {
            key: value.model_dump(mode="json") for key, value in mechanism_effects.items()
        },
        "condition_primary_effects": {
            key: value.model_dump(mode="json") for key, value in condition_effects.items()
        },
        "counterevidence_effect": counterevidence.model_dump(mode="json"),
        "emitted_claim_summaries": {
            key: value.model_dump(mode="json") for key, value in emitted_summaries.items()
        },
        "abstention_rates": abstention_rates,
        "missing_key_causal_overclaim": {
            key: value.model_dump(mode="json") for key, value in causal_intervals.items()
        },
        "full_evidence_false_abstention_a3": false_abstention.model_dump(mode="json"),
        "citation_validity": citation_validity,
    }
    return DiagnosisMainAnalysisReport.model_validate(
        {**payload, "report_sha256": canonical_execution_sha256(payload)}
    )


__all__ = [
    "ALL_CONDITIONS",
    "ANALYSIS_CENSUS_SCHEMA_VERSION",
    "ANALYSIS_INPUT_SCHEMA_VERSION",
    "ANALYSIS_PLAN_SCHEMA_VERSION",
    "ANALYSIS_REPORT_SCHEMA_VERSION",
    "CONTROLLED_VARIANTS",
    "CORE_CONDITIONS",
    "EXPECTED_MECHANISM_COUNTS",
    "EXPECTED_RESPONSE_MODE",
    "HARM_LABELS",
    "PRIMARY_VARIANTS",
    "ClaimType",
    "ControlledVariant",
    "DiagnosisMainAnalysisCensus",
    "DiagnosisMainAnalysisError",
    "DiagnosisMainAnalysisInput",
    "DiagnosisMainAnalysisPlan",
    "DiagnosisMainAnalysisReport",
    "DiagnosisMainClaim",
    "DiagnosisMainContext",
    "DiagnosisMainExpectedRequest",
    "DiagnosisMainFamily",
    "DiagnosisMainObservedRecord",
    "EmittedClaimSummary",
    "EvidenceCondition",
    "ExpectedResponseMode",
    "Mechanism",
    "MetricInterval",
    "OutputStatus",
    "StabilityDiagnostics",
    "SupportLabel",
    "TechnicalStatus",
    "analyse_diagnosis_main",
]

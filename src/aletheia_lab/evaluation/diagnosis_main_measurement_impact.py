"""Offline sensitivity of the frozen P5 loss to audited claim labels.

The audit sampled claims, whereas P5 aggregates claim loss within output,
condition, and family. This module keeps those denominators intact and never
replaces the registered analysis or imputes claims for failed requests.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    CORE_CONDITIONS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainObservedRecord,
    MetricInterval,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import classify_output

ARMS = ("B1", "A3")
LABELS = frozenset(("fully_supported", "partially_supported", "unsupported", "contradicted"))


class MeasurementImpactError(ValueError):
    """The audit or recovery source cannot support a bound analysis."""


@dataclass(frozen=True)
class ClaimUnit:
    claim_id: str
    request_id: str
    family_id: str
    arm: str
    condition: str
    auto_label: str
    output_claim_count: int
    scored_output: bool


@dataclass
class ImpactFrame:
    family_ids: tuple[str, ...] = ()
    units: dict[str, ClaimUnit] = field(default_factory=dict)
    family_sizes: Counter[str] = field(default_factory=Counter)
    output_claims: dict[str, set[str]] = field(default_factory=dict)
    output_categories: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    output_losses: dict[tuple[str, str, str], dict[float, float]] = field(default_factory=dict)
    requests_by_cell: dict[tuple[str, str, str], str] = field(default_factory=dict)


@dataclass(frozen=True)
class HumanAudit:
    sampled: dict[str, dict[str, Any]]
    enriched: dict[str, dict[str, Any]]
    agreement: float

    @property
    def known(self) -> dict[str, dict[str, Any]]:
        return self.sampled | self.enriched


def _harm(label: str, partial_weight: float) -> float:
    if label == "partially_supported":
        return partial_weight
    return 0.0 if label == "fully_supported" else 1.0


def _effect_sensitivity(
    *,
    units: dict[str, ClaimUnit],
    family_sizes: Counter[str],
    sampled: dict[str, dict[str, Any]],
    known: dict[str, dict[str, Any]],
    baseline: float,
    arms: tuple[str, ...],
    conditions: tuple[str, ...],
    partial_weight: float,
    family_count: int,
) -> dict[str, Any]:
    """HT residual correction and assumption-free bounds for one fixed loss mean."""

    def coefficient(unit: ClaimUnit) -> float:
        if not unit.scored_output or unit.arm not in arms or unit.condition not in conditions:
            return 0.0
        sign = -1.0 if unit.arm == "A3" and len(arms) == 2 else 1.0
        return sign / (family_count * len(conditions) * unit.output_claim_count)

    sampled_by_family: dict[str, list[float]] = defaultdict(list)
    exact_deltas: list[float] = []
    for claim_id, row in sampled.items():
        unit = units[claim_id]
        delta = coefficient(unit) * (
            _harm(row["human_final_label"], partial_weight) - _harm(unit.auto_label, partial_weight)
        )
        sampled_by_family[unit.family_id].append(delta)
    for claim_id, row in known.items():
        unit = units[claim_id]
        exact_deltas.append(
            coefficient(unit)
            * (
                _harm(row["human_final_label"], partial_weight)
                - _harm(unit.auto_label, partial_weight)
            )
        )

    estimated_corrections: list[float] = []
    variance_parts: list[float] = []
    for family_id, population_size in sorted(family_sizes.items()):
        values = sampled_by_family[family_id]
        n = len(values)
        if n != 5 or population_size < n:
            raise MeasurementImpactError("five sampled claims per eligible family are required")
        mean = math.fsum(values) / n
        sample_variance = math.fsum((value - mean) ** 2 for value in values) / (n - 1)
        estimated_corrections.append(population_size * mean)
        variance_parts.append(
            population_size**2 * (1.0 - n / population_size) * sample_variance / n
        )
    correction = math.fsum(estimated_corrections)
    variance = math.fsum(variance_parts)
    standard_error = math.sqrt(variance)
    sampled_delta = math.fsum(exact_deltas)

    unknown_lower: list[float] = []
    unknown_upper: list[float] = []
    for claim_id, unit in units.items():
        if claim_id in known:
            continue
        coeff = coefficient(unit)
        if coeff == 0.0:
            continue
        automatic_harm = _harm(unit.auto_label, partial_weight)
        options = (coeff * (0.0 - automatic_harm), coeff * (1.0 - automatic_harm))
        unknown_lower.append(min(options))
        unknown_upper.append(max(options))

    estimate = baseline + correction
    degenerate = standard_error == 0.0 and any(
        coefficient(unit) != 0 for claim_id, unit in units.items() if claim_id not in known
    )
    return {
        "automatic_loss": baseline,
        "known_claim_substitution_only_not_population_estimate": baseline + sampled_delta,
        "design_weighted_human_relabel_estimate": estimate,
        "design_standard_error": standard_error,
        "design_interval_suppressed_sparse_zero_variance": degenerate,
        "approx_95_design_interval_not_external_generalization": None
        if degenerate
        else [estimate - 1.96 * standard_error, estimate + 1.96 * standard_error],
        "assumption_free_label_bounds": [
            baseline + sampled_delta + math.fsum(unknown_lower),
            baseline + sampled_delta + math.fsum(unknown_upper),
        ],
    }


def _validate_recovery(
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
) -> tuple[MetricInterval, MetricInterval, dict[str, MetricInterval]]:
    primary = recovery_report.primary_effect
    sensitivity = recovery_report.partial_support_sensitivity_effect
    conditions = recovery_report.condition_primary_effects
    if (
        recovery_report.status != "valid_registered_analysis"
        or recovery_report.analysis_plan_sha256 != plan.plan_sha256
        or recovery_input.analysis_plan_sha256 != plan.plan_sha256
        or recovery_report.census_sha256 != census.census_sha256
        or recovery_input.census_sha256 != census.census_sha256
        or recovery_report.input_sha256 != recovery_input.input_sha256
        or primary is None
        or sensitivity is None
        or conditions is None
    ):
        raise MeasurementImpactError("recovery is not bound to the frozen analysis")
    return primary, sensitivity, conditions


def _add_request(
    frame: ImpactFrame,
    request: DiagnosisMainExpectedRequest,
    record: DiagnosisMainObservedRecord,
    context: DiagnosisMainContext,
    plan: DiagnosisMainAnalysisPlan,
) -> None:
    if record.request_sha256 != request.request_sha256:
        raise MeasurementImpactError("request identity changed")
    request_id = request.request_id
    family_id = context.case_family_id
    arm = request.variant
    condition = context.evidence_condition
    claim_ids = {claim.claim_id for claim in record.claims}
    if len(claim_ids) != len(record.claims) or claim_ids & frame.units.keys():
        raise MeasurementImpactError("claim identity is duplicated")
    frame.output_claims[request_id] = claim_ids
    category, primary = classify_output(record, condition, plan.partial_support_primary_weight)
    for claim in record.claims:
        frame.units[claim.claim_id] = ClaimUnit(
            claim_id=claim.claim_id,
            request_id=request_id,
            family_id=family_id,
            arm=arm,
            condition=condition,
            auto_label=claim.support_label,
            output_claim_count=len(record.claims),
            scored_output=category == "claim_scored",
        )
        frame.family_sizes[family_id] += 1
    if arm not in ARMS or condition not in CORE_CONDITIONS:
        return
    key = (family_id, condition, arm)
    if key in frame.output_losses:
        raise MeasurementImpactError("family, condition and arm are not unique")
    frame.requests_by_cell[key] = request_id
    _, sensitivity = classify_output(record, condition, plan.partial_support_sensitivity_weight)
    frame.output_categories[(condition, arm, category)] += 1
    frame.output_losses[key] = {
        plan.partial_support_primary_weight: primary,
        plan.partial_support_sensitivity_weight: sensitivity,
    }


def _build_frame(
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
) -> ImpactFrame:
    requests = {request.request_id: request for request in census.requests}
    records = {record.request_id: record for record in recovery_input.records}
    contexts = {context.context_id: context for context in census.contexts}
    if len(requests) != 1024 or len(records) != 1024 or set(requests) != set(records):
        raise MeasurementImpactError("recovery request census is incomplete")
    frame = ImpactFrame(family_ids=tuple(sorted(family.family_id for family in census.families)))
    for request_id, request in requests.items():
        _add_request(frame, request, records[request_id], contexts[request.context_id], plan)
    if (
        len(frame.units) != recovery_report.raw_claim_count
        or len(frame.units) != 3181
        or set(frame.family_sizes) != set(frame.family_ids)
        or len(frame.family_ids) != 32
        or len(frame.output_losses) != 32 * len(CORE_CONDITIONS) * len(ARMS)
    ):
        raise MeasurementImpactError("claim or paired family census differs from recovery")
    return frame


def _validate_human_row(
    row: dict[str, Any],
    mappings: list[dict[str, Any]],
    frame: ImpactFrame,
) -> None:
    claim_id = row["source_claim_id"]
    unit = frame.units[claim_id]
    if row["human_final_label"] not in LABELS:
        raise MeasurementImpactError("invalid human support label")
    fields = (
        "request_id",
        "family_id",
        "arm",
        "condition",
        "component",
        "automatic_label",
        "claim_type",
    )
    if any(mapped[key] != row[key] for mapped in mappings for key in fields):
        raise MeasurementImpactError("human mapping does not match frozen sample")
    source_fields = {
        "request_id": unit.request_id,
        "family_id": unit.family_id,
        "arm": unit.arm,
        "condition": unit.condition,
        "automatic_label": unit.auto_label,
    }
    if source_fields != {key: row[key] for key in source_fields}:
        raise MeasurementImpactError("human mapping does not match frozen claim source")
    if row["component"] == "probability" and any(
        mapped["inclusion_probability"] != 5 / frame.family_sizes[unit.family_id]
        for mapped in mappings
    ):
        raise MeasurementImpactError("probability inclusion weight differs")


def _weighted_agreement(
    frame: ImpactFrame,
    sampled: dict[str, dict[str, Any]],
    audit_report: dict[str, Any],
) -> float:
    family_counts = Counter(frame.units[claim_id].family_id for claim_id in sampled)
    if any(family_counts[family_id] != 5 for family_id in frame.family_ids):
        raise MeasurementImpactError("probability draw is not five claims per family")
    agreement = math.fsum(
        frame.family_sizes[family_id]
        / len(frame.units)
        * sum(
            sampled[claim_id]["human_final_label"] == frame.units[claim_id].auto_label
            for claim_id in sampled
            if frame.units[claim_id].family_id == family_id
        )
        / 5
        for family_id in frame.family_ids
    )
    locked = audit_report["primary_design_weighted_automatic_human_agreement"]["estimate"]
    if not math.isclose(agreement, locked, rel_tol=0.0, abs_tol=1e-12):
        raise MeasurementImpactError("weighted agreement does not reproduce locked audit")
    return agreement


def _read_human_audit(
    frame: ImpactFrame,
    audit_rows: list[dict[str, Any]],
    sample_map: dict[str, Any],
    audit_report: dict[str, Any],
) -> HumanAudit:
    main_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sample_map["mapping"]:
        if row["phase"] == "main":
            main_map[row["source_claim_id"]].append(row)
    if len(main_map) != 200 or any(len(rows) != 2 for rows in main_map.values()):
        raise MeasurementImpactError("main human mapping is incomplete")
    sampled: dict[str, dict[str, Any]] = {}
    enriched: dict[str, dict[str, Any]] = {}
    for row in audit_rows:
        claim_id = row["source_claim_id"]
        if claim_id not in frame.units or claim_id not in main_map:
            raise MeasurementImpactError("human claim not in frozen recovery and map")
        if claim_id in sampled or claim_id in enriched:
            raise MeasurementImpactError("human source claim repeated")
        _validate_human_row(row, main_map[claim_id], frame)
        component = row["component"]
        if component == "probability":
            sampled[claim_id] = row
        elif component == "enriched":
            enriched[claim_id] = row
        else:
            raise MeasurementImpactError("pilot or unknown component entered main analysis")
    if set(main_map) != set(sampled) | set(enriched) or len(sampled) != 160 or len(enriched) != 40:
        raise MeasurementImpactError("160/40 human sample is incomplete")
    agreement = _weighted_agreement(frame, sampled, audit_report)
    return HumanAudit(sampled=sampled, enriched=enriched, agreement=agreement)


def _paired_coverage(frame: ImpactFrame, sampled_core: set[str]) -> tuple[int, int]:
    paired_any = 0
    paired_complete = 0
    for family_id in frame.family_ids:
        for condition in CORE_CONDITIONS:
            pair = {arm: frame.requests_by_cell[(family_id, condition, arm)] for arm in ARMS}
            if all(frame.output_claims[pair[arm]] & sampled_core for arm in ARMS):
                paired_any += 1
            if all(
                frame.output_claims[pair[arm]] and frame.output_claims[pair[arm]] <= sampled_core
                for arm in ARMS
            ):
                paired_complete += 1
    return paired_any, paired_complete


def analyze_measurement_impact(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
    audit_rows: list[dict[str, Any]],
    sample_map: dict[str, Any],
    audit_report: dict[str, Any],
) -> dict[str, Any]:
    """Join the locked claim frame to the probability audit and aggregate safely."""

    primary_report, sensitivity_report, condition_reports = _validate_recovery(
        plan, census, recovery_input, recovery_report
    )
    frame = _build_frame(plan, census, recovery_input, recovery_report)
    audit = _read_human_audit(frame, audit_rows, sample_map, audit_report)
    units = frame.units
    family_sizes = frame.family_sizes
    output_claims = frame.output_claims
    output_categories = frame.output_categories
    output_losses = frame.output_losses
    family_ids = frame.family_ids
    sampled = audit.sampled
    enriched = audit.enriched
    known = audit.known
    agreement = audit.agreement

    def baseline(
        conditions: tuple[str, ...], partial_weight: float, arms: tuple[str, ...]
    ) -> float:
        return math.fsum(
            (1 if arm == "B1" or len(arms) == 1 else -1)
            * output_losses[(family_id, condition, arm)][partial_weight]
            for family_id in family_ids
            for condition in conditions
            for arm in arms
        ) / (len(family_ids) * len(conditions))

    primary_baseline = baseline(CORE_CONDITIONS, plan.partial_support_primary_weight, ARMS)
    sensitivity_baseline = baseline(CORE_CONDITIONS, plan.partial_support_sensitivity_weight, ARMS)
    if not math.isclose(
        primary_baseline, primary_report.estimate, rel_tol=0, abs_tol=1e-12
    ) or not math.isclose(
        sensitivity_baseline,
        sensitivity_report.estimate,
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise MeasurementImpactError("frozen B1 minus A3 effects did not reproduce")

    def analysis(
        conditions: tuple[str, ...], weight: float, arms: tuple[str, ...]
    ) -> dict[str, Any]:
        return _effect_sensitivity(
            units=units,
            family_sizes=family_sizes,
            sampled=sampled,
            known=known,
            baseline=baseline(conditions, weight, arms),
            arms=arms,
            conditions=conditions,
            partial_weight=weight,
            family_count=len(family_ids),
        )

    cells: dict[str, Any] = {}
    for condition in CORE_CONDITIONS:
        condition_effect = analysis((condition,), plan.partial_support_primary_weight, ARMS)
        if not math.isclose(
            condition_effect["automatic_loss"],
            condition_reports[condition].estimate,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise MeasurementImpactError("frozen condition effect did not reproduce")
        for arm in ARMS:
            emitted_claim_ids = {
                claim_id
                for claim_id, unit in units.items()
                if unit.arm == arm and unit.condition == condition
            }
            claim_ids = {
                claim_id for claim_id in emitted_claim_ids if units[claim_id].scored_output
            }
            observed = claim_ids & sampled.keys()
            matching_outputs = {
                unit.request_id
                for unit in units.values()
                if unit.arm == arm and unit.condition == condition
            }
            cells[f"{condition}.{arm}"] = {
                "request_count": 32,
                "emitted_claim_count": len(emitted_claim_ids),
                "scored_claim_count": len(claim_ids),
                "probability_sample_claim_count": len(observed),
                "probability_sample_output_count": len(
                    {units[claim_id].request_id for claim_id in observed}
                ),
                "fully_human_labeled_claim_output_count": sum(
                    bool(output_claims[request_id])
                    and all(units[claim_id].scored_output for claim_id in output_claims[request_id])
                    and output_claims[request_id] <= set(sampled)
                    for request_id in matching_outputs
                ),
                "technical_failure_count": output_categories[(condition, arm, "technical_failure")],
                "primary": analysis((condition,), plan.partial_support_primary_weight, (arm,)),
            }
        cells[f"{condition}.B1_minus_A3"] = condition_effect

    sampled_core = {
        claim_id
        for claim_id in sampled
        if units[claim_id].arm in ARMS
        and units[claim_id].condition in CORE_CONDITIONS
        and units[claim_id].scored_output
    }
    scored_core = {
        claim_id
        for claim_id, unit in units.items()
        if unit.arm in ARMS and unit.condition in CORE_CONDITIONS and unit.scored_output
    }
    known_scored_core = scored_core & known.keys()
    paired_any, paired_complete = _paired_coverage(frame, sampled_core)

    return {
        "schema_version": "diagnosis-main-claim-measurement-impact/v1",
        "status": "offline_sensitivity_complete",
        "scientific_scope": "post_outcome_finite_benchmark_measurement_sensitivity",
        "frozen_outcomes_mutated": False,
        "provider_calls": 0,
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "recovery_input_sha256": recovery_input.input_sha256,
        "recovery_report_sha256": recovery_report.report_sha256,
        "human_audit_design_agreement_reproduced": agreement,
        "sample": {
            "emitted_claim_population": len(units),
            "probability_claim_count": len(sampled),
            "enriched_diagnostic_claim_count_excluded_from_estimators": len(enriched),
            "probability_core_B1_A3_claim_count": len(sampled_core),
            "probability_core_B1_A3_output_count": len(
                {units[claim_id].request_id for claim_id in sampled_core}
            ),
            "probability_core_B1_A3_family_count": len(
                {units[claim_id].family_id for claim_id in sampled_core}
            ),
            "scored_core_B1_A3_output_count": len(
                {units[claim_id].request_id for claim_id in scored_core}
            ),
            "scored_core_B1_A3_claim_count": len(scored_core),
            "human_known_scored_core_claim_count": len(known_scored_core),
            "human_unknown_scored_core_claim_count": len(scored_core) - len(known_scored_core),
            "paired_family_condition_count": len(family_ids) * len(CORE_CONDITIONS),
            "paired_family_condition_with_both_arms_sampled": paired_any,
            "fully_human_labeled_paired_family_condition_count": paired_complete,
        },
        "primary_B1_minus_A3": analysis(CORE_CONDITIONS, plan.partial_support_primary_weight, ARMS),
        "prespecified_partial_half_B1_minus_A3": analysis(
            CORE_CONDITIONS, plan.partial_support_sensitivity_weight, ARMS
        ),
        "arm_condition": cells,
        "interpretation": [
            "Only 160 family-stratified probability claims enter design estimators; 40 enriched claims remain separate.",
            "The estimated relabel correction is design-based for the finite emitted-claim frame, not a confirmed corrected P5 endpoint.",
            "All unsampled human labels remain unknown; assumption-free bounds assign their allowed harm weights to extrema.",
            "Claim-level agreement cannot be substituted for output/family loss, and failed requests emit no claims to relabel.",
            "Human-final is an operational reference with observed rater disagreement, not an infallible truth oracle.",
        ],
    }

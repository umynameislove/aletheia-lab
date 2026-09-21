"""Validated contracts for the registered diagnosis main-study analysis."""

from __future__ import annotations

from collections import defaultdict
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ANALYSIS_PLAN_SCHEMA_VERSION: Final = "diagnosis-main-analysis-plan/v2"
ANALYSIS_CENSUS_SCHEMA_VERSION: Final = "diagnosis-main-analysis-census/v2"
ANALYSIS_INPUT_SCHEMA_VERSION: Final = "diagnosis-main-analysis-input/v2"
ANALYSIS_REPORT_SCHEMA_VERSION: Final = "diagnosis-main-analysis-report/v2"

_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
CORE_CONDITIONS: Final = ("full", "missing_key", "noisy")
ALL_CONDITIONS: Final = (*CORE_CONDITIONS, "counterevidence")
CONTROLLED_VARIANTS: Final = (
    "A1",
    "A2",
    "A3",
    "B0",
    "B1",
    "B2",
    "CodeGraph",
    "FULL",
)
PRIMARY_VARIANTS: Final = ("B1", "A3")
HARM_LABELS: Final = frozenset(("contradicted", "unsupported"))
EXPECTED_MECHANISM_COUNTS: Final = {
    "data_drift": 10,
    "preprocessing_mismatch": 10,
    "label_noise": 12,
}
EXPECTED_RESPONSE_MODE: Final = {
    "full": "diagnose",
    "missing_key": "abstain_or_request_evidence",
    "noisy": "diagnose_with_uncertainty",
    "counterevidence": "acknowledge_conflict_or_abstain",
}

SupportLabel = Literal["contradicted", "unsupported", "partially_supported", "fully_supported"]
ClaimType = Literal[
    "cause_assertion",
    "evidence_statement",
    "uncertainty_statement",
    "recommended_action",
    "other",
]
Mechanism = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
EvidenceCondition = Literal["full", "missing_key", "noisy", "counterevidence"]
ExpectedResponseMode = Literal[
    "diagnose",
    "abstain_or_request_evidence",
    "diagnose_with_uncertainty",
    "acknowledge_conflict_or_abstain",
]
ControlledVariant = Literal["A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL"]
TechnicalStatus = Literal["success", "provider_failure", "parse_failure", "unresolved"]
OutputStatus = Literal["completed", "abstained"]


class DiagnosisMainAnalysisError(ValueError):
    """Raised when a registered input cannot be analysed without changing scope."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class DiagnosisMainAnalysisPlan(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-analysis-plan/v3"]
    plan_status: Literal["outcome_blind_analysis_contract"]
    protected_outcomes_opened: Literal[False]
    scientific_scope: Literal["finite_frozen_benchmark_policy_comparison"]
    causal_effect_claim_permitted: Literal[False]
    superpopulation_generalization_permitted: Literal[False]
    formal_null_hypothesis_test: Literal["none_descriptive_registered_rule"]
    primary_variants: tuple[Literal["B1"], Literal["A3"]]
    primary_conditions: tuple[Literal["full"], Literal["missing_key"], Literal["noisy"]]
    auxiliary_condition: Literal["counterevidence"]
    primary_metric: Literal["evidence_accountability_loss"]
    harmful_labels: tuple[Literal["contradicted"], Literal["unsupported"]]
    partial_support_primary_weight: float = Field(ge=0.0, le=1.0)
    partial_support_sensitivity_weight: float = Field(ge=0.0, le=1.0)
    terminal_failure_loss: float = Field(ge=0.0, le=1.0)
    completed_zero_claim_loss: float = Field(ge=0.0, le=1.0)
    appropriate_abstention_loss: float = Field(ge=0.0, le=1.0)
    false_abstention_loss: float = Field(ge=0.0, le=1.0)
    cluster_unit: Literal["case_family_id"]
    dependence_sensitivity_unit: Literal["superfamily_id"]
    interval_method: Literal["bca_family_resampling_stability"]
    interval_role: Literal["descriptive_stability_not_hypothesis_test"]
    bootstrap_replicates: int = Field(ge=10_000)
    bootstrap_seed: int = Field(ge=0)
    confidence_level: float = Field(gt=0.0, lt=1.0)
    minimum_observed_effect_for_support: float = Field(gt=0.0, le=1.0)
    positive_family_fraction_floor: float = Field(gt=0.0, le=1.0)
    support_rule: Literal[
        "estimate_at_least_minimum_and_stability_lower_above_zero_and_positive_family_fraction_at_least_floor_and_leave_one_superfamily_out_min_above_zero"
    ]
    primary_multiplicity: Literal["single_registered_primary_rule"]
    secondary_multiplicity: Literal["descriptive_intervals_no_confirmatory_secondary_claims"]
    primary_estimand: Literal[
        "finite_census_equal_family_mean_of_B1_minus_A3_accountability_loss_across_full_missing_key_noisy"
    ]
    primary_loss_formula: Literal[
        "technical_failure=1; completed_zero_claim=1; appropriate_abstention=0; false_abstention=1; otherwise_mean_atomic_claim_harm"
    ]
    aggregation_order: tuple[
        Literal["claim_to_output_equal_claim_weight"],
        Literal["output_to_family_condition_variant_equal_output_weight"],
        Literal["condition_to_family_variant_equal_condition_weight"],
        Literal["paired_B1_minus_A3_within_family"],
        Literal["family_to_finite_census_equal_family_weight"],
    ]
    secondary_estimands: tuple[
        Literal["emitted_claim_harm_and_output_coverage_by_variant"],
        Literal["missing_key_causal_overclaim_rate_by_B1_and_A3"],
        Literal["full_evidence_A3_false_abstention_rate"],
        Literal["citation_validity_rate_by_citation_required_variant"],
        Literal["partial_support_half_weight_primary_sensitivity"],
        Literal["mechanism_and_condition_stratified_primary_effects"],
        Literal["counterevidence_behavior_separate_from_primary"],
        Literal["leave_one_family_and_superfamily_out_stability"],
    ]
    secondary_claim_status: Literal["descriptive_only"]
    missing_terminal_policy: Literal["invalidate_registered_execution"]
    extra_or_duplicate_record_policy: Literal["invalidate_registered_execution"]
    terminal_failure_policy: Literal["retain_in_denominator_as_loss_one"]
    completed_zero_claim_policy: Literal["retain_in_denominator_as_loss_one"]
    abstention_policy: Literal[
        "condition_aware_score_and_report_separately_from_emitted_claim_harm"
    ]
    post_outcome_exclusion_permitted: Literal[False]
    complete_case_primary_permitted: Literal[False]
    family_count: Literal[32]
    context_count: Literal[128]
    controlled_request_count: Literal[1024]
    superfamily_count: Literal[6]
    precision_classification: Literal["benchmark_local_precision_limited"]
    precision_interpretation: Literal[
        "The interval is a descriptive family-resampling stability summary for the frozen census; it is not a p-value, causal interval, or guarantee of superpopulation coverage."
    ]
    qwen_sensitivity_status: Literal["secondary_descriptive_only"]
    qwen_sensitivity_model: Literal["Qwen3.8-27B-Q8_0"]
    qwen_sensitivity_estimand: Literal[
        "finite_census_equal_family_mean_of_B1_minus_A3_accountability_loss_across_full_missing_key_noisy"
    ]
    qwen_sensitivity_family_count: Literal[12]
    qwen_sensitivity_request_count: Literal[72]
    qwen_cross_model_superiority_claim_permitted: Literal[False]
    qwen_pooling_permitted: Literal[False]
    logdx_pooling_permitted: Literal[False]
    rq6b_pooling_permitted: Literal[False]
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @model_validator(mode="after")
    def _identity_and_order_reconcile(self) -> Self:
        if self.primary_variants != PRIMARY_VARIANTS:
            raise ValueError("primary contrast must be ordered B1 minus A3")
        if self.primary_conditions != CORE_CONDITIONS:
            raise ValueError("primary conditions must retain full/missing_key/noisy order")
        if self.harmful_labels != ("contradicted", "unsupported"):
            raise ValueError("harmful claim labels changed")
        exact_numeric_contract = {
            "partial_support_primary_weight": (self.partial_support_primary_weight, 0.0),
            "partial_support_sensitivity_weight": (
                self.partial_support_sensitivity_weight,
                0.5,
            ),
            "terminal_failure_loss": (self.terminal_failure_loss, 1.0),
            "completed_zero_claim_loss": (self.completed_zero_claim_loss, 1.0),
            "appropriate_abstention_loss": (self.appropriate_abstention_loss, 0.0),
            "false_abstention_loss": (self.false_abstention_loss, 1.0),
            "confidence_level": (self.confidence_level, 0.95),
            "minimum_observed_effect_for_support": (
                self.minimum_observed_effect_for_support,
                0.05,
            ),
            "positive_family_fraction_floor": (
                self.positive_family_fraction_floor,
                0.75,
            ),
        }
        if any(actual != expected for actual, expected in exact_numeric_contract.values()):
            raise ValueError("analysis numeric contract changed")
        if self.plan_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("analysis plan identity does not match content")
        return self


class DiagnosisMainFamily(_StrictFrozenModel):
    family_id: str
    mechanism: Mechanism
    dataset_id: str
    source_unit_id: str
    source_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    intervention_template_id: str
    superfamily_id: str
    source_partition: Literal["prior_registered_outcome"]
    family_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"family_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        text_values = (
            self.family_id,
            self.dataset_id,
            self.source_unit_id,
            self.intervention_template_id,
            self.superfamily_id,
        )
        if any(not value or value != value.strip() for value in text_values):
            raise ValueError("family identifiers must be non-blank and trimmed")
        if self.family_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("family identity does not match content")
        return self


class DiagnosisMainContext(_StrictFrozenModel):
    context_id: str
    case_family_id: str
    evidence_condition: EvidenceCondition
    expected_response_mode: ExpectedResponseMode
    visible_context_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalized_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_cluster_id: str
    counterevidence_source_family_id: str | None
    context_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"context_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if any(
            not value or value != value.strip()
            for value in (self.context_id, self.case_family_id, self.semantic_cluster_id)
        ):
            raise ValueError("context identifiers must be non-blank and trimmed")
        if self.expected_response_mode != EXPECTED_RESPONSE_MODE[self.evidence_condition]:
            raise ValueError("context response mode differs from the frozen condition policy")
        is_counterevidence = self.evidence_condition == "counterevidence"
        if is_counterevidence != (self.counterevidence_source_family_id is not None):
            raise ValueError("only counterevidence contexts require a source family")
        if self.counterevidence_source_family_id == self.case_family_id:
            raise ValueError("counterevidence must come from a different registered family")
        if self.context_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("context identity does not match content")
        return self


class DiagnosisMainExpectedRequest(_StrictFrozenModel):
    request_id: str
    context_id: str
    variant: ControlledVariant
    request_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _request_identity_reconciles(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"request_sha256"})
        if self.request_sha256 != canonical_execution_sha256(payload):
            raise ValueError("expected request identity does not match content")
        if not all(
            value and value == value.strip() for value in (self.request_id, self.context_id)
        ):
            raise ValueError("request and context IDs must be non-blank and trimmed")
        return self


def _validate_census_families(
    families: tuple[DiagnosisMainFamily, ...], expected_count: int
) -> tuple[str, ...]:
    family_ids = tuple(item.family_id for item in families)
    if family_ids != tuple(sorted(family_ids)) or len(family_ids) != len(set(family_ids)):
        raise ValueError("families must be unique and canonically sorted")
    if len(families) != expected_count:
        raise ValueError("family_count does not match the family census")
    mechanism_counts = {
        mechanism: sum(item.mechanism == mechanism for item in families)
        for mechanism in EXPECTED_MECHANISM_COUNTS
    }
    if mechanism_counts != EXPECTED_MECHANISM_COUNTS:
        raise ValueError("mechanism census must retain the frozen 10/10/12 allocation")
    if len({item.dataset_id for item in families}) != 2:
        raise ValueError("main census must retain both registered datasets")
    if len({item.superfamily_id for item in families}) != 6:
        raise ValueError("main census must retain six dataset-by-mechanism superfamilies")
    return family_ids


def _validate_counterevidence_pairings(
    contexts: tuple[DiagnosisMainContext, ...],
    families: tuple[DiagnosisMainFamily, ...],
) -> None:
    family_by_id = {item.family_id: item for item in families}
    for item in contexts:
        source_id = item.counterevidence_source_family_id
        if source_id is None:
            continue
        if source_id not in family_by_id:
            raise ValueError("counterevidence references an unknown family")
        target = family_by_id[item.case_family_id]
        source = family_by_id[source_id]
        if target.mechanism != source.mechanism or target.dataset_id != source.dataset_id:
            raise ValueError("counterevidence pairing must remain within dataset and mechanism")


def _validate_census_contexts(
    contexts: tuple[DiagnosisMainContext, ...],
    families: tuple[DiagnosisMainFamily, ...],
    family_ids: tuple[str, ...],
    expected_count: int,
) -> tuple[str, ...]:
    context_ids = tuple(item.context_id for item in contexts)
    if context_ids != tuple(sorted(context_ids)) or len(context_ids) != len(set(context_ids)):
        raise ValueError("contexts must be unique and canonically sorted")
    if len(contexts) != expected_count:
        raise ValueError("context_count does not match the context census")
    if any(item.case_family_id not in set(family_ids) for item in contexts):
        raise ValueError("context references an unknown family")
    family_conditions = {(item.case_family_id, item.evidence_condition) for item in contexts}
    required = {(family_id, condition) for family_id in family_ids for condition in ALL_CONDITIONS}
    if family_conditions != required:
        raise ValueError("every family must have exactly four evidence siblings")
    visible_hashes = tuple(item.visible_context_sha256 for item in contexts)
    normalized_hashes = tuple(item.normalized_content_sha256 for item in contexts)
    if len(visible_hashes) != len(set(visible_hashes)) or len(normalized_hashes) != len(
        set(normalized_hashes)
    ):
        raise ValueError("exact or normalized duplicate contexts are forbidden")
    semantic_families: dict[str, set[str]] = defaultdict(set)
    for item in contexts:
        semantic_families[item.semantic_cluster_id].add(item.case_family_id)
    if any(len(group) != 1 for group in semantic_families.values()):
        raise ValueError("semantic duplicate clusters cannot cross family boundaries")
    _validate_counterevidence_pairings(contexts, families)
    return context_ids


def _validate_census_requests(
    requests: tuple[DiagnosisMainExpectedRequest, ...],
    context_ids: tuple[str, ...],
    expected_count: int,
) -> None:
    request_ids = tuple(item.request_id for item in requests)
    request_hashes = tuple(item.request_sha256 for item in requests)
    if request_ids != tuple(sorted(request_ids)):
        raise ValueError("census requests must be canonically sorted by request_id")
    if len(request_ids) != len(set(request_ids)) or len(request_hashes) != len(set(request_hashes)):
        raise ValueError("census request identities must be unique")
    if len(requests) != expected_count:
        raise ValueError("request_count does not match the request census")
    if any(item.context_id not in set(context_ids) for item in requests):
        raise ValueError("request references an unknown context")
    expected_cells = {
        (context_id, variant) for context_id in context_ids for variant in CONTROLLED_VARIANTS
    }
    actual_cells = {(item.context_id, item.variant) for item in requests}
    if actual_cells != expected_cells:
        raise ValueError("controlled eight-path request matrix is incomplete")


class DiagnosisMainAnalysisCensus(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-analysis-census/v2"]
    source_partition: Literal["sealed_main"]
    family_count: Literal[32]
    context_count: Literal[128]
    request_count: Literal[1024]
    exact_duplicate_context_count: Literal[0]
    cross_family_semantic_duplicate_count: Literal[0]
    families: tuple[DiagnosisMainFamily, ...]
    contexts: tuple[DiagnosisMainContext, ...]
    requests: tuple[DiagnosisMainExpectedRequest, ...]
    census_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"census_sha256"})

    @model_validator(mode="after")
    def _census_reconciles(self) -> Self:
        family_ids = _validate_census_families(self.families, self.family_count)
        context_ids = _validate_census_contexts(
            self.contexts, self.families, family_ids, self.context_count
        )
        _validate_census_requests(self.requests, context_ids, self.request_count)
        if self.census_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("analysis census identity does not match content")
        return self


class DiagnosisMainClaim(_StrictFrozenModel):
    claim_id: str
    claim_type: ClaimType
    support_label: SupportLabel
    citation_required: bool
    citation_present: bool
    citation_ids_valid: bool

    @model_validator(mode="after")
    def _claim_fields_reconcile(self) -> Self:
        if not self.claim_id or self.claim_id != self.claim_id.strip():
            raise ValueError("claim_id must be non-blank and trimmed")
        if not self.citation_present and self.citation_ids_valid:
            raise ValueError("absent citations cannot be declared valid")
        return self


class DiagnosisMainObservedRecord(_StrictFrozenModel):
    request_id: str
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    technical_status: TechnicalStatus
    output_status: OutputStatus | None
    claims: tuple[DiagnosisMainClaim, ...]

    @model_validator(mode="after")
    def _terminal_shape_reconciles(self) -> Self:
        if not self.request_id or self.request_id != self.request_id.strip():
            raise ValueError("request_id must be non-blank and trimmed")
        if self.technical_status == "success":
            if self.output_status is None:
                raise ValueError("successful records require an output status")
        elif self.output_status is not None or self.claims:
            raise ValueError("technical failures cannot claim a parsed output")
        if self.output_status == "abstained" and any(
            item.claim_type == "cause_assertion" for item in self.claims
        ):
            raise ValueError("abstained outputs cannot retain cause assertions")
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique within one output")
        return self


class DiagnosisMainAnalysisInput(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-analysis-input/v2"]
    analysis_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    census_sha256: str = Field(pattern=_SHA256_PATTERN)
    records: tuple[DiagnosisMainObservedRecord, ...]
    input_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"input_sha256"})

    @model_validator(mode="after")
    def _input_reconciles(self) -> Self:
        request_ids = tuple(item.request_id for item in self.records)
        if request_ids != tuple(sorted(request_ids)):
            raise ValueError("analysis records must be canonically sorted by request_id")
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("analysis records contain duplicate request IDs")
        if self.input_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("analysis input identity does not match content")
        return self


class MetricInterval(_StrictFrozenModel):
    estimate: float
    lower: float
    upper: float
    confidence_level: float = Field(default=0.95, ge=0.95, le=0.95)
    family_count: int = Field(ge=1)
    interval_role: Literal["descriptive_family_resampling_stability"] = (
        "descriptive_family_resampling_stability"
    )


class StabilityDiagnostics(_StrictFrozenModel):
    positive_family_fraction: float = Field(ge=0.0, le=1.0)
    zero_family_fraction: float = Field(ge=0.0, le=1.0)
    family_effect_minimum: float
    family_effect_median: float
    family_effect_maximum: float
    leave_one_family_out_minimum: float
    leave_one_family_out_maximum: float
    leave_one_superfamily_out_minimum: float
    leave_one_superfamily_out_maximum: float
    superfamily_count: int = Field(ge=2)


class EmittedClaimSummary(_StrictFrozenModel):
    total_output_count: int = Field(ge=1)
    output_with_claims_count: int = Field(ge=0)
    emitted_claim_count: int = Field(ge=0)
    harmful_claim_count: int = Field(ge=0)
    partially_supported_claim_count: int = Field(ge=0)
    output_coverage: float = Field(ge=0.0, le=1.0)
    harmful_claim_rate: float | None


class DiagnosisMainAnalysisReport(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-analysis-report/v2"]
    status: Literal["valid_registered_analysis", "invalid_registered_execution"]
    scientific_scope: Literal["finite_frozen_benchmark_policy_comparison"]
    causal_effect_claimed: Literal[False]
    formal_p_value_reported: Literal[False]
    analysis_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    census_sha256: str = Field(pattern=_SHA256_PATTERN)
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_request_count: int = Field(ge=1)
    terminal_request_count: int = Field(ge=0)
    missing_request_ids: tuple[str, ...]
    unexpected_request_ids: tuple[str, ...]
    technical_status_counts: dict[str, int]
    raw_claim_count: int = Field(ge=0)
    primary_effect: MetricInterval | None
    partial_support_sensitivity_effect: MetricInterval | None
    primary_disposition: Literal[
        "benchmark_local_support",
        "benchmark_local_support_not_established",
        "unstable_or_precision_limited",
        "invalid_result",
    ]
    stability_diagnostics: StabilityDiagnostics | None
    mechanism_primary_effects: dict[str, MetricInterval] | None
    condition_primary_effects: dict[str, MetricInterval] | None
    counterevidence_effect: MetricInterval | None
    emitted_claim_summaries: dict[str, EmittedClaimSummary] | None
    abstention_rates: dict[str, float] | None
    missing_key_causal_overclaim: dict[str, MetricInterval] | None
    full_evidence_false_abstention_a3: MetricInterval | None
    citation_validity: dict[str, float]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"report_sha256"})

    @model_validator(mode="after")
    def _report_reconciles(self) -> Self:
        invalid = self.status == "invalid_registered_execution"
        if invalid != bool(self.missing_request_ids or self.unexpected_request_ids):
            raise ValueError("report validity does not match request reconciliation")
        estimates = (
            self.primary_effect,
            self.partial_support_sensitivity_effect,
            self.stability_diagnostics,
            self.mechanism_primary_effects,
            self.condition_primary_effects,
            self.counterevidence_effect,
            self.emitted_claim_summaries,
            self.abstention_rates,
            self.missing_key_causal_overclaim,
            self.full_evidence_false_abstention_a3,
        )
        if invalid and any(item is not None for item in estimates):
            raise ValueError("invalid executions cannot publish registered estimates")
        if invalid and (self.primary_disposition != "invalid_result" or self.citation_validity):
            raise ValueError("invalid execution requires an empty invalid disposition")
        if self.report_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("analysis report identity does not match content")
        return self

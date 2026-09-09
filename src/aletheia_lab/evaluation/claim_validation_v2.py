"""Close V1 honestly and freeze a separate prospective V2 validation design.

This module is deliberately provider-free.  It verifies the immutable V1
artifacts, characterizes only diagnostics that were actually persisted, and
validates the prospective V2 protocol before any V2 provider request exists.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import median
from typing import Annotated, Final, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.claim_corpus_recovery_closeout import (
    RecoveryExecutionCloseout,
    RecoveryMissingnessSummary,
)
from aletheia_lab.evaluation.claim_corpus_terminal_reader import (
    ClaimCorpusTerminalReader,
)
from aletheia_lab.evaluation.claim_pool_closeout import (
    ClaimPoolFeasibilityCloseout,
    LabelStratumCensus,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.instrument_validation import ClaimSupportValidationProtocol
from aletheia_lab.model_gateway.contracts import AttemptRecord
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
CommitRef = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
ModelT = TypeVar("ModelT", bound=BaseModel)
ValidationLabel = Literal["contradicted", "unsupported", "partially_supported", "fully_supported"]
V2_LABEL_ORDER: Final[tuple[ValidationLabel, ...]] = (
    "contradicted", "unsupported", "partially_supported", "fully_supported"
)

V1_AUDIT_PATH: Final = Path("configs/evaluation/claim_support_validation_v1_failure_audit.json")
V1_VALIDATION_PROTOCOL_PATH: Final = Path(
    "configs/evaluation/claim_support_validation_protocol.json"
)
V2_PROTOCOL_PATH: Final = Path("configs/evaluation/claim_support_validation_v2_protocol.json")


class ClaimValidationV2Error(ValueError):
    """Raised when V1 evidence or the prospective V2 freeze is inconsistent."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class CountSummary(_StrictFrozenModel):
    """An exact closed-cohort count for one named stratum."""

    stratum_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    scheduled_count: int = Field(ge=0, le=1800)
    parsed_count: int = Field(ge=0, le=1800)
    technical_failure_count: int = Field(ge=0, le=1800)
    retried_count: int = Field(ge=0, le=1800)

    @model_validator(mode="after")
    def _counts_reconcile(self) -> Self:
        if (
            self.parsed_count + self.technical_failure_count != self.scheduled_count
            or self.retried_count > self.scheduled_count
        ):
            raise ValueError("closed-cohort stratum counts do not reconcile")
        return self


class LabelAvailabilitySummary(_StrictFrozenModel):
    """Observed V1 pool availability; this is not a prevalence estimate."""

    automatic_label: Literal[
        "contradicted",
        "unsupported",
        "partially_supported",
        "fully_supported",
    ]
    claim_count: int = Field(ge=0, le=1800)
    unique_claim_text_count: int = Field(ge=0, le=1800)
    family_count: int = Field(ge=0, le=15)
    output_count: int = Field(ge=0, le=360)
    quota_satisfied: bool


class LatencySummary(_StrictFrozenModel):
    """Exact stored monotonic timing summary in nanoseconds."""

    count: int = Field(ge=0, le=720)
    minimum_ns: int = Field(ge=0)
    median_ns: float = Field(ge=0)
    maximum_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def _range_reconciles(self) -> Self:
        if not self.count or not self.minimum_ns <= self.median_ns <= self.maximum_ns:
            raise ValueError("latency summary is empty or unordered")
        return self


class ClosedV1FailureAudit(_StrictFrozenModel):
    """Public-safe aggregate of the immutable V1 diagnosis and pool artifacts."""

    schema_version: Literal["claim-support-validation-v1-failure-audit/v1"]
    status: Literal["closed_v1_inadequate_for_balanced_validation_v2_required"]
    diagnosis_source_commit_ref: CommitRef
    closeout_source_commit_ref: CommitRef
    recovery_authorization_sha256: Sha256
    recovery_protocol_sha256: Sha256
    recovery_execution_receipt_sha256: Sha256
    recovery_terminal_store_sha256: Sha256
    terminal_store_independently_recomputed: Literal[True]
    recovery_closeout_sha256: Sha256
    pool_feasibility_closeout_sha256: Sha256
    corpus_manifest_sha256: Sha256
    v1_validation_protocol_sha256: Sha256
    terminal_request_count: Literal[360]
    parsed_terminal_count: int = Field(ge=0, le=360)
    technical_failure_terminal_count: int = Field(ge=0, le=360)
    technical_attempt_count: int = Field(ge=360, le=720)
    attempt_outcome_counts: dict[str, int]
    terminal_issue_code_counts: dict[str, int]
    attempt_issue_code_counts: dict[str, int]
    retried_request_count: int = Field(ge=0, le=360)
    recovered_on_second_attempt_count: int = Field(ge=0, le=360)
    failed_after_second_attempt_count: int = Field(ge=0, le=360)
    failed_request_ordinals: tuple[int, ...]
    recovered_request_ordinals: tuple[int, ...]
    first_technical_failure_ordinal: int = Field(ge=1, le=360)
    consecutive_parsed_prefix_count: int = Field(ge=0, le=359)
    response_latency: LatencySummary
    transient_error_latency: LatencySummary
    retry_gap: LatencySummary
    provider_attempt_reference_count: int = Field(ge=0, le=720)
    distinct_provider_attempt_reference_count: int = Field(ge=0, le=720)
    failure_diagnostics_present_count: int = Field(ge=0, le=720)
    failed_request_raw_response_count: int = Field(ge=0, le=360)
    exact_provider_failure_cause_known: Literal[False]
    rate_limit_cause_established: Literal[False]
    diagnostic_limitation: Literal["transport_collapsed_transient_subtypes_before_persistence"]
    mechanism_summaries: tuple[CountSummary, ...] = Field(min_length=3, max_length=3)
    condition_summaries: tuple[CountSummary, ...] = Field(min_length=3, max_length=3)
    variant_summaries: tuple[CountSummary, ...] = Field(min_length=8, max_length=8)
    execution_order_dimensions: tuple[
        Literal["mechanism"],
        Literal["family"],
        Literal["evidence_condition"],
        Literal["variant"],
    ]
    missingness_exchangeability_status: Literal["not_established_execution_order_confounded"]
    normalized_output_count: int = Field(ge=0, le=360)
    normalization_rejection_count: Literal[0]
    claim_candidate_count: int = Field(ge=0, le=1800)
    label_availability: tuple[LabelAvailabilitySummary, ...] = Field(min_length=4, max_length=4)
    exact_frozen_selection_feasible: Literal[False]
    blocking_label_strata: tuple[Literal["contradicted"], Literal["unsupported"]]
    v1_results_poolable_with_v2: Literal[False]
    v1_rerun_or_relabel_forbidden: Literal[True]
    provider_calls_executed_during_audit: Literal[False]
    claims_materialized_during_audit: Literal[False]
    blind_packets_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    audit_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"audit_sha256"})

    @model_validator(mode="after")
    def _audit_reconciles(self) -> Self:
        failed = self.failed_request_ordinals
        retried_from_outcomes = self.attempt_outcome_counts.get("transient_error", 0)
        labels = tuple(item.automatic_label for item in self.label_availability)
        dimension_totals = tuple(
            sum(item.scheduled_count for item in summaries)
            for summaries in (
                self.mechanism_summaries,
                self.condition_summaries,
                self.variant_summaries,
            )
        )
        if (
            self.parsed_terminal_count + self.technical_failure_terminal_count
            != self.terminal_request_count
            or sum(self.attempt_outcome_counts.values()) != self.technical_attempt_count
            or len(failed) != self.technical_failure_terminal_count
            or len(set(failed)) != len(failed)
            or len(self.recovered_request_ordinals) != self.recovered_on_second_attempt_count
            or self.retried_request_count
            != self.recovered_on_second_attempt_count + self.failed_after_second_attempt_count
            or self.failed_after_second_attempt_count != self.technical_failure_terminal_count
            or retried_from_outcomes
            != self.retried_request_count + self.failed_after_second_attempt_count
            or self.attempt_outcome_counts
            != {"response": self.parsed_terminal_count, "transient_error": retried_from_outcomes}
            or self.terminal_issue_code_counts
            != {"retry_exhausted": self.technical_failure_terminal_count}
            or self.attempt_issue_code_counts
            != {
                "retry_exhausted": self.technical_failure_terminal_count,
                "transient_provider_error": self.retried_request_count,
            }
            or failed[0] != self.first_technical_failure_ordinal
            or self.consecutive_parsed_prefix_count != self.first_technical_failure_ordinal - 1
            or tuple(sorted(failed)) != failed
            or tuple(sorted(self.recovered_request_ordinals)) != self.recovered_request_ordinals
            or set(failed) & set(self.recovered_request_ordinals)
            or self.provider_attempt_reference_count
            != self.distinct_provider_attempt_reference_count
            or self.provider_attempt_reference_count != self.technical_attempt_count
            or self.response_latency.count != self.parsed_terminal_count
            or self.transient_error_latency.count != retried_from_outcomes
            or self.retry_gap.count != self.retried_request_count
            or self.normalized_output_count != self.parsed_terminal_count
            or dimension_totals != (360, 360, 360)
            or labels
            != (
                "contradicted",
                "unsupported",
                "partially_supported",
                "fully_supported",
            )
            or tuple(item.quota_satisfied for item in self.label_availability)
            != (False, False, True, True)
            or sum(item.claim_count for item in self.label_availability)
            != self.claim_candidate_count
            or self.failure_diagnostics_present_count
            or self.failed_request_raw_response_count
        ):
            raise ValueError("closed V1 audit counts do not reconcile")
        if self.audit_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("closed V1 audit hash differs from canonical content")
        return self


class ProspectiveScientificScope(_StrictFrozenModel):
    research_question: Literal["construct_validity_of_automatic_claim_evidence_relation_labels"]
    source_population: Literal["new_development_only_diagnoses_and_authentic_visible_evidence"]
    target_population: Literal[
        "prospectively_constructed_development_challenge_pairs_conditioned_on_technical_success"
    ]
    primary_estimand: Literal[
        "agreement_of_automatic_relation_labels_with_blinded_adjudicated_human_labels"
    ]
    prevalence_estimation_for_natural_model_outputs: Literal[False]
    technical_success_conditioned_estimand_disclosed: Literal[True]
    missingness_exchangeability_claim_authorized: Literal[False]
    technical_failure_sensitivity_analysis_required: Literal[True]
    failure_rate_as_variant_or_mechanism_performance_forbidden: Literal[True]
    diagnosis_variant_superiority_claim_authorized: Literal[False]
    mechanism_causal_claim_authorized: Literal[False]
    main_evaluation_claim_authorized: Literal[False]


class ProspectiveSourceFrame(_StrictFrozenModel):
    mechanism_count: Literal[3]
    primary_family_count: Literal[15]
    evidence_conditions: tuple[Literal["full"], Literal["missing_key"], Literal["noisy"]]
    diagnosis_variants: tuple[
        Literal["A1"],
        Literal["A2"],
        Literal["A3"],
        Literal["B0"],
        Literal["B1"],
        Literal["B2"],
        Literal["CodeGraph"],
        Literal["FULL"],
    ]
    diagnosis_request_count: Literal[360]
    provider_backed_diagnosis_request_count: Literal[315]
    deterministic_diagnosis_request_count: Literal[45]
    execution_schedule: Literal["balanced_interleave_sha256/v1"]
    new_request_identities_required: Literal[True]
    maximum_source_claims_per_output: Literal[2]
    relation_frames: tuple[
        Literal["natural_context"],
        Literal["support_withdrawal"],
        Literal["partial_support_projection"],
        Literal["direct_counterevidence"],
    ]
    maximum_relation_contexts_per_source_claim: Literal[2]
    relation_request_ceiling: Literal[1440]
    authentic_development_evidence_required: Literal[True]
    outcome_blind_frame_assignment_required: Literal[True]
    source_claim_selection_algorithm: Literal["canonical-claim-hash/v1"]
    frame_assignment_algorithm: Literal[
        "pre-relation-balanced-hash-over-eligible-authentic-frames/v1"
    ]
    frame_assignment_uses_relation_outcomes: Literal[False]
    frame_intent_is_design_stratum_not_ground_truth: Literal[True]
    ineligible_frame_logged_without_post_outcome_substitution: Literal[True]
    minimum_families_per_relation_frame: Literal[10]
    minimum_scheduled_diagnosis_cells_per_relation_frame: Literal[25]
    synthetic_evidence_forbidden: Literal[True]
    language_negation_as_counterevidence_forbidden: Literal[True]
    main_or_sealed_evidence_forbidden: Literal[True]


class ProspectiveReliabilityPolicy(_StrictFrozenModel):
    provider: Literal["openai"]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_diagnosis_output_tokens_per_request: Literal[2048]
    maximum_relation_output_tokens_per_request: Literal[600]
    matched_variant_model_context_evidence_and_budget_equal: Literal[True]
    request_timeout_seconds: Literal[60]
    maximum_attempts_per_request: Literal[2]
    minimum_provider_start_interval_ms: Literal[1000]
    retry_initial_backoff_ms: Literal[5000]
    retry_backoff_multiplier: Literal[2]
    retry_backoff_ceiling_ms: Literal[60000]
    retry_after_honored: Literal[True]
    retry_after_ceiling_ms: Literal[60000]
    retryable_categories: tuple[
        Literal["rate_limited"],
        Literal["timeout"],
        Literal["connection"],
        Literal["http_408"],
        Literal["http_409"],
        Literal["http_425"],
        Literal["server_error"],
    ]
    non_retryable_categories: tuple[
        Literal["request_rejected"],
        Literal["refusal"],
        Literal["truncated"],
        Literal["invalid_envelope"],
        Literal["schema_incompatible"],
    ]
    raw_provider_messages_persisted: Literal[False]
    safe_failure_subtype_persisted: Literal[True]
    terminal_failures_preserved_in_denominator: Literal[True]
    synthetic_qualification_request_count: Literal[7]
    qualification_requires_all_parsed: Literal[True]
    qualification_outputs_admitted_to_corpus: Literal[False]
    minimum_global_parsed_rate: float = Field(ge=0.0, le=1.0)
    minimum_mechanism_parsed_rate: float = Field(ge=0.0, le=1.0)
    minimum_condition_parsed_rate: float = Field(ge=0.0, le=1.0)
    minimum_model_backed_variant_parsed_rate: float = Field(ge=0.0, le=1.0)
    deterministic_variant_requires_all_terminal: Literal[True]

    @model_validator(mode="after")
    def _rates_are_frozen(self) -> Self:
        if (
            self.minimum_global_parsed_rate,
            self.minimum_mechanism_parsed_rate,
            self.minimum_condition_parsed_rate,
            self.minimum_model_backed_variant_parsed_rate,
        ) != (0.95, 0.9, 0.9, 0.9):
            raise ValueError("V2 technical admission rates differ from the freeze")
        return self


class ProspectiveSamplingPolicy(_StrictFrozenModel):
    sample_target: Literal[200]
    automatic_label_quota: Literal[50]
    sampling_algorithm: Literal["balanced-label-round-robin-hash/v2"]
    minimum_families_per_label: Literal[10]
    minimum_outputs_per_label: Literal[25]
    maximum_claims_per_family_per_label: Literal[5]
    maximum_claims_per_output_per_label: Literal[2]
    maximum_selected_pairs_per_source_claim: Literal[1]
    repeated_canonical_claim_text_forbidden: Literal[True]
    source_frame_intent_withheld_from_raters: Literal[True]
    independent_rater_count: Literal[2]
    adjudicator_count: Literal[1]
    adjudication_required_for_disagreement: Literal[True]
    adjudication_required_if_either_rater_marks_contradicted: Literal[True]
    ordered_labels: tuple[ValidationLabel, ...]
    rater_visibility_contract: Literal["blind-id-claim-visible-evidence-only/v2"]
    false_supported_denominator: Literal["automatic_supported_predictions"]
    contradicted_to_supported_denominator: Literal["adjudicated_contradicted_claims"]
    cluster_unit: Literal["case_family_id"]
    bootstrap_replicates: Literal[2000]
    bootstrap_seed: Literal[73021]
    model_as_human_rater_forbidden: Literal[True]
    minimum_quadratic_weighted_kappa: float = Field(ge=0.0, le=1.0)
    minimum_automatic_macro_f1: float = Field(ge=0.0, le=1.0)
    maximum_false_supported_rate: float = Field(ge=0.0, le=1.0)
    maximum_contradicted_to_supported_rate: float = Field(ge=0.0, le=1.0)
    padding_or_post_outcome_resampling_forbidden: Literal[True]
    blind_packets_require_exact_feasibility_closeout: Literal[True]

    @model_validator(mode="after")
    def _human_validation_thresholds_are_frozen(self) -> Self:
        if (
            self.minimum_quadratic_weighted_kappa,
            self.minimum_automatic_macro_f1,
            self.maximum_false_supported_rate,
            self.maximum_contradicted_to_supported_rate,
        ) != (0.7, 0.8, 0.1, 0.05) or self.ordered_labels != V2_LABEL_ORDER:
            raise ValueError("V2 human-validation thresholds differ from V1")
        return self


class ProspectiveCostPlan(_StrictFrozenModel):
    currency: Literal["USD"]
    diagnosis_estimated_upper_cost: float = Field(gt=0.0)
    diagnosis_estimate_basis: Literal["registered_v1_recovery_authorization"]
    relation_estimated_upper_cost: float = Field(gt=0.0)
    relation_estimate_basis: Literal[
        "v1_relation_estimate_per_request_scaled_to_v2_request_ceiling"
    ]
    qualification_allowance: float = Field(gt=0.0)
    combined_planning_estimate: float = Field(gt=0.0)
    combined_operator_ceiling: float = Field(gt=0.0)
    exact_tokens_and_current_prices_required_before_authorization: Literal[True]
    authorization_created_by_this_protocol: Literal[False]

    @model_validator(mode="after")
    def _planning_values_are_frozen_and_reconcile(self) -> Self:
        values = (
            self.diagnosis_estimated_upper_cost,
            self.relation_estimated_upper_cost,
            self.qualification_allowance,
            self.combined_planning_estimate,
            self.combined_operator_ceiling,
        )
        if values != (13.230724, 22.344669, 0.5, 36.075393, 36.09):
            raise ValueError("V2 cost planning values differ from the freeze")
        return self


class ClaimSupportValidationV2Protocol(_StrictFrozenModel):
    """Prospective V2 design; validation does not grant execution authority."""

    schema_version: Literal["claim-support-validation-v2-protocol/v1"]
    protocol_id: Literal["claim-support-instrument-validation-v2"]
    status: Literal["prospective_v2_protocol_frozen_no_provider_authority"]
    frozen_before_v2_provider_calls: Literal[True]
    predecessor_failure_audit_sha256: Sha256
    predecessor_validation_protocol_sha256: Sha256
    predecessor_cohort_disposition: Literal["closed_insufficient_nonpoolable_historical_evidence"]
    scientific_scope: ProspectiveScientificScope
    source_frame: ProspectiveSourceFrame
    reliability_policy: ProspectiveReliabilityPolicy
    sampling_policy: ProspectiveSamplingPolicy
    cost_plan: ProspectiveCostPlan
    v1_outputs_reused_in_v2_sample: Literal[False]
    v1_automatic_labels_reused_in_v2_sample: Literal[False]
    v1_human_annotations_reused_in_v2_sample: Literal[False]
    provider_calls_executed: Literal[False]
    claims_materialized: Literal[False]
    blind_packets_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    protocol_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"protocol_sha256"})

    @model_validator(mode="after")
    def _design_and_identity_reconcile(self) -> Self:
        frame = self.source_frame
        sampling = self.sampling_policy
        if (
            frame.diagnosis_request_count
            != frame.primary_family_count
            * len(frame.evidence_conditions)
            * len(frame.diagnosis_variants)
            or frame.provider_backed_diagnosis_request_count
            + frame.deterministic_diagnosis_request_count
            != frame.diagnosis_request_count
            or frame.relation_request_ceiling
            != frame.diagnosis_request_count
            * frame.maximum_source_claims_per_output
            * frame.maximum_relation_contexts_per_source_claim
            or sampling.sample_target != sampling.automatic_label_quota * 4
        ):
            raise ValueError("prospective V2 design counts do not reconcile")
        if self.protocol_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("prospective V2 protocol hash differs from canonical content")
        return self


def _load_model(path: Path, model: type[ModelT], *, label: str) -> ModelT:
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_file():
            raise OSError("artifact is not a regular file")
        return model.model_validate_json(resolved.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2Error(f"{label} is unavailable or invalid") from exc


def load_v1_failure_audit(path: Path) -> ClosedV1FailureAudit:
    return _load_model(path, ClosedV1FailureAudit, label="V1 failure audit")


def load_v2_protocol(path: Path) -> ClaimSupportValidationV2Protocol:
    return _load_model(path, ClaimSupportValidationV2Protocol, label="V2 protocol")


def verify_tracked_v2_protocol(root: Path) -> dict[str, object]:
    """Verify the V1 audit, historical V1 protocol and prospective V2 freeze."""

    checked_root = root.resolve()
    audit = load_v1_failure_audit(checked_root / V1_AUDIT_PATH)
    v2 = load_v2_protocol(checked_root / V2_PROTOCOL_PATH)
    v1 = _load_model(
        checked_root / V1_VALIDATION_PROTOCOL_PATH,
        ClaimSupportValidationProtocol,
        label="V1 validation protocol",
    )
    if (
        v2.predecessor_failure_audit_sha256 != audit.audit_sha256
        or v2.predecessor_validation_protocol_sha256 != v1.protocol_sha256
        or audit.v1_validation_protocol_sha256 != v1.protocol_sha256
    ):
        raise ClaimValidationV2Error("V1 audit and V2 protocol lineage do not reconcile")
    return {
        "schema_version": "claim-support-validation-v2-readiness/v1",
        "status": "claim_support_validation_v2_protocol_frozen_implementation_pending",
        "v1_audit_sha256": audit.audit_sha256,
        "v2_protocol_sha256": v2.protocol_sha256,
        "v1_terminal_request_count": audit.terminal_request_count,
        "v1_parsed_terminal_count": audit.parsed_terminal_count,
        "v1_technical_failure_count": audit.technical_failure_terminal_count,
        "v1_exact_selection_feasible": audit.exact_frozen_selection_feasible,
        "v2_diagnosis_request_count": v2.source_frame.diagnosis_request_count,
        "v2_relation_request_ceiling": v2.source_frame.relation_request_ceiling,
        "next_gate": "v2_runtime_and_source_frame_implementation",
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _latency_summary(values: Sequence[int]) -> dict[str, int | float]:
    if not values:
        raise ClaimValidationV2Error("V1 timing evidence is unexpectedly empty")
    return {
        "count": len(values),
        "minimum_ns": min(values),
        "median_ns": median(values),
        "maximum_ns": max(values),
    }


def _count_summary(item: RecoveryMissingnessSummary) -> dict[str, object]:
    return {
        "stratum_id": item.stratum_id,
        "scheduled_count": item.scheduled_request_count,
        "parsed_count": item.parsed_request_count,
        "technical_failure_count": item.technical_failure_count,
        "retried_count": item.retried_request_count,
    }


def _label_summary(item: LabelStratumCensus) -> dict[str, object]:
    return {
        "automatic_label": item.automatic_label,
        "claim_count": item.claim_count,
        "unique_claim_text_count": item.unique_claim_text_count,
        "family_count": item.family_count,
        "output_count": item.output_count,
        "quota_satisfied": item.claim_quota_satisfied,
    }


def _request_attempt_evidence(
    store: Path,
    closeout: RecoveryExecutionCloseout,
) -> dict[str, object]:
    outcomes: Counter[str] = Counter()
    attempt_issues: Counter[str] = Counter()
    terminal_issues: Counter[str] = Counter()
    response_latencies: list[int] = []
    transient_latencies: list[int] = []
    retry_gaps: list[int] = []
    provider_refs: list[str] = []
    failed_ordinals: list[int] = []
    recovered_ordinals: list[int] = []
    diagnostics = failed_raw = retried = 0
    identities = {item.request_identity_sha256 for item in closeout.request_dispositions}
    _verify_store_members(store, identities)
    authority_hashes: list[tuple[str, str]] = []
    shard_hashes: list[tuple[str, str]] = []
    for disposition in closeout.request_dispositions:
        identity = disposition.request_identity_sha256
        reader = _reader(store, identity)
        authority_hashes.append(
            (identity, content_sha256((store / "authorities" / f"{identity}.json").read_bytes()))
        )
        shard_hashes.append((identity, reader.store_sha256()))
        inventory = reader.terminal_inventory(identity)
        records = reader.terminal_attempt_records(identity)
        terminal_issue = reader.terminal_issue(identity)
        if (
            inventory.gateway_status != disposition.gateway_status
            or len(records) != disposition.attempt_count
            or inventory.request_identity_sha256 != identity
        ):
            raise ClaimValidationV2Error("V1 terminal evidence differs from its closeout")
        retried += len(records) > 1
        if len(records) > 1:
            retry_gaps.append(records[1].timing.started_ns - records[0].timing.ended_ns)
            if inventory.gateway_status == "parsed":
                recovered_ordinals.append(disposition.request_ordinal)
        if inventory.gateway_status != "parsed":
            failed_ordinals.append(disposition.request_ordinal)
            failed_raw += inventory.raw_response_sha256 is not None
        if terminal_issue is not None:
            terminal_issues[terminal_issue.code] += 1
        _collect_attempt_records(
            records,
            outcomes=outcomes,
            issues=attempt_issues,
            response_latencies=response_latencies,
            transient_latencies=transient_latencies,
            provider_refs=provider_refs,
        )
        diagnostics += sum(record.failure_diagnostics is not None for record in records)
    store_identity = canonical_execution_sha256(
        {
            "schema_version": "claim-corpus-sharded-attempt-store/v1",
            "authorities": tuple(sorted(authority_hashes)),
            "shards": tuple(sorted(shard_hashes)),
        }
    )
    if store_identity != closeout.recovery_terminal_store_sha256:
        raise ClaimValidationV2Error("V1 diagnosis store identity differs from closeout")
    return {
        "attempt_outcome_counts": dict(sorted(outcomes.items())),
        "terminal_issue_code_counts": dict(sorted(terminal_issues.items())),
        "attempt_issue_code_counts": dict(sorted(attempt_issues.items())),
        "retried_request_count": retried,
        "recovered_on_second_attempt_count": len(recovered_ordinals),
        "failed_after_second_attempt_count": len(failed_ordinals),
        "failed_request_ordinals": tuple(failed_ordinals),
        "recovered_request_ordinals": tuple(recovered_ordinals),
        "response_latency": _latency_summary(response_latencies),
        "transient_error_latency": _latency_summary(transient_latencies),
        "retry_gap": _latency_summary(retry_gaps),
        "provider_attempt_reference_count": len(provider_refs),
        "distinct_provider_attempt_reference_count": len(set(provider_refs)),
        "failure_diagnostics_present_count": diagnostics,
        "failed_request_raw_response_count": failed_raw,
    }


def _verify_store_members(store: Path, identities: set[str]) -> None:
    if {path.name for path in store.iterdir()} != {"authorities", "requests"}:
        raise ClaimValidationV2Error("V1 diagnosis store has unexpected members")
    authorities = store / "authorities"
    requests = store / "requests"
    if any(path.is_symlink() or not path.is_dir() for path in (authorities, requests)):
        raise ClaimValidationV2Error("V1 diagnosis store directories are invalid")
    authority_names = {path.name for path in authorities.iterdir()}
    request_names = {path.name for path in requests.iterdir()}
    if (
        authority_names != {f"{identity}.json" for identity in identities}
        or request_names != identities
    ):
        raise ClaimValidationV2Error("V1 diagnosis store census differs from closeout")
    if any(path.is_symlink() or not path.is_file() for path in authorities.iterdir()):
        raise ClaimValidationV2Error("V1 diagnosis authority is not a regular file")


def _collect_attempt_records(
    records: Sequence[AttemptRecord],
    *,
    outcomes: Counter[str],
    issues: Counter[str],
    response_latencies: list[int],
    transient_latencies: list[int],
    provider_refs: list[str],
) -> None:
    for record in records:
        outcomes[record.outcome] += 1
        if record.issue is not None:
            issues[record.issue.code] += 1
        if record.provider_attempt_ref is not None:
            provider_refs.append(record.provider_attempt_ref)
        if record.outcome == "response":
            response_latencies.append(record.timing.latency_ns)
        elif record.outcome == "transient_error":
            transient_latencies.append(record.timing.latency_ns)


def build_closed_v1_failure_audit(
    *,
    recovery_closeout_path: Path,
    feasibility_closeout_path: Path,
    diagnosis_store: Path,
) -> ClosedV1FailureAudit:
    """Recompute the tracked public-safe V1 aggregate from private immutable bytes."""

    recovery = _load_model(
        recovery_closeout_path,
        RecoveryExecutionCloseout,
        label="V1 recovery closeout",
    )
    feasibility = _load_model(
        feasibility_closeout_path,
        ClaimPoolFeasibilityCloseout,
        label="V1 pool feasibility closeout",
    )
    store = diagnosis_store.resolve(strict=True)
    if diagnosis_store.is_symlink() or not store.is_dir():
        raise ClaimValidationV2Error("V1 diagnosis store must be a real directory")
    attempt_evidence = _request_attempt_evidence(store, recovery)
    if (
        feasibility.recovery_execution_closeout_sha256 != recovery.closeout_sha256
        or feasibility.diagnosis_source_commit_ref != recovery.source_commit_ref
        or feasibility.parsed_diagnosis_output_count != recovery.parsed_terminal_count
        or feasibility.technical_diagnosis_failure_count
        != recovery.technical_failure_terminal_count
    ):
        raise ClaimValidationV2Error("V1 recovery and feasibility lineage differ")
    payload: dict[str, object] = {
        "schema_version": "claim-support-validation-v1-failure-audit/v1",
        "status": "closed_v1_inadequate_for_balanced_validation_v2_required",
        "diagnosis_source_commit_ref": recovery.source_commit_ref,
        "closeout_source_commit_ref": feasibility.closeout_source_commit_ref,
        "recovery_authorization_sha256": recovery.authorization_sha256,
        "recovery_protocol_sha256": recovery.protocol_sha256,
        "recovery_execution_receipt_sha256": recovery.recovery_receipt_sha256,
        "recovery_terminal_store_sha256": recovery.recovery_terminal_store_sha256,
        "terminal_store_independently_recomputed": True,
        "recovery_closeout_sha256": recovery.closeout_sha256,
        "pool_feasibility_closeout_sha256": feasibility.closeout_sha256,
        "corpus_manifest_sha256": feasibility.corpus_manifest_sha256,
        "v1_validation_protocol_sha256": feasibility.validation_protocol_sha256,
        "terminal_request_count": recovery.terminal_request_count,
        "parsed_terminal_count": recovery.parsed_terminal_count,
        "technical_failure_terminal_count": recovery.technical_failure_terminal_count,
        "technical_attempt_count": recovery.technical_attempt_count,
        **attempt_evidence,
        "first_technical_failure_ordinal": recovery.first_technical_failure_ordinal,
        "consecutive_parsed_prefix_count": recovery.consecutive_parsed_prefix_count,
        "exact_provider_failure_cause_known": False,
        "rate_limit_cause_established": False,
        "diagnostic_limitation": ("transport_collapsed_transient_subtypes_before_persistence"),
        "mechanism_summaries": tuple(_count_summary(item) for item in recovery.mechanism_summaries),
        "condition_summaries": tuple(_count_summary(item) for item in recovery.condition_summaries),
        "variant_summaries": tuple(_count_summary(item) for item in recovery.variant_summaries),
        "execution_order_dimensions": recovery.execution_order_dimensions,
        "missingness_exchangeability_status": recovery.missingness_exchangeability_status,
        "normalized_output_count": recovery.normalized_output_count,
        "normalization_rejection_count": 0,
        "claim_candidate_count": recovery.claim_candidate_count,
        "label_availability": tuple(_label_summary(item) for item in feasibility.label_strata),
        "exact_frozen_selection_feasible": False,
        "blocking_label_strata": ("contradicted", "unsupported"),
        "v1_results_poolable_with_v2": False,
        "v1_rerun_or_relabel_forbidden": True,
        "provider_calls_executed_during_audit": False,
        "claims_materialized_during_audit": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    try:
        return ClosedV1FailureAudit.model_validate(
            {**payload, "audit_sha256": canonical_execution_sha256(payload)}
        )
    except ValidationError as exc:
        raise ClaimValidationV2Error("derived V1 failure audit is invalid") from exc


__all__ = [
    "ClaimSupportValidationV2Protocol",
    "ClaimValidationV2Error",
    "ClosedV1FailureAudit",
    "V1_AUDIT_PATH",
    "V2_PROTOCOL_PATH",
    "build_closed_v1_failure_audit",
    "load_v1_failure_audit",
    "load_v2_protocol",
    "verify_tracked_v2_protocol",
]

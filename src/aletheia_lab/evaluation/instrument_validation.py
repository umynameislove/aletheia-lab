"""Outcome-blind preparation and scoring for claim-support instrument validation.

The preparation stage creates deterministic, blinded packets from a development
claim pool. It never invents human judgments and never reads main or sealed
outcomes. Scientific use remains blocked until two independent raters and one
adjudicator complete the frozen protocol and every prespecified gate passes.
"""

from __future__ import annotations

import json
import random
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SupportLabel = Literal[
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
]
ClaimType = Literal[
    "cause_assertion",
    "evidence_statement",
    "uncertainty_statement",
    "recommended_action",
    "other",
]
LABEL_ORDER: Final[tuple[SupportLabel, ...]] = (
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
)
SUPPORTED_LABELS: Final[frozenset[SupportLabel]] = frozenset(
    {"partially_supported", "fully_supported"}
)


class InstrumentValidationError(ValueError):
    """Raised when preparation or scoring violates the frozen protocol."""


def _fail(message: str) -> NoReturn:
    raise InstrumentValidationError(message)


def _canonical_text(value: str, label: str) -> str:
    if not value or value != value.strip() or value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must be non-empty trimmed Unicode NFC")
    return value


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class InstrumentThresholds(_StrictFrozenModel):
    minimum_quadratic_weighted_kappa: float = Field(ge=0.0, le=1.0)
    minimum_automatic_macro_f1: float = Field(ge=0.0, le=1.0)
    maximum_false_supported_rate: float = Field(ge=0.0, le=1.0)
    maximum_contradicted_to_supported_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _thresholds_are_frozen(self) -> Self:
        if self.model_dump() != {
            "minimum_quadratic_weighted_kappa": 0.7,
            "minimum_automatic_macro_f1": 0.8,
            "maximum_false_supported_rate": 0.1,
            "maximum_contradicted_to_supported_rate": 0.05,
        }:
            raise ValueError("instrument thresholds must equal the frozen values")
        return self


class ClaimSupportValidationProtocol(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-protocol/v1"]
    protocol_id: Literal["claim-support-instrument-validation-v1"]
    frozen_before_main_outcomes: Literal[True]
    source_partition: Literal["development"]
    sample_minimum: Literal[150]
    sample_target: Literal[200]
    sample_maximum: Literal[250]
    automatic_label_quota: Literal[50]
    sampling_algorithm: Literal["balanced-label-round-robin-hash/v1"]
    stratification_dimensions: tuple[
        Literal["automatic_label"],
        Literal["claim_type"],
        Literal["evidence_condition"],
        Literal["variant"],
    ]
    maximum_claims_per_family_per_label: Literal[5]
    maximum_claims_per_output_per_label: Literal[2]
    independent_rater_count: Literal[2]
    adjudicator_count: Literal[1]
    adjudication_required_for_disagreement: Literal[True]
    adjudication_required_if_either_rater_marks_contradicted: Literal[True]
    ordered_labels: tuple[SupportLabel, ...]
    rater_visible_fields: tuple[
        Literal["blind_claim_id"],
        Literal["claim_text"],
        Literal["visible_evidence"],
    ]
    rater_withheld_fields: tuple[
        Literal["automatic_label"],
        Literal["claim_id"],
        Literal["output_id"],
        Literal["case_family_id"],
        Literal["claim_type"],
        Literal["evidence_condition"],
        Literal["variant"],
        Literal["mechanism"],
        Literal["hidden_ground_truth"],
    ]
    false_supported_denominator: Literal["automatic_supported_predictions"]
    contradicted_to_supported_denominator: Literal["adjudicated_contradicted_claims"]
    cluster_unit: Literal["case_family_id"]
    bootstrap_replicates: Literal[2000]
    bootstrap_seed: Literal[73021]
    main_or_sealed_outcomes_forbidden: Literal[True]
    model_as_human_rater_forbidden: Literal[True]
    synthetic_or_padded_claims_forbidden: Literal[True]
    thresholds: InstrumentThresholds
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def _protocol_is_coherent(self) -> Self:
        if self.ordered_labels != LABEL_ORDER:
            raise ValueError("support labels must preserve the frozen ordinal order")
        if self.sample_target != self.automatic_label_quota * len(LABEL_ORDER):
            raise ValueError("label quotas must sum to the target sample size")
        payload = self.model_dump(exclude={"protocol_sha256"})
        if self.protocol_sha256 != canonical_sha256(payload):
            raise ValueError("protocol_sha256 is not derived from the canonical protocol")
        return self


class VisibleEvidenceExcerpt(_StrictFrozenModel):
    evidence_id: str = Field(pattern=r"^evidence-[0-9a-f]{16,64}$")
    artifact_ref: str
    excerpt: str
    excerpt_sha256: Sha256

    @field_validator("artifact_ref", "excerpt")
    @classmethod
    def _text_is_canonical(cls, value: str, info: object) -> str:
        return _canonical_text(value, str(getattr(info, "field_name", "evidence text")))

    @field_validator("artifact_ref")
    @classmethod
    def _artifact_ref_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact_ref must be repository relative")
        return value

    @model_validator(mode="after")
    def _excerpt_identity_is_derived(self) -> Self:
        payload = self.model_dump(exclude={"excerpt_sha256"})
        if self.excerpt_sha256 != canonical_sha256(payload):
            raise ValueError("excerpt_sha256 is not derived from visible evidence")
        return self


class ClaimPoolEntry(_StrictFrozenModel):
    schema_version: Literal["claim-support-pool-entry/v1"] = "claim-support-pool-entry/v1"
    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{16,64}$")
    output_id: str = Field(pattern=r"^output-[0-9a-f]{16,64}$")
    case_family_id: str = Field(pattern=r"^family-[0-9a-f]{16,64}$")
    claim_text: str
    claim_type: ClaimType
    evidence_condition: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    variant: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,31}$")
    automatic_label: SupportLabel
    visible_evidence: tuple[VisibleEvidenceExcerpt, ...]
    source_partition: Literal["development"]
    source_record_sha256: Sha256
    entry_sha256: Sha256

    @field_validator("claim_text")
    @classmethod
    def _claim_text_is_canonical(cls, value: str) -> str:
        return _canonical_text(value, "claim_text")

    @field_validator("visible_evidence")
    @classmethod
    def _visible_evidence_is_nonempty_unique(
        cls, values: tuple[VisibleEvidenceExcerpt, ...]
    ) -> tuple[VisibleEvidenceExcerpt, ...]:
        evidence_ids = [value.evidence_id for value in values]
        if not values or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("visible evidence must be non-empty with unique IDs")
        return values

    @model_validator(mode="after")
    def _entry_identity_is_derived(self) -> Self:
        payload = self.model_dump(exclude={"entry_sha256"})
        if self.entry_sha256 != canonical_sha256(payload):
            raise ValueError("entry_sha256 is not derived from the claim-pool entry")
        return self


class BlindEvidenceExcerpt(_StrictFrozenModel):
    evidence_id: str
    artifact_ref: str
    excerpt: str
    excerpt_sha256: Sha256


class BlindClaim(_StrictFrozenModel):
    schema_version: Literal["blind-claim/v1"] = "blind-claim/v1"
    blind_claim_id: str = Field(pattern=r"^blind-claim-[0-9a-f]{64}$")
    claim_text: str
    visible_evidence: tuple[BlindEvidenceExcerpt, ...]


class BlankAnnotationDecision(_StrictFrozenModel):
    blind_claim_id: str = Field(pattern=r"^blind-claim-[0-9a-f]{64}$")
    support_label: None = None
    evidence_ids_used: tuple[str, ...] = ()
    rationale: None = None

    @field_validator("evidence_ids_used")
    @classmethod
    def _template_evidence_must_be_blank(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values:
            raise ValueError("tracked annotation templates must not contain judgments")
        return values


class BlindAnnotationPacket(_StrictFrozenModel):
    schema_version: Literal["claim-support-blind-annotation-packet/v1"] = (
        "claim-support-blind-annotation-packet/v1"
    )
    protocol_sha256: Sha256
    rater_slot: Literal["rater_1", "rater_2"]
    packet_id: str = Field(pattern=r"^annotation-packet-[0-9a-f]{64}$")
    claims: tuple[BlindClaim, ...]
    decisions: tuple[BlankAnnotationDecision, ...]
    packet_sha256: Sha256

    @model_validator(mode="after")
    def _packet_is_blank_complete_and_bound(self) -> Self:
        claim_ids = tuple(claim.blind_claim_id for claim in self.claims)
        decision_ids = tuple(decision.blind_claim_id for decision in self.decisions)
        if not claim_ids or len(claim_ids) != len(set(claim_ids)):
            raise ValueError("blind claims must be non-empty and unique")
        if claim_ids != decision_ids:
            raise ValueError("blank decisions must align exactly with blind claims")
        payload = self.model_dump(exclude={"packet_sha256"})
        if self.packet_sha256 != canonical_sha256(payload):
            raise ValueError("packet_sha256 is not derived from the annotation packet")
        return self


class EvaluatorMappingEntry(_StrictFrozenModel):
    blind_claim_id: str
    claim_id: str
    output_id: str
    case_family_id: str
    claim_type: ClaimType
    evidence_condition: str
    variant: str
    automatic_label: SupportLabel
    source_record_sha256: Sha256
    pool_entry_sha256: Sha256


class EvaluatorMappingPacket(_StrictFrozenModel):
    schema_version: Literal["claim-support-evaluator-mapping/v1"] = (
        "claim-support-evaluator-mapping/v1"
    )
    protocol_sha256: Sha256
    entries: tuple[EvaluatorMappingEntry, ...]
    mapping_sha256: Sha256

    @model_validator(mode="after")
    def _mapping_is_unique_and_bound(self) -> Self:
        blind_ids = [entry.blind_claim_id for entry in self.entries]
        claim_ids = [entry.claim_id for entry in self.entries]
        if not self.entries or len(blind_ids) != len(set(blind_ids)):
            raise ValueError("mapping blind IDs must be non-empty and unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("mapping claim IDs must be unique")
        payload = self.model_dump(exclude={"mapping_sha256"})
        if self.mapping_sha256 != canonical_sha256(payload):
            raise ValueError("mapping_sha256 is not derived from the evaluator mapping")
        return self


class PreparedStudyReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-prepared-study-receipt/v1"] = (
        "claim-support-prepared-study-receipt/v1"
    )
    protocol_sha256: Sha256
    sample_count: int = Field(ge=150, le=250)
    label_census: Mapping[SupportLabel, int]
    rater_1_packet_sha256: Sha256
    rater_2_packet_sha256: Sha256
    evaluator_mapping_sha256: Sha256
    human_annotations_collected: Literal[False] = False
    validation_metrics_generated: Literal[False] = False
    main_outcomes_opened: Literal[False] = False
    status: Literal["prepared_for_independent_human_annotation"] = (
        "prepared_for_independent_human_annotation"
    )
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_bound(self) -> Self:
        if sum(self.label_census.values()) != self.sample_count:
            raise ValueError("label census must equal the prepared sample count")
        payload = self.model_dump(exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("receipt_sha256 is not derived from the prepared study")
        return self


class OutcomeBlindPreparationReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-preparation-receipt/v1"]
    protocol_sha256: Sha256
    preparation_scope: Literal["protocol_and_packet_generator_only"]
    development_claim_pool_materialized: Literal[False]
    validation_sample_materialized: Literal[False]
    human_annotations_collected: Literal[False]
    validation_metrics_generated: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    independent_human_validation_required: Literal[True]
    status: Literal["outcome_blind_preparation_complete_human_validation_pending"]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_bound(self) -> Self:
        payload = self.model_dump(exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("receipt_sha256 is not derived from the preparation receipt")
        return self


class FinalClaimJudgment(_StrictFrozenModel):
    blind_claim_id: str
    claim_id: str
    case_family_id: str
    automatic_label: SupportLabel
    rater_1_label: SupportLabel
    rater_2_label: SupportLabel
    adjudicated_label: SupportLabel


class MetricInterval(_StrictFrozenModel):
    estimate: float = Field(ge=-1.0, le=1.0)
    lower: float = Field(ge=-1.0, le=1.0)
    upper: float = Field(ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def _bounds_contain_estimate(self) -> Self:
        if not self.lower <= self.estimate <= self.upper:
            raise ValueError("metric interval must contain its estimate")
        return self


class InstrumentValidationReport(_StrictFrozenModel):
    schema_version: Literal["claim-support-instrument-validation/v1"] = (
        "claim-support-instrument-validation/v1"
    )
    protocol_sha256: Sha256
    sample_count: int
    family_count: int
    adjudicated_label_census: Mapping[SupportLabel, int]
    automatic_vs_adjudicated_confusion: Mapping[SupportLabel, Mapping[SupportLabel, int]]
    quadratic_weighted_kappa: MetricInterval
    automatic_macro_f1: MetricInterval
    false_supported_rate: MetricInterval
    contradicted_to_supported_rate: MetricInterval
    kappa_gate_passed: bool
    macro_f1_gate_passed: bool
    false_supported_gate_passed: bool
    contradicted_to_supported_gate_passed: bool
    status: Literal["pass", "blocked"]
    report_sha256: Sha256

    @model_validator(mode="after")
    def _report_is_fail_closed_and_bound(self) -> Self:
        all_passed = all(
            (
                self.kappa_gate_passed,
                self.macro_f1_gate_passed,
                self.false_supported_gate_passed,
                self.contradicted_to_supported_gate_passed,
            )
        )
        if (self.status == "pass") != all_passed:
            raise ValueError("instrument status must equal the conjunction of all gates")
        payload = self.model_dump(exclude={"report_sha256"})
        if self.report_sha256 != canonical_sha256(payload):
            raise ValueError("report_sha256 is not derived from validation results")
        return self


def load_validation_protocol(path: Path) -> ClaimSupportValidationProtocol:
    try:
        return ClaimSupportValidationProtocol.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise InstrumentValidationError("claim-support validation protocol is invalid") from exc


def load_preparation_receipt(path: Path) -> OutcomeBlindPreparationReceipt:
    try:
        return OutcomeBlindPreparationReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise InstrumentValidationError("claim-support preparation receipt is invalid") from exc


def load_claim_pool(path: Path) -> tuple[ClaimPoolEntry, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            _fail("development claim pool must be a JSON list")
        entries = tuple(ClaimPoolEntry.model_validate_json(json.dumps(item)) for item in raw)
    except (OSError, ValidationError, ValueError, TypeError) as exc:
        if isinstance(exc, InstrumentValidationError):
            raise
        raise InstrumentValidationError("development claim pool is invalid") from exc
    entry_hashes = [entry.entry_sha256 for entry in entries]
    claim_ids = [entry.claim_id for entry in entries]
    if len(entry_hashes) != len(set(entry_hashes)) or len(claim_ids) != len(set(claim_ids)):
        _fail("development claim pool contains duplicate claims")
    return entries


def _rank(protocol_sha256: str, label: str, value: object) -> str:
    return canonical_sha256({"protocol_sha256": protocol_sha256, "label": label, "value": value})


def _select_label_stratum(
    entries: Sequence[ClaimPoolEntry],
    *,
    label: SupportLabel,
    protocol: ClaimSupportValidationProtocol,
) -> tuple[ClaimPoolEntry, ...]:
    strata: dict[tuple[str, str, str], list[ClaimPoolEntry]] = defaultdict(list)
    for entry in entries:
        if entry.automatic_label == label:
            strata[(entry.claim_type, entry.evidence_condition, entry.variant)].append(entry)
    for key, values in strata.items():
        values.sort(key=lambda item: _rank(protocol.protocol_sha256, "entry", item.entry_sha256))
        strata[key] = values
    ordered_keys = sorted(
        strata,
        key=lambda key: _rank(protocol.protocol_sha256, "stratum", key),
    )
    selected: list[ClaimPoolEntry] = []
    family_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    while len(selected) < protocol.automatic_label_quota:
        progress = False
        for key in ordered_keys:
            candidates = strata[key]
            while candidates:
                candidate = candidates.pop(0)
                if (
                    family_counts[candidate.case_family_id]
                    >= protocol.maximum_claims_per_family_per_label
                    or output_counts[candidate.output_id]
                    >= protocol.maximum_claims_per_output_per_label
                ):
                    continue
                selected.append(candidate)
                family_counts[candidate.case_family_id] += 1
                output_counts[candidate.output_id] += 1
                progress = True
                break
            if len(selected) == protocol.automatic_label_quota:
                break
        if not progress:
            _fail(f"insufficient eligible development claims for automatic label {label}")
    return tuple(selected)


def select_validation_sample(
    entries: Sequence[ClaimPoolEntry],
    protocol: ClaimSupportValidationProtocol,
) -> tuple[ClaimPoolEntry, ...]:
    """Select the exact frozen sample without padding or reading outcomes."""

    if len(entries) < protocol.sample_target:
        _fail("development claim pool is smaller than the frozen target")
    if any(entry.source_partition != protocol.source_partition for entry in entries):
        _fail("only development-partition claims may enter instrument validation")
    claim_ids = [entry.claim_id for entry in entries]
    entry_hashes = [entry.entry_sha256 for entry in entries]
    if len(claim_ids) != len(set(claim_ids)) or len(entry_hashes) != len(set(entry_hashes)):
        _fail("claim pool must not contain duplicate identities")
    selected = tuple(
        entry
        for label in LABEL_ORDER
        for entry in _select_label_stratum(entries, label=label, protocol=protocol)
    )
    return tuple(
        sorted(
            selected,
            key=lambda item: _rank(protocol.protocol_sha256, "blind-order", item.entry_sha256),
        )
    )


def _blind_claim(entry: ClaimPoolEntry, protocol: ClaimSupportValidationProtocol) -> BlindClaim:
    blind_id = f"blind-claim-{_rank(protocol.protocol_sha256, 'blind-id', entry.entry_sha256)}"
    return BlindClaim(
        blind_claim_id=blind_id,
        claim_text=entry.claim_text,
        visible_evidence=tuple(
            BlindEvidenceExcerpt(**evidence.model_dump()) for evidence in entry.visible_evidence
        ),
    )


def _annotation_packet(
    claims: tuple[BlindClaim, ...],
    *,
    rater_slot: Literal["rater_1", "rater_2"],
    protocol: ClaimSupportValidationProtocol,
) -> BlindAnnotationPacket:
    packet_id = f"annotation-packet-{_rank(protocol.protocol_sha256, rater_slot, [claim.blind_claim_id for claim in claims])}"
    decisions = tuple(
        BlankAnnotationDecision(blind_claim_id=claim.blind_claim_id) for claim in claims
    )
    payload: dict[str, object] = {
        "schema_version": "claim-support-blind-annotation-packet/v1",
        "protocol_sha256": protocol.protocol_sha256,
        "rater_slot": rater_slot,
        "packet_id": packet_id,
        "claims": tuple(claim.model_dump() for claim in claims),
        "decisions": tuple(decision.model_dump() for decision in decisions),
    }
    return BlindAnnotationPacket(
        protocol_sha256=protocol.protocol_sha256,
        rater_slot=rater_slot,
        packet_id=packet_id,
        claims=claims,
        decisions=decisions,
        packet_sha256=canonical_sha256(payload),
    )


def prepare_validation_packets(
    entries: Sequence[ClaimPoolEntry],
    protocol: ClaimSupportValidationProtocol,
) -> tuple[BlindAnnotationPacket, BlindAnnotationPacket, EvaluatorMappingPacket, PreparedStudyReceipt]:
    selected = select_validation_sample(entries, protocol)
    claims = tuple(_blind_claim(entry, protocol) for entry in selected)
    rater_1 = _annotation_packet(claims, rater_slot="rater_1", protocol=protocol)
    rater_2 = _annotation_packet(claims, rater_slot="rater_2", protocol=protocol)
    mappings = tuple(
        EvaluatorMappingEntry(
            blind_claim_id=claim.blind_claim_id,
            claim_id=entry.claim_id,
            output_id=entry.output_id,
            case_family_id=entry.case_family_id,
            claim_type=entry.claim_type,
            evidence_condition=entry.evidence_condition,
            variant=entry.variant,
            automatic_label=entry.automatic_label,
            source_record_sha256=entry.source_record_sha256,
            pool_entry_sha256=entry.entry_sha256,
        )
        for entry, claim in zip(selected, claims, strict=True)
    )
    mapping_payload: dict[str, object] = {
        "schema_version": "claim-support-evaluator-mapping/v1",
        "protocol_sha256": protocol.protocol_sha256,
        "entries": tuple(mapping.model_dump() for mapping in mappings),
    }
    mapping = EvaluatorMappingPacket(
        protocol_sha256=protocol.protocol_sha256,
        entries=mappings,
        mapping_sha256=canonical_sha256(mapping_payload),
    )
    census: dict[SupportLabel, int] = {
        label: sum(entry.automatic_label == label for entry in selected) for label in LABEL_ORDER
    }
    receipt_payload: dict[str, object] = {
        "schema_version": "claim-support-prepared-study-receipt/v1",
        "protocol_sha256": protocol.protocol_sha256,
        "sample_count": len(selected),
        "label_census": census,
        "rater_1_packet_sha256": rater_1.packet_sha256,
        "rater_2_packet_sha256": rater_2.packet_sha256,
        "evaluator_mapping_sha256": mapping.mapping_sha256,
        "human_annotations_collected": False,
        "validation_metrics_generated": False,
        "main_outcomes_opened": False,
        "status": "prepared_for_independent_human_annotation",
    }
    receipt = PreparedStudyReceipt(
        protocol_sha256=protocol.protocol_sha256,
        sample_count=len(selected),
        label_census=census,
        rater_1_packet_sha256=rater_1.packet_sha256,
        rater_2_packet_sha256=rater_2.packet_sha256,
        evaluator_mapping_sha256=mapping.mapping_sha256,
        receipt_sha256=canonical_sha256(receipt_payload),
    )
    return rater_1, rater_2, mapping, receipt


def _quadratic_weighted_kappa(first: Sequence[SupportLabel], second: Sequence[SupportLabel]) -> float:
    if len(first) != len(second) or not first:
        _fail("weighted kappa requires equal non-empty rating sequences")
    size = len(LABEL_ORDER)
    indices = {label: index for index, label in enumerate(LABEL_ORDER)}
    observed = [[0.0] * size for _ in range(size)]
    for left, right in zip(first, second, strict=True):
        observed[indices[left]][indices[right]] += 1.0
    first_counts = [sum(row) for row in observed]
    second_counts = [sum(observed[row][column] for row in range(size)) for column in range(size)]
    weighted_observed = 0.0
    weighted_expected = 0.0
    denominator = float((size - 1) ** 2)
    total = float(len(first))
    for row in range(size):
        for column in range(size):
            weight = ((row - column) ** 2) / denominator
            weighted_observed += weight * observed[row][column] / total
            weighted_expected += weight * (first_counts[row] * second_counts[column]) / (total**2)
    if weighted_expected == 0.0:
        _fail("weighted kappa is undefined for the observed rating marginals")
    return max(-1.0, min(1.0, 1.0 - weighted_observed / weighted_expected))


def _macro_f1(predicted: Sequence[SupportLabel], actual: Sequence[SupportLabel]) -> float:
    scores: list[float] = []
    for label in LABEL_ORDER:
        true_positive = sum(p == label and a == label for p, a in zip(predicted, actual, strict=True))
        false_positive = sum(p == label and a != label for p, a in zip(predicted, actual, strict=True))
        false_negative = sum(p != label and a == label for p, a in zip(predicted, actual, strict=True))
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(0.0 if denominator == 0 else (2 * true_positive) / denominator)
    return sum(scores) / len(scores)


def _metric_values(records: Sequence[FinalClaimJudgment]) -> tuple[float, float, float, float]:
    rater_1 = [record.rater_1_label for record in records]
    rater_2 = [record.rater_2_label for record in records]
    automatic = [record.automatic_label for record in records]
    adjudicated = [record.adjudicated_label for record in records]
    kappa = _quadratic_weighted_kappa(rater_1, rater_2)
    macro_f1 = _macro_f1(automatic, adjudicated)
    automatic_supported = [record for record in records if record.automatic_label in SUPPORTED_LABELS]
    if not automatic_supported:
        _fail("false-supported rate has an empty registered denominator")
    false_supported = sum(
        record.adjudicated_label not in SUPPORTED_LABELS for record in automatic_supported
    ) / len(automatic_supported)
    contradicted = [record for record in records if record.adjudicated_label == "contradicted"]
    if not contradicted:
        _fail("contradicted-to-supported rate has an empty registered denominator")
    contradicted_to_supported = sum(
        record.automatic_label in SUPPORTED_LABELS for record in contradicted
    ) / len(contradicted)
    return kappa, macro_f1, false_supported, contradicted_to_supported


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _interval(estimate: float, bootstrap_values: Sequence[float]) -> MetricInterval:
    return MetricInterval(
        estimate=estimate,
        lower=min(estimate, _quantile(bootstrap_values, 0.025)),
        upper=max(estimate, _quantile(bootstrap_values, 0.975)),
    )


def compile_validation_report(
    records: Sequence[FinalClaimJudgment],
    protocol: ClaimSupportValidationProtocol,
) -> InstrumentValidationReport:
    """Compile prespecified metrics after real human adjudication is complete."""

    if len(records) != protocol.sample_target:
        _fail("the completed human sample must equal the frozen target")
    blind_ids = [record.blind_claim_id for record in records]
    claim_ids = [record.claim_id for record in records]
    if len(blind_ids) != len(set(blind_ids)) or len(claim_ids) != len(set(claim_ids)):
        _fail("completed human judgments contain duplicate claim identities")
    automatic_census = Counter(record.automatic_label for record in records)
    if any(automatic_census[label] != protocol.automatic_label_quota for label in LABEL_ORDER):
        _fail("completed sample does not preserve registered automatic-label quotas")
    estimates = _metric_values(records)
    by_family: dict[str, list[FinalClaimJudgment]] = defaultdict(list)
    for record in records:
        by_family[record.case_family_id].append(record)
    family_ids = sorted(by_family)
    if len(family_ids) < 2:
        _fail("family-clustered inference requires at least two families")
    generator = random.Random(protocol.bootstrap_seed)
    bootstrap: list[tuple[float, float, float, float]] = []
    attempts = 0
    maximum_attempts = protocol.bootstrap_replicates * 20
    while len(bootstrap) < protocol.bootstrap_replicates and attempts < maximum_attempts:
        attempts += 1
        sampled_ids = [generator.choice(family_ids) for _ in family_ids]
        sampled = [record for family_id in sampled_ids for record in by_family[family_id]]
        try:
            bootstrap.append(_metric_values(sampled))
        except InstrumentValidationError:
            continue
    if len(bootstrap) != protocol.bootstrap_replicates:
        _fail("insufficient valid family-clustered bootstrap replicates")
    intervals = tuple(
        _interval(estimate, [replicate[index] for replicate in bootstrap])
        for index, estimate in enumerate(estimates)
    )
    adjudicated_census: dict[SupportLabel, int] = {
        label: sum(record.adjudicated_label == label for record in records) for label in LABEL_ORDER
    }
    confusion: dict[SupportLabel, dict[SupportLabel, int]] = {
        actual: {
            predicted: sum(
                record.adjudicated_label == actual and record.automatic_label == predicted
                for record in records
            )
            for predicted in LABEL_ORDER
        }
        for actual in LABEL_ORDER
    }
    kappa_passed = estimates[0] >= protocol.thresholds.minimum_quadratic_weighted_kappa
    macro_f1_passed = estimates[1] >= protocol.thresholds.minimum_automatic_macro_f1
    false_supported_passed = estimates[2] <= protocol.thresholds.maximum_false_supported_rate
    contradicted_passed = (
        estimates[3] <= protocol.thresholds.maximum_contradicted_to_supported_rate
    )
    report_payload: dict[str, object] = {
        "schema_version": "claim-support-instrument-validation/v1",
        "protocol_sha256": protocol.protocol_sha256,
        "sample_count": len(records),
        "family_count": len(family_ids),
        "adjudicated_label_census": adjudicated_census,
        "automatic_vs_adjudicated_confusion": confusion,
        "quadratic_weighted_kappa": intervals[0].model_dump(),
        "automatic_macro_f1": intervals[1].model_dump(),
        "false_supported_rate": intervals[2].model_dump(),
        "contradicted_to_supported_rate": intervals[3].model_dump(),
        "kappa_gate_passed": kappa_passed,
        "macro_f1_gate_passed": macro_f1_passed,
        "false_supported_gate_passed": false_supported_passed,
        "contradicted_to_supported_gate_passed": contradicted_passed,
        "status": (
            "pass"
            if all((kappa_passed, macro_f1_passed, false_supported_passed, contradicted_passed))
            else "blocked"
        ),
    }
    return InstrumentValidationReport(
        protocol_sha256=protocol.protocol_sha256,
        sample_count=len(records),
        family_count=len(family_ids),
        adjudicated_label_census=adjudicated_census,
        automatic_vs_adjudicated_confusion=confusion,
        quadratic_weighted_kappa=intervals[0],
        automatic_macro_f1=intervals[1],
        false_supported_rate=intervals[2],
        contradicted_to_supported_rate=intervals[3],
        kappa_gate_passed=kappa_passed,
        macro_f1_gate_passed=macro_f1_passed,
        false_supported_gate_passed=false_supported_passed,
        contradicted_to_supported_gate_passed=contradicted_passed,
        status=(
            "pass"
            if all((kappa_passed, macro_f1_passed, false_supported_passed, contradicted_passed))
            else "blocked"
        ),
        report_sha256=canonical_sha256(report_payload),
    )

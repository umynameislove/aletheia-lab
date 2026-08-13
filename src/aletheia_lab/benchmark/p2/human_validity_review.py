"""Blind human-review infrastructure for evidence sufficiency and validity.

This module prepares review material; it never manufactures human judgments.
The blind packet contains only diagnosis-safe projections and opaque identifiers.
An evaluator mapping, opened after the leakage pass, binds each entry to its
mechanism, evidence condition, and frozen claim-sufficiency rubric.

Sampling is deterministic and family-clustered: one complete three-condition
family is selected per mechanism.  This prevents condition siblings from being
mistaken for independent units and guarantees mechanism/condition coverage.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from typing import Annotated, Final, Literal, NoReturn, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import EvidenceConditionName, FamilyId
from aletheia_lab.benchmark.p2.evidence_conditions import EvidenceConditionBundle
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN, FaultTypeName
from aletheia_lab.benchmark.p2.validation import ContractViolation

HUMAN_REVIEW_PROTOCOL_VERSION: Final[Literal["p2-human-validity-review/v1"]] = (
    "p2-human-validity-review/v1"
)
BLIND_REVIEW_PACKET_SCHEMA_VERSION: Final[Literal["p2-human-review-blind-packet/v1"]] = (
    "p2-human-review-blind-packet/v1"
)
REVIEW_MAPPING_PACKET_SCHEMA_VERSION: Final[
    Literal["p2-human-review-mapping-packet/v1"]
] = "p2-human-review-mapping-packet/v1"
HUMAN_REVIEW_RECORD_SCHEMA_VERSION: Final[Literal["p2-human-review-record/v1"]] = (
    "p2-human-review-record/v1"
)
HUMAN_REVIEW_WORKSHEET_SCHEMA_VERSION: Final[Literal["p2-human-review-worksheet/v1"]] = (
    "p2-human-review-worksheet/v1"
)
HUMAN_VALIDITY_REPORT_SCHEMA_VERSION: Final[Literal["p2-human-validity-report/v1"]] = (
    "p2-human-validity-report/v1"
)

REVIEW_ID_PATTERN: Final[str] = r"^p2-review-[0-9a-f]{64}$"
FAMILY_REVIEW_ID_PATTERN: Final[str] = r"^p2-family-review-[0-9a-f]{64}$"
ReviewId = Annotated[str, Field(pattern=REVIEW_ID_PATTERN)]
FamilyReviewId = Annotated[str, Field(pattern=FAMILY_REVIEW_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

ReviewAnswer = Literal["yes", "no", "uncertain"]
ObservedSufficiency = Literal[
    "bounded_hypothesis_supported",
    "bounded_hypothesis_tentative_only",
    "cannot_assess",
]
MaximumSupportedClaim = Literal[
    "observation",
    "comparison",
    "bounded_tentative_hypothesis",
    "bounded_causal_hypothesis",
    "cannot_assess",
]
ValidityThreat = Literal[
    "accuracy_threshold_construct",
    "aggregate_evidence_ambiguity",
    "condition_asymmetry",
    "missing_key_overwithholding",
    "noise_salience",
    "projection_information_loss",
    "other",
]
FindingKind = Literal[
    "leakage",
    "sufficiency_mismatch",
    "claim_boundary",
    "family_pairing",
    "uncertain_judgment",
]
GateStatus = Literal["pass", "blocked"]

_CONDITIONS: Final[tuple[EvidenceConditionName, ...]] = ("full", "missing_key", "noisy")
_MECHANISMS: Final[tuple[FaultTypeName, ...]] = (
    "data_drift",
    "label_noise",
    "preprocessing_bug",
)
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class HumanValidityReviewError(ContractViolation):
    """Raised when review packets or judgments violate the frozen protocol."""


def _fail(message: str) -> NoReturn:
    raise HumanValidityReviewError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_text(value: str, label: str) -> str:
    if value != value.strip() or value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must be trimmed Unicode NFC")
    if not value:
        raise ValueError(f"{label} must not be blank")
    return value


class HumanEvidenceRubric(_StrictFrozenModel):
    """Evaluator-side claim policy for one evidence condition."""

    schema_version: Literal["p2-human-evidence-rubric/v1"] = "p2-human-evidence-rubric/v1"
    evidence_condition: EvidenceConditionName
    expected_sufficiency: Literal[
        "bounded_hypothesis_supported", "bounded_hypothesis_tentative_only"
    ]
    maximum_supported_claim: Literal[
        "bounded_tentative_hypothesis", "bounded_causal_hypothesis"
    ]
    allowed_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    required_guardrails: tuple[str, ...]

    @field_validator("allowed_claims", "forbidden_claims", "required_guardrails")
    @classmethod
    def _values_are_unique_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _canonical_text(value, "rubric value")
        if not values or len(values) != len(set(values)):
            raise ValueError("rubric values must be non-empty and unique")
        return values

    @model_validator(mode="after")
    def _claim_sets_are_disjoint(self) -> HumanEvidenceRubric:
        if set(self.allowed_claims) & set(self.forbidden_claims):
            raise ValueError("allowed and forbidden claims must be disjoint")
        if "causal_conclusion" not in self.forbidden_claims:
            raise ValueError("every condition must forbid causal conclusions")
        if "strong_causal_conclusion" not in self.forbidden_claims:
            raise ValueError("every condition must forbid strong causal conclusions")
        if self.maximum_supported_claim not in self.allowed_claims:
            raise ValueError("maximum supported claim must be explicitly allowed")
        if self.evidence_condition == "missing_key":
            if self.expected_sufficiency != "bounded_hypothesis_tentative_only":
                raise ValueError("missing_key must be tentative-only")
            if self.maximum_supported_claim != "bounded_tentative_hypothesis":
                raise ValueError("missing_key must cap claims at a tentative hypothesis")
            if "explicit_uncertainty" not in self.required_guardrails:
                raise ValueError("missing_key must require explicit uncertainty")
        elif (
            self.expected_sufficiency != "bounded_hypothesis_supported"
            or self.maximum_supported_claim != "bounded_causal_hypothesis"
        ):
            raise ValueError("full and noisy must support only a bounded hypothesis")
        if self.evidence_condition == "noisy" and "reject_unsupported_secondary_cause" not in (
            self.required_guardrails
        ):
            raise ValueError("noisy must require rejection of unsupported secondary causes")
        return self


_COMMON_ALLOWED: Final[tuple[str, ...]] = (
    "observation",
    "comparison",
    "uncertainty",
    "missing_evidence_request",
    "next_check",
)
_COMMON_FORBIDDEN: Final[tuple[str, ...]] = (
    "causal_conclusion",
    "strong_causal_conclusion",
    "unsupported_remediation",
)
_RUBRICS: Final[dict[EvidenceConditionName, HumanEvidenceRubric]] = {
    "full": HumanEvidenceRubric(
        evidence_condition="full",
        expected_sufficiency="bounded_hypothesis_supported",
        maximum_supported_claim="bounded_causal_hypothesis",
        allowed_claims=(*_COMMON_ALLOWED, "bounded_causal_hypothesis"),
        forbidden_claims=_COMMON_FORBIDDEN,
        required_guardrails=("cite_visible_evidence", "qualify_causal_language"),
    ),
    "missing_key": HumanEvidenceRubric(
        evidence_condition="missing_key",
        expected_sufficiency="bounded_hypothesis_tentative_only",
        maximum_supported_claim="bounded_tentative_hypothesis",
        allowed_claims=(*_COMMON_ALLOWED, "bounded_tentative_hypothesis"),
        forbidden_claims=_COMMON_FORBIDDEN,
        required_guardrails=(
            "explicit_uncertainty",
            "request_missing_decisive_evidence",
            "abstain_from_causal_conclusion",
        ),
    ),
    "noisy": HumanEvidenceRubric(
        evidence_condition="noisy",
        expected_sufficiency="bounded_hypothesis_supported",
        maximum_supported_claim="bounded_causal_hypothesis",
        allowed_claims=(*_COMMON_ALLOWED, "bounded_causal_hypothesis"),
        forbidden_claims=_COMMON_FORBIDDEN,
        required_guardrails=(
            "cite_visible_evidence",
            "qualify_causal_language",
            "reject_unsupported_secondary_cause",
        ),
    ),
}


def human_evidence_rubric_for(condition: EvidenceConditionName) -> HumanEvidenceRubric:
    """Return the frozen evaluator-side rubric for ``condition``."""

    return _RUBRICS[condition]


def _review_id(bundle: EvidenceConditionBundle) -> str:
    digest = canonical_sha256(
        {
            "protocol_version": HUMAN_REVIEW_PROTOCOL_VERSION,
            "bundle_sha256": bundle.bundle_sha256,
        }
    )
    return f"p2-review-{digest}"


def _family_review_id(case_family_id: str) -> str:
    digest = canonical_sha256(
        {
            "protocol_version": HUMAN_REVIEW_PROTOCOL_VERSION,
            "case_family_id": case_family_id,
        }
    )
    return f"p2-family-review-{digest}"


class BlindReviewEntry(_StrictFrozenModel):
    """One diagnosis-safe entry with all evaluator metadata withheld."""

    review_id: ReviewId
    diagnosis_projection: dict[str, object]
    diagnosis_projection_sha256: Sha256

    @model_validator(mode="after")
    def _projection_hash_matches(self) -> BlindReviewEntry:
        if canonical_sha256(self.diagnosis_projection) != self.diagnosis_projection_sha256:
            raise ValueError("blind diagnosis projection hash mismatch")
        return self


class BlindReviewPacket(_StrictFrozenModel):
    """Packet used for leakage review before opening evaluator metadata."""

    schema_version: Literal["p2-human-review-blind-packet/v1"] = (
        BLIND_REVIEW_PACKET_SCHEMA_VERSION
    )
    protocol_version: Literal["p2-human-validity-review/v1"] = HUMAN_REVIEW_PROTOCOL_VERSION
    sampling_strategy: Literal["one-complete-family-per-mechanism/v1"] = (
        "one-complete-family-per-mechanism/v1"
    )
    instructions: tuple[str, ...]
    entries: tuple[BlindReviewEntry, ...]

    @field_validator("instructions")
    @classmethod
    def _instructions_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("blind review instructions must not be empty")
        for value in values:
            _canonical_text(value, "blind instruction")
        return values

    @field_validator("entries")
    @classmethod
    def _entries_are_unique_ordered(
        cls, values: tuple[BlindReviewEntry, ...]
    ) -> tuple[BlindReviewEntry, ...]:
        keys = tuple(entry.review_id for entry in values)
        if len(values) != 9:
            raise ValueError("blind packet must contain exactly nine entries")
        if len(set(keys)) != len(keys):
            raise ValueError("blind packet review IDs must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("blind packet entries must use canonical review-ID order")
        return values

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ReviewMappingEntry(_StrictFrozenModel):
    """Evaluator-only condition and rubric mapping for one blind entry."""

    review_id: ReviewId
    family_review_id: FamilyReviewId
    evidence_bundle_id: str
    bundle_sha256: Sha256
    diagnosis_projection_sha256: Sha256
    case_family_id: FamilyId
    fault_type: FaultTypeName
    evidence_condition: EvidenceConditionName
    rubric: HumanEvidenceRubric
    binding_sha256: Sha256

    @model_validator(mode="after")
    def _mapping_is_derived(self) -> ReviewMappingEntry:
        if self.family_review_id != _family_review_id(self.case_family_id):
            raise ValueError("family review ID does not bind the mapped family")
        if self.rubric != human_evidence_rubric_for(self.evidence_condition):
            raise ValueError("mapping rubric differs from the frozen condition rubric")
        payload = self.model_dump(mode="json", exclude={"binding_sha256"})
        if self.binding_sha256 != canonical_sha256(payload):
            raise ValueError("review mapping binding mismatch")
        return self


class ReviewMappingPacket(_StrictFrozenModel):
    """Evaluator mapping opened only after blind leakage decisions are recorded."""

    schema_version: Literal["p2-human-review-mapping-packet/v1"] = (
        REVIEW_MAPPING_PACKET_SCHEMA_VERSION
    )
    protocol_version: Literal["p2-human-validity-review/v1"] = HUMAN_REVIEW_PROTOCOL_VERSION
    blind_packet_sha256: Sha256
    instructions: tuple[str, ...]
    entries: tuple[ReviewMappingEntry, ...]

    @field_validator("instructions")
    @classmethod
    def _instructions_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("mapping instructions must not be empty")
        for value in values:
            _canonical_text(value, "mapping instruction")
        return values

    @field_validator("entries")
    @classmethod
    def _entries_are_unique_ordered(
        cls, values: tuple[ReviewMappingEntry, ...]
    ) -> tuple[ReviewMappingEntry, ...]:
        keys = tuple(entry.review_id for entry in values)
        bundle_ids = tuple(entry.evidence_bundle_id for entry in values)
        if len(values) != 9 or len(set(keys)) != 9 or len(set(bundle_ids)) != 9:
            raise ValueError("mapping must contain nine one-to-one entries")
        if keys != tuple(sorted(keys)):
            raise ValueError("mapping entries must use canonical review-ID order")
        return values

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _validate_complete_family(bundles: tuple[EvidenceConditionBundle, ...]) -> None:
    conditions = tuple(bundle.evidence_condition for bundle in bundles)
    if len(bundles) != 3 or set(conditions) != set(_CONDITIONS):
        _fail("review sampling requires complete full, missing_key and noisy families")
    if len({bundle.candidate_id for bundle in bundles}) != 1:
        _fail("condition siblings must bind one candidate")
    if len({bundle.fault_type for bundle in bundles}) != 1:
        _fail("condition siblings must bind one mechanism")
    if len({bundle.source_execution_id for bundle in bundles}) != 1:
        _fail("condition siblings must bind one execution")
    if len({bundle.source_binding_sha256 for bundle in bundles}) != 1:
        _fail("condition siblings must bind one validated source")


def select_review_bundles(
    bundles: tuple[EvidenceConditionBundle, ...],
) -> tuple[EvidenceConditionBundle, ...]:
    """Select one complete family per mechanism with deterministic tie-breaking."""

    bundles = tuple(_revalidated(bundle) for bundle in bundles)
    by_family: dict[str, list[EvidenceConditionBundle]] = defaultdict(list)
    for bundle in bundles:
        by_family[bundle.case_family_id].append(bundle)

    eligible_by_mechanism: dict[str, list[tuple[EvidenceConditionBundle, ...]]] = defaultdict(list)
    for family_bundles in by_family.values():
        materialized = tuple(family_bundles)
        conditions = {bundle.evidence_condition for bundle in materialized}
        if conditions != set(_CONDITIONS):
            continue
        _validate_complete_family(materialized)
        eligible_by_mechanism[materialized[0].fault_type].append(materialized)

    selected: list[EvidenceConditionBundle] = []
    for mechanism in _MECHANISMS:
        candidates = eligible_by_mechanism.get(mechanism, [])
        if not candidates:
            _fail(f"review sampling has no complete eligible family for {mechanism}")
        chosen = min(
            candidates,
            key=lambda family: canonical_sha256(
                {
                    "protocol_version": HUMAN_REVIEW_PROTOCOL_VERSION,
                    "case_family_id": family[0].case_family_id,
                }
            ),
        )
        selected.extend(chosen)
    return tuple(sorted(selected, key=lambda bundle: _review_id(bundle)))


def build_human_review_packets(
    bundles: tuple[EvidenceConditionBundle, ...],
) -> tuple[BlindReviewPacket, ReviewMappingPacket]:
    """Build bound blind and evaluator packets from canonical evidence bundles."""

    selected = select_review_bundles(bundles)
    blind_entries = tuple(
        BlindReviewEntry(
            review_id=_review_id(bundle),
            diagnosis_projection=bundle.diagnosis_projection,
            diagnosis_projection_sha256=bundle.diagnosis_projection_sha256,
        )
        for bundle in selected
    )
    blind = BlindReviewPacket(
        instructions=(
            "Inspect only the visible projection and do not consult the mapping packet.",
            "Record whether hidden answers, expected judgments, or unsupported causal wording are visible.",
            "Use uncertain whenever the visible projection cannot be assessed reliably.",
        ),
        entries=blind_entries,
    )
    mapping_entries: list[ReviewMappingEntry] = []
    for bundle in selected:
        review_id = _review_id(bundle)
        family_review_id = _family_review_id(bundle.case_family_id)
        rubric = human_evidence_rubric_for(bundle.evidence_condition)
        values: dict[str, object] = {
            "review_id": review_id,
            "family_review_id": family_review_id,
            "evidence_bundle_id": bundle.evidence_bundle_id,
            "bundle_sha256": bundle.bundle_sha256,
            "diagnosis_projection_sha256": bundle.diagnosis_projection_sha256,
            "case_family_id": bundle.case_family_id,
            "fault_type": bundle.fault_type,
            "evidence_condition": bundle.evidence_condition,
            "rubric": rubric.model_dump(mode="json"),
        }
        mapping_entries.append(
            ReviewMappingEntry(
                review_id=review_id,
                family_review_id=family_review_id,
                evidence_bundle_id=bundle.evidence_bundle_id,
                bundle_sha256=bundle.bundle_sha256,
                diagnosis_projection_sha256=bundle.diagnosis_projection_sha256,
                case_family_id=bundle.case_family_id,
                fault_type=bundle.fault_type,
                evidence_condition=bundle.evidence_condition,
                rubric=rubric,
                binding_sha256=canonical_sha256(values),
            )
        )
    mapping = ReviewMappingPacket(
        blind_packet_sha256=blind.canonical_sha256(),
        instructions=(
            "Open this packet only after completing the blind leakage fields.",
            "Compare observed sufficiency and claim boundaries with the frozen rubric.",
            "Review all three mapped siblings together and record threats or deviations.",
        ),
        entries=tuple(sorted(mapping_entries, key=lambda entry: entry.review_id)),
    )
    validate_review_packets(blind, mapping)
    return blind, mapping


def validate_review_packets(
    blind: BlindReviewPacket,
    mapping: ReviewMappingPacket,
) -> None:
    """Reject packet tamper, incomplete coverage, and broken family pairing."""

    blind = _revalidated(blind)
    mapping = _revalidated(mapping)
    if mapping.blind_packet_sha256 != blind.canonical_sha256():
        _fail("mapping packet is not bound to the blind packet")
    blind_by_id = {
        entry.review_id: entry.diagnosis_projection_sha256 for entry in blind.entries
    }
    mapped_by_id = {
        entry.review_id: entry.diagnosis_projection_sha256 for entry in mapping.entries
    }
    if blind_by_id != mapped_by_id:
        _fail("mapping does not exactly cover blind projection hashes")
    mechanism_counts = Counter(entry.fault_type for entry in mapping.entries)
    condition_counts = Counter(entry.evidence_condition for entry in mapping.entries)
    if mechanism_counts != {mechanism: 3 for mechanism in _MECHANISMS}:
        _fail("review packet must contain three entries per mechanism")
    if condition_counts != {condition: 3 for condition in _CONDITIONS}:
        _fail("review packet must contain three entries per evidence condition")
    families: dict[str, list[ReviewMappingEntry]] = defaultdict(list)
    for entry in mapping.entries:
        families[entry.family_review_id].append(entry)
    if len(families) != 3:
        _fail("review packet must contain exactly three selected families")
    for entries in families.values():
        if len(entries) != 3 or {entry.evidence_condition for entry in entries} != set(
            _CONDITIONS
        ):
            _fail("each selected family must preserve all three evidence conditions")
        if len({entry.case_family_id for entry in entries}) != 1:
            _fail("family review mapping mixes case families")
        if len({entry.fault_type for entry in entries}) != 1:
            _fail("family review mapping mixes mechanisms")


class HumanReviewDecision(_StrictFrozenModel):
    """One human judgment covering blind leakage and mapped sufficiency fields."""

    review_id: ReviewId
    diagnosis_projection_sha256: Sha256
    hidden_answer_cue_found: ReviewAnswer
    expected_judgment_cue_found: ReviewAnswer
    unsupported_causal_wording_found: ReviewAnswer
    observed_sufficiency: ObservedSufficiency
    maximum_supported_claim: MaximumSupportedClaim
    allowed_claims_match: ReviewAnswer
    forbidden_claims_enforced: ReviewAnswer
    threshold_or_guardrail_threats: tuple[ValidityThreat, ...]
    protocol_deviations: tuple[str, ...]
    rationale: str = Field(min_length=20, max_length=4096)

    @field_validator("threshold_or_guardrail_threats")
    @classmethod
    def _threats_are_unique(cls, values: tuple[ValidityThreat, ...]) -> tuple[ValidityThreat, ...]:
        if len(values) != len(set(values)):
            raise ValueError("validity threats must be unique")
        return values

    @field_validator("protocol_deviations")
    @classmethod
    def _deviations_are_unique_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _canonical_text(value, "protocol deviation")
        if len(values) != len(set(values)):
            raise ValueError("protocol deviations must be unique")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale_is_canonical(cls, value: str) -> str:
        return _canonical_text(value, "review rationale")


class HumanFamilyDecision(_StrictFrozenModel):
    """Paired audit of the three condition siblings from one family."""

    family_review_id: FamilyReviewId
    family_binding_preserved: ReviewAnswer
    core_observations_preserved: ReviewAnswer
    missing_key_withholds_decisive_evidence: ReviewAnswer
    noisy_adds_only_neutral_secondary_evidence: ReviewAnswer
    noisy_secondary_not_marked_as_distractor: ReviewAnswer
    threats: tuple[ValidityThreat, ...]
    protocol_deviations: tuple[str, ...]
    rationale: str = Field(min_length=20, max_length=4096)

    @field_validator("threats")
    @classmethod
    def _threats_are_unique(cls, values: tuple[ValidityThreat, ...]) -> tuple[ValidityThreat, ...]:
        if len(values) != len(set(values)):
            raise ValueError("family validity threats must be unique")
        return values

    @field_validator("protocol_deviations")
    @classmethod
    def _deviations_are_unique_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _canonical_text(value, "family protocol deviation")
        if len(values) != len(set(values)):
            raise ValueError("family protocol deviations must be unique")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale_is_canonical(cls, value: str) -> str:
        return _canonical_text(value, "family review rationale")


class HumanReviewRecord(_StrictFrozenModel):
    """Human-authored decisions bound to both immutable review packets."""

    schema_version: Literal["p2-human-review-record/v1"] = HUMAN_REVIEW_RECORD_SCHEMA_VERSION
    protocol_version: Literal["p2-human-validity-review/v1"] = HUMAN_REVIEW_PROTOCOL_VERSION
    reviewer_kind: Literal["human"]
    reviewer_id: str = Field(min_length=1, max_length=128)
    blind_stage_completed_before_mapping_opened: Literal[True]
    judgments_personally_recorded: Literal[True]
    blind_packet_sha256: Sha256
    mapping_packet_sha256: Sha256
    decisions: tuple[HumanReviewDecision, ...]
    family_decisions: tuple[HumanFamilyDecision, ...]

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer_id_is_canonical(cls, value: str) -> str:
        return _canonical_text(value, "reviewer ID")

    @field_validator("decisions")
    @classmethod
    def _decisions_are_unique_ordered(
        cls, values: tuple[HumanReviewDecision, ...]
    ) -> tuple[HumanReviewDecision, ...]:
        keys = tuple(decision.review_id for decision in values)
        if len(values) != 9 or len(set(keys)) != 9:
            raise ValueError("review record must contain nine unique entry decisions")
        if keys != tuple(sorted(keys)):
            raise ValueError("review decisions must use canonical review-ID order")
        return values

    @field_validator("family_decisions")
    @classmethod
    def _family_decisions_are_unique_ordered(
        cls, values: tuple[HumanFamilyDecision, ...]
    ) -> tuple[HumanFamilyDecision, ...]:
        keys = tuple(decision.family_review_id for decision in values)
        if len(values) != 3 or len(set(keys)) != 3:
            raise ValueError("review record must contain three unique family decisions")
        if keys != tuple(sorted(keys)):
            raise ValueError("family decisions must use canonical family-review order")
        return values


class HumanReviewDecisionForm(_StrictFrozenModel):
    """Fillable entry form; ``None`` is explicit incompleteness, never a verdict."""

    review_id: ReviewId
    diagnosis_projection_sha256: Sha256
    hidden_answer_cue_found: ReviewAnswer | None = None
    expected_judgment_cue_found: ReviewAnswer | None = None
    unsupported_causal_wording_found: ReviewAnswer | None = None
    observed_sufficiency: ObservedSufficiency | None = None
    maximum_supported_claim: MaximumSupportedClaim | None = None
    allowed_claims_match: ReviewAnswer | None = None
    forbidden_claims_enforced: ReviewAnswer | None = None
    threshold_or_guardrail_threats: tuple[ValidityThreat, ...] = ()
    protocol_deviations: tuple[str, ...] = ()
    rationale: str | None = None


class HumanFamilyDecisionForm(_StrictFrozenModel):
    """Fillable paired-family form with no encoded expected answers."""

    family_review_id: FamilyReviewId
    family_binding_preserved: ReviewAnswer | None = None
    core_observations_preserved: ReviewAnswer | None = None
    missing_key_withholds_decisive_evidence: ReviewAnswer | None = None
    noisy_adds_only_neutral_secondary_evidence: ReviewAnswer | None = None
    noisy_secondary_not_marked_as_distractor: ReviewAnswer | None = None
    threats: tuple[ValidityThreat, ...] = ()
    protocol_deviations: tuple[str, ...] = ()
    rationale: str | None = None


class HumanReviewWorksheet(_StrictFrozenModel):
    """Bound form written to disk for completion by a human reviewer."""

    schema_version: Literal["p2-human-review-worksheet/v1"] = (
        HUMAN_REVIEW_WORKSHEET_SCHEMA_VERSION
    )
    protocol_version: Literal["p2-human-validity-review/v1"] = HUMAN_REVIEW_PROTOCOL_VERSION
    reviewer_kind: Literal["human"] = "human"
    reviewer_id: str | None = None
    blind_stage_completed_before_mapping_opened: bool = False
    judgments_personally_recorded: bool = False
    blind_packet_sha256: Sha256
    mapping_packet_sha256: Sha256
    decisions: tuple[HumanReviewDecisionForm, ...]
    family_decisions: tuple[HumanFamilyDecisionForm, ...]

    @model_validator(mode="after")
    def _worksheet_exactly_covers_bound_forms(self) -> HumanReviewWorksheet:
        review_ids = tuple(decision.review_id for decision in self.decisions)
        family_ids = tuple(decision.family_review_id for decision in self.family_decisions)
        if len(review_ids) != 9 or len(set(review_ids)) != 9:
            raise ValueError("worksheet must contain nine unique entry forms")
        if review_ids != tuple(sorted(review_ids)):
            raise ValueError("worksheet entry forms must use canonical order")
        if len(family_ids) != 3 or len(set(family_ids)) != 3:
            raise ValueError("worksheet must contain three unique family forms")
        if family_ids != tuple(sorted(family_ids)):
            raise ValueError("worksheet family forms must use canonical order")
        return self


def build_human_review_worksheet(
    blind: BlindReviewPacket,
    mapping: ReviewMappingPacket,
) -> HumanReviewWorksheet:
    """Create an incomplete, hash-bound worksheet without expected answers."""

    validate_review_packets(blind, mapping)
    return HumanReviewWorksheet(
        blind_packet_sha256=blind.canonical_sha256(),
        mapping_packet_sha256=mapping.canonical_sha256(),
        decisions=tuple(
            HumanReviewDecisionForm(
                review_id=entry.review_id,
                diagnosis_projection_sha256=entry.diagnosis_projection_sha256,
            )
            for entry in mapping.entries
        ),
        family_decisions=tuple(
            HumanFamilyDecisionForm(family_review_id=family_id)
            for family_id in sorted({entry.family_review_id for entry in mapping.entries})
        ),
    )


def finalize_human_review_worksheet(worksheet: HumanReviewWorksheet) -> HumanReviewRecord:
    """Convert a complete human worksheet into the strict immutable record."""

    worksheet = _revalidated(worksheet)
    if worksheet.reviewer_id is None:
        _fail("human review worksheet requires a reviewer ID")
    if not worksheet.blind_stage_completed_before_mapping_opened:
        _fail("reviewer must attest that the blind stage preceded mapping access")
    if not worksheet.judgments_personally_recorded:
        _fail("reviewer must attest that judgments were personally recorded")

    decisions: list[HumanReviewDecision] = []
    for entry_form in worksheet.decisions:
        entry_required = (
            entry_form.hidden_answer_cue_found,
            entry_form.expected_judgment_cue_found,
            entry_form.unsupported_causal_wording_found,
            entry_form.observed_sufficiency,
            entry_form.maximum_supported_claim,
            entry_form.allowed_claims_match,
            entry_form.forbidden_claims_enforced,
            entry_form.rationale,
        )
        if any(value is None for value in entry_required):
            _fail(f"human review worksheet entry is incomplete: {entry_form.review_id}")
        decisions.append(
            HumanReviewDecision(
                review_id=entry_form.review_id,
                diagnosis_projection_sha256=entry_form.diagnosis_projection_sha256,
                hidden_answer_cue_found=cast(ReviewAnswer, entry_form.hidden_answer_cue_found),
                expected_judgment_cue_found=cast(
                    ReviewAnswer, entry_form.expected_judgment_cue_found
                ),
                unsupported_causal_wording_found=cast(
                    ReviewAnswer, entry_form.unsupported_causal_wording_found
                ),
                observed_sufficiency=cast(
                    ObservedSufficiency, entry_form.observed_sufficiency
                ),
                maximum_supported_claim=cast(
                    MaximumSupportedClaim, entry_form.maximum_supported_claim
                ),
                allowed_claims_match=cast(ReviewAnswer, entry_form.allowed_claims_match),
                forbidden_claims_enforced=cast(
                    ReviewAnswer, entry_form.forbidden_claims_enforced
                ),
                threshold_or_guardrail_threats=entry_form.threshold_or_guardrail_threats,
                protocol_deviations=entry_form.protocol_deviations,
                rationale=cast(str, entry_form.rationale),
            )
        )

    family_decisions: list[HumanFamilyDecision] = []
    for family_form in worksheet.family_decisions:
        family_required = (
            family_form.family_binding_preserved,
            family_form.core_observations_preserved,
            family_form.missing_key_withholds_decisive_evidence,
            family_form.noisy_adds_only_neutral_secondary_evidence,
            family_form.noisy_secondary_not_marked_as_distractor,
            family_form.rationale,
        )
        if any(value is None for value in family_required):
            _fail(
                f"human family worksheet entry is incomplete: {family_form.family_review_id}"
            )
        family_decisions.append(
            HumanFamilyDecision(
                family_review_id=family_form.family_review_id,
                family_binding_preserved=cast(
                    ReviewAnswer, family_form.family_binding_preserved
                ),
                core_observations_preserved=cast(
                    ReviewAnswer, family_form.core_observations_preserved
                ),
                missing_key_withholds_decisive_evidence=(
                    cast(
                        ReviewAnswer,
                        family_form.missing_key_withholds_decisive_evidence,
                    )
                ),
                noisy_adds_only_neutral_secondary_evidence=(
                    cast(
                        ReviewAnswer,
                        family_form.noisy_adds_only_neutral_secondary_evidence,
                    )
                ),
                noisy_secondary_not_marked_as_distractor=(
                    cast(
                        ReviewAnswer,
                        family_form.noisy_secondary_not_marked_as_distractor,
                    )
                ),
                threats=family_form.threats,
                protocol_deviations=family_form.protocol_deviations,
                rationale=cast(str, family_form.rationale),
            )
        )
    return HumanReviewRecord(
        reviewer_kind="human",
        reviewer_id=worksheet.reviewer_id,
        blind_stage_completed_before_mapping_opened=True,
        judgments_personally_recorded=True,
        blind_packet_sha256=worksheet.blind_packet_sha256,
        mapping_packet_sha256=worksheet.mapping_packet_sha256,
        decisions=tuple(decisions),
        family_decisions=tuple(family_decisions),
    )

class HumanValidityFinding(_StrictFrozenModel):
    """Machine-derived blocker reference without copying free-text rationale."""

    kind: FindingKind
    review_id: ReviewId | None = None
    family_review_id: FamilyReviewId | None = None
    detail: str

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> HumanValidityFinding:
        if (self.review_id is None) == (self.family_review_id is None):
            raise ValueError("validity finding must reference exactly one review subject")
        return self


class HumanValidityReport(_StrictFrozenModel):
    """Derived review status; absence of a completed human record is never PASS."""

    schema_version: Literal["p2-human-validity-report/v1"] = (
        HUMAN_VALIDITY_REPORT_SCHEMA_VERSION
    )
    protocol_version: Literal["p2-human-validity-review/v1"] = HUMAN_REVIEW_PROTOCOL_VERSION
    blind_packet_sha256: Sha256
    mapping_packet_sha256: Sha256
    reviewed_entries: int = Field(ge=0)
    reviewed_families: int = Field(ge=0)
    mechanism_coverage: tuple[FaultTypeName, ...]
    condition_coverage: tuple[EvidenceConditionName, ...]
    findings: tuple[HumanValidityFinding, ...]
    status: GateStatus

    @model_validator(mode="after")
    def _status_is_derived(self) -> HumanValidityReport:
        expected = "blocked" if self.findings else "pass"
        if self.status != expected:
            raise ValueError("human validity status must be derived from findings")
        if self.status == "pass" and (self.reviewed_entries != 9 or self.reviewed_families != 3):
            raise ValueError("a passing review must cover all sampled entries and families")
        return self


def evaluate_human_review(
    blind: BlindReviewPacket,
    mapping: ReviewMappingPacket,
    record: HumanReviewRecord,
) -> HumanValidityReport:
    """Validate bindings and derive a fail-closed report from human judgments."""

    validate_review_packets(blind, mapping)
    record = _revalidated(record)
    if record.blind_packet_sha256 != blind.canonical_sha256():
        _fail("human review record is not bound to the blind packet")
    if record.mapping_packet_sha256 != mapping.canonical_sha256():
        _fail("human review record is not bound to the mapping packet")
    mapping_by_id = {entry.review_id: entry for entry in mapping.entries}
    decisions_by_id = {decision.review_id: decision for decision in record.decisions}
    if set(decisions_by_id) != set(mapping_by_id):
        _fail("human decisions do not exactly cover mapped review IDs")
    for review_id, decision in decisions_by_id.items():
        if decision.diagnosis_projection_sha256 != mapping_by_id[review_id].diagnosis_projection_sha256:
            _fail("human decision is bound to a different diagnosis projection")
    expected_families = {entry.family_review_id for entry in mapping.entries}
    actual_families = {decision.family_review_id for decision in record.family_decisions}
    if actual_families != expected_families:
        _fail("human family decisions do not exactly cover mapped families")

    findings: list[HumanValidityFinding] = []
    for decision in record.decisions:
        mapping_entry = mapping_by_id[decision.review_id]
        rubric = mapping_entry.rubric
        if any(
            answer == "uncertain"
            for answer in (
                decision.hidden_answer_cue_found,
                decision.expected_judgment_cue_found,
                decision.unsupported_causal_wording_found,
                decision.allowed_claims_match,
                decision.forbidden_claims_enforced,
            )
        ) or decision.observed_sufficiency == "cannot_assess" or decision.maximum_supported_claim == (
            "cannot_assess"
        ):
            findings.append(
                HumanValidityFinding(
                    kind="uncertain_judgment",
                    review_id=decision.review_id,
                    detail="one or more entry judgments are uncertain or cannot be assessed",
                )
            )
        if decision.hidden_answer_cue_found != "no" or decision.expected_judgment_cue_found != "no":
            findings.append(
                HumanValidityFinding(
                    kind="leakage",
                    review_id=decision.review_id,
                    detail="visible evidence may reveal hidden or evaluator-only information",
                )
            )
        if decision.unsupported_causal_wording_found != "no":
            findings.append(
                HumanValidityFinding(
                    kind="claim_boundary",
                    review_id=decision.review_id,
                    detail="visible evidence contains unsupported causal wording",
                )
            )
        if decision.observed_sufficiency != rubric.expected_sufficiency:
            findings.append(
                HumanValidityFinding(
                    kind="sufficiency_mismatch",
                    review_id=decision.review_id,
                    detail="observed sufficiency differs from the frozen condition policy",
                )
            )
        if decision.maximum_supported_claim != rubric.maximum_supported_claim:
            findings.append(
                HumanValidityFinding(
                    kind="claim_boundary",
                    review_id=decision.review_id,
                    detail="maximum supported claim differs from the frozen condition policy",
                )
            )
        if decision.allowed_claims_match != "yes" or decision.forbidden_claims_enforced != "yes":
            findings.append(
                HumanValidityFinding(
                    kind="claim_boundary",
                    review_id=decision.review_id,
                    detail="allowed or forbidden claim boundary did not pass human review",
                )
            )

    for family_decision in record.family_decisions:
        answers = (
            family_decision.family_binding_preserved,
            family_decision.core_observations_preserved,
            family_decision.missing_key_withholds_decisive_evidence,
            family_decision.noisy_adds_only_neutral_secondary_evidence,
            family_decision.noisy_secondary_not_marked_as_distractor,
        )
        if any(answer != "yes" for answer in answers):
            findings.append(
                HumanValidityFinding(
                    kind=(
                        "uncertain_judgment" if "uncertain" in answers else "family_pairing"
                    ),
                    family_review_id=family_decision.family_review_id,
                    detail="paired-family evidence construction did not pass every human check",
                )
            )

    findings.sort(
        key=lambda finding: (
            finding.kind,
            finding.review_id or "",
            finding.family_review_id or "",
            finding.detail,
        )
    )
    return HumanValidityReport(
        blind_packet_sha256=blind.canonical_sha256(),
        mapping_packet_sha256=mapping.canonical_sha256(),
        reviewed_entries=len(record.decisions),
        reviewed_families=len(record.family_decisions),
        mechanism_coverage=tuple(sorted({entry.fault_type for entry in mapping.entries})),
        condition_coverage=tuple(
            condition for condition in _CONDITIONS if any(
                entry.evidence_condition == condition for entry in mapping.entries
            )
        ),
        findings=tuple(findings),
        status="blocked" if findings else "pass",
    )


def validate_human_review(
    blind: BlindReviewPacket,
    mapping: ReviewMappingPacket,
    record: HumanReviewRecord,
) -> HumanValidityReport:
    """Return a passing report or raise when any blocker/uncertainty remains."""

    report = evaluate_human_review(blind, mapping, record)
    if report.status != "pass":
        _fail(f"human validity review contains {len(report.findings)} blocking finding(s)")
    return report

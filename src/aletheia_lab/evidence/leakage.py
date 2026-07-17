"""Structural, semantic and human-review leakage gates for P1 evidence."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aletheia_lab.evidence.rubric import (
    EvidenceCondition,
    EvidenceSufficiency,
    condition_rubric_for,
)
from aletheia_lab.evidence.schema import (
    HIDDEN_GROUND_TRUTH_MARKERS,
    DiagnosisEvidenceView,
    EvidenceBundle,
    canonical_json,
    contains_condition_or_rubric_label,
    project_diagnosis_evidence,
    sha256_text,
)

LEAKAGE_AUDIT_SCHEMA_VERSION: Final[Literal["evidence-leakage-audit/2"]] = (
    "evidence-leakage-audit/2"
)
BLIND_REVIEW_PACKET_SCHEMA_VERSION: Final[Literal["human-evidence-blind-packet/2"]] = (
    "human-evidence-blind-packet/2"
)
REVIEW_MAPPING_PACKET_SCHEMA_VERSION: Final[Literal["human-evidence-review-mapping/2"]] = (
    "human-evidence-review-mapping/2"
)
HUMAN_REVIEW_RECORD_SCHEMA_VERSION: Final[Literal["human-evidence-review/2"]] = (
    "human-evidence-review/2"
)
HUMAN_REVIEW_ATTESTATION: Final = (
    "I completed rounds A-C without the mapping packet, opened the mapping only for "
    "round D and paired-family audit, and personally recorded every decision."
)

_SEMANTIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "explicit-causal-answer",
        re.compile(
            r"\b(?:root\s+cause|cause\s+(?:is|was)|caused\s+by|responsible\s+for|because\s+of)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "diagnosis-instruction-cue",
        re.compile(
            r"\b(?:should|must)\s+(?:diagnose|conclude|abstain|reject)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "rubric-sufficiency-cue",
        re.compile(
            r"\b(?:decisive|sufficient|insufficient)\s+evidence\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "semantic-shift-label",
        re.compile(
            r"\b(?:distribution|population)\s+(?:drift|drifted|shift|shifted)\b",
            flags=re.IGNORECASE,
        ),
    ),
)


def normalize_text(value: str) -> str:
    """Normalize text for simple leakage scanning."""

    return " ".join(value.casefold().split())


def find_forbidden_terms(text: str, forbidden_terms: Iterable[str]) -> list[str]:
    """Return forbidden terms that appear in text."""

    normalized_text = normalize_text(text)
    matches: list[str] = []
    for term in forbidden_terms:
        normalized_term = normalize_text(term)
        if normalized_term and normalized_term in normalized_text:
            matches.append(term)
    return matches


def bundle_text(bundle: EvidenceBundle) -> str:
    """Flatten visible evidence into text."""

    return "\n".join(f"{item.title}\n{item.content}" for item in bundle.diagnosis_visible_items)


def bundle_has_leakage(bundle: EvidenceBundle, forbidden_terms: Iterable[str]) -> bool:
    """Return true when visible evidence leaks forbidden answer-key terms."""

    return bool(find_forbidden_terms(bundle_text(bundle), forbidden_terms))


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LeakageFinding(_StrictFrozenModel):
    """One reproducible machine finding without copying sensitive excerpts."""

    category: Literal["structural", "semantic"]
    rule_id: str
    evidence_bundle_id: str
    evidence_id: str
    matched_indicator: str


class LeakageAuditReport(_StrictFrozenModel):
    """Machine audit whose PASS value is recomputed from findings."""

    schema_version: Literal["evidence-leakage-audit/2"]
    bundle_count: int
    visible_item_count: int
    findings: tuple[LeakageFinding, ...]
    passed: bool

    @field_validator("findings")
    @classmethod
    def _canonical_findings(cls, value: tuple[LeakageFinding, ...]) -> tuple[LeakageFinding, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.evidence_bundle_id,
                    item.evidence_id,
                    item.category,
                    item.rule_id,
                    item.matched_indicator,
                ),
            )
        )

    @model_validator(mode="after")
    def _derived_pass(self) -> LeakageAuditReport:
        if self.bundle_count <= 0 or self.visible_item_count <= 0:
            raise ValueError("leakage audit requires non-empty bundles and visible evidence")
        if self.passed != (not self.findings):
            raise ValueError("leakage audit PASS must be derived from zero findings")
        return self


def audit_bundle_leakage(bundles: Iterable[EvidenceBundle]) -> LeakageAuditReport:
    """Run structural and conservative semantic scans on diagnosis-visible items."""

    materialized = tuple(bundles)
    findings: list[LeakageFinding] = []
    visible_count = 0
    for bundle in materialized:
        projection = project_diagnosis_evidence(bundle)
        if contains_condition_or_rubric_label(projection.model_dump(mode="json")):
            findings.append(
                LeakageFinding(
                    category="structural",
                    rule_id="condition-or-rubric-metadata",
                    evidence_bundle_id=bundle.evidence_bundle_id,
                    evidence_id="projection",
                    matched_indicator="structured evaluator metadata",
                )
            )
        for item in bundle.diagnosis_visible_items:
            visible_count += 1
            text = f"{item.title}\n{item.content}"
            for marker in find_forbidden_terms(text, HIDDEN_GROUND_TRUTH_MARKERS):
                findings.append(
                    LeakageFinding(
                        category="structural",
                        rule_id="hidden-ground-truth-marker",
                        evidence_bundle_id=bundle.evidence_bundle_id,
                        evidence_id=item.evidence_id,
                        matched_indicator=marker,
                    )
                )
            for rule_id, pattern in _SEMANTIC_RULES:
                match = pattern.search(text)
                if match:
                    findings.append(
                        LeakageFinding(
                            category="semantic",
                            rule_id=rule_id,
                            evidence_bundle_id=bundle.evidence_bundle_id,
                            evidence_id=item.evidence_id,
                            matched_indicator=normalize_text(match.group(0)),
                        )
                    )
    return LeakageAuditReport(
        schema_version=LEAKAGE_AUDIT_SCHEMA_VERSION,
        bundle_count=len(materialized),
        visible_item_count=visible_count,
        findings=tuple(findings),
        passed=not findings,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REVIEW_ID_PATTERN = r"^review-[0-9a-f]{64}$"
_FAMILY_REVIEW_ID_PATTERN = r"^family-review-[0-9a-f]{64}$"


def _review_id(diagnosis_view_sha256: str) -> str:
    return f"review-{sha256_text(f'p1-blind-review/v2:{diagnosis_view_sha256}')}"


def _family_review_id(case_family_id: str) -> str:
    return f"family-review-{sha256_text(f'p1-family-review/v2:{case_family_id}')}"


class BlindReviewPacketEntry(_StrictFrozenModel):
    """Round A-C unit: exactly one opaque ID and one diagnosis projection."""

    review_id: str
    diagnosis_view: DiagnosisEvidenceView

    @model_validator(mode="after")
    def _opaque_id_is_bound_to_view(self) -> BlindReviewPacketEntry:
        expected = _review_id(self.diagnosis_view.canonical_sha256())
        if self.review_id != expected:
            raise ValueError("blind review ID is not bound to its diagnosis view")
        return self


class BlindReviewPacket(_StrictFrozenModel):
    """Census packet used before any condition or evaluator label is revealed."""

    schema_version: Literal["human-evidence-blind-packet/2"]
    sampling_strategy: Literal["census_all_contexts"]
    review_questions: tuple[str, str, str]
    entries: tuple[BlindReviewPacketEntry, ...]

    @field_validator("review_questions")
    @classmethod
    def _questions_present(cls, value: tuple[str, str, str]) -> tuple[str, str, str]:
        if any(not question.strip() for question in value):
            raise ValueError("blind review questions must be non-empty")
        return value

    @field_validator("entries")
    @classmethod
    def _unique_ordered_entries(
        cls, value: tuple[BlindReviewPacketEntry, ...]
    ) -> tuple[BlindReviewPacketEntry, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.review_id))
        ids = [entry.review_id for entry in ordered]
        contexts = [entry.diagnosis_view.diagnosis_context_id for entry in ordered]
        if len(ids) != len(set(ids)) or len(contexts) != len(set(contexts)):
            raise ValueError("blind review packet entries must be unique")
        return ordered

    def canonical_sha256(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


def _mapping_binding_payload(
    *,
    review_id: str,
    diagnosis_view_sha256: str,
    evidence_bundle_id: str,
    case_family_id: str,
    family_review_id: str,
    evidence_condition: EvidenceCondition,
    expected_sufficiency: EvidenceSufficiency,
    expected_diagnosis_behavior: str,
) -> dict[str, str]:
    return {
        "review_id": review_id,
        "diagnosis_view_sha256": diagnosis_view_sha256,
        "evidence_bundle_id": evidence_bundle_id,
        "case_family_id": case_family_id,
        "family_review_id": family_review_id,
        "evidence_condition": evidence_condition,
        "expected_sufficiency": expected_sufficiency,
        "expected_diagnosis_behavior": expected_diagnosis_behavior,
    }


class ReviewMappingEntry(_StrictFrozenModel):
    """Evaluator-only round-D mapping, cryptographically bound per entry."""

    review_id: str
    diagnosis_view_sha256: str
    evidence_bundle_id: str
    case_family_id: str
    family_review_id: str
    evidence_condition: EvidenceCondition
    expected_sufficiency: EvidenceSufficiency
    expected_diagnosis_behavior: str
    binding_sha256: str

    @field_validator("review_id")
    @classmethod
    def _valid_review_id(cls, value: str) -> str:
        if re.fullmatch(_REVIEW_ID_PATTERN, value) is None:
            raise ValueError("invalid opaque review ID")
        return value

    @field_validator("family_review_id")
    @classmethod
    def _valid_family_review_id(cls, value: str) -> str:
        if re.fullmatch(_FAMILY_REVIEW_ID_PATTERN, value) is None:
            raise ValueError("invalid opaque family review ID")
        return value

    @field_validator("diagnosis_view_sha256", "binding_sha256")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("invalid SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _binding_and_rubric_integrity(self) -> ReviewMappingEntry:
        rubric = condition_rubric_for(self.evidence_condition)
        if self.expected_sufficiency != rubric.causal_claim_sufficiency:
            raise ValueError("mapping sufficiency differs from the canonical rubric")
        if self.expected_diagnosis_behavior != rubric.expected_diagnosis_behavior:
            raise ValueError("mapping behavior differs from the canonical rubric")
        if self.family_review_id != _family_review_id(self.case_family_id):
            raise ValueError("family review ID is not bound to the case family")
        expected_binding = sha256_text(
            canonical_json(
                _mapping_binding_payload(
                    review_id=self.review_id,
                    diagnosis_view_sha256=self.diagnosis_view_sha256,
                    evidence_bundle_id=self.evidence_bundle_id,
                    case_family_id=self.case_family_id,
                    family_review_id=self.family_review_id,
                    evidence_condition=self.evidence_condition,
                    expected_sufficiency=self.expected_sufficiency,
                    expected_diagnosis_behavior=self.expected_diagnosis_behavior,
                )
            )
        )
        if self.binding_sha256 != expected_binding:
            raise ValueError("review mapping entry binding mismatch")
        return self


class ReviewMappingPacket(_StrictFrozenModel):
    """Evaluator packet opened only after rounds A-C are complete."""

    schema_version: Literal["human-evidence-review-mapping/2"]
    blind_packet_sha256: str
    round_d_question: str
    paired_family_questions: tuple[str, ...]
    entries: tuple[ReviewMappingEntry, ...]

    @field_validator("blind_packet_sha256")
    @classmethod
    def _valid_blind_digest(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("invalid blind packet SHA-256")
        return value

    @field_validator("round_d_question")
    @classmethod
    def _round_d_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("round D question must not be blank")
        return value

    @field_validator("paired_family_questions")
    @classmethod
    def _family_questions_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not question.strip() for question in value):
            raise ValueError("paired-family questions must be non-empty")
        return value

    @field_validator("entries")
    @classmethod
    def _unique_ordered_entries(
        cls, value: tuple[ReviewMappingEntry, ...]
    ) -> tuple[ReviewMappingEntry, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.review_id))
        for values in (
            [entry.review_id for entry in ordered],
            [entry.evidence_bundle_id for entry in ordered],
            [entry.diagnosis_view_sha256 for entry in ordered],
        ):
            if len(values) != len(set(values)):
                raise ValueError("review mapping entries must be one-to-one")
        return ordered

    def canonical_sha256(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


def build_human_review_packets(
    bundles: Iterable[EvidenceBundle],
) -> tuple[BlindReviewPacket, ReviewMappingPacket]:
    """Build blind A-C and evaluator-only D/family packets from one census."""

    materialized = tuple(bundles)
    blind_entries: list[BlindReviewPacketEntry] = []
    mapping_values: list[dict[str, str]] = []
    for bundle in materialized:
        view = project_diagnosis_evidence(bundle)
        view_sha = view.canonical_sha256()
        review_id = _review_id(view_sha)
        family_review_id = _family_review_id(bundle.case_family_id)
        rubric = condition_rubric_for(bundle.evidence_condition)
        blind_entries.append(BlindReviewPacketEntry(review_id=review_id, diagnosis_view=view))
        mapping_values.append(
            _mapping_binding_payload(
                review_id=review_id,
                diagnosis_view_sha256=view_sha,
                evidence_bundle_id=bundle.evidence_bundle_id,
                case_family_id=bundle.case_family_id,
                family_review_id=family_review_id,
                evidence_condition=bundle.evidence_condition,
                expected_sufficiency=rubric.causal_claim_sufficiency,
                expected_diagnosis_behavior=rubric.expected_diagnosis_behavior,
            )
        )
    blind_packet = BlindReviewPacket(
        schema_version=BLIND_REVIEW_PACKET_SCHEMA_VERSION,
        sampling_strategy="census_all_contexts",
        review_questions=(
            "A. Does the visible material reveal or paraphrase a hidden answer?",
            "B. Does any visible identifier or wording reveal experiment design or expected judgment?",
            "C. Does the visible material state an unsupported causal conclusion?",
        ),
        entries=tuple(blind_entries),
    )
    mapping_entries = tuple(
        ReviewMappingEntry(
            **values,
            binding_sha256=sha256_text(canonical_json(values)),
        )
        for values in mapping_values
    )
    mapping_packet = ReviewMappingPacket(
        schema_version=REVIEW_MAPPING_PACKET_SCHEMA_VERSION,
        blind_packet_sha256=blind_packet.canonical_sha256(),
        round_d_question=(
            "D. After opening this mapping, does the visible evidence support exactly the "
            "bounded claim policy and expected behavior in the evaluator rubric?"
        ),
        paired_family_questions=(
            "Are the core observations identical between the first and third mapped conditions?",
            "Does the second mapped condition omit exactly the declared evidence roles?",
            "Does the third mapped condition add only the neutral secondary comparison?",
            "Does any diagnosis-facing wording reveal which comparison is evaluator-designed noise?",
            "Are all three family entries mapped to their original diagnosis-view hashes?",
        ),
        entries=mapping_entries,
    )
    validate_review_packets(blind_packet, mapping_packet)
    return blind_packet, mapping_packet


def validate_review_packets(
    blind_packet: BlindReviewPacket, mapping_packet: ReviewMappingPacket
) -> None:
    """Require exact packet binding, a 15-entry census and five complete families."""

    # Re-run schema/model validators so ``model_copy(update=...)`` cannot be used
    # as a caller-side bypass around frozen Pydantic construction.
    blind_packet = BlindReviewPacket.model_validate(blind_packet.model_dump())
    mapping_packet = ReviewMappingPacket.model_validate(mapping_packet.model_dump())
    if mapping_packet.blind_packet_sha256 != blind_packet.canonical_sha256():
        raise ValueError("mapping packet is not bound to this blind packet")
    blind = {
        entry.review_id: entry.diagnosis_view.canonical_sha256() for entry in blind_packet.entries
    }
    mapped = {entry.review_id: entry.diagnosis_view_sha256 for entry in mapping_packet.entries}
    if blind != mapped:
        raise ValueError("mapping does not exactly cover the blind packet view hashes")
    if len(blind) != 15:
        raise ValueError("human review packets must contain exactly 15 entries")
    counts = Counter(entry.evidence_condition for entry in mapping_packet.entries)
    if counts != {"full": 5, "missing_key": 5, "noisy": 5}:
        raise ValueError("review mapping must preserve the 5/5/5 condition census")
    families: dict[str, list[ReviewMappingEntry]] = defaultdict(list)
    for entry in mapping_packet.entries:
        families[entry.family_review_id].append(entry)
    if len(families) != 5 or any(
        {entry.evidence_condition for entry in entries} != {"full", "missing_key", "noisy"}
        or len(entries) != 3
        for entries in families.values()
    ):
        raise ValueError("review mapping must preserve five complete three-condition families")


ReviewAnswer = Literal["yes", "no", "uncertain"]


class HumanReviewDecision(_StrictFrozenModel):
    """One A-D decision; every answer requires a written rationale."""

    review_id: str
    diagnosis_view_sha256: str
    answer_revealing_cue_found: ReviewAnswer
    design_or_expectation_cue_found: ReviewAnswer
    unsupported_causal_wording_found: ReviewAnswer
    bounded_claim_policy_matches_rubric: ReviewAnswer
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("every human review decision requires a rationale")
        return value


class HumanFamilyDecision(_StrictFrozenModel):
    """Paired-family audit completed only after the mapping is opened."""

    family_review_id: str
    core_observations_match: ReviewAnswer
    declared_omissions_match: ReviewAnswer
    secondary_comparison_only_addition: ReviewAnswer
    noise_design_is_hidden: ReviewAnswer
    mapping_hashes_match: ReviewAnswer
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("every family review decision requires a rationale")
        return value


class HumanReviewRecord(_StrictFrozenModel):
    """Strict, signed review bound to both immutable packet hashes."""

    schema_version: Literal["human-evidence-review/2"]
    reviewer_kind: Literal["human"]
    reviewer_id: str
    started_at: str
    completed_at: str
    independent_from_implementation: Literal[True]
    prohibited_sources_consulted: Literal[False]
    ai_assistance_used: Literal[False]
    blind_stage_completed_before_mapping_opened: Literal[True]
    attestation: Literal[
        "I completed rounds A-C without the mapping packet, opened the mapping only for "
        "round D and paired-family audit, and personally recorded every decision."
    ]
    signature: str
    blind_packet_sha256: str
    mapping_packet_sha256: str
    decisions: tuple[HumanReviewDecision, ...]
    family_decisions: tuple[HumanFamilyDecision, ...]

    @field_validator("reviewer_id", "signature")
    @classmethod
    def _identity_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewer identity and signature must not be blank")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def _timezone_aware(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("review timestamps must be ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("review timestamps must include a timezone")
        return value

    @field_validator("blind_packet_sha256", "mapping_packet_sha256")
    @classmethod
    def _valid_packet_hash(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("invalid review packet SHA-256")
        return value

    @field_validator("decisions")
    @classmethod
    def _unique_decisions(
        cls, value: tuple[HumanReviewDecision, ...]
    ) -> tuple[HumanReviewDecision, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.review_id))
        ids = [decision.review_id for decision in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("human review decisions must be unique")
        return ordered

    @field_validator("family_decisions")
    @classmethod
    def _unique_family_decisions(
        cls, value: tuple[HumanFamilyDecision, ...]
    ) -> tuple[HumanFamilyDecision, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.family_review_id))
        ids = [decision.family_review_id for decision in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("human family decisions must be unique")
        return ordered

    @model_validator(mode="after")
    def _valid_review_interval(self) -> HumanReviewRecord:
        started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        if completed <= started:
            raise ValueError("human review completion must be after its start")
        return self


def validate_human_review(
    blind_packet: BlindReviewPacket,
    mapping_packet: ReviewMappingPacket,
    record: HumanReviewRecord,
) -> None:
    """Require exact signed coverage; UNCERTAIN is never silently promoted to PASS."""

    validate_review_packets(blind_packet, mapping_packet)
    record = HumanReviewRecord.model_validate(record.model_dump())
    if record.blind_packet_sha256 != blind_packet.canonical_sha256():
        raise ValueError("human review is not bound to the blind packet")
    if record.mapping_packet_sha256 != mapping_packet.canonical_sha256():
        raise ValueError("human review is not bound to the mapping packet")
    expected = {entry.review_id: entry.diagnosis_view_sha256 for entry in mapping_packet.entries}
    actual = {decision.review_id: decision.diagnosis_view_sha256 for decision in record.decisions}
    if actual != expected:
        raise ValueError("human review decisions do not exactly cover packet hashes")
    expected_families = {entry.family_review_id for entry in mapping_packet.entries}
    actual_families = {decision.family_review_id for decision in record.family_decisions}
    if actual_families != expected_families:
        raise ValueError("human family decisions do not exactly cover five families")
    blockers = [
        decision.review_id
        for decision in record.decisions
        if decision.answer_revealing_cue_found != "no"
        or decision.design_or_expectation_cue_found != "no"
        or decision.unsupported_causal_wording_found != "no"
        or decision.bounded_claim_policy_matches_rubric != "yes"
    ]
    family_blockers = [
        decision.family_review_id
        for decision in record.family_decisions
        if any(
            answer != "yes"
            for answer in (
                decision.core_observations_match,
                decision.declared_omissions_match,
                decision.secondary_comparison_only_addition,
                decision.noise_design_is_hidden,
                decision.mapping_hashes_match,
            )
        )
    ]
    if blockers or family_blockers:
        raise ValueError(
            "human evidence review found blocker or uncertain decision(s): "
            f"entries={blockers}, families={family_blockers}"
        )

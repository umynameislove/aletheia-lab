"""Fail-closed integration of the frozen P1 machine and human review results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evidence.leakage import (
    BlindReviewPacket,
    ReviewMappingPacket,
    validate_review_packets,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class P1MachineSummary(_StrictFrozenModel):
    correct: int = Field(ge=0)
    incorrect: int = Field(ge=0)
    not_asserted: int = Field(ge=0)
    fully_supported: int = Field(ge=0)
    behavior_compliant: int = Field(ge=0)
    missing_key_sensitive: int = Field(ge=0)
    noisy_robust: int = Field(ge=0)

    @model_validator(mode="after")
    def _p1_census(self) -> Self:
        if (self.correct, self.incorrect, self.not_asserted) != (23, 1, 6):
            raise ValueError("P1 machine correctness census must remain 23/1/6")
        if (
            self.fully_supported,
            self.behavior_compliant,
            self.missing_key_sensitive,
            self.noisy_robust,
        ) != (30, 29, 10, 8):
            raise ValueError("P1 machine evaluation census differs from the result lock")
        return self


class EvidenceReviewSummary(_StrictFrozenModel):
    schema_version: Literal["p1-evidence-public-review/1"]
    reviewer_id: str = Field(min_length=1)
    review_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_before_mapping: Literal[True]
    ai_assistance_used: Literal[False]
    entry_pass_count: Literal[15]
    family_pass_count: Literal[5]
    uncertain_count: Literal[0]
    verdict: Literal["pass"]


class DiagnosisReviewSummary(_StrictFrozenModel):
    schema_version: Literal["p1-diagnosis-public-review/1"]
    reviewer_id: str = Field(min_length=1)
    review_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_before_mapping: Literal[True]
    ai_assistance_used: Literal[False]
    round1_pass: Literal[18]
    round1_fail: Literal[0]
    round1_uncertain: Literal[12]
    human_correct: Literal[24]
    human_incorrect: Literal[0]
    human_not_asserted: Literal[6]
    human_behavior_compliant: Literal[30]
    human_machine_agreement: Literal[29]
    human_missing_key_sensitive: Literal[10]
    machine_noisy_robust: Literal[8]
    human_semantic_noisy_robust: Literal[10]
    status: Literal["valid_with_disclosed_correction"]


class EvidencePublicReview(_StrictFrozenModel):
    schema_version: Literal["p1-evidence-public-review/1"]
    reviewer_id: str = Field(min_length=1)
    blind_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_before_mapping: Literal[True]
    ai_assistance_used: Literal[False]
    entry_pass_count: Literal[15]
    family_pass_count: Literal[5]
    uncertain_count: Literal[0]
    verdict: Literal["pass"]


class DiagnosisPublicReview(_StrictFrozenModel):
    schema_version: Literal["p1-diagnosis-public-review/1"]
    reviewer_id: str = Field(min_length=1)
    blind_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_packet_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    blind_before_mapping: Literal[True]
    ai_assistance_used: Literal[False]
    round1_pass: Literal[18]
    round1_fail: Literal[0]
    round1_uncertain: Literal[12]
    human_correct: Literal[24]
    human_incorrect: Literal[0]
    human_not_asserted: Literal[6]
    human_behavior_compliant: Literal[30]
    human_machine_agreement: Literal[29]
    human_missing_key_sensitive: Literal[10]
    machine_noisy_robust: Literal[8]
    human_semantic_noisy_robust: Literal[10]
    status: Literal["valid_with_disclosed_correction"]


class P1FinalCloseout(_StrictFrozenModel):
    schema_version: Literal["p1-final-closeout/1"]
    phase: Literal["P1"]
    status: Literal["complete"]
    decision: Literal["go_to_phase_2"]
    result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    machine_result_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    machine_summary: P1MachineSummary
    evidence_review: EvidenceReviewSummary
    diagnosis_review: DiagnosisReviewSummary
    independent_family_count: Literal[5]
    diagnosis_context_count: Literal[15]
    model_output_count: Literal[30]
    claim_boundary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_review_binding(self) -> Self:
        if self.diagnosis_review.source_result_lock_sha256 != self.result_lock_sha256:
            raise ValueError("diagnosis review is not bound to the P1 result lock")
        forbidden = ("superiority", "generalization", "production-ready")
        lowered = self.claim_boundary.lower()
        if any(term in lowered for term in forbidden):
            raise ValueError("P1 claim boundary contains an unauthorized claim")
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _validate_diagnosis_packets(
    blind_path: Path,
    mapping_path: Path,
    record: P1FinalCloseout,
) -> None:
    blind = _load_json_object(blind_path)
    mapping = _load_json_object(mapping_path)
    if blind.get("schema_version") != "diagnosis-human-review-blind-packet/1":
        raise ValueError("unexpected diagnosis blind packet schema")
    if mapping.get("schema_version") != "diagnosis-human-review-mapping-packet/1":
        raise ValueError("unexpected diagnosis mapping packet schema")
    if _sha256(blind_path) != record.diagnosis_review.blind_packet_sha256:
        raise ValueError("diagnosis blind packet SHA-256 mismatch")
    if _sha256(mapping_path) != record.diagnosis_review.mapping_packet_sha256:
        raise ValueError("diagnosis mapping packet SHA-256 mismatch")
    if mapping.get("blind_packet_sha256") != _sha256(blind_path):
        raise ValueError("diagnosis mapping is not bound to the blind packet")
    if (
        blind.get("source_result_lock_sha256"),
        mapping.get("source_result_lock_sha256"),
    ) != (record.result_lock_sha256, record.result_lock_sha256):
        raise ValueError("diagnosis packets are not bound to the result lock")

    blind_entries = blind.get("entries")
    mapping_entries = mapping.get("entries")
    paired_groups = mapping.get("paired_groups")
    if not isinstance(blind_entries, list) or not isinstance(mapping_entries, list):
        raise ValueError("diagnosis packets must contain entry lists")
    if not isinstance(paired_groups, list):
        raise ValueError("diagnosis mapping must contain paired groups")
    if (
        blind.get("entry_count"),
        mapping.get("entry_count"),
        mapping.get("paired_group_count"),
        len(blind_entries),
        len(mapping_entries),
        len(paired_groups),
    ) != (30, 30, 10, 30, 30, 10):
        raise ValueError("diagnosis packets do not preserve the 30-entry/10-pair census")

    def pairs(entries: list[object]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("diagnosis packet entry must be an object")
            review_id = entry.get("review_id")
            binding = entry.get("entry_binding_sha256")
            if not isinstance(review_id, str) or not isinstance(binding, str):
                raise ValueError("diagnosis packet entry is missing its binding")
            result.add((review_id, binding))
        return result

    blind_pairs = pairs(blind_entries)
    mapping_pairs = pairs(mapping_entries)
    if len(blind_pairs) != 30 or blind_pairs != mapping_pairs:
        raise ValueError("diagnosis blind and mapping entries differ")
    grouped: list[object] = []
    for group in paired_groups:
        if not isinstance(group, dict):
            raise ValueError("diagnosis paired group must be an object")
        conditions = group.get("conditions")
        if not isinstance(conditions, dict) or set(conditions) != {
            "full",
            "missing_key",
            "noisy",
        }:
            raise ValueError("diagnosis paired group does not contain all three conditions")
        grouped.extend(conditions.values())
    if len(grouped) != 30 or pairs(grouped) != mapping_pairs:
        raise ValueError("diagnosis paired groups do not exactly cover the mapping")


def validate_p1_final_closeout(
    record_path: str | Path,
    machine_result_path: str | Path,
    result_lock_path: str | Path,
    evidence_review_path: str | Path,
    evidence_blind_packet_path: str | Path,
    evidence_mapping_packet_path: str | Path,
    diagnosis_review_path: str | Path,
    diagnosis_blind_packet_path: str | Path,
    diagnosis_mapping_packet_path: str | Path,
) -> P1FinalCloseout:
    """Validate the complete P1 decision without network access or mutable inference."""

    paths = tuple(
        Path(path)
        for path in (
            record_path,
            machine_result_path,
            result_lock_path,
            evidence_review_path,
            evidence_blind_packet_path,
            evidence_mapping_packet_path,
            diagnosis_review_path,
            diagnosis_blind_packet_path,
            diagnosis_mapping_packet_path,
        )
    )
    if any(path.is_symlink() for path in paths):
        raise ValueError("P1 final closeout inputs must not be symlinks")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    record = P1FinalCloseout.model_validate_json(paths[0].read_text(encoding="utf-8"))
    machine = _load_json_object(paths[1])
    if _sha256(paths[1]) != record.machine_result_file_sha256:
        raise ValueError("machine result SHA-256 mismatch")
    if _sha256(paths[2]) != record.result_lock_sha256:
        raise ValueError("result-lock SHA-256 mismatch")
    if (
        machine.get("schema_version"),
        machine.get("result_status"),
        machine.get("result_lock_sha256"),
        machine.get("independent_family_count"),
        machine.get("diagnosis_context_count"),
        machine.get("run_count"),
    ) != (
        "p1-canonical-result/1",
        "machine_scored_pending_human_review",
        record.result_lock_sha256,
        5,
        15,
        30,
    ):
        raise ValueError("machine result does not match the frozen P1 census")
    summary = machine.get("evaluation_summary")
    if not isinstance(summary, dict):
        raise ValueError("machine result is missing its evaluation summary")
    if summary.get("correctness_counts") != {
        "correct": record.machine_summary.correct,
        "incorrect": record.machine_summary.incorrect,
        "not_asserted": record.machine_summary.not_asserted,
    }:
        raise ValueError("machine correctness differs from final closeout")
    if (
        summary.get("support_counts"),
        summary.get("behavior_compliant_count"),
        summary.get("missing_key_sensitive_count"),
        summary.get("noisy_robust_count"),
    ) != (
        {"fully_supported": record.machine_summary.fully_supported},
        record.machine_summary.behavior_compliant,
        record.machine_summary.missing_key_sensitive,
        record.machine_summary.noisy_robust,
    ):
        raise ValueError("machine evaluation differs from final closeout")

    if _sha256(paths[3]) != record.evidence_review.review_file_sha256:
        raise ValueError("evidence review SHA-256 mismatch")
    evidence_public = EvidencePublicReview.model_validate_json(
        paths[3].read_text(encoding="utf-8")
    )
    if evidence_public.model_dump() != record.evidence_review.model_dump(
        exclude={"review_file_sha256"}
    ):
        raise ValueError("evidence review differs from final closeout")
    evidence_blind = BlindReviewPacket.model_validate_json(
        paths[4].read_text(encoding="utf-8")
    )
    evidence_mapping = ReviewMappingPacket.model_validate_json(
        paths[5].read_text(encoding="utf-8")
    )
    validate_review_packets(evidence_blind, evidence_mapping)
    if (
        evidence_blind.canonical_sha256(),
        evidence_mapping.canonical_sha256(),
    ) != (
        record.evidence_review.blind_packet_sha256,
        record.evidence_review.mapping_packet_sha256,
    ):
        raise ValueError("evidence packet binding differs from final closeout")

    if _sha256(paths[6]) != record.diagnosis_review.review_file_sha256:
        raise ValueError("diagnosis review SHA-256 mismatch")
    diagnosis_public = DiagnosisPublicReview.model_validate_json(
        paths[6].read_text(encoding="utf-8")
    )
    if diagnosis_public.model_dump() != record.diagnosis_review.model_dump(
        exclude={"review_file_sha256"}
    ):
        raise ValueError("diagnosis review differs from final closeout")
    _validate_diagnosis_packets(paths[7], paths[8], record)
    return record

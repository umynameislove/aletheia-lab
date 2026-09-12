"""Prospective V3.2 source-measurement role amendment.

V3.2 preserves the failed V3.1 cohort as history.  It changes the source
stimulus constructor prospectively: authenticated visible measurements are
projected by deterministic code, while the model remains responsible only for
the later claim/evidence relation judgment that the study intends to validate.
This module grants no provider, corpus, relation-execution or human-review
authority.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_support_v3_cohort_failure import (
    FAILURE_CLOSEOUT_PATH,
    verify_failure_closeout,
)
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    facts,
    target_parts,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    PROTOCOL_PATH as V3_1_PROTOCOL_PATH,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import read_document, seal
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
from aletheia_lab.project.identity import content_sha256

VERSION = "claim-support-validation-v3.2/1"
PROTOCOL_PATH = "configs/evaluation/claim_support_validation_v3_2_protocol.json"
PREDECESSOR_CLOSEOUT_SHA256 = (
    "e62bd2c59c411a2df8dc29b45459c2c5d538674eac560584215c030d2da79d59"
)
SOURCE_ROLE = "deterministic_evaluator_projection_from_authenticated_visible_evidence"
ESTIMAND = "human_agreement_with_part_coverage_on_authentic_measurement_challenges"
FALSE_FLAGS = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "admitted_to_corpus": False,
    "qualification_execution_authorized": False,
    "relation_planning_unlocked": False,
    "relation_execution_authorized": False,
}


def _implementation_bindings(root: Path) -> dict[str, str]:
    paths = (
        "scripts/claim_support_validation_v3_2.py",
        "src/aletheia_lab/evaluation/claim_support_v3_2_role.py",
        "src/aletheia_lab/evaluation/claim_validation_v3_design.py",
        "src/aletheia_lab/evaluation/claim_support_v3_cohort_failure.py",
        V3_1_PROTOCOL_PATH,
        FAILURE_CLOSEOUT_PATH,
    )
    return {path: content_sha256((root / path).read_bytes()) for path in paths}


def _source_slot_identity(slot: dict[str, Any]) -> str:
    return digest(
        {
            "schema_version": VERSION,
            "source_measurement_role": SOURCE_ROLE,
            "predecessor_slot_sha256": slot["slot_sha256"],
            "source_binding_sha256": slot["source_binding_sha256"],
            "source_schedule": slot["source_schedule"],
        }
    )


def _source_instance_identity(source_slot: str, ordinal: int, claim: dict[str, Any]) -> str:
    return digest(
        {
            "schema_version": VERSION,
            "source_measurement_role": SOURCE_ROLE,
            "source_slot_sha256": source_slot,
            "claim_ordinal": ordinal,
            "claim_content_sha256": digest(claim),
        }
    )


def _assert_claim_is_visible(claim: dict[str, Any], context: ModelVisibleEvidenceContext) -> None:
    visible = facts(context)
    parts = target_parts(claim)
    if not parts or any(visible.get((evidence_id, pointer)) != value for evidence_id, pointer, value in parts):
        raise ValueError("deterministic source claim differs from authenticated visible evidence")


def _build_deterministic_source_census(design: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot in design["slots"]:
        context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(slot["context"]))
        source_slot = _source_slot_identity(slot)
        for ordinal, claim in enumerate(slot["expected"], 1):
            _assert_claim_is_visible(claim, context)
            rows.append(
                {
                    "source_slot_sha256": source_slot,
                    "source_instance_sha256": _source_instance_identity(
                        source_slot, ordinal, claim
                    ),
                    "predecessor_slot_sha256": slot["slot_sha256"],
                    "source_binding_sha256": slot["source_binding_sha256"],
                    "family_id": slot["source_schedule"]["family_id"],
                    "mechanism": slot["source_schedule"]["mechanism"],
                    "evidence_condition": slot["source_schedule"]["evidence_condition"],
                    "variant": slot["source_schedule"]["variant"],
                    "claim_ordinal": ordinal,
                    "claim": claim,
                }
            )
    if len(rows) != 720 or len({row["source_instance_sha256"] for row in rows}) != 720:
        raise ValueError("deterministic source census is incomplete or has identity collisions")
    return rows


def build_deterministic_source_census(root: Path) -> list[dict[str, Any]]:
    """Rebuild all source claims without reading a provider output or an outcome."""

    return _build_deterministic_source_census(build_design(root))


def _audit_source_census(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slot_counts = Counter(row["source_slot_sha256"] for row in rows)
    variants = Counter(row["variant"] for row in rows)
    conditions = Counter(row["evidence_condition"] for row in rows)
    mechanisms = Counter(row["mechanism"] for row in rows)
    claim_texts = {row["claim"]["claim_text"] for row in rows}
    expected_variants = {
        "A1": 90,
        "A2": 90,
        "A3": 90,
        "B0": 90,
        "B1": 90,
        "B2": 90,
        "CodeGraph": 90,
        "FULL": 90,
    }
    if (
        len(slot_counts) != 360
        or set(slot_counts.values()) != {2}
        or variants != expected_variants
        or conditions != {"full": 240, "missing_key": 240, "noisy": 240}
        or mechanisms
        != {"data_drift": 240, "preprocessing_mismatch": 240, "label_noise": 240}
        or len(claim_texts) != 328
    ):
        raise ValueError("deterministic source census balance differs from prospective design")
    return {
        "source_slot_count": len(slot_counts),
        "source_claim_instance_count": len(rows),
        "distinct_canonical_claim_text_count": len(claim_texts),
        "variant_instance_counts": dict(sorted(variants.items())),
        "condition_instance_counts": dict(sorted(conditions.items())),
        "mechanism_instance_counts": dict(sorted(mechanisms.items())),
    }


def build_protocol(root: Path) -> dict[str, Any]:
    """Build the outcome-transparent V3.2 role decision and frozen invariants."""

    predecessor = verify_failure_closeout(root)
    if predecessor["closeout_sha256"] != PREDECESSOR_CLOSEOUT_SHA256:
        raise ValueError("V3.2 requires the exact immutable V3.1 source failure closeout")
    design = build_design(root)
    rows = _build_deterministic_source_census(design)
    audit = _audit_source_census(rows)
    relation_census = design["relation_census"]
    if (
        design["relation_request_count"] != 240
        or design["capacity_assignment"]["counts"]
        != {"counter": 60, "withdrawal": 60, "partial": 60, "natural": 60}
    ):
        raise ValueError("preassigned relation capacity differs from the V3.1 freeze")
    return seal(
        {
            "schema_version": VERSION,
            "status": "v3_2_source_measurement_role_frozen",
            "predecessor_v3_1_closeout_sha256": predecessor["closeout_sha256"],
            "predecessor_v3_1_protocol_sha256": predecessor["protocol_sha256"],
            "predecessor_v3_1_design_sha256": predecessor["design_sha256"],
            "predecessor_v3_1_rerun_forbidden": True,
            "historical_provider_outputs_reused": False,
            "historical_accepted_sources_admitted": False,
            "scientific_amendment_after_development_observations": True,
            "estimand": ESTIMAND,
            "source_measurement_role": SOURCE_ROLE,
            "source_provider_request_count": 0,
            "source_provider_transcription_is_measured_construct": False,
            "source_values_derived_only_from_authenticated_visible_evidence": True,
            "source_target_allocation_reused_without_outcome_adaptation": True,
            "source_instance_identity_version_changed": True,
            "deterministic_source_census_sha256": digest(rows),
            **audit,
            "relation_assignment_reused_without_outcome_adaptation": True,
            "relation_assignment_census_sha256": digest(relation_census),
            "prospective_relation_request_count": len(relation_census),
            "relation_capacity_counts": design["capacity_assignment"]["counts"],
            "relation_provider_role_unchanged": True,
            "fresh_relation_qualification_required": True,
            "source_qualification_provider_request_count": 0,
            "relation_qualification_provider_request_count": 12,
            "qualification_requires_all_parsed_and_exact": True,
            "qualification_success_authorizes_planning_only": True,
            "variant_schedule_retained_for_provenance_only": True,
            "variant_superiority_claims_permitted": False,
            "failure_rate_generalization_permitted": False,
            "family_is_statistical_dependence_unit": True,
            "sample_target": 200,
            "automatic_label_quota": 50,
            "implementation_bindings": _implementation_bindings(root),
            "next_authorized_action": "implement_v3_2_relation_qualification_boundary",
            "provider_calls_executed": False,
            **FALSE_FLAGS,
        },
        "protocol_sha256",
    )


def verify_protocol(root: Path) -> dict[str, Any]:
    tracked = read_document(root / PROTOCOL_PATH, "protocol_sha256")
    expected = build_protocol(root)
    if tracked != expected:
        raise ValueError("tracked V3.2 source-measurement role protocol differs")
    return tracked


__all__ = [
    "ESTIMAND",
    "FALSE_FLAGS",
    "PREDECESSOR_CLOSEOUT_SHA256",
    "PROTOCOL_PATH",
    "SOURCE_ROLE",
    "VERSION",
    "build_deterministic_source_census",
    "build_protocol",
    "verify_protocol",
]

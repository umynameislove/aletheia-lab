"""Finite-frame transfer analysis; model and solver contributions stay separate."""

from __future__ import annotations

from collections import Counter
from typing import Any

from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    Decision,
    assess_decision,
    baseline_decision,
    visible_reference,
)
from aletheia_lab.evaluation.compositional_lineage_guard import (
    GuardMode,
    canonicalize_decision,
    guard_decision,
)
from aletheia_lab.evaluation.proof_aware_lineage_transfer_cases import (
    REPRESENTATIONS,
    validate_cases,
)

STAGES = ("raw", "action_canonicalized", "reject_only", "proof_guarded")
MODES: tuple[GuardMode, ...] = ("reject_only", "proof_guarded")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: sum(row[key] for row in rows) for key in rows[0] if key != "reference_state"}
    commitments = counts["commitment"]
    return {
        "planned_views": len(rows),
        **counts,
        "commitment_coverage": commitments / len(rows),
        "selective_unwarranted_risk": counts["unwarranted_commitment"] / commitments
        if commitments
        else None,
        "action_success_rate": counts["action_success"] / len(rows),
    }


def _paired(cases: list[dict[str, Any]], rows: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    pairs = []
    for case in cases[::2]:
        motif = case["motif"]
        ids = [f"{motif}:{representation}" for representation in REPRESENTATIONS]
        joint = {
            arm: all(rows[(case_id, arm)]["action_success"] for case_id in ids) for arm in POLICIES
        }
        pairs.append(
            {
                "motif": motif,
                "state": case["expected_state"],
                "dependent_format_views": 2,
                **joint,
                "a4_minus_a3_joint_success": int(joint[POLICIES[1]]) - int(joint[POLICIES[0]]),
            }
        )
    return pairs


def _stage_summaries(
    cases: list[dict[str, Any]],
    rows: dict[str, dict[tuple[str, str], Any]],
    decisions: dict[str, dict[tuple[str, str], Decision | None]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    summaries = {}
    format_checks = {}
    for stage, indexed in rows.items():
        arms = {}
        checks = {}
        for arm in POLICIES:
            selected = [indexed[(case["case_id"], arm)] for case in cases]
            arms[arm] = {
                **_summary(selected),
                "by_state": {
                    state: _summary([row for row in selected if row["reference_state"] == state])
                    for state in ("identified", "ambiguous", "conflict")
                },
                "by_representation": {
                    representation: _summary(
                        [
                            indexed[(c["case_id"], arm)]
                            for c in cases
                            if c["representation"] == representation
                        ]
                    )
                    for representation in REPRESENTATIONS
                },
            }
            pairs = [
                ((f"{case['motif']}:records", arm), (f"{case['motif']}:table", arm))
                for case in cases[::2]
            ]
            observed = [
                (left, right)
                for left, right in pairs
                if all(decisions[stage][key] is not None for key in (left, right))
            ]
            checks[arm] = {
                "planned_motif_pairs": 24,
                "both_assessable_pairs": len(observed),
                "same_decision_on_assessable_pairs": sum(
                    decisions[stage][left] == decisions[stage][right] for left, right in observed
                ),
                "action_success_discordant_pairs": sum(
                    indexed[left]["action_success"] != indexed[right]["action_success"]
                    for left, right in pairs
                ),
                "both_formats_action_success": sum(
                    indexed[left]["action_success"] and indexed[right]["action_success"]
                    for left, right in pairs
                ),
            }
        summaries[stage] = {"arms": arms, "paired_motif_transitions": _paired(cases, indexed)}
        format_checks[stage] = checks
    return summaries, format_checks


def analyze(
    cases: list[dict[str, Any]], proposals: dict[tuple[str, str], Decision | None]
) -> dict[str, Any]:
    """Require all planned observations, including explicit missing proposals.

    The fixed guard is never retuned here. Safe simple abstention is preserved:
    useful-query success can therefore increase for a worse raw proposal. Raw
    status correctness, boundedness and tool attribution remain visible.
    """
    validate_cases(cases)
    expected = {(case["case_id"], arm) for case in cases for arm in POLICIES}
    if proposals.keys() != expected:
        raise ValueError("analysis requires all planned case/arm slots exactly once")
    rows: dict[str, dict[tuple[str, str], Any]] = {stage: {} for stage in STAGES}
    decisions: dict[str, dict[tuple[str, str], Decision | None]] = {stage: {} for stage in STAGES}
    provenance = {mode: {arm: Counter[str]() for arm in POLICIES} for mode in MODES}
    assisted = {arm: Counter[str]() for arm in POLICIES}
    raw_status_correct = dict.fromkeys(POLICIES, 0)
    canonicalized = dict.fromkeys(POLICIES, 0)
    for case in cases:
        context = case["context"]
        reference = visible_reference(context)
        for arm in POLICIES:
            key = (case["case_id"], arm)
            proposal = proposals[key]
            normalized = canonicalize_decision(proposal)
            canonicalized[arm] += int(normalized != proposal)
            raw_status_correct[arm] += int(
                proposal is not None and reference["compatible"] == [proposal.decision]
            )
            selected = {"raw": proposal, "action_canonicalized": normalized}
            for mode in MODES:
                result = guard_decision(context, proposal, mode=mode)
                selected[mode] = result.decision
                provenance[mode][arm][result.origin] += 1
                if mode == "proof_guarded":
                    assessment = assess_decision(context, result.decision)
                    if assessment["action_success"]:
                        assisted[arm][result.origin] += 1
                    if (
                        result.origin == "model"
                        and result.decision is not None
                        and result.state == "ambiguous"
                        and result.decision.decision == "abstain"
                    ):
                        assisted[arm]["preserved_safe_simple_abstention"] += 1
            for stage, decision in selected.items():
                decisions[stage][key] = decision
                rows[stage][key] = assess_decision(context, decision)
    summaries, format_checks = _stage_summaries(cases, rows, decisions)
    baselines = {}
    for name in ("visible_resolver", "always_abstain", "symptom_only"):
        baselines[name] = _summary(
            [
                assess_decision(c["context"], baseline_decision(c["context"], baseline=name))
                for c in cases
            ]
        )
    return {
        "schema_version": "proof-aware-lineage-transfer-analysis/v1",
        "stages": summaries,
        "format_pair_checks": format_checks,
        "primary_within_arm_system_effects": {
            arm: {
                "planned_views": 48,
                "proof_guarded_minus_raw_action_success_rate": (
                    summaries["proof_guarded"]["arms"][arm]["action_success"]
                    - summaries["raw"]["arms"][arm]["action_success"]
                )
                / 48,
                "view_gains": sum(
                    rows["proof_guarded"][(c["case_id"], arm)]["action_success"]
                    and not rows["raw"][(c["case_id"], arm)]["action_success"]
                    for c in cases
                ),
                "view_losses": sum(
                    rows["raw"][(c["case_id"], arm)]["action_success"]
                    and not rows["proof_guarded"][(c["case_id"], arm)]["action_success"]
                    for c in cases
                ),
            }
            for arm in POLICIES
        },
        "deterministic_baselines": baselines,
        "raw_identified_status_correct_count": raw_status_correct,
        "action_canonicalization_count": canonicalized,
        "guard_provenance": {
            mode: {arm: dict(sorted(counts.items())) for arm, counts in arms.items()}
            for mode, arms in provenance.items()
        },
        "proof_guarded_success_origin_and_preserved_abstention": {
            arm: dict(sorted(counts.items())) for arm, counts in assisted.items()
        },
        "statistical_unit": "24 authored motifs; 48 dependent format views per arm; finite census, no population CI",
        "stage_regimes_are_independent_model_experiments": False,
        "tool_generated_success_is_model_reasoning": False,
        "guarded_query_success_is_monotone_in_raw_quality": False,
        "interpretation": "bounded typed-grammar development transfer; report raw safety/coverage/proof and assistance separately, not causal loss diagnosis or general deployment reliability",
        "protected_predictions_used": False,
        "mechanism_admitted": False,
    }

"""Fixed-dose M5 cells; fit/calibrate separately and retain failed rivals."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    mapping_observation,
    target_binding_rival_observation,
    zero_dose_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    apply_evaluator_mapping_fault,
)
from aletheia_lab.benchmark.p2.score_mapping_new_source_wire import (
    audit_reader_pair,
    capture_reader_wire,
)
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    match_target_swaps,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import verify_evaluator_mapping
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    apply_paired_target_binding_fault,
    restore_target_bindings,
)
from aletheia_lab.benchmark.p2.target_binding_prospective import _fit_cell
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
)
from aletheia_lab.benchmark.p2.target_binding_verification import verify_paired_target_binding


def _mapping_control(inputs: dict[str, Any], dose: int) -> tuple[Any, Any]:
    if type(dose) is not int or dose not in (0, 1):
        raise ProspectiveBindingError("prospective M5 allows only sham 0 and frozen dose 1")
    intervention = apply_evaluator_mapping_fault(
        inputs["score_source"], selected_shard_count=cast(Literal[0, 1], dose)
    )
    checked = verify_evaluator_mapping(
        witness=inputs["witness"],
        source=inputs["score_source"],
        intervention=intervention,
        scoring_target_rows=inputs["witness"].target_rows,
        reference_model=inputs["reference_model"],
        evaluation_matrix=inputs["evaluation_matrix"],
        reference_calibration=inputs["reference_calibration"],
        artifacts=inputs["artifacts"],
    )
    return intervention, checked


def _observation_args(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "witness": inputs["witness"],
        "source": inputs["score_source"],
        "reference_features": inputs["reference_features"],
        "evaluation_features": inputs["evaluation_matrix"],
    }


def _rival(root: Path, inputs: dict[str, Any], active: Any, checked: Any) -> dict[str, Any]:
    if checked.faulty_log_loss <= checked.healthy_log_loss:
        return {"match_status": "nonpositive_mapping_effect", "g2_pass": False}
    if len(checked.affected_record_ids) < 2 or not checked.changed_score_record_ids:
        return {"match_status": "insufficient_mapping_footprint", "g2_pass": False}
    witness = inputs["witness"]
    match_args = {
        "record_ids": witness.record_ids,
        "targets": tuple(label for _, label in witness.target_rows),
        "probabilities": tuple(row[1] for row in witness.calibrated_score_rows),
        "mapping_log_loss": checked.faulty_log_loss,
        "max_changed_targets": len(checked.affected_record_ids),
    }
    # A frozen, outcome-conditioned construction, not target-blind sampling.
    matched = match_target_swaps(**match_args)
    verify_target_swap_match(**match_args, result=matched)
    paired = apply_paired_target_binding_fault(
        inputs["target_source"],
        swapped_pairs=matched.swapped_pairs,
    )
    target_args = {key: value for key, value in inputs.items() if key != "reference_features"}
    target_checked = verify_paired_target_binding(
        **target_args,
        intervention=paired,
        corrected_target_rows=restore_target_bindings(inputs["target_source"], paired),
    )
    if paired.observed_target_rows != matched.scoring_target_rows:
        raise ProspectiveBindingError("rival targets differ from the independent donor ledger")
    result: dict[str, Any] = {
        "match_status": "unmatched",
        "g2_pass": False,
        "swapped_pairs": matched.swapped_pairs,
        "paired_target_verification": asdict(target_checked),
        "absolute_loss_gap": matched.absolute_loss_gap,
    }
    if not matched.swapped_pairs:
        return result
    observation = mapping_observation(
        **_observation_args(inputs),
        intervention=active,
        scoring_target_rows=witness.target_rows,
        reference_model=inputs["reference_model"],
        reference_calibration=inputs["reference_calibration"],
        artifacts=inputs["artifacts"],
    )
    rival = target_binding_rival_observation(
        **_observation_args(inputs),
        scoring_target_rows=paired.observed_target_rows,
    )
    audit = audit_reader_pair(root, observation, rival)
    g2 = (
        matched.resolution_matched
        and audit["wire_equal"]["missing_key"]
        and not audit["wire_equal"]["full"]
    )
    result.update(
        match_status="resolution_matched" if g2 else "unmatched",
        g2_pass=g2,
        input_channel_audit=audit,
    )
    return result


def run_cell(root: Path, data: ParsedSource, kind: str, cell_dir: Path) -> dict[str, Any]:
    base = {
        "cell_id": f"{data.spec.dataset_id}/{kind}",
        "dataset_id": data.spec.dataset_id,
        "model_kind": kind,
    }
    if any({data.targets[index] for index in rows} != {0, 1} for rows in data.partitions.values()):
        return {**base, "status": "ineligible_class_partition", "g1_pass": False, "g2_pass": False}
    # Reuse the separate-calibration helper, never the old development _cell.
    inputs = _fit_cell(data, kind, cell_dir)
    _, sham = _mapping_control(inputs, 0)
    active, checked = _mapping_control(inputs, 1)
    common = {
        **_observation_args(inputs),
        "reference_model": inputs["reference_model"],
        "reference_calibration": inputs["reference_calibration"],
        "artifacts": inputs["artifacts"],
    }
    zero = zero_dose_observation(**common)
    sham_pass = (
        sham.faulty_log_loss == sham.healthy_log_loss == sham.corrected_log_loss
        and not sham.changed_score_record_ids
        and zero.evaluator_classes == zero.model_classes
    )
    correction_pass = checked.corrected_log_loss == checked.healthy_log_loss
    full_witness = None
    if checked.changed_score_record_ids:
        observed = mapping_observation(
            **common,
            intervention=active,
            scoring_target_rows=inputs["witness"].target_rows,
        )
        full_witness = capture_reader_wire(root, observed, "full")
    effect = checked.faulty_log_loss - checked.healthy_log_loss
    g1 = bool(sham_pass and correction_pass and full_witness and effect >= 0.01)
    return {
        **base,
        "status": "verified",
        "final_count": len(inputs["witness"].record_ids),
        "source_scores_unchanged_verified": True,
        "sham_verification": asdict(sham),
        "mapping_verification": asdict(checked),
        "sham_pass": sham_pass,
        "correction_exact": correction_pass,
        "mapping_full_wire": full_witness,
        "absolute_effect": effect,
        "effect_direction": "positive" if effect > 0 else "negative" if effect < 0 else "flat",
        "g1_pass": g1,
        **_rival(root, inputs, active, checked),
    }

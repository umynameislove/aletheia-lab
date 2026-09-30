"""Replay source fitting and control ledgers without loading model pickles.

No mapping injector or greedy search is run during verification. The trusted
source recipe independently regenerates model scores and calibration, then
the runtime scorer consumes the retained intervention/target ledgers.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import ModelKind, fit_logit_calibration
from aletheia_lab.benchmark.p2.score_mapping_development import _fit_reference_model
from aletheia_lab.benchmark.p2.score_mapping_evidence import _observation
from aletheia_lab.benchmark.p2.score_mapping_new_source_wire import (
    audit_reader_pair,
    capture_reader_wire,
)
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    MAX_ABSOLUTE_LOSS_GAP,
    TargetSwapMatch,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_verify import (
    _mapping_measurement,
    _require_equal,
    _source_witness,
    _target_measurement,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256


def _replay_model(
    data: ParsedSource, kind: ModelKind, cell_dir: Path
) -> tuple[Any, dict[str, Any]]:
    witness, matrices = _source_witness(data, cell_dir)
    train_targets = tuple(data.targets[index] for index in data.partitions["train"])
    model = _fit_reference_model(kind, matrices["train"], train_targets)
    retained = io.BytesIO()
    joblib.dump(model, retained)  # Serialize our fresh model; never joblib.load supplied input.
    _require_equal(
        content_sha256(retained.getvalue()), file_sha256(cell_dir / "fitted_model.joblib")
    )
    raw = model.predict_proba(matrices["final"])
    _require_equal(witness.raw_score_rows, raw.tolist())
    calibration = fit_logit_calibration(
        model.predict_proba(matrices["calibration"])[:, 1].tolist(),
        tuple(data.targets[index] for index in data.partitions["calibration"]),
        probability_clip=1e-12,
        max_iter=200,
        tolerance=1e-9,
    )
    _require_equal(calibration.canonical_sha256(), witness.calibration_sha256)
    return witness, matrices


def _observed(witness: Any, matrices: dict[str, Any], mapping: dict[str, Any], faulty: Any) -> Any:
    index = witness.record_ids.index(mapping["changed_score_record_ids"][0])
    return _observation(
        witness,
        reference_log_loss=mapping["healthy_log_loss"],
        reference_features=matrices["train"],
        evaluation_features=matrices["final"],
        observed_log_loss=mapping["faulty_log_loss"],
        evaluator_classes=(1, 0),
        example_index=index,
        example_observed_positive=faulty[index],
        target_binding_matches_source=True,
        corrected_log_loss=mapping["healthy_log_loss"],
    )


def _verify_rival(
    root: Path, witness: Any, matrices: Any, result: Any, mapping: Any, faulty: Any
) -> None:
    if mapping["faulty_log_loss"] <= mapping["healthy_log_loss"]:
        _require_equal(result["match_status"], "nonpositive_mapping_effect")
        _require_equal(result["g2_pass"], False)
        return
    if len(mapping["affected_record_ids"]) < 2 or not mapping["changed_score_record_ids"]:
        _require_equal(result["match_status"], "insufficient_mapping_footprint")
        _require_equal(result["g2_pass"], False)
        return
    ids, labels = witness.record_ids, dict(witness.target_rows)
    donors = dict(zip(ids, ids, strict=True))
    pairs = tuple(tuple(pair) for pair in result["swapped_pairs"])
    for left, right in pairs:
        donors[left], donors[right] = right, left
    observed = tuple((row_id, labels[donors[row_id]]) for row_id in ids)
    measurement = _target_measurement(witness, [donors[row_id] for row_id in ids])
    _require_equal(result["paired_target_verification"], measurement)
    loss, mapping_loss = measurement["faulty_log_loss"], mapping["faulty_log_loss"]
    gap = abs(loss - mapping_loss)
    matched = (
        bool(pairs) and gap <= MAX_ABSOLUTE_LOSS_GAP and round(loss, 6) == round(mapping_loss, 6)
    )
    verify_target_swap_match(
        record_ids=ids,
        targets=tuple(labels.values()),
        probabilities=tuple(row[1] for row in witness.calibrated_score_rows),
        mapping_log_loss=mapping_loss,
        max_changed_targets=len(mapping["affected_record_ids"]),
        result=TargetSwapMatch(observed, pairs, loss, gap, matched),
    )
    _require_equal(result["absolute_loss_gap"], gap)
    audit = None
    if pairs:
        index = next(
            index for index, row in enumerate(observed) if row != witness.target_rows[index]
        )
        rival = _observation(
            witness,
            reference_log_loss=mapping["healthy_log_loss"],
            reference_features=matrices["train"],
            evaluation_features=matrices["final"],
            observed_log_loss=loss,
            evaluator_classes=(0, 1),
            example_index=index,
            example_observed_positive=witness.calibrated_score_rows[index][1],
            target_binding_matches_source=False,
            corrected_log_loss=loss,
        )
        audit = audit_reader_pair(root, _observed(witness, matrices, mapping, faulty), rival)
        _require_equal(result["input_channel_audit"], audit)
    g2 = bool(
        matched and audit and audit["wire_equal"]["missing_key"] and not audit["wire_equal"]["full"]
    )
    _require_equal(result["g2_pass"], g2)
    _require_equal(result["match_status"], "resolution_matched" if g2 else "unmatched")


def verify_cell(
    root: Path, data: ParsedSource, kind: ModelKind, cell_dir: Path, result: Any
) -> None:
    base = {
        "cell_id": f"{data.spec.dataset_id}/{kind}",
        "dataset_id": data.spec.dataset_id,
        "model_kind": kind,
    }
    for key, value in base.items():
        _require_equal(result[key], value)
    if result["status"] == "ineligible_class_partition":
        if not any(
            {data.targets[index] for index in indices} != {0, 1}
            for indices in data.partitions.values()
        ):
            raise ProspectiveBindingError("eligible source was silently excluded")
        _require_equal(result["g1_pass"], False)
        _require_equal(result["g2_pass"], False)
        return
    if result["status"] == "runtime_failure":
        if not isinstance(result.get("error_type"), str) or result["g1_pass"] or result["g2_pass"]:
            raise ProspectiveBindingError("runtime failure cannot be a scientific pass")
        return
    if result["status"] != "verified":
        raise ProspectiveBindingError("unknown cell disposition")
    witness, matrices = _replay_model(data, kind, cell_dir)
    mapping, faulty = _mapping_measurement(witness)
    _require_equal(result["mapping_verification"], mapping)
    sham = {
        **mapping,
        "affected_record_ids": (),
        "changed_score_record_ids": (),
        "nominal_shard_fraction": 0.0,
        "achieved_affected_fraction": 0.0,
        "faulty_log_loss": mapping["healthy_log_loss"],
    }
    _require_equal(result["sham_verification"], sham)
    _require_equal(result["final_count"], len(witness.record_ids))
    _require_equal(result["source_scores_unchanged_verified"], True)
    _require_equal(result["sham_pass"], True)
    _require_equal(result["correction_exact"], True)
    wire = None
    if mapping["changed_score_record_ids"]:
        wire = capture_reader_wire(root, _observed(witness, matrices, mapping, faulty), "full")
    _require_equal(result["mapping_full_wire"], wire)
    effect = mapping["faulty_log_loss"] - mapping["healthy_log_loss"]
    _require_equal(result["absolute_effect"], effect)
    _require_equal(result["g1_pass"], bool(wire and effect >= 0.01))
    _require_equal(
        result["effect_direction"],
        "positive" if effect > 0 else "negative" if effect < 0 else "flat",
    )
    _verify_rival(root, witness, matrices, result, mapping, faulty)

"""Replay final stored scores and donor joins without a refit, injector, or search."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    apply_logit_calibration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import reference_prior_standardized_log_loss
from aletheia_lab.benchmark.p2.score_mapping_evidence import _observation
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    MAX_ABSOLUTE_LOSS_GAP,
    TargetSwapMatch,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    IndependentScoreWitness,
    MappingVerification,
    SourceArtifactHashes,
    _feature_matrix_sha256,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    json_bytes,
)
from aletheia_lab.benchmark.p2.target_binding_verification import TargetBindingVerification
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.score_mapping_reader import build_score_mapping_reader_context
from aletheia_lab.project.identity import content_sha256


def _require_equal(actual: Any, expected: Any) -> None:
    if json_bytes(actual) != json_bytes(expected):
        raise ProspectiveBindingError(
            "stored control differs from independent source/metric replay"
        )


def _source_witness(
    data: ParsedSource, cell_dir: Path
) -> tuple[IndependentScoreWitness, dict[str, Any]]:
    raw = json.loads((cell_dir / "source-witness.json").read_bytes())
    hashes = SourceArtifactHashes(**raw.pop("artifact_hashes"))
    for key in ("record_ids", "model_classes"):
        raw[key] = tuple(raw[key])
    for key in ("target_rows", "raw_score_rows", "calibrated_score_rows"):
        raw[key] = tuple(tuple(row) for row in raw[key])
    witness = IndependentScoreWitness(artifact_hashes=hashes, **raw)
    _require_equal(witness.dataset_id, data.spec.dataset_id)
    _require_equal(
        witness.record_ids, [data.record_ids[index] for index in data.partitions["final"]]
    )
    _require_equal(
        witness.target_rows,
        [(data.record_ids[index], data.targets[index]) for index in data.partitions["final"]],
    )
    _require_equal(witness.model_classes, (0, 1))
    _require_equal(
        asdict(hashes),
        {
            "dataset": data.spec.archive_sha256,
            "split": file_sha256(cell_dir / "split.json"),
            "preprocessor": file_sha256(cell_dir / "preprocessor.json"),
            "fitted_model": file_sha256(cell_dir / "fitted_model.joblib"),
        },
    )
    _require_equal(json.loads((cell_dir / "split.json").read_bytes()), data.audit())
    calibration = CalibrationResult.model_validate_json(
        (cell_dir / "calibration.json").read_bytes()
    )
    _require_equal(calibration.development_record_count, len(data.partitions["calibration"]))
    _require_equal(witness.calibration_sha256, calibration.canonical_sha256())
    scores = np.asarray(witness.raw_score_rows)
    if (
        scores.shape != (len(witness.record_ids), 2)
        or not np.isfinite(scores).all()
        or np.any(scores < 0)
        or np.any(scores > 1)
        or not np.allclose(scores.sum(axis=1), 1, rtol=0, atol=1e-12)
    ):
        raise ProspectiveBindingError("stored score source is invalid")
    positive = apply_logit_calibration(scores[:, 1].tolist(), calibration)
    _require_equal(witness.calibrated_score_rows, [(1 - value, value) for value in positive])
    for key, value in (
        ("raw_scores_sha256", {"record_ids": witness.record_ids, "scores": witness.raw_score_rows}),
        (
            "calibrated_scores_sha256",
            {"record_ids": witness.record_ids, "scores": witness.calibrated_score_rows},
        ),
        ("target_binding_sha256", {"target_rows": witness.target_rows}),
    ):
        _require_equal(getattr(witness, key), canonical_sha256(value))
    state = json.loads((cell_dir / "preprocessor.json").read_bytes())
    _require_equal(state["fit_partition"], "train")
    train = data.features[list(data.partitions["train"])]
    scaler = StandardScaler().fit(train)
    _require_equal(state["mean"], scaler.mean_.tolist())
    _require_equal(state["scale"], scaler.scale_.tolist())
    matrices = {
        name: (data.features[list(indices)] - state["mean"]) / state["scale"]
        for name, indices in data.partitions.items()
    }
    _require_equal(
        witness.feature_matrix_sha256, _feature_matrix_sha256(witness.record_ids, matrices["final"])
    )
    return witness, matrices


def _target_measurement(witness: IndependentScoreWitness, donors: list[str]) -> dict[str, Any]:
    ids = witness.record_ids
    labels = dict(witness.target_rows)
    if len(donors) != len(ids) or set(donors) != set(ids):
        raise ProspectiveBindingError("target donors are not a source permutation")
    observed = tuple((row_id, labels[donor]) for row_id, donor in zip(ids, donors, strict=True))
    scores = tuple(row[1] for row in witness.calibrated_score_rows)
    healthy = reference_prior_standardized_log_loss(
        true_labels=tuple(labels.values()), probabilities=scores
    )
    faulty = reference_prior_standardized_log_loss(
        true_labels=tuple(label for _, label in observed), probabilities=scores
    )
    return asdict(
        TargetBindingVerification(
            healthy,
            faulty,
            healthy,
            sum(labels[row_id] != labels[donor] for row_id, donor in zip(ids, donors, strict=True)),
            sum(row_id != donor for row_id, donor in zip(ids, donors, strict=True)),
            True,
            canonical_sha256({"target_rows": witness.target_rows}),
            canonical_sha256({"target_rows": observed}),
            canonical_sha256({"target_rows": witness.target_rows}),
            witness.calibrated_scores_sha256,
        )
    )


def _verify_cyclic(witness: IndependentScoreWitness, result: dict[str, Any]) -> None:
    controls = result["cyclic_controls"]
    _require_equal([control["dose"] for control in controls], [0, 1])
    ids = witness.record_ids
    keys = {
        row_id: content_sha256(
            f"target-binding-development-v1\0{witness.dataset_id}\0{row_id}".encode()
        )
        for row_id in ids
    }
    labels = dict(witness.target_rows)
    for control in controls:
        selected = [row_id for row_id in ids if int(keys[row_id], 16) % 20 < control["dose"]]
        ordered = sorted(selected, key=lambda row_id: (keys[row_id], row_id))
        donor_map = dict(zip(ordered, ordered[1:] + ordered[:1], strict=True))
        donors = [donor_map.get(row_id, row_id) for row_id in ids]
        measurement = _target_measurement(witness, donors)
        effect = measurement["faulty_log_loss"] - measurement["healthy_log_loss"]
        expected = {
            "dose": control["dose"],
            "verification": measurement,
            "donor_record_ids": donors,
            "selected_record_ids": selected,
            "changed_record_ids": [
                row_id
                for row_id, donor in zip(ids, donors, strict=True)
                if labels[row_id] != labels[donor]
            ],
            "effect_direction": "positive" if effect > 0 else "negative" if effect < 0 else "flat",
        }
        _require_equal(control, expected)


def _mapping_measurement(
    witness: IndependentScoreWitness,
) -> tuple[dict[str, Any], tuple[float, ...]]:
    ids = witness.record_ids
    shards = tuple(
        int(content_sha256(f"m5-dev-v1-2026-09-25\0{witness.dataset_id}\0{row_id}".encode()), 16)
        % 20
        for row_id in ids
    )
    scores = tuple(row[1] for row in witness.calibrated_score_rows)
    faulty = tuple(
        row[0] if shard < 1 else row[1]
        for row, shard in zip(witness.calibrated_score_rows, shards, strict=True)
    )
    labels = tuple(label for _, label in witness.target_rows)
    healthy_loss = reference_prior_standardized_log_loss(true_labels=labels, probabilities=scores)
    affected = tuple(row_id for row_id, shard in zip(ids, shards, strict=True) if shard < 1)
    changed = tuple(
        row_id for row_id, before, after in zip(ids, scores, faulty, strict=True) if before != after
    )
    return asdict(
        MappingVerification(
            witness.artifact_hashes,
            witness.raw_scores_sha256,
            witness.calibrated_scores_sha256,
            witness.target_binding_sha256,
            affected,
            changed,
            1 / 20,
            len(affected) / len(ids),
            healthy_loss,
            reference_prior_standardized_log_loss(true_labels=labels, probabilities=faulty),
            healthy_loss,
        )
    ), faulty


def _verify_pair(
    witness: IndependentScoreWitness,
    matrices: dict[str, Any],
    result: dict[str, Any],
    mapping: dict[str, Any],
    faulty: tuple[float, ...],
) -> None:
    ids, labels = witness.record_ids, dict(witness.target_rows)
    donors = dict(zip(ids, ids, strict=True))
    pairs = tuple(tuple(pair) for pair in result["swapped_pairs"])
    for left, right in pairs:
        donors[left], donors[right] = right, left
    observed = tuple((row_id, labels[donors[row_id]]) for row_id in ids)
    measurement = _target_measurement(witness, [donors[row_id] for row_id in ids])
    _require_equal(result["paired_target_verification"], measurement)
    loss = measurement["faulty_log_loss"]
    gap = abs(loss - mapping["faulty_log_loss"])
    matched = (
        bool(pairs)
        and gap <= MAX_ABSOLUTE_LOSS_GAP
        and round(loss, 6) == round(mapping["faulty_log_loss"], 6)
    )
    verify_target_swap_match(
        record_ids=ids,
        targets=tuple(labels.values()),
        probabilities=tuple(row[1] for row in witness.calibrated_score_rows),
        mapping_log_loss=mapping["faulty_log_loss"],
        max_changed_targets=len(mapping["affected_record_ids"]),
        result=TargetSwapMatch(observed, pairs, loss, gap, matched),
    )
    _require_equal(result["absolute_loss_gap"], gap)
    _require_equal(result["match_status"], "resolution_matched" if matched else "unmatched")
    if not matched:
        if "reader_payloads" in result or "reader_equality" in result:
            raise ProspectiveBindingError("unmatched cell must not furnish a matched reader pair")
        return
    common = {
        "reference_log_loss": mapping["healthy_log_loss"],
        "reference_features": matrices["train"],
        "evaluation_features": matrices["final"],
    }
    index = ids.index(mapping["changed_score_record_ids"][0])
    mapping_observed = _observation(
        witness,
        **common,
        observed_log_loss=mapping["faulty_log_loss"],
        evaluator_classes=(1, 0),
        example_index=index,
        example_observed_positive=faulty[index],
        target_binding_matches_source=True,
        corrected_log_loss=mapping["healthy_log_loss"],
    )
    index = next(i for i, row in enumerate(observed) if row != witness.target_rows[i])
    target_observed = _observation(
        witness,
        **common,
        observed_log_loss=loss,
        evaluator_classes=(0, 1),
        example_index=index,
        example_observed_positive=witness.calibrated_score_rows[index][1],
        target_binding_matches_source=False,
        corrected_log_loss=loss,
    )
    expected = {
        view: [
            build_score_mapping_reader_context(item, condition=view).model_payload()
            for item in (mapping_observed, target_observed)
        ]
        for view in ("full", "missing_key", "noisy", "misleading")
    }
    _require_equal(result["reader_payloads"], expected)
    equality = {view: payloads[0] == payloads[1] for view, payloads in expected.items()}
    _require_equal(result["reader_equality"], equality)
    if not equality["missing_key"] or equality["full"]:
        raise ProspectiveBindingError("reader projection fails its predeclared boundary")


def verify_cell(data: ParsedSource, cell_dir: Path, result: dict[str, Any]) -> None:
    ineligible = any(
        {data.targets[index] for index in indices} != {0, 1} for indices in data.partitions.values()
    )
    if result["status"] == "ineligible_class_partition":
        if not ineligible:
            raise ProspectiveBindingError("eligible source was silently excluded")
        return
    if result["status"] == "runtime_failure":
        if not isinstance(result.get("error_type"), str):
            raise ProspectiveBindingError("runtime failure is missing its disposition")
        return  # A failure receipt is not a scientific success.
    if result["status"] != "verified" or ineligible:
        raise ProspectiveBindingError("unknown cell status or ineligible success")
    witness, matrices = _source_witness(data, cell_dir)
    _require_equal(result["final_count"], len(witness.record_ids))
    _require_equal(result["sham_verified"], True)
    _verify_cyclic(witness, result)
    mapping, faulty = _mapping_measurement(witness)
    _require_equal(result["mapping_verification"], mapping)
    if mapping["faulty_log_loss"] <= mapping["healthy_log_loss"]:
        _require_equal(result["match_status"], "nonpositive_mapping_effect")
    elif len(mapping["affected_record_ids"]) < 2 or not mapping["changed_score_record_ids"]:
        _require_equal(result["match_status"], "insufficient_mapping_footprint")
    else:
        _verify_pair(witness, matrices, result, mapping, faulty)

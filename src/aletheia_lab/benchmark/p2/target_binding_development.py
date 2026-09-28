"""Offline target-binding development on the existing pinned score sources."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import reconstruct_runtime_split
from aletheia_lab.benchmark.p2.score_mapping_development import (
    DOSES,
    MODEL_KINDS,
    _private_output_guard,
    _write_json,
)
from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import (
    DevelopmentCellReplay,
    _checked_prior_summary,
    load_development_cell,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    mapping_observation,
    serialize_development_evidence_view,
    target_binding_rival_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import apply_evaluator_mapping_fault
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    TargetBindingSource,
    TargetDose,
    apply_paired_target_binding_fault,
    apply_target_binding_fault,
    restore_target_bindings,
)
from aletheia_lab.benchmark.p2.target_binding_verification import (
    verify_paired_target_binding,
    verify_target_binding,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256


def _checked_dose_census(cell: dict[str, Any]) -> None:
    doses = [item["selected_shards"] for item in cell["measurements"]]
    if len(doses) != len(DOSES) or set(doses) != set(DOSES):
        raise ValueError("matched predecessor omits or repeats a development dose")


def _matched_cells(
    *, root: Path, output: Path, prior: Path | None, expected_sha256: str | None
) -> tuple[dict[tuple[str, str], dict[str, Any]], str | None]:
    if prior is None:
        if expected_sha256 is not None:
            raise ValueError("matched predecessor hash requires its private path")
        return {}, None
    if prior.is_symlink() or prior.resolve().is_relative_to(root) or expected_sha256 is None:
        raise ValueError("matched predecessor must be private, immutable and hash-confirmed")
    prior = prior.resolve(strict=True)
    if output.resolve().is_relative_to(prior) or prior.is_relative_to(output.resolve()):
        raise ValueError("matched predecessor and output must not overlap")
    path = prior / "summary.json"
    if path.is_symlink():
        raise ValueError("matched summary must not be a symlink")
    digest = file_sha256(path)
    if digest != expected_sha256:
        raise ValueError("matched predecessor byte hash differs")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("schema_version") != "score-mapping-development-symptom-study/v1":
        raise ValueError("matched predecessor has an unknown schema")
    cells = {(cell["dataset_id"], cell["model_kind"]): cell for cell in summary["cells"]}
    if len(cells) != len(summary["cells"]):
        raise ValueError("matched predecessor repeats a source/estimator cell")
    for cell in cells.values():
        _checked_dose_census(cell)
    return cells, digest


def _paired_measurement(
    *,
    replay: DevelopmentCellReplay,
    source: TargetBindingSource,
    item: dict[str, Any],
    dose: TargetDose,
) -> dict[str, Any]:
    if item["status"] != "resolution_matched_pair":
        raise ValueError("matched predecessor is not a complete adversarial pair")
    paired = apply_paired_target_binding_fault(
        source, swapped_pairs=tuple(tuple(pair) for pair in item["swapped_pairs"])
    )
    checked = verify_paired_target_binding(
        witness=replay.witness,
        score_source=replay.source,
        target_source=source,
        intervention=paired,
        corrected_target_rows=restore_target_bindings(source, paired),
        reference_model=replay.model,
        evaluation_matrix=replay.development,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
    )
    if (
        checked.faulty_log_loss != item["rival_log_loss"]
        or canonical_sha256({"target_rows": paired.observed_target_rows})
        != item["rival_target_binding_sha256"]
    ):
        raise ValueError("adversarial target binding does not replay predecessor")
    mapping = apply_evaluator_mapping_fault(replay.source, selected_shard_count=dose)
    mapping_view = mapping_observation(
        witness=replay.witness,
        source=replay.source,
        intervention=mapping,
        scoring_target_rows=replay.target_rows,
        reference_model=replay.model,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    if mapping_view.observed_log_loss != item["mapping_log_loss"] or len(
        paired.changed_record_ids
    ) > len(mapping.affected_record_ids):
        raise ValueError("mapping symptom or adversarial footprint differs from predecessor")
    paired_view = target_binding_rival_observation(
        witness=replay.witness,
        source=replay.source,
        scoring_target_rows=paired.observed_target_rows,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    equality = {
        str(decimals): {
            condition: serialize_development_evidence_view(
                mapping_view, condition=condition, metric_decimal_places=decimals
            )
            == serialize_development_evidence_view(
                paired_view, condition=condition, metric_decimal_places=decimals
            )
            for condition in ("full", "missing_key")
        }
        for decimals in (6, 12)
    }
    return {
        "selection_uses_development_mapping_metric": True,
        "pair_count": len(paired.swapped_pairs),
        "changed_target_count": checked.changed_target_count,
        "class_counts_preserved": checked.class_counts_preserved,
        "correction_exact": checked.corrected_log_loss == checked.healthy_log_loss,
        "absolute_loss_gap": abs(checked.faulty_log_loss - mapping_view.observed_log_loss),
        "payload_equality": equality,
    }


def _cyclic_measurement(
    *, replay: DevelopmentCellReplay, target_source: TargetBindingSource, dose: TargetDose
) -> dict[str, Any]:
    fault = apply_target_binding_fault(target_source, selected_shard_count=dose)
    corrected = restore_target_bindings(target_source, fault)
    checked = verify_target_binding(
        witness=replay.witness,
        score_source=replay.source,
        target_source=target_source,
        intervention=fault,
        corrected_target_rows=corrected,
        reference_model=replay.model,
        evaluation_matrix=replay.development,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
    )
    measurement: dict[str, Any] = asdict(checked)
    delta = checked.faulty_log_loss - checked.healthy_log_loss
    measurement.update(
        {
            "selected_shards": dose,
            "selected_rows": len(fault.selected_record_ids),
            "delta_log_loss": delta,
            "effect_direction": "positive" if delta > 0 else "negative" if delta < 0 else "flat",
        }
    )
    if fault.changed_record_ids:
        target_observation = target_binding_rival_observation(
            witness=replay.witness,
            source=replay.source,
            scoring_target_rows=fault.observed_target_rows,
            reference_features=replay.train,
            evaluation_features=replay.development,
        )
        mapping = apply_evaluator_mapping_fault(replay.source, selected_shard_count=dose)
        mapping_view = mapping_observation(
            witness=replay.witness,
            source=replay.source,
            intervention=mapping,
            scoring_target_rows=replay.target_rows,
            reference_model=replay.model,
            reference_calibration=replay.calibration,
            artifacts=replay.artifacts,
            reference_features=replay.train,
            evaluation_features=replay.development,
        )
        comparisons = {}
        for decimals in (6, 12):
            comparisons[str(decimals)] = {
                condition: serialize_development_evidence_view(
                    target_observation,
                    condition=condition,
                    metric_decimal_places=decimals,
                )
                == serialize_development_evidence_view(
                    mapping_view, condition=condition, metric_decimal_places=decimals
                )
                for condition in ("full", "missing_key", "noisy", "misleading")
            }
        measurement["mapping_pair_payload_equality"] = comparisons
        measurement["view_sha256"] = {
            condition: content_sha256(
                serialize_development_evidence_view(
                    target_observation, condition=condition, metric_decimal_places=6
                )
            )
            for condition in ("full", "missing_key", "noisy", "misleading")
        }
    return measurement


def run_target_binding_development(
    *,
    root: Path,
    prior: Path,
    output: Path,
    matched_prior: Path | None = None,
    expected_matched_sha256: str | None = None,
) -> dict[str, Any]:
    """Retain all target-only doses, including flat, negative, and leaking pairs."""

    _private_output_guard(root, output)
    root = root.resolve(strict=True)
    if prior.is_symlink() or prior.resolve().is_relative_to(root):
        raise ValueError("development predecessor must be private and non-symlink")
    prior = prior.resolve(strict=True)
    if output.resolve().is_relative_to(prior) or prior.is_relative_to(output.resolve()):
        raise ValueError("output must not overlap the immutable predecessor")
    predecessor = _checked_prior_summary(prior)
    matched_cells, matched_sha256 = _matched_cells(
        root=root, output=output, prior=matched_prior, expected_sha256=expected_matched_sha256
    )
    protocol_path = root / "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, _ = verify_v3_protocol_artifacts(protocol, root=root)
    if protocol.canonical_sha256() != predecessor["protocol_sha256"]:
        raise ValueError("development protocol differs from pinned predecessor")
    if matched_prior is not None and set(matched_cells) != {
        (dataset.dataset_id, kind) for dataset in manifest.datasets for kind in MODEL_KINDS
    }:
        raise ValueError("matched predecessor has a different source/estimator census")
    cells: list[dict[str, Any]] = []
    for dataset, receipt in zip(manifest.datasets, protocol.dataset_splits, strict=True):
        archive = root / "data/raw/p2-v3" / dataset.archive.file_name
        _, frame = load_v3_dataset_snapshot_for_registration(dataset=dataset, archive_path=archive)
        split = reconstruct_runtime_split(
            protocol=protocol, dataset=dataset, frame=frame, receipt=receipt
        )
        for kind in MODEL_KINDS:
            previous = next(
                cell
                for cell in predecessor["cells"]
                if cell["dataset_id"] == dataset.dataset_id and cell["model_kind"] == kind
            )
            replay = load_development_cell(
                root=root,
                prior=prior,
                dataset=dataset,
                frame=frame,
                split=split,
                protocol=protocol,
                prior_cell=previous,
                kind=kind,
                protocol_path=protocol_path,
            )
            target_source = TargetBindingSource(dataset.dataset_id, replay.target_rows)
            matched_cell = matched_cells.get((dataset.dataset_id, kind), {"measurements": []})
            matched_measurements = {
                item["selected_shards"]: item for item in matched_cell["measurements"]
            }
            measurements: list[dict[str, Any]] = []
            for dose in DOSES:
                measurement = _cyclic_measurement(
                    replay=replay, target_source=target_source, dose=dose
                )
                if matched_measurements and dose > 0:
                    measurement["adversarial_paired_binding"] = _paired_measurement(
                        replay=replay,
                        source=target_source,
                        item=matched_measurements[dose],
                        dose=dose,
                    )
                measurements.append(measurement)
            cells.append(
                {
                    "dataset_id": dataset.dataset_id,
                    "model_kind": kind,
                    "development_count": len(replay.dev_ids),
                    "measurements": measurements,
                }
            )
    summary = {
        "schema_version": "target-binding-development/v2"
        if matched_prior is not None
        else "target-binding-development/v1",
        "status": "development_only",
        "provider_calls": 0,
        "registered_attempt": False,
        "sealed_predictions_or_metrics_computed": False,
        "selector_uses_targets_scores_or_outcomes": False,
        "independently_admitted": False,
        "source_count": len(manifest.datasets),
        "cell_count": len(cells),
        "cells": cells,
        "sampling_note": "Estimators and doses nested in sources are correlated; no population CI or admission claim.",
    }
    if matched_prior is not None:
        assert matched_sha256 is not None
        summary.update(
            {
                "adversarial_pair_search_uses_development_targets_scores_and_mapping_metric": True,
                "matched_predecessor_byte_sha256": matched_sha256,
            }
        )
    output.mkdir(mode=0o700)
    _write_json(output / "summary.json", summary)
    return summary

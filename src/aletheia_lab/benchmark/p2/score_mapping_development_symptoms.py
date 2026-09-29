"""Replay pinned evaluator-mapping sources against a target-binding hard negative.

Only the historical train/development partition is predicted or scored. The
prior feasibility folder is read-only; a new private folder holds this study.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    PreprocessorState,
    fit_logit_calibration,
    reconstruct_runtime_split,
    transform_features,
)
from aletheia_lab.benchmark.p2.score_mapping_development import (
    DOSES,
    MODEL_KINDS,
    _private_output_guard,
    _write_json,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    ProjectionBundle,
    build_development_evidence_views,
    mapping_observation,
    serialize_m5_diagnostic_view,
    target_binding_rival_observation,
    zero_dose_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    BinaryProbabilityModel,
    EvaluatorScoreSource,
    apply_evaluator_mapping_fault,
    capture_evaluator_score_source,
)
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    MAX_ABSOLUTE_LOSS_GAP,
    VISIBLE_METRIC_DECIMALS,
    match_target_swaps,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    IndependentScoreWitness,
    SourceArtifactPaths,
    capture_independent_score_witness,
    verify_evaluator_mapping,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.score_mapping_reader import build_score_mapping_reader_context

PRIOR_SUMMARY_SHA256 = "2074633e1dbae29f4b9178344a752f8ff6b49a3bbc643bb0c206d4302406759a"


class DevelopmentSymptomStudyError(ValueError):
    """The pinned development replay or hard-negative study failed closed."""


@dataclass(frozen=True, slots=True)
class DevelopmentCellReplay:
    witness: IndependentScoreWitness
    source: EvaluatorScoreSource
    model: BinaryProbabilityModel
    calibration: CalibrationResult
    artifacts: SourceArtifactPaths
    train: NDArray[np.float64]
    development: NDArray[np.float64]
    dev_ids: tuple[str, ...]
    dev_targets: tuple[int, ...]
    target_rows: tuple[tuple[str, int], ...]
    healthy_scores: tuple[float, ...]


_CellReplay = DevelopmentCellReplay


def _checked_prior_summary(prior: Path) -> dict[str, Any]:
    path = prior / "summary.json"
    if prior.is_symlink() or path.is_symlink() or not path.is_file():
        raise DevelopmentSymptomStudyError("pinned predecessor summary is unavailable")
    if file_sha256(path) != PRIOR_SUMMARY_SHA256:
        raise DevelopmentSymptomStudyError("pinned predecessor summary bytes changed")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != "score-mapping-development-feasibility/v1"
        or payload.get("status") != "development_only"
        or payload.get("cell_count") != 4
        or payload.get("registered_attempt") is not False
        or payload.get("provider_calls") != 0
        or payload.get("sealed_predictions_or_metrics_computed") is not False
    ):
        raise DevelopmentSymptomStudyError("predecessor is not the verified development run")
    return payload


def _coarsened_views(observation: DevelopmentObservation) -> ProjectionBundle:
    """Use the same source-to-visible rounding rule as the matching search."""

    return build_development_evidence_views(
        observation, metric_decimal_places=VISIBLE_METRIC_DECIMALS
    )


def _required_artifact(path: Path, expected_sha256: str) -> None:
    if path.is_symlink() or not path.is_file() or file_sha256(path) != expected_sha256:
        raise DevelopmentSymptomStudyError("pinned source artifact is missing or changed")


def _measure_dose(replay: DevelopmentCellReplay, previous: dict[str, Any]) -> dict[str, Any]:
    dose = previous["selected_shards"]
    if dose not in DOSES:
        raise DevelopmentSymptomStudyError("predecessor used an unknown development dose")
    intervention = apply_evaluator_mapping_fault(replay.source, selected_shard_count=dose)
    verified = verify_evaluator_mapping(
        witness=replay.witness,
        source=replay.source,
        intervention=intervention,
        scoring_target_rows=replay.target_rows,
        reference_model=replay.model,
        evaluation_matrix=replay.development,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
    )
    if (
        verified.healthy_log_loss != previous["healthy_log_loss"]
        or verified.faulty_log_loss != previous["faulty_log_loss"]
        or verified.corrected_log_loss != previous["corrected_log_loss"]
        or len(verified.affected_record_ids) != previous["affected_rows"]
        or len(verified.changed_score_record_ids) != previous["changed_score_rows"]
    ):
        raise DevelopmentSymptomStudyError("predecessor metric does not replay exactly")
    result: dict[str, Any] = {
        "selected_shards": dose,
        "affected_rows": previous["affected_rows"],
        "achieved_mapping_fraction": previous["achieved_fraction"],
        "mapping_delta_log_loss": previous["delta_log_loss"],
        "mapping_log_loss": previous["faulty_log_loss"],
    }
    if dose == 0 or not verified.changed_score_record_ids:
        if dose == 0:
            control = zero_dose_observation(
                witness=replay.witness,
                source=replay.source,
                reference_model=replay.model,
                reference_calibration=replay.calibration,
                artifacts=replay.artifacts,
                reference_features=replay.train,
                evaluation_features=replay.development,
            )
            for condition in ("full", "missing_key", "noisy", "misleading"):
                build_score_mapping_reader_context(control, condition=condition)
        result["status"] = "zero_or_flat_control"
        return result
    mapping = mapping_observation(
        witness=replay.witness,
        source=replay.source,
        intervention=intervention,
        scoring_target_rows=replay.target_rows,
        reference_model=replay.model,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    match = match_target_swaps(
        record_ids=replay.dev_ids,
        targets=replay.dev_targets,
        probabilities=replay.healthy_scores,
        mapping_log_loss=verified.faulty_log_loss,
        max_changed_targets=len(verified.affected_record_ids),
    )
    verify_target_swap_match(
        record_ids=replay.dev_ids,
        targets=replay.dev_targets,
        probabilities=replay.healthy_scores,
        mapping_log_loss=verified.faulty_log_loss,
        max_changed_targets=len(verified.affected_record_ids),
        result=match,
    )
    if not match.swapped_pairs:
        result["status"] = "no_eligible_rival_pair"
        return result
    rival = target_binding_rival_observation(
        witness=replay.witness,
        source=replay.source,
        scoring_target_rows=match.scoring_target_rows,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    mapping_views = _coarsened_views(mapping)
    rival_views = _coarsened_views(rival)
    original_mapping = build_development_evidence_views(mapping)
    original_rival = build_development_evidence_views(rival)
    full_distinguishes = serialize_m5_diagnostic_view(
        mapping, condition="full"
    ) != serialize_m5_diagnostic_view(rival, condition="full")
    missing_identical = serialize_m5_diagnostic_view(
        mapping, condition="missing_key"
    ) == serialize_m5_diagnostic_view(rival, condition="missing_key")
    # The gateway sends the whole context envelope, including derived IDs and
    # digests.  Projection-byte equality alone cannot rule out that shortcut.
    gateway_missing_identical = (
        build_score_mapping_reader_context(mapping, condition="missing_key").model_payload()
        == build_score_mapping_reader_context(rival, condition="missing_key").model_payload()
    )
    gateway_full_distinguishes = (
        build_score_mapping_reader_context(mapping, condition="full").model_payload()
        != build_score_mapping_reader_context(rival, condition="full").model_payload()
    )
    if (
        gateway_missing_identical != missing_identical
        or gateway_full_distinguishes != full_distinguishes
    ):
        raise DevelopmentSymptomStudyError("gateway reader envelope disagrees with the projection")
    source_shared = (
        mapping.source_identity_sha256 == rival.source_identity_sha256
        and mapping.reference_features_sha256 == rival.reference_features_sha256
    )
    result.update(
        {
            "status": (
                "resolution_matched_pair"
                if match.resolution_matched
                and missing_identical
                and full_distinguishes
                and source_shared
                else "unmatched_or_shortcut"
            ),
            "rival_changed_target_rows": 2 * len(match.swapped_pairs),
            "rival_changed_fraction": 2 * len(match.swapped_pairs) / len(replay.dev_ids),
            "rival_log_loss": match.observed_log_loss,
            "absolute_loss_gap": match.absolute_loss_gap,
            "source_identity_shared": source_shared,
            "visible_missing_key_identical": missing_identical,
            "full_witness_distinguishes": full_distinguishes,
            "twelve_decimal_missing_key_identical": (
                original_mapping["missing_key"] == original_rival["missing_key"]
            ),
            "swapped_pairs": match.swapped_pairs,
            "rival_target_binding_sha256": canonical_sha256(
                {"target_rows": match.scoring_target_rows}
            ),
            "mapping_view_sha256": {
                name: canonical_sha256(view) for name, view in mapping_views.items()
            },
            "rival_view_sha256": {
                name: canonical_sha256(view) for name, view in rival_views.items()
            },
        }
    )
    return result


def load_development_cell(
    *,
    root: Path,
    prior: Path,
    dataset: Any,
    frame: Any,
    split: Any,
    protocol: Any,
    prior_cell: dict[str, Any],
    kind: str,
    protocol_path: Path,
) -> DevelopmentCellReplay:
    """Replay a hash-pinned train/development source without scoring a holdout."""
    cell_dir = prior / f"{dataset.dataset_id}-{kind}"
    if cell_dir.is_symlink() or not cell_dir.is_dir():
        raise DevelopmentSymptomStudyError("predecessor cell is unavailable")
    measurement = cell_dir / "measurement.json"
    if measurement.is_symlink() or json.loads(measurement.read_text()) != prior_cell:
        raise DevelopmentSymptomStudyError("predecessor cell disagrees with its summary")
    train_indices = split.indices("train")
    dev_indices = split.indices("development")
    if not train_indices or not dev_indices:
        raise DevelopmentSymptomStudyError("pinned development partition is empty")
    train_ids = tuple(split.record_ids[index] for index in train_indices)
    dev_ids = tuple(split.record_ids[index] for index in dev_indices)
    dev_targets = tuple(split.labels[index] for index in dev_indices)
    target_rows = tuple(zip(dev_ids, dev_targets, strict=True))
    if (
        prior_cell["dataset_id"] != dataset.dataset_id
        or prior_cell["model_kind"] != kind
        or prior_cell["split_membership_sha256"] != split.membership_sha256
        or prior_cell["train_count"] != len(train_ids)
        or prior_cell["development_count"] != len(dev_ids)
    ):
        raise DevelopmentSymptomStudyError("predecessor census or split has drifted")
    archive = root / "data/raw/p2-v3" / dataset.archive.file_name
    preprocessor_path = cell_dir / "preprocessor.json"
    model_path = cell_dir / "fitted_model.joblib"
    artifacts = SourceArtifactPaths(archive, protocol_path, preprocessor_path, model_path)
    expected = prior_cell["source_artifact_sha256"]
    for name, path in (
        ("dataset", archive),
        ("split", protocol_path),
        ("preprocessor", preprocessor_path),
        ("fitted_model", model_path),
    ):
        _required_artifact(path, expected[name])
    state = PreprocessorState.model_validate_json(preprocessor_path.read_text(encoding="utf-8"))
    features = list(dataset.analysis_features)
    train_frame = frame.iloc[list(train_indices)].loc[:, features]
    dev_frame = frame.iloc[list(dev_indices)].loc[:, features]
    train = transform_features(dataset=dataset, state=state, frame=train_frame)
    development = transform_features(dataset=dataset, state=state, frame=dev_frame)
    # The model is a hash-checked artifact written by the predecessor, not an
    # arbitrary supplied pickle. Never load before the byte identity check.
    model = joblib.load(model_path)
    if tuple(int(value) for value in model.classes_) != (0, 1):
        raise DevelopmentSymptomStudyError("pinned model class order changed")
    raw_scores = np.asarray(model.predict_proba(development), dtype=np.float64)
    calibration = fit_logit_calibration(
        raw_scores[:, 1].tolist(),
        dev_targets,
        probability_clip=protocol.models.calibration_probability_clip,
        max_iter=protocol.models.calibration_max_iter,
        tolerance=protocol.models.calibration_tolerance,
    )
    witness = capture_independent_score_witness(
        dataset_id=dataset.dataset_id,
        record_ids=dev_ids,
        target_rows=target_rows,
        evaluation_matrix=development,
        model=model,
        calibration=calibration,
        artifacts=artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id=dataset.dataset_id,
        record_ids=dev_ids,
        evaluation_matrix=development,
        model=model,
        calibration=calibration,
    )
    if (
        witness.raw_scores_sha256 != prior_cell["source_score_sha256"]
        or witness.target_binding_sha256 != prior_cell["target_binding_sha256"]
        or witness.calibration_sha256 != prior_cell["calibration_sha256"]
    ):
        raise DevelopmentSymptomStudyError("development source differs from pinned feasibility")
    healthy_scores = tuple(
        row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows
    )
    return DevelopmentCellReplay(
        witness,
        source,
        model,
        calibration,
        artifacts,
        train,
        development,
        dev_ids,
        dev_targets,
        target_rows,
        healthy_scores,
    )


def _cell_study(
    *,
    root: Path,
    prior: Path,
    dataset: Any,
    frame: Any,
    split: Any,
    protocol: Any,
    prior_cell: dict[str, Any],
    kind: str,
    protocol_path: Path,
) -> dict[str, Any]:
    replay = load_development_cell(
        root=root,
        prior=prior,
        dataset=dataset,
        frame=frame,
        split=split,
        protocol=protocol,
        prior_cell=prior_cell,
        kind=kind,
        protocol_path=protocol_path,
    )
    measurements = [_measure_dose(replay, previous) for previous in prior_cell["measurements"]]
    return {
        "dataset_id": dataset.dataset_id,
        "model_kind": kind,
        "development_count": len(replay.dev_ids),
        "source_score_sha256": replay.witness.raw_scores_sha256,
        "source_target_binding_sha256": replay.witness.target_binding_sha256,
        "measurements": measurements,
    }


def run_development_symptom_study(*, root: Path, prior: Path, output: Path) -> dict[str, Any]:
    """Execute one deterministic offline hard-negative replay into a new folder."""

    _private_output_guard(root, output)
    root = root.resolve(strict=True)
    if prior.is_symlink():
        raise DevelopmentSymptomStudyError("predecessor cannot be a symlink")
    prior = prior.resolve(strict=True)
    resolved_output = output.absolute().resolve()
    if (
        prior.is_relative_to(root)
        or resolved_output.is_relative_to(prior)
        or prior.is_relative_to(resolved_output)
    ):
        raise DevelopmentSymptomStudyError("predecessor must stay private and read-only")
    prior_summary = _checked_prior_summary(prior)
    protocol_path = root / "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, _ = verify_v3_protocol_artifacts(protocol, root=root)
    if prior_summary["protocol_sha256"] != protocol.canonical_sha256():
        raise DevelopmentSymptomStudyError("pinned predecessor protocol has drifted")
    if len(prior_summary["cells"]) != len(manifest.datasets) * len(MODEL_KINDS):
        raise DevelopmentSymptomStudyError("pinned predecessor has an incomplete matrix")
    output.mkdir(mode=0o700)
    cells: list[dict[str, Any]] = []
    for dataset, receipt in zip(manifest.datasets, protocol.dataset_splits, strict=True):
        if (dataset.dataset_id, dataset.role) != (receipt.dataset_id, receipt.role):
            raise DevelopmentSymptomStudyError("manifest and protocol dataset order disagree")
        archive = root / "data/raw/p2-v3" / dataset.archive.file_name
        _, frame = load_v3_dataset_snapshot_for_registration(dataset=dataset, archive_path=archive)
        split = reconstruct_runtime_split(
            protocol=protocol, dataset=dataset, frame=frame, receipt=receipt
        )
        for kind in MODEL_KINDS:
            prior_cell = next(
                (
                    cell
                    for cell in prior_summary["cells"]
                    if cell["dataset_id"] == dataset.dataset_id and cell["model_kind"] == kind
                ),
                None,
            )
            if prior_cell is None:
                raise DevelopmentSymptomStudyError("pinned predecessor cell is missing")
            cells.append(
                _cell_study(
                    root=root,
                    prior=prior,
                    dataset=dataset,
                    frame=frame,
                    split=split,
                    protocol=protocol,
                    prior_cell=prior_cell,
                    kind=kind,
                    protocol_path=protocol_path,
                )
            )
    candidate = next(
        (
            dose
            for dose in DOSES[1:]
            if all(
                next(
                    measurement
                    for measurement in cell["measurements"]
                    if measurement["selected_shards"] == dose
                )["status"]
                == "resolution_matched_pair"
                for cell in cells
            )
        ),
        None,
    )
    summary = {
        "schema_version": "score-mapping-development-symptom-study/v1",
        "status": "development_candidate" if candidate is not None else "exploratory_unmatched",
        "registered_attempt": False,
        "provider_calls": 0,
        "sealed_predictions_or_metrics_computed": False,
        "prior_summary_byte_sha256": PRIOR_SUMMARY_SHA256,
        "protocol_sha256": protocol.canonical_sha256(),
        "visible_metric_decimal_places": VISIBLE_METRIC_DECIMALS,
        "maximum_absolute_loss_gap": MAX_ABSOLUTE_LOSS_GAP,
        "candidate_selected_shards": candidate,
        "cell_count": len(cells),
        "cells": cells,
    }
    _write_json(output / "summary.json", summary)
    return summary

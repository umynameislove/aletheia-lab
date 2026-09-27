"""Fail-closed development-only source and output guards."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import aletheia_lab.benchmark.p2.score_mapping_development as development
import aletheia_lab.benchmark.p2.score_mapping_development_symptoms as symptoms
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PreprocessorState,
    apply_logit_calibration,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.score_mapping_development import (
    ScoreMappingDevelopmentError,
    _cell,
    _fit_reference_model,
    _private_output_guard,
    run_score_mapping_development_feasibility,
)
from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import _cell_study


def test_private_output_must_be_new_and_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    _private_output_guard(root, private / "new")
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, root / "internal")
    existing = private / "existing"
    existing.mkdir()
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, existing)
    link = private / "link"
    link.symlink_to(root / "internal")
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, link)


@pytest.mark.parametrize("kind", ["logistic_regression", "hist_gradient_boosting"])
def test_reference_model_has_two_ordered_source_columns(kind: str) -> None:
    x = np.asarray([[float(index), float(index % 3)] for index in range(60)])
    y = tuple(index % 2 for index in range(60))
    model = _fit_reference_model(kind, x, y)  # type: ignore[arg-type]
    scores = model.predict_proba(x)
    assert tuple(model.classes_) == (0, 1)
    assert scores.shape == (60, 2)
    np.testing.assert_allclose(scores.sum(axis=1), 1.0, atol=1e-12, rtol=0)


def test_unknown_model_kind_fails_closed() -> None:
    with pytest.raises(ScoreMappingDevelopmentError):
        _fit_reference_model("other", np.ones((4, 2)), (0, 1, 0, 1))  # type: ignore[arg-type]


def test_development_cell_replays_source_and_zero_dose_without_sealed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    train_count = 80
    total_count = 160
    labels = tuple(index % 2 for index in range(total_count))
    frame = pd.DataFrame(
        {
            "x": [
                float((index // 2) % 20)
                + (2.0 if labels[index] else -2.0)
                + (1.0 if index >= train_count else 0.0)
                for index in range(total_count)
            ]
        }
    )
    ids = tuple(f"record-{index:03d}" for index in range(total_count))
    dataset = SimpleNamespace(
        dataset_id="synthetic-development",
        analysis_features=("x",),
        archive=SimpleNamespace(file_name="dataset.bin"),
    )
    split = SimpleNamespace(
        record_ids=ids,
        labels=labels,
        membership_sha256="a" * 64,
        indices=lambda partition: (
            tuple(range(train_count))
            if partition == "train"
            else tuple(range(train_count, total_count))
        ),
    )
    models = SimpleNamespace(
        calibration_probability_clip=1e-12,
        calibration_max_iter=200,
        calibration_tolerance=1e-9,
    )
    protocol = SimpleNamespace(models=models)
    state = PreprocessorState(
        dataset_id=dataset.dataset_id,
        categorical_columns=(),
        numeric_columns=("x",),
        category_vocabulary={},
        numeric_means=(0.0,),
        numeric_scales=(1.0,),
        output_columns=("x",),
        output_columns_sha256=canonical_sha256(
            {"schema_version": "p2-v3-preprocessor/1", "columns": ("x",)}
        ),
    )
    monkeypatch.setattr(development, "fit_preprocessor", lambda _dataset, _frame: state)
    monkeypatch.setattr(
        development,
        "transform_features",
        lambda *, dataset, state, frame: frame.loc[:, ["x"]].to_numpy(dtype=np.float64),
    )
    monkeypatch.setattr(symptoms, "transform_features", development.transform_features)

    def independent_runtime_refit(**kwargs: object) -> SimpleNamespace:
        model = _fit_reference_model(
            "logistic_regression",
            kwargs["training_matrix"],  # type: ignore[arg-type]
            tuple(kwargs["training_targets"]),  # type: ignore[arg-type]
        )
        raw = model.predict_proba(kwargs["development_matrix"])[:, 1].tolist()
        calibration = fit_logit_calibration(
            raw,
            kwargs["development_targets"],  # type: ignore[arg-type]
            probability_clip=models.calibration_probability_clip,
            max_iter=models.calibration_max_iter,
            tolerance=models.calibration_tolerance,
        )
        return SimpleNamespace(
            calibration=calibration,
            development_record_ids=kwargs["development_record_ids"],
            development_probabilities=apply_logit_calibration(
                raw, calibration, clip=models.calibration_probability_clip
            ),
        )

    monkeypatch.setattr(development, "fit_registered_model", independent_runtime_refit)
    root = tmp_path / "repo"
    prior = tmp_path / "prior"
    archive = root / "data/raw/p2-v3/dataset.bin"
    split_path = root / "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
    archive.parent.mkdir(parents=True)
    split_path.parent.mkdir(parents=True)
    prior.mkdir()
    archive.write_bytes(b"synthetic development data")
    split_path.write_bytes(b"synthetic split receipt")
    result = _cell(
        dataset=dataset,
        archive=archive,
        split=split,
        protocol=protocol,
        frame=frame,
        train_indices=tuple(range(train_count)),
        development_indices=tuple(range(train_count, total_count)),
        kind="logistic_regression",
        cell_dir=prior / "synthetic-development-logistic_regression",
        split_path=split_path,
    )

    assert result["train_count"] == result["development_count"] == train_count
    assert result["runtime_refit_agreed"] is True
    assert tuple(item["selected_shards"] for item in result["measurements"]) == (0, 1, 2, 4)
    healthy = result["measurements"][0]
    assert healthy["affected_rows"] == 0
    assert healthy["delta_log_loss"] == 0.0
    assert all(
        item["corrected_log_loss"] == healthy["healthy_log_loss"] for item in result["measurements"]
    )
    assert all(
        item["target_flip_rival"]["full_payload_differs"] for item in result["measurements"][1:]
    )
    (prior / "synthetic-development-logistic_regression/measurement.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    replay = _cell_study(
        root=root,
        prior=prior,
        dataset=dataset,
        frame=frame,
        split=split,
        protocol=protocol,
        prior_cell=result,
        kind="logistic_regression",
        protocol_path=split_path,
    )
    assert replay["source_score_sha256"] == result["source_score_sha256"]
    assert tuple(item["selected_shards"] for item in replay["measurements"]) == (0, 1, 2, 4)
    assert replay["measurements"][0]["status"] == "zero_or_flat_control"


def test_development_matrix_is_four_private_cells_and_never_scores_sealed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    output = tmp_path / "private" / "new"
    output.parent.mkdir()
    datasets = tuple(
        SimpleNamespace(
            dataset_id=f"development-{index}",
            role=f"role-{index}",
            archive=SimpleNamespace(file_name=f"dataset-{index}.bin"),
        )
        for index in range(2)
    )
    receipts = tuple(
        SimpleNamespace(dataset_id=dataset.dataset_id, role=dataset.role) for dataset in datasets
    )
    protocol = SimpleNamespace(dataset_splits=receipts, canonical_sha256=lambda: "a" * 64)
    monkeypatch.setattr(development, "load_v3_confirmatory_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        development,
        "verify_v3_protocol_artifacts",
        lambda _protocol, *, root: (None, SimpleNamespace(datasets=datasets), None),
    )
    monkeypatch.setattr(
        development,
        "load_v3_dataset_snapshot_for_registration",
        lambda *, dataset, archive_path: (None, pd.DataFrame({"x": (0, 1, 2, 3)})),
    )
    split = SimpleNamespace(indices=lambda partition: (0, 1) if partition == "train" else (2, 3))
    monkeypatch.setattr(development, "reconstruct_runtime_split", lambda **kwargs: split)
    visited: list[tuple[str, str]] = []

    def capture_cell(**kwargs: object) -> dict[str, object]:
        dataset = kwargs["dataset"]
        kind = kwargs["kind"]
        cell_dir = kwargs["cell_dir"]
        assert kwargs["development_indices"] == (2, 3)
        assert isinstance(cell_dir, Path)
        assert cell_dir.parent == output
        cell_dir.mkdir()
        visited.append((dataset.dataset_id, kind))  # type: ignore[attr-defined]
        return {"dataset_id": dataset.dataset_id, "model_kind": kind}  # type: ignore[attr-defined]

    monkeypatch.setattr(development, "_cell", capture_cell)
    result = run_score_mapping_development_feasibility(root=root, output=output)

    assert result["status"] == "development_only"
    assert result["registered_attempt"] is False
    assert result["provider_calls"] == 0
    assert result["sealed_predictions_or_metrics_computed"] is False
    assert result["cell_count"] == 4
    assert visited == [
        (dataset.dataset_id, kind)
        for dataset in datasets
        for kind in ("logistic_regression", "hist_gradient_boosting")
    ]
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == result

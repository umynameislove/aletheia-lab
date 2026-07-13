"""Orchestration for the baseline: resolve config, train, and verify reproducibility.

``train`` loads the processed dataset, builds a seeded split, fits the pipeline on
the training split only, evaluates every split, and writes a structured, mostly
byte-deterministic artifact set. ``verify`` runs ``train`` twice into two clean
directories with the same config and seed and compares the results, distinguishing
fields that must be identical from those checked within a numerical tolerance.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from aletheia_lab.baseline.artifacts import (
    package_versions,
    sha256_file,
    utc_now_iso,
    write_json,
)
from aletheia_lab.baseline.evaluate import evaluate_split, predict_split
from aletheia_lab.baseline.loader import (
    LoadedSplits,
    assert_no_overlap,
    load_processed,
    split_dataset,
)
from aletheia_lab.baseline.model import ModelConfig, build_pipeline
from aletheia_lab.config import load_yaml

_DEFAULT_RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


@dataclass(frozen=True)
class BaselineSettings:
    """Fully-resolved settings for one baseline run."""

    dataset_id: str
    processed_path: str
    seed: int
    ratios: dict[str, float]
    stratified: bool
    model: ModelConfig
    output_root: str
    tolerance: float

    def run_id(self) -> str:
        """Deterministic default run directory name (no wall-clock)."""

        return f"{self.model.kind}_seed{self.seed}"


def resolve_settings(config_path: str | Path) -> BaselineSettings:
    """Read project + baseline config and resolve a concrete settings object."""

    cfg = load_yaml(config_path)
    dataset = cfg.get("dataset", {})
    paths = cfg.get("paths", {})
    baseline = cfg.get("baseline", {})

    dataset_id = dataset.get("id", "telco_customer_churn")
    processed_dir = paths.get("processed_data", "data/processed")
    processed_path = str(Path(processed_dir) / f"{dataset_id}.csv")

    split_cfg = baseline.get("split", {})
    ratios = {
        "train": float(split_cfg.get("train", _DEFAULT_RATIOS["train"])),
        "validation": float(split_cfg.get("validation", _DEFAULT_RATIOS["validation"])),
        "test": float(split_cfg.get("test", _DEFAULT_RATIOS["test"])),
    }
    model_cfg = baseline.get("model", {})
    seed = int(baseline.get("seed", 42))
    model = ModelConfig(
        kind=str(model_cfg.get("type", "logistic_regression")),
        seed=seed,
        max_iter=int(model_cfg.get("max_iter", 1000)),
        C=float(model_cfg.get("C", 1.0)),
        class_weight=model_cfg.get("class_weight"),
    )
    return BaselineSettings(
        dataset_id=dataset_id,
        processed_path=processed_path,
        seed=seed,
        ratios=ratios,
        stratified=bool(split_cfg.get("stratify", True)),
        model=model,
        output_root=str(baseline.get("output_dir", "experiments/baseline/runs")),
        tolerance=float(baseline.get("tolerance", 1e-9)),
    )


def _load_splits(settings: BaselineSettings) -> tuple[LoadedSplits, str]:
    frame = load_processed(settings.processed_path)
    dataset_sha = sha256_file(settings.processed_path)
    splits = split_dataset(
        frame,
        dataset_id=settings.dataset_id,
        dataset_sha256=dataset_sha,
        seed=settings.seed,
        ratios=settings.ratios,
        stratified=settings.stratified,
    )
    assert_no_overlap(splits)
    # Target must not have leaked into the feature matrix.
    from aletheia_lab.baseline.schema import ID_COLUMN, TARGET_COLUMN

    for data in splits.as_dict().values():
        leaked = {ID_COLUMN, TARGET_COLUMN} & set(data.features.columns)
        if leaked:
            msg = f"leakage: {sorted(leaked)} present in feature matrix of split {data.name!r}"
            raise ValueError(msg)
    return splits, dataset_sha


def _predictions_payload(pipeline: Any, splits: LoadedSplits) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, data in splits.as_dict().items():
        y_pred, proba = predict_split(pipeline, data)
        payload[name] = {
            "ids": [str(v) for v in data.ids.tolist()],
            "y_true": [int(v) for v in data.target.tolist()],
            "y_pred": [int(v) for v in y_pred.tolist()],
            "proba": [float(v) for v in proba.tolist()],
        }
    return payload


def train(settings: BaselineSettings, output_dir: str | Path | None = None) -> dict[str, Any]:
    """Fit the baseline and write all artifacts. Return a summary dict."""

    run_dir = (
        Path(output_dir)
        if output_dir is not None
        else Path(settings.output_root) / settings.run_id()
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    splits, dataset_sha = _load_splits(settings)
    pipeline = build_pipeline(settings.model)
    pipeline.fit(splits.train.features, splits.train.target)

    metrics = {
        name: evaluate_split(pipeline, data).model_dump() for name, data in splits.as_dict().items()
    }
    from aletheia_lab.baseline.schema import MetricsReport, SplitMetrics

    report = MetricsReport(splits={k: SplitMetrics(**v) for k, v in metrics.items()})

    config_path = write_json(
        _settings_as_json(settings, dataset_sha), run_dir / "config.resolved.json"
    )
    manifest_path = write_json(splits.manifest.model_dump(), run_dir / "split_manifest.json")
    metrics_path = write_json(report.model_dump(), run_dir / "metrics.json")
    predictions_path = write_json(
        _predictions_payload(pipeline, splits), run_dir / "predictions.json"
    )

    model_path = run_dir / "model.joblib"
    joblib.dump(pipeline, model_path)

    # model.joblib is byte-deterministic for identical data + seed, so its digest
    # is recorded alongside the JSON artifacts and checked across runs.
    checksums = {
        "config.resolved.json": sha256_file(config_path),
        "split_manifest.json": sha256_file(manifest_path),
        "metrics.json": sha256_file(metrics_path),
        "predictions.json": sha256_file(predictions_path),
        "model.joblib": sha256_file(model_path),
    }
    write_json(checksums, run_dir / "checksums.json")

    provenance = {
        "dataset_id": settings.dataset_id,
        "dataset_sha256": dataset_sha,
        "seed": settings.seed,
        "model": asdict(settings.model),
        "package_versions": package_versions(),
        "artifact_checksums": checksums,
        "created_at": utc_now_iso(),
    }
    write_json(provenance, run_dir / "provenance.json")

    return {
        "run_dir": str(run_dir),
        "dataset_sha256": dataset_sha,
        "manifest": splits.manifest.model_dump(),
        "metrics": report.model_dump(),
        "checksums": checksums,
    }


def _settings_as_json(settings: BaselineSettings, dataset_sha: str) -> dict[str, Any]:
    return {
        "dataset_id": settings.dataset_id,
        "dataset_sha256": dataset_sha,
        "processed_path": settings.processed_path,
        "seed": settings.seed,
        "ratios": settings.ratios,
        "stratified": settings.stratified,
        "model": asdict(settings.model),
        "tolerance": settings.tolerance,
    }


@dataclass
class VerifyReport:
    """Result of a two-run reproducibility comparison."""

    passed: bool
    exact_fields: dict[str, bool] = field(default_factory=dict)
    tolerance_fields: dict[str, float] = field(default_factory=dict)
    tolerance: float = 1e-9
    run_dirs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "exact_fields": self.exact_fields,
            "tolerance_fields": self.tolerance_fields,
            "tolerance": self.tolerance,
            "run_dirs": self.run_dirs,
            "notes": self.notes,
        }


def _max_abs_proba_diff(a: dict[str, Any], b: dict[str, Any]) -> float:
    worst = 0.0
    for split_name in a:
        pa = np.asarray(a[split_name]["proba"], dtype=float)
        pb = np.asarray(b[split_name]["proba"], dtype=float)
        if pa.shape != pb.shape:
            return float("inf")
        worst = max(worst, float(np.max(np.abs(pa - pb))) if pa.size else 0.0)
    return worst


def _preds_exact(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a[s]["y_pred"] == b[s]["y_pred"] and a[s]["ids"] == b[s]["ids"] for s in a)


def verify(settings: BaselineSettings, output_dir: str | Path | None = None) -> VerifyReport:
    """Run training twice with the same config/seed and compare the outputs."""

    base = (
        Path(output_dir)
        if output_dir is not None
        else Path(tempfile.mkdtemp(prefix="baseline_verify_"))
    )
    dir_a, dir_b = base / "run_a", base / "run_b"
    r_a = train(settings, dir_a)
    r_b = train(settings, dir_b)

    import json

    manifest_a = (dir_a / "split_manifest.json").read_text(encoding="utf-8")
    manifest_b = (dir_b / "split_manifest.json").read_text(encoding="utf-8")
    metrics_a = (dir_a / "metrics.json").read_text(encoding="utf-8")
    metrics_b = (dir_b / "metrics.json").read_text(encoding="utf-8")
    preds_a = json.loads((dir_a / "predictions.json").read_text(encoding="utf-8"))
    preds_b = json.loads((dir_b / "predictions.json").read_text(encoding="utf-8"))

    coef_a = joblib.load(dir_a / "model.joblib").named_steps["model"].coef_
    coef_b = joblib.load(dir_b / "model.joblib").named_steps["model"].coef_
    coef_diff = (
        float(np.max(np.abs(coef_a - coef_b))) if coef_a.shape == coef_b.shape else float("inf")
    )

    proba_diff = _max_abs_proba_diff(preds_a, preds_b)

    exact = {
        "split_manifest_bytes": manifest_a == manifest_b,
        "metrics_bytes": metrics_a == metrics_b,
        "predictions_labels_and_ids": _preds_exact(preds_a, preds_b),
        "checksums_match": r_a["checksums"] == r_b["checksums"],
    }
    tol_fields = {"probabilities_max_abs_diff": proba_diff, "coefficients_max_abs_diff": coef_diff}
    passed = all(exact.values()) and all(v <= settings.tolerance for v in tol_fields.values())

    return VerifyReport(
        passed=passed,
        exact_fields=exact,
        tolerance_fields=tol_fields,
        tolerance=settings.tolerance,
        run_dirs=[str(dir_a), str(dir_b)],
        notes=[
            "created_at and package_versions in provenance.json are metadata and excluded.",
            "exact fields must match byte-for-byte; tolerance fields must be <= tolerance.",
        ],
    )

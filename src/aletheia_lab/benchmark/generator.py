"""Deterministic generator for the 15 P1 data-drift benchmark cases.

Matrix: 5 injection settings (from ``configs/benchmark/fault_types.yaml``) x 3
evidence conditions (full / missing_key / noisy) = 15 cases.

Construct validity: the P1-C-02 baseline is trained on the train split and scored
on the clean test split (reference) and on each drifted test split (observed), so
the "metric change" recorded as evidence is measured, not assumed. Drift is scored
by PSI between the training distribution and the drifted evaluation batch. The full
injected dataset is never persisted; only signals, provenance and checksums are
written, so no large artifact enters the tree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from aletheia_lab.baseline.loader import load_processed, split_dataset
from aletheia_lab.baseline.model import build_pipeline
from aletheia_lab.baseline.run import resolve_settings
from aletheia_lab.baseline.schema import FEATURE_COLUMNS
from aletheia_lab.benchmark.case_schema import (
    EVIDENCE_CONDITIONS,
    EXPECTED_BEHAVIOR,
    SCHEMA_VERSION,
    MetricComparison,
    ObservableSignals,
)
from aletheia_lab.benchmark.case_writer import (
    diagnosis_input_leakage,
    dumps_deterministic,
    load_case_dir,
    sha256_file,
    write_case,
)
from aletheia_lab.benchmark.injectors import CategoricalDriftInjector, DriftSpec
from aletheia_lab.benchmark.signals import categorical_distribution, population_stability_index
from aletheia_lab.config import load_yaml

_CONDITION_SLUG = {"full": "full", "missing_key": "missing-key", "noisy": "noisy"}
_DISTRACTOR_FEATURES = ("gender", "PaymentMethod")
_EXPECTED_SETTINGS = 5
_TARGET_COLUMN = "__target__"


class GeneratorConfigError(RuntimeError):
    """Raised when the benchmark generation config is invalid."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Setting:
    injection_id: str
    seed: int
    target_distribution: dict[str, float]


@dataclass(frozen=True)
class _Injected:
    setting: _Setting
    observed: dict[str, float]
    psi: float
    metric: MetricComparison
    injected_change: str


def _load_settings(config_path: Path) -> tuple[str, list[_Setting]]:
    fault_path = config_path.parent / "benchmark" / "fault_types.yaml"
    if not fault_path.exists():
        msg = f"fault_types config not found: {fault_path}"
        raise GeneratorConfigError(msg)
    data = load_yaml(fault_path)
    try:
        injection = data["fault_types"]["data_drift"]["injection"]
        feature = injection["feature"]
        raw_settings = injection["settings"]
    except (KeyError, TypeError) as exc:
        msg = f"malformed data_drift injection config in {fault_path}"
        raise GeneratorConfigError(msg) from exc
    if not raw_settings:
        raise GeneratorConfigError("no data_drift injection settings configured")
    settings = [
        _Setting(
            injection_id=str(item["injection_id"]),
            seed=int(item["seed"]),
            target_distribution={str(k): float(v) for k, v in item["target_distribution"].items()},
        )
        for item in raw_settings
    ]
    ids = [s.injection_id for s in settings]
    if len(set(ids)) != len(ids):
        raise GeneratorConfigError(f"duplicate injection_id in settings: {ids}")
    if len(settings) != _EXPECTED_SETTINGS:
        raise GeneratorConfigError(
            f"P1 requires exactly {_EXPECTED_SETTINGS} injection settings, got {len(settings)}"
        )
    return feature, settings


def _observable_signals(
    condition: str,
    *,
    feature: str,
    reference: dict[str, float],
    observed: dict[str, float],
    psi: float,
    sample_size: int,
    metric: MetricComparison,
    distractors: dict[str, Any],
) -> ObservableSignals:
    """Transform the base evidence per condition (full / missing_key / noisy)."""

    change_note = (
        f"Baseline {metric.metric} on the {metric.reference_split} split changed from "
        f"{metric.reference:.4f} to {metric.observed:.4f} (delta {metric.delta:+.4f}) "
        "for this evaluation batch."
    )
    if condition == "full":
        return ObservableSignals(
            candidate_feature=feature,
            distribution_reference=reference,
            distribution_observed=observed,
            psi=psi,
            sample_size=sample_size,
            baseline_metric_reference=metric,
            notes=[change_note],
        )
    if condition == "missing_key":
        # Withhold the decisive comparison: reference distribution, PSI and the
        # reference/observed metric. The current snapshot alone cannot establish a
        # shift, so a grounded diagnosis abstains.
        return ObservableSignals(
            candidate_feature=feature,
            distribution_observed=observed,
            sample_size=sample_size,
            notes=[
                "Reference distribution, PSI and the baseline metric comparison are unavailable."
            ],
        )
    if condition == "noisy":
        return ObservableSignals(
            candidate_feature=feature,
            distribution_reference=reference,
            distribution_observed=observed,
            psi=psi,
            sample_size=sample_size,
            baseline_metric_reference=metric,
            distractor_signals=distractors,
            notes=[
                change_note,
                "Unrelated stable feature distributions are included as distractors.",
            ],
        )
    msg = f"unknown evidence condition: {condition}"
    raise GeneratorConfigError(msg)


def generate_p1(
    config_path: str | Path, output_dir: str | Path, *, overwrite: bool = True
) -> dict[str, Any]:
    """Generate the 15 P1 data-drift cases and return a summary."""

    config_path = Path(config_path)
    feature, settings = _load_settings(config_path)

    resolved = resolve_settings(config_path)
    frame = load_processed(resolved.processed_path)
    dataset_sha = sha256_file(resolved.processed_path)

    splits = split_dataset(
        frame,
        dataset_id=resolved.dataset_id,
        dataset_sha256=dataset_sha,
        seed=resolved.seed,
        ratios=resolved.ratios,
        stratified=resolved.stratified,
    )
    split_manifest_sha = _sha256_text(dumps_deterministic(splits.manifest.model_dump()))

    # Train the P1-C-02 baseline on the train split; measure clean-vs-drifted
    # accuracy on the held-out test split (never on training rows).
    pipeline = build_pipeline(resolved.model)
    pipeline.fit(splits.train.features, splits.train.target)

    def _accuracy(features: pd.DataFrame, target: pd.Series) -> float:
        predictions = pipeline.predict(features)
        return float((predictions == target.to_numpy()).mean())

    reference_metric = _accuracy(splits.test.features, splits.test.target)
    reference = categorical_distribution(splits.train.features[feature].astype(str).tolist())

    test_frame = splits.test.features.copy()
    test_frame[_TARGET_COLUMN] = splits.test.target.to_numpy()
    output_size = int(len(test_frame))

    distractors = {
        col: categorical_distribution(splits.test.features[col].astype(str).tolist())
        for col in _DISTRACTOR_FEATURES
        if col in splits.test.features.columns
    }

    injected: list[_Injected] = []
    for setting in settings:
        result = CategoricalDriftInjector(
            DriftSpec(
                injection_id=setting.injection_id,
                feature=feature,
                target_distribution=setting.target_distribution,
                output_size=output_size,
                seed=setting.seed,
            )
        ).inject(test_frame)
        drifted = result.injected
        observed = categorical_distribution(drifted[feature].astype(str).tolist())
        psi = population_stability_index(reference, observed)
        observed_metric = _accuracy(
            drifted[list(FEATURE_COLUMNS)], cast("pd.Series", drifted[_TARGET_COLUMN])
        )
        metric = MetricComparison(
            metric="accuracy",
            reference_split="test",
            reference=reference_metric,
            observed=observed_metric,
            delta=observed_metric - reference_metric,
        )
        injected.append(
            _Injected(
                setting=setting,
                observed=observed,
                psi=psi,
                metric=metric,
                injected_change=f"{feature}: {reference} -> {observed}",
            )
        )
    severity = {
        item.setting.injection_id: rank
        for rank, item in enumerate(sorted(injected, key=lambda x: x.psi, reverse=True), start=1)
    }

    output_dir = Path(output_dir)
    case_ids: list[str] = []
    condition_counts: dict[str, int] = {c: 0 for c in EVIDENCE_CONDITIONS}
    leakage_total = 0
    settings_table: list[dict[str, Any]] = []

    for index, item in enumerate(injected, start=1):
        setting = item.setting
        settings_table.append(
            {
                "index": index,
                "injection_id": setting.injection_id,
                "seed": setting.seed,
                "psi": item.psi,
                "metric_delta": item.metric.delta,
                "severity_rank": severity[setting.injection_id],
            }
        )
        injection_parameters: dict[str, Any] = {
            "feature": feature,
            "target_distribution": setting.target_distribution,
            "output_size": output_size,
            "seed": setting.seed,
        }
        for condition in EVIDENCE_CONDITIONS:
            slug = _CONDITION_SLUG[condition]
            case_id = f"p1-data-drift-{index:02d}-{slug}"
            public_id = f"p1-case-{index:02d}-{slug}"
            signals = _observable_signals(
                condition,
                feature=feature,
                reference=reference,
                observed=item.observed,
                psi=item.psi,
                sample_size=output_size,
                metric=item.metric,
                distractors=distractors,
            )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "case_id": case_id,
                "public_id": public_id,
                "fault_type": "data_drift",
                "dataset_id": resolved.dataset_id,
                "dataset_sha256": dataset_sha,
                "split_manifest_sha256": split_manifest_sha,
                "injection_id": setting.injection_id,
                "injection_seed": setting.seed,
                "injection_parameters": injection_parameters,
                "injection_setting": setting.injection_id,
                "severity_rank": severity[setting.injection_id],
                "evidence_condition": condition,
                "evidence_bundle_id": f"eb-{public_id}",
                "expected_diagnosis_behavior": EXPECTED_BEHAVIOR[condition],
                "observable_signals": signals.model_dump(),
                "artifacts": {
                    "manifest": "manifest.json",
                    "diagnosis_input": "diagnosis_input.json",
                    "ground_truth": "ground_truth.json",
                    "injection": "injection.json",
                    "checksums": "checksums.json",
                },
                "reproduction": {
                    "command": (
                        "PYTHONPATH=src python -m aletheia_lab benchmark generate-p1 "
                        "--config configs/project.yaml --output-dir experiments/p1/cases"
                    ),
                    "config": str(config_path),
                    "injection_seed": str(setting.seed),
                },
                "ground_truth_ref": "ground_truth.json",
                "split": "dev",
                "tag": "P1",
            }
            ground_truth = {
                "cause_label": "data_drift",
                "causal_mechanism": "categorical_distribution_shift",
                "injected_change": item.injected_change,
                "affected_components": [feature],
                "expected_symptoms": ["metric_regression", f"distribution_shift:{feature}"],
                "injection_parameters": injection_parameters,
            }
            injection = {
                "injection_id": setting.injection_id,
                "injector": "aletheia_lab.benchmark.injectors.CategoricalDriftInjector",
                "fault_type": "data_drift",
                "feature": feature,
                "seed": setting.seed,
                "target_distribution": setting.target_distribution,
                "achieved_distribution": item.observed,
                "reference_distribution": reference,
                "psi": item.psi,
                "output_size": output_size,
                "dataset_id": resolved.dataset_id,
                "dataset_sha256": dataset_sha,
            }
            write_case(output_dir / case_id, manifest, ground_truth, injection, overwrite=overwrite)
            loaded = load_case_dir(output_dir / case_id)
            leakage_total += len(diagnosis_input_leakage(loaded.diagnosis_input))
            case_ids.append(case_id)
            condition_counts[condition] += 1

    return {
        "case_count": len(case_ids),
        "case_ids": case_ids,
        "settings": settings_table,
        "condition_counts": condition_counts,
        "dataset_id": resolved.dataset_id,
        "dataset_sha256": dataset_sha,
        "split_manifest_sha256": split_manifest_sha,
        "reference_metric": {"metric": "accuracy", "split": "test", "value": reference_metric},
        "leakage_total": leakage_total,
        "output_dir": str(output_dir),
    }
